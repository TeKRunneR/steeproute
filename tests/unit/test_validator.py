# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same networkx-boundary pattern as tests/unit/test_grasp_construction.py.
"""Unit tests for `validator.py` (Story 3.9).

One crafted-violating + one crafted-clean fixture per constraint (PRD
structural requirement), asserting the right `constraint_id` and the
`observed`/`required` numerics. Per-route constraints are exercised through
`validate_route`; the set-level Jaccard constraint through `validate_set`; the
`Solution → Route` conversion and orchestration through `validate`.

All graphs are hand-built `MultiDiGraph`s wrapped in a `ContractedGraph`, with
`super_edge_to_base` naming exactly the edges that should be treated as
non-connector climbs — the same shape `solver/grasp.py` consumes.

Coverage map (AC → test):
- AC #2 slope_floor / difficulty_cap / edge_reuse / graph_membership
  → the four `test_validate_route_*` violating+clean pairs.
- AC #3 pairwise Jaccard → `test_validate_set_*`.
- AC #4 orchestration / metrics / ordering → `test_validate_*`.
- AC #1 purity → `test_validate_route_does_not_mutate_inputs`.
"""

from __future__ import annotations

import networkx as nx
import pytest

from steeproute.models import (
    ContractedGraph,
    Edge,
    Route,
    RouteMetrics,
    RouteValidation,
    Solution,
    SolverParams,
)
from steeproute.validator import validate, validate_route, validate_set

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

_THETA = 0.20
_DIFFICULTY_CAP = "T3"  # rank 3
_J_MAX = 0.30


def _params(
    *,
    theta: float = _THETA,
    j_max: float = _J_MAX,
    start_at_junction: bool = False,
    max_descent_slope: float | None = None,
) -> SolverParams:
    """`SolverParams` carrying only the fields the validator reads."""
    return SolverParams(
        theta=theta,
        min_climb_slope=theta,
        difficulty_cap=_DIFFICULTY_CAP,
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=j_max,
        n=3,
        area_cap=500.0,
        untagged_policy="include",
        seed=42,
        iter_budget=100,
        time_budget=60.0,
        stagnation_iters=0,
        start_at_junction=start_at_junction,
        max_descent_slope=max_descent_slope,
    )


def _edge(
    u: int,
    v: int,
    *,
    key: int = 0,
    length_m: float = 400.0,
    d_plus_m: float = 120.0,
    d_minus_m: float = 0.0,
    avg_gradient: float = 0.30,
    sac_scale: str | None = "hiking",
) -> Edge:
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


def _seg(u: int, v: int, key: int = 0) -> tuple[int, int, int]:
    """Undirected base-segment id (canonical sorted node-pair + key), per Story 5.1."""
    return (min(u, v), max(u, v), key)


def _graph(
    edges: list[Edge],
    super_ids: set[tuple[int, int, int]],
    *,
    base_ids: dict[tuple[int, int, int], frozenset[tuple[int, int, int]]] | None = None,
    reusable_ids: set[tuple[int, int, int]] | None = None,
    descent_grads: dict[tuple[int, int, int], float] | None = None,
) -> ContractedGraph:
    """Build a `ContractedGraph` whose nx-graph contains exactly `edges`.

    `super_ids` names which `(u, v, key)` identities are super-edges (climbs);
    each gets a single-element `super_edge_to_base` entry (the back-mapping
    content is irrelevant to the validator — only membership matters).

    Every edge is tagged with the Story 5.1 reuse attributes the validator now
    reads: `base_segment_id` defaults to the edge's own undirected id and
    `reusable` to `False`. `base_ids` / `reusable_ids` override per identity so
    the undirected-reuse tests can make a climb and its reverse connector share
    a base id, or mark a short connector exempt.
    """
    base_ids = base_ids or {}
    reusable_ids = reusable_ids or set()
    descent_grads = descent_grads or {}
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {}
    for e in edges:
        eid = (e.node_u, e.node_v, e.key)
        g.add_edge(
            e.node_u,
            e.node_v,
            key=e.key,
            length_m=e.length_m,
            d_plus_m=e.d_plus_m,
            d_minus_m=e.d_minus_m,
            avg_gradient=e.avg_gradient,
            sac_scale=e.sac_scale,
            max_windowed_descent_grad=descent_grads.get(eid, 0.0),
            base_segment_id=base_ids.get(eid, frozenset({_seg(e.node_u, e.node_v, e.key)})),
            reusable=eid in reusable_ids,
        )
        if eid in super_ids:
            super_edge_to_base[eid] = (e,)
    return ContractedGraph(graph=g, super_edge_to_base=super_edge_to_base)


