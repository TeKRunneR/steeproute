"""ProgressEvent dataclass and throttled-callback helper (Story 7.1, FR13).

Architecture §Cat 8 splits the output streams: progress lines and the final run
summary go to **stdout** via plain `print(...)`; `logging` is reserved for
diagnostics and warnings on **stderr**. The solver (`solver/grasp.py`) emits a
`ProgressEvent` once per iteration through an injected callback; the CLI installs
a rendering callback wrapped by `throttle(...)` so a long run prints at most one
line per `--progress-interval` seconds. Tests inject a collecting callback (and,
for the throttle, a fake clock) instead.

The throttle is a pure side-effect on top of the solver loop: it reads a
monotonic wall-clock to decide *whether* to forward an event, and never feeds the
solver's RNG or alters iteration count. FR29 byte-identical edge-sets are
therefore unaffected by whether, or how often, progress fires.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["ProgressCallback", "ProgressEvent", "estimate_remaining", "throttle"]


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
