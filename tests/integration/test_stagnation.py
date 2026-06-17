# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same networkx-boundary pattern as tests/unit/test_grasp_construction.py.
"""Stagnation termination (Story 7.2, Architecture §Cat 5e).

GRASP must stop early once the top-N total objective is unchanged for
`--stagnation-iters` consecutive iterations, tagging the run `converged`. On a
graph whose only feasible route is found on the first iteration, the objective
plateaus immediately, so a small window terminates the solve far short of a
large `iter_budget`. Setting `--stagnation-iters 0` disables the check, leaving
iter-budget the terminator.

The fixture is a single node with one self-loop edge — the start-node sample is
forced (only one node) and the only constructible route is that self-loop
(Story 3.6 admits length-1 self-loop routes). So *every* GRASP iteration builds
the identical route: the tracker admits it once on iteration 1, then rejects the
duplicate forever after. The top-N total objective is therefore bit-stable from
iteration 2 on, making the iteration at which stagnation trips exactly
predictable — which is what lets this test pin the off-by-one termination
semantics rather than just "stopped somewhere early".
"""

from __future__ import annotations

import networkx as nx
import numpy as np

from steeproute.models import ContractedGraph, Edge, SolverParams
from steeproute.progress import ProgressEvent
from steeproute.solver.grasp import GraspSolver

_THETA = 0.20


