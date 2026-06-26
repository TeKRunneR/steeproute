"""Flag-on golden + no-over-cap-descent property for `--max-descent-slope` (Story 10.2, FR32).

Runs the real `steeproute` query against the committed `grenoble_small` cache with
`--max-descent-slope` on (via the `grenoble_small_descent` flag-on fixture) and asserts:

1. **Property** — no returned route descends a segment steeper than the cap. Proven
   through the validator: under the cap, `validate` flags any over-cap descending
   traversal with a `max_descent_slope` violation (unit-tested in
   `tests/unit/test_validator.py`), so "no route carries that violation" means no
   returned route descends an over-cap segment.
2. **Regression** — the deterministic route set matches its committed golden.

The fixture is deliberately NOT in `regression.FIXTURES` (the zero-tolerance CI
gate) yet — folding it in alongside the realistic tier is Story 8.5's job. The
existing default-param goldens are untouched (no rebake); that non-regression proof
is `tests/e2e/test_pinned_regressions.py`.
"""

from __future__ import annotations

from steeproute import regression

_DESCENT_FIXTURE = next(
    f for f in regression.FLAG_ON_FIXTURES if f.name == "grenoble_small_descent"
)


def test_flag_on_no_route_descends_over_cap_segment() -> None:
    """No returned route descends an over-cap segment (no `max_descent_slope` violation)."""
    sidecars = regression.run_fixture(_DESCENT_FIXTURE)

    assert sidecars, "the flag-on run must return at least one route"
    for sidecar in sidecars:
        violation_ids = {v["constraint_id"] for v in sidecar["validation"]["violations"]}
        assert "max_descent_slope" not in violation_ids, (
            f"route {sidecar['route_index']} descends an over-cap segment: "
            f"{sidecar['validation']['violations']}"
        )


def test_flag_on_run_matches_committed_golden() -> None:
    """The `--max-descent-slope` run is deterministic and pinned by its committed golden."""
    golden = regression.read_golden(_DESCENT_FIXTURE)
    assert golden is not None, (
        "no committed golden for grenoble_small_descent - run "
        "`uv run update-regression --fixture grenoble_small_descent`"
    )

    actual = regression.build_golden(_DESCENT_FIXTURE, regression.run_fixture(_DESCENT_FIXTURE))

    assert actual == golden, (
        "flag-on golden drift for grenoble_small_descent. If intentional, run "
        "`uv run update-regression --fixture grenoble_small_descent` and commit "
        "the new golden WITH an explicit rationale."
    )
