# Deferred Work

Items deferred during code review that are owned by a future story.

---

## Deferred from: code review of 1-1-scaffold-project-via-simple-modern-uv-copier-template.md (2026-04-24)

**Target story for all items below: Story 1.3** (customize CI workflow and establish three-layer test structure)

| # | Finding | File | Detail |
|---|---------|------|--------|
| 1 | Python 3.14 in CI matrix may not exist yet | `.github/workflows/ci.yml` | `python-version: ["3.11", "3.12", "3.13", "3.14"]` — 3.14 is pre-release; CI may fail when first triggered. Story 1.3 trims the matrix to match NFR7 (Windows primary). |
| 2 | `Makefile` Windows compatibility unverified | `Makefile` | Template's Makefile uses Unix shell; NFR7 designates Windows as primary platform. Story 1.3 either adapts or deletes it. |
| 3 | `norecursedirs = []` broad pytest collection | `pyproject.toml` | Template default collects everything; will pick up non-test files. Story 1.3 scopes to `tests/unit tests/integration tests/e2e`. |
| 4 | `python_files = ["*.py"]` too broad for test discovery | `pyproject.toml` | Matches all `.py` files, not just `test_*.py`. Story 1.3 tightens test discovery globs. |
| 5 | `codespell --write-changes` mutates working tree in CI | `devtools/lint.py` | Auto-fixing in CI can cause dirty-tree failures on subsequent steps. Story 1.3 customizes the lint script for CI vs local use. |
| 6 | `ruff check --fix` mutates working tree in CI | `devtools/lint.py` | Same issue as above. Story 1.3 separates check-only (CI) from fix (local) invocations. |
