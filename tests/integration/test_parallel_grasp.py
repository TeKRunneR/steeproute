# pyright: reportImplicitRelativeImport=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false
# Reason: `from conftest import ...` is the import shape that resolves under
# pytest's prepend import mode (no `__init__.py` under `tests/`) — same as
# `test_grasp_reproducible.py` / `test_oracle_correctness.py`;
# `reportPrivateUsage` — `_aggregate_progress` is a module-private pure helper the
# threaded drainer delegates to, unit-tested directly here (the thread isn't).
"""Parallel GRASP restarts — Story 14.4 (`solver/parallel.py`).

Exercises the multiprocess orchestration on the offline toy `ContractedGraph`
(`conftest.make_toy_contracted_graph`) so no OSM/DEM fixture or network is
touched and the tests stay seed-deterministic:

- `split_iter_budget` — pure budget partition (base + remainder-to-worker-0,
  clamped when the budget is below the worker count);
- **determinism per `(seed, workers)`** — two `run_parallel_grasp` calls with the
  same `(seed, workers)` produce byte-identical merged results (FR29 discipline:
  raw `==` on objectives and edge-id sequences);
- **worker-0 seed derivation** — a 1-worker parallel run equals a `GraspSolver`
  seeded from `SeedSequence(seed).spawn(1)[0]`, pinning the RNG-derivation scheme;
- **interrupt salvage** — a Ctrl-C mid-merge raises `ParallelGraspInterrupted`
  carrying the top-N merged from workers that had already returned.

The pool is pinned to the `spawn` start method inside `run_parallel_grasp`, so
these run the exact pickling + fresh-import path on every OS (not just Windows).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from concurrent.futures import Future
from concurrent.futures import as_completed as _real_as_completed

import numpy as np
import pytest
from conftest import make_toy_contracted_graph, make_toy_solver_params

from steeproute.models import Solution
from steeproute.solver.grasp import GraspSolver
from steeproute.solver.parallel import (
    ParallelGraspFailed,
    ParallelGraspInterrupted,
    ParallelProgress,
    _aggregate_progress,
    round_count,
    round_plan,
    run_parallel_grasp,
    solver_graph_view,
    split_iter_budget,
)


def _edge_id_sequences(solutions: list[Solution]) -> list[list[tuple[int, int, int]]]:
    """Canonical `(node_u, node_v, key)` sequence per solution — the FR29 identity."""
    return [[(e.node_u, e.node_v, e.key) for e in s.edges] for s in solutions]


# --- split_iter_budget (pure; no processes) ----------------------------------


def test_split_iter_budget_even_division() -> None:
    assert split_iter_budget(100, 4) == [25, 25, 25, 25]


def test_split_iter_budget_remainder_goes_to_worker_zero() -> None:
    budgets = split_iter_budget(103, 4)
    assert budgets == [28, 25, 25, 25]
    assert sum(budgets) == 103  # the split never loses or invents iterations


def test_split_iter_budget_single_worker_gets_everything() -> None:
    assert split_iter_budget(2000, 1) == [2000]


def test_split_iter_budget_clamps_when_budget_below_workers() -> None:
    """`iter_budget < workers` → clamp worker count so no worker gets a 0 budget."""
    budgets = split_iter_budget(2, 5)
    assert budgets == [1, 1]  # only 2 workers, each with the minimum 1 iteration
    assert all(b >= 1 for b in budgets)
    assert sum(budgets) == 2


@pytest.mark.parametrize("bad", [0, -1])
def test_split_iter_budget_rejects_non_positive_budget(bad: int) -> None:
    with pytest.raises(ValueError, match="iter_budget"):
        split_iter_budget(bad, 2)


@pytest.mark.parametrize("bad", [0, -1])
def test_split_iter_budget_rejects_non_positive_workers(bad: int) -> None:
    """Defense-in-depth: the CLI rejects `--workers < 1` first, but the split guards too."""
    with pytest.raises(ValueError, match="workers"):
        split_iter_budget(100, bad)


# --- round_count / round_plan (island-migration budget planning; pure) ------


def test_round_count_disabled_or_too_coarse_is_single_round() -> None:
    assert round_count(1_000_000, 0, 4) == 1  # migration off
    assert round_count(1_000_000, 1_000_000, 4) == 1  # interval >= budget
    assert round_count(1_000_000, 5_000_000, 4) == 1


def test_round_count_ceils_and_clamps() -> None:
    assert round_count(1_000_000, 250_000, 4) == 4
    assert round_count(1_000_000, 300_000, 4) == 4  # ceil(1e6/3e5)=4
    # Clamp: never so many rounds that a round can't give every worker >= 1 iter.
    assert round_count(10, 1, 4) == 2  # min(10, 10//4=2)


def test_round_plan_sums_to_budget_and_every_entry_positive() -> None:
    for rounds in (1, 3, 4):
        plan = round_plan(1_000_000, 4, rounds)
        assert len(plan) == rounds
        assert all(len(r) == 4 for r in plan)
        assert all(b >= 1 for r in plan for b in r)
        assert sum(b for r in plan for b in r) == 1_000_000  # nothing lost/invented


def test_grasp_reused_adjacency_is_byte_identical() -> None:
    """A solver given a prebuilt adjacency produces identical output to one that builds it.

    This is the safety property behind reusing the adjacency across migration rounds
    (the ~8 s `_build_adjacency` is a pure function of graph + params, so reuse can't
    change any decision).
    """
    graph = make_toy_contracted_graph(23)
    params = make_toy_solver_params(iter_budget=300, seed=42)

    built = GraspSolver(graph, params, np.random.default_rng(42))
    from_built = built.run()
    assert built.adjacency, "run() should have built a non-empty adjacency table"

    reused = GraspSolver(graph, params, np.random.default_rng(42), adjacency=built.adjacency)
    from_reused = reused.run()

    assert _edge_id_sequences(from_built) == _edge_id_sequences(from_reused)
    assert [s.objective for s in from_built] == [s.objective for s in from_reused]


def test_parallel_deterministic_with_migration() -> None:
    """Two runs with the same `(seed, workers, merge_interval>0)` are byte-identical."""
    graph = make_toy_contracted_graph(11)
    params = make_toy_solver_params(iter_budget=400, seed=42)

    first = run_parallel_grasp(graph, params, seed=42, workers=2, merge_interval=100)
    second = run_parallel_grasp(graph, params, seed=42, workers=2, merge_interval=100)

    assert first.effective_workers == 2
    assert first.solutions, "expected the migrating parallel solve to find >= 1 route"
    assert [s.objective for s in first.solutions] == [s.objective for s in second.solutions]
    assert _edge_id_sequences(first.solutions) == _edge_id_sequences(second.solutions)


# --- solver_graph_view + progress aggregation (pure; no processes) -----------


def test_solver_graph_view_strips_heavy_attrs_but_preserves_solver_output() -> None:
    """The lean view drops rendering-only attrs yet yields byte-identical solver output.

    This is the correctness guarantee behind sending workers a lean graph: GRASP
    reads none of `HEAVY_EDGE_ATTRS`, so stripping them cannot change any decision.
    """
    graph = make_toy_contracted_graph(11)
    # Inject the heavy rendering-only attrs a real contracted graph carries.
    for _u, _v, _k, data in graph.graph.edges(keys=True, data=True):
        data["vertices_resampled"] = [(1.0, 2.0, 3.0)] * 64
        data["geometry"] = ("pretend-shapely-geometry",) * 8
    params = make_toy_solver_params(iter_budget=200, seed=42)

    view = solver_graph_view(graph)

    for _u, _v, _k, data in view.graph.edges(keys=True, data=True):
        assert "vertices_resampled" not in data and "geometry" not in data
        assert "length_m" in data and "base_segment_id" in data  # solver attrs kept
    assert view.super_edge_to_base is graph.super_edge_to_base

    from_full = GraspSolver(graph, params, np.random.default_rng(42)).run()
    from_view = GraspSolver(view, params, np.random.default_rng(42)).run()
    assert _edge_id_sequences(from_full) == _edge_id_sequences(from_view)
    assert [s.objective for s in from_full] == [s.objective for s in from_view]


def test_aggregate_progress_folds_latest_per_worker() -> None:
    """`_aggregate_progress` sums iterations, maxes the objective, counts reporters."""
    latest = {0: (1000, 50.0), 2: (3000, 80.0)}  # worker 1 hasn't reported yet
    assert _aggregate_progress(latest, elapsed_s=4.0, workers_total=3) == ParallelProgress(
        total_iterations=4000,
        best_worker_objective=80.0,
        elapsed_s=4.0,
        workers_reporting=2,
        workers_total=3,
    )


# --- Parallel solve (spawns worker processes) --------------------------------


def test_parallel_deterministic_per_seed_workers() -> None:
    """Two runs with the same `(seed, workers)` are byte-identical (reproducible)."""
    graph = make_toy_contracted_graph(11)
    params = make_toy_solver_params(iter_budget=120, seed=42)

    first = run_parallel_grasp(graph, params, seed=42, workers=2)
    second = run_parallel_grasp(graph, params, seed=42, workers=2)

    assert first.effective_workers == 2
    assert first.solutions, "expected the parallel solve to find >= 1 route"
    # Raw `==` (not isclose): FR29 promises byte-identical reproducibility.
    assert [s.objective for s in first.solutions] == [s.objective for s in second.solutions]
    assert _edge_id_sequences(first.solutions) == _edge_id_sequences(second.solutions)


def test_parallel_respects_top_n_and_status() -> None:
    """The merged set honours `n`, and a budget-bound run reports `budget-exhausted`."""
    graph = make_toy_contracted_graph(23)
    params = make_toy_solver_params(iter_budget=120, n=3, seed=42)  # stagnation disabled

    result = run_parallel_grasp(graph, params, seed=42, workers=2)

    assert len(result.solutions) <= params.n
    assert result.convergence_status == "budget-exhausted"


def test_parallel_worker_zero_uses_spawned_seed() -> None:
    """A 1-worker parallel run == `GraspSolver` seeded from `SeedSequence(seed).spawn(1)[0]`.

    Pins the RNG-derivation scheme: worker `i` draws from `spawn(N)[i]`, so a single
    worker must match an independently-spawned reference (not `default_rng(seed)`,
    which is the *single-process* stream — deliberately different).
    """
    graph = make_toy_contracted_graph(37)
    params = make_toy_solver_params(iter_budget=200, seed=7)

    parallel = run_parallel_grasp(graph, params, seed=7, workers=1)
    reference_seed = np.random.SeedSequence(7).spawn(1)[0]
    reference = GraspSolver(graph, params, np.random.default_rng(reference_seed)).run()

    assert parallel.effective_workers == 1
    assert _edge_id_sequences(parallel.solutions) == _edge_id_sequences(reference)
    assert [s.objective for s in parallel.solutions] == [s.objective for s in reference]


def test_parallel_interrupt_salvages_completed_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C mid-collection → `ParallelGraspInterrupted` carrying the already-returned top-N.

    Patches the module's `as_completed` to yield exactly one finished worker (so its
    result is collected + merged) and then raise `KeyboardInterrupt`, driving the
    real salvage path deterministically without a live OS signal.
    """
    graph = make_toy_contracted_graph(53)
    params = make_toy_solver_params(iter_budget=120, seed=42)

    def interrupt_after_first(fs: Iterable[Future[object]]) -> Iterator[Future[object]]:
        iterator = _real_as_completed(fs)
        yield next(iterator)  # let one worker complete and be collected
        raise KeyboardInterrupt  # interrupt before collecting the rest

    monkeypatch.setattr("steeproute.solver.parallel.as_completed", interrupt_after_first)

    with pytest.raises(ParallelGraspInterrupted) as exc_info:
        run_parallel_grasp(graph, params, seed=42, workers=2)

    partial = exc_info.value.partial
    assert partial.convergence_status == "interrupted"
    # The one worker that returned before the interrupt was merged into the partial.
    assert partial.solutions, "expected >= 1 route salvaged from the completed worker"


def test_parallel_setup_failure_raises_parallel_grasp_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure serializing the graph view (e.g. OOM) → `ParallelGraspFailed`, not a raw crash.

    The parallel-specific setup (building + pickling the lean graph view for the pool
    initializer) runs before any worker is spawned; if it raises, the CLI must still
    get a `ParallelGraspFailed` so it can fall back to a single-process solve. Patches
    `solver_graph_view` to raise `MemoryError` — the realistic large-graph failure —
    and asserts the exception is translated rather than propagating uncaught.
    """
    graph = make_toy_contracted_graph(53)
    params = make_toy_solver_params(iter_budget=120, seed=42)

    def oom(_contracted: object) -> object:
        raise MemoryError("simulated OOM building the graph view")

    monkeypatch.setattr("steeproute.solver.parallel.solver_graph_view", oom)

    with pytest.raises(ParallelGraspFailed) as exc_info:
        run_parallel_grasp(graph, params, seed=42, workers=2)
    # The original cause is chained so the real error is still diagnosable.
    assert isinstance(exc_info.value.__cause__, MemoryError)
