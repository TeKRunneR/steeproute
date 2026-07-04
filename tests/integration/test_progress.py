# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx / networkx boundary as tests/integration/test_grasp_on_fixture.py.
"""Progress-emission integration test on the real Grenoble fixture (Story 7.1 AC #7).

Runs the setup → climbs → contract → `GraspSolver.run()` chain against the
committed fixture with a progress callback installed, and asserts the contract a
`ProgressEvent` consumer relies on:

- **Field population**: every event carries a 1-based iteration, a non-negative
  monotonic `elapsed_s`, a finite `best_objective`, an ETA that is `None` or
  non-negative, and a non-negative `stagnation_counter`.
- **Stagnation semantics**: `stagnation_counter` is exactly the count of
  consecutive iterations whose top-N total objective was unchanged — it resets
  to 0 on any change and increments otherwise (reconstructed independently from
  the `best_objective` stream and asserted equal).
- **Throttling**: a `throttle(...)`-wrapped callback fires fewer times than the
  raw per-iteration stream, spaced by at least `--progress-interval` (measured
  via each event's own `elapsed_s`).

Progress is a pure reporting side-effect, so the solver still runs to its
iter-budget identically; FR29 determinism is covered by the seeded-repro tests.
"""

from __future__ import annotations

import importlib.util
import math
import pathlib
from unittest.mock import patch

import networkx as nx
import numpy as np
import osmnx
import pytest

from steeproute.models import Area, ContractedGraph, PipelineConfig, SolverParams
from steeproute.pipeline import operationalize_graph, run_setup_stages
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import normalize_edges
from steeproute.progress import ProgressEvent, throttle
from steeproute.solver.grasp import GraspSolver

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

_THETA = 0.20
_DIFFICULTY_CAP = "T3"
_L_CONNECTOR = 200.0
_MIN_CLIMB_GROUND_LENGTH_M = 300.0
_J_MAX = 0.30
_N = 5
_SEED = 42
# Enough iterations that the tracker fills early and later iterations stagnate
# (so the counter both resets and climbs), while the solve stays well under a
# second on this small fixture. Per-iter ~0.02 ms after the Epic 12 solver
# optimizations (was ~0.7 ms when this suite was written — the budget and the
# interval below were rescaled in Story 12.3 so the solve still spans several
# throttle intervals).
_ITER_BUDGET = 5000
# Small relative to the solve duration (~0.1 s at the measured per-iter cost) so
# the throttled stream reliably yields several spaced fires regardless of host
# speed.
_PROGRESS_INTERVAL_S = 0.01


def _load_fixture_constants() -> tuple[float, float, int]:
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen_progress", regen_path)
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
def contracted_graph() -> ContractedGraph:
    """Setup → climbs → contract on the committed fixture (shared across tests)."""

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        base_graph = operationalize_graph(run_setup_stages(area, config))
    climbs = detect_climbs(
        base_graph, min_climb_slope=_THETA, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M
    )
    assert climbs, "expected >= 1 climb on the Grenoble Le Sappey fixture"
    return contract_climbs(base_graph, climbs, l_connector=_L_CONNECTOR)


@pytest.fixture(scope="module")
def raw_events(contracted_graph: ContractedGraph) -> list[ProgressEvent]:
    """Every per-iteration event (unthrottled collector), captured once."""
    events: list[ProgressEvent] = []
    solver = GraspSolver(
        contracted_graph, _params(), np.random.default_rng(_SEED), progress_callback=events.append
    )
    solver.run()
    assert events, "expected the solver to emit at least one progress event"
    return events


def test_one_event_emitted_per_iteration(raw_events: list[ProgressEvent]) -> None:
    """The raw stream fires exactly once per GRASP iteration, 1-based and contiguous."""
    assert [e.iteration for e in raw_events] == list(range(1, _ITER_BUDGET + 1))


def test_every_event_has_all_fields_populated(raw_events: list[ProgressEvent]) -> None:
    prev_elapsed = -1.0
    for e in raw_events:
        assert e.iteration >= 1
        assert math.isfinite(e.elapsed_s) and e.elapsed_s >= 0.0
        assert e.elapsed_s >= prev_elapsed, "elapsed_s must be monotonic non-decreasing"
        prev_elapsed = e.elapsed_s
        assert math.isfinite(e.best_objective) and e.best_objective >= 0.0
        assert e.estimated_remaining_s is None or e.estimated_remaining_s >= 0.0
        assert e.stagnation_counter >= 0


def test_stagnation_counter_tracks_unchanged_objective(raw_events: list[ProgressEvent]) -> None:
    """`stagnation_counter` == consecutive iterations with an unchanged top-N total.

    Reconstructs the expected counter purely from the `best_objective` stream
    (reset to 0 on any change, else +1) and asserts the solver-reported value
    matches exactly — this both pins the semantics and confirms the counter is
    driven off the tracker total, not the iteration index.
    """
    expected: list[int] = []
    last = 0.0  # pre-loop tracker total is 0.0 (empty tracker)
    counter = 0
    for e in raw_events:
        if e.best_objective != last:
            counter = 0
            last = e.best_objective
        else:
            counter += 1
        expected.append(counter)
    assert [e.stagnation_counter for e in raw_events] == expected
    # Non-vacuity: the run both improves (reset) and stalls (increment).
    assert any(e.stagnation_counter == 0 for e in raw_events)
    assert max(e.stagnation_counter for e in raw_events) > 0


def test_throttled_stream_is_sparser_and_interval_spaced(
    contracted_graph: ContractedGraph,
) -> None:
    """A throttled callback fires fewer times than the raw stream, spaced >= interval."""
    throttled_events: list[ProgressEvent] = []
    callback = throttle(throttled_events.append, _PROGRESS_INTERVAL_S)
    solver = GraspSolver(
        contracted_graph, _params(), np.random.default_rng(_SEED), progress_callback=callback
    )
    solver.run()

    assert len(throttled_events) >= 2, "solve should span several progress intervals"
    assert len(throttled_events) < _ITER_BUDGET, "throttling must drop intra-interval events"
    for prev, cur in zip(throttled_events, throttled_events[1:], strict=False):
        # Spacing is measured via the events' own solver-clock elapsed_s. Allow a
        # small tolerance for the microsecond offset between the solver's
        # monotonic read and the throttle's.
        assert cur.elapsed_s - prev.elapsed_s >= _PROGRESS_INTERVAL_S * 0.9
