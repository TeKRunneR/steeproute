# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same networkx-boundary pattern as tests/integration/test_oracle_correctness.py.
"""GRASP construction unit tests (Story 3.6 AC #4).

Exercises the three things the solver-core must guarantee in isolation:

1. Per-iteration determinism under a seeded `numpy.random.Generator` — the
   foundation FR29 (`tests/integration/test_grasp_reproducible.py`) builds on.
2. Constructor / `best_so_far` shape — readable before `run()` is called and
   returns `[]`.
3. The route-level slope floor (θ on the whole-route average, enforced at
   finalization) and the SAC-cap RCL filter — one directed test each, on
   hand-built graphs where the only-correct outcome is unambiguous by inspection.

All graphs constructed inline with an explanatory comment block; see
`tests/integration/test_oracle_correctness.py` for the established pattern.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pytest

from steeproute.models import ContractedGraph, Edge, SolverParams
from steeproute.solver.grasp import GraspSolver


def _params(
    *,
    n: int = 3,
    theta: float = 0.20,
    j_max: float = 0.30,
    iter_budget: int = 20,
    difficulty_cap: str = "T3",
) -> SolverParams:
    """Build a `SolverParams` carrying only the fields these tests exercise.

    The §Cat 5e budgets are pinned non-binding so iter-budget is the sole
    terminator: `stagnation_iters=0` disables stagnation (Story 7.2 made it
    live), and `time_budget=10.0` dwarfs these tiny hand-built-graph runs.
    """
    return SolverParams(
        theta=theta,
        min_climb_slope=theta,
        difficulty_cap=difficulty_cap,
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=j_max,
        n=n,
        area_cap=500.0,
        untagged_policy="include",
        seed=42,
        iter_budget=iter_budget,
        time_budget=10.0,
        stagnation_iters=0,
    )


def _seg(u: int, v: int, key: int = 0) -> tuple[int, int, int]:
    """Undirected base-segment id (canonical sorted node-pair + key), per Story 5.1."""
    return (min(u, v), max(u, v), key)


def _add_edge(
    g: nx.MultiDiGraph,
    u: int,
    v: int,
    *,
    length_m: float,
    d_plus_m: float,
    d_minus_m: float = 0.0,
    sac_scale: str | None = "hiking",
    key: int = 0,
    base_segment_id: frozenset[tuple[int, int, int]] | None = None,
    reusable: bool = False,
) -> Edge:
    """Add an edge carrying the post-stage-7 attribute contract + Story 5.1 reuse tags.

    `base_segment_id` defaults to the edge's own undirected id and `reusable` to
    `False` (a non-exempt segment) — the common case. Tests probing the
    undirected-reuse / short-connector-exemption rule (Story 5.2) override them:
    a reverse-of-climb connector reuses the climb's `base_segment_id`; a short
    linking connector passes `reusable=True`.
    """
    avg_gradient = (d_plus_m + d_minus_m) / length_m
    g.add_edge(
        u,
        v,
        key=key,
        length_m=length_m,
        d_plus_m=d_plus_m,
        d_minus_m=d_minus_m,
        avg_gradient=avg_gradient,
        sac_scale=sac_scale,
        base_segment_id=base_segment_id
        if base_segment_id is not None
        else frozenset({_seg(u, v, key)}),
        reusable=reusable,
    )
    return Edge(
        node_u=u,
        node_v=v,
        key=key,
        length_m=length_m,
        d_plus_m=d_plus_m,
        d_minus_m=d_minus_m,
        avg_gradient=avg_gradient,
        sac_scale=sac_scale,
    )


def _edge_ids(sol_edges: tuple[Edge, ...]) -> tuple[tuple[int, int, int], ...]:
    return tuple((e.node_u, e.node_v, e.key) for e in sol_edges)


# ---------------------------------------------------------------------------
# Fixture A — single dominant climb chain (4 nodes, 3 super-edges).
# ---------------------------------------------------------------------------
#
#   0 --A1--> 1 --A2--> 2 --A3--> 3
#
# A1: 0→1 super, len=400, d+=200, d-=0  (avg_gradient=0.500)
# A2: 1→2 super, len=600, d+=300, d-=0  (avg_gradient=0.500)
# A3: 2→3 super, len=300, d+=150, d-=0  (avg_gradient=0.500)
#
# Every node has at most one outgoing edge → the RCL has size <= 1 at each
# step → GRASP construction is fully deterministic regardless of RNG state.
# Useful both as a "the solver actually returns something" smoke check and as
# a reproducibility base.


def _build_chain_fixture() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    a1 = _add_edge(g, 0, 1, length_m=400.0, d_plus_m=200.0)
    a2 = _add_edge(g, 1, 2, length_m=600.0, d_plus_m=300.0)
    a3 = _add_edge(g, 2, 3, length_m=300.0, d_plus_m=150.0)
    return ContractedGraph(
        graph=g,
        super_edge_to_base={
            (0, 1, 0): (a1,),
            (1, 2, 0): (a2,),
            (2, 3, 0): (a3,),
        },
    )


def test_grasp_run_is_deterministic_under_same_seed() -> None:
    """AC #4(a): two `default_rng(42)` runs produce identical `Solution.edges` tuples.

    Foundation for the FR29 integration test in
    `tests/integration/test_grasp_reproducible.py` — the unit-level claim is
    that GRASP construction is fully deterministic given identical seed + graph
    + params (no ambient RNG state leaks in).
    """
    graph = _build_chain_fixture()
    params = _params(iter_budget=5, n=3)

    solver_a = GraspSolver(graph, params, np.random.default_rng(42))
    solver_b = GraspSolver(graph, params, np.random.default_rng(42))
    result_a = solver_a.run()
    result_b = solver_b.run()

    assert len(result_a) == len(result_b)
    for sol_a, sol_b in zip(result_a, result_b, strict=True):
        assert _edge_ids(sol_a.edges) == _edge_ids(sol_b.edges)
        # Raw `==` (not `math.isclose`) is deliberate here: FR29 promises
        # *byte-identical* reproducibility, so the objectives must be
        # bit-for-bit equal. `math.isclose` would mask exactly the drift this
        # test exists to catch — it is the one place the "never `==` on floats"
        # standard does not apply.
        assert sol_a.objective == sol_b.objective


def test_grasp_best_so_far_is_readable_before_run() -> None:
    """AC #4(b): `best_so_far` is `[]` after constructor, before `run()` is called."""
    graph = _build_chain_fixture()
    params = _params(iter_budget=5, n=3)

    solver = GraspSolver(graph, params, np.random.default_rng(42))

    assert solver.best_so_far == []


