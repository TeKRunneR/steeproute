# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: networkx operations on MultiDiGraph surface as Unknown; same boundary
# pattern as `pipeline/` modules and `tests/unit/test_graph_contraction.py`.
# `reportImplicitRelativeImport`: pytest's default `prepend` import mode (with
# no `__init__.py` files under `tests/`) puts each test file's parent dir on
# sys.path — `from exhaustive_oracle import ...` is the import shape that
# actually resolves at runtime. Relative imports fail because the test file
# isn't loaded as part of a package; the fully-qualified `tests.integration.*`
# fails because `tests/` is never on sys.path either.
"""Oracle-correctness tests (Story 3.5).

Pinned by PRD Appendix A: the brute-force enumerator used as the GRASP
reference (Story 3.7) is itself verified against 2-3 tiny handcrafted graphs
with known-by-inspection optima. Architecture §Cat 11c lists oracle correctness
as a CI gate, pass-required — `pytest.skip` / `xfail` are forbidden here.

Each fixture is constructed inline with an ASCII comment block documenting the
topology, per-edge metrics, and expected optimum the author derived by hand.
A reviewer should be able to validate the expected results from the comment
alone, without re-running the enumerator.
"""

from __future__ import annotations

import math
import time

import networkx as nx
from exhaustive_oracle import enumerate_best

from steeproute.models import ContractedGraph, Edge, SolverParams

# All fixtures share these params; `j_max=0.30` matches the PRD default and
# `theta=0.20` keeps super-edges with `avg_gradient >= 0.20` in-bounds.
_THETA = 0.20
_DIFFICULTY_CAP = "T3"
_J_MAX = 0.30


def _params(*, n: int = 1, theta: float = _THETA, j_max: float = _J_MAX) -> SolverParams:
    """Build a `SolverParams` carrying only the fields the oracle reads.

    The remaining fields (`seed`, `iter_budget`, etc.) are GRASP-only; the
    oracle ignores them. Defaults pin them to inert values so tests stay
    focused on the parameters under test.
    """
    return SolverParams(
        theta=theta,
        difficulty_cap=_DIFFICULTY_CAP,
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=j_max,
        n=n,
        area_cap=500.0,
        untagged_policy="include",
        seed=42,
        iter_budget=1000,
        time_budget=10.0,
        stagnation_iters=100,
    )


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
) -> Edge:
    """Add an edge to `g` carrying the post-stage-7 attribute contract.

    Returns the corresponding `Edge` value so super-edge fixtures can pass it
    into `super_edge_to_base`. `avg_gradient` is computed as
    `(d_plus_m + d_minus_m) / length_m` matching `pipeline.climbs`'s
    absolute-churn definition (stage 7).
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


def _edge_set(sol_edges: tuple[Edge, ...]) -> frozenset[tuple[int, int, int]]:
    """Canonical `(node_u, node_v, key)` set for a solution's edges."""
    return frozenset((e.node_u, e.node_v, e.key) for e in sol_edges)


def _assert_valid_walk(sol_edges: tuple[Edge, ...]) -> None:
    """Assert `sol_edges` is a non-empty edge-simple directed walk.

    Catches a class of `_dfs` bugs the set-membership / objective assertions
    would miss: a regression that produces the right canonical edge-set with
    the wrong traversal order or with a repeated `(u, v, key)` triple would
    leave `_edge_set(...)` and `Solution.objective` untouched. This helper
    pins the *structural* contract on the returned tuple.

    (1) `sol_edges` is non-empty (an empty `Solution` is illegal — `_dfs`
        only emits when `path_edges` is non-empty).
    (2) Consecutive edges share an endpoint: `prev.node_v == next.node_u`.
    (3) No `(node_u, node_v, key)` triple repeats (edge-simple-walk
        constraint enforced via `used_ids` in `_dfs`).
    """
    assert sol_edges, "Solution.edges must be non-empty"
    seen: set[tuple[int, int, int]] = set()
    for i, edge in enumerate(sol_edges):
        eid = (edge.node_u, edge.node_v, edge.key)
        assert eid not in seen, f"edge {eid} repeated at position {i} in {sol_edges}"
        seen.add(eid)
        if i > 0:
            prev = sol_edges[i - 1]
            assert prev.node_v == edge.node_u, (
                f"walk discontinuity at position {i}: prev edge ends at "
                f"{prev.node_v}, next edge starts at {edge.node_u}"
            )


