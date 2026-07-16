# pyright: reportUnknownMemberType=false
# Reason: `pytest.approx` is typed as returning a partially-unknown `ApproxBase`.
"""Unit tests for the setup + query stdout classifiers (App Stories 1.4 / 2.2).

Driven against the pinned Story 1.1 spike fixtures
(`tests/fixtures/app_stdout/*.stdout.txt`) — the same files the classifiers were
specified from, so these assertions verify the real captured line shapes map to
the expected `ProgressModel` fields.
"""

from __future__ import annotations

import pathlib

import pytest

from steeproute.app.cli_adapter import (
    QueryProgressParser,
    SetupProgressParser,
    progress_parser_for,
)
from steeproute.app.cli_adapter.progress_parse import QUERY_STAGES, SETUP_STAGES
from steeproute.app.models import JobKind, Phase, ProgressModel

_FIXTURES = pathlib.Path(__file__).parents[1] / "fixtures" / "app_stdout"
_FIXTURE = _FIXTURES / "setup_cache_miss.stdout.txt"


def _feed_all(lines: list[str]) -> list[ProgressModel]:
    """Feed each line and collect the non-None model snapshots."""
    parser = SetupProgressParser()
    return [m for line in lines if (m := parser.feed(line)) is not None]


def _feed_query(name: str) -> list[ProgressModel]:
    """Feed a query fixture through a fresh parser; collect model snapshots."""
    lines = (_FIXTURES / name).read_text(encoding="utf-8").splitlines()
    parser = QueryProgressParser()
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
    # Query jobs now reach the worker (App Story 2.1) — the factory must return
    # a working (non-raising) classifier, not defer with NotImplementedError.
    assert isinstance(progress_parser_for(JobKind.QUERY), QueryProgressParser)


# --- QueryProgressParser: full stage + GRASP classifier (App Story 2.2) -----


def test_query_parser_blank_line_emits_nothing() -> None:
    assert QueryProgressParser().feed("   ") is None


def test_query_stage_start_enters_stage_with_query_phase_and_strips_note() -> None:
    parser = QueryProgressParser()
    _ = parser.feed("stage: load-prepared-area ...")  # index 1
    _ = parser.feed("stage: load-prepared-area: 0.02 s")
    # A note on the start line is stripped to the clean canonical name.
    model = parser.feed("stage: elevation-reshape (stages 6-7) ...")
    assert model is not None
    assert model.phase is Phase.QUERY
    assert model.stage_name == "elevation-reshape"
    assert model.stage_index == 2
    assert model.stage_total == len(QUERY_STAGES) == 6
    assert model.grasp is None


def test_query_cache_hit_cue_lands_in_log_tail_without_advancing() -> None:
    parser = QueryProgressParser()
    _ = parser.feed("stage: load-prepared-area ...")  # index 1
    _ = parser.feed("stage: load-prepared-area: 0.02 s")
    cue = parser.feed("steeproute: cache-hit cache_key_hash: fb7092ddd1059ea2")
    assert cue is not None
    # The single-line query cache-hit cue is informational: no stage advance.
    assert cue.stage_index == 1
    assert cue.stage_name == "load-prepared-area"
    assert cue.grasp is None
    assert "steeproute: cache-hit cache_key_hash: fb7092ddd1059ea2" in cue.log_tail


def test_query_single_process_grasp_sets_solve_phase_and_grasp() -> None:
    parser = QueryProgressParser()
    _ = parser.feed("stage: climb-contraction ...")  # index 1 (partial run)
    _ = parser.feed("stage: climb-contraction: 0.02 s")
    model = parser.feed(
        "progress: iter=24510 best_objective=9719.6 elapsed=1.6s eta=11s stagnation=9631"
    )
    assert model is not None
    assert model.phase is Phase.SOLVE
    assert model.grasp is not None
    assert model.grasp.iter == 24510
    assert model.grasp.best_cost == pytest.approx(9719.6)
    assert model.elapsed == pytest.approx(1.6)
    # A progress line does NOT advance the stage index.
    assert model.stage_index == 1
    assert model.stage_name == "climb-contraction"


