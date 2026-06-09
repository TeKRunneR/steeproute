# Story 7.2: Time-budget and stagnation termination

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want GRASP to terminate when either the `--time-budget` wall-clock is exhausted OR the top-N total objective stops improving for `--stagnation-iters` consecutive iterations, with `convergence_status` set correctly,
so that NFR1's compute budget has a real termination mechanism and Journey 2's iterative tuning doesn't burn cycles once the solver has nothing more to find.

## Acceptance Criteria

1. **Time-budget termination.** `GraspSolver.run()` checks monotonic wall-clock elapsed against `params.time_budget` *between* iterations and stops once it is exceeded (soft budget — the in-flight iteration finishes). Termination via time-budget sets `convergence_status = "budget-exhausted"`.

2. **Stagnation termination.** `run()` stops early when the top-N total objective (`tracker.total_objective()`) has been unchanged for `params.stagnation_iters` consecutive iterations, setting `convergence_status = "converged"`. The check naturally activates only after the tracker fills (the window can't be reached while admissions are still changing the objective). `params.stagnation_iters == 0` disables the stagnation check entirely (solver runs to iter-budget or time-budget).

3. **`convergence_status` attribute.** The solver exposes a `convergence_status` attribute typed over the three §Cat 5e values (`converged` / `budget-exhausted` / `interrupted`). `run()` sets it at every termination branch — `converged` on stagnation, `budget-exhausted` on iter-budget OR time-budget. `interrupted` is reserved for Story 7.3's CLI handler (this story never sets it). It is readable after `run()` returns; initialize it in `__init__` so it is always typed/readable, including on the empty-graph early return.

4. **Termination bookkeeping is callback-independent.** The stagnation counter and elapsed-time tracking that drive AC-1/AC-2 must run on every iteration regardless of whether a `progress_callback` is installed (they now gate termination, not just the `ProgressEvent`). FR29 still holds: the monotonic-clock reads feed only `elapsed_s`/ETA/the time-budget comparison — never the RNG, `_construct_one`, or the admission sequence.

5. **Stagnation default constant.** Declare `STAGNATION_ITERS_DEFAULT_PLACEHOLDER = 100` as a module-scope named constant in `solver/grasp.py`, with a comment marking it provisional — to be tuned during implementation by observing the metamorphic suite, the new time-budget/stagnation integration tests, and real-fixture runs. The query CLI resolves an unset `--stagnation-iters` to this constant (replacing the current `else 0`); de-`TBD` the `--stagnation-iters` help text.

6. **CLI wires the real status.** `cli/query.py` reads `solver.convergence_status` after `run()` and passes it to `output.render(...)`, replacing the hard-coded `_CONVERGENCE_STATUS = "budget-exhausted"` placeholder. `convergence_status` already flows into report metadata (Story 3.10 / `output.render`); this story populates it with the full three-value contract.

7. **Boundary validation.** `--time-budget` (finiteness + `> 0`) and `--stagnation-iters` (`>= 0`) are validated in `validate_solver_options` (§Cat 10 → `BadCLIArgError` → exit 2), mirroring the Story 7.1 `--progress-interval` patch.

8. **Tests.** `tests/integration/test_time_budget.py` runs GRASP on the real Grenoble fixture with `--time-budget 1` and asserts termination within ~1.5 s with `convergence_status == "budget-exhausted"`. `tests/integration/test_stagnation.py` runs GRASP on a small programmatic fixture where the optimum is found rapidly and asserts termination well before iter-budget with `convergence_status == "converged"`, plus a case asserting `stagnation_iters=0` disables the check (runs to iter-budget).

## Tasks / Subtasks

- [x] Add stagnation/time-budget termination to `GraspSolver.run()` (AC: #1, #2, #4)
  - [x] Lift the stagnation-counter + elapsed-time bookkeeping out of the `if callback is not None` gate so it runs every iteration; keep the `ProgressEvent` build gated behind the callback
  - [x] After each `consider(...)`, evaluate stagnation (`stagnation_iters > 0` and counter ≥ window) and time-budget (`elapsed >= params.time_budget`); break with the correct status
- [x] Add the `convergence_status` attribute + `STAGNATION_ITERS_DEFAULT_PLACEHOLDER` constant (AC: #3, #5)
  - [x] Initialize `convergence_status` in `__init__`; set it at each termination branch (incl. empty-graph early return)
  - [x] Home the three-value literal type once and share it with `output` (avoid a solver→output import — see Dev Notes)
- [x] Wire CLI: resolve the stagnation default, read the status, validate the flags (AC: #5, #6, #7)
  - [x] `cli/query.py`: `stagnation_iters` unset → `STAGNATION_ITERS_DEFAULT_PLACEHOLDER`; pass `solver.convergence_status` to `output.render`; drop `_CONVERGENCE_STATUS`
  - [x] `cli/_shared.py`: add `--time-budget` / `--stagnation-iters` checks to `validate_solver_options`; de-`TBD` the help text; thread the two values in from `query.py`
- [x] Tests (AC: #8)
  - [x] `test_time_budget.py` (real fixture, `--time-budget 1`, `budget-exhausted`)
  - [x] `test_stagnation.py` (small programmatic fixture → `converged`; `stagnation_iters=0` → runs to iter-budget)
  - [x] Audit existing solver tests whose params now make stagnation/time-budget *live* (see Dev Notes / Completion Notes)

## Dev Notes

- **Most of the plumbing already exists.** `SolverParams` already carries `iter_budget`/`time_budget`/`stagnation_iters` ([models.py:206-208](src/steeproute/models.py:206)); the CLI already defines and forwards `--time-budget` (default `600.0`) and `--stagnation-iters` ([cli/_shared.py:398-411](src/steeproute/cli/_shared.py:398), [cli/query.py:208-211](src/steeproute/cli/query.py:208)); `output.render` already accepts `convergence` and emits `convergence_status` in metadata ([output.py:54](src/steeproute/output.py:54), [output.py:197](src/steeproute/output.py:197)). The solver simply **ignores** `time_budget`/`stagnation_iters` today — `run()` terminates on iter-budget only. This story makes them act.
- **The stagnation counter is already computed** in `run()` — but *inside* the `if callback is not None` block ([grasp.py:191-197](src/steeproute/solver/grasp.py:191)). It counts consecutive iterations whose `tracker.total_objective()` was unchanged (the value changes iff a candidate was admitted), which is exactly the §Cat 5e stagnation definition. Lift it (and the `time.monotonic()` start/`elapsed_s`) out of the gate so it drives termination unconditionally; keep the `ProgressEvent` construction gated.
- **FR29 is the trap, and it has two faces here:**
  - *Stagnation termination preserves byte-identical output.* It depends only on the objective sequence, which is deterministic for a fixed seed → same seed terminates at the same iteration → same top-N. Safe.
  - *Time-budget termination intentionally breaks iteration-count determinism* — wall-clock varies per run, so the iteration at which it trips varies. This is expected (the budget is "soft", §Cat 5e). The consequence: any test or gate that needs byte-identical output (Story 3.7 quality gate, `test_grasp_reproducible.py`, future Story 8 regressions) must pin a large `time_budget` and `stagnation_iters=0` so neither triggers, leaving iter-budget the sole terminator. The monotonic reads must continue to feed only timing — never the RNG, `_construct_one`, or admission — exactly as the [grasp.py:60-77](src/steeproute/solver/grasp.py:60) determinism contract already states.
- **Regression audit (do not skip).** Existing solver tests pass concrete `stagnation_iters`/`time_budget` values that were dead until now — e.g. `test_grasp_reproducible.py` uses `stagnation_iters=50, time_budget=60.0` ([test_grasp_reproducible.py:75-77](tests/integration/test_grasp_reproducible.py:75)). With `iter_budget=50` the stagnation window can't trip and 60 s dwarfs the runtime, so it stays green — but verify each existing solver/quality-gate/metamorphic test the same way; if any now terminates early, set `stagnation_iters=0` + a generous `time_budget` to restore intent.
- **`convergence_status` type — avoid a layering inversion.** `output.py` defines `ConvergenceStatus = Literal["converged", "budget-exhausted", "interrupted"]` ([output.py:54](src/steeproute/output.py:54)). The solver must not import from `output` (output is the higher layer). Recommended: re-home the literal to `models.py` and import it in both `solver/grasp.py` and `output.py`. Keep the same three values so 3.10's metadata wiring is untouched.
- **Stagnation activation.** No special-casing needed for "after the first N+1 iterations" — the counter resets on every objective change, so while the tracker is still filling it never reaches the window. The window itself implies the activation delay (§Cat 5e).
- **Small programmatic fixture for `test_stagnation.py`.** Build a tiny `ContractedGraph` directly (see `tests/unit/test_grasp_construction.py` / `tests/integration/conftest.py` for hand-built graphs) shaped so the optimal top-N is discovered within a handful of iterations, then give a large `iter_budget` and a small `stagnation_iters`; assert it stops well short of `iter_budget`.
- **Out of scope (don't drift):** Ctrl-C / `interrupted` status + best-so-far flush (7.3), sparse-area degradation messaging (7.4), the `--- Run summary ---` block that will print `convergence_status` and `time_budget`/`stagnation_iters` to stdout (7.5). This story only makes the solver terminate correctly and tag the report metadata.

### Project Structure Notes

- Files touched: `src/steeproute/solver/grasp.py` (termination + status + constant), `src/steeproute/cli/query.py` (default resolution + status read), `src/steeproute/cli/_shared.py` (validation + help text), `src/steeproute/models.py` (re-home `ConvergenceStatus`), `src/steeproute/output.py` (import the re-homed literal). New: `tests/integration/test_time_budget.py`, `tests/integration/test_stagnation.py`.
- `--time-budget` / `--stagnation-iters` options and their `SolverParams` fields already exist — this story consumes them, it does not redefine them.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 7.2] — ACs and BDD acceptance scenarios.
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 5e] — termination table (lines 415-428): iter/time/stagnation/interrupt → `convergence_status`; stagnation definition; `--stagnation-iters 0` disables; soft time-budget (checked between iterations).
- [Source: _bmad-output/planning-artifacts/prd.md] — NFR1 (10-min compute budget), NFR4/FR29 (determinism).
- [Source: src/steeproute/solver/grasp.py:169-211] — `run()` loop + existing stagnation-counter computation (the wiring site).
- [Source: src/steeproute/cli/query.py:82-86, 205-211, 255-273] — `_CONVERGENCE_STATUS` placeholder; `stagnation_iters` default resolution; `output.render` call.
- [Source: src/steeproute/cli/_shared.py:168-242] — `validate_solver_options` (extend with time-budget/stagnation checks).
- [Source: src/steeproute/output.py:54, 173-197] — `ConvergenceStatus` literal + metadata emission (already wired by Story 3.10).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `test_stagnation.py` uses a **single-node self-loop** graph rather than a chain: with one node the start-node sample is forced and the only constructible route is the self-loop, so every iteration builds the identical route and the top-N objective is bit-stable from iteration 2. That makes the stagnation termination iteration exactly predictable (admit on iter 1 → 5 unchanged iters → terminate at iter 6), which a multi-route chain wouldn't allow (random start node ⇒ 0→1→2 vs 1→2 ⇒ objective could change at a late iteration and reset the counter).
- `test_time_budget.py` pins `iter_budget=1_000_000` (≈12 min uncapped at the 7.1-measured ~0.7 ms/iter) with `--time-budget 1`, so reaching iter-budget is impossible — the only way the run ends quickly is the time-budget check. Wall-clock ceiling asserted at a generous 15 s to prove time-budget bound the run without CI flake.

### Completion Notes List

- **`solver/grasp.py`** — `run()` now terminates on any of the three §Cat 5e conditions. The stagnation-counter + monotonic-elapsed bookkeeping was lifted out of the `if callback is not None` gate (it now drives termination, not just the `ProgressEvent`); only the event *construction* stays gated. Checks run between iterations, stagnation before time so a truly-converged search is labelled `converged` even if it also just crossed the clock. Added `STAGNATION_ITERS_DEFAULT_PLACEHOLDER = 100` (module-scope, provisional) and the public `convergence_status` attribute (init `"budget-exhausted"`, set at each branch; `interrupted` left for 7.3). FR29 preserved: clock reads feed only `elapsed_s`/ETA/the time-budget comparison — never RNG or construction, so a fixed seed yields a byte-identical iteration *sequence*; only the *count* is wall-clock-dependent, and solely when the soft time-budget binds.
- **`models.py`** — re-homed `ConvergenceStatus` literal here (lowest layer) so the solver sets it and `output` emits it from one definition, avoiding a solver→output import inversion.
- **`output.py`** — imports `ConvergenceStatus` from `models` (dropped the local definition + now-unused `Literal`).
- **`cli/query.py`** — drops the hard-coded `_CONVERGENCE_STATUS`; reads `solver.convergence_status` and passes it to `output.render`. Unset `--stagnation-iters` now resolves to `STAGNATION_ITERS_DEFAULT_PLACEHOLDER` (was `0`). Refreshed stale "Epic 3 / Epic 4" termination comments to the live Epic-7 reality.
- **`cli/_shared.py`** — `validate_solver_options` gained `--time-budget` (finiteness + `> 0`) and `--stagnation-iters` (`>= 0`) checks (§Cat 10 → exit 2), mirroring the 7.1 `--progress-interval` patch; `--stagnation-iters` help de-`TBD`'d.
- **Regression audit (the FR29 trap).** Existing solver tests passed `stagnation_iters`/`time_budget` values that were dead until now. Fixed to preserve intent: set `stagnation_iters=0` in the four GRASP-running fixture tests (`test_grasp_on_fixture`, `test_output_on_fixture`, `test_validator_on_fixture`, `test_grasp_reproducible`) — early stagnation could otherwise cut the search before a late improvement and change the pinned route set; `test_grasp_construction` helper set to `0` too. Bumped the metamorphic `make_toy_solver_params` `time_budget` 60→3600 s so its 5000-iter runs stay wall-clock-independent (invariants must not depend on the clock). Stale "inert / Epic 4" comments in those helpers refreshed. `test_oracle_correctness` feeds `_params` to the exhaustive enumerator (not GRASP), so its `stagnation_iters=100` is genuinely inert — left as-is.
- **Validation:** new `test_stagnation.py` (2) + `test_time_budget.py` (1) pass; metamorphic (53) + quality-gate + reproducibility + output/validator fixture tests green; `validate_solver_options` rejection suite extended (time-budget nan/inf/0/neg, stagnation neg) — all pass. ruff check + format clean and basedpyright 0/0/0 on all touched src + new test files.

### File List

- `src/steeproute/solver/grasp.py` (modified — three-way §Cat 5e termination, `convergence_status`, `STAGNATION_ITERS_DEFAULT_PLACEHOLDER`)
- `src/steeproute/models.py` (modified — re-homed `ConvergenceStatus` literal)
- `src/steeproute/output.py` (modified — import `ConvergenceStatus` from models)
- `src/steeproute/cli/query.py` (modified — read `solver.convergence_status`; resolve stagnation default; thread flags into validation)
- `src/steeproute/cli/_shared.py` (modified — validate `--time-budget` / `--stagnation-iters`; help text)
- `tests/integration/test_stagnation.py` (new — stagnation termination + disable case)
- `tests/integration/test_time_budget.py` (new — wall-clock termination on the real fixture)
- `tests/unit/test_area_parsing.py` (modified — time-budget/stagnation rejection + boundary cases)
- `tests/unit/test_grasp_construction.py` (modified — `stagnation_iters=0`; refreshed helper docstring)
- `tests/integration/conftest.py` (modified — `make_toy_solver_params` non-binding `time_budget`; **added shared session-scoped `grenoble_fixture`** + single-sourced `GRENOBLE_*` build constants)
- `tests/integration/test_grasp_on_fixture.py` (modified — `stagnation_iters=0`; consume shared `grenoble_fixture` instead of rebuilding the setup chain)
- `tests/integration/test_output_on_fixture.py` (modified — `stagnation_iters=0`; consume `grenoble_fixture`)
- `tests/integration/test_validator_on_fixture.py` (modified — `stagnation_iters=0`; consume `grenoble_fixture`)
- `tests/integration/test_grasp_reproducible.py` (modified — `stagnation_iters=0`; consume `grenoble_fixture`)

### Review Findings

Lightweight review 2026-06-09 (`code-review` medium: 3 correctness angles + cleanup/altitude). Correctness clean — termination off-by-one, stagnation-before-time ordering, the `!=` float compare, empty-graph status, FR29, and all signature propagation (`validate_solver_options` call sites, `ConvergenceStatus` re-home, `STAGNATION_ITERS_DEFAULT_PLACEHOLDER` import) verified. Two cleanup notes surfaced:

- [Review][Patch] **Duplicated fixture-build chain.** The grenoble_small setup→climbs→contract boilerplate (+ path constants + `regenerate.py` constant loader) was copy-pasted across five integration tests. **Fixed:** extracted a session-scoped `grenoble_fixture` (built once for the whole suite, ~4× faster) into `tests/integration/conftest.py`, single-sourcing the PRD-default build constants as `GRENOBLE_*` so detection thresholds and `SolverParams` can't drift; the five tests now consume it. [tests/integration/conftest.py + the four `*_on_fixture` / reproducible / time_budget tests]
- [Review][Dismiss] **Per-iteration `total_objective()` runs even when stagnation is disabled and no callback is installed.** Genuinely wasted work on that path, but `total_objective()` sums ≤ N≤5 held solutions — negligible — and a guard would cost a branch + readability. Kept the simpler always-compute form (the user agreed to leave it).

## Change Log

- 2026-06-09: Implemented Story 7.2 — time-budget + stagnation termination in `GraspSolver.run()` with the full `convergence_status` three-value contract (FR/§Cat 5e); CLI reads the status and validates the new flag bounds. Status → review.
- 2026-06-09: Applied lightweight-review patch — extracted the duplicated grenoble_small fixture-build into a shared session-scoped `grenoble_fixture` conftest fixture (5 integration tests de-duplicated).
- 2026-06-09: Close-out — full suite green (751 passed / 2 deselected), lint/format/types clean. Status → done.
