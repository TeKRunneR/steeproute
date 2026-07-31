# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false, reportMissingTypeStubs=false
# Reason: pytest-benchmark ships no type information (the `benchmark` fixture and
# `BenchmarkFixture` resolve as Unknown); `reportImplicitRelativeImport` — `from
# conftest import ...` is the shape that resolves under pytest's prepend import
# mode (see tests/integration/test_oracle_correctness.py for the rationale);
# `_construct_one` is the stable interrupt/test seam measured directly here.
# pyright: reportPrivateUsage=false
"""Solver throughput baseline: seconds per 1k GRASP iterations.

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
from steeproute.solver.grasp import GraspSolver, SolverStaticContext

pytestmark = pytest.mark.benchmark

_COMPONENT_ITERATIONS = 1_000


def test_solver_static_context_construction(
    benchmark: BenchmarkFixture, contracted_graph: ContractedGraph
) -> None:
    """Time init plus every graph-derived static field, including adjacency."""

    def _build() -> SolverStaticContext:
        solver = GraspSolver(
            contracted_graph, BENCH_PARAMS, np.random.default_rng(BENCH_PARAMS.seed)
        )
        return solver.static_context

    context = benchmark.pedantic(_build, rounds=5, warmup_rounds=1)
    assert context.adjacency


def test_grasp_1k_hot_loop_with_static_context(
    benchmark: BenchmarkFixture, contracted_graph: ContractedGraph
) -> None:
    """Time only steady-state iterations against one prebuilt static context."""
    owner = GraspSolver(contracted_graph, BENCH_PARAMS, np.random.default_rng(BENCH_PARAMS.seed))
    context = owner.static_context
    solvers: list[GraspSolver] = []

    def _fresh_solver() -> tuple[tuple[GraspSolver], dict[str, object]]:
        solver = GraspSolver(
            contracted_graph,
            BENCH_PARAMS,
            np.random.default_rng(BENCH_PARAMS.seed),
            static_context=context,
        )
        solvers.append(solver)
        return (solver,), {}

    benchmark.pedantic(
        GraspSolver.run,
        setup=_fresh_solver,
        rounds=5,
        warmup_rounds=1,
    )
    assert all(s.convergence_status == "budget-exhausted" for s in solvers)


def test_discarded_constructed_walk_objective_sum(
    benchmark: BenchmarkFixture, contracted_graph: ContractedGraph
) -> None:
    """Measure the exact temporary sum removed from `_construct_one`."""
    owner = GraspSolver(contracted_graph, BENCH_PARAMS, np.random.default_rng(BENCH_PARAMS.seed))
    context = owner.static_context
    constructor = GraspSolver(
        contracted_graph,
        BENCH_PARAMS,
        np.random.default_rng(BENCH_PARAMS.seed),
        static_context=context,
    )
    walks = [constructor._construct_one().edges for _ in range(_COMPONENT_ITERATIONS)]

    def _discarded_sums() -> float:
        return sum(sum((edge.d_plus_m + edge.d_minus_m for edge in walk), 0.0) for walk in walks)

    assert benchmark.pedantic(_discarded_sums, rounds=10, warmup_rounds=2) >= 0.0


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
