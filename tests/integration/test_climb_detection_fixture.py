# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx boundary as tests/integration/test_pipeline_end_to_end.py.
"""Integration test for `pipeline.climbs.detect_climbs` (stage 8, Story 3.2).

Runs `detect_climbs` against the post-stage-7 output of `run_setup_stages`
on the committed Grenoble Le Sappey fixture (16 km² of dense alpine hiking
terrain — 468 nodes, 1208 edges; see `test_pipeline_end_to_end.py`).

The baselines below are **regression snapshots**, not independently-derived
topology bounds. They were recorded by running the current `detect_climbs`
against the committed fixture once during Story 3.2 dev (then re-recorded
after the post-review node-monotonicity fix). Sanity-check against topology:
the Le Sappey bbox spans Chamechaude's south flank, Col de Porte approaches,
and the La Pinéa ridge — a handful of major ascents plus dozens of local
hill sections, broadly consistent with the recorded climb count. But the
test's actual contract is "the algorithm continues to produce these numbers
on this fixture" — i.e. a regression-pin — not "this many climbs exist on
this terrain." Treat the ±10 % drift band as absorbing routine fixture
regeneration noise (same `_DRIFT_TOLERANCE` convention as
`test_pipeline_end_to_end.py`), not as a topology-verification window. AC #3
of Story 3.2 asked for the latter; the implementation delivers the former.
"""

from __future__ import annotations

import importlib.util
import math
import pathlib
from unittest.mock import patch

import networkx as nx
import osmnx
import pytest

from steeproute.models import Area, PipelineConfig
from steeproute.pipeline import operationalize_graph, run_setup_stages
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

# PRD §"Initial parameter defaults" — the values shipped as click defaults
# in `cli/_shared.py`. Pinned here so the test exercises the production
# defaults (not arbitrary numbers).
_MIN_CLIMB_SLOPE = 0.20
_MIN_CLIMB_GROUND_LENGTH_M = 300.0

# Regression-snapshot baselines: the climb count and total D+ recorded the
# first time the (current) `detect_climbs` ran against the committed fixture.
# These are not independently topology-derived — see module docstring. The
# ±10 % drift band absorbs OSM / DEM regeneration noise, not algorithm
# changes; an algorithm change that shifts these is intentional and
# requires re-recording the baselines.
_BASELINE_CLIMB_COUNT = 50
_BASELINE_TOTAL_D_PLUS_M = 8065.5
_DRIFT_TOLERANCE = 0.10


def _load_fixture_constants() -> tuple[float, float, int]:
    """Import CENTER_LAT/CENTER_LON/DIST_M from the fixture's regenerate.py."""
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen", regen_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CENTER_LAT, module.CENTER_LON, module.DIST_M


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()


@pytest.fixture(scope="module")
def prepared_graph() -> nx.MultiDiGraph:
    """Operational Grenoble fixture (query-side reshaped), fed into stage-8 climb detection.

    Mirrors `tests/integration/test_pipeline_end_to_end.py`: patches `osm_load` to
    read the committed `.graphml`. `run_setup_stages` caches the raw post-stage-5
    elevation; `operationalize_graph` (Story 6.3) applies the query-side stages 6-7
    (smooth → deadband → naive-sum metrics) at the production defaults, mirroring
    what `cli/query.py` feeds to `detect_climbs`.
    """
    if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; climb-detection integration skipped.")

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        return operationalize_graph(run_setup_stages(area, config))


