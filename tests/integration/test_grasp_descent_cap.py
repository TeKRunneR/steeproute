# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: same networkx-boundary pattern as test_grasp_construction.py;
# `reportImplicitRelativeImport` — `from exhaustive_oracle import ...` is the shape
# that resolves under pytest's prepend import mode (see test_solver_on_toy_graph.py).
"""Direction-aware descent cap: GRASP ↔ oracle one feasible set + FR29 (Story 10.2, FR32).

With `--max-descent-slope` set, GRASP construction and the exhaustive oracle reject
any *descending* traversal of an edge whose `max_windowed_descent_grad` exceeds the
cap, while leaving the same segment eligible *uphill*. This test pins, on a small
hand-built graph, that (a) the cap actually constrains the result (the
highest-objective route descends an over-cap segment and is excluded), (b) GRASP and
the oracle stay on one shared feasible set under the cap, and (c) FR29 byte-identical
determinism holds with the cap on.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
from exhaustive_oracle import enumerate_best

from steeproute.models import ContractedGraph, Edge, SolverParams
from steeproute.solver.grasp import GraspSolver


def _seg(u: int, v: int, key: int = 0) -> tuple[int, int, int]:
    return (min(u, v), max(u, v), key)


def _add_edge(
    g: nx.MultiDiGraph,
    u: int,
    v: int,
    *,
    length_m: float,
    d_plus_m: float,
    d_minus_m: float = 0.0,
    max_windowed_descent_grad: float = 0.0,
    sac_scale: str | None = "hiking",
    key: int = 0,
) -> Edge:
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
        max_windowed_descent_grad=max_windowed_descent_grad,
        base_segment_id=frozenset({_seg(u, v, key)}),
        reusable=False,
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


def _params(*, max_descent_slope: float | None) -> SolverParams:
    return SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T5",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=5,
        area_cap=500.0,
        untagged_policy="include",
        seed=42,
        iter_budget=400,
        time_budget=30.0,
        stagnation_iters=0,
        max_descent_slope=max_descent_slope,
    )


def _edge_id_sets(sols: list) -> list[tuple[tuple[int, int, int], ...]]:  # noqa: ANN001
    return [tuple((e.node_u, e.node_v, e.key) for e in s.edges) for s in sols]


# ---------------------------------------------------------------------------
# Fixture (4 nodes): the high-objective route descends an over-cap segment.
# ---------------------------------------------------------------------------
#
#   0 --super 0→1--> 1 --descent 1→2--> 2
#                    1 --super  1→3--> 3
#
# super   0→1: len 400, d+ 250, d- 0   (uphill; descent grad 0.0)
# descent 1→2: len 400, d+ 0,  d- 300  (NET DESCENT; windowed grad 0.70) obj 300
# super   1→3: len 400, d+ 100, d- 0   (uphill; descent grad 0.0)        obj 100
#
# Cap OFF: best route is [0→1, 1→2] (objective 550) — it descends segment 1↔2.
# Cap 0.45: 1→2 is a net descent with grad 0.70 > 0.45 → blocked everywhere;
#           best becomes [0→1, 1→3] (objective 350) and no route descends 1↔2.


def _build_descent_fixture() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    e01 = _add_edge(g, 0, 1, length_m=400.0, d_plus_m=250.0)
    e12 = _add_edge(
        g, 1, 2, length_m=400.0, d_plus_m=0.0, d_minus_m=300.0, max_windowed_descent_grad=0.70
    )
    e13 = _add_edge(g, 1, 3, length_m=400.0, d_plus_m=100.0)
    return ContractedGraph(
        graph=g, super_edge_to_base={(0, 1, 0): (e01,), (1, 2, 0): (e12,), (1, 3, 0): (e13,)}
    )


def test_cap_off_best_route_descends_the_steep_segment() -> None:
    """Baseline: without the cap, the highest-objective route descends segment 1↔2."""
    graph = _build_descent_fixture()
    params = _params(max_descent_slope=None)

    grasp_ids = _edge_id_sets(GraspSolver(graph, params, np.random.default_rng(params.seed)).run())

    assert ((0, 1, 0), (1, 2, 0)) in grasp_ids, (
        f"cap-off GRASP should find the best route [0→1, 1→2] descending 1↔2; got {grasp_ids}"
    )


def test_cap_on_no_route_descends_over_cap_segment() -> None:
    """With the cap on, no returned route descends the over-cap segment 1↔2."""
    graph = _build_descent_fixture()
    params = _params(max_descent_slope=0.45)

    grasp_result = GraspSolver(graph, params, np.random.default_rng(params.seed)).run()
    grasp_ids = _edge_id_sets(grasp_result)

    assert grasp_result, "GRASP must still find the uphill alternative under the cap"
    for route in grasp_ids:
        assert (1, 2, 0) not in route, f"route {route} descends the over-cap segment 1→2"
    # The uphill super-edge 1→3 stays reachable (segment eligibility is direction-aware).
    assert ((0, 1, 0), (1, 3, 0)) in grasp_ids


def test_cap_on_grasp_matches_oracle_one_feasible_set() -> None:
    """Under the cap, GRASP and the exhaustive oracle enumerate the same set."""
    graph = _build_descent_fixture()
    params = _params(max_descent_slope=0.45)

    grasp_ids = _edge_id_sets(GraspSolver(graph, params, np.random.default_rng(params.seed)).run())
    oracle_ids = _edge_id_sets(enumerate_best(graph, params, params.n))

    assert grasp_ids == oracle_ids, (
        f"GRASP {grasp_ids} should match the oracle {oracle_ids} under --max-descent-slope"
    )


def test_cap_on_is_deterministic_under_same_seed() -> None:
    """FR29: two seeded GRASP runs with the cap on produce byte-identical edge sequences."""
    graph = _build_descent_fixture()
    params = _params(max_descent_slope=0.45)

    first = _edge_id_sets(GraspSolver(graph, params, np.random.default_rng(params.seed)).run())
    second = _edge_id_sets(GraspSolver(graph, params, np.random.default_rng(params.seed)).run())

    assert first == second
