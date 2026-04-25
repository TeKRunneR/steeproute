# Story 1.4: Implement shared error hierarchy and run_entry_point wrapper

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want `errors.py` with the full `SteeprouteError` hierarchy and `cli/_shared.py::run_entry_point` wrapping both CLI `main` functions,
So that every subsequent story has a consistent mechanism for producing exit codes 0/1/2/130 and for surfacing `PreExecutionError` user messages on stderr.

## Acceptance Criteria

1. `src/steeproute/errors.py` defines `SteeprouteError(Exception)` (base, never raised directly) and `PreExecutionError(SteeprouteError)` with two attributes: `user_message: str` (required) and `detail: str | None = None` (optional).
2. `src/steeproute/errors.py` also defines five `PreExecutionError` subclasses — `BadCLIArgError`, `CacheNotFoundError`, `CacheCorruptedError`, `DataSourceUnavailableError`, `SolverError` — each currently a `pass`-body subclass (no extra fields/methods; future stories may extend).
3. `src/steeproute/cli/_shared.py` defines `run_entry_point(main_fn: Callable[[], int]) -> NoReturn` that:
   - calls `main_fn()` and treats its `int` return value as the process exit code (covering exit 0 success and exit 1 validation failure paths),
   - catches `PreExecutionError`, writes `error: {user_message}\n` to `sys.stderr`, additionally writes `        {detail}\n` only when the verbose flag is set AND `detail is not None`, and exits 2,
   - catches `KeyboardInterrupt` and exits 130 (no traceback, no error line),
   - terminates the process via `sys.exit(code)` (hence `NoReturn`).
