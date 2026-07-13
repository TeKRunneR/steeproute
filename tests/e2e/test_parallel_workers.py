"""Parallel GRASP restarts end-to-end through the query CLI (Story 14.4).

Four contracts, all in-process via the shared `seeded_cache` / `run_query`
fixtures (`tests/e2e/conftest.py`):

- **`--workers 1` is byte-identical to no flag** — the default single-process path
  is never routed through the parallel machinery, so goldens and FR29/NFR4 hold
  (AC #1). Asserted on the JSON sidecars, like `test_seeded_reproducibility.py`.
- **`--workers N` is reproducible per `(seed, workers)`** — two identical N>1
  invocations produce byte-identical JSON, and the run summary carries
  `workers=N` plus the coarse per-worker completion progress line (AC #2).
- **N>1 Ctrl-C** — the two branches of the interrupt handler: a salvaged partial
  set is rendered `interrupted` (exit 130), and an interrupt before any worker
  returned writes nothing + warns (exit 130). Driven by monkeypatching
  `run_parallel_grasp` to raise `ParallelGraspInterrupted`, mirroring
  `test_interrupt_in_process.py`'s monkeypatch-not-real-signal approach.

The N>1 runs spawn real worker processes (the `spawn` start method is pinned in
`run_parallel_grasp`); `--iter-budget` is kept small so they stay fast.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable

import pytest
from click.testing import Result

from steeproute.models import Edge, Solution
from steeproute.solver.parallel import (
    ParallelGraspFailed,
    ParallelGraspInterrupted,
    ParallelResult,
)

# Small budget so the two spawned workers finish quickly; determinism is
# budget-independent, so this only bounds wall-clock.
_N_WORKER_ARGS = ["--workers", "2", "--iter-budget", "80"]

# A route whose synthetic negative node ids can't exist in any contracted graph, so
# the validator's graph_membership check fails — but FR28 still renders it. Reused
# to give the interrupt-with-partial path a renderable payload (see test_run_summary).
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


def test_workers_1_byte_identical_to_default(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """`--workers 1` output == no `--workers` flag (same seed) — AC #1 byte-identity."""
    out_default = tmp_path / "default"
    out_workers_1 = tmp_path / "workers-1"

    result_default = run_query(seeded_cache, out_default, seed=42)
    result_workers_1 = run_query(
        seeded_cache, out_workers_1, seed=42, extra_args=["--workers", "1"]
    )

    assert result_default.exit_code == 0, result_default.output
    assert result_workers_1.exit_code == 0, result_workers_1.output

    json_default = sorted(out_default.glob("route-*.json"))
    json_workers_1 = sorted(out_workers_1.glob("route-*.json"))
    assert json_default, "expected at least one JSON sidecar"
    assert [p.name for p in json_default] == [p.name for p in json_workers_1]
    for path_default, path_w1 in zip(json_default, json_workers_1, strict=True):
        assert path_default.read_bytes() == path_w1.read_bytes(), (
            f"{path_default.name} differs between default and --workers 1 (AC #1 broken)"
        )


