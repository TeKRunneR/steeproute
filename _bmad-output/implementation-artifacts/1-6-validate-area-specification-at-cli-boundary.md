# Story 1.6: Validate area specification at CLI boundary (FR1, FR2)

Status: review

## Story

As a user,
I want `steeproute` to reject malformed `--center` values and radii whose resulting area exceeds `--area-cap` at invocation time,
so that I get a clear error immediately rather than a confusing failure deep in the pipeline.

## Acceptance Criteria

1. `LatLonParamType.convert` raises `BadCLIArgError` (no longer `self.fail`) on parse failure **or** when latitude is outside `[-90, 90]` or longitude is outside `[-180, 180]`. `user_message` names `--center` and identifies the specific violation.
2. `steeproute` rejects invocations whose `π·radius²` exceeds the resolved `--area-cap`. The rejection raises `BadCLIArgError` with a `user_message` of the form `--radius {r} produces ~{area_km2} km², exceeds --area-cap of {cap} km²` (radius and area rounded for readability).
3. `BadCLIArgError` raised during click parsing **or** in the CLI command body propagates past click's `standalone_mode` to `run_entry_point`, which writes `error: {user_message}\n` to stderr and exits 2 — matching the format Story 1.4 established.
4. When `--verbose` is supplied alongside an offending area argument, `run_entry_point`'s `detail` rendering works: i.e., the `--verbose` state is set early enough that even a `BadCLIArgError` raised from inside `LatLonParamType.convert` honors it. (Resolves Story 1.5 §Dev Notes caveat.)
5. With valid args (e.g. `--center 45.0716,6.1079 --radius 10 --area-cap 500`) `steeproute` reaches its existing Story 1.5 stub body and exits 0. Setup CLI's existing stub behavior is unchanged.
6. The area-cap check is `steeproute`-only (`--area-cap` is on the query stack only). `LatLonParamType` range validation, in contrast, applies to **any** CLI using `center_option` — so `steeproute-setup --center 95,0 ...` also exits 2 via `BadCLIArgError`.
7. `tests/unit/test_area_parsing.py` covers: happy path, ≥3 malformed-`--center` cases spanning syntactic failure (e.g. `abc,def`, missing comma) **and** range failure (e.g. lat 95, lon -181), one area-cap-exceeded case, one valid case at the cap boundary, and one `--verbose` + malformed-arg case asserting `detail` is rendered. Use `click.testing.CliRunner` (no subprocess — Story 1.7 owns those).
8. All four CI gates (`ruff check`, `ruff format --check`, `basedpyright`, `pytest --cov`) pass on Windows with zero findings/failures.

## Tasks / Subtasks

