# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
# Reason: same osmnx/networkx/shapely boundary as the underlying pipeline modules.
# reportPrivateUsage relaxed because the AC #5 guard tests deliberately import
# `pipeline._assert_*` / `_drop_*` helpers — the pipeline boundary keeps these
# private to outside callers (Architecture §Boundaries) but unit-style
# orchestrator-guard coverage needs direct access.
"""Integration test for `pipeline.run_setup_stages` (Story 2.5).

The fixture test runs the orchestrator end-to-end on the committed Grenoble
data by monkeypatching `pipeline.osm_load` to read `tests/fixtures/grenoble_small/`
instead of hitting Overpass. Stages 2-7 + the four guard helpers exercise their
real production code paths on real Alpine terrain.

The four guard-helper tests cover the contract branches the real fixture
doesn't reach (it has no empty-after-filter case, no out-and-back self-loops,
no NaN elevations).
"""

from __future__ import annotations

import importlib.util
import math
import pathlib

import networkx as nx
import osmnx
import pytest
import shapely

from steeproute.errors import BadCLIArgError, PipelineContractError
from steeproute.models import Area, PipelineConfig
from steeproute.pipeline import (
    _assert_finite_elevations,
    _assert_non_empty,
    _drop_short_edges,
    run_setup_stages,
)
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"


def _load_fixture_constants() -> tuple[float, float, int]:
    """Import CENTER_LAT/CENTER_LON/DIST_M from the fixture's regenerate.py.

    Same pattern as `test_osm_live.py`: keeps the orchestrator test's `Area`
    in lock-step with the committed fixture's fetch parameters.
    """
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen", regen_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CENTER_LAT, module.CENTER_LON, module.DIST_M


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()


# AC #4 baselines — recorded by running `run_setup_stages` against the committed
# fixtures. The committed Grenoble Le Sappey 2 km bbox is dense hiking-trail
# terrain; on the current fixture (844 nodes, 2086 edges — trails + minor-road
# connectors since Story 6.2) none of the orchestrator guards activate (all
# trails pass T6, no self-loops, no NaN elevations) so the orchestrator output
# matches the input shape exactly. The ±10% band absorbs routine fixture
# regeneration drift.
_BASELINE_NODES = 844
_BASELINE_EDGES = 2086
_BASELINE_TOTAL_LENGTH_M = 242_443.0  # ~242 km of trail + road across the 16 km² bbox
_DRIFT_TOLERANCE = 0.10


# --- AC #4: real-fixture orchestrator integration ----------------------------


@pytest.fixture(scope="module")
def prepared_graph() -> nx.MultiDiGraph:
    """Run `run_setup_stages` against the committed Grenoble fixture.

    Stage 1 (`osm_load`) is patched to read the committed `.graphml` instead
    of fetching live; stages 2-7 + the four guards run unchanged. We use
    `unittest.mock.patch` as a context manager rather than pytest's
    function-scoped `monkeypatch` fixture so the patch covers a module-scoped
    fixture invocation.
    """
    from unittest.mock import patch

    if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; orchestrator integration test skipped.")

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        return run_setup_stages(area, config)


def test_run_setup_stages_topology_baseline(prepared_graph: nx.MultiDiGraph) -> None:
    """AC #4: node + edge counts are within ±10% of recorded baselines."""
    nodes = prepared_graph.number_of_nodes()
    edges = prepared_graph.number_of_edges()
    node_drift = abs(nodes - _BASELINE_NODES) / _BASELINE_NODES
    edge_drift = abs(edges - _BASELINE_EDGES) / _BASELINE_EDGES
    assert node_drift <= _DRIFT_TOLERANCE, (
        f"Node count drift {node_drift:.1%} exceeds {_DRIFT_TOLERANCE:.0%} "
        f"(baseline={_BASELINE_NODES}, actual={nodes})."
    )
    assert edge_drift <= _DRIFT_TOLERANCE, (
        f"Edge count drift {edge_drift:.1%} exceeds {_DRIFT_TOLERANCE:.0%} "
        f"(baseline={_BASELINE_EDGES}, actual={edges})."
    )