def test_climb_count_within_regression_baseline(prepared_graph: nx.MultiDiGraph) -> None:
    """Climb count within ±10 % of the regression-snapshot baseline."""
    climbs = detect_climbs(
        prepared_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    count_drift = abs(len(climbs) - _BASELINE_CLIMB_COUNT) / _BASELINE_CLIMB_COUNT
    assert count_drift <= _DRIFT_TOLERANCE, (
        f"Climb count drift {count_drift:.1%} exceeds {_DRIFT_TOLERANCE:.0%} "
        f"(baseline={_BASELINE_CLIMB_COUNT}, actual={len(climbs)})."
    )


def test_total_climb_d_plus_within_regression_baseline(prepared_graph: nx.MultiDiGraph) -> None:
    """Summed climb D+ within ±10 % of the regression-snapshot baseline."""
    climbs = detect_climbs(
        prepared_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    total_d_plus = sum(c.d_plus_m for c in climbs)
    drift = abs(total_d_plus - _BASELINE_TOTAL_D_PLUS_M) / _BASELINE_TOTAL_D_PLUS_M
    assert drift <= _DRIFT_TOLERANCE, (
        f"Total climb D+ drift {drift:.1%} exceeds {_DRIFT_TOLERANCE:.0%} "
        f"(baseline={_BASELINE_TOTAL_D_PLUS_M:.0f} m, actual={total_d_plus:.0f} m)."
    )


def test_every_climb_meets_floor_constraints(prepared_graph: nx.MultiDiGraph) -> None:
    """Every emitted climb satisfies the two floor constraints by construction."""
    climbs = detect_climbs(
        prepared_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    assert climbs, "expected ≥ 1 climb on this fixture"
    for climb in climbs:
        assert climb.length_m >= _MIN_CLIMB_GROUND_LENGTH_M, (
            f"climb under length floor: {climb.length_m:.1f} m < {_MIN_CLIMB_GROUND_LENGTH_M} m"
        )
        assert climb.avg_slope >= _MIN_CLIMB_SLOPE, (
            f"climb under slope floor: avg_slope={climb.avg_slope:.3f} < min_climb_slope={_MIN_CLIMB_SLOPE}"
        )


def test_detect_climbs_does_not_mutate_real_fixture(prepared_graph: nx.MultiDiGraph) -> None:
    """AC #4: real-fixture purity check — node + edge counts unchanged."""
    nodes_before = prepared_graph.number_of_nodes()
    edges_before = prepared_graph.number_of_edges()
    _ = detect_climbs(
        prepared_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    assert prepared_graph.number_of_nodes() == nodes_before
    assert prepared_graph.number_of_edges() == edges_before


def test_climbs_are_edge_disjoint_on_real_fixture(prepared_graph: nx.MultiDiGraph) -> None:
    """AC #4: real-fixture edge-disjointness — Story 3.3's back-mapping injectivity."""
    climbs = detect_climbs(
        prepared_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    seen: set[tuple[int, int, int]] = set()
    for climb in climbs:
        for edge in climb.edges:
            key = (edge.node_u, edge.node_v, edge.key)
            assert key not in seen, f"edge {key} appears in multiple climbs"
            seen.add(key)


def test_aggregate_identity_holds_on_real_fixture(prepared_graph: nx.MultiDiGraph) -> None:
    """AC #1: per-climb aggregate equals the sum of underlying edge metrics.

    Cross-check on the real fixture catches ULP-level reassociation drift
    that hand-built single-climb tests can't surface (incremental
    `cum_d_plus` / `cum_length` accumulation vs. fresh `sum(...)` over the
    final edge tuple).
    """
    climbs = detect_climbs(
        prepared_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    for i, climb in enumerate(climbs):
        ctx = f"climb #{i}"
        length_sum = sum(e.length_m for e in climb.edges)
        d_plus_sum = sum(e.d_plus_m for e in climb.edges)
        assert math.isclose(climb.length_m, length_sum, abs_tol=1e-9), (
            f"{ctx}: length_m={climb.length_m} vs sum={length_sum}"
        )
        assert math.isclose(climb.d_plus_m, d_plus_sum, abs_tol=1e-9), (
            f"{ctx}: d_plus_m={climb.d_plus_m} vs sum={d_plus_sum}"
        )
        assert math.isclose(climb.avg_slope, d_plus_sum / length_sum, abs_tol=1e-9), (
            f"{ctx}: avg_slope={climb.avg_slope} vs sum-ratio={d_plus_sum / length_sum}"
        )


def test_climbs_are_node_monotone_on_real_fixture(prepared_graph: nx.MultiDiGraph) -> None:
    """Every climb is a simple path — no node revisited within a single climb.

    Pins the node-monotonicity guard's behavior at production scale: a climb
    of N edges must touch exactly N+1 distinct nodes. Catches a regression
    that re-admits zigzag walks through bidirectional / parallel edges.
    """
    climbs = detect_climbs(
        prepared_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    for i, climb in enumerate(climbs):
        nodes = [climb.edges[0].node_u] + [e.node_v for e in climb.edges]
        assert len(set(nodes)) == len(nodes), f"climb #{i} revisits a node: {nodes}"
