# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: same osmnx / networkx boundary as tests/integration/test_grasp_reproducible.py;
# `reportImplicitRelativeImport` — `from conftest import ...` is the shape that resolves
# under pytest's prepend import mode (see test_oracle_correctness.py for the rationale).
"""Time-budget termination (Story 7.2, Architecture §Cat 5e).

GRASP must stop once `--time-budget` wall-clock is exhausted (checked between
iterations) and tag the run `budget-exhausted`. This runs the solver on the real
Grenoble fixture with `--time-budget 1` and an `iter_budget` so large it could
never be reached in a second (~0.7 ms/iter ⇒ ~1400 iters/s, so a million-iter
budget would take ~12 minutes uncapped). Stagnation is disabled so the time
budget is unambiguously the terminator.

The contracted graph comes from the shared session-scoped `grenoble_fixture`
(tests/integration/conftest.py) — the setup chain isn't under test here, only
the termination clock.
"""

from __future__ import annotations

import time

import numpy as np
from conftest import (
    GRENOBLE_DIFFICULTY_CAP,
    GRENOBLE_J_MAX,
    GRENOBLE_L_CONNECTOR,
    GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
    GRENOBLE_SEED,
    GRENOBLE_THETA,
    GrenobleFixture,
)

from steeproute.models import SolverParams
from steeproute.progress import ProgressEvent
from steeproute.solver.grasp import GraspSolver

_N = 3
# Far more iterations than 1 second can ever consume on this fixture, so reaching
# iter-budget would mean the time-budget check failed.
_HUGE_ITER_BUDGET = 1_000_000
_TIME_BUDGET_S = 1.0
# Generous upper bound: proves time-budget bound the run (~1 s) versus the
# ~12-minute uncapped iter-budget, without flaking on a loaded CI box.
_WALL_CLOCK_CEILING_S = 15.0


def _params() -> SolverParams:
    return SolverParams(
        theta=GRENOBLE_THETA,
        min_climb_slope=GRENOBLE_THETA,
        difficulty_cap=GRENOBLE_DIFFICULTY_CAP,
        l_connector=GRENOBLE_L_CONNECTOR,
        min_climb_ground_length=GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=GRENOBLE_J_MAX,
        n=_N,
        area_cap=500.0,
        untagged_policy="include",
        seed=GRENOBLE_SEED,
        iter_budget=_HUGE_ITER_BUDGET,
        time_budget=_TIME_BUDGET_S,
        stagnation_iters=0,  # isolate the time budget
    )


def test_time_budget_terminates_the_solver(grenoble_fixture: GrenobleFixture) -> None:
    """`--time-budget 1` stops a million-iter run in ~1 s with `budget-exhausted`."""
    events: list[ProgressEvent] = []

    started = time.monotonic()
    solver = GraspSolver(
        grenoble_fixture.contracted,
        _params(),
        np.random.default_rng(GRENOBLE_SEED),
        progress_callback=events.append,
    )
    solver.run()
    elapsed = time.monotonic() - started

    assert solver.convergence_status == "budget-exhausted"
    # Bound the wall-clock so a broken check (running to iter-budget) fails loudly.
    assert elapsed < _WALL_CLOCK_CEILING_S, f"solve took {elapsed:.1f}s; time-budget did not bind"
    # The time budget — not the iteration budget — ended the run.
    assert 0 < len(events) < _HUGE_ITER_BUDGET
