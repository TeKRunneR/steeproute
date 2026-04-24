# Story 1.2: Establish steeproute package structure and entry points

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want the `src/steeproute/` package scaffolded with its sub-packages (`cli/`, `pipeline/`, `solver/`) and flat-module placeholders, plus both console-script entry points wired in `pyproject.toml`,
So that `steeproute` and `steeproute-setup` are invokable commands and the module layout matches the Architecture Project Structure before any real logic lands.

## Acceptance Criteria

1. **Template residue removed.** The stub `src/steeproute/steeproute.py` (template-generated `main()`) and the `from .steeproute import *` line in `src/steeproute/__init__.py` are deleted. The repo-root disposable `main.py` (`uv init` stub) is deleted.
2. **Sub-packages created.** `src/steeproute/cli/`, `src/steeproute/pipeline/`, and `src/steeproute/solver/` each exist with an `__init__.py` file containing a one-line module docstring describing the sub-package's role. No other code in these `__init__.py` files (no `__all__`, no re-exports, no imports).
3. **CLI entry-point stubs created.** `src/steeproute/cli/query.py` and `src/steeproute/cli/setup.py` each define a `main()` function that prints a recognizable stub message to stdout and returns the integer `0`. Absolute imports only (no relative imports). Module docstring at top, one line.
4. **Flat-module placeholders created.** The following files exist directly under `src/steeproute/`, each containing only a one-line module docstring (no code, no `TODO:` markers, no placeholder functions): `validator.py`, `cache.py`, `output.py`, `progress.py`, `errors.py`, `models.py`, `provenance.py`.
5. **Top-level `__init__.py` is clean.** `src/steeproute/__init__.py` contains a one-line module docstring and nothing else (no `__all__`, no `from .steeproute import *`, no imports of sub-packages).
6. **`[project.scripts]` entries updated.** `pyproject.toml` contains exactly these two entries under `[project.scripts]`:
   ```toml
   [project.scripts]
   steeproute = "steeproute.cli.query:main"
   steeproute-setup = "steeproute.cli.setup:main"
   ```
   The old `steeproute = "steeproute:main"` entry is gone. No other `pyproject.toml` sections are edited by this story.
7. **Console scripts install and run.** After `uv sync`, `uv run steeproute` executes the `cli/query.py` stub, prints its stub message to stdout, and exits with code `0`. Same for `uv run steeproute-setup` against `cli/setup.py`. On Windows, the generated launchers land at `.venv/Scripts/steeproute.exe` and `.venv/Scripts/steeproute-setup.exe`.
8. **`uv run pytest` still passes.** The template's `tests/test_placeholder.py` continues to pass (no regression). This story adds no new tests — Story 1.3 restructures the test tree; Story 1.7 lands CLI smoke tests.
9. **Lint + type-check still green.** `uv run python devtools/lint.py` reports zero findings against the new module layout (ruff check, ruff format check, codespell, basedpyright).

## Tasks / Subtasks

