"""Per-job JSON persistence — the store IS the runs index (architecture-app.md
§Category 5).

One directory per job under the store root: `<root>/<job_id>/job.json`. Writes
are atomic (temp-file in the same dir + `os.replace`), mirroring the CLI cache's
discipline so a crash mid-write never surfaces a partial record. Alongside it,
`progress.ndjson` is an **append-only** progress log (one `ProgressModel` per
line) that powers the SSE snapshot-then-tail (Story 1.4). Boot-time restart
recovery arrives in Story app-3-3.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import uuid
from typing import final

import platformdirs

from steeproute.app.models import JobRecord, ProgressModel

_JOB_FILE = "job.json"
_PROGRESS_FILE = "progress.ndjson"


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

    def job_dir(self, job_id: str) -> pathlib.Path:
        """The job's own directory (public accessor; App Story 2.1).

        A query job's `--output-dir` must be a per-job path (the CLI's own
        `./results` default is relative to the server's cwd and would collide
        across jobs) — this is the one place that path is computed, so
        `cli_adapter.argv` and the worker never recompute or duplicate the
        store's directory-layout formula (architecture-app.md §Category 5)."""
        return self._job_dir(job_id)

    def create(self, record: JobRecord) -> None:
        """Persist a new job record, creating its per-job directory."""
        self._job_dir(record.id).mkdir(parents=True, exist_ok=True)
        self._write_atomic(record)

    def update(self, record: JobRecord) -> None:
        """Re-persist an existing record (status transitions, exit code, tail)."""
        self._write_atomic(record)

    def delete(self, job_id: str) -> None:
        """Remove a job's entire directory (App Story 3.2 — cancel queued).

        Deleting the record is what makes a cancelled job disappear from `list()`
        (hence `GET /jobs` and the run library) and from any result serving. The
        job id may still sit in the worker's in-memory queue; when the worker
        pops it, `get` returns `None` and it hits the skip-missing-record branch
        (queue.py) — so no `asyncio.Queue` surgery is needed here. Tolerant of an
        already-absent dir (a double DELETE is a no-op)."""
        shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

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

    def append_progress(self, job_id: str, model: ProgressModel) -> None:
        """Append one progress entry to the job's `progress.ndjson`.

        Append-only (one JSON object per line), distinct from the atomic
        `job.json` rewrite. The single worker is the sole appender (concurrency =
        1), so line order == emission order and the line count == the next event
        sequence number — which lets the SSE endpoint stitch snapshot-then-tail.
        """
        path = self._job_dir(job_id) / _PROGRESS_FILE
        with path.open("a", encoding="utf-8") as fh:
            _ = fh.write(model.model_dump_json() + "\n")

    def read_progress(self, job_id: str) -> list[ProgressModel]:
        """Read the persisted progress snapshot (empty if none yet).

        Tolerant of a partial trailing line (a crash mid-append can leave one):
        unparseable lines are skipped rather than failing the whole read.
        """
        path = self._job_dir(job_id) / _PROGRESS_FILE
        if not path.is_file():
            return []
        out: list[ProgressModel] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(ProgressModel.model_validate_json(line))
            except ValueError:
                continue
        return out

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
