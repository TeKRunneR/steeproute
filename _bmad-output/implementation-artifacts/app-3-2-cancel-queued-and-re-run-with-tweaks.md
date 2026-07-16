# Story 3.2: Cancel queued and re-run with tweaks

Status: done

<!-- App track (epics-app.md). Story key `app-3-2-*` is `app-`-prefixed to avoid
     collision with the CLI track's `3-2-*` (already `done`); both share
     sprint-status.yaml. Second story of App Epic 3, after 3.1 (run library list). -->

## Story

As a user,
I want to cancel a job that hasn't started and re-run any past query with tweaked params,
so that I can manage the queue and iterate on a configuration quickly.

## Acceptance Criteria

1. **`DELETE /jobs/{id}` cancels a queued job.** A new `DELETE /jobs/{job_id}` removes a `queued` job so it disappears from `GET /jobs` and never runs. 404 for an unknown id; **409 if the job is not queued** (a running job → use Stop; a terminal job → nothing to cancel). On success it returns `204 No Content`. The endpoint deletes the job's store record; the stale id left in the in-memory queue is a no-op because the worker already skips a popped id with no store record — that existing skip path must remain the mechanism (do not attempt to surgically remove an id from the `asyncio.Queue`).

2. **Cancel is atomic against the worker.** The queued→cancelled decision cannot race the worker's queued→running transition: a job is either cancelled while still queued (gone, never runs) or the request 409s because the worker already started it. There is no window where a running job's store record is deleted mid-run.

3. **The run library wires Cancel on queued cards.** A `queued` card offers a **Cancel** action that calls `DELETE /jobs/{id}` and, on success, removes the card (a re-fetch of `GET /jobs` / list re-render is sufficient — no live subscription). A failed cancel (e.g. the job just started → 409) surfaces a message rather than silently leaving a dead card; the list stays consistent with the server.

4. **Re-run with tweaks opens the query config form prefilled.** A done or failed **query** run offers **Re-run with tweaks** (in the run library card and in the Run-watch failed footer — replacing the inert `href="/"` placeholder already there). Activating it lands on the Map-home config form, opened directly for the source run's stored `area` (bypassing the map picker), with each field pre-populated from the run's stored `params`: a stored non-null value wins, otherwise the field shows its schema (quality-demo) default. A plain "Configure query" open from the map picker is unchanged — no prefill.

5. **A re-run enqueues a new job; the original is untouched.** Submitting the prefilled form goes through the existing `POST /jobs` (kind=query) path and creates a brand-new job with its own id; the source run's record is never mutated. The re-run then behaves exactly like any queued query (serial, watchable, stoppable).

6. **Scope guard.** Cancel is queued-only (Stop, not `DELETE`, cancels a running job — Category 7); Re-run-with-tweaks is **query-only** (a failed setup is redone by rebuilding from the map — there is no setup config form to prefill); no deletion of terminal jobs from history and no queue reorder (both Cluster-D, out of scope). No SSE/store schema changes beyond the new `store.delete` seam; `GET /jobs`, `POST /jobs`, and the params schema are unchanged.

