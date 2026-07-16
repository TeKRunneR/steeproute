# Story 3.3: Restart recovery

Status: done

<!-- App track (epics-app.md). Story key `app-3-3-*` is `app-`-prefixed to avoid
     collision with the CLI track's `3-3-*` (already `done`); both share
     sprint-status.yaml. Final story of App Epic 3, after 3.2 (cancel queued &
     re-run with tweaks). Closes FR10. -->

## Story

As a user,
I want runs that were interrupted by a server restart to show up correctly,
so that the library never lies about a job still "running" after a crash or restart.

## Acceptance Criteria

1. **On boot, an interrupted `running` job becomes `failed (interrupted)`.** When the server starts, the store is scanned and any job persisted with status `running` is transitioned to `failed` with `failure_reason="interrupted"` and a `finished_at` timestamp, persisted atomically. This runs **before** the worker starts consuming the queue, so the worker never observes a stale `running` record.

2. **The in-memory queue is rebuilt from the store on boot.** Jobs persisted as `queued` are re-enqueued in creation order (the store's id ordering) so they remain `queued` and resume processing in order once the worker starts. Currently the lifespan creates an empty queue, so a queued job submitted before a restart is silently lost — this story fixes that as the queue-rebuild half of the same boot pass.

3. **Terminal and queued jobs are left untouched.** `done`, `failed`, `stopped`, and already-`queued` records are not mutated by the boot scan (only `running` → `failed`+`interrupted`). A second boot with no `running` jobs is a no-op (idempotent): an already-recovered `failed`+`interrupted` job is not re-touched.

4. **The run library renders an interrupted job as failed and offers Re-run (query only).** After recovery, a `GET /jobs` interrupted job renders in the run library as `failed` with its `interrupted` reason shown, and — for a **query** job — offers Re-run with tweaks. (A failed/interrupted *setup* job is redone by rebuilding from the map, consistent with Story 3.2 — no query form to prefill.) The frontend already reads `failure_reason` and gates Re-run to query kind, so this AC is a verification, not new UI.

5. **Scope guard.** No new API endpoint, no store/SSE schema change, no new status value (`interrupted` stays `status=failed` + `failure_reason`, per the models contract). The recovery + rebuild is a boot-time pass inside the existing lifespan reusing `store.list()`/`store.update()`/`queue.enqueue`; no partial-result recovery, no automatic re-queue of interrupted (running) jobs, no `progress.ndjson` rewrite.

6. **Tested at the seam.** Unit (`test_app_store.py`): the recovery method flips only `running` records (→ `failed`+`interrupted`+`finished_at`), returns the flipped ids, leaves `queued`/terminal records untouched, and is idempotent on a second call. Integration (`test_app_api.py`): seed a store with one `running`, two `queued`, and a `done` record, then start the app via the `TestClient` context (which runs the lifespan/boot pass) with a fast fake CLI — assert `GET /jobs` shows the ex-running job as `failed`+`interrupted`, the `done` untouched, and the two ex-queued jobs run to completion in creation order.

## Tasks / Subtasks

- [x] Add the boot recovery seam to the store (AC: #1, #3, #5)
  - [x] `JobStore.recover_interrupted() -> list[str]` — scan `list()`, and for each record with `status is JobStatus.RUNNING` set `status=FAILED`, `failure_reason="interrupted"`, `finished_at=utcnow_iso()` (only if unset), persist via `update`, and collect its id. Return the flipped ids (for the boot log). Terminal/queued records untouched; a second call finds no `running` and returns `[]` (idempotent).
- [x] Wire recovery + queue rebuild into the lifespan, before the worker starts (AC: #1, #2)
  - [x] In `main._make_lifespan`, after building `store`/`queue` and before `asyncio.create_task(worker.run())`: call `store.recover_interrupted()` (log the count if non-empty), then re-enqueue every `queued` record from `store.list()` (creation order) via `queue.enqueue(record.id)`. Log the rebuilt-queue depth.
- [x] Verify the run-library rendering of an interrupted job (AC: #4)
  - [x] Confirmed (no code change) `runs.js` shows `failed · interrupted` (metricText) and offers Re-run for a query interrupted job / not for a setup one (offersRerun) — deterministic display logic already shipped in Story 3.1/3.2.
- [x] Tests (AC: #6)
  - [x] `tests/unit/test_app_store.py`: `recover_interrupted` flips running→failed+interrupted (+finished_at, +returned id), leaves queued/done/stopped/failed untouched, idempotent on re-call.
  - [x] `tests/integration/test_app_api.py`: seed running + 2 queued + done on a tmp store; enter `TestClient(create_app(store_root=..., build_argv=<fast fake>))`; assert boot flips running→failed+interrupted, done untouched, and the two queued jobs reach a terminal state in creation order.

## Dev Notes

**This closes FR10 (restart recovery) and the queue-rebuild half of Category 2, finishing App Epic 3.** The store IS the runs index, so recovery is a pure store scan on boot — there is no separate index to reconcile [Source: _bmad-output/planning-artifacts/epics-app.md#Story 3.3: Restart recovery; #FR Coverage Map — FR10 → Epic 3; architecture-app.md#Category 5].

**Two things happen on boot, both before the worker starts.** (1) *Recovery:* an ungraceful kill (crash / `kill -9` / OS shutdown) leaves a job persisted as `running` — the process died without running the lifespan shutdown. The next boot's scan flips it to `failed`+`interrupted` so the library never lies. (2) *Queue rebuild:* the in-memory `asyncio.Queue` is empty on every boot, so `queued` records on disk must be re-enqueued or they never run. Both are a single pass over `store.list()` in the lifespan, sequenced **before** `asyncio.create_task(worker.run())` so the worker never pops a stale `running` id and the rebuilt queue order is deterministic [Source: src/steeproute/app/main.py:91-115 (the lifespan; currently `JobQueue()` is created empty, no scan); architecture-app.md#Category 2 — "in-memory `asyncio.Queue`, rebuilt from the persisted store on boot"].

**`interrupted` is not a status — it is `status=failed` + `failure_reason="interrupted"`.** The `JobRecord.failure_reason` field already exists exactly for this; do not add a status value or a schema field [Source: src/steeproute/app/models.py:12-13, :180 (the field, defined "restart recovery lands in Story app-3-3"); :37-48 (JobStatus — no new value)].

**The graceful-shutdown path already writes the identical terminal state — recovery only catches the *ungraceful* kill.** The worker's `CancelledError` branch (lifespan shutdown / Ctrl-C) already sets `failed`+`failure_reason="interrupted"` for the in-flight job and even names this story: "the same state restart recovery (Story app-3-3) would set on the next boot." So a clean shutdown needs no recovery; the boot scan exists for the crash where that branch never ran. Match its field values verbatim (`failure_reason="interrupted"`) so both paths are indistinguishable to the library [Source: src/steeproute/app/queue.py:282-298].

**Recovery belongs in `store.py`.** The architecture assigns "restart recovery" to the store module and its unit coverage to `test_app_store.py`; the lifespan only orchestrates (call the store method, then enqueue). Reuse `list()`/`update()`/`utcnow_iso()` — no new persistence primitive [Source: architecture-app.md#Complete project tree (`store.py … restart recovery`; `test_app_store.py … restart→failed(interrupted)`); #Cross-Cutting Concerns — "boot-time running→failed(interrupted) transition … shared by the worker, the store …"].

**Frontend is already done (Story 3.1/3.2).** `runs.js::metricText` renders `exit code … · interrupted` from `failure_reason`, and `offersRerun` gates Re-run to query kind (done/failed) — an interrupted query card already offers Re-run, an interrupted setup card does not. Verify, don't rebuild [Source: src/steeproute/app/static/js/runs.js:43-53 (metricText, with the "boot-interrupted job carries failure_reason=interrupted (Story 3.3)" comment), :72-77 (offersRerun), :131-134].

### Project Structure Notes

Target tree — **edits** the daggered files (no new files) [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── store.py                       † (edit) JobStore.recover_interrupted() -> list[str]
└── main.py                        † (edit) lifespan: recover + rebuild queue before worker start
tests/
├── unit/test_app_store.py         † (edit) recover_interrupted: flip-only-running, idempotent
└── integration/test_app_api.py    † (edit) boot recovery + queue rebuild via TestClient lifespan
```

No changes to `models.py` (`failure_reason` already exists), `queue.py` (graceful path already sets the same state), `api.py`, `sse.py`, `cli_adapter/**`, or any frontend file (the run-library rendering already handles `interrupted`).

### Testing

Per AGENTS.md: type-check with `uv run basedpyright <changed files>`; run `tests/unit` and `tests/integration` in **separate** invocations (wrong `conftest.py` otherwise). Seed records directly on a tmp store (the `_seed_job` pattern) — no worker/subprocess is needed to exercise the store method. For the integration boot test, seed the store on disk first (a `running` record, two `queued`, one `done`), then construct the app with that `store_root` and a fast fake `build_argv` (the echo/fast fake used by existing queue tests, not the sleeper) and enter the `TestClient` context so the lifespan runs; poll `GET /jobs` until the queued jobs reach a terminal state, then assert order and the flipped statuses [Source: C:\Users\yfontana\Code\steeproute\AGENTS.md#Dev environment; tests/integration/test_app_api.py:76-102 (fake-CLI client builders + create_app injection), :530-563 (`_seed_job` — build a JobRecord straight onto the store)].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 3.3: Restart recovery] — the epic AC (running→failed+interrupted on boot; queued rebuilt & resume in order; renders as failed+interrupted with Re-run)
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 5 — Job persistence & store] — "on boot any job left `running` is marked `failed (interrupted)`"; the store IS the runs index
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 2 — Worker & concurrency] — the in-memory queue is rebuilt from the persisted store on boot
- [Source: _bmad-output/planning-artifacts/architecture-app.md#JSON & data-format conventions] — `interrupted` = `status=failed` + `failure_reason`, not a status
- [Source: src/steeproute/app/main.py:77-115] — the lifespan to extend (currently builds an empty `JobQueue()`, starts the worker with no boot scan)
- [Source: src/steeproute/app/store.py:63-92, :125-135] — `update`/`list`/`_write_atomic` to reuse for the recovery scan; where `recover_interrupted` belongs
- [Source: src/steeproute/app/models.py:37-48, :159-183] — `JobStatus` (no new value), `JobRecord.failure_reason`/`finished_at`; `utcnow_iso`
- [Source: src/steeproute/app/queue.py:282-298] — the worker's graceful-shutdown branch that already writes `failed`+`interrupted` (the state recovery mirrors)
- [Source: src/steeproute/app/static/js/runs.js:43-53, :72-77] — the run-library rendering of `failure_reason` + query-gated Re-run (already handles interrupted)
- [Source: _bmad-output/implementation-artifacts/app-3-2-cancel-queued-and-re-run-with-tweaks.md] — immediate predecessor; `_seed_job` + TestClient-lifespan patterns; Re-run query-only rationale

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Recovery lives in `store.py`, orchestration in the lifespan.** `JobStore.recover_interrupted()` scans `list()`, flips each `running` → `failed`+`failure_reason="interrupted"`+`finished_at` (only stamped if unset, so idempotent), persists via the existing atomic `update`, and returns the flipped ids. The `main._make_lifespan` boot block calls it, then re-enqueues every `queued` record from `store.list()` (id order = creation order), **before** `asyncio.create_task(worker.run())` — so the worker never pops a stale `running` id and the resume order is deterministic. No new persistence primitive, no new status/schema.
- **Graceful vs. ungraceful.** The worker's `CancelledError` branch (queue.py:282-298) already writes the identical `failed`+`interrupted` state on a clean shutdown; recovery exists only for the *ungraceful* kill where that branch never ran. Field values match verbatim so both paths are indistinguishable to the library.
- **Frontend was already complete.** `runs.js::metricText` renders `failed · interrupted` from `failure_reason` and `offersRerun` gates Re-run to query kind (done/failed) — an interrupted query card offers Re-run, an interrupted setup card does not. Zero frontend change; AC #4 verified by inspection (deterministic display, shipped in 3.1/3.2).
- **Type-check hiccup:** initial unit test used mypy-style `# type: ignore[union-attr]` which basedpyright doesn't honor (4 `reportOptionalMemberAccess` errors); rewrote the assertions to `loaded = store.get(...); assert loaded is not None` in a loop. Clean after.

### Completion Notes List

- **Two files of production code, both small.** `store.py` gained `recover_interrupted()` (+`JobStatus`/`utcnow_iso` imports, docstring update); `main.py` gained the boot reconciliation block in the lifespan (+`JobStatus` import, docstring update). No changes to `models.py` (`failure_reason` already existed), `queue.py`, `api.py`, `sse.py`, `cli_adapter/**`, or any frontend file.
- **This closes FR10 and the queue-rebuild half of Category 2, finishing App Epic 3.** Before this story the lifespan built an *empty* queue, so a `queued` job submitted before a restart was silently lost — now fixed alongside recovery in the same boot pass.
- **Validation:** `basedpyright` 0/0/0 and `ruff check` + `ruff format --check` clean on the 4 changed files; 3 new store unit tests + 1 new integration test all pass; full offline suite **1034 passed, 17 deselected** (was 1030), no regressions.

### File List

- `src/steeproute/app/store.py` (modified) — `JobStore.recover_interrupted()` + `JobStatus`/`utcnow_iso` imports + module docstring
- `src/steeproute/app/main.py` (modified) — lifespan boot reconciliation (recover interrupted + rebuild queue before worker start) + `JobStatus` import + docstrings
- `tests/unit/test_app_store.py` (modified) — 3 `recover_interrupted` tests (flip-only-running, leaves-others, idempotent)
- `tests/integration/test_app_api.py` (modified) — boot recovery + queue-rebuild-in-order test (`_FAKE_CLI_ORDER`, `_seed_status`)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-16 | Story drafted from epics-app.md (Story 3.3) + architecture-app.md (Cat 2/5, data-format). Scoped as a boot-time pass in the existing lifespan: `JobStore.recover_interrupted()` flips `running`→`failed`+`interrupted` (mirroring the worker's graceful-shutdown branch), plus queue rebuild (re-enqueue `queued` records in order) before the worker starts. Frontend already renders interrupted + gates Re-run to query kind (Story 3.1/3.2) — verification only. No new endpoint/status/schema. Status → ready-for-dev. |
| 2026-07-16 | Implemented `JobStore.recover_interrupted()` (running→failed+interrupted+finished_at, idempotent, returns flipped ids) and the `main` lifespan boot reconciliation (recover + rebuild queue from persisted `queued` records, before the worker starts). Frontend unchanged (runs.js already renders interrupted + query-gated Re-run — verified by inspection). 4 tests added (3 store unit + 1 integration boot recovery/queue-rebuild-in-order). `basedpyright`/`ruff` clean on the 4 changed files; full offline suite **1034 passed, 17 deselected**, no regressions. Status → review. |
| 2026-07-16 | Code review (low effort, diff-only pass over the non-test hunks: `main.py` + `store.py`) found no correctness issues — recovery/queue-rebuild sequencing, idempotency, and status filtering all checked out. Status → done. |