def test_query_parallel_grasp_maps_iters_and_best_worker_objective() -> None:
    parser = QueryProgressParser()
    model = parser.feed(
        "progress: workers=4/4 iters=158393 best_worker_objective=10118.8 elapsed=9.1s"
    )
    assert model is not None
    assert model.phase is Phase.SOLVE
    assert model.grasp is not None
    # Parallel: grasp.iter = aggregate iters, best_cost = best_worker_objective.
    assert model.grasp.iter == 158393
    assert model.grasp.best_cost == pytest.approx(10118.8)
    assert model.elapsed == pytest.approx(9.1)


def test_query_stage_start_after_solve_resets_grasp_and_phase() -> None:
    # The crux of AC #3: grasp is present ONLY during the solve; the
    # validate-render start that follows resets it and returns to query phase.
    parser = QueryProgressParser()
    _ = parser.feed("stage: climb-contraction: 0.02 s")
    solving = parser.feed(
        "progress: iter=100 best_objective=500.0 elapsed=0.5s eta=10s stagnation=0"
    )
    assert solving is not None and solving.phase is Phase.SOLVE and solving.grasp is not None
    after = parser.feed("stage: validate-render ...")
    assert after is not None
    assert after.phase is Phase.QUERY
    assert after.grasp is None
    assert after.stage_name == "validate-render"


def test_query_summary_block_lands_in_log_tail_only() -> None:
    parser = QueryProgressParser()
    _ = parser.feed("stage: validate-render: 0.22 s")
    model = parser.feed("--- Run summary ---")
    assert model is not None
    # The summary delimiter matches no stage/grasp rule → log tail only.
    assert model.phase is Phase.QUERY
    assert model.grasp is None
    assert "--- Run summary ---" in model.log_tail
    # `total_objective` has no ProgressModel field: it just rides in log_tail.
    obj = parser.feed("total_objective: 9719.6")
    assert obj is not None
    assert obj.grasp is None
    assert "total_objective: 9719.6" in obj.log_tail


def test_query_workers1_fixture_single_process_end_to_end() -> None:
    models = _feed_query("query_workers1.stdout.txt")
    assert models, "fixture produced no progress models"

    # The solve phase is entered and populates grasp with the single-process shape.
    solve_models = [m for m in models if m.phase is Phase.SOLVE]
    assert solve_models, "no solve-phase models seen"
    assert all(m.grasp is not None for m in solve_models)
    # Solve happens while stage 5 (climb-contraction) is the current stage.
    assert all(m.stage_index == 5 and m.stage_name == "climb-contraction" for m in solve_models)

    # The final model is post-solve: validate-render (stage 6), grasp dropped.
    final = models[-1]
    assert final.phase is Phase.QUERY
    assert final.stage_index == 6
    assert final.stage_name == "validate-render"
    assert final.stage_total == 6
    assert final.grasp is None


def test_query_workers4_fixture_parallel_end_to_end() -> None:
    models = _feed_query("query_workers4.stdout.txt")
    assert models, "fixture produced no progress models"

    solve_models = [m for m in models if m.phase is Phase.SOLVE]
    assert solve_models, "no solve-phase models seen"
    assert all(m.grasp is not None for m in solve_models)
    # Parallel best_worker_objective is non-monotonic-friendly: just report it.
    last_solve = solve_models[-1]
    assert last_solve.grasp is not None
    assert last_solve.grasp.iter == 158393
    assert last_solve.grasp.best_cost == pytest.approx(10118.8)

    final = models[-1]
    assert final.phase is Phase.QUERY
    assert final.stage_index == 6
    assert final.stage_name == "validate-render"
    assert final.grasp is None