7. **Tested at the altitude the surface allows.** Integration (`TestClient`): `DELETE` returns 204 and the job vanishes from `GET /jobs`; 404 unknown; 409 for a running job (via the sleeper fake CLI) and for a terminal job; a job cancelled while the worker is busy on another never runs. Store unit test for `delete`. Frontend (AC #3–#5) — Cancel removal, re-run prefill, and new-job-on-submit — verified end-to-end via a browser DOM dump (this buildless project has no JS unit harness; Browser-pane screenshots flake on this machine — use `javascript_exec`, per Story 2.2/2.3/3.1).

## Tasks / Subtasks

- [x] Add `DELETE /jobs/{id}` + the store delete seam (AC: #1, #2, #6)
  - [x] `JobStore.delete(job_id)` — `shutil.rmtree(..., ignore_errors=True)` on the per-job dir (so the record leaves `list()`); tolerant of an already-absent dir.
  - [x] `DELETE /jobs/{job_id}` in `api.py`, **`async def`** (like `stop_job`) so it runs on the event loop and cannot interleave with the worker's synchronous queued→running transition: resolve via `_require_job` (404), `409` if `status is not JobStatus.QUEUED`, else `store.delete` and return `Response(status_code=204)` (no body).
  - [x] Confirmed (no code change) the worker's existing "queued job … has no store record; skipping" path (`queue.py`) handles the stale queue id — added a comment at the DELETE handler pointing to it; integration-tested that the queue keeps serving after a cancel.
  - [x] Updated the `api.py` module docstring — `DELETE /jobs/{id}` is now "Story 3.2 the cancel-queued".
- [x] `api.js`: `cancelJob(jobId)` (DELETE, no-JSON-body path) + a `rerunConfigUrl(jobId)` helper (AC: #3, #4) — the only file with URLs.
- [x] Run library: Cancel + Re-run actions (AC: #3, #4)
  - [x] `runs.js`: a **Cancel** `<button>` on `queued` cards → `cancelJob` → reload the list; on `ApiError` show a per-card message (409 → "already started"). **Re-run with tweaks** links on done-query and failed-query cards → `rerunConfigUrl` (`offersRerun` gates to query kind).
  - [x] CSS: `button.run-card-action` reset (reads like the sibling links), a `--danger` variant for Cancel, and a `.run-card-error` message style.
- [x] Config-form prefill + re-run entry (AC: #4, #5)
  - [x] `config-form.js`: extended `openConfigForm(area, prefill=null)`; `effectiveValue(field)` uses `prefill?.[field.name]` when non-null else the schema default (covers text/number, checkbox `checked`, and select `selected`). Default (no `prefill`) path unchanged.
  - [x] `map-home.js`: `handleRerun()` on load — if `?rerun=<job_id>` present, `history.replaceState` to clear it, then `getJob(id)` → `openConfigForm(record.area, record.params)`; a missing/unknown id surfaces a message.
  - [x] `run-watch.js`: failed-footer Re-run link → `rerunConfigUrl(jobId)` for a query (a failed setup gets a plain "Back to map" link — no query form to prefill).
- [x] Tests (AC: #7)
  - [x] `tests/integration/test_app_api.py`: `DELETE` 204 + gone from `GET /jobs`; 404 unknown; 409 running (sleeper) and 409 terminal; cancel-while-busy never runs the cancelled job and the queue keeps serving.
  - [x] `tests/unit/test_app_store.py`: `delete` removes the dir + drops from `list()`; missing-id is a no-op.
  - [x] Verified end-to-end (DOM dump): queued card's Cancel removes it (API 4→3, no queued left); done/failed query Re-run opens the config form prefilled from stored params (theta/n/difficulty_cap/seed) with defaults for the rest and `?rerun` cleared; submitting created a new job with the prefilled params while the original stayed `done` (objective + result intact).

## Dev Notes

**This closes the mutating half of FR9 (run library).** 3.1 shipped the read-only list with Cancel/Re-run deliberately deferred here; 3.3 then adds restart recovery. The store still IS the runs index [Source: _bmad-output/planning-artifacts/epics-app.md#Story 3.2; #FR Coverage Map — FR9 → Epic 3].

**Cancel = delete the store record (tombstone), not queue surgery — the one real correctness point.** The in-memory `asyncio.Queue` holds job ids with no public removal, and `store.list()` (hence `GET /jobs` and the library) is driven purely by which per-job dirs exist. So `DELETE` deleting the record is what makes the job "disappear from the store and queue and the list" (epic AC): the id lingers in the queue, but when the worker pops it, `store.get` returns `None` and it hits the existing `logger.warning("queued job … has no store record; skipping")` branch and moves on — no crash, no run. Do **not** rebuild the `asyncio.Queue` to excise the id. Make the handler `async` (mirrors `stop_job`): both the handler and the worker's `queued→running` transition are synchronous with no `await` in the middle, so on the single event loop they can't interleave — a cancel either wins (job never runs) or the worker already flipped it to `running` and the handler 409s. That is AC #2's atomicity, for free, without a lock [Source: src/steeproute/app/queue.py:99-104, :209-213; src/steeproute/app/store.py:57-80; src/steeproute/app/api.py:220-245 (the async `stop_job` pattern); architecture-app.md#Category 7 — "Queued-but-not-started jobs are cancelled by removing them from the store/queue (`DELETE /jobs/{id}`)"; #Category 8].

**`DELETE` status codes.** 404 unknown (via `_require_job`), 409 if `status is not JobStatus.QUEUED` (running → Stop covers it; terminal → nothing to cancel), 204 on success — consistent with the API conventions (`409` for illegal transitions) [Source: architecture-app.md#API conventions; #Category 8]. The `api.py` docstring's "`DELETE /jobs/{id}` … arrives with Story 3.2" line is now satisfied and should be updated [Source: src/steeproute/app/api.py:6-8].

**Re-run is query-only, and the config-form author already planned the prefill seam.** The config form is the *query* form (schema-driven from the CLI arg parser); a setup job has no such form, so "Re-run with tweaks" is offered only on query runs (done or failed) — a failed setup is redone by rebuilding from the map (deliberate two-step, FR2). `config-form.js`'s `openConfigForm` docstring explicitly notes it "keeps prefill logic in one place for a later re-run-with-tweaks" — extend that entry point with a `prefill` params dict rather than adding a parallel path. Prefill semantics: `QueryParams` stores every field (mostly `null` = "use the App default"), so pre-populating with **stored non-null value else schema default** faithfully reproduces the run's effective config plus the user's explicit tweaks, and shows quality-demo defaults for the untouched fields [Source: src/steeproute/app/static/js/config-form.js:27-51, :108-127; src/steeproute/app/models.py:88-123 (QueryParams — all-None defaults) , :159-183 (JobRecord.params/area); _bmad-output/planning-artifacts/epics-app.md#UX-DR2/UX-DR4].

**Re-run navigation: `/?rerun=<job_id>`.** The config form lives on Map-home (`index.html`), so re-run lands there. `map-home.js` reads `?rerun`, fetches the job via the existing `getJob`, and opens the form directly on the stored area — it does **not** go through the map picker or `resolveArea` (the area is taken verbatim from the record; coverage isn't re-checked — if the cache was since cleared, the query fails gracefully at run time, acceptable for v1). Clear the query param afterward (`history.replaceState`). Submitting uses the unchanged `createJob({kind:"query", area, params})`, which mints a new id — so the re-run is a new job and the original is untouched (AC #5) [Source: src/steeproute/app/static/js/map-home.js:150-153; :127-132; src/steeproute/app/static/js/config-form.js:116-154; src/steeproute/app/static/js/api.js:55-67].

**Wire the two existing placeholders, don't invent new UI.** `runs.js` already gates actions by status and left `// Cancel (queued) + Re-run … are Story 3.2` — add the two actions there. `run-watch.js`'s failed footer already renders a "Re-run with tweaks" link with a placeholder `href="/"` — repoint it at `rerunConfigUrl(jobId)`. Keep all backend access in `api.js` (the only URL holder); no inline handlers; the list is a one-shot fetch (server is the source of truth) [Source: src/steeproute/app/static/js/runs.js:90-97; src/steeproute/app/static/js/run-watch.js:82-97; architecture-app.md#Frontend conventions].

### Project Structure Notes

Target tree — **edits** the daggered files (no new files) [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── api.py                         † (edit) DELETE /jobs/{id} (async, 204/404/409) + docstring
├── store.py                       † (edit) JobStore.delete(job_id)
└── static/
    └── js/
        ├── api.js                 † (edit) cancelJob + rerunConfigUrl (only file with URLs)
        ├── runs.js                † (edit) Cancel (queued) + Re-run (done/failed query) actions
        ├── config-form.js         † (edit) openConfigForm(area, prefill) — prefill inputs
        ├── map-home.js            † (edit) ?rerun=<id> → getJob → openConfigForm(area, params)
        └── run-watch.js           † (edit) failed-footer Re-run href → rerunConfigUrl
tests/
├── integration/test_app_api.py    † (edit) DELETE 204/404/409 + cancel-while-busy
└── unit/test_app_store.py         † (edit) delete removes dir + drops from list()
```

No changes to `models.py`, `queue.py`, `sse.py`, or `cli_adapter/**` — the cancel path reuses the worker's existing skip-missing-record branch, and the re-run path reuses `POST /jobs` unchanged.

### Testing

Per AGENTS.md: type-check with `uv run basedpyright <changed files>`; run `tests/unit` and `tests/integration` in **separate** invocations (wrong `conftest.py` otherwise). For the 409-on-running case use the sleeper fake CLI so a job is genuinely `running` (`_sleeper_client`); for cancel-while-busy, queue two jobs behind the sleeper and `DELETE` the second while it is `queued`, then assert it never appears as run/terminal and is absent from `GET /jobs`. Seed terminal jobs with `_seed_job` for the 409-terminal case. The frontend (Cancel removal, re-run prefill, new-job-on-submit) has no unit harness — verify end-to-end with a `javascript_exec` DOM dump against the real app (not a screenshot — Browser-pane screenshots flake / map tiles don't load in-pane), reusing the Story 2.3/3.1 seed-then-drive pattern [Source: C:\Users\yfontana\Code\steeproute\AGENTS.md#Dev environment; tests/integration/test_app_api.py:69-95 (sleeper client + poll helpers), :461-494 (`_seed_job`); _bmad-output/implementation-artifacts/app-3-1-run-library-list.md#Testing].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 3.2: Cancel queued and re-run with tweaks] — the epic AC (cancel queued via DELETE; re-run prefilled; re-run is a new job, original unchanged)
- [Source: _bmad-output/planning-artifacts/epics-app.md#UX-DR4] — run-card status-gated actions ([Cancel] queued, [Re-run with tweaks] finished/failed)
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 7 — Stop / cancel semantics] — `DELETE /jobs/{id}` removes a queued job from store/queue; Stop (not DELETE) hard-cancels a running one
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 8 — API surface; #API conventions] — the endpoint list incl. `DELETE /jobs/{id}`; 404/409 code conventions
- [Source: src/steeproute/app/api.py:6-8, :220-245] — the "arrives with Story 3.2" note; the async `stop_job` handler to mirror
- [Source: src/steeproute/app/queue.py:99-104, :209-213] — the `asyncio.Queue` (no removal) + the skip-missing-record branch that makes the tombstone cancel safe
- [Source: src/steeproute/app/store.py:57-80] — `create`/`update`/`list`/`_job_dir`; where `delete` belongs
- [Source: src/steeproute/app/static/js/config-form.js:27-51, :108-127] — `buildInput` + `openConfigForm` (the prefill seam the author reserved for re-run)
- [Source: src/steeproute/app/static/js/map-home.js:150-153] — the existing `openConfigForm(area)` call to reuse for `?rerun`
- [Source: src/steeproute/app/static/js/runs.js:90-97] — the action-gating block with the `// … are Story 3.2` marker to fill in
- [Source: src/steeproute/app/static/js/run-watch.js:82-97] — the failed-footer Re-run placeholder (`href="/"`) to repoint
- [Source: src/steeproute/app/static/js/api.js:55-73] — `createJob`/`getJob`/`stopJob`; where `cancelJob` + `rerunConfigUrl` belong
- [Source: _bmad-output/implementation-artifacts/app-3-1-run-library-list.md] — immediate predecessor; `_seed_job` + DOM-dump verification pattern; the Cancel/Re-run deferral this story picks up

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Cancel = tombstone delete, async handler for lock-free atomicity.** `DELETE /jobs/{id}` (async, mirrors `stop_job`) deletes the store record via the new `JobStore.delete` (`shutil.rmtree(..., ignore_errors=True)`); 409 if `status is not QUEUED`, 404 unknown, 204 on success. The id lingering in the `asyncio.Queue` is harmless — the worker's existing `store.get is None → skip` branch (`queue.py`) handles it, proven by `test_cancelled_job_is_skipped_and_queue_keeps_serving` (cancel a queued job behind a running sleeper, free the worker, confirm a subsequent job still reaches `running`). No `asyncio.Queue` surgery.
- **`cancelJob` bypasses `_json`** — a 204 has no body to parse, so it does its own `fetch` + `resp.ok` check, throwing `ApiError` with the `{detail}` on 409/404.
- **Re-run prefill** reuses the config-form seam its author reserved: `openConfigForm(area, prefill)` + `effectiveValue` (stored non-null wins, else schema default). `map-home.js::handleRerun` reads `?rerun`, clears it with `history.replaceState`, fetches the job, and opens the form on the stored area (no map-picker / coverage re-check). Submit is the unchanged `createJob` path → a new job id.
- **End-to-end browser verification (DOM dump, not screenshot — `/runs` has no map; per Story 2.2/2.3/3.1).** Seeded one job per state on a tmp store (`scratchpad/seed_and_run.py`), ran the real `create_app` under uvicorn, drove it via `javascript_exec`. Note: the browser caches ES modules per origin — a stale `api.js` (pre-edit) initially made `runs.js` fail its import and render zero cards; relaunching on a fresh port (new origin) cleared it. Confirmed: correct list order + status-gated actions (Cancel is a `<button>`, Re-run links `/?rerun=`, failed query has no View-routes); Cancel removes the card and drops it from `GET /jobs`; Re-run opens the form prefilled (theta 0.25 / n 3 / difficulty_cap T5 / seed 42, defaults for the rest) with `?rerun` cleared; submit enqueued a new job with those params while the original stayed `done` (objective 8421.5 + result dir intact).

### Completion Notes List

- **Backend: one endpoint + one store seam.** `DELETE /jobs/{id}` (`api.py`) + `JobStore.delete` (`store.py`) are the only backend additions; `GET /jobs`, `POST /jobs`, the params schema, the queue, and the SSE hub are untouched. Cancel is atomic against the worker without a lock (async handler on the single event loop).
- **Frontend: wired the two placeholders 3.1 left.** `runs.js` fills in the deferred Cancel (queued) + Re-run (done/failed query) actions; `run-watch.js`'s failed-footer Re-run link now points at the prefilled config form. `api.js` gained `cancelJob` + `rerunConfigUrl` (still the only URL holder). Re-run is query-only (`offersRerun`) — a failed setup is rebuilt from the map.
- **Prefill is faithful and degrades.** Stored non-null params override, everything else shows the quality-demo default; a re-run enqueues a brand-new job and never mutates the source. A since-cleared cache just fails the re-run gracefully at run time (no coverage re-check on the re-run path — acceptable for v1).
- **Validation:** `basedpyright` 0/0/0 and `ruff check` + `ruff format --check` clean on the changed Python; `tests/unit/test_app_store.py` (2 new), `tests/integration/test_app_api.py` (5 new) all pass; full offline suite green (see Change Log).

### File List

- `src/steeproute/app/api.py` (modified) — `DELETE /jobs/{job_id}` async cancel handler (204/404/409) + `Response` import + docstring update
- `src/steeproute/app/store.py` (modified) — `JobStore.delete` + `shutil` import
- `src/steeproute/app/static/js/api.js` (modified) — `cancelJob` (DELETE) + `rerunConfigUrl`
- `src/steeproute/app/static/js/runs.js` (modified) — Cancel (queued) + Re-run (done/failed query) actions; `load()` reload after cancel
- `src/steeproute/app/static/js/config-form.js` (modified) — `openConfigForm(area, prefill)` + `effectiveValue` prefill
- `src/steeproute/app/static/js/map-home.js` (modified) — `?rerun=<id>` → `getJob` → prefilled `openConfigForm`
- `src/steeproute/app/static/js/run-watch.js` (modified) — failed-footer Re-run link → `rerunConfigUrl` (query) / map (setup)
- `src/steeproute/app/static/runs.html` (modified) — list-level `#runs-status` line for cancel-failure messages
- `src/steeproute/app/static/css/app.css` (modified) — Cancel button reset + `--danger` + `.runs-status`
- `tests/integration/test_app_api.py` (modified) — 5 Story-3.2 DELETE tests (204/404/409 + cancel-while-busy)
- `tests/unit/test_app_store.py` (modified) — 2 `delete` tests
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-16 | Story drafted from epics-app.md (Story 3.2) + architecture-app.md (Cat 7/8) + the 3.1-deferred Cancel/Re-run seams. Cancel designed as a store-record delete (tombstone) leaning on the worker's existing skip-missing-record path, with an `async` DELETE handler for lock-free atomicity against the worker. Re-run scoped query-only, reusing the config-form prefill seam its author reserved, entered via `/?rerun=<id>`. Setup re-run + history deletion + queue reorder scoped out. Status → ready-for-dev. |
| 2026-07-16 | Implemented cancel-queued (`DELETE /jobs/{id}` async + `JobStore.delete` tombstone; worker's skip-missing-record path handles the lingering queue id) and re-run-with-tweaks (config-form `openConfigForm(area, prefill)` + `map-home.js` `?rerun=<id>` entry + `runs.js`/`run-watch.js` action wiring; `api.js` `cancelJob`+`rerunConfigUrl`). 7 tests added (5 integration + 2 unit); verified end-to-end in the browser (DOM dump): Cancel removes the card + drops it from `GET /jobs`; Re-run opens the form prefilled from stored params with `?rerun` cleared; submit enqueues a new job while the original is untouched. `basedpyright`/`ruff` clean; full offline suite **1030 passed, 17 deselected**, no regressions. Status → review. |
| 2026-07-16 | Code-review fix (1 finding): a failed-cancel message was appended to the card, then immediately wiped by the unconditional `load()` re-render — so a 409/404 cancel reloaded silently (AC #3 unmet), and a 404 left a phantom card. Moved the message to a new list-level `#runs-status` element (outside `#runs-list`, so `load()` doesn't clear it) and still reload so the list stays server-consistent; success clears it. Verified in-browser: a forced 404 cancel shows the message AND removes the phantom card; the happy-path cancel clears the card and the prior message. |
| 2026-07-16 | Code review (low effort, diff-only pass over the non-test hunks) found and fixed the 1 finding above; no further findings on re-check. Status → done. |