def test_grasp_best_so_far_reflects_run_results() -> None:
    """AC #4(b): after `run()`, `best_so_far` returns the same list `run()` returned.

    `best_so_far` is `tracker.current_top()` — same admission state any time
    after the last `consider(...)` call.
    """
    graph = _build_chain_fixture()
    params = _params(iter_budget=5, n=3)

    solver = GraspSolver(graph, params, np.random.default_rng(42))
    result = solver.run()

    assert solver.best_so_far == result
    assert result, "chain fixture should yield at least one route"


# ---------------------------------------------------------------------------
# Fixture B — route-level slope-floor probe (3 nodes, 2 super-edges from node 0).
# ---------------------------------------------------------------------------
#
#       B_pass route (above θ)
#   0 -------------------------> 1     (dead-end)
#    \
#     `--B_fail route (below θ)--> 2   (dead-end)
#
# B_pass: 0→1, len=400, d+=200, d-=0  (route avg (D+ + D−)/length = 0.500 ≥ θ=0.20)
# B_fail: 0→2, len=1000, d+=100, d-=0 (route avg = 0.100 < θ=0.20)
#
# Both are single-edge dead-end routes, so the whole-route average equals the
# edge's own gradient. With higher `iter_budget` every start node is sampled;
# in every case the B_fail route is rejected by `_route_slope_ok` at
# finalization (NOT by any RCL membership filter — that no longer exists, Story
# 4.2), so `(0, 2, 0)` must NEVER appear in any returned route.


