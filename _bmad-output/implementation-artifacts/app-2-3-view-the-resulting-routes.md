# Story 2.3: View the resulting routes

Status: done

<!-- App track (epics-app.md). Story key `app-2-3-*` is `app-`-prefixed to avoid
     collision with the CLI track's `2-3-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want to open a finished query's routes in the app,
so that I can see the generated steep routes without leaving the UI.

## Acceptance Criteria

1. **"View routes" appears only on a `done` query with a produced result.** On the S3 Run-watch screen, the `done` footer of a **query** job whose result exists offers a "View routes" action, replacing Story 1.5's `"done"` placeholder. `stopped`, `failed`, still-running, and `setup` jobs offer no View-routes action (a hard-cancelled job has no result — architecture-app.md §Category 7; setup jobs produce no route report).

2. **The result view embeds the CLI report as-is in an iframe — no re-render.** "View routes" opens a result view that embeds the run's existing CLI `route-<i>.html` report in an `<iframe>`; the App never re-renders the map or profile natively. The reports are self-contained (Leaflet 1.9.4 + Chart.js inlined at CLI render time), so serving the single HTML file is sufficient — no additional asset/static mount is introduced for the iframe.

3. **All returned routes are reachable, not just the first.** A query returns up to N routes written as `route-1.html … route-N.html` (1-indexed; FR21). The result view makes every produced route reachable (e.g. a simple route selector), not only `route-1`, driven by an actual listing of the files present — not an assumed count.

4. **Serving is constrained to `<job>/result/` — no traversal, no store leakage.** Report files are served only from the job's own `<store_root>/<job_id>/result/` directory. A request that escapes that directory (`..` segments, absolute paths, a symlink pointing outside) or targets the job's own `job.json` / `progress.ndjson` is refused with 404 — never served. This closes architecture-app.md's flagged "Static-serve safety" gap.

5. **Unknown / ineligible jobs 404, with the App-store boundary respected.** An unknown job id, a job with no result (`stopped` / `failed` / still running / `setup`), or a missing route file returns 404 with FastAPI's default `{detail}` (no envelope). Result serving reads the App's own job store (`JobStore.job_dir` + the worker-assigned `result_dir`) — it does **not** touch `cli_adapter` or any CLI internal (result files are already on disk from the query subprocess).

6. **Tested at unit + integration altitude, and verified end-to-end.** Integration tests (FastAPI `TestClient`) cover: a crafted `done` query job with hand-written `route-1.html`/`route-2.html` under its `result/` dir serves each file and lists both; a traversal attempt (e.g. `../job.json`, encoded `..`) is refused (404); a `stopped` / `failed` / unknown job serves and offers no result. AC #1's on/off gating is asserted against real job records. End-to-end (AC #2/#3 verification): a real `done` query's `route-*.html` render inside the iframe and the selector switches between them.

7. **Scope guard.** Result view page + its file-serving and route-listing endpoints + the S3 "View routes" wiring only. **No** run-library (S4) changes — Epic 3 (Story app-3-1/3-2) wires View routes from the library and re-run-with-tweaks. No CLI changes; no native route re-render. `result_dir` is already assigned by the worker (Story 2.1) — consume it via `JobStore.job_dir`; do not recompute the per-job path formula.

## Tasks / Subtasks

