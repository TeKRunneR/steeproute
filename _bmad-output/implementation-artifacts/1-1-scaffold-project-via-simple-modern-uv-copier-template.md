# Story 1.1: Scaffold project via simple-modern-uv Copier template

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want to apply the `simple-modern-uv` Copier template over the current `uv init` scaffold,
So that the project gets a modern Python foundation (uv, ruff, BasedPyright, pytest + pytest-sugar, GH Actions CI) without hand-building boilerplate.

## Acceptance Criteria

1. The Copier template is applied with: `copier copy gh:jlevy/simple-modern-uv .` answering `project_name=steeproute`, `author=Yann Fontana`, `python_version=3.13`, accepting template defaults for any other prompts not explicitly specified.
2. `.copier-answers.yml` exists at repo root and is tracked in git (not listed in `.gitignore`) so future `copier update` runs are reproducible.
3. Template-generated artifacts exist: `pyproject.toml` (template-shape, `name = "steeproute"`, with ruff + BasedPyright tool tables), template `README.md`, `.github/workflows/ci.yml`, `.github/workflows/publish.yml` (left inert — not deleted, not edited), and a `tests/` directory with the template's placeholder test.
4. Preserved untouched (byte-identical contents): `_bmad/`, `_bmad-output/`, `.claude/`, and `.git/` metadata. Any existing commits (if the branch has any) remain reachable; the `master` branch and any remotes are unchanged.
5. Disposable pre-Copier files are replaced by the template's versions (overwrite is acceptable): `main.py` (repo-root `uv init` stub), the empty `README.md`, and the bmad-test-stub `pyproject.toml`. Repo-root `main.py` is NOT kept after this story (Story 1.2 removes it if the template leaves it; this story only requires that the template's own output replaces the stub pyproject/README).
6. `uv sync` runs to completion from a clean state and produces a working `.venv/` with all template-declared dev dependencies resolved. `uv.lock` is generated and committed.
7. `uv run pytest` executes successfully (exit 0) on the template's placeholder test.
8. The repo-root folder name on disk is NOT required to change (stays `bmad-test/` physically); only the Python package/project name inside `pyproject.toml` is `steeproute`. The user will rename the folder manually when convenient.
9. All changes produced by this story are committed in one atomic commit titled `chore: apply simple-modern-uv Copier template (Story 1.1)` (or equivalent), so that `_bmad-output/` and the Copier-generated changes land together and the repository has an initial commit if it previously had none.

## Tasks / Subtasks

