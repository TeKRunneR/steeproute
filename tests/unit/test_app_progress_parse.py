# pyright: reportUnknownMemberType=false
# Reason: `pytest.approx` is typed as returning a partially-unknown `ApproxBase`.
"""Unit tests for the setup stdout classifier (App Story 1.4).

Driven against the pinned Story 1.1 spike fixture
(`tests/fixtures/app_stdout/setup_cache_miss.stdout.txt`) — the same file the
classifier was specified from, so these assertions verify the real captured line
shapes map to the expected `ProgressModel` fields.
"""

from __future__ import annotations

import pathlib

import pytest

from steeproute.app.cli_adapter import SetupProgressParser, progress_parser_for
from steeproute.app.cli_adapter.progress_parse import SETUP_STAGES
from steeproute.app.models import JobKind, Phase, ProgressModel

_FIXTURE = (
    pathlib.Path(__file__).parents[1]
    / "fixtures"
    / "app_stdout"
    / "setup_cache_miss.stdout.txt"
)


def _feed_all(lines: list[str]) -> list[ProgressModel]:
    """Feed each line and collect the non-None model snapshots."""
    parser = SetupProgressParser()
    return [m for line in lines if (m := parser.feed(line)) is not None]


def test_setup_stage_start_enters_stage_and_strips_note() -> None:
    parser = SetupProgressParser()
    model = parser.feed("stage: osm-download (one Overpass request; typically takes minutes) ...")
    assert model is not None
    assert model.phase is Phase.SETUP
    # Note is stripped to the clean canonical name.
    assert model.stage_name == "osm-download"
    assert model.stage_index == 1
    assert model.stage_total == len(SETUP_STAGES) == 7
    assert model.grasp is None
    assert model.elapsed is None


def test_setup_stage_done_records_elapsed_without_advancing() -> None:
    parser = SetupProgressParser()
    _ = parser.feed("stage: osm-download (note) ...")
    done = parser.feed("stage: osm-download: 7.69 s")
    assert done is not None
    assert done.stage_name == "osm-download"
    assert done.stage_index == 1  # done line does not advance the index
    assert done.elapsed == pytest.approx(7.69)


def test_tile_within_stage_line_lands_in_log_tail_without_advancing() -> None:
    parser = SetupProgressParser()
    _ = parser.feed("stage: osm-download ...")  # index 1
    _ = parser.feed("stage: osm-download: 7.69 s")
    _ = parser.feed("stage: trail-filter ...")  # index 2
    _ = parser.feed("stage: trail-filter: 0.02 s")
    _ = parser.feed("stage: polyline-smoothing ...")  # index 3
    _ = parser.feed("stage: polyline-smoothing: 0.01 s")
    _ = parser.feed("stage: resampling ...")  # index 4
    _ = parser.feed("stage: resampling: 0.07 s")
    _ = parser.feed("stage: dem-resolve ...")  # index 5
    tile = parser.feed("  tile 0/1")
    assert tile is not None
    # The within-stage tile line does NOT advance the stage...
    assert tile.stage_index == 5
    assert tile.stage_name == "dem-resolve"
    # ...but it IS surfaced in the log tail.
    assert "  tile 0/1" in tile.log_tail


def test_blank_line_emits_nothing() -> None:
    assert SetupProgressParser().feed("   ") is None


def test_cache_hit_summary_only_is_tolerated() -> None:
    # A setup cache-hit emits the summary block and NO stage lines — the model
    # must stay coherent with zero stages seen.
    parser = SetupProgressParser()
    model = parser.feed("steeproute-setup: cache-hit")
    assert model is not None
    assert model.phase is Phase.SETUP
    assert model.stage_index == 0
    assert model.stage_name is None
    assert model.stage_total == 7
    assert model.grasp is None


def test_full_fixture_run_reaches_seventh_stage() -> None:
    lines = _FIXTURE.read_text(encoding="utf-8").splitlines()
    models = _feed_all(lines)
    assert models, "fixture produced no progress models"

    final = models[-1]
    # All 7 setup stages seen, in order; grasp stays null for setup throughout.
    assert final.stage_index == 7
    assert final.stage_name == "cache-write"
    assert final.stage_total == 7
    assert all(m.grasp is None for m in models)
    assert all(m.phase is Phase.SETUP for m in models)
    # Last recorded stage elapsed is cache-write's (the final `stage: … : t s`).
    assert final.elapsed == pytest.approx(0.05)


def test_stage_index_progression_over_fixture() -> None:
    lines = _FIXTURE.read_text(encoding="utf-8").splitlines()
    models = _feed_all(lines)
    # stage_index is monotonic non-decreasing and tops out at 7.
    indices = [m.stage_index for m in models]
    assert indices == sorted(indices)
    assert max(indices) == 7


def test_parser_factory_setup_and_query() -> None:
    assert isinstance(progress_parser_for(JobKind.SETUP), SetupProgressParser)
    # Query classification is Story 2.2; the factory refuses it explicitly.
    with pytest.raises(NotImplementedError):
        _ = progress_parser_for(JobKind.QUERY)
