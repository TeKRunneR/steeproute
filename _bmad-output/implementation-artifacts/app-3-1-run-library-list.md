# Story 3.1: Run library list

Status: done

<!-- App track (epics-app.md). Story key `app-3-1-*` is `app-`-prefixed to avoid
     collision with the CLI track's `3-1-*` (already `done`); both share
     sprint-status.yaml. First story of App Epic 3. -->

## Story

As a user,
I want one page listing all my runs by lifecycle and recency,
so that I can see what's running, queued, and finished at a glance.

## Acceptance Criteria

1. **`/runs` serves the S4 run-library page.** A new `GET /runs` (no id — distinct from the existing `/runs/{job_id}` and `/runs/{job_id}/result`, so no route conflict) serves a `runs.html` shell, reached by the "Runs" link already present in the global header on every page. The page carries the same global chrome as the others (app-name → Map home, Runs link, live-indicator slot).

2. **One list, ordered running → queued (in order) → history (newest first).** The page renders every job from the existing `GET /jobs` registry in a single list: the running job first, then queued jobs in queue order (creation-ascending), then all terminal jobs newest-first. An empty registry shows an empty-state message, not an error.

3. **Each run card shows identity, timestamp, and a status-appropriate metric.** A card shows `kind · area-label` (derived from center/radius, as Run-watch does), center/radius, the created timestamp, the status, and a status-appropriate metric: a finished (`done`) query shows its result objective (cost); a `failed` job shows its exit code (and `failure_reason` when present, e.g. `interrupted`). The metric is sourced without per-card result-file I/O in the list path, and a card omits it gracefully when it is unavailable (e.g. a query that produced no routes).

4. **Navigational actions are wired; mutating actions are Story 3.2.** Status-gated actions: a running job offers **Watch** (→ its Run-watch), a `done` query offers **View routes** (→ the S5 result view) — both reuse existing pages. **Cancel** (queued) and **Re-run with tweaks** (finished/failed) belong to Story 3.2 (they need `DELETE /jobs/{id}` and config-form prefill, neither built yet); 3.1 does not implement them — omit them or leave an inert marker, consistent with the Run-watch "View routes"/re-run placeholder precedent.

