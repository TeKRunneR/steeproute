# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: same networkx-boundary pattern as test_grasp_construction.py;
# `reportImplicitRelativeImport` — `from exhaustive_oracle import ...` is the shape
# that resolves under pytest's prepend import mode (see test_solver_on_toy_graph.py).
"""GRASP θ-feasible prefix recovery regression test (Story 9.2, review finding #10).

Fail-first regression for the gap where `run()` offered only the maximal walk to
the tracker and checked θ on that whole walk, so a steep θ-clearing prefix forced
to append a flat tail was discarded entirely — GRASP returned `[]` where a
θ-feasible route demonstrably exists. The exhaustive oracle emits every prefix,
so it returns the steep-only route GRASP threw away; this test pins GRASP to the
oracle's result on the minimal steep-edge-plus-forced-flat-tail graph.

Basis: `tmp/repro_findings.py::repro_finding_10`. On pre-9.2 code GRASP returns
`[]` here (only `[0→1, 1→2]` is offered, avg 200/2400 < θ); after the fix GRASP
offers the longest θ-clearing prefix `[0→1]` — the same route `enumerate_best`
returns.
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


def _params() -> SolverParams:
    """`SolverParams` with θ=0.20 and a budget large enough to sample node 0."""
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
        iter_budget=300,
        time_budget=30.0,
        stagnation_iters=0,
    )


def _edge_id_sets(sols: list) -> list[tuple[tuple[int, int, int], ...]]:  # noqa: ANN001
    return [tuple((e.node_u, e.node_v, e.key) for e in s.edges) for s in sols]


# ---------------------------------------------------------------------------
# Steep-edge-plus-forced-flat-tail graph (3 nodes, 2 edges).
# ---------------------------------------------------------------------------
#
#   0 --steep (super)--> 1 --flat connector--> 2
#
# steep 0→1: len=400, d+=200 (avg 0.500, clears θ=0.20 on its own).
# flat  1→2: len=2000, d+=0  (avg 0.000) — always RCL-feasible, so the greedy
#            walk is forced to append it, dragging the maximal-walk average
#            (200 / 2400 ≈ 0.083) below θ.
#
# The only θ-clearing route is the steep-only prefix [0→1]. Pre-9.2 GRASP
# offered only the maximal walk and rejected it → []. The oracle enumerates the
# prefix → [0→1].


def _build_forced_flat_tail_fixture() -> ContractedGraph:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    steep = _add_edge(g, 0, 1, length_m=400.0, d_plus_m=200.0)
    _add_edge(g, 1, 2, length_m=2000.0, d_plus_m=0.0)
    return ContractedGraph(graph=g, super_edge_to_base={(0, 1, 0): (steep,)})


def test_grasp_recovers_theta_clearing_prefix_under_forced_flat_tail() -> None:
    """Story 9.2 / FR3: GRASP returns the θ-clearing prefix the oracle returns.

    Fail-first: pre-fix `run()` returns `[]` here because only the maximal walk
    `[0→1, 1→2]` (avg ≈ 0.083 < θ) is offered to the tracker. After the fix the
    longest θ-clearing prefix `[0→1]` is offered, so GRASP matches the oracle and
    never returns a false empty result.
    """
    graph = _build_forced_flat_tail_fixture()
    params = _params()

    grasp_result = GraspSolver(graph, params, np.random.default_rng(params.seed)).run()
    oracle_result = enumerate_best(graph, params, params.n)

    grasp_ids = _edge_id_sets(grasp_result)
    oracle_ids = _edge_id_sets(oracle_result)

    assert grasp_result, "GRASP must not return [] when a θ-feasible prefix exists"
    assert ((0, 1, 0),) in grasp_ids, (
        f"GRASP must recover the steep θ-clearing prefix [0→1]; got {grasp_ids}"
    )
    # The forced flat tail must never appear: no θ-clearing route includes 1→2.
    assert (1, 2, 0) not in {eid for ids in grasp_ids for eid in ids}, (
        f"the sub-θ maximal walk must not be admitted; got {grasp_ids}"
    )
    # Both sides on one feasible set: GRASP matches the oracle's enumeration.
    assert grasp_ids == oracle_ids, (
        f"GRASP {grasp_ids} should match the oracle {oracle_ids} on this graph"
    )
