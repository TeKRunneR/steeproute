# Story 2.2: Query-flavour progress (GRASP + non-solve stages)

Status: done

<!-- App track (epics-app.md). Story key `app-2-2-*` is `app-`-prefixed to avoid
     collision with the CLI track's `2-2-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want query progress to show the solver's best-so-far cost and iteration alongside stage progress,
so that I can judge how a long solve is going, CLI-honestly.

## Acceptance Criteria

1. **Query non-solve stages advance `stage n/total`.** The query classifier maps the query stage lines (same A1/A3 `stage: <name> ...` / `stage: <name>: <t> s` shapes as setup) to `phase=query` and derives `stage_index`/`stage_total` **positionally** over the known 6-stage query order (`load-prepared-area`, `elevation-reshape`, `trail-filter`, `climb-detection`, `climb-contraction`, `validate-render`) — never parsed from the wire (the wire carries a name only). The `steeproute: cache-hit cache_key_hash: <hex>` cue line and the `--- Run summary ---` block feed `log_tail` only and never advance a stage.

2. **GRASP events populate `grasp={iter, best_cost}` and set `phase=solve`.** `progress:` lines are classified in both shapes — single-process (`iter=`/`best_objective=`) and parallel (`workers=`/`iters=`/`best_worker_objective=`), disambiguated by the first token after `progress: ` — mapping to `grasp.iter`, `grasp.best_cost`, and `elapsed`. A `progress:` line does **not** advance `stage_index` (the solve happens between the `climb-contraction` done line and the `validate-render` start line).

3. **`grasp` is present only during the solve phase, never reserved.** Outside the solve phase `grasp` stays `null` (present-as-null, never omitted): the `validate-render` stage start that follows the solve returns `phase` to `query` and resets `grasp` to `null`. Setup jobs remain always-`null`. `stagnation`/`eta` (single) have no model field and are dropped (or land in `log_tail` via the raw line).

4. **Run-watch shows the readout during solve and hides it otherwise.** On the query flavour, the best-so-far-cost + iteration line appears during the solve phase and is absent before/after it and for setup jobs. The frontend already renders `grasp` and `stage n/total` conditionally (built forward-compatibly in Story 1.5) — verify end-to-end that a real query drives the line on/off correctly; add frontend changes only if a gap is found.

5. **Unit-tested against both pinned query fixtures.** The classifier is tested over `query_workers1.stdout.txt` (single-process GRASP) **and** `query_workers4.stdout.txt` (parallel GRASP), asserting: query stage lines → expected `stage_index/total` and `phase=query`; single vs parallel GRASP disambiguation → correct `grasp`/`phase=solve`/`elapsed`; the cache-hit cue and summary lines → `log_tail` only; and the post-solve `validate-render` start → `phase=query` with `grasp` reset to `null`.

