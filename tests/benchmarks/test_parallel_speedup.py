# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false, reportMissingTypeStubs=false
# Reason: pytest-benchmark ships no type information (the `benchmark` fixture and
# `BenchmarkFixture` resolve as Unknown); `reportImplicitRelativeImport` — `from
# conftest import ...` is the prepend-import shape (see test_solver_throughput.py).
"""Parallel GRASP startup + speedup baseline.

Two opt-in measurements on the grenoble_small contracted graph (excluded from the
default suite — run with `uv run pytest tests/benchmarks -m benchmark`):

- **per-worker startup payload** — `pickle.dumps(contracted_graph)` size, the
  dominant cost a spawned worker pays on top of process launch (the handoff's
  "measure the ContractedGraph pickle" item). Reported, not gated.
- **parallel wall-clock** — `run_parallel_grasp(workers=2)` timed against the
  single-process `test_solver_throughput.py` baseline on the same graph, so the
  effective speedup can be read off the two `.benchmarks/` entries. Full-scale
  (r50, more cores) speedup is measured separately, on real hardware at scale.
"""

from __future__ import annotations

import os
import pickle
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest
from conftest import BENCH_PARAMS
from pytest_benchmark.fixture import BenchmarkFixture

from steeproute.models import ContractedGraph, Solution
from steeproute.solver.grasp import GraspSolver
from steeproute.solver.parallel import run_parallel_grasp
from steeproute.solver.shared_state import (
    SharedBlobOwner,
    SharedSolverOwner,
    SharedSolverState,
    load_shared_blob,
)

pytestmark = pytest.mark.benchmark


def _edge_id_sequences(solutions: list[Solution]) -> list[list[tuple[int, int, int]]]:
    return [[(e.node_u, e.node_v, e.key) for e in s.edges] for s in solutions]


def test_contracted_graph_pickle_size(contracted_graph: ContractedGraph) -> None:
    """Report the per-worker pickle payload — the dominant spawn startup cost.

    Measures the payload workers *actually* receive: `contract_climbs` emits a lean
    graph (`HEAVY_EDGE_ATTRS` never attached), so this is the same blob
    `run_parallel_grasp` ships. Do not substitute a heavy graph here — that measures
    a view the workers never see.
    """
    assert contracted_graph.lean, "stage 9 must advertise the lean contract"
    blob = pickle.dumps(contracted_graph)
    # A reported measurement, not a gate: `-s` surfaces the number. Bounded only for
    # sanity — non-trivial, and far below the ~166 MB `graph.pkl` scale a full r20
    # graph reaches.
    print(f"\ncontracted_graph pickle size: {len(blob):,} bytes")
    assert len(blob) > 0


def test_parallel_two_workers(
    benchmark: BenchmarkFixture, contracted_graph: ContractedGraph
) -> None:
    """Time a 2-worker parallel solve (500+500 iters) incl. spawn + pickle overhead.

    Compare against `test_solver_throughput.py`'s single-process 1k-iteration baseline
    on the same graph to read the effective speedup (both are machine-local numbers).
    """

    def _run() -> None:
        run_parallel_grasp(contracted_graph, BENCH_PARAMS, seed=BENCH_PARAMS.seed, workers=2)

    # Sanity-check the same-seed determinism holds before timing, so a broken merge
    # can't masquerade as a fast run.
    first = run_parallel_grasp(contracted_graph, BENCH_PARAMS, seed=BENCH_PARAMS.seed, workers=2)
    second = run_parallel_grasp(contracted_graph, BENCH_PARAMS, seed=BENCH_PARAMS.seed, workers=2)
    assert _edge_id_sequences(first.solutions) == _edge_id_sequences(second.solutions)

    benchmark.pedantic(_run, rounds=3, warmup_rounds=0)


@dataclass(frozen=True, slots=True)
class _ProcessTreeMemory:
    rss_kib: int
    pss_kib: int
    private_kib: int
    processes: int
    worker_processes: int


@dataclass(slots=True)
class _PeakProcessTreeMemory:
    rss_kib: int = 0
    pss_kib: int = 0
    private_kib: int = 0
    samples: int = 0
    max_processes: int = 0
    max_worker_processes: int = 0

    def observe(self, sample: _ProcessTreeMemory) -> None:
        self.rss_kib = max(self.rss_kib, sample.rss_kib)
        self.pss_kib = max(self.pss_kib, sample.pss_kib)
        self.private_kib = max(self.private_kib, sample.private_kib)
        self.max_processes = max(self.max_processes, sample.processes)
        self.max_worker_processes = max(self.max_worker_processes, sample.worker_processes)
        self.samples += 1


def _descendant_pids() -> frozenset[int]:
    proc = Path("/proc")
    if not proc.exists():
        return frozenset()
    pending = [os.getpid()]
    seen: set[int] = set()
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            children = (proc / str(pid) / "task" / str(pid) / "children").read_text().split()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        pending.extend(int(value) for value in children)
    seen.discard(os.getpid())
    return frozenset(seen)


