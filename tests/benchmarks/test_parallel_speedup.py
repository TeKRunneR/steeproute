# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false, reportMissingTypeStubs=false
# Reason: pytest-benchmark ships no type information (the `benchmark` fixture and
# `BenchmarkFixture` resolve as Unknown); `reportImplicitRelativeImport` — `from
# conftest import ...` is the prepend-import shape (see test_solver_throughput.py).
"""Parallel GRASP startup + speedup baseline (Story 14.4 AC #3).

Two opt-in measurements on the grenoble_small contracted graph (excluded from the
default suite — run with `uv run pytest tests/benchmarks -m benchmark`):

- **per-worker startup payload** — `pickle.dumps(contracted_graph)` size, the
  dominant cost a spawned worker pays on top of process launch (the handoff's
  "measure the ContractedGraph pickle" item). Reported, not gated.
- **parallel wall-clock** — `run_parallel_grasp(workers=2)` timed against the
  single-process `test_solver_throughput.py` baseline on the same graph, so the
  effective speedup can be read off the two `.benchmarks/` entries. Full-scale
  (r50, more cores) speedup lands at the 14.6 probe.
"""

from __future__ import annotations

import pickle

import pytest
from conftest import BENCH_PARAMS
from pytest_benchmark.fixture import BenchmarkFixture

from steeproute.models import ContractedGraph, Solution
from steeproute.solver.parallel import run_parallel_grasp

pytestmark = pytest.mark.benchmark


def _edge_id_sequences(solutions: list[Solution]) -> list[list[tuple[int, int, int]]]:
    return [[(e.node_u, e.node_v, e.key) for e in s.edges] for s in solutions]


def test_contracted_graph_pickle_size(contracted_graph: ContractedGraph) -> None:
    """Report the per-worker pickle payload — the dominant spawn startup cost (AC #3)."""
    blob = pickle.dumps(contracted_graph)
    # A reported measurement, not a gate. `-s` surfaces the number; it is also the
    # figure recorded in the story close-out. Sanity-bound only: non-trivial and far
    # below the graph.pkl scale (166 MB @ r20) the handoff flagged for the full graph.
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
