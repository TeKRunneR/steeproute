# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
# Reason: `ContractedGraph.graph` is a networkx `MultiDiGraph[Unknown]` — the same
# external-boundary pattern as `solver/grasp.py` and the `pipeline/` modules.
"""Parallel GRASP restarts across processes (Story 14.4, Architecture §Cat 5a).

GRASP iterations are independent restarts — embarrassingly parallel. §Cat 5a
shaped the solver loop to be `ProcessPoolExecutor`-convertible and chose
`numpy.random.Generator` precisely for `SeedSequence.spawn` compatibility; this
module realizes that latent design without touching `GraspSolver` itself.

**Why this lives at the orchestration layer, not in `SolverParams`.** `models.py`
is content-hashed (`cache.py` `_PIPELINE_CONTENT_GLOBS`), so adding a `workers`
field to `SolverParams` would invalidate every prepared cache for a knob the
solver never reads. Instead `--workers` is a plain CLI argument threaded straight
here; the only per-worker `SolverParams` change is the iteration budget, produced
by `dataclasses.replace`. `solver/` and `cli/` are not content-hashed at all, so
this whole module is invisible to the cache key.

**Sending the graph is the bottleneck, not the solve (measured).** At r20 the full
contracted graph pickles to ~204 MB — because every edge carries its
`vertices_resampled` polyline (and `geometry`), which the **solver never reads**
(only the renderer, in the parent, does). Shipping that to every worker under
`spawn` dominated wall-clock and erased the speedup. So workers receive a
`solver_graph_view` — the same graph with those heavy geometry attributes
stripped (~72 MB at r20), serialized **once** to bytes in the parent and handed to
each worker as a cheap buffer copy. GRASP output is byte-identical on the lean
graph (it reads none of the stripped attributes); the parent keeps the full graph
for validation/render. The per-worker solve then runs concurrently at full
single-core throughput.

**Determinism (FR29 / NFR4).** Output is deterministic and reproducible for a
fixed `(seed, workers)`, but **differs by design from `--workers 1`** (independent
`SeedSequence(seed).spawn(N)[i]` streams + a partitioned budget). What is pinned:
`spawn(N)` and the `divmod` split are deterministic and platform-independent, and
the merge feeds a single fresh `TopNTracker` in **worker-id order** (results are
collected into id-indexed slots, never completion order — the tracker is
order-sensitive under overlaps) then each worker's `current_top()` order.
`seed=None` is non-deterministic in both modes, by design.

**Island-model elite migration.** The `iter_budget` is split into rounds of
~`merge_interval` total iterations; after each round the workers' top-Ns are merged
and the merged elite *seeds* every worker's tracker for the next round (via
`GraspSolver(initial_solutions=...)`). So workers cooperate toward one shared top-N
rather than drifting into independent, redundant local optima — this bounds the
parallel downside (variance) so the merged result reliably matches/beats
single-process. `merge_interval <= 0` collapses to a single final merge. The lean
graph is loaded once per worker process (pool `initializer`), never per round.

**Windows spawn.** The pool is pinned to the `spawn` start method explicitly (not
the platform default) so Linux CI exercises the exact pickling/fresh-import path
Windows uses. `_run_round_worker` / `_init_worker` are module-level (pickle by
reference); the entry-point modules are side-effect-free `[project.scripts]`
console_scripts.

**Progress.** Per-iteration progress can't cross to workers, so each worker pushes
a throttled `(worker_id, iteration, best_objective)` snapshot onto a
`multiprocessing.Manager` queue; a parent daemon thread aggregates the latest
per-worker snapshots and calls `on_progress` on the same `--progress-interval`
cadence. The queue is a pure side-effect (like the single-process progress
callback) and never touches the RNG or the iteration sequence.
"""

from __future__ import annotations

import dataclasses
import pickle
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from multiprocessing.queues import Queue as MpQueue
from typing import NamedTuple

import numpy as np

from steeproute.models import (
    ContractedGraph,
    ConvergenceStatus,
    Solution,
    SolverParams,
)
from steeproute.progress import ProgressEvent, throttle
from steeproute.solver.distinctness import TopNTracker
from steeproute.solver.grasp import AdjacencyTable, GraspSolver
from steeproute.solver.reuse import base_segment_id_map

