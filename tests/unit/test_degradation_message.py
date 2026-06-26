"""Unit tests for `cli/query.py::_degradation_message` (FR12).

`_degradation_message` reads only `len(validated.routes)` and the params, so the
tests build a `ValidatedRouteSet` with a controllable route count and assert the
returned explanation. Story 10.1 adds the `--start-at-junction` lever wording.
"""

from __future__ import annotations

from steeproute.cli.query import _degradation_message
from steeproute.models import SolverParams, ValidatedRouteSet


def _params(*, n: int = 5, start_at_junction: bool = False) -> SolverParams:
    return SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=n,
        area_cap=500.0,
        untagged_policy="include",
        seed=42,
        iter_budget=100,
        time_budget=60.0,
        stagnation_iters=0,
        start_at_junction=start_at_junction,
    )


def _route_set(returned: int) -> ValidatedRouteSet:
    """A `ValidatedRouteSet` whose `len(routes)` is `returned` (contents irrelevant here)."""
    # `_degradation_message` reads only the count; `None` placeholders keep it cheap
    # and avoid building real `Route`s the function never inspects.
    return ValidatedRouteSet(routes=[None] * returned, set_violations=[])  # type: ignore[list-item]


def test_full_set_returns_no_message() -> None:
    """A full N-route result is not degraded → returns None."""
    assert _degradation_message(_route_set(5), _params(n=5)) is None


def test_degraded_message_flag_off_names_theta_and_jmax_only() -> None:
    """Flag off: message names --theta / --j-max and NOT start-at-junction (unchanged wording)."""
    msg = _degradation_message(_route_set(2), _params(n=5))
    assert msg is not None
    assert msg == (
        "Only 2 of 5 requested routes satisfy the current constraints "
        "(theta=0.20, J_max <= 0.30); relax --theta or --j-max to admit more."
    )


def test_degraded_message_flag_on_names_start_at_junction_lever() -> None:
    """Flag on: the message surfaces --start-at-junction as both a cause and a lever."""
    msg = _degradation_message(_route_set(2), _params(n=5, start_at_junction=True))
    assert msg is not None
    assert "start-at-junction" in msg
    assert "drop --start-at-junction" in msg
