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
from conftest import BENCH_UNTAGGED_POLICY, DEM_FIXTURE_PATH, OSM_FIXTURE_PATH
from pytest_benchmark.fixture import BenchmarkFixture

from steeproute.pipeline.dem import sample_elevation
from steeproute.pipeline.osm import filter_trails, normalize_edges
from steeproute.pipeline.smoothing import resample_edges, smooth_polylines

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
