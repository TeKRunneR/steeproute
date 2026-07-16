"""REST routes for jobs (architecture-app.md §Category 8).

Thin by design: parse → store/enqueue → serialize. snake_case on the wire, no
response envelope (the resource is returned directly; errors are FastAPI's
default `{detail}` via `HTTPException`). Story 1.4 added the SSE progress stream
(`GET /jobs/{id}/events`), Story 1.5 the hard-cancel `POST /jobs/{id}/stop`, and
Story 1.6 the read-only `GET /regions` map overlay; `DELETE /jobs/{id}` (cancel
queued) arrives with Story 3.2.

The store, queue, and SSE hub are created in `main.lifespan` and read off
`app.state`.
"""

from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from steeproute.app.cli_adapter import SchemaField, list_regions, query_params_schema, resolve_area
from steeproute.app.models import (
    AreaResolution,
    JobCreate,
    JobRecord,
    JobStatus,
    RegionInfo,
    new_job_id,
    utcnow_iso,
)
from steeproute.app.queue import JobQueue, Worker
from steeproute.app.sse import ProgressEvent, ProgressHub
from steeproute.app.store import JobStore

router = APIRouter()

# Terminal states — once reached, the SSE stream emits a final `status` and closes.
_TERMINAL: frozenset[JobStatus] = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.STOPPED})


def _store(request: Request) -> JobStore:
    return request.app.state.job_store


def _queue(request: Request) -> JobQueue:
    return request.app.state.job_queue


def _hub(request: Request) -> ProgressHub:
    return request.app.state.progress_hub


def _worker(request: Request) -> Worker:
    return request.app.state.job_worker


def _regions_cache_root(request: Request) -> pathlib.Path | None:
    """The cache root `GET /regions` reads. `None` → the CLI default root (the
    real location the setup subprocess writes to); tests inject a crafted root.
    `getattr` default guards the (test-only) case where the lifespan hasn't run."""
    return getattr(request.app.state, "regions_cache_root", None)


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

    Both `setup` and `query` kinds are accepted (App Story 2.1); `body.params`
    is already validated against the kind-matching model (`SetupParams` or
    `QueryParams`) by `JobCreate`'s own kind-dispatch, so a malformed or
    mismatched body has already failed 422 before this handler runs.
    """
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


@router.get("/params/query-schema")
def get_query_params_schema() -> list[SchemaField]:
    """The introspected query-form schema (App Story 2.1, architecture-app.md
    §Category 9): field name/type/default/choices/help/basic-or-advanced group,
    derived from `steeproute.cli.query`'s click command — never hand-duplicated.
    `config-form.js` renders the basic/advanced form directly from this; no
    other file hand-lists query flags.
    """
    return query_params_schema()


@router.get("/regions")
def get_regions(request: Request) -> list[RegionInfo]:
    """Built regions for the map overlay (architecture-app.md §Category 6).

    Read straight from the CLI's on-disk cache through `cli_adapter.regions` (the
    only cache-reading code); an empty or absent cache returns `[]`, not an error.
    Read-only — listing regions never triggers a build. snake_case, no envelope.
    """
    return list_regions(cache_root=_regions_cache_root(request))


@router.get("/regions/resolve")
def resolve_region(
    request: Request,
    lat: float,
    lon: float,
    radius_km: Annotated[float, Query(gt=0)],
) -> AreaResolution:
    """Resolve a candidate selection to its bbox + green/grey coverage (Story 1.6).

    The map picker sends its picked `center`/`radius_km`; the server returns the
    exact WGS84 bbox and the coverage decision computed by the CLI cache's own
    conversion + containment (`cli_adapter.resolve_area`). Keeps ALL km→deg and
    containment server-side so the overlay can't drift from query-side coverage.
    Read-only. `radius_km` must be > 0 (else 422).
    """
    return resolve_area((lat, lon), radius_km, cache_root=_regions_cache_root(request))


@router.get("/jobs/{job_id}")
def get_job(job: Annotated[JobRecord, Depends(_require_job)]) -> JobRecord:
    """One job record, or 404 if there is no such job (via `_require_job`)."""
    return job


@router.post("/jobs/{job_id}/stop")
async def stop_job(job: Annotated[JobRecord, Depends(_require_job)], request: Request) -> JobRecord:
    """Hard-cancel a running job (architecture-app.md §Category 7).

    Requests the worker to kill the child; the worker owns the terminal transition
    to `stopped`/exit 130 (it is the single writer of terminal status), so the
    record returned here may still read `running` — the client observes `stopped`
    over SSE / on re-fetch. 409 if the job is not currently running (unknown id →
    404 via `_require_job`). `async` so `proc.kill()` runs on the event loop, not a
    threadpool thread.
    """
    if job.status is not JobStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"job {job.id!r} is not running (status {job.status.value!r})",
        )
    # A 200 must mean the kill was actually dispatched. `stop()` returns False only
    # when the record reads `running` but the worker has no such active job — a
    # stale record (e.g. left by a crash; reconciled on boot in Story 3.3), not a
    # live job — so surface that as 409 rather than a misleading success.
    if not _worker(request).stop(job.id):
        raise HTTPException(
            status_code=409,
            detail=f"job {job.id!r} is not the active running job",
        )
    return _store(request).get(job.id) or job


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
