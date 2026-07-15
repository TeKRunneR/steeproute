"""Per-job JSON persistence — the store IS the runs index (architecture-app.md
§Category 5).

One directory per job under the store root: `<root>/<job_id>/job.json`. Writes
are atomic (temp-file in the same dir + `os.replace`), mirroring the CLI cache's
discipline so a crash mid-write never surfaces a partial record. The append-only
`progress.ndjson` and boot-time restart recovery arrive in later stories (1.4,
app-3-3); this store handles only the `job.json` record.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from typing import final

import platformdirs

from steeproute.app.models import JobRecord

_JOB_FILE = "job.json"


def default_store_root() -> pathlib.Path:
    """The runtime job-store root: `user_data_dir("steeproute")/app/jobs/`.

    Distinct from the CLI's *cache* root (`user_cache_dir`): the job store is the
    App's own state, the cache is external and read-only (architecture-app.md
    §Runtime-resolved paths)."""
    return pathlib.Path(platformdirs.user_data_dir("steeproute")) / "app" / "jobs"


@final
class JobStore:
    """File-backed job store. The root is injectable so tests use a tmp dir."""

    def __init__(self, root: pathlib.Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _job_dir(self, job_id: str) -> pathlib.Path:
        return self._root / job_id

    def create(self, record: JobRecord) -> None:
        """Persist a new job record, creating its per-job directory."""
        self._job_dir(record.id).mkdir(parents=True, exist_ok=True)
        self._write_atomic(record)

    def update(self, record: JobRecord) -> None:
        """Re-persist an existing record (status transitions, exit code, tail)."""
        self._write_atomic(record)

    def get(self, job_id: str) -> JobRecord | None:
        """Load one record, or `None` if there is no such job."""
        path = self._job_dir(job_id) / _JOB_FILE
        if not path.is_file():
            return None
        return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[JobRecord]:
        """All records, ordered by id (time-sortable → creation order)."""
        records: list[JobRecord] = []
        for job_dir in sorted(p for p in self._root.iterdir() if p.is_dir()):
            path = job_dir / _JOB_FILE
            if path.is_file():
                records.append(JobRecord.model_validate_json(path.read_text(encoding="utf-8")))
        return records

    def _write_atomic(self, record: JobRecord) -> None:
        """Write `job.json` via a same-dir temp file + `os.replace` (atomic).

        The temp file is a sibling so `os.replace` is a same-filesystem rename;
        a crash leaves at most the temp file, never a partial `job.json`.
        """
        job_dir = self._job_dir(record.id)
        target = job_dir / _JOB_FILE
        tmp = job_dir / f".{_JOB_FILE}.{uuid.uuid4().hex}.tmp"
        tmp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, target)
