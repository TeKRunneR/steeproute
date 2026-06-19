"""Graceful degradation for sparse areas end-to-end (Story 7.4 / FR12).

When fewer than N routes satisfy the current constraints, `steeproute` returns
the feasible subset with a clear explanation rather than silently loosening them
(Architecture §"What's not an exception"). Degradation is a normal outcome: exit
code stays 0.

Both tests run in-process via `CliRunner` (the shared `run_query` fixture) — no
OS process is needed, unlike the interrupt e2e test. The degradation regime is
induced with a steep `--theta 0.50` route-level floor: at that floor only a few
routes clear feasibility, so the run returns fewer than the default `--n 5`, and
relaxing `--theta` admits more — exercising Journey 2's "tighten, review, relax"
tuning loop. Counts are asserted as inequalities (not exact values) so the tests
track the binding behavior, not a single GRASP tipping-point number.

Why `--theta`, not `--j-max` (Story 9.3): the original regime induced degradation
with `--theta 0.35` and relaxed `--j-max` to admit more, exercising the *distinctness*
constraint. The Epic 9 route-discovery fixes (climb maximality #7, θ-prefix recovery
#10) made the solver find a far richer, near-disjoint route set on this fixture — it
returns a full N=5 even at `--j-max 0.02`, so distinctness no longer binds at any
feasible θ. Degradation here is now feasibility-bound, so the binding lever the
tuning loop relaxes is `--theta`. (Distinctness/Jaccard logic stays covered by the
`TopNTracker` unit tests and the `relax_j_max` metamorphic invariant.)
"""

from __future__ import annotations

import html
import json
import pathlib
import re
from collections.abc import Callable

from click.testing import Result

# `--theta 0.50` makes feasibility the binding constraint on the committed fixture:
# only a few routes clear that floor, so the run returns < N=5; relaxing theta to
# 0.20 admits the full set (see module docstring for why theta, not j-max).
_DEGRADE_THETA = ["--theta", "0.50"]
_RELAXED_THETA = ["--theta", "0.20"]

# Mirrors the message built in `cli/query.py::_degradation_message` (plain ASCII,
# so it survives a redirected stdout on any platform without stream reconfiguring).
# The message names both tuning levers (theta/j-max) rather than asserting a cause —
# here the regime is theta-bound (see module docstring), but the wording is generic.
_DEGRADATION_PATTERN = re.compile(
    r"Only (\d+) of \d+ requested routes satisfy the current constraints "
    r"\(theta=[\d.]+, J_max <= [\d.]+\); relax --theta or --j-max to admit more\."
)


def test_degradation_returns_fewer_than_n_with_explanation(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42, extra_args=_DEGRADE_THETA)

    # Graceful degradation is a normal outcome, not an error (AC #5).
    assert result.exit_code == 0, result.output

    html_files = sorted(output_dir.glob("route-*.html"))
    returned = len(html_files)
    assert 0 < returned < 5, f"expected a degraded set (<N=5), got {returned}"

    # The explanation appears on stdout (AC #3), naming the observed count, the N
    # requested, and the theta / J_max constraints in force.
    match = _DEGRADATION_PATTERN.search(result.output)
    assert match is not None, f"degradation line not found in stdout:\n{result.output}"
    assert int(match.group(1)) == returned, "message count must match emitted report count"

    # The explanation also rides in every report's metadata so a reader of a
    # single report sees it was part of a degraded set (AC #4).
    # HTML autoescapes the `<` in `<=` to `&lt;=`; the JSON sidecar is raw.
    escaped = html.escape(match.group(0))
    for i in range(1, returned + 1):
        html_text = (output_dir / f"route-{i}.html").read_text(encoding="utf-8")
        assert "<th>degradation</th>" in html_text
        assert escaped in html_text
        payload = json.loads((output_dir / f"route-{i}.json").read_text(encoding="utf-8"))
        assert payload["metadata"]["degradation"] == match.group(0)


def test_relaxed_theta_produces_more_routes(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """Re-querying the same cache with a looser --theta admits more routes (Journey 2).

    Theta is the binding lever on the post-Epic-9 fixture (see module docstring):
    the steep `--theta 0.50` floor degrades below N=5, and relaxing to `--theta 0.20`
    admits the full set — the same tighten→review→relax loop, on the constraint that
    actually limits the result here.
    """
    tight_dir = tmp_path / "tight"
    relaxed_dir = tmp_path / "relaxed"

    tight = run_query(seeded_cache, tight_dir, seed=42, extra_args=_DEGRADE_THETA)
    relaxed = run_query(seeded_cache, relaxed_dir, seed=42, extra_args=_RELAXED_THETA)

    assert tight.exit_code == 0, tight.output
    assert relaxed.exit_code == 0, relaxed.output

    tight_n = len(list(tight_dir.glob("route-*.html")))
    relaxed_n = len(list(relaxed_dir.glob("route-*.html")))
    assert relaxed_n > tight_n, (
        f"relaxing theta should admit more routes: tight={tight_n} relaxed={relaxed_n}"
    )

    # The re-run hit the prepared cache (no re-preprocessing) — Journey 2's fast
    # tuning loop. The cache-hit cue is printed on every successful query.
    assert "cache-hit cache_key_hash:" in relaxed.output

    # Relaxed enough to return the full N: no degradation line this time.
    assert "relax --theta or --j-max to admit more" not in relaxed.output