def _build_slope_floor_fixture() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    b_pass = _add_edge(g, 0, 1, length_m=400.0, d_plus_m=200.0)
    b_fail = _add_edge(g, 0, 2, length_m=1000.0, d_plus_m=100.0)
    return ContractedGraph(
        graph=g,
        super_edge_to_base={
            (0, 1, 0): (b_pass,),
            (0, 2, 0): (b_fail,),
        },
    )


def test_grasp_discards_routes_below_route_level_theta() -> None:
    """FR3: the finalization gate discards a sub-θ route; an at/above-θ route is admitted.

    `B_fail`'s whole-route average is `0.10 < θ=0.20`, so `_route_slope_ok`
    rejects it before it reaches the tracker — `(0, 2, 0)` must never appear in
    any returned route. `B_pass` (route avg `0.50 ≥ θ`) is admitted. Run enough
    iterations that every start node is sampled.
    """
    graph = _build_slope_floor_fixture()
    params = _params(iter_budget=50, n=3)
    solver = GraspSolver(graph, params, np.random.default_rng(42))

    result = solver.run()

    all_edge_ids = {(e.node_u, e.node_v, e.key) for sol in result for e in sol.edges}
    assert (0, 2, 0) not in all_edge_ids, (
        f"B_fail (sub-θ route) must not appear in any route; got {all_edge_ids}"
    )
    # Sanity: B_pass should appear (otherwise the test would pass vacuously
    # because the result was empty).
    assert (0, 1, 0) in all_edge_ids


# ---------------------------------------------------------------------------
# Fixture C — SAC-cap filter probe (3 nodes, 2 outgoing connectors from 0).
# ---------------------------------------------------------------------------
#
#   0 --C_pass (sac=hiking, rank 1)--> 1   (dead-end)
#    \
#     `-C_fail (sac=demanding_alpine_hiking, rank 5)-> 2   (dead-end)
#
# Cap = "T3" → cap_rank = 3. C_fail's `demanding_alpine_hiking` ranks 5 > 3 →
# must be filtered. Both are plain connectors (not super-edges), so the slope
# floor is not in play — this isolates the SAC branch from the θ branch.
# C_pass carries non-zero D+ so it's a candidate the greedy would otherwise
# pick.


def _build_sac_cap_fixture() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    _add_edge(g, 0, 1, length_m=400.0, d_plus_m=80.0, sac_scale="hiking")
    _add_edge(g, 0, 2, length_m=400.0, d_plus_m=200.0, sac_scale="demanding_alpine_hiking")
    return ContractedGraph(graph=g, super_edge_to_base={})


def test_grasp_rcl_excludes_edges_above_sac_cap() -> None:
    """AC #4(c) SAC-cap branch: edges ranking above `difficulty_cap` are filtered.

    Even though `C_fail` carries the higher D+ contribution (200 vs 80), it
    must never appear in any route because its `sac_scale` ranks above the
    `T3` cap. `C_pass` (sac=hiking, rank 1) is the only admissible extension
    from node 0 — every non-empty route starting at 0 ends with exactly that
    edge.
    """
    graph = _build_sac_cap_fixture()
    params = _params(iter_budget=50, n=3, difficulty_cap="T3")
    solver = GraspSolver(graph, params, np.random.default_rng(42))

    result = solver.run()

    all_edge_ids = {(e.node_u, e.node_v, e.key) for sol in result for e in sol.edges}
    assert (0, 2, 0) not in all_edge_ids, (
        f"C_fail (above-cap edge) must not appear in any route; got {all_edge_ids}"
    )
    assert (0, 1, 0) in all_edge_ids


