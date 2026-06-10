# Story 7.5: Run summary on stdout

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want a clear run summary printed to stdout at the end of every successful `steeproute` invocation — parameters, routes returned vs. N requested, validation-failure count, graceful-degradation explanation, convergence status, and wall-clock total,
so that FR22 is fulfilled and I can judge a run's outcome at a glance without opening any HTML.

## Acceptance Criteria

1. **Summary block emitted after render, always on stdout.** On the normal (non-interrupted) termination path, `cli/query.py` prints a run-summary block to stdout after `output.render(...)` returns and before the validation-driven `ctx.exit(...)`. It is emitted regardless of `--quiet` — per Architecture §Cat 8, only intermediate progress is suppressible; the final summary is always stdout. (The interrupt path keeps exiting 130 *before* this block — Story 7.3 already renders the partial set there; wiring a summary into the interrupt path is out of scope.)

2. **Exact structure with stable labels.** The block matches this literal structure so tests can regex-match the labels:
   ```
   --- Run summary ---
   parameters: theta=<v> j_max=<v> n=<v> seed=<v> iter_budget=<v> time_budget=<v> stagnation_iters=<v>
   routes_returned: <X>/<N>
   validation_failures: <count>
   convergence_status: <converged|budget-exhausted|interrupted>
   degradation: <explanation>                      # only when routes_returned < N
   wall_clock_total: <seconds>s
   ```
   The `--- Run summary ---` delimiter line is present so downstream scripts can split stdout on it. Plain ASCII (matching the §Cat 8 stdout-discipline already used by progress and the degradation line).

3. **Parameters line.** Reads the run's `SolverParams`: `theta`, `j_max`, `n`, `seed`, `iter_budget`, `time_budget`, `stagnation_iters`. An unseeded run (`seed is None`) renders a stable token (e.g. `seed=none`), not `None` or a crash.

4. **Counts.** `routes_returned` is `len(validated.routes)` over `params.n` (the same set the exit code reads — passed and failed alike). `validation_failures` is the count of returned routes failing per-route validation (`not r.validation.passed`).

5. **Degradation folded in.** The `degradation:` line carries the Story 7.4 explanation and appears only when `routes_returned < N`. This story **absorbs** 7.4's interim standalone `print(degradation)` into this line — the interim print is removed so the explanation appears once, inside the summary.

6. **Wall-clock total.** `wall_clock_total` reports whole-invocation elapsed seconds (started near the top of `cli()`, computed at summary time), mirroring `cli/setup.py`'s `time.perf_counter()` pattern. Degradation never affects the exit code (§Cat 6c, unchanged).

