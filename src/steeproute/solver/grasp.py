# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations on `ContractedGraph.graph` surface as Unknown — same
# boundary pattern as `pipeline/` modules and `tests/integration/exhaustive_oracle.py`.
"""GRASP construction loop + anytime best-so-far (Story 3.6).

Implements Architecture §Cat 5's solver shape: a class with an injected RNG,
parameter snapshot, prepared `ContractedGraph`, and a continuously-readable
`best_so_far`. Termination handled here covers only the iter-budget; the
time-budget, stagnation, and KeyboardInterrupt branches in §Cat 5e land in
Epic 4 (Stories 4.2 / 4.3) at the CLI layer. The `progress_callback` parameter
is accepted but not yet invoked — Story 4.1 wires the throttled call.

Construction shape
==================

Each GRASP iteration builds **one** candidate route from a randomly-chosen
start node by greedy-randomized walk extension:

1. Sample a start node uniformly at random over the contracted graph's nodes
   (via the injected `numpy.random.Generator`).
2. At each step, build the restricted candidate list (RCL): the outgoing edges
   from the current node that pass the feasibility filters
   (not-yet-used + SAC cap + θ-on-super-edges), sorted by per-edge objective
   contribution (`d_plus_m + d_minus_m`) descending, truncated to
   `RCL_SIZE` entries.
3. Sample one edge uniformly from the RCL; append it; advance the current
   node to its `node_v`.
4. Repeat until the RCL is empty (no feasible extension); the walk emits as a
   `Solution`.

Each completed `Solution` is offered to a `TopNTracker(params.n, params.j_max)`
— the same admission policy the oracle uses (`tests/integration/exhaustive_oracle.py`,
Story 3.5). This is what makes the Story 3.7 GRASP-vs-exhaustive quality
ratio apples-to-apples: identical distinctness semantics on both sides.

Walks are **edge-simple**: each `(node_u, node_v, key)` triple appears at most
once per route. Node-revisits via distinct edges are allowed (Story 3.5 oracle
contract). Strict containment (FR10) is guaranteed upstream — `contract_climbs`
cuts the contracted graph to the area before the solver sees it; no `Area`
check is performed here.

Determinism (FR29)
==================

All randomness flows through the injected `numpy.random.Generator`. No
ambient `numpy.random.seed`, no `random` stdlib usage, no time-derived seeds.
Two `GraspSolver` instances built with `numpy.random.default_rng(seed)` on
the same `ContractedGraph` and `SolverParams` produce byte-identical
`list[Solution]` results — including the edges' traversal order. The two
order-sensitive sites are pinned explicitly so this holds across Python /
networkx versions, where dict-insertion order is not a contract (see
`deferred-work.md` "Story 3.5 deferred #5"):

- **Start-node sampling** draws an index into `tuple(sorted(graph.graph.nodes))`
  (sorted once in `__init__`), so the start node depends only on the RNG, not
  on node-insertion order.
- **RCL ranking** ends in a total sort (`-objective`, then `(node_v, key)`),
  which fully determines the candidate order regardless of the order
  `nx_graph.out_edges(...)` yields edges in.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from steeproute.models import ContractedGraph, Edge, Solution, SolverParams
from steeproute.pipeline.osm import max_sac_rank, parse_difficulty_cap
from steeproute.solver.distinctness import TopNTracker

__all__ = ["GraspSolver", "RCL_SIZE"]


RCL_SIZE: int = 5
"""Restricted candidate list cap (cardinality-based GRASP).

