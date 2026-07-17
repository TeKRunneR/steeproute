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
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    `steeproute-setup` flags in `cli_adapter.argv`.

    `extra="forbid"` so a body carrying `QueryParams`-shaped fields under
    `kind=setup` fails 422 instead of silently ignoring them (JobCreate's
    kind-dispatch below relies on this to actually reject a mismatched body)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    untagged_trails: Literal["include", "exclude"] = "include"
    force_refresh: bool = False
    dem_version: str | None = None


class QueryParams(BaseModel):
    """Query-job parameters beyond the area (App Story 2.1). Field names/types
    mirror the subset of `steeproute` CLI flags `cli_adapter.params_schema`
    exposes on the form (excludes area/output/verbosity, which the App owns).

    Every field defaults to `None` — "unset, use the App's actual default" —
    so this model never hand-duplicates a default value; `cli_adapter.
    params_schema.resolve_query_defaults()` is the single place that resolves
    `None` to the quality-demo value (AGENTS.md) or the CLI's own default, and
    `cli_adapter.argv.build_query_argv` is the only consumer of that
    resolution. `extra="forbid"` — see `SetupParams`."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    theta: float | None = None
    min_climb_slope: float | None = None
    difficulty_cap: Literal["T1", "T2", "T3", "T4", "T5", "T6"] | None = None
    l_connector: float | None = None
    min_climb_ground_length: float | None = None
    elevation_smoothing: float | None = None
    elevation_deadband: float | None = None
    j_max: float | None = None
    start_at_junction: bool | None = None
    max_descent_slope: float | None = None
    n: int | None = None
    area_cap: float | None = None
    untagged_trails: Literal["include", "exclude"] | None = None
    seed: int | None = None
    iter_budget: int | None = None
    time_budget: float | None = None
    stagnation_iters: int | None = None
    workers: int | None = None
    merge_interval: int | None = None
    progress_interval: float | None = None
    osm_age_warn_days: int | None = None


class JobCreate(BaseModel):
    """`POST /jobs` request body. `params` is validated against `SetupParams`
    (kind=setup) or `QueryParams` (kind=query); an invalid/missing `area`,
    `kind`, or param fails FastAPI/pydantic validation (422).

    The `kind`→params-model dispatch happens in `_coerce_params` (mode=`before`,
    so it runs ahead of pydantic's own `SetupParams | QueryParams` union
    resolution): the raw `params` dict is parsed against whichever model
    matches the sibling `kind` field, so a mismatched or malformed body for the
    given kind fails 422 rather than being silently coerced into the wrong
    model or accepted with fields ignored."""

    kind: JobKind
    area: AreaSpec
    params: SetupParams | QueryParams = Field(default_factory=SetupParams)

    @model_validator(mode="before")
    @classmethod
    def _coerce_params(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = cast("dict[str, Any]", data)
        model = QueryParams if raw.get("kind") == JobKind.QUERY else SetupParams
        raw_params: Any = raw.get("params")
        params_obj: SetupParams | QueryParams
        if raw_params is None:
            params_obj = model()
        elif isinstance(raw_params, dict):
            params_obj = model.model_validate(raw_params)
        else:
            params_obj = raw_params
        return {**raw, "params": params_obj}


class JobRecord(BaseModel):
    """The persisted job record — the sole contents of `job.json` and the wire
    shape returned by the job endpoints. Kind-agnostic: `params` holds the
    validated per-kind params as a plain dict so the record shape is stable
    across setup and (later) query."""

    id: str
    kind: JobKind
    area: AreaSpec
    params: dict[str, Any] = Field(default_factory=dict)
    # A human place label for the run — a nearby town/place name best-effort
    # reverse-geocoded from `area.center` at creation (App Story 4.3, `app.geocode`).
    # `None` when geocoding is disabled, offline, or found no place; the run
    # library then falls back to the coordinate display. Additive: a `job.json`
    # written before this field existed loads with `area_label=None`.
    area_label: str | None = None
    status: JobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    result_dir: str | None = None
    # The finished query's run-summary `total_objective` (App Story 3.1),
    # captured by the worker at completion so the run-library card shows a
    # done query's cost without re-parsing route JSON. `None` for setup jobs,
    # non-terminal jobs, and a query that produced no routes.
    result_objective: float | None = None
    failure_reason: str | None = None
    stdout_tail: list[str] = Field(default_factory=list)
    stderr_tail: list[str] = Field(default_factory=list)


class RegionBounds(BaseModel):
    """WGS84 lat/lon bbox corners of a prepared region (`south`/`west`/`north`/
    `east` degrees). Precomputed server-side from the CLI cache's shared km→deg
    conversion (`steeproute.cache.area_bbox_wgs84`) so the Leaflet overlay and
    the green/grey containment test use exact geometry — the frontend renders
    and tests against these, never re-deriving km→deg."""

    south: float
    west: float
    north: float
    east: float


class RegionInfo(BaseModel):
    """A built (prepared) region for the map overlay (`GET /regions`,
    architecture-app.md §Category 6). Mirrors the cache's per-entry coverage
    view: the entry hash, the area center, its bbox half-side, and the
    precomputed WGS84 bbox. snake_case; App-side type so nothing outside
    `cli_adapter` imports the CLI `Area`."""

    cache_key_hash: str
    center: tuple[float, float]
    radius_km: float
    bounds: RegionBounds


class AreaResolution(BaseModel):
    """Server-computed resolution of a candidate selection (`GET /regions/resolve`).

    The map home sends a picked `center` + `radius_km`; the server returns the
    exact WGS84 `bounds` (via `steeproute.cache.area_bbox_wgs84`) and the
    green/grey decision (`covered`, plus the containing entry's `cache_key_hash`)
    from the CLI cache's own containment (`cache.find_covering_entry`). This keeps
    ALL km→deg + containment on the server — the frontend never re-derives either,
    so its overlay can't drift from the query-side coverage check."""

    center: tuple[float, float]
    radius_km: float
    bounds: RegionBounds
    covered: bool
    cache_key_hash: str | None = None


class RouteInfo(BaseModel):
    """One route report a done query produced (App Story 2.3): the CLI's
    `route-<index>.html` file. `index` is the 1-based route number parsed from
    the filename server-side, so the S5 selector labels routes without
    re-parsing the filename in JS."""

    index: int
    filename: str


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
