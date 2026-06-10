"""Pinned-regression goldens (Story 8.1 AC #3/#4/#8 / Architecture §Cat 11c/11d).

Runs `steeproute` on each registered fixture's committed cache at its explicitly-pinned
params + seed (no mocking of the solver or output layers), derives the 5-field hash
tuple per route from the real `route-*.json` sidecars, and asserts an exact match
against the committed golden. Seeded GRASP is deterministic, so any drift is a behavior
change worth noticing — zero tolerance.

Story 8.1 ships one proof fixture (`grenoble_small`); Story 8.2 adds the 2-3 Grenoble
cutouts to `regression.FIXTURES` and wires the zero-tolerance CI gate. `pytest.skip` /
`xfail` on these tests is not a sanctioned workaround (Architecture §Cat 11c).
"""

from __future__ import annotations

import pytest

from steeproute import regression


@pytest.mark.parametrize("fixture", regression.FIXTURES, ids=lambda f: f.name)
def test_pinned_regression_matches_golden(fixture: regression.Fixture) -> None:
    golden = regression.read_golden(fixture)
    assert golden is not None, (
        f"no committed golden for {fixture.name!r} - "
        f"run `uv run update-regression --fixture {fixture.name}`"
    )

    actual = regression.build_golden(fixture, regression.run_fixture(fixture))

    assert actual == golden, (
        f"regression: {fixture.name!r} output drifted from its golden. If this is an "
        f"intentional behavior change, run `uv run update-regression --fixture "
        f"{fixture.name}` and commit the new golden WITH an explicit rationale."
    )
