"""In-process SSE progress hub (App Story 1.4, architecture-app.md §Category 4).

Single process, single worker: a per-job asyncio fan-out. The worker publishes
progress + terminal-status events as they happen; the SSE endpoint subscribes
and streams them. The persisted `progress.ndjson` supplies the reconnect
snapshot — this hub carries only the live tail. Every progress event carries a
0-based `seq` (its index in the append-only log) so the endpoint can stitch
snapshot-then-tail with neither a gap nor a duplicate across the handoff.

One-way server→client only. No cross-thread concerns: worker and endpoints share
the one event loop, so `asyncio.Queue` fan-out is safe without locking.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import final

from steeproute.app.models import ProgressModel


@dataclass(frozen=True)
class ProgressEvent:
    """A live progress update. `seq` is the entry's 0-based index in
    `progress.ndjson`, used to dedupe against the replayed snapshot."""

    seq: int
    model: ProgressModel


@dataclass(frozen=True)
class StatusEvent:
    """A terminal status transition; closes the stream when emitted."""

    status: str
    exit_code: int | None
    failure_reason: str | None


Event = ProgressEvent | StatusEvent


@final
class ProgressHub:
    """Per-job publish/subscribe over `asyncio.Queue` fan-out."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)

    def subscribe(self, job_id: str) -> asyncio.Queue[Event]:
        """Register a new subscriber queue for a job."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[job_id].add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[Event]) -> None:
        """Drop a subscriber (safe to call twice; prunes the empty job bucket)."""
        subscribers = self._subscribers.get(job_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            _ = self._subscribers.pop(job_id, None)

    def publish(self, job_id: str, event: Event) -> None:
        """Fan an event out to every current subscriber of the job (no-op if
        none — a job with no live watchers still persists to `progress.ndjson`)."""
        for queue in self._subscribers.get(job_id, ()):
            queue.put_nowait(event)
