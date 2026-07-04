"""E2E: the query CLI reports non-solver phase timings via stage lines.

The setup CLI has announced its pipeline stages since Story 11.1 (FR33); the
query side ran silent outside the solver's own iteration progress, even though
on large areas the non-solver phases dominate wall-clock (Epic 13 finding).
The query CLI now runs its phases inside the same `StageProgress` seam: cache
load, elevation reshaping (stages 6-7), trail-filter redux, climb detection,
contraction, and validate+render each print a start line and an elapsed line
on stdout, with the exact `stage:` format the setup CLI established.

Same offline in-process `CliRunner` path as `test_progress_cli.py`. The
`--quiet` suppression side is asserted in `test_quiet_suppresses_progress.py`
alongside the solver-progress suppression it already covers.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Callable

from click.testing import Result

QUERY_STAGES = [
    "load-prepared-area",
    "elevation-reshape",
    "trail-filter",
    "climb-detection",
    "climb-contraction",
    "validate-render",
]


def test_query_run_reports_stage_lines(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """Every non-solver query phase announces a start line and a timed done line."""
    result = run_query(seeded_cache, tmp_path / "reports", seed=42)

    assert result.exit_code == 0, result.output
    for stage in QUERY_STAGES:
        assert f"stage: {stage}" in result.output, f"missing stage line for {stage}"
        assert re.search(rf"stage: {stage}: \d+\.\d{{2}} s", result.output), (
            f"missing elapsed line for {stage}:\n{result.output}"
        )
    # The stage lines coexist with the pre-existing output contract: cache-hit
    # cue and the final run summary are still present.
    assert "cache-hit" in result.output
    assert "routes_returned" in result.output


def test_stage_lines_precede_their_phase_outputs(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """Stages appear in pipeline order — the timeline reads top-to-bottom."""
    result = run_query(seeded_cache, tmp_path / "reports", seed=42)

    assert result.exit_code == 0, result.output
    positions = [result.output.index(f"stage: {stage}") for stage in QUERY_STAGES]
    assert positions == sorted(positions), f"stage lines out of pipeline order:\n{result.output}"