# ---------------------------------------------------------------------------
# Fixture A — dominant single-climb path (5 nodes, 5 edges)
# ---------------------------------------------------------------------------
#
#   0 --A1--> 1 --A2--> 2 --A3--> 3 --conn--> 4
#    \                                       ^
#     `------------- bypass ---------------'
#
# Edges (all directed):
#   A1: 0→1   super,  len=400, d+=200, d-=0     (avg_gradient=0.500)
#   A2: 1→2   super,  len=600, d+=300, d-=0     (avg_gradient=0.500)
#   A3: 2→3   super,  len=300, d+=150, d-=0     (avg_gradient=0.500)
#   conn: 3→4 long connector, len=500, d+=20, d-=20
#   bypass: 0→4 long connector, len=1000, d+=0, d-=0
#
# Expected top-1 (n=1, j_max=0.30):
#   route = 0→1→2→3→4 via A1+A2+A3+conn
#   objective = (200+0) + (300+0) + (150+0) + (20+20) = 690.0
# No competing route comes close: any subset drops a climb or the conn,
# any path through `bypass` skips all three climbs.


def _build_fixture_a() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    a1 = _add_edge(g, 0, 1, length_m=400.0, d_plus_m=200.0)
    a2 = _add_edge(g, 1, 2, length_m=600.0, d_plus_m=300.0)
    a3 = _add_edge(g, 2, 3, length_m=300.0, d_plus_m=150.0)
    _add_edge(g, 3, 4, length_m=500.0, d_plus_m=20.0, d_minus_m=20.0)
    _add_edge(g, 0, 4, length_m=1000.0, d_plus_m=0.0, d_minus_m=0.0)
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {
        (0, 1, 0): (a1,),
        (1, 2, 0): (a2,),
        (2, 3, 0): (a3,),
    }
    return ContractedGraph(graph=g, super_edge_to_base=super_edge_to_base)


def test_enumerate_best_finds_dominant_climb_path_in_chain() -> None:
    """Fixture A: linear climb-rich chain dominates; top-1 == full chain."""
    graph = _build_fixture_a()
    params = _params(n=1)

    result = enumerate_best(graph, params, n=1)

    assert len(result) == 1
    expected_edges = frozenset({(0, 1, 0), (1, 2, 0), (2, 3, 0), (3, 4, 0)})
    assert _edge_set(result[0].edges) == expected_edges
    assert math.isclose(result[0].objective, 690.0, abs_tol=1e-9)
    _assert_valid_walk(result[0].edges)


# ---------------------------------------------------------------------------
# Fixture B — two structurally-distinct, equal-objective paths (5 nodes, 4 edges)
# ---------------------------------------------------------------------------
#
#         B1 (super)         B2 (super)
#     0 ---------> 1 ---------> 2
#      \
#       \ B3 (super)         B4 (super)
#        `-------> 3 ---------> 4
#
# Edges:
#   B1: 0→1   super, len=200, d+=100, d-=0   (avg_gradient=0.500)
#   B2: 1→2   super, len=400, d+=200, d-=0   (avg_gradient=0.500)
#   B3: 0→3   super, len=400, d+=200, d-=0   (avg_gradient=0.500)
#   B4: 3→4   super, len=200, d+=100, d-=0   (avg_gradient=0.500)
#
# Expected top-2 (n=2, j_max=0.30 — permissive enough for fully disjoint
# routes, threshold = 1 - 0.30 = 0.70):
#   path P1 = 0→1→2  via B1+B2,  objective = 100 + 200 = 300.0
#   path P2 = 0→3→4  via B3+B4,  objective = 200 + 100 = 300.0
# Edge-sets are disjoint → jaccard_distance = 1.0 ≥ 0.70 → both admitted.
# Deterministic tie-break (`(-objective, sorted_edge_ids)`):
#   sorted_edge_ids(P1) = ((0,1,0), (1,2,0)) < ((0,3,0), (3,4,0)) = sorted_edge_ids(P2)
# so P1 sorts before P2.