- [x] Add result-serving + route-listing endpoints in `api.py` (AC: #2, #3, #4, #5)
  - [x] `GET /jobs/{id}/routes` → the sorted list of `route-*.html` present under the job's `result/` dir (numeric order `route-1` < `route-2` < `route-10`); `[]` for a done query that produced none, 404 for a job with no viewable result.
  - [x] `GET /jobs/{id}/result/{filename:path}` → serve the named file from `<store_root>/<job_id>/result/` via `FileResponse`; resolve-then-`is_relative_to` guard refuses any path escaping that dir (`..`, absolute, out-of-tree symlink) — `job.json`/`progress.ndjson` live in the parent dir so are unreachable by construction. Request-time guarded handler (not a `StaticFiles` mount), per Dev Notes.
  - [x] Gate both on `kind == query` + `status == done` (via `_viewable_result_dir`); 404 otherwise. Unknown id → 404 (reused the `_require_job` dependency). Result dir derived from `store.job_dir(id) / RESULT_DIR_NAME`, not recomputed.
- [x] Add the result-view page (AC: #2, #3)
  - [x] `GET /runs/{job_id}/result` in `main.py` (distinct extra segment from `GET /runs/{job_id}` — no conflict) serves the new `static/result.html` shell.
  - [x] `static/js/result.js`: reads the job id from the URL, fetches the routes list, iframes the first, and renders a route selector that switches the iframe `src`. Vanilla ES module; backend access only through `api.js`. CSS added for the full-bleed iframe + selector (`app.css` §S5).
  - [x] Extended `api.js` with `resultViewUrl` / `listRoutes` / `resultFileUrl` (the only file that hardcodes endpoint URLs).
- [x] Wire "View routes" into Run-watch (AC: #1)
  - [x] `run-watch.js::showFooter` `done` branch now appends a "View routes" link (→ `resultViewUrl`) **only** for `jobKind === "query"` (module-level `jobKind` set from the loaded record); setup-done stays a plain `"done"`. `stopped`/`failed` branches unchanged.
- [x] Tests (AC: #6)
  - [x] `tests/integration/test_app_api.py`: crafted `done` query records with `route-*.html` on disk; assert the list endpoint returns all (numeric order incl. route-10), each file serves as `text/html`, encoded traversal (`%2e%2e%2fjob.json` / `progress.ndjson`) 404s without leaking, missing file 404s, `stopped`/`failed`/`setup`/`running`/unknown jobs 404, empty done query lists `[]`, and the result page + `result.js` are served. (8 new tests.)
  - [x] Verified end-to-end: seeded a `done` query whose `result/` holds the real `docs/examples/chamrousse` reports, ran the real app, and drove the result page in the browser — iframe loaded `route-1.html` (real report, Leaflet map present), the selector switched to `route-2.html`, and the Run-watch footer showed `done · View routes` → `/runs/{id}/result`.

## Dev Notes

**This closes FR8 (result view) and completes Epic 2.** Story 2.1 stood up the query form + runner and made the worker assign each query job a per-job `result_dir`; Story 2.2 layered the GRASP progress readout. This story is the last Epic-2 piece: turn the `done`-query's on-disk report into something viewable in-app [Source: _bmad-output/planning-artifacts/epics-app.md#Story 2.3; #FR Coverage Map — FR8 → Epic 2].

**The report is self-contained — serve one HTML file, mount nothing extra.** The CLI's `render()` writes one `route-<i>.html` + `route-<i>.json` per route, 1-indexed (`route-1`, `route-2`, …; FR21), into the output dir, with Leaflet 1.9.4 and Chart.js **inlined** at render time (no external CDN, no sibling asset files) — so an `<iframe src=".../route-1.html">` is fully functional on its own; there is no `index.html` and no asset directory to also serve [Source: src/steeproute/output.py:7, :70, :154-155; _bmad-output/planning-artifacts/epics.md#HTML Report Asset Strategy].

**`result_dir` is already set — consume it, don't recompute the path.** The worker sets `record.result_dir = str(JobStore.job_dir(id) / "result")` on every query job before spawning (`RESULT_DIR_NAME = "result"`), and `JobStore.job_dir` is the single source of the per-job directory formula. Read the served directory as `JobStore.job_dir(id) / "result"` (or from `record.result_dir`); do not re-derive `<store_root>/<job_id>/result` anywhere else [Source: src/steeproute/app/queue.py:56, :212-218; src/steeproute/app/store.py:47-55].

**Prefer a guarded request-time handler over a `StaticFiles` mount.** Two reasons a create_app-time `app.mount("/…", StaticFiles(directory=store_root))` is the wrong tool here: (a) the store root is **injected at lifespan** (`store_root` param, `app.state.job_store`), not known at `create_app` time the way `_STATIC_DIR`/`_VENDOR_ASSETS_DIR` are; (b) mounting at the store root would expose each job's `job.json` and `progress.ndjson`, not just `result/`. A handler that resolves `job_dir(id)/result/<filename>`, rejects anything escaping that dir, and 404s the store's own files gives exactly the "constrained to the job-store root, no path outside `<job>/result/`" guarantee the architecture asks for [Source: _bmad-output/planning-artifacts/architecture-app.md#Gap Analysis Results — Static-serve safety; #Category 8 — static mounts; src/steeproute/app/main.py:100-121].

**Path-traversal defense — resolve then contain.** Validate by resolving the candidate path and asserting it is inside the result dir (e.g. `resolved.is_relative_to(result_dir.resolve())`), rather than string-matching `..`. Reject absolute filenames and any resolved target that is not a regular file under `result/`. This is the one security-relevant surface in the App (single-user/localhost otherwise; architecture-app.md §NFR3) — get it right and test the escape attempts explicitly [Source: _bmad-output/planning-artifacts/architecture-app.md#Gap Analysis Results].

**View-routes gating lives in the existing footer branch.** `run-watch.js::showFooter` currently sets `footerEl.textContent = "done"` with a `// [View routes] lands with Epic 2` marker — this is the exact hook. Only the `done` branch changes, and only for `job.kind === "query"` (the `job` record is already in the module closure). `stopped` stays `"stopped · no result"` and `failed` keeps its exit-code + Re-run link — both correct as-is under Category 7 [Source: src/steeproute/app/static/js/run-watch.js:74-95].

**No `cli_adapter` involvement.** Unlike `GET /regions` (Category 6) and the params schema (Category 9), result serving reads only the App's own job store — the query subprocess already produced the files. Keep it out of `cli_adapter/`; the load-bearing rule is about CLI-internal coupling, and there is none here [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule].

**Deviation from the UX spec (already settled in the architecture).** The UX spec draws "View routes (partial)" for `stopped` jobs (S3/S4/F6); the architecture's Category 7 hard-cancel supersedes that — a `stopped` job has **no** result, so no View-routes action. Follow the architecture, not the UX "(partial)" affordance [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 7; #Architecture Validation — One recorded deviation].

### Project Structure Notes

Target tree — this story **adds** the starred files and **edits** the daggered ones; everything else is prior work [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── api.py                         † (edit) GET /jobs/{id}/routes + GET /jobs/{id}/result/{filename}
├── main.py                        † (edit) GET /runs/{job_id}/result page route
└── static/
    ├── result.html                ★ (new)  S5 result-view shell (iframe + selector)
    └── js/
        ├── result.js              ★ (new)  fetch routes list, iframe + switch
        ├── api.js                 † (edit) result-view URLs/helpers (only file with URLs)
        └── run-watch.js           † (edit) done-query footer → "View routes" link
tests/
└── integration/
    └── test_app_api.py            † (edit) result serving/listing + traversal + gating tests
```

**Variance from the original architecture tree (with rationale):** the architecture's tree listed only `index.html` / `run-watch.html` / `runs.html` and mapped "S5 Result view (iframe)" to "static mount over `<job>/result/`" with no dedicated page file — because the structure was explicitly to be "refined in later steps." This story realizes S5 as a small `result.html` + `result.js` pair (needed for the FR8/UX "embedded in an iframe" requirement and the multi-route selector of AC #3) plus a guarded serving handler instead of a raw static mount (see Dev Notes for the injected-store-root + store-file-leakage reasons). No model, queue, SSE, store, or `cli_adapter` files change [Source: _bmad-output/planning-artifacts/architecture-app.md#Project Structure & Boundaries; #Requirements → structure mapping — S5 row].

### Testing

Per AGENTS.md: type-check with `uv run basedpyright <changed files>`; run `tests/unit` and `tests/integration` in **separate** invocations (wrong `conftest.py` otherwise). Result-serving tests belong in `tests/integration/test_app_api.py` (FastAPI `TestClient`) — follow the Story 2.1 query-lifecycle helpers (`_lifecycle_client`, `_query_body`, `_poll_until_terminal`) and craft the `done` record + `result/route-*.html` files directly on the tmp store, no real solver run needed. Existing suites (`test_app_api.py`, `test_app_sse.py`) must stay green. End-to-end (AC #2/#3): drive a real query at quality-demo params against a built cache entry so `route-*.html` actually exist, then open `/runs/{id}/result`; a crafted `done` job pointing `result_dir` at a prior real run's output dir is an acceptable shortcut if a full setup+query is too slow. Note (from Story 2.2's Debug Log): the Browser pane's screenshot can time out on this machine — verify iframe/selector state via a `javascript_exec` DOM dump if so [Source: C:\Users\yfontana\Code\steeproute\AGENTS.md#Dev environment; _bmad-output/implementation-artifacts/app-2-2-query-flavour-progress-grasp-and-non-solve-stages.md#Debug Log References].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 2.3: View the resulting routes] — the epic AC this story realizes (done→iframe; static mount constrained to job-store root; no result for stopped/failed)
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 7 — Stop / cancel semantics: hard cancel] — stopped job has no result; supersedes the UX "(partial)" affordance
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 8 — API surface] — static mounts for per-run output dirs; REST conventions (404/{detail}, no envelope)
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Gap Analysis Results — Static-serve safety] — the traversal constraint this story closes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Requirements → structure mapping] — S5 Result view row (static mount over `<job>/result/`)
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#S5 / F1 / F5] — result view = existing Leaflet HTML report in an iframe, reached from S3 (this story) and S4 (Epic 3)
- [Source: src/steeproute/output.py:53-155] — `render()`: 1-indexed `route-<i>.html`/`.json`, self-contained (inlined assets), no index.html
- [Source: src/steeproute/app/queue.py:56, :204-218] — `RESULT_DIR_NAME`, worker sets `result_dir = job_dir(id)/result` on query jobs
- [Source: src/steeproute/app/store.py:44-55] — `JobStore.job_dir` (the single per-job path formula) + the `result/` note
- [Source: src/steeproute/app/main.py:45-56, :100-121] — the `GET /runs/{job_id}` page route pattern and the create_app static mounts
- [Source: src/steeproute/app/api.py:66-71, :150-153] — `_require_job` (404) dependency + `GET /jobs/{id}` shape to mirror
- [Source: src/steeproute/app/static/js/run-watch.js:74-95] — `showFooter` done branch: the "[View routes] lands with Epic 2" hook
- [Source: src/steeproute/app/static/js/api.js:81-84] — `runWatchUrl`: the pattern for a `resultUrl` helper (URLs live only here)
- [Source: tests/integration/test_app_api.py:370-395] — Story 2.1 query-job lifecycle test helpers to reuse for the crafted `done` record
- [Source: _bmad-output/implementation-artifacts/app-2-1-configure-and-queue-a-query-schema-driven-form.md] — introduced the per-job `result_dir` this story consumes
- [Source: _bmad-output/implementation-artifacts/app-2-2-query-flavour-progress-grasp-and-non-solve-stages.md] — the immediate predecessor (query progress); Browser-pane screenshot flakiness note

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **End-to-end verified against the real app + real reports + browser DOM**, not just the TestClient. Seeded a `done` query job whose `result/` held copies of the real `docs/examples/chamrousse/route-{1,2,3}.html` reports, ran the actual `create_app(store_root=…)` under uvicorn, and drove `/runs/{id}/result` in the Browser pane. The DOM showed status `query · r6 (center 45.12, 5.88)`, three selector tabs (Route 1 active), `#route-frame` `src=/jobs/{id}/result/route-1.html`; clicking Route 2 flipped the active tab and switched `src` to `route-2.html`, and the same-origin `iframe.contentDocument.title` read `steeproute — route 2` with a `.leaflet-container` present — i.e. the real CLI report rendered as-is. The Run-watch page for the same job showed footer `done · View routes` → `/runs/{id}/result` with Stop hidden. Verified via `javascript_exec` DOM dumps (not screenshots — the Browser-pane screenshot flakiness noted in Story 2.2 + the map tiles not loading in-pane make a DOM dump the authoritative check).
- **Traversal guard confirmed reachable, not normalized away.** The `%2e%2e%2f`-encoded `../job.json` / `../progress.ndjson` requests reach the `{filename:path}` handler (Starlette decodes them into the path param) and are refused by the `is_relative_to` guard — the integration test asserts 404 *and* that the record body (`"id"`) is not leaked.

### Completion Notes List

- **Backend: two read-only endpoints + one page route.** `GET /jobs/{id}/routes` (numeric-sorted `route-*.html` list) and `GET /jobs/{id}/result/{filename:path}` (guarded `FileResponse`), both gated to a done query via `_viewable_result_dir` (kind+status check, path derived from `store.job_dir(id)/RESULT_DIR_NAME` — never recomputed). `GET /runs/{job_id}/result` in `main.py` serves the S5 shell.
- **Path safety by construction.** The result dir is resolved and the candidate must be `is_relative_to` it — so `..`, absolute paths, and out-of-tree symlinks are refused, and `job.json`/`progress.ndjson` (in the *parent* dir) are unreachable. No `StaticFiles` mount at the store root (which would have leaked those + can't see the lifespan-injected store root). Result serving touches only the App's own store — no `cli_adapter`, no CLI internals (the load-bearing rule has nothing to confine here).
- **Frontend: buildless result page.** `result.html` + `result.js` (fetch routes → iframe first → selector switches `src`), CSS for a full-bleed iframe. `api.js` gained the three URL helpers (still the only file with URLs). `run-watch.js` done branch appends "View routes" only for `jobKind === "query"`; setup-done stays a plain `done`, `stopped`/`failed` unchanged (Category 7 — a stopped job has no result).
- **Reuse over re-render.** The CLI reports are self-contained (Leaflet/Chart.js inlined), so the iframe needs only the single HTML file — no asset mount, no native map re-render, confirmed by the real chamrousse reports rendering in-frame.
- **Validation:** `basedpyright` 0/0/0 and `ruff check` + `ruff format --check` clean on the changed Python files; `tests/integration/test_app_api.py` 35 passed (8 new), app unit suites (`test_app_store`/`test_app_queue`/`test_app_progress_parse`) 32 passed, full offline suite green (see Change Log).

### File List

- `src/steeproute/app/api.py` (modified) — `_viewable_result_dir` helper (reads `job.result_dir`); `GET /jobs/{id}/routes` (returns `RouteInfo` list, files only) + `GET /jobs/{id}/result/{filename:path}`; `_ROUTE_FILE` regex; imports (`re`, `FileResponse`, `JobKind`, `RouteInfo`)
- `src/steeproute/app/models.py` (modified) — `RouteInfo` (`{index, filename}`) for the routes-list endpoint
- `src/steeproute/app/main.py` (modified) — `GET /runs/{job_id}/result` page route serving `result.html`
- `src/steeproute/app/static/result.html` (new) — S5 result-view shell (route selector + iframe + global chrome)
- `src/steeproute/app/static/js/result.js` (new) — fetch routes, iframe first, selector switches `src`
- `src/steeproute/app/static/js/api.js` (modified) — `resultViewUrl`, `listRoutes`, `resultFileUrl`
- `src/steeproute/app/static/js/run-watch.js` (modified) — done-query footer "View routes" link (module-level `jobKind`)
- `src/steeproute/app/static/css/app.css` (modified) — §S5 result-view styles (full-bleed iframe, selector tabs)
- `tests/integration/test_app_api.py` (modified) — 8 Story-2.3 tests + `_seed_job`/`_seeded_client` helpers; imports extended
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-16 | Story drafted from epics-app.md (Story 2.3) + architecture-app.md (Cat 7/8, Static-serve-safety gap) + the Story 2.1 `result_dir` seam and CLI `output.py` report layout. Result view realized as a small `result.html`/`result.js` + guarded serving handler (variance from the original tree, rationale recorded). Status → ready-for-dev. |
| 2026-07-16 | Implemented the S5 result view: `/jobs/{id}/routes` + guarded `/jobs/{id}/result/{filename:path}` endpoints (done-query-gated, resolve-then-contain traversal guard), `/runs/{id}/result` page, `result.html`/`result.js`/CSS, `api.js` helpers, and the Run-watch "View routes" wiring. 8 integration tests added (listing/serving/ordering/traversal/gating/empty/page). Verified end-to-end in the browser against the real chamrousse reports (iframe + selector + footer link). `basedpyright`/`ruff` clean; full offline suite **1013 passed, 17 deselected** (~4m15s), no regressions. Status → review. |
| 2026-07-16 | Code review (low effort). Applied 4 findings: (1) `list_result_routes` now filters `p.is_file()` so it can't list a directory/symlink that the serve endpoint would 404 on; (2) `_viewable_result_dir` reads `job.result_dir` instead of recomputing the path (per the story's own "consume it" directive) — dropped the now-unused `JobStore`/`Request` params from both endpoints; (4) the routes endpoint now returns `RouteInfo{index, filename}` so the frontend selector drops its duplicate filename regex; (5) shared `jobLoadErrorMessage` helper in `api.js` replaces the duplicated getJob-404 ternary in run-watch.js + result.js. Two findings not actioned (client-side kind/status gating; output.py↔api.py filename coupling — both low-risk, boundary-constrained). `basedpyright`/`ruff` clean; `test_app_api.py` 35 passed. |
