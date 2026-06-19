"""End-of-run summary on stdout (Story 7.5 / FR22).

Every successful `steeproute` invocation prints a labeled run-summary block to
stdout after rendering — parameters, routes returned vs. N requested,
validation-failure count, convergence status, an optional graceful-degradation
explanation, and wall-clock total. The block is emitted regardless of `--quiet`
(Architecture §Cat 8: only intermediate progress is suppressible; the final
summary is always stdout). The `--- Run summary ---` delimiter line lets
downstream scripts split stdout on it.

All tests run in-process via `CliRunner` (the shared `run_query` fixture) — no OS
process is needed. `--theta 0.50` reuses `test_degradation.py`'s regime to induce
graceful degradation (< N=5 routes clear that floor) on the committed fixture for
the degraded-path test — see that module for why theta, not j-max, is the binding
lever after the Epic 9 route-discovery fixes.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Callable

import pytest
from click.testing import Result

from steeproute.models import Edge, Solution

_SUMMARY_DELIM = "--- Run summary ---"

# `--theta 0.50` makes feasibility the binding constraint on the committed fixture:
# only a few routes clear that floor, so the default --j-max 0.30 returns < N=5
# (same regime as test_degradation.py — see its docstring for why theta, not j-max).
_DEGRADE_THETA = ["--theta", "0.50"]

# An edge whose endpoints can't exist in any real contracted graph (negative
# synthetic node ids) → the validator's `graph_membership` check fails, so the
# route's `RouteValidation.passed` is False. Mirrors test_validation_failure_path.py.
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


def test_happy_path(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """A successful run prints every labeled summary line with matching values."""
    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42)
    assert result.exit_code == 0, result.output

    out = result.output
    assert _SUMMARY_DELIM in out

    # routes_returned: X/N — X matches the emitted report count.
    routes_m = re.search(r"routes_returned:\s*(\d+)/(\d+)", out)
    assert routes_m is not None, out
    returned, requested = int(routes_m.group(1)), int(routes_m.group(2))
    assert returned == len(list(output_dir.glob("route-*.html")))

    # parameters line: labels stable, values match the invocation (seed 42, n=requested).
    params_m = re.search(
        r"parameters:\s*theta=(\S+)\s+j_max=(\S+)\s+n=(\S+)\s+seed=(\S+)\s+"
        r"iter_budget=(\S+)\s+time_budget=(\S+)\s+stagnation_iters=(\S+)",
        out,
    )
    assert params_m is not None, out
    assert params_m.group(3) == str(requested)  # n
    assert params_m.group(4) == "42"  # seed

    assert re.search(r"validation_failures:\s*0", out), out
    assert re.search(r"convergence_status:\s*(converged|budget-exhausted|interrupted)", out), out
    assert re.search(r"wall_clock_total:\s*[\d.]+s", out), out

    # A full-N run carries no degradation line.
    assert "degradation:" not in out


def test_degraded_path(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """When fewer than N routes are returned, the summary carries the degradation line."""
    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42, extra_args=_DEGRADE_THETA)
    assert result.exit_code == 0, result.output

    out = result.output
    routes_m = re.search(r"routes_returned:\s*(\d+)/(\d+)", out)
    assert routes_m is not None, out
    returned, requested = int(routes_m.group(1)), int(routes_m.group(2))
    assert returned < requested, f"expected a degraded set (<N), got {out}"

    # The degradation explanation (Story 7.4) now lives in the summary's field.
    deg_m = re.search(r"degradation:\s*Only (\d+) of \d+ requested routes satisfy the current", out)
    assert deg_m is not None, out
    assert int(deg_m.group(1)) == returned


def test_validation_failure_path(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing route makes `validation_failures:` non-zero; the summary still prints."""

    def _fake_run(_self: object) -> list[Solution]:
        return [Solution(edges=(_BOGUS_EDGE,), objective=50.0)]

    monkeypatch.setattr("steeproute.solver.grasp.GraspSolver.run", _fake_run)

    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42)

    # Validation failure → exit 1, but the summary is emitted before the exit-code call.
    assert result.exit_code == 1, result.output
    assert _SUMMARY_DELIM in result.output
    failures_m = re.search(r"validation_failures:\s*(\d+)", result.output)
    assert failures_m is not None, result.output
    assert int(failures_m.group(1)) >= 1


def test_quiet_preserves_summary(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """`--quiet` suppresses progress lines but never the final summary (§Cat 8)."""
    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42, extra_args=["--quiet"])
    assert result.exit_code == 0, result.output

    out = result.output
    assert "progress:" not in out, f"--quiet must suppress progress lines:\n{out}"
    assert _SUMMARY_DELIM in out
    assert re.search(r"routes_returned:\s*\d+/\d+", out), out
