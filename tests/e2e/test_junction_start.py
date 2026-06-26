"""Flag-on golden + junction-start property assertion for `--start-at-junction` (Story 10.1, FR31).

Runs the real `steeproute` query against the committed `grenoble_small` cache with
`--start-at-junction` on (via the `grenoble_small_junction` flag-on fixture) and asserts:

1. **Property** — every returned route's start endpoint is a road/trail junction.
   Proven through the validator: under the flag, `validate` flags any route whose
   start endpoint isn't a junction with a `start_at_junction` violation (unit-tested
   in `tests/unit/test_validator.py`), so "no route carries that violation" means
   every returned route starts at a junction.
2. **Regression** — the deterministic route set matches its committed golden.

The fixture is deliberately NOT in `regression.FIXTURES` (the zero-tolerance CI
gate) yet — folding it in alongside the realistic tier is Story 8.5's job. The
existing default-param goldens are untouched (no rebake); that non-regression
proof is `tests/e2e/test_pinned_regressions.py`.
"""

from __future__ import annotations

from steeproute import regression

_JUNCTION_FIXTURE = regression.FLAG_ON_FIXTURES[0]


def test_flag_on_routes_all_start_at_a_junction() -> None:
    """Every returned route starts at a road/trail junction (no `start_at_junction` violation)."""
    sidecars = regression.run_fixture(_JUNCTION_FIXTURE)

    assert sidecars, "the flag-on run must return at least one route"
    for sidecar in sidecars:
        violation_ids = {v["constraint_id"] for v in sidecar["validation"]["violations"]}
        assert "start_at_junction" not in violation_ids, (
            f"route {sidecar['route_index']} does not start at a junction: "
            f"{sidecar['validation']['violations']}"
        )


def test_flag_on_run_matches_committed_golden() -> None:
    """The `--start-at-junction` run is deterministic and pinned by its committed golden."""
    golden = regression.read_golden(_JUNCTION_FIXTURE)
    assert golden is not None, (
        "no committed golden for grenoble_small_junction - run "
        "`uv run update-regression --fixture grenoble_small_junction`"
    )

    actual = regression.build_golden(_JUNCTION_FIXTURE, regression.run_fixture(_JUNCTION_FIXTURE))

    assert actual == golden, (
        "flag-on golden drift for grenoble_small_junction. If intentional, run "
        "`uv run update-regression --fixture grenoble_small_junction` and commit "
        "the new golden WITH an explicit rationale."
    )
