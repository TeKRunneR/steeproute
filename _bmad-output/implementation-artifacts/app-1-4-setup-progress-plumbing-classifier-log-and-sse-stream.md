# Story 1.4: Setup progress plumbing — classifier, log, and SSE stream

Status: done

<!-- App track (epics-app.md). Story key `app-1-4-*` is `app-`-prefixed to avoid
     collision with the CLI track's `1-4-*`; both share sprint-status.yaml. -->

## Story

As a developer,
I want the worker to classify setup stdout into a progress model, persist it, and expose it over SSE,
so that a client can watch a running build live and a reconnecting client can catch up.

## Acceptance Criteria

1. A stateful classifier `cli_adapter.progress_parse` maps each raw **setup** stdout line to the unified `ProgressModel` (`phase, stage_name, stage_index, stage_total, grasp, elapsed, log_tail`), deriving `stage_index`/`stage_total` positionally from the known ordered setup stage list (7 stages) — the wire carries only a stage name, never `n/total`. `grasp` is always `null` for setup jobs. The classifier tolerates a setup **cache-hit** (summary block only, zero stage lines).
2. As the worker drains a running setup subprocess's stdout, each line that advances the model is classified and appended to the job's append-only `progress.ndjson` (one JSON object per line) in the job's store dir, via an atomic-append discipline consistent with the store.
3. `GET /jobs/{id}/events` returns an SSE stream that first replays the persisted `progress.ndjson` snapshot as named `progress` events, then streams the live tail of subsequent events (snapshot-then-tail), with no gap or duplication across the handoff. Unknown id → 404.
4. When the job reaches a terminal state (`done`/`failed`), a final named `status` event carrying the terminal status is sent and the stream closes; a client that connects after the job already finished still gets the full snapshot followed immediately by the terminal `status` event.
5. A long idle stream stays alive via periodic SSE heartbeat comments (framework-native keepalive — see Dev Notes), so a multi-minute build with sparse output does not time out the connection.
6. The classifier is unit-tested against the pinned spike fixture `tests/fixtures/app_stdout/setup_cache_miss.stdout.txt` (Story 1.1): its setup lines map to the expected `ProgressModel` field values, including the `dem-resolve` within-stage `  tile i/N` line landing in `log_tail` without advancing the stage.
7. Scope guard: **setup flavour only.** Query non-solve stages and GRASP `progress:` lines (Flavour B/C → `grasp` populated) are NOT classified here — they land in Story 2.2, which extends the same classifier and `ProgressModel`. No Stop/`stopped`, no `/regions`, no query kind. All CLI-stdout-format knowledge stays inside `cli_adapter`.

## Tasks / Subtasks

