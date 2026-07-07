# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
# Reason: same osmnx/networkx boundary as the underlying pipeline modules;
# `reportPrivateUsage` — the fixture chain calls the orchestrator's private guard
# prunes (`_drop_orphan_nodes`/`_drop_short_edges`) so each stage's benchmark input
# matches production exactly (same shape as tests/integration/test_pipeline_end_to_end.py).
"""Benchmark-suite fixtures + locally-pinned parameters (Story 11.3).

Everything here is pinned **locally, on purpose** — never imported from
`steeproute.regression` or CLI defaults. The regression pins exist so route
*output* can't drift silently; these pins exist so throughput *baselines*
can't drift silently. A future re-tune of either must not move the other
(Story 11.3 AC #4).

The suite measures time, never route output (quality is the goldens' job —
mixing the two in one metric makes both noisy, per the performance-tuning
research). Network stages (Overpass download, DEM WMS fetch) are out of
benchmark scope by construction; their baseline is the cold-cache capture in
`_bmad-output/planning-artifacts/research/profiling/setup-timeline.txt`.

Baselines are machine-local: numbers in `.benchmarks/` are only comparable to
runs on the same machine. See README "Performance benchmarks".
"""

from __future__ import annotations

import pathlib

import networkx as nx
import osmnx
import pytest

from steeproute.cache import check_coverage
from steeproute.models import Area, Climb, ContractedGraph, SolverParams
from steeproute.pipeline import _drop_orphan_nodes, _drop_short_edges, operationalize_graph
from steeproute.pipeline.climbs import compute_edge_metrics, detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import filter_trails, normalize_edges
from steeproute.pipeline.smoothing import (
    graph_deadband_elevation,
    graph_smooth_elevation,
    resample_edges,
    smooth_polylines,
)

_TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]

# The committed grenoble_small artifacts. The e2e cache is a full queryable cache
# root (feeds the solver benchmark); the unit/integration fixture pair is the raw
# pre-pipeline input (feeds the setup-stage benchmarks).
E2E_CACHE_ROOT = _TESTS_ROOT / "e2e" / "fixtures" / "grenoble_small" / "cache"
OSM_FIXTURE_PATH = _TESTS_ROOT / "fixtures" / "grenoble_small" / "osm_graph.graphml"
DEM_FIXTURE_PATH = _TESTS_ROOT / "fixtures" / "grenoble_small" / "dem.tif"

# grenoble_small query geometry — hardcoded here (not imported from
# `regression.FIXTURES`) per the local-pinning rule above.
BENCH_CENTER: tuple[float, float] = (45.260, 5.788)
BENCH_RADIUS_KM: float = 1.5
BENCH_SEED: int = 42

# Exactly 1000 GRASP iterations per measured round ("seconds per 1k iterations"):
# `stagnation_iters=0` disables stagnation (§Cat 5e) and `time_budget` is pinned
# high so wall-clock never binds — `iter_budget` is the only live terminator, and
# `test_solver_throughput.py` asserts it was the one that fired. The remaining
# values mirror the fast-tier regression pins as of 2026-07-03 (copied, not
# imported); `untagged_policy`/`difficulty_cap` also drive the graph-shaping
# calls in the fixtures below.
BENCH_UNTAGGED_POLICY: str = "include"
BENCH_DIFFICULTY_CAP: str = "T3"
BENCH_ELEVATION_SMOOTHING_M: float = 50.0
BENCH_ELEVATION_DEADBAND_M: float = 0.0

# Non-zero deadband for the `graph_deadband_elevation` benchmark ONLY. The
# production default (`BENCH_ELEVATION_DEADBAND_M` above) is 0.0, which short-
# circuits to a no-op — useless as a throughput baseline. 2 m exercises the real
# per-edge hysteresis + interpolation path. Pinned locally, never a CLI default.
BENCH_DEADBAND_ACTIVE_M: float = 2.0
BENCH_PARAMS = SolverParams(
    theta=0.20,
    min_climb_slope=0.20,
    difficulty_cap=BENCH_DIFFICULTY_CAP,
    l_connector=200.0,
    min_climb_ground_length=300.0,
    j_max=0.30,
    n=5,
    area_cap=500.0,
    untagged_policy=BENCH_UNTAGGED_POLICY,
    seed=BENCH_SEED,
    iter_budget=1000,
    time_budget=100000.0,
    stagnation_iters=0,
    # Pinned explicitly (not inherited from the model default): it shapes the
    # contracted graph via `annotate_junctions`, so a future default flip must
    # not silently move the solver baseline.
    start_at_junction=False,
)


