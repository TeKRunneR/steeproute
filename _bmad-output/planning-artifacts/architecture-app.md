---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
inputDocuments:
  - _bmad-output/brainstorming/brainstorming-session-2026-07-14-1437.md
  - _bmad-output/planning-artifacts/ux-design-specification.md
  - _bmad-output/planning-artifacts/future-ideas.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/prd.md
workflowType: 'architecture'
project_name: 'steeproute web App'
user_name: 'Yann'
date: '2026-07-14'
lastStep: 8
status: 'complete'
completedAt: '2026-07-14'
subsystem: 'web-app'
relatedArchitecture: '_bmad-output/planning-artifacts/architecture.md'
---

# Architecture Decision Document — steeproute web App

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

_Scope: the **web App** subsystem — a thin FastAPI + HTML/JS/Leaflet UI whose heart is a single-worker backend job runner over the two existing CLIs (`steeproute-setup` + `steeproute`). The CLI subsystem's own architecture is settled in [`architecture.md`](architecture.md); this document cross-references it at the interface (subprocess contract, stdout formats, output-dir reuse) and does not re-open it._

## Project Context Analysis

### Requirements Overview

**Requirements source:** There is no App-specific PRD. Requirements are the
*settled decisions* recorded in the brainstorming session (2026-07-14) and the
lean UX design spec. Both are treated here as the authoritative baseline; the
main `prd.md` governs the CLI subsystem this App wraps, not the App itself.

**Functional capabilities (derived from brainstorm + UX spec):**

- **Area selection & cache visibility** — full-bleed Leaflet map; drop center,
  drag radius; cached regions shown as green (instant query) / grey (needs build)
  overlays. (UX S1; brainstorm Phase-1 ①②)
- **Deliberate two-step orchestration** — an uncached area offers "Build this
  region" (a `setup` job); only a built/green area offers "Configure query" (a
  `query` job). Never auto-chained. (brainstorm B1; UX F2)
- **Config form** — basic/advanced split with ALL CLI flags accessible; App
  defaults = the *quality* demo params (not the low CLI defaults). (brainstorm
  Phase-1 ③; UX S2)
- **Job submission & serial queue** — one worker, one run at a time; multiple
  jobs may be queued and execute in order. Two job kinds: `setup`, `query`.
  (brainstorm concurrency decision; UX F4)
- **Live progress & return-to-live** — fire-and-forget; a persistent live-job
  indicator on every screen links back to the running job's progress at any time;
  no blocking modal. (brainstorm fire-and-forget; UX S3/F3)
- **Unified progress surface** — one Run-watch screen renders three stdout
  flavours through one model: phase + stage(n/total) + log tail + (GRASP-only)
  best-so-far cost & iteration. (brainstorm B2; UX S3)
- **Stop = best-so-far flush** — mirrors CLI Ctrl-C; partial routes still render.
  (brainstorm Phase-1 ⑤; UX F6)
- **Result view** — reuse the existing CLI Leaflet HTML report, embedded in an
  iframe as-is. (brainstorm Phase-1 ⑥; UX S5)
- **Run library** — the job registry rendered directly: running → queued (in
  order) → history; open finished run (iframe), re-run-with-tweaks (prefill config
  from stored params), cancel queued. (brainstorm Phase-1 ⑦/emergent theme; UX S4)
- **Restart recovery** — on server boot, any job left `running` is marked
  `failed (interrupted)`. (brainstorm B3 edge; UX F7)

**Non-Functional Requirements (mostly inherited):**

- **Concurrency = 1** — the solver is built to saturate all compute and its
  thread-safety across parallel runs is unintended; the single-worker serial
  queue is a hard constraint, not a simplification.
- **Long-running jobs** — a query can approach the CLI's ~10-min soft budget; the
  UI and transport must tolerate multi-minute streams and reconnection.
- **Single-user / local / no-auth** — inherits the CLI's N=1 posture: no security,
  multi-tenancy, accessibility, or responsive/mobile requirements (explicitly out
  of scope in the UX spec).
- **CLI-honest progress** — no fabricated bars/ETAs in v1; scraped stdout surfaced
  faithfully.
- **Thinness / low ceremony** — no frontend build step (plain HTML/JS/Leaflet);
  minimal backend ceremony (FastAPI).