def _build_self_loop_graph() -> ContractedGraph:
    """One node, one self-loop super-edge clearing θ (avg_gradient 0.75)."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    edge = Edge(
        node_u=0,
        node_v=0,
        key=0,
        length_m=400.0,
        d_plus_m=300.0,
        d_minus_m=0.0,
        avg_gradient=0.75,
        sac_scale="hiking",
    )
    g.add_edge(
        0,
        0,
        key=0,
        length_m=400.0,
        d_plus_m=300.0,
        d_minus_m=0.0,
        avg_gradient=0.75,
        sac_scale="hiking",
        base_segment_id=frozenset({(0, 0, 0)}),
        reusable=False,
    )
    return ContractedGraph(graph=g, super_edge_to_base={(0, 0, 0): (edge,)})


def _params(*, iter_budget: int, stagnation_iters: int) -> SolverParams:
    return SolverParams(
        theta=_THETA,
        min_climb_slope=_THETA,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=3,
        area_cap=500.0,
        untagged_policy="include",
        seed=42,
        iter_budget=iter_budget,
        # Large, non-binding: this test isolates stagnation, not the time budget.
        time_budget=3600.0,
        stagnation_iters=stagnation_iters,
    )


def test_stagnation_terminates_well_before_iter_budget() -> None:
    """Plateaued objective → `converged` far short of a large iter-budget.

    A list-collecting callback counts how many iterations actually ran. With the
    objective unchanged after iteration 1 and a window of 5, the solver stops at
    iteration 6 (counter reaches 5) — nowhere near `iter_budget=10_000`.
    """
    events: list[ProgressEvent] = []
    params = _params(iter_budget=10_000, stagnation_iters=5)
    solver = GraspSolver(
        _build_self_loop_graph(), params, np.random.default_rng(42), progress_callback=events.append
    )
    result = solver.run()

    assert result, "self-loop fixture should yield exactly one route"
    assert solver.convergence_status == "converged"
    # Stops the iteration after the counter hits the window: admit on iter 1,
    # then 5 unchanged iterations → terminate at iteration 6.
    assert len(events) == 6
    assert events[-1].stagnation_counter == 5
    # Convergence iteration (Story 7.3): the one and only admission lands on
    # iteration 1, so that is where the search last improved — and it matches the
    # `(i + 1) − stagnation_counter` identity evaluated at the final event.
    assert solver.convergence_iteration == 1
    assert solver.convergence_iteration == events[-1].iteration - events[-1].stagnation_counter


def test_stagnation_iters_zero_disables_the_check() -> None:
    """`stagnation_iters=0` runs to the full iter-budget and reports `budget-exhausted`."""
    events: list[ProgressEvent] = []
    params = _params(iter_budget=20, stagnation_iters=0)
    solver = GraspSolver(
        _build_self_loop_graph(), params, np.random.default_rng(42), progress_callback=events.append
    )
    solver.run()

    assert solver.convergence_status == "budget-exhausted"
    assert len(events) == 20  # every iteration ran; no early stop


# ---------------------------------------------------------------------------
# Regression: the admission signal must come from `tracker.consider()`'s bool,
# NOT a top-N total-objective delta. The evict-many-admit-one branch
# (`TopNTracker`) can change the held set while leaving the total *unchanged*,
# so a delta-based signal would miss the admission entirely — wrongly counting
# it as stagnant and never advancing `convergence_iteration` past it.
#
#   P:  0 --(base (0,10,0))--> 10        objective 5   (dead-end)
#   Q:  1 --(base (1,11,0))--> 11        objective 8   (dead-end)
#   R:  2 --(base (0,10,0))--> 3 --(base (1,11,0))--> 4   objective 13
#
# With `j_max=0.0` any shared base segment counts as overlap. R shares P's base
# segment (its first edge) and Q's (its second), so R overlaps both. R's
# objective 13 strictly beats each of P (5) and Q (8), so when R is constructed
# after P and Q are already held it evicts BOTH and is admitted — and the held
# total moves 13 → 13 (unchanged: 5 + 8 == 13). Seed 3 is chosen so this
# evict-many is the run's LAST admission (verified deterministic).
# ---------------------------------------------------------------------------


def _build_evict_many_graph() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for u, v, d_plus, bid in (
        (0, 10, 5.0, (0, 10, 0)),  # P
        (1, 11, 8.0, (1, 11, 0)),  # Q
        (2, 3, 6.0, (0, 10, 0)),  # R first edge — shares P's base segment
        (3, 4, 7.0, (1, 11, 0)),  # R second edge — shares Q's base segment
    ):
        g.add_edge(
            u,
            v,
            key=0,
            length_m=10.0,
            d_plus_m=d_plus,
            d_minus_m=0.0,
            avg_gradient=d_plus / 10.0,
            sac_scale="hiking",
            base_segment_id=frozenset({bid}),
            reusable=False,
        )
    return ContractedGraph(graph=g, super_edge_to_base={})


def test_admission_with_unchanged_total_still_advances_convergence_iteration() -> None:
    """An evict-many admission that leaves the total unchanged is NOT stagnant.

    Drives the scenario where the final admission is an evict-many that keeps the
    top-N total objective bit-stable (13 → 13). `convergence_iteration` must point
    at that admission, and the iteration must show a *zero* total-objective delta
    — proving the solver keys off `tracker.consider()`'s verdict, not a
    total-objective change (the latter would have left `convergence_iteration`
    stuck at the earlier P+Q fill and counted the real admission as stagnant).
    """
    events: list[ProgressEvent] = []
    params = SolverParams(
        theta=_THETA,
        min_climb_slope=_THETA,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.0,  # any shared base segment overlaps → drives the evict-many branch
        n=3,
        area_cap=500.0,
        untagged_policy="include",
        seed=3,
        iter_budget=200,
        time_budget=3600.0,
        stagnation_iters=0,  # isolate the convergence-iteration signal from termination
    )
    solver = GraspSolver(
        _build_evict_many_graph(), params, np.random.default_rng(3), progress_callback=events.append
    )
    result = solver.run()

    # The evict-many winner R is the sole survivor → the eviction really happened.
    held = sorted(tuple((e.node_u, e.node_v, e.key) for e in sol.edges) for sol in result)
    assert held == [((2, 3, 0), (3, 4, 0))]

    ci = solver.convergence_iteration
    # The recorded admission iteration shows a zero total-objective delta: its
    # event's total equals the previous iteration's. A delta-based signal could
    # never set `convergence_iteration` here — only `consider()`'s bool can.
    assert events[ci - 1].best_objective == events[ci - 2].best_objective == 13.0
    # Pinned for this seed/graph: the equal-total evict-many lands on iteration 11.
    assert ci == 11
