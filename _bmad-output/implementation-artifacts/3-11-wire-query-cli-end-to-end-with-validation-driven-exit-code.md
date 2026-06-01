# Story 3.11: Wire query CLI end-to-end with validation-driven exit code

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want `steeproute --center ... --radius ... --seed 42` on a prepared area to produce the full happy-path output — up to N validated reports with exit 0, or reports-with-banners + exit 1 if any route fails validation,
so that Journey 1 works end-to-end and FR28, FR30 (exit codes 0 and 1) are fulfilled.

## Acceptance Criteria

1. `cli/query.py::main` (the existing `cli` callback) wires the full happy path on top of the Story 2.10 cache-hit path: `check_coverage` → `detect_climbs` (stage 8) → `contract_climbs` (stage 9) → `GraspSolver.run()` → `validate(...)` → `output.render(...)` → compute the exit code. The solver/validator/render parameters come from the parsed CLI flags (built into a `SolverParams` + `ProvenanceInfo`), and `prepared.graph` is the `base_graph` passed to `render`.

2. **Exit code is validation-driven (§Cat 6c, FR28/FR30):** `0` when every route passes, `1` when any `RouteValidation.passed is False` **OR** `ValidatedRouteSet.set_violations` is non-empty. The exit code is computed **after** all HTML + JSON files are written, so disk state is identical regardless of exit code. The code reaches the process exit (see Dev Notes — returning it from the click callback is **not** sufficient).

3. `tests/e2e/test_journey_1_happy_path.py`: seed a fixture cache (in-process `setup` CLI with the committed-graphml patch), then run the query CLI against it with a fixed seed; asserts exit 0, exactly N `route-<i>.html` + N `route-<i>.json` files (`i` in `1..N`, FR21), and each HTML parses and carries the map + elevation-profile sections.

4. `tests/e2e/test_seeded_reproducibility.py`: runs the same query twice with `--seed 42` and asserts **byte-identical JSON sidecars** across runs (FR29/NFR4 verified end-to-end).

5. `tests/e2e/test_validation_failure_path.py`: `monkeypatch`-injects a solver output containing a deliberately-invalid `Solution` (e.g. references a non-existent edge); asserts exit `1`, reports **still written** to disk, and the HTML contains the `VALIDATION FAILED` banner (FR27/FR28).

6. Progress/interrupt are out of scope: the solver is constructed with a stub no-op progress callback (`progress_callback=None`); real progress UI + Ctrl-C handling land in Epic 4 (Stories 4.1/4.3).

7. All four CI gates green on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. Coverage floors hold (`cli/query.py` under the 80% overall floor, not the 95% pure-logic floor).

## Tasks / Subtasks

