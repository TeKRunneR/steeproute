# pyright: reportUnknownMemberType=false
# Reason: `pytest.approx` is typed as returning a partially-unknown `ApproxBase`.
"""Unit tests for `progress.py` — `ProgressEvent`, `throttle`, ETA, and `StageProgress`.

The throttle and the stage seam are exercised with an injected fake clock so
their behaviour is asserted deterministically (no wall-clock flake) — the same
dependency-injection pattern `cli/_shared.emit_osm_age_warning(now=...)` uses.
Real-wall-clock behaviour is covered end-to-end by
`tests/integration/test_progress.py` and the e2e tests.
"""

from __future__ import annotations

import dataclasses

import pytest

from steeproute.progress import ProgressEvent, StageProgress, estimate_remaining, throttle


class _FakeClock:
    """A manually-advanced monotonic clock. Set `.t` then call as `clock()`."""

    def __init__(self, t0: float = 0.0) -> None:
        self.t: float = t0

    def __call__(self) -> float:
        return self.t


def _event(iteration: int = 1) -> ProgressEvent:
    return ProgressEvent(
        iteration=iteration,
        elapsed_s=0.0,
        best_objective=0.0,
        estimated_remaining_s=None,
        stagnation_counter=0,
    )


# --- ProgressEvent -----------------------------------------------------------


def test_progress_event_fields_round_trip() -> None:
    e = ProgressEvent(
        iteration=7,
        elapsed_s=1.5,
        best_objective=1234.5,
        estimated_remaining_s=3.0,
        stagnation_counter=4,
    )
    assert (e.iteration, e.elapsed_s, e.best_objective, e.stagnation_counter) == (7, 1.5, 1234.5, 4)
    assert e.estimated_remaining_s == 3.0


def test_progress_event_allows_none_eta() -> None:
    assert _event().estimated_remaining_s is None