**Scale & Complexity:**

- Primary domain: local single-user web app + subprocess-orchestration backend.
- Complexity level: medium — low ecosystem complexity, with implementation weight
  concentrated in the job runner and stdout→progress plumbing.
- Estimated architectural components: ~6–7 (API layer, job queue + single worker,
  subprocess spawn + stdout capture, progress classifier + model, SSE hub,
  job/persistence store, static frontend).

### Technical Constraints & Dependencies

**Settled in the brainstorm (inputs, not open questions):**

- **Stack:** FastAPI (Python) backend; plain HTML + JS + Leaflet frontend, no
  build step. Python parity with the CLIs means the worker can spawn them as
  subprocesses OR (open) import internals directly.
- **Transport:** SSE (server→client one-way) for progress.
- **Persistence:** per-job JSON files to start; the job registry doubles as the
  runs index. SQLite deferred until the library grows.
- **Progress ingestion:** scrape CLI stdout as-is (three flavours); do NOT teach
  the CLIs to emit structured JSON for v1. Accepted tradeoff: couples the App to
  stdout formatting; structured-emit is the fallback if scraping gets painful.

**Interface with the CLI subsystem (the load-bearing boundary):**

- Invocation of `steeproute-setup` and `steeproute` as subprocesses; consumption
  of their stdout progress and exit codes {0, 1, 2, 130}.
- Cache-coverage detection to drive the green/grey region overlays (the App must
  know what `steeproute-setup` has already built without running a query).
- Reuse of the per-run output dir (HTML report + JSON sidecar) for the result view.
- See [`architecture.md`](architecture.md) for the CLI-side contracts these depend on.

### Cross-Cutting Concerns Identified

