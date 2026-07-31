# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportMissingTypeStubs=false, reportPrivateUsage=false
# Reason: pytest-benchmark has no type stubs; private pure helpers are measured
# directly so the endpoint, cardinality, and ordering changes stay separable.
"""Micro-benchmarks for Story 16.5's distinctness-loop changes."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from steeproute.models import Solution
from steeproute.solver.distinctness import TopNTracker, _jaccard_from_sets

pytestmark = pytest.mark.benchmark

_REPEATS = 1_000


def _exercise_endpoint(solution: Solution, j_max: float) -> list[Solution]:
    tracker = TopNTracker(n=64, j_max=j_max)
    for index in range(_REPEATS):
        tracker.consider(replace(solution, objective=float(_REPEATS - index)))
    return tracker.current_top()


@pytest.mark.parametrize("j_max", [0.0, 1.0], ids=["jmax-zero", "jmax-one"])
def test_jmax_endpoint_fast_path(
    benchmark: BenchmarkFixture,
    solved_route_set: list[Solution],
    j_max: float,
) -> None:
    """Time each inclusive endpoint independently of general Jaccard math."""
    assert benchmark.pedantic(
        _exercise_endpoint,
        args=(solved_route_set[0], j_max),
        rounds=10,
        warmup_rounds=2,
    )


def test_general_jaccard_without_union_allocation(benchmark: BenchmarkFixture) -> None:
    """Time the general cardinality path on overlapping non-empty sets."""
    left = frozenset((index, index + 1, 0) for index in range(1_000))
    right = frozenset((index, index + 1, 0) for index in range(500, 1_500))

    distance = benchmark.pedantic(
        _jaccard_from_sets,
        args=(left, right),
        rounds=20,
        iterations=100,
        warmup_rounds=3,
    )
    assert distance == 1.0 - 500 / 1_500


def test_cached_held_sort_keys(
    benchmark: BenchmarkFixture,
    solved_route_set: list[Solution],
) -> None:
    """Time repeated ordering reads after keys have been cached at admission."""
    tracker = TopNTracker(n=64, j_max=1.0)
    for index in range(64):
        tracker.consider(replace(solved_route_set[0], objective=float(index)))

    def _read_top_repeatedly() -> list[Solution]:
        result: list[Solution] = []
        for _ in range(_REPEATS):
            result = tracker.current_top()
        return result

    assert benchmark.pedantic(_read_top_repeatedly, rounds=10, warmup_rounds=2)
