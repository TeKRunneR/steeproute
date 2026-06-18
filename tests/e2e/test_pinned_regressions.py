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


def _assert_matches_golden(fixture: regression.Fixture, update_cmd: str) -> None:
    golden = regression.read_golden(fixture)
    assert golden is not None, (
        f"no committed golden for {fixture.name!r} ({fixture.tier} tier) - run `{update_cmd}`"
    )

    actual = regression.build_golden(fixture, regression.run_fixture(fixture))

    assert actual == golden, (
        f"regression: {fixture.name!r} ({fixture.tier} tier) output drifted from its golden. "
        f"If this is an intentional behavior change, run `{update_cmd}` and commit the new "
        f"golden WITH an explicit rationale."
    )


@pytest.mark.parametrize("fixture", regression.FIXTURES, ids=lambda f: f.name)
def test_pinned_regression_matches_golden(fixture: regression.Fixture) -> None:
    _assert_matches_golden(fixture, f"uv run update-regression --fixture {fixture.name}")


@pytest.mark.slow
@pytest.mark.parametrize("fixture", regression.REALISTIC_FIXTURES, ids=lambda f: f.name)
def test_pinned_regression_matches_golden_realistic(fixture: regression.Fixture) -> None:
    """Realistic-budget regression (~200k iters): the regime the tool is actually used in.

    Gated `slow` (deselected by default; run with `uv run pytest -m slow`). The fast
    tier converges in a fraction of a second on an unconverged, low-quality solution
    set; this tier pins the converged output, so a regression in the GRASP hot loop
    that only manifests after thousands of iterations is actually caught.
    """
    _assert_matches_golden(
        fixture, f"uv run update-regression --fixture {fixture.name} --tier realistic"
    )
