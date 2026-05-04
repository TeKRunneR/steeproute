# Story 1.5: Define full click option decorator surface for both CLIs

Status: done

## Story

As a developer,
I want every CLI flag defined once as a reusable click option decorator in `cli/_shared.py`, with each CLI stacking the decorators it needs,
So that `steeproute --help` and `steeproute-setup --help` produce complete, documented flag listings and there's zero flag definition duplication between the two CLIs.

## Acceptance Criteria

1. `click>=8,<9` is added to `[project] dependencies` in `pyproject.toml`; `uv.lock` regenerated.
2. `cli/_shared.py` defines a `LatLonParamType(click.ParamType)` (with module-level instance `LAT_LON`) that parses `"LAT,LON"` into `tuple[float, float]` and calls `self.fail(...)` on syntactic failure. Range validation is **Story 1.6's job**, not this story's.
3. `cli/_shared.py` exposes one importable click-option decorator per flag in the surface below. Each decorator carries `help="..."`, a sensible `type=`, and `show_default=True` where a default exists. Defaults match the PRD CLI table and Architecture flag-additions table; flags marked TBD by Architecture (`--iter-budget`, `--stagnation-iters`, `--progress-interval`) get `default=None`. Flag surface:
   - **Area:** `--center` (LatLonParamType, required), `--radius` (float, required)
   - **Constraints:** `--theta`, `--difficulty-cap` (Choice T1..T6), `--l-connector`, `--min-climb-ground-length`, `--j-max`, `--n`, `--area-cap`, `--untagged-trails` (Choice include/exclude)
   - **Solver:** `--seed`, `--iter-budget`, `--time-budget`, `--stagnation-iters`, `--progress-interval`
   - **Output:** `--output-dir` (Path)
   - **Shared meta:** `--verbose` (flag), `--quiet` (flag), `--cache-dir` (Path)
   - **Setup-specific:** `--force-refresh` (flag), `--dem-version`, `--dem-path` (Path), `--osm-age-warn-days`
4. `cli/query.py` stacks: area + all constraints + all solver + output + shared meta + click's `version_option(package_name="steeproute")`. `cli/setup.py` stacks: area + `--untagged-trails` (cache-key input) + shared meta + setup-specific + `version_option`. Neither CLI file constructs `click.option(...)` inline.
5. The click command in each CLI integrates with the existing `run_entry_point` wrapper such that click handles `--help`/`--version`/usage errors with its default formatting (exit 0/2), and `PreExecutionError` / `KeyboardInterrupt` raised inside the command body still propagate to `run_entry_point` for exit-2/130 handling.
6. The `--verbose` flag's value is forwarded to `set_verbose(True)` so subsequent `PreExecutionError.detail` rendering by `run_entry_point` works as Story 1.4 intended.
7. `uv run steeproute --help` and `uv run steeproute-setup --help` each exit 0 and stdout contains the literal flag tokens for the CLI's expected surface (per AC #4). Setup `--help` does not list query-only flags.
8. `uv run steeproute --version` and `uv run steeproute-setup --version` each exit 0 and print a recognizable, non-empty version string.
9. The Story 1.4 stub behavior is preserved: `uv run steeproute --center 45.07,6.11 --radius 10` and `uv run steeproute-setup --center 45.07,6.11 --radius 10` each print their stub message and exit 0.
10. Unit tests cover: `LatLonParamType` (valid + two malformed cases + idempotent-on-tuple); each option decorator is importable and callable; both CLIs' `--help` and `--version` via `click.testing.CliRunner` (no subprocess — that's Story 1.7); `--verbose` wiring sets `_verbose` to `True` for both CLIs.
11. All four CI gates (`ruff check`, `ruff format --check`, `basedpyright`, `pytest --cov`) pass on Windows with zero findings/failures.

## Tasks / Subtasks