6. **Scope guard.** Classifier + its tests only. No result iframe / static result mount (Story 2.3), no run-library changes (Epic 3). The **setup** classifier and its tests stay unchanged. `progress_parser_for(JobKind.QUERY)` now returns the full stage/GRASP-aware parser (replacing Story 2.1's minimal log-tail-only stand-in). The `ProgressModel`/`GraspProgress`/`Phase` shapes are already defined (Story 1.4) — extend behaviour, do not redefine them.

## Tasks / Subtasks

- [x] Extend the query classifier in `cli_adapter/progress_parse.py` (AC: #1, #2, #3, #6)
  - [x] Add `QUERY_STAGES` (the 6-stage ordered tuple) alongside `SETUP_STAGES`.
  - [x] Reuse the existing module-level `_STAGE_START` / `_STAGE_DONE` regexes for query B1/B3 lines (identical shapes — do not duplicate them). Factored the shared positional stage tracking into a `_StageParser` base; `SetupProgressParser`/`QueryProgressParser` are the two `@final` subclasses (regexes stay module-level, matched once).
  - [x] Add `progress:` classification: two regexes (single `iter=… best_objective=…`, parallel `workers=…/… iters=… best_worker_objective=…`), disambiguated by the first token; set `phase=solve`, populate `grasp`, update `elapsed`.
  - [x] Make a stage-start reset `grasp` to `None` and set `phase=query`, so a solve→`validate-render` transition drops the readout (AC #3) — `QueryProgressParser._enter_stage` override.
  - [x] Replace the minimal `QueryProgressParser` (log-tail-only, Story 2.1) with this stage/GRASP-aware one; `progress_parser_for` already routes `QUERY` here.
- [x] Unit tests in `tests/unit/test_app_progress_parse.py` (AC: #5)
  - [x] Drive the classifier over both `query_workers1` and `query_workers4` fixtures; assert stage progression (6 stages, positional), single/parallel GRASP fields, cache-hit cue + summary → `log_tail`, and the post-solve phase/grasp reset.
  - [x] Keep the existing setup and factory tests green; replaced the two Story-2.1 "minimal query parser" tests whose premise ("no stage/grasp") is now false with the full Story-2.2 suite (mirrors how Story 2.1 replaced `test_query_kind_rejected_422`).
- [x] Verify end-to-end (AC: #4)
  - [x] Drove a query job (fake CLI replaying `query_workers1.stdout.txt`) through the real classifier → ndjson → SSE → Run-watch page. During solve: `PHASE: solve`, `STAGE: climb-contraction (5/6)`, GRASP line shown (`best-so-far cost: 9488 · iteration: 12122`). After solve: `PHASE: query`, `STAGE: validate-render (6/6)`, GRASP line hidden (`graspHidden:true`), footer `done`. Zero frontend changes needed — the Story-1.5 `renderProgress` conditional was already correct.

## Dev Notes

**This closes FR6's query extension** — Epic 1 established the flavour-agnostic progress frame against setup; this layers the query non-solve stages and the GRASP best-cost/iteration readout onto the *same* `ProgressModel` and the *same* Run-watch screen [Source: _bmad-output/planning-artifacts/epics-app.md#FR Coverage Map; #Story 2.2].

**The line shapes are already pinned — do not guess.** Build directly against the Story 1.1 inventory and its two query fixtures; they are the classifier spec and the unit-test inputs [Source: tests/fixtures/app_stdout/format-inventory.md#Flavour B; #Flavour C; tests/fixtures/app_stdout/query_workers1.stdout.txt; tests/fixtures/app_stdout/query_workers4.stdout.txt]. Load-bearing facts from that inventory:
- Query stages, 6 in order: `load-prepared-area`, `elevation-reshape`, `trail-filter`, `climb-detection`, `climb-contraction`, `validate-render`. `trail-filter` appears in **both** setup and query, so a name→index map is ambiguous — track position by incrementing on each stage start, exactly as `SetupProgressParser` does [Source: format-inventory.md#Key finding: no n/total on the wire].
- B1/B3 stage lines are the **same shape** as setup's A1/A3 — the `_STAGE_START` / `_STAGE_DONE` regexes already in the module match them as-is; the `(<note>)` on a start line (`elevation-reshape (stages 6-7)`, `trail-filter (difficulty-cap redux)`) is stripped the same way [Source: src/steeproute/app/cli_adapter/progress_parse.py:42-45; format-inventory.md#Flavour B].
- `progress:` lines appear only **between** `stage: climb-contraction: … s` and `stage: validate-render ...` (the solve). Single-process: `progress: iter=<int> best_objective=<%.1f> elapsed=<%.1f>s eta=<…> stagnation=<int>`. Parallel: `progress: workers=<r>/<t> iters=<int> best_worker_objective=<%.1f> elapsed=<%.1f>s`. **Disambiguate by the first token after `progress: `** (`iter=` → single, `workers=` → parallel) [Source: format-inventory.md#Flavour C].
- Parallel `best_worker_objective` is the leading worker's running sum and **understates** the merged result (the honest final figure is the summary's `total_objective`). Map it to `grasp.best_cost` with that understood — there is no `total_objective` field on `ProgressModel`, so the summary block simply lands in `log_tail`; do not invent a field [Source: format-inventory.md#Flavour C; #ProgressModel field map].
- `best_objective` (single) is **not monotonic** (top-N overlap eviction can step it down) — report it verbatim; do not clamp or max-track it [Source: format-inventory.md#Flavour C].

**Phase transitions are the one real subtlety.** `phase` walks `query` (non-solve stages) → `solve` (during `progress:` lines) → back to `query` when `validate-render` starts. `grasp` must be `null` everywhere except the solve phase — the epic AC and UX spec both say the readout appears *only* during the solve, "absent otherwise, not reserved" [Source: epics-app.md#Story 2.2 AC2; ux-design-specification.md#S3 (UX-DR3)]. Implement this by resetting `grasp=None` (and `phase=query`) on every stage start; the solve `progress:` lines are the only place `grasp` is set and `phase=solve`. A `progress:` line never advances `stage_index` [Source: format-inventory.md#ProgressModel field map — C1/C2 rows].

**The frontend is already done — this is a classifier story.** `run-watch.js::renderProgress` already renders the `grasp` line only when `model.grasp` is truthy and shows `stage n/total` when `stage_total` is set; `run-watch.html` has the hidden `#progress-grasp` slot. Both were built forward-compatibly in Story 1.5 explicitly anticipating this story [Source: src/steeproute/app/static/js/run-watch.js (renderProgress); src/steeproute/app/static/run-watch.html:32-37]. Expect zero frontend edits — AC #4 is a verification, not a build; only touch the frontend if the real drive-through exposes a genuine gap.

**Model shapes are fixed (Story 1.4), extend behaviour only.** `ProgressModel` (`phase, stage_name, stage_index, stage_total, grasp, elapsed, log_tail`), `GraspProgress` (`iter, best_cost`), and `Phase` (`setup|query|solve`) already exist and were defined in full anticipating query/GRASP — do not add or rename fields [Source: src/steeproute/app/models.py:51-61, 223-248].

**`--workers` default is 1**, so a typical query emits the single-process (`iter=`) shape; parallel (`workers=`) appears only when the user raises `--workers`. Both must be handled — the fixtures pin one of each [Source: format-inventory.md#The three flavours; src/steeproute/app/models.py:119 (`workers` param)].

**stderr is not a classifier input.** The parallel→single fallback `warning: …`, OSM-age warnings, and `interrupted before any solution found` are on stderr and keep their bounded-failed-diagnostic-tail role; the classifier reads **stdout only** [Source: format-inventory.md#Stream discipline].

### Project Structure Notes

Target tree — this story edits the **starred** files; everything else is prior work [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree]:

```
src/steeproute/app/
└── cli_adapter/
    └── progress_parse.py          ★ (edit) full query stage + GRASP classifier;
                                          QUERY_STAGES; replaces Story 2.1's minimal QueryProgressParser
tests/
└── unit/
    └── test_app_progress_parse.py ★ (edit) query fixtures (workers1 + workers4);
                                          update/replace the Story-2.1 minimal-query tests
```

No model, API, queue, SSE, or frontend files are expected to change — all CLI-stdout-format knowledge stays inside `cli_adapter/progress_parse.py` (the load-bearing rule) [Source: architecture-app.md#The load-bearing rule].

### Testing

Per AGENTS.md: `uv run basedpyright src/steeproute/app/cli_adapter/progress_parse.py tests/unit/test_app_progress_parse.py`; run `tests/unit` and `tests/integration` in **separate** invocations (wrong conftest otherwise). The classifier unit tests need no server — feed fixture lines directly, as the existing setup tests do. Existing suites (`test_app_progress_parse.py` setup cases, `test_app_sse.py`, `test_app_api.py` query lifecycle) must stay green. End-to-end (AC #4): a real query at quality-demo params through the app, or a real `steeproute` subprocess against a built cache entry (the Story 2.1 Debug Log documents seeding a structurally-valid empty-graph cache entry for exactly this kind of drive-through — but a real trail-rich area exercises the solve phase, which an empty graph does not).

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 2.2: Query-flavour progress (GRASP + non-solve stages)] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 3 — Progress ingestion & unified model] — grasp populated only during solve, null otherwise (present-as-null)
- [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule: one CLI-adapter boundary] — all stdout-format knowledge stays in `cli_adapter/progress_parse.py`
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#S3] — UX-DR3: GRASP line only during solve, absent otherwise, not reserved; Stop is hard-cancel (no partial)
- [Source: tests/fixtures/app_stdout/format-inventory.md] — the classifier spec: Flavour B (query stages), Flavour C (single/parallel GRASP), disambiguation, the ProgressModel field map
- [Source: tests/fixtures/app_stdout/query_workers1.stdout.txt] — single-process GRASP unit-test input
- [Source: tests/fixtures/app_stdout/query_workers4.stdout.txt] — parallel GRASP unit-test input
- [Source: src/steeproute/app/cli_adapter/progress_parse.py] — the file to extend: `SETUP_STAGES`, `_STAGE_START`/`_STAGE_DONE` to reuse, `SetupProgressParser` to mirror, the minimal `QueryProgressParser` to replace
- [Source: src/steeproute/app/models.py:51-61, 223-248] — `Phase`, `GraspProgress`, `ProgressModel` (already defined; do not redefine)
- [Source: src/steeproute/app/static/js/run-watch.js] — `renderProgress` already renders grasp + stage n/total conditionally (no edit expected)
- [Source: src/steeproute/app/static/run-watch.html:32-37] — the hidden `#progress-grasp` slot Story 1.5 reserved for this story
- [Source: _bmad-output/implementation-artifacts/app-1-4-setup-progress-plumbing-classifier-log-and-sse-stream.md] — the setup-flavour predecessor: the classifier + model + SSE plumbing this extends
- [Source: _bmad-output/implementation-artifacts/app-2-1-configure-and-queue-a-query-schema-driven-form.md] — the immediate predecessor: introduced the minimal `QueryProgressParser` this story replaces; documents the empty-graph cache-entry drive-through technique

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **End-to-end verified against a real subprocess + SSE + browser DOM, not just unit tests.** Started the app via `create_app(build_argv=…)` with an injected fake CLI that replays `query_workers1.stdout.txt` line-by-line (the same `build_argv` injection seam the SSE integration test uses), POSTed a `kind=query` job, and read the live Run-watch page (`/runs/{id}`). Mid-solve the DOM showed `PHASE: solve` / `STAGE: climb-contraction (stage 5 / 6)` / `best-so-far cost: 9488 · iteration: 12122`; after the `validate-render` frame it showed `PHASE: query` / `STAGE: validate-render (stage 6 / 6)` with `#progress-grasp` `hidden=true`/empty and footer `done` (confirmed via a `javascript_exec` state dump, since the Browser pane's screenshot timed out — a known flakiness, and `read_page` lists labeled buttons even when `hidden`, so the JS dump was the authoritative check). No frontend edits were needed or made.
- **`os.replace` `PermissionError` (WinError 5) flake** hit `test_sse_snapshot_replay_after_terminal` once on the first integration run — a Windows temp-file-lock race in the store's atomic `job.json` rewrite, unrelated to this change (the classifier never touches the store). Passed on immediate re-run and in the full suite.

### Completion Notes List

- **Classifier extended, not rewritten.** The setup (Flavour A) and query (Flavour B) stage-line shapes are byte-identical, so the positional stage tracking (`_STAGE_START`/`_STAGE_DONE` match, increment-per-start, `stage_total` from a per-kind list) moved into a shared `_StageParser` base. `SetupProgressParser` (phase always `setup`, `grasp` always `null`) and `QueryProgressParser` are the two `@final` subclasses. Setup behaviour is unchanged — all existing setup tests pass untouched.
- **Query GRASP classification (Flavour C).** Two module-level regexes, disambiguated by the first token after `progress: ` — single-process (`iter=`/`best_objective=`) and parallel (`workers=`/`iters=`/`best_worker_objective=`) — set `phase=solve`, populate `grasp={iter, best_cost}`, and update `elapsed`. A `progress:` line never advances `stage_index`.
- **`grasp` present only during the solve (AC #3).** `QueryProgressParser._enter_stage` resets `grasp=None` and `phase=query` on every stage start, so the `validate-render` start that follows the solve drops the readout. Verified in the fixture end-to-end tests (solve-phase models all carry `grasp` at `stage_index==5`; the final post-solve model is `phase=query`, `stage 6/6`, `grasp is None`).
- **`total_objective` deliberately not modelled.** The parallel `best_worker_objective` understates the merged result and the summary's `total_objective` is the honest final figure — but `ProgressModel` has no field for it (per the pinned inventory), so the `--- Run summary ---` block simply rides in `log_tail`. Did not invent a field.
- **Stale Story-2.1 tests replaced, not left green-by-accident.** The two "minimal query parser" tests asserted `stage_name is None`/`stage_index == 0` for a query stage line — now false. Replaced with the full Story-2.2 query suite (8 tests) driving both fixtures.
- **Validation:** `basedpyright` 0 errors / 0 warnings on the changed files; `ruff check` + `ruff format --check` clean. Full offline suite **1005 passed, 17 deselected** (~2m56s), no regressions.

### File List

- `src/steeproute/app/cli_adapter/progress_parse.py` (modified) — `QUERY_STAGES`; `_StageParser` base; `SetupProgressParser`/`QueryProgressParser` subclasses; GRASP single/parallel regexes; full query stage+GRASP classification replacing the Story-2.1 log-tail-only stand-in
- `tests/unit/test_app_progress_parse.py` (modified) — replaced the two stale Story-2.1 minimal-query tests with the full Story-2.2 query suite (stage progression, single/parallel GRASP, cache-hit cue + summary → log tail, post-solve phase/grasp reset, both fixtures end-to-end)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-16 | Story drafted from epics-app.md + architecture-app.md + the Story 1.1 query fixtures, on top of Story 2.1's query runner and Story 1.4's setup classifier. Frontend already forward-compatible (Story 1.5); this is a classifier-only story. Status → ready-for-dev. |
| 2026-07-16 | Implemented the full query classifier: `_StageParser` base shared with setup, `QUERY_STAGES`, single/parallel GRASP regexes, and the solve→query grasp-reset. Replaced the two stale Story-2.1 query tests with an 8-test Story-2.2 suite over both fixtures. Zero frontend changes (Story-1.5 `renderProgress` already correct). Verified end-to-end (fake-CLI query job → SSE → Run-watch DOM). `basedpyright`/`ruff` clean; full suite green (1005 passed). Status → review. |
| 2026-07-16 | Code review (low effort, 1 diff pass, no test-hunk review): no findings — the diff was correct as submitted. Status → done. |