def _build_fixture_b() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    b1 = _add_edge(g, 0, 1, length_m=200.0, d_plus_m=100.0)
    b2 = _add_edge(g, 1, 2, length_m=400.0, d_plus_m=200.0)
    b3 = _add_edge(g, 0, 3, length_m=400.0, d_plus_m=200.0)
    b4 = _add_edge(g, 3, 4, length_m=200.0, d_plus_m=100.0)
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {
        (0, 1, 0): (b1,),
        (1, 2, 0): (b2,),
        (0, 3, 0): (b3,),
        (3, 4, 0): (b4,),
    }
    return ContractedGraph(graph=g, super_edge_to_base=super_edge_to_base)


def test_enumerate_best_returns_two_distinct_paths_under_permissive_jmax() -> None:
    """Fixture B: two disjoint top routes; top-2 returns both, sorted deterministically."""
    graph = _build_fixture_b()
    params = _params(n=2)

    result = enumerate_best(graph, params, n=2)

    assert len(result) == 2
    edge_sets = [_edge_set(s.edges) for s in result]
    assert edge_sets[0] == frozenset({(0, 1, 0), (1, 2, 0)})
    assert edge_sets[1] == frozenset({(0, 3, 0), (3, 4, 0)})
    assert math.isclose(result[0].objective, 300.0, abs_tol=1e-9)
    assert math.isclose(result[1].objective, 300.0, abs_tol=1e-9)
    for sol in result:
        _assert_valid_walk(sol.edges)


# ---------------------------------------------------------------------------
# Fixture C — disjoint-component graph (7 nodes, 5 edges)
# ---------------------------------------------------------------------------
#
#   Component 1:  0 --C1--> 1 --C2--> 2 --C3--> 3
#   Component 2:  4 --C4--> 5 --C5--> 6
#
# Edges:
#   C1: 0→1   super, len=200, d+=100, d-=0    (avg_gradient=0.500)
#   C2: 1→2   super, len=400, d+=200, d-=0    (avg_gradient=0.500)
#   C3: 2→3   super, len=300, d+=150, d-=0    (avg_gradient=0.500)
#   C4: 4→5   super, len=300, d+=120, d-=0    (avg_gradient=0.400)
#   C5: 5→6   super, len=200, d+=80,  d-=0    (avg_gradient=0.400)
#
# Expected top-2 (n=2, j_max=0.30):
#   dominant: 0→1→2→3 (full Component-1 chain), objective = 100+200+150 = 450.0
#   next:     4→5→6   (full Component-2 chain), objective = 120+80      = 200.0
# Cross-component disjointness → distance 1.0 ≥ 0.70 → both admitted.
# Sub-chains within Component 1 overlap the dominant path (e.g. 1→2→3 shares
# 2 of 3 edges with the dominant route → distance = 1 - 2/3 ≈ 0.333 < 0.70),
# so they cannot displace it for the second slot. The Component-2 chain is
# the highest-objective disjoint candidate.


def _build_fixture_c() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    c1 = _add_edge(g, 0, 1, length_m=200.0, d_plus_m=100.0)
    c2 = _add_edge(g, 1, 2, length_m=400.0, d_plus_m=200.0)
    c3 = _add_edge(g, 2, 3, length_m=300.0, d_plus_m=150.0)
    c4 = _add_edge(g, 4, 5, length_m=300.0, d_plus_m=120.0)
    c5 = _add_edge(g, 5, 6, length_m=200.0, d_plus_m=80.0)
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {
        (0, 1, 0): (c1,),
        (1, 2, 0): (c2,),
        (2, 3, 0): (c3,),
        (4, 5, 0): (c4,),
        (5, 6, 0): (c5,),
    }
    return ContractedGraph(graph=g, super_edge_to_base=super_edge_to_base)


def test_enumerate_best_picks_top_route_from_each_disjoint_component() -> None:
    """Fixture C (7 nodes): top-2 = dominant chain of each disjoint component."""
    graph = _build_fixture_c()
    params = _params(n=2)

    result = enumerate_best(graph, params, n=2)

    assert len(result) == 2
    assert _edge_set(result[0].edges) == frozenset({(0, 1, 0), (1, 2, 0), (2, 3, 0)})
    assert _edge_set(result[1].edges) == frozenset({(4, 5, 0), (5, 6, 0)})
    assert math.isclose(result[0].objective, 450.0, abs_tol=1e-9)
    assert math.isclose(result[1].objective, 200.0, abs_tol=1e-9)
    for sol in result:
        _assert_valid_walk(sol.edges)