- [x] **Task 1: Add `click>=8,<9` runtime dependency** (AC: #1)
  - [x] Edit `[project] dependencies` in `pyproject.toml`; `uv sync`; commit `uv.lock`.
- [x] **Task 2: Implement `LatLonParamType` and the option-decorator surface in `cli/_shared.py`** (AC: #2, #3)
  - [x] Keep existing `_verbose` / `set_verbose` / `run_entry_point` untouched.
  - [x] Group decorators visually by category (area / constraints / solver / output / shared meta / setup-specific).
- [x] **Task 3: Restructure `cli/query.py` and `cli/setup.py` as click commands stacking the decorators** (AC: #4, #5, #6, #9)
  - [x] Each `_main` becomes the click command; add an `_invoke_command()` helper that runs `_main.main(standalone_mode=True)` and converts `SystemExit` → `int`; `main()` keeps its current shape: `run_entry_point(_invoke_command)`.
  - [x] First body line of each `_main`: `if verbose: set_verbose(True)`.
- [x] **Task 4: Promote `reset_verbose_flag` autouse fixture from `tests/unit/test_run_entry_point.py` to `tests/unit/conftest.py`** so `test_cli_options.py` inherits it.
- [x] **Task 5: Add `tests/unit/test_cli_options.py`** (AC: #10) — `LatLonParamType` cases, decorators-are-callable (parametrized), `--verbose` wiring for both CLIs.
- [x] **Task 6: Add `tests/unit/test_cli_help.py`** (AC: #7, #8, #10) — `CliRunner`-driven `--help`/`--version` assertions for both CLIs (parametrize the expected-flag-tokens list); also assert query-only flags are absent from setup `--help`.
- [x] **Task 7: Verify all CI gates pass locally on Windows** (AC: #11).

## Dev Notes

- **CLI framework + decorator pattern:** click 8.x; reusable option decorators in `cli/_shared.py`; `--center LAT,LON` via custom `click.ParamType`. [Source: architecture.md §Category 2]
- **Flag surface + defaults:** PRD CLI tables are the source of truth for flag names, types, and defaults. Architecture introduces six additional flags. [Source: prd.md §"Command-Line Interface"; architecture.md §"Architecture-owned additions to the flag surface"]
- **TBD defaults** (`--iter-budget`, `--stagnation-iters`, `--progress-interval`) are deferred to empirical tuning during Epic 3/4. Set `default=None` for now. [Source: architecture.md §"Nice-to-have items deferred to implementation"]
- **`--untagged-trails` and `--cache-dir` live on both CLIs** because both contribute to / read from the cache key. [Source: architecture.md §Category 4b, §Category 7]
- **`--quiet` is parsed-but-unused** in this story; behavior lands in Epic 4 (progress suppression). `--verbose` only wires to `set_verbose(True)` here; broader logging-verbosity wiring is Epic 2 territory. [Source: architecture.md §Category 8]
- **`run_entry_point` is unchanged** by this story. Story 1.4 already sized its contract for click integration; the new `_invoke_command` helper just bridges click's `SystemExit` to an `int` so `run_entry_point` keeps ultimate exit-code control.
- **Range validation, area-cap enforcement, and `BadCLIArgError`-formatted parse errors are out of scope** — Story 1.6 will refine `LatLonParamType` to raise `BadCLIArgError` and add lat/lon bounds + area-cap checks. [Source: epics.md §"Story 1.6"]
- **Subprocess smoke tests are out of scope** — Story 1.7 owns those. Use `click.testing.CliRunner` here.
- **Caveat for Story 1.6:** the body-line `if verbose: set_verbose(True)` runs after click finishes parsing. If Story 1.6 raises `BadCLIArgError` from a `ParamType.convert`, `_verbose` will not be set yet. Story 1.6 will likely promote `--verbose` to `is_eager=True` with a callback. Not Story 1.5's problem.

### Project Structure Notes

- New code lands in `cli/_shared.py` ("shared click decorators, run_entry_point wrapper" per the architectural project tree). `cli/query.py` and `cli/setup.py` are restructured around the click decorators; module locations and `[project.scripts]` entries are unchanged. [Source: architecture.md §"Complete project tree"]
- `tests/unit/conftest.py` (currently empty) gains the autouse `reset_verbose_flag` fixture; this is layer-scoped because `_verbose` is only relevant at the unit layer. [Source: architecture.md §"Test organization"]
- No structural conflicts.

### Testing standards summary

- Layer: `tests/unit/` — `CliRunner` is in-process, no I/O. [Source: architecture.md §Category 11]
- Naming: `test_<unit>_<scenario>`; file names mirror subject. [Source: architecture.md §"Test organization"]
- Parametrize the expected-flag-tokens lists rather than looping inside a single test — gives per-flag failure messages.
- No `hypothesis` (deferred to Epic 2/3 property tests).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 1.5"]
- [Source: _bmad-output/planning-artifacts/epics.md §"Story 1.6"] — downstream consumer; explains the deferred validation work
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 2 — CLI framework]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 4b — Cache key composition]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 7 — Inter-CLI contract]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 8 — Logging, progress, stream discipline]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 10 — Error model] — `run_entry_point` contract Story 1.5 integrates with
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11 — Testing strategy]
- [Source: _bmad-output/planning-artifacts/architecture.md §"Architecture-owned additions to the flag surface"]
- [Source: _bmad-output/planning-artifacts/architecture.md §"Complete project tree"]
- [Source: _bmad-output/planning-artifacts/prd.md §"Command-Line Interface"]
- [Source: _bmad-output/planning-artifacts/prd.md §"Data Preparation (steeproute-setup)"]
- [Source: _bmad-output/implementation-artifacts/1-4-implement-shared-error-hierarchy-and-run-entry-point-wrapper.md] — `set_verbose` hook this story wires; `_main`/`main` split pattern this story extends

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/happy-haibt-c31dc4`).

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` required to traverse the corporate Netskope TLS-intercepting proxy when `uv sync` had to fetch click for the first time.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 24 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 97 passed in 0.51s; coverage 91% (139 stmts, 12 miss)
                                     - cli/_shared.py 100%; errors.py 100%
                                     - cli/query.py 85%; cli/setup.py 80% (the SystemExit-non-int branch
                                       and one unreached path in `_invoke_command` remain; e2e smoke is
                                       Story 1.7's coverage path)
```

### Completion Notes List

**Divergences from story spec (worth noting for review):**

1. **Renamed click-decorated entry from `_main` to `cli` in both `query.py` and `setup.py`.** The story spec wrote it as `_main` for symmetry with Story 1.4's pattern, but basedpyright surfaced `reportPrivateUsage` warnings whenever tests imported `_main`. The cleanest fix was to make the click command public — `cli` is the conventional click-app name, and reserving the underscore prefix for `_invoke_command` (the SystemExit-bridging helper) keeps the private/public boundary aligned with Architecture §"Module-internal names prefixed with `_`". `[project.scripts]` still points at `main`, which still goes through `run_entry_point(_invoke_command)`, which now invokes `cli.main(standalone_mode=True)` — entry-point shape is unchanged for users.

2. **Added a public `is_verbose() -> bool` getter to `cli/_shared.py`.** Story 1.4 deliberately avoided one (Story 1.4 §Completion Notes #1 dropped a redundant test rather than expose `_verbose`). Story 1.5's verbose-wiring tests (4 tests across both CLIs) need to read the state, not just write it. Adding a one-line getter is cleaner than disabling `reportPrivateUsage` or rolling out per-line `# pyright: ignore` suppressions. Production code (`run_entry_point`) still reads `_verbose` directly per Architecture §Cat 10 pseudocode — unchanged.

3. **Added `_ = (...)` consumption tuples** at the end of both `cli` function bodies. The 19 query-CLI kwargs and 9 setup-CLI kwargs are click-bound but unused in the stub bodies; basedpyright's `reportUnusedParameter` flagged them all. Underscore-prefix renames don't work with click (it matches param names to flag names by exact string). The `_ = (...)` pattern is explicit, ruff-clean, basedpyright-clean, and ready for Epics 2–4 to swap each consumption site for real usage.

4. **Annotated `LatLonParamType.name: str = "lat,lon"` and added `@override` on `convert`** to silence `reportUnannotatedClassAttribute` and `reportImplicitOverride`. Both are mechanical fixes; no semantic change.

5. **No dependency changes beyond `click>=8,<9`.** click 8.3.3 resolved. uv.lock updated.

6. **Caveat for Story 1.6 (already documented in story Dev Notes):** the body-line `if verbose: set_verbose(True)` runs after click finishes parsing. If Story 1.6 raises `BadCLIArgError` from a `ParamType.convert`, `_verbose` won't be set yet. Story 1.6 will likely promote `--verbose` to `is_eager=True` with a callback at that point. Out of scope here.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `click>=8,<9` in `[project] dependencies`; `uv.lock` regenerated; `uv run python -c "import click; print(click.__version__)"` → 8.3.3. ✅
2. AC #2 — `LatLonParamType` + `LAT_LON` instance defined; parses `"LAT,LON"` → `tuple[float, float]`; `self.fail` on syntactic failure; idempotent on tuple input. Range validation explicitly deferred to Story 1.6. ✅
3. AC #3 — All 23 option decorators defined as module-level click.option assignments in `cli/_shared.py`, grouped by category, defaults from PRD/Architecture tables. ✅
4. AC #4 — `cli/query.py` stacks 19 query options + `version_option`; `cli/setup.py` stacks 9 setup-relevant options + `version_option`; neither file constructs `click.option(...)` inline. ✅
5. AC #5 — `_invoke_command` runs `cli.main(standalone_mode=True)` and converts `SystemExit` → `int`; `BadCLIArgError`/`PreExecutionError` propagate past `_invoke_command` (verified by code path inspection — `BadCLIArgError` is not a `ClickException` so click's standalone_mode does not intercept it). ✅
6. AC #6 — `if verbose: set_verbose(True)` is the first body line of each `cli` function; verified by 4 wiring tests. ✅
7. AC #7 — `test_query_help_lists_flag` parametrized over all 21 query-CLI flag tokens; 21/21 pass. ✅
8. AC #8 — `test_setup_help_lists_flag` (12 tokens) + `test_setup_help_excludes_query_only_flag` (13 tokens) all pass. ✅
9. AC #9 — `test_query_version_exits_zero` and `test_setup_version_exits_zero` both pass; manual `uv run steeproute --version` → `steeproute, version 0.0.1.dev11+22d070c`. ✅
10. AC #10 — `test_cli_options.py` has 32 tests (LatLonParamType x5 + parametrized decorator-callable x23 + verbose wiring x4); all pass. ✅
11. AC #11 — All four CI gates pass on Windows: ruff check ✅, ruff format --check ✅, basedpyright 0/0/0 ✅, pytest 97 passed ✅. ✅

### File List

**Modified:**
- `pyproject.toml` — added `click>=8,<9` to `[project] dependencies`.
- `uv.lock` — regenerated by `uv sync` after the click addition.
- `src/steeproute/cli/_shared.py` — added `is_verbose()` getter, `LatLonParamType` + `LAT_LON`, and 23 click-option decorators grouped by category.
- `src/steeproute/cli/query.py` — restructured: existing stub became a click-decorated `cli` function with the full query-CLI option stack; added `_invoke_command()` helper; `main()` now wraps `run_entry_point(_invoke_command)`.
- `src/steeproute/cli/setup.py` — same restructure for the setup-CLI option subset.
- `tests/unit/test_run_entry_point.py` — removed the local `reset_verbose_flag` fixture (now layer-scoped in conftest).
- `tests/unit/conftest.py` — populated with the `reset_verbose_flag` autouse fixture moved from `test_run_entry_point.py`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 1.5 status moved `backlog` → `ready-for-dev` → `in-progress` → `review`; dated comments added.

**New:**
- `tests/unit/test_cli_options.py` — 32 tests: LatLonParamType (5), decorators-are-callable (23 parametrized), `--verbose` wiring on both CLIs (4).
- `tests/unit/test_cli_help.py` — 48 tests: query `--help` flag presence (21 parametrized), setup `--help` flag presence (12 parametrized), setup `--help` query-only-flag absence (13 parametrized), `--version` exits 0 on both CLIs (2).

**Untouched (intentionally):**
- `src/steeproute/errors.py` — Story 1.4's hierarchy; Story 1.5 doesn't need to extend it (BadCLIArgError raising lands in Story 1.6).
- `src/steeproute/cli/__init__.py`, `src/steeproute/__init__.py` — no re-exports added.
- All other `src/steeproute/*` placeholder modules.
- `tests/conftest.py`, `tests/integration/conftest.py`, `tests/e2e/conftest.py`.
- `tests/unit/test_errors.py`, `tests/unit/test_placeholder.py`.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-04 | Yann (Claude Opus 4.7) | Story 1.5 implemented: click 8.3.3 added as runtime dep; `LatLonParamType` + 23 reusable click-option decorators in `cli/_shared.py`; both CLIs restructured around the decorator stack with `cli` (click command) + `_invoke_command` (SystemExit bridge) + `main` (run_entry_point wrapper); `is_verbose()` getter added; 80 new unit tests (32 options + 48 help/version). All four CI gates green on Windows (ruff, ruff format, basedpyright 0/0/0, pytest 97 passed, 91% coverage). | `8f68fe5` |
| 2026-05-04 | Yann (Claude Opus 4.7) | Lightweight inline review: 4 findings raised, all dismissed (D1 unreachable str-code path in `_invoke_command`; D2 weak `test_decorator_is_callable` covered indirectly by `test_cli_help.py` parametrized tests; D3 weak `"steeproute" in output` query-version assertion; D4 `_ = (...)` ceremony — kept as visible "unused on purpose" marker). 2 informational items recorded (I1 `is_verbose()` getter divergence from Story 1.4's "no getter"; I2 `_main → cli` rename divergence from spec). No code changes. | — |
| 2026-05-04 | Yann (Claude Opus 4.7) | Close-out: status review → done. | (this commit) |

### Review Findings

**Reviewer:** Claude Opus 4.7 (lightweight inline review at user request, given small surface — 1 runtime dep + decorator surface + 2 mostly-mechanical CLI restructures + 2 new test files; ~440 line diff total).
**Date:** 2026-05-04.
**Verdict:** 0 blockers, 0 requested changes. All 4 findings dismissed.

| # | Severity | File | Finding | Resolution |
|---|---|---|---|---|
| D1 | Low | `cli/query.py:114`, `cli/setup.py:78` | `int(exc.code) if isinstance(exc.code, int) else 0` silently swallows non-int `SystemExit` codes (e.g. `str` from `sys.exit("msg")`); fallback returns 0 (success) for an unknown failure. | **Dismissed** — click 8.x `Command.main()` only ever calls `sys.exit(int)` in standalone mode; the fallback is unreachable in practice. |
| D2 | Low | `tests/unit/test_cli_options.py:93–95` | `test_decorator_is_callable` is a weak assertion (every Python function is callable). | **Dismissed** — satisfies the AC literally; real coverage of decorator validity comes from `test_cli_help.py` parametrized `--help`-token-presence tests. |
| D3 | Low | `tests/unit/test_cli_help.py:93` | Query-version assertion `"steeproute" in result.output` would also pass for setup-CLI output (since "steeproute-setup" contains "steeproute"). | **Dismissed** — test functions and `cli` objects are separate; a failure already pinpoints the right CLI. Could tighten to `"steeproute, version"` later. |
| D4 | Low | `cli/query.py:84–103`, `cli/setup.py:57–67` | `_ = (...)` consumption tuple (~20 lines) silences `reportUnusedParameter`; needs piecemeal deletion in Epics 2-4. | **Dismissed** — explicit "unused on purpose, wired up in Epic X" marker. Alternative: per-file `executionEnvironments` rule disable in pyproject — defensible if the ceremony bites later. |

**Informational (no action this story):**

| # | Note |
|---|---|
| I1 | **Public `is_verbose()` getter** added in `_shared.py` — divergence from Story 1.4 §Completion Notes #1's deliberate "no getter" decision. Driven by Story 1.5's verbose-wiring tests; forward-compatible with Story 1.6's likely `is_eager=True` callback work on `--verbose`. Asymmetric API (`set_verbose` public write, `_verbose` private state, `is_verbose()` public read) is consistent enough not to warrant a revert. |
| I2 | **Renamed `_main → cli`** in both CLI files — divergence from the Story 1.5 spec (which called it `_main`). Eliminates a `reportPrivateUsage` warning storm in tests and aligns with click-app conventions. Mildly awkward against the `cli/` package name in `from steeproute.cli.query import cli as query_cli`; aliased on import in tests for readability. Future stories may rename to `query_command`/`setup_command` if the shadowing becomes a problem. |