# ---------------------------------------------------------------------------
# Fixture D — undirected base-segment reuse: out-and-back over a climb (Story 5.2).
# ---------------------------------------------------------------------------
#
#   0 ==climb (super)==> 1
#   1 --short reverse connector--> 0
#
# The climb 0→1 (super-edge, reusable=False) and the short reverse connector 1→0
# (reusable=True, length 100 < l_connector 200) share base_segment_id {(0,1,0)} —
# the connector is the reverse of the climb's own trail. This is exactly the
# short-edge-climb class deferred from Story 5.1: the connector is `reusable`
# per-edge, but its id is non-exempt (carried by the non-reusable super-edge), so
# per-id exemption forbids descending it after ascending the climb. No route may
# contain both — the degenerate out-and-back is rejected by construction.


def _build_out_and_back_fixture() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    climb = _add_edge(
        g, 0, 1, length_m=400.0, d_plus_m=200.0, base_segment_id=frozenset({(0, 1, 0)})
    )
    # Reverse of the climb's trail: short (< l_connector) → reusable per-edge, but
    # shares the climb's base id, so it is non-exempt for the once-only rule.
    _add_edge(
        g,
        1,
        0,
        length_m=100.0,
        d_plus_m=0.0,
        d_minus_m=200.0,
        base_segment_id=frozenset({(0, 1, 0)}),
        reusable=True,
    )
    return ContractedGraph(graph=g, super_edge_to_base={(0, 1, 0): (climb,)})


def test_grasp_rejects_out_and_back_over_a_climb() -> None:
    """Story 5.2: ascending a climb forbids descending its reverse (per-id exemption).

    The climb and its short reverse connector share a base segment that is
    non-exempt (the super-edge carrying it is not reusable). So no returned route
    may contain both `(0, 1, 0)` (climb) and `(1, 0, 0)` (reverse) — the
    out-and-back is killed at the source. The climb itself must still appear
    (non-vacuity guard).

    Seed 44 is tuned to the Story 12.3 batched-draw sequence: the climb route and
    the reverse-connector route have EQUAL objectives (200 m each) and overlap
    (same base id), so whichever is constructed first is held forever — the
    non-vacuity guard needs the very first start-node draw to land on node 0
    (`default_rng(44).random(1024)[0] ≈ 0.123 → int(u * 2) == 0`; the pre-12.3
    seed 42's first `integers` draw did the same).
    """
    graph = _build_out_and_back_fixture()
    params = _params(iter_budget=50, n=3)
    solver = GraspSolver(graph, params, np.random.default_rng(44))

    result = solver.run()

    for sol in result:
        ids = set(_edge_ids(sol.edges))
        assert not ({(0, 1, 0), (1, 0, 0)} <= ids), (
            f"out-and-back over the climb must be rejected; got route {ids}"
        )
    all_ids = {eid for sol in result for eid in _edge_ids(sol.edges)}
    assert (0, 1, 0) in all_ids, "the climb should still be reachable as a route"


# ---------------------------------------------------------------------------
# Fixture E — a genuinely-exempt short connector may recur (Story 5.2).
# ---------------------------------------------------------------------------
#
#   0 <==short connector (both directions)==> 1
#
# Both directed connectors are reusable and share base_segment_id {(0,1,0)}, and
# NO non-reusable edge carries that id → it is reuse-exempt. So a route may
# legitimately traverse the linking segment in both directions (0→1→0). The
# directed-edge-simple bound still caps it at once per direction, guaranteeing
# termination. Both half-edges carry enough vertical to clear θ on the round trip.


def _build_exempt_connector_loop_fixture() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    _add_edge(
        g,
        0,
        1,
        length_m=100.0,
        d_plus_m=30.0,
        base_segment_id=frozenset({(0, 1, 0)}),
        reusable=True,
    )
    _add_edge(
        g,
        1,
        0,
        length_m=100.0,
        d_plus_m=0.0,
        d_minus_m=30.0,
        base_segment_id=frozenset({(0, 1, 0)}),
        reusable=True,
    )
    return ContractedGraph(graph=g, super_edge_to_base={})


