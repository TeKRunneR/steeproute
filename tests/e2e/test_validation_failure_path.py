"""Validation-failure path end-to-end (Story 3.11 AC #5 / FR27, FR28, FR30 code 1).

`monkeypatch`es GRASP to emit a deliberately-invalid route (an edge absent from
the operational graph → `graph_membership` violation) and asserts the §Cat 6c
contract: the process exits 1, the report is *still* written to disk, and its
HTML carries the `VALIDATION FAILED` banner. Failed routes are produced and
flagged, never suppressed (FR28).
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable

import pytest
from click.testing import Result

from steeproute.models import Edge, Solution

# An edge whose endpoints can't exist in any real contracted graph (negative
# synthetic node ids) → the validator's `graph_membership` check fails → the
# route's `RouteValidation.passed` is False. sac_scale=None keeps the difficulty
# cap satisfied, so `graph_membership` is the sole, deterministic violation.
_BOGUS_EDGE = Edge(
    node_u=-999,
    node_v=-998,
    key=0,
    length_m=100.0,
    d_plus_m=50.0,
    d_minus_m=0.0,
    avg_gradient=0.5,
    sac_scale=None,
)


def test_invalid_route_exits_1_writes_report_with_banner(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Replace GRASP's output with a single invalid route. Patching `run` on the
    # class hits the instance the CLI builds (it imports the same class object).
    def _fake_run(_self: object) -> list[Solution]:
        return [Solution(edges=(_BOGUS_EDGE,), objective=50.0)]

    monkeypatch.setattr("steeproute.solver.grasp.GraspSolver.run", _fake_run)

    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42)

    # FR30 code 1: validation failure → non-zero exit.
    assert result.exit_code == 1, result.output

    # FR28: the failed route is still written to disk.
    html_path = output_dir / "route-1.html"
    assert html_path.exists(), "failed route must still be written (FR28)"

    # FR27: the report shows the validation-failure banner.
    html_text = html_path.read_text(encoding="utf-8")
    assert "VALIDATION FAILED" in html_text
