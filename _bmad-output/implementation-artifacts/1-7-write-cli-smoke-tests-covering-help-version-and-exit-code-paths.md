# Story 1.7: Write CLI smoke tests covering help, version, and exit-code paths

Status: review

## Story

As a developer,
I want end-to-end smoke tests for both CLIs covering `--help`, `--version`, malformed args, area-cap rejection, and the happy path,
so that every Epic 1 deliverable has CI coverage of the **installed** CLI surface and any regression in the entry-point shim, console-script wiring, or exit-code contract is caught on the next commit.

## Acceptance Criteria

1. `tests/e2e/test_cli_smoke.py` exists and invokes the installed binaries via `subprocess.run(["uv", "run", "steeproute", ...])` (or `steeproute-setup`) — i.e. exercises the real `[project.scripts]` entry-point shim, not click's `CliRunner` (in-process coverage already lives in `tests/unit/`).
2. `steeproute --help` and `steeproute-setup --help` each exit `0`, and the stdout of each invocation contains **every flag** from that CLI's option stack (Story 1.5). The expected per-CLI flag list is enumerated explicitly in the test file (one assertion per flag, parametrized) so a regression names the missing flag.
3. `steeproute --version` and `steeproute-setup --version` each exit `0` and print stdout containing the program name (`steeproute` / `steeproute-setup`) plus a recognizable version token (any non-empty token after the program name; we don't assert a specific version string).
4. `steeproute --center abc,def --radius 10` exits `2` and stderr starts with `error:` — verifying that a `BadCLIArgError` raised inside `LatLonParamType.convert` (Story 1.6) propagates through `_invoke_command` → `run_entry_point` (Story 1.4) end-to-end.
5. `steeproute --center 45.07,6.11 --radius 30` (no `--area-cap` override → default `500.0`) exits `2` and stderr both starts with `error:` and contains the substring `--area-cap` — verifying `validate_area_size` (Story 1.6) under the real subprocess.
6. **Happy path:** `steeproute --center 45.0716,6.1079 --radius 10` and `steeproute-setup --center 45.0716,6.1079 --radius 10 --dem-path .` each exit `0` and stdout contains the Story 1.5 stub message ("steeproute (query CLI) - stub..." / "steeproute-setup (data preparation CLI) - stub..."). Satisfies Architecture §Category 11e structural requirement: "Both CLIs have smoke tests covering the full happy path (exit 0)."
7. All four CI gates (`ruff check`, `ruff format --check`, `basedpyright`, `pytest --cov`) pass on Windows with zero findings/failures, and the new e2e tests run as part of the default `pytest` collection (no `pytest.mark.live`/skip — these are unconditional).

## Tasks / Subtasks

- [x] **Task 1: Add `tests/e2e/test_cli_smoke.py` with a small `_run_cli` subprocess helper** (AC: #1)
  - [x] Helper signature: `_run_cli(*args: str) -> subprocess.CompletedProcess[str]`. Body: `subprocess.run(["uv", "run", *args], capture_output=True, text=True, check=False, cwd=<repo root>)`. Resolve repo root via `pathlib.Path(__file__).resolve().parents[2]` so the test is location-stable.
  - [x] Use `text=True` so stdout/stderr are `str`; default encoding is fine on Windows for ASCII-only assertions.
  - [x] Do **not** set `shell=True` (avoid quoting hazards; also a basedpyright/ruff smell).
- [x] **Task 2: `--help` smoke tests for both CLIs** (AC: #2)
  - [x] Define two module-level lists `QUERY_FLAGS` and `SETUP_FLAGS` mirroring `tests/unit/test_cli_help.py` (intentional duplication — drift between the in-process and subprocess views will fail loudly here, which is the point of an e2e layer). Add a one-line comment cross-referencing the unit-layer list so future edits update both.
  - [x] Two parametrized tests: `test_query_help_lists_flag(flag)` and `test_setup_help_lists_flag(flag)`. Each parametrizes over its CLI's flag list, runs `--help` once per case (yes — duplicate subprocess launches; only ~33 total, ~2-3 s wall-clock; the named-failure benefit outweighs the cost). If subprocess overhead becomes painful in CI, collapse to one `--help` invocation per CLI + multiple `in result.stdout` asserts in a single test (loses per-flag failure naming).
  - [x] Assert exit code `0` and `flag in result.stdout`.
- [x] **Task 3: `--version` smoke tests for both CLIs** (AC: #3)
  - [x] One test per CLI. Assert exit code `0`, program name in stdout, and stdout has at least one token after the program name (e.g. `len(result.stdout.split()) >= 2` is sufficient; don't pin the version string — `uv-dynamic-versioning` derives it from git tags and will drift).
- [x] **Task 4: Exit-code-2 smoke tests for malformed `--center` and area-cap rejection** (AC: #4, #5)
  - [x] `test_query_malformed_center_exits_2`: `_run_cli("steeproute", "--center", "abc,def", "--radius", "10")` → assert exit `2` and `result.stderr.startswith("error:")`.
  - [x] `test_query_area_cap_exceeded_exits_2`: `_run_cli("steeproute", "--center", "45.07,6.11", "--radius", "30")` → assert exit `2`, `result.stderr.startswith("error:")`, and `"--area-cap" in result.stderr`.
- [x] **Task 5: Happy-path smoke tests for both CLIs** (AC: #6)
  - [x] `test_query_happy_path_exits_0`: `_run_cli("steeproute", "--center", "45.0716,6.1079", "--radius", "10")` → assert exit `0` and `"stub" in result.stdout`.
  - [x] `test_setup_happy_path_exits_0`: `_run_cli("steeproute-setup", "--center", "45.0716,6.1079", "--radius", "10", "--dem-path", ".")` → assert exit `0` and `"stub" in result.stdout`. (Setup needs `--dem-path` because the option exists in its stack; passing `.` is fine — the stub doesn't read it. If `--dem-path` is not actually `required=True` today, drop it from the invocation to keep the case minimal.)
- [x] **Task 6: Verify all CI gates pass locally on Windows** (AC: #7)
  - [x] `uv sync` first (otherwise the smoke tests fail at the subprocess layer — see Dev Notes).
  - [x] Run `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov`. Confirm the new e2e file is collected by default (no marker).

## Dev Notes

- **Why subprocess and not `CliRunner` here:** the unit layer (`tests/unit/test_cli_help.py`, `test_cli_options.py`, `test_area_parsing.py`) already covers the click commands in-process. The e2e layer's job is to prove the **installed entry-point shim** works: that `[project.scripts]` wires up correctly, that `_invoke_command` translates `SystemExit` into an int, that `run_entry_point` formats `error: ...` to the real stderr stream, and that exit codes survive the OS-level boundary. None of that is exercised by `CliRunner`. [Source: architecture.md §Category 11e — "Both CLIs have smoke tests covering the full happy path (exit 0)"]
- **Why `uv run` and not `python -m steeproute.cli.query`:** the epic AC explicitly says `via uv run`. Beyond the AC: invoking via `uv run steeproute` proves the `[project.scripts]` console-script entries actually install and resolve, which `python -m` would bypass. [Source: epics.md §"Story 1.7" AC; pyproject.toml `[project.scripts]`]
- **Test prerequisite — `uv sync` must have run:** the smoke tests assume `steeproute` and `steeproute-setup` are installed in the active uv environment. CI's `Sync dependencies` step satisfies this (see `.github/workflows/ci.yml`). Locally, a developer who edits `cli/_shared.py` between an editable install and the e2e run is fine (editable install picks up edits); a developer who hasn't run `uv sync` at all will see all e2e tests fail with `command not found`. Don't paper over this with try/skip — the prerequisite is enforced by the human-readable error.
- **Subprocess overhead:** each `uv run` launch is ~200-500 ms on Windows. Total e2e wall-clock for this story: ~33 invocations (`--help` × 21+12 + `--version` × 2 + exit-2 × 2 + happy × 2) ≈ 7-15 s. Acceptable for an e2e layer that runs once per CI build. If this becomes the longest test in the suite, the optimization is to collapse the 21+12 parametrized `--help` cases to "one `--help` invocation per CLI, all flags asserted in one test" (loses per-flag failure naming — pick named-failure now, optimize later if needed).
- **Don't redefine the flag lists in a shared helper module:** duplication between `tests/unit/test_cli_help.py::QUERY_FLAGS` and the e2e equivalent is intentional. The two layers verify different things (in-process click structure vs. installed-binary stdout); any drift between them is a real signal worth a CI failure.
- **Coverage incidental:** `cli/query.py` and `cli/setup.py` are excluded from the coverage percentage floor (Architecture §Category 11e), but these subprocess tests will exercise the `_invoke_command` SystemExit-branch and `run_entry_point`'s exit-code translation that Story 1.6's unit tests left at 95% / 79%. No threshold tightening this story; just observe the bump.
- **Out of scope:**
  - Verbose-detail line rendering through subprocess (`--verbose` + malformed `--center`). Story 1.6's `test_verbose_with_malformed_center_renders_detail_via_main` already exercises the `main() → run_entry_point` path that the subprocess hits; subprocess adds no new coverage. Skip.
  - `--quiet` behavior, `KeyboardInterrupt` (exit 130), `cache.py`-driven exit-2 paths. Those land in their own epics.
  - The deferred negative-`--radius` issue (deferred-work.md §Story 1.6 D1) is a future-epic concern, not an Epic 1 smoke-test target.

### Project Structure Notes

- New file: `tests/e2e/test_cli_smoke.py`. No other production-code or test edits.
- The `tests/e2e/conftest.py` is currently a one-line stub; this story does not need to add fixtures (a single `_run_cli` helper inside the test module is simpler than a fixture for a tool used by every test).
- No structural conflicts with the architecture project tree.

### Testing standards summary

- Layer: `tests/e2e/` — subprocess-based, real-binary smoke tests. [Source: architecture.md §Category 11e]
- Naming: `test_<cli>_<scenario>` (e.g. `test_query_help_lists_flag`, `test_setup_happy_path_exits_0`). Module name per epic AC (`test_cli_smoke.py`). [Source: architecture.md §"Test organization"]
- Conventions inherited from prior Epic 1 test files: absolute imports only, PEP 604 unions, no `Any`, type-checked under basedpyright, ruff-formatted. [Source: architecture.md §"Implementation Patterns & Consistency Rules"]
- Use `subprocess.run(..., check=False)` (we want non-zero exit codes — `check=True` would raise `CalledProcessError` and make exit-code assertions awkward).
- No `pytest.mark` markers on these tests — they run unconditionally as part of the default collection (the e2e layer is part of the CI default `pytest --cov`, see `.github/workflows/ci.yml`).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 1.7"]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 2 — CLI framework + two-binary structure]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 10 — Error model] — `error: {user_message}` stderr format that AC #4/#5 assert
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11e — Testing strategy] — structural requirement "Both CLIs have smoke tests covering the full happy path (exit 0)" backing AC #6
- [Source: _bmad-output/planning-artifacts/architecture.md §"FR → module mapping"] — FR1, FR2, FR30 (this story is the e2e validator of all three)
- [Source: _bmad-output/planning-artifacts/prd.md §FR30] — distinct exit codes for success / pre-execution error
- [Source: _bmad-output/implementation-artifacts/1-4-implement-shared-error-hierarchy-and-run-entry-point-wrapper.md] — `run_entry_point` contract (`error:` prefix, exit-code mapping) consumed by AC #4/#5
- [Source: _bmad-output/implementation-artifacts/1-5-define-full-click-option-decorator-surface-for-both-clis.md] — flag list per CLI, drives AC #2
- [Source: _bmad-output/implementation-artifacts/1-6-validate-area-specification-at-cli-boundary.md] — `LatLonParamType` + `validate_area_size` paths exercised by AC #4/#5
- [Source: tests/unit/test_cli_help.py] — `QUERY_FLAGS` / `SETUP_FLAGS` lists to mirror in the e2e layer
- [Source: pyproject.toml `[project.scripts]`] — console-script wiring under test
- [Source: .github/workflows/ci.yml] — `uv sync` step that satisfies the e2e tests' install prerequisite

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/suspicious-edison-eb82e0`).

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` required to traverse the corporate Netskope TLS-intercepting proxy.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 26 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 156 passed in 10.70s; coverage 95% overall
                                     (117 prior unit + 39 new e2e smoke)
                                     - cli/_shared.py 100%; errors.py 100%
                                     - cli/query.py 95%; cli/setup.py 79%
                                       (unchanged from Story 1.6 — pytest-cov
                                        does not track subprocess-launched
                                        coverage; the e2e tests structurally
                                        validate the entry-point shim, but
                                        line-coverage in the cli/ modules is
                                        in-process-only)
```

### Completion Notes List

**Divergences from story spec (worth noting for review):**

1. **Task 5: `--dem-path` omitted from setup happy-path test.** Story Task 5 instruction allowed dropping `--dem-path` if the option isn't `required=True`. `dem_path_option` in `cli/_shared.py` has `default=None`, so omitting it is the minimal invocation and matches what a real user would type. Test still exits 0 and reaches the stub.
2. **Subprocess coverage observation:** `pytest-cov` is in-process; subprocess-launched binaries don't contribute to the coverage report. So `cli/query.py` (95%) and `cli/setup.py` (79%) numbers are unchanged from Story 1.6 — the e2e layer's value is structural (proves `[project.scripts]` wiring + `_invoke_command`/`run_entry_point` plumbing under the OS-level boundary), not coverage padding. This was noted in story Dev Notes ("Coverage incidental"); the prediction "smoke tests will exercise [...] left at 95% / 79%" turned out to be wrong because of the subprocess-coverage limitation. No story change needed — the structural validation is the real deliverable. If we ever want subprocess-coverage tracking, it requires `coverage.py`'s `parallel = true` + `subprocess.run` env wiring, which is overkill for this story's purpose.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `tests/e2e/test_cli_smoke.py` exists; `_run_cli(*args)` invokes `subprocess.run(["uv", "run", *args], capture_output=True, text=True, check=False, cwd=_REPO_ROOT)`. No `CliRunner` in the file. ✅
2. AC #2 — `test_query_help_lists_flag` parametrized over 21 query flags; `test_setup_help_lists_flag` parametrized over 12 setup flags. Each subprocess-launches `--help`, asserts exit 0 + flag substring. All 33 cases pass. ✅
3. AC #3 — `test_query_version_exits_zero` and `test_setup_version_exits_zero` assert exit 0 + program name in `tokens[0]` + `len(tokens) >= 2` (program name + version token). Both pass. ✅
4. AC #4 — `test_query_malformed_center_exits_2` confirms `--center abc,def --radius 10` exits 2 with `stderr.startswith("error:")`. End-to-end proves the `BadCLIArgError` raised inside `LatLonParamType.convert` (Story 1.6) propagates through `_invoke_command` → `run_entry_point` → real stderr. ✅
5. AC #5 — `test_query_area_cap_exceeded_exits_2` confirms `--center 45.07,6.11 --radius 30` (default `--area-cap 500`) exits 2 with `stderr.startswith("error:")` and `"--area-cap" in stderr`. End-to-end validation of `validate_area_size` (Story 1.6) under the real subprocess. ✅
6. AC #6 — `test_query_happy_path_exits_0` and `test_setup_happy_path_exits_0` confirm both CLIs exit 0 with `"stub" in stdout` for valid invocations. Satisfies Architecture §Category 11e structural requirement. ✅
7. AC #7 — All four CI gates pass on Windows: ruff check ✅, ruff format --check ✅, basedpyright 0/0/0 ✅, pytest 156 passed (39 new + 117 prior) ✅. New e2e file collected by default (no marker), runs in ~11 s. ✅

### File List

**New:**
- `tests/e2e/test_cli_smoke.py` — 39 subprocess-based smoke tests covering AC #1–#6: parametrized `--help` flag presence (21 query + 12 setup), `--version` (2), exit-2 paths (malformed `--center` + area-cap exceeded), and happy paths (2). Single `_run_cli` helper wraps `subprocess.run(["uv", "run", *args], ...)`.

**Modified:**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 1.7 status `backlog → ready-for-dev → in-progress → review`; dated comments added.

**Untouched (intentionally):**
- `tests/e2e/conftest.py` — remained a one-line stub; no fixtures needed (the `_run_cli` helper is module-local and used by every test, making a fixture unnecessary indirection).
- `tests/unit/test_cli_help.py` — its `QUERY_FLAGS`/`SETUP_FLAGS` lists are deliberately mirrored (not imported) in the e2e file. Drift between the two is a real signal worth a CI failure (per story Dev Notes).
- All production code under `src/steeproute/` — no production changes needed; this is a test-only story validating Stories 1.4–1.6's surface end-to-end.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-04 | Yann (Claude Opus 4.7) | Story 1.7 implemented: new `tests/e2e/test_cli_smoke.py` with 39 subprocess-based smoke tests covering both CLIs' `--help` (parametrized over every flag from Story 1.5 — 21 query + 12 setup), `--version` exit 0, `BadCLIArgError` exit-2 paths from Story 1.6 (malformed `--center` + area-cap exceeded), and happy-path stub invocations (Architecture §Category 11e structural requirement). All four CI gates green on Windows: ruff, ruff format, basedpyright 0/0/0, pytest 156 passed (39 new + 117 prior), 95% coverage. Test-only story — no production code changes. | (this commit) |
