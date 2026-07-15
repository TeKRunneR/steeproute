# Story 1.1: Stdout format-inventory spike

Status: review

<!-- App track (epics-app.md). Story key `app-1-1-*` is `app-`-prefixed to avoid
     collision with the CLI track's `1-1-*`; both share sprint-status.yaml. -->

## Story

As a developer,
I want a pinned inventory of the exact stdout line shapes emitted by real `setup` and `query` runs at the quality demo params,
so that the progress classifier is built against known formats and regression-tested against fixtures, not guesses.

## Acceptance Criteria

1. A genuine `steeproute-setup` build and a genuine `steeproute` query — both run as **real subprocesses** (`uv run …`, not in-process `CliRunner`) at the quality demo params — have their raw stdout captured verbatim to committed fixture files under `tests/fixtures/app_stdout/`.
2. All three progress flavours are represented across the captures: setup stage lines, query non-solve stage lines, and GRASP solver `progress:` lines. The parallel (`--workers > 1`) `progress:` variant — a distinct line shape — is captured too.
3. A `format-inventory.md` enumerates every distinguishable stdout line type across the three flavours. Each is described precisely enough to write one classifier rule: its stable prefix/shape, its variable fields, and which `ProgressModel` field it feeds (`phase`, `stage_name`, `stage_index/total`, `grasp.{iter,best_cost}`, `elapsed`, `log_tail`). The terminal summary blocks and the stdout/stderr split are documented alongside the progress lines.
4. The captures are faithful — no hand-editing of line shapes. Any redaction of machine-specific tokens (absolute cache/entry paths, cache-key hashes) is called out as a redaction so the classifier author knows the real token is present at runtime.
5. No app code and no CLI changes are made. The spike's only artifacts are the capture fixtures and the doc; they are placed so the future classifier's unit test (`tests/unit/test_app_progress_parse.py`, Story 1.4 / 2.2) reads them directly.

## Tasks / Subtasks

