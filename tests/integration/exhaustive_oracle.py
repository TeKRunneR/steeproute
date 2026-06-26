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

- A **route** is a directed walk in `graph.graph` obeying **undirected
  base-segment reuse** (Story 5.2, FR5): a sequence of edges where each
  consecutive pair shares an endpoint and no non-exempt base segment is
  traversed more than once, *in either direction*. The rule keys on the
  `base_segment_id` tags (Story 5.1) and is single-sourced with the GRASP solver
  and the validator through `steeproute.solver.reuse`, so the oracle and GRASP
  enumerate the identical feasible set (this is what keeps Story 3.7's quality
  ratio meaningful). Exempt short connectors (`reusable`) may recur; node-revisits
  via distinct non-conflicting segments are allowed. Open and closed walks are
  both admissible; the start node is unconstrained.
- The **objective** is `sum(e.d_plus_m + e.d_minus_m for e in route.edges)` —
  total vertical effort per Architecture §Cat 5e / §"Stagnation definition".
  Super-edges already carry aggregated metrics (Story 3.3); the oracle never
  re-expands them via `super_edge_to_base`.
- **Feasibility filters** applied during DFS:
    - SAC difficulty cap per edge (`max_sac_rank(sac_scale) > cap_rank` → drop).
      Edges with `sac_scale=None` or unrecognized values pass — they already
      cleared `filter_trails` upstream under the prevailing `untagged_policy`.
    - Undirected base-segment reuse: an edge is infeasible iff any of its
      non-exempt base-segment ids is already used on the current walk
      (`solver.reuse.blocking_ids` against the graph's non-exempt id set). Exempt
      short connectors never block.
    - Direction-aware descent cap (FR32, opt-in): a *descending* traversal whose
      `max_windowed_descent_grad` exceeds `--max-descent-slope` is dropped
      (`solver.descent.descends_over_cap`); uphill is unconstrained. Off when the
      cap is unset. Single-sourced with GRASP + the validator. Note the
      *candidate-dedup* key below stays
      *directed* (`(node_u, node_v, key)`) — its only job is to collapse
      different traversal orderings of the same directed edge-set. The
      **distinctness/Jaccard** step (the `TopNTracker` below) keys on the
      *undirected* `base_segment_id` (Story 6.1), so opposite-direction reuse of
      one trail counts as overlap — matching GRASP and the validator.
  The slope floor θ (FR3) is **not** a DFS filter — it is a route-level
  constraint, so it is applied to each enumerated candidate before admission:
  `(Σ d_plus_m + Σ d_minus_m) / Σ length_m ≥ θ`. Because `_dfs` emits **every
  prefix** of every feasible walk (not just maximal walks), the candidate set
  spans all θ-clearing prefixes — and GRASP now recovers the best θ-clearing
  prefix of each constructed walk as well (`GraspSolver._best_theta_prefix`,
  Story 9.2 / review finding #10), rather than only checking its maximal walk.
  So the oracle's and GRASP's feasible sets are genuinely identical, which is
  what makes Story 3.7's quality ratio meaningful.
- **Top-N + distinctness:** the full enumeration is deduplicated by directed
  canonical edge-set (different traversal orderings of the same edge-set
  collapse), then sorted objective-descending and fed through
  `TopNTracker(n, params.j_max, base_segment_id_map(graph))` — the same
  undirected-distinctness admission semantics GRASP uses (Story 3.6 + Story
  6.1). This is what makes Story 3.7's quality ratio meaningful.

Pure: takes no shared state, mutates no inputs. Lives under `tests/` and is
never imported from `src/steeproute/` — strictly testing infrastructure.
"""

from __future__ import annotations

from typing import Any

from steeproute.models import ContractedGraph, Edge, Solution, SolverParams, route_avg_gradient
from steeproute.pipeline.graph import is_junction_node
from steeproute.pipeline.osm import max_sac_rank, parse_difficulty_cap
from steeproute.solver.descent import descends_over_cap
from steeproute.solver.distinctness import TopNTracker
from steeproute.solver.reuse import (
    base_segment_id_map,
    blocking_ids,
    non_exempt_base_segment_ids,
)

__all__ = ["enumerate_best"]


def enumerate_best(
    graph: ContractedGraph,
    params: SolverParams,
    n: int,
) -> list[Solution]:
    """Return the top-`n` distinct feasible routes in `graph`, objective-descending.

    Args:
        graph: post-stage-9 `ContractedGraph` — super-edges + long connectors.
        params: only `theta`, `difficulty_cap`, `j_max`, `start_at_junction`, and
            `max_descent_slope` are read here; the remaining `SolverParams` fields
            (`seed`, `iter_budget`, etc.) are GRASP-only and the oracle ignores them.
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
    # Base-segment ids subject to the once-only rule, computed once. Single-
    # sourced with GRASP + the validator (`solver.reuse`) so the oracle and GRASP
    # share one feasible set (keeps the Story 3.7 ratio honest).
    non_exempt = non_exempt_base_segment_ids(graph)

    # Canonical edge-set → first-discovered Solution. Different traversal
    # orderings of the same edge-set collapse: they share an objective by
    # construction (sum over the same multiset), so any representative is fine.
    candidates: dict[frozenset[tuple[int, int, int]], Solution] = {}

    # Start-node pool. Under `--start-at-junction` (FR31, Story 10.1) the oracle
    # enumerates only walks that *start* at a road/trail junction node, via the
    # shared `is_junction_node` predicate — the same one GRASP and the validator
    # use, so all three stay on one feasible set. `_dfs` emits every prefix of a
    # walk starting at `start`, so a prefix's start endpoint is always `start`;
    # restricting starts therefore keeps the oracle's set start-endpoint-feasible.
    if params.start_at_junction:
        start_nodes = [node for node in nx_graph.nodes if is_junction_node(graph, node)]
    else:
        start_nodes = list(nx_graph.nodes)

    for start in start_nodes:
        _dfs(
            nx_graph=nx_graph,
            current=start,
            path_edges=[],
            used_directed=set(),
            used_segments=set(),
            cap_rank=cap_rank,
            non_exempt=non_exempt,
            max_descent_slope=params.max_descent_slope,
            results=candidates,
        )

    # Route-level slope floor (FR3) is applied here, post-enumeration, on each
    # complete candidate — via the same `models.route_avg_gradient` the solver's
    # finalization gate uses, so both share one feasible set bit-for-bit (keeps
    # the Story 3.7 ratio honest).
    feasible = (s for s in candidates.values() if route_avg_gradient(s.edges) >= params.theta)
    sorted_candidates = sorted(feasible, key=lambda s: -s.objective)
    # Undirected base-segment distinctness (Story 6.1), single-sourced with GRASP
    # + the validator via `solver.reuse` so the oracle and GRASP keep one
    # feasible/distinct set (the Story 3.7 quality ratio stays apples-to-apples).
    tracker = TopNTracker(n, params.j_max, base_segment_id_map(graph))
    for sol in sorted_candidates:
        tracker.consider(sol)
    return tracker.current_top()


def _dfs(
    *,
    nx_graph: Any,
    current: int,
    path_edges: list[Edge],
    used_directed: set[tuple[int, int, int]],
    used_segments: set[tuple[int, int, int]],
    cap_rank: int,
    non_exempt: frozenset[tuple[int, int, int]],
    max_descent_slope: float | None,
    results: dict[frozenset[tuple[int, int, int]], Solution],
) -> None:
    """Backtracking walk-enumerator; emits each non-empty prefix as a candidate.

    Every non-empty feasible walk starting at the original `start` is a valid
    route, so the function emits at every recursion depth (not only at leaves).
    Feasibility mirrors `GraspSolver._build_rcl` exactly (shared via
    `solver.reuse`): an edge is skipped iff its directed `(u, v, key)` is already
    used (`used_directed`, edge-simple → termination) or any of its non-exempt
    base ids is already used (`used_segments`, undirected once-only, Story 5.2).
    On recursion both are recorded then rolled back on backtrack — the non-exempt
    ids are disjoint from `used_segments` by the skip check, so the removal is
    exact. The directed-simple bound is what guarantees the brute-force recursion
    terminates even when exempt connectors (empty blocking set) are present. The
    dedup key stays the *directed* canonical edge-set — distinctness is unchanged
    — so different traversal orders of the same edge-set still collapse.
    """
    if path_edges:
        identity = frozenset((e.node_u, e.node_v, e.key) for e in path_edges)
        if identity not in results:
            objective = sum(e.d_plus_m + e.d_minus_m for e in path_edges)
            results[identity] = Solution(edges=tuple(path_edges), objective=objective)

    for u, v, k, data in nx_graph.out_edges(current, keys=True, data=True):
        if (u, v, k) in used_directed:
            continue
        blocking = blocking_ids(data, u, v, k, non_exempt)
        if blocking & used_segments:
            continue
        rank = max_sac_rank(data["sac_scale"])
        if rank is not None and rank > cap_rank:
            continue
        # Direction-aware descent cap (FR32, Story 10.2): mirror GRASP's RCL filter
        # via the shared `solver.descent` predicate so both enumerate one feasible set.
        if descends_over_cap(data, max_descent_slope):
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
        used_directed.add((u, v, k))
        used_segments |= blocking
        _dfs(
            nx_graph=nx_graph,
            current=v,
            path_edges=path_edges,
            used_directed=used_directed,
            used_segments=used_segments,
            cap_rank=cap_rank,
            non_exempt=non_exempt,
            max_descent_slope=max_descent_slope,
            results=results,
        )
        path_edges.pop()
        used_directed.discard((u, v, k))
        used_segments -= blocking
