"""REST routes for jobs (architecture-app.md §Category 8).

Thin by design: parse → store/enqueue → serialize. snake_case on the wire, no
response envelope (the resource is returned directly; errors are FastAPI's
default `{detail}` via `HTTPException`). Story 1.4 adds the SSE progress stream
(`GET /jobs/{id}/events`, snapshot-then-tail) on top of Story 1.3's job
endpoints; stop, delete, and `/regions` arrive in later stories.

The store, queue, and SSE hub are created in `main.lifespan` and read off
`app.state`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from steeproute.app.models import (
    JobCreate,
    JobKind,
    JobRecord,
    JobStatus,
    new_job_id,
    utcnow_iso,
)
from steeproute.app.queue import JobQueue
from steeproute.app.sse import ProgressEvent, ProgressHub
from steeproute.app.store import JobStore

router = APIRouter()

# Terminal states — once reached, the SSE stream emits a final `status` and closes.
_TERMINAL: frozenset[JobStatus] = frozenset(
    {JobStatus.DONE, JobStatus.FAILED, JobStatus.STOPPED}
)


def _store(request: Request) -> JobStore:
    return request.app.state.job_store


def _queue(request: Request) -> JobQueue:
    return request.app.state.job_queue


def _hub(request: Request) -> ProgressHub:
    return request.app.state.progress_hub


def _require_job(job_id: str, request: Request) -> JobRecord:
    """Dependency: resolve a job or 404 *before* a streaming response starts."""
    record = _store(request).get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no job with id {job_id!r}")
    return record


def _status_payload(
    status: str, exit_code: int | None, failure_reason: str | None
) -> dict[str, object | None]:
    """The terminal `status` SSE event body. One shape, fed from either a
    `JobRecord` (already-terminal branch) or a `StatusEvent` (live branch)."""
    return {"status": status, "exit_code": exit_code, "failure_reason": failure_reason}


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
def get_job(job: Annotated[JobRecord, Depends(_require_job)]) -> JobRecord:
    """One job record, or 404 if there is no such job (via `_require_job`)."""
    return job


@router.get("/jobs/{job_id}/events", response_class=EventSourceResponse)
async def job_events(
    job_id: str,
    request: Request,
    _job: Annotated[JobRecord, Depends(_require_job)],
) -> AsyncIterator[ServerSentEvent]:
    """SSE progress stream: snapshot-then-tail (architecture-app.md §Category 4).

    On connect, replays the persisted `progress.ndjson` snapshot as named
    `progress` events, then streams the live tail; on terminal it emits one
    `status` event and closes. Unknown id → 404 (via the `_require_job`
    dependency, before the stream starts). Heartbeat keepalive comments are
    inserted natively by FastAPI's `EventSourceResponse` when the stream idles —
    no hand-rolled ping loop.

    Snapshot/tail are stitched with no gap or duplicate: we subscribe *before*
    reading the snapshot, then skip any live event whose `seq` is already covered
    by the replayed snapshot.
    """
    store = _store(request)
    hub = _hub(request)
    queue = hub.subscribe(job_id)
    try:
        snapshot = store.read_progress(job_id)
        emitted = len(snapshot)  # snapshot covers seq 0 .. emitted-1
        for model in snapshot:
            yield ServerSentEvent(event="progress", data=model)

        record = store.get(job_id)
        if record is not None and record.status in _TERMINAL:
            # Already finished (possibly before we subscribed): drain any progress
            # queued after our snapshot read, then emit the terminal status.
            while not queue.empty():
                event = queue.get_nowait()
                if isinstance(event, ProgressEvent) and event.seq >= emitted:
                    yield ServerSentEvent(event="progress", data=event.model)
                    emitted = event.seq + 1
            yield ServerSentEvent(
                event="status",
                data=_status_payload(record.status.value, record.exit_code, record.failure_reason),
            )
            return

        # Live tail: stream progress until the terminal status arrives.
        while True:
            event = await queue.get()
            if isinstance(event, ProgressEvent):
                if event.seq >= emitted:
                    yield ServerSentEvent(event="progress", data=event.model)
                    emitted = event.seq + 1
            else:  # StatusEvent → terminal, close the stream.
                yield ServerSentEvent(
                    event="status",
                    data=_status_payload(event.status, event.exit_code, event.failure_reason),
                )
                return
    finally:
        hub.unsubscribe(job_id, queue)