def _route(edges: list[Edge]) -> Route:
    """Wrap edges in a `Route` with a placeholder validation.

    `validate_route` reads `route.edges` only and ignores `route.validation`
    (the field is its *output*), so the placeholder is safe for isolated tests.
    """
    return Route(
        edges=edges,
        metrics=RouteMetrics(length_m=0.0, d_plus_m=0.0, d_minus_m=0.0, avg_gradient=0.0),
        validation=RouteValidation(passed=True, violations=[]),
    )


# ----------------------------------------------------------------------------
# AC #2 — slope floor (route-level: (D+ + D−)/length ≥ θ)
# ----------------------------------------------------------------------------


def test_validate_route_flags_route_below_theta() -> None:
    """A route whose whole-route average gradient is below θ violates `slope_floor`."""
    # (D+ + D−)/length = (100 + 0) / 800 = 0.125 < θ=0.20.
    edges = [_edge(0, 1, length_m=800.0, d_plus_m=100.0, d_minus_m=0.0)]
    graph = _graph(edges, super_ids={(0, 1, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is False
    slope = [v for v in result.violations if v.constraint_id == "slope_floor"]
    assert len(slope) == 1
    assert slope[0].numeric["required"] == 0.20
    assert abs(slope[0].numeric["observed"] - 0.125) < 1e-9


def test_validate_route_admits_route_at_theta() -> None:
    """A route whose average exactly meets θ passes — the floor is `>=`, not `>`."""
    # (200 + 0) / 1000 = 0.20 == θ.
    edges = [_edge(0, 1, length_m=1000.0, d_plus_m=200.0, d_minus_m=0.0)]
    graph = _graph(edges, super_ids={(0, 1, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is True
    assert result.violations == []


def test_validate_route_slope_floor_counts_descent_in_average() -> None:
    """Route average uses (D+ + D−)/length — descent counts toward clearing θ.

    Under the old uphill-only metric (D+/length = 0.10) this route would have
    been flagged; with the corrected total-vertical metric (0.20) it passes.
    """
    edges = [_edge(0, 1, length_m=1000.0, d_plus_m=100.0, d_minus_m=100.0)]
    graph = _graph(edges, super_ids={(0, 1, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is True
    assert result.violations == []


# ----------------------------------------------------------------------------
# AC #2 — difficulty cap (per edge)
# ----------------------------------------------------------------------------


def test_validate_route_flags_edge_above_difficulty_cap() -> None:
    """`alpine_hiking` (rank 4) exceeds the T3 (rank 3) cap → `difficulty_cap`."""
    edges = [_edge(0, 1, sac_scale="alpine_hiking")]
    graph = _graph(edges, super_ids={(0, 1, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is False
    cap = [v for v in result.violations if v.constraint_id == "difficulty_cap"]
    assert len(cap) == 1
    assert cap[0].numeric == {"observed": 4.0, "required": 3.0}


def test_validate_route_admits_edge_within_cap_and_untagged() -> None:
    """In-cap SAC (`hiking`) and untagged (`None`) edges pass the difficulty cap."""
    edges = [_edge(0, 1, sac_scale="hiking"), _edge(1, 2, sac_scale=None)]
    graph = _graph(edges, super_ids={(0, 1, 0), (1, 2, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is True
    assert result.violations == []


# ----------------------------------------------------------------------------
# AC #5 (Story 10.2) — direction-aware descent cap (per edge, opt-in FR32)
# ----------------------------------------------------------------------------


def test_validate_route_flags_descent_above_cap() -> None:
    """A descending traversal (net loss) of an over-cap segment → `max_descent_slope`."""
    # Net descent (d_minus > d_plus); avg_gradient clears θ so only the cap can bite.
    edges = [_edge(0, 1, length_m=400.0, d_plus_m=10.0, d_minus_m=200.0, avg_gradient=0.525)]
    graph = _graph(edges, super_ids=set(), descent_grads={(0, 1, 0): 0.60})

    result = validate_route(_route(edges), graph, _params(max_descent_slope=0.45))

    assert result.passed is False
    flagged = [v for v in result.violations if v.constraint_id == "max_descent_slope"]
    assert len(flagged) == 1
    assert abs(flagged[0].numeric["observed"] - 0.60) < 1e-9
    assert flagged[0].numeric["required"] == 0.45


def test_validate_route_admits_uphill_over_cap_segment() -> None:
    """An over-cap segment traversed *uphill* is NOT flagged — it stays eligible as a climb."""
    # Net climb (d_plus > d_minus) over the SAME steep segment grade as above.
    edges = [_edge(0, 1, length_m=400.0, d_plus_m=200.0, d_minus_m=10.0, avg_gradient=0.525)]
    graph = _graph(edges, super_ids={(0, 1, 0)}, descent_grads={(0, 1, 0): 0.60})

    result = validate_route(_route(edges), graph, _params(max_descent_slope=0.45))

    assert result.passed is True
    assert result.violations == []


def test_validate_route_admits_descent_at_or_below_cap() -> None:
    """A descent whose windowed grade is exactly the cap passes — the limit is `>`, not `>=`."""
    edges = [_edge(0, 1, length_m=400.0, d_plus_m=10.0, d_minus_m=200.0, avg_gradient=0.525)]
    graph = _graph(edges, super_ids=set(), descent_grads={(0, 1, 0): 0.45})

    result = validate_route(_route(edges), graph, _params(max_descent_slope=0.45))

    assert result.passed is True
    assert result.violations == []


def test_validate_route_descent_cap_off_by_default() -> None:
    """With the cap unset, even a steep descent of an over-cap segment is not flagged."""
    edges = [_edge(0, 1, length_m=400.0, d_plus_m=10.0, d_minus_m=200.0, avg_gradient=0.525)]
    graph = _graph(edges, super_ids=set(), descent_grads={(0, 1, 0): 0.99})

    result = validate_route(_route(edges), graph, _params())  # max_descent_slope=None

    assert result.passed is True
    assert result.violations == []


# ----------------------------------------------------------------------------
# AC #2 — edge-reuse limit (undirected base segment, Story 5.2)
# ----------------------------------------------------------------------------


def test_validate_route_flags_repeated_edge() -> None:
    """The same edge twice reuses its (non-exempt) base segment → `edge_reuse`."""
    repeated = _edge(0, 1)
    edges = [repeated, _edge(1, 2), repeated]
    graph = _graph([_edge(0, 1), _edge(1, 2)], super_ids={(0, 1, 0), (1, 2, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is False
    reuse = [v for v in result.violations if v.constraint_id == "edge_reuse"]
    assert len(reuse) == 1
    assert reuse[0].numeric == {"observed": 2.0, "required": 1.0}


def test_validate_route_admits_edge_simple_route() -> None:
    """Distinct edge identities → no `edge_reuse` violation."""
    edges = [_edge(0, 1), _edge(1, 2), _edge(2, 3)]
    graph = _graph(edges, super_ids={(0, 1, 0), (1, 2, 0), (2, 3, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is True
    assert result.violations == []


def test_validate_route_dedups_per_edge_violations_on_reuse() -> None:
    """A reused edge that also fails a per-edge constraint is reported once, not per occurrence."""
    bad = _edge(0, 1, sac_scale="alpine_hiking")  # rank 4 > T3 cap, traversed twice
    edges = [bad, _edge(1, 2), bad]
    graph = _graph([_edge(0, 1), _edge(1, 2)], super_ids={(0, 1, 0), (1, 2, 0)})

    result = validate_route(_route(edges), graph, _params())

    # Exactly one difficulty_cap (deduped across the two traversals) + one edge_reuse.
    cap = [v for v in result.violations if v.constraint_id == "difficulty_cap"]
    reuse = [v for v in result.violations if v.constraint_id == "edge_reuse"]
    assert len(cap) == 1
    assert len(reuse) == 1
    assert reuse[0].numeric == {"observed": 2.0, "required": 1.0}


# ----------------------------------------------------------------------------
# Story 5.2 — undirected base-segment reuse + short-connector exemption
# ----------------------------------------------------------------------------


def test_validate_route_flags_undirected_base_segment_reuse() -> None:
    """Ascending a climb then descending its reverse violates `edge_reuse` (FR5, Story 5.2).

    The climb `(0,1,0)` (super-edge, non-reusable) and the short reverse connector
    `(1,0,0)` share base segment `(0,1,0)`. Even though the connector is
    `reusable` per-edge, the id is non-exempt (carried by the non-reusable
    super-edge), so the route traverses base segment `(0,1,0)` twice → one
    `edge_reuse` violation with observed count 2.
    """
    climb = _edge(0, 1, length_m=400.0, d_plus_m=200.0, d_minus_m=0.0)
    reverse = _edge(1, 0, length_m=100.0, d_plus_m=0.0, d_minus_m=200.0)
    graph = _graph(
        [climb, reverse],
        super_ids={(0, 1, 0)},
        base_ids={(0, 1, 0): frozenset({(0, 1, 0)}), (1, 0, 0): frozenset({(0, 1, 0)})},
        reusable_ids={(1, 0, 0)},  # short per-edge, but its id is non-exempt
    )

    result = validate_route(_route([climb, reverse]), graph, _params())

    reuse = [v for v in result.violations if v.constraint_id == "edge_reuse"]
    assert len(reuse) == 1
    assert reuse[0].numeric == {"observed": 2.0, "required": 1.0}


def test_validate_route_does_not_flag_repeated_exempt_connector() -> None:
    """A genuinely-exempt short connector traversed both directions is not `edge_reuse` (Story 5.2).

    Base segment `(0,1,0)` is carried only by reusable edges → reuse-exempt, so
    walking the linking segment `0→1→0` is legitimate and raises no violation.
    """
    fwd = _edge(0, 1, length_m=100.0, d_plus_m=30.0, d_minus_m=0.0)
    rev = _edge(1, 0, length_m=100.0, d_plus_m=0.0, d_minus_m=30.0)
    graph = _graph(
        [fwd, rev],
        super_ids=set(),
        base_ids={(0, 1, 0): frozenset({(0, 1, 0)}), (1, 0, 0): frozenset({(0, 1, 0)})},
        reusable_ids={(0, 1, 0), (1, 0, 0)},
    )

    result = validate_route(_route([fwd, rev]), graph, _params())

    assert result.passed is True
    assert [v.constraint_id for v in result.violations] == []


# ----------------------------------------------------------------------------
# AC #2 — graph membership
# ----------------------------------------------------------------------------


def test_validate_route_flags_edge_absent_from_graph() -> None:
    """A route edge missing from the operational graph violates `graph_membership`."""
    route_edges = [_edge(0, 1), _edge(5, 6)]  # (5, 6, 0) not in the graph
    graph = _graph([_edge(0, 1)], super_ids={(0, 1, 0)})

    result = validate_route(_route(route_edges), graph, _params())

    assert result.passed is False
    membership = [v for v in result.violations if v.constraint_id == "graph_membership"]
    assert len(membership) == 1
    assert membership[0].numeric == {"observed": 0.0, "required": 1.0}


def test_validate_route_admits_edges_present_in_graph() -> None:
    """All route edges present in the graph → no membership violation."""
    edges = [_edge(0, 1), _edge(1, 2)]
    graph = _graph(edges, super_ids={(0, 1, 0), (1, 2, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is True
    assert result.violations == []


# ----------------------------------------------------------------------------
# AC #3 — set-level pairwise Jaccard
# ----------------------------------------------------------------------------


def test_validate_set_flags_overlapping_pair() -> None:
    """Two near-identical routes (similarity > j_max) yield one `PairwiseViolation`."""
    # Routes share 3 of 4 edges → similarity 3/5 = 0.6 > j_max=0.30.
    edges_a = [_edge(0, 1), _edge(1, 2), _edge(2, 3), _edge(3, 4)]
    edges_b = [_edge(0, 1), _edge(1, 2), _edge(2, 3), _edge(3, 9, key=0)]
    routes = [_route(edges_a), _route(edges_b)]

    violations = validate_set(routes, _params())

    assert len(violations) == 1
    pv = violations[0]
    assert (pv.route_index_a, pv.route_index_b) == (0, 1)
    assert pv.jaccard_max == 0.30
    # |∩|=3, |∪|=5 → similarity 0.6.
    assert abs(pv.jaccard_observed - 0.6) < 1e-9


def test_validate_set_admits_distinct_pair() -> None:
    """Fully disjoint routes (similarity 0) yield no set violations."""
    routes = [
        _route([_edge(0, 1), _edge(1, 2)]),
        _route([_edge(5, 6), _edge(6, 7)]),
    ]

    assert validate_set(routes, _params()) == []


# ----------------------------------------------------------------------------
# AC #4 — orchestrator: Solution → Route, metrics, ordering, purity
# ----------------------------------------------------------------------------


def test_validate_builds_routes_with_aggregate_metrics() -> None:
    """`validate` converts solutions to routes and sums per-edge metrics."""
    solution = Solution(
        edges=(
            _edge(0, 1, length_m=400.0, d_plus_m=120.0, d_minus_m=10.0),
            _edge(1, 2, length_m=600.0, d_plus_m=80.0, d_minus_m=20.0),
        ),
        objective=200.0,
    )
    graph = _graph([_edge(0, 1), _edge(1, 2)], super_ids={(0, 1, 0), (1, 2, 0)})

    result = validate(solutions=[solution], graph=graph, params=_params())

    assert len(result.routes) == 1
    metrics = result.routes[0].metrics
    assert metrics.length_m == 1000.0
    assert metrics.d_plus_m == 200.0
    assert metrics.d_minus_m == 30.0
    assert abs(metrics.avg_gradient - 0.23) < 1e-9  # (200 + 30) / 1000
    assert result.routes[0].validation.passed is True
    assert result.set_violations == []


def test_validate_preserves_solution_order_and_flags_set_violations() -> None:
    """Routes keep solver order; an overlapping pair surfaces in `set_violations`."""
    sol_a = Solution(edges=(_edge(0, 1), _edge(1, 2), _edge(2, 3)), objective=3.0)
    sol_b = Solution(edges=(_edge(0, 1), _edge(1, 2), _edge(2, 9)), objective=2.0)
    graph = _graph(
        [_edge(0, 1), _edge(1, 2), _edge(2, 3), _edge(2, 9)],
        super_ids={(0, 1, 0), (1, 2, 0), (2, 3, 0), (2, 9, 0)},
    )

    result = validate(solutions=[sol_a, sol_b], graph=graph, params=_params())

    assert [r.edges[0].node_v for r in result.routes] == [1, 1]  # order preserved
    assert len(result.set_violations) == 1
    assert (result.set_violations[0].route_index_a, result.set_violations[0].route_index_b) == (
        0,
        1,
    )


def test_validate_rejects_empty_solution() -> None:
    """A zero-edge `Solution` is illegal at the validator stage → `ValueError`."""
    graph = _graph([_edge(0, 1)], super_ids={(0, 1, 0)})

    with pytest.raises(ValueError, match="zero-edge Solution"):
        validate(solutions=[Solution(edges=(), objective=0.0)], graph=graph, params=_params())


def test_validate_route_does_not_mutate_inputs() -> None:
    """Purity: validating leaves the route's edge list untouched."""
    edges = [_edge(0, 1, avg_gradient=0.05)]
    graph = _graph(edges, super_ids={(0, 1, 0)})
    route = _route(edges)
    before = list(route.edges)

    validate_route(route, graph, _params())

    assert route.edges == before
    assert len(graph.super_edge_to_base) == 1  # graph untouched


# ----------------------------------------------------------------------------
# FR31 — start-at-junction (only when params.start_at_junction is set)
# ----------------------------------------------------------------------------


def _mark_junctions(graph: ContractedGraph, junction_nodes: set[int]) -> None:
    """Tag `is_road_trail_junction` on every node (True only for `junction_nodes`).

    Mirrors `pipeline.graph._annotate_junctions`' contract: the attribute is set
    on every node, so the validator's `.get(..., False)` never falls back.
    """
    for node in graph.graph.nodes:
        graph.graph.nodes[node]["is_road_trail_junction"] = node in junction_nodes


def test_start_at_junction_flagged_when_start_not_a_junction() -> None:
    """With the flag on, a route starting at a non-junction node is flagged."""
    edges = [_edge(0, 1)]
    graph = _graph(edges, super_ids={(0, 1, 0)})
    _mark_junctions(graph, junction_nodes=set())  # node 0 is NOT a junction

    result = validate_route(_route(edges), graph, _params(start_at_junction=True))

    assert result.passed is False
    flagged = [v for v in result.violations if v.constraint_id == "start_at_junction"]
    assert len(flagged) == 1
    assert flagged[0].numeric == {"observed": 0.0, "required": 1.0}


def test_start_at_junction_passes_when_start_is_a_junction() -> None:
    """With the flag on, a route starting at a junction node passes the check."""
    edges = [_edge(0, 1)]
    graph = _graph(edges, super_ids={(0, 1, 0)})
    _mark_junctions(graph, junction_nodes={0})  # start node 0 IS a junction

    result = validate_route(_route(edges), graph, _params(start_at_junction=True))

    assert not [v for v in result.violations if v.constraint_id == "start_at_junction"]


def test_start_at_junction_not_checked_when_flag_off() -> None:
    """With the flag off (default), the start node is never constrained."""
    edges = [_edge(0, 1)]
    graph = _graph(edges, super_ids={(0, 1, 0)})
    _mark_junctions(graph, junction_nodes=set())  # not a junction — but flag is off

    result = validate_route(_route(edges), graph, _params())

    assert not [v for v in result.violations if v.constraint_id == "start_at_junction"]