- [x] Define `ProgressModel` and the setup stage list (AC: #1, #7)
  - [x] Add `ProgressModel` to `steeproute/app/models.py` with the pinned field names; `grasp` is `GraspProgress | None` (a `{iter, best_cost}` model, present-as-`null` for setup). Added a `Phase` enum (`setup|query|solve`).
  - [x] Put the ordered setup stage names (`SETUP_STAGES`) in `cli_adapter/progress_parse.py` — it is CLI-format knowledge.
- [x] Build the setup classifier (AC: #1, #6, #7)
  - [x] `steeproute/app/cli_adapter/progress_parse.py`: a stateful parser fed one stdout line at a time, returning an updated `ProgressModel` (or `None` for a blank line) per line. Handles A1 stage-start, A2 `  tile i/N` within-stage, A3 stage-done, A4 `steeproute-setup: cache-miss|cache-hit` summary.
  - [x] Export the classifier (`SetupProgressParser`, `progress_parser_for`) through `cli_adapter/__init__.py`'s public interface.
- [x] Persist progress to `progress.ndjson` (AC: #2)
  - [x] Extend `store.py` with `append_progress` + `read_progress` for `progress.ndjson` (append-only; distinct from the atomic `job.json` rewrite; tolerant of a partial trailing line).
- [x] Wire the classifier into the worker's stdout drain (AC: #2, #4)
  - [x] In `queue.py`, replaced the plain stdout-tail drain with a classify → append-to-ndjson → publish-to-hub path (`_consume_stdout`); kept the bounded `stdout_tail`/`stderr_tail` and the concurrent stderr drain (Story-1.3 deadlock fix preserved). Publish a terminal `StatusEvent` on every terminal transition (done/failed/spawn-fail/interrupted/worker-exception).
- [x] SSE hub + endpoint (AC: #3, #4, #5)
  - [x] `steeproute/app/sse.py`: an in-process per-job hub (asyncio fan-out) the worker publishes to and the endpoint subscribes to; `ProgressEvent(seq, model)` / `StatusEvent` envelopes; snapshot-then-tail dedupe by `seq`.
  - [x] `api.py`: `GET /jobs/{id}/events` using `response_class=EventSourceResponse`, yielding `ServerSentEvent(event="progress")` for each snapshot+live item and a final `ServerSentEvent(event="status")` on terminal; 404 for unknown id via an `Annotated[..., Depends(_require_job)]` guard (before the stream starts).
  - [x] Wired the hub into `main.lifespan` (create it, pass to `Worker`, expose on `app.state.progress_hub`) alongside store/queue.
- [x] Tests (AC: all)
  - [x] `tests/unit/test_app_progress_parse.py` (8 tests): drives the classifier over `setup_cache_miss.stdout.txt`; asserts per-line `ProgressModel` fields, positional stage index over the 7-stage list, `tile` → `log_tail`, cache-hit-with-no-stages tolerance, blank-line skip, factory setup/query behaviour.
  - [x] `tests/integration/test_app_sse.py` (3 tests): a fake CLI emitting real setup stage lines → connect after-terminal (snapshot replay) and immediately (live tail); asserts snapshot-then-tail count (no gap/dupe), named `progress`/`status` events, terminal close/ordering, 404 for unknown id.

## Dev Notes

**This is step 3 of the architecture's implementation sequence** — progress plumbing (classifier → model → persisted log → SSE), built against the simpler `setup` flavour on top of Story 1.3's proven job runner [Source: architecture-app.md#Decision Impact Analysis]. Categories 3 (progress ingestion), 4 (SSE transport), and 5 (persistence) are one triangle: the log is written by the worker, persisted by the store, replayed by the SSE endpoint [Source: architecture-app.md#Cross-component dependencies].

**The classifier spec is already pinned — do not guess line shapes.** Story 1.1 captured real setup stdout and wrote the authoritative inventory. Build `progress_parse.py` directly against it [Source: tests/fixtures/app_stdout/format-inventory.md; tests/fixtures/app_stdout/setup_cache_miss.stdout.txt]. The load-bearing facts:
- Stage lines carry **a name only, no `n/total`** — derive `stage_index`/`stage_total` positionally from the known ordered setup stage list: `osm-download, trail-filter, polyline-smoothing, resampling, dem-resolve, elevation-sampling, cache-write` (7 stages) [Source: format-inventory.md#Key finding: no n/total on the wire].
- A1 start: `stage: <name>[ (<note>)] ...` — strip the optional ` (<note>)`; canonical `stage_name` is the text between `stage: ` and the first ` (` or ` ...`.
- A2 within-stage: `  tile <i>/<N>` (2-space indent, counter starts at 0) → append to `log_tail`, do **not** advance `stage_index`.
- A3 done: `stage: <name>: <elapsed> s` (`%.2f`) → record `elapsed`.
- A4 summary: `steeproute-setup: cache-miss|cache-hit` (+ `cache_key_hash`/`entry`/`elapsed` lines) → terminal marker. A **cache-hit** emits this block and *nothing else* — the classifier must produce a coherent model with zero stage lines [Source: format-inventory.md#Flavour A].
- Setup emits no `progress:` line ⇒ `grasp` stays `null` [Source: format-inventory.md#Flavour A].

**The `ProgressModel` is the single source of truth, defined in full now** [Source: architecture-app.md#SSE event conventions]: `{phase, stage_name, stage_index, stage_total, grasp, elapsed, log_tail}`; `grasp` is `{iter, best_cost}|null` — **present-as-null, never omitted**. Story 2.2 populates `grasp` and the query stage list against the same model; define fields now so 2.2 extends rather than redefines. `log_tail` is a bounded rolling window of recent raw lines.

**SSE via FastAPI-native `EventSourceResponse` (0.139) — heartbeat is free.** Use `from fastapi.sse import EventSourceResponse, ServerSentEvent`; put `response_class=EventSourceResponse` on the endpoint and `yield ServerSentEvent(event="progress", data=model)` / `yield ServerSentEvent(event="status", data=...)`; declare the return type so Pydantic serializes each item [Source: .venv/.../fastapi/.agents/skills/fastapi/references/streaming.md]. **The routing layer auto-inserts a keepalive comment (`: ping`) whenever the generator is idle past `fastapi.sse._PING_INTERVAL` (15 s)** — do NOT hand-roll a heartbeat loop; just don't let the generator block forever and close it on terminal status [Source: .venv/.../fastapi/sse.py:230-236; .venv/.../fastapi/routing.py:577-599]. This is the whole of AC #5. No `sse-starlette` dependency [Source: architecture-app.md#Category 4].

**Snapshot-then-tail without a gap.** On connect: subscribe to the hub *before* reading the `progress.ndjson` snapshot, then replay the snapshot, then drain live events — so an event emitted during snapshot-read is not lost (dedupe by a monotonic sequence number if snapshot and live can overlap). If the job is already terminal at connect time, the snapshot IS the whole stream: replay it, emit the terminal `status`, close. A per-job in-memory asyncio hub (`sse.py`) is the transport; the persisted ndjson makes reconnection cheap without `Last-Event-ID` resume [Source: architecture-app.md#Category 4; #Gap Analysis — SSE reconnection semantics].

**Two output streams stay separate** [Source: architecture-app.md#Process patterns]: the classified CLI stdout is *data* → `progress.ndjson` + SSE; the server's own `logging` is operational and never merged in. stderr keeps its Story-1.3 role as the bounded failed-job diagnostic tail — it is not a classifier input [Source: format-inventory.md#Stream discipline].

**Persistence discipline** [Source: architecture-app.md#Category 5; src/steeproute/app/store.py]: `job.json` stays an atomic temp-file+`os.replace` rewrite; `progress.ndjson` is **append-only** (open in append mode, one JSON object per line). Keep both under the existing per-job dir `<store_root>/<job_id>/`.

**Worker wiring.** The worker currently drains stdout into a bounded tail only [Source: src/steeproute/app/queue.py:151-157]. Swap the stdout drain for classify→append→publish; keep draining stderr concurrently (the Story-1.3 deadlock fix must not regress — never read only one pipe) [Source: src/steeproute/app/queue.py:77-90; app-1-3 Completion Notes]. The hub is constructed in `main.lifespan` and passed to `Worker` (mirror the existing `store`/`queue` injection) [Source: src/steeproute/app/main.py:59-77].

### Project Structure Notes

Target tree — this story creates the **starred** files (rest are prior/later stories) [Source: architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── models.py              ★ (edit) add ProgressModel + GraspProgress
├── store.py               ★ (edit) append/read progress.ndjson
├── queue.py               ★ (edit) classify stdout → ndjson + hub publish
├── sse.py                 ★ per-job in-process SSE hub (fan-out, snapshot-then-tail)
├── api.py                 ★ (edit) GET /jobs/{id}/events
├── main.py                ★ (edit) create hub in lifespan, pass to Worker, app.state
├── cli_adapter/
│   ├── __init__.py        ★ (edit) export progress classifier
│   └── progress_parse.py  ★ setup stdout line-classifier → ProgressModel
```

- `progress_parse.py` is CLI-format knowledge → it lives **inside `cli_adapter`** and is the only place that knows setup line shapes; `ProgressModel` (a plain data shape) lives in `models.py` and is imported by the classifier [Source: architecture-app.md#The load-bearing rule; #Complete project tree].
- `api.py` orchestrates store + hub; the worker uses `cli_adapter` + `store` + hub [Source: architecture-app.md#Internal boundaries].

### Testing

Per AGENTS.md: `uv run basedpyright <files>`; run `tests/unit` and `tests/integration` in **separate** invocations (never mixed — wrong conftest). App tests use FastAPI's `TestClient` and a **fake/echo subprocess** — no real solver/network run [Source: architecture-app.md#Development workflow]. For SSE, `TestClient` as a context manager runs the `lifespan` (and worker); the classifier unit test needs no server — feed it fixture lines directly. The fake CLI for the SSE test should print the **real** setup stage-line shapes (copy from `setup_cache_miss.stdout.txt`) so the classifier path is genuinely exercised. The existing `test_app_api.py` job-lifecycle tests (whose fake CLI prints only `steeproute-setup: cache-miss (fake)`) must stay green — the added classification path must not break the status/exit-code assertions [Source: tests/integration/test_app_api.py].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 1.4: Setup progress plumbing — classifier, log, and SSE stream] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 3 — Progress ingestion & unified model] — the classifier + unified model
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 4 — Transport: FastAPI native SSE, snapshot-then-tail] — SSE transport, no sse-starlette
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 5 — Job persistence & store] — progress.ndjson, atomic/append discipline
- [Source: _bmad-output/planning-artifacts/architecture-app.md#SSE event conventions] — ProgressModel field names, named progress/status events, grasp present-as-null
- [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule] — classifier belongs in cli_adapter
- [Source: tests/fixtures/app_stdout/format-inventory.md] — the classifier spec (line shapes → fields, positional stage index, cache-hit tolerance)
- [Source: tests/fixtures/app_stdout/setup_cache_miss.stdout.txt] — the pinned unit-test input
- [Source: src/steeproute/app/queue.py:77-180] — worker drain path to extend (keep the concurrent stderr drain)
- [Source: src/steeproute/app/store.py] — persistence to extend with progress.ndjson
- [Source: src/steeproute/app/main.py:48-98] — lifespan/injection pattern for the new hub
- [Source: src/steeproute/app/api.py] — endpoints to extend with the SSE route
- [Source: .venv/Lib/site-packages/fastapi/sse.py; .../fastapi/.agents/skills/fastapi/references/streaming.md] — native `EventSourceResponse`/`ServerSentEvent` API + auto keepalive
- [Source: _bmad-output/implementation-artifacts/app-1-3-job-store-and-single-worker-queue.md] — the runner this builds on (stderr-drain deadlock fix, injection seams)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **FastAPI-native SSE keepalive is free (0.139).** `EventSourceResponse` used as `response_class` on a `yield`-ing endpoint auto-inserts a `: ping` keepalive comment whenever the generator idles past `fastapi.sse._PING_INTERVAL` (15 s), inserted in the routing layer (`fastapi/routing.py`). So AC #5 needed no hand-rolled heartbeat loop — the endpoint just streams and closes on terminal. Confirmed against the installed 0.139 source, not memory.
- **404-before-stream via a dependency.** A generator endpoint can't cleanly raise a pre-stream 404 (the 200 stream has already started). Resolved by moving the existence check into an `Annotated[JobRecord, Depends(_require_job)]` guard, which runs and can 404 before the streaming response begins. Used `Annotated[...]` (not a `Depends()` default value) to stay clean under basedpyright's `reportCallInDefaultInitializer`.
- **Snapshot/tail seq stays in lock-step for free.** Because the single worker (concurrency = 1) is the sole appender to `progress.ndjson`, its running `seq` equals the persisted line count. The endpoint reads a snapshot of length `L` (covering seq `0..L-1`) and skips any live event with `seq < L` — no gap, no duplicate — without any cross-process coordination. Asserted by pinning the exact progress-event count (8) regardless of the snapshot/live split.
- **Existing fake CLIs are inert to the classifier.** The Story-1.3 queue/api fake CLIs print `stage: fake-stage ... (tag)` / `steeproute-setup: cache-miss (tag)`, neither of which matches the A1 (`… ...$`) or A3 (`… : <float> s$`) rules, so they land in `log_tail` only and those tests stayed green unchanged.

### Completion Notes List

- **Progress plumbing stood up** on Story 1.3's runner: `ProgressModel`+`GraspProgress`+`Phase` (models), `SetupProgressParser`+`progress_parser_for`+`SETUP_STAGES` (cli_adapter — the 4th CLI-adapter seam), `append_progress`/`read_progress` over append-only `progress.ndjson` (store), an in-process `ProgressHub` fan-out (sse.py), the worker's classify→append→publish stdout path + terminal `StatusEvent`, and `GET /jobs/{id}/events` snapshot-then-tail.
- **CLI-adapter boundary respected:** all stdout-line-format knowledge is confined to `cli_adapter/progress_parse.py`; `ProgressModel` (a plain data shape) lives in `models.py`; the worker/endpoint import only the public `cli_adapter` interface.
- **Scope held (AC #7):** setup flavour only — `grasp` is always `null`; query stages + GRASP `progress:` classification are left for Story 2.2 (the factory raises `NotImplementedError` for `query`, which never reaches the worker anyway since the API rejects it 422). No Stop/`stopped`, no `/regions`.
- **Positional stage index (the pinned spike finding):** stage lines carry a name only; `stage_index` is incremented per A1 start and `stage_total` = `len(SETUP_STAGES)` = 7. The `dem-resolve` `  tile i/N` line feeds `log_tail` without advancing the stage; a cache-hit (summary-only, zero stage lines) yields a coherent model.
- **Validation:** `basedpyright src/steeproute/app` + new tests 0/0; `ruff` clean. Full offline suite **955 passed, 17 deselected** (~6m28s), no regressions (944 baseline → 955, +11: 8 classifier + 3 SSE).
- **Code-review fixes (low-effort diff pass, 2 findings, both fixed, both in `api.py`):**
  1. *Duplicated 404 lookup:* `get_job` re-implemented the same store-get→404 pattern already used by the new `_require_job` SSE dependency. Fixed by having `get_job` depend on `_require_job` too, so the not-found error shape lives in one place. No behavior change (same 404 body); regression: existing `test_get_unknown_job_returns_404`.
  2. *Duplicated status-payload shape:* the SSE endpoint's already-terminal branch (fed from a `JobRecord`) and its live-tail branch (fed from a `StatusEvent`) each hand-built the same `{status, exit_code, failure_reason}` dict. Fixed by widening `_status_payload` to take the three primitive fields so both branches call it. No behavior change; regression: existing `test_sse_snapshot_replay_after_terminal` + `test_sse_live_tail_streams_progress_then_status`.

### File List

- `src/steeproute/app/models.py` (modified) — added `Phase` enum, `GraspProgress`, `ProgressModel`
- `src/steeproute/app/cli_adapter/progress_parse.py` (new) — setup stdout classifier + `SETUP_STAGES` + `progress_parser_for`
- `src/steeproute/app/cli_adapter/__init__.py` (modified) — export the classifier through the public interface
- `src/steeproute/app/store.py` (modified) — `append_progress` / `read_progress` over append-only `progress.ndjson`
- `src/steeproute/app/sse.py` (new) — in-process `ProgressHub` fan-out + `ProgressEvent`/`StatusEvent`
- `src/steeproute/app/queue.py` (modified) — worker classify→append→publish stdout path; terminal status publish; `hub` injection
- `src/steeproute/app/api.py` (modified) — `GET /jobs/{id}/events` SSE (snapshot-then-tail) + 404 guard dependency
- `src/steeproute/app/main.py` (modified) — create `ProgressHub` in lifespan, pass to `Worker`, expose on `app.state`
- `tests/unit/test_app_progress_parse.py` (new) — classifier vs the pinned setup fixture
- `tests/integration/test_app_sse.py` (new) — SSE snapshot-then-tail, named events, terminal, 404
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-15 | Story drafted from epics-app.md + architecture-app.md + Story 1.1 fixtures. Status → ready-for-dev. |
| 2026-07-15 | Implemented setup progress plumbing: classifier + `ProgressModel` + append-only `progress.ndjson` + in-process SSE hub + `GET /jobs/{id}/events` snapshot-then-tail; 11 new tests; full suite green (955). Status → review. |
| 2026-07-15 | Code review (low effort): fixed 2 duplication findings in `api.py` (404 lookup, status-payload shape). No behavior change; existing tests re-verified green. Status → done. |
