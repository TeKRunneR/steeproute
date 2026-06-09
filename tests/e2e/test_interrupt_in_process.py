# pyright: reportPrivateUsage=false
# Reason: these tests intentionally monkeypatch the solver's private
# `_construct_one` seam — the per-iteration construction hook — to inject a
# KeyboardInterrupt at a controlled point. There is no public equivalent.
"""In-process Ctrl-C flow for the query CLI (Story 7.3, FR14 / §Cat 5b).

Monkeypatches a `KeyboardInterrupt` into GRASP construction to drive the query
CLI's interrupt handler deterministically — without a real OS signal — and
asserts the two branches of that handler:

- **partial flush:** once a route has been admitted, the interrupt makes the CLI
  validate + render the best-so-far set tagged ``convergence_status="interrupted"``
  with the convergence iteration, and exit 130;
- **no solution yet:** an interrupt before any admission writes nothing, warns on
  stderr, and exits 130.

The real-signal counterpart (a genuine OS interrupt to a subprocess) lives in
`test_interrupt.py`. Both share the offline `seeded_cache` / `run_query` fixtures
(`tests/e2e/conftest.py`), which is why this in-process test sits in the e2e layer
rather than `tests/integration/` despite being monkeypatch-driven.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

import pytest
from click.testing import Result

from steeproute.solver.grasp import GraspSolver


def test_interrupt_after_admission_flushes_best_so_far(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C after >=1 admission → reports written, tagged interrupted, exit 130."""
    real_construct = GraspSolver._construct_one

    def construct_then_interrupt(self: GraspSolver) -> object:
        # Interrupt as soon as the tracker holds a route, so best_so_far is
        # non-empty when the handler reads it (the partial-flush branch). Until
        # then, build normally so an admission can happen.
        if self.best_so_far:
            raise KeyboardInterrupt
        return real_construct(self)

    monkeypatch.setattr(GraspSolver, "_construct_one", construct_then_interrupt)

    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42)

    assert result.exit_code == 130, result.output
    html_files = sorted(output_dir.glob("route-*.html"))
    assert html_files, "best-so-far should have been flushed to disk before exit"

    payload = json.loads((output_dir / "route-1.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["convergence_status"] == "interrupted"
    # The last improvement landed on a real iteration before the interrupt.
    assert payload["metadata"]["convergence_iteration"] >= 1
    # The validator still ran on the partial set (per-route validation block present).
    assert "passed" in payload["validation"]


def test_interrupt_before_any_solution_writes_nothing(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C before any route is admitted → no reports, stderr warning, exit 130."""

    def construct_interrupt_immediately(_self: GraspSolver) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(GraspSolver, "_construct_one", construct_interrupt_immediately)

    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42)

    assert result.exit_code == 130, result.output
    assert not list(output_dir.glob("route-*.html")), "no reports on the no-solution path"
    assert "interrupted before any solution found" in result.stderr