def test_grasp_allows_exempt_connector_in_both_directions() -> None:
    """Story 5.2: an exempt short connector may recur (traversed both directions).

    Its base id is carried only by reusable edges, so it never blocks the
    once-only rule; the route `0→1→0` reuses the linking segment legitimately.
    A returned route therefore contains both `(0, 1, 0)` and `(1, 0, 0)`.
    """
    graph = _build_exempt_connector_loop_fixture()
    params = _params(iter_budget=20, n=3, theta=0.20)
    solver = GraspSolver(graph, params, np.random.default_rng(42))

    result = solver.run()

    assert any({(0, 1, 0), (1, 0, 0)} <= set(_edge_ids(sol.edges)) for sol in result), (
        "an exempt short connector should be traversable in both directions in one route"
    )


def test_grasp_returns_empty_on_empty_graph() -> None:
    """Pathological: zero-node graph → `run()` returns `[]` without error.

    Exercises the `if not self._nodes` guard in `run()` (the zero-node branch).
    """
    graph = ContractedGraph(graph=nx.MultiDiGraph(), super_edge_to_base={})
    params = _params(iter_budget=10)
    solver = GraspSolver(graph, params, np.random.default_rng(42))

    assert solver.run() == []
    assert solver.best_so_far == []


def test_grasp_returns_empty_on_isolated_nodes_with_no_edges() -> None:
    """`_nodes` non-empty but every walk is empty → tracker never admits anything.

    Distinct from the zero-node case: here the outer guard `if not self._nodes`
    is False (there ARE nodes), so the loop runs `iter_budget` times, each
    `_construct_one` samples a start node, `_build_rcl` returns `[]`
    immediately, and the empty walk is discarded by `run()`'s `if
    solution.edges:` guard before reaching the tracker. Pins that guard — a
    regression dropping it would push `Solution(edges=(), objective=0.0)` into
    `TopNTracker` (which accepts a finite `0.0` objective) and pollute
    `current_top()`.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0)
    g.add_node(1)
    g.add_node(2)
    graph = ContractedGraph(graph=g, super_edge_to_base={})
    params = _params(iter_budget=20)
    solver = GraspSolver(graph, params, np.random.default_rng(42))

    assert solver.run() == []
    assert solver.best_so_far == []


def test_grasp_admits_self_loop_super_edge_as_single_edge_route() -> None:
    """Self-loop super-edge `(u, u, k)` is a valid length-1 edge-simple walk.

    Documents the deliberate semantic (see `_construct_one` docstring): OSM
    self-loops (lollipop trail-ends, roundabouts) are real, and the solver
    admits them rather than special-casing. After traversing the self-loop the
    walk terminates (its only outgoing edge is now in `used_ids`). Any policy
    on rejecting them is the Story 3.9 validator's job, not the solver's.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    loop = _add_edge(g, 0, 0, length_m=400.0, d_plus_m=200.0)
    graph = ContractedGraph(graph=g, super_edge_to_base={(0, 0, 0): (loop,)})
    params = _params(iter_budget=10, n=3)
    solver = GraspSolver(graph, params, np.random.default_rng(42))

    result = solver.run()

    assert len(result) == 1
    assert _edge_ids(result[0].edges) == ((0, 0, 0),)


def test_grasp_rejects_non_positive_iter_budget() -> None:
    """`iter_budget < 1` fails loud at construction, symmetric with `TopNTracker`.

    A 0/negative budget would otherwise make `run()` silently return `[]`,
    indistinguishable from "searched and found nothing".
    """
    graph = _build_chain_fixture()
    for bad_budget in (0, -1):
        params = _params(iter_budget=bad_budget)
        with pytest.raises(ValueError, match="iter_budget must be >= 1"):
            GraspSolver(graph, params, np.random.default_rng(42))


def test_anytime_module_imports() -> None:
    """`solver.anytime` is a Story 3.6 stub for Epic 4 — just verify it imports.

    AC #3: keep the import surface stable for Story 4.3. This test catches a
    regression where the module accidentally grows a syntax error or a broken
    dependency before Epic 4 fleshes it out. Asserts `__all__` is a list (not
    that it is empty) so legitimate Epic 4 exports do not trip this test.
    """
    from steeproute.solver import anytime  # noqa: PLC0415

    assert isinstance(anytime.__all__, list)
