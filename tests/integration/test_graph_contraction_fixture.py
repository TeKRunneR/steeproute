# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx / networkx boundary as tests/integration/test_pipeline_end_to_end.py.
"""Integration test for `pipeline.graph.contract_climbs` (stage 9, Story 3.3).

Runs the full setup → climb-detection → contraction chain against the
committed Grenoble Le Sappey fixture and asserts the two contract relations
required by AC #3: (1) the contracted graph has strictly fewer edges than
the post-stage-7 base graph (multi-edge climbs collapse into single
super-edges; since Story 5.1 all connectors are retained, so the reduction
comes purely from climb collapse), (2) for every super-edge the back-mapped
base-edge sequence's `sum(e.length_m)` and `sum(e.d_plus_m)` equal the
super-edge's stored aggregates within `math.isclose(abs_tol=1e-9)`.

Reuses the `osm_load` monkeypatch + `run_setup_stages` pattern from
`tests/integration/test_climb_detection_fixture.py`.
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
from steeproute.pipeline import run_setup_stages
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

# PRD §"Initial parameter defaults" — the values shipped as click defaults
# in `cli/_shared.py`. Same values as `test_climb_detection_fixture.py` so
# both integration tests exercise the production-default chain.
_MIN_CLIMB_SLOPE = 0.20
_MIN_CLIMB_GROUND_LENGTH_M = 300.0
_L_CONNECTOR = 200.0


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
def base_graph() -> nx.MultiDiGraph:
    """Post-stage-7 Grenoble fixture, fed into stage 8 then stage 9.

    Mirrors `test_climb_detection_fixture.py::prepared_graph`: patches
    `osm_load` to read the committed `.graphml`, then runs `run_setup_stages`
    unchanged for stages 2-7.
    """
    if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; graph-contraction integration skipped.")

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        return run_setup_stages(area, config)


def test_contracted_graph_has_fewer_edges_than_base(base_graph: nx.MultiDiGraph) -> None:
    """AC #3: contracted graph is smaller via climb collapse (connectors all retained)."""
    climbs = detect_climbs(
        base_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    assert climbs, "expected ≥ 1 climb on this fixture"

    contracted = contract_climbs(base_graph, climbs, l_connector=_L_CONNECTOR)

    assert contracted.graph.number_of_edges() < base_graph.number_of_edges(), (
        f"contracted graph not smaller: "
        f"{contracted.graph.number_of_edges()} >= {base_graph.number_of_edges()}"
    )
    # Sanity: at least one super-edge per climb. Junction-aware splitting (Story
    # 6.1, default on) can break a climb into several super-edges at interior
    # trail junctions, so the count is `>= len(climbs)` rather than `==`.
    assert len(contracted.super_edge_to_base) >= len(climbs)


def test_super_edge_aggregates_match_back_expanded_base_metrics(
    base_graph: nx.MultiDiGraph,
) -> None:
    """AC #3: per super-edge, sum of metrics in `base_graph` matches stored aggregate.

    Cross-checks aggregation correctness end-to-end: the super-edge's stored
    `length_m` / `d_plus_m` / `d_minus_m` must equal the sum of the SAME-named
    attributes on the underlying base edges as they live in `base_graph`'s
    edge-data dicts. Going back to `base_graph[u][v][k]` (rather than to the
    `Edge` projections in `super_edge_to_base`, which are themselves the
    source `contract_climbs` summed) is what makes the check non-tautological
    — it catches any drift between `Edge.length_m` (projected by stage 8) and
    `base_graph[u][v][k]["length_m"]` (the stage-7 storage of record).
    ULP-level reassociation drift on the real fixture should stay within
    `math.isclose(abs_tol=1e-9)`.
    """
    climbs = detect_climbs(
        base_graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    contracted = contract_climbs(base_graph, climbs, l_connector=_L_CONNECTOR)

    for super_id, base_edges in contracted.super_edge_to_base.items():
        u, v, k = super_id
        data = contracted.graph[u][v][k]
        # Look up each base edge's metrics from `base_graph` directly, NOT
        # from the `Edge` projections in `base_edges` (which were the input
        # to `contract_climbs`'s aggregation — comparing against them would
        # be tautological).
        base_lengths: list[float] = []
        base_d_plus: list[float] = []
        base_d_minus: list[float] = []
        for e in base_edges:
            # `get_edge_data(..., key=...)` is the method-form lookup;
            # `base_graph[u][v][k]` would trip basedpyright on networkx's
            # `__getitem__(key: str)` partial stub.
            base_data = base_graph.get_edge_data(e.node_u, e.node_v, key=e.key)
            assert base_data is not None, (
                f"super-edge {super_id}: base edge "
                f"({e.node_u}, {e.node_v}, {e.key}) missing from base_graph"
            )
            base_lengths.append(base_data["length_m"])
            base_d_plus.append(base_data["d_plus_m"])
            base_d_minus.append(base_data["d_minus_m"])
        length_sum = sum(base_lengths)
        d_plus_sum = sum(base_d_plus)
        d_minus_sum = sum(base_d_minus)
        assert math.isclose(data["length_m"], length_sum, abs_tol=1e-9), (
            f"super-edge {super_id}: length_m={data['length_m']} vs base-graph sum={length_sum}"
        )
        assert math.isclose(data["d_plus_m"], d_plus_sum, abs_tol=1e-9), (
            f"super-edge {super_id}: d_plus_m={data['d_plus_m']} vs base-graph sum={d_plus_sum}"
        )
        assert math.isclose(data["d_minus_m"], d_minus_sum, abs_tol=1e-9), (
            f"super-edge {super_id}: d_minus_m={data['d_minus_m']} vs base-graph sum={d_minus_sum}"
        )
