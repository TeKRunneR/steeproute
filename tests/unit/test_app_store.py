"""Unit tests for the per-job JSON store (App Story 1.3).

Covers create/get/update/list round-trips, unknown-id handling, id-ordered
listing, and the atomic-write discipline (no leftover temp file, `job.json`
present).
"""

from __future__ import annotations

import pathlib

from steeproute.app.models import AreaSpec, JobKind, JobRecord, JobStatus, utcnow_iso
from steeproute.app.store import JobStore


def _record(job_id: str, *, status: JobStatus = JobStatus.QUEUED) -> JobRecord:
    return JobRecord(
        id=job_id,
        kind=JobKind.SETUP,
        area=AreaSpec(center=(45.26, 5.788), radius_km=2.0),
        params={"untagged_trails": "include", "force_refresh": False, "dem_version": None},
        status=status,
        created_at=utcnow_iso(),
    )


def test_create_then_get_round_trips(tmp_path: pathlib.Path) -> None:
    store = JobStore(tmp_path)
    record = _record("job-a")
    store.create(record)

    loaded = store.get("job-a")
    assert loaded is not None
    assert loaded.id == "job-a"
    assert loaded.kind is JobKind.SETUP
    assert loaded.status is JobStatus.QUEUED
    assert loaded.area.center == (45.26, 5.788)


def test_get_unknown_id_returns_none(tmp_path: pathlib.Path) -> None:
    assert JobStore(tmp_path).get("nope") is None


def test_update_persists_status_transition(tmp_path: pathlib.Path) -> None:
    store = JobStore(tmp_path)
    record = _record("job-b")
    store.create(record)

    record.status = JobStatus.DONE
    record.exit_code = 0
    store.update(record)

    loaded = store.get("job-b")
    assert loaded is not None
    assert loaded.status is JobStatus.DONE
    assert loaded.exit_code == 0


def test_list_is_ordered_by_id(tmp_path: pathlib.Path) -> None:
    store = JobStore(tmp_path)
    # Insert out of order; list() must return id-sorted (== creation order for
    # time-sortable ids).
    for job_id in ("003", "001", "002"):
        store.create(_record(job_id))
    assert [r.id for r in store.list()] == ["001", "002", "003"]


def test_write_is_atomic_no_temp_leftover(tmp_path: pathlib.Path) -> None:
    store = JobStore(tmp_path)
    store.create(_record("job-c"))

    job_dir = tmp_path / "job-c"
    assert (job_dir / "job.json").is_file()
    # The temp sibling used for the atomic replace must not survive the write.
    assert list(job_dir.glob(".job.json.*.tmp")) == []


def test_empty_store_lists_nothing(tmp_path: pathlib.Path) -> None:
    assert JobStore(tmp_path).list() == []


def test_delete_removes_dir_and_drops_from_list(tmp_path: pathlib.Path) -> None:
    # Cancel-queued (App Story 3.2) deletes the per-job dir so the record leaves
    # both `get` and `list()`; a queued id lingering in the worker's queue then
    # hits the worker's skip-missing-record path (queue.py) and never runs.
    store = JobStore(tmp_path)
    store.create(_record("job-keep"))
    store.create(_record("job-drop"))

    store.delete("job-drop")

    assert store.get("job-drop") is None
    assert not (tmp_path / "job-drop").exists()
    assert [r.id for r in store.list()] == ["job-keep"]


def test_delete_missing_id_is_a_noop(tmp_path: pathlib.Path) -> None:
    # Tolerant of an already-absent dir (e.g. a double DELETE) — no error.
    JobStore(tmp_path).delete("never-existed")


# --- App Story 3.3: restart recovery ----------------------------------------


def test_recover_interrupted_flips_running_to_failed(tmp_path: pathlib.Path) -> None:
    # A job persisted as `running` when the server was killed is reconciled on
    # the next boot to `failed` + failure_reason="interrupted" + finished_at.
    store = JobStore(tmp_path)
    store.create(_record("job-run", status=JobStatus.RUNNING))

    flipped = store.recover_interrupted()

    assert flipped == ["job-run"]
    loaded = store.get("job-run")
    assert loaded is not None
    assert loaded.status is JobStatus.FAILED
    assert loaded.failure_reason == "interrupted"
    assert loaded.finished_at is not None


def test_recover_interrupted_leaves_queued_and_terminal_untouched(
    tmp_path: pathlib.Path,
) -> None:
    # Only `running` records are reconciled; queued and terminal ones are left
    # exactly as they were (no spurious flips).
    store = JobStore(tmp_path)
    expected = {
        "01-queued": JobStatus.QUEUED,
        "02-done": JobStatus.DONE,
        "03-failed": JobStatus.FAILED,
        "04-stopped": JobStatus.STOPPED,
    }
    for job_id, status in expected.items():
        store.create(_record(job_id, status=status))

    flipped = store.recover_interrupted()

    assert flipped == []
    for job_id, status in expected.items():
        loaded = store.get(job_id)
        assert loaded is not None
        assert loaded.status is status


def test_recover_interrupted_is_idempotent(tmp_path: pathlib.Path) -> None:
    # A second boot with no `running` jobs is a no-op: the already-recovered
    # failed(interrupted) job is not re-touched and no ids are returned.
    store = JobStore(tmp_path)
    store.create(_record("job-run", status=JobStatus.RUNNING))

    assert store.recover_interrupted() == ["job-run"]
    first = store.get("job-run")
    assert first is not None

    assert store.recover_interrupted() == []
    second = store.get("job-run")
    assert second is not None
    assert second.status is JobStatus.FAILED
    assert second.finished_at == first.finished_at  # not re-stamped
