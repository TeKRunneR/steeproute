# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations on `ContractedGraph.graph` surface as Unknown — same
# boundary pattern as `pipeline/` modules and `tests/integration/exhaustive_oracle.py`.
"""GRASP construction loop with a continuously-readable best-so-far.

Implements Architecture §Cat 5's solver shape: a class with an injected RNG, a
parameter snapshot, a prepared `ContractedGraph`, and an anytime `best_so_far`.
`run()` terminates on three of §Cat 5e's four conditions — iter-budget,
`--time-budget` wall-clock, and `--stagnation-iters` — recording the outcome in
`convergence_status`. The fourth, `KeyboardInterrupt`, is handled at the CLI
layer per §Cat 5b. The `progress_callback` is invoked once per iteration with a
`ProgressEvent`; the CLI wraps it with `progress.throttle(...)` so emission
honours `--progress-interval`.

Construction shape
==================

Each GRASP iteration builds **one** candidate route from a randomly-chosen
start node by greedy-randomized walk extension:

1. Sample a start node uniformly at random over the contracted graph's nodes
   (from the injected `numpy.random.Generator`, via the chunked draw buffer —
   see "Determinism" below).
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
average `(Σ d_plus_m + Σ d_minus_m) / Σ length_m` must clear θ — so it is NOT an
RCL filter; it is enforced at finalization (`_route_slope_ok`, which holds the
argument for why). Per-climb steepness is a separate concern, the
`--min-climb-slope` detection threshold upstream in stage 8.

Because construction always extends to a **maximal** walk, that walk's average
can be dragged below θ by a forced flat tail even when a steep **prefix** of it
clears θ. So `run()` offers the best θ-clearing prefix of each constructed walk
to the tracker rather than only the whole maximal walk; `_best_theta_prefix`
holds the argument for why the *longest* qualifying prefix is the right one. A
prefix of a feasible walk is itself edge-simple and reuse-respecting, and the
exhaustive oracle enumerates every prefix, so this keeps GRASP on the same
feasible set the oracle does — without it, GRASP discards θ-feasible routes the
oracle keeps.

Admission goes through `TopNTracker(params.n, params.j_max)` — the same policy
`tests/integration/exhaustive_oracle.py` applies. That is what makes the
GRASP-vs-exhaustive quality ratio apples-to-apples: identical distinctness
semantics on both sides.

Walks obey **undirected base-segment reuse** (FR5): a route may traverse any
non-exempt physical trail segment at most once, *in either direction*. The rule
keys on the `base_segment_id` tags written at contraction and is single-sourced
through `solver/reuse.py` so GRASP, the exhaustive oracle, and the validator
share one feasible set. Short connectors (`reusable`, `length_m < l_connector`)
are exempt and may recur in both directions, so loops stay constructible;
everything else — climbs and long connectors — is once-only. This forbids
descending the reverse of a climb you just ascended, eliminating the degenerate
out-and-back by construction. Node-revisits via distinct (non-conflicting)
segments are still allowed. Strict containment (FR10) is guaranteed upstream —
`contract_climbs` cuts the contracted graph to the area before the solver sees
it; no `Area` check is performed here.

Determinism (FR29)
==================

All randomness flows through the injected `numpy.random.Generator`. No ambient
`numpy.random.seed`, no `random` stdlib usage, no time-derived seeds. Two
`GraspSolver` instances built with `numpy.random.default_rng(seed)` on the same
`ContractedGraph` and `SolverParams` produce byte-identical `list[Solution]`
results — including the edges' traversal order.

Draws are **batched**: `_next_uniform` consumes uniform `[0, 1)` values from a
buffer refilled by one native `rng.random(_RNG_CHUNK)` call per `_RNG_CHUNK`
draws, because one scalar `Generator` call per walk step cost ~13% of query
wall-clock — pure numpy per-call boundary overhead, no compute (py-spy, r6
Grenoble area, 2026-07-03). A bounded index in `0..n-1` is derived as
`int(u * n)` — uniform up to float64 granularity, which is exact for every `n`
this solver sees (`n ≤` node count `< 2^53`, so `int(u * n) < n` always holds
and no bounds clamp is needed). The buffer refills only on exhaustion — never on
a clock, callback, or termination condition — so a fixed seed yields a
byte-identical iteration sequence.

The `time.monotonic()` reads in `run()` feed only the `ProgressEvent`'s
`elapsed_s` / ETA and the time-budget comparison — a pure reporting/termination
side-effect that never touches the RNG or the iteration *content*, so progress
timing cannot perturb the route output.

Two order-sensitive sites are pinned explicitly, because dict-insertion order is
not a contract across Python / networkx versions:

- **Start-node sampling** draws an index into `tuple(sorted(graph.graph.nodes))`
  (sorted once in `__init__`), so the start node depends only on the RNG, not
  on node-insertion order.
- **RCL ranking** comes from the per-node adjacency table `run()` precomputes
  once per solve: each node's candidate records are pre-sorted by the total key
  (`-objective`, then `(node_v, key)`), which fully determines the candidate
  order regardless of the order `graph.edges(...)` yields edges in during the
  table build.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
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
from steeproute.solver.distinctness import SegmentMap, TopNTracker
from steeproute.solver.reuse import (
    base_segment_id_map,
    blocking_ids,
    non_exempt_base_segment_ids,
)

__all__ = [
    "STAGNATION_ITERS_DEFAULT_PLACEHOLDER",
    "AdjacencyTable",
    "GraspSolver",
    "RCL_SIZE",
    "SolverStaticContext",
]


STAGNATION_ITERS_DEFAULT_PLACEHOLDER: int = 200_000
"""Default `--stagnation-iters` window when the flag is unset.

