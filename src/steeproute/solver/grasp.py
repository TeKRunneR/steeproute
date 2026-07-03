# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations on `ContractedGraph.graph` surface as Unknown — same
# boundary pattern as `pipeline/` modules and `tests/integration/exhaustive_oracle.py`.
"""GRASP construction loop + anytime best-so-far (Story 3.6).

Implements Architecture §Cat 5's solver shape: a class with an injected RNG,
parameter snapshot, prepared `ContractedGraph`, and a continuously-readable
`best_so_far`. `run()` terminates on three of §Cat 5e's four conditions —
iter-budget, `--time-budget` wall-clock, and `--stagnation-iters` (Story 7.2) —
recording the outcome in `convergence_status`. The fourth, `KeyboardInterrupt`,
is handled at the CLI layer (Story 7.3) per §Cat 5b. The `progress_callback` is
invoked once per iteration with a `ProgressEvent` (Story 7.1); the CLI wraps it
with `progress.throttle(...)` so emission honours `--progress-interval`.

Construction shape
==================

Each GRASP iteration builds **one** candidate route from a randomly-chosen
start node by greedy-randomized walk extension:

1. Sample a start node uniformly at random over the contracted graph's nodes
   (via the injected `numpy.random.Generator`).
2. At each step, build the restricted candidate list (RCL): the outgoing edges
   from the current node that pass the feasibility filters (directed-edge-simple
   + no non-exempt base segment already used + SAC cap + the opt-in FR32 descent
   cap), sorted by per-edge objective contribution (`d_plus_m + d_minus_m`)
   descending, truncated to `RCL_SIZE` entries.
3. Sample one edge uniformly from the RCL; append it; advance the current
   node to its `node_v`.
4. Repeat until the RCL is empty (no feasible extension); the walk emits as a
   `Solution`.

The slope floor θ (FR3) is a **route-level** constraint — the whole-route
average `(Σ d_plus_m + Σ d_minus_m) / Σ length_m` must clear θ — so it is NOT
applied per-edge during construction. It is enforced at finalization in `run()`:
a partial walk may dip below θ and recover by appending a steep climb, so greedy
mid-walk pruning would wrongly discard recoverable routes. Per-climb steepness
lives in the separate `--min-climb-slope` detection threshold (Story 4.1),
upstream in stage 8.

Because construction always extends to a **maximal** walk, that walk's average
can be dragged below θ by a forced flat tail even when a steep **prefix** of it
clears θ. So `run()` offers the best θ-clearing prefix of each constructed walk
to the tracker — `_best_theta_prefix` — rather than only the whole maximal walk
(Story 9.2 / review finding #10). This stops GRASP discarding a feasible route
the exhaustive oracle keeps (the oracle emits every prefix), so GRASP no longer
returns `[]` where a θ-feasible route exists. The longest θ-clearing prefix is
chosen because per-edge `d_plus_m + d_minus_m` is non-negative, so objective is
non-decreasing in prefix length — the longest is the highest-objective one, and
the choice is deterministic with no tie-break (FR29). A prefix of a feasible
walk is itself edge-simple and reuse-respecting, so this keeps GRASP on the same
feasible set the oracle enumerates (Story 3.7 stays apples-to-apples).

The best θ-clearing prefix of each constructed walk is offered to a
`TopNTracker(params.n, params.j_max)`
— the same admission policy the oracle uses (`tests/integration/exhaustive_oracle.py`,
Story 3.5). This is what makes the Story 3.7 GRASP-vs-exhaustive quality
ratio apples-to-apples: identical distinctness semantics on both sides.

Walks obey **undirected base-segment reuse** (Story 5.2, FR5): a route may
traverse any non-exempt physical trail segment at most once, *in either
direction*. The rule keys on the `base_segment_id` tags Story 5.1 wrote at
contraction and is single-sourced through `solver/reuse.py` so GRASP, the
exhaustive oracle, and the validator share one feasible set. Short connectors
(`reusable`, `length_m < l_connector`) are exempt and may recur in both
directions, so loops stay constructible; everything else — climbs and long
connectors — is once-only. This forbids descending the reverse of a climb you
just ascended, eliminating the degenerate out-and-back by construction.
Node-revisits via distinct (non-conflicting) segments are still allowed (Story
3.5 oracle contract). Strict containment (FR10) is guaranteed upstream —
`contract_climbs` cuts the contracted graph to the area before the solver sees
it; no `Area` check is performed here.

Determinism (FR29)
==================

All randomness flows through the injected `numpy.random.Generator`. No
ambient `numpy.random.seed`, no `random` stdlib usage, no time-derived seeds.
The `time.monotonic()` reads in `run()` feed only the `ProgressEvent`'s
`elapsed_s` / ETA — a pure reporting side-effect that never touches the RNG or
the iteration count, so progress timing cannot perturb the route output.
Two `GraspSolver` instances built with `numpy.random.default_rng(seed)` on
the same `ContractedGraph` and `SolverParams` produce byte-identical
`list[Solution]` results — including the edges' traversal order. The two
order-sensitive sites are pinned explicitly so this holds across Python /
networkx versions, where dict-insertion order is not a contract (see
`deferred-work.md` "Story 3.5 deferred #5"):

- **Start-node sampling** draws an index into `tuple(sorted(graph.graph.nodes))`
  (sorted once in `__init__`), so the start node depends only on the RNG, not
  on node-insertion order.
- **RCL ranking** comes from the per-node adjacency table `run()` precomputes
  once per solve (Story 12.1): each node's candidate records are pre-sorted by
  the total key (`-objective`, then `(node_v, key)`), which fully determines
  the candidate order regardless of the order `graph.edges(...)` yields edges
  in during the table build.
"""

