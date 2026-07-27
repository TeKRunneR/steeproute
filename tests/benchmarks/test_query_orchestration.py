# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false, reportMissingTypeStubs=false
# Reason: same osmnx/networkx boundary as the pipeline modules; pytest-benchmark
# ships no type information; `reportImplicitRelativeImport` — `from conftest
# import ...` is the shape that resolves under pytest's prepend import mode.
"""Query-orchestration wall-clock baselines (Story 16.1).

The two seams the ownership pass targets that had no benchmark coverage:

- the **query-side** trail-filter redux — re-filtering the metrics-bearing
  operational graph at the user's `--difficulty-cap`. Distinct from
  `test_setup_stages.py::test_stage2_filter_trails`, which measures the
  *setup-side* stage-2 filter at `T6` over the raw graph.
- `validate` over a whole route set — where graph-wide invariants were derived
  once per route rather than once per call.

Both are measured against the committed `grenoble_small` fixture, so the numbers
are a machine-local before/after signal, not an r20 projection: the review's r20
anchors come from the real CLI (AGENTS.md §Scale target forbids extrapolating a
component benchmark to the whole operation).

Run: `uv run pytest tests/benchmarks -m benchmark`.
"""

from __future__ import annotations

import networkx as nx
import pytest
from conftest import BENCH_DIFFICULTY_CAP, BENCH_PARAMS, BENCH_UNTAGGED_POLICY
from pytest_benchmark.fixture import BenchmarkFixture

from steeproute.models import ContractedGraph, Solution
from steeproute.pipeline.osm import filter_trails
from steeproute.validator import validate

pytestmark = pytest.mark.benchmark


def test_query_filter_trails_copying(
    benchmark: BenchmarkFixture, operational_graph: nx.MultiDiGraph
) -> None:
    """Query-side stage-2 redux at the user difficulty cap, rebuilding the graph.

    The pure default path — still what every non-CLI caller gets, so it keeps its
    own baseline.
    """
    benchmark(filter_trails, operational_graph, BENCH_UNTAGGED_POLICY, BENCH_DIFFICULTY_CAP)


def test_query_filter_trails_consuming(
    benchmark: BenchmarkFixture, operational_graph: nx.MultiDiGraph
) -> None:
    """The same redux on the ownership path — what the query CLI actually runs.

    Each round needs a fresh graph (the call mutates it), so the copy happens in
    `setup`, outside the measured region — otherwise this would time `graph.copy()`
    rather than the filter.
    """

    def _fresh_graph() -> tuple[tuple[nx.MultiDiGraph], dict[str, object]]:
        return (operational_graph.copy(),), {}

    def _filter(graph: nx.MultiDiGraph) -> None:
        filter_trails(graph, BENCH_UNTAGGED_POLICY, BENCH_DIFFICULTY_CAP, consume=True)

    benchmark.pedantic(_filter, setup=_fresh_graph, rounds=20, warmup_rounds=1)


def test_validate_route_set(
    benchmark: BenchmarkFixture,
    solved_route_set: list[Solution],
    contracted_graph: ContractedGraph,
) -> None:
    """Validate a real N-route solver result — the per-route full-graph-rescan seam.

    Guardrail, not the headline: `grenoble_small` is a 1.5 km area, so its
    contracted graph is small enough that the per-route rescans this story hoisted
    barely register. The win is visible only at scale — see the r20 `validate-render`
    stage line in the story's close-out — and per AGENTS.md §Scale target a
    component benchmark must not be extrapolated to it. This pins that validation
    does not get *slower*.
    """
    result = benchmark(validate, solved_route_set, contracted_graph, BENCH_PARAMS)
    # A collapsed result would make the metric meaningless (one route pays one
    # scan either way), so pin what was actually measured.
    assert len(result.routes) == len(solved_route_set)
