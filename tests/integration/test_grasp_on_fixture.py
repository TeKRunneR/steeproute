# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: same osmnx / networkx boundary as tests/integration/test_graph_contraction_fixture.py;
# `reportImplicitRelativeImport` — `from conftest import ...` is the shape that resolves
# under pytest's prepend import mode (see test_oracle_correctness.py for the rationale).
"""GRASP solver integration test on the real Grenoble fixture (Story 3.6 AC #5).

Runs `GraspSolver.run()` against the shared session-scoped `grenoble_fixture`
(tests/integration/conftest.py — the setup → climbs → contract chain) and
asserts the structural contract every returned route must satisfy:

- `len(result) <= params.n` (FR11 cap).
- Each route is an edge-simple walk in the contracted graph (no repeated
  `(node_u, node_v, key)`, consecutive edges share an endpoint).
- Each route clears the route-level slope floor: `(ΣD+ + ΣD−)/Σlength >= θ`
  (FR3 — enforced by `GraspSolver._route_slope_ok` at finalization).
- Each edge's SAC scale ranks at or below `params.difficulty_cap`.
- Pairwise Jaccard distance >= `1 - params.j_max` across all returned pairs
  (FR11 distinctness — self-consistency check against what the tracker
  admitted).

FR10 strict-containment is checked transitively: every edge is drawn from the
area-clipped contracted graph, which `contract_climbs` cuts to the query area
upstream.

`iter_budget` is tuned for a CI-friendly wall-clock — Story 3.6 AC #5 caps the
test at ~30 s.
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

from steeproute.models import (
    ContractedGraph,
    Edge,
    Solution,
    SolverParams,
    route_avg_gradient,
)
from steeproute.pipeline.osm import max_sac_rank, parse_difficulty_cap
from steeproute.solver.distinctness import jaccard_distance
from steeproute.solver.grasp import GraspSolver
from steeproute.solver.reuse import blocking_ids, non_exempt_base_segment_ids

_N = 3
# Conservative: enough iterations to populate the tracker on this small
# fixture without blowing the CI wall-clock cap (AC #5: ~30 s).
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
        # the quality-gate run is an iter-budget-only function of the seed (early
        # stagnation could cut the search before a late improvement). time_budget
        # can't bind on this small fixture's ~100 fast iterations.
        time_budget=60.0,
        stagnation_iters=0,
    )


@pytest.fixture(scope="module")
def solver_chain(grenoble_fixture: GrenobleFixture) -> tuple[ContractedGraph, list[Solution]]:
    """Run GRASP once on the shared contracted graph; return graph + routes.

    Module-scoped because every assertion operates on the same output —
    re-running construction for each test would multiply the wall-clock by the
    test count for no semantic gain. The contracted graph is exposed alongside
    the routes so the Story 5.2 reuse check can read the `base_segment_id` tags.
    The `assert result` here is the single non-vacuity guard for the whole
    module — every dependent test iterates the result, so pinning non-emptiness
    once at the fixture trips them all if a regression empties the output.
    """
    contracted = grenoble_fixture.contracted
    solver = GraspSolver(contracted, _params(), np.random.default_rng(GRENOBLE_SEED))
    result = solver.run()
    assert result, "expected >= 1 GRASP route on the Grenoble Le Sappey fixture"
    return contracted, result


@pytest.fixture(scope="module")
def grasp_result(solver_chain: tuple[ContractedGraph, list[Solution]]) -> list[Solution]:
    """The GRASP routes from `solver_chain` (kept as a separate fixture name for clarity)."""
    return solver_chain[1]


def _assert_valid_walk(sol_edges: tuple[Edge, ...]) -> None:
    """Edge-simple directed walk: no repeated `(u, v, key)`; consecutive edges share endpoint."""
    assert sol_edges, "GRASP routes returned by the tracker must be non-empty"
    seen: set[tuple[int, int, int]] = set()
    for i, edge in enumerate(sol_edges):
        eid = (edge.node_u, edge.node_v, edge.key)
        assert eid not in seen, f"edge {eid} repeated at position {i}"
        seen.add(eid)
        if i > 0:
            prev = sol_edges[i - 1]
            assert prev.node_v == edge.node_u, (
                f"walk discontinuity at position {i}: prev ends at {prev.node_v}, "
                f"next starts at {edge.node_u}"
            )


def test_grasp_returns_at_most_n_routes(grasp_result: list[Solution]) -> None:
    """FR11: top-N cap holds on real-fixture output.

    Non-emptiness is guaranteed by the `grasp_run` fixture's `assert result`,
    so this test focuses on the upper bound.
    """
    assert len(grasp_result) <= _N


def test_every_grasp_route_is_an_edge_simple_walk(grasp_result: list[Solution]) -> None:
    """Edge-simple-walk contract on every returned route."""
    for sol in grasp_result:
        _assert_valid_walk(sol.edges)


def test_every_grasp_route_clears_route_level_theta(grasp_result: list[Solution]) -> None:
    """FR3: every admitted route's whole-route average gradient clears θ.

    The binding slope constraint is route-level — `GraspSolver._route_slope_ok`
    admits a finalized solution only if `(ΣD+ + ΣD−)/Σlength >= θ`. So no
    returned route may fall below θ on average, even though individual connector
    edges within it may be flat or downhill. This is feasible-by-construction:
    a failure here signals a solver bug (the gate was bypassed), not a tuning
    issue. Allow a tiny epsilon for float summation order.
    """
    for sol in grasp_result:
        avg = route_avg_gradient(sol.edges)
        assert avg >= GRENOBLE_THETA - 1e-9, (
            f"route avg_gradient={avg} fell below the route-level floor θ={GRENOBLE_THETA}"
        )


def test_every_edge_in_every_route_satisfies_sac_cap(
    grasp_result: list[Solution],
) -> None:
    """Architecture §Cat 6: per-edge SAC cap holds on every route edge.

    `max_sac_rank(None)` and unrecognized values return `None` and are
    admitted (same policy as the oracle in Story 3.5). Only known SAC scales
    above `cap_rank` should be rejected — and none can appear in any route.
    """
    cap_rank = parse_difficulty_cap(GRENOBLE_DIFFICULTY_CAP)
    for sol in grasp_result:
        for edge in sol.edges:
            rank = max_sac_rank(edge.sac_scale)
            if rank is not None:
                assert rank <= cap_rank, (
                    f"edge {(edge.node_u, edge.node_v, edge.key)} has sac_scale="
                    f"{edge.sac_scale!r} (rank {rank}) > cap_rank {cap_rank}"
                )


def test_no_grasp_route_reuses_a_nonexempt_base_segment(
    solver_chain: tuple[ContractedGraph, list[Solution]],
) -> None:
    """Story 5.2 / FR5: no returned route walks a non-exempt base segment twice, in any direction.

    Reads the `base_segment_id` tags off the real contracted graph and replays
    the once-only rule (`solver.reuse`) over each route. This is the empirical
    confirmation — deferred from Story 5.1 — that the undirected ids actually
    collide on real OSM data (forward/reverse of a trail share an id), so the
    out-and-back is killed in practice and not just on synthetic fixtures.
    """
    contracted, result = solver_chain
    non_exempt = non_exempt_base_segment_ids(contracted)
    nx_graph = contracted.graph
    for sol in result:
        used: set[tuple[int, int, int]] = set()
        for edge in sol.edges:
            data = nx_graph.get_edge_data(edge.node_u, edge.node_v, edge.key)
            blocking = blocking_ids(data, edge.node_u, edge.node_v, edge.key, non_exempt)
            clash = blocking & used
            assert not clash, (
                f"route reuses non-exempt base segment(s) {clash} at edge "
                f"{(edge.node_u, edge.node_v, edge.key)}"
            )
            used |= blocking


def test_pairwise_jaccard_distance_meets_distinctness_threshold(
    grasp_result: list[Solution],
) -> None:
    """FR11 self-consistency: held routes are pairwise distinct per `j_max`.

    `TopNTracker`'s admission policy guarantees `jaccard_distance >= 1 - j_max`
    between any held pair; this test pins the invariant in the integration
    layer (catches a regression where GRASP bypasses `tracker.consider(...)`).
    """
    threshold = 1.0 - GRENOBLE_J_MAX
    for i in range(len(grasp_result)):
        for j in range(i + 1, len(grasp_result)):
            dist = jaccard_distance(grasp_result[i], grasp_result[j])
            assert dist >= threshold, (
                f"routes {i} and {j} jaccard_distance={dist} < threshold={threshold}"
            )