from __future__ import annotations

import time
from typing import NamedTuple

import numpy as np

from steeproute.models import (
    ContractedGraph,
    ConvergenceStatus,
    Edge,
    Solution,
    SolverParams,
    route_avg_gradient,
)
from steeproute.pipeline.graph import is_junction_node
from steeproute.pipeline.osm import max_sac_rank, parse_difficulty_cap
from steeproute.progress import ProgressCallback, ProgressEvent, estimate_remaining
from steeproute.solver.descent import descends_over_cap
from steeproute.solver.distinctness import TopNTracker
from steeproute.solver.reuse import (
    base_segment_id_map,
    blocking_ids,
    non_exempt_base_segment_ids,
)

__all__ = ["STAGNATION_ITERS_DEFAULT_PLACEHOLDER", "GraspSolver", "RCL_SIZE"]


STAGNATION_ITERS_DEFAULT_PLACEHOLDER: int = 100
"""Default `--stagnation-iters` window when the flag is unset (Story 7.2).

PROVISIONAL — tune empirically before release by observing convergence behaviour
on the metamorphic suite, the `test_time_budget.py` / `test_stagnation.py`
integration tests, and real Grenoble-fixture runs. 100 consecutive iterations
without a top-N total-objective improvement is a first guess: large enough that
a still-productive search isn't cut short, small enough that a plateaued search
on a sparse area stops well inside NFR1's budget. `--stagnation-iters 0` disables
the check entirely (Architecture §Cat 5e).
"""


RCL_SIZE: int = 5
"""Restricted candidate list cap (cardinality-based GRASP).

Pinned module-scope for FR29 determinism — Epic 4 may surface this as a CLI
flag once the Story 3.7 quality gate establishes a baseline. Five is the
classic "small but not greedy" default; smaller values starve diversity,
larger values approach uniform-random construction.
"""


class _CandidateRecord(NamedTuple):
    """One pre-built RCL candidate in the per-node adjacency table (Story 12.1).

    Everything about a candidate that does not depend on walk state, computed
    once per solve so `_build_rcl` never touches the networkx graph, never
    re-wraps `Edge` objects, and never recomputes blocking sets in the hot loop.
    `directed_id` is `(node_u, node_v, key)` — pre-built so the `used_directed`
    membership test needs no per-step tuple construction.
    """

    directed_id: tuple[int, int, int]
    edge: Edge
    blocking: frozenset[tuple[int, int, int]]