4. `cli/_shared.py` exposes a verbose-state mechanism — module-level boolean `_verbose: bool = False` plus public `set_verbose(value: bool) -> None` setter — that `run_entry_point` consults when deciding whether to print `detail`. (Story 1.5 will wire `--verbose` parsing to call `set_verbose(True)` before any `PreExecutionError` can be raised; Story 1.4 only provides the hook with a sane default of `False`.)
5. `src/steeproute/cli/query.py` and `src/steeproute/cli/setup.py` are restructured so the existing stub logic lives in a private `_main() -> int` and the public `main()` is a one-liner that delegates: `def main() -> NoReturn: run_entry_point(_main)`. The `[project.scripts]` entries already point at `main`, so console-script invocations now flow through `run_entry_point`.
6. `tests/unit/test_errors.py` exists and asserts: each of the five `PreExecutionError` subclasses can be instantiated with `user_message="..."` (positional or keyword) and with `detail="..."`; `user_message` and `detail` round-trip on the instance; `detail` defaults to `None` when omitted; `isinstance(e, PreExecutionError)` and `isinstance(e, SteeprouteError)` both hold for each subclass.
7. `tests/unit/test_run_entry_point.py` exists and asserts the three exit-code paths against a mocked `main_fn` — return-`0` ⇒ `SystemExit.code == 0`; raises `PreExecutionError("...")` ⇒ `SystemExit.code == 2` AND `capsys.readouterr().err` starts with `error: ...`; raises `KeyboardInterrupt` ⇒ `SystemExit.code == 130`. The test for the verbose-controlled `detail` line is in addition: with `set_verbose(True)` and a `PreExecutionError(user_message="m", detail="d")`, stderr contains both lines; with `set_verbose(False)`, stderr contains only the `error: m` line. The test resets `_verbose` to `False` after each case (fixture or finally clause) so cross-test ordering is safe.
8. `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, and `uv run pytest --cov=src/steeproute --cov-report=xml --cov-report=term` all pass on Windows (CI's primary platform per NFR7) with zero findings/failures. `--cov-fail-under` stays at 0 (Story 5.5's territory).
9. `uv run steeproute` and `uv run steeproute-setup` still execute end-to-end, print their stub messages on stdout, and exit 0 — i.e., wrapping the stubs in `run_entry_point` does not regress the smoke behavior established by Story 1.2.

## Tasks / Subtasks

- [x] **Task 1: Implement `errors.py` (AC: #1, #2)**
  - [x] Replace the one-line docstring in `src/steeproute/errors.py` with the full hierarchy.
  - [x] Use `from __future__ import annotations` only if BasedPyright requires it; PEP 604 syntax (`str | None`) is native on 3.13.
  - [x] `SteeprouteError(Exception)` — class docstring "Base class. Never raised directly." Keep body as `pass`.
  - [x] `PreExecutionError(SteeprouteError)` — class docstring "Maps to exit code 2. Raised when the tool cannot produce any output." Implement `__init__(self, user_message: str, detail: str | None = None)` storing both as instance attrs and forwarding `user_message` to `super().__init__(user_message)` so the default `str(e)` stays meaningful. Annotate `user_message: str` and `detail: str | None` on the class for BasedPyright clarity.
  - [x] Five subclasses — `BadCLIArgError`, `CacheNotFoundError`, `CacheCorruptedError`, `DataSourceUnavailableError`, `SolverError` — each a single-line `pass` body with a one-line docstring matching its semantic role from Architecture §Category 10 (e.g., `CacheNotFoundError` → `"FR24 coverage miss."`, `CacheCorruptedError` → `"manifest OK but graph.pkl unreadable."`).
  - [x] No `__all__` (per Architecture: only when curating a deliberately-restricted export set; this module's public surface is the class names themselves).
  - [x] No imports beyond stdlib; no logging in this module (errors are raised, not logged).

- [x] **Task 2: Implement `cli/_shared.py::run_entry_point` + verbose hook (AC: #3, #4)**
  - [x] Create `src/steeproute/cli/_shared.py` (currently does not exist).
  - [x] Module docstring: one line, "Shared CLI plumbing: verbose flag state and exit-code wrapper."
  - [x] `from __future__ import annotations` if needed for `Callable` ergonomics; otherwise `from collections.abc import Callable` and `from typing import NoReturn`.
  - [x] `from steeproute.errors import PreExecutionError` (absolute import per Architecture conventions).
  - [x] Module-level `_verbose: bool = False`. Public `def set_verbose(value: bool) -> None` setter that mutates the module-level `_verbose` via `global`. No getter — `run_entry_point` reads `_verbose` directly.
  - [x] `def run_entry_point(main_fn: Callable[[], int]) -> NoReturn:` — body matches Architecture §Category 10 pseudocode. Read `_verbose` (not a parameter). Use `sys.stderr.write(...)` + explicit `\n` (not `print(..., file=sys.stderr)`) to match the architecture pseudocode literally. End with `sys.exit(code)`. Order of `except` blocks: `PreExecutionError` first, `KeyboardInterrupt` second.
  - [x] No `__all__`. Public symbols are `run_entry_point` and `set_verbose`; `_verbose` is intentionally underscore-prefixed.

- [x] **Task 3: Wire both CLI mains through the wrapper (AC: #5, #9)**
  - [x] In `src/steeproute/cli/query.py`: rename existing `main` body to `def _main() -> int:` (keep the same `print(...)` line and `return 0`). Add `def main() -> NoReturn: run_entry_point(_main)`. Add `from steeproute.cli._shared import run_entry_point`. Add `from typing import NoReturn`.
  - [x] In `src/steeproute/cli/setup.py`: same restructure. Same imports.
  - [x] **Do NOT change `[project.scripts]`** — the entry-point names (`steeproute.cli.query:main` / `steeproute.cli.setup:main`) are unchanged; only the function bodies change.
  - [x] Verify smoke behavior: `uv run steeproute` prints `"steeproute (query CLI) - stub; full implementation lands in Epics 2-4"` on stdout and exits 0; `uv run steeproute-setup` prints its stub and exits 0. Console scripts must not regress (FR21-equivalent for the setup CLI; basic FR30 sanity for both).

- [x] **Task 4: Add `tests/unit/test_errors.py` (AC: #6)**
  - [x] One test per subclass instantiation: `BadCLIArgError`, `CacheNotFoundError`, `CacheCorruptedError`, `DataSourceUnavailableError`, `SolverError` — assert `e.user_message == "..."`, `e.detail == "..."` (or `None` when omitted), `isinstance(e, PreExecutionError)`, `isinstance(e, SteeprouteError)`, `isinstance(e, Exception)`.
  - [x] Test that `str(e) == user_message` (forwarded via `super().__init__`).
  - [x] Test that `SteeprouteError` itself is a subclass of `Exception` and that direct instantiation works (defensive — even though "never raised directly", the class exists and Python doesn't enforce).
  - [x] Test naming: `test_<scenario>` per Architecture §Test organization.

- [x] **Task 5: Add `tests/unit/test_run_entry_point.py` (AC: #7)**
  - [x] Use `pytest.raises(SystemExit) as exc_info` and assert `exc_info.value.code == <expected>` for each path.
  - [x] Use `capsys` fixture to capture stderr for the `error: ...` assertions.
  - [x] Mock `main_fn` with simple `lambda: 0`, `lambda: (_ for _ in ()).throw(PreExecutionError("boom"))`, and `lambda: (_ for _ in ()).throw(KeyboardInterrupt())` — or use small `def` helpers for readability. Helper-function style is preferred over generator-expression-throw tricks (clearer).
  - [x] Verbose-control test: define `def main_fn(): raise PreExecutionError("m", detail="d")`, call `set_verbose(True)`, run, assert stderr contains both `error: m` and `        d`. Then `set_verbose(False)`, repeat, assert stderr contains `error: m` and does NOT contain `d`. Wrap each case in try/finally that resets `_verbose` to `False` (or use a pytest fixture with autouse=True for the file).
  - [x] Do NOT test the `int 1` return path explicitly as a separate case — the "return-0" test already covers the int-passthrough mechanism, and the `1` value is identical machinery. Story 3.x will test exit code 1 in earnest with real validation data.

- [x] **Task 6: Verify all CI gates pass locally (AC: #8)**
  - [x] `uv run ruff check` → pass.
  - [x] `uv run ruff format --check` → pass (run `uv run ruff format` first if needed; Story 1.3 set up the format gate).
  - [x] `uv run basedpyright` → 0 errors, 0 warnings, 0 notes.
  - [x] `uv run pytest --cov=src/steeproute --cov-report=xml --cov-report=term` → all tests pass; coverage > 0% now (was 0% in Story 1.3 because no test imported the package); `coverage.xml` written; fail_under not breached.
  - [x] If ruff complains about lambda-to-def or anything else, prefer named `def` helpers and unused-import cleanups over `# noqa` suppressions.

