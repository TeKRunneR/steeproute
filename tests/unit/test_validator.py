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


def _params(*, theta: float = _THETA, j_max: float = _J_MAX) -> SolverParams:
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


def _graph(edges: list[Edge], super_ids: set[tuple[int, int, int]]) -> ContractedGraph:
    """Build a `ContractedGraph` whose nx-graph contains exactly `edges`.

    `super_ids` names which `(u, v, key)` identities are super-edges (climbs);
    each gets a single-element `super_edge_to_base` entry (the back-mapping
    content is irrelevant to the validator — only membership matters).
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {}
    for e in edges:
        g.add_edge(
            e.node_u,
            e.node_v,
            key=e.key,
            length_m=e.length_m,
            d_plus_m=e.d_plus_m,
            d_minus_m=e.d_minus_m,
            avg_gradient=e.avg_gradient,
            sac_scale=e.sac_scale,
        )
        eid = (e.node_u, e.node_v, e.key)
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
# AC #2 — slope floor (super-edges only)
# ----------------------------------------------------------------------------


def test_validate_route_flags_super_edge_below_theta() -> None:
    """A super-edge below θ violates `slope_floor` with observed/required numerics."""
    edges = [_edge(0, 1, avg_gradient=0.12)]  # 0.12 < θ=0.20
    graph = _graph(edges, super_ids={(0, 1, 0)})

    result = validate_route(_route(edges), graph, _params())

    assert result.passed is False
    slope = [v for v in result.violations if v.constraint_id == "slope_floor"]
    assert len(slope) == 1
    assert slope[0].numeric == {"observed": 0.12, "required": 0.20}


def test_validate_route_ignores_below_theta_connector() -> None:
    """A below-θ *connector* (not a super-edge) is exempt — no slope violation."""
    edges = [_edge(0, 1, avg_gradient=0.05)]  # below θ, but NOT in super_ids
    graph = _graph(edges, super_ids=set())

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
# AC #2 — edge-reuse limit (edge-simple)
# ----------------------------------------------------------------------------


def test_validate_route_flags_repeated_edge() -> None:
    """The same `(u, v, key)` twice violates `edge_reuse` with observed count."""
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
    bad = _edge(0, 1, avg_gradient=0.05)  # super-edge below θ, traversed twice
    edges = [bad, _edge(1, 2), bad]
    graph = _graph([_edge(0, 1), _edge(1, 2)], super_ids={(0, 1, 0), (1, 2, 0)})

    result = validate_route(_route(edges), graph, _params())

    # Exactly one slope_floor (deduped across the two traversals) + one edge_reuse.
    slope = [v for v in result.violations if v.constraint_id == "slope_floor"]
    reuse = [v for v in result.violations if v.constraint_id == "edge_reuse"]
    assert len(slope) == 1
    assert len(reuse) == 1
    assert reuse[0].numeric == {"observed": 2.0, "required": 1.0}


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
    assert abs(metrics.avg_gradient - 0.20) < 1e-9  # 200 / 1000
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