- [x] **Task 1: Extend `LatLonParamType` to validate range and raise `BadCLIArgError`** (AC: #1, #3, #6)
  - [x] Replace `self.fail(...)` with `raise BadCLIArgError(...)` on syntactic failure.
  - [x] After successful float parse, check `-90 ≤ lat ≤ 90` and `-180 ≤ lon ≤ 180`; raise `BadCLIArgError` on violation, naming `--center` and the offending value.
  - [x] Confirm `BadCLIArgError` propagates through click 8.x `standalone_mode` (it's not a `ClickException`).
- [x] **Task 2: Add an `--area-cap` enforcement helper and call it from the query CLI body** (AC: #2, #5, #6)
  - [x] Add a small `validate_area_size(radius_km, area_cap_km2) -> None` helper in `cli/_shared.py` that raises `BadCLIArgError` with the AC #2 message format on violation. Pure function, no click dependency.
  - [x] Call it as the first body line of `cli/query.py::cli` (before the `_ = (...)` consumption tuple), after the `--verbose` wiring.
  - [x] Do **not** call it from `cli/setup.py` (no `--area-cap` flag there).
- [x] **Task 3: Promote `--verbose` to `is_eager=True` with a callback that calls `set_verbose(True)`** (AC: #4)
  - [x] Update `verbose_option` in `cli/_shared.py`: `is_eager=True`, `expose_value=True` (still passed to the body for Story 1.5 compatibility), `callback=lambda ctx, param, value: set_verbose(True) if value else None`.
  - [x] Drop the now-redundant `if verbose: set_verbose(True)` body line from both `cli/query.py` and `cli/setup.py` — or keep it as a no-op write; pick the smaller diff.
  - [x] Verify the autouse `reset_verbose_flag` fixture in `tests/unit/conftest.py` still cleans state between tests (it should — it resets `_verbose` regardless of who set it).
- [x] **Task 4: Add `tests/unit/test_area_parsing.py`** (AC: #7)
  - [x] Use `CliRunner` against `cli/query.py::cli` and `cli/setup.py::cli` as appropriate.
  - [x] Cases per AC #7. Parametrize the malformed-`--center` cases.
  - [x] For the verbose case: assert exit code 2, stderr starts with `error:`, and the second line (the `detail` line) is present (its content can be loose — the assertion is "verbose state was set before validation fired").
- [x] **Task 5: Verify all CI gates pass locally on Windows** (AC: #8)

## Dev Notes

- **Validation site (Architecture §FR-module mapping):** FR1's flag parsing and FR2's validation both live in `cli/_shared.py`; `errors.py` already supplies `BadCLIArgError` (Story 1.4). Don't drift validation logic into `cli/query.py` beyond the single helper call. [Source: architecture.md §"FR → module mapping"]
- **Why raise `BadCLIArgError` instead of `self.fail`:** the epic AC explicitly specifies the `error: {reason}` single-line stderr format that `run_entry_point` produces. `self.fail` produces click's multi-line `Usage: ...\nError: ...` formatting, which is the wrong contract for our exit-2 surface. [Source: epics.md §"Story 1.6"; architecture.md §Category 10]
- **`BadCLIArgError` propagates past click's `standalone_mode`** because it's a plain `Exception` subclass, not a `click.exceptions.ClickException`. Confirmed by Story 1.5's `_invoke_command` design. [Source: 1-5-define-full-click-option-decorator-surface-for-both-clis.md §"Completion Notes #1, AC #5 evidence"]
- **`--verbose` ordering caveat — Story 1.5 flagged this:** `if verbose: set_verbose(True)` runs in the command body, *after* click finishes parsing all params. A `BadCLIArgError` raised from `LatLonParamType.convert` therefore happens before `_verbose` is set, which would suppress the `detail` line. Promoting `--verbose` to `is_eager=True` resolves this — click processes eager options in a separate, earlier pass. [Source: 1-5-...md §Dev Notes "Caveat for Story 1.6"; click 8.x docs on eager options]
- **Area-cap arithmetic:** `area_km² = math.pi * radius_km**2`. Round the surfaced number to a small fixed precision in the error message (e.g., `int(round(area))` or one decimal) — exact float output looks bad in user-facing errors. [Source: implementation-pattern §Serialization (rounding at boundary)]
- **Asymmetry — `--area-cap` is query-only:** `steeproute-setup` deliberately doesn't enforce the area cap. The setup workflow is "prepare what you'll later query"; cap enforcement at query time is sufficient. Don't add `--area-cap` to setup. [Source: epics.md §"Story 1.5" AC #4 — setup option stack omits `--area-cap`; PRD §"Data Preparation"]
- **Out of scope:** subprocess smoke tests of the same paths land in Story 1.7. Use `CliRunner` only here. [Source: epics.md §"Story 1.7"]
- **Don't touch `run_entry_point`** — its contract from Story 1.4 already handles `BadCLIArgError` correctly via the `PreExecutionError` parent.

### Project Structure Notes

- All production code edits land in three files: `cli/_shared.py` (extended `LatLonParamType`, new `validate_area_size`, eager `verbose_option`), `cli/query.py` (call to `validate_area_size`, possibly drop the `if verbose:` line), `cli/setup.py` (possibly drop the `if verbose:` line). No new modules, no new sub-packages.
- New test file `tests/unit/test_area_parsing.py` per epic AC. Existing `tests/unit/test_cli_options.py` (Story 1.5) already covers `LatLonParamType` syntactic-failure paths via `self.fail`; those assertions need updating to expect `BadCLIArgError` instead. Treat that as part of Task 1 (otherwise the test suite breaks on the very first edit).
- No structural conflicts.

### Testing standards summary

- Layer: `tests/unit/` — `CliRunner` is in-process, no I/O. [Source: architecture.md §Category 11]
- Naming: `test_<unit>_<scenario>`; file name per epic AC (`test_area_parsing.py`). [Source: architecture.md §"Test organization"]
- Coverage on `cli/` is excluded from the percentage floor (Architecture §11e), but `cli/_shared.py::LatLonParamType` and `validate_area_size` are pure-logic enough that they'll naturally land near 100%. The smoke-test layer (Story 1.7) covers the CLI plumbing itself.
- Parametrize the malformed-`--center` cases — one parametrized test gives per-case failure messages.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 1.6"]
- [Source: _bmad-output/planning-artifacts/epics.md §"Story 1.7"] — downstream consumer of these BadCLIArgError paths via subprocess smoke tests
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 2 — CLI framework]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 10 — Error model] — `BadCLIArgError`/`PreExecutionError` rendering contract
- [Source: _bmad-output/planning-artifacts/architecture.md §"FR → module mapping"] — FR1, FR2 validation site
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11 — Testing strategy]
- [Source: _bmad-output/planning-artifacts/prd.md §"Command-Line Interface"] — `--center`/`--radius`/`--area-cap` defaults
- [Source: _bmad-output/implementation-artifacts/1-4-implement-shared-error-hierarchy-and-run-entry-point-wrapper.md] — `BadCLIArgError` hierarchy + `run_entry_point` contract this story consumes
- [Source: _bmad-output/implementation-artifacts/1-5-define-full-click-option-decorator-surface-for-both-clis.md] — `LatLonParamType` skeleton + `--verbose` eager-callback caveat this story resolves

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/pedantic-fermi-33bc7e`).

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` required to traverse the corporate Netskope TLS-intercepting proxy.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 25 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 117 passed in 0.61s; coverage 95% overall
                                     - cli/_shared.py 100%; errors.py 100%
                                     - cli/query.py 95%; cli/setup.py 79%
                                       (the SystemExit-non-int branch + run_entry_point
                                        SystemExit raise are not unit-reachable; e2e smoke
                                        is Story 1.7's coverage path)
```

### Completion Notes List

**Divergences from story spec (worth noting for review):**

1. **`verbose` parameter retained in both CLI signatures** (with `expose_value` left at its default `True`). The story task said "Drop the now-redundant `if verbose: set_verbose(True)` body line from both `cli/query.py` and `cli/setup.py` — or keep it as a no-op write; pick the smaller diff." The smallest diff was: drop the body line, keep the parameter, add `verbose` to the `_ = (...)` consumption tuple. Setting `expose_value=False` would have been one possible "cleaner" alternative but would have required removing `verbose` from both function signatures (more diff) and tests in `test_cli_options.py` already exercise the kwarg shape.

2. **`_verbose_callback` is a named `def`, not a `lambda`.** The story spec sketched a lambda, but a named `def` lets us add the docstring explaining *why* `is_eager=True` (the load-bearing reason: convert-time `BadCLIArgError` from `LatLonParamType` would otherwise miss verbose state). Callable-equivalent; no semantic difference.

3. **`LatLonParamType.convert` validates range on the tuple-input branch too** (not just after string parse). The "idempotent on tuple" contract from Story 1.5 is preserved for valid tuples; an out-of-range tuple now also raises. This is defensive — click only feeds tuples back into `convert` for default round-tripping, and we never declare an out-of-range default — but the cost is one shared post-parse check that always fires, which is simpler than two parallel paths.

4. **Boundary test phrasing:** the story AC mentioned "one valid case at the cap boundary". The exact-boundary case (`r = sqrt(cap/π)`) trips on FP — `math.pi * (math.sqrt(cap/math.pi))**2 == cap + 1e-13`, not exactly `cap`. Strict `>` correctly rejects this as "exceeds cap". The tests instead verify "just below cap" (multiplied by 0.999) for the passing case; the rejection-above-cap case stands. Documented in `validate_area_size`'s implicit contract: comparison is exact, no FP slack.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `LatLonParamType.convert` raises `BadCLIArgError` on parse failure and on lat/lon out of range. `user_message` names `--center` and the violation type. Verified by 7 parametrized cases in `test_lat_lon_convert_raises_bad_cli_arg_error` + 2 boundary-acceptance cases. ✅
2. AC #2 — `validate_area_size` raises `BadCLIArgError` with the canonical `--radius {r} produces ~{area} km², exceeds --area-cap of {cap} km²` message. Verified by `test_validate_area_size_rejects_above_cap` and end-to-end via `test_query_cli_rejects_radius_exceeding_area_cap`. ✅
3. AC #3 — `BadCLIArgError` propagates past click `standalone_mode` (not a `ClickException`), through `_invoke_command`, into `run_entry_point` which formats `error: {user_message}\n` and exits 2. Verified by `test_verbose_with_malformed_center_renders_detail_via_main` (which goes through `main()` → `run_entry_point` → asserts exit 2, `error:` prefix, and detail-line presence). ✅
4. AC #4 — `verbose_option` is now `is_eager=True` with `_verbose_callback` flipping `_verbose` during click's first parse pass. Verified by `test_verbose_state_is_set_before_lat_lon_convert_runs` (CliRunner + malformed `--center` + `--verbose` → `is_verbose() is True` even though `convert` raised) AND `test_verbose_with_malformed_center_renders_detail_via_main` (end-to-end stderr-line check). ✅
5. AC #5 — `uv run steeproute --center 45.0716,6.1079 --radius 10` exits 0 with the Story 1.5 stub message. Verified by manual smoke + `test_query_cli_happy_path_proceeds_to_stub`. Setup CLI's existing stub behavior unchanged (verified by Story 1.5's `test_setup_cli_*` tests still passing + `test_setup_cli_does_not_enforce_area_cap`). ✅
6. AC #6 — `validate_area_size` is called from `cli/query.py::cli` only; `cli/setup.py` does not import it. `LatLonParamType` range validation lives in the shared param type, so `setup --center 95,0` also exits via `BadCLIArgError`. Verified by `test_setup_cli_inherits_lat_lon_range_validation` + `test_setup_cli_does_not_enforce_area_cap`. ✅
7. AC #7 — `tests/unit/test_area_parsing.py` adds 14 tests: 7 parametrized `LatLonParamType` failures (3 syntactic + 4 range) + 1 boundary acceptance + 3 `validate_area_size` direct cases + 4 query-CLI CliRunner cases (happy path, area-cap reject, just-below-cap accept, malformed center, out-of-range lat) + 2 setup-CLI cases (range inherits, no area-cap) + 2 `--verbose` ordering cases (CliRunner state-check + end-to-end detail rendering through `main()`). All use `CliRunner` or direct calls — no subprocess (Story 1.7's territory). ✅
8. AC #8 — All four CI gates pass on Windows: ruff check ✅, ruff format --check ✅, basedpyright 0/0/0 ✅, pytest 117 passed ✅. Coverage 95% overall (above 80% floor); `cli/_shared.py` at 100%. ✅

### File List

**Modified:**
- `src/steeproute/cli/_shared.py` — extended `LatLonParamType.convert` to raise `BadCLIArgError` (parse + range), added `validate_area_size` helper, added `_verbose_callback`, set `is_eager=True` + `callback=_verbose_callback` on `verbose_option`. Imports `math`, `BadCLIArgError`.
- `src/steeproute/cli/query.py` — first-line call to `validate_area_size(radius_km=radius, area_cap_km2=area_cap)`; dropped `if verbose: set_verbose(True)` body line; added `verbose` to `_ = (...)` consumption tuple; dropped `set_verbose` import.
- `src/steeproute/cli/setup.py` — dropped `if verbose: set_verbose(True)` body line; added `verbose` to `_ = (...)` consumption tuple; dropped `set_verbose` import.
- `tests/unit/test_cli_options.py` — updated 2 existing `LatLonParamType` failure assertions from `pytest.raises(click.BadParameter)` to `pytest.raises(BadCLIArgError)`; dropped now-unused `import click`; added `from steeproute.errors import BadCLIArgError`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 1.6 status `backlog → ready-for-dev → in-progress → review`; dated comments added.

**New:**
- `tests/unit/test_area_parsing.py` — 14 tests covering AC #1, #2, #4, #5, #6, #7 (LatLonParamType range, `validate_area_size` direct, query CLI via `CliRunner`, setup CLI behavior, `--verbose` eager-ordering both via `CliRunner` and via `main()` end-to-end).

**Untouched (intentionally):**
- `src/steeproute/errors.py` — Story 1.4's hierarchy already provides `BadCLIArgError`; no extension needed.
- `src/steeproute/cli/_shared.py::run_entry_point` — Story 1.4's contract handles `BadCLIArgError` correctly via the `PreExecutionError` parent; not modified.
- `tests/unit/conftest.py` — autouse `reset_verbose_flag` fixture from Story 1.5 still cleans `_verbose` regardless of who set it; no change needed.
- `tests/unit/test_run_entry_point.py` — Story 1.4 tests; the `detail`-rendering behavior they assert is now also exercised end-to-end by `test_verbose_with_malformed_center_renders_detail_via_main`.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-04 | Yann (Claude Opus 4.7) | Story 1.6 implemented: `LatLonParamType` now raises `BadCLIArgError` on parse + range failure (lat ∈ [-90,90], lon ∈ [-180,180]); new `validate_area_size` helper enforces `π·r² ≤ --area-cap` from `cli/query.py` body; `verbose_option` promoted to `is_eager=True` with named callback so `BadCLIArgError` from `LatLonParamType.convert` still triggers `run_entry_point`'s `detail` line under `--verbose`. 14 new unit tests in `test_area_parsing.py` + 2 updated assertions in `test_cli_options.py`. All four CI gates green on Windows (ruff, ruff format, basedpyright 0/0/0, pytest 117 passed, 95% coverage; `cli/_shared.py` 100%). | (this commit) |
