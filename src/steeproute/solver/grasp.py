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
   + no non-exempt base segment already used + SAC cap), sorted by per-edge
   objective contribution (`d_plus_m + d_minus_m`) descending, truncated to
   `RCL_SIZE` entries.
3. Sample one edge uniformly from the RCL; append it; advance the current
   node to its `node_v`.
4. Repeat until the RCL is empty (no feasible extension); the walk emits as a
   `Solution`.

The slope floor θ (FR3) is a **route-level** constraint — the whole-route
average `(Σ d_plus_m + Σ d_minus_m) / Σ length_m` must clear θ — so it is NOT
applied per-edge during construction. It is enforced at finalization in `run()`
(`_route_slope_ok`): a partial walk may dip below θ and recover by appending a
steep climb, so greedy mid-walk pruning would wrongly discard recoverable
routes. Per-climb steepness lives in the separate `--min-climb-slope`
detection threshold (Story 4.1), upstream in stage 8.

Each completed `Solution` that clears the route-level floor is offered to a
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
- **RCL ranking** ends in a total sort (`-objective`, then `(node_v, key)`),
  which fully determines the candidate order regardless of the order
  `nx_graph.out_edges(...)` yields edges in.
"""

from __future__ import annotations

import time

import numpy as np

from steeproute.models import (
    ContractedGraph,
    ConvergenceStatus,
    Edge,
    Solution,
    SolverParams,
    route_avg_gradient,
)
from steeproute.pipeline.osm import max_sac_rank, parse_difficulty_cap
from steeproute.progress import ProgressCallback, ProgressEvent, estimate_remaining
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
        # Sort nodes ascending so start-node sampling is deterministic across
        # Python / networkx versions (dict-insertion order is the FR29 fragility).
        self._nodes: tuple[int, ...] = tuple(sorted(graph.graph.nodes))
        self._cap_rank: int = parse_difficulty_cap(params.difficulty_cap)
        # Base-segment ids subject to the once-only reuse rule, computed once per
        # graph (Story 5.2). Single-sourced with the oracle + validator via
        # `solver/reuse.py` so all three share one feasible set.
        self._non_exempt_ids: frozenset[tuple[int, int, int]] = non_exempt_base_segment_ids(graph)
        # Termination outcome (§Cat 5e). Initialised to the iter-budget outcome so
        # the attribute is always readable/typed — including after the empty-graph
        # early return in `run()` — and set definitively at each termination
        # branch. `interrupted` is never set here; Story 7.3's CLI handler owns it.
        self.convergence_status: ConvergenceStatus = "budget-exhausted"
        # 1-based iteration at which the top-N total objective last improved — the
        # last admission that changed it (Story 7.3). Anytime-readable like
        # `best_so_far`/`convergence_status`, so it holds the right value on every
        # termination path, *including* a `KeyboardInterrupt` that unwinds `run()`
        # and discards its locals. `0` means no improvement ever landed (empty
        # graph, no admissible route, or interrupt before the first admission). It
        # equals `(i + 1) − stagnation_counter` at any point, since the stagnation
        # counter resets to 0 exactly when an improvement lands.
        self.convergence_iteration: int = 0

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
        - **stagnation** — the top-N total objective is unchanged for
          `params.stagnation_iters` consecutive iterations → `"converged"`.
          `stagnation_iters == 0` disables it. The window only ever fires after
          the tracker has filled: while admissions are still improving the
          objective the counter keeps resetting, so the check self-activates
          after the first N+1 iterations (Architecture §Cat 5e) with no special
          casing. Stagnation is checked before time so a search that has truly
          converged is labelled `converged` even if it also just crossed the
          clock.

        `stagnation_counter` counts consecutive iterations whose top-N total
        objective was unchanged — the tracker's value changes iff a candidate was
        admitted, so this is exactly "iterations since the last admission". This
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
        callback = self._progress_callback
        stagnation_iters = self._params.stagnation_iters
        time_budget = self._params.time_budget
        start = time.monotonic()
        last_objective = self._tracker.total_objective()
        stagnation_counter = 0
        for i in range(self._params.iter_budget):
            solution = self._construct_one()
            if solution.edges and self._route_slope_ok(solution):
                self._tracker.consider(solution)
            current_objective = self._tracker.total_objective()
            if current_objective != last_objective:
                stagnation_counter = 0
                last_objective = current_objective
                # Record where the last real improvement landed (Story 7.3). Held
                # on `self` (not a local) so an interrupt mid-loop preserves it.
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
                        best_objective=current_objective,
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

    def _route_slope_ok(self, solution: Solution) -> bool:
        """Route-level slope floor (FR3): admit iff `(Σd+ + Σd−)/Σlength ≥ θ`.

        The binding constraint is the *whole-route* average gradient, enforced
        here at finalization rather than greedily in `_build_rcl` — a partial
        walk may legitimately dip below θ and recover by appending a steep
        climb, so mid-construction pruning would wrongly kill recoverable
        routes. The ratio is single-sourced through `models.route_avg_gradient`
        — the same function the validator's `slope_floor` check uses — so the
        validator can never flag a GRASP-admitted route over a float-summation
        discrepancy. An empty/zero-length route yields gradient `0.0` and is
        rejected at any positive θ.
        """
        return route_avg_gradient(solution.edges) >= self._params.theta

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

        Feasibility (same filters as `tests/integration/exhaustive_oracle.py`):

        - Reuse: an edge is rejected iff its directed `(u, v, key)` is already in
          `used_directed` (edge-simple → termination) OR any of its non-exempt
          base-segment ids is already in `used_segments` (undirected
          base-segment once-only, Story 5.2). The blocking-id set is
          single-sourced via `solver.reuse.blocking_ids` against
          `self._non_exempt_ids`; an exempt short connector has an empty blocking
          set, so only the directed-simple bound limits it (to once per
          direction).
        - SAC cap: `max_sac_rank(sac_scale) > cap_rank` rejects. `None` /
          unrecognized values pass (cleared `filter_trails` upstream).

        The slope floor θ is **not** an RCL filter: it is a route-level
        constraint enforced at finalization (`run` / `_route_slope_ok`), not a
        per-edge one. Every edge that clears the two filters above is a
        candidate regardless of its own gradient.

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
        cap_rank = self._cap_rank
        feasible: list[tuple[Edge, frozenset[tuple[int, int, int]]]] = []
        for u, v, k, data in nx_graph.out_edges(current, keys=True, data=True):
            if (u, v, k) in used_directed:
                continue
            blocking = blocking_ids(data, u, v, k, self._non_exempt_ids)
            if blocking & used_segments:
                continue
            rank = max_sac_rank(data["sac_scale"])
            if rank is not None and rank > cap_rank:
                continue
            feasible.append(
                (
                    Edge(
                        node_u=u,
                        node_v=v,
                        key=k,
                        length_m=data["length_m"],
                        d_plus_m=data["d_plus_m"],
                        d_minus_m=data["d_minus_m"],
                        avg_gradient=data["avg_gradient"],
                        sac_scale=data["sac_scale"],
                    ),
                    blocking,
                )
            )
        feasible.sort(
            key=lambda pair: (-(pair[0].d_plus_m + pair[0].d_minus_m), pair[0].node_v, pair[0].key)
        )
        return feasible[:RCL_SIZE]