- [x] Capture a real setup build (AC: #1, #2)
  - [x] Ran `uv run steeproute-setup --center 45.260,5.788 --radius 2 --force-refresh --cache-dir <scratch>` (Le Sappey-en-Chartreuse — the `grenoble_small` fixture's area), stdout → `tests/fixtures/app_stdout/setup_cache_miss.stdout.txt`. Live network run (Overpass 7.7 s + IGN WMS 4.4 s), 12.5 s total; produced the built cache Task 2/3 query.
  - [x] Verified the capture contains all 7 setup stage start/done lines, the DEM `  tile 0/1` / `  tile 1/1` within-stage lines, and the terminal `steeproute-setup: cache-miss` summary block.
- [x] Capture a real query at quality demo params, `--workers 1` (AC: #1, #2)
  - [x] `uv run steeproute --center 45.260,5.788 --radius 1.5 --cache-dir <scratch> --iter-budget 200000 --stagnation-iters 10000 --difficulty-cap T4 --elevation-deadband 1 --seed 42 --progress-interval 0.05`, stdout → `tests/fixtures/app_stdout/query_workers1.stdout.txt`.
  - [x] Verified: the `steeproute: cache-hit …` cue, all 6 query `stage:` lines, 33 `progress: iter=…` lines (interleaved between `climb-contraction` and `validate-render`), and the `--- Run summary ---` block. (`--progress-interval` lowered from the 5 s default — the ~2 s solve emitted zero progress lines at default; cadence-only, shape identical — deviation documented in `format-inventory.md`.)
- [x] Capture a query with parallel restarts for the parallel progress line (AC: #2)
  - [x] Re-ran the Task 2 command with `--workers 4`, stdout → `tests/fixtures/app_stdout/query_workers4.stdout.txt`; confirmed 93 `progress: workers=…/4 …` lines appear.
- [x] Write `tests/fixtures/app_stdout/format-inventory.md` (AC: #3, #4)
  - [x] Enumerated each line type per flavour (A setup / B query stages / C GRASP single+parallel) with shape, variable fields, and target `ProgressModel` field; documented both summary blocks, the `convergence_status` value set, the stdout-vs-stderr discipline, the no-`n/total` finding, and all redactions/deviations.
- [x] Guard the spike's scope (AC: #5)
  - [x] `git status` shows 0 `src/` edits — only additions under `tests/fixtures/app_stdout/` (+ the story file and sprint-status tracking). Classifier NOT written (deferred to Story 1.4 / 2.2).

## Dev Notes

This is a **read-and-capture spike**, not a feature. The `src/steeproute/app/` package does not exist yet (Story 1.2 creates it), so write no application code. The one genuine unknown this de-risks is the exact stdout shapes that will feed `cli_adapter/progress_parse.py` [Source: architecture-app.md#Category 3]. The App scrapes CLI **stdout of a child subprocess** [Source: architecture-app.md#Category 1] — capture from a real `uv run` subprocess, because that is exactly what the classifier consumes; the in-process `CliRunner` output is textually identical but is not the honest capture surface.

**The unified `ProgressModel` the captures must map onto** [Source: architecture-app.md#SSE event conventions]:
`{phase, stage_name, stage_index, stage_total, grasp:{iter, best_cost}|null, elapsed, log_tail}` — `grasp` is populated only during a query solve phase, `null` (present, never omitted) otherwise, and always `null` for setup jobs.

### The three flavours and their exact source

**Flavour A — setup stage lines** (the `StageProgress` seam; sink is `print`, suppressed by `--quiet`) [Source: src/steeproute/progress.py:85-133]:
- Stage start: `stage: <name> ...` — or `stage: <name> (<note>) ...` when a note is attached.
- Within-stage line: `  <text>` (two-space indent). The only current instance is the DEM fetch loop's `  tile <i>/<N>` [Source: src/steeproute/pipeline/dem_download.py:445,470].
- Stage done: `stage: <name>: <elapsed>.2f s`.
- Setup stage names, in pipeline order on a cache-miss: `osm-download` (note "one Overpass request; typically takes minutes"), `trail-filter`, `polyline-smoothing`, `resampling`, `elevation-sampling` [Source: src/steeproute/pipeline/__init__.py:183-215], then `dem-resolve`, `cache-write` [Source: src/steeproute/cli/setup.py:189,211].
- Terminal summary, **always printed** (even `--quiet`) [Source: src/steeproute/cli/setup.py:255-267]:
  `steeproute-setup: cache-miss` (or `cache-hit`) / `  cache_key_hash: <16-hex>` / `  entry: <path>` / `  elapsed: <x>.2f s`.

**Flavour B — query non-solve stage lines** (same `StageProgress` seam, reused query-side) [Source: src/steeproute/cli/query.py:216-465]:
- Same `stage: …` shapes as Flavour A. Query stage names in order: `load-prepared-area`, `elevation-reshape` (note "stages 6-7"), `trail-filter` (note "difficulty-cap redux"), `climb-detection`, `climb-contraction`, `validate-render`.
- Cache-hit cue line: `steeproute: cache-hit cache_key_hash: <16-hex>` [Source: src/steeproute/cli/query.py:238].
- Terminal run-summary block [Source: src/steeproute/cli/query.py:590-638]:
  `--- Run summary ---` / `parameters: theta=… j_max=… n=… seed=… iter_budget=… time_budget=… stagnation_iters=… workers=… merge_interval=…` / `routes_returned: <k>/<N>` / `total_objective: <x>.1f` / `validation_failures: <k>` / `convergence_status: <converged|budget-exhausted|interrupted>` / (optional) `degradation: <msg>` / `wall_clock_total: <x>.2fs`.

**Flavour C — GRASP solver events** (throttled to `--progress-interval`; every line carries the stable `progress: ` sentinel) [Source: src/steeproute/cli/query.py:495-526]:
- Single-process (`--workers 1`, the default): `progress: iter=<n> best_objective=<x>.1f elapsed=<x>.1fs eta=<x>0fs stagnation=<n>` — `eta=?` when the ETA is not yet measurable.
- Parallel (`--workers > 1`): `progress: workers=<r>/<T> iters=<n> best_worker_objective=<x>.1f elapsed=<x>.1fs`. Note `best_worker_objective` is the leading worker's running sum, not the merged result — document it as such; the summary's `total_objective` is the comparable figure.

**Field-mapping the classifier will need (document, do not implement):** `stage: <name> ...` → `stage_name` + advance `stage_index`; `progress: iter=…` → `grasp.iter` / `grasp.best_cost` (from `best_objective`) + `elapsed`; the summary block → the terminal `status` (Category 3 / SSE `status` event). Setup emits no `progress:` line, so `grasp` stays `null` for setup jobs.

### Stream discipline (matters for what to capture)

Progress lines and both summary blocks go to **stdout** via `print`; `logging`, warnings, and the "interrupted before any solution found" notice go to **stderr** [Source: src/steeproute/progress.py:1-22; architecture-app.md#Process patterns]. The classifier reads stdout only. Capture stdout for the fixtures; a separate stderr capture is optional context, not a classifier input.

### Biggest shortcut — the existing e2e tests already pin these formats

Read these before capturing; they assert the exact shapes and give you a ready checklist of what a complete capture must contain:
- `tests/e2e/test_query_stage_progress.py` — the six query stage names + the `stage: <name>: \d+\.\d{2} s` regex.
- `tests/e2e/test_progress_cli.py` — the `progress: iter=…` line contract.
- `tests/e2e/test_run_summary.py` — the `--- Run summary ---` block labels.
- `tests/e2e/test_steeproute_setup.py` — the setup stage + summary contract.
- `tests/e2e/test_parallel_workers.py` — the `--workers > 1` behaviour.

### Quality demo params

Query defaults for the App are the quality demo params, not the low CLI defaults: `--iter-budget 200000 --stagnation-iters 10000 --difficulty-cap T4 --elevation-deadband 1` [Source: architecture-app.md#Category 9; AGENTS.md]. Run the captures at these so the pinned GRASP-line volume/shape matches what the App will actually surface. Setup has no solver params — its "quality" capture is simply a real cache-miss build of a small area.

### Project Structure Notes

- New files only, all under `tests/fixtures/app_stdout/`: `setup_cache_miss.stdout.txt`, `query_workers1.stdout.txt`, `query_workers4.stdout.txt`, `format-inventory.md`. The top-level `tests/fixtures/` dir already exists (holds `grenoble_small`).
- The future `tests/unit/test_app_progress_parse.py` (Story 1.4 / 2.2) will read these fixtures [Source: architecture-app.md#Complete project tree]. Keep the raw captures free of test scaffolding — they are inputs, not tests.
- There is no committed *built* cache to query offline (the e2e `seeded_cache` fixture builds one in-process from `grenoble_small`'s raw graphml+DEM). Hence the sequence: one real setup build → query its output. A real query subprocess itself touches no network.

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 1.1: Stdout format-inventory spike]
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 3 — Progress ingestion & unified model]
- [Source: _bmad-output/planning-artifacts/architecture-app.md#SSE event conventions]
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Implementation Readiness Validation] — this story is the gated first unknown
- [Source: src/steeproute/progress.py] — `StageProgress`, `ProgressEvent`, `throttle`
- [Source: src/steeproute/cli/setup.py] — setup stages + summary
- [Source: src/steeproute/cli/query.py] — query stages, `_render_progress`, `_render_parallel_progress`, `_run_summary`
- [Source: src/steeproute/pipeline/__init__.py; src/steeproute/pipeline/dem_download.py] — setup stage names + `tile i/N`

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- First `query_workers1` run at the plain demo params (default `--progress-interval 5.0`) emitted **0** `progress:` lines — the small-area solve converged (stagnation) in ~1.4 s, before the throttle's first 5 s fire. Re-ran both query captures at `--progress-interval 0.05` (cadence-only knob; byte-identical results per FR29) to sample the GRASP line shape. Documented as a deviation in `format-inventory.md`.
- Installed build is stale (`0.0.1.dev117+585262f`, uv-dynamic-versioning lag vs HEAD `c1e8a66` per AGENTS.md); irrelevant to this spike — the three progress code paths (`progress.py`, `cli/setup.py`, `cli/query.py`) are stable across that range.

### Completion Notes List

- **Spike outcome (de-risks architecture-app.md §Category 3):** all three stdout progress flavours captured from real `uv run` subprocesses and pinned as fixtures; `format-inventory.md` specifies each line type precisely enough to write the classifier.
- **Key finding for the classifier author:** CLI stage lines carry a stage **name only — no `n/total`**. `stage_index`/`stage_total` must be derived from a known ordered stage list per job kind (setup=7, query=6), tracked **positionally** (`trail-filter` occurs in both kinds). A setup **cache-hit** emits no stage lines at all.
- **Parallel caveat pinned:** `best_worker_objective` (parallel `progress:` line) understates the merged result — the `query_workers4` fixture shows a running max ~10118 vs the summary's `total_objective` 10670.1. Prefer the summary figure.
- **No production code or CLI changes** (AC #5). Fixtures live at `tests/fixtures/app_stdout/` and are the direct unit-test inputs for the future `tests/unit/test_app_progress_parse.py` (Stories 1.4 / 2.2). No test scaffolding added by design.
- **No regression surface:** 0 `src/`/`tests/*.py` changes, so the suite was not re-run (nothing to regress).

### File List

- `tests/fixtures/app_stdout/setup_cache_miss.stdout.txt` (new) — real setup cache-miss capture (Flavour A)
- `tests/fixtures/app_stdout/query_workers1.stdout.txt` (new) — real query capture, `--workers 1` (Flavours B + C single-process)
- `tests/fixtures/app_stdout/query_workers4.stdout.txt` (new) — real query capture, `--workers 4` (Flavours B + C parallel)
- `tests/fixtures/app_stdout/format-inventory.md` (new) — the classifier spec / line inventory
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-15 | Implemented spike: captured setup + query (workers 1 & 4) stdout from real subprocesses at quality demo params; wrote `format-inventory.md`. Status → review. |
