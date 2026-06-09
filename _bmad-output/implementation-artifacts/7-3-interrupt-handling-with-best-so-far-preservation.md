# Story 7.3: Interrupt handling with best-so-far preservation

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want Ctrl-C during a query to preserve best-so-far top-N routes to disk with `convergence_status: "interrupted"` and exit 130, leaving the cache valid and reusable — and I want every report to show the iteration at which the search last actually improved,
so that Journey 3's partial-progress-preserved experience works (NFR3/FR14) and I can tell at a glance how far a run got before it stopped, killed or not.

## Acceptance Criteria

1. **Ctrl-C mid-solve flushes best-so-far.** `cli/query.py::main` catches `KeyboardInterrupt` raised from the solve, reads `solver.best_so_far` as the working solutions, passes them through the validator, and calls `output.render(...)` with `convergence_status="interrupted"`. The process exits **130**. Outputs are written before the process exits.

2. **Interrupt before any solution.** An interrupt that arrives before any route has been admitted (e.g. during stages 8–9, or before the solver's first admission) exits 130 with **no output files** and a single stderr line `interrupted before any solution found`.

3. **Cache integrity (NFR3).** The cache directory is byte-unchanged across an interrupted run (the query side never writes the cache), and a re-invocation of `steeproute` with the same args on the same cache afterward succeeds normally.

4. **Convergence-iteration exposure.** `GraspSolver` exposes a `convergence_iteration: int` attribute holding the 1-based iteration at which the top-N total objective last changed (the last objective-improving admission) — equivalently `(i + 1) − stagnation_counter` at termination. It is **anytime-readable** (initialized in `__init__`, updated each iteration, like `best_so_far`/`convergence_status`), so it holds the correct value on every termination path including a `KeyboardInterrupt` that abandons the loop. `0` means no improvement ever landed (empty graph, no admissible route, or interrupt before the first admission).

5. **Reports display the convergence iteration.** `convergence_iteration` flows through `output.render(...)` into each report's metadata block and is shown in the HTML metadata table and the JSON sidecar, mirroring `convergence_status` (Architecture §Cat 9: HTML + JSON carry the same metadata). It appears on **all** reports regardless of `convergence_status`.

6. **Tests.** `tests/e2e/test_interrupt.py` launches `uv run steeproute` via `subprocess`, sends the platform-appropriate interrupt mid-solve (`CTRL_C_EVENT` on Windows with `CREATE_NEW_PROCESS_GROUP`, `SIGINT` on POSIX), and asserts: exit 130, output files present with `convergence_status: "interrupted"` and a `convergence_iteration` value in metadata, cache contents unchanged. `tests/integration/test_interrupt_integration.py` uses a `monkeypatch`-raised `KeyboardInterrupt` at a chosen iteration to assert the in-process flow (outputs written, status correct, validator ran on the partial set, `convergence_iteration` reflects the last admission). Coverage also asserts the no-solution path (AC #2) and the post-interrupt re-run (AC #3), plus that `convergence_iteration == (i+1) − stagnation_counter` on a normal converged/budget run.

## Tasks / Subtasks

- [x] Add `convergence_iteration` to `GraspSolver` (AC: #4)
  - [x] Initialize in `__init__` (alongside `convergence_status`); update to `i + 1` whenever `current_objective != last_objective` inside the `run()` loop
  - [x] Confirm it survives a mid-loop `KeyboardInterrupt` (instance attribute, not a local)
- [x] Wire interrupt handling in `cli/query.py::main` (AC: #1, #2, #3)
  - [x] Wrap the contraction → solve region in `try/except KeyboardInterrupt`
  - [x] On catch with non-empty `best_so_far`: validate → `output.render(..., convergence_status="interrupted", ...)` → exit 130
  - [x] On catch with empty `best_so_far`: write nothing, emit the stderr warning, exit 130
  - [x] Verify the realized exit code is actually 130 (see Dev Notes — click standalone-mode trap)
- [x] Thread `convergence_iteration` through the output layer (AC: #5)
  - [x] `output.render` + `_build_metadata` accept `convergence_iteration`; add it to the metadata dict
  - [x] Add a metadata-table row in `templates/route.html.j2`
  - [x] Pass `solver.convergence_iteration` from `query.py` on both the normal and interrupted render calls
- [x] Tests (AC: #6)
  - [x] `tests/e2e/test_interrupt.py` (subprocess + signal; exit 130; files + metadata; cache unchanged; re-run succeeds)
  - [x] `tests/e2e/test_interrupt_in_process.py` (monkeypatch interrupt; in-process flow; no-solution path) — placed in `tests/e2e/` (not `tests/integration/`); see Completion Notes
  - [x] Extend an existing solver/integration test to assert `convergence_iteration == (i+1) − stagnation_counter` (in `test_stagnation.py`)
  - [x] Update `tests/unit/test_output.py` metadata assertions for the new field

## Dev Notes

- **Best-so-far is already anytime-readable.** `solver.best_so_far` returns `tracker.current_top()` ([grasp.py:194](src/steeproute/solver/grasp.py:194)) at any point — that is the whole interrupt contract. The handler just reads it after catching the interrupt; no solver change is needed for the flush itself.
- **`convergence_iteration` must be an instance attribute, not derived from progress events.** A `KeyboardInterrupt` unwinds `run()` and discards its local `i` / `stagnation_counter` ([grasp.py:236-245](src/steeproute/solver/grasp.py:236)), so the value has to be stored on `self` as the loop runs. **Do not** try to reconstruct it at the CLI from the last `ProgressEvent`: progress is throttled and fully suppressed under `--quiet` (the callback is `None`), so the last event rarely coincides with the final iteration. Track it directly: it equals `(i+1) − stagnation_counter` at every point because the counter resets to 0 exactly when an improvement lands, so recording `i + 1` on each objective change is the same number — and it's robust to the interrupt.
- **FR29 holds for the new attribute.** `convergence_iteration` derives only from the deterministic objective sequence, so a fixed seed reproduces it on the iter-budget/stagnation paths. It varies on the time-budget and interrupt paths — expected, exactly as `convergence_status` already does (§Cat 5e soft budgets). It never feeds the RNG or construction.
- **The exit-130 trap (verify, don't assume).** The epic text says "re-raise `KeyboardInterrupt` — the Epic 1 wrapper maps it to 130". But `run_entry_point` ([cli/_shared.py:50-61](src/steeproute/cli/_shared.py:50)) only sees the interrupt if it reaches it, and `_invoke_command` calls `cli.main(standalone_mode=True)` ([cli/query.py:333-339](src/steeproute/cli/query.py:333)) — click's standalone mode catches `KeyboardInterrupt`, prints `Aborted!`, and raises `SystemExit(1)`, which would mask the 130. After the flush+render, get a clean 130 by `click.get_current_context().exit(130)` (mirrors the existing validation-driven `ctx.exit(...)` at [query.py:284](src/steeproute/cli/query.py:284)) rather than a bare re-raise. The e2e test asserting exit 130 is the ground truth — make it pass.
- **Catch broadly enough for AC #2.** Wrap the stages 8–9 + solve region ([query.py:240-258](src/steeproute/cli/query.py:240)) so an interrupt during detection/contraction (before the solver has admitted anything) lands in the same handler; branch on whether `best_so_far` is non-empty to decide render-vs-warn. `output.render` is already atomic (`.tmp` + `os.replace`, [output.py:145](src/steeproute/output.py:145)), so a second interrupt mid-render can't leave a half-written file.
- **Metadata plumbing is a one-field addition.** `_build_metadata` ([output.py:169-198](src/steeproute/output.py:169)) builds the dict shared verbatim by HTML and JSON; add `"convergence_iteration"` next to `"convergence_status"` and a `<tr>` in the template next to the `convergence` row ([route.html.j2:73](src/steeproute/templates/route.html.j2:73)). `test_output.py`'s `_EXPECTED_METADATA_STRINGS` ([tests/unit/test_output.py:83](tests/unit/test_output.py:83)) pins the metadata surface — extend it.
- **Scope note — convergence-iteration is an addition to the epic.** The original Epic 7.3 ACs cover only interrupt handling. AC #4–#5 (display the convergence iteration on every report) were added at the user's request; they belong here because they share all of 7.3's touch-points and the anytime-readable-attribute pattern is the same one interrupt handling relies on.
- **Out of scope (don't drift):** the `--- Run summary ---` stdout block reporting `convergence_status`/params (Story 7.5); sparse-area degradation messaging (Story 7.4). This story only handles the interrupt flush and the per-report convergence-iteration display.

### Project Structure Notes

- Files touched: `src/steeproute/solver/grasp.py` (`convergence_iteration` attribute), `src/steeproute/cli/query.py` (interrupt handler + thread the new field), `src/steeproute/output.py` (`render`/`_build_metadata` signature + metadata field), `src/steeproute/templates/route.html.j2` (metadata row). New: `tests/e2e/test_interrupt.py`, `tests/integration/test_interrupt_integration.py`. Modified: `tests/unit/test_output.py`.
- The four exit codes (0/1/2/130) and the `KeyboardInterrupt → 130` mapping already exist in `run_entry_point`; this story makes the query CLI realize the 130 path correctly with a flush.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 7.3] — interrupt-handling ACs and BDD acceptance scenarios (lines 877-890).
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 5b] — anytime best-so-far + interrupt at the CLI layer; §Cat 5e termination table (`interrupted` status); §Cat 6c/§Cat 9 (HTML+JSON parallel metadata).
- [Source: _bmad-output/planning-artifacts/prd.md] — FR14 (Ctrl-C best-so-far + interrupt exit code), FR30 (distinct exit codes), NFR3 (cache valid after interrupt), FR29 (determinism).
- [Source: src/steeproute/solver/grasp.py:194-267] — `best_so_far`, `convergence_status`, and the `run()` loop with the `stagnation_counter`/objective bookkeeping (the `convergence_iteration` wiring site).
- [Source: src/steeproute/cli/query.py:255-284, 333-339] — solve + `output.render` call (interrupt-handler site); `_invoke_command` standalone-mode trap.
- [Source: src/steeproute/cli/_shared.py:50-61] — `run_entry_point` exit-code policy (KeyboardInterrupt → 130).
- [Source: src/steeproute/output.py:56-65, 169-198] — `render`/`_build_metadata` signatures + metadata block (add `convergence_iteration`).
- [Source: src/steeproute/templates/route.html.j2:71-83] — metadata table (`convergence` row to mirror).
- [Source: tests/unit/test_output.py:80-95, 168-174] — `_EXPECTED_METADATA_STRINGS` metadata-surface assertions.
- [Source: Story 7.2 — 7-2-time-budget-and-stagnation-termination.md] — `convergence_status` three-value contract this story completes with `interrupted`.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- **Windows real-Ctrl+C delivery (probed before writing the e2e test).** A `CTRL_C_EVENT` sent to a child created with `CREATE_NEW_PROCESS_GROUP` is *ignored* — MSDN: that flag disables Ctrl+C for the new group (probe child ran to completion, exit 0, no `KeyboardInterrupt`). Re-enabling in the child with `ctypes.windll.kernel32.SetConsoleCtrlHandler(None, 0)` makes a targeted `CTRL_C_EVENT` arrive as a real `KeyboardInterrupt` → exit 130 (probe confirmed). The e2e test therefore launches the entry point via `python -u -c "<reenable>; ...main()"` so the re-enable is the *only* scaffolding around the genuine production `main()`; the new process group keeps the signal off the pytest runner.
- **Timing the interrupt.** The test waits for the first throttled `progress:` line (emitted only from inside the solver loop, after dozens of iterations at ~0.7 ms/iter) before signalling — that guarantees the interrupt lands mid-solve with ≥1 admitted route, so the partial-flush branch runs deterministically without a fixed sleep.
- **`convergence_iteration == (i+1) − stagnation_counter`.** Verified on the deterministic single-self-loop fixture (`test_stagnation.py`): the sole admission lands on iteration 1, final event has `iteration=6, stagnation_counter=5`, and `6 − 5 == 1 == solver.convergence_iteration`.

### Completion Notes List

- **`solver/grasp.py`** — added the public `convergence_iteration: int` attribute (init `0` in `__init__`, set to `i + 1` in `run()` whenever the top-N objective changes). It's an instance attribute, not a loop local, so it survives a `KeyboardInterrupt` that unwinds `run()` — that's what makes it readable on the interrupt path. `0` = no improvement ever landed.
- **`cli/query.py`** — wrapped the detect → contract → solve region in `try/except KeyboardInterrupt`. On catch: if `solver`/`contracted` exist and `best_so_far` is non-empty, validate + render tagged `"interrupted"` then `ctx.exit(130)`; otherwise warn `interrupted before any solution found` on stderr and `ctx.exit(130)`. Both `solver` and `contracted` start `None` so an interrupt during stages 8-9 is handled identically. The interrupt is caught *inside* the command (not re-raised) because click's standalone mode would map a bubbling `KeyboardInterrupt` to "Aborted!" / exit 1 — `ctx.exit(130)` → `SystemExit(130)` is forwarded verbatim by `_invoke_command`. Verified exit 130 via the real-signal e2e test. The validate → render pair is single-sourced through a local `_validate_and_render(route_set, status, contracted, convergence_iteration)` closure shared by both paths, and the handler also sets `solver.convergence_status = "interrupted"` so the attribute agrees with the rendered report (review fixes).
- **`output.py`** — `render` and `_build_metadata` take `convergence_iteration: int`; it's emitted in the metadata dict next to `convergence_status`, so HTML and JSON mirror it (§Cat 9).
- **`templates/route.html.j2`** — added a `convergence_iteration` row to the run-metadata table, next to `convergence`.
- **Test placement deviation (intentional).** The story named `tests/integration/test_interrupt_integration.py` for the in-process flow, but that test runs the *query CLI* against a seeded cache and so depends on the offline `seeded_cache` / `run_query` fixtures that live in `tests/e2e/conftest.py`. Rather than duplicate ~25 lines of offline cache-seeding into the integration layer, it lives at `tests/e2e/test_interrupt_in_process.py` alongside its fixtures. Both interrupt tests are thus in the e2e layer.
- **Added fixture** `fixture_query_target` to `tests/e2e/conftest.py` (exposes `(center, query_radius_km)`) so the subprocess test can build its own command line.
- **Signature-change fan-out:** `render`'s new positional `convergence_iteration` required updating its two non-CLI callers — `tests/unit/test_output.py` (new distinctive `987` value asserted present in both HTML + JSON) and `tests/integration/test_output_on_fixture.py`.
- **Validation:** full suite **754 passed / 2 deselected** (was 751 at 7.2 close-out; +3 interrupt tests); the real-Ctrl+C subprocess test passes on the primary Windows platform. `ruff check` + `ruff format` clean, basedpyright 0/0/0 on all touched files. FR29 preserved — `convergence_iteration` derives only from the deterministic objective sequence; it never feeds the RNG or construction.

### File List

- `src/steeproute/solver/grasp.py` (modified — `convergence_iteration` attribute + loop update)
- `src/steeproute/cli/query.py` (modified — `KeyboardInterrupt` handler around detect→contract→solve; thread `convergence_iteration`; import `ContractedGraph`)
- `src/steeproute/output.py` (modified — `render`/`_build_metadata` take + emit `convergence_iteration`)
- `src/steeproute/templates/route.html.j2` (modified — `convergence_iteration` metadata row)
- `tests/e2e/test_interrupt.py` (new — real-signal subprocess interrupt; exit 130; flush; cache unchanged; re-run)
- `tests/e2e/test_interrupt_in_process.py` (new — monkeypatch in-process flow + no-solution path)
- `tests/e2e/conftest.py` (modified — `fixture_query_target` fixture)
- `tests/integration/test_stagnation.py` (modified — `convergence_iteration` identity assertion)
- `tests/unit/test_output.py` (modified — thread `convergence_iteration`; assert new metadata field)
- `tests/integration/test_output_on_fixture.py` (modified — `render` signature call site)

### Review Findings

Lightweight review 2026-06-09 (`code-review` medium: 3 correctness + 3 cleanup + 1 altitude angle, 1-vote verify). No correctness bugs in the happy or interrupt paths; the `output.render` signature fan-out traced clean. Findings triaged:

- [Review][Patch] **`convergence_status` not set on the solver after an interrupt** (verdict: plausible/latent). The handler rendered the `"interrupted"` literal but left `solver.convergence_status == "budget-exhausted"` — a footgun for Story 7.5's run summary. **Fixed:** the handler now sets `solver.convergence_status = "interrupted"`. [cli/query.py]
- [Review][Patch] **Duplicated `validate` + 9-arg `output.render` pair** across the interrupt handler and the normal path (cleanup). **Fixed:** extracted a local `_validate_and_render(route_set, status, contracted, convergence_iteration)` closure single-sourcing the render call shape, so the two paths can't drift (FR28). [cli/query.py]
- [Review][Patch] **FR28 comment overstated** "outputs written before exiting" (a rare *second* Ctrl-C during render can truncate the set; verdict confirmed: a bubbling second interrupt also maps to exit 1, not 130 — inherent to cleanup paths, not worth engineering around). **Fixed:** narrowed the comment to scope the guarantee to a single Ctrl-C and note per-file atomicity keeps every emitted file + the cache valid. [cli/query.py]
- [Review][Dismiss] **`convergence_iteration` skipped when an admitted route's objective equals the prior total (e.g. `0.0`)** (verdict: refuted). The θ floor rejects a zero-objective route under any θ>0; reachable only at the explicit opt-out `--theta 0`, and the `!=` compare predates this story (Story 7.1/7.2 stagnation logic). Left as-is.
- [Review][Dismiss] **e2e launches via `python -c "...main()"` rather than the `steeproute` console script** (altitude). The entry-point shim is already covered by `test_cli_smoke.py`; the Windows signal bootstrap justifies the custom launch. Left as-is.
- [Review][Dismiss] **`test_interrupt.py` progress-wait could fail slowly** if progress emission regressed (bounded by the child's `--time-budget`, not infinite). Real but low-value test hardening; left as-is.

Post-fix: `cli/query.py` basedpyright 0/0/0, ruff clean; e2e + unit (90) and the affected integration tests (3) green, including the real-Ctrl+C subprocess test on Windows.

## Change Log

- 2026-06-09: Implemented Story 7.3 — Ctrl-C interrupt handling in `cli/query.py` (best-so-far flush → `convergence_status="interrupted"` → exit 130, or stderr warning when no solution yet), plus the `GraspSolver.convergence_iteration` attribute surfaced in every report's metadata (HTML + JSON). Real-signal e2e test passes on Windows. Status → review.
- 2026-06-09: Applied lightweight-review patches — set `solver.convergence_status = "interrupted"` on the interrupt path, extracted a shared `_validate_and_render` closure (de-duplicated the validate/render pair), and narrowed the FR28 comment. Types/lint clean; affected tests green.
- 2026-06-09: Close-out — full suite green, lint/format/types clean. Status → done.