def test_run_setup_stages_full_attribute_contract(prepared_graph: nx.MultiDiGraph) -> None:
    """AC #4: every edge carries the nine setup-side contract attributes."""
    assert prepared_graph.number_of_edges() > 0
    for u, v, k, data in prepared_graph.edges(data=True, keys=True):
        ctx = f"edge ({u}, {v}, {k})"
        assert isinstance(data["geometry"], shapely.LineString), ctx
        assert isinstance(data["vertices_resampled"], list), ctx
        assert isinstance(data["length_m"], float), ctx
        assert isinstance(data["d_plus_m"], float), ctx
        assert isinstance(data["d_minus_m"], float), ctx
        assert isinstance(data["avg_gradient"], float), ctx
        assert "sac_scale" in data, ctx
        assert "highway" in data, ctx
        assert "osm_way_id" in data, ctx


def test_run_setup_stages_sign_and_finiteness_invariants(
    prepared_graph: nx.MultiDiGraph,
) -> None:
    """AC #4: every metric is finite; sign invariants hold per edge."""
    for u, v, k, data in prepared_graph.edges(data=True, keys=True):
        ctx = f"edge ({u}, {v}, {k})"
        assert math.isfinite(data["length_m"]), ctx
        assert math.isfinite(data["d_plus_m"]), ctx
        assert math.isfinite(data["d_minus_m"]), ctx
        assert math.isfinite(data["avg_gradient"]), ctx
        assert data["length_m"] > 0.0, ctx
        assert data["d_plus_m"] >= 0.0, ctx
        assert data["d_minus_m"] >= 0.0, ctx
        assert data["avg_gradient"] >= 0.0, ctx


def test_run_setup_stages_aggregate_length_plausibility(
    prepared_graph: nx.MultiDiGraph,
) -> None:
    """AC #4: `sum(length_m)` is within ±10% of the recorded baseline.

    Catches gross axis-swap / unit / sign-flip bugs at the orchestrator scale.
    Sub-edge bugs are caught by the unit-layer tests in `test_climbs.py` and
    `test_smoothing.py`.
    """
    total = sum(data["length_m"] for _u, _v, _k, data in prepared_graph.edges(data=True, keys=True))
    drift = abs(total - _BASELINE_TOTAL_LENGTH_M) / _BASELINE_TOTAL_LENGTH_M
    assert drift <= _DRIFT_TOLERANCE, (
        f"Total length drift {drift:.1%} exceeds {_DRIFT_TOLERANCE:.0%} "
        f"(baseline={_BASELINE_TOTAL_LENGTH_M:.0f} m, actual={total:.0f} m)."
    )


def test_run_setup_stages_no_orphan_nodes(prepared_graph: nx.MultiDiGraph) -> None:
    """AC #4: orphan-node prune leaves every node with degree ≥ 1."""
    min_degree = min(deg for _, deg in prepared_graph.degree())
    assert min_degree >= 1, f"Expected min degree ≥ 1 after orphan prune, got {min_degree}."


# --- AC #5: orchestrator-guard tests against crafted inputs ------------------


def _make_single_edge_graph_with_geometry(
    coords: list[tuple[float, float]],
) -> nx.MultiDiGraph:
    """Build a one-edge MultiDiGraph with the given `(lon, lat)` geometry."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=coords[0][0], y=coords[0][1])
    g.add_node(1, x=coords[-1][0], y=coords[-1][1])
    g.add_edge(
        0,
        1,
        key=0,
        geometry=shapely.LineString(coords),
        sac_scale="hiking",
        highway="path",
        osm_way_id=12345,
    )
    return g


def _make_single_edge_graph_with_elevation(
    vertices_resampled: list[tuple[float, float, float]],
) -> nx.MultiDiGraph:
    """Build a one-edge MultiDiGraph with stage-6-shaped `vertices_resampled`."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    first_lat, first_lon, _ = vertices_resampled[0]
    last_lat, last_lon, _ = vertices_resampled[-1]
    g.add_node(0, x=first_lon, y=first_lat)
    g.add_node(1, x=last_lon, y=last_lat)
    g.add_edge(
        0,
        1,
        key=0,
        geometry=shapely.LineString([(lon, lat) for lat, lon, _ in vertices_resampled]),
        vertices_resampled=vertices_resampled,
        sac_scale="hiking",
        highway="path",
        osm_way_id=12345,
    )
    return g


