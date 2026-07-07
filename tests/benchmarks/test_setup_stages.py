# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false, reportMissingTypeStubs=false, reportUnknownLambdaType=false
# Reason: same osmnx/networkx boundary as the pipeline modules; pytest-benchmark
# ships no type information; `reportImplicitRelativeImport` — `from conftest
# import ...` is the shape that resolves under pytest's prepend import mode.
"""Setup-stage wall-clock baselines on committed fixture data (Story 11.3 AC #3).

One benchmark per CPU-bound setup stage, chained off the session-scoped
stage-input fixtures in `conftest.py` (each stage's input is built once; the
stage functions are pure per the architecture's pipeline-boundary rule, so
re-running them across rounds is sound). No live network anywhere:

- Stage 1 is benchmarked as its offline stand-in — graphml parse + `normalize_edges`
  of the committed fixture — NOT the Overpass download it replaces in production.
- The two network stages (`osm-download`, `dem-resolve`) are out of benchmark
  scope by construction; their baseline is Story 11.2's cold-cache capture
  (~81% of a 54 s real setup), recorded in
  `_bmad-output/planning-artifacts/research/profiling/setup-timeline.txt`.
- Stage 5 benchmarks `sample_elevation` against the committed DEM GeoTIFF (local
  raster I/O + interpolation — the CPU-side cost of the `elevation-sampling`
  stage; production `attach_elevation` adds only a microsecond finite guard).

`difficulty_cap="T6"` on the filter benchmark mirrors what production setup pins
(`pipeline._SETUP_DIFFICULTY_CAP` — setup is difficulty-independent; the user cap
is a query-side knob).

Run: `uv run pytest tests/benchmarks -m benchmark` (see README "Performance
benchmarks" for the autosave/compare workflow).
"""

from __future__ import annotations

import networkx as nx
import osmnx
import pytest
from conftest import (
    BENCH_DEADBAND_ACTIVE_M,
    BENCH_PARAMS,
    BENCH_UNTAGGED_POLICY,
    DEM_FIXTURE_PATH,
    OSM_FIXTURE_PATH,
)
from pytest_benchmark.fixture import BenchmarkFixture

from steeproute.models import Climb
from steeproute.pipeline.climbs import compute_edge_metrics
from steeproute.pipeline.dem import sample_elevation
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import filter_trails, normalize_edges
from steeproute.pipeline.smoothing import (
    graph_deadband_elevation,
    resample_edges,
    smooth_polylines,
)

pytestmark = pytest.mark.benchmark


def test_stage1_standin_graphml_load_normalize(benchmark: BenchmarkFixture) -> None:
    """Offline stage-1 stand-in: parse the committed graphml + `normalize_edges`."""
    if not OSM_FIXTURE_PATH.is_file():
        pytest.skip("grenoble_small OSM fixture not committed.")
    benchmark(lambda: normalize_edges(osmnx.load_graphml(OSM_FIXTURE_PATH)))


def test_stage2_filter_trails(benchmark: BenchmarkFixture, raw_osm_graph: nx.MultiDiGraph) -> None:
    benchmark(filter_trails, raw_osm_graph, BENCH_UNTAGGED_POLICY, "T6")


def test_stage3_smooth_polylines(
    benchmark: BenchmarkFixture, filtered_graph: nx.MultiDiGraph
) -> None:
    benchmark(smooth_polylines, filtered_graph)


def test_stage4_resample_edges(
    benchmark: BenchmarkFixture, smoothed_graph: nx.MultiDiGraph
) -> None:
    benchmark(resample_edges, smoothed_graph)


def test_stage5_sample_elevation(
    benchmark: BenchmarkFixture, resampled_graph: nx.MultiDiGraph
) -> None:
    benchmark(sample_elevation, resampled_graph, DEM_FIXTURE_PATH)


# --- Query-side stages (6b / 7 / 9) — Story 14.2 (Q2, Q3) --------------------


def test_stage6b_graph_deadband_elevation(
    benchmark: BenchmarkFixture, smoothed_elevation_graph: nx.MultiDiGraph
) -> None:
    """Stage 6b deadband over an active (non-zero) threshold — the real hysteresis path."""
    benchmark(graph_deadband_elevation, smoothed_elevation_graph, BENCH_DEADBAND_ACTIVE_M)


def test_stage7_compute_edge_metrics(
    benchmark: BenchmarkFixture, metrics_input_graph: nx.MultiDiGraph
) -> None:
    """Stage 7 per-edge metrics (length, gain/loss, avg gradient, windowed descent)."""
    benchmark(compute_edge_metrics, metrics_input_graph)


def test_stage9_contract_climbs(
    benchmark: BenchmarkFixture, routable_and_climbs: tuple[nx.MultiDiGraph, list[Climb]]
) -> None:
    """Stage 9 contracted-graph build — the Q3 profiling seam (optimize only if material)."""
    routable, climbs = routable_and_climbs
    benchmark(
        contract_climbs,
        routable,
        climbs,
        l_connector=BENCH_PARAMS.l_connector,
        annotate_junctions=BENCH_PARAMS.start_at_junction,
    )
