# steeproute — Agent Notes

## Planning artifacts (BMAD)

Planning lives under `_bmad-output/planning-artifacts/`:
- `prd.md` is the authoritative source on scope/requirements/v1 in-out
  decisions (supersedes the brainstorming doc where they conflict).
- `epics.md` was slimmed 2026-07-04: completed epics' full detail was moved
  byte-for-byte into `archive/epics-completed-1-12.md` (named so it doesn't
  match the `*epic*.md` glob other BMAD workflows scan). `epics.md` keeps
  only the Overview, Requirements Inventory, a one-line-per-epic summary table
  for completed epics (pointing at the archive), and full detail for
  active/future epics. When an epic finishes and is marked `done` in
  `sprint-status.yaml`, fold its section into the archive the same way —
  batch this opportunistically, not per-story.
- BMAD story files (from `create-story`) should guide implementation, not
  pre-implement it in prose. Stick to `template.md`'s actual sections (Story /
  AC / Tasks / Dev Notes / References / Dev Agent Record) — don't invent
  sections like "Implementation sketches," "Anti-patterns," or "Verification
  commands." ACs belong at outcome altitude (~5-10 outcomes for a 4-bullet
  epic AC), not 15 micro-specs that pre-write the code. Target ~100-200 lines;
  Dev Notes should point at architecture/PRD sources, not duplicate them.
- Keep `sprint-status.yaml` lean: a `last_updated` date and a terse
  `# (was epic-N) <name>` tag per renumbered epic is enough. Narrative history
  goes in the correct-course proposal doc, not here.
- FR/requirements lists should stay at high altitude: exclude standard CLI
  hygiene (arg validation, `--quiet`, stderr/stdout separation), dev-only
  mechanisms (regression suites, quality benchmarking, CI checks), and
  project-deliverable artifacts (README contents, gallery examples) — those
  belong in quality-commitments/scope sections, not FRs. Merge FRs that
  describe one capability from multiple angles; don't split one capability
  into per-edge-case FRs.

## Solver / GRASP

- Regression goldens (Epic 8) must pin the **full explicit param set** they
  run with, never lean on CLI defaults, so future default re-tuning can't
  silently invalidate a golden. `params_hash` is computed over the pinned set
  a fixture specifies, not the whole `SolverParams` dataclass. A golden is a
  regression only if its output changes without a deliberate
  `update-regression` + commit rationale. Don't pre-design how future solver
  features will arrive (new param vs. separate layer) — only commit to: a
  future change must not silently alter existing goldens, and new behavior
  gets its own regression coverage.
- Good manual-run / demo / gallery params (CLI defaults are too low for
  quality output): `--iter-budget 200000 --stagnation-iters 10000
  --difficulty-cap T4 --elevation-deadband 1`. `--j-max 0` for `--n >= 2` on a
  reasonably large box returns fully segment-disjoint routes (only affects
  routes 2+, not route 1). Do **not** lower `--theta` below its 0.20 default —
  the route-level average-slope floor is intentionally limiting; that's the
  whole point of the tool.

## Scale target

Performance/optimization work must measure **end-to-end wall-clock — setup
phase, query phase, and total — before and after**, on a representative area
(e.g. r20), not just the phase being optimized. A micro-benchmark of the
optimized function alone is necessary but not sufficient: an optimization can
shift cost between phases, and isolated benchmarks can hide real stalls (e.g.
timing a merge function in isolation missed ~5-7s per-round
`_build_adjacency` stalls in the real parallel-GRASP run). Never extrapolate a
sub-component micro-benchmark to the cost of the whole operation — measure the
operation the way it's actually run (the real CLI invocation), not a proxy.

## Dev environment (Windows, uv-managed)

- Type-check: `uv run basedpyright <files>` (not `pyright` — not installed).
- Test: `uv run pytest <targets>`. Don't mix test files from
  `tests/unit/...` and `tests/integration/...` in one invocation — the wrong
  `conftest.py` gets imported and collection fails. Run per-directory.
- Full suite (`uv run --no-sync pytest --cov`, ~842 tests) takes ~4m15s and is
  fully offline (benchmarks/live/slow are marker-deselected). If a run takes
  much longer, suspect a test hitting the real network rather than
  environmental noise — check `--durations=25` for an outlier.
- `uv run` can flake on this machine (Windows, corporate TLS-inspecting
  network, Python from the Windows Store) after any new commit or
  `pyproject.toml` edit, because `uv-dynamic-versioning` bakes the commit hash
  into the version, invalidating the cached editable build. Symptom: ~43
  failures concentrated in `tests/e2e/test_cli_smoke.py` (its subprocesses
  spawn their own `uv run`, which retriggers the rebuild) or a
  `invalid peer certificate: UnknownIssuer` TLS error. Fix once per
  stale-build occurrence with `uv sync --native-tls` (uses the OS cert store).
  Do NOT use `uv sync --reinstall-package steeproute` (deletes `.venv` first).

## Git

When committing via the Bash tool (not the PowerShell tool) on this Windows
machine, don't use PowerShell here-string syntax (`git commit -m @'...'@`) —
bash parses `@'` as a literal `@` plus a quoted string, leaking a stray `@` as
the commit subject. Use multiple `-m` flags or a real bash heredoc
(`-F - <<'EOF'`).