class GraspSolver:
    """GRASP solver driving construction + restart for FR10 / FR11 / FR29.

    Constructor stores references — `params` / `graph` are immutable
    (`frozen=True, slots=True`) and the injected `rng` is the solver's sole
    randomness source. The internal `TopNTracker` is built eagerly so
    `best_so_far` is readable before `run()` is called (and returns `[]`).

    `run()` drives GRASP iterations until any of three termination conditions
    fires (Architecture §Cat 5e) — iter-budget, `--time-budget` wall-clock, or
    `--stagnation-iters` consecutive iterations without a top-N improvement — and
    returns the final `tracker.current_top()`. It records which one fired in the
    public `convergence_status` attribute (`converged` on stagnation,
    `budget-exhausted` on iter/time budget). It does not catch
    `KeyboardInterrupt` — Architecture §Cat 5b puts that at the CLI layer, where
    Story 7.3 sets the third status value (`interrupted`).
    """

    def __init__(
        self,
        graph: ContractedGraph,
        params: SolverParams,
        rng: np.random.Generator,
        progress_callback: ProgressCallback | None = None,
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
        # Invoked once per iteration in `run()` (Story 7.1). The CLI passes a
        # `progress.throttle(...)`-wrapped renderer; `None` disables emission
        # (e.g. `--quiet`, or non-CLI callers like the quality-gate tests).
        self._progress_callback: ProgressCallback | None = progress_callback
        # Undirected base-segment distinctness (Story 6.1): the tracker keys
        # Jaccard on the same `base_segment_id` identity the reuse rule uses, so
        # opposite-direction reuse of one trail counts as overlap. Single-sourced
        # with the oracle + validator via `solver/reuse.py`.
        self._segment_map: dict[tuple[int, int, int], frozenset[tuple[int, int, int]]] = (
            base_segment_id_map(graph)
        )
        self._tracker: TopNTracker = TopNTracker(params.n, params.j_max, self._segment_map)
        # Seed-node pool. Sorted ascending so start-node sampling is deterministic
        # across Python / networkx versions (dict-insertion order is the FR29
        # fragility). Under `--start-at-junction` (FR31, Story 10.1) the pool is
        # pruned to road/trail junction nodes via the shared `is_junction_node`
        # predicate — the same one the oracle and validator use, so all three stay
        # on one feasible set. This restriction is an *efficiency/guidance* prune,
        # not the constraint's enforcement: FR31 is enforced by the validator's
        # independent `start_at_junction` check on `edges[0].node_u`, which holds
        # whatever the solver does. An empty pool (no junctions) makes `run()`
        # return `[]` via its existing `if not self._nodes` guard — correct FR12.
        all_nodes = sorted(graph.graph.nodes)
        if params.start_at_junction:
            nodes = [n for n in all_nodes if is_junction_node(graph, n)]
        else:
            nodes = all_nodes
        self._nodes: tuple[int, ...] = tuple(nodes)
        self._cap_rank: int = parse_difficulty_cap(params.difficulty_cap)
        # Direction-aware descent cap (FR32, Story 10.2). `None` → off; when set,
        # `_build_rcl` drops any descending candidate edge steeper than this, via
        # the `solver.descent` predicate single-sourced with the oracle + validator.
        self._max_descent_slope: float | None = params.max_descent_slope
        # Base-segment ids subject to the once-only reuse rule, computed once per
        # graph (Story 5.2). Single-sourced with the oracle + validator via
        # `solver/reuse.py` so all three share one feasible set.
        self._non_exempt_ids: frozenset[tuple[int, int, int]] = non_exempt_base_segment_ids(graph)
        # Termination outcome (§Cat 5e). Initialised to the iter-budget outcome so
        # the attribute is always readable/typed — including after the empty-graph
        # early return in `run()` — and set definitively at each termination
        # branch. `interrupted` is never set here; Story 7.3's CLI handler owns it.
        self.convergence_status: ConvergenceStatus = "budget-exhausted"
        # 1-based iteration of the last admission — the last time the top-N held
        # set changed (`tracker.consider()` returned `True`), Story 7.3.
        # Anytime-readable like `best_so_far`/`convergence_status`, so it holds the
        # right value on every termination path, *including* a `KeyboardInterrupt`
        # that unwinds `run()` and discards its locals. `0` means no admission ever
        # landed (empty graph, no admissible route, or interrupt before the first
        # admission). It equals `(i + 1) − stagnation_counter` at any point, since
        # the stagnation counter resets to 0 exactly when an admission lands.
        self.convergence_iteration: int = 0
        # Per-node adjacency table of pre-built candidate records (Story 12.1).
        # Empty until `run()` builds it once per solve — solver instances are
        # single-run, and building it inside `run()` keeps the (one-off) cost
        # inside the benchmark suite's measured region.
        self._adjacency: dict[int, tuple[_CandidateRecord, ...]] = {}

    @property
    def best_so_far(self) -> list[Solution]:
        """Current top-N (Architecture §Cat 5b: always-readable anytime view)."""
        return self._tracker.current_top()

    def run(self) -> list[Solution]:
        """Drive GRASP iterations to the first §Cat 5e termination; return final top-N.

        Three conditions stop the loop, checked *between* iterations (the
        in-flight iteration always finishes — the budgets are soft):

        - **iter-budget** — the `for` exhausts `params.iter_budget` → `convergence_status = "budget-exhausted"`.
        - **time-budget** — monotonic elapsed reaches `params.time_budget` → `"budget-exhausted"`.
        - **stagnation** — no candidate is admitted to the top-N for
          `params.stagnation_iters` consecutive iterations → `"converged"`.
          `stagnation_iters == 0` disables it. The window only ever fires after
          the tracker has filled: while candidates are still being admitted the
          counter keeps resetting, so the check self-activates after the first
          N+1 iterations (Architecture §Cat 5e) with no special casing.
          Stagnation is checked before time so a search that has truly converged
          is labelled `converged` even if it also just crossed the clock.

        `stagnation_counter` counts consecutive iterations with no admission —
        it resets exactly when `tracker.consider()` returns `True` (the held set
        changed), so this is exactly "iterations since the last admission". It is
        driven off that verdict, not a top-N total-objective delta: the
        evict-many-admit-one branch can change the held set while leaving the
        total equal (or lowering it), so a delta would miscount. This
        bookkeeping (and the monotonic-clock reads) now runs every iteration
        because it gates termination, not just the `ProgressEvent`; only the
        event *construction* stays behind the callback check. FR29 still holds:
        the clock reads feed `elapsed_s` / the ETA / the time-budget comparison
        only — never the RNG, `_construct_one`, or the admission sequence. So a
        fixed seed yields a byte-identical iteration *sequence*; only the *count*
        is wall-clock-dependent, and solely when the soft time-budget binds.
        """
        if not self._nodes:
            return self._tracker.current_top()
        # Once-per-solve precompute (Story 12.1): the contracted graph is
        # immutable for the duration of a solve, so every walk-state-independent
        # part of RCL construction is hoisted out of the hot loop here.
        self._adjacency = self._build_adjacency()
        callback = self._progress_callback
        stagnation_iters = self._params.stagnation_iters
        time_budget = self._params.time_budget
        start = time.monotonic()
        stagnation_counter = 0
        for i in range(self._params.iter_budget):
            solution = self._construct_one()
            # Drive stagnation off the tracker's admission verdict, NOT a
            # total-objective delta. The two are not equivalent: the evict-many-
            # admit-one branch can admit a candidate that leaves the total
            # unchanged (a delta would read it as stagnant) or even lowers it (a
            # delta would read it as an improvement). `consider()` returns True iff
            # the held set actually changed, so this counter is exactly
            # "iterations since the last admission".
            # Offer the best θ-clearing prefix of the constructed walk (Story 9.2),
            # not just the maximal walk: a steep prefix forced to append a flat tail
            # would otherwise drag the whole-walk average below θ and be discarded,
            # losing a feasible route the oracle keeps. `_best_theta_prefix` returns
            # None when no prefix clears θ (including the empty walk).
            admitted = False
            candidate = self._best_theta_prefix(solution.edges)
            if candidate is not None:
                admitted = self._tracker.consider(candidate)
            if admitted:
                stagnation_counter = 0
                # Record where the held set last changed (Story 7.3). Held on
                # `self` (not a local) so an interrupt mid-loop preserves it.
                self.convergence_iteration = i + 1
            else:
                stagnation_counter += 1
            elapsed_s = time.monotonic() - start
            if callback is not None:
                iteration = i + 1
                callback(
                    ProgressEvent(
                        iteration=iteration,
                        elapsed_s=elapsed_s,
                        best_objective=self._tracker.total_objective(),
                        estimated_remaining_s=estimate_remaining(
                            iteration, self._params.iter_budget, elapsed_s
                        ),
                        stagnation_counter=stagnation_counter,
                    )
                )
            if stagnation_iters > 0 and stagnation_counter >= stagnation_iters:
                self.convergence_status = "converged"
                return self._tracker.current_top()
            if elapsed_s >= time_budget:
                self.convergence_status = "budget-exhausted"
                return self._tracker.current_top()
        self.convergence_status = "budget-exhausted"
        return self._tracker.current_top()

    def _route_slope_ok(self, edges: tuple[Edge, ...]) -> bool:
        """Route-level slope floor (FR3): True iff `(Σd+ + Σd−)/Σlength ≥ θ`.

        The binding constraint is the *whole-route* average gradient, enforced
        at finalization rather than greedily in `_build_rcl` — a partial walk
        may legitimately dip below θ and recover by appending a steep climb, so
        mid-construction pruning would wrongly kill recoverable routes. The
        ratio is single-sourced through `models.route_avg_gradient` — the same
        function the validator's `slope_floor` check uses — so the validator can
        never flag a GRASP-admitted route over a float-summation discrepancy. An
        empty/zero-length route yields gradient `0.0` and is rejected at any
        positive θ.
        """
        return route_avg_gradient(edges) >= self._params.theta

    def _best_theta_prefix(self, edges: tuple[Edge, ...]) -> Solution | None:
        """Longest θ-clearing prefix of `edges` as a `Solution`, or `None` if none clears θ.

        A maximal walk can dip below θ when a forced flat tail follows a steep
        start (review finding #10); offering the whole walk alone would discard a
        feasible route. Every prefix of a feasible walk is itself a feasible walk
        (edge-simple, reuse-respecting), and the exhaustive oracle enumerates
        every prefix, so recovering the best θ-clearing prefix keeps GRASP on one
        feasible set with the oracle (Story 9.2).

        The **longest** θ-clearing prefix is the best: per-edge `d_plus_m +
        d_minus_m` is non-negative, so the objective is non-decreasing in prefix
        length and the longest qualifying prefix carries the highest objective.
        Scanning from the full length downward returns it on the first hit — a
        single, deterministic answer with no tie-break, so FR29 holds (this is a
        pure function of the already-deterministic walk; no RNG). The empty walk
        has no non-empty prefix and yields `None`, so `run()`'s old
        `if solution.edges` guard is subsumed.
        """
        for end in range(len(edges), 0, -1):
            prefix = edges[:end]
            if self._route_slope_ok(prefix):
                objective = sum((e.d_plus_m + e.d_minus_m for e in prefix), 0.0)
                return Solution(edges=prefix, objective=objective)
        return None

    def _construct_one(self) -> Solution:
        """Build one GRASP candidate via greedy-randomized walk extension.

        Emits a walk obeying **undirected base-segment reuse** (Story 5.2): each
        non-exempt base segment is traversed at most once, in either direction
        (`used_segments`). The walk additionally stays **directed-edge-simple**
        (`used_directed`) — no `(node_u, node_v, key)` triple twice — which is
        what guarantees termination: exempt short connectors don't block on a
        segment, so without the directed-simple bound a reusable connector could
        be walked `a→b→a→b…` forever. An exempt connector is therefore bounded by
        the directed-simple rule (each directed `(u, v, key)` at most once) rather
        than the once-only segment rule — so a simple two-node connector recurs at
        most twice (once per direction), while parallel keys over the same exempt
        segment may each appear once. A non-exempt segment is used at most once.
        Taking an edge records its directed id and its non-exempt base ids. Node-revisits via distinct non-conflicting segments are allowed, and
        so are closed walks — including a single self-loop edge `(u, u, k)`, a
        valid length-1 route. Such pathological-but-real OSM shapes (lollipop
        trail-ends, roundabouts) are admitted by design; the runtime validator
        (Story 3.9) owns any policy on rejecting them.

        A start node with no feasible extension yields an empty walk
        (`edges == ()`, `objective == 0.0`); `run()` discards those before they
        reach the tracker.
        """
        start_idx = int(self._rng.integers(0, len(self._nodes)))
        current: int = self._nodes[start_idx]
        path_edges: list[Edge] = []
        used_directed: set[tuple[int, int, int]] = set()
        used_segments: set[tuple[int, int, int]] = set()
        while True:
            rcl = self._build_rcl(current, used_directed, used_segments)
            if not rcl:
                break
            choice_idx = int(self._rng.integers(0, len(rcl)))
            chosen, chosen_blocking = rcl[choice_idx]
            path_edges.append(chosen)
            used_directed.add((chosen.node_u, chosen.node_v, chosen.key))
            used_segments |= chosen_blocking
            current = chosen.node_v
        # `0.0` (not int `0`) on the empty-walk branch — `Solution.objective` is float.
        objective = sum((e.d_plus_m + e.d_minus_m for e in path_edges), 0.0)
        return Solution(edges=tuple(path_edges), objective=objective)

    def _build_adjacency(self) -> dict[int, tuple[_CandidateRecord, ...]]:
        """Per-node adjacency table of pre-built candidate records (Story 12.1).

        One pass over the immutable contracted graph, hoisting everything about
        RCL construction that does not depend on walk state — the 11.2 profile
        attributed ~35–40% of query wall-clock to redoing this per step:

        - **Static filters applied once.** SAC cap (`max_sac_rank(sac_scale) >
          cap_rank` rejects; `None` / unrecognized values pass — cleared
          `filter_trails` upstream) and the direction-aware descent cap (FR32;
          off when unset). Both read only edge data and per-solve params, so an
          edge failing them can never become feasible and is dropped from the
          table outright — exactly the edges the old per-step loop re-rejected
          on every visit.
        - **`Edge` built once** per graph edge instead of re-wrapped per visit
          (`Edge` is frozen, so sharing one instance across RCLs/solutions is
          safe), alongside its pre-built `directed_id` triple.
        - **Blocking sets computed once** — single-sourced via
          `solver.reuse.blocking_ids` against `self._non_exempt_ids`, same as
          before.
        - **Static sort applied once per node**: by per-edge objective
          contribution `d_plus_m + d_minus_m` descending, ties broken by
          `(node_v, key)` ascending. The key is static per edge and total —
          `node_u` is omitted because it is constant within a node's records,
          and `(node_v, key)` is unique per source node in a `MultiDiGraph` —
          so this pre-sort fully determines candidate order (FR29) regardless
          of the order `graph.edges(...)` yields edges in.

        Nodes with no surviving out-edges are simply absent; `_build_rcl` reads
        with `.get(current, ())`.
        """
        cap_rank = self._cap_rank
        grouped: dict[int, list[_CandidateRecord]] = {}
        for u, v, k, data in self._graph.graph.edges(keys=True, data=True):
            rank = max_sac_rank(data["sac_scale"])
            if rank is not None and rank > cap_rank:
                continue
            if descends_over_cap(data, self._max_descent_slope):
                continue
            record = _CandidateRecord(
                directed_id=(u, v, k),
                edge=Edge(
                    node_u=u,
                    node_v=v,
                    key=k,
                    length_m=data["length_m"],
                    d_plus_m=data["d_plus_m"],
                    d_minus_m=data["d_minus_m"],
                    avg_gradient=data["avg_gradient"],
                    sac_scale=data["sac_scale"],
                ),
                blocking=blocking_ids(data, u, v, k, self._non_exempt_ids),
            )
            grouped.setdefault(u, []).append(record)
        for records in grouped.values():
            records.sort(
                key=lambda r: (-(r.edge.d_plus_m + r.edge.d_minus_m), r.edge.node_v, r.edge.key)
            )
        return {u: tuple(records) for u, records in grouped.items()}

    def _build_rcl(
        self,
        current: int,
        used_directed: set[tuple[int, int, int]],
        used_segments: set[tuple[int, int, int]],
    ) -> list[tuple[Edge, frozenset[tuple[int, int, int]]]]:
        """Restricted candidate list at `current`: top-`RCL_SIZE` feasible extensions.

        Returns `(edge, blocking_ids)` pairs — the blocking ids ride along so
        `_construct_one` records them on the chosen edge without a second graph
        lookup.

        Consumes the pre-sorted per-node table `run()` built once per solve
        (`_build_adjacency`, Story 12.1) — no graph access, no `Edge`
        construction, no set math beyond the walk-state checks, no sorting.
        Only the two walk-state-dependent filters run here (same feasibility as
        `tests/integration/exhaustive_oracle.py`):

        - Directed edge-simple: rejected iff `directed_id` is already in
          `used_directed` (→ termination).
        - Undirected base-segment once-only (Story 5.2): rejected iff any
          blocking id is already in `used_segments`. An exempt short connector
          has an empty blocking set, so only the directed-simple bound limits
          it (to once per direction).

        Because each node's records are pre-sorted by the total static key,
        collecting the first `RCL_SIZE` survivors in table order is identical
        to the old filter-everything → sort → truncate — same candidates, same
        order (FR29).

        The slope floor θ is **not** an RCL filter: it is a route-level
        constraint enforced at finalization (`run` / `_route_slope_ok`), not a
        per-edge one. Every edge that clears the two filters above is a
        candidate regardless of its own gradient.
        """
        rcl: list[tuple[Edge, frozenset[tuple[int, int, int]]]] = []
        for directed_id, edge, blocking in self._adjacency.get(current, ()):
            if directed_id in used_directed:
                continue
            if blocking & used_segments:
                continue
            rcl.append((edge, blocking))
            if len(rcl) == RCL_SIZE:
                break
        return rcl