Pinned module-scope for FR29 determinism — Epic 4 may surface this as a CLI
flag once the Story 3.7 quality gate establishes a baseline. Five is the
classic "small but not greedy" default; smaller values starve diversity,
larger values approach uniform-random construction.
"""


class GraspSolver:
    """GRASP solver driving construction + restart for FR10 / FR11 / FR29.

    Constructor stores references — `params` / `graph` are immutable
    (`frozen=True, slots=True`) and the injected `rng` is the solver's sole
    randomness source. The internal `TopNTracker` is built eagerly so
    `best_so_far` is readable before `run()` is called (and returns `[]`).

    `run()` performs `params.iter_budget` GRASP iterations and returns the
    final `tracker.current_top()`. It does not catch `KeyboardInterrupt` —
    Architecture §Cat 5b puts that at the CLI layer (Story 4.3 wires the
    try/except).
    """

    def __init__(
        self,
        graph: ContractedGraph,
        params: SolverParams,
        rng: np.random.Generator,
        progress_callback: Callable[[Any], None] | None = None,
    ) -> None:
        if params.iter_budget < 1:
            # Fail loud at the boundary, symmetric with `TopNTracker`'s `n >= 1`
            # guard (Story 3.4). A 0/negative budget would otherwise make
            # `run()` silently return `[]` — indistinguishable from "searched
            # and found nothing", which would mislead Story 3.7's quality-ratio
            # comparator on a misconfigured budget.
            raise ValueError(f"iter_budget must be >= 1, got {params.iter_budget}")
        self._graph: ContractedGraph = graph
        self._params: SolverParams = params
        self._rng: np.random.Generator = rng
        # Accepted but not invoked here — Story 4.1 wires throttled callback dispatch.
        self._progress_callback: Callable[[Any], None] | None = progress_callback
        self._tracker: TopNTracker = TopNTracker(params.n, params.j_max)
        # Sort nodes ascending so start-node sampling is deterministic across
        # Python / networkx versions (dict-insertion order is the FR29 fragility).
        self._nodes: tuple[int, ...] = tuple(sorted(graph.graph.nodes))
        self._cap_rank: int = parse_difficulty_cap(params.difficulty_cap)

    @property
    def best_so_far(self) -> list[Solution]:
        """Current top-N (Architecture §Cat 5b: always-readable anytime view)."""
        return self._tracker.current_top()

    def run(self) -> list[Solution]:
        """Drive `params.iter_budget` GRASP iterations; return final top-N."""
        if not self._nodes:
            return self._tracker.current_top()
        for _ in range(self._params.iter_budget):
            solution = self._construct_one()
            if solution.edges:
                self._tracker.consider(solution)
        return self._tracker.current_top()

    def _construct_one(self) -> Solution:
        """Build one GRASP candidate via greedy-randomized walk extension.

        Emits an **edge-simple** walk: each `(node_u, node_v, key)` is used at
        most once (`used_ids`). Node-revisits via distinct edges are allowed,
        and so are closed walks — including a single self-loop edge `(u, u, k)`,
        which is a valid length-1 route here. Such pathological-but-real OSM
        shapes (lollipop trail-ends, roundabouts) are admitted by design; the
        runtime validator (Story 3.9) owns any policy on rejecting them.

        A start node with no feasible extension yields an empty walk
        (`edges == ()`, `objective == 0.0`); `run()` discards those before they
        reach the tracker.
        """
        start_idx = int(self._rng.integers(0, len(self._nodes)))
        current: int = self._nodes[start_idx]
        path_edges: list[Edge] = []
        used_ids: set[tuple[int, int, int]] = set()
        while True:
            rcl = self._build_rcl(current, used_ids)
            if not rcl:
                break
            choice_idx = int(self._rng.integers(0, len(rcl)))
            chosen = rcl[choice_idx]
            path_edges.append(chosen)
            used_ids.add((chosen.node_u, chosen.node_v, chosen.key))
            current = chosen.node_v
        # `0.0` (not int `0`) on the empty-walk branch — `Solution.objective` is float.
        objective = sum((e.d_plus_m + e.d_minus_m for e in path_edges), 0.0)
        return Solution(edges=tuple(path_edges), objective=objective)

    def _build_rcl(
        self,
        current: int,
        used_ids: set[tuple[int, int, int]],
    ) -> list[Edge]:
        """Restricted candidate list at `current`: top-`RCL_SIZE` feasible extensions.

        Feasibility (same filters as `tests/integration/exhaustive_oracle.py`):

        - Not-yet-used: `(u, v, key)` not in `used_ids` — edge-simple-walk.
        - SAC cap: `max_sac_rank(sac_scale) > cap_rank` rejects. `None` /
          unrecognized values pass (cleared `filter_trails` upstream).
        - Slope floor θ on **super-edges only** (membership test against
          `graph.super_edge_to_base`); plain connectors carry whatever
          gradient their underlying trail has.

        Ranking: by per-edge objective contribution `d_plus_m + d_minus_m`
        descending; ties broken by `(node_v, key)` ascending. This `feasible.sort`
        is the *sole* determinant of order — and hence of FR29 reproducibility —
        so it does not matter what order `nx_graph.out_edges(...)` yields the
        edges in (the dict-insertion order is not a contract, but it is also
        not observed: the final sort fully re-orders). `node_u` is omitted from
        the tie-break because it is always `current` within one call; `(node_v,
        key)` is unique per source node in a `MultiDiGraph`, so the tie-break is
        total.
        """
        nx_graph = self._graph.graph
        super_edges = self._graph.super_edge_to_base
        theta = self._params.theta
        cap_rank = self._cap_rank
        feasible: list[Edge] = []
        for u, v, k, data in nx_graph.out_edges(current, keys=True, data=True):
            eid = (u, v, k)
            if eid in used_ids:
                continue
            rank = max_sac_rank(data["sac_scale"])
            if rank is not None and rank > cap_rank:
                continue
            if eid in super_edges and data["avg_gradient"] < theta:
                continue
            feasible.append(
                Edge(
                    node_u=u,
                    node_v=v,
                    key=k,
                    length_m=data["length_m"],
                    d_plus_m=data["d_plus_m"],
                    d_minus_m=data["d_minus_m"],
                    avg_gradient=data["avg_gradient"],
                    sac_scale=data["sac_scale"],
                )
            )
        feasible.sort(key=lambda e: (-(e.d_plus_m + e.d_minus_m), e.node_v, e.key))
        return feasible[:RCL_SIZE]