7. **Tests.** `tests/e2e/test_run_summary.py` runs in-process (CliRunner, per the existing Journey e2e pattern) and covers:
   - `test_happy_path` — a successful query regex-asserts each label line appears with values matching the invocation (e.g. `re.search(r"routes_returned:\s*(\d+)/(\d+)", stdout)`).
   - `test_degraded_path` — the `degradation:` line appears when fewer than N routes are returned (reuse 7.4's degraded regime).
   - `test_validation_failure_path` — `validation_failures:` shows a non-zero count when some routes fail validation.
   - `test_quiet_preserves_summary` — with `--quiet`, no progress lines appear during the run (Story 7.1), but the summary still appears on stdout at the end.

## Tasks / Subtasks

- [x] Add a whole-invocation wall-clock timer (AC: #6)
  - [x] Capture `start = time.perf_counter()` near the top of `cli()`; compute elapsed at summary time (add `import time`)
- [x] Build the summary as a pure formatter, then print it (AC: #1, #2, #3, #4)
  - [x] Add `_run_summary(validated, params, status, wall_clock_s, degradation) -> str` returning the multi-line block (testable without I/O)
  - [x] Print it after the normal-path `output.render(...)`/`_validate_and_render(...)` returns, before `ctx.exit(_exit_code_for(...))`
- [x] Fold in degradation; remove the interim print (AC: #5)
  - [x] Render the `degradation:` line inside the summary only when set; delete the standalone `print(degradation)` added in Story 7.4
- [x] Tests (AC: #7)
  - [x] `tests/e2e/test_run_summary.py` — happy / degraded / validation-failure / quiet-preserves-summary

## Dev Notes

- **Insertion site.** The summary goes in the normal path at [query.py:340-357](src/steeproute/cli/query.py:340), where `validated` and `degradation` already exist and `solver.convergence_status` is readable. Print after render, before `ctx.exit(_exit_code_for(validated))`. The interrupt path ([query.py:317-335](src/steeproute/cli/query.py:317)) exits 130 earlier and is untouched.
- **`--quiet` already works for free.** Quiet only gates the progress callback ([query.py:253-255](src/steeproute/cli/query.py:253)); a plain `print()` after the solve is unaffected. No flag check needed — the `test_quiet_preserves_summary` test just confirms this.
- **Reuse the existing stdout patterns.** Keep it plain ASCII `print()` to stdout (never `logging`, which §Cat 8 binds to stderr) — same discipline as the `cache-hit` cue ([query.py:194](src/steeproute/cli/query.py:194)) and the `progress:` lines. `cli/setup.py::_print_summary` ([setup.py:210-224](src/steeproute/cli/setup.py:210)) is the sibling precedent (labels + `elapsed: <s> s`); its `time.perf_counter()` bracket ([setup.py:123,182](src/steeproute/cli/setup.py:123)) is the timer pattern to mirror.
- **Keep it a pure formatter.** A `_run_summary(...) -> str` (like `_degradation_message`) keeps the block testable and the side-effecting `print` trivial. Wall-clock is the only non-deterministic value — keep it out of the formatter's comparison-sensitive parts (tests regex labels, not the elapsed number).
- **Data sources.** Parameters from `SolverParams` ([models.py:202-214](src/steeproute/models.py:202)); `routes_returned`/`validation_failures` from `ValidatedRouteSet` ([models.py:266-319](src/steeproute/models.py:266) — `routes`, `RouteValidation.passed`); `convergence_status` from `solver.convergence_status` ([grasp.py:192](src/steeproute/solver/grasp.py:192)). `seed` may be `None` — render `seed=none`.
- **Degradation is single-sourced.** `_degradation_message` ([query.py:399-415](src/steeproute/cli/query.py:399)) already returns the exact string and `_validate_and_render` returns it. Feed that value straight into the summary; don't recompute. Removing the interim `print(degradation)` (lines 349-350) is the whole "absorb into the summary" change 7.4 anticipated.
- **Validation-failure test fixture.** A returned route fails validation when it can't meet the route-level `--theta` floor (§Cat 6c). Craft the `test_validation_failure_path` query by tightening `--theta` on the seeded cache so at least one returned route fails (`validation_failures > 0`, exit 1); pin the chosen knobs with a comment as the other e2e tests do. Reuse the `seeded_cache` / `run_query` fixtures ([tests/e2e/conftest.py:86-164](tests/e2e/conftest.py:86)).
- **Degraded regime is known.** Story 7.4 established that distinctness binds at `--theta 0.35`: `--j-max 0.30` → 4 of N=5 routes (a genuine `<N` degradation). Reuse that for `test_degraded_path` rather than re-discovering a sparse regime.
- **FR29 unaffected.** The summary derives only from deterministic post-solve values and wall-clock; it never feeds the RNG or construction.

### Project Structure Notes

- Files touched: `src/steeproute/cli/query.py` (timer + `_run_summary` helper + print; remove interim degradation print). New: `tests/e2e/test_run_summary.py`.
- No new CLI flags, models, solver, or output-layer changes — this is a stdout-formatting story over data that already exists at the end of the run.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 7.5] — run-summary ACs and exact format block (lines 906-929).
- [Source: _bmad-output/planning-artifacts/prd.md] — FR22 (run summary on stdout: parameters, routes returned vs. N, validation-failure count, wall-clock total).
- [Source: _bmad-output/planning-artifacts/architecture.md] — §Cat 8 stream discipline: final summary always stdout, only intermediate progress suppressible (~line 573); FR22 → `cli/query.py` end-of-run block (line 178).
- [Source: src/steeproute/cli/query.py:340-357] — normal-path summary insertion site; [query.py:349-350] interim degradation print to remove; [query.py:399-415] `_degradation_message`; [query.py:194] existing cache-hit stdout cue.
- [Source: src/steeproute/cli/setup.py:210-224, 123, 182] — `_print_summary` sibling precedent + `time.perf_counter()` timer pattern.
- [Source: src/steeproute/models.py:202-214, 266-319] — `SolverParams` fields, `ValidatedRouteSet.routes` / `RouteValidation.passed` (count sources).
- [Source: src/steeproute/solver/grasp.py:192] — `solver.convergence_status` (three-value contract).
- [Source: tests/e2e/conftest.py:86-164] — `seeded_cache`, `run_query` fixtures for the in-process tests.
- [Source: 7-4-graceful-degradation-messaging-for-sparse-areas.md] — the interim degradation stdout print this story absorbs; the `--theta 0.35` / `--j-max 0.30` degraded regime to reuse.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- **Removing the interim degradation print didn't break Story 7.4's tests.** 7.4's `test_degradation.py` uses `_DEGRADATION_PATTERN.search(result.output)` — a substring search. The explanation now appears on stdout only as the summary's `degradation: Only X distinct routes…` line, and the `Only X…` text still matches as a substring of that line, so both 7.4 e2e tests stayed green with no edit. The relaxed-j-max test's `"would exceed the overlap threshold" not in output` assertion also holds because the relaxed run returns full N (no degradation line).
- **Summary prints on the validation-failure (exit 1) path too.** It's emitted before `ctx.exit(_exit_code_for(...))`, so `test_validation_failure_path` (monkeypatched bogus route → exit 1) still sees the block with `validation_failures: 1`. Confirmed the interrupt path is untouched — it `ctx.exit(130)`s earlier, so no summary there (out of scope per the ACs).

### Completion Notes List

- **`cli/query.py`** — added `import time`; capture `start = time.perf_counter()` at the top of `cli()` (spans the whole invocation). New pure `_run_summary(validated, params, status, wall_clock_s, degradation) -> str` builds the `--- Run summary ---` block (plain ASCII, stable labels): `parameters:` (theta, j_max, n, seed [→ `none` when unseeded], iter_budget, time_budget, stagnation_iters), `routes_returned: X/N`, `validation_failures:` (count of `not r.validation.passed`), `convergence_status:`, conditional `degradation:` (only when `routes_returned < N`), `wall_clock_total: <s>s`. Printed on the normal path after render, before the exit-code call, so it always appears (including exit 1) and is unaffected by `--quiet` (which only gates the progress callback). Removed Story 7.4's interim standalone `print(degradation)` — the explanation is now single-sourced into the summary's field (same value computed once in `_validate_and_render`). The interrupt path (`ctx.exit(130)`) is untouched. Refreshed two stale module/inline comments that referenced the summary as future work.
- **`tests/e2e/test_run_summary.py`** (new) — `test_happy_path` (regex-asserts every label line; n + seed match the invocation; no degradation line at full N), `test_degraded_path` (`--theta 0.35` → `degradation:` line with count == emitted reports), `test_validation_failure_path` (monkeypatched bogus route → exit 1, `validation_failures >= 1`, summary still present), `test_quiet_preserves_summary` (`--quiet` → no `progress:` lines but summary present). In-process via the shared `seeded_cache`/`run_query` fixtures.
- **Validation:** full suite **762 passed / 2 deselected** (was 758 at 7.4 close-out; +4 new e2e tests). `ruff format` + `ruff check` clean; basedpyright 0/0/0 on touched files. Message is plain ASCII, so no Windows console-encoding concern (unlike 7.4's `≤` probe). FR29 preserved — the summary derives only from deterministic post-solve values plus wall-clock; it never feeds the RNG or construction.

### File List

- `src/steeproute/cli/query.py` (modified — wall-clock timer; `_run_summary` helper; summary print on the normal path; removed interim degradation print; comment refresh)
- `tests/e2e/test_run_summary.py` (new — happy / degraded / validation-failure / quiet-preserves-summary)

## Change Log

- 2026-06-10: Implemented Story 7.5 — end-of-run summary on stdout (FR22). `cli/query.py` prints a labeled `--- Run summary ---` block (parameters, routes_returned X/N, validation_failures, convergence_status, conditional degradation, wall_clock_total) after render on the normal path, always stdout regardless of `--quiet` (§Cat 8); absorbed Story 7.4's interim degradation print into the summary's `degradation:` field. Full suite 762 passed; lint/format/types clean. Status → review.
- 2026-06-10: Lightweight review (medium effort) — no required changes. One low-severity, spec-conformant gap surfaced (`validation_failures` counts per-route failures only, not set-level pairwise violations that also drive exit 1); verified not reachable on a correct run (TopNTracker admission + `validate_set` share the same threshold/identity) and sanctioned by AC #4, so left out of scope. Close-out — Status → done.