- **Unified progress model** — spans three stdout sources, the SSE hub, and the UI;
  the single riskiest concern (brainstorm's "crux"). Gated by a stdout
  **format-inventory spike** as the first implementation task.
- **Job lifecycle state machine** — queued → running → {done | failed | stopped},
  plus the boot-time running→failed(interrupted) transition; shared by the worker,
  the store, the SSE hub, and both list/watch screens.
- **CLI subprocess contract** — invocation, stdout capture, exit-code→status
  mapping, cache-coverage probing; the seam between App and CLIs.
- **Persistence layout** — per-job records + accumulated progress; readable by a
  reconnecting client (snapshot-then-tail) and by the run library.
- **Deferred (Cluster D) open questions** — offer-to-build vs. block on uncached
  area; run-card field set; queue reorder/cancel UX. Flagged for resolution when
  implementation reaches them; not v1-blocking.

## Starter Template Evaluation

### Primary Technology Domain

Local single-user web app + subprocess-orchestration backend, added as a
**new subsystem inside the existing `steeproute` repository** (not a new
project). The repo is already scaffolded from `simple-modern-uv` (uv-managed,
ruff + basedpyright + pytest configured) — see the CLI [`architecture.md`](architecture.md).

### Starter Options Considered

1. **Extend the existing project (no starter)** — add a FastAPI app package
   and a static frontend dir to the current uv project; reuse its tooling,
   lint/type/test config, and the already-vendored Leaflet assets.
2. **FastAPI full-stack boilerplate** (e.g. `full-stack-fastapi-template`) —
   rejected: ships SQLAlchemy/Alembic/Postgres/Docker/JWT-auth/React, all of
   which contradict the settled thin, buildless, per-job-JSON, single-user,
   no-auth design. Would be almost entirely deleted on arrival.
3. **Frontend framework starter** (Vite/Next/SvelteKit) — rejected: the
   frontend is deliberately buildless plain HTML/JS/Leaflet. A build step and
   framework runtime are explicit non-goals.

### Selected Starter: None — extend the existing uv project

**Rationale for Selection:**
The App is additive to a repo that already made every scaffolding decision a
starter would (packaging, dependency management, lint, type-check, test). The
settled stack is small enough that "add a dependency and write the app module"
is the whole setup. Adopting a starter would import unwanted infrastructure and
fight the thin-wrapper goal. FastAPI itself has no canonical `create` command
worth adopting; the idiomatic start is a bare app module.

**Verified current versions (2026-07):** FastAPI `0.136.x` (install as
`fastapi[standard]`, which bundles the server); Uvicorn `~0.42.0`; Leaflet
`1.9.4` stable (2.0 still alpha — avoid). Reuse the Leaflet copy already
vendored by the CLI HTML report rather than adding a new asset dependency.

**Initialization ("first implementation story"):**

```bash
# add runtime deps to the existing uv project (no new repo, no scaffold)
uv add "fastapi[standard]"
# uvicorn is pulled in by fastapi[standard]; run the app in dev with:
#   uv run fastapi dev src/steeproute_app/main.py
```

- Frontend: a plain static directory served by FastAPI (`StaticFiles`) — no
  npm, no bundler. Leaflet + CSS copied from the assets the CLI HTML report
  already vendors (no CDN at runtime).

**Architectural Decisions Provided (inherited from the existing repo):**

- **Language & Runtime:** Python (existing modern version), single uv-managed
  project; the App package sits alongside the CLI packages.
- **Styling Solution:** none / hand-rolled CSS — no design system (out of scope
  per the UX spec).
- **Build Tooling:** none for the frontend (buildless); uv for Python.
- **Testing Framework:** existing `pytest` setup (per-directory conftest
  discipline already documented in AGENTS.md).
- **Lint/Type:** existing ruff + basedpyright config.
- **Code Organization:** a new `steeproute_app` package (API, job runner,
  progress, store) + a `static/` frontend dir — structure refined in later steps.

**Note:** The first implementation story is adding the FastAPI dependency and a
minimal runnable app skeleton within the existing project — not scaffolding a
new repo.

## Core Architectural Decisions

### Decision Priority Analysis

**Critical (block implementation):** CLI invocation model; worker/concurrency
execution model; progress ingestion + unified model; job persistence + restart
recovery; cache-coverage detection; SSE transport.

**Important (shape architecture):** API surface; config/params model; Stop/cancel
semantics; frontend structure + live-job indicator.

**Deferred (Cluster D, non-blocking):** offer-to-build vs. block on uncached area;
run-card field set; queue reorder/cancel UX; SQLite migration if the run library
outgrows per-job JSON.

### Category 1 — CLI invocation model: subprocess

`setup` and `query` run as **child processes** (`asyncio.create_subprocess_exec`),
never imported in-process. Rationale: the entire progress design is stdout
scraping, which presupposes a process boundary; process isolation also protects
the server from a solver crash/OOM. Import-internals is the recorded, rejected v1
alternative — and the fallback if stdout scraping proves too brittle.

### Category 2 — Worker & concurrency: single asyncio worker in lifespan

One worker coroutine started in FastAPI's `lifespan`. Loop: pop next queued job
→ spawn subprocess → async-read stdout line-by-line → classify → append to the
job's progress log + push to SSE → set terminal status. **Concurrency = 1** is a
hard constraint (the solver saturates all cores; cross-run thread-safety is
unintended). No worker threads: only subprocesses do heavy compute, so the App
has no shared-state concurrency. The queue is an in-memory `asyncio.Queue`,
rebuilt from the persisted store on boot.

### Category 3 — Progress ingestion & unified model

A **line classifier** maps raw stdout from all three flavours (GRASP
`ProgressEvent`s, query non-solve stage timing, setup stages) into one
flavour-agnostic model: `{phase, stage_name, stage_index, stage_total,
grasp:{iter, best_cost}|null, elapsed, log_tail[]}`. The GRASP block is populated
only during a query's solve phase; null otherwise. **First implementation task is
the stdout format-inventory spike** (capture real setup + query runs at demo
params, pin the line shapes) before writing the classifier — the brainstorm's
identified crux/only-real-risk. Coupling to stdout formatting is the accepted
tradeoff; structured-emit from the CLIs is the escape hatch.

### Category 4 — Transport: FastAPI native SSE, snapshot-then-tail

Progress reaches the browser over **SSE via FastAPI's built-in
`EventSourceResponse`** (native since 0.135; no `sse-starlette` dependency). On
connect the endpoint replays the persisted progress snapshot, then attaches the
live tail (satisfies return-to-live, F3). Periodic heartbeat comments keep
multi-minute idle streams from timing out. One-way server→client only.

### Category 5 — Job persistence & store

**Per-job JSON**, one directory per job: `job.json` (the record) + an
append-only `progress.ndjson` (the accumulated progress log that powers
snapshot-then-tail). Job record:
`{id, kind:setup|query, params, area:{center,radius}, status, created_at,
started_at, finished_at, exit_code, result_dir|null}`. Writes are atomic
(temp-file + rename, matching the CLI cache discipline). The store IS the runs
library — no separate index. **Restart recovery:** on boot any job left `running`
is marked `failed (interrupted)`. SQLite deferred until the library grows.

### Category 6 — Cache-coverage detection: read the CLI cache manifest

The green/grey overlay is driven by the App **reading the CLI's on-disk cache
manifest/metadata directly** (a `GET /regions` endpoint returns built areas).
Accepted cost: this couples the App to the cache's internal layout. **Mitigation:**
isolate all cache-manifest reading behind a single adapter module so a
cache-format change touches exactly one place; the CLI cache-key/layout contract
it depends on lives in the CLI [`architecture.md`](architecture.md) (Category 4 —
Cache architecture). Chosen over an App-only job-store (which would be blind to
regions built by invoking the CLI directly) and over adding a CLI coverage
command (avoids expanding the CLI surface for v1).

### Category 7 — Stop / cancel semantics: hard cancel

**Stop = terminate the subprocess (no best-so-far flush).** A stopped job has no
result. This is a deliberate deviation from the brainstorm's "best-so-far flush"
(Phase-1 ⑤) and the UX spec's "View routes (partial)" affordance (S3/S4/F6),
chosen to avoid a CLI change and Windows child-signalling fragility. **Ripples:**
a `stopped` job exposes no "View routes" action; the S3/S4 "(partial)" states are
dropped. Queued-but-not-started jobs are cancelled by removing them from the
store/queue (`DELETE /jobs/{id}`); reorder remains deferred (Cluster D).

### Category 8 — API surface

REST + one SSE endpoint, all local/unauthenticated:
- `POST /jobs` — enqueue a `setup` or `query` job (kind, area, params).
- `GET /jobs` — list all jobs (running → queued → history) for the run library.
- `GET /jobs/{id}` — one job record (snapshot).
- `GET /jobs/{id}/events` — SSE progress stream (snapshot-then-tail).
- `POST /jobs/{id}/stop` — hard-cancel a running job.
- `DELETE /jobs/{id}` — cancel a queued job.
- `GET /regions` — built-region list for the map overlay.
- Static mounts — the frontend, and the per-run output dirs for iframe result views.

### Category 9 — Config / params model

**One flag schema is the single source of truth**, used to (a) render the
basic/advanced form, (b) validate submitted params, and (c) build the subprocess
argv. **Recommendation: derive the schema from the CLIs' own argument parser**
(introspect, don't hand-duplicate) so the form cannot drift from the real flags.
App defaults = the quality demo params (`--iter-budget 200000 --stagnation-iters
10000 --difficulty-cap T4 --elevation-deadband 1`, per AGENTS.md), not the low
CLI defaults. Basic row = common knobs; advanced = the collapsed full set.

### Category 10 — Frontend architecture

Buildless static pages (plain HTML/JS/Leaflet, no bundler). A persistent header
carries the **live-job indicator** (subscribes to the active job's SSE / falls
back to polling `GET /jobs`), making return-to-live real without a modal. Flat
navigation: Map home, Run library, active Run watch; Config and Result are
reached through those. Result view embeds the existing CLI HTML report in an
`<iframe>` — no native re-render. Leaflet assets reused from the CLI report's
vendored copy.

### Decision Impact Analysis

**Implementation sequence (from the brainstorm build order):**
1. Stdout format-inventory spike (de-risks Category 3).
2. Backend skeleton — store + single-worker queue + subprocess spawn +
   `POST/GET /jobs` (Categories 1, 2, 5, 8).
3. Progress plumbing — classifier → model → SSE (Categories 3, 4).
4. Frontend run+watch — config form → live progress → return-to-live
   (Categories 9, 10).
5. Map picker + cached-region overlay + build button (Categories 6, 10).
6. Run library — list / iframe open / re-run-with-tweaks (Categories 8, 10).

**Cross-component dependencies:**
- Categories 3+4+5 are one triangle: the progress log is written by the worker
  (2), persisted by the store (5), and replayed by the SSE endpoint (4).
- Category 6 (cache manifest) and Category 9 (CLI arg introspection) both bind to
  CLI internals → both isolated behind adapter modules, both cross-referencing the
  CLI `architecture.md` so a CLI change has a known blast radius.
- Category 7 (hard cancel) removes the partial-result paths the UX spec drew;
  the structure/validation steps must reflect `stopped → no result`.

## Implementation Patterns & Consistency Rules

_General Python/tooling conventions are inherited from the existing repo — see the
CLI [`architecture.md`](architecture.md) "Implementation Patterns & Consistency
Rules". Only App-specific patterns are defined here._

### The load-bearing rule: one CLI-adapter boundary

All coupling to the CLI subsystem lives in a single `cli_adapter` package —
**nothing else in the App imports CLI internals, reads the cache layout, knows a
stdout line format, or hand-builds argv.** It owns exactly four seams:
1. **argv construction** from a validated params object,
2. **cache-manifest reading** for `GET /regions` (Category 6),
3. **params-schema introspection** from the CLI arg parser (Category 9),
4. **stdout line classification** into the progress model (Category 3).

Rationale: Categories 6 and 9 knowingly bind to CLI internals; confining them to
one package makes a CLI change a one-file blast radius and keeps the rest of the
App testable against the adapter's typed interface, not real subprocesses.

### JSON & data-format conventions

- **snake_case everywhere** — Python fields AND JSON on the wire. No camelCase
  translation layer; the plain-JS frontend reads snake_case directly, matching
  the CLI's JSON sidecar convention.
- **No response envelope** — endpoints return the resource directly (a job is
  `{...}`, not `{data: {...}}`). Errors use FastAPI's default `{detail: ...}`
  via `HTTPException`.
- **Timestamps** — ISO-8601 UTC strings (`created_at`, `started_at`, …), never
  epoch numbers.
- **Enums are the single source of truth** — job `status` is a Python `Enum`
  with values `queued | running | done | failed | stopped`, serialized as its
  string value. `interrupted` is NOT a separate status: it is `status=failed`
  plus a `failure_reason: "interrupted"` field (restart recovery, F7).
- **Job id** — a time-sortable opaque string (ULID or `uuid4` hex) so the per-job
  directory listing orders by creation without extra indexing.

### API conventions

- Lowercase plural-noun collections (`/jobs`, `/regions`); `{id}` path params;
  actions as sub-resources (`POST /jobs/{id}/stop`), not query verbs.
- Status codes: `201` on job create, `200` on reads, `404` unknown id, `409` for
  illegal transitions (e.g. stopping a non-running job), `422` for FastAPI/pydantic
  validation failures.

### SSE event conventions

- Named events on the one progress stream: `event: progress` (data = the progress
  model JSON), `event: status` (data = a terminal/transition status), and a
  periodic heartbeat **comment** line (`: keepalive`) — not a named event.
- The progress model is one dataclass with pinned field names (Category 3):
  `{phase, stage_name, stage_index, stage_total, grasp, elapsed, log_tail}`;
  `grasp` is `null` outside a query solve phase — never omitted, always present-as-null.

### Process patterns

- **The worker loop never dies on a bad job.** A subprocess failure → job
  `status=failed`, record `exit_code` + a tail of stdout; the worker moves to the
  next queued job. One poisoned job must not stall the queue.
- **Two distinct output streams, never merged:** the server's own stdlib
  `logging` (operational) vs. a job's scraped CLI progress log (`progress.ndjson`).
  Scraped CLI stdout is data, not server logging.
- **Atomic persistence** — every `job.json` write is temp-file + `os.replace`
  (same discipline as the CLI cache); `progress.ndjson` is append-only.

### Frontend conventions

- Vanilla ES modules, no framework, no bundler. `fetch` for REST; `EventSource`
  for SSE. One shared `api.js` wraps all endpoint calls; no other file hardcodes
  a URL. No inline event handlers; state is local and minimal (the server is the
  source of truth, re-fetched/streamed rather than mirrored).
- Frontend files kebab-case (`run-watch.js`); Python modules snake_case.

### Enforcement

**All implementers MUST:** route every CLI touch through `cli_adapter`; use the
`status` Enum (never string literals); keep JSON snake_case; write `job.json`
atomically; ensure the worker catches per-job exceptions. These are the five
places divergence would actually break interop.

## Project Structure & Boundaries

The App is a **subpackage inside the existing distribution** (`src/steeproute/app/`),
not a separate package — this lets the CLI-adapter import `steeproute.cache`
(coverage check already exists) and `steeproute.cli._shared` (click options) for
its in-process reads, while `setup`/`query` themselves still run only as
subprocesses (Category 1).

### Complete project tree (App additions only)

```
src/steeproute/
├── ... (existing CLI package — unchanged; see architecture.md) ...
├── templates/assets/                 # existing Leaflet 1.9.4 — REUSED, not re-vendored
│   ├── leaflet-1.9.4.min.js          #   served to the App frontend via a static mount
│   └── leaflet-1.9.4.min.css
└── app/                              # NEW — the web App subsystem
    ├── __init__.py
    ├── main.py                       # FastAPI factory + lifespan (starts worker) + static mounts;
    │                                 #   `steeproute-app` entry point
    ├── api.py                        # REST + SSE routes — thin: parse → store/queue/sse → serialize
    ├── models.py                     # JobRecord, JobKind, JobStatus enum, ProgressModel,
    │                                 #   QueryParams/SetupParams, RegionInfo
    ├── store.py                      # per-job JSON persistence: create/read/update/list,
    │                                 #   atomic job.json, append progress.ndjson, restart recovery
    ├── queue.py                      # single-worker asyncio queue + worker loop
    │                                 #   (spawn subprocess, stream stdout, drive status)
    ├── sse.py                        # SSE hub: per-job fan-out, snapshot-then-tail, heartbeat
    ├── cli_adapter/                  # THE boundary — the only code that knows CLI internals
    │   ├── __init__.py               #   public typed interface; rest of App imports only this
    │   ├── argv.py                   #   validated params object → subprocess argv
    │   ├── params_schema.py          #   introspect click options → form/validation schema + quality defaults
    │   ├── regions.py                #   built-region list via steeproute.cache coverage (read-only)
    │   └── progress_parse.py         #   stdout line-classifier → ProgressModel (3 flavours; format from the spike)
    └── static/                       # buildless frontend (package data, served via StaticFiles)
        ├── index.html                #   S1 map home
        ├── run-watch.html            #   S3 run watch
        ├── runs.html                 #   S4 run library
        ├── css/app.css
        └── js/
            ├── api.js                #   single fetch/EventSource wrapper — only file with URLs
            ├── map-home.js           #   S1 Leaflet picker + region overlay + build/configure
            ├── config-form.js        #   S2 schema-driven basic/advanced form
            ├── run-watch.js          #   S3 SSE subscribe + render progress model
            ├── runs.js               #   S4 list render
            └── live-indicator.js     #   global header live-job indicator

tests/
├── unit/
│   ├── test_app_store.py             # persistence + restart→failed(interrupted)
│   ├── test_app_queue.py             # worker loop against a fake/echo subprocess
│   ├── test_app_progress_parse.py    # classifier vs pinned stdout fixtures (spike output)
│   ├── test_app_argv.py              # params → argv
│   ├── test_app_params_schema.py     # click introspection → schema
│   └── test_app_regions.py           # crafted cache → region list
└── integration/
    ├── test_app_api.py               # FastAPI TestClient: job lifecycle, fake CLI
    └── test_app_sse.py               # snapshot-then-tail + heartbeat
```

**pyproject additions:** `uv add "fastapi[standard]"`; `[project.scripts]
steeproute-app = "steeproute.app.main:run"`; ensure `app/static/**` ships as
package data.

**Runtime-resolved paths (not in repo):**
- **Job store:** `platformdirs.user_data_dir("steeproute")/app/jobs/<job_id>/`
  containing `job.json`, `progress.ndjson`, and `result/` (the per-job output dir
  passed to `query` as `--output-dir`, then served for the S5 iframe).
- **Cache root:** the CLI's existing cache dir, read **read-only** by
  `cli_adapter/regions.py`.

### Architectural Boundaries

- **App ↔ CLI (the seam):** `setup`/`query` are opaque subprocesses invoked with
  adapter-built argv; their stdout is scraped. The ONLY in-process coupling is the
  adapter's read-only imports (`steeproute.cache` coverage, `steeproute.cli._shared`
  click options). No App code outside `cli_adapter/` imports `steeproute.*` internals.
- **API boundary:** REST + one SSE stream over localhost; no auth (single-user).
- **Internal boundaries:** `api.py` orchestrates `store` + `queue` + `sse`; the
  worker (`queue.py`) uses `cli_adapter` + `store` + `sse`; `cli_adapter` is
  imported only through its public interface.
- **Data boundary:** the job store dir is the App's own state; the CLI cache is
  external and read-only; result dirs are produced by `query` and served static.

### Requirements → structure mapping

| Screen / capability (UX / brainstorm) | Lives in |
|---|---|
| S1 Map home + green/grey overlay | `static/index.html` + `js/map-home.js` + `api.py::GET /regions` → `cli_adapter/regions.py` |
| S2 Config form (all flags, quality defaults) | `js/config-form.js` + `cli_adapter/params_schema.py` + `api.py::POST /jobs` |
| S3 Run watch (3 progress flavours, Stop) | `static/run-watch.html` + `js/run-watch.js` + `sse.py` + `api.py::GET /jobs/{id}/events`, `POST /jobs/{id}/stop` |
| S4 Run library | `static/runs.html` + `js/runs.js` + `api.py::GET /jobs` → `store.py` |
| S5 Result view (iframe) | static mount over `<job>/result/` (existing CLI HTML report) |
| Cluster B — job runner core | `queue.py` + `store.py` + `sse.py` + `cli_adapter/progress_parse.py` |
| Cluster C — build region (setup job) | `api.py::POST /jobs` (kind=setup) + overlay flip |
| F7 — restart recovery | `store.py` boot scan (`running` → `failed`+`interrupted`) |
| Global live-job indicator | `js/live-indicator.js` (subscribes SSE / polls `GET /jobs`) |

### Data flow

```
Browser ──REST──▶ api.py ──▶ store.py (persist job) ──▶ asyncio.Queue
                                                            │
                             worker loop (queue.py) ◀───────┘
                                │  cli_adapter/argv.py → create_subprocess_exec(setup|query)
                                │  child stdout ─▶ cli_adapter/progress_parse.py ─▶ ProgressModel
                                │                    │
                                ▼                    ▼
                          store.py (job.json,   sse.py hub ──SSE──▶ Browser (run-watch, live-indicator)
                          progress.ndjson)
child exit ─▶ status {done|failed}; result/ dir served static ──iframe──▶ Browser (S5)
```

### Development workflow

- Dev: `uv run fastapi dev src/steeproute/app/main.py` (hot reload).
- Run: `uv run steeproute-app` (programmatic uvicorn, single worker).
- Tests: per-directory as documented in AGENTS.md; App tests use FastAPI's
  `TestClient` and a fake/echo subprocess so no real solver run is needed for
  unit/integration coverage.

## Architecture Validation Results

### Coherence Validation — ✅

**Decision compatibility:** The core chain is internally consistent — subprocess
invocation (Cat 1) is what makes stdout scraping (Cat 3) meaningful, which feeds
the SSE stream (Cat 4), persisted by the store (Cat 5) and replayed on reconnect.
Concurrency=1 (Cat 2) matches the single asyncio worker; no threads means no
shared-state races. Versions are compatible (FastAPI 0.136 native SSE, Python
3.13, Leaflet 1.9.4 reused).

**Pattern consistency:** snake_case-everywhere + no-envelope + the `status` Enum
are used uniformly across API, store, and SSE. The CLI-adapter boundary rule is
respected by the structure — only `cli_adapter/` touches `steeproute.*` internals.

**Structure alignment:** the tree realizes every decision — `queue.py`+`store.py`+
`sse.py` are the Cluster-B core; `cli_adapter/` localizes the two CLI-coupling
decisions (Cat 6 regions, Cat 9 schema); `static/` is the buildless frontend.

**One recorded deviation (accepted, not a defect):** Cat 7 (hard cancel) diverges
from the brainstorm/UX "best-so-far flush + view partial." Ripples are applied
(no result for `stopped`; `(partial)` states dropped). Flagged here so it is not
mistaken for an oversight.

### Requirements Coverage Validation — ✅

**UX screens:** S1→`map-home.js`+`GET /regions`; S2→`config-form.js`+
`params_schema`; S3→`run-watch.js`+SSE+stop; S4→`runs.js`+`GET /jobs`;
S5→static iframe. All five covered.

**UX flows:** F1 (cached query), F2 (build→query two-step), F3 (return-to-live),
F4 (queue several), F5 (browse/re-run), F7 (restart recovery) — all supported.
**F6 (Stop)** is covered but intentionally reduced to hard-cancel (no partial).

**Brainstorm clusters:** A (thin/reuse) → form + iframe + prefill; B (job runner
core) → queue/store/sse/progress_parse; C (deliberate orchestration) → setup-job
+ overlay; D (open questions) → explicitly deferred below.

**NFRs:** concurrency=1 (worker), long jobs (SSE heartbeat + snapshot-then-tail),
single-user/no-auth (localhost), CLI-honest progress (raw log tail), thinness
(no build step, native SSE, per-job JSON).

### Implementation Readiness Validation — ✅ (with one gated unknown)

Decisions, patterns, and structure are complete and specific enough for
consistent implementation. **The one genuine unknown is the exact stdout line
shapes** feeding `progress_parse.py` — deliberately unresolved and gated as the
**format-inventory spike (story #1)**. The architecture is ready to start; the
classifier's internals are pinned by the spike's output (which also becomes the
test fixtures for `test_app_progress_parse.py`). This is a known, sequenced
dependency, not a gap.

### Gap Analysis Results

**Critical:** none.

**Important (specify when reached):**
- **SSE reconnection semantics** — snapshot-then-tail is defined; `Last-Event-ID`
  based resume is not. Acceptable because the persisted `progress.ndjson` snapshot
  makes a full re-send cheap; revisit only if streams get large.
- **Static-serve safety** — result dirs are served for the iframe; the mount must
  be constrained to the job-store root (no path traversal outside `<job>/result/`).

**Nice-to-have / deferred (Cluster D, non-blocking):** offer-to-build vs. block on
uncached area; run-card field set; queue reorder/cancel UX; SQLite migration if
the run library outgrows per-job JSON.

### Architecture Completeness Checklist

**Requirements Analysis** — [x] context [x] scale [x] constraints [x] cross-cutting
**Architectural Decisions** — [x] critical decisions w/ versions [x] stack [x] integration [x] NFRs
**Implementation Patterns** — [x] naming [x] structure [x] communication (SSE/API) [x] process (worker/errors)
**Project Structure** — [x] full tree [x] boundaries [x] integration points [x] requirements→structure map

### Architecture Readiness Assessment

**Overall status:** READY FOR IMPLEMENTATION.

**Confidence:** High on structure/decisions/boundaries; medium on the progress
classifier until the format-inventory spike pins stdout shapes (explicitly
sequenced first).

**Key strengths:** the single CLI-adapter boundary contains all coupling and
keeps the App testable against fakes; heavy reuse (CLI cache coverage, click
options, Leaflet assets, HTML report) keeps the App genuinely thin; the job store
doubling as the runs index removes a whole component.

**Future enhancements:** structured-emit from the CLIs (retire scraping);
best-so-far Stop if a CLI stop-contract is later added; SQLite; Cluster D
resolutions.

### Implementation Handoff

**AI agent guidelines:** follow decisions exactly; route ALL CLI coupling through
`cli_adapter/`; use the `status` Enum and snake_case JSON; keep the worker
crash-proof; treat this doc as authoritative and the CLI [`architecture.md`](architecture.md)
as the interface reference.

**First implementation priority:** the **stdout format-inventory spike** — capture
real `setup` + `query` runs at the quality demo params and pin the three flavours'
line shapes. Then the backend skeleton (`uv add "fastapi[standard]"` + store +
single-worker queue + subprocess spawn + `POST/GET /jobs`).