__all__ = [
    "HEAVY_EDGE_ATTRS",
    "ParallelGraspFailed",
    "ParallelGraspInterrupted",
    "ParallelProgress",
    "ParallelResult",
    "round_count",
    "round_plan",
    "run_parallel_grasp",
    "solver_graph_view",
    "split_iter_budget",
]

HEAVY_EDGE_ATTRS: frozenset[str] = frozenset({"vertices_resampled", "geometry"})
"""Per-edge attributes stripped from the worker graph view.

Both are pure rendering payloads (the resampled polyline vertices and the shapely
geometry) that `GraspSolver` never reads — dropping them shrinks the r20 contracted
graph from ~204 MB to ~72 MB per worker without changing a single solver decision.
Everything else (numeric edge metrics, `sac_scale`, `base_segment_id`, `reusable`,
`max_windowed_descent_grad`, and **all node attributes** incl. the
`--start-at-junction` flag) is preserved.
"""


class _WorkerResult(NamedTuple):
    """One worker's return payload (picklable back to the parent)."""

    solutions: list[Solution]
    convergence_status: ConvergenceStatus
    convergence_iteration: int


class ParallelResult(NamedTuple):
    """Merged parallel-solve outcome handed back to the CLI.

    `effective_workers` is the *actual* worker count used, clamped down from the
    requested `--workers` when `iter_budget < workers` (so no worker gets a `0`
    budget — `GraspSolver` rejects `iter_budget < 1`).
    """

    solutions: list[Solution]
    convergence_status: ConvergenceStatus
    convergence_iteration: int
    effective_workers: int


class ParallelProgress(NamedTuple):
    """Aggregated live progress snapshot across workers (parent-side display only).

    `best_worker_objective` is the max over workers of each worker's *own* running
    top-N sum — deliberately NOT the merged objective. Merging live would mean each
    worker streaming its full top-N every tick; the cheap scalar each worker already
    reports only supports a per-worker figure. It therefore *understates* the final
    merged result (which combines all workers) — the run summary's `total_objective`
    is the real, comparable number. Named explicitly so it can't be mistaken for it.
    """

    total_iterations: int
    best_worker_objective: float
    elapsed_s: float
    workers_reporting: int
    workers_total: int


class ParallelGraspFailed(RuntimeError):
    """The parallel solve can't run/be trusted — the CLI falls back to single-process.

    Two failure modes raise this, both handled identically by the CLI (fall back to a
    correct single-process solve so the query still completes — slower, but without a
    raw traceback):

    * **A worker process died (crash / OOM) mid-solve.** Every worker holds its own
      copy of the (lean) contracted graph, so worker memory grows O(N × graph); under
      memory pressure — a high `--workers` count, a large area, a loaded machine — a
      worker can be killed, surfacing as `concurrent.futures`' `BrokenProcessPool`.
    * **Parallel-specific setup failed before any worker ran.** Serializing the lean
      graph view for the pool initializer (`pickle.dumps(solver_graph_view(...))`) can
      raise `MemoryError` on a large graph or `PicklingError` if some edge attribute
      is ever made unpicklable, and creating the progress queue can hit an OS
      handle/semaphore limit. These are unique to the parallel path — the budget math
      and `base_segment_id_map` above the guard are shared with the single-process
      solver, so a failure there is not parallel-specific and propagates as-is
      (falling back would just re-hit it).
    """


class ParallelGraspInterrupted(KeyboardInterrupt):
    """Ctrl-C during a parallel solve, carrying the salvaged partial merged result.

    Subclasses `KeyboardInterrupt` so a caller that only catches `KeyboardInterrupt`
    still treats it as an interrupt; the query CLI catches this type *first* to read
    `.partial` — the top-N merged from workers that had **already returned** before
    the interrupt. Workers still in flight cannot have their partial best-so-far
    recovered across the process boundary, so their progress is lost by design
    (documented degradation from the single-process Story 7.3 flush).
    """

    def __init__(self, partial: ParallelResult) -> None:
        super().__init__()
        self.partial: ParallelResult = partial


def split_iter_budget(iter_budget: int, workers: int) -> list[int]:
    """Per-worker iteration budgets: `base` each, the remainder added to worker 0.

    The effective worker count is clamped to `min(workers, iter_budget)` so every
    worker receives `>= 1` iterations (a `0` budget would trip `GraspSolver`'s
    `iter_budget < 1` guard). The returned list therefore has length
    `min(workers, iter_budget)` and always sums back to `iter_budget`.
    """
    if iter_budget < 1:
        raise ValueError(f"iter_budget must be >= 1, got {iter_budget}")
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    eff = min(workers, iter_budget)
    base, remainder = divmod(iter_budget, eff)
    budgets = [base] * eff
    budgets[0] += remainder
    return budgets


