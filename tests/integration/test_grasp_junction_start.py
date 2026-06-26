# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: same networkx-boundary pattern as test_grasp_construction.py;
# `reportImplicitRelativeImport` — `from exhaustive_oracle import ...` is the shape
# that resolves under pytest's prepend import mode (see test_solver_on_toy_graph.py).
"""Start-at-junction constraint: GRASP ↔ oracle one feasible set + FR29 (Story 10.1, FR31).

With `--start-at-junction` set, GRASP seeds construction and the exhaustive oracle
start their walks only at road/trail junction nodes (`is_road_trail_junction`), so
every returned route's start endpoint is a junction. This test pins, on a small
hand-built graph, that (a) the flag actually constrains the result (the
highest-objective route starts at a non-junction and is excluded), (b) GRASP and
the oracle stay on one shared feasible set under the flag, and (c) FR29
byte-identical determinism holds with the flag on.
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


def _params(*, start_at_junction: bool) -> SolverParams:
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
        start_at_junction=start_at_junction,
    )


def _edge_id_sets(sols: list) -> list[tuple[tuple[int, int, int], ...]]:  # noqa: ANN001
    return [tuple((e.node_u, e.node_v, e.key) for e in s.edges) for s in sols]


# ---------------------------------------------------------------------------
# Fixture (4 nodes): only node 1 is a road/trail junction.
# ---------------------------------------------------------------------------
#
#   0 --super 0→1--> 1 --super 1→2--> 2        3 --road 3→1--> 1
#
# super 0→1: len 400, d+ 200 (avg 0.5)   } the two climbs of one trail
# super 1→2: len 400, d+ 200 (avg 0.5)   }
# road  3→1: len 300, d+ 0   (avg 0.0)   minor-road connector (sac=None)
#
# Junction nodes: only node 1 (incident to trail super-edges AND the road).
# Nodes 0 and 2 are trail-only; node 3 is road-only — none are junctions.
#
# Flag OFF: the best route is [0→1, 1→2] (objective 400), starting at node 0.
# Flag ON : seeds restricted to node 1 → only [1→2] (objective 200) is reachable;
#           the higher-objective [0→1, 1→2] is excluded because node 0 isn't a junction.


def _build_one_junction_fixture() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    e01 = _add_edge(g, 0, 1, length_m=400.0, d_plus_m=200.0)
    e12 = _add_edge(g, 1, 2, length_m=400.0, d_plus_m=200.0)
    _add_edge(g, 3, 1, length_m=300.0, d_plus_m=0.0, sac_scale=None)
    for node in g.nodes:
        g.nodes[node]["is_road_trail_junction"] = node == 1
    return ContractedGraph(graph=g, super_edge_to_base={(0, 1, 0): (e01,), (1, 2, 0): (e12,)})


def test_flag_off_best_route_starts_at_non_junction() -> None:
    """Baseline: without the flag, the highest-objective route starts at node 0 (not a junction)."""
    graph = _build_one_junction_fixture()
    params = _params(start_at_junction=False)

    grasp_ids = _edge_id_sets(GraspSolver(graph, params, np.random.default_rng(params.seed)).run())

    assert ((0, 1, 0), (1, 2, 0)) in grasp_ids, (
        f"flag-off GRASP should find the best route [0→1, 1→2] starting at node 0; got {grasp_ids}"
    )


def test_flag_on_every_route_starts_at_a_junction() -> None:
    """With the flag on, GRASP returns only routes whose start endpoint is a junction node."""
    graph = _build_one_junction_fixture()
    params = _params(start_at_junction=True)

    grasp_result = GraspSolver(graph, params, np.random.default_rng(params.seed)).run()
    grasp_ids = _edge_id_sets(grasp_result)

    assert grasp_result, "GRASP must find the [1→2] route seeded at junction node 1"
    # Every route starts at node 1 (the only junction); the non-junction-start
    # route [0→1, 1→2] must not appear.
    for route in grasp_ids:
        assert route[0][0] == 1, f"route {route} does not start at junction node 1"
    assert ((0, 1, 0), (1, 2, 0)) not in grasp_ids


def test_flag_on_grasp_matches_oracle_one_feasible_set() -> None:
    """Under the flag, GRASP and the exhaustive oracle enumerate the same set."""
    graph = _build_one_junction_fixture()
    params = _params(start_at_junction=True)

    grasp_ids = _edge_id_sets(GraspSolver(graph, params, np.random.default_rng(params.seed)).run())
    oracle_ids = _edge_id_sets(enumerate_best(graph, params, params.n))

    assert grasp_ids == oracle_ids, (
        f"GRASP {grasp_ids} should match the oracle {oracle_ids} under --start-at-junction"
    )


def test_flag_on_is_deterministic_under_same_seed() -> None:
    """FR29: two seeded GRASP runs with the flag on produce byte-identical edge sequences."""
    graph = _build_one_junction_fixture()
    params = _params(start_at_junction=True)

    first = _edge_id_sets(GraspSolver(graph, params, np.random.default_rng(params.seed)).run())
    second = _edge_id_sets(GraspSolver(graph, params, np.random.default_rng(params.seed)).run())

    assert first == second