Sized to the real manual-run/demo/gallery params (AGENTS.md §Solver / GRASP):
large enough that a still-productive search at the 1_000_000 default
`--iter-budget` isn't cut short, small enough that a plateaued search on a
sparse area still stops well inside NFR1's budget. `--stagnation-iters 0`
disables the check entirely (Architecture §Cat 5e).
"""


RCL_SIZE: int = 5
"""Restricted candidate list cap (cardinality-based GRASP).

Pinned module-scope rather than exposed as a CLI flag, so it cannot vary per
run and perturb FR29 determinism. Five is the classic "small but not greedy"
default; smaller values starve diversity, larger values approach
uniform-random construction.
"""


_RNG_CHUNK: int = 1024
"""Draws per native `Generator.random` call in the batched scheme.

Large enough to amortize the numpy call boundary to noise (one native call per
1024 draws), small enough that the refill itself is microseconds and the
never-consumed tail of the final chunk is a trivial overdraw. Not a tuning
knob: changing it does NOT change solver output — the consumed value sequence
is the generator's `random()` stream in order, independent of how it is
chunked (`Generator.random` produces one float64 per stream step regardless of
the requested size).
"""


class _CandidateRecord(NamedTuple):
    """One pre-built RCL candidate in the per-node adjacency table.

    Everything about a candidate that does not depend on walk state, computed
    once per solve so `_build_rcl` never touches the networkx graph, never
    re-wraps `Edge` objects, and never recomputes blocking sets in the hot loop.
    `directed_id` is `(node_u, node_v, key)` — pre-built so the `used_directed`
    membership test needs no per-step tuple construction.
    """

    directed_id: tuple[int, int, int]
    edge: Edge
    blocking: frozenset[tuple[int, int, int]]


AdjacencyTable = Mapping[int, tuple[_CandidateRecord, ...]]
"""Per-node pre-built RCL candidate table (`_build_adjacency`'s output).

A pure function of the contracted graph + the SAC/descent filter params, so it is
identical across a parallel worker's migration rounds and can be built once and
reused. Exposed as a named type so `solver/parallel.py` can cache and pass it
back without naming the private `_CandidateRecord`.
"""


@dataclass(frozen=True, slots=True)
class SolverStaticContext:
    """Read-only graph-derived state reusable across compatible solver runs.

    The contracted graph is immutable for a query. Its node pool, reuse maps,
    filter snapshot, and adjacency therefore need deriving only once per worker,
    even when island migration creates a fresh `GraspSolver` each round. Mapping
    proxies make the public cache read-only without copying its large dicts.
    """

    graph: ContractedGraph
    start_at_junction: bool
    difficulty_cap: str
    max_descent_slope: float | None
    nodes: tuple[int, ...]
    segment_map: Mapping[tuple[int, int, int], frozenset[tuple[int, int, int]]]
    non_exempt_ids: frozenset[tuple[int, int, int]]
    adjacency: AdjacencyTable

    def assert_compatible(self, graph: ContractedGraph, params: SolverParams) -> None:
        """Reject a cache built for any graph/filter configuration mismatch."""
        if graph is not self.graph:
            raise ValueError("static context graph is not the exact solver graph object")
        if params.start_at_junction != self.start_at_junction:
            raise ValueError("static context start-at-junction parameter is incompatible")
        if params.difficulty_cap != self.difficulty_cap:
            raise ValueError("static context difficulty-cap parameter is incompatible")
        if params.max_descent_slope != self.max_descent_slope:
            raise ValueError("static context max-descent-slope parameter is incompatible")


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
    `KeyboardInterrupt` — Architecture §Cat 5b puts that at the CLI layer, which
    owns the third status value (`interrupted`).
    """

    def __init__(
        self,
        graph: ContractedGraph,
        params: SolverParams,
        rng: np.random.Generator,
        progress_callback: ProgressCallback | None = None,
        initial_solutions: list[Solution] | None = None,
        adjacency: AdjacencyTable | None = None,
        static_context: SolverStaticContext | None = None,
    ) -> None:
        if params.iter_budget < 1:
            # Fail loud at the boundary, symmetric with `TopNTracker`'s `n >= 1`
            # guard. A 0/negative budget would otherwise make `run()` silently
            # return `[]` — indistinguishable from "searched and found nothing",
            # which would mislead the GRASP-vs-exhaustive quality-ratio
            # comparator on a misconfigured budget.
            raise ValueError(f"iter_budget must be >= 1, got {params.iter_budget}")
        self._graph: ContractedGraph = graph
        self._params: SolverParams = params
        self._rng: np.random.Generator = rng
        # Invoked once per iteration in `run()`. The CLI passes a
        # `progress.throttle(...)`-wrapped renderer; `None` disables emission
        # (e.g. `--quiet`, or non-CLI callers like the quality-gate tests).
        self._progress_callback: ProgressCallback | None = progress_callback
        if adjacency is not None and static_context is not None:
            raise ValueError("static_context and adjacency cannot both be supplied")
        if static_context is not None:
            static_context.assert_compatible(graph, params)
        # Undirected base-segment distinctness: the tracker keys Jaccard on the
        # same `base_segment_id` identity the reuse rule uses, so
        # opposite-direction reuse of one trail counts as overlap. Single-sourced
        # with the oracle + validator via `solver/reuse.py`.
        if static_context is not None:
            self._segment_map: SegmentMap = static_context.segment_map
            self._segment_map_view: SegmentMap = static_context.segment_map
        else:
            segment_map = base_segment_id_map(graph)
            self._segment_map = segment_map
            self._segment_map_view = MappingProxyType(segment_map)
        self._tracker: TopNTracker = TopNTracker(params.n, params.j_max, self._segment_map)
        # Elite migration (parallel island model): pre-seed the tracker with
        # solutions merged from other workers' previous rounds, so this worker only
        # *keeps* routes that beat the shared global elite (construction itself is
        # unaffected — it's memoryless random restart). This bounds the parallel
        # downside: with periodic migration, workers converge toward one shared
        # elite instead of drifting into independent, redundant local optima.
        # `None` (the default, and every single-process caller) leaves the tracker
        # empty. The tracker is order-sensitive, so callers must pass a
        # deterministically-ordered list (the merged `current_top()`).
        if initial_solutions:
            for solution in initial_solutions:
                self._tracker.consider(solution)
        # Seed-node pool. Sorted ascending so start-node sampling is deterministic
        # across Python / networkx versions (dict-insertion order is the FR29
        # fragility). Under `--start-at-junction` (FR31) the pool is pruned to
        # road/trail junction nodes via the shared `is_junction_node` predicate —
        # the same one the oracle and validator use, so all three stay on one
        # feasible set. This restriction is an *efficiency/guidance* prune, not the
        # constraint's enforcement: FR31 is enforced by the validator's independent
        # `start_at_junction` check on `edges[0].node_u`, which holds whatever the
        # solver does. An empty pool (no junctions) makes `run()` return `[]` via
        # its `if not self._nodes` guard — correct FR12.
        if static_context is not None:
            self._nodes: tuple[int, ...] = static_context.nodes
        else:
            all_nodes = sorted(graph.graph.nodes)
            if params.start_at_junction:
                nodes = [n for n in all_nodes if is_junction_node(graph, n)]
            else:
                nodes = all_nodes
            self._nodes = tuple(nodes)
        self._cap_rank: int = parse_difficulty_cap(params.difficulty_cap)
        # Direction-aware descent cap (FR32). `None` → off; when set, `_build_rcl`
        # drops any descending candidate edge steeper than this, via the
        # `solver.descent` predicate single-sourced with the oracle + validator.
        self._max_descent_slope: float | None = params.max_descent_slope
        # Base-segment ids subject to the once-only reuse rule, computed once per
        # graph. Single-sourced with the oracle + validator via `solver/reuse.py`
        # so all three share one feasible set.
        self._non_exempt_ids: frozenset[tuple[int, int, int]] = (
            static_context.non_exempt_ids
            if static_context is not None
            else non_exempt_base_segment_ids(graph)
        )
        # Termination outcome (§Cat 5e). Initialised to the iter-budget outcome so
        # the attribute is always readable/typed — including after the empty-graph
        # early return in `run()` — and set definitively at each termination
        # branch. `interrupted` is never set here; the CLI's interrupt handler owns it.
        self.convergence_status: ConvergenceStatus = "budget-exhausted"
        # 1-based iteration of the last admission — the last time the top-N held
        # set changed (`tracker.consider()` returned `True`).
        # Anytime-readable like `best_so_far`/`convergence_status`, so it holds the
        # right value on every termination path, *including* a `KeyboardInterrupt`
        # that unwinds `run()` and discards its locals. `0` means no admission ever
        # landed (empty graph, no admissible route, or interrupt before the first
        # admission). It equals `(i + 1) − stagnation_counter` at any point, since
        # the stagnation counter resets to 0 exactly when an admission lands.
        self.convergence_iteration: int = 0
        # Per-node adjacency table of pre-built candidate records. Empty until
        # `run()` builds it once per solve — solver instances are single-run, and
        # building it inside `run()` keeps the (one-off) cost inside the benchmark
        # suite's measured region. Build state is tracked separately because an
        # empty table is also a valid completed result when every edge is filtered.
        #
        # A caller MAY pass a prebuilt `adjacency`: it is a pure function of the
        # graph + the SAC/descent filter params, so it is identical across a
        # parallel worker's migration rounds. Reusing it skips a ~8 s
        # `_build_adjacency` rebuild each round (r20 area, 2026-07-08) — the
        # dominant per-round cost. The caller is responsible for passing an adjacency
        # built from the *same* graph + params; `run()` reuses the supplied table
        # verbatim, including an empty one, so a mismatched table would silently
        # corrupt results.
        self._adjacency: AdjacencyTable = (
            static_context.adjacency
            if static_context is not None
            else adjacency
            if adjacency is not None
            else {}
        )
        self._adjacency_view: AdjacencyTable = (
            static_context.adjacency
            if static_context is not None
            else MappingProxyType(self._adjacency)
        )
        self._adjacency_built: bool = static_context is not None or adjacency is not None
        self._static_context: SolverStaticContext | None = static_context
        # Batched-draw buffer: uniform [0, 1) values, refilled by `_next_uniform`
        # in `_RNG_CHUNK`-sized native calls and held as a plain list (one exact
        # `.tolist()` per refill) so per-draw consumption is a native list index
        # yielding a Python float, not an ndarray scalar. Starts empty
        # (`index == len`) so the first draw triggers a refill.
        self._draw_buffer: list[float] = []
        self._draw_index: int = 0

    @property
    def best_so_far(self) -> list[Solution]:
        """Current top-N (Architecture §Cat 5b: always-readable anytime view)."""
        return self._tracker.current_top()

    @property
    def adjacency(self) -> AdjacencyTable:
        """Read-only adjacency view (empty until `run()` builds it, or injected).

        Retained as the narrow legacy injection seam. Parallel migration workers
        reuse `static_context` now, which includes this table plus every other
        graph-derived invariant.
        """
        return self._adjacency_view

    @property
    def segment_map(self) -> SegmentMap:
        """Read-only base-segment map, available without building adjacency."""
        return self._segment_map_view

    def _ensure_adjacency(self) -> None:
        """Build adjacency at most once, including when the completed table is empty."""
        if self._adjacency_built:
            return
        adjacency = self._build_adjacency()
        self._adjacency = adjacency
        self._adjacency_view = MappingProxyType(adjacency)
        self._adjacency_built = True

    @property
    def static_context(self) -> SolverStaticContext:
        """Reusable read-only static state, building adjacency on first access."""
        if self._static_context is None:
            self._ensure_adjacency()
            self._static_context = SolverStaticContext(
                graph=self._graph,
                start_at_junction=self._params.start_at_junction,
                difficulty_cap=self._params.difficulty_cap,
                max_descent_slope=self._params.max_descent_slope,
                nodes=self._nodes,
                segment_map=self._segment_map_view,
                non_exempt_ids=self._non_exempt_ids,
                adjacency=self._adjacency_view,
            )
        return self._static_context

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

        `stagnation_counter` counts consecutive iterations with no admission — it
        resets exactly when `tracker.consider()` returns `True` (the held set
        changed), so it is exactly "iterations since the last admission". Drive it
        off that verdict, never off a top-N total-objective delta: the
        evict-many-admit-one branch can admit a candidate that leaves the total
        unchanged (a delta reads it as stagnant) or even lowers it (a delta reads
        it as an improvement).

        The counter and the monotonic-clock reads run every iteration because they
        gate termination; only the `ProgressEvent` *construction* sits behind the
        callback check. FR29 still holds: the clock reads feed `elapsed_s` / the
        ETA / the time-budget comparison only — never the RNG, `_construct_one`, or
        the admission sequence. So a fixed seed yields a byte-identical iteration
        *sequence*; only the *count* is wall-clock-dependent, and solely when the
        soft time-budget binds.
        """
        if not self._nodes:
            return self._tracker.current_top()
        # Once-per-solve precompute: the contracted graph is immutable for the
        # duration of a solve, so every walk-state-independent part of RCL
        # construction is hoisted out of the hot loop here. Skipped when a prebuilt
        # table was injected (island-migration reuse).
        self._ensure_adjacency()
        callback = self._progress_callback
        stagnation_iters = self._params.stagnation_iters
        time_budget = self._params.time_budget
        start = time.monotonic()
        stagnation_counter = 0
        for i in range(self._params.iter_budget):
            solution = self._construct_one()
            # Offer the best θ-clearing prefix of the constructed walk, not just
            # the maximal walk: a steep prefix forced to append a flat tail would
            # otherwise drag the whole-walk average below θ and be discarded,
            # losing a feasible route the oracle keeps. `_best_theta_prefix`
            # returns None when no prefix clears θ (including the empty walk), so
            # no separate empty-walk guard is needed.
            admitted = False
            candidate = self._best_theta_prefix(solution.edges)
            if candidate is not None:
                admitted = self._tracker.consider(candidate)
            if admitted:
                stagnation_counter = 0
                # Record where the held set last changed. Held on `self` (not a
                # local) so an interrupt mid-loop preserves it.
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

        The **longest** θ-clearing prefix is the best one: per-edge `d_plus_m +
        d_minus_m` is non-negative, so the objective is non-decreasing in prefix
        length and the longest qualifying prefix carries the highest objective.
        Scanning from the full length downward returns it on the first hit — a
        single, deterministic answer with no tie-break, so FR29 holds (a pure
        function of the already-deterministic walk; no RNG). The empty walk has no
        non-empty prefix and yields `None`.

        Each prefix is tested in O(1) from forward cumulative sums rather than
        re-summing the whole prefix per candidate (quadratic in walk length). The
        cumulative sums are the same left fold `route_avg_gradient` computes —
        start `0.0`, add per edge in walk order — so the value at every `end` is
        bit-identical to `route_avg_gradient(edges[:end])`, and the zero-length
        branch (`0.0` when `Σlength ≤ 0`) is mirrored exactly. The winning prefix
        still passes through the canonical `_route_slope_ok` gate before
        admission, which keeps the `models.py` single-sourcing contract (solver /
        validator / oracle compare bit-identical values) structural rather than
        incidental; by the fold identity the gate always agrees with the
        incremental test.
        """
        n = len(edges)
        cum_length = [0.0] * (n + 1)
        cum_climb = [0.0] * (n + 1)
        length = 0.0
        climb = 0.0
        for i, e in enumerate(edges, start=1):
            length += e.length_m
            climb += e.d_plus_m + e.d_minus_m
            cum_length[i] = length
            cum_climb[i] = climb
        theta = self._params.theta
        for end in range(n, 0, -1):
            total_length = cum_length[end]
            gradient = cum_climb[end] / total_length if total_length > 0.0 else 0.0
            if gradient < theta:
                continue
            prefix = edges[:end]
            if self._route_slope_ok(prefix):
                return Solution(edges=prefix, objective=cum_climb[end])
        return None

    def _next_uniform(self) -> float:
        """Next uniform `[0, 1)` draw from the chunked buffer.

        Semantically `float(self._rng.random())` — same stream, same values, in
        the same order — but the native `Generator` boundary is crossed once per
        `_RNG_CHUNK` draws instead of once per draw. Refills happen only here,
        only on exhaustion — never conditioned on wall-clock, callbacks, or
        termination state — so the consumed sequence stays a pure function of
        the seed (FR29). Callers derive a bounded index as `int(u * n)`; see the
        module docstring's Determinism section for the exactness argument and the
        measurement that motivates the batching.
        """
        i = self._draw_index
        buf = self._draw_buffer
        if i == len(buf):
            buf = self._rng.random(_RNG_CHUNK).tolist()
            self._draw_buffer = buf
            i = 0
        self._draw_index = i + 1
        return buf[i]

    def _construct_one(self) -> Solution:
        """Build one GRASP candidate via greedy-randomized walk extension.

        Two walk-state sets enforce the feasible set described in the module
        docstring: `used_segments` (undirected base-segment once-only) and
        `used_directed` (no `(node_u, node_v, key)` triple twice). The second is
        what guarantees **termination**: exempt short connectors don't block on a
        segment, so without the directed-simple bound a reusable connector could be
        walked `a→b→a→b…` forever. An exempt connector is therefore bounded by the
        directed-simple rule rather than the once-only segment rule — a simple
        two-node connector recurs at most twice (once per direction), while
        parallel keys over the same exempt segment may each appear once.

        Node-revisits via distinct non-conflicting segments are allowed, and so are
        closed walks — including a single self-loop edge `(u, u, k)`, a valid
        length-1 route. Such pathological-but-real OSM shapes (lollipop trail-ends,
        roundabouts) are admitted by design; the runtime validator owns any policy
        on rejecting them.

        A start node with no feasible extension yields an empty walk
        (`edges == ()`, `objective == 0.0`); `run()` discards those before they
        reach the tracker.
        """
        start_idx = int(self._next_uniform() * len(self._nodes))
        current: int = self._nodes[start_idx]
        path_edges: list[Edge] = []
        used_directed: set[tuple[int, int, int]] = set()
        used_segments: set[tuple[int, int, int]] = set()
        while True:
            rcl = self._build_rcl(current, used_directed, used_segments)
            if not rcl:
                break
            choice_idx = int(self._next_uniform() * len(rcl))
            chosen, chosen_blocking = rcl[choice_idx]
            path_edges.append(chosen)
            used_directed.add((chosen.node_u, chosen.node_v, chosen.key))
            used_segments |= chosen_blocking
            current = chosen.node_v
        # The whole-walk objective was discarded by `run()`: the admitted
        # candidate is rescored exactly once by `_best_theta_prefix` while it
        # computes cumulative slope. Keep the temporary shape without the sum.
        return Solution(edges=tuple(path_edges), objective=0.0)

    def _build_adjacency(self) -> dict[int, tuple[_CandidateRecord, ...]]:
        """Per-node adjacency table of pre-built candidate records.

        One pass over the immutable contracted graph, hoisting everything about RCL
        construction that does not depend on walk state. Doing this work per step
        instead cost ~35–40% of query wall-clock (py-spy, r6 Grenoble area,
        2026-07-03), so keep it out of `_build_rcl`:

        - **Static filters applied once.** SAC cap (`max_sac_rank(sac_scale) >
          cap_rank` rejects; `None` / unrecognized values pass — cleared
          `filter_trails` upstream) and the direction-aware descent cap (FR32;
          off when unset). Both read only edge data and per-solve params, so an
          edge failing them can never become feasible and is dropped from the
          table outright.
        - **`Edge` built once** per graph edge instead of re-wrapped per visit
          (`Edge` is frozen, so sharing one instance across RCLs/solutions is
          safe), alongside its pre-built `directed_id` triple.
        - **Blocking sets computed once** — single-sourced via
          `solver.reuse.blocking_ids` against `self._non_exempt_ids`.
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
        (`_build_adjacency`) — no graph access, no `Edge` construction, no set math
        beyond the walk-state checks, no sorting. Only the two walk-state-dependent
        filters run here (same feasibility as
        `tests/integration/exhaustive_oracle.py`):

        - Directed edge-simple: rejected iff `directed_id` is already in
          `used_directed` (→ termination).
        - Undirected base-segment once-only: rejected iff any blocking id is
          already in `used_segments`. An exempt short connector has an empty
          blocking set, so only the directed-simple bound limits it (to once per
          direction).

        Because each node's records are pre-sorted by the total static key,
        collecting the first `RCL_SIZE` survivors in table order yields the same
        candidates in the same order as filtering the node's whole edge set and
        then sorting would (FR29).

        The slope floor θ is **not** an RCL filter — it is a route-level constraint
        enforced at finalization (`_route_slope_ok`). Every edge that clears the two
        filters above is a candidate regardless of its own gradient.
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