def test_progress_event_is_frozen() -> None:
    """`frozen=True, slots=True` discipline (Architecture conventions / Story 3.1)."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        _event().iteration = 2  # pyright: ignore[reportAttributeAccessIssue]


# --- throttle ----------------------------------------------------------------


def test_throttle_does_not_fire_before_interval_elapses() -> None:
    """No fire at iteration 0 / before one full interval has passed from start."""
    clock = _FakeClock(0.0)
    fired: list[ProgressEvent] = []
    throttled = throttle(fired.append, 1.0, clock=clock)
    for t in (0.0, 0.25, 0.5, 0.99):
        clock.t = t
        throttled(_event())
    assert fired == []


def test_throttle_fires_once_interval_has_elapsed() -> None:
    clock = _FakeClock(0.0)
    fired: list[ProgressEvent] = []
    throttled = throttle(fired.append, 1.0, clock=clock)
    clock.t = 1.0
    throttled(_event(iteration=5))
    assert len(fired) == 1
    assert fired[0].iteration == 5


def test_throttle_spaces_fires_by_at_least_the_interval() -> None:
    """Calls every 0.4s with interval 1.0 → fires at 1.2, 2.4, 3.6 (spacing >= 1.0)."""
    clock = _FakeClock(0.0)
    fired: list[float] = []
    throttled = throttle(lambda _e: fired.append(clock.t), 1.0, clock=clock)
    for step in range(10):
        clock.t = step * 0.4
        throttled(_event())
    assert fired == pytest.approx([1.2, 2.4, 3.6])
    for prev, cur in zip(fired, fired[1:], strict=False):
        assert cur - prev >= 1.0


def test_throttle_measures_spacing_from_actual_fire_not_a_fixed_grid() -> None:
    """A long gap does not trigger a catch-up burst: next fire is interval-after-fire."""
    clock = _FakeClock(0.0)
    fired: list[float] = []
    throttled = throttle(lambda _e: fired.append(clock.t), 1.0, clock=clock)
    # First eligible fire at t=5.0 (well past one interval); the next must wait
    # until t >= 6.0, NOT fire repeatedly to "catch up" the 5 missed intervals.
    clock.t = 5.0
    throttled(_event())
    clock.t = 5.5
    throttled(_event())
    assert fired == [5.0]


def test_throttle_nonpositive_interval_fires_every_call() -> None:
    clock = _FakeClock(0.0)
    fired: list[ProgressEvent] = []
    throttled = throttle(fired.append, 0.0, clock=clock)
    for _ in range(3):
        throttled(_event())
    assert len(fired) == 3


# --- StageProgress (Story 11.1, FR33) -----------------------------------------


def test_stage_emits_start_and_done_lines_and_records_timing() -> None:
    """One `with progress.stage(...)` block → start line, done line, timings entry."""
    clock = _FakeClock(10.0)
    lines: list[str] = []
    progress = StageProgress(lines.append, clock=clock)
    with progress.stage("osm-download"):
        clock.t = 12.5
    assert lines == ["stage: osm-download ...", "stage: osm-download: 2.50 s"]
    assert progress.timings == {"osm-download": pytest.approx(2.5)}


def test_stage_note_is_rendered_on_the_start_line_only() -> None:
    """The honest "takes minutes" annotation rides the start line, not the done line."""
    clock = _FakeClock(0.0)
    lines: list[str] = []
    progress = StageProgress(lines.append, clock=clock)
    with progress.stage("osm-download", note="one Overpass request; typically takes minutes"):
        clock.t = 1.0
    assert lines[0] == "stage: osm-download (one Overpass request; typically takes minutes) ..."
    assert lines[1] == "stage: osm-download: 1.00 s"


def test_silent_seam_records_timings_without_any_output() -> None:
    """`on_line=None` (the `--quiet` install) still times stages — timing-only no-op."""
    clock = _FakeClock(0.0)
    progress = StageProgress(clock=clock)
    with progress.stage("resampling"):
        clock.t = 0.25
    progress.line("tile 1/4")  # must be a silent no-op, not an error
    assert progress.timings == {"resampling": pytest.approx(0.25)}


def test_line_is_indented_under_the_current_stage() -> None:
    """Within-stage progress (`tile i/N`) renders indented beneath the stage lines."""
    lines: list[str] = []
    progress = StageProgress(lines.append, clock=_FakeClock(0.0))
    with progress.stage("dem-resolve"):
        progress.line("tile 1/4")
        progress.line("tile 2/4")
    assert lines[1:3] == ["  tile 1/4", "  tile 2/4"]


def test_stage_exception_propagates_without_done_line_or_timing() -> None:
    """A failing stage emits no done line and records no timing; the error propagates
    unchanged to `run_entry_point`'s stderr path (stdout stays clean)."""
    lines: list[str] = []
    progress = StageProgress(lines.append, clock=_FakeClock(0.0))
    with pytest.raises(RuntimeError, match="boom"):
        with progress.stage("elevation-sampling"):
            raise RuntimeError("boom")
    assert lines == ["stage: elevation-sampling ..."]
    assert progress.timings == {}


def test_multiple_stages_accumulate_timings_in_execution_order() -> None:
    """The timings dict keeps insertion order — Story 11.2 reads it as the timeline."""
    clock = _FakeClock(0.0)
    progress = StageProgress(clock=clock)
    with progress.stage("osm-download"):
        clock.t = 2.0
    with progress.stage("trail-filter"):
        clock.t = 2.5
    assert list(progress.timings) == ["osm-download", "trail-filter"]
    assert progress.timings["trail-filter"] == pytest.approx(0.5)


# --- estimate_remaining (ETA) -----------------------------------------------


def test_estimate_remaining_none_until_a_rate_is_measurable() -> None:
    assert estimate_remaining(0, 100, 5.0) is None  # no completed iteration
    assert estimate_remaining(10, 100, 0.0) is None  # no elapsed time yet


def test_estimate_remaining_linear_extrapolation() -> None:
    # 10 of 100 iterations in 5s → rate 0.5 s/iter → 90 * 0.5 = 45s remaining.
    assert estimate_remaining(10, 100, 5.0) == pytest.approx(45.0)


def test_estimate_remaining_zero_on_final_iteration() -> None:
    assert estimate_remaining(100, 100, 5.0) == 0.0
