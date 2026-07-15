"""Single-worker serial job queue (architecture-app.md §Category 2).

Concurrency = 1 is a hard constraint, not a simplification: the solver saturates
all cores and cross-run thread-safety is unintended. One worker coroutine (started
in the FastAPI `lifespan`) drains an in-memory `asyncio.Queue` of job ids, one at
a time: pop → mark running → spawn the CLI as a subprocess → drain its stdout
(keeping a bounded tail) → record the exit code → set the terminal status.

The worker NEVER dies on a bad job: any per-job failure marks that job `failed`
and moves on (architecture-app.md §Process patterns). As of Story 1.4 the worker
also classifies each stdout line into the unified `ProgressModel`, appends it to
the job's append-only `progress.ndjson`, and publishes it (plus a terminal
status) to the SSE hub. A bounded stdout/stderr tail is still kept on the record
for the failed-job diagnostic. The live Stop action (→ `stopped`, exit 130) lands
in Story 1.5.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import final

from steeproute.app.cli_adapter import build_setup_argv, progress_parser_for
from steeproute.app.models import JobKind, JobRecord, JobStatus, SetupParams, utcnow_iso
from steeproute.app.sse import ProgressEvent, ProgressHub, StatusEvent
from steeproute.app.store import JobStore

logger = logging.getLogger(__name__)

# How many trailing stdout lines to retain on the record for diagnostics.
STDOUT_TAIL_LINES = 50

# record → argv. Injectable so tests point argv[0] at a fake command while still
# driving the real spawn/drain/exit path.
BuildArgv = Callable[[JobRecord], list[str]]


def default_build_argv(record: JobRecord) -> list[str]:
    """Build the subprocess argv for a job via the CLI adapter (setup only for now)."""
    return build_setup_argv(record.area, SetupParams.model_validate(record.params))


@final
class JobQueue:
    """Thin wrapper over an `asyncio.Queue` of job ids.

    Created inside the running event loop (the queue binds to it). Unbounded, so
    `enqueue` never blocks and can be called from an async request handler.
    """

    def __init__(self) -> None:
        self._ids: asyncio.Queue[str] = asyncio.Queue()

    def enqueue(self, job_id: str) -> None:
        self._ids.put_nowait(job_id)

    async def get(self) -> str:
        return await self._ids.get()

    def task_done(self) -> None:
        self._ids.task_done()


# argv → spawned process. Injectable only for the rare test that needs to fake the
# process object itself; the default is the real subprocess spawn.
Spawn = Callable[[list[str]], Awaitable["asyncio.subprocess.Process"]]


async def _default_spawn(argv: list[str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _drain(stream: asyncio.StreamReader | None, tail: deque[str]) -> None:
    """Read a subprocess stream to EOF, retaining the last `tail.maxlen` lines.

    Both stdout and stderr MUST be drained concurrently: if only one is read, the
    child can block writing to the other's full pipe buffer while the worker waits
    for EOF that never comes — a deadlock that (concurrency = 1) freezes the whole
    queue. The CLI's `run_entry_point` writes `error: ...` to stderr, so the
    stderr tail is the useful diagnostic on a failed job.
    """
    if stream is None:
        return
    async for raw in stream:
        tail.append(raw.decode("utf-8", errors="replace").rstrip("\r\n"))


@final
class Worker:
    """The single serial worker. `run()` loops forever until cancelled."""

    def __init__(
        self,
        store: JobStore,
        queue: JobQueue,
        *,
        build_argv: BuildArgv = default_build_argv,
        spawn: Spawn = _default_spawn,
        hub: ProgressHub | None = None,
    ) -> None:
        self._store = store
        self._queue = queue
        self._build_argv = build_argv
        self._spawn = spawn
        # A hub with no subscribers is a harmless no-op, so a caller that doesn't
        # care about live streaming (e.g. some unit tests) can omit it.
        self._hub = hub if hub is not None else ProgressHub()

    async def run(self) -> None:
        """Serial worker loop. Cancellation (lifespan shutdown) propagates out."""
        while True:
            job_id = await self._queue.get()
            try:
                await self._run_one(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # One poisoned job must never stall the queue. Best-effort mark
                # it failed, then keep serving.
                logger.exception("job %s crashed in the worker loop", job_id)
                self._mark_failed(job_id, reason="worker-exception")
            finally:
                self._queue.task_done()

    async def _run_one(self, job_id: str) -> None:
        record = self._store.get(job_id)
        if record is None:
            logger.warning("queued job %s has no store record; skipping", job_id)
            return

        record.status = JobStatus.RUNNING
        record.started_at = utcnow_iso()
        self._store.update(record)

        argv = self._build_argv(record)
        stdout_tail: deque[str] = deque(maxlen=STDOUT_TAIL_LINES)
        stderr_tail: deque[str] = deque(maxlen=STDOUT_TAIL_LINES)
        proc: asyncio.subprocess.Process | None = None
        try:
            try:
                proc = await self._spawn(argv)
            except OSError as exc:
                # e.g. the console script is not on PATH — an honest failed job,
                # not a worker crash.
                record.finished_at = utcnow_iso()
                record.status = JobStatus.FAILED
                record.failure_reason = f"spawn-failed: {exc}"
                self._store.update(record)
                self._publish_status(record)
                return

            # Classify + persist + stream stdout, and drain stderr, concurrently
            # (see `_drain` — reading only one pipe deadlocks), then reap the exit
            # code.
            await asyncio.gather(
                self._consume_stdout(record.id, record.kind, proc.stdout, stdout_tail),
                _drain(proc.stderr, stderr_tail),
            )
            exit_code = await proc.wait()
        except asyncio.CancelledError:
            # Lifespan shutdown mid-run (cancel can land during spawn, drain, or
            # wait): kill the child so it can't outlive the server as an orphan,
            # and record the interrupted terminal state (status=failed +
            # failure_reason, per architecture-app.md §data-format) so the record
            # never lies "running" forever. This is the same state restart
            # recovery (Story app-3-3) would set on the next boot.
            if proc is not None and proc.returncode is None:
                proc.kill()
            record.finished_at = utcnow_iso()
            record.status = JobStatus.FAILED
            record.failure_reason = "interrupted"
            record.stdout_tail = list(stdout_tail)
            record.stderr_tail = list(stderr_tail)
            self._store.update(record)
            self._publish_status(record)
            raise

        record.exit_code = exit_code
        record.finished_at = utcnow_iso()
        record.stdout_tail = list(stdout_tail)
        record.stderr_tail = list(stderr_tail)
        record.status = JobStatus.DONE if exit_code == 0 else JobStatus.FAILED
        self._store.update(record)
        self._publish_status(record)

    async def _consume_stdout(
        self,
        job_id: str,
        kind: JobKind,
        stream: asyncio.StreamReader | None,
        tail: deque[str],
    ) -> None:
        """Read stdout to EOF, classifying each line into the `ProgressModel`,
        appending it to `progress.ndjson`, and publishing it to the SSE hub.

        Keeps the bounded raw `tail` too (the failed-job diagnostic). This
        coroutine is the sole appender for the job, so the running `seq` stays in
        lock-step with the persisted line count.
        """
        if stream is None:
            return
        parser = progress_parser_for(kind)
        seq = 0
        async for raw in stream:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            tail.append(line)
            model = parser.feed(line)
            if model is None:
                continue
            self._store.append_progress(job_id, model)
            self._hub.publish(job_id, ProgressEvent(seq=seq, model=model))
            seq += 1

    def _publish_status(self, record: JobRecord) -> None:
        """Publish the terminal status to the hub, closing any live stream."""
        self._hub.publish(
            record.id,
            StatusEvent(
                status=record.status.value,
                exit_code=record.exit_code,
                failure_reason=record.failure_reason,
            ),
        )

    def _mark_failed(self, job_id: str, *, reason: str) -> None:
        record = self._store.get(job_id)
        if record is None:
            return
        record.status = JobStatus.FAILED
        record.failure_reason = reason
        if record.finished_at is None:
            record.finished_at = utcnow_iso()
        self._store.update(record)
        self._publish_status(record)