- [ ] **Task 1: Pre-flight checks** (AC: #4, #5)
  - [ ] Verify the repo root is `C:\Users\yfontana\Code\bmad-test` and `git status` is clean of un-related uncommitted work other than the known scaffold files (`.claude/`, `.gitignore`, `.python-version`, `README.md`, `_bmad/`, `_bmad-output/`, `main.py`, `pyproject.toml`, `docs/`).
  - [ ] Confirm `copier` is installed (if not: `uv tool install copier` or `pipx install copier`). Note: Copier is a dev-time tool, not a project dependency — do NOT add it to `pyproject.toml`.
  - [ ] Confirm network access to `github.com` (Copier fetches the template over HTTPS).
  - [ ] Snapshot the contents of `_bmad/`, `_bmad-output/`, `.claude/`, `docs/` paths (e.g., `git status`/listing) for post-apply comparison.

- [ ] **Task 2: Apply the Copier template** (AC: #1, #2, #3)
  - [ ] Run from repo root: `copier copy gh:jlevy/simple-modern-uv .`
  - [ ] Answer prompts:
    - `project_name` → `steeproute`
    - `author` (or `author_name` / similar) → `Yann Fontana`
    - `author_email` → `yann.fontana@hardis-group.com`
    - Python version → `3.13`
    - For any prompt not listed here, accept the template's default (press Enter). If a prompt is genuinely ambiguous (e.g., `github_user`, `package_description`, license choice), pick the most conservative/obvious value (`yfontana`, a one-line description, `MIT` if asked) rather than abandoning the run.
  - [ ] Resolve any file-conflict prompts by letting Copier overwrite the disposable files (`main.py`, `README.md`, `pyproject.toml`). Refuse overwrites for anything in `_bmad/`, `_bmad-output/`, `.claude/`, `.gitignore` (if the template tries to replace it, keep the existing one and hand-merge if needed).
  - [ ] Verify `.copier-answers.yml` was generated at repo root.

- [ ] **Task 3: Post-apply verification of preserved paths** (AC: #4)
  - [ ] Re-check `_bmad/`, `_bmad-output/`, `.claude/` against the pre-apply snapshot — contents MUST be byte-identical.
  - [ ] If any preserved path was modified, revert it (`git checkout --` for tracked files, or restore from snapshot for untracked) and note in Completion Notes.
  - [ ] Confirm `.copier-answers.yml` is NOT listed in `.gitignore`. If the template's `.gitignore` excludes it, remove that line.

- [ ] **Task 4: Install and validate** (AC: #6, #7)
  - [ ] From a clean state (`rm -rf .venv uv.lock` if they exist from earlier experimentation), run `uv sync` and confirm exit 0.
  - [ ] Confirm `uv.lock` is generated at repo root and will be tracked in git (not in `.gitignore`).
  - [ ] Run `uv run pytest` — the template's placeholder test(s) must pass (exit 0).
  - [ ] If `uv run ruff check` and `uv run basedpyright` are template-provided commands, run them too as a sanity check. Any template-level zero-finding baseline is fine; any findings against the template's own output are a template bug, not this story's concern — note and move on.

- [ ] **Task 5: Single atomic commit** (AC: #9)
  - [ ] `git add` all new/modified files — but DOUBLE-CHECK that `_bmad/`, `_bmad-output/`, and `.claude/` are included (they are currently untracked per `git status`; this story is the first opportunity to commit them).
  - [ ] Use `git status` to confirm no unexpected deletions (especially: no files from `_bmad/`, `_bmad-output/`, `.claude/` should be missing).
  - [ ] Commit with message: `chore: apply simple-modern-uv Copier template (Story 1.1)`.
  - [ ] Do NOT push. Leave branch `master` local. (No remote configured at time of writing; if one exists, this story does not push.)

- [ ] **Task 6: Self-check the acceptance criteria** (AC: all)
  - [ ] Walk through ACs #1–#9 one by one, confirming each with a concrete artifact (file path, command output, git log entry). Capture the evidence in the Completion Notes List.

## Dev Notes

### Architecture & template facts (authoritative)

- **Template source:** `gh:jlevy/simple-modern-uv` (Joshua Levy, Copier template). See Architecture §"Selected Starter: simple-modern-uv" (`_bmad-output/planning-artifacts/architecture.md` lines 100–154). It provides: ruff (lint + format, black-compatible), BasedPyright (type-check — the template migrated from mypy recently; do NOT substitute mypy), pytest + pytest-sugar, GH Actions CI + publish (publish left inert), uv native build backend (hatchling under the hood), dynamic versioning from git tags (inert without tags — stays `0.1.0`).
- **Python version:** 3.13, already pinned via `.python-version`. Do not change it. If the template asks for Python version, answer `3.13`.
- **Selected partly as a learning exercise** in the Copier workflow itself; do not second-guess the choice or propose alternatives (`copier-uv`, `cookiecutter-*`, plain `uv init --package`). These were evaluated and rejected — see Architecture §"Starter Options Considered".
- **Out of scope for this story:** the `src/steeproute/` package tree, `[project.scripts]` entries, `cli/` / `pipeline/` / `solver/` sub-packages, flat modules, `tests/unit/` / `tests/integration/` / `tests/e2e/` three-layer split, CI customizations beyond what the template ships. Those all belong to Stories 1.2, 1.3, and onward. Do not preemptively create them here — subsequent stories own that work and depend on this story's output being the vanilla template.

### Critical preservation list (do NOT touch)

The repo currently contains BMAD workflow artifacts that MUST survive this story untouched. Copier should never rewrite these paths because the template has no files under them, but verify anyway:

- `_bmad/` — BMAD skill/workflow state
- `_bmad-output/` — all planning artifacts (PRD, epics, architecture, implementation-artifacts folder where this story file lives)
- `.claude/` — Claude Code config + skills
- `.git/` — git metadata; commits (if any) and branch refs must remain intact

### Disposable files (template will replace them)

- `main.py` at repo root — the `uv init` stub. Template will overwrite (or leave no replacement); Story 1.2 removes it definitively.
- `README.md` at repo root — currently empty. Template generates its own.
- `pyproject.toml` at repo root — currently has `name = "bmad-test"`. Template generates a new one with `name = "steeproute"`.
- `docs/` — currently empty directory at repo root; harmless either way. Architecture §"Template files retained vs. dropped" says to drop any template docs-site scaffolding (mkdocs, etc.) since we only use `docs/examples/` (populated in Epic 5). If the template generates a `docs/` site, leave it for now; Story 5.3 or 5.4 will clean up.

### Git state nuance — the branch has no commits yet

`git log` on `master` currently returns "does not have any commits yet". The epic AC phrase "git history as the things worth preserving" is aspirational — there is no history to preserve at the moment, but whatever commits exist after this story runs should include `_bmad/`, `_bmad-output/`, and `.claude/` alongside the template output (Task 5). If in the meantime the user has created commits, preserve them; do not reset or rewrite history.

### The template prompt surface (what simple-modern-uv will ask)

The exact prompt set evolves with the template; do not hardcode expectations. Walk through the prompts interactively, applying:

- Values fixed by Architecture / PRD: `project_name=steeproute`, `author=Yann Fontana`, `author_email=yann.fontana@hardis-group.com`, Python version `3.13`.
- For anything else, accept the default. If a default is not offered, pick the least-opinionated value (e.g., `MIT` for license, an empty/one-line project description, `yfontana` for GitHub user).
- Do NOT fabricate values like a PyPI token, Codecov token, or release-automation URL. These are inert for this project; empty/default is correct.

### Things that will look wrong but aren't

- `.github/workflows/publish.yml` — inert, never triggers. Architecture §"Template files retained vs. dropped" says explicitly: **leave as-is**. Deleting would be template drift and complicate future `copier update`.
- Dynamic versioning showing `0.1.0` or `0.0.0` — correct, we have no git tags.
- Template's `tests/` layout may be flat (single `tests/test_*.py` file) rather than the three-layer `unit/integration/e2e/` split. That's fine — Story 1.3 restructures.
- Template's CI workflow may not match our final job matrix (e.g., Windows-latest per NFR7 primary platform). Story 1.3 customizes it.
- Template may or may not install `pytest-cov`, `hypothesis`, `click`, `osmnx`, `rasterio`, `networkx`, `shapely`, `jinja2`, `platformdirs`. These are project-level dependencies added in later stories (2.x, 3.x). Do NOT add them here.

### Key anti-patterns for this story (from Architecture §"Key anti-patterns to avoid" + story-specific)

- Do NOT hand-edit the template-generated `pyproject.toml` in this story beyond what Copier wrote. Any tweaks (second `[project.scripts]` entry, adding dependencies) belong to Story 1.2+.
- Do NOT pre-create `src/steeproute/` — Story 1.2 owns that structure.
- Do NOT add pre-commit hooks, mkdocs, or any other tooling the template doesn't ship with. The architecture decision is explicit: simple-modern-uv is the lightest option on purpose.
- Do NOT substitute mypy for BasedPyright. Architecture §Category "Selected Starter" locks in BasedPyright.
- Do NOT substitute black for ruff-format or flake8 for ruff. Architecture locks in ruff for both linting and formatting.
- Do NOT commit secrets — if Copier asks for a PyPI token or similar, leave blank.

### Verification commands (copy-paste runnable)

```bash
# Pre-apply snapshot
ls -la _bmad _bmad-output .claude docs
git status --short
git log --oneline 2>&1 | head -5

# Apply
copier copy gh:jlevy/simple-modern-uv .

# Post-apply verification
ls -la .copier-answers.yml pyproject.toml README.md .github/workflows/
cat .copier-answers.yml
grep -E 'name|version|python' pyproject.toml

# Preserved-paths check
ls -la _bmad _bmad-output .claude docs

# Install + test
uv sync
uv run pytest

# Optional sanity
uv run ruff check || true
uv run basedpyright || true

# Commit
git add -A
git status
git commit -m "chore: apply simple-modern-uv Copier template (Story 1.1)"
```

### Project Structure Notes

- Pre-apply repo tree (current state):
  ```
  bmad-test/
  ├── .claude/                # preserve
  ├── .git/                   # preserve (no commits yet)
  ├── .gitignore              # may be overwritten; hand-merge if so
  ├── .python-version         # 3.13 — preserve
  ├── README.md               # disposable (empty)
  ├── _bmad/                  # preserve
  ├── _bmad-output/           # preserve (contains planning artifacts + this story file)
  ├── docs/                   # leave as-is
  ├── main.py                 # disposable
  └── pyproject.toml          # disposable (bmad-test stub)
  ```
- Post-apply target: above preserved paths intact, plus template-generated `pyproject.toml`, `README.md`, `.github/workflows/` (ci.yml + publish.yml), `.copier-answers.yml`, `tests/` placeholder, `uv.lock`.
- Final target structure (all of Epic 1 combined) is in Architecture §"Complete project tree" (`_bmad-output/planning-artifacts/architecture.md` lines 776–860). This story contributes ONLY the template layer.

### Testing standards summary

This story has no unit/integration/e2e tests of its own — it's a scaffolding operation. Verification is by hand via the AC walkthrough in Task 6 and the commands above. The template's placeholder `pytest` test passing is the testing-infrastructure smoke test (AC #7).

Story 1.3 establishes the three-layer test structure + CI gates. Story 1.7 lands the first real CLI smoke tests.

### References

- [Epic 1 overview + Story 1.1 AC](_bmad-output/planning-artifacts/epics.md:229) — epic file lines 229–247
- [Architecture starter-template selection](_bmad-output/planning-artifacts/architecture.md:100) — Selected Starter section, lines 100–153
- [Architecture implementation handoff](_bmad-output/planning-artifacts/architecture.md:1132) — first-story guidance, lines 1132–1149
- [Architecture project tree](_bmad-output/planning-artifacts/architecture.md:776) — full target layout, lines 776–860
- [Architecture template-files retained-vs-dropped](_bmad-output/planning-artifacts/architecture.md:868) — lines 868–876
- [PRD NFR7 Portability — Windows primary](_bmad-output/planning-artifacts/prd.md:545) — Windows is the primary-tested platform
- Template upstream: https://github.com/jlevy/simple-modern-uv

## Dev Agent Record

### Agent Model Used

{{agent_model_name_version}}

### Debug Log References

### Completion Notes List

### File List
