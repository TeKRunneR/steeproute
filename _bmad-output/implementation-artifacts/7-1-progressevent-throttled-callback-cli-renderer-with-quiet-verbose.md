# Story 7.1: ProgressEvent + throttled callback + CLI renderer with --quiet / --verbose

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want `steeproute` on a long-running query to emit periodic progress lines (iteration, best-so-far, elapsed, ETA) honoring `--progress-interval` and suppressible via `--quiet`,
so that I can judge whether to wait or kill a run (Journey 3) and FR13 is fulfilled.

## Acceptance Criteria

1. **`ProgressEvent` dataclass** in `progress.py` with exactly the Architecture §Cat 8 shape: `iteration: int`, `elapsed_s: float`, `best_objective: float` (D+ + D− summed across the current top-N), `estimated_remaining_s: float | None`, `stagnation_counter: int`.

2. **Throttled callback wrapper** in `progress.py` that wraps a render function + interval and fires the wrapped function at most once per `--progress-interval` seconds, tracked against a monotonic wall-clock; the first fire happens after the interval elapses from start (no fire at iteration 0). Throttling state and timing reads never touch the RNG or route construction — FR29 byte-identical edge-sets are unaffected by whether/when progress fires.

3. **GRASP loop wiring.** `solver/grasp.py::GraspSolver.run()` invokes its `progress_callback` once per iteration with a populated `ProgressEvent`: `best_objective` from `tracker.total_objective()`; `stagnation_counter` = iterations since the top-N total objective last improved (the counter is *created* here; acting on it for termination + `convergence_status` is Story 7.2); `estimated_remaining_s` derived from the iter-budget pace (`remaining_iters × elapsed/iteration`), `None` until a rate is computable. The currently-stored-but-unused `self._progress_callback` becomes live.

4. **CLI renderer.** `cli/query.py` formats a `ProgressEvent` as a single-line `print(...)` to stdout and installs it (wrapped by the AC-2 throttle at `--progress-interval`) as the solver's `progress_callback` — or installs `None` when `--quiet`. Replaces the Epic-3 placeholder (`progress_callback=None` plus the `_ = (progress_interval, quiet)` pyright-silencer).

5. **Concrete default.** `--progress-interval` resolves to a concrete value (5 seconds) — set the default in `cli/_shared.py` and replace its `(default: TBD)` help text — documented as "tunable post-baseline".

6. **Stream discipline (§Cat 8).** Progress lines go through `print()` to stdout only — never `logging.*`. `--verbose` does not change stdout progress; `--quiet` suppresses it.

7. **Tests.** `tests/integration/test_progress.py` runs GRASP on the real Grenoble fixture with a list-collecting callback and asserts events are spaced ≥ `--progress-interval` (± timing slop), every field is populated, and `stagnation_counter` increments when the top-N objective is unchanged across iterations. `tests/e2e/test_progress_cli.py` runs `uv run steeproute --progress-interval 1` on the fixture and asserts progress lines appear on stdout during the solver phase. `tests/e2e/test_quiet_suppresses_progress.py` runs with `--quiet` and asserts no progress lines appear during the solver phase.

## Tasks / Subtasks

