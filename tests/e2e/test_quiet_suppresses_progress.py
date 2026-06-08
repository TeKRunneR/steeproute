"""E2E: `--quiet` suppresses progress lines during the solve (Story 7.1 AC #7).

Same offline in-process `CliRunner` path as `test_progress_cli.py`, but with
`--quiet`: the CLI installs a `None` progress callback, so no `progress:` line
should reach stdout even with a tiny `--progress-interval`. The run still
completes normally (exit 0, cache-hit cue still printed) — `--quiet` suppresses
only intermediate progress, not the run itself. The final run summary is out of
scope here (Story 7.5).
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable

from click.testing import Result


def test_quiet_suppresses_progress_lines(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    output_dir = tmp_path / "reports"
    result = run_query(
        seeded_cache,
        output_dir,
        seed=42,
        extra_args=["--quiet", "--progress-interval", "0.05"],
    )

    assert result.exit_code == 0, result.output
    progress_lines = [ln for ln in result.output.splitlines() if ln.startswith("progress:")]
    assert not progress_lines, f"--quiet must suppress progress lines, got:\n{progress_lines}"
    # The solve still ran end-to-end: the cache-hit cue is emitted before it, and
    # reports were written after it.
    assert "cache-hit" in result.output
    assert list(output_dir.glob("route-*.html")), "expected reports despite --quiet"
