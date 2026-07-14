---
stepsCompleted: [1, 2, 3]
inputDocuments:
  - _bmad-output/planning-artifacts/architecture-app.md
  - _bmad-output/planning-artifacts/ux-design-specification.md
  - _bmad-output/brainstorming/brainstorming-session-2026-07-14-1437.md
---

# steeproute web App - Epic Breakdown

## Overview

This document provides the epic and story breakdown for the **steeproute web App**
subsystem — a thin FastAPI + HTML/JS/Leaflet UI over the two existing CLIs
(`steeproute-setup` + `steeproute`). There is no App-specific PRD; requirements
are extracted from the settled architecture decisions ([architecture-app.md](architecture-app.md))
and the lean UX spec ([ux-design-specification.md](ux-design-specification.md)),
both derived from the [brainstorming session](../brainstorming/brainstorming-session-2026-07-14-1437.md).
The CLI subsystem's own `epics.md` / `prd.md` / `architecture.md` are the
interface reference, not inputs to this breakdown.

## Requirements Inventory

### Functional Requirements

FR1: Area selection & cache visibility — a full-bleed Leaflet map lets the user drop a center point and drag a radius; cached regions render as overlays (green = instant query, grey = needs build).
FR2: Deliberate two-step orchestration — an uncached (grey) area offers "Build this region" (a `setup` job); only a built (green) area offers "Configure query" (a `query` job). The two are never auto-chained.
FR3: Config form — a basic/advanced form exposes ALL CLI flags; App defaults are the quality demo params (not the low CLI defaults). Re-run-with-tweaks prefills the form from a prior run's stored params.
FR4: Job submission & serial queue — one worker executes one run at a time; multiple jobs may be queued and run in order. Two job kinds: `setup` and `query`.
FR5: Fire-and-forget & return-to-live — submitting a job never blocks; a persistent live-job indicator on every screen links back to the running job's progress at any time (no blocking modal).
FR6: Unified progress surface — one Run-watch screen renders all three stdout flavours (GRASP solver events, query non-solve stages, setup stages) through one model: phase + stage (n/total) + log tail + (GRASP-only) best-so-far cost & iteration.
FR7: Stop a running job — a Stop action hard-cancels the subprocess. A stopped job has no result (per architecture Category 7; supersedes the UX spec's best-so-far/partial affordance).
FR8: Result view — the finished routes are shown by embedding the existing CLI Leaflet HTML report in an iframe as-is (no native re-render).
FR9: Run library — the job registry is rendered directly as one list: running → queued (in order) → history (newest first). Actions are status-gated: watch running, cancel queued, open finished (iframe), re-run-with-tweaks.
FR10: Restart recovery — on server boot, any job left `running` is marked `failed` with `failure_reason: "interrupted"`.

### NonFunctional Requirements

NFR1: Concurrency = 1 — a single-worker serial queue is a hard constraint (the solver saturates all cores; cross-run thread-safety is unintended), not a simplification.
NFR2: Long-running jobs — a query can approach the CLI's ~10-min soft budget; the UI and transport must tolerate multi-minute streams, idle periods, and client reconnection (SSE heartbeat + snapshot-then-tail).
NFR3: Single-user / local / no-auth — inherits the CLI's N=1 posture: no security, multi-tenancy, accessibility, or responsive/mobile requirements (explicitly out of scope).
NFR4: CLI-honest progress — no fabricated progress bars or ETAs in v1; scraped stdout is surfaced faithfully.
NFR5: Thinness / low ceremony — no frontend build step (plain HTML/JS/Leaflet); minimal backend ceremony (FastAPI); heavy reuse of existing CLI output (HTML report, Leaflet assets, cache coverage, click options).

### Additional Requirements

_Technical requirements from the architecture that shape implementation:_

- **No starter / extend the existing repo** — the App is a subpackage (`src/steeproute/app/`) inside the existing uv-managed project. Setup = `uv add "fastapi[standard]"` + a runnable app skeleton + `[project.scripts] steeproute-app`; no new repo, no scaffold. `app/static/**` ships as package data. (First implementation story.)
- **The single CLI-adapter boundary** — all coupling to the CLI subsystem lives in one `cli_adapter` package owning exactly four seams: (1) argv construction from validated params, (2) cache-manifest reading for `GET /regions`, (3) params-schema introspection from the CLI arg parser, (4) stdout line classification into the progress model. No App code outside `cli_adapter/` imports `steeproute.*` internals, reads cache layout, knows a stdout line format, or hand-builds argv.
- **Subprocess invocation model** — `setup`/`query` run only as child processes (`asyncio.create_subprocess_exec`), never imported in-process; exit-code → status mapping over {0, 1, 2, 130}.
- **Single asyncio worker** — one worker coroutine in FastAPI `lifespan`: pop next queued → spawn → async-read stdout line-by-line → classify → append + push to SSE → set terminal status. In-memory `asyncio.Queue` rebuilt from the persisted store on boot. The worker never dies on a bad job (per-job exception → `failed` + exit_code + stdout tail, move on).
- **Per-job JSON persistence** — one directory per job: `job.json` (record) + append-only `progress.ndjson` (powers snapshot-then-tail). Atomic writes (temp-file + `os.replace`). The store IS the runs index. Job store under `platformdirs.user_data_dir("steeproute")/app/jobs/<job_id>/` with a `result/` output dir. SQLite deferred.
- **Cache-coverage detection** — the green/grey overlay is driven by reading the CLI's on-disk cache manifest directly, isolated behind `cli_adapter/regions.py` (`GET /regions`).
- **Params schema = CLI arg parser** — introspect the CLIs' click options into one flag schema that is the single source of truth for form render, validation, and argv build (no hand-duplication).
- **SSE transport** — FastAPI native `EventSourceResponse` (no `sse-starlette`); snapshot-then-tail on connect; periodic heartbeat comment; named events `progress` / `status`. One-way server→client.
- **API surface** — `POST /jobs` (201), `GET /jobs`, `GET /jobs/{id}` (404 unknown), `GET /jobs/{id}/events` (SSE), `POST /jobs/{id}/stop` (409 if not running), `DELETE /jobs/{id}` (cancel queued), `GET /regions`; static mounts for the frontend and per-run output dirs.
- **Data-format conventions** — snake_case on the wire; no response envelope (`{detail}` errors); ISO-8601 UTC timestamps; `status` Enum (`queued|running|done|failed|stopped`) as single source of truth; time-sortable job id (ULID/uuid4 hex).
- **Stdout format-inventory spike** — the first implementation task: capture real `setup` + `query` runs at quality demo params and pin the three flavours' line shapes; the output becomes the classifier's test fixtures. This is the one gated unknown.
- **Static-serve safety** — the per-run result mount must be constrained to the job-store root (no path traversal outside `<job>/result/`).

### UX Design Requirements

_Five screens (S1–S5) + global chrome. Only S3 and S4 carry non-obvious design weight (wireframed in the UX spec)._

UX-DR1: **S1 Map home** — a Leaflet picker (click to drop center, drag handle to set radius) with green/grey cached-region overlays; the primary action is context-sensitive: "Build this region" over grey, "Configure query" over green. Over grey, "Configure query" is **disabled with a "Build this region first" prompt** (block, not offer-to-build) — this enforces the settled deliberate two-step (no silent/auto setup); see UX spec §F2 and brainstorm.
UX-DR2: **S2 Config form** — a basic row (common knobs) + collapsible advanced section exposing all flags, prefilled with quality-demo defaults; supports prefill-from-stored-params for re-run-with-tweaks. (Conventional pattern; no wireframe.)
UX-DR3: **S3 Run watch** — the crux screen: a flavour-agnostic frame (phase / stage_name (stage_index/total) / monospace auto-scrolling log tail) that reads identically for setup and query; the GRASP best-so-far-cost + iteration line appears ONLY during a query's solve phase (absent otherwise, not reserved globally); a Stop action; status-gated result actions ([View routes] on done; failed shows exit code + [Re-run with tweaks]); body fed by SSE, rendering the persisted snapshot then the live tail. (No "partial" state — Stop is hard-cancel per FR7.)
UX-DR4: **S4 Run library** — one list ordered running → queued (in order) → history (newest first); run cards show `kind · area-label`, center/radius, timestamp, and a status-appropriate metric (cost for done query jobs, exit code for failed); actions are status-gated: [Watch] running, [Cancel] queued, [View routes] + [Re-run with tweaks] finished, [Re-run with tweaks] on failed/interrupted.
UX-DR5: **S5 Result view** — the finished routes shown by mounting and iframing the per-run output dir's existing CLI HTML report; no native re-render.
UX-DR6: **Global chrome** — a persistent header on every screen with (a) app name → Map home, (b) a Runs link → Run library, and (c) a compact live-job indicator (e.g. "● query running · r20 · iter 12k") that subscribes to the active job's SSE (falls back to polling `GET /jobs`) and links to that job's S3. Flat navigation: Map home, Run library, active Run watch are the only top-level destinations; S2 and S5 are reached through them.

### FR Coverage Map

- FR1 (map + cache overlay) → Epic 1
- FR2 (deliberate two-step build) → Epic 1
- FR3 (config form, all flags, quality defaults) → Epic 2
- FR4 (serial single-worker queue) → Epic 1 (established on setup jobs; re-exercised by query in Epic 2)
- FR5 (fire-and-forget + return-to-live) → Epic 1 (re-exercised by query in Epic 2)
- FR6 (unified progress surface) → Epic 1 (setup + query non-solve stage frame) → extended in Epic 2 (GRASP best-cost/iter readout)
- FR7 (stop = hard-cancel) → Epic 1 (re-exercised by query in Epic 2)
- FR8 (result view, iframe report) → Epic 2
- FR9 (run library) → Epic 3
- FR10 (restart recovery) → Epic 3

_NFRs are cross-cutting: NFR1 concurrency=1 & NFR2 long-jobs land in Epic 1's worker + SSE; NFR3 single-user/no-auth, NFR4 CLI-honest progress, NFR5 thinness apply across all epics._
_UX-DRs: UX-DR1/DR3/DR6 → Epic 1; UX-DR2/DR5 → Epic 2; UX-DR4 → Epic 3._

## Epic List

### Epic 1: Pick and build a region from the map
A full-bleed Leaflet map lets the user pick center+radius, see cached (green) vs. needs-build (grey) regions, and build an uncached region via a `setup` job — watching its live progress (with return-to-live via a persistent header indicator), stopping it if needed, and seeing the area flip green when done. This epic stands up the entire job-runner pipeline (single-worker queue, subprocess spawn, per-job persistence, unified progress, SSE snapshot-then-tail, run-watch UI) against the simpler setup progress flavour, and makes the deliberate build→query two-step visible. Fully standalone — no CLI prep required.
**FRs covered:** FR1, FR2, FR4, FR5, FR6, FR7

### Epic 2: Configure, run, and watch a route query
On a built (green) region, the user opens a basic/advanced config form exposing all flags (defaults = quality demo params), queues a `query` job, watches its progress — now including the GRASP best-so-far-cost + iteration readout — and views the resulting routes in the embedded CLI HTML report. Layers the query flavour and the result view onto Epic 1's proven job runner and run-watch screen.
**FRs covered:** FR3, FR8

### Epic 3: Browse, revisit, and re-run past runs
The job registry is rendered directly as one run library (running → queued → history); the user can cancel a queued job, open a finished run's routes, re-run any run with tweaked params (prefilled form), and — after a server restart — see interrupted runs correctly marked failed and offered for re-run.
**FRs covered:** FR9, FR10

## Epic 1: Pick and build a region from the map

Stands up the entire job-runner pipeline against the simpler `setup` progress flavour, and makes the deliberate build→query two-step visible. Fully standalone — no CLI prep required.

### Story 1.1: Stdout format-inventory spike

As a developer,
I want a pinned inventory of the exact stdout line shapes emitted by real `setup` and `query` runs at the quality demo params,
So that the progress classifier is built against known formats and regression-tested against fixtures, not guesses.

**Acceptance Criteria:**

**Given** a real `setup` run and a real `query` run at the quality demo params,
**When** their stdout is captured,
**Then** representative samples of all three progress flavours (setup stages, query non-solve stages, GRASP solver events) are saved as committed fixture files.

**Given** the captured samples,
**When** the line shapes are documented,
**Then** each distinguishable line type (phase marker, stage start/end + timing, GRASP iteration/best-cost) is described well enough to write a classifier rule.

**Given** the fixtures,
**When** the classifier is later built (Story 1.4 / 2.2),
**Then** these same files serve as its unit-test inputs.

### Story 1.2: Runnable app skeleton with static shell

As a developer,
I want a minimal runnable FastAPI app with the `steeproute-app` entry point and a static frontend shell,
So that there is a working, serve-able foundation to hang the job runner and screens on.

**Acceptance Criteria:**

**Given** the existing uv project,
**When** `fastapi[standard]` is added and the `steeproute.app` package + `steeproute-app` script are created,
**Then** `uv run steeproute-app` (and `uv run fastapi dev …`) start a server serving a placeholder home page.

**Given** the running server,
**When** the home page loads,
**Then** it renders the persistent global header (app name → Map home, Runs link) with the live-job-indicator slot present (empty for now).

**Given** the frontend static dir,
**When** it is served,
**Then** Leaflet assets are the copy already vendored by the CLI report (no CDN, no new dependency), mounted via StaticFiles, and `app/static/**` ships as package data.

### Story 1.3: Job store and single-worker queue (setup jobs, curl-drivable)

As a developer,
I want a persistent job store and a single-worker serial queue that spawns a `setup` subprocess,
So that a build job can be submitted, run one-at-a-time, and inspected without any UI.

**Acceptance Criteria:**

**Given** `POST /jobs` with `kind=setup` and an area,
**When** it is called,
**Then** a job record is created (status `queued`, HTTP 201) and persisted as an atomic `job.json` in its own per-job dir under the runtime job-store path.

**Given** a queued setup job and a free worker,
**When** the worker loop runs,
**Then** it builds argv via `cli_adapter.argv`, spawns the CLI as a subprocess (never in-process), and drives status `queued`→`running`→{`done`|`failed`} with `exit_code` recorded (mapping {0,1,2,130}).

**Given** a subprocess that exits non-zero,
**When** it fails,
**Then** the job is marked `failed` with a stdout tail and the worker proceeds to the next queued job (one bad job never stalls the queue).

**Given** multiple submitted jobs,
**When** the worker processes them,
**Then** exactly one runs at a time and the rest wait in order (concurrency = 1).

**Given** `GET /jobs` and `GET /jobs/{id}`,
**When** called,
**Then** they return the registry / a single record (404 for unknown id), snake_case, no response envelope.

### Story 1.4: Setup progress plumbing — classifier, log, and SSE stream

As a developer,
I want the worker to classify setup stdout into a progress model, persist it, and expose it over SSE,
So that a client can watch a running build live and a reconnecting client can catch up.

**Acceptance Criteria:**

**Given** a running setup subprocess,
**When** it emits stdout,
**Then** each line is classified by `cli_adapter.progress_parse` into the ProgressModel (`phase, stage_name, stage_index, stage_total, grasp=null, elapsed, log_tail`) and appended to the job's append-only `progress.ndjson`.

**Given** the pinned spike fixtures (Story 1.1),
**When** the classifier is unit-tested,
**Then** setup lines map to the expected model fields.

**Given** `GET /jobs/{id}/events`,
**When** a client connects,
**Then** the endpoint replays the persisted progress snapshot and then streams the live tail (snapshot-then-tail) using named `progress`/`status` events.

**Given** a long idle stream,
**When** no progress arrives,
**Then** periodic heartbeat comments keep the connection alive; and when the job reaches a terminal state, a final `status` event is sent.

### Story 1.5: Watch a running job (run-watch screen + Stop + live indicator)

As a user,
I want a Run-watch screen that shows a job's live progress and lets me stop it, plus a header indicator that returns me to it,
So that I can follow a long build without a blocking modal and come back to it from anywhere.

**Acceptance Criteria:**

**Given** a running job,
**When** I open its Run-watch screen,
**Then** it renders job identity, status/elapsed, and the flavour-agnostic progress frame (phase · stage n/total · auto-scrolling monospace log tail) fed by the SSE stream (snapshot then live tail).

**Given** I navigate away and return via the header live-job indicator,
**When** the active job is running,
**Then** the indicator is visible on every screen and links back to the running job's Run-watch.

**Given** a running job,
**When** I click Stop,
**Then** `POST /jobs/{id}/stop` hard-cancels the subprocess, the status becomes `stopped`, and no result is offered (409 if the job is not running).

**Given** a job that reaches a terminal state,
**When** I view Run-watch,
**Then** a status-appropriate footer is shown (`failed` shows exit code + a re-run affordance); the View-routes action lands with Epic 2.

### Story 1.6: Map home — pick an area and build a region

As a user,
I want a map where I pick a center and radius, see which regions are cached, and build an uncached one,
So that I can prepare a region for querying entirely from the app, as a deliberate step.

**Acceptance Criteria:**

**Given** the Map home,
**When** it loads,
**Then** a full-bleed Leaflet map lets me click to drop a center and drag a handle to set a radius (circle overlay), and cached regions render as green (built) / grey (needs build) overlays from `GET /regions`.

**Given** `GET /regions`,
**When** called,
**Then** it returns built regions by reading the CLI cache manifest through `cli_adapter.regions` (read-only; no other module touches the cache layout).

**Given** my selection lands over a grey area,
**When** I act,
**Then** the primary action is "Build this region" (enqueues a setup job and navigates to its Run-watch), while "Configure query" is disabled with a "Build this region first" prompt.

**Given** a setup job completes,
**When** I return to the map,
**Then** that area now renders green and offers "Configure query" (handed off to Epic 2).

## Epic 2: Configure, run, and watch a route query

Layers the query flavour and the result view onto Epic 1's proven job runner and run-watch screen.

### Story 2.1: Configure and queue a query (schema-driven form)

As a user,
I want a basic/advanced config form exposing all query flags with quality defaults, and a way to queue the query,
So that I can launch a route generation against a built region with full control.

**Acceptance Criteria:**

**Given** the CLI's query arguments,
**When** the form is built,
**Then** its fields are derived from the CLI arg parser via `cli_adapter.params_schema` (single source of truth; no hand-duplicated flag list) — a basic row of common knobs plus a collapsed advanced section with the full set.

**Given** the form,
**When** it first opens,
**Then** it is prefilled with the quality demo params as defaults (not the low CLI defaults).

**Given** a completed form for a green region,
**When** I click Queue query,
**Then** `POST /jobs` with `kind=query` and the validated params enqueues the job (422 on invalid params) and I am navigated to its Run-watch.

**Given** the query runs on Epic 1's runner,
**When** it executes,
**Then** it is queued/serial/stoppable and its log tail is visible (the GRASP readout arrives in Story 2.2).

### Story 2.2: Query-flavour progress (GRASP + non-solve stages)

As a user,
I want query progress to show the solver's best-so-far cost and iteration alongside stage progress,
So that I can judge how a long solve is going, CLI-honestly.

**Acceptance Criteria:**

**Given** a running query,
**When** its stdout is classified,
**Then** `cli_adapter.progress_parse` handles both query flavours — non-solve stage timing lines (cache load, elevation, climb detect, contraction, validate/render) advance stage n/total, and GRASP events populate `grasp={iter, best_cost}`.

**Given** the solve phase,
**When** GRASP events arrive,
**Then** Run-watch shows the best-so-far cost + iteration line; outside the solve phase (and for setup jobs) that line is absent (not reserved).

**Given** the pinned spike fixtures (Story 1.1),
**When** the classifier is unit-tested for query,
**Then** query lines map to the expected model fields.

### Story 2.3: View the resulting routes

As a user,
I want to open a finished query's routes in the app,
So that I can see the generated steep routes without leaving the UI.

**Acceptance Criteria:**

**Given** a query that reached `done`,
**When** I click View routes,
**Then** the existing CLI Leaflet HTML report from that run's output dir is embedded in an iframe as-is (no native re-render).

**Given** the per-run output dir is served statically,
**When** it is mounted,
**Then** the mount is constrained to the job-store root so no path outside `<job>/result/` is reachable (no traversal).

**Given** a job that did not produce a result (`stopped`/`failed`),
**When** I view it,
**Then** no View-routes action is offered.

## Epic 3: Browse, revisit, and re-run past runs

The job registry rendered directly as the run library, plus queue management and restart recovery.

### Story 3.1: Run library list

As a user,
I want one page listing all my runs by lifecycle and recency,
So that I can see what's running, queued, and finished at a glance.

**Acceptance Criteria:**

**Given** `GET /jobs`,
**When** the Run library loads,
**Then** jobs render in one list ordered running → queued (in order) → history (newest first).

**Given** each run,
**When** its card renders,
**Then** it shows `kind · area-label`, center/radius, timestamp, and a status-appropriate metric (cost for finished queries, exit code for failed), with status-gated action buttons.

**Given** the header Runs link,
**When** clicked from any screen,
**Then** the Run library opens.

### Story 3.2: Cancel queued and re-run with tweaks

As a user,
I want to cancel a job that hasn't started and re-run any past run with tweaked params,
So that I can manage the queue and iterate on a configuration quickly.

**Acceptance Criteria:**

**Given** a queued job,
**When** I click Cancel,
**Then** `DELETE /jobs/{id}` removes it from the store and queue and it disappears from the list (cancelling a running job is not offered here — Stop covers that).

**Given** a finished or failed run,
**When** I click Re-run with tweaks,
**Then** the config form (Epic 2) opens prefilled from that run's stored params.

**Given** I adjust and queue the re-run,
**When** submitted,
**Then** it enqueues as a new job and the original is left unchanged.

### Story 3.3: Restart recovery

As a user,
I want runs that were interrupted by a server restart to show up correctly,
So that the library never lies about a job still "running" after a crash or restart.

**Acceptance Criteria:**

**Given** the server boots,
**When** the store is scanned,
**Then** any job left in status `running` is marked `failed` with `failure_reason="interrupted"`.

**Given** such a job,
**When** the Run library renders it,
**Then** it shows as failed (interrupted) and offers Re-run with tweaks.

**Given** the in-memory queue is rebuilt on boot,
**When** previously-queued jobs are reloaded,
**Then** they remain `queued` and resume processing in order.
