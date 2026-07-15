# Story 1.5: Watch a running job (run-watch screen + Stop + live indicator)

Status: done

<!-- App track (epics-app.md). Story key `app-1-5-*` is `app-`-prefixed to avoid
     collision with the CLI track's `1-5-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want a Run-watch screen that shows a job's live progress and lets me stop it, plus a header indicator that returns me to it,
so that I can follow a long build without a blocking modal and come back to it from anywhere.

## Acceptance Criteria

1. **Stop (backend).** `POST /jobs/{id}/stop` hard-cancels the running subprocess (terminate the child; no best-so-far flush, per architecture Category 7). The job transitions to `stopped` with `exit_code` 130 (the CLI's Ctrl-C convention) and no `result_dir`. It returns 409 if the target job is not currently `running` (queued or already-terminal → 409), 404 for an unknown id. The worker remains the **single writer** of the terminal transition — the endpoint requests the kill; the worker's existing drain/reap path detects the requested stop and sets `stopped` + publishes the terminal `StatusEvent`.

2. **Run-watch screen.** A run-watch page (served at a stable per-job URL) renders job identity (`kind · area-label`, center/radius), status + started/elapsed, and the **flavour-agnostic progress frame**: `phase`, `stage_name` (`stage_index / stage_total`), and an auto-scrolling monospace `log_tail`. The body is fed by the existing SSE stream (`GET /jobs/{id}/events`) — it renders the persisted snapshot first, then the live tail. The GRASP best-cost/iteration line is **absent** for setup jobs (not reserved — it lands with query in Story 2.2).

3. **Stop control + terminal footer.** While `running`, a Stop control on run-watch calls the stop endpoint. On the terminal `status` SSE event the stream closes, the Stop control disappears, and a status-appropriate footer is shown: `failed` shows the exit code + a Re-run-with-tweaks affordance; `stopped` and `done` offer **no** View-routes action here (View-routes lands with Epic 2; Re-run-with-tweaks prefill lands with Epic 2/3 — the affordance is a placeholder link/button for now).

4. **Live-job indicator.** A compact live-job indicator in the persistent header is present on every current screen (Map home + Run-watch), discovers the active `running` job by polling `GET /jobs` and subscribing to its SSE for live updates, shows a compact label (e.g. `● setup running · <area> · <stage>`), and links to that job's run-watch. It is empty when no job is running.

5. **Frontend structure (first JS story).** Vanilla ES modules, no framework/bundler. A single shared `api.js` is the only file that holds endpoint URLs (`fetch` for REST, `EventSource` for SSE); no other JS file hardcodes a URL. Files are kebab-case; no inline event handlers. The live-indicator module is wired into the existing `index.html` too, so the header behaves identically across screens.

6. **Scope guard.** Setup flavour only. No map/build button (Story 1.6), no config form / query kind / View-routes iframe (Epic 2), no cancel-queued `DELETE /jobs/{id}` (Story 3.2), no run-library list (Story 3.1). All CLI-stdout/argv/cache knowledge stays inside `cli_adapter` (untouched by this story).

## Tasks / Subtasks

- [x] Worker: track the running child and add a stop seam (AC: #1)
  - [x] In `queue.py`, hold the current `(job_id, proc)` on the `Worker` and add `stop(job_id) -> bool`: verify it matches the running child, `proc.kill()`, and record the requested-stop intent so the terminal transition marks `stopped`.
  - [x] In `_run_one`'s post-reap terminal logic, branch: requested-stop → `status=stopped`, `exit_code=130`; else keep `done`/`failed`. Do NOT derive `stopped` from the OS exit code (Windows `kill()` does not surface 130 — see Dev Notes). Clear the stop intent after the transition.
- [x] API: `POST /jobs/{id}/stop` (AC: #1)
  - [x] Add the route to `api.py`: 404 via the existing `_require_job` dependency, 409 if `record.status is not JobStatus.RUNNING`, else call `worker.stop(...)`. Return the (updated) record or 200; keep snake_case, no envelope.
  - [x] Expose the `Worker` on `app.state` in `main.lifespan` (mirror the store/queue/hub injection) so the endpoint can reach it.
- [x] Run-watch page + serve route (AC: #2, #3, #5)
  - [x] `static/run-watch.html` — the global header (with `#live-indicator` slot) + the S3 progress frame markup; loads `js/run-watch.js` and `js/live-indicator.js`.
  - [x] Add an HTML serve route for run-watch in `main.py` (`GET /runs/{job_id}` → `run-watch.html`; keeps UI under `/runs*`, API under `/jobs*`).
  - [x] `static/js/api.js` — the single fetch/EventSource wrapper (`getJob`, `listJobs`, `stopJob`, `openJobEvents`, `runWatchUrl`); the only file with URLs.
  - [x] `static/js/run-watch.js` — read the job id from the URL, `getJob` for identity/status, subscribe to `openJobEvents` (snapshot→live tail), render the frame + auto-scroll log tail, wire Stop, swap in the terminal footer on the `status` event.
- [x] Live-job indicator (AC: #4, #5)
  - [x] `static/js/live-indicator.js` — find the running job (`listJobs`), render the compact label into `#live-indicator`, link to its run-watch, subscribe to its SSE for live updates; empty when idle.
  - [x] Wire the indicator module into `index.html` (add the `<script type="module">` include).
- [x] Tests (AC: #1, #2, #4, #5)
  - [x] `tests/integration/test_app_api.py`: a **long-running** fake CLI (sleeps until killed) → `POST /jobs/{id}/stop` yields `status=stopped`, `exit_code=130`, no `result_dir`; 409 stopping a queued/terminal job; 404 unknown id. Existing lifecycle tests stay green.
  - [x] Assert `GET /runs/{job_id}` serves HTML and the run-watch JS/`api.js` are served from the static mount (200 + content markers), matching the Story-1.2 markup-assertion pattern.

## Dev Notes

**This is step 4 of the architecture's implementation sequence** — the frontend run+watch surface over Story 1.4's proven SSE plumbing, and the first browser-facing story [Source: architecture-app.md#Decision Impact Analysis]. The SSE stream, the `ProgressModel`, snapshot-then-tail, and the terminal `status` event already exist (1.4) — this story **consumes** them; it does not touch `cli_adapter` or the classifier.

**Stop = hard cancel (deliberate deviation).** Architecture Category 7 supersedes the brainstorm/UX "best-so-far flush + View routes (partial)": a `stopped` job has **no result**, no partial affordance [Source: architecture-app.md#Category 7 — Stop / cancel semantics]. The UX spec's F6/S3 "(partial)" states are dropped; do not implement them.

**The stop seam is the one new backend mechanism.** Concurrency = 1 means at most one child runs, so the `Worker` need only track a single `(job_id, proc)` [Source: architecture-app.md#Category 2]. Design (do not over-engineer):
- The endpoint requests; the worker executes and owns the terminal write. Two coroutines must not both write the record — the worker is already the sole writer of `done`/`failed`/`interrupted` [Source: src/steeproute/app/queue.py:132-191]. A stop is **not** an `asyncio` task-cancel (that path is reserved for lifespan shutdown → `interrupted` [Source: src/steeproute/app/queue.py:167-183]); `proc.kill()` just EOFs the child's pipes, so the existing `asyncio.gather(...)` + `proc.wait()` completes normally and the terminal branch runs.
- **Windows exit code:** `proc.kill()` does not yield 130 on Windows; force `exit_code=130` on a requested stop rather than reading `proc.returncode` — it mirrors the CLI's `{0,1,2,130}` Ctrl-C convention the record already models [Source: architecture-app.md#Category 1; src/steeproute/app/models.py:37-48].
- 409/404 pattern: reuse the `_require_job` `Annotated[..., Depends(...)]` dependency for the pre-handler 404 (it also sidesteps basedpyright's `reportCallInDefaultInitializer`, a Story-1.4 learning); raise `HTTPException(409)` in the handler when `status is not RUNNING` [Source: src/steeproute/app/api.py:53-58; app-1-4 Debug Log; architecture-app.md#API conventions].

**Frontend conventions (this story establishes them).** Vanilla ES modules; `fetch` + `EventSource`; one shared `api.js` wraps all endpoints and is the **only** file that hardcodes a URL; kebab-case filenames; no inline handlers; state is minimal — the server is the source of truth, re-streamed rather than mirrored [Source: architecture-app.md#Frontend conventions]. `EventSource` handles SSE reconnection natively; named events are `progress` / `status` (1.4) — listen per name [Source: src/steeproute/app/api.py:106-161; architecture-app.md#SSE event conventions].

**The progress frame is flavour-agnostic on purpose.** Render `phase / stage_name (stage_index/stage_total) / log_tail` identically regardless of kind; the GRASP `best_cost`/`iter` line renders only when `model.grasp` is non-null — for setup it is always `null`, so the line is simply absent (do not reserve space) [Source: ux-design-specification.md#S3; architecture-app.md#SSE event conventions; src/steeproute/app/models.py:121-138].

**Header is duplicated per page (accepted).** Buildless, no templating — the ~10-line header markup is repeated in each HTML page; `live-indicator.js` targets `#live-indicator` on whichever page includes it [Source: src/steeproute/app/static/index.html:12-22]. "Every screen" today = Map-home shell + Run-watch (Run library is Story 3.1).

**Reaching run-watch in this story.** With no map/build button yet (Story 1.6), a job is started via `POST /jobs` (curl or a later screen); the live indicator then surfaces it and links to `/runs/{job_id}`. That is the standalone manual-verification path for this story.

### Project Structure Notes

Target tree — this story creates the **starred** files (rest are prior/later stories) [Source: architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── queue.py                ★ (edit) track running (job_id, proc); add stop()
├── api.py                  ★ (edit) POST /jobs/{id}/stop
├── main.py                 ★ (edit) expose Worker on app.state; serve run-watch.html
└── static/
    ├── index.html          ★ (edit) include live-indicator module
    ├── run-watch.html      ★ S3 run-watch page
    └── js/
        ├── api.js          ★ single fetch/EventSource wrapper (only file with URLs)
        ├── run-watch.js    ★ SSE subscribe + render progress model + Stop
        └── live-indicator.js ★ global header live-job indicator
```

- `run-watch.js`/`live-indicator.js` never import `steeproute.*` or hardcode a URL — they go through `api.js` [Source: architecture-app.md#Frontend conventions].
- No `cli_adapter` change: this story is API-consumer + UI only.

### Testing

Per AGENTS.md: `uv run basedpyright <files>`; run `tests/unit` and `tests/integration` in **separate** invocations. App integration tests use FastAPI's `TestClient` (context manager → runs `lifespan` + worker) and a **fake subprocess** — no real solver/network [Source: architecture-app.md#Development workflow; tests/integration/test_app_api.py]. The existing fake CLI exits immediately; the stop test needs a fake that **sleeps until killed** so the job is genuinely `running` when `stop` is called. There is no JS unit harness (buildless, no npm) — the frontend is covered by asset-served assertions here and manual/`run`-skill verification; do not add a JS test runner. Existing `test_app_api.py` + `test_app_sse.py` must stay green [Source: tests/integration/test_app_api.py; tests/integration/test_app_sse.py].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 1.5: Watch a running job (run-watch screen + Stop + live indicator)] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 7 — Stop / cancel semantics: hard cancel] — stopped = no result, no partial
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 10 — Frontend architecture] — buildless static pages, live-job indicator, flat nav
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Frontend conventions] — ES modules, single api.js, kebab-case, no inline handlers
- [Source: _bmad-output/planning-artifacts/architecture-app.md#API conventions] — 201/200/404/409/422 codes; actions as sub-resources (`POST /jobs/{id}/stop`)
- [Source: _bmad-output/planning-artifacts/architecture-app.md#SSE event conventions] — named progress/status events, grasp present-as-null
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#S3 — Run watch (live progress)] — the S3 wireframe + global chrome/live-indicator
- [Source: src/steeproute/app/queue.py:132-230] — worker drain/reap path to extend with the stop seam (keep the concurrent stderr drain + single-writer terminal)
- [Source: src/steeproute/app/api.py:53-163] — `_require_job` 404 dependency + the existing SSE endpoint this UI consumes
- [Source: src/steeproute/app/main.py:49-101] — lifespan injection pattern (add Worker to app.state) + `index()` FileResponse pattern to mirror for run-watch
- [Source: src/steeproute/app/models.py:37-48,92-138] — `JobStatus` (STOPPED), `JobRecord`, `ProgressModel`/`GraspProgress` shapes
- [Source: src/steeproute/app/static/index.html] — the existing header + `#live-indicator` slot to wire
- [Source: _bmad-output/implementation-artifacts/app-1-4-setup-progress-plumbing-classifier-log-and-sse-stream.md] — SSE plumbing this consumes; FastAPI-native SSE + `Annotated` 404 learnings

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Stop is not a task-cancel.** `worker.stop()` only calls `proc.kill()` + records the intent; the child's pipes then EOF and the in-flight `asyncio.gather(...)` / `proc.wait()` completes *normally* — so the terminal transition runs in `_run_one`'s normal reap path, not the `except asyncio.CancelledError` (lifespan-shutdown → `interrupted`) branch. Kept the two paths distinct: a user Stop → `stopped`/130; a server shutdown → `failed`+`interrupted`.
- **Exit code pinned to 130, not read from the OS.** On Windows `proc.kill()` does not surface 130, so the reap path forces `exit_code=130` on a stop-requested job rather than trusting `proc.returncode`. Confirmed end-to-end: the stopped record reads `exit_code=130`, `result_dir=null`.
- **`run_watch` handler needs no path param.** FastAPI routes `/runs/{job_id}` fine with a zero-arg handler (the page's JS reads the id from `location.pathname`); declaring an unused `job_id` param only tripped basedpyright's `reportUnusedParameter`. Dropped it → 0 warnings.
- **Browser verification (fake sleeper subprocess).** Drove the real frontend against a fake `steeproute-setup` that emits the real setup stage lines then blocks: run-watch rendered identity, `status: RUNNING` + ticking elapsed, `PHASE: setup`, `STAGE: cache-write (7/7)`, and the full log tail (incl. `  tile i/3`) via snapshot-then-tail; the header live-indicator showed `● setup running · r20 · cache-write` and linked back; no GRASP line (setup). Clicking Stop → `status: STOPPED`, footer `stopped · no result`, indicator cleared.

### Completion Notes List

- **Stop seam (backend).** `Worker` now tracks the single running `(job_id, proc)` (concurrency = 1) and exposes `stop(job_id)`; the worker remains the sole writer of terminal status. `POST /jobs/{id}/stop` (`api.py`) 404s via the existing `_require_job` dependency, 409s when `status is not RUNNING`, else requests the kill; `async` so `proc.kill()` runs on the event loop. `Worker` exposed on `app.state.job_worker`.
- **Terminal branch.** A stop-requested job → `status=stopped`, `exit_code=130`, no `result_dir`; the lifespan-shutdown `CancelledError` path (`failed`+`interrupted`) is untouched and still discards the stop intent.
- **Frontend (first JS story).** Established the vanilla-ES-module frontend: `api.js` is the single URL holder (`getJob`/`listJobs`/`stopJob`/`openJobEvents`/`runWatchUrl` + a small `ApiError`); `run-watch.js` renders the flavour-agnostic frame fed by the SSE stream and wires Stop + terminal footer; `live-indicator.js` polls `GET /jobs`, subscribes to the running job's SSE, and links back to its run-watch (empty when idle), wired into both `index.html` and `run-watch.html`. `run-watch.html` + the `GET /runs/{job_id}` serve route added.
- **Scope held (AC #6):** setup flavour only — the GRASP line renders only when `model.grasp` is non-null (never for setup). No map/build (1.6), no config form/query/View-routes iframe (Epic 2), no cancel-queued `DELETE` (3.2), no run library (3.1). `cli_adapter` untouched.
- **Validation:** `basedpyright` on the changed backend files 0/0; `ruff` clean. New tests: 6 (`tests/integration/test_app_api.py` — 4 stop-path + 2 asset-served). Full offline suite **961 passed, 17 deselected** (~7m44s), no regressions (955 baseline → 961). Frontend has no unit harness (buildless, no npm) — covered by asset-served assertions + the browser drive-through above.
- **Code-review fix (low-effort pass, 1 finding, fixed).** *Silent no-op Stop in a race window:* `POST /jobs/{id}/stop` returned 200 even when `Worker.stop()` returned `False` — and `stop()` could return `False` for a genuinely-running job, because `_current` (the process handle) was only set *after* `await self._spawn(argv)`, while the store flipped to `RUNNING` *before* the spawn. A Stop landing in that await gap saw `running` (no 409), got dropped, and reported success while the job ran on with its Stop button already disabled. Fix: split active-job tracking into `_current_job_id` (set **synchronously** the instant the record flips to `RUNNING`, no `await` between) + `_current_proc` (set once spawned); `stop()` now records the intent and matches on the id (killing the child if spawned, else honored right after spawn); centralized `_current_*`/intent cleanup in one `finally`; and the endpoint now 409s when `stop()` returns `False` (the stale-`RUNNING`-record case, e.g. a pre-restart crash) so a 200 always means the kill was dispatched. Two regression unit tests added (`test_app_queue.py`): direct stop → `stopped`/130, and a stop injected *during the spawn window* → still `stopped`. Full suite **963 passed**.

### File List

- `src/steeproute/app/queue.py` (modified) — track running `(job_id, proc)`; `stop()`; `stopped`/130 terminal branch
- `src/steeproute/app/api.py` (modified) — `POST /jobs/{id}/stop` (+ `_worker` accessor)
- `src/steeproute/app/main.py` (modified) — expose `Worker` on `app.state`; `GET /runs/{job_id}` serve route
- `src/steeproute/app/static/run-watch.html` (new) — S3 Run-watch page
- `src/steeproute/app/static/js/api.js` (new) — single fetch/EventSource wrapper (only URL holder)
- `src/steeproute/app/static/js/run-watch.js` (new) — SSE-fed progress frame + Stop + terminal footer
- `src/steeproute/app/static/js/live-indicator.js` (new) — global header live-job indicator
- `src/steeproute/app/static/index.html` (modified) — include the live-indicator module
- `src/steeproute/app/static/css/app.css` (modified) — run-watch frame + live-indicator link styles
- `tests/integration/test_app_api.py` (modified) — stop-path (stopped/130, 409, 404) + run-watch/JS asset-served tests
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-15 | Story drafted from epics-app.md + architecture-app.md + ux-design-specification.md, on top of Story 1.4's SSE plumbing. Status → ready-for-dev. |
| 2026-07-15 | Implemented the hard-cancel Stop seam (worker `stop()` + `POST /jobs/{id}/stop`) and the first frontend: `api.js`/`run-watch.js`/`live-indicator.js` + `run-watch.html` + `/runs/{id}` route. 6 new tests; full suite green (961). Browser-verified run-watch + live indicator + Stop end-to-end. Status → review. |
| 2026-07-15 | Code review (low effort): fixed 1 finding — a Stop landing in the pre-spawn await window was silently dropped while returning 200. Split active-job tracking into id (set synchronously at RUNNING) + proc; endpoint 409s when `stop()` reports no active job. 2 regression tests added; full suite green (963). |
| 2026-07-15 | Code review passed, no further findings. Status → done. |