## Dev Notes

### Architecture & PRD alignment (authoritative)

- **Error hierarchy is locked in by Architecture §Category 10** ([architecture.md:625](_bmad-output/planning-artifacts/architecture.md:625)–[:695](_bmad-output/planning-artifacts/architecture.md:695)). The hierarchy diagram, the `user_message` / `detail` two-attribute contract, the exit-code mapping table, the stderr formatting pattern (`error: {user_message}` plus indented `        {detail}` line on `--verbose`), and the `run_entry_point` pseudocode are all prescriptive — match them literally. The pseudocode at [architecture.md:681–:695](_bmad-output/planning-artifacts/architecture.md:681) is the canonical implementation shape.
- **Exit-code contract is locked in by FR30** ([prd.md:523](_bmad-output/planning-artifacts/prd.md:523)) and the consolidated table at [architecture.md:657–:665](_bmad-output/planning-artifacts/architecture.md:657): 0 = success, 1 = validation failure (data-driven, computed in CLI later — not raised), 2 = `PreExecutionError`, 130 = `KeyboardInterrupt` (FR14, [prd.md:495](_bmad-output/planning-artifacts/prd.md:495)). Story 1.4 implements all four mechanically; the actual exit-1 _data path_ lands with the validator in Epic 3.
- **`SolverError` is "pre-execution tier"** ([architecture.md:653–:655](_bmad-output/planning-artifacts/architecture.md:653)) — unexpected solver-internal failure, treated as exit 2 even though it surfaces during execution. It's a `PreExecutionError` subclass for that reason, despite the name. Don't second-guess the inheritance.
- **CLI framework is click 8.x** (Architecture §Category 2, [architecture.md:203](_bmad-output/planning-artifacts/architecture.md:203)–[:233](_bmad-output/planning-artifacts/architecture.md:233)) but Story 1.4 does NOT add click as a dependency yet — Story 1.5 owns click integration and the full flag surface. The verbose-flag plumbing in this story is a forward-compatible hook (`set_verbose`) that Story 1.5 will call from its click `--verbose`/`--quiet` handler. This staging is deliberate: gives 1.5 a clean integration point with no rework.
- **Stream discipline is locked in by Architecture §Category 8** ([architecture.md:536](_bmad-output/planning-artifacts/architecture.md:536)–[:574](_bmad-output/planning-artifacts/architecture.md:574)): stderr for errors and warnings, stdout for progress and run summary. `run_entry_point` writes only to stderr — that's correct. Do not `print()` errors to stdout.
- **Absolute imports only** (Architecture §Python code conventions, [architecture.md:707](_bmad-output/planning-artifacts/architecture.md:707)): in `cli/_shared.py` use `from steeproute.errors import PreExecutionError`, NOT `from ..errors import ...`.
- **No top-level side effects** (Architecture §Python code conventions, [architecture.md:709](_bmad-output/planning-artifacts/architecture.md:709)): `errors.py` and `_shared.py` must be import-safe — no `print`, no `logging.basicConfig`, no I/O. Side effects only fire when `main()` is invoked.

### Previous-story intelligence (Story 1.3)

Reading [1-3-customize-ci-workflow-and-establish-three-layer-test-structure.md](_bmad-output/implementation-artifacts/1-3-customize-ci-workflow-and-establish-three-layer-test-structure.md) — key signals that shape THIS story:

