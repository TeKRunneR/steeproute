"""REST routes for jobs (architecture-app.md §Category 8).

Thin by design: parse → store/enqueue → serialize. snake_case on the wire, no
response envelope (the resource is returned directly; errors are FastAPI's
default `{detail}` via `HTTPException`). Story 1.3 surfaces three endpoints
against `setup` jobs; SSE, stop, delete, and `/regions` arrive in later stories.

The store and queue are created in `main.lifespan` and read off `app.state`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from steeproute.app.models import (
    JobCreate,
    JobKind,
    JobRecord,
    JobStatus,
    new_job_id,
    utcnow_iso,
)
from steeproute.app.queue import JobQueue
from steeproute.app.store import JobStore

router = APIRouter()


def _store(request: Request) -> JobStore:
    return request.app.state.job_store


def _queue(request: Request) -> JobQueue:
    return request.app.state.job_queue


@router.post("/jobs", status_code=201)
async def create_job(body: JobCreate, request: Request) -> JobRecord:
    """Enqueue a job. Returns the created record (status `queued`) with HTTP 201.

    Only `setup` jobs are supported in this version; a `query` job is a valid
    enum value but not yet wired (Epic 2), so it is rejected 422.
    """
    if body.kind is not JobKind.SETUP:
        raise HTTPException(
            status_code=422,
            detail=f"job kind {body.kind.value!r} is not supported yet (setup only)",
        )
    record = JobRecord(
        id=new_job_id(),
        kind=body.kind,
        area=body.area,
        params=body.params.model_dump(),
        status=JobStatus.QUEUED,
        created_at=utcnow_iso(),
    )
    _store(request).create(record)
    _queue(request).enqueue(record.id)
    return record


@router.get("/jobs")
def list_jobs(request: Request) -> list[JobRecord]:
    """The full job registry (ordered by creation)."""
    return _store(request).list()


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> JobRecord:
    """One job record, or 404 if there is no such job."""
    record = _store(request).get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no job with id {job_id!r}")
    return record