# ---------------------------------------------------------------------------
# Fixture D — parallel multi-edges (2 nodes, 2 edges sharing endpoints)
# ---------------------------------------------------------------------------
#
#         D1 (super, key=0)
#     0 ===================> 1
#         D2 (super, key=1)
#     0 ===================> 1
#
# Edges:
#   D1: 0→1   super, key=0, len=200, d+=100, d-=0   (avg_gradient=0.500)
#   D2: 0→1   super, key=1, len=300, d+=150, d-=0   (avg_gradient=0.500)
#
# Both edges share `(node_u, node_v) = (0, 1)` and are distinguished only by
# the networkx multi-edge `key`. Production `contract_climbs` deliberately
# emits parallel keys when a connector and a super-edge share endpoints
# (`pipeline/graph.py::_next_key_for`), so the `(u, v, key)` granularity in
# the oracle's dedup, `used_ids` tracking, and `super_edge_to_base` lookup
# must distinguish them.
#
# Expected top-2 (n=2, j_max=0.30):
#   Each parallel edge is a single-edge walk on its own. Their edge-sets
#   `{(0,1,0)}` and `{(0,1,1)}` are disjoint by key, so
#   jaccard_distance = 1.0 ≥ 0.70 → both admitted.
#   Sorted by `(-objective, sorted_edge_ids)`:
#     D2 alone (obj 150) sorts first; D1 alone (obj 100) sorts second.
# A regression that keyed `used_ids` or the dedup-set on `(u, v)` instead of
# `(u, v, key)` would either collapse them into one candidate or fail to
# enumerate the second walk at all.


def _build_fixture_d() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    d1 = _add_edge(g, 0, 1, length_m=200.0, d_plus_m=100.0, key=0)
    d2 = _add_edge(g, 0, 1, length_m=300.0, d_plus_m=150.0, key=1)
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {
        (0, 1, 0): (d1,),
        (0, 1, 1): (d2,),
    }
    return ContractedGraph(graph=g, super_edge_to_base=super_edge_to_base)


def test_enumerate_best_distinguishes_parallel_edges_by_key() -> None:
    """Fixture D: two parallel `(0,1)` edges with different `key`s → two distinct candidates."""
    graph = _build_fixture_d()
    params = _params(n=2)

    result = enumerate_best(graph, params, n=2)

    assert len(result) == 2
    assert _edge_set(result[0].edges) == frozenset({(0, 1, 1)})
    assert _edge_set(result[1].edges) == frozenset({(0, 1, 0)})
    assert math.isclose(result[0].objective, 150.0, abs_tol=1e-9)
    assert math.isclose(result[1].objective, 100.0, abs_tol=1e-9)
    for sol in result:
        _assert_valid_walk(sol.edges)


# ---------------------------------------------------------------------------
# Pathological inputs (AC #3)
# ---------------------------------------------------------------------------


def test_enumerate_best_returns_empty_on_empty_graph() -> None:
    """No edges → no candidate routes → empty result."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    graph = ContractedGraph(graph=g, super_edge_to_base={})

    result = enumerate_best(graph, _params(n=3), n=3)

    assert result == []


def test_enumerate_best_returns_empty_on_isolated_nodes_with_no_edges() -> None:
    """Isolated nodes (no edges) → outer DFS loop runs but emits nothing.

    Complements the empty-graph case (which never enters the outer loop) by
    exercising the inner DFS path: for each isolated node, `_dfs` is invoked
    with `path_edges=[]` and no outgoing edges to recurse into, so the
    empty-`path_edges` early-return branch executes and `candidates` stays
    empty. A regression that, say, emitted a phantom solution on the empty
    prefix would surface here but not in `test_enumerate_best_returns_empty_on_empty_graph`.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0)
    g.add_node(1)
    g.add_node(2)
    graph = ContractedGraph(graph=g, super_edge_to_base={})

    result = enumerate_best(graph, _params(n=3), n=3)

    assert result == []