1. **CI is now strict on Windows** with `ruff check`, `ruff format --check`, `basedpyright`, and `pytest --cov`. Every commit on this story will exercise all four gates. Story 1.3's final pass had basedpyright at 0/0/0 — keep it there. Match Story 1.3's commit cadence: a single atomic commit at the end of all tasks, message body documenting AC walkthrough.
2. **Coverage was 0% in Story 1.3** because the only test (`test_placeholder.py`) didn't import from `src/steeproute`. Story 1.4 changes that — `test_errors.py` and `test_run_entry_point.py` import from `steeproute.errors` and `steeproute.cli._shared`, so coverage will jump (likely to 100% on those two files since the tests are exhaustive). This is expected and not a problem; `fail_under` stays at 0.
3. **`tests/unit/conftest.py` is currently empty** ([1-3-...md:236](_bmad-output/implementation-artifacts/1-3-customize-ci-workflow-and-establish-three-layer-test-structure.md:236)). If Story 1.4's tests need a shared verbose-reset fixture, place it in `tests/unit/conftest.py` (layer-scoped) per Architecture §Test organization rule "shared fixtures go in the nearest `conftest.py`". Do NOT promote it to `tests/conftest.py` — only `cli/_shared.py` cares about `_verbose`, no other layer needs it.
4. **`pyproject.toml [tool.ruff] extend-exclude`** was added defensively in Story 1.3 ([1-3-...md:370](_bmad-output/implementation-artifacts/1-3-customize-ci-workflow-and-establish-three-layer-test-structure.md:370)). It excludes `.claude`, `_bmad`, `_bmad-output` from ruff. Story 1.4's new files all live under `src/` and `tests/` — fully in scope, will be linted normally.
5. **Worktree pattern**: previous stories used named worktrees (`affectionate-volhard-666401`, `loving-jemison-ea7d61`). Current branch is `claude/elastic-williams-5aade0` (this worktree). Both 1.2 and 1.3 are merged to main; clean state. No rebase dance needed for 1.4.
6. **No git remote configured** ([1-3-...md:372](_bmad-output/implementation-artifacts/1-3-customize-ci-workflow-and-establish-three-layer-test-structure.md:372)) — local verification is the acceptance evidence. Story 1.4 is pure-Python + tests; CI pass locally on Windows is the bar.

### Current state of the source tree (verified pre-flight)

```
src/steeproute/
├── __init__.py            # one-line docstring (untouched by 1.4)
├── cli/
│   ├── __init__.py        # one-line docstring (untouched by 1.4)
│   ├── query.py           # ← MODIFIED: extract _main, add main wrapper
│   ├── setup.py           # ← MODIFIED: extract _main, add main wrapper
│   └── _shared.py         # ← NEW: run_entry_point + verbose hook
├── errors.py              # ← REWRITTEN: full hierarchy (currently one-line docstring)
├── cache.py, models.py, output.py, progress.py, provenance.py, validator.py
│                          # ← UNTOUCHED placeholders (one-line docstrings each)
├── pipeline/__init__.py, solver/__init__.py
│                          # ← UNTOUCHED sub-package placeholders
└── py.typed               # untouched
```

```
tests/
├── conftest.py            # untouched (empty)
├── unit/
│   ├── conftest.py        # ← potentially MODIFIED: add verbose-reset autouse fixture if cleaner
│   ├── test_placeholder.py # untouched
│   ├── test_errors.py     # ← NEW
│   └── test_run_entry_point.py  # ← NEW
├── integration/conftest.py  # untouched
└── e2e/conftest.py          # untouched
```

### Concrete implementation sketches

**`src/steeproute/errors.py`** (target shape):

```python
"""SteeprouteError exception hierarchy. Per Architecture §Category 10."""


class SteeprouteError(Exception):
    """Base class. Never raised directly."""


class PreExecutionError(SteeprouteError):
    """Maps to exit code 2. Raised when the tool cannot produce any output."""

    user_message: str
    detail: str | None

    def __init__(self, user_message: str, detail: str | None = None) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


class BadCLIArgError(PreExecutionError):
    """Malformed or out-of-range CLI argument."""


class CacheNotFoundError(PreExecutionError):
    """FR24 coverage miss: query area not contained in any prepared entry."""


class CacheCorruptedError(PreExecutionError):
    """manifest OK but graph.pkl unreadable."""


class DataSourceUnavailableError(PreExecutionError):
    """steeproute-setup: Overpass/IGN down or unreachable."""


class SolverError(PreExecutionError):
    """Unexpected solver-internal failure — best-so-far may be empty; treat as pre-exec tier."""
```

