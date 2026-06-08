"""E2E: the query CLI prints throttled progress lines during the solve (Story 7.1 AC #7).

Runs the `steeproute` query CLI in-process against a seeded fixture cache (same
offline `CliRunner` path as the Journey-1 happy-path test — a real `uv run`
subprocess can't be patched offline) with a small `--progress-interval`, and
asserts `progress:`-prefixed lines reach stdout. The default iter-budget (2000)
takes ~1.4 s on the fixture, so a 0.05 s interval reliably yields many fires.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Callable

from click.testing import Result


def test_progress_lines_appear_on_stdout_during_solve(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    output_dir = tmp_path / "reports"
    result = run_query(
        seeded_cache, output_dir, seed=42, extra_args=["--progress-interval", "0.05"]
    )

    assert result.exit_code == 0, result.output
    progress_lines = [ln for ln in result.output.splitlines() if ln.startswith("progress:")]
    assert progress_lines, f"expected >= 1 progress line on stdout, got:\n{result.output}"

    # The renderer's field shape (Story 7.1 ProgressEvent) is stable enough to pin.
    assert re.search(
        r"^progress: iter=\d+ best_objective=[\d.]+ elapsed=[\d.]+s eta=\S+ stagnation=\d+$",
        progress_lines[0],
    ), progress_lines[0]
