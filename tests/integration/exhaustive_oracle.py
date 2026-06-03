# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations on MultiDiGraph surface as Unknown; same boundary
# pattern as the `pipeline/` modules and `test_graph_contraction.py`.
"""Brute-force top-N enumerator over a `ContractedGraph` — reference oracle.

Used by:

- `tests/integration/test_oracle_correctness.py` (this story, 3.5) — verifies the
  oracle itself against handcrafted graphs with known-by-inspection optima.
- `tests/integration/test_solver_on_toy_graph.py` (Story 3.7) — feeds GRASP and
  the oracle the same toy fixture and asserts a quality-ratio CI gate.

PRD Appendix A frames the existence of (3.7) as "validating against an
unvalidated oracle"; this module + the correctness tests close that loop.

Semantics:

- A **route** is an edge-simple directed walk in `graph.graph`: a sequence of
  edges where each consecutive pair shares an endpoint and no
  `(node_u, node_v, key)` triple repeats. Node-revisits via distinct edges are
  allowed. Open and closed walks are both admissible; the start node is
  unconstrained.
- The **objective** is `sum(e.d_plus_m + e.d_minus_m for e in route.edges)` —
  total vertical effort per Architecture §Cat 5e / §"Stagnation definition".
  Super-edges already carry aggregated metrics (Story 3.3); the oracle never
  re-expands them via `super_edge_to_base`.
- **Feasibility filters** applied during DFS:
    - SAC difficulty cap per edge (`max_sac_rank(sac_scale) > cap_rank` → drop).
      Edges with `sac_scale=None` or unrecognized values pass — they already
      cleared `filter_trails` upstream under the prevailing `untagged_policy`.
    - Edge-reuse (graph-membership): enforced by the simple-walk constraint;
      sub-`l_connector` connectors are absent from the input by construction
      (Story 3.3), so no separate length check is needed.
  The slope floor θ (FR3) is **not** a DFS filter — it is a route-level
  constraint, so (exactly like GRASP's `_route_slope_ok` finalization gate) it
  is applied to each fully-enumerated candidate before admission:
  `(Σ d_plus_m + Σ d_minus_m) / Σ length_m ≥ θ`. This keeps the oracle's and
  GRASP's feasible sets identical, which is what makes Story 3.7's quality
  ratio meaningful.
- **Top-N + distinctness:** the full enumeration is deduplicated by canonical
  edge-set (different traversal orderings of the same edge-set collapse), then
  sorted objective-descending and fed through `TopNTracker(n, params.j_max)` —
  the same admission semantics GRASP will use in Story 3.6. This is what makes
  Story 3.7's quality ratio meaningful.

Pure: takes no shared state, mutates no inputs. Lives under `tests/` and is
never imported from `src/steeproute/` — strictly testing infrastructure.
"""

from __future__ import annotations

from typing import Any

from steeproute.models import ContractedGraph, Edge, Solution, SolverParams, route_avg_gradient
from steeproute.pipeline.osm import max_sac_rank, parse_difficulty_cap
from steeproute.solver.distinctness import TopNTracker

__all__ = ["enumerate_best"]


def enumerate_best(
    graph: ContractedGraph,
    params: SolverParams,
    n: int,
) -> list[Solution]:
    """Return the top-`n` distinct feasible routes in `graph`, objective-descending.

    Args:
        graph: post-stage-9 `ContractedGraph` — super-edges + long connectors.
        params: only `theta`, `difficulty_cap`, and `j_max` are read here; the
            remaining `SolverParams` fields (`seed`, `iter_budget`, etc.) are
            GRASP-only and the oracle ignores them.
        n: desired top-N route count. The result may be shorter than `n` when
            fewer feasible-and-distinct routes exist (FR12 graceful
            degradation), or empty when none qualify.

    Returns:
        Objective-descending `list[Solution]` of length `<= n`. Ordering and
        tie-break match `TopNTracker.current_top()`'s `(-objective,
        sorted_edge_ids)` rule (Story 3.4), so comparisons against GRASP output
        in Story 3.7 are apples-to-apples.

    Complexity is exponential in the edge count — intended for hand-built test
    graphs with <= ~15 edges. Larger inputs will not return in reasonable time;
    see AC #4 in Story 3.5 (1 s wall-clock budget on a 5-node hand-graph).
    """
    cap_rank = parse_difficulty_cap(params.difficulty_cap)
    nx_graph = graph.graph

    # Canonical edge-set → first-discovered Solution. Different traversal
    # orderings of the same edge-set collapse: they share an objective by
    # construction (sum over the same multiset), so any representative is fine.
    candidates: dict[frozenset[tuple[int, int, int]], Solution] = {}

    for start in list(nx_graph.nodes):
        _dfs(
            nx_graph=nx_graph,
            current=start,
            path_edges=[],
            used_ids=set(),
            cap_rank=cap_rank,
            results=candidates,
        )

    # Route-level slope floor (FR3) is applied here, post-enumeration, on each
    # complete candidate — via the same `models.route_avg_gradient` the solver's
    # finalization gate uses, so both share one feasible set bit-for-bit (keeps
    # the Story 3.7 ratio honest).
    feasible = (s for s in candidates.values() if route_avg_gradient(s.edges) >= params.theta)
    sorted_candidates = sorted(feasible, key=lambda s: -s.objective)
    tracker = TopNTracker(n, params.j_max)
    for sol in sorted_candidates:
        tracker.consider(sol)
    return tracker.current_top()


def _dfs(
    *,
    nx_graph: Any,
    current: int,
    path_edges: list[Edge],
    used_ids: set[tuple[int, int, int]],
    cap_rank: int,
    results: dict[frozenset[tuple[int, int, int]], Solution],
) -> None:
    """Backtracking walk-enumerator; emits each non-empty prefix as a candidate.

    Every non-empty edge-simple walk starting at the original `start` is a
    valid route, so the function emits at every recursion depth (not only at
    leaves). The dedup key drops duplicates produced by reaching the same
    edge-set from different start nodes or traversal orders.
    """
    if path_edges:
        identity = frozenset((e.node_u, e.node_v, e.key) for e in path_edges)
        if identity not in results:
            objective = sum(e.d_plus_m + e.d_minus_m for e in path_edges)
            results[identity] = Solution(edges=tuple(path_edges), objective=objective)

    for u, v, k, data in nx_graph.out_edges(current, keys=True, data=True):
        eid = (u, v, k)
        if eid in used_ids:
            continue
        rank = max_sac_rank(data["sac_scale"])
        if rank is not None and rank > cap_rank:
            continue
        edge = Edge(
            node_u=u,
            node_v=v,
            key=k,
            length_m=data["length_m"],
            d_plus_m=data["d_plus_m"],
            d_minus_m=data["d_minus_m"],
            avg_gradient=data["avg_gradient"],
            sac_scale=data["sac_scale"],
        )
        path_edges.append(edge)
        used_ids.add(eid)
        _dfs(
            nx_graph=nx_graph,
            current=v,
            path_edges=path_edges,
            used_ids=used_ids,
            cap_rank=cap_rank,
            results=results,
        )
        path_edges.pop()
        used_ids.discard(eid)
