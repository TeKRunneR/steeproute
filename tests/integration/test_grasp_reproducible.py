# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx / networkx boundary as tests/integration/test_graph_contraction_fixture.py.
"""GRASP FR29 reproducibility: same seed + same graph → byte-identical results.

Story 3.6 AC #6: two `GraspSolver` runs with two fresh
`numpy.random.default_rng(42)` instances on the same module-scoped
`ContractedGraph` and identical `SolverParams` produce identical
`list[Solution]` — same length, same `Solution.objective` per entry, same
`Solution.edges` traversal order. Downstream golden-hash regressions (Story
5.1) hash the canonical edge-sequence per route, so FR29 protects edge-set
identity AND ordering.

The contracted graph is built once at module scope so the test isolates the
solver's determinism contract — any drift in the upstream setup chain would
be a Story 2.x bug, not a GRASP bug.
"""

from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import patch

import networkx as nx
import numpy as np
import osmnx
import pytest

from steeproute.models import Area, ContractedGraph, PipelineConfig, SolverParams
from steeproute.pipeline import operationalize_graph, run_setup_stages
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import normalize_edges
from steeproute.solver.grasp import GraspSolver

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

_THETA = 0.20
_DIFFICULTY_CAP = "T3"
_L_CONNECTOR = 200.0
_MIN_CLIMB_GROUND_LENGTH_M = 300.0
_J_MAX = 0.30
_N = 3
_ITER_BUDGET = 50
_SEED = 42


def _load_fixture_constants() -> tuple[float, float, int]:
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen", regen_path)
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
def contracted() -> ContractedGraph:
    """Build the contracted Grenoble graph once for both GRASP runs.

    Module-scoped so the determinism check times in a few seconds — the
    setup chain itself isn't under test here. The committed fixtures are
    required (no `pytest.skip` fallback — AC #8 forbids it); a missing fixture
    hard-fails.
    """

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        base = operationalize_graph(run_setup_stages(area, config))
    climbs = detect_climbs(
        base, min_climb_slope=_THETA, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M
    )
    return contract_climbs(base, climbs, l_connector=_L_CONNECTOR)


def test_grasp_two_runs_with_same_seed_are_byte_identical(contracted: ContractedGraph) -> None:
    """FR29 / NFR4: `--seed 42` produces identical edge-sets AND identical traversal orders.

    The downstream golden-hash regression (Story 5.1) hashes the canonical edge
    *sequence*, so this test pins both the multiset and the ordering. Each
    `GraspSolver` instance gets its own fresh `default_rng(42)` — sharing a
    Generator between runs would let state from the first run bleed into the
    second.
    """
    params = _params()
    result_a = GraspSolver(contracted, params, np.random.default_rng(_SEED)).run()
    result_b = GraspSolver(contracted, params, np.random.default_rng(_SEED)).run()

    assert len(result_a) == len(result_b), (
        f"different result lengths: {len(result_a)} vs {len(result_b)}"
    )
    for i, (sol_a, sol_b) in enumerate(zip(result_a, result_b, strict=True)):
        # Raw `==` (not `math.isclose`) is deliberate: FR29 promises
        # *byte-identical* reproducibility, so objectives must be bit-for-bit
        # equal. `math.isclose` would mask exactly the drift this test guards.
        assert sol_a.objective == sol_b.objective, (
            f"route {i}: objectives diverge ({sol_a.objective} vs {sol_b.objective})"
        )
        # Canonical edge identity sequence — same triples in the same order.
        ids_a = [(e.node_u, e.node_v, e.key) for e in sol_a.edges]
        ids_b = [(e.node_u, e.node_v, e.key) for e in sol_b.edges]
        assert ids_a == ids_b, f"route {i}: edge sequences diverge ({ids_a} vs {ids_b})"