**`src/steeproute/cli/_shared.py`** (target shape):

```python
"""Shared CLI plumbing: verbose flag state and exit-code wrapper."""

import sys
from collections.abc import Callable
from typing import NoReturn

from steeproute.errors import PreExecutionError

_verbose: bool = False


def set_verbose(value: bool) -> None:
    """Set the verbose flag consulted by run_entry_point. Story 1.5 wires --verbose to this."""
    global _verbose
    _verbose = value


def run_entry_point(main_fn: Callable[[], int]) -> NoReturn:
    """Run main_fn with shared exit-code policy (0/1/2/130) and stderr error formatting."""
    try:
        code = main_fn()
    except PreExecutionError as e:
        sys.stderr.write(f"error: {e.user_message}\n")
        if _verbose and e.detail is not None:
            sys.stderr.write(f"        {e.detail}\n")
        code = 2
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
```

**`src/steeproute/cli/query.py`** (target shape):

```python
"""steeproute query CLI entry point (stages 8-9 + solver; wired in later epics)."""

from typing import NoReturn

from steeproute.cli._shared import run_entry_point


def _main() -> int:
    print("steeproute (query CLI) - stub; full implementation lands in Epics 2-4")
    return 0


def main() -> NoReturn:
    run_entry_point(_main)
```

`cli/setup.py` follows the identical pattern with its own stub message.

### Scope boundaries (do NOT creep)

