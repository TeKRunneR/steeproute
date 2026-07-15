# Story 1.3: Job store and single-worker queue (setup jobs, curl-drivable)

Status: done

<!-- App track (epics-app.md). Story key `app-1-3-*` is `app-`-prefixed to avoid
     collision with the CLI track's `1-3-*`; both share sprint-status.yaml. -->

## Story

As a developer,
I want a persistent job store and a single-worker serial queue that spawns a `setup` subprocess,
so that a build job can be submitted, run one-at-a-time, and inspected without any UI.

## Acceptance Criteria

1. `POST /jobs` with `kind=setup` and an area returns HTTP 201 with the created job record (status `queued`), and persists it as an atomic `job.json` in its own per-job directory under the runtime job-store path. Invalid input (missing/bad area or kind) is rejected `422`.
2. A single worker coroutine, started in the FastAPI `lifespan`, pops the next queued job, builds argv via `cli_adapter`, spawns `steeproute-setup` as a **subprocess** (never in-process), and drives status `queued`→`running`→{`done`|`failed`} with `exit_code` recorded (exit-code map over {0, 1, 2, 130}).
3. A subprocess that exits non-zero marks the job `failed` with a tail of its stdout captured on the record, and the worker proceeds to the next queued job — one bad job never stalls the queue (the worker loop catches per-job exceptions too).
4. With multiple jobs submitted, exactly one runs at a time and the rest wait in submission order (concurrency = 1).
5. `GET /jobs` returns the registry and `GET /jobs/{id}` returns a single record (`404` for unknown id); both are snake_case with no response envelope, and the `status` field is the shared `JobStatus` enum value.
6. All CLI coupling (argv construction for `steeproute-setup`) lives only in `cli_adapter`; no other App module imports `steeproute.*` internals, hand-builds argv, or invokes the CLI in-process.
7. Scope guard: no SSE, no stdout→progress classification, no `progress.ndjson`, no `Stop`/`stopped` action, no `GET /regions`, no query kind, and no boot-time queue rebuild / restart recovery (each lands in its own later story — see Dev Notes). This story is curl/`TestClient`-drivable only.

## Tasks / Subtasks