- [x] Implement `ProgressEvent` + throttle wrapper in `progress.py` (AC: #1, #2)
  - [x] `ProgressEvent` dataclass — five fields per §Cat 8, `estimated_remaining_s` nullable (frozen+slots)
  - [x] `throttle(render, interval_s, *, clock=time.monotonic)` closure; monotonic-clock gating; first fire after one interval; spacing measured from actual fire (no catch-up burst); injectable clock for deterministic tests; no RNG/time coupling into construction
  - [x] `estimate_remaining(...)` ETA helper (moved here for cohesion; nullable contract)
- [x] Wire the callback into the GRASP loop (AC: #3)
  - [x] In `run()`, after each `tracker.consider(...)`, build the `ProgressEvent` and call `self._progress_callback` if set — whole block gated behind the callback check so the no-progress path stays zero-overhead/deterministic
  - [x] Track "iterations since the top-N total objective last changed" via `tracker.total_objective()`; reset to 0 on change, else increment — populate `stagnation_counter`
  - [x] Compute `elapsed_s` (monotonic) and `estimated_remaining_s` from iter-budget pace (`None` until a per-iteration rate exists)
- [x] CLI renderer + install (AC: #4, #6)
  - [x] Single-line `print()` formatter (`progress:`-prefixed) for a `ProgressEvent` (stdout)
  - [x] Build throttle-wrapped renderer at `--progress-interval`; pass it as `progress_callback`, or `None` under `--quiet`; remove the `_ = (progress_interval, quiet)` placeholder
- [x] Set `--progress-interval` default to 5s (`PROGRESS_INTERVAL_DEFAULT_S`) and fix its help text (AC: #5)
- [x] Tests: integration spacing/fields/stagnation; e2e lines-appear; e2e quiet-suppresses; unit throttle+ETA (AC: #7)

## Dev Notes

- **Epic renumbering — read this first.** `progress.py`, `solver/grasp.py`, and `cli/query.py` docstrings/comments reference "Epic 4", "Story 4.1 / 4.2 / 4.3 / 4.5". Those are the pre-correct-course identities of *this* Operational-Robustness work; correct-course (2026-06-03) inserted Epics 4–6 and pushed Operational Robustness to **Epic 7**. The mapping is: old 4.1 → **this story (7.1)**, 4.2 → 7.2 (time-budget/stagnation), 4.3 → 7.3 (interrupt), 4.5 → 7.5 (run summary). Update any stale "Epic 4 / Story 4.x" comments you touch to the Epic-7 numbering as you go; don't be misled into thinking the work is missing.
- **The hook already exists, unused.** `GraspSolver.__init__` accepts `progress_callback: Callable[[Any], None] | None` and stores it as `self._progress_callback` (currently never invoked — see `solver/grasp.py:122-140`). This story makes it live in `run()` (`solver/grasp.py:163-171`). Keep the parameter type as-is or tighten to `Callable[[ProgressEvent], None] | None` (Architecture's stated signature) — your call, but tightening means importing `ProgressEvent` into `grasp.py`.
- **`best_objective` is already available** — `TopNTracker.total_objective()` returns the sum of held objectives (`solver/distinctness.py:198`, `0.0` when empty). That is the §Cat 8 `best_objective`.
- **Stagnation counter vs. stagnation termination.** This story only *populates* `stagnation_counter` for the event (iterations since `total_objective()` last changed). Story 7.2 adds the early-termination decision and the `convergence_status` three-value contract on top of it — do **not** implement termination here.
- **FR29 is the trap.** Route output must stay byte-identical regardless of progress timing. The throttle must read a monotonic wall-clock for gating only; it must not feed the RNG, gate `_construct_one`, or change iteration count. The grasp docstring's determinism contract (`solver/grasp.py:60-77`) still holds after this change — progress is a pure side-effect.
- **Throttle placement.** Per §Cat 8 the *wrapper* in `progress.py` owns throttling; the solver calls the callback every iteration and the injected wrapper decides whether to actually render. That keeps the solver interval-agnostic and deterministic. `--progress-interval` plumbs into the wrapper at the CLI, not into `SolverParams`.
- **ETA.** No time-budget exists yet (Story 7.2). Derive `estimated_remaining_s` from iter-budget progress: `remaining_iters × (elapsed_s / completed_iters)`; return `None` before any iteration has completed (no rate). Keep it a rough estimate — FR13 says "rough ETA".
- **Out of scope (so the dev doesn't drift):** time-budget/stagnation termination (7.2), Ctrl-C / interrupt handling (7.3), graceful-degradation messaging (7.4), and the end-of-run `--- Run summary ---` block (7.5). The 7.1 e2e quiet test only asserts no *progress* lines during the solver phase; the final summary doesn't exist yet.

### Project Structure Notes

- Files touched: `src/steeproute/progress.py` (currently a one-line placeholder docstring), `src/steeproute/solver/grasp.py`, `src/steeproute/cli/query.py`, `src/steeproute/cli/_shared.py` (default + help text only). New tests under `tests/integration/` and `tests/e2e/`.
- `--progress-interval` and `--quiet` options are already defined in `cli/_shared.py` (`progress_interval_option` ~line 401, `quiet_option` ~line 447) and already stacked on the query command — this story only sets the interval default and consumes both, not redefines them.
- `configure_cli_logging(verbose=...)` (`cli/_shared.py:34`) already routes `logging` to stderr; progress stays on the `print`/stdout side of that split. No new logging config needed.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 7.1] — ACs and BDD acceptance scenarios.
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 8 — Logging, progress, and stream discipline] — `ProgressEvent` shape (lines 546-556), throttle/first-fire semantics (569), renderer + `--quiet` install (571), stream routing (544). Anti-pattern: "Progress via `logging.info`" is forbidden (773).
- [Source: _bmad-output/planning-artifacts/prd.md] — FR13 (progress emission), NFR1 (budget visibility).
- [Source: src/steeproute/solver/grasp.py:122-171] — constructor stores `progress_callback`; `run()` loop is the wiring site.
- [Source: src/steeproute/solver/distinctness.py:198-200] — `total_objective()` = `best_objective`.
- [Source: src/steeproute/cli/query.py:243-265] — Epic-3 placeholder (`progress_callback=None`, `_ = (progress_interval, quiet)`) this story replaces.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- Measured GRASP solve pace on the Grenoble fixture: ~0.7 ms/iter (2000 iters ≈ 1.4 s) — used to size the e2e `--progress-interval 0.05` and the integration `iter_budget=600` / `interval=0.02` so progress reliably fires without flake.
- Confirmed FR29 holds: a seeded solve with progress active produces byte-identical route edge-sets to the same solve with `progress_callback=None`.

### Completion Notes List

- **`progress.py`**: `ProgressEvent` (frozen+slots, 5 fields per §Cat 8), `throttle(render, interval_s, *, clock=time.monotonic)`, and `estimate_remaining(...)`. The throttle takes an injectable clock (mirrors `emit_osm_age_warning(now=...)`) so spacing is asserted deterministically; spacing is measured from the actual fire time so a slow iteration can't trigger a catch-up burst.
- **`solver/grasp.py`**: `run()` now emits a `ProgressEvent` per iteration when a callback is installed. `stagnation_counter` counts consecutive iterations with an **unchanged** top-N total objective (`tracker.total_objective()` changes iff a candidate was admitted) — matching the documented `distinctness.py` stagnation hook that Story 7.2 will act on. All progress bookkeeping (timing, objective read, event build) is gated behind the callback check, so the no-progress/`--quiet`/quality-gate path carries zero overhead and stays deterministic. The `time.monotonic()` reads feed only `elapsed_s`/ETA — never the RNG. Updated the stale "Epic 4 / Story 4.x" docstring/comment references to the post-correct-course Epic-7 numbering.
- **`cli/query.py`**: installs `throttle(_render_progress, progress_interval)` as the solver's `progress_callback`, or `None` under `--quiet`. `_render_progress` prints one `progress:`-prefixed line to stdout (§Cat 8 — never `logging`). Removed the Epic-3 `progress_callback=None` + `_ = (progress_interval, quiet)` placeholder; updated the module docstring.
- **`cli/_shared.py`**: `--progress-interval` default is now the concrete `PROGRESS_INTERVAL_DEFAULT_S = 5.0` (named constant, `show_default=True`), help text de-`TBD`'d to "tunable post-baseline".
- **Out of scope (deferred to later Epic 7 stories, as specced):** stagnation/time-budget *termination* + `convergence_status` (7.2), Ctrl-C interrupt (7.3), degradation messaging (7.4), end-of-run summary (7.5).
- **Validation:** full suite 738 passed / 2 deselected (live network tests); 17 new tests pass; `progress.py` 100% line coverage; ruff check + format clean on all touched files; basedpyright 0 errors on touched files (2 `pytest.approx` partial-unknown warnings, consistent with the existing baseline in `test_cache.py`/`test_dem.py`).
- **Pre-existing, untouched:** project-wide basedpyright reports 11 errors in `tests/unit/test_graph_contraction.py` and `tests/integration/test_route_discovery_fixes.py` (networkx `.graph` attr / dict-key typing) and a `ruff format` drift in `tests/unit/test_dem_download.py` — none touched by this story, left as-is.

### File List

- `src/steeproute/progress.py` (modified — implemented `ProgressEvent`, `throttle`, `estimate_remaining`)
- `src/steeproute/solver/grasp.py` (modified — wired per-iteration progress emission into `run()`)
- `src/steeproute/cli/query.py` (modified — throttled stdout renderer install + `_render_progress`)
- `src/steeproute/cli/_shared.py` (modified — `--progress-interval` concrete default + help)
- `tests/unit/test_progress_helpers.py` (new — `ProgressEvent`, `throttle`, `estimate_remaining`)
- `tests/integration/test_progress.py` (new — real-fixture field/stagnation/throttle-spacing)
- `tests/e2e/test_progress_cli.py` (new — progress lines appear on stdout)
- `tests/e2e/test_quiet_suppresses_progress.py` (new — `--quiet` suppresses progress)
- `tests/unit/test_area_parsing.py` (modified — `--progress-interval` finiteness/positivity rejection cases + CLI exit-2 case; review fix)

### Review Findings

Lightweight review 2026-06-08 (`code-review` medium: correctness + test-audit angles). High-risk areas (FR29 determinism, stagnation float-equality, throttle first-fire/no-catch-up semantics) verified clean. One patch finding applied; minor test-coverage gaps dismissed as low-value.

- [x] [Review][Patch] `--progress-interval` was the only float CLI flag bypassing boundary validation (`validate_solver_options`) — `nan`/`inf` made the throttle never fire (silent no-progress despite no `--quiet`); `0`/negative forwarded every iteration (stdout flood). **Fixed:** threaded `progress_interval` into `validate_solver_options` with finiteness + `> 0` checks (same §Cat 10 pattern as the Story 6.3 elevation-flag patch); added 4 rejection cases + 1 CLI exit-2 case to `test_area_parsing.py`. Verified `--progress-interval nan` now exits 2 with `error:` [src/steeproute/cli/_shared.py, src/steeproute/cli/query.py, tests/unit/test_area_parsing.py]
- [Review][Dismiss] No test for AC6's "`--verbose` doesn't change stdout progress" clause, and no explicit `result.stderr` assertion for "never logging". Low value: `--verbose` only sets the stderr logging level (progress uses `print`, independent path), and the positive e2e test already fails if progress regresses to logging (which routes to stderr, absent from the stdout-only `result.output`).

## Change Log

- 2026-06-08: Implemented Story 7.1 — `ProgressEvent` + throttled callback + CLI renderer with `--quiet` (FR13). Status → review.
- 2026-06-08: Applied lightweight-review patch — `--progress-interval` finiteness/positivity validation at the CLI boundary.