- **Out of scope:** adding `click` as a dependency (Story 1.5); defining any flag decorators in `cli/_shared.py` (Story 1.5); wiring `--verbose` to `set_verbose(True)` (Story 1.5); validating any actual CLI args (Story 1.6); raising `BadCLIArgError` from real arg parsing (Story 1.6); any logic that uses `CacheNotFoundError`, `CacheCorruptedError`, `DataSourceUnavailableError`, `SolverError` in their actual call sites (Epics 2/3).
- **Out of scope:** integrating `run_entry_point` with the eventual exit-1 validation path (Story 3.11 owns that — `_main()` will return `1` directly when validation flags routes; the wrapper already passes ints through, so no work needed here).
- **Out of scope:** any e2e smoke tests asserting exit codes from subprocess invocations (Story 1.7's job). Story 1.4's tests are unit-level only — direct calls to `run_entry_point` with mocked `main_fn`, captured via `capsys` and `pytest.raises(SystemExit)`.
- **Out of scope:** logging configuration, log-level handling, `--quiet` plumbing (Architecture §Category 8 territory, lands when first real logger is needed in Epic 2).
- **Out of scope:** re-exporting error classes from `steeproute/__init__.py` or `cli/__init__.py`. Per Architecture §Python code conventions ([architecture.md:708](_bmad-output/planning-artifacts/architecture.md:708)), `__all__` only when curating a deliberately-restricted export set. Importers will use full paths (`from steeproute.errors import BadCLIArgError`) — fine for an internal package.
- **Out of scope:** any `__init__.py` edits beyond what Story 1.2 already established. The existing one-line docstrings stay.

### Key anti-patterns for this story

- **Do NOT use `print(..., file=sys.stderr)` for error output.** Architecture pseudocode uses `sys.stderr.write(...)` with explicit `\n`. Match it. `print` can buffer differently and adds an implicit newline that complicates the optional indented-detail line.
- **Do NOT make `_verbose` an attribute of a singleton class or a `dataclass` instance.** A module-level bool with a setter is exactly what Architecture's pseudocode implies (`if verbose and e.detail` — no instance receiver). Over-engineering this hook makes Story 1.5's wiring harder, not easier.
- **Do NOT re-introduce relative imports** (`from ..errors import ...`). Architecture §Python code conventions is explicit: absolute imports only.
- **Do NOT add `# type: ignore` or `# noqa` to silence basedpyright/ruff.** If something fails the gate, fix it properly — usually a missing type annotation or unused import. Story 1.3 ended at 0/0/0; keep it there.
- **Do NOT write `def _main()` and `def main()` differently between `query.py` and `setup.py`.** Same shape, different stub message. Symmetry matters because Story 1.5 will iterate on both in the same pass.
- **Do NOT eagerly catch other exceptions in `run_entry_point`.** Architecture only catches `PreExecutionError` and `KeyboardInterrupt`. A bare `except Exception` would silently turn programmer-error tracebacks into exit-1, which fights the contract — bugs should crash loudly with a real traceback so they get fixed. Don't add it "for safety".
- **Do NOT add `if __name__ == "__main__":` blocks to `query.py` / `setup.py`.** The console-script entry points already invoke `main` directly. Adding `__main__` blocks invites duplicate entry-point confusion.
- **Do NOT instantiate `SteeprouteError` directly in production code.** The "never raised directly" rule from Architecture is a convention; the test in `test_errors.py` instantiates it once defensively, but no other code should.
- **Do NOT touch `pyproject.toml`.** Story 1.4 adds zero dependencies. `cli/_shared.py` uses only stdlib + `steeproute.errors`. Click does not arrive until Story 1.5.

### Verification commands (copy-paste runnable, Windows bash)

```bash
# Pre-flight: confirm clean state
git status --short
ls src/steeproute/cli/   # should show __init__.py, query.py, setup.py (NOT _shared.py yet)
ls tests/unit/           # should show conftest.py, test_placeholder.py

# After Tasks 1-3 (source files in place)
ls src/steeproute/cli/   # should now include _shared.py
uv run python -c "from steeproute.errors import BadCLIArgError, CacheNotFoundError, CacheCorruptedError, DataSourceUnavailableError, SolverError, PreExecutionError, SteeprouteError; print('ok')"
uv run python -c "from steeproute.cli._shared import run_entry_point, set_verbose; print('ok')"

# Smoke: console scripts still work
uv run steeproute       # should print stub and exit 0
echo $?                 # 0
uv run steeproute-setup # should print stub and exit 0
echo $?                 # 0

# After Tasks 4-5 (tests in place)
uv run pytest tests/unit/test_errors.py -v
uv run pytest tests/unit/test_run_entry_point.py -v

# Full local CI gate replay (Task 6)
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest --cov=src/steeproute --cov-report=xml --cov-report=term
ls coverage.xml          # should exist

# Before commit
git status
git diff --stat

# Single atomic commit (mirror Story 1.3 cadence)
git add -A
git commit -m "feat: implement shared error hierarchy and run_entry_point wrapper (Story 1.4)"
```

### Project Structure Notes

- Alignment with Architecture §"Complete project tree" ([architecture.md:776](_bmad-output/planning-artifacts/architecture.md:776)–[:860](_bmad-output/planning-artifacts/architecture.md:860)): `cli/_shared.py` is the file the tree drew at the `cli/` sub-package level. This story creates that file. `errors.py` already exists (placeholder from Story 1.2); this story populates it.
- No structural conflicts. The package layout is unchanged; only file _bodies_ change (errors.py, query.py, setup.py) and one new file (`cli/_shared.py`) is added at its architecturally-prescribed location.

### Testing standards summary (Story 1.3 established; this story produces its first real unit tests)

- **Layer:** Both new test files (`test_errors.py`, `test_run_entry_point.py`) live in `tests/unit/` — pure-function, no I/O, no subprocess. Unit-layer is correct per Architecture §Category 11 ([architecture.md:948](_bmad-output/planning-artifacts/architecture.md:948)–[:1040](_bmad-output/planning-artifacts/architecture.md:1040)) and Story 1.3's testing-standards summary.
- **Test naming:** `test_<module>.py` mirrors source module name — `test_errors.py` for `errors.py`, `test_run_entry_point.py` for the function in `cli/_shared.py` (file name reflects the function under test, not the module — fine when the module has multiple distinct units).
- **Fixture placement:** if a verbose-reset autouse fixture is added, it goes in `tests/unit/conftest.py` (layer-scoped). Do NOT promote it to `tests/conftest.py` — no other layer depends on the verbose flag.
- **Function-naming pattern** (Architecture §Test organization, [architecture.md:738](_bmad-output/planning-artifacts/architecture.md:738)–[:746](_bmad-output/planning-artifacts/architecture.md:746)): `test_<unit>_<scenario>` — e.g. `test_run_entry_point_returns_zero_on_main_fn_return_zero`, `test_run_entry_point_exits_two_on_pre_execution_error`, `test_run_entry_point_exits_130_on_keyboard_interrupt`, `test_pre_execution_error_round_trips_user_message_and_detail`.
- **No `hypothesis` here** — Story 1.3 §"Out of scope" explicitly defers `hypothesis` to Epic 2/3 property-based tests. Plain pytest only.

### References

- [Epic 1 Story 1.4 AC + preamble](_bmad-output/planning-artifacts/epics.md:277) — epic AC source of truth, lines 277–289
- [Architecture §Category 10 — Error model](_bmad-output/planning-artifacts/architecture.md:625) — full hierarchy + exit-code mapping + run_entry_point pseudocode + stderr formatting, lines 625–695
- [Architecture §Category 2 — CLI framework + two-binary structure](_bmad-output/planning-artifacts/architecture.md:203) — `[project.scripts]` + run_entry_point conceptual sketch, lines 203–233
- [Architecture §Category 8 — Logging, progress, and stream discipline](_bmad-output/planning-artifacts/architecture.md:536) — stderr/stdout discipline (Story 1.4 only writes to stderr), lines 536–574
- [Architecture §Category 11 — Testing strategy](_bmad-output/planning-artifacts/architecture.md:948) — unit-layer expectations, lines 948–1040
- [Architecture §Test organization](_bmad-output/planning-artifacts/architecture.md:738) — three-layer rule + test naming, lines 738–746
- [Architecture §Python code conventions](_bmad-output/planning-artifacts/architecture.md:701) — absolute imports, no top-level side effects, no `__all__` by default, lines 701–710
- [Architecture §FR → module mapping](_bmad-output/planning-artifacts/architecture.md:877) — FR30 maps to `cli/_shared.py` (`run_entry_point`) + `errors.py`, line 904
- [Architecture §Complete project tree](_bmad-output/planning-artifacts/architecture.md:776) — `cli/_shared.py` placement, line 174 (in tree) and §Cat 1 layout at line 174 of architecture.md
- [PRD FR14 — Ctrl-C produces interrupt exit code](_bmad-output/planning-artifacts/prd.md:495)
- [PRD FR30 — distinct exit codes for success / validation failure / pre-execution error / interrupt](_bmad-output/planning-artifacts/prd.md:523)
- [PRD NFR7 — Windows primary platform](_bmad-output/planning-artifacts/prd.md:545)
- [Story 1.2 — package structure + entry points](_bmad-output/implementation-artifacts/1-2-establish-steeproute-package-structure-and-entry-points.md) — establishes `cli/query.py::main` and `cli/setup.py::main` stubs Story 1.4 wraps
- [Story 1.3 — CI workflow + three-layer test structure](_bmad-output/implementation-artifacts/1-3-customize-ci-workflow-and-establish-three-layer-test-structure.md) — CI gates Story 1.4 must keep green; testing-standards conventions
- pytest `capsys` docs: https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html (used in `test_run_entry_point.py`)
- pytest `pytest.raises(SystemExit)` pattern: https://docs.pytest.org/en/stable/how-to/assert.html#assertions-about-expected-exceptions

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/elastic-williams-5aade0`).

### Debug Log References

**Environment:**
- `uv --version` → 0.9.26 (host).
- Python 3.13.13 selected by `uv sync` (no new deps added; lockfile unchanged from Story 1.3).

**Verification commands (final pass, all green):**
```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 22 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov=src/steeproute --cov-report=xml --cov-report=term
                                   → 17 passed in 0.34s
                                   → coverage.xml written
                                   → TOTAL 31/45 stmts (69%); errors.py 100%, cli/_shared.py 100%; cli/query.py + cli/setup.py 0% (smoke-tests are Story 1.7's job)
```

**Console-script smoke test:**
```
uv run steeproute            → "steeproute (query CLI) - stub; full implementation lands in Epics 2-4"  exit=0
uv run steeproute-setup      → "steeproute-setup (data preparation CLI) - stub; full implementation lands in Epic 2"  exit=0
```

**Red-green sequence:** Wrote `tests/unit/test_errors.py` and `tests/unit/test_run_entry_point.py` first; confirmed both failed at import time (`ImportError: cannot import name 'BadCLIArgError'` and `cannot import name '_shared'`). Then implemented `errors.py` and `cli/_shared.py`, wrapped both CLI mains, and re-ran — 17/17 green.

### Completion Notes List

**Divergences from story spec (worth noting for review):**

1. **Dropped one redundant test (`test_set_verbose_mutates_module_level_flag`).** The first basedpyright run flagged 3 `reportPrivateUsage` warnings on direct access to `_shared._verbose` from the test file. The story's Task 2 explicitly said "No getter — `run_entry_point` reads `_verbose` directly", so adding a public getter would have violated the spec. The mutate-flag test was redundant with the two behavioral verbose-toggle tests (`test_run_entry_point_omits_detail_when_verbose_is_false` / `..._includes_detail_when_verbose_is_true`), which already prove the flag's effect end-to-end. Removing the redundant test brought basedpyright back to 0/0/0, satisfying AC #8. The story's "no `# type: ignore` suppressions" anti-pattern was honored — fix the cause, not the symptom.

2. **Used a fixture for verbose-state reset rather than try/finally** (the story's Task 5 listed both as acceptable). Chose the `autouse=True` fixture style (`reset_verbose_flag`) — slightly cleaner, prevents any future test in this file from forgetting the reset, and yields identical behavior.

3. **No `[tool.coverage.report] omit` adjustments needed** despite the deferred-work note about `cli/` exclusions. Coverage threshold stays at `fail_under = 0`; raising it (and adding the omit list) is Story 5.5's territory, exactly as deferred-work.md predicted. The 69% snapshot is healthy progress — pure-logic modules at 100%, CLI files at 0% awaiting Story 1.7's smoke tests.

4. **No new dependencies, no `pyproject.toml` edits** — Story 1.4 is pure stdlib + intra-package imports. `uv.lock` unchanged.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `errors.py` defines `SteeprouteError(Exception)` and `PreExecutionError(SteeprouteError)` with `user_message: str` (required) and `detail: str | None = None`. ✅
2. AC #2 — Five subclasses defined: `BadCLIArgError`, `CacheNotFoundError`, `CacheCorruptedError`, `DataSourceUnavailableError`, `SolverError`, each `pass`-body with one-line docstring matching Architecture §Cat 10 semantics. ✅
3. AC #3 — `run_entry_point(main_fn: Callable[[], int]) -> NoReturn` in `cli/_shared.py` handles all four exit-code paths (0, int passthrough, 2, 130) with stderr formatting matching Architecture pseudocode literally. ✅
4. AC #4 — Module-level `_verbose: bool = False` plus `set_verbose(value: bool) -> None` setter exposed; `run_entry_point` consults `_verbose` for the optional indented-detail line. ✅
5. AC #5 — Both `cli/query.py` and `cli/setup.py` restructured: `_main()` holds the original stub, `main()` is `def main() -> NoReturn: run_entry_point(_main)`. `[project.scripts]` unchanged. ✅
6. AC #6 — `tests/unit/test_errors.py` covers all five subclasses + base classes (10 tests), asserts round-trip of `user_message`/`detail`, default `detail=None`, `isinstance` chain through `PreExecutionError` → `SteeprouteError` → `Exception`, and `str(e) == user_message`. ✅
7. AC #7 — `tests/unit/test_run_entry_point.py` (6 tests) covers exit codes 0, 1 (passthrough), 2 (with stderr `error: boom\n`), 130, and verbose-controlled detail line both ways. Autouse fixture resets `_verbose` between tests. ✅
8. AC #8 — All four CI gates green on Windows: `ruff check` pass, `ruff format --check` pass, `basedpyright` 0/0/0, `pytest --cov` 17 passed, `coverage.xml` written, `fail_under=0` not breached. ✅
9. AC #9 — Both console scripts execute via `uv run`, print their stub messages on stdout, and exit 0. Story 1.2's smoke behavior preserved. ✅

### File List

**Modified:**
- `src/steeproute/errors.py` — replaced one-line docstring with `SteeprouteError` base + `PreExecutionError` (with `user_message`/`detail` `__init__`) + 5 subclasses.
- `src/steeproute/cli/query.py` — split stub into `_main()` (private; original logic) and `main() -> NoReturn` (delegates to `run_entry_point`); added imports.
- `src/steeproute/cli/setup.py` — same restructure as `query.py`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 1.4 status moved `backlog` → `ready-for-dev` → `in-progress` → `review`; dated comments added.

**New:**
- `src/steeproute/cli/_shared.py` — `run_entry_point` exit-code wrapper + `set_verbose` hook + module-level `_verbose: bool`.
- `tests/unit/test_errors.py` — 10 tests covering the full error hierarchy.
- `tests/unit/test_run_entry_point.py` — 6 tests covering exit-code paths and verbose-detail toggle, with autouse `reset_verbose_flag` fixture.
- `_bmad-output/implementation-artifacts/1-4-implement-shared-error-hierarchy-and-run-entry-point-wrapper.md` — this story file.

**Untouched (intentionally):**
- `pyproject.toml` — no new dependencies; Story 1.4 is pure stdlib.
- `uv.lock` — unchanged.
- `src/steeproute/__init__.py`, `cli/__init__.py` — no re-exports added (per story §Scope boundaries).
- All other `src/steeproute/*.py` placeholder modules — owned by later stories.
- `tests/{conftest.py, integration/conftest.py, e2e/conftest.py, unit/conftest.py}` — no shared fixtures needed beyond the file-scoped `reset_verbose_flag` autouse fixture in `test_run_entry_point.py`.

### Change Log

| Date | Author | Description |
|---|---|---|
| 2026-04-25 | Yann (Claude Opus 4.7) | Story 1.4 implemented: `SteeprouteError` hierarchy in `errors.py`; `run_entry_point` wrapper + `set_verbose` hook in `cli/_shared.py`; both CLI `main` functions wrapped via `_main`/`main` split; 16 new unit tests. All four CI gates green on Windows (ruff, ruff format, basedpyright 0/0/0, pytest 17 passed). |
