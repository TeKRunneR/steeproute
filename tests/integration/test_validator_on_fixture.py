# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: same osmnx / networkx boundary as tests/integration/test_grasp_on_fixture.py;
# `reportImplicitRelativeImport` — `from conftest import ...` is the shape that resolves
# under pytest's prepend import mode (see test_oracle_correctness.py for the rationale).
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

The setup → climbs → contract chain comes from the shared `grenoble_fixture`
(tests/integration/conftest.py).
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import (
    GRENOBLE_DIFFICULTY_CAP,
    GRENOBLE_J_MAX,
    GRENOBLE_L_CONNECTOR,
    GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
    GRENOBLE_SEED,
    GRENOBLE_THETA,
    GrenobleFixture,
)

from steeproute.models import ContractedGraph, Edge, Solution, SolverParams
from steeproute.solver.grasp import GraspSolver
from steeproute.validator import validate, validate_route

_N = 3
_ITER_BUDGET = 100


def _params() -> SolverParams:
    return SolverParams(
        theta=GRENOBLE_THETA,
        min_climb_slope=GRENOBLE_THETA,
        difficulty_cap=GRENOBLE_DIFFICULTY_CAP,
        l_connector=GRENOBLE_L_CONNECTOR,
        min_climb_ground_length=GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=GRENOBLE_J_MAX,
        n=_N,
        area_cap=500.0,
        untagged_policy="include",
        seed=GRENOBLE_SEED,
        iter_budget=_ITER_BUDGET,
        # Story 7.2 made time/stagnation termination live; disable stagnation so
        # the result stays an iter-budget-only function of the seed (the
        # assertions below pin that exact route set). time_budget can't bind on
        # this small fixture's ~100 fast iterations.
        time_budget=60.0,
        stagnation_iters=0,
    )


@pytest.fixture(scope="module")
def fixture_run(grenoble_fixture: GrenobleFixture) -> tuple[ContractedGraph, list[Solution]]:
    """Run GRASP once on the shared contracted graph; return the graph + solutions."""
    contracted = grenoble_fixture.contracted
    solver = GraspSolver(contracted, _params(), np.random.default_rng(GRENOBLE_SEED))
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
    assert slope[0].numeric == {"observed": 0.05, "required": GRENOBLE_THETA}
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