def test_workers_gt1_deterministic_and_summary(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """Two identical N>1 runs → byte-identical JSON; summary + worker progress emit."""
    out_a = tmp_path / "run-a"
    out_b = tmp_path / "run-b"

    result_a = run_query(seeded_cache, out_a, seed=42, extra_args=_N_WORKER_ARGS)
    result_b = run_query(seeded_cache, out_b, seed=42, extra_args=_N_WORKER_ARGS)

    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output

    # Run summary records the parallel worker count. (Live `progress:` lines are
    # timing-dependent — the tiny 80-iter budget finishes well inside the default
    # 5 s interval — so their emission is asserted separately with a fast interval.)
    assert "workers=2" in result_a.output, result_a.output

    json_a = sorted(out_a.glob("route-*.json"))
    json_b = sorted(out_b.glob("route-*.json"))
    assert json_a, "expected the parallel run to emit >= 1 route"
    assert [p.name for p in json_a] == [p.name for p in json_b]
    for path_a, path_b in zip(json_a, json_b, strict=True):
        assert path_a.read_bytes() == path_b.read_bytes(), (
            f"{path_a.name} differs between two --workers 2 --seed 42 runs (determinism broken)"
        )


def test_workers_gt1_migration_deterministic(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    """Island migration (`--merge-interval` < budget → multiple rounds) is deterministic.

    Budget 80 / interval 20 → ~4 migration rounds. Two identical runs must produce
    byte-identical JSON (determinism per `(seed, workers, merge_interval)`).
    """
    args = ["--workers", "2", "--iter-budget", "80", "--merge-interval", "20"]
    out_a = tmp_path / "mig-a"
    out_b = tmp_path / "mig-b"
    result_a = run_query(seeded_cache, out_a, seed=42, extra_args=args)
    result_b = run_query(seeded_cache, out_b, seed=42, extra_args=args)

    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output
    assert "merge_interval=20" in result_a.output, result_a.output  # recorded in summary

    json_a = sorted(out_a.glob("route-*.json"))
    json_b = sorted(out_b.glob("route-*.json"))
    assert json_a
    for path_a, path_b in zip(json_a, json_b, strict=True):
        assert path_a.read_bytes() == path_b.read_bytes(), (
            f"{path_a.name} differs between two migrating runs (determinism broken)"
        )


def test_workers_gt1_worker_death_falls_back_to_single_process(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead worker (`ParallelGraspFailed`) → warn + single-process fallback, exit 0.

    Each worker holds its own graph copy, so a high `--workers` / large area can OOM a
    worker (surfaces as `BrokenProcessPool` → `ParallelGraspFailed`). The query must
    still complete correctly instead of crashing with a traceback.
    """

    def raise_failed(*_args: object, **_kwargs: object) -> ParallelResult:
        raise ParallelGraspFailed("a worker process died (simulated)")

    monkeypatch.setattr("steeproute.cli.query.run_parallel_grasp", raise_failed)

    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42, extra_args=_N_WORKER_ARGS)

    assert result.exit_code == 0, result.output
    assert "falling back to --workers 1" in result.stderr
    assert sorted(output_dir.glob("route-*.json")), (
        "fallback single-process solve should emit routes"
    )


def test_workers_gt1_interrupt_renders_partial(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N>1 Ctrl-C with >=1 worker returned → partial rendered `interrupted`, exit 130."""
    partial = ParallelResult(
        solutions=[Solution(edges=(_BOGUS_EDGE,), objective=50.0)],
        convergence_status="interrupted",
        convergence_iteration=7,
        effective_workers=2,
    )

    def raise_interrupt(*_args: object, **_kwargs: object) -> ParallelResult:
        raise ParallelGraspInterrupted(partial)

    monkeypatch.setattr("steeproute.cli.query.run_parallel_grasp", raise_interrupt)

    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42, extra_args=_N_WORKER_ARGS)

    assert result.exit_code == 130, result.output
    html_files = sorted(output_dir.glob("route-*.html"))
    assert html_files, "the salvaged partial set should have been rendered before exit"
    payload = json.loads((output_dir / "route-1.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["convergence_status"] == "interrupted"


def test_workers_gt1_interrupt_before_any_worker_returns(
    seeded_cache: pathlib.Path,
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N>1 Ctrl-C before any worker returned → nothing written, stderr warning, exit 130."""
    empty_partial = ParallelResult(
        solutions=[],
        convergence_status="interrupted",
        convergence_iteration=0,
        effective_workers=2,
    )

    def raise_interrupt(*_args: object, **_kwargs: object) -> ParallelResult:
        raise ParallelGraspInterrupted(empty_partial)

    monkeypatch.setattr("steeproute.cli.query.run_parallel_grasp", raise_interrupt)

    output_dir = tmp_path / "reports"
    result = run_query(seeded_cache, output_dir, seed=42, extra_args=_N_WORKER_ARGS)

    assert result.exit_code == 130, result.output
    assert not list(output_dir.glob("route-*.html")), "no reports on the no-solution path"
    assert "interrupted before any solution found" in result.stderr