5. **No new list/data endpoint; `GET /jobs` + `JobRecord` are the contract.** The run library reads the existing creation-ordered `GET /jobs`; its shape is unchanged. The only backend additions are the `GET /runs` page route and the done-query metric seam (Dev Notes). No SSE/queue/store changes beyond capturing that metric. All CLI-stdout-format knowledge (parsing the run's objective) stays inside `cli_adapter` — no stdout format is parsed in JS (the load-bearing boundary rule).

6. **Tested at the altitude the surface allows.** Integration (`TestClient`): `GET /runs` serves the page and `runs.js` is served. Any `cli_adapter` metric parser added is unit-tested against the pinned summary-line fixtures. Frontend ordering/rendering (AC #2–#4) is verified end-to-end via a browser DOM dump — this buildless project has no JS unit harness, and Browser-pane screenshots flake on this machine (use `javascript_exec`, per Story 2.2/2.3).

7. **Scope guard.** S4 list page + `GET /runs` route + `runs.html`/`runs.js`/CSS + the done-query metric seam only. No cancel/re-run (Story 3.2); no restart-recovery (Story 3.3) — a failed-and-interrupted job just renders generically as failed here. No CLI changes; no changes to `GET /jobs`, SSE, the queue, or the store beyond the metric capture.

## Tasks / Subtasks

- [x] Add the `GET /runs` page route + the `runs.html` shell (AC: #1)
  - [x] `GET /runs` in `main.py` serving `static/runs.html` — no path param, distinct from `/runs/{job_id}`; the run-watch page's own comment already reserved this ("the `/runs` run-library list (no id) lands in Story 3.1 — no route conflict").
  - [x] `runs.html`: global chrome (copy the header block from `run-watch.html`) + an empty list container that `runs.js` fills; load `live-indicator.js` too.
- [x] Source the done-query metric without per-card I/O (AC: #3, #5) — took the recommended persist-at-completion seam
  - [x] Added `cli_adapter.parse_summary_objective` extracting the run's `total_objective` from the CLI summary; unit-tested against the Story 1.1 pinned fixtures (both worker counts).
  - [x] The worker captures that objective onto `JobRecord.result_objective` at query completion, so `GET /jobs` carries it and the list endpoint stays `store.list()`.
- [x] Build `runs.js` — fetch, order, render cards (AC: #2, #3, #4)
  - [x] Fetch `GET /jobs` via `api.js`; order running → queued (creation-asc) → terminal (newest-first); render the empty-state when there are none.
  - [x] Per card: `kind · area-label`, center/radius, created timestamp, status, status-appropriate metric; status-gated **Watch** (`runWatchUrl`) / **View routes** (`resultViewUrl`) actions. Backend access only through `api.js`.
  - [x] No new URL needed — reused `listJobs`/`runWatchUrl`/`resultViewUrl`; `api.js` unchanged (the `/runs` nav is a static header href).
  - [x] CSS: a `/* --- S4 Run library (Story 3.1) --- */` section in `app.css` for the list + card layout (per-status left-rail color).
- [x] Tests (AC: #6)
  - [x] `tests/integration/test_app_api.py`: `GET /runs` serves the page + `runs.js`; `/runs` vs `/runs/{id}` distinctness; done-query captures `result_objective`; setup-done & failed-query stay `None`.
  - [x] `tests/unit/test_app_progress_parse.py`: `parse_summary_objective` vs. the pinned summary-line fixtures (merged total, not the understated worker frame) + anchored/absent cases.
  - [x] Verified end-to-end: seeded running/queued/done-query/failed records, drove `/runs` in the browser (DOM dump) — ordering, card fields, `cost 8421.5` / `exit code 2` metrics, and Watch/View-routes links all correct; no console errors.

## Dev Notes

**This is the first Epic-3 story and closes the list half of FR9.** Epic 3 renders the job registry directly as the run library; 3.1 is the read-only list, 3.2 adds cancel + re-run-with-tweaks, 3.3 adds restart recovery. The store already *is* the runs index (no separate index to build) [Source: _bmad-output/planning-artifacts/epics-app.md#Story 3.1: Run library list; #FR Coverage Map — FR9 → Epic 3; architecture-app.md#Category 5].

**Reuse the existing `GET /jobs` — do not add a list endpoint.** `api.py::list_jobs` returns `store.list()`, all records in creation order (job ids are time-sortable, so a plain directory listing is chronological). The run library's running→queued→history ordering is a **display** regrouping done in `runs.js`, not a server change: running first, queued in creation-ascending order (= queue order), then terminal jobs reversed (newest-first). Leave `GET /jobs` and `JobRecord` untouched — `live-indicator.js` also depends on `GET /jobs` being creation-ordered [Source: src/steeproute/app/api.py:112-115; src/steeproute/app/store.py:73-80; src/steeproute/app/models.py:266-270; src/steeproute/app/static/js/live-indicator.js:62-75].

**The done-query metric — the one real decision in this story.** The card must show "cost for finished queries" (UX-DR4), but the honest final figure is **not** currently a structured field. The GRASP progress frames carry a running `best_cost`, and for parallel runs `best_worker_objective` *understates* the merged result — so progress frames are the wrong source. The authoritative number is the run summary's `total_objective`, which today lives only in the CLI stdout (and therefore in the job record's bounded `stdout_tail`) [Source: src/steeproute/app/cli_adapter/progress_parse.py:72-80; src/steeproute/app/models.py:176; _bmad-output/planning-artifacts/epics.md#FR22].

- **Recommended:** add a small `cli_adapter` parser for the summary `total_objective` line, and have the **worker persist it onto `JobRecord` at query completion** (a `result_objective: float | None`, populated once from the freshest stdout). Then `GET /jobs` carries it, the list endpoint stays `store.list()`, and `runs.js` reads a structured field — no JS stdout parsing (boundary rule), no per-card result-file reads, and 3.2/3.3 inherit it for free.
- **Alternative** (no worker touch): parse `total_objective` from the already-persisted `stdout_tail` server-side at read time via the same `cli_adapter` helper. Keeps the worker untouched but re-parses on every read.
- Either way: keep the parse in `cli_adapter` (it knows a stdout format), and degrade gracefully — no summary line (zero-route query, non-query, older record) → the card simply omits the metric. Do **not** read route JSON sidecars per card in the list path.

**area-label + card fields — copy the Run-watch derivation.** There is no stored human area label; `run-watch.js::renderIdentity` derives `${kind} · r${radius} (center …)` from `area.center`/`area.radius_km`. Reuse that shape for the card so the two screens read consistently. Failed cards show `exit_code` and, when set, `failure_reason` — a boot-interrupted job carries `failure_reason="interrupted"` (`status=failed`, not a separate status), which renders generically here; its dedicated handling is Story 3.3 [Source: src/steeproute/app/static/js/run-watch.js:54-59; src/steeproute/app/models.py:37-49, :170-176; architecture-app.md#JSON & data-format conventions].

**Actions: wire only what already has a destination.** **Watch** → `api.js::runWatchUrl` (S3), **View routes** → `api.js::resultViewUrl` (S5, done queries only — a `stopped`/`failed` job has no result, Category 7). **Cancel** and **Re-run with tweaks** are Story 3.2: `DELETE /jobs/{id}` does not exist yet (the api.py header notes it "arrives with Story 3.2") and the config-form prefill isn't built. Follow the established placeholder precedent — Run-watch shipped its "View routes" as an inert marker until Epic 2 owned it; do the same rather than wiring dead buttons [Source: src/steeproute/app/static/js/api.js:81-89; src/steeproute/app/api.py:6-8; src/steeproute/app/static/js/run-watch.js:82-111; architecture-app.md#Category 7].

**Frontend conventions (unchanged).** Vanilla ES module, no framework/bundler; all backend access through `api.js` (the only file with URLs); no inline handlers; server is the source of truth (fetch, don't mirror). Frontend files kebab-case. The list is a one-shot fetch on load — it does not need to live-update (the live-indicator already covers the running job); a manual refresh/`GET /jobs` re-fetch is enough for v1 [Source: architecture-app.md#Frontend conventions; #Category 10].

### Project Structure Notes

Target tree — **adds** the starred files, **edits** the daggered; everything else is prior work [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree — the `runs.html`/`runs.js` rows]:

```
src/steeproute/app/
├── main.py                        † (edit) GET /runs page route
├── queue.py                       † (edit, recommended) persist result_objective at query completion
├── models.py                      † (edit, recommended) JobRecord.result_objective
├── cli_adapter/
│   └── progress_parse.py          † (edit) summary total_objective parser  [or a new adapter helper]
└── static/
    ├── runs.html                  ★ (new)  S4 run-library shell + global chrome
    ├── js/
    │   ├── runs.js                ★ (new)  fetch GET /jobs, order, render cards + actions
    │   └── api.js                 † (edit, if a new URL is needed) — only file with URLs
    └── css/app.css                † (edit) §S4 run-library styles
tests/
├── integration/test_app_api.py    † (edit) GET /runs serves page + runs.js served
└── unit/test_app_progress_parse.py † (edit, if the seam lands here) summary-objective parse
```

This realizes the architecture's planned `runs.html` + `js/runs.js` + `api.py::GET /jobs` → `store.py` mapping for S4 [Source: architecture-app.md#Requirements → structure mapping — S4 Run library row]. The `queue.py`/`models.py`/`cli_adapter` edits exist only to source the card metric; if the read-time-parse alternative is chosen, the worker/model are untouched.

### Testing

Per AGENTS.md: type-check with `uv run basedpyright <changed files>`; run `tests/unit` and `tests/integration` in **separate** invocations (wrong `conftest.py` otherwise). The page-serving assertions belong in `tests/integration/test_app_api.py` (FastAPI `TestClient`) — mirror the Story 2.3 pattern that asserts the result page + `result.js` are served, and the Story 1.5 assertion that a page carries the `id="live-indicator"` chrome. The metric-parser unit test uses the Story 1.1 pinned stdout fixtures (`test_app_progress_parse.py`). The ordering/card/action rendering is JS with no unit harness in this buildless project — verify it end-to-end: seed a running + queued + done-query + failed record on a tmp store (reuse the Story 2.3 `_seed_job` helper), run the real app, open `/runs`, and assert order + fields + links via a `javascript_exec` DOM dump (not a screenshot — the Browser pane's screenshot flakes and map tiles don't load in-pane; Story 2.2/2.3 Debug Log) [Source: C:\Users\yfontana\Code\steeproute\AGENTS.md#Dev environment; tests/integration/test_app_api.py:461-499; _bmad-output/implementation-artifacts/app-2-3-view-the-resulting-routes.md#Testing].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 3.1: Run library list] — the epic AC this story realizes (one list; run→queued→history; card fields + status-gated actions)
- [Source: _bmad-output/planning-artifacts/epics-app.md#Epic 3; #FR Coverage Map] — FR9 (run library) → Epic 3; 3.1 = list, 3.2 = cancel/re-run, 3.3 = restart recovery
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 8 — API surface] — `GET /jobs` list (running → queued → history) for the run library
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Requirements → structure mapping] — S4 Run library row (`static/runs.html` + `js/runs.js` + `api.py::GET /jobs` → `store.py`)
- [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule: one CLI-adapter boundary] — stdout-format knowledge stays in `cli_adapter`, never in JS
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#S4] — one list ordered running → queued → history; run-card fields; status-gated actions
- [Source: src/steeproute/app/api.py:112-115] — `list_jobs` = `store.list()` (creation-ordered); the endpoint to reuse as-is
- [Source: src/steeproute/app/main.py:50-65] — `/runs/{job_id}` + `/runs/{job_id}/result` page-route pattern to mirror for `GET /runs`
- [Source: src/steeproute/app/store.py:73-80; models.py:266-270] — `list()` order + time-sortable job id (creation order)
- [Source: src/steeproute/app/static/js/run-watch.js:54-59, :82-111] — `renderIdentity` area-label derivation + the footer action/placeholder precedent
- [Source: src/steeproute/app/static/js/api.js:30-33, :81-89] — `listJobs`, `runWatchUrl`, `resultViewUrl` (the only file with URLs)
- [Source: src/steeproute/app/cli_adapter/progress_parse.py:72-80] — why `best_worker_objective` understates; `total_objective` is the honest final figure (summary-only today)
- [Source: src/steeproute/app/queue.py:257-269] — the query-completion transition where a `result_objective` would be captured (recommended seam)
- [Source: _bmad-output/implementation-artifacts/app-2-3-view-the-resulting-routes.md] — immediate predecessor; `_seed_job` test helper + Browser-pane DOM-dump verification pattern

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Metric sourcing — took the recommended persist-at-completion seam.** Added `cli_adapter.parse_summary_objective(lines)` (anchored `^total_objective: <num>$`, scans the tail last-to-first, returns `None` when absent) and had the worker call it on the bounded `stdout_tail` in the **done-query branch only** — so `JobRecord.result_objective` is captured once at completion, `GET /jobs` carries it, and the list endpoint stays `store.list()` (no per-card route-JSON I/O, no JS stdout parsing). Confirmed against both pinned fixtures that the summary total (workers1 9719.6 / workers4 10670.1) is read, **not** the understated last `best_worker_objective` frame (workers4 10118.8).
- **Verified end-to-end against the real app + browser DOM** (not just the TestClient). Seeded a tmp store with one job per lifecycle state (done query w/ `result_objective`+route file, failed setup exit 2, queued query, stale running setup), ran `create_app(store_root=…)` under uvicorn, and dumped `/runs` via `javascript_exec` (per the Story 2.2/2.3 note — Browser-pane screenshots flake / map tiles don't load in-pane, so a DOM dump is authoritative; this page has no map anyway). DOM confirmed: order running → queued → history-newest-first; cards show `kind · r<radius>`, center/radius/timestamp, status; metric `cost 8421.5` (done query) and `exit code 2` (failed), absent for running/queued; actions `Watch → /runs/{id}` (running) and `View routes → /runs/{id}/result` (done query) only — no Cancel/Re-run (Story 3.2); no console errors. The stale `running` job stays running (restart recovery is Story 3.3), which is what surfaced the running card.

### Completion Notes List

- **Backend: one page route + a metric seam.** `GET /runs` in `main.py` serves `runs.html` (no path param — distinct from `/runs/{job_id}`, integration-tested for non-collision). `cli_adapter.parse_summary_objective` (exported from the adapter package — only `cli_adapter` knows the stdout format) + `JobRecord.result_objective` + the worker's done-query capture are the only additions; `GET /jobs`, SSE, queue, and store are otherwise untouched.
- **Frontend: buildless list page.** `runs.html` (global chrome + list container + empty-state) and `runs.js` (fetch `GET /jobs` → regroup running → queued (creation-asc) → terminal (reversed) → render cards). Actions gated to what already has a destination — Watch (`runWatchUrl`) and View routes (`resultViewUrl`, done queries only); Cancel/Re-run deferred to Story 3.2. `api.js` unchanged (no new API URL; the `/runs` header link is static HTML). CSS `§S4` adds the list + card layout with a per-status left-rail color.
- **Metric is honest and degrades.** A done query shows its merged `total_objective`; a failed job shows exit code (+ `failure_reason` like `interrupted` when present, forward-compatible with Story 3.3); setup-done / no-route / non-terminal jobs show no metric. Verified failed and setup jobs keep `result_objective=None` (capture skipped off the done-query path).
- **Validation:** `basedpyright` 0/0/0 and `ruff check` + `ruff format --check` clean on the changed Python files; `tests/unit/test_app_progress_parse.py` 22 passed (5 new), `tests/integration/test_app_api.py` 40 passed (6 new), app store/queue unit suites 15 passed, full offline suite **1023 passed, 17 deselected** (~3m14s), no regressions.

### File List

- `src/steeproute/app/main.py` (modified) — `GET /runs` page route serving `runs.html`
- `src/steeproute/app/models.py` (modified) — `JobRecord.result_objective` field
- `src/steeproute/app/queue.py` (modified) — capture `result_objective` from the stdout tail at done-query completion
- `src/steeproute/app/cli_adapter/progress_parse.py` (modified) — `parse_summary_objective` + `_SUMMARY_OBJECTIVE` regex
- `src/steeproute/app/cli_adapter/__init__.py` (modified) — export `parse_summary_objective`
- `src/steeproute/app/static/runs.html` (new) — S4 run-library shell (global chrome + list + empty-state)
- `src/steeproute/app/static/js/runs.js` (new) — fetch `GET /jobs`, order, render cards + gated actions
- `src/steeproute/app/static/css/app.css` (modified) — §S4 run-library styles
- `tests/unit/test_app_progress_parse.py` (modified) — 5 `parse_summary_objective` tests
- `tests/integration/test_app_api.py` (modified) — 6 Story-3.1 tests (page/js served, route distinctness, objective capture on done/setup/failed)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-16 | Story drafted from epics-app.md (Story 3.1) + architecture-app.md (Cat 5/8/10, S4 structure map) + the existing `GET /jobs`/store/run-watch seams. Metric-sourcing surfaced as the one real decision, with a recommended `cli_adapter`-parsed `result_objective` persisted at query completion (read-time-parse alternative noted). Cancel/re-run scoped out to Story 3.2, restart recovery to 3.3. Status → ready-for-dev. |
| 2026-07-16 | Implemented the S4 run library: `GET /runs` page + `runs.html`/`runs.js`/CSS, and the done-query metric seam (`cli_adapter.parse_summary_objective` → worker captures `JobRecord.result_objective` at completion). List regroups the existing `GET /jobs` (running → queued → history-newest-first) client-side; actions gated to Watch + View-routes (Cancel/Re-run deferred to 3.2). 11 tests added (5 unit + 6 integration); verified end-to-end in the browser (DOM dump). `basedpyright`/`ruff` clean; full offline suite **1023 passed, 17 deselected**, no regressions. Status → review. |
| 2026-07-16 | Code review (low effort, diff-only pass over the non-test hunks — metric-sourcing seam in `queue.py`/`cli_adapter`, the new `GET /runs` route, `runs.js`/`runs.html`/CSS). No findings: `result_objective` capture correctly gated to done queries only, no route collision between `/runs` and `/runs/{job_id}`, no dead/duplicated code. Status → done. |