@pytest.fixture(scope="session")
def contracted_graph() -> ContractedGraph:
    """The grenoble_small contracted climb graph, built once from the committed cache.

    Replays the query-side sequence from `cli/query.py` (cache load →
    operationalize → filter → detect → contract) so the solver benchmark runs on
    exactly the graph a real query would. Read-only across rounds: `GraspSolver`
    never mutates its graph, so one session-scoped build is sound.
    """
    if not (E2E_CACHE_ROOT / "steeproute" / "index.json").is_file():
        pytest.skip("grenoble_small e2e fixture cache not committed; solver benchmark skipped.")
    prepared = check_coverage(E2E_CACHE_ROOT, Area(center=BENCH_CENTER, radius_km=BENCH_RADIUS_KM))
    operational = operationalize_graph(
        prepared.graph,
        elevation_smoothing_m=BENCH_ELEVATION_SMOOTHING_M,
        elevation_deadband_m=BENCH_ELEVATION_DEADBAND_M,
    )
    routable = filter_trails(operational, BENCH_UNTAGGED_POLICY, BENCH_DIFFICULTY_CAP)
    climbs = detect_climbs(
        routable,
        min_climb_slope=BENCH_PARAMS.min_climb_slope,
        min_climb_ground_length=BENCH_PARAMS.min_climb_ground_length,
    )
    return contract_climbs(
        routable,
        climbs,
        l_connector=BENCH_PARAMS.l_connector,
        annotate_junctions=BENCH_PARAMS.start_at_junction,
    )


# --- Setup-stage input chain -------------------------------------------------
#
# Each fixture builds one stage's *input* exactly as `pipeline.build_graph_geometry`
# would hand it over (including the inter-stage guard prunes, which live in the
# fixtures — outside the measured region — mirroring the orchestrator's folding of
# guards into their preceding stage). Stage functions are pure (the architecture's
# pipeline-boundary rule), so re-running them across benchmark rounds on a shared
# input is sound. Session-scoped: built once, read many.


@pytest.fixture(scope="session")
def raw_osm_graph() -> nx.MultiDiGraph:
    """Stage-1 output stand-in: the committed graphml fixture, normalized."""
    if not OSM_FIXTURE_PATH.is_file():
        pytest.skip("grenoble_small OSM fixture not committed; setup-stage benchmarks skipped.")
    return normalize_edges(osmnx.load_graphml(OSM_FIXTURE_PATH))


@pytest.fixture(scope="session")
def filtered_graph(raw_osm_graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Post-stage-2 graph (filter + orphan prune), input to `smooth_polylines`."""
    return _drop_orphan_nodes(
        filter_trails(raw_osm_graph, BENCH_UNTAGGED_POLICY, difficulty_cap="T6")
    )


@pytest.fixture(scope="session")
def smoothed_graph(filtered_graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Post-stage-3 graph, input to `resample_edges`."""
    return smooth_polylines(filtered_graph)


@pytest.fixture(scope="session")
def resampled_graph(smoothed_graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Post-stage-4 graph (resample + short-edge prune), input to `sample_elevation`."""
    if not DEM_FIXTURE_PATH.is_file():
        pytest.skip("grenoble_small DEM fixture not committed; elevation benchmark skipped.")
    return _drop_short_edges(resample_edges(smoothed_graph))


# --- Query-stage input chain (stages 6b/7/9) --------------------------------
#
# These replay the query-side reshaping from `cli/query.py` off the committed
# e2e cache (the same raw post-stage-5 graph the solver benchmark starts from),
# so the stage-6b / stage-7 / stage-9 seams measure exactly what a real query
# pays. Session-scoped: built once, read many; the stage functions are pure.


@pytest.fixture(scope="session")
def prepared_grenoble_graph() -> nx.MultiDiGraph:
    """Raw post-stage-5 graph from the committed e2e cache (query-side input)."""
    if not (E2E_CACHE_ROOT / "steeproute" / "index.json").is_file():
        pytest.skip(
            "grenoble_small e2e fixture cache not committed; query-stage benchmarks skipped."
        )
    return check_coverage(
        E2E_CACHE_ROOT, Area(center=BENCH_CENTER, radius_km=BENCH_RADIUS_KM)
    ).graph


@pytest.fixture(scope="session")
def smoothed_elevation_graph(prepared_grenoble_graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Post stage-6a (elevation smoothing), input to the `graph_deadband_elevation` bench."""
    return graph_smooth_elevation(prepared_grenoble_graph, BENCH_ELEVATION_SMOOTHING_M)


@pytest.fixture(scope="session")
def metrics_input_graph(smoothed_elevation_graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Post stage-6 (smooth + deadband), input to the stage-7 `compute_edge_metrics` bench."""
    return graph_deadband_elevation(smoothed_elevation_graph, BENCH_ELEVATION_DEADBAND_M)


@pytest.fixture(scope="session")
def routable_and_climbs(
    metrics_input_graph: nx.MultiDiGraph,
) -> tuple[nx.MultiDiGraph, list[Climb]]:
    """`(routable_graph, climbs)` — the two inputs to `contract_climbs` (stage 9)."""
    operational = compute_edge_metrics(metrics_input_graph)
    routable = filter_trails(operational, BENCH_UNTAGGED_POLICY, BENCH_DIFFICULTY_CAP)
    climbs = detect_climbs(
        routable,
        min_climb_slope=BENCH_PARAMS.min_climb_slope,
        min_climb_ground_length=BENCH_PARAMS.min_climb_ground_length,
    )
    return routable, climbs