- [x] Task 1: Replace the Story 2.10 stub body in `cli/query.py::cli` with the full chain. (AC: #1, #6)
  - [x] After `check_coverage`/`emit_osm_age_warning`, build `SolverParams` from the parsed flags, resolve `iter_budget` (see Dev Notes — `None` needs a concrete default), and build `ProvenanceInfo` from the cache manifest (see Dev Notes for the field mapping).
  - [x] `detect_climbs(prepared.graph, theta, min_climb_ground_length)` → `contract_climbs(prepared.graph, climbs, l_connector)` → `GraspSolver(contracted, params, np.random.default_rng(seed), progress_callback=None).run()`.
  - [x] `validate(solutions, contracted, params)` → `output.render(validated, prepared.graph, contracted, params, provenance, "budget-exhausted", output_dir)`.
- [x] Task 2: Compute the exit code from the `ValidatedRouteSet` and thread it to the process exit. (AC: #2)
  - [x] `exit_code = 1 if any(not r.validation.passed for r in vset.routes) or vset.set_violations else 0`, computed **after** `render` returns.
  - [x] Make the code reach the OS exit — `return` from the click callback is discarded by `standalone_mode=True` (see Dev Notes). Keep exit-2 (`PreExecutionError`) and exit-130 semantics from `run_entry_point` intact.
- [x] Task 3: `tests/e2e/test_journey_1_happy_path.py` — seed cache in-process, run query, assert exit 0 + file count/pattern + HTML parses with map + profile. (AC: #3)
- [x] Task 4: `tests/e2e/test_seeded_reproducibility.py` — run twice with `--seed 42`, assert byte-identical JSON. (AC: #4)
- [x] Task 5: `tests/e2e/test_validation_failure_path.py` — `monkeypatch` an invalid `Solution`, assert exit 1 + files on disk + `VALIDATION FAILED` banner. (AC: #5)
- [x] Task 6: Run all four gates on Windows; confirm coverage floors. (AC: #7)

### Review Findings

_Adversarial code review (3 layers: Blind Hunter, Edge Case Hunter, Acceptance Auditor) run 2026-06-01 against the uncommitted working-tree diff. All 7 ACs verified satisfied — chain order, exit-code coupling, the three design decisions, and the three e2e tests all match the spec and pass. 1 decision-needed (→ patched) + 1 patch (folded in) + 1 defer + ~14 dismissed. No HIGH issue inside the story's stated scope; the substantive finding was a CLI input-validation gap newly reachable through the wiring._

- [x] [Review][Patch] **(MED)** Out-of-range / non-finite CLI flags bypassed the §Cat 10 exit-2 contract — newly reachable now that 3.11 builds `SolverParams` + constructs the solver: `--iter-budget 0`/negative raised an uncaught `ValueError` from `GraspSolver.__init__`; `--n 0` or `--j-max` outside `[0,1]` raised an uncaught `ValueError` from `TopNTracker`; `--theta nan` / `--min-climb-ground-length nan` silently yielded zero/nonsense climbs and exit 0. `ValueError` is not a `PreExecutionError`, so it escaped `run_entry_point` as a raw traceback (exit 1) instead of `BadCLIArgError → exit 2`. **Fixed:** added `validate_solver_options(...)` (finiteness + range guards for theta/l_connector/min_climb_ground_length/j_max/n/iter_budget, following the `validate_setup_radius` pattern) wired into `cli/query.py` before the cache walk; 15-case unit table + a `--n 0` CliRunner exit-path test in `tests/unit/test_area_parsing.py`. [src/steeproute/cli/_shared.py:validate_solver_options] [src/steeproute/cli/query.py] [source: edge]
- [x] [Review][Patch] **(LOW)** `--output-dir` whose parent is a file, or an unwritable dir → uncaught `OSError` from `render`'s `mkdir` (the common output-dir-is-a-file case was already exit 2 via `click.Path(file_okay=False)`; this was the narrow residual of Story 3.10's deferral). **Fixed:** added `ensure_output_dir(...)` mapping `OSError` → `BadCLIArgError`, called in `cli/query.py` before the solve (also fails fast); 3 unit tests in `tests/unit/test_area_parsing.py`. [src/steeproute/cli/_shared.py:ensure_output_dir] [source: edge+blind]
- [x] [Review][Defer] **Zero routes found exits 0 with an empty output dir and no message.** If the solver returns `[]` (sparse area, no climbs, degenerate graph), `validate([])` + `render(empty)` write nothing, `_exit_code_for` returns 0 — indistinguishable from success. [src/steeproute/cli/query.py:_exit_code_for] — deferred: graceful sparse-area degradation messaging is Epic 4 Story 4.4, run summary is Story 4.5. [source: edge]

## Dev Notes

- **The chain is already proven — copy it.** `tests/integration/test_output_on_fixture.py:93-114` runs the exact `setup → detect_climbs → contract_climbs → GraspSolver.run → validate → render` sequence this story wires into the CLI. Use it as the reference for imports, argument order, and the `SolverParams` shape. The only new work is sourcing the params/provenance from CLI flags + the cache manifest, and the exit-code coupling.

- **⚠️ Click discards the callback's return value.** `cli.main(standalone_mode=True)` (used by `_invoke_command`) always exits `0` on success — **a `return 1` from the callback does NOT produce exit 1** (verified: `standalone_mode=True` → `SystemExit(0)` regardless of return). The existing `return 0` lines are effectively dead for exit-code purposes. To make exit 1 reach the process, **raise it from the callback** via `ctx.exit(exit_code)` (acquire `ctx` with `click.get_current_context()` or a `@click.pass_context` param). `ctx.exit(1)` raises `SystemExit(1)`, which propagates through standalone mode and is caught by `_invoke_command`'s existing `except SystemExit` → returns `1` → `run_entry_point` exits 1. Do **not** switch `_invoke_command` to `standalone_mode=False` — that would change `--help`/`--version`/usage-error handling and break the Story 1.7 smoke tests.

- **`SolverParams` from flags.** All 12 fields map 1:1 to CLI flags (`models.py:124-164`) except resolution of two `None`-defaulted flags:
  - `iter_budget`: CLI default is `None` (`_shared.py:255`), but `SolverParams.iter_budget` is `int` and `GraspSolver` requires `>= 1`. Epic 3's solver only terminates on iter-budget (time-budget/stagnation are Epic 4), so the CLI **must** resolve a concrete value. Pick a module-scope default constant in `cli/query.py` (documented "tunable post-baseline", matching the project's `RCL_SIZE`/`QUALITY_THRESHOLD` convention) — large enough to find routes on a real query, small enough to stay well inside NFR1's budget. The happy-path e2e command passes no `--iter-budget`, so this default must yield routes on the fixture.
  - `stagnation_iters`: CLI default `None`; `SolverParams.stagnation_iters` is `int` (`0` disables, §Cat 5e). Resolve `None → 0` for Epic 3 (no stagnation termination yet).
  - `seed`: pass through verbatim (`SolverParams.seed` is `int | None`); feed the same value to `np.random.default_rng(seed)`. `--seed 42` is what makes AC #4 byte-identical; an unseeded run is allowed but non-reproducible.

- **`ProvenanceInfo` from the cache manifest.** `prepared.manifest` (`cache.py:159`) carries `steeproute_version`, `osm_extract_date`, `dem_version`, `pipeline_content_hash` — copy these four verbatim into `ProvenanceInfo` (the report describes the *cached data* that fed the query, per the `ProvenanceInfo` docstring `models.py:270-284`). For `git_commit_short` + `git_dirty`: split `manifest.steeproute_commit` on the `-dirty` suffix (it was produced by `provenance.get_commit_short()`, which appends it) into `(short, bool)`. A small `_build_provenance(manifest)` helper keeps this off the main flow.

- **`convergence` is a fixed value this epic.** Pass the literal `"budget-exhausted"` — Epic 3's solver runs to iter-budget, which maps to `budget-exhausted` in the §Cat 5e termination table. Story 4.2 replaces this with the full three-value contract (`converged`/`budget-exhausted`/`interrupted`).

- **Write-before-exit-code is load-bearing (§Cat 6c).** `render(...)` must complete for **all** routes before the exit code is computed — failed routes are written *with banners* (FR28), then the process exits 1. Never short-circuit on a failed route.

- **e2e tests are in-process, not real subprocesses.** Follow `tests/e2e/test_coverage_check.py`: seed the cache with the in-process `setup` CLI under `patch("steeproute.pipeline.osm_load", _osm_load_from_fixture)` (a real `uv run` subprocess can't be patched and would hit Overpass — the fixture is offline). Then invoke the query CLI in-process via `CliRunner` (no patch needed — it reads the seeded cache). The validation-failure test `monkeypatch`es `GraspSolver.run` (or the `validate` input) to return a `Solution` with a bogus edge; this works only in-process. Reuse `_load_fixture_constants` + the `_skip_if_fixtures_missing` autouse guard from the existing e2e files.

- **Stream discipline (§Cat 8):** the full run-summary on stdout is Epic 4 Story 4.5 — **not** this story. Keep this story's stdout minimal (the existing cache-hit cue is fine to keep or trim); the e2e tests assert files + exit code, not a summary block.

### Project Structure Notes

- **Implement:** `src/steeproute/cli/query.py` — replace the Story 2.10 stub body (`query.py:127-152`); keep the option decorators, `validate_area_size`, `check_coverage`, and `emit_osm_age_warning` calls.
- **New tests:** `tests/e2e/test_journey_1_happy_path.py`, `tests/e2e/test_seeded_reproducibility.py`, `tests/e2e/test_validation_failure_path.py`.
- **Reuse (do not duplicate):** `pipeline.climbs.detect_climbs`, `pipeline.graph.contract_climbs`, `solver.grasp.GraspSolver`, `validator.validate`, `output.render`, `provenance.get_commit_short` — all already implemented and tested. The CLI is pure glue: no new algorithmic code.
- **Networkx boundary:** `query.py` already carries the `# pyright: reportUnknown* = false` header (`query.py:1-4`) for `prepared.graph` access — keep it.
- **Coverage:** `cli/query.py` is a boundary module under the 80% overall floor, not the 95% pure-logic floor (Architecture §Cat 11e). The three e2e tests exercise both exit-code branches end-to-end.

### Testing standards summary

- e2e tests in `tests/e2e/`; naming `test_<scenario>` per the existing files. No `pytest.skip`/`xfail` for logic (the fixture-missing autouse skip is the one allowed exception, already established).
- Determinism: the reproducibility test depends on `--seed 42` threading cleanly through `np.random.default_rng` to byte-identical edge-sets (the solver already guarantees this — Story 3.6 `test_grasp_reproducible.py`). If JSON differs, suspect a non-seed-derived value leaking into the sidecar (e.g. a render timestamp), not the solver.
- Assert HTML parses with stdlib `html.parser` (no new test dep), as `test_output_on_fixture.py:135` does.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.11"](../planning-artifacts/epics.md) — full-chain wiring, three e2e tests, exit-code coupling, stub progress callback
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 6c (lines 489-498)](../planning-artifacts/architecture.md) — exit-code coupling computed after all writes complete
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 5e (lines 415-428)](../planning-artifacts/architecture.md) — termination → `convergence_status` mapping; iter-budget → `budget-exhausted`
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 8 (lines 536-543)](../planning-artifacts/architecture.md) — stream discipline; run-summary is Epic 4
- [Source: tests/integration/test_output_on_fixture.py:93-148](../../tests/integration/test_output_on_fixture.py) — the exact chain to wire + HTML/JSON assertion patterns to reuse
- [Source: tests/e2e/test_coverage_check.py:69-138](../../tests/e2e/test_coverage_check.py) — in-process cache-seeding + query-invocation pattern for the e2e tests
- [Source: src/steeproute/cli/query.py:79-152](../../src/steeproute/cli/query.py) — current stub to replace; option surface + cache-hit path to keep
- [Source: src/steeproute/output.py:56-83](../../src/steeproute/output.py) — `render(validated_set, base_graph, contracted, params, provenance, convergence, output_dir)` signature
- [Source: src/steeproute/validator.py:69-133](../../src/steeproute/validator.py) — `validate(solutions, graph, params) -> ValidatedRouteSet`
- [Source: src/steeproute/solver/grasp.py:100-138](../../src/steeproute/solver/grasp.py) — `GraspSolver(graph, params, rng, progress_callback=None)` + `run()`
- [Source: src/steeproute/models.py:124-164, 270-292](../../src/steeproute/models.py) — `SolverParams` 12 fields, `ProvenanceInfo` shape
- [Source: src/steeproute/cache.py:159-180, 292-302](../../src/steeproute/cache.py) — `Manifest` (provenance source) + `PreparedData.graph` (base graph)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new dependencies — the story is pure glue over already-shipped components.

**Verification of the click exit-code threading (the story's flagged trap):** confirmed empirically before writing code that `cli.main(standalone_mode=True)` discards the callback's return value and exits `0`, whereas `ctx.exit(1)` raises `SystemExit(1)` that propagates through standalone mode and is mapped to exit `1` by `_invoke_command`'s existing `except SystemExit`. The CLI therefore ends with `click.get_current_context().exit(...)` rather than `return`.

**Final pass (all green, repo-wide):**

```
uv run ruff check .             → All checks passed!
uv run ruff format --check .    → 73 files already formatted
uv run basedpyright             → 0 errors, 0 warnings, 0 notes
uv run pytest                   → 623 passed, 1 deselected in 135 s
                                  (was 620 after 3.10; +3 = the three new e2e tests)
```

### Completion Notes List

**Wiring (AC #1, #2, #6).** `cli/query.py::cli` now continues past the Story 2.10 cache-hit cue into the full Journey-1 chain: `SolverParams` built from the parsed flags → `detect_climbs` → `contract_climbs` → `GraspSolver(...).run()` → `validate(...)` → `output.render(...)` → exit code. `prepared.graph` is the `base_graph` passed to `render`; the solver gets `progress_callback=None` (real progress is Epic 4). The chain mirrors `tests/integration/test_output_on_fixture.py` exactly, as the story directed.

**Design decisions resolved (the three the story left to the dev):**

1. **`iter_budget` default** — added `DEFAULT_ITER_BUDGET = 2000` as a module-scope constant in `cli/query.py`, documented "tunable post-baseline" (matching the `RCL_SIZE` / `QUALITY_THRESHOLD` convention). Epic 3's solver terminates on iter-budget only, so `--iter-budget` being unset (`None`) must resolve to a concrete positive count; 2000 finds routes on the fixture in well under a second. `stagnation_iters` resolves `None → 0` (disabled until Epic 4 Story 4.2).
2. **`ProvenanceInfo` source** — built by a small `_build_provenance(manifest)` helper from `prepared.manifest`: the four data-fingerprint fields (`steeproute_version`, `osm_extract_date`, `dem_version`, `pipeline_content_hash`) copied verbatim; `git_commit_short` + `git_dirty` split out of `manifest.steeproute_commit` (which carries the `-dirty` suffix `get_commit_short()` produced). The report thus describes the *prepared data* it was generated from (§Cat 4b/§Cat 9), not the query-time tree.
3. **`convergence`** — fixed literal `"budget-exhausted"` (`_CONVERGENCE_STATUS`), per the §Cat 5e termination table (iter-budget → budget-exhausted). Story 4.2 replaces this with the live three-value contract.

**Exit code (§Cat 6c).** `_exit_code_for(validated)` returns `1` if any `route.validation.passed is False` OR `validated.set_violations` is non-empty, else `0`. `render(...)` runs to completion for **all** routes (failed ones with a banner — FR28) *before* the exit code is computed, so disk state is identical regardless of outcome. The exit reaches the OS via `ctx.exit(...)` (see Debug Log).

**Tests (AC #3-5).** Three e2e tests, all in-process (a real `uv run` subprocess can't be patched to the offline fixture):
- `test_journey_1_happy_path.py` — exit 0; N `route-<i>.{html,json}` (FR21); each HTML parses and carries the map + elevation-profile sections; seed recorded in the sidecar.
- `test_seeded_reproducibility.py` — two `--seed 42` runs produce byte-identical JSON sidecars (FR29/NFR4).
- `test_validation_failure_path.py` — `monkeypatch`es `GraspSolver.run` to emit a route with an edge absent from the graph → `graph_membership` failure → exit 1, report still written, `VALIDATION FAILED` banner present (FR27/FR28).

**Shared test infrastructure.** Rather than triplicate the ~40-line fixture-loading + cache-seeding boilerplate the older Story 2.x e2e files each copy, the Journey-1 plumbing (`seeded_cache` fixture, `run_query` helper fixture, fixture constants) lives once in `tests/e2e/conftest.py`. The seed radius is the fixture's full bbox half-side; queries run at `−0.5 km` so the FR24 strict-containment coverage check passes.

**Out of scope (deferred, per the story):** real progress UI + Ctrl-C/exit-130 (Epic 4 Stories 4.1/4.3), the stdout run-summary block (Epic 4 Story 4.5), and query-area clipping of the prepared graph — the solver runs on the full prepared graph, matching the `test_output_on_fixture.py` precedent; no clipping function exists in the chain and none is in this story's scope.

### File List

**Modified:**
- `src/steeproute/cli/query.py` — replaced the Story 2.10 stub body with the full chain; added `DEFAULT_ITER_BUDGET`, `_CONVERGENCE_STATUS`, `_build_provenance`, `_exit_code_for`; new imports (`numpy`, `output`, `Manifest`, `SolverParams`/`ProvenanceInfo`/`ValidatedRouteSet`, `detect_climbs`, `contract_climbs`, `GraspSolver`, `validate`). **(review)** wired in `validate_solver_options` + `ensure_output_dir` before the cache walk.
- `tests/e2e/conftest.py` — added the shared Journey-1 `seeded_cache` + `run_query` fixtures and fixture constants.
- **(review)** `src/steeproute/cli/_shared.py` — added `validate_solver_options` + `ensure_output_dir` (§Cat 10 CLI-boundary guards).
- **(review)** `tests/unit/test_area_parsing.py` — added `validate_solver_options` (15-case table + boundary/accept) and `ensure_output_dir` (3) unit tests, plus a `--n 0` query-CLI exit-path test.

**New:**
- `tests/e2e/test_journey_1_happy_path.py` — AC #3.
- `tests/e2e/test_seeded_reproducibility.py` — AC #4.
- `tests/e2e/test_validation_failure_path.py` — AC #5.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-01 | Yann (Claude Opus 4.8) | Code review (3 adversarial layers: blind hunter, edge-case hunter, acceptance auditor) — all 7 ACs verified satisfied; 1 decision-needed + 1 patch + 1 defer + ~14 dismissed. **2 patches applied (user chose fix-now):** **(MED)** added `validate_solver_options` (CLI-boundary finiteness/range guards for `--theta`/`--l-connector`/`--min-climb-ground-length`/`--j-max`/`--n`/`--iter-budget`) so out-of-range input surfaces as `BadCLIArgError → exit 2` instead of a raw `ValueError` traceback (§Cat 10) — newly reachable via 3.11's wiring; **(LOW)** added `ensure_output_dir` mapping `OSError` → exit 2 (the narrow residual of Story 3.10's output-dir deferral; common case already handled by `click.Path(file_okay=False)`). +21 unit tests in `test_area_parsing.py`. 1 defer (zero-routes-found messaging → Epic 4 Story 4.4/4.5) logged to `deferred-work.md`. All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 644 passed (was 623; +21). Status → done. | _pending_ |
| 2026-06-01 | Yann (Claude Opus 4.8) | Story 3.11 implemented: wired `cli/query.py` end-to-end (cache-hit → stages 8-9 → GRASP → validate → render) with the validation-driven exit code (§Cat 6c / FR28/FR30 codes 0+1). Outputs always written before exit-code computation; exit threaded via `ctx.exit(...)` (click discards callback returns). Resolved the three open decisions: `DEFAULT_ITER_BUDGET=2000`, `ProvenanceInfo` from the cache manifest, fixed `convergence="budget-exhausted"`. **New:** three Journey-1 e2e tests (happy path, seeded reproducibility, validation-failure path) + shared `seeded_cache`/`run_query` fixtures in `tests/e2e/conftest.py`. All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 623 passed (was 620; +3). No new dependencies. Status → review. | _pending_ |