def test_drop_short_edges_removes_out_and_back_self_loop() -> None:
    """AC #5: out-and-back coincident-2D polyline is dropped (Story 2.4 D3).

    A self-loop `(u, u)` whose geometry is `[(lon, lat), (lon+eps, lat+eps),
    (lon, lat)]` has near-zero length-along-polyline but passes stage 4's
    bit-identical-coord check. The guard's local-equirectangular length probe
    catches it.
    """
    # Use 1e-9 degrees ≈ 0.1 mm at this latitude — well below the 1 mm floor.
    coords = [(5.0, 45.0), (5.0 + 1e-9, 45.0 + 1e-9), (5.0, 45.0)]
    g = _make_single_edge_graph_with_geometry(coords)
    assert g.number_of_edges() == 1
    out = _drop_short_edges(g)
    assert out.number_of_edges() == 0, "Out-and-back self-loop was not dropped."


def test_drop_short_edges_keeps_normal_edge() -> None:
    """AC #5 (negative case): a normal ~100 m edge survives the prune."""
    # 0.001° latitude ≈ 111 m — well above the 1 mm floor.
    coords = [(5.0, 45.0), (5.0, 45.001)]
    g = _make_single_edge_graph_with_geometry(coords)
    out = _drop_short_edges(g)
    assert out.number_of_edges() == 1


def test_assert_finite_elevations_raises_on_nan_elevation() -> None:
    """AC #5: NaN elevation post-stage-6 → `PipelineContractError` naming the edge (Story 2.4 D2)."""
    verts = [(45.0, 5.0, 1000.0), (45.001, 5.0, math.nan), (45.002, 5.0, 1010.0)]
    g = _make_single_edge_graph_with_elevation(verts)
    with pytest.raises(PipelineContractError, match=r"\(0, 1, 0\)") as exc_info:
        _assert_finite_elevations(g)
    assert "non-finite elevation" in exc_info.value.user_message


def test_assert_finite_elevations_accepts_all_finite() -> None:
    """AC #5 (negative case): all-finite elevations do not raise."""
    verts = [(45.0, 5.0, 1000.0), (45.001, 5.0, 1005.0), (45.002, 5.0, 1010.0)]
    g = _make_single_edge_graph_with_elevation(verts)
    _assert_finite_elevations(g)  # no raise


def test_assert_non_empty_raises_on_zero_edges() -> None:
    """AC #5: zero edges after stage 2 → `PipelineContractError` naming area + policy (Story 2.1 D2)."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=5.0, y=45.0)  # node-only, no edges
    area = Area(center=(45.0, 5.0), radius_km=1.0)
    with pytest.raises(PipelineContractError, match="zero edges") as exc_info:
        _assert_non_empty(g, area, "include")
    msg = exc_info.value.user_message
    assert "(45.0, 5.0)" in msg
    assert "include" in msg


def test_assert_non_empty_accepts_non_empty_graph() -> None:
    """AC #5 (negative case): one-edge graph is accepted."""
    coords = [(5.0, 45.0), (5.0, 45.001)]
    g = _make_single_edge_graph_with_geometry(coords)
    area = Area(center=(45.0, 5.0), radius_km=1.0)
    _assert_non_empty(g, area, "include")  # no raise


# --- Review patch P2: dem_path fail-fast at orchestrator entry ---------------


def test_run_setup_stages_fails_fast_on_missing_dem_path(tmp_path: pathlib.Path) -> None:
    """P2: `run_setup_stages` raises `BadCLIArgError` before stage 1 if `dem_path`
    does not exist, so the expensive stages 1-4 are not run on bad input.
    """
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    missing_dem = tmp_path / "does_not_exist.tif"
    config = PipelineConfig(untagged_policy="include", dem_path=missing_dem)
    with pytest.raises(BadCLIArgError, match="does not exist or is not a regular file"):
        _ = run_setup_stages(area, config)
