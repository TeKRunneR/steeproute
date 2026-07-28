# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false, reportMissingTypeStubs=false
# Reason: same osmnx/networkx boundary as the pipeline modules; pytest-benchmark
# ships no type information; `reportImplicitRelativeImport` — `from conftest
# import ...` is the shape that resolves under pytest's prepend import mode.
"""Query-orchestration wall-clock baselines.

The seams the ownership pass targets that had no benchmark coverage:

- the **query-side** trail-filter redux — re-filtering the metrics-bearing
  operational graph at the user's `--difficulty-cap`. Distinct from
  `test_setup_stages.py::test_stage2_filter_trails`, which measures the
  *setup-side* stage-2 filter at `T6` over the raw graph.
- `validate` over a whole route set — where graph-wide invariants were derived
  once per route rather than once per call.
- the prepared-entry load (`check_coverage`), and — measured separately — the
  per-edge `LineString` reconstruction the schema-v2 read paid on top of it.

Both are measured against the committed `grenoble_small` fixture, so the numbers
are a machine-local before/after signal, not an r20 projection: the review's r20
anchors come from the real CLI (AGENTS.md §Scale target forbids extrapolating a
component benchmark to the whole operation).

Run: `uv run pytest tests/benchmarks -m benchmark`.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pytest
import shapely
from conftest import (
    BENCH_CENTER,
    BENCH_DIFFICULTY_CAP,
    BENCH_PARAMS,
    BENCH_RADIUS_KM,
    BENCH_UNTAGGED_POLICY,
    E2E_CACHE_ROOT,
)
from pytest_benchmark.fixture import BenchmarkFixture

from steeproute.cache import check_coverage
from steeproute.models import Area, ContractedGraph, Solution
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


def test_cache_load_prepared_entry(benchmark: BenchmarkFixture) -> None:
    """The `load-prepared-area` stage: index walk + entry deserialization.

    A guardrail, not the headline: the read must not get *slower* now that it no
    longer reconstructs geometry. The real number is the r20 stage line — a 1.5 km
    fixture entry is ~1.4 MB against r20's ~166 MB — per AGENTS.md §Scale target.
    """
    prepared = benchmark(
        check_coverage, E2E_CACHE_ROOT, Area(center=BENCH_CENTER, radius_km=BENCH_RADIUS_KM)
    )
    assert prepared.graph.number_of_edges() > 0


def test_cache_geometry_reconstruction(
    benchmark: BenchmarkFixture, prepared_grenoble_graph: nx.MultiDiGraph
) -> None:
    """The bulk `LineString` rebuild + reattach that the schema-v2 read paid per load.

    Isolated on purpose: the payload schema no longer stores geometry, so nothing in
    production does this work any more. Benchmarking it here keeps it measurable, and
    the pair (this vs `test_cache_load_prepared_entry`) shows what share of a load it
    accounted for. The ragged arrays are built in `setup`,
    outside the measured region — v2 read them straight out of the pickle, so
    building them here would fold array construction into a number that is meant to
            be the rebuild alone.
    """

    def _fresh_inputs() -> tuple[
        tuple[nx.MultiDiGraph, np.ndarray, tuple[np.ndarray]], dict[str, object]
    ]:
        lengths = [
            len(data["vertices_resampled"])
            for _u, _v, data in prepared_grenoble_graph.edges(data=True)
        ]
        coords = np.array(
            [
                (lon, lat)
                for _u, _v, data in prepared_grenoble_graph.edges(data=True)
                for lat, lon, _elev in data["vertices_resampled"]
            ],
            dtype=np.float64,
        )
        offsets = np.concatenate(([0], np.cumsum(lengths))).astype(np.int64)
        # A fresh graph per round: the attach mutates edge dicts, and the session
        # fixture is shared with every other query-stage benchmark.
        return (prepared_grenoble_graph.copy(), coords, (offsets,)), {}

    def _reconstruct(
        graph: nx.MultiDiGraph, coords: np.ndarray, offsets: tuple[np.ndarray]
    ) -> None:
        geometries = shapely.from_ragged_array(shapely.GeometryType.LINESTRING, coords, offsets)
        for (_u, _v, data), geometry in zip(graph.edges(data=True), geometries, strict=True):
            data["geometry"] = geometry

    benchmark.pedantic(_reconstruct, setup=_fresh_inputs, rounds=20, warmup_rounds=1)


def test_validate_route_set(
    benchmark: BenchmarkFixture,
    solved_route_set: list[Solution],
    contracted_graph: ContractedGraph,
) -> None:
    """Validate a real N-route solver result — the per-route full-graph-rescan seam.

    Guardrail, not the headline: `grenoble_small` is a 1.5 km area, so its contracted
    graph is small enough that the hoisted per-route graph rescans barely register.
    The win is visible only at r20 scale, and per AGENTS.md §Scale target a component
    benchmark must not be extrapolated there. All this pins is that validation does
    not get *slower*.
    """
    result = benchmark(validate, solved_route_set, contracted_graph, BENCH_PARAMS)
    # A collapsed result would make the metric meaningless (one route pays one
    # scan either way), so pin what was actually measured.
    assert len(result.routes) == len(solved_route_set)
