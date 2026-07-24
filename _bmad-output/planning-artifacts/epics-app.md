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

_Post-v1 refinements (App Epic 4, correct-course 2026-07-17):_

FR11: Map selection modes — the map picker offers explicit, switchable modes: (a) area-pick (default; click drops a new center, drag handle sets radius — Epic 1 behavior), (b) move-selection (drag the whole selection box to reposition it), (c) select-region (click a built/green overlay to snap the selection to that region's exact geometry and enable "Configure query" directly). Modes are separate so area-pick never has to double as region-select.
FR12: Flat config form — the query config form exposes ALL parameters at once with no basic/advanced collapse; every flag is always visible.
FR13: Recognizable runs — each run carries a human area label (a nearby town/place name reverse-geocoded from the center, stored on the job record), shown on its run-library card for both setup and query jobs; a query card additionally exposes its full stored parameter set on demand (click to reveal).
FR14: Readable numbers — long numeric values (e.g. iter budget) render with space thousands separators (never commas — French decimal collision) in both the config form inputs and the run-library parameter display.

_Post-v1 refinement (App Epic 5, correct-course 2026-07-24):_

FR15: Rotated-rectangle map selection — the map picker can define and edit a rotated rectangle (center + width + height + rotation angle), pass it through `AreaSpec` → argv to setup/query jobs, and render built rotated regions as their true polygons. Depends on CLI Epic 15 (the engine). Arbitrary free-form polygons remain out of scope.

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

_Post-v1 revisions (App Epic 4, correct-course 2026-07-17):_

UX-DR1 (revised): S1 Map home gains three explicit selection modes (FR11); area-pick stays the default and behaves exactly as shipped. Region overlays are inert except in select-region mode.
UX-DR2 (revised): S2 Config form drops the collapsible advanced section — all flags render in one always-visible list (FR12). Quality-demo defaults now include `max_descent_slope=0.4` and `start_at_junction` on.
UX-DR4 (revised): S4 run cards lead with the town label (FR13) instead of raw coordinates as the primary identifier; coordinates remain as secondary detail. Query cards add a click-to-reveal parameter view (FR14 grouping applies). This resolves the Cluster-D "run-card fields" open question the UX spec left deferred.

_Post-v1 revision (App Epic 5, correct-course 2026-07-24):_

UX-DR1 (revised again): S1 area-pick mode gains a second dimension handle and a rotation handle so the selection can be a rotated rectangle (FR15); the selection and built-region overlays render as `L.polygon` true polygons rather than axis-aligned `L.rectangle`. A single radius/square remains expressible. Move-selection translates the rotated box rigidly; select-region snaps to a built region's exact rotated geometry.

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
- FR11 (map selection modes) → Epic 4
- FR12 (flat config form) → Epic 4
- FR13 (recognizable runs) → Epic 4
- FR14 (readable numbers) → Epic 4
- FR15 (rotated-rectangle map selection) → Epic 5

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

### Epic 4: App UX refinements
Post-v1 refinements from hands-on use of the finished app: switchable map selection modes (incl. click-to-query a built region), a flat all-flags config form with readable space-grouped numbers and corrected steep-route defaults, and human-recognizable runs (town label + a query-params view). Additive on the delivered App Epics 1–3; no rollback. (correct-course 2026-07-17)
**FRs covered:** FR11, FR12, FR13, FR14

### Epic 5: Rotated-Rectangle Map Selection
Exposes CLI Epic 15's rotated-rectangle search areas in the map picker: a second dimension handle and a rotation handle on area-pick, move-selection and select-region generalized to rotated geometry, the new shape plumbed through `AreaSpec` → argv, and built regions rendered as their true (possibly rotated) polygons. Depends on CLI Epic 15 (the engine must accept the shape first). Additive on App Epics 1–4; no rollback. (correct-course 2026-07-24)
**FRs covered:** FR15

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

## Epic 4: App UX refinements

Post-v1 refinements surfaced by hands-on use of the finished app (see
[future-ideas.md](future-ideas.md) "App UX improvements" and
[sprint-change-proposal-2026-07-17-app-ux-improvements.md](sprint-change-proposal-2026-07-17-app-ux-improvements.md)).
Additive on the delivered App Epics 1–3 — no shipped behavior is rolled back;
each story is independently shippable. Reverse-geocoding (Story 4.3) is a **new
outbound seam** in its own module, NOT a `cli_adapter` change (that boundary is
CLI-coupling only, [architecture-app.md](architecture-app.md)).

### Story 4.1: Map selection modes (area-pick / move / select-region)

As a user,
I want distinct map modes so I can drop a new area, reposition the whole
selection, or click a built region to query it directly,
So that one mode never has to do double duty and querying an existing region
doesn't require re-deriving its geometry by hand.

**Acceptance Criteria:**

**Given** the Map home,
**When** it loads,
**Then** a visible mode control offers area-pick (default), move-selection, and
select-region, and area-pick behaves exactly as shipped in Epic 1 (click drops a
new center; the handle drags the radius).

**Given** move-selection mode,
**When** I drag the selection box,
**Then** the whole selection repositions (center follows the drag; radius
unchanged) and coverage re-resolves from the server on release.

**Given** select-region mode,
**When** I click a built (green) region overlay,
**Then** the selection snaps to that region's exact geometry (from `GET /regions`)
and "Configure query" becomes enabled directly — no manual center/radius
reproduction. Region overlays are inert in the other modes.

### Story 4.2: Config form overhaul — flat layout, readable numbers, corrected defaults

As a user,
I want every query parameter visible at once, long numbers grouped for
readability, and defaults that fit a steep-route tool,
So that the config pane is fast to scan and edit without hunting or misreading.

**Acceptance Criteria:**

**Given** the config form,
**When** it opens,
**Then** all schema fields render in one always-visible list — the basic/advanced
`<details>` collapse is removed and the `basic`/`advanced` grouping is retired
from `params_schema` without breaking argv construction or validation (the
introspected schema stays the single source of truth).

**Given** a long-number field (e.g. iter budget),
**When** it renders and I edit it,
**Then** the value displays with space thousands separators (`1 000 000`, never
commas), formatting on blur and parsing back to a plain number on submit — the
value sent on the wire / to argv stays plain (grouping is display-only). A small
number-format helper is factored for reuse by Story 4.3.

**Given** a fresh "Configure query",
**When** the form prefills its quality-demo defaults,
**Then** `max_descent_slope` defaults to `0.4` and `start_at_junction` defaults
to on (in addition to the existing quality-demo overrides).

### Story 4.3: Recognizable runs (town label + query-params view)

As a user,
I want to recognize past runs by place and inspect a query's configuration,
So that the run library is usable without decoding GPS coordinates or losing a
run's params.

**Acceptance Criteria:**

**Given** a job is created,
**When** the record is written,
**Then** a best-effort reverse-geocode (its own offline-safe module) resolves the
center to a nearby town/place name stored as `area_label` on the job record; a
failed or unavailable lookup leaves it unset (no error, no blocked job).

**Given** the run library,
**When** a run card renders (setup or query),
**Then** it leads with the town `area_label` as the primary identifier, with the
raw center/radius kept as secondary detail; a run with no label falls back to
today's coordinate display.

**Given** a query run card,
**When** I click to reveal its parameters,
**Then** the full stored `params` set is shown, with long numbers grouped via
Story 4.2's format helper.

## Epic 5: Rotated-Rectangle Map Selection

Exposes CLI Epic 15's rotated-rectangle areas in the map picker. Depends on CLI
Epic 15 — the CLI must accept the shape (new area flags) before the App can emit
it. Additive on App Epics 1–4; no shipped behavior is rolled back. The change is
two-seamed: (1) the `cli_adapter` argv seam + the `GET /regions` region seam
learn the rotated shape (a `cli_adapter` change, in scope for that boundary); (2)
the buildless frontend picker (`js/map-home.js`) gains dimension + rotation
handles and renders true polygons. Watch item mirrored from CLI Epic 15: the
**envelope-leak audit** applies App-side too — `RegionBounds` currently ships an
axis-aligned bbox that over-reports a rotated region.

### Story 5.1: Rotated AreaSpec, argv, and regions plumbing

As a user,
I want a rotated area chosen on the map to reach the CLI and built rotated regions
to come back with their true shape,
So that the App can drive CLI Epic 15 end-to-end.

**Acceptance Criteria:**

**Given** a rotated area from the picker,
**When** a job is created,
**Then** `AreaSpec` carries center + width + height + angle and `cli_adapter.argv`
builds the CLI Epic 15 flags; a square still emits `--radius` for backward compat.

**Given** `GET /regions`,
**When** a built rotated region is returned,
**Then** it carries its true polygon (`RegionBounds` generalized), and any
axis-aligned envelope it also exposes is documented as an over-approximation
(App-side envelope-leak audit).

**Given** the argv and regions seams,
**When** unit-tested,
**Then** rotated and square areas round-trip correctly through `cli_adapter`.

### Story 5.2: Map picker rotation and dimension handles

As a user,
I want to draw, move, and pick rotated rectangles on the map,
So that I can align the search box to a diagonal range without leaving the app.

**Acceptance Criteria:**

**Given** area-pick mode,
**When** I edit the selection,
**Then** I can set two dimensions and a rotation angle; the box renders as an
`L.polygon`; a centered square is still expressible.

**Given** move-selection mode,
**When** I drag the selection,
**Then** the rotated box translates rigidly (shape and angle unchanged) and
coverage re-resolves from the server on release.

**Given** select-region mode,
**When** I click a built (green) rotated region,
**Then** the selection snaps to that region's exact rotated geometry (from
`GET /regions`) and "Configure query" becomes enabled directly.