- [x] **Task 1: Remove template residue** (AC: #1, #5)
  - [x] Delete `src/steeproute/steeproute.py` (template's stub `main()` file).
  - [x] Delete repo-root `main.py` (pre-existing `uv init` stub; explicitly owned by this story per Story 1.1 Completion Notes divergence #2).
  - [x] Rewrite `src/steeproute/__init__.py` so it contains ONLY a one-line module docstring (e.g. `"""steeproute — trail route optimization package."""`). Remove the existing `__all__` tuple and the `from .steeproute import *` line entirely.
  - [x] Leave `src/steeproute/py.typed` in place (PEP 561 marker — keep it).

- [x] **Task 2: Create sub-package directories** (AC: #2)
  - [x] `src/steeproute/cli/__init__.py` — one-line docstring (e.g. `"""CLI entry points: steeproute (query) and steeproute-setup (data preparation)."""`).
  - [x] `src/steeproute/pipeline/__init__.py` — one-line docstring (e.g. `"""Data pipeline stages 1–9 and orchestrator."""`).
  - [x] `src/steeproute/solver/__init__.py` — one-line docstring (e.g. `"""GRASP solver: construction loop, anytime best-so-far, top-N distinctness."""`).
  - [x] Do NOT add `__all__`, re-exports, or sub-module imports — keep each file to the single docstring. Architecture §Python code conventions: `__all__` only when curating a deliberately restricted export set, not habitually.

- [x] **Task 3: Create CLI entry-point stubs** (AC: #3)
  - [x] `src/steeproute/cli/query.py`: module docstring (`"""steeproute query CLI entry point (stages 8–9 + solver; wired in later epics)."""`) and a `main() -> int` function that prints a stub message (e.g. `"steeproute (query CLI) — stub; full implementation lands in Epics 2–4"`) and returns `0`. No imports beyond what's needed (none for now). No click, no argparse — Story 1.5 adds the flag surface.
  - [x] `src/steeproute/cli/setup.py`: same shape, with `main() -> int` printing a setup-specific stub (e.g. `"steeproute-setup (data preparation CLI) — stub; full implementation lands in Epic 2"`) and returning `0`.
  - [x] Both `main` functions are top-level module functions. Do NOT wrap them in click decorators, argparse, or `run_entry_point` — those wrappers land in Stories 1.4 and 1.5.
  - [x] Do NOT create `src/steeproute/cli/_shared.py` in this story — it's owned by Stories 1.4 and 1.5. The architecture project tree lists it as the final-state target, but this story's AC enumerates only `__init__.py`, `query.py`, and `setup.py` for the `cli/` directory.

- [x] **Task 4: Create flat-module placeholders** (AC: #4)
  - [x] For each of these seven files under `src/steeproute/`, create a file containing ONLY a one-line module docstring (blank line after docstring is fine; no imports, no classes, no functions, no `TODO` comments):
    - `validator.py` → `"""Runtime route validation (FR26–28). Implementation lands in Epic 3."""`
    - `cache.py` → `"""Cache I/O: key hashing, atomic writes, coverage check. Implementation lands in Epic 2."""`
    - `output.py` → `"""HTML + JSON report rendering. Implementation lands in Epic 3."""`
    - `progress.py` → `"""ProgressEvent dataclass and throttled-callback helpers. Implementation lands in Epic 4."""`
    - `errors.py` → `"""SteeprouteError exception hierarchy. Implementation lands in Story 1.4."""`
    - `models.py` → `"""Route, Climb, ContractedGraph, and solver-side dataclasses. Implementation lands across Epics 2–3."""`
    - `provenance.py` → `"""Commit hash + dirty flag; OSM/DEM version resolution; datetime helpers. Implementation lands in Epic 2."""`
  - [x] Do NOT create `templates/` (Epic 3 Story 3.10 owns the Jinja2 template and vendored JS assets).

- [x] **Task 5: Wire `[project.scripts]` in pyproject.toml** (AC: #6)
  - [x] Open `pyproject.toml` and locate the `[project.scripts]` section (currently lines 56–58).
  - [x] Replace the single entry `steeproute = "steeproute:main"` with the two entries specified in AC #6, preserving section formatting and the existing comment.
  - [x] Do NOT modify any other section: `dependencies` stays `[]` (Story 1.5 adds click; Epic 2 adds runtime deps), dev deps unchanged, ruff/basedpyright/pytest/hatch config unchanged, `requires-python` unchanged (already `>=3.13,<4.0` per Story 1.1 fix).

- [x] **Task 6: Reinstall and smoke-test** (AC: #7, #8, #9)
  - [x] Run `uv sync` from repo root — must succeed. This regenerates the console-script launchers in `.venv/Scripts/` (Windows) or `.venv/bin/` (POSIX) against the new entry-point targets.
  - [x] Run `uv run steeproute` — must print the `cli/query.py` stub message to stdout and exit with code `0`. Verify exit code with `echo %ERRORLEVEL%` on cmd, `echo $LASTEXITCODE` on PowerShell, or `echo $?` on bash.
  - [x] Run `uv run steeproute-setup` — must print the `cli/setup.py` stub message to stdout and exit with code `0`.
  - [x] Run `uv run pytest` — `tests/test_placeholder.py` still passes; no other changes expected.
  - [x] Run `uv run python devtools/lint.py` — must report zero findings. Ruff's `I` (isort) rule is enabled; the empty placeholder modules have no imports to sort, so there's nothing to flag. Basedpyright will see the new modules via its `include = ["src", "tests", "devtools"]` config; empty docstring-only modules produce no type-check warnings.

- [x] **Task 7: Commit** (applies to all AC)
  - [x] Stage only the files this story creates, modifies, or deletes (no unrelated `.claude/settings.local.json` or IDE cruft). Expected file-list: 11 new files under `src/steeproute/` (2 rewritten: `__init__.py`, plus `cli/__init__.py`, `cli/query.py`, `cli/setup.py`, `pipeline/__init__.py`, `solver/__init__.py`, and 7 flat-module placeholders), 1 modified (`pyproject.toml`), 2 deleted (`src/steeproute/steeproute.py`, `main.py`).
  - [x] Commit message: `feat(scaffold): establish steeproute package structure and entry points (Story 1.2)` or equivalent `feat:` / `chore:` label per the user's preference. Reference the story in the body if needed.
  - [x] Do NOT push.

## Dev Notes

### Previous Story Intelligence (Story 1.1)

Story 1.1 applied the `simple-modern-uv` Copier template. From its Completion Notes (see [1-1-scaffold-project-via-simple-modern-uv-copier-template.md](1-1-scaffold-project-via-simple-modern-uv-copier-template.md):217):

- **Divergence #2 (template residue):** the template ships `src/steeproute/__init__.py` with `__all__ = (...)` + `from .steeproute import *`, a `src/steeproute/steeproute.py` with a stub `main()`, and `src/steeproute/py.typed`. The current `[project.scripts]` is `steeproute = "steeproute:main"` pointing at the template placeholder. Story 1.2 restructures into the real layout and retargets the entry points — AC #1 and AC #6 explicitly own this cleanup.
- **Divergence #7:** `main.py` at repo root is unchanged from the `uv init` stub (template did not replace it). Story 1.2 deletes it (AC #1).
- **Divergence #6:** `.claude/settings.local.json` is deliberately untracked and must stay out of any commit.
- `.gitignore` already includes `.claude/settings.local.json` and `.claude/worktrees/` (Story 1.1 review patch P1). Don't touch `.gitignore`.

### Out of scope for this story (do NOT pre-do)

- **`cli/_shared.py`** — owned by Story 1.4 (`run_entry_point`) and Story 1.5 (shared click option decorators). Do not create it now.
- **`tests/unit/`, `tests/integration/`, `tests/e2e/` restructure** — owned by Story 1.3.
- **CI workflow changes** (Windows-latest runner, drop multi-version matrix, `pytest-cov` wiring, fix lint script mutation) — owned by Story 1.3. Six CI/pytest findings were deferred to Story 1.3 (see [deferred-work.md](deferred-work.md)). Leave `.github/workflows/ci.yml`, `Makefile`, `devtools/lint.py`, and the `[tool.pytest.ini_options]` block untouched.
- **`templates/` directory, Jinja2 template, vendored JS assets** — owned by Epic 3 Story 3.10.
- **Runtime dependencies (click, osmnx, rasterio, networkx, shapely, jinja2, platformdirs)** — `dependencies = []` stays empty. Click arrives in Story 1.5.
- **Shared error hierarchy, error subclasses** — `errors.py` is a placeholder here; Story 1.4 fills it.
- **Dataclass definitions** — `models.py` is a placeholder here; Epics 2–3 fill it.

### Target module layout after this story

```
src/steeproute/
├── __init__.py                 # one-line docstring only (AC #5)
├── py.typed                    # (kept from Story 1.1)
├── cli/
│   ├── __init__.py             # one-line docstring
│   ├── query.py                # main() → 0 stub (AC #3)
│   └── setup.py                # main() → 0 stub (AC #3)
├── pipeline/
│   └── __init__.py             # one-line docstring
├── solver/
│   └── __init__.py             # one-line docstring
├── validator.py                # placeholder (one-line docstring)
├── cache.py                    # placeholder
├── output.py                   # placeholder
├── progress.py                 # placeholder
├── errors.py                   # placeholder
├── models.py                   # placeholder
└── provenance.py               # placeholder
```

Final (post-Epic-5) layout is in [architecture.md:776](../planning-artifacts/architecture.md:776). This story contributes the skeletal frame; subsequent stories populate each module.

### Architecture conventions the placeholders must already honor

From [architecture.md:701](../planning-artifacts/architecture.md:701) (Python code conventions) and [architecture.md:763](../planning-artifacts/architecture.md:763) (Key anti-patterns). Even one-line placeholders must follow:

| Convention | Applied to this story |
|---|---|
| Absolute imports only — no `from .x import y` | The CLI stubs import nothing; future dev must use `from steeproute.cli._shared import ...` not `from ._shared import ...`. |
| No top-level side effects in importable modules | The `print()` in CLI stubs is inside `main()`, so it executes only on invocation — not at import. ✅ |
| Module docstrings: one short line stating the module's role | Every `__init__.py` and flat-module placeholder gets exactly this. |
| `__all__` only for deliberately-restricted export sets | None of the new files need `__all__`. Do not add it reflexively. |
| `_` prefix for module-internal names | N/A for stubs; relevant once real code lands. |
| `logger = logging.getLogger(__name__)` in modules that log | N/A — placeholders don't log. |

### CLI stub shape (copy-paste target)

`src/steeproute/cli/query.py`:
```python
"""steeproute query CLI entry point (stages 8–9 + solver; wired in later epics)."""


def main() -> int:
    print("steeproute (query CLI) — stub; full implementation lands in Epics 2–4")
    return 0
```

`src/steeproute/cli/setup.py`:
```python
"""steeproute-setup data-preparation CLI entry point (stages 1–7; wired in Epic 2)."""


def main() -> int:
    print("steeproute-setup (data preparation CLI) — stub; full implementation lands in Epic 2")
    return 0
```

The console-script launcher generated by hatch/uv for `[project.scripts] steeproute = "steeproute.cli.query:main"` does `sys.exit(main())` internally, so `return 0` correctly produces process exit code 0. No `sys.exit()` call inside `main()` is needed (or wanted — Story 1.4 will route exit-code policy through `run_entry_point`, which treats returned ints as exit codes).

### `pyproject.toml` edit — surgical diff

Current (lines 56–58 of `pyproject.toml`):
```toml
[project.scripts]
# Add script entry points here:
steeproute = "steeproute:main"
```

Target:
```toml
[project.scripts]
# Add script entry points here:
steeproute = "steeproute.cli.query:main"
steeproute-setup = "steeproute.cli.setup:main"
```

Do not touch `[build-system]`, `[tool.hatch.build.targets.wheel]` (still `packages = ["src/steeproute"]` — correct, the sub-packages are discovered automatically by hatch), `[tool.basedpyright]`, `[tool.ruff]`, `[tool.pytest.ini_options]`, `[dependency-groups]`, or anything else.

### Why the script retargeting rebuilds cleanly

`uv sync` notices `pyproject.toml` changed → rebuilds the wheel via hatchling → reinstalls the package into `.venv/` → regenerates both console-script launchers. No manual wheel-build, no `pip install -e` needed. On Windows, the launchers are `.exe` files in `.venv/Scripts/`.

### Verification commands (Windows bash — copy-paste runnable)

```bash
# After writing all files and editing pyproject.toml:
uv sync
uv run steeproute && echo "query exit=$?"
uv run steeproute-setup && echo "setup exit=$?"
uv run pytest
uv run python devtools/lint.py

# Sanity checks:
test ! -f main.py && echo "main.py deleted: OK"
test ! -f src/steeproute/steeproute.py && echo "template residue deleted: OK"
ls src/steeproute/         # expect: __init__.py, py.typed, cli/, pipeline/, solver/, and 7 flat .py files
ls src/steeproute/cli/     # expect: __init__.py, query.py, setup.py
ls src/steeproute/pipeline/ # expect: __init__.py
ls src/steeproute/solver/  # expect: __init__.py
grep -n '\[project.scripts\]' pyproject.toml  # confirm still exactly one [project.scripts] table
```

### Project Structure Notes

- Pre-story tree in `src/steeproute/`: `__init__.py` (with `__all__` + wildcard import), `py.typed`, `steeproute.py` (stub `main()`). Post-story: as shown in the target layout above — `steeproute.py` gone, `__init__.py` cleaned, 10 new files created.
- Conflicts with target layout: none. The story's AC-enumerated outputs are a strict subset of the Architecture full project tree at [architecture.md:776](../planning-artifacts/architecture.md:776); missing pieces (`cli/_shared.py`, `templates/`, per-layer `tests/` structure, CI workflow customizations) are explicitly owned by later stories.
- `cli/` directory intentionally omits `_shared.py` for this story — the epic AC lists exactly 3 files in `cli/` for Story 1.2. Creating an empty `_shared.py` pre-emptively would violate the "don't add features beyond what the task requires" guidance and complicate Story 1.4's review.

### Testing standards summary

This story has no unit/integration/e2e tests of its own — it's a scaffolding operation. Verification is by hand via Task 6:

1. `uv run steeproute` and `uv run steeproute-setup` each print their stub and exit 0.
2. `uv run pytest` still green (template placeholder test intact).
3. `uv run python devtools/lint.py` still green (ruff + format + codespell + basedpyright).

Story 1.3 establishes the three-layer test structure + CI gates. Story 1.7 lands the first real CLI smoke tests (subprocess-invoking `--help` and `--version` assertions).

### References

- [Epic 1 Story 1.2 AC](../planning-artifacts/epics.md:248) — source of AC #1–#7, lines 248–260.
- [Architecture Category 1: Module & package structure](../planning-artifacts/architecture.md:159) — sub-package vs. flat-module rationale, layout diagram, lines 159–201.
- [Architecture Category 2: CLI framework + two-binary structure](../planning-artifacts/architecture.md:203) — `[project.scripts]` shape, `main` function style, lines 203–232.
- [Architecture Category 3a: Pipeline pure-function stages](../planning-artifacts/architecture.md:234) — informs `pipeline/__init__.py` role (orchestrator), lines 234–258.
- [Architecture Python code conventions](../planning-artifacts/architecture.md:701) — absolute imports, no top-level side effects, module docstring discipline, `__all__` policy, lines 701–726.
- [Architecture key anti-patterns to avoid](../planning-artifacts/architecture.md:763) — module-level mutable state, env-var reads, inline path strings, lines 763–772.
- [Architecture complete project tree](../planning-artifacts/architecture.md:776) — final-state target layout, lines 776–860.
- [PRD FR23 steeproute-setup separate CLI](../planning-artifacts/prd.md:510) — motivation for the two-binary structure.
- [Previous story 1.1 Completion Notes](1-1-scaffold-project-via-simple-modern-uv-copier-template.md:215) — template residue that Story 1.2 cleans up (divergences #2 and #7).
- [Deferred work — Story 1.3 targets](deferred-work.md) — CI/pytest/Makefile/lint-script items explicitly NOT owned by this story.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11.

### Debug Log References

- `uv sync` → 17 packages installed in fresh `.venv/`; built `steeproute==0.0.1.dev3+ee57774` from source with new `[project.scripts]`.
- `uv run steeproute` → `steeproute (query CLI) - stub; full implementation lands in Epics 2-4` / exit=0.
- `uv run steeproute-setup` → `steeproute-setup (data preparation CLI) - stub; full implementation lands in Epic 2` / exit=0.
- `uv run pytest` → `1 passed in 0.19s` (`tests/test_placeholder.py` unchanged).
- `uv run python devtools/lint.py` → codespell clean; ruff check "All checks passed!"; ruff format "15 files left unchanged"; basedpyright "0 errors, 0 warnings, 0 notes" on 15 source files. Overall: exit=0.
- Final tree check: `src/steeproute/` contains `__init__.py`, `py.typed`, `cli/`, `pipeline/`, `solver/`, and 7 flat `.py` placeholders. `main.py` and `src/steeproute/steeproute.py` confirmed deleted.

### Completion Notes List

**Stub-message ASCII choice:** module docstrings and `print()` strings use plain ASCII hyphens and word "stub", not en-dashes or em-dashes. Rationale: (a) keeps codespell happy with no Unicode-handling edge cases, (b) matches the template's existing ASCII-only style, (c) avoids any platform-dependent encoding surprises when `uv run` launches the .exe shim on Windows. The story Dev Notes suggested en-dashes as examples; using hyphens is equivalent and lower-risk.

**AC walkthrough — evidence per criterion:**

1. AC #1 — Template residue removed: `git status` confirms `D main.py` and `D src/steeproute/steeproute.py`; `src/steeproute/__init__.py` rewritten to a single docstring line. ✅
2. AC #2 — Sub-packages created: `src/steeproute/cli/__init__.py`, `pipeline/__init__.py`, `solver/__init__.py` each contain exactly one docstring line, no `__all__`, no imports. ✅
3. AC #3 — CLI stubs: `src/steeproute/cli/query.py` and `src/steeproute/cli/setup.py` each define `main() -> int` returning 0 after a `print()`; no click/argparse/`_shared.py` imports. ✅
4. AC #4 — 7 flat placeholders (`validator`, `cache`, `output`, `progress`, `errors`, `models`, `provenance`): each a single-line module docstring, no code. ✅
5. AC #5 — Top-level `__init__.py`: `"""steeproute — trail route optimization package."""` and nothing else. ✅
6. AC #6 — `[project.scripts]` in pyproject.toml now:
   ```toml
   steeproute = "steeproute.cli.query:main"
   steeproute-setup = "steeproute.cli.setup:main"
   ```
   (Old `steeproute = "steeproute:main"` removed.) No other pyproject sections touched. ✅
7. AC #7 — `uv sync` succeeded; `uv run steeproute` and `uv run steeproute-setup` each printed stub and exited 0 (see Debug Log). ✅
8. AC #8 — `uv run pytest` → 1 passed, no regressions. ✅
9. AC #9 — `uv run python devtools/lint.py` → ruff/codespell/basedpyright all clean. ✅

**Nothing deferred to a future story from this execution.** The Story 1.3 backlog already carries 6 CI/pytest findings inherited from Story 1.1 (see [deferred-work.md](deferred-work.md)); no new items added.

### File List

**New:**
- `src/steeproute/cli/__init__.py`
- `src/steeproute/cli/query.py`
- `src/steeproute/cli/setup.py`
- `src/steeproute/pipeline/__init__.py`
- `src/steeproute/solver/__init__.py`
- `src/steeproute/validator.py`
- `src/steeproute/cache.py`
- `src/steeproute/output.py`
- `src/steeproute/progress.py`
- `src/steeproute/errors.py`
- `src/steeproute/models.py`
- `src/steeproute/provenance.py`

**Modified:**
- `src/steeproute/__init__.py` (wildcard import and `__all__` stub removed; replaced with one-line docstring)
- `pyproject.toml` (`[project.scripts]` retargeted to `steeproute.cli.query:main`; added `steeproute-setup` entry; no other sections changed)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status transitions: `backlog` → `ready-for-dev` → `in-progress` → `review`)
- `_bmad-output/implementation-artifacts/1-2-establish-steeproute-package-structure-and-entry-points.md` (this file — status, checkboxes, Dev Agent Record, File List, Change Log)

**Deleted:**
- `main.py` (repo-root `uv init` stub; disposable per story AC #1)
- `src/steeproute/steeproute.py` (simple-modern-uv template stub; disposable per story AC #1)

### Change Log

| Date | Change | Commit |
|---|---|---|
| 2026-04-24 | Establish `src/steeproute/` package structure: create `cli/`, `pipeline/`, `solver/` sub-packages; add 7 flat-module placeholders (`validator`, `cache`, `output`, `progress`, `errors`, `models`, `provenance`); wire `steeproute` and `steeproute-setup` console-script entry points; delete template residue (`main.py`, `steeproute.py`). Story 1.2 ready for review. | `d997d3d` |
