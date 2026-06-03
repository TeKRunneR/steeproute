# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx / networkx boundary as tests/integration/test_grasp_on_fixture.py.
"""Validator integration tests on the real Grenoble fixture (Story 3.9 AC #6).

Two assertions the unit suite can't make:

1. **Real GRASP output validates by construction.** Running `validate` on the
   actual `GraspSolver` output for the committed Grenoble Le Sappey fixture must
   yield every route `passed=True` with no set-level Jaccard violations — GRASP
   builds routes through the same θ / SAC / edge-simple filters the validator
   re-checks and feeds them through `TopNTracker`, so a failure here signals a
   *solver* regression, not a validator bug.
2. **A crafted violation is caught with correct metadata.** Splicing one
   below-θ super-edge into an otherwise-valid solution must surface exactly one
   `slope_floor` violation with the right observed/required numerics.

Reuses the `osm_load` monkeypatch + setup → climbs → contract → GRASP chain
from `test_grasp_on_fixture.py`.
"""

from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import patch

import networkx as nx
import numpy as np
import osmnx
import pytest

from steeproute.models import (
    Area,
    ContractedGraph,
    Edge,
    PipelineConfig,
    Solution,
    SolverParams,
)
from steeproute.pipeline import run_setup_stages
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import normalize_edges
from steeproute.solver.grasp import GraspSolver
from steeproute.validator import validate, validate_route

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

# PRD §"Initial parameter defaults" — same values as test_grasp_on_fixture.py.
_THETA = 0.20
_DIFFICULTY_CAP = "T3"
_L_CONNECTOR = 200.0
_MIN_CLIMB_GROUND_LENGTH_M = 300.0
_J_MAX = 0.30
_N = 3
_ITER_BUDGET = 100
_SEED = 42


def _load_fixture_constants() -> tuple[float, float, int]:
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen_validator", regen_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CENTER_LAT, module.CENTER_LON, module.DIST_M


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()


def _params() -> SolverParams:
    return SolverParams(
        theta=_THETA,
        min_climb_slope=_THETA,
        difficulty_cap=_DIFFICULTY_CAP,
        l_connector=_L_CONNECTOR,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=_J_MAX,
        n=_N,
        area_cap=500.0,
        untagged_policy="include",
        seed=_SEED,
        iter_budget=_ITER_BUDGET,
        time_budget=60.0,
        stagnation_iters=50,
    )


@pytest.fixture(scope="module")
def fixture_run() -> tuple[ContractedGraph, list[Solution]]:
    """Run setup → climbs → contract → GRASP once; return the graph + solutions."""

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        base_graph = run_setup_stages(area, config)

    climbs = detect_climbs(
        base_graph,
        min_climb_slope=_THETA,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    assert climbs, "expected >= 1 climb on the Grenoble Le Sappey fixture"
    contracted = contract_climbs(base_graph, climbs, l_connector=_L_CONNECTOR)

    solver = GraspSolver(contracted, _params(), np.random.default_rng(_SEED))
    solutions = solver.run()
    assert solutions, "expected >= 1 GRASP route on the Grenoble Le Sappey fixture"
    return contracted, solutions


def test_real_grasp_output_validates_clean(
    fixture_run: tuple[ContractedGraph, list[Solution]],
) -> None:
    """Every GRASP-produced route passes; no set-level Jaccard violations."""
    graph, solutions = fixture_run

    validated = validate(solutions, graph, _params())

    assert len(validated.routes) == len(solutions)
    for i, route in enumerate(validated.routes):
        assert route.validation.passed, (
            f"route {i} failed validation by construction: {route.validation.violations}"
        )
    assert validated.set_violations == [], (
        f"GRASP output should be pairwise-distinct, got {validated.set_violations}"
    )


def test_crafted_below_theta_super_edge_is_caught(
    fixture_run: tuple[ContractedGraph, list[Solution]],
) -> None:
    """Splicing a below-θ super-edge into a real solution surfaces a slope_floor violation."""
    graph, _ = fixture_run

    # Pick any real super-edge id and craft an Edge sharing its identity but with
    # an avg_gradient below θ — so it is treated as a non-connector climb yet
    # fails the slope floor.
    super_edge_id = next(iter(graph.super_edge_to_base))
    bad_edge = Edge(
        node_u=super_edge_id[0],
        node_v=super_edge_id[1],
        key=super_edge_id[2],
        length_m=400.0,
        d_plus_m=20.0,
        d_minus_m=0.0,
        avg_gradient=0.05,  # below θ=0.20
        sac_scale="hiking",
    )
    crafted = Solution(edges=(bad_edge,), objective=20.0)

    validated = validate([crafted], graph, _params())

    assert len(validated.routes) == 1
    violations = validated.routes[0].validation.violations
    slope = [v for v in violations if v.constraint_id == "slope_floor"]
    assert len(slope) == 1
    assert slope[0].numeric == {"observed": 0.05, "required": _THETA}
    assert validated.routes[0].validation.passed is False


def test_validate_route_matches_orchestrator(
    fixture_run: tuple[ContractedGraph, list[Solution]],
) -> None:
    """`validate_route` on a built route equals the orchestrator's per-route result."""
    graph, solutions = fixture_run
    validated = validate(solutions, graph, _params())

    for route in validated.routes:
        standalone = validate_route(route, graph, _params())
        assert standalone.passed == route.validation.passed
        assert len(standalone.violations) == len(route.validation.violations)
