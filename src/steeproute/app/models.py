"""Job domain models for the web App (App Story 1.3).

The wire contract and the persisted `job.json` share these shapes. Conventions
(architecture-app.md §JSON & data-format conventions):

- snake_case field names on the wire AND in Python; no response envelope.
- `status`/`kind` are string `Enum`s — the single source of truth, never string
  literals elsewhere in the App.
- Timestamps are ISO-8601 UTC strings, never epoch numbers.
- `id` is a time-sortable opaque string so a plain directory listing (and the
  run library, later) orders by creation without a separate index.
- `interrupted` is NOT a status: it is `status=failed` + `failure_reason`
  (restart recovery lands in Story app-3-3; the field is defined here).

Only `setup` jobs are exercised in Story 1.3; the enums define the full set so
later stories (query kind, stopped, progress model) extend rather than redefine.
"""

from __future__ import annotations

import datetime
import enum
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class JobKind(enum.StrEnum):
    """The two job kinds the worker can run as subprocesses."""

    SETUP = "setup"
    QUERY = "query"


class JobStatus(enum.StrEnum):
    """Job lifecycle states (architecture-app.md §Category 5 / §data-format).

    `queued → running → {done | failed | stopped}`. `stopped` (hard cancel) is
    produced by Story 1.5; Story 1.3 drives only `queued → running → done|failed`.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"


class Phase(enum.StrEnum):
    """Coarse progress phase within a run (architecture-app.md §Category 3).

    `setup` = a build job's stages; `query`/`solve` = a query job's non-solve
    stages and its GRASP solve phase (Story 2.2). Story 1.4 emits only `setup`.
    """

    SETUP = "setup"
    QUERY = "query"
    SOLVE = "solve"


class AreaSpec(BaseModel):
    """Search area on the wire — mirrors `steeproute.models.Area` (center + bbox
    half-side km), kept as its own App-side model so nothing outside `cli_adapter`
    imports the CLI domain type."""

    center: tuple[float, float]
    radius_km: float


class SetupParams(BaseModel):
    """Setup-job parameters beyond the area. Minimal by design for v1 — the full
    click-introspected schema is Epic 2 (query). Field names map 1:1 onto
    `steeproute-setup` flags in `cli_adapter.argv`."""

    untagged_trails: Literal["include", "exclude"] = "include"
    force_refresh: bool = False
    dem_version: str | None = None


class JobCreate(BaseModel):
    """`POST /jobs` request body. `params` is validated against `SetupParams`;
    an invalid/missing `area`, `kind`, or param fails FastAPI/pydantic validation
    (422)."""

    kind: JobKind
    area: AreaSpec
    params: SetupParams = Field(default_factory=SetupParams)


class JobRecord(BaseModel):
    """The persisted job record — the sole contents of `job.json` and the wire
    shape returned by the job endpoints. Kind-agnostic: `params` holds the
    validated per-kind params as a plain dict so the record shape is stable
    across setup and (later) query."""

    id: str
    kind: JobKind
    area: AreaSpec
    params: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    result_dir: str | None = None
    failure_reason: str | None = None
    stdout_tail: list[str] = Field(default_factory=list)
    stderr_tail: list[str] = Field(default_factory=list)


class GraspProgress(BaseModel):
    """GRASP solver readout — populated only during a query's solve phase
    (Story 2.2), `null` on the `ProgressModel` otherwise."""

    iter: int
    best_cost: float


class ProgressModel(BaseModel):
    """The unified, flavour-agnostic progress snapshot (architecture-app.md
    §SSE event conventions). One is emitted per meaningful stdout line and
    persisted (append-only) to `progress.ndjson`; the SSE stream replays them.

    `stage_index`/`stage_total` are NOT parsed from stdout (the wire carries a
    stage name only) — the classifier derives them positionally from a known
    ordered stage list per job kind. `grasp` is present-as-`null`, never omitted;
    it is always `null` for setup jobs.
    """

    phase: Phase
    stage_name: str | None = None
    stage_index: int = 0
    stage_total: int = 0
    grasp: GraspProgress | None = None
    elapsed: float | None = None
    log_tail: list[str] = Field(default_factory=list)


def utcnow_iso() -> str:
    """Current time as an ISO-8601 UTC string (the App's only timestamp format)."""
    return datetime.datetime.now(datetime.UTC).isoformat()


def new_job_id() -> str:
    """A time-sortable opaque job id: zero-padded nanosecond timestamp + a short
    random suffix. Lexical order == creation order, so the per-job directory
    listing sorts chronologically with no separate index (§data-format)."""
    return f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}"
