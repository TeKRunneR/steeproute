"""Progress primitives: query-side solver events and setup-side stage timing.

Architecture §Cat 8 splits the output streams: progress lines and the final run
summary go to **stdout** via plain `print(...)`; `logging` is reserved for
diagnostics and warnings on **stderr**. Two consumers share this module:

- **Query side (Story 7.1, FR13):** the solver (`solver/grasp.py`) emits a
  `ProgressEvent` once per iteration through an injected callback; the CLI
  installs a rendering callback wrapped by `throttle(...)` so a long run prints
  at most one line per `--progress-interval` seconds.
- **Setup side (Story 11.1, FR33):** `StageProgress` is the stage-timing seam —
  `cli/setup.py` creates one (rendering through `print`, or silent under
  `--quiet`) and threads it through the setup orchestrator so every pipeline
  stage announces itself, reports elapsed time, and records machine-readable
  per-stage timings for profiling attribution (Story 11.2).

Tests inject a collecting callback (and a fake clock) instead. Both mechanisms
are pure side-effects on the loops they observe: they read a monotonic
wall-clock for display/attribution only, and never feed the solver's RNG or
alter control flow. FR29 byte-identical edge-sets are therefore unaffected by
whether, or how often, progress fires.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field

__all__ = [
    "ProgressCallback",
    "ProgressEvent",
    "StageProgress",
    "estimate_remaining",
    "throttle",
]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """A single progress snapshot emitted by the solver (Architecture §Cat 8 shape).

    - `iteration`: 1-based GRASP iteration index at emit time.
    - `elapsed_s`: wall-clock seconds since the solve started (monotonic).
    - `best_objective`: `D+ + D−` summed across the current top-N
      (`TopNTracker.total_objective()`). NOT monotonic — the tracker's
      overlap-eviction branch can shrink the held set (FR12 graceful
      degradation), so this value may step down as well as up.
    - `estimated_remaining_s`: rough ETA against the iteration budget, or `None`
      when no rate is yet measurable (no elapsed time recorded).
    - `stagnation_counter`: consecutive iterations whose top-N total objective
      was unchanged. Resets to 0 on any change. Story 7.2 turns this into an
      early-termination signal; here it is reported only.
    """

    iteration: int
    elapsed_s: float
    best_objective: float
    estimated_remaining_s: float | None
    stagnation_counter: int


ProgressCallback = Callable[[ProgressEvent], None]
"""Signature of a progress sink: consumes a `ProgressEvent`, returns nothing."""


def estimate_remaining(iteration: int, iter_budget: int, elapsed_s: float) -> float | None:
    """Rough ETA (seconds) against the iteration budget, or `None` if not yet measurable.

    Linear extrapolation: `remaining_iters × (elapsed / completed_iters)`. Returns
    `None` until a rate exists (no completed iteration, or no elapsed time recorded
    yet — e.g. a sub-resolution first iteration or an injected fake clock at t=0),
    matching `ProgressEvent.estimated_remaining_s`'s nullable contract. Clamped at
    `0.0` so the final iteration reports `0.0`, never a negative estimate. This is a
    deliberately crude "rough ETA" (FR13); it ignores the time-budget cap (Story 7.2)
    and assumes a roughly uniform per-iteration cost.
    """
    if iteration <= 0 or elapsed_s <= 0.0:
        return None
    rate = elapsed_s / iteration
    return max(0.0, (iter_budget - iteration) * rate)


@dataclass
class StageProgress:
    """Stage-timing seam for the setup pipeline (Story 11.1, FR33 / T1).

    One object serves both FR33 goals: a `with progress.stage(name):` block
    emits a stage-start line and a stage-complete line with elapsed time through
    `on_line`, and records the elapsed seconds into `timings` (stage name →
    seconds, insertion-ordered) for machine-readable profiling attribution
    (Story 11.2 reads it instead of re-instrumenting).

    - `on_line`: rendering sink for formatted lines. `cli/setup.py` installs
      `print` (stdout, §Cat 8), or `None` under `--quiet` — with `None` the seam
      still times stages but emits nothing (timing-only no-op).
    - `clock`: injectable monotonic clock, testing-only (mirrors `throttle`).
    - `line(text)`: within-stage progress (e.g. the DEM `tile i/N` loop),
      rendered indented beneath the stage lines; silent no-op without a sink.

    A stage body that raises emits no done line and records no timing — the
    exception propagates unchanged so `run_entry_point` reports it on stderr
    while stdout keeps only the stages that actually completed.
    """

    on_line: Callable[[str], None] | None = None
    clock: Callable[[], float] = time.monotonic
    timings: dict[str, float] = field(default_factory=dict)

    @contextlib.contextmanager
    def stage(self, name: str, *, note: str | None = None) -> Generator[None]:
        """Time the enclosed stage, announcing start and completion via `on_line`.

        `note` is an honesty annotation for the start line only (e.g. the
        blocking Overpass download's "typically takes minutes").
        """
        suffix = f" ({note})" if note else ""
        self._emit(f"stage: {name}{suffix} ...")
        started = self.clock()
        yield
        elapsed = self.clock() - started
        self.timings[name] = elapsed
        self._emit(f"stage: {name}: {elapsed:.2f} s")

    def line(self, text: str) -> None:
        """Emit a within-stage progress line, indented under the stage lines."""
        self._emit(f"  {text}")

    def _emit(self, text: str) -> None:
        if self.on_line is not None:
            self.on_line(text)


def throttle(
    render: ProgressCallback,
    interval_s: float,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> ProgressCallback:
    """Wrap `render` so it fires at most once per `interval_s` seconds.

    Returns a new callback to install as the solver's `progress_callback`. The
    first forwarded call happens only after one full `interval_s` has elapsed
    from creation (no fire at iteration 0); thereafter each forwarded call is
    spaced at least `interval_s` from the previous one, measured against `clock`.
    Calls arriving inside a closed interval are dropped silently.

    `clock` is injectable purely for deterministic testing — production uses the
    default `time.monotonic`. A non-positive `interval_s` degenerates to "forward
    every call" (the first fire still waits for `now >= start`, i.e. immediately).

    Spacing is measured from the actual fire time (`next_fire = now + interval_s`),
    not from a fixed grid, so a slow iteration cannot trigger a catch-up burst of
    back-to-back lines on the next fast iterations.
    """
    start = clock()
    next_fire = start + interval_s

    def _throttled(event: ProgressEvent) -> None:
        nonlocal next_fire
        now = clock()
        if now >= next_fire:
            render(event)
            next_fire = now + interval_s

    return _throttled
