# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
# Reason: same osmnx/networkx boundary as test_output_on_fixture.py; reportPrivateUsage
# relaxed because the box==curve assertion deliberately reuses `output._route_vertices`
# (the exact vertex list the renderer plots) to compare against the metric box.
"""Story 6.3 regression test: the metric box, the solver objective, and the plotted
elevation curve all read ONE canonical profile (box == curve).

The pre-6.3 design split the profile: the box/solver summed a per-edge-smoothed
elevation while the deadband was applied only at sum-time (it produced a *number*
and never reshaped the displayed vertices), so a route's reported D+/D- disagreed
with its plotted curve by tens of meters whenever the deadband was active. This
test runs the real query-side chain with BOTH `--elevation-smoothing` and
`--elevation-deadband` engaged and asserts:

1. box D+/D- (route.metrics) equals the plotted-curve cumulative at the final
   vertex (the exact vertex list `output.render` plots), within float tolerance;
2. the max per-segment |ΔElev| in the operational (smoothed + deadbanded) profile
   never exceeds the raw-DEM maximum — diffusion is low-pass, so no manufactured
   slope spikes.

Constructed to FAIL on the pre-fix code: a sum-time deadband (or a display-only
smoothing pass) breaks assertion 1; a per-edge moving-average that dumps a node
offset into one segment breaks assertion 2.
"""

from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import patch

import networkx as nx
import numpy as np
import osmnx
import pytest

from steeproute import output
from steeproute.models import Area, ContractedGraph, PipelineConfig, Route, SolverParams
from steeproute.pipeline import operationalize_graph, run_setup_stages
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import filter_trails, normalize_edges
from steeproute.solver.grasp import GraspSolver
from steeproute.validator import validate

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

# Both reshaping knobs ON so the box==curve guarantee is exercised under the
# configuration that breaks the pre-fix sum-time deadband. 50 m smoothing is the
# production default; a 3 m deadband is a realistic field value (flattens sub-3 m
# DEM wiggle) — any positive deadband stresses the invariant, the exact value is
# not a recommendation.
_ELEVATION_SMOOTHING_M = 50.0
_ELEVATION_DEADBAND_M = 3.0

_THETA = 0.20
_DIFFICULTY_CAP = "T3"
_L_CONNECTOR = 200.0
_MIN_CLIMB_GROUND_LENGTH_M = 300.0
_J_MAX = 0.30
_N = 3
_ITER_BUDGET = 200
_SEED = 42

# The plotted curve is summed from `output._route_vertices` (the unrounded vertex
# list the renderer derives the chart from). The metric box sums per-edge floats
# in a different association order, so a few-ULP reassociation gap is expected;
# 0.05 m is far below the ~58-78 m pre-fix disagreement this guards against.
_BOX_CURVE_TOL_M = 0.05


def _load_fixture_constants() -> tuple[float, float, int]:
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen_elev", regen_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CENTER_LAT, module.CENTER_LON, module.DIST_M


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()


def _params() -> SolverParams:
    return SolverParams(
        theta=_THETA,
        min_climb_slope=_THETA,
        difficulty_cap=_DIFFICULTY_CAP,
        l_connector=_L_CONNECTOR,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=_J_MAX,
        n=_N,
        area_cap=500.0,
        untagged_policy="include",
        seed=_SEED,
        iter_budget=_ITER_BUDGET,
        time_budget=60.0,
        stagnation_iters=0,
    )


@pytest.fixture(scope="module")
def query_run() -> tuple[nx.MultiDiGraph, nx.MultiDiGraph, ContractedGraph, list[Route]]:
    """Run the full query-side chain once: returns (raw_graph, operational_graph, contracted, routes)."""
    if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; elevation-consistency test skipped.")

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        raw_graph = run_setup_stages(area, config)

    # Mirror cli/query.py exactly: smooth → deadband → metrics, then filter for
    # the solver, render off the same operational graph.
    operational = operationalize_graph(
        raw_graph,
        elevation_smoothing_m=_ELEVATION_SMOOTHING_M,
        elevation_deadband_m=_ELEVATION_DEADBAND_M,
    )
    routable = filter_trails(operational, "include", _DIFFICULTY_CAP)
    climbs = detect_climbs(
        routable, min_climb_slope=_THETA, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M
    )
    contracted = contract_climbs(routable, climbs, l_connector=_L_CONNECTOR)
    solutions = GraspSolver(contracted, _params(), np.random.default_rng(_SEED)).run()
    assert solutions, "expected >= 1 GRASP route on the Grenoble fixture"
    validated = validate(solutions, contracted, _params())
    return raw_graph, operational, contracted, validated.routes


def _curve_gain_loss(vertices: list[tuple[float, float, float]]) -> tuple[float, float]:
    """Naive cumulative D+/D- over the plotted vertex list (the curve)."""
    d_plus = 0.0
    d_minus = 0.0
    for i in range(1, len(vertices)):
        delta = vertices[i][2] - vertices[i - 1][2]
        if delta > 0:
            d_plus += delta
        elif delta < 0:
            d_minus += -delta
    return d_plus, d_minus


def test_box_equals_curve_for_every_route(
    query_run: tuple[nx.MultiDiGraph, nx.MultiDiGraph, ContractedGraph, list[Route]],
) -> None:
    """Box D+/D- (route.metrics) == plotted-curve cumulative at the final vertex."""
    _raw, operational, contracted, routes = query_run
    assert routes, "expected >= 1 route"
    for i, route in enumerate(routes):
        vertices = output._route_vertices(route, operational, contracted.super_edge_to_base)
        assert len(vertices) >= 2, f"route {i}: no geometry resolved"
        curve_d_plus, curve_d_minus = _curve_gain_loss(vertices)
        assert abs(route.metrics.d_plus_m - curve_d_plus) <= _BOX_CURVE_TOL_M, (
            f"route {i}: box D+ {route.metrics.d_plus_m:.3f} != curve D+ {curve_d_plus:.3f}"
        )
        assert abs(route.metrics.d_minus_m - curve_d_minus) <= _BOX_CURVE_TOL_M, (
            f"route {i}: box D- {route.metrics.d_minus_m:.3f} != curve D- {curve_d_minus:.3f}"
        )


def test_profile_has_no_manufactured_spikes(
    query_run: tuple[nx.MultiDiGraph, nx.MultiDiGraph, ContractedGraph, list[Route]],
) -> None:
    """Max per-segment |ΔElev| in the reshaped profile never exceeds the raw-DEM max.

    Diffusion is a low-pass filter and the deadband only flattens, so neither can
    create a step larger than the raw DEM already had — the structural guarantee
    the rejected per-edge moving-average violated (it manufactured ~1000% spikes).
    """
    raw, operational, _contracted, _routes = query_run

    def _max_step(graph: nx.MultiDiGraph) -> float:
        worst = 0.0
        for _u, _v, _k, data in graph.edges(data=True, keys=True):
            verts = data["vertices_resampled"]
            for i in range(1, len(verts)):
                worst = max(worst, abs(verts[i][2] - verts[i - 1][2]))
        return worst

    raw_max = _max_step(raw)
    operational_max = _max_step(operational)
    assert operational_max <= raw_max + 1e-9, (
        f"reshaped profile manufactured a spike: max step {operational_max:.3f} m "
        f"exceeds raw-DEM max {raw_max:.3f} m"
    )
