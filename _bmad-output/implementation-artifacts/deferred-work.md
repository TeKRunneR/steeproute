# Deferred Work

Items deferred during code review that are owned by a future story.

---

## Deferred from: code review of 1-1-scaffold-project-via-simple-modern-uv-copier-template.md (2026-04-24)

**Target story for all items below: Story 1.3** (customize CI workflow and establish three-layer test structure)

**Status: ✅ All 6 items resolved in Story 1.3 (2026-04-25).**

| # | Finding | File | Detail | Resolution |
|---|---------|------|--------|------------|
| 1 | Python 3.14 in CI matrix may not exist yet | `.github/workflows/ci.yml` | `python-version: ["3.11", "3.12", "3.13", "3.14"]` — 3.14 is pre-release; CI may fail when first triggered. Story 1.3 trims the matrix to match NFR7 (Windows primary). | ✅ Story 1.3 — matrix trimmed to `["3.13"]` only; classifier `Python :: 3.14` also dropped from `pyproject.toml`. |
| 2 | `Makefile` Windows compatibility unverified | `Makefile` | Template's Makefile uses Unix shell; NFR7 designates Windows as primary platform. Story 1.3 either adapts or deletes it. | ✅ Story 1.3 — `Makefile` deleted (`git rm`). Windows devs use direct `uv` commands. |
| 3 | `norecursedirs = []` broad pytest collection | `pyproject.toml` | Template default collects everything; will pick up non-test files. Story 1.3 scopes to `tests/unit tests/integration tests/e2e`. | ✅ Story 1.3 — `norecursedirs` populated with `_bmad`, `_bmad-output`, `.claude`, `.venv`, `.git`, `node_modules`, `dist`, `.pytest_cache`, `__pycache__`. `testpaths` also tightened to `["tests"]` (was `["src", "tests"]`). |
| 4 | `python_files = ["*.py"]` too broad for test discovery | `pyproject.toml` | Matches all `.py` files, not just `test_*.py`. Story 1.3 tightens test discovery globs. | ✅ Story 1.3 — `python_files = ["test_*.py"]`. |
| 5 | `codespell --write-changes` mutates working tree in CI | `devtools/lint.py` | Auto-fixing in CI can cause dirty-tree failures on subsequent steps. Story 1.3 customizes the lint script for CI vs local use. | ✅ Story 1.3 — CI no longer invokes `devtools/lint.py`. CI runs explicit check-only commands (`uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`). `lint.py` retained as a local-dev fix-mode helper. |
| 6 | `ruff check --fix` mutates working tree in CI | `devtools/lint.py` | Same issue as above. Story 1.3 separates check-only (CI) from fix (local) invocations. | ✅ Story 1.3 — same resolution as #5 (CI uses explicit check-only commands). |

---

## Deferred from: lightweight review of 1-3-customize-ci-workflow-and-establish-three-layer-test-structure.md (2026-04-25)

**Target story: Story 5.5** (final CI threshold tightening and Linux best-effort job)

| # | Finding | File | Detail |
|---|---------|------|--------|
| 1 | Coverage `omit` list missing | `pyproject.toml` `[tool.coverage.run]` | Story 1.3 set `source = ["src/steeproute"]` and `fail_under = 0` as scaffolding. Architecture §Category 11e excludes `src/steeproute/templates/` and `src/steeproute/cli/` (beyond smoke tests) from coverage targets. When Story 5.5 raises `fail_under` to 80%/95%, it must add `omit = ["src/steeproute/templates/*", "src/steeproute/cli/*"]` to `[tool.coverage.run]` so the threshold is computed against pure-logic modules only. Threshold-raise without omit-list = false-fail on cli/templates. |