def solver_graph_view(contracted: ContractedGraph) -> ContractedGraph:
    """A copy of `contracted` with the heavy rendering-only edge attrs stripped.

    Preserves every node attribute and every non-`HEAVY_EDGE_ATTRS` edge attribute,
    plus `super_edge_to_base` unchanged. Because `GraspSolver` reads none of the
    stripped attributes, a solve on this view is **byte-identical** to one on the
    full graph — but the payload shipped to each worker shrinks several-fold, which
    is what makes parallelism pay off (the full graph's per-worker pickle otherwise
    dominates wall-clock at r20+). Rebuilding the graph changes internal
    edge-insertion order, which is FR29-safe: the solver sorts nodes and pre-sorts
    adjacency by a total key, and the reuse/segment maps are order-independent.
    """
    lean = contracted.graph.__class__()
    lean.add_nodes_from(contracted.graph.nodes(data=True))
    for node_u, node_v, key, data in contracted.graph.edges(keys=True, data=True):
        kept = {attr: value for attr, value in data.items() if attr not in HEAVY_EDGE_ATTRS}
        lean.add_edge(node_u, node_v, key=key, **kept)
    return ContractedGraph(graph=lean, super_edge_to_base=contracted.super_edge_to_base)


def round_count(iter_budget: int, merge_interval: int, workers: int) -> int:
    """How many migration rounds to split `iter_budget` into.

    `merge_interval` is the target number of *total* iterations between elite
    merges. `<= 0` or `>= iter_budget` means a single round (one final merge — the
    independent-islands behaviour). Otherwise `ceil(iter_budget / merge_interval)`,
    clamped so each round still has `>= workers` iterations (every worker gets
    `>= 1` per round — `GraspSolver`'s `iter_budget < 1` guard).
    """
    if merge_interval <= 0 or merge_interval >= iter_budget:
        return 1
    rounds = -(-iter_budget // merge_interval)  # ceil
    return max(1, min(rounds, iter_budget // workers))


def round_plan(iter_budget: int, workers: int, rounds: int) -> list[list[int]]:
    """Per-round, per-worker iteration budgets. Every entry `>= 1`; grand sum `== iter_budget`.

    The budget is first split across rounds (`base` each, remainder to round 0),
    then each round's total is split across workers the same way. `round_count`
    guarantees each round total is `>= workers`, so every per-worker entry is `>= 1`
    and each round uses the full worker count.
    """
    return [
        split_iter_budget(round_total, workers)
        for round_total in split_iter_budget(iter_budget, rounds)
    ]


# Set once per worker process by the pool `initializer` and reused across every
# round's tasks — so the (72 MB) lean graph is unpickled once per process, never
# per round. `_run_round_worker` reads it instead of receiving the graph per call.
_worker_graph: ContractedGraph | None = None

# Built once per worker process on its first round and reused across the rest —
# the per-node adjacency table is a pure function of the graph + filter params, so
# rebuilding it every round (~8 s, measured) is pure waste. Persists across a
# process's round tasks because the pool reuses its processes.
_worker_adjacency: AdjacencyTable | None = None

# The parent's progress queue, handed to each worker once via the pool
# `initializer` (never per-`submit()`). A plain `multiprocessing.Queue` is
# shareable only through inheritance — passing one as a `submit()` task arg raises
# `RuntimeError: Queue objects should only be shared ... through inheritance` — so
# it rides the initializer instead. That lets us use a plain `context.Queue()`
# rather than a `Manager().Queue()` proxy (which pickles fine through `submit()`
# but costs an extra manager process plus a proxied IPC hop per put). `None` when
# progress reporting is off.
_worker_progress_queue: MpQueue[tuple[int, int, float]] | None = None


def _init_worker(
    graph_blob: bytes, progress_queue: MpQueue[tuple[int, int, float]] | None
) -> None:  # pragma: no cover
    """Pool initializer: stash the lean graph and progress queue as per-process globals (once)."""
    global _worker_graph, _worker_progress_queue
    _worker_graph = pickle.loads(graph_blob)
    _worker_progress_queue = progress_queue


def _run_round_worker(  # pragma: no cover
    params: SolverParams,
    seed_sequence: np.random.SeedSequence,
    round_budget: int,
    elite: list[Solution],
    worker_id: int,
    iteration_base: int,
    progress_interval: float | None,
) -> _WorkerResult:
    """Run one worker's slice of one migration round in a child process.

    Reads the graph from the `_init_worker`-loaded global (no per-round transfer).
    Runs `round_budget` GRASP iterations seeded with the shared `elite` (merged
    from the previous round) so it only keeps routes that beat the global best.
    Only `iter_budget` differs per worker; `--time-budget`/`--stagnation-iters`
    apply per worker per round (documented interpretation).

    Progress reports the *absolute* iteration (`iteration_base + within-round`) so
    the parent's aggregate keeps climbing across rounds. Pure side-effect; never
    touches the RNG or the iteration sequence.

    `# pragma: no cover`: executes only inside spawned workers (invisible to
    parent-side coverage); exercised end-to-end by the determinism tests.
    """
    global _worker_adjacency
    graph = _worker_graph
    if graph is None:  # defensive: initializer must have run
        raise RuntimeError("worker graph not initialised")
    worker_params = dataclasses.replace(params, iter_budget=round_budget)
    rng = np.random.default_rng(seed_sequence)

    progress_queue = _worker_progress_queue
    callback = None
    if progress_queue is not None and progress_interval is not None:

        def _emit(event: ProgressEvent) -> None:
            try:
                progress_queue.put_nowait(
                    (worker_id, iteration_base + event.iteration, event.best_objective)
                )
            except Exception:
                pass  # a full/closed queue must never perturb the solve

        callback = throttle(_emit, progress_interval)

    # Reuse this process's adjacency table across rounds (built on the first round);
    # it is graph/param-derived and identical every round, so rebuilding it (~8 s)
    # each round is the dominant, avoidable per-round stall.
    solver = GraspSolver(
        graph,
        worker_params,
        rng,
        progress_callback=callback,
        initial_solutions=elite,
        adjacency=_worker_adjacency,
    )
    solutions = solver.run()
    if _worker_adjacency is None:
        _worker_adjacency = solver.adjacency
    return _WorkerResult(solutions, solver.convergence_status, solver.convergence_iteration)


def _aggregate_progress(
    latest: dict[int, tuple[int, float]], elapsed_s: float, workers_total: int
) -> ParallelProgress:
    """Fold the per-worker latest snapshots into one aggregate (pure, testable).

    `total_iterations` sums each worker's most recent iteration count (progress
    toward the whole `iter_budget`); `best_objective` is the max across workers.
    """
    return ParallelProgress(
        total_iterations=sum(iteration for iteration, _ in latest.values()),
        best_worker_objective=max((objective for _, objective in latest.values()), default=0.0),
        elapsed_s=elapsed_s,
        workers_reporting=len(latest),
        workers_total=workers_total,
    )


def _drain_progress(
    progress_queue: MpQueue[tuple[int, int, float]],
    stop: threading.Event,
    interval_s: float,
    workers_total: int,
    on_progress: Callable[[ParallelProgress], None],
) -> None:  # pragma: no cover
    """Parent daemon thread: aggregate worker snapshots and emit on the interval.

    `# pragma: no cover`: a timing/IPC-driven thread loop that the deterministic
    test suite can't meaningfully drive; the pure fold it delegates to
    (`_aggregate_progress`) is unit-tested directly.
    """
    latest: dict[int, tuple[int, float]] = {}
    start = time.monotonic()
    last_emit = start
    while not stop.is_set():
        try:
            worker_id, iteration, objective = progress_queue.get(timeout=0.2)
            latest[worker_id] = (iteration, objective)
        except queue.Empty:
            pass
        now = time.monotonic()
        if latest and now - last_emit >= interval_s:
            on_progress(_aggregate_progress(latest, now - start, workers_total))
            last_emit = now


def _merge(
    segment_map: dict[tuple[int, int, int], frozenset[tuple[int, int, int]]],
    params: SolverParams,
    results: list[_WorkerResult],
) -> tuple[list[Solution], ConvergenceStatus, int]:
    """Merge per-worker top-Ns through one fresh `TopNTracker`, in worker-id order.

    `results` arrive already ordered by worker id (the caller collects into
    id-indexed slots). Feeding each worker's `current_top()` list in that fixed
    order is what makes the merge reproducible for `(seed, workers)` — the tracker
    is order-sensitive under overlaps. `segment_map` is built once by the caller
    (`base_segment_id_map`, ~0.4 s) and reused across every migration round — the
    merge itself is then ~milliseconds.

    Aggregated status: `converged` iff **every** worker converged (a single still
    productive worker means the search as a whole was not stagnant); else
    `budget-exhausted`. Aggregated `convergence_iteration` is the max across workers.
    """
    tracker = TopNTracker(params.n, params.j_max, segment_map)
    for result in results:
        for solution in result.solutions:
            tracker.consider(solution)
    merged_status: ConvergenceStatus = (
        "converged"
        if results and all(r.convergence_status == "converged" for r in results)
        else "budget-exhausted"
    )
    merged_iteration = max((r.convergence_iteration for r in results), default=0)
    return tracker.current_top(), merged_status, merged_iteration


def run_parallel_grasp(
    contracted: ContractedGraph,
    params: SolverParams,
    seed: int | None,
    workers: int,
    *,
    merge_interval: int = 0,
    progress_interval: float | None = None,
    on_progress: Callable[[ParallelProgress], None] | None = None,
) -> ParallelResult:
    """Fan `GraspSolver` across `workers` processes with island-model elite migration.

    Only called for `workers > 1` — the CLI keeps the unchanged single-process path
    at `workers == 1` so default output stays byte-identical.

    The `iter_budget` is split into migration *rounds* of ~`merge_interval` total
    iterations (`merge_interval <= 0` → one round = independent islands, single final
    merge). Each round every worker runs its slice seeded with the previous round's
    merged elite (via `initial_solutions`), so workers cooperate toward one shared
    top-N instead of drifting into redundant local optima — which bounds the parallel
    downside (variance) and lets the merged result reliably match/beat single-process.
    The lean `solver_graph_view` is loaded once per worker process (pool
    `initializer`), never per round; the per-round merge is ~milliseconds
    (`segment_map` precomputed once). RNG streams are `SeedSequence(seed).spawn(N *
    rounds)[round * N + worker]`, so output is deterministic per
    `(seed, workers, merge_interval, iter_budget)`.

    On `KeyboardInterrupt` the pool is shut down without waiting (`wait=False`,
    `cancel_futures=True`) and a `ParallelGraspInterrupted` carries the best elite
    salvaged so far (this round's completed workers merged, else the previous
    round's elite) — rendered immediately, not after in-flight workers finish. A
    dead worker (`BrokenProcessPool`) raises `ParallelGraspFailed` →
    single-process fallback.

    Teardown never blocks the return: worker processes free their whole heap
    (lean graph + adjacency table) at exit — a measured ~8 s wall at r20 — so the
    pool is shut down `wait=False` and that teardown overlaps the caller's
    validation/render. `concurrent.futures`' atexit hook still joins the workers
    before the CLI process exits, so nothing is orphaned.
    """
    rounds = round_count(params.iter_budget, merge_interval, workers)
    plan = round_plan(params.iter_budget, workers, rounds)
    eff_workers = len(plan[0])
    seed_sequences = np.random.SeedSequence(seed).spawn(eff_workers * rounds)
    segment_map = base_segment_id_map(contracted)
    context = get_context("spawn")
    emit_progress = on_progress is not None and progress_interval is not None

    # Acquire the parallel-specific resources before spinning up the pool, and route
    # any failure to `ParallelGraspFailed` → single-process fallback (same contract as
    # the BrokenProcessPool path below), rather than crashing with a raw traceback.
    # `pickle.dumps(solver_graph_view(...))` serializes the lean graph once for the
    # pool initializer; it can OOM on a large graph (the dominant setup cost at r20+)
    # or raise `PicklingError`. The progress queue is a plain `context.Queue()` (not a
    # `Manager().Queue()`): it reaches the workers via the pool initializer below —
    # the only channel through which a non-proxy queue can be shared — so we skip the
    # manager process a proxy queue would require; creating it can hit an OS
    # handle/semaphore limit. The budget math / `base_segment_id_map` above are shared
    # with the single-process solver, so failures there are not parallel-specific and
    # are left to propagate (falling back would just re-hit them).
    try:
        graph_blob = pickle.dumps(solver_graph_view(contracted))
        progress_queue: MpQueue[tuple[int, int, float]] | None = (
            context.Queue() if emit_progress else None
        )
    except Exception as exc:
        raise ParallelGraspFailed(
            f"could not set up the parallel solve ({exc!r}); most likely out of "
            f"memory serializing the graph at workers={eff_workers}"
        ) from exc

    stop = threading.Event()
    drain_thread: threading.Thread | None = None

    elite: list[Solution] = []
    status: ConvergenceStatus = "budget-exhausted"
    convergence_iteration = 0
    interrupted = False
    iteration_base = [0] * eff_workers

    # Deliberately NOT `with ProcessPoolExecutor(...)`: the context manager's
    # `__exit__` is `shutdown(wait=True)`, which blocks the parent on every worker
    # process freeing its whole heap (lean graph + adjacency table) at interpreter
    # exit — a measured ~8 s dead tail at r20 between the last collected result and
    # validate/render starting. The `finally` below shuts down `wait=False` instead,
    # overlapping worker teardown with the caller's validation/render.
    pool = ProcessPoolExecutor(
        max_workers=eff_workers,
        mp_context=context,
        initializer=_init_worker,
        initargs=(graph_blob, progress_queue),
    )
    try:
        if progress_queue is not None and on_progress is not None and progress_interval:
            drain_thread = threading.Thread(
                target=_drain_progress,
                args=(progress_queue, stop, progress_interval, eff_workers, on_progress),
                daemon=True,
            )
            drain_thread.start()

        for round_index in range(rounds):
            round_budgets = plan[round_index]
            round_results: list[_WorkerResult | None] = [None] * eff_workers
            future_to_id = {
                pool.submit(
                    _run_round_worker,
                    params,
                    seed_sequences[round_index * eff_workers + worker_id],
                    round_budgets[worker_id],
                    elite,
                    worker_id,
                    iteration_base[worker_id],
                    progress_interval,
                ): worker_id
                for worker_id in range(eff_workers)
            }
            try:
                for future in as_completed(future_to_id):
                    round_results[future_to_id[future]] = future.result()
            except BrokenProcessPool as exc:
                # Shutdown happens in the `finally`; the dead pool has nothing to wait on.
                raise ParallelGraspFailed(
                    f"a worker process died ({exc}); most likely out of memory at "
                    f"workers={eff_workers}"
                ) from exc
            except KeyboardInterrupt:
                # Salvage: keep whatever this round completed (each was seeded
                # with `elite`, so it dominates it); else the previous round's
                # elite survives untouched. Then stop launching rounds. The
                # `finally` shuts the pool down without waiting, so the salvage
                # renders immediately — in-flight workers finish their round in
                # the background and their results are discarded (the documented
                # degradation; previously the salvage *blocked* on them here).
                partial = [r for r in round_results if r is not None]
                if partial:
                    elite, _status, convergence_iteration = _merge(segment_map, params, partial)
                interrupted = True
                break

            completed = [r for r in round_results if r is not None]
            elite, status, convergence_iteration = _merge(segment_map, params, completed)
            for worker_id in range(eff_workers):
                iteration_base[worker_id] += round_budgets[worker_id]
    finally:
        # Signal the workers to exit but do NOT wait for their interpreter
        # teardown (freeing the per-process graph + adjacency heaps, ~8 s at r20)
        # — it overlaps the caller's validation/render instead.
        # `concurrent.futures`' atexit hook still joins the worker processes
        # before the CLI process exits, so nothing is orphaned. `cancel_futures`
        # drops queued never-started tasks on the interrupt/failure paths (a
        # no-op on the success path, where every future was already collected).
        pool.shutdown(wait=False, cancel_futures=True)
        stop.set()
        if drain_thread is not None:
            drain_thread.join(timeout=1.0)
        if progress_queue is not None:
            # Workers may still be draining in the background; their throttled
            # `put_nowait` on a closed queue is swallowed by `_emit`'s guard.
            # `cancel_join_thread` discards unconsumed snapshots so the feeder
            # thread can't block exit.
            progress_queue.cancel_join_thread()
            progress_queue.close()

    if interrupted:
        raise ParallelGraspInterrupted(
            ParallelResult(elite, "interrupted", convergence_iteration, eff_workers)
        )
    return ParallelResult(elite, status, convergence_iteration, eff_workers)