def test_enumerate_best_returns_empty_when_every_edge_violates_sac_cap() -> None:
    """All edges above SAC cap → no feasible path → empty result.

    Cap is T3; all edges are tagged `difficult_alpine_hiking` (rank 6, > 3).
    Every walk in the graph is therefore infeasible at every depth.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    _add_edge(g, 0, 1, length_m=200.0, d_plus_m=100.0, sac_scale="difficult_alpine_hiking")
    _add_edge(g, 1, 2, length_m=200.0, d_plus_m=100.0, sac_scale="difficult_alpine_hiking")
    graph = ContractedGraph(graph=g, super_edge_to_base={})

    result = enumerate_best(graph, _params(n=2), n=2)

    assert result == []


def test_enumerate_best_returns_fewer_than_n_when_few_feasible_paths_exist() -> None:
    """Graph with strictly fewer feasible candidates than n → returns what exists.

    A single super-edge admits exactly one route (itself). Asking for n=5
    should yield a length-1 result, not raise.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    e = _add_edge(g, 0, 1, length_m=200.0, d_plus_m=100.0)
    graph = ContractedGraph(graph=g, super_edge_to_base={(0, 1, 0): (e,)})

    result = enumerate_best(graph, _params(n=5), n=5)

    assert len(result) == 1
    assert _edge_set(result[0].edges) == frozenset({(0, 1, 0)})
    assert math.isclose(result[0].objective, 100.0, abs_tol=1e-9)


def test_enumerate_best_drops_super_edges_below_theta() -> None:
    """Super-edges with `avg_gradient < theta` are infeasible; oracle ignores them.

    The only super-edge has avg_gradient 0.05 (< theta 0.20). No other edges
    exist, so no feasible path → empty result. This case isolates the
    slope-floor branch from the SAC-cap branch: `sac_scale=None` deliberately
    side-steps the SAC filter (`max_sac_rank(None)` returns `None`, which the
    oracle treats as "admit" — same posture as `filter_trails` upstream), so
    the only filter that can return `[]` here is the θ check.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    # d+=10, d-=0, len=200 → avg_gradient = 0.05; `sac_scale=None` rules out
    # the SAC filter so the test is unambiguously a θ-filter test.
    e = _add_edge(g, 0, 1, length_m=200.0, d_plus_m=10.0, sac_scale=None)
    graph = ContractedGraph(graph=g, super_edge_to_base={(0, 1, 0): (e,)})

    result = enumerate_best(graph, _params(n=1, theta=0.20), n=1)

    assert result == []


def test_enumerate_best_admits_super_edge_at_theta_boundary() -> None:
    """Strict `<` boundary: a super-edge with `avg_gradient == theta` is admitted.

    Pins the comparison-operator contract: `_dfs` uses `avg_gradient < theta`
    as the *drop* condition, so equality is a pass. A future flip to `<=`
    (or a production-side flip from `>=` to `>` for the equivalent
    inclusion check) would silently change which super-edges qualify;
    this test catches that.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    # d+=40, d-=0, len=200 → avg_gradient = 0.20 = theta exactly.
    e = _add_edge(g, 0, 1, length_m=200.0, d_plus_m=40.0, sac_scale=None)
    graph = ContractedGraph(graph=g, super_edge_to_base={(0, 1, 0): (e,)})

    result = enumerate_best(graph, _params(n=1, theta=0.20), n=1)

    assert len(result) == 1
    assert _edge_set(result[0].edges) == frozenset({(0, 1, 0)})
    assert math.isclose(result[0].objective, 40.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Timing bound (AC #4)
# ---------------------------------------------------------------------------


def test_enumerate_best_completes_under_one_second_on_five_node_fixture() -> None:
    """Fixture A (5 nodes, 5 edges) enumerates in < 1 s.

    Architecture §Cat 11c — the oracle is for *toy* graphs; if a 5-node
    instance takes longer than this, the algorithm has regressed into the
    exponential blowup the size cap is meant to avoid.
    """
    graph = _build_fixture_a()
    params = _params(n=3)

    t0 = time.perf_counter()
    enumerate_best(graph, params, n=3)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, f"expected enumeration under 1 s, took {elapsed:.3f} s"