- [x] Define the job domain models (AC: #1, #5)
  - [x] `steeproute/app/models.py`: `JobKind` enum (`setup | query`), `JobStatus` enum (`queued | running | done | failed | stopped`), a `JobRecord` (pydantic model) with `id, kind, area, params, status, created_at, started_at, finished_at, exit_code, result_dir, failure_reason` and a `stdout_tail` field. Full enums defined now (single source of truth) though only the `setup` + `queued|running|done|failed` subset is exercised.
  - [x] A minimal typed `SetupParams` (+ `AreaSpec` = center/radius_km, mirroring `steeproute.models.Area`). Full click-introspected schema deferred to Epic 2.
- [x] Per-job JSON store (AC: #1, #3, #5)
  - [x] `steeproute/app/store.py`: create/get/update/list over `<job_store>/<job_id>/job.json`; atomic writes (same-dir temp-file + `os.replace`); `list` orders by id (time-sortable id → creation order).
  - [x] Job-store root via `platformdirs.user_data_dir("steeproute")/app/jobs/`; root injectable so tests use a tmp dir.
- [x] Single-worker queue + worker loop (AC: #2, #3, #4)
  - [x] `steeproute/app/queue.py`: in-memory `asyncio.Queue` + one worker coroutine (pop → build argv via `cli_adapter` → `asyncio.create_subprocess_exec` → drain stdout into a bounded 50-line tail → await exit → terminal status + `exit_code`).
  - [x] Worker loop catches per-job exceptions and `OSError` spawn failures → mark `failed` + move on (crash-proof).
  - [x] Exit-code map: `0`→`done`; non-zero→`failed` (record `exit_code`). `130`/Stop deferred to Story 1.5.
- [x] The CLI-adapter argv seam (AC: #6)
  - [x] `steeproute/app/cli_adapter/__init__.py` (public interface) + `cli_adapter/argv.py`: validated `SetupParams`+`AreaSpec` → `steeproute-setup` argv; console script resolved via `shutil.which`. Only this package knows the flag names.
- [x] REST wiring (AC: #1, #5)
  - [x] `steeproute/app/api.py`: `POST /jobs` (201), `GET /jobs`, `GET /jobs/{id}` (404). Thin: parse → store/enqueue → serialize. `{detail}` errors via `HTTPException`.
  - [x] `main.py`: worker started in `lifespan` (replaced the placeholder), store/queue on `app.state`, `include_router(jobs_router)`; static/vendor mounts + home route unchanged.
- [x] Tests (AC: all)
  - [x] `tests/unit/test_app_store.py` (atomic create/get/update/list, ordering), `tests/unit/test_app_argv.py` (SetupParams → argv), `tests/unit/test_app_queue.py` (worker loop against a **fake CLI subprocess**: done / failed / poisoned-job / spawn-error / serial-ordering).
  - [x] `tests/integration/test_app_api.py` (extended): create→queue→run→done + failed-exit lifecycle via a fake CLI; `404`; `422` for query kind and bad area; snake_case / no-envelope shape.

## Dev Notes

**This is the backend-skeleton story** — step 2 of the architecture's implementation sequence (store + single-worker queue + subprocess spawn + `POST/GET /jobs`), built against the simpler `setup` flavour. Progress plumbing (classifier → SSE) is step 3 / Story 1.4; the UI is step 4+ [Source: architecture-app.md#Decision Impact Analysis]. Everything hangs off the skeleton Story 1.2 already stood up (`create_app()` factory, placeholder `lifespan`, static mounts) [Source: app-1-2-runnable-app-skeleton-with-static-shell.md; src/steeproute/app/main.py].

**The load-bearing rule — one CLI-adapter boundary.** All coupling to the CLI subsystem lives in `steeproute/app/cli_adapter/`; nothing else hand-builds argv, imports `steeproute.*` internals, or invokes the CLI. This story implements exactly one of the four seams: **argv construction** for `setup`. The other three (regions/coverage → 1.6, params-schema introspection → Epic 2, stdout classification → 1.4) are NOT built here [Source: architecture-app.md#The load-bearing rule; architecture-app.md#Complete project tree].

**Subprocess invocation model (Category 1).** `setup` runs only as a child process via `asyncio.create_subprocess_exec`, never imported in-process [Source: architecture-app.md#Category 1]. The installed console script is `steeproute-setup` [Source: pyproject.toml:94]. Recommended: resolve the script from the current environment (e.g. `shutil.which("steeproute-setup")`, resolved once in `cli_adapter`) rather than assuming bare `steeproute-setup` is on `PATH`; argv[0] is owned by `cli_adapter`. Do **not** add a `__main__` to the CLI (that is a CLI change, out of scope). Make the spawn point injectable so the queue tests can substitute a fake/echo command [Source: architecture-app.md#Development workflow — "App tests use FastAPI's TestClient and a fake/echo subprocess"].

**Setup argv shape.** `steeproute-setup --center <lat>,<lon> --radius <km>` are the only required flags [Source: src/steeproute/cli/setup.py:96-105; src/steeproute/cli/_shared.py:329-341]. `--center` is a single `LAT,LON` string (comma-joined), `--radius` is km. Optional setup flags exist (`--untagged-trails`, `--force-refresh`, `--dem-version`, `--dem-fetch-workers`, `--osm-age-warn-days`, `--cache-dir`, `--quiet`, `--verbose`) [Source: src/steeproute/cli/_shared.py:588-636] — expose only what `SetupParams` carries; keep it minimal for v1.

**Cache root — do NOT override `--cache-dir`.** Let `setup` write to the CLI's default cache root (`platformdirs.user_cache_dir("steeproute")` via `resolve_cache_root(None)`) [Source: src/steeproute/cache.py:336-349]. The App's green/grey overlay (Story 1.6) reads that same on-disk cache through `cli_adapter/regions.py`; passing a private `--cache-dir` would make built regions invisible to the overlay. Note the job **store** (`user_data_dir`) and the CLI **cache** (`user_cache_dir`) are two distinct roots — don't conflate them [Source: architecture-app.md#Runtime-resolved paths].

**Exit-code → status.** The CLI's shared policy emits {0, 1, 2, 130} — `PreExecutionError`→2, `KeyboardInterrupt`→130, success→0 [Source: src/steeproute/cli/_shared.py:51-62; src/steeproute/errors.py:8-9]. Map `0`→`done`, any non-zero→`failed`, always recording the raw `exit_code`. `130`/Stop is produced by the live cancel action in Story 1.5, not here.

**Data-format conventions (enforce even in the skeleton)** [Source: architecture-app.md#JSON & data-format conventions; #API conventions]:
- snake_case on the wire AND in Python; no response envelope (return the resource directly; errors are FastAPI `{detail}`).
- `status` is the `JobStatus` **Enum** — never a string literal. `interrupted` is NOT a status: it is `status=failed` + `failure_reason="interrupted"` (used by restart recovery, Story app-3-3 — define the field now, populate it there).
- Timestamps are ISO-8601 UTC strings (`created_at`, `started_at`, `finished_at`), never epoch numbers.
- Job `id` is a time-sortable opaque string (ULID or `uuid4` hex) so the per-job directory listing orders by creation without a separate index.
- Status codes: `201` create, `200` reads, `404` unknown id, `422` validation (FastAPI/pydantic default).

**Process patterns** [Source: architecture-app.md#Process patterns]:
- The worker loop **never dies on a bad job** — a subprocess failure or any per-job exception → `status=failed` + `exit_code` + stdout tail, then move to the next queued job.
- Two distinct output streams, never merged: the server's own stdlib `logging` (operational) vs. the job's scraped CLI stdout (data). This story only needs a **bounded stdout tail** for the failed-job diagnostic; the full append-only `progress.ndjson` + classification arrive in Story 1.4.
- Atomic `job.json` writes (temp-file + `os.replace`), same discipline as the CLI cache.

### Project Structure Notes

Target tree — this story creates the **starred** files (rest are later stories, shown for context) [Source: architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── main.py                ★ (edit) start worker in lifespan + include api router
├── models.py              ★ JobRecord, JobKind, JobStatus, SetupParams, area
├── store.py               ★ per-job JSON persistence (atomic job.json, list)
├── queue.py               ★ single-worker asyncio queue + worker loop
├── cli_adapter/
│   ├── __init__.py        ★ public typed interface
│   └── argv.py            ★ SetupParams → steeproute-setup argv
├── api.py                 ★ POST/GET /jobs
├── sse.py / progress model / cli_adapter/{progress_parse,regions,params_schema}.py   (Stories 1.4/1.6/2.x — NOT now)
└── static/                (unchanged from 1.2)
```

- Package path is `steeproute.app` (a subpackage of the existing distribution), NOT `steeproute_app` — the earlier architecture §Initialization mention is superseded [Source: architecture-app.md#Project Structure & Boundaries; app-1-2 Dev Notes].
- `api.py` orchestrates `store` + `queue`; the worker (`queue.py`) uses `cli_adapter` + `store`; `cli_adapter` is imported only through its public interface [Source: architecture-app.md#Architectural Boundaries].

### Testing

Per AGENTS.md: `uv run basedpyright <files>`; tests per-directory (never mix `tests/unit` and `tests/integration` in one invocation). App tests use FastAPI's `TestClient` and a **fake/echo subprocess** — no real solver/network run for unit/integration coverage [Source: architecture-app.md#Development workflow]. A fake CLI can be a tiny `python -c "..."` (or `sys.executable` + a script) that prints canned lines and exits with a chosen code, letting the queue tests cover both the `done` (exit 0) and `failed` (exit non-zero, stdout tail captured) paths and the serial-ordering guarantee deterministically. The existing `tests/integration/test_app_api.py` (5 skeleton smoke tests) stays green — extend it, don't replace it [Source: app-1-2 File List].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 1.3: Job store and single-worker queue] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 1 — CLI invocation model] — subprocess, never in-process
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 2 — Worker & concurrency] — single asyncio worker in lifespan, concurrency=1
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 5 — Job persistence & store] — per-job JSON, job-record fields, atomic writes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 8 — API surface] — endpoints + status codes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Implementation Patterns & Consistency Rules] — CLI-adapter boundary, snake_case/no-envelope/Enum, process patterns
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree] — file layout + runtime-resolved job-store path
- [Source: src/steeproute/cli/setup.py:91-118] — `steeproute-setup` option surface (argv target)
- [Source: src/steeproute/cli/_shared.py:51-62, 329-341, 595-609] — exit-code policy; `--center`/`--radius`/`--cache-dir` option defs
- [Source: src/steeproute/cache.py:336-349] — `resolve_cache_root` / default `user_cache_dir` (why not to override `--cache-dir`)
- [Source: src/steeproute/app/main.py] — the skeleton to extend (factory, lifespan, mounts)
- [Source: tests/integration/test_app_api.py] — existing App test module to extend
- [Source: pyproject.toml:44, 91-97] — `platformdirs>=4` already a dep; `[project.scripts]` names

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Async testing without a plugin.** The repo has no `pytest-asyncio`/`anyio` test config, so the worker unit tests wrap each scenario in `asyncio.run(...)` (main-thread loop → real `create_subprocess_exec` works everywhere). The API integration tests drive the worker through FastAPI's `TestClient` **as a context manager** (`with TestClient(app) as client:`), which runs `lifespan` and thus the worker task in the client's background loop; the test polls `GET /jobs/{id}` until terminal. Real subprocesses spawn fine from that loop on Windows (ProactorEventLoop).
- **`which` returns uppercase `.EXE` on this machine.** `shutil.which("steeproute-setup")` resolved to `…/Scripts/steeproute-setup.EXE`; the argv unit test matches the executable stem case-insensitively.
- **basedpyright `@final`.** Store/queue/worker classes are `@final` to satisfy `reportUnannotatedClassAttribute` (0/0 on `src/steeproute/app`). The integration test extends the existing per-file httpx-boundary pyright relaxation with `reportUnknownArgumentType=false` (`.json()` returns Unknown).

### Completion Notes List

- **Backend skeleton stood up** on top of Story 1.2's shell: `models.py` (JobKind/JobStatus enums, AreaSpec/SetupParams/JobCreate/JobRecord, `new_job_id` = zero-padded `time_ns` + short uuid → time-sortable, `utcnow_iso`), `store.py` (atomic per-job `job.json`, id-ordered list), `queue.py` (`JobQueue` over `asyncio.Queue` + serial `Worker`), `cli_adapter/{__init__,argv}.py` (setup argv seam only), `api.py` (`POST/GET /jobs`), and `main.py` wiring the worker into `lifespan` via an injectable `_make_lifespan`.
- **CLI-adapter boundary respected (AC #6):** only `cli_adapter/argv.py` knows `steeproute-setup` flag names or resolves the console script; nothing else imports `steeproute.*` internals or hand-builds argv. `create_app(store_root=…, build_argv=…)` injection keeps tests off the real CLI/network while exercising the real store + real subprocess spawn/drain/exit path.
- **Deliberately NOT passing `--cache-dir`:** setup writes to the CLI default cache root so Story 1.6's region overlay will see built regions (architecture-app.md §Category 6).
- **Scope held (AC #7):** no SSE, no stdout→progress classification / `progress.ndjson`, no Stop/`stopped`, no `/regions`, no query kind (rejected 422), no boot-time queue rebuild / restart recovery. A bounded 50-line stdout tail is captured for the failed-job diagnostic only.
- **Validation:** `basedpyright src/steeproute/app` 0/0; `ruff` clean. Full offline suite **944 passed, 17 deselected** (~1m53s), no regressions (917 baseline → 944, +27 new App tests: 7 argv + 6 store + 7 queue + 7 api, alongside the 5 retained 1.2 smoke tests).
- **Code-review fixes (low-effort diff pass, 2 findings, both fixed):**
  1. *stderr deadlock (queue.py):* the worker piped both stdout and stderr but drained only stdout — a child writing enough to stderr to fill its pipe buffer (~64 KB) would block while the worker waited for stdout EOF, hanging the whole (concurrency=1) queue. Fixed by draining both concurrently via `asyncio.gather` + a shared `_drain` helper; stderr is now also captured into a new `stderr_tail` field (the CLI writes `error: …` to stderr, so it's the useful diagnostic on a failed job). Regression: `test_large_stderr_does_not_deadlock`.
  2. *orphaned child + stuck `running` on shutdown (queue.py/main.py):* lifespan cancellation cancelled the worker coroutine but never killed the in-flight subprocess, orphaning it and leaving `job.json` at `status=running` forever. Fixed by catching `CancelledError` across the whole spawn/drain/wait region, `proc.kill()`-ing the child, and recording the interrupted terminal state (`status=failed` + `failure_reason="interrupted"`, the same state Story app-3-3's restart recovery will produce). Regression: `test_shutdown_interrupts_running_job_and_kills_child`.

### File List

- `src/steeproute/app/models.py` (new) — JobKind/JobStatus enums, AreaSpec/SetupParams/JobCreate/JobRecord (incl. `stderr_tail`), id + timestamp helpers
- `src/steeproute/app/store.py` (new) — per-job JSON store, atomic writes, id-ordered list
- `src/steeproute/app/queue.py` (new) — JobQueue + single serial Worker (subprocess spawn, stdout tail, exit-code map, crash-proof)
- `src/steeproute/app/cli_adapter/__init__.py` (new) — CLI-adapter public interface
- `src/steeproute/app/cli_adapter/argv.py` (new) — SetupParams+AreaSpec → steeproute-setup argv
- `src/steeproute/app/api.py` (new) — POST/GET /jobs, GET /jobs/{id}
- `src/steeproute/app/main.py` (modified) — worker in lifespan, store/queue on app.state, include jobs router, injectable create_app
- `tests/unit/test_app_argv.py` (new) — argv seam mapping
- `tests/unit/test_app_store.py` (new) — store round-trips + atomicity + ordering
- `tests/unit/test_app_queue.py` (new) — worker loop (done/failed/poisoned/spawn-error/serial)
- `tests/integration/test_app_api.py` (modified) — job-lifecycle API tests added alongside the 1.2 smoke tests
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-15 | Story drafted from epics-app.md + architecture-app.md. Status → ready-for-dev. |
| 2026-07-15 | Implemented job store + single-worker queue + setup-argv adapter + `POST/GET /jobs`; 25 new tests; full suite green (942). Status → review. |
| 2026-07-15 | Code review (low effort): fixed 2 findings — stderr-drain deadlock and orphaned-child/stuck-`running` on shutdown; added `stderr_tail` + 2 regression tests. Full suite green (944). |
| 2026-07-15 | Code review findings resolved and verified. Status → done. |
