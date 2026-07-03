# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false, reportMissingTypeStubs=false
# Reason: pytest-benchmark ships no type information (the `benchmark` fixture and
# `BenchmarkFixture` resolve as Unknown); `reportImplicitRelativeImport` — `from
# conftest import ...` is the shape that resolves under pytest's prepend import
# mode (see tests/integration/test_oracle_correctness.py for the rationale).
"""Solver throughput baseline: seconds per 1k GRASP iterations (Story 11.3 AC #2).

Measures `GraspSolver.run()` only — construction (`base_segment_id_map`, tracker,
node sort) happens in the per-round `setup` callable, outside the measured region.
Each round runs *exactly* `BENCH_PARAMS.iter_budget` (1000) iterations: stagnation
is disabled (`stagnation_iters=0`) and the time budget is pinned high, so the
iteration budget is the only live terminator — asserted via `convergence_status`
after the run, so a silent early-exit can't fake a speedup.

Run: `uv run pytest tests/benchmarks -m benchmark` (see README "Performance
benchmarks" for the autosave/compare workflow).
"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import BENCH_PARAMS
from pytest_benchmark.fixture import BenchmarkFixture

from steeproute.models import ContractedGraph
from steeproute.solver.grasp import GraspSolver

pytestmark = pytest.mark.benchmark


def test_grasp_1k_iterations(
    benchmark: BenchmarkFixture, contracted_graph: ContractedGraph
) -> None:
    """Time 1k seeded GRASP iterations on the grenoble_small contracted graph."""
    solvers: list[GraspSolver] = []

    def _fresh_solver() -> tuple[tuple[GraspSolver], dict[str, object]]:
        # A solver instance is single-run (tracker state accumulates), so each
        # round gets a fresh solver + fresh seeded RNG — identical workload.
        solver = GraspSolver(
            contracted_graph, BENCH_PARAMS, np.random.default_rng(BENCH_PARAMS.seed)
        )
        solvers.append(solver)
        return (solver,), {}

    def _run(solver: GraspSolver) -> None:
        solver.run()

    benchmark.pedantic(_run, setup=_fresh_solver, rounds=5, warmup_rounds=1)

    # Every round must have exhausted the full 1000-iteration budget — anything
    # else means the "seconds per 1k iterations" metric measured fewer.
    assert solvers, "benchmark ran zero rounds"
    assert all(s.convergence_status == "budget-exhausted" for s in solvers), (
        f"expected every round to exhaust iter_budget={BENCH_PARAMS.iter_budget}; "
        f"got statuses {sorted({s.convergence_status for s in solvers})}"
    )