def _process_tree_memory_kib(
    excluded_descendants: frozenset[int],
) -> _ProcessTreeMemory | None:
    proc = Path("/proc")
    if not proc.exists():
        return None
    root_pid = os.getpid()
    pending = [root_pid]
    seen: set[int] = set()
    totals = {"Rss": 0, "Pss": 0, "Private": 0}
    processes = 0
    worker_processes = 0
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if pid != root_pid and pid in excluded_descendants:
            continue
        try:
            children = (proc / str(pid) / "task" / str(pid) / "children").read_text().split()
            pending.extend(int(value) for value in children)
            lines = (proc / str(pid) / "smaps_rollup").read_text().splitlines()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        values = {
            key: int(line.split()[1])
            for line in lines
            if (key := line.split(":", 1)[0]) in {"Rss", "Pss", "Private_Clean", "Private_Dirty"}
        }
        totals["Rss"] += values.get("Rss", 0)
        totals["Pss"] += values.get("Pss", 0)
        totals["Private"] += values.get("Private_Clean", 0) + values.get("Private_Dirty", 0)
        processes += 1
        try:
            command = (proc / str(pid) / "cmdline").read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            command = b""
        worker_processes += b"spawn_main" in command
    if processes == 0:
        return None
    return _ProcessTreeMemory(
        totals["Rss"],
        totals["Pss"],
        totals["Private"],
        processes,
        worker_processes,
    )


def _monitor_process_tree_memory(
    stop: threading.Event,
    peaks: _PeakProcessTreeMemory,
    excluded_descendants: frozenset[int],
) -> None:
    while True:
        sample = _process_tree_memory_kib(excluded_descendants)
        if sample is not None:
            peaks.observe(sample)
        if stop.wait(0.05):
            return


@pytest.mark.parametrize("workers", (2, 4, 8))
@pytest.mark.parametrize("backend", ("object", "blob", "array"))
def test_parallel_backend_phase_and_ownership_report(
    contracted_graph: ContractedGraph,
    workers: int,
    backend: Literal["object", "blob", "array"],
) -> None:
    preexisting_descendants = _descendant_pids()
    memory_stop = threading.Event()
    memory_peaks = _PeakProcessTreeMemory()
    memory_thread = threading.Thread(
        target=_monitor_process_tree_memory,
        args=(memory_stop, memory_peaks, preexisting_descendants),
        daemon=True,
    )
    memory_thread.start()
    start = time.perf_counter()
    try:
        result = run_parallel_grasp(
            contracted_graph,
            BENCH_PARAMS,
            seed=BENCH_PARAMS.seed,
            workers=workers,
            _backend=backend,
        )
    finally:
        memory_stop.set()
        memory_thread.join(timeout=30.0)
    assert not memory_thread.is_alive()
    pre_return_s = time.perf_counter() - start
    if memory_peaks.samples:
        assert memory_peaks.max_worker_processes >= result.effective_workers
    cleanup_start = time.perf_counter()
    assert result.cleanup is not None and result.cleanup.wait(120.0)
    cleanup_wait_s = time.perf_counter() - cleanup_start
    assert result.timings is not None
    print(
        f"\nbackend={backend} workers={workers} shared_bytes={result.timings.shared_bytes:,} "
        f"parent_build={result.timings.parent_build_s:.6f}s "
        f"spawn_attach={result.timings.startup_s:.6f}s solve={result.timings.solve_s:.6f}s "
        f"pre_return={pre_return_s:.6f}s cleanup_wait={cleanup_wait_s:.6f}s "
        f"reaper={result.cleanup.elapsed_s!r}s peak_tree_rss_kib={memory_peaks.rss_kib:,} "
        f"peak_tree_pss_kib={memory_peaks.pss_kib:,} "
        f"peak_tree_private_kib={memory_peaks.private_kib:,} "
        f"memory_samples={memory_peaks.samples} "
        f"max_tree_processes={memory_peaks.max_processes} "
        f"max_worker_processes={memory_peaks.max_worker_processes}"
    )


def test_shared_state_component_report(contracted_graph: ContractedGraph) -> None:
    graph_blob = pickle.dumps(contracted_graph)
    started = time.perf_counter()
    blob_owner = SharedBlobOwner(graph_blob)
    blob_copy_s = time.perf_counter() - started
    started = time.perf_counter()
    loaded = load_shared_blob(blob_owner.descriptor)
    blob_attach_unpickle_s = time.perf_counter() - started
    blob_owner.close_unlink()
    assert isinstance(loaded, ContractedGraph)

    started = time.perf_counter()
    context = GraspSolver(
        contracted_graph, BENCH_PARAMS, __import__("numpy").random.default_rng(0)
    ).static_context
    array_owner = SharedSolverOwner(context)
    csr_build_s = time.perf_counter() - started
    started = time.perf_counter()
    state = SharedSolverState(array_owner.descriptor)
    csr_attach_s = time.perf_counter() - started
    state.close()
    array_owner.close_unlink()
    print(
        f"\nblob_bytes={len(graph_blob):,} blob_copy={blob_copy_s:.6f}s "
        f"blob_attach_unpickle={blob_attach_unpickle_s:.6f}s "
        f"csr_bytes={array_owner.descriptor.size:,} csr_build={csr_build_s:.6f}s "
        f"csr_attach={csr_attach_s:.6f}s"
    )
