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
