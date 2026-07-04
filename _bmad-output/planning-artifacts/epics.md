---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-create-stories', 'step-04-final-validation']
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/research/technical-steeproute-performance-tuning-research-2026-07-02.md
---

# bmad-test - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for bmad-test (product name: `steeproute`), decomposing the requirements from the PRD and Architecture into implementable stories.

## Requirements Inventory

### Functional Requirements

**Area Specification & Invocation**

- FR1: User can specify a search area via center point and radius.
- FR2: System rejects search areas exceeding the configured area-size cap with a descriptive error.

**Route Search & Solver**

- FR3: User can configure the route-level average-slope floor — minimum `(D+ + D−) / length` for a returned route as a whole.
- FR3b: User can configure the climb-detection slope threshold — minimum running-average uphill slope (`d_plus / length`) for a segment to qualify as a climb (distinct from FR3's route-level floor).
- FR4: User can configure the SAC difficulty ceiling for eligible route segments.
- FR5: User can configure the short-connector length threshold below which a linking segment is exempt from the once-per-route reuse limit — short connectors may be reused and traversed in both directions, while every other segment may be used at most once regardless of direction.
- FR6: User can configure the minimum ground-length threshold for a segment to count as a climb.
- FR7: User can configure the pairwise segment-overlap ceiling for top-N distinctness.
- FR8: User can configure the target result count.
- FR9: User can configure the policy for untagged OSM trails (include or exclude).
- FR10: System searches for routes maximizing total vertical effort (D+ + D−) subject to the configured constraints, with returned routes strictly contained within the specified search area.
- FR11: System returns up to N distinct routes, where distinctness is defined by a pairwise segment-overlap ceiling.
- FR12: System gracefully returns fewer than N routes with a clear explanation when the distinctness constraint cannot be satisfied.

**Progress & Interrupt Handling**

- FR13: System emits progress information during the search — at minimum: iteration count, best-so-far objective, elapsed time, rough ETA.
- FR14: System responds to manual interrupt (Ctrl-C) by writing best-so-far results to disk and exiting with a dedicated interrupt exit code.

**Result Output**

- FR15: System produces one static HTML report per returned route.
- FR16: System produces one machine-readable JSON sidecar per returned route alongside the HTML report.
- FR17: HTML reports include an interactive map showing the route polyline on an OSM-derived basemap.
- FR18: HTML reports include an elevation profile with gradient-color coding along the route.
- FR19: Each report records metadata including length, D+, D−, average gradient, all solver parameters used, seed, DEM version, OSM extract date, code commit hash, and convergence status.
- FR20: User can configure the output directory.
- FR21: System uses a stable, predictable filename pattern for output artifacts across runs.
- FR22: System prints a run summary to stdout upon completion including parameters, routes returned vs. N requested, validation-failure count, and wall-clock total.

**Data Preparation**

- FR23: The project provides a separate CLI, `steeproute-setup`, for preparing OSM and DEM data for a specified area. It accepts the same area-specification flags as `steeproute`.
- FR24: `steeproute` fails fast with a descriptive error if the requested query area is not covered by prepared data, instructing the user to run `steeproute-setup` first.
- FR25: Preprocessed data is locally cached and reused across runs; the cache is invalidated when any input affecting output changes (DEM version, OSM extract date, area boundaries, or relevant solver parameters).

**Result Validation**

- FR26: System validates every returned route against all declared constraints (slope floor, difficulty cap, edge-reuse limit, Jaccard distinctness, graph membership) before presenting it to the user.
- FR27: When a returned route fails constraint validation, the affected HTML report displays a prominent VALIDATION FAILED banner identifying the violated constraint(s).
- FR28: When any route fails constraint validation, the system exits with a dedicated non-zero code while still writing all results (including failed ones) to disk.

**Scripting & Reproducibility**

- FR29: User can supply an explicit random seed that, together with identical inputs and code version, produces identical output route edge-sets; the seed used is recorded in each HTML report's metadata and in each JSON sidecar.
- FR30: System uses distinct exit codes for success, validation failure, pre-execution error, and user interrupt.

**Practical-Route Constraints (opt-in; promoted from future-ideas 2026-06-25)**

- FR31: User can constrain a returned route's start endpoint to a road/trail junction (a node incident to both an admitted road/connector and a trail) via an opt-in flag. Default off.
- FR32: User can configure a direction-aware maximum descent slope — a route may descend a segment only if its windowed uphill-measured slope stays at or below the threshold, while the segment remains eligible as a climb. Opt-in; default off.

**Setup Progress Reporting (post-v1 increment 2026-07-02)**

- FR33: `steeproute-setup` emits progress during preparation: each pipeline stage announces itself when it starts and reports elapsed time when it completes, and long-running stages emit within-stage progress (e.g. DEM tile fetch reports `tile i/N`) so the user can always distinguish "working" from "stuck". Follows the existing stream discipline (progress on stdout, suppressed by `--quiet`, errors/warnings on stderr).

### NonFunctional Requirements

**Performance**

- NFR1: Default-configured queries (Grenoble box, 10 km radius, default parameters) complete within ~10 minutes wall-clock on commodity laptop hardware. Design target, not an SLO — budget-breaking allowed, silent budget-breaking not.
- NFR2: Typical query runs comfortably on a commodity 16 GB laptop. Operational-region size is the primary memory-pressure lever.

**Reliability**

- NFR3: Ctrl-C during a run preserves best-so-far output and leaves the cache in a valid, reusable state.
- NFR4: Same `--seed` + same code version + same prepared data → identical output route edge-sets. Bit-exact floating-point reproducibility is explicitly not guaranteed.
- NFR5: Cache writes are atomic; an interrupted preprocessing or search run does not leave the cache corrupted.

**Integration**

- NFR6: When an OSM or DEM source is temporarily unavailable at setup time, `steeproute-setup` exits with a clear, actionable error rather than hanging or silently producing partial data.

**Portability**

- NFR7: Windows is the primary development/test platform (v1 quality contract).
- NFR8: Linux is expected to work but is not actively tested; macOS is not a v1 commitment.

**Explicitly Not Applicable** (documented for intentionality):

- Security (N=1, no auth, no runtime network I/O, no sensitive data).
- Scalability (single user, single machine).
- Accessibility (CLI + private author-only HTML reports; reopens if Phase 3 web app ships).

### Additional Requirements

Derived from the Architecture document — requirements that shape epic and story structure beyond the PRD body.

**Starter Template (impacts Epic 1 Story 1):**

- The project applies the **`simple-modern-uv`** Copier template (`gh:jlevy/simple-modern-uv`) as the foundational scaffold. Architecture's "first implementation story" per the handoff section. The existing `uv init` stub (`main.py`, stub README, minimal `pyproject.toml`) is disposable and overwritten by the template. Git history, `_bmad/`, `_bmad-output/`, and `.claude/` are preserved.
- The template provides: Python 3.13 + uv, `src/<package>/` layout, `[project.scripts]` entries, ruff (lint + format), BasedPyright (type-check), pytest + pytest-sugar, GitHub Actions CI scaffolding. Publishing workflow is inert (not deleted).

**Package & Entry-Point Structure:**

- Single `steeproute` Python package under `src/` with sub-packages for `cli/`, `pipeline/`, `solver/` and flat modules for the rest (per Architecture §Category 1, §Project Structure).
- Two console-script entry points in `pyproject.toml`: `steeproute` → `steeproute.cli.query:main`, `steeproute-setup` → `steeproute.cli.setup:main`.

**Pipeline & Caching Boundaries:**

- Pipeline is pure-function staged (9 stages). `steeproute-setup` runs stages 1–7 (parameter-independent, cached); `steeproute` runs stages 8–9 at query time (parameter-dependent, not cached).
- Cache directory resolved via `platformdirs.user_cache_dir("steeproute")`; overridable via `--cache-dir`. Contains an `index.json` coverage summary and per-area entries keyed on a SHA256 over `(canonicalized area bounds, untagged-trails-policy, DEM version, pipeline source content hash)`.
- `manifest.json` is the atomic-commit signal per entry; writes use `.tmp/` + `os.replace()`.

**CLI Flags Introduced by Architecture** (supplement the PRD flag surface):

- `--stagnation-iters N` — early-termination window for GRASP (Cat 5e).
- `--cache-dir PATH` — override cache root.
- `--force-refresh` — rebuild cache entry despite key match.
- `--osm-age-warn-days N` — OSM-staleness warning threshold (default 90 days).
- `--dem-version TAG` — explicit DEM version tag for cache keying.
- `--dem-path PATH` — DEM files location for `steeproute-setup`.

**HTML Report Asset Strategy:**

- Leaflet (pinned v1.9.4) and Chart.js (pinned v4.4.0) shipped as **vendored** assets inside `src/steeproute/templates/assets/` and **inlined** into each HTML report at render time (no external CDN at runtime per PRD; self-contained files).

**Testing Strategy & CI Gates** (Architecture §Category 11):

- Three test layers from day 1: `tests/unit/`, `tests/integration/`, `tests/e2e/`.
- Fixture approach: programmatic toy-graph generator (primary), handcrafted 5–8 node oracle fixtures (verifies brute-force enumerator itself), pinned real-data regression fixtures (2–3 Grenoble-area cutouts).
- CI gates: GRASP/exhaustive ratio ≥ 0.80 on seeded toy; zero-tolerance regression-golden match; constraint invariants + metamorphic tests + oracle correctness pass-required; coverage 80% overall / 95% on pure-logic modules (`pipeline/`, `solver/distinctness.py`, `validator.py`, `cache.py`).
- Eight metamorphic invariants from PRD Appendix A(b) must be implemented as tests.
- Regression golden update workflow: `uv run update-regression [--fixture NAME | --all]` with explicit commit-message rationale.
- Property-based tests (`hypothesis`) on geometric/elevation primitives.

**README Gallery:**

- README includes 3–5 pre-computed example reports (map screenshots + elevation profile PNGs) covering distinct regions. Cut-order item: reducible to minimum 3 if time-constrained.

**Known-Limitations Documentation:**

- README contains a first-class "Known Limitations" section covering data-level error (DEM/polyline-drift cliff-bias) and solver-level error (GRASP heuristic non-optimality).

**Performance Instrumentation & Baseline (research 2026-07-02 — Phases 0–2; post-v1 increment):**

Derived from `research/technical-steeproute-performance-tuning-research-2026-07-02.md` and the decisions recorded in it. These drive Epic 11 stories; they are dev-tooling/measurement work, not PRD FRs.

- T1: Stage-level timing seams — one reusable decorator/context-manager around each setup pipeline stage; the same seam serves FR33 progress output and profiling attribution.
- T2: Verify osmnx HTTP cache is enabled and persistent under `platformdirs`; fix if not.
- T3: Deliverable: per-stage wall-clock breakdown of a real setup run on a Grenoble-scale area.
- T4: py-spy flamegraphs of a realistic GRASP run (~200k iter-budget, Grenoble-scale area); py-spy is native on Windows, Scalene/WSL2 is fallback only if Python-vs-native attribution is ambiguous.
- T5: Deliverable: ranked bottleneck list with Python-vs-native attribution answering the research's decision question (scoring math vs. networkx calls vs. loop skeleton) — the input that scopes Phase 3+.
- T6: Dedicated `tests/benchmarks/` pytest-benchmark suite, excluded from the default run (marker/testpath, same exclusion pattern as `live`/`slow`); never mixed into functional tests.
- T7: Throughput metric: seconds per 1k GRASP iterations at fixed seed/params on the `grenoble_small` fixture; setup-stage wall-clock as the second metric family.
- T8: Baselines pinned (`--benchmark-autosave` / `--benchmark-compare`) before any optimization work lands.

Constraints: Phases 0–2 are behavior-preserving — regression goldens stay green untouched; the only observable change is FR33's new setup output. Phase order is non-negotiable (no optimization before flamegraphs). Phases 3–4 are explicitly out of scope until T5's bottleneck list exists.

### UX Design Requirements

Not applicable — CLI-only project, no UI. UX Design spec deliberately omitted per PRD project-type configuration.

### FR Coverage Map

| FR | Primary epic | Notes |
|---|---|---|
| FR1 (area via center/radius) | Epic 1 | Area flag surface + custom click type |
| FR2 (area-cap rejection) | Epic 1 | Validation at CLI layer; `BadCLIArgError` path |
| FR3 (route-level slope floor θ) | Epic 1 (flag) / Epic 3 (initial) / Epic 4 (corrected to route-level) | Flag in `cli/_shared.py`; route-level `(D+ + D−)/length` floor enforced at solve + validate |
| FR3b (climb-detection slope) | Epic 4 (flag) / Epic 3 (climb detection) | New `--min-climb-slope`; running-avg `d_plus/length` in `detect_climbs` |
| FR4 (difficulty cap SAC) | Epic 1 (flag) / Epic 3 (enforcement) | Enforced per-edge in validator + pipeline filter |
| FR5 (L_connector) | Epic 1 (flag) / Epic 3 (initial) / Epic 5 (undirected reuse + connector tolerance) | Undirected base-segment reuse limit; short connectors `< --l-connector` exempt and reusable |
| FR6 (min climb length) | Epic 1 (flag) / Epic 3 (enforcement) | Enforced by climb detection (pipeline stage 8) |
| FR7 (J_max pairwise overlap) | Epic 1 (flag) / Epic 3 (enforcement) | Enforced by TopNTracker |
| FR8 (N result count) | Epic 1 (flag) / Epic 3 (enforcement) | TopNTracker capacity |
| FR9 (untagged trails policy) | Epic 1 (flag) / Epic 2 (enforcement) | Enforced in pipeline stage 2 (trail filter) |
| FR10 (vertical-effort objective + strict containment) | Epic 3 | GRASP + climb-graph construction |
| FR11 (top-N distinctness) | Epic 3 | TopNTracker |
| FR12 (graceful degradation) | Epic 7 | Run summary messaging + distinctness-tracker output |
| FR13 (progress emission) | Epic 7 | `ProgressEvent` + throttled callback + CLI renderer |
| FR14 (Ctrl-C best-so-far) | Epic 7 | CLI try/except around `solver.run()`; `best_so_far` flush |
| FR15 (HTML per route) | Epic 3 | `output.py` + Jinja2 |
| FR16 (JSON sidecar) | Epic 3 | `output.py` |
| FR17 (Leaflet map) | Epic 3 | Vendored Leaflet 1.9.4, inlined in template |
| FR18 (gradient elevation profile) | Epic 3 | Vendored Chart.js 4.4.0, inlined in template |
| FR19 (report metadata) | Epic 3 | `output.py` + `provenance.py` + `models.py` |
| FR20 (--output-dir) | Epic 1 (flag) / Epic 3 (use) | Click option + render target |
| FR21 (stable filename pattern) | Epic 3 | `route-<i>.{html,json}` |
| FR22 (run summary on stdout) | Epic 7 | `cli/query.py` end-of-run block |
| FR23 (steeproute-setup CLI) | Epic 2 | Entry point + stages 1–7 orchestrator |
| FR24 (fail-fast unprepared area) | Epic 2 | `cache.py` coverage check + exit 2 |
| FR25 (local cache + invalidation) | Epic 2 | Cache architecture + key hashing |
| FR26 (runtime validation) | Epic 3 | `validator.py` |
| FR27 (validation-failure banner) | Epic 3 | `output.py` + `templates/route.html.j2` |
| FR28 (exit code + write-to-disk) | Epic 3 | Exit-code coupling from `ValidatedRouteSet` |
| FR29 (seed reproducibility) | Epic 3 | `numpy.random.Generator` threading + metadata surfacing |
| FR30 (distinct exit codes) | Epic 1 (scaffolding) / Epic 3 (code 1) / Epic 7 (code 130) | `run_entry_point` wrapper + feature-specific returns |
| FR31 (start-at-junction, opt-in) | Epic 10 | Junction annotation (Stage 9) + GRASP/oracle seed restriction + validator |
| FR32 (direction-aware descent cap, opt-in) | Epic 10 | Windowed descent metric + GRASP/oracle descent feasibility + validator |
| FR33 (setup progress) | Epic 11 | Stage seams shared with profiling instrumentation; stream discipline per Architecture Cat 8 |

**NFR coverage:**

- NFR1 (compute budget ≤10min design target): Epic 7 — time-budget termination, stagnation, progress reporting surfaces elapsed; Epic 11 makes the target measurable (benchmark baselines + per-stage timing); Epic 12 raises solver throughput against those baselines
- NFR2 (16 GB memory envelope): Epic 8 — validated during gallery generation; documented if notable
- NFR3 (Ctrl-C preserves output + cache valid): Epic 7
- NFR4 (seeded determinism, edge-set level): Epic 3
- NFR5 (atomic cache writes): Epic 2
- NFR6 (OSM/DEM actionable-error on source down): Epic 2
- NFR7 (Windows primary platform): Epic 1 — CI runs on Windows; all subsequent epics validated there
- NFR8 (Linux best-effort, macOS uncommitted): Epic 1 — CI may include Linux job; not gated

## Epic List

### Epic 1: Project Foundation & CLI Shell

Deliver a scaffolded, installable project with both CLIs responding to `--help` / `--version`, the full flag surface defined as click decorators, the three-layer test structure and CI gates operational, and `BadCLIArgError` paths producing exit code 2 on malformed arguments. Establishes the scaffolding + code-quality gates every subsequent epic builds on.

**FRs covered:** FR1, FR2 (CLI-level), FR20 (flag definition), FR30 (exit-code wrapper scaffolding); flag-surface definitions for FR3–FR9 (values parsed and validated at CLI; enforcement in Epics 2–3).

### Epic 2: Data Preparation & Caching

Deliver `steeproute-setup` end-to-end: user can run it on a Grenoble-area center/radius and get a prepared cache entry on disk (graph.pkl + manifest.json + bounds.geojson), atomic-write-safe under interruption, with OSM-age warning on stale entries and an actionable error when OSM/DEM sources are unavailable. `steeproute` (without solver) fails-fast with an actionable message when no prepared data covers the query area.

**FRs covered:** FR23, FR24, FR25.

### Epic 3: Query Pipeline, Solver, Validation & Report Rendering

Deliver Journey 1 happy path end-to-end. User runs `steeproute` on a prepared area and receives up to N validated HTML + JSON reports in the output directory. Seeded runs produce byte-identical edge-sets. Failed-validation routes render with prominent banners and the process exits 1 with all results still written to disk. Includes the full correctness-driving test stack — exhaustive enumerator oracle, GRASP-vs-exhaustive integration test, the 8 metamorphic invariants, property-based tests on primitives, TopNTracker + validator + output unit tests — because these tests are the implementation feedback loop for GRASP.

**FRs covered:** FR3–FR9 (enforcement), FR10, FR11, FR15–FR19, FR20 (usage), FR21, FR26, FR27, FR28, FR29, FR30 (exit code 1).

### Epic 4: Route-Level Slope-Floor Correction

Bring the average-slope floor in line with the PRD/architecture intent: `θ` becomes a route-level floor on `(D+ + D−)/length`, and a new `--min-climb-slope` flag carries the per-climb detection threshold. Removes the near-vacuous per-super-edge slope check from the solver, validator, and exhaustive oracle, fixes the route `avg_gradient` metric, and re-validates the metamorphic + CLI test suites. Sequenced ahead of Operational Robustness so graceful degradation (FR12) reasons about correct feasible-route counts. Inserted via correct-course 2026-06-03 (see `sprint-change-proposal-2026-06-03.md`).

**FRs covered:** FR3 (corrected to route-level), FR3b (new climb-detection flag).

### Epic 5: Undirected Segment-Reuse Semantics

Change the edge-reuse rule from directed (once per direction) to **undirected on the underlying base trail segment** (once per route regardless of direction), with **short connectors (`length_m < --l-connector`) exempt** — reusable and bidirectional as linking segments. Kills the degenerate out-and-back-chain routes observed in testing, where the solver banked `D+` uphill and `D−` down the same trail reversed. Realizes the originally-intended FR5 semantics (`--l-connector` as a reuse-exemption threshold, not a graph-pruning threshold) and keeps the solver, exhaustive oracle, and validator on one feasible set. Sequenced ahead of Operational Robustness so its graceful-degradation logic (FR12) reasons about correct feasible-route counts. Inserted via correct-course 2026-06-03 (see `sprint-change-proposal-2026-06-03-undirected-segment-reuse.md`).

**FRs covered:** FR5 (realized as undirected base-segment reuse + short-connector tolerance).

### Epic 6: Route-Discovery & Elevation-Consistency Fixes

Fix the stacked defects that prevented a known-good Grenoble loop from ever being discovered, plus the elevation metric/display inconsistency, before Operational Robustness. Junction-aware climb splitting and SAC cap-aware contraction make legitimate routes reachable; undirected Jaccard distinctness aligns FR11 with FR5; minor roads are admitted as connectors; and one canonical elevation profile (graph-Laplacian smoothing + deadband-as-transform) feeds solver, metric box, and plotted curve alike. The bugs were found only by manual real-area testing, so each ships with a regression test that fails on the pre-fix code and an explicit human-review checkpoint. Inserted via correct-course 2026-06-07 (see `sprint-change-proposal-2026-06-07-route-discovery.md`).

**FRs covered:** none new — realizes FR10 (route discovery) and FR11 (undirected distinctness) correctly and extends the FR9 trail-policy data surface (roads as connectors).

### Epic 7: Operational Robustness

Deliver Journeys 2 and 3. Long-running queries emit throttled progress lines; Ctrl-C preserves best-so-far top-N with an "interrupted" convergence flag and exits 130; sparse areas return fewer than N routes with a clear explanation rather than silently loosening distinctness; stagnation detection converges the solver early when no further improvement is occurring; the final run summary on stdout reports parameters, routes returned vs. N requested, validation-failure count, and wall-clock total. Turns the tool from "happy-path only" to "usable on realistic queries."

**FRs covered:** FR12, FR13, FR14, FR22, FR30 (exit code 130).

### Epic 8: Release Polish

Deliver the interview-ready state. Pinned regression golden fixtures on 2–3 real Grenoble-area cutouts lock in current-known-good behavior going forward, with a documented `uv run update-regression` workflow and commit-message discipline. The README presents a 3–5 region gallery (map screenshots + elevation profile PNGs + links to HTML files in `docs/examples/`), a first-class "Known Limitations" section covering DEM/cliff-bias and GRASP-non-optimality, and a quickstart for both CLIs. If any CI thresholds (coverage, GRASP/exhaustive ratio) were held lenient during earlier epics, they tighten to final committed values here.

**FRs covered:** none directly. Covers PRD success criteria: regression protection commitment (do-not-cut), portfolio credibility, 3–5 region gallery.

## Epic 1: Project Foundation & CLI Shell

Deliver a scaffolded, installable project with both CLIs responding to `--help` / `--version`, the full flag surface defined as click decorators, the three-layer test structure and CI gates operational, and `BadCLIArgError` paths producing exit code 2 on malformed arguments. Establishes the scaffolding + code-quality gates every subsequent epic builds on.

### Story 1.1: Scaffold project via simple-modern-uv Copier template

As a developer,
I want to apply the `simple-modern-uv` Copier template over the current scaffold,
So that the project gets a modern Python foundation (uv, ruff, BasedPyright, pytest + pytest-sugar, GH Actions CI) without hand-building boilerplate.

**Acceptance Criteria:**

**Given** the repo contains `_bmad/`, `_bmad-output/`, `.claude/`, and git history as the things worth preserving (`main.py`, `README.md`, and the `uv init`-generated `pyproject.toml` are disposable)
**When** I run `copier copy gh:jlevy/simple-modern-uv .` and answer its prompts (project_name=steeproute, author=Yann Fontana, Python 3.13)
**Then** the template generates `pyproject.toml`, `README.md`, `.github/workflows/` (ci.yml + publish.yml inert), ruff and BasedPyright config blocks, and a `.copier-answers.yml` tracked in git
**And** `_bmad/`, `_bmad-output/`, `.claude/`, and git history remain untouched
**And** `uv sync` succeeds and produces a working virtualenv
**And** `uv run pytest` passes the template's default test placeholder

### Story 1.2: Establish steeproute package structure and entry points

As a developer,
I want the `src/steeproute/` package scaffolded with its sub-packages (`cli/`, `pipeline/`, `solver/`) and flat-module placeholders, plus both console-script entry points wired in `pyproject.toml`,
So that `steeproute` and `steeproute-setup` are invokable commands and the module layout matches the Architecture Project Structure before any real logic lands.

**Acceptance Criteria:**

**Given** Story 1.1 has applied the Copier template
**When** I create `src/steeproute/` with sub-package directories (`cli/`, `pipeline/`, `solver/` — each with `__init__.py`) and flat-module placeholder files (`validator.py`, `cache.py`, `output.py`, `progress.py`, `errors.py`, `models.py`, `provenance.py` — each containing a one-line module docstring only), then add `[project.scripts]` entries mapping `steeproute` → `steeproute.cli.query:main` and `steeproute-setup` → `steeproute.cli.setup:main`, with placeholder `main` functions in `cli/query.py` and `cli/setup.py` that print a stub message and return 0
**Then** `uv sync` installs both console scripts
**And** `uv run steeproute` and `uv run steeproute-setup` each execute, print their stub, and return exit code 0
**And** the disposable `main.py` at repo root is removed

### Story 1.3: Customize CI workflow and establish three-layer test structure

As a developer,
I want `tests/unit/`, `tests/integration/`, `tests/e2e/` as separate layers each with its own `conftest.py`, plus a CI workflow that runs ruff + BasedPyright + pytest with coverage reporting on Windows,
So that every subsequent story can place tests in the right layer and the quality gates block regressions from day 1.

**Acceptance Criteria:**

**Given** Story 1.2 is complete
**When** I restructure `tests/` into `tests/unit/`, `tests/integration/`, `tests/e2e/` (each with an empty `conftest.py`) plus a top-level `tests/conftest.py`, and update `.github/workflows/ci.yml` to trigger on push + PR and run `uv sync`, `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, and `uv run pytest --cov=src/steeproute --cov-report=xml --cov-report=term`
**Then** the CI workflow runs on a `windows-latest` runner (primary platform per NFR7) and succeeds on a push of the current codebase
**And** `pyproject.toml` configures `pytest-cov` with `--cov-fail-under` scaffolding in place (threshold may be 0 at this point; tightens in Epic 8)
**And** `uv run ruff check` and `uv run basedpyright` pass on the current codebase with zero findings
**And** the template's legacy test file (if any) is moved into the appropriate layer or replaced with a layer-appropriate placeholder

### Story 1.4: Implement shared error hierarchy and run_entry_point wrapper

As a developer,
I want `errors.py` with the full `SteeprouteError` hierarchy and `cli/_shared.py::run_entry_point` wrapping both CLI `main` functions,
So that every subsequent story has a consistent mechanism for producing exit codes 0/1/2/130 and for surfacing `PreExecutionError` user messages on stderr.

**Acceptance Criteria:**

**Given** Stories 1.2 and 1.3 are complete
**When** I implement `errors.py` with `SteeprouteError` → `PreExecutionError` → (`BadCLIArgError`, `CacheNotFoundError`, `CacheCorruptedError`, `DataSourceUnavailableError`, `SolverError`), each carrying `user_message` (required) and `detail` (optional), and implement `cli/_shared.py::run_entry_point(main_fn)` that catches `PreExecutionError` → stderr `error: {user_message}\n` (plus `detail` on `--verbose`) + exit 2, catches `KeyboardInterrupt` → exit 130, and treats a returned int from `main_fn` as the exit code
**Then** both `cli/query.py::main` and `cli/setup.py::main` are wrapped in `run_entry_point`
**And** `tests/unit/test_errors.py` instantiates each `PreExecutionError` subclass and asserts `user_message` and `detail` round-trip
**And** a dedicated unit test of `run_entry_point` asserts the three exit-code paths (0 on return-0, 2 on `PreExecutionError`, 130 on `KeyboardInterrupt`) using a mocked `main_fn`

### Story 1.5: Define full click option decorator surface for both CLIs

As a developer,
I want every CLI flag defined once as a reusable click option decorator in `cli/_shared.py`, with each CLI stacking the decorators it needs,
So that `steeproute --help` and `steeproute-setup --help` produce complete, documented flag listings and there's zero flag definition duplication between the two CLIs.

**Acceptance Criteria:**

**Given** Stories 1.2–1.4 are complete
**When** I implement reusable click option decorators in `cli/_shared.py` for every flag the Architecture specifies — area (`--center` with a custom `LAT,LON` `ParamType`; `--radius`), constraints (`--theta`, `--difficulty-cap`, `--l-connector`, `--min-climb-ground-length`, `--j-max`, `--n`, `--area-cap`, `--untagged-trails`), solver (`--seed`, `--iter-budget`, `--time-budget`, `--stagnation-iters`, `--progress-interval`), output (`--output-dir`), shared meta (`--verbose`/`--quiet`, `--version`, `--cache-dir`), and setup-specific (`--force-refresh`, `--dem-version`, `--dem-path`, `--osm-age-warn-days`) — and stack the relevant ones on each CLI's click command
**Then** `uv run steeproute --help` lists every `steeproute`-relevant flag (area + constraints + solver + output + shared meta) with default values and one-line descriptions
**And** `uv run steeproute-setup --help` lists every `steeproute-setup`-relevant flag (area + shared meta + setup-specific; no solver flags)
**And** `uv run steeproute --version` and `uv run steeproute-setup --version` print a recognizable version string and exit 0
**And** each decorator is a single importable symbol — no duplication between `query.py` and `setup.py`

### Story 1.6: Validate area specification at CLI boundary (FR1, FR2)

As a user,
I want `steeproute` to reject malformed `--center` values and radii whose resulting area exceeds `--area-cap` at invocation time,
So that I get a clear error immediately rather than a confusing failure deep in the pipeline.

**Acceptance Criteria:**

**Given** Story 1.5 has defined `--center`, `--radius`, and `--area-cap` as click options and Story 1.4 has wired `run_entry_point`
**When** a user invokes `steeproute` with a malformed `--center` value (e.g. `abc,def`, missing comma, latitude outside [-90,90], longitude outside [-180,180]) or a radius whose `π·r²` exceeds the current `--area-cap` value
**Then** the CLI raises `BadCLIArgError` → `run_entry_point` catches it → process exits 2
**And** stderr shows `error: {reason}` naming the offending flag and the specific violation (e.g. `error: --radius 30 km produces ~2827 km², exceeds --area-cap of 500 km²`)
**And** with valid args (`--center 45.0716,6.1079 --radius 10`) the CLI proceeds to whatever its current `main` does (stub at this point; full logic lands in Epic 3)
**And** `tests/unit/test_area_parsing.py` covers happy path, three malformed-`--center` variants, and one area-cap-exceeded case

### Story 1.7: Write CLI smoke tests covering help, version, and exit-code paths

As a developer,
I want end-to-end smoke tests for both CLIs covering `--help`, `--version`, malformed args, and area-cap rejection,
So that every Epic 1 deliverable has coverage that runs in CI and any regression in the CLI surface is caught on the next commit.

**Acceptance Criteria:**

**Given** Stories 1.1–1.6 are complete
**When** I add `tests/e2e/test_cli_smoke.py` with subprocess-based tests that invoke the installed CLIs via `uv run`
**Then** `steeproute --help` and `steeproute-setup --help` each exit 0 and stdout contains every flag name defined in Story 1.5
**And** `steeproute --version` and `steeproute-setup --version` each exit 0 and print a version string
**And** `steeproute --center abc,def --radius 10` exits 2 and stderr starts with `error:`
**And** `steeproute --center 45.07,6.11 --radius 30` exits 2 and stderr mentions `--area-cap`
**And** the tests pass in CI on the Windows runner

## Epic 2: Data Preparation & Caching

Deliver `steeproute-setup` end-to-end: user can run it on a Grenoble-area center/radius and get a prepared cache entry on disk (graph.pkl + manifest.json + bounds.geojson), atomic-write-safe under interruption, with OSM-age warning on stale entries and an actionable error when OSM/DEM sources are unavailable. `steeproute` (without solver) fails-fast with an actionable message when no prepared data covers the query area.

**Test-data convention for this epic:** real-data fixtures at `tests/fixtures/grenoble_small/` (OSM GraphML + IGN RGE ALTI 5m DEM for a ~2 km radius Grenoble-area cutout) drive pipeline and E2E tests. Synthetic inputs remain for purely analytical properties (smoothing math, cache-key canonicalization) and unavoidable error-path simulations (network failures).

### Story 2.1: Implement pipeline stages 1–2 — OSM ingestion, trail filtering, and commit real-OSM test fixture

As a developer,
I want `pipeline/osm.py` to download OSM trail data for a given area and filter it by `sac_scale`, `highway` type, and untagged-trails policy, plus a committed real-OSM test fixture for downstream use,
So that downstream pipeline stages receive a clean `networkx.MultiDiGraph` containing only trails the user cares about, and subsequent pipeline + E2E tests have a realistic OSM graph to exercise against.

**Acceptance Criteria:**

**Given** Story 1.2 has placed `pipeline/osm.py` as a module placeholder
**When** I implement `osm_load(area) -> MultiDiGraph` using `osmnx` (stage 1) and `filter_trails(graph, untagged_policy, difficulty_cap) -> MultiDiGraph` (stage 2) as pure functions applying the edge-attribute contract (`sac_scale`, `highway`, `osm_way_id`, `geometry` as `shapely.LineString`), then commit `tests/fixtures/grenoble_small/osm_graph.graphml` captured via `osmnx.graph_from_point` for a ~2 km radius Grenoble-area point with a `README.md` documenting the exact fetch parameters and a `regenerate.py` script that reproduces the capture
**Then** `tests/unit/test_osm.py` loads the real fixture and covers: include-untagged vs. exclude-untagged policy (assert edge count difference), difficulty-cap filtering at each SAC scale boundary, and real-trail edge-cases surfaced by the fixture (e.g. edges with missing `highway`, unusual geometries)
**And** `tests/unit/test_osm.py` also includes a small number of crafted synthetic graphs for edge cases the fixture doesn't cover (e.g., a graph with a single untagged-only edge to verify the include-policy admits it, a graph with every `highway` type)
**And** `tests/integration/test_osm_live.py::test_live_osm_matches_fixture` (marked `@pytest.mark.live`) makes a real `osmnx.graph_from_point` call to the same area and asserts structural similarity with the fixture (node/edge counts within a tolerance band) — skipped in CI, enabled locally for osmnx/Overpass drift checks
**And** fixture size stays under 5 MB committed

### Story 2.2: Implement pipeline stages 3–4 — 2D polyline smoothing and resampling

As a developer,
I want `pipeline/smoothing.py` to smooth each edge's 2D polyline and resample it to a uniform vertex spacing,
So that downstream DEM sampling hits consistent, drift-dampened positions rather than raw OSM vertices (which contributes to the cliff-bias artifacts the PRD Known-Limitations section discusses).

**Acceptance Criteria:**

**Given** Story 2.1 produces a filtered `MultiDiGraph` with `geometry` on each edge and a committed real-OSM fixture
**When** I implement `smooth_polylines(graph) -> MultiDiGraph` (stage 3: moving-average smoothing on 2D coords per edge, preserving endpoints) and `resample_edges(graph, spacing_m=10.0) -> MultiDiGraph` (stage 4: replaces each edge's `geometry` with a polyline having ~10 m vertex spacing)
**Then** smoothing window size and resample spacing are module-scope named constants (not inline magic numbers per Architecture conventions)
**And** resampled edge endpoints exactly match input endpoints (topology preserved) — asserted against both synthetic and real-fixture inputs
**And** `tests/unit/test_smoothing.py` covers analytical correctness on synthetic polylines (straight line → unchanged by smoothing; noisy zigzag → smoother by L2-distance-from-straight-line baseline; uniform resampling spacing) AND runs stages 3–4 on the real OSM fixture asserting attribute-contract preservation across every fixture edge
**And** a `hypothesis` property-based test asserts that for any valid input polyline, the resampled output preserves the first and last coordinates exactly

### Story 2.3: Implement pipeline stage 5 — DEM elevation sampling and commit real-DEM test fixture

As a developer,
I want `pipeline/dem.py` to sample elevation from a local DEM GeoTIFF at every resampled vertex and attach the result as a `vertices_resampled` attribute on each edge, plus a committed real IGN DEM fixture covering the same area as the OSM fixture,
So that elevation data is in the graph for the downstream smoothing and metrics stages, and tests exercise real Alpine terrain characteristics rather than synthetic surfaces.

**Acceptance Criteria:**

**Given** Story 2.2 produces a graph with resampled edge geometries
**When** I implement `sample_elevation(graph, dem_path) -> MultiDiGraph` using `rasterio` that for each edge attaches `vertices_resampled: list[tuple[float, float, float]]` (lat, lon, elevation_m) with explicit CRS handling between the graph's WGS84 and the DEM's native CRS, and commit `tests/fixtures/grenoble_small/dem.tif` — an IGN RGE ALTI 5m extract matching the OSM fixture's geographic extent — with generation details in the fixtures README
**Then** `tests/unit/test_dem.py` samples the real fixture at known coordinates (trailheads, summit points verified against topo maps or the IGN web viewer) and asserts elevations are within expected ranges
**And** vertices outside the DEM raster coverage produce a clear `PreExecutionError` subclass naming the offending edge and DEM bounds — no silent NaN — exercised by a test positioning an edge deliberately outside the fixture's extent
**And** `tests/unit/test_dem.py` includes a CRS-transformation correctness test: either via a synthetic in-memory GeoTIFF with a non-WGS84 CRS (`rasterio.io.MemoryFile`) or by asserting sampling roundtrips correctly at fixture corner coordinates
**And** DEM fixture size stays under 5 MB committed

### Story 2.4: Implement pipeline stages 6–7 — elevation smoothing and per-edge metrics

As a developer,
I want `pipeline/smoothing.py::median_smooth_elevation` and `pipeline/climbs.py::compute_edge_metrics` to close out the setup-side pipeline,
So that each edge carries `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient` in addition to its smoothed elevation samples — the attribute contract downstream stages 8–9 and validation depend on.

**Acceptance Criteria:**

**Given** Story 2.3 produces a graph with `vertices_resampled` containing raw elevation samples
**When** I implement `median_smooth_elevation(graph, window=...) -> MultiDiGraph` (stage 6: moving-median on the elevation component of `vertices_resampled`) and `compute_edge_metrics(graph) -> MultiDiGraph` (stage 7: sets `length_m` as cumulative 2D polyline distance, `d_plus_m` as sum of positive elevation deltas, `d_minus_m` as sum of negative, `avg_gradient` as `(d_plus_m + d_minus_m) / length_m`)
**Then** window size is a module-scope named constant
**And** `tests/unit/test_climbs.py::test_metrics_*` covers analytical correctness on synthetic edges (flat → `d_plus_m = d_minus_m = 0`; known-slope uphill → metrics match analytical values to float tolerance; staircase profile → median smoothing reduces spikes)
**And** an integration-style test runs stages 6–7 on the real post-stage-5 fixture and asserts aggregate plausibility: no NaN, `d_plus_m ≥ 0` and `d_minus_m ≥ 0` for every edge, `avg_gradient` values within plausible Alpine range (most edges < 80% gradient)
**And** a `hypothesis` property-based test asserts `d_plus_m ≥ 0`, `d_minus_m ≥ 0`, `length_m > 0` for any non-degenerate synthetic input edge

### Story 2.5: Implement pipeline orchestrator and integration-test stages 1–7 end-to-end on real fixture

As a developer,
I want `pipeline/__init__.py::run_setup_stages(area, config) -> MultiDiGraph` wiring stages 1–7 in order, and an integration test running the full pipeline on the real Grenoble fixture,
So that `steeproute-setup` has a single entry point and the stages-1–7 pipeline is end-to-end-validated against real Alpine terrain before the CLI and cache stories land.

**Acceptance Criteria:**

**Given** Stories 2.1–2.4 are complete and real OSM + DEM fixtures are committed
**When** I implement `pipeline/__init__.py::run_setup_stages(area, config) -> MultiDiGraph` wiring OSM load → trail filter → smooth → resample → DEM sample → elevation smooth → metrics
**Then** `tests/integration/test_pipeline_end_to_end.py` runs the orchestrator against the real Grenoble fixture (OSM + DEM) and asserts: output-graph has the expected topology (node/edge counts in the range the filter + fixture predict), every edge has the full attribute contract populated (`geometry`, `vertices_resampled`, `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`, `highway`, `osm_way_id`), aggregate metrics sum to sensible totals (total `length_m` roughly matches the known real trail length in that area within ±10%)
**And** the orchestrator is pure — no I/O beyond the caller-provided `dem_path`, no global state, no `print` calls
**And** each stage returns a new graph per Architecture §Cat 3 function-signature convention (`def stage(input_graph, config) -> output_graph`)

### Story 2.6: Implement cache key hashing, manifest schema, and provenance helpers

As a developer,
I want `cache.py` to compute a canonical cache-key hash from area bounds + untagged policy + DEM version + pipeline source content hash, and `provenance.py` to resolve git commit hash + dirty flag + OSM extract date,
So that the cache-key composition and manifest schema are ready for the atomic-write story that follows.

**Acceptance Criteria:**

**Given** Stories 2.1–2.5 are complete
**When** I implement `cache.py::compute_cache_key(area, untagged_policy, dem_version, pipeline_content_hash) -> str` (16-hex SHA256 truncation over canonical JSON) with area canonicalization rounding lat/lon to 6 decimals and radius_km to 3 decimals, `cache.py::compute_pipeline_content_hash() -> str` hashing `src/steeproute/pipeline/**/*.py` + `src/steeproute/models.py`, and `provenance.py` helpers: `get_commit_short() -> str` (append `-dirty` suffix if working tree is modified), `iso8601_utc_now() -> str`
**Then** `tests/unit/test_cache_key.py` asserts: two areas differing only in 7th-decimal lat produce identical keys (canonicalization works); changing `untagged_policy` produces a different key; modifying a pipeline file changes the content hash; two back-to-back calls on an unchanged tree produce identical keys
**And** `tests/unit/test_provenance.py` asserts the dirty flag responds correctly to a tracked-file modification (uses a fixture git worktree — real git behavior preferred over mocking `subprocess`)
**And** the `manifest.json` schema from Architecture §Cat 4 is encoded as a `@dataclass` in `cache.py` (or `models.py`) with `schema_version: int = 1`

### Story 2.7: Implement atomic cache write, read, and index maintenance

As a developer,
I want `cache.py::write_entry(entry_data)`, `cache.py::read_entry(cache_key) -> PreparedData`, and `cache.py::rebuild_index()` implementing the `.tmp/` → `os.replace()` atomic pattern with `manifest.json` as the commit signal,
So that `steeproute-setup` can safely persist prepared pipeline output and a Ctrl-C mid-write cannot produce an entry that consumers mistake for valid.

**Acceptance Criteria:**

**Given** Story 2.6 has computed cache keys and provenance helpers, and Story 2.5 produces a real-fixture-derived graph usable as test input
**When** I implement the write + read + index flow per Architecture §Cat 4d using `platformdirs.user_cache_dir("steeproute")` as default root (overridable via `--cache-dir` plumbed from Epic 1 Story 1.5), plus a generic `write_json_atomic(path, obj)` helper used for manifest + index writes
**Then** `tests/integration/test_cache_roundtrip.py` writes the real-fixture-derived graph (output of Story 2.5's orchestrator) then reads it back, asserts byte-identical graph via pickle roundtrip, `manifest.json` validates against the schema, and `index.json` reflects the new entry
**And** `tests/integration/test_cache_atomic.py` simulates a mid-write abort (`monkeypatch` raising `KeyboardInterrupt` after `graph.pkl.tmp` is written but before directory rename) and asserts the entry's directory without `manifest.json` is ignored by subsequent `read_entry` / `rebuild_index` calls — no partial data surfaced
**And** a unit test of `rebuild_index()` asserts recovery from a missing or corrupted `index.json` by scanning `areas/*/manifest.json`
**And** all cache JSON writes route through `write_json_atomic` (no direct `open(...).write(...)` on JSON files — per Architecture key anti-patterns)

### Story 2.8: Wire steeproute-setup end-to-end with --force-refresh semantics on real fixture

As a user,
I want `steeproute-setup --center ... --radius ... --dem-path ...` to run the full stages 1–7 pipeline and write a cache entry on disk, skipping recomputation when a valid entry already exists for the composite cache key (unless `--force-refresh` is set),
So that I can prepare an area once and re-prepare only when I explicitly ask (e.g., after a DEM update).

**Acceptance Criteria:**

**Given** Stories 2.5–2.7 are complete and real OSM + DEM fixtures are committed
**When** I implement `cli/setup.py::main` that: parses flags via the Epic 1 Story 1.5 decorators, computes the cache key, checks for an existing valid entry, either skips (cache hit) or runs `run_setup_stages` + `write_entry` (cache miss or `--force-refresh`), and prints a summary (hit vs. miss, entry path, wall-clock time) to stdout
**Then** `tests/e2e/test_steeproute_setup.py` runs `uv run steeproute-setup --center <fixture point> --radius 2 --dem-path tests/fixtures/grenoble_small/dem.tif --cache-dir <tmp>` and asserts: a cache entry directory is created with `graph.pkl`, `manifest.json`, and `bounds.geojson`; the graph matches expectations from Story 2.5's integration test (same edge count in the same area); re-running on the same area produces a cache-hit (same key, fast completion, no re-computation); `--force-refresh` forces re-prepare even on hit
**And** the stdout summary contains the cache-key-hash truncation and the entry path
**And** a unit test asserts that a cache-key change (e.g., different `--untagged-trails`) produces a miss and a new entry rather than an overwrite

### Story 2.9: Handle source-unavailable errors and emit OSM-age warnings

As a user,
I want `steeproute-setup` to exit with a clear `error:` line and exit code 2 when the OSM or DEM source is unreachable, and to emit a stderr warning when a cache-hit entry's OSM extract date exceeds `--osm-age-warn-days`,
So that data-source issues are distinguishable from bugs and stale data is surfaced without blocking the happy path.

**Acceptance Criteria:**

**Given** Story 2.8 is complete
**When** a network or I/O failure occurs inside `osm_load` or `sample_elevation`
**Then** `steeproute-setup` raises `DataSourceUnavailableError` → `run_entry_point` maps to exit 2 with stderr starting `error: OSM source unreachable` or `error: DEM source unreachable` (identifying which)
**And** with `--verbose`, the wrapped original exception message appears as the `detail` line
**And** `tests/e2e/test_source_unavailable.py::test_missing_dem_path` exercises a real filesystem failure: `--dem-path <nonexistent.tif>` triggers `DataSourceUnavailableError` with exit 2 (no mocking — real `rasterio.RasterioIOError` propagated)
**And** `tests/e2e/test_source_unavailable.py::test_osm_network_failure` uses `monkeypatch` to raise `requests.ConnectionError` from `osmnx`, asserting the `DataSourceUnavailableError` path — this simulation is required because the test's purpose *is* to validate the simulated failure handling
**And** on a successful cache-hit in `steeproute-setup` or `steeproute` where `manifest.osm_extract_date` age exceeds the current `--osm-age-warn-days` threshold (default 90), a non-blocking `logging.warning(...)` line appears on stderr suggesting `--force-refresh` — process continues normally
**And** a unit test asserts the age-warning logic by constructing a manifest dated > 90 days ago

### Story 2.10: Implement query-side fail-fast on unprepared area (FR24)

As a user,
I want `steeproute` to exit 2 with an actionable error message pointing me at `steeproute-setup` when the query area isn't fully covered by any prepared cache entry,
So that I discover the missing prep immediately and know exactly what to run.

**Acceptance Criteria:**

**Given** Stories 2.7 and 2.8 are complete, so cache entries and their coverage bounds exist on disk
**When** I implement `cache.py::check_coverage(query_area) -> PreparedData` that reads `index.json` (or rebuilds it), computes strict `shapely.contains` for each indexed area against the query polygon (built from `--center/--radius`), picks the smallest containing area if multiple, and raises `CacheNotFoundError` with an actionable message if none contains — then wire it into `cli/query.py::main`
**Then** `tests/e2e/test_coverage_check.py` uses real `steeproute-setup` runs as fixtures: running `steeproute` against an area with no prior setup exits 2 with stderr like `error: No prepared cache covers this area. Run: steeproute-setup --center 45.07,6.11 --radius 10 --dem-path <your DEM>`
**And** running against an area partially covered (query pokes outside the prepared area) exits 2 with a message naming the nearest prepared area(s) and a suggested smaller radius
**And** running against an area fully contained by multiple entries picks the smallest one (minimum graph-load cost) without error
**And** these three scenarios use real cache entries produced by real `steeproute-setup` invocations in test setup (no mocked coverage data)

## Epic 3: Query Pipeline, Solver, Validation & Report Rendering

Deliver Journey 1 happy path end-to-end. User runs `steeproute` on a prepared area and receives up to N validated HTML + JSON reports in the output directory. Seeded runs produce byte-identical edge-sets. Failed-validation routes render with prominent banners and the process exits 1 with all results still written to disk. Includes the full correctness-driving test stack — exhaustive enumerator oracle, GRASP-vs-exhaustive integration test, the 8 metamorphic invariants, property-based tests on primitives, TopNTracker + validator + output unit tests — because these tests are the implementation feedback loop for GRASP.

**Test-data note:** real Grenoble fixture from Epic 2 drives pipeline and end-to-end tests. The GRASP-vs-exhaustive comparison (3.7) and metamorphic invariants (3.8) use small programmatic fixtures — the exhaustive oracle has exponential blowup and can't run on full real-area graphs. Real-data is used where it adds coverage; synthetic is used where it's mechanically necessary (oracle complexity, controlled-parameter-change invariants).

### Story 3.1: Core query-side data models

As a developer,
I want all query-side dataclasses defined in `models.py` (Edge, Climb, ContractedGraph, SolverParams, Solution, RouteMetrics, ConstraintViolation, RouteValidation, Route, PairwiseViolation, ValidatedRouteSet, ProvenanceInfo),
So that subsequent stories consume a stable data contract and the `@dataclass(frozen=True, slots=True)` discipline from Architecture conventions is applied consistently.

**Acceptance Criteria:**

**Given** Epic 2 is complete
**When** I implement all query-side dataclasses in `models.py` with `frozen=True, slots=True`, complete type hints using PEP 604 unions and built-in generics, and no `Any` outside explicit boundary comments
**Then** `tests/unit/test_models.py` asserts: instantiation round-trips every field; `frozen=True` prevents mutation (raises `FrozenInstanceError`); `slots=True` rejects new attribute assignment; equality works on value semantics
**And** no data shape in this epic is ever passed as a loose `dict` — Architecture Anti-pattern §"Python code conventions" explicitly forbids this

### Story 3.2: Pipeline stage 8 — climb detection

As a developer,
I want `pipeline/climbs.py::detect_climbs(graph, theta, min_climb_ground_length) -> list[Climb]` identifying contiguous edge-sequences meeting slope-floor + min-length,
So that the contracted-graph stage has canonical climbs to build super-edges from and FR3, FR6 have their enforcement home.

**Acceptance Criteria:**

**Given** Story 3.1 defines `Climb` and Epic 2 produces per-edge metrics
**When** I implement `detect_climbs(graph, theta, min_climb_ground_length) -> list[Climb]` traversing the graph to find maximal contiguous edge-sequences where running-average slope ≥ θ and cumulative ground length ≥ min
**Then** `tests/unit/test_climb_detection.py` uses synthetic graphs with hand-placed climbs to assert: uphill sequence of slope-0.25 edges totaling 500m → detected; 100m uphill below threshold → not detected; undulating terrain where running-average falls below θ → climb correctly terminated at that point; empty graph / no-qualifying-edges → empty list, no error
**And** an integration test runs `detect_climbs` on the real Grenoble fixture and asserts climb count is within an expected topo-verified range and total climb D+ is within ±10% of a manual count
**And** the function is pure (no input-graph mutation)

> **Latent gap closed by Epic 9 (Story 9.1):** the "maximal contiguous edge-sequences" guarantee above was not actually met — `detect_climbs` seeded by sorted node-id and extended forward only, so a mid-chain seed could orphan the upstream steep edge (output maximal-forward-from-seed, dependent on OSM node-id labeling). Story 9.1 makes detection genuinely maximal regardless of labeling (review finding #7).

### Story 3.3: Pipeline stage 9 — contracted climb-graph construction

As a developer,
I want `pipeline/graph.py::contract_climbs(base_graph, climbs, l_connector) -> ContractedGraph` building the solver's input graph — climbs as super-edges, connectors ≥ `l_connector` preserved, with back-mapping to underlying edges,
So that GRASP operates on the right abstraction and FR5 (L_connector) has its enforcement home.

**Acceptance Criteria:**

**Given** Story 3.2 produces a list of Climbs
**When** I implement `contract_climbs(base_graph, climbs, l_connector) -> ContractedGraph` creating super-edges per Climb with aggregated metrics, retaining connectors ≥ `l_connector`, dropping shorter ones, and maintaining a super-edge → base-edges back-mapping
**Then** `tests/unit/test_graph_contraction.py` asserts on synthetic graphs with known climbs: super-edge metrics sum to underlying edges' metrics; bidirectionality preserved; back-mapping round-trips; sub-`l_connector` connectors removed
**And** an integration test on the real Grenoble fixture post-stages 8–9 asserts: contracted graph has fewer edges than base; back-expansion of every super-edge totals the same aggregate D+ and length as the original
**And** a `hypothesis` property test asserts the back-mapping is injective (no base edge mapped to two super-edges) and contraction is pure

> **Superseded by Epic 5 (Story 5.1):** the drop-shorter-connectors behaviour above is no longer current. `contract_climbs` now retains **all** connectors and tags every contracted edge with an undirected `base_segment_id` + a `reusable` flag (`length_m < l_connector`); the once-per-route reuse limit is enforced **undirected** at solve/validate time (Story 5.2), with short connectors exempt. `--l-connector` is a reuse-exemption threshold, not a contraction-time drop threshold.

### Story 3.4: TopNTracker with Jaccard distinctness

As a developer,
I want `solver/distinctness.py::TopNTracker` — admits solutions, rejects duplicates per Jaccard ceiling, tracks total objective for stagnation — plus the pure `jaccard_distance` function it uses,
So that FR11 has a clean, testable home orthogonal to GRASP.

**Acceptance Criteria:**

**Given** Story 3.1 defines `Solution`
**When** I implement `TopNTracker(n, j_max)` with `consider(solution) -> bool`, `current_top() -> list[Solution]`, `total_objective() -> float`, plus pure `jaccard_distance(a, b) -> float` using canonical `(node_u, node_v, key)` edge ordering per Architecture §"Numerical and data discipline"
**Then** `tests/unit/test_distinctness.py` covers all four PRD structural cases: admission into empty tracker; rejection-by-worse; rejection-by-Jaccard; substitution (better + distinct replaces worst existing)
**And** `hypothesis` property tests: `jaccard_distance(a, b) == jaccard_distance(b, a)`; `jaccard_distance(a, a) == 0`; value always in `[0, 1]`; admission order-independent for sufficiently-distinct solutions
**And** the tracker holds no reference to mutable external state — given equivalent inputs from a fresh tracker, `consider(x)` produces equivalent states

### Story 3.5: Exhaustive enumerator oracle with its own correctness tests

As a developer,
I want a brute-force path enumerator in `tests/integration/exhaustive_oracle.py` with correctness tests on 2–3 handcrafted 5–8 node graphs where the optimum is known by inspection,
So that GRASP can be validated against ground truth on small instances and the oracle itself is trusted (addressing PRD Appendix A's "validating against an unvalidated oracle" concern).

**Acceptance Criteria:**

**Given** Stories 3.1–3.3 provide models and graph abstraction
**When** I implement `tests/integration/exhaustive_oracle.py::enumerate_best(graph, params, n)` brute-forcing all valid paths subject to slope floor, difficulty cap, edge-reuse limit, strict containment, then applying top-N distinctness post-hoc
**Then** `tests/integration/test_oracle_correctness.py` uses 2–3 handcrafted ContractedGraphs with 5–8 nodes — each with a comment block documenting the expected optimum route + D+ — and asserts the oracle finds it
**And** pathological cases handled: no-valid-route graph returns empty; graph smaller than N returns what exists
**And** the oracle lives under `tests/` (never exported by the main package)
**And** a run on a 5-node hand-graph completes in < 1 second

### Story 3.6: GRASP solver main loop

As a developer,
I want `solver/grasp.py::GraspSolver` with injected RNG, parameter snapshot, prepared `ContractedGraph`, and a readable `best_so_far` — driving construction + restart,
So that FR10, FR11, FR29 have an implementation and Epic 7 can layer progress/interrupt handling on top.

**Acceptance Criteria:**

**Given** Stories 3.1, 3.3, 3.4 are complete
**When** I implement `GraspSolver(graph, params, rng, progress_callback=None)` with `run() -> list[Solution]` driving construction (greedy-random with restricted candidate list, TopNTracker-admitted) terminating on iter-budget (time-budget + stagnation land in Epic 7), plus `best_so_far` property returning `tracker.current_top()`, plus `solver/anytime.py` with interrupt hooks (real handling wires in Epic 7)
**Then** `tests/unit/test_grasp_construction.py` asserts one iteration is deterministic given seeded RNG (two identical-seed runs → identical candidate sequences)
**And** `tests/integration/test_grasp_on_fixture.py` runs GRASP on the real Grenoble fixture's contracted graph and asserts: ≤ N routes returned; every route strictly contained in the query area (FR10); every route's non-connector edges have slope ≥ θ; every route's max edge difficulty ≤ difficulty_cap
**And** `tests/integration/test_grasp_reproducible.py` asserts two runs with `--seed 42` produce byte-identical edge-sets (FR29/NFR4)
**And** RNG is always explicit (`numpy.random.default_rng(seed)` — never `numpy.random.seed` ambient state — Architecture §Cat 5c)

### Story 3.7: GRASP-vs-exhaustive CI quality gate

As a developer,
I want a CI test that runs GRASP against the exhaustive oracle on seeded programmatic toy fixtures and fails if the quality ratio drops below threshold,
So that silent solver-quality regressions are caught automatically (Architecture §Cat 11c).

**Acceptance Criteria:**

**Given** Stories 3.5 (oracle) and 3.6 (GRASP) are complete
**When** I implement `tests/integration/test_solver_on_toy_graph.py::test_grasp_meets_quality_threshold` using a programmatic toy-ContractedGraph factory in `tests/integration/conftest.py` (~20–30 nodes, configurable density and terrain variance) with fixed seed and params
**Then** the test asserts `grasp_best.objective / exhaustive_best.objective >= QUALITY_THRESHOLD` with `QUALITY_THRESHOLD = 0.80` as a module-scope named constant labeled "initial target — tighten to 0.85–0.90 once baseline established"
**And** the test parameterizes across 3–5 toy-graph generator seeds to catch generator-bias
**And** total test time ≤ 60 seconds in CI (constrains toy-graph size)
**And** `pytest.skip`/`xfail` on this test is explicitly forbidden (Architecture §Cat 11c) — disabling requires an issue reference + commit-message rationale

### Story 3.8: Metamorphic invariants test suite (8 invariants from PRD Appendix A)

As a developer,
I want `tests/integration/test_metamorphic.py` covering all 8 metamorphic invariants,
So that logical bugs (inverted Jaccard, broken seed threading, wrong objective direction) that unit tests can miss are caught automatically.

**Acceptance Criteria:**

**Given** Stories 3.4, 3.6 are complete
**When** I implement all 8 invariant tests, each running GRASP twice with a controlled change and asserting expected monotonicity/equality on a small programmatic fixture:
- `test_relax_theta_objective_non_decreasing`
- `test_relax_j_max_objective_non_decreasing`
- `test_relax_difficulty_cap_objective_non_decreasing`
- `test_increase_iter_budget_objective_non_decreasing`
- `test_scale_elevation_objective_scales_proportionally`
- `test_adding_edge_objective_non_decreasing`
- `test_graph_isomorphism_objective_identical` (relabel node IDs → identical objective)
- `test_duplicate_seed_identical_result`
**Then** each test uses a small programmatic fixture (not the Grenoble fixture — too slow) with fixed base seed, and asserts include informative failure messages (`assert new_obj >= old_obj, f"Relaxing theta {old_theta}→{new_theta}: objective dropped {old_obj}→{new_obj}"`)
**And** all 8 tests run under 2 minutes total in CI
**And** none use `pytest.skip`/`xfail`

### Story 3.9: Runtime route validation (validator.py)

As a developer,
I want `validator.py` implementing per-route + set-level + orchestrator validation per Architecture §Cat 6,
So that FR26–28 are fulfilled: every route validated, failures carry structured violations, exit-code logic drives off the validated set.

**Acceptance Criteria:**

**Given** Stories 3.1, 3.3, 3.6 are complete
**When** I implement `validator.py::validate_route(route, graph, params) -> RouteValidation`, `validate_set(routes, params) -> list[PairwiseViolation]`, and orchestrator `validate(solutions, graph, params) -> ValidatedRouteSet` covering all constraints: per-route (slope floor ≥ θ on non-connector edges, difficulty cap per edge, edge-reuse limit `l_connector`, graph membership), set-level (pairwise Jaccard ≤ J_max)
**Then** `tests/unit/test_validator.py` has one test per constraint with crafted-violating + crafted-clean fixtures (PRD structural requirement), each asserting the right `constraint_id` and `numeric` observed vs. required
**And** an integration test runs the validator on real GRASP output from the Grenoble fixture and asserts every route passes (GRASP-produced routes should validate by construction — failure here would signal a solver bug)
**And** another integration test crafts a `Solution` deliberately violating one constraint (inserts an edge below θ) and asserts the violation is caught with correct metadata
**And** `validator.py` is pure (no I/O, no state)

### Story 3.10: HTML + JSON output rendering with vendored assets

As a developer,
I want `output.py::render(validated_set, params, provenance, convergence, output_dir)` producing self-contained HTML + JSON per route with Leaflet + Chart.js inlined from vendored assets,
So that FR15–21 and FR29 (seed recording) are fulfilled and reports are portable files with zero runtime CDN dependency.

**Acceptance Criteria:**

**Given** Stories 3.1, 3.9 and Epic 2 Story 2.7 (atomic writes) + 2.6 (provenance) are complete
**When** I implement `output.py::render(...)` using Jinja2 with `src/steeproute/templates/route.html.j2`, inlining vendored `leaflet-1.9.4.min.{js,css}` + `chart-4.4.0.min.js` from `src/steeproute/templates/assets/` as `<script>` / `<style>` blocks, writing `route-<i>.html` and `route-<i>.json` atomically
**Then** `tests/unit/test_output.py` invokes `render(...)` on a validated set built from real fixture output and asserts every metadata field from Architecture §Cat 9 appears in both HTML and JSON (PRD structural requirement): all solver params, provenance (steeproute_version, git_commit_short + -dirty, osm_extract_date, dem_version, pipeline_content_hash), convergence_status, route metrics, validation summary
**And** HTML self-containment: a grep asserts zero `src=` or `href=` references to external URLs
**And** validation-failure banner renders conditionally — present when `route.validation.passed=False` OR any `PairwiseViolation` references this route (Architecture §Cat 6b banner logic); absent otherwise
**And** filename pattern `route-<i>.{html,json}` for i in 1..N (FR21)
**And** mid-render `monkeypatch`-raised `KeyboardInterrupt` does not leave half-written files (atomic via Story 2.7's helper)
**And** vendored asset versions (`leaflet-1.9.4`, `chart-4.4.0`) are pinned constants surfaced in the metadata block

### Story 3.11: Wire query CLI end-to-end with validation-driven exit code

As a user,
I want `steeproute --center ... --radius ... --seed 42` on a prepared area to produce the full happy-path output — up to N validated reports with exit 0, or reports-with-banners + exit 1 if any fail —
So that Journey 1 works end-to-end and FR28, FR30 (codes 0 and 1) are fulfilled.

**Acceptance Criteria:**

**Given** Stories 3.1–3.10 and all of Epics 1–2 are complete
**When** I implement `cli/query.py::main` wiring: `cache.check_coverage` + `cache.read_entry` → pipeline stages 8–9 → `GraspSolver.run()` → `validator.validate(...)` → `output.render(...)` → compute exit code (0 if all pass, 1 if any `RouteValidation.passed=False` OR any `PairwiseViolation`) and return it; outputs written before exit-code computation per Architecture §Cat 6c
**Then** `tests/e2e/test_journey_1_happy_path.py` pre-prepares a fixture cache via real `uv run steeproute-setup`, then runs `uv run steeproute --center <fixture area> --radius 2 --seed 42 --output-dir <tmp> --cache-dir <tmp>`, asserts exit 0 + N HTML + N JSON files exist with the correct filename pattern + each HTML parses as valid HTML with map + profile sections present
**And** `tests/e2e/test_seeded_reproducibility.py` runs the same command twice with `--seed 42` and asserts byte-identical JSON across runs (FR29/NFR4 verified end-to-end)
**And** `tests/e2e/test_validation_failure_path.py` uses `monkeypatch` to inject a fake solver output that includes a deliberately-invalid Solution (e.g., references a non-existent edge), asserts exit 1, reports still on disk, HTML contains `VALIDATION FAILED` banner
**And** Epic 7 is responsible for real progress UI and interrupt handling; this epic's CLI uses a stub no-op progress callback

## Epic 4: Route-Level Slope-Floor Correction

Bring the average-slope floor in line with the PRD/architecture intent. `θ` (`--theta`) becomes a **route-level** floor on `(D+ + D−)/length`; a new `--min-climb-slope` flag carries the **per-climb** detection threshold (the role `θ` plays today in `detect_climbs`). The near-vacuous per-super-edge slope check is removed from the GRASP RCL, the validator, and the exhaustive oracle; route-level feasibility is enforced at solution finalization and re-checked by the validator. The route `avg_gradient` metric is corrected to `(D+ + D−)/length`. Sequenced ahead of Operational Robustness so its graceful-degradation logic (FR12) reasons about correct feasible-route counts. Inserted via correct-course 2026-06-03 (see `sprint-change-proposal-2026-06-03.md`).

**FRs covered:** FR3 (corrected to route-level enforcement), FR3b (new climb-detection flag).

**Compatibility note:** defaults preserve current numeric behaviour (`--theta 0.20`, `--min-climb-slope 0.20`); the change is in *where* `θ` binds. The route-level default may warrant empirical retuning — tracked as an open item, not a blocker.

### Story 4.1: Split θ — introduce --min-climb-slope and route-level semantics

As a user,
I want `--theta` to mean the route-level average-slope floor and a new `--min-climb-slope` flag to carry the climb-detection threshold,
So that the two distinct concepts are independently configurable and `--theta` matches its documented (FR3) route-level intent.

**Acceptance Criteria:**

**Given** Epic 3 is complete and `θ` currently drives both climb detection and the (near-vacuous) per-super-edge check
**When** I add `min_climb_slope` to `SolverParams`, add a `--min-climb-slope` click option (default 0.20) with finiteness/`>= 0` validation in `cli/_shared.py`, reword `--theta` help to "Route-level average-slope floor, (D+ + D−)/length", thread `min_climb_slope` through `cli/query.py`, and rename `detect_climbs`'s slope argument from `theta` to `min_climb_slope`
**Then** `steeproute --help` lists both `--theta` and `--min-climb-slope` with correct defaults and descriptions
**And** climb-detection behaviour is unchanged at the default (`detect_climbs` fed `min_climb_slope`), verified by the existing Story 3.2 tests re-pointed to the new parameter
**And** `--min-climb-slope` rejection paths (non-finite, negative) exit 2 via `BadCLIArgError`, covered by unit + CLI smoke tests

### Story 4.2: Route-level slope enforcement in solver, oracle, and validator (+ metric fix)

As a developer,
I want the binding slope constraint to be the whole-route average `(D+ + D−)/length ≥ θ`, enforced consistently by the solver, the exhaustive oracle, and the validator,
So that every returned route is genuinely steep on average (FR3/FR26) rather than steep only on its individual climbs.

**Acceptance Criteria:**

**Given** Story 4.1 has split the parameters
**When** I remove the per-super-edge `avg_gradient < theta` filter from `solver/grasp.py::_build_rcl`, add a route-level feasibility gate at solution finalization in `GraspSolver.run()` (admit to the tracker only if `(Σ d_plus + Σ d_minus) / Σ length ≥ params.theta`), replace the validator's per-super-edge `slope_floor` check with a route-level check on `route.metrics.avg_gradient`, apply the identical route-level feasibility filter in `tests/integration/exhaustive_oracle.py`, and fix `validator._route_metrics` to compute `avg_gradient = (d_plus_m + d_minus_m) / length_m`
**Then** unit tests assert a route below `θ` yields a `slope_floor` violation and a route at/above `θ` passes; the corrected `avg_gradient` value flows to the HTML/JSON report
**And** an integration test asserts no GRASP-admitted route on the real fixture falls below the route-level `θ` (validates by construction)
**And** the GRASP-vs-exhaustive quality gate (Story 3.7) still passes with both sides sharing the route-level feasibility definition
**And** the solver, validator, and oracle remain pure (no I/O, no input mutation)

### Story 4.3: Re-validate metamorphic + CLI tests and sync planning docs

As a developer,
I want the metamorphic suite, CLI smoke tests, and planning docs brought into line with the route-level semantics,
So that the correction is fully covered and the PRD/architecture/epics no longer contradict the code.

**Acceptance Criteria:**

**Given** Stories 4.1–4.2 have changed slope semantics
**When** I re-validate the 8 metamorphic invariants (Story 3.8) under route-level `θ`
**Then** the `scale_elevation` invariant scales `θ` and `min_climb_slope` by the same factor `k` (or sets both to 0) so feasibility is preserved, and the `relax_theta → objective non-decreasing` invariant is confirmed to bind meaningfully; a `relax_min_climb_slope → objective non-decreasing` invariant is added or the existing set is documented as sufficient
**And** CLI smoke/help tests (Stories 1.5/1.7 layer) assert `--min-climb-slope` appears in `--help` for `steeproute`
**And** PRD (FR3/FR3b, Config Schema, defaults), architecture (stage 8, constraint table, metadata, SolverParams count), and this epics file are consistent with the implemented route-level behaviour
**And** the full test suite (unit + integration + e2e) passes on the primary Windows platform

## Epic 5: Undirected Segment-Reuse Semantics

Change the edge-reuse rule from directed (once per direction) to undirected on the underlying base trail segment, with short connectors (`length_m < --l-connector`) exempt and reusable in both directions. Kills the degenerate out-and-back chains observed in testing. Realizes the originally-intended FR5 semantics. Solver, exhaustive oracle, and validator share one feasible set so the GRASP-vs-exhaustive gate stays meaningful. Inserted via correct-course 2026-06-03 (see `sprint-change-proposal-2026-06-03-undirected-segment-reuse.md`).

**FRs covered:** FR5 (realized as undirected base-segment reuse + short-connector tolerance).

**Compatibility note:** the prepared stages-1–7 cache is unaffected (contraction is query-time, Architecture §3b). No regression goldens exist yet (Epic 8), so route outputs simply change on regeneration. The base-segment identity scheme (OSM way id vs. canonical sorted node-pair + key) is an implementation choice; the binding contract is that it is identical for a segment and its reverse.

### Story 5.1: Base-segment identity and connector revival at contraction

As a developer,
I want `contract_climbs` to retain all connectors and tag every contracted edge with an undirected base-segment identity and a reuse-exemption flag,
So that the solver, oracle, and validator can enforce undirected once-only reuse with a short-connector tolerance off shared, single-sourced edge data.

**Acceptance Criteria:**

**Given** Epic 3 Story 3.3 contracts climbs into super-edges and drops sub-`l_connector` connectors
**When** I change `pipeline/graph.py::contract_climbs` to carry over all connectors (no length-based drop), tag each contracted edge with a stable undirected `base_segment_id` (a connector maps to its own; a super-edge maps to the set of base-segment ids of the edges it contracts, via `super_edge_to_base`) and a `reusable` flag (`True` only for connectors with `length_m < l_connector`), and update the `ContractedGraph` / edge-attribute docstrings in `models.py`
**Then** `tests/unit/test_graph_contraction.py` asserts short connectors are retained and tagged `reusable=True`, long connectors and super-edges are tagged `reusable=False`, and a climb super-edge shares at least one `base_segment_id` with the reverse-direction connectors of the same trail
**And** the orphan-prune-after-connector-drop step is gone (no connector is dropped) and the contraction remains pure (no input mutation)

### Story 5.2: Undirected reuse enforcement in solver, oracle, and validator

As a developer,
I want the once-only reuse rule keyed on the undirected base-segment identity with short connectors exempt, enforced consistently by the GRASP solver, the exhaustive oracle, and the validator,
So that no returned route walks a non-exempt trail segment twice in any direction (FR5/FR26) and out-and-back chains are eliminated by construction.

**Acceptance Criteria:**

**Given** Story 5.1 tags every contracted edge with `base_segment_id` + `reusable`
**When** I replace the directed `(node_u, node_v, key)` reuse set with a used-base-segment set in `solver/grasp.py` (an edge is infeasible if any of its non-exempt base-segment ids is already used; reusable edges never block and are never recorded) and apply the identical rule in `tests/integration/exhaustive_oracle.py`, and change the validator's `edge_reuse` check to flag a non-exempt base segment appearing more than once while never flagging repeated exempt short connectors
**Then** unit + integration tests assert: the classic out-and-back over a climb is rejected; a route may legitimately repeat a short connector; `edge_reuse` fires on undirected base-segment reuse and not on exempt connectors
**And** the GRASP-vs-exhaustive quality gate (Story 3.7) passes with both sides sharing the undirected feasible set
**And** the solver, oracle, and validator remain pure; the report renderer (`output.py`) handles a reusable connector traversed twice without error

### Story 5.3: Re-validate metamorphic + CLI tests and sync planning docs

As a developer,
I want the metamorphic suite, CLI help tests, and planning docs brought into line with the undirected-reuse semantics,
So that the change is fully covered and the PRD/architecture/epics no longer describe directed edge-simple reuse.

**Acceptance Criteria:**

**Given** Stories 5.1–5.2 have changed reuse semantics
**When** I re-validate the 8 metamorphic invariants (Story 3.8) under undirected reuse — confirming the base-segment identity is node-relabel-invariant and that adding an edge does not retro-block an existing segment — and add (or justify omitting) a `raise l_connector → best objective non-decreasing` invariant
**Then** all metamorphic invariants pass and the `--l-connector` help string assertion in the CLI smoke/help tests (Stories 1.5/1.7 layer) matches the reworded text
**And** PRD (FR5, Config Schema `--l-connector`), architecture (stage 9, constraint table, edge-attribute contract), and this epics file describe undirected base-segment reuse with the short-connector tolerance
**And** the full test suite (unit + integration + e2e) passes on the primary Windows platform

## Epic 6: Route-Discovery & Elevation-Consistency Fixes

Fix the stacked defects that prevented a known-good Grenoble loop from being discovered, plus the elevation metric/display inconsistency, before Operational Robustness. All five substantive changes were prototyped and verified on throwaway spikes (`spike/junction-aware-climbs`, `spike/smoothing-consistency`); re-implement cleanly against the architecture and test conventions — do not merge the spike commits. Inserted via correct-course 2026-06-07 (see `sprint-change-proposal-2026-06-07-route-discovery.md`).

**FRs covered:** none new; realizes FR10 (route discovery) and FR11 (undirected distinctness) correctly and extends the FR9 data surface (roads as connectors).

**Compatibility note:** junction-split and undirected distinctness are query-time (no cache change). Elevation smoothing moves the cache boundary (setup caches raw post-stage-5 elevation; stages 6–7 move query-side) and roads re-fetch — both change `pipeline_content_hash`, so prepared caches re-prepare once, after which `--elevation-smoothing` is a free query knob. Implementation knobs the brief defers (exact minor-road set, split-all-vs-routable junctions, smoothing strength/unit conversion to meters) are dev/architecture choices.

### Story 6.1: Route-discovery bug fixes — junction split, SAC cap-aware contraction, undirected distinctness

As a user,
I want the solver to find legitimate routes that join a climb mid-way, to keep the easy majority of a climb that contains one over-cap pitch, and to treat opposite-direction reuse of the same trail as overlap,
So that known-good routes (like the Grenoble loop that triggered this correction) are actually returned and the top-N set is genuinely distinct.

**Acceptance Criteria:**

**Given** Epic 3's contracted-graph solver and Epic 5's undirected `base_segment_id` edge identity
**When** I (a) add junction-aware climb splitting to `pipeline/graph.py::contract_climbs` (split a climb at any interior node incident to a base segment outside the climb — a real trail junction; default on, preserving the `base_segment_id`/`reusable`/`super_edge_to_base` tagging), (b) run `filter_trails(..., difficulty_cap)` before `detect_climbs` in `cli/query.py` so above-cap pitches never weld into otherwise-usable climbs, and (c) key `solver/distinctness.py::_canonical_edge_set` on the undirected `base_segment_id` (single-sourced via `solver.reuse`) instead of the directed `(node_u, node_v, key)`
**Then** three regression tests — each constructed to **fail on the pre-fix code** — assert: a climb is split at an interior trail junction and a mid-climb-turn route is constructible; at a difficulty cap below an embedded over-cap pitch, no above-cap super-edge survives and the under-cap terrain stays routable; two routes traversing one trail in opposite directions have `jaccard_distance < 1` and are rejected under `--j-max 0`
**And** the GRASP-vs-exhaustive quality gate (Story 3.7) and the existing contraction/distinctness unit tests pass (oracle and solver share the split + undirected-distinctness feasible set)
**And** the solver, contraction, and distinctness functions remain pure (no input mutation)
**And** a **human-review checkpoint** (`bmad-checkpoint-preview`) on the real trigger area (seed 44, T4 — repro command in the brief) confirms all three fixes before the story is marked done: the target loop appears and opposite-direction reuse is correctly treated as overlap

### Story 6.2: Roads as connectors

As a user,
I want routes to use short minor-road segments that link steep trails,
So that loops requiring a brief paved connector (like the trigger route) become constructible.

**Acceptance Criteria:**

**Given** Epic 2's setup-side trail filter (`pipeline/osm.py`) excludes all road highway types at both the Overpass fetch filter and `TRAIL_HIGHWAY_TAGS`
**When** I admit a curated minor-road set (starting point: residential, unclassified, service, living_street, tertiary) to the fetch filter and to `filter_trails` as connectors (no SAC grade, never climbs, bypassing the untagged policy), with tightened multi-tag handling so a way also tagged as a major road (e.g. motorway) does not leak in
**Then** the two `test_osm.py` tests that assert roads are dropped are **inverted** to the new contract, and new tests assert minor roads are admitted as connectors while major roads are excluded
**And** `pipeline_content_hash` changes so prepared caches re-prepare on next setup (documented; this is a setup-side data change)
**And** short road connectors follow the same `--l-connector` reuse-exemption rule as short trail connectors (no separate road-cost term — the vertical-effort objective self-limits road use)

### Story 6.3: Unified elevation profile, slope-display readability, and closeout

As a user,
I want the metric box, the value the solver optimizes, and the plotted elevation curve to agree, the elevation deadband to reshape the actual profile, and the displayed slope and colors to be readable,
So that a route's reported D+/D− matches its curve and the profile is trustworthy at a glance.

**Acceptance Criteria:**

**Given** Stories 6.1–6.2 and the cached raw post-stage-5 elevation
**When** I add `pipeline/smoothing.py::graph_smooth_elevation` (global graph-Laplacian diffusion; each graph node a single shared variable) and `graph_deadband_elevation` (profile transform flattening sub-floor reversals), wire them query-side in `cli/query.py` (smooth → deadband once over the whole graph → naive-sum `compute_edge_metrics` → climbs → contraction → solver, and the same graph to `output.render` with the render-side continuous pass disabled), add `--elevation-smoothing` (meters) and `--elevation-deadband` (meters) flags, raise the slope color clamp to `tan(30°)≈0.58` and compute the displayed slope over a longer baseline (±2–3 vertices), add cumulative D+/D− to the profile hover, and remove the dead per-edge `median_smooth_elevation` / render-side continuous-smoothing code (no `deadband_m` parameter on `compute_edge_metrics`)
**Then** a regression test — constructed to **fail on the pre-fix code** — asserts over a route that box D+/D− equals the plotted-curve cumulative at the final vertex (gap ≤ tolerance) and that max per-segment `|ΔElev|` never exceeds the raw-DEM maximum (no manufactured spikes)
**And** the 8 metamorphic invariants (Story 3.8) are re-validated under the new contraction/distinctness/smoothing (esp. `scale_elevation`, `relax_theta`, node-relabel isomorphism), `steeproute --help` lists `--elevation-smoothing` and `--elevation-deadband`, and the full suite (unit + integration + e2e) passes on the primary Windows platform
**And** a **human-review checkpoint** (`bmad-checkpoint-preview`) on the real trigger area confirms box==curve, no manufactured slope spikes, genuine steep terrain preserved, and readable color/baseline display before the story is marked done

## Epic 7: Operational Robustness

Deliver Journeys 2 and 3. Long-running queries emit throttled progress lines; Ctrl-C preserves best-so-far top-N with an "interrupted" convergence flag and exits 130; sparse areas return fewer than N routes with a clear explanation rather than silently loosening distinctness; stagnation detection converges the solver early when no further improvement is occurring; the final run summary on stdout reports parameters, routes returned vs. N requested, validation-failure count, and wall-clock total. Turns the tool from "happy-path only" to "usable on realistic queries."

### Story 7.1: ProgressEvent + throttled callback + CLI renderer with --quiet / --verbose

As a user,
I want `steeproute` on a long-running query to emit periodic progress lines (iteration, best-so-far, elapsed, ETA) honoring `--progress-interval` and suppressible via `--quiet`,
So that Journey 3's "can I judge whether to wait or kill this?" capability exists and FR13 is fulfilled.

**Acceptance Criteria:**

**Given** Epic 3 Story 3.6 provides `GraspSolver` with a `progress_callback` parameter
**When** I implement `progress.py::ProgressEvent` (dataclass: `iteration`, `elapsed_s`, `best_objective`, `estimated_remaining_s: float | None`, `stagnation_counter`), a throttling wrapper in `progress.py` that fires the callback at most once per `--progress-interval` seconds (wall-clock, first fire after the interval elapses from start), hook the wrapper into GRASP's iteration loop in `solver/grasp.py`, then implement a CLI renderer in `cli/query.py` that formats `ProgressEvent` as a single-line `print(...)` to stdout and installs the callback (or `None`) based on `--quiet`
**Then** `tests/integration/test_progress.py` runs GRASP on the real Grenoble fixture with a list-collecting callback, asserts events are spaced by ≥ `--progress-interval` seconds (± timing slop), every event has all fields populated, and `stagnation_counter` increments when top-N objective is unchanged across iterations
**And** `tests/e2e/test_progress_cli.py` runs `uv run steeproute --progress-interval 1` on the fixture and asserts progress lines appear on stdout during the solver phase
**And** `tests/e2e/test_quiet_suppresses_progress.py` runs with `--quiet` and asserts no progress lines appear during the solver phase (final run summary from Story 7.5 remains out of scope for this story)
**And** progress output goes through `print()` to stdout — never `logging.info` (Architecture §Cat 8 stream-discipline)
**And** `--progress-interval`'s default is set to a concrete value (e.g., 5 seconds) documented as "tunable post-baseline"

### Story 7.2: Time-budget and stagnation termination

As a user,
I want GRASP to terminate when either `--time-budget` wall-clock is exhausted OR the top-N total objective stops improving for `--stagnation-iters` consecutive iterations, with `convergence_status` set correctly,
So that NFR1's compute budget has a surfaceable termination mechanism and Journey 2's iterative tuning doesn't waste cycles when the solver has nothing more to find.

**Acceptance Criteria:**

**Given** Epic 3 Story 3.6 implements GRASP with only iter-budget termination
**When** I extend `GraspSolver.run()` to also check wall-clock against `--time-budget` between iterations and check stagnation (`tracker.total_objective()` unchanged for `--stagnation-iters` consecutive iterations, activated after the first N+1 iterations), and set a `convergence_status` attribute on the solver at termination taking one of `converged` (stagnation), `budget-exhausted` (iter-budget OR time-budget), `interrupted` (set by Story 7.3's handler)
**Then** `tests/integration/test_time_budget.py` runs GRASP on the real Grenoble fixture with `--time-budget 1` and asserts termination within ~1.5 seconds with `convergence_status == "budget-exhausted"`
**And** `tests/integration/test_stagnation.py` runs GRASP on a small programmatic fixture where the optimum is found rapidly and asserts termination well before iter-budget with `convergence_status == "converged"`
**And** `--stagnation-iters 0` disables the stagnation check (solver runs to iter-budget or time-budget)
**And** the `--stagnation-iters` default is declared as a module-scope named constant `STAGNATION_ITERS_DEFAULT_PLACEHOLDER = 100` in `solver/grasp.py` with a comment marking it as a provisional value to be tuned during implementation by observing behavior on the metamorphic test suite, the time-budget/stagnation integration tests, and real-fixture runs
**And** convergence status surfaces in every rendered report's metadata block (Story 3.10 wires `convergence_status` through `output.render`; this story now populates it with the full three-value contract)

### Story 7.3: Interrupt handling with best-so-far preservation (FR14)

As a user,
I want Ctrl-C during a query to preserve best-so-far top-N routes to disk with `convergence_status: "interrupted"`, exit 130, and leave the cache in a valid, reusable state,
So that Journey 3's partial-progress-preserved experience works and NFR3 is fulfilled.

**Acceptance Criteria:**

**Given** Epic 3 Story 3.6 makes `solver.best_so_far` readable mid-run and Story 3.11 wires the CLI end-to-end
**When** I update `cli/query.py::main` to catch `KeyboardInterrupt` raised from `solver.run()`, read `solver.best_so_far` as the working solutions, pass them through the validator, call `output.render(...)` with `convergence_status="interrupted"`, then signal exit 130 via `run_entry_point` (re-raise `KeyboardInterrupt` — the wrapper in Epic 1 already maps it to 130)
**Then** `tests/e2e/test_interrupt.py` launches `uv run steeproute` via `subprocess`, sends the platform-appropriate interrupt signal (`CTRL_C_EVENT` on Windows with `CREATE_NEW_PROCESS_GROUP`, `SIGINT` on POSIX) mid-solver after a short delay, and asserts: exit code 130, output files present in the output directory with `convergence_status: "interrupted"` in metadata, cache directory contents unchanged from before the run (no corruption)
**And** `tests/integration/test_interrupt_integration.py` uses a `monkeypatch`-raised `KeyboardInterrupt` at a specific iteration inside the solver to assert the in-process flow: CLI writes outputs before returning, `convergence_status` correctly set, validator still runs on the partial best-so-far
**And** an interrupt received *before* the solver produces any solution (e.g., during pipeline stages 8–9) exits 130 with no output files but with a stderr warning `interrupted before any solution found`
**And** post-interrupt, `uv run steeproute` re-invoked with the same args on the same cache succeeds normally — confirms cache integrity (NFR3)

### Story 7.4: Graceful degradation messaging for sparse areas (FR12)

As a user,
I want `steeproute` to return fewer than N routes with a clear explanation when distinctness under the current J_max cannot be satisfied, instead of silently loosening the constraint,
So that Journey 2's sparse-area experience is explicit and preserves the user's J_max intent.

**Acceptance Criteria:**

**Given** Epic 3 Story 3.4 (TopNTracker) naturally returns fewer than N when admission fails under the distinctness constraint
**When** I update `cli/query.py::main` to detect `len(validated_set.routes) < params.n`, construct a degradation explanation string naming the observed-vs-requested counts and the J_max value, and surface the explanation in the run summary (Story 7.5) AND in each emitted report's metadata
**Then** `tests/e2e/test_degradation.py` runs `steeproute` on a real-data fixture crafted to have only 2–3 distinct routes under `--j-max 0.30` (a narrow area within the Grenoble fixture) and asserts: fewer than N reports emitted; stdout's run summary (when Story 7.5 lands) OR final output contains a line matching pattern `Only X distinct routes satisfy J_max ≤ 0.30. Returning X routes; additional candidates would exceed the overlap threshold.`; exit code is 0 (graceful degradation is a normal outcome, not an error)
**And** a follow-up test `test_relaxed_jmax_produces_more_routes` runs the same query with `--j-max 0.50` on the same prepared cache and asserts: more routes returned (demonstrates iterative re-query); preprocessing cache-hit (fast re-run) — exercising Journey 2's tuning loop
**And** the degradation explanation appears in each emitted report's metadata block so the user reading a single report can see it was part of a degraded set

> **Updated by Epic 9 (Story 9.3):** the route-discovery fixes (#7, #10) made the solver return a richer, near-disjoint route set, so distinctness (`--j-max`) no longer binds on this small fixture. The degradation e2e was re-anchored to a feasibility-bound regime (`--theta 0.50` degrades; relaxing `--theta` admits more), and `test_relaxed_jmax_produces_more_routes` → `test_relaxed_theta_produces_more_routes`. The message was also made cause-neutral — `Only X of N requested routes satisfy the current constraints (theta=…, J_max <= …); relax --theta or --j-max to admit more.` — since degradation can now be feasibility- *or* distinctness-bound.

### Story 7.5: Run summary on stdout (FR22)

As a user,
I want a clear run summary printed to stdout at the end of every `steeproute` invocation — parameters, routes returned vs. N requested, validation-failure count, graceful-degradation explanation, convergence status, wall-clock total —
So that FR22 is fulfilled and I can judge a run's outcome at a glance without opening any HTML.

**Acceptance Criteria:**

**Given** Stories 7.1–7.4 are complete
**When** I add a final run-summary block emitted to stdout in `cli/query.py` after `output.render(...)` returns — emitted regardless of `--quiet` (Architecture §Cat 8: final summary is always stdout; only intermediate progress is suppressible) — formatted as labeled human-readable lines with this exact structure (labels stable so tests can regex-match against them):
```
--- Run summary ---
parameters: theta=<v> j_max=<v> n=<v> seed=<v> iter_budget=<v> time_budget=<v> stagnation_iters=<v>
routes_returned: <X>/<N>
validation_failures: <count>
convergence_status: <converged|budget-exhausted|interrupted>
degradation: <explanation>                      # only if routes_returned < N requested
wall_clock_total: <seconds>s
```
**Then** `tests/e2e/test_run_summary.py::test_happy_path` runs a successful query and regex-asserts each label line appears with values matching the invocation (`re.search(r"routes_returned:\s*(\d+)/(\d+)", stdout)`, etc.)
**And** `tests/e2e/test_run_summary.py::test_degraded_path` asserts the `degradation:` line appears when fewer than N routes are returned (integrates with Story 7.4)
**And** `tests/e2e/test_run_summary.py::test_validation_failure_path` asserts the `validation_failures:` line shows a non-zero count when some routes failed validation (integrates with Epic 3 Story 3.11)
**And** `tests/e2e/test_run_summary.py::test_quiet_preserves_summary` runs with `--quiet` and asserts: no progress lines during the run (Story 7.1), but the run summary still appears on stdout at the end
**And** the `--- Run summary ---` delimiter line is present so downstream scripts can split stdout on it

## Epic 8: Release Polish

Deliver the interview-ready state. Pinned regression golden fixtures on 2–3 real Grenoble-area cutouts lock in current-known-good behavior going forward, with a documented `uv run update-regression` workflow and commit-message discipline. The README presents a 3–5 region gallery (map screenshots + elevation profile PNGs + links to HTML files in `docs/examples/`), a first-class "Known Limitations" section covering DEM/cliff-bias and GRASP-non-optimality, and a quickstart for both CLIs. If any CI thresholds (coverage, GRASP/exhaustive ratio) were held lenient during earlier epics, they tighten to final committed values here.

### Story 8.1: Regression golden test harness and update-regression workflow

As a developer,
I want a test harness that compares each pinned regression fixture's GRASP output against a committed 5-field hash tuple (`objective`, `d_plus_m`, `d_minus_m`, `edge_count`, `canonical_edge_sequence_hash`) per route, plus a single `uv run update-regression` workflow command with commit-message rationale discipline,
So that silent behavioral drift between commits is caught automatically and goldens can be intentionally updated when justified (Architecture §Cat 11d).

**Acceptance Criteria:**

**Given** Epic 3 Stories 3.6, 3.10 produce deterministic GRASP output with stable JSON sidecars, and Epic 2 Story 2.8 produces reusable cache entries
**When** I implement `tests/e2e/test_pinned_regressions.py` that loads each pinned fixture's cache entry, runs `steeproute` with the fixture's pinned params + seed, computes the 5-field hash tuple per route (canonical edge sequence sorted by `(node_u, node_v, key)` per Architecture §"Numerical and data discipline", then SHA256'd), and compares against committed `tests/e2e/goldens/<fixture_name>.json`; implement `update-regression` as a `[project.scripts]` entry invocable via `uv run update-regression [--fixture NAME | --all]` that re-runs each named fixture and overwrites its golden file, printing a clear before/after diff
**Then** `tests/e2e/goldens/` has a documented JSON schema per file (`fixture_name`, `seed`, `params_hash`, `routes: [{route_index, objective, d_plus_m, d_minus_m, edge_count, canonical_edge_sequence_hash}]`)
**And** `tests/unit/test_canonical_edge_hash.py` asserts the canonical-edge-sequence hash is stable across Python runs (determinism) and sensitive to a single-edge substitution (mutation detection)
**And** `README.md` (dev-notes section) documents that any commit updating goldens must include an explicit rationale in the commit message
**And** the harness uses real cache entries + real output paths — no mocking of the solver or output layers

### Story 8.2: Pin 2–3 Grenoble regression fixtures with zero-tolerance CI gate

As a developer,
I want 2–3 committed Grenoble-area regression fixtures — each a pre-prepared cache entry + a golden hash tuple at a fixed seed + fixed params — enforced in CI with zero tolerance,
So that representative real-world query regressions are caught immediately (deterministic GRASP ⇒ any drift is a behavior change worth noticing per Architecture §Cat 11c).

**Acceptance Criteria:**

**Given** Story 8.1 provides the harness + schema + update workflow
**When** I pick 2–3 distinct small Grenoble-area cutouts (5–10 km radius, chosen for trail-density and terrain-character variety — e.g., one Chartreuse-style, one Belledonne-style, one Vercors-style), run `steeproute-setup` for each into a committed location (`tests/e2e/fixtures/<region_name>/cache/`), commit the prepared cache entries, then run `steeproute` with fixed params + seed on each and commit the resulting goldens to `tests/e2e/goldens/<region_name>.json`
**Then** `test_pinned_regressions.py` is parameterized over the 2–3 fixtures and asserts exact match on all 5 hash fields per route — zero tolerance (Architecture §Cat 11c)
**And** each fixture's cache stays under ~10 MB committed (documented per fixture; DEM excerpts shared across fixtures where geographic overlap permits)
**And** `tests/e2e/fixtures/<region_name>/README.md` documents: center/radius, DEM source, params + seed, last-updated date + commit reference
**And** each fixture's regression test runs in CI in under 30 seconds; total pinned-regression CI time under 2 minutes
**And** `pytest.skip` / `xfail` on pinned-regression tests is forbidden per Architecture §Cat 11c

### Story 8.3: README gallery with 3–5 pre-computed example reports

As a reader of the GitHub repo,
I want a visible gallery in the README showing 3–5 Grenoble-area example query results — map screenshot + elevation profile PNG + link to the interactive HTML report for each —
So that portfolio credibility is established: a visiting reviewer can see the tool works and produces the kind of route ideas the PRD describes.

**Acceptance Criteria:**

**Given** Stories 3.11 and 6.5 are complete
**When** I pick 3–5 Grenoble-area query regions demonstrating distinct terrain character (different from Story 8.2's regression fixtures — these are gallery regions, not test fixtures; full-size queries), run `steeproute-setup` + `steeproute` for each with a fixed seed + documented params, save the generated HTML files to `docs/examples/<region_name>/route-*.html`, capture a map-screenshot PNG + elevation-profile PNG for each region's route-1
**Then** `README.md` has a `## Gallery` section with one row per example region containing: thumbnail map PNG, thumbnail elevation PNG, one-line area description (e.g., `Chamrousse ridgeline · 10 km radius · ~12 min query`), clickable link to the full HTML report in `docs/examples/`
**And** `tests/e2e/test_gallery_self_contained.py` asserts each HTML file in `docs/examples/` is self-contained (zero external URL references via grep — reuses the check from Story 3.10)
**And** `docs/examples/README.md` documents how each region was generated (exact commands + seed) so any gallery file can be regenerated
**And** total PNG asset size committed to the repo stays under 5 MB
**And** during gallery generation, approximate peak memory usage per query is recorded; if any region exceeds 12 GB, surface in Story 8.4's Known Limitations section as an NFR2 reality check

### Story 8.4: README Known Limitations + Quickstart sections

As a reader of the GitHub repo,
I want the README to document the tool's known failure modes (DEM/polyline cliff-bias, GRASP heuristic non-optimality) and include a Quickstart section for both CLIs,
So that the PRD's error-model documentation commitment is fulfilled and a visiting reviewer can run the tool in under two minutes.

**Acceptance Criteria:**

**Given** Story 8.3 populates the gallery and the full tool is feature-complete
**When** I add a `## Known Limitations` section to `README.md` covering:
- **Data-level error**: DEM / polyline-drift interaction and resulting cliff-bias risk — phantom steepness near cliffs is possible; users should cross-check cliff-proximate routes against topo maps before treating them as ideas
- **Solver-level error**: GRASP is a heuristic, not an optimal solver; the repo's GRASP-vs-exhaustive CI ratio provides one empirical anchor on small instances but doesn't generalize to a claim of optimality on real-scale queries; the tool finds "a good route," not "*the* route"
- **Memory envelope (NFR2)**: validated memory behavior from Story 8.3 gallery generation — "runs comfortably on a commodity 16 GB laptop" or flagged notable usage
- **Portability (NFR7/NFR8)**: "Developed and tested on Windows. Linux is expected to work but not actively tested; macOS is not a v1 commitment."
**And** a `## Quickstart` section with concrete install + invocation commands for both `steeproute-setup` and `steeproute`, using one of the gallery regions as the example
**Then** the Known Limitations section appears in the top third of the README — portfolio-visible, not hidden in an appendix
**And** `tests/e2e/test_readme_references_gallery.py` asserts every HTML filename in `docs/examples/` is referenced from `README.md` (catches README drift when gallery is regenerated)

### Story 8.5: Final CI threshold tightening and Linux best-effort job

As a developer,
I want CI coverage thresholds tightened to the committed values (80% overall / 95% on pure-logic modules), the GRASP-vs-exhaustive ratio gate revisited against observed baseline, and a Linux matrix job running alongside Windows for NFR8 best-effort coverage,
So that Epic 8's quality commitments are enforceable in CI and the repo can credibly claim its quality bar.

**Acceptance Criteria:**

**Given** Epics 1–7 and Stories 8.1–8.4 are complete; the full test suite passes on main
**When** I update CI configuration to: set `--cov-fail-under=80` for overall coverage and `--cov-fail-under=95` targeted at `src/steeproute/pipeline/`, `src/steeproute/solver/distinctness.py`, `src/steeproute/validator.py`, `src/steeproute/cache.py` (Architecture §Cat 11e); revisit Story 3.7's `QUALITY_THRESHOLD = 0.80` against observed baseline — if the baseline supports it, tighten to a higher value (target 0.85–0.90 per Architecture) with the new value recorded in the commit message and updated in the Story 3.7 test's module-scope constant; add a `ubuntu-latest` matrix job to `.github/workflows/ci.yml` running the same pytest + ruff + basedpyright gates
**Then** CI passes on the current main on Windows (primary) and produces a Linux run whose status is visible in CI
**And** coverage thresholds are enforced — any PR dropping below either threshold fails CI
**And** the Linux job is marked `continue-on-error: true` (Linux failures visible in CI summary but not merge-gating per NFR8's "best-effort, not actively tested" stance) — unless it turns out Linux runs clean, in which case the flag is omitted and Linux gates fully
**And** `README.md` (dev-notes section) documents the CI gates — coverage thresholds, GRASP-ratio gate, regression-golden zero-tolerance, metamorphic pass-required — with a sentence linking each gate to the PRD/Architecture commitment it enforces

> **Sequencing (correct-course 2026-06-18, updated 2026-06-25):** Story 8.5 runs **after Epic 10**. Epic 9's θ-prefix-recovery fix (Story 9.2, review finding #10) raises the GRASP-vs-exhaustive baseline, so the `QUALITY_THRESHOLD` revisit above must be done against the post-Epic-9 ratio. Epic 10 (correct-course 2026-06-25) adds two opt-in constraints that do **not** shift the baseline (their flags are off in the gate fixtures), but 8.5 should additionally pin Epic 10's two new flag-on golden fixtures into its regression set. The coverage-threshold and Linux-job parts of 8.5 are independent of both epics.

## Epic 9: Route-Discovery Quality (Climb Maximality & θ-Prefix Recovery)

Close two route-discovery quality gaps surfaced by the v1 general review (2026-06-11) and confirmed with repros: climb detection that wasn't actually maximal (finding #7) and a GRASP search that discarded θ-feasible prefixes (finding #10). Both are defects bringing code in line with intended behavior — neither is a constraint violation, and neither invalidates a PRD/architecture requirement. Both change route output, so both regression golden tiers (fast + realistic) are rebaked here, and Story 8.5's GRASP-ratio threshold revisit is sequenced after this epic. Inserted via correct-course 2026-06-18 (`sprint-change-proposal-2026-06-18-route-discovery-quality.md`); no epic renumber. Companion to the already-merged review fixes #6 (`90bc38f`) and the realistic-budget golden tier (`926e597`).

### Story 9.1: Climb-detection maximality (review finding #7)

As a user,
I want every detected climb to be genuinely maximal — rooted at its true steep bottom regardless of OSM node-id labeling —
So that no steep chain-start is silently demoted to a connector and routes can board climbs from the bottom.

**Acceptance Criteria:**

**Given** `detect_climbs` currently seeds in sorted `(u, v, key)` order and extends forward only, so a mid-chain seed orphans the upstream steep edge (contradicting Story 3.2's "maximal" AC)
**When** I make detection capture the full maximal contiguous steep chain independent of seed order (e.g. backward extension from the seed, or descending-slope seeding), preserving FR29 determinism and edge-disjointness (each base edge in ≤ 1 climb)
**Then** a fail-first regression test asserts the same steep chain under two node labelings yields identical maximal climbs and the steep bottom edge is always captured (never orphaned/dropped)
**And** the existing stage-8 unit/property tests, the contraction tests, and the Story 3.3 back-mapping injectivity all still hold

### Story 9.2: GRASP θ-feasible prefix recovery (review finding #10)

As a user,
I want GRASP to keep a θ-clearing route even when its greedy walk is forced to append a flat tail that drags the whole-walk average below θ,
So that the solver stops returning nothing (or fewer routes) where feasible routes demonstrably exist.

**Acceptance Criteria:**

**Given** `_construct_one` emits only the maximal walk and θ is checked only on the finished walk, so a feasible steep prefix is discarded when a flat tail follows
**When** I track the best θ-clearing prefix of each constructed walk and offer it to the tracker, keeping FR29 determinism and one shared feasible set with the exhaustive oracle
**Then** a fail-first regression test asserts GRASP returns the θ-clearing prefix the oracle returns on a steep-edge-plus-forced-flat-tail graph (no false empty result)
**And** the Story 3.7 GRASP-vs-exhaustive ratio is unchanged or higher with both sides on one feasible set, and the oracle docstring's identical-feasible-set claim is made accurate

### Story 9.3: Revalidation, golden rebake, and doc sync (Epic 9 closeout)

As a developer,
I want the route-output changes from 9.1 + 9.2 revalidated end-to-end and the regression baselines regenerated,
So that the suite reflects the corrected behavior and Story 8.5 can tighten the quality threshold against a trustworthy baseline.

**Acceptance Criteria:**

**Given** Stories 9.1 and 9.2 are complete
**When** I re-validate the 8 metamorphic invariants and the Story 3.7 quality gate, rebake both golden tiers (`uv run update-regression --all` and `--all --tier realistic`) with an explicit rationale, and sync docs (Story 3.2 maximality note, oracle docstring, any known-limitations wording)
**Then** the full suite passes on Windows (default tier) and the realistic tier passes via `uv run pytest -m slow`
**And** Story 3.7's `QUALITY_THRESHOLD` is left unchanged here (its tightening is Story 8.5's job, now sequenced after this epic)
**And** an optional `bmad-checkpoint-preview` on a real Grenoble area confirms climbs root at their true bottoms and the returned route set improved

## Epic 10: Practical Route Constraints (Junction Start & Descent Cap)

Promote `future-ideas.md` items #1 and #2 into v1 as two opt-in route-practicality constraints, ahead of final release polish (Story 8.5). `--start-at-junction` (FR31) forces a route's start endpoint to a road/trail junction — where you'd realistically park or step onto the trail. `--max-descent-slope` (FR32) is a direction-aware cap that forbids descending a segment steeper than the threshold (windowed, uphill-measured) while leaving it eligible as a climb — so routes don't bomb down dangerous grades. Both default **off**, so default-parameter output is byte-identical to today and the existing regression goldens do not rebake; each feature instead adds a new golden pinning its flag on. Inserted via correct-course 2026-06-25 (`sprint-change-proposal-2026-06-25-junction-start-and-descent-cap.md`); no epic renumber; runs before Story 8.5.

**FRs covered:** FR31, FR32.

### Story 10.1: Junction-start constraint (FR31)

As a user,
I want an opt-in flag that forces a route's start endpoint to a road/trail junction,
So that the surfaced route idea begins where I'd realistically park or step onto the trail.

**Acceptance Criteria:**

**Given** the contracted graph already distinguishes connectors (roads) from trails but marks no node as a road/trail junction, and GRASP may seed a walk anywhere
**When** I annotate junction nodes at contraction (Stage 9) and, under `--start-at-junction`, restrict GRASP seeding and the exhaustive oracle's walk-starts to junction nodes — adding start-endpoint-is-junction to the validated constraint set (FR26/FR27/FR28), wiring FR12 messaging when the constraint limits results below N, and preserving FR29 determinism and one shared feasible set
**Then** with the flag off, default output is byte-identical to today and the existing default-param goldens (both tiers) match without rebake
**And** with the flag on, every returned route starts at a junction node — pinned by a new flag-on golden fixture — and a route whose start isn't a junction is banner-flagged with the non-zero exit code

### Story 10.2: Direction-aware descent-slope cap (FR32)

As a user,
I want an opt-in cap that refuses to descend a segment steeper than a threshold while still letting routes climb that segment,
So that returned routes don't bomb down dangerous grades.

**Acceptance Criteria:**

**Given** the edge-attribute contract has per-edge metrics but no descent-governing windowed slope, and the reuse model is undirected
**When** I precompute a per-base-segment steepest windowed uphill-measured gradient (`max_windowed_descent_grad`), make GRASP construction and the exhaustive oracle reject any descending traversal exceeding `--max-descent-slope` (uphill unconstrained; a super-edge taken in reverse treated as a descent), add no-segment-descended-above-cap to the validated set (FR26/FR27/FR28), and keep FR29 determinism with one shared feasible set
**Then** with the flag off, output is byte-identical to today and the existing default-param goldens (both tiers) match without rebake
**And** with the flag on, no returned route descends an over-cap segment though it stays eligible as a climb — pinned by a new flag-on golden fixture — and a new metamorphic invariant (relax `--max-descent-slope` → best objective monotone non-decreasing) passes alongside the existing eight
**And** the Epic 10 closeout is folded in here: re-validate the metamorphic suite and the Story 3.7 GRASP-vs-exhaustive gate (expected unchanged — flags off in those fixtures), sync PRD/architecture docs, and an optional `bmad-checkpoint-preview` on a real Grenoble area confirms sensible junction starts and avoided steep descents

## Epic 11: Performance Instrumentation & Baseline

Establish the measurement foundation the performance-tuning roadmap requires before any optimization work, and close the v1 gap where `steeproute-setup` runs for minutes in complete silence. One reusable stage-timing seam serves both goals: each setup pipeline stage announces itself and reports elapsed time, long stages (DEM tile fetch) emit within-stage progress so "working" is distinguishable from "stuck" (FR33), and the same seams give profiling its per-stage attribution. py-spy flamegraphs of a realistic GRASP run (~200k iterations, Grenoble-scale) produce the epic's decision deliverable — a ranked bottleneck list with Python-vs-native attribution answering the research's central question (scoring math vs. networkx calls vs. loop skeleton). A dedicated `tests/benchmarks/` pytest-benchmark suite (excluded from the default run) pins throughput baselines — seconds per 1k GRASP iterations at fixed seed/params, setup-stage wall-clock — before anything changes. Behavior-preserving apart from the new setup output: regression goldens stay green untouched. Phases 3–4 of the roadmap (cheap wins, conditional native kernel) are deliberately **not** planned here — their scope depends on the bottleneck list this epic produces. Promotes the `future-ideas.md` "Performance tuning" item per `research/technical-steeproute-performance-tuning-research-2026-07-02.md` (Phases 0–2); inserted via post-v1 increment run 2026-07-02; no epic renumber.

**FRs covered:** FR33. Supports NFR1 (the ~10-min design target becomes measurable rather than anecdotal).

### Story 11.1: Setup-stage timing seams and progress reporting (FR33)

As a user,
I want `steeproute-setup` to tell me which stage it's running, how long each completed stage took, and progress within long stages,
So that a multi-minute setup run is visibly working rather than apparently hung.

**Acceptance Criteria:**

**Given** `steeproute-setup` currently emits nothing between invocation and the end-of-run summary
**When** I add one reusable stage-timing seam (context manager or decorator, natural home `progress.py`) wrapped around every setup pipeline stage — emitting a stage-start line and a stage-complete line with elapsed time to stdout, within-stage `tile i/N` progress for the DEM tile-fetch loop, and an honest "single request, typically takes minutes" start line for the blocking OSM/Overpass download
**Then** a real setup run prints a per-stage timeline whose stage times account for the run's wall-clock (the T3 deliverable), and `--quiet` suppresses all progress while errors/warnings stay on stderr (Architecture Cat 8)
**And** the seam also captures timings machine-readably (e.g. a per-stage dict on the run result), so Story 11.2's attribution reuses it rather than re-instrumenting
**And** osmnx's HTTP cache is verified enabled and persistent under `platformdirs`, fixed if not (T2), with the outcome asserted by a test or recorded in the story's close-out
**And** existing e2e setup tests stay green, extended to assert stage lines present by default and absent under `--quiet`; regression goldens untouched

### Story 11.2: Profile solver and setup pipeline into a ranked bottleneck list

As a developer,
I want py-spy flamegraphs of a realistic GRASP run plus the instrumented setup breakdown, analyzed into a ranked bottleneck list,
So that Phase 3 optimization targets measured hotspots instead of guesses.

**Acceptance Criteria:**

**Given** Story 11.1's seams exist and py-spy is available as a dev dependency (native on Windows)
**When** I profile a quality-params GRASP run (~200k iter-budget / 10k stagnation, Grenoble-scale prepared area) with `py-spy record`, and run a cold-cache real setup capturing the per-stage breakdown
**Then** a findings document in `_bmad-output/planning-artifacts/` records: the ranked bottleneck list with percentage attribution, Python-vs-native attribution per hotspot, and an explicit answer to the research's decision question — scoring math vs. networkx calls vs. loop skeleton — plus the setup per-stage table separating network wait from CPU work
**And** the document closes with a Phase-3 recommendation following the research's decision tree (numpy batching / rustworkx / PyO3 kernel / setup-side levers), flamegraph artifacts committed or linked
**And** Scalene-under-WSL2 is used only if flamegraphs leave the Python-vs-native split ambiguous; no production code changes in this story

### Story 11.3: Dedicated benchmark suite pinning pre-optimization baselines

As a developer,
I want a `tests/benchmarks/` pytest-benchmark suite measuring solver throughput and setup-stage wall-clock, excluded from the default test run,
So that every future optimization is judged against pinned baselines instead of anecdotes.

**Acceptance Criteria:**

**Given** Stories 11.1–11.2 are complete and no optimization work has landed
**When** I add pytest-benchmark as a dev dependency and create `tests/benchmarks/` with its own marker/testpath excluded from the default run (same pattern as `live`/`slow`), with benchmark fixtures sized for measurement (the `grenoble_small` graph, fixed seed and params) independent of functional fixtures
**Then** the suite measures seconds per 1k GRASP iterations at fixed seed/params (throughput) and per-stage setup timings on cached fixture data (no live network), and `--benchmark-autosave` baselines are committed or their location documented
**And** the default `uv run pytest` collects zero benchmark tests and all functional tests are unmodified
**And** the dev-notes/README document the `--benchmark-compare` workflow expected around every future optimization commit

## Epic 12: Solver Performance Optimization (Phase 3 — Pure-Python Cheap Wins)

Execute the Phase-3 optimizations the bottleneck analysis indicts, in its ranked order. Profiling (Story 11.2) resolved the research's decision question: the GRASP solver is ~94% of query wall-clock and its cost is the bespoke loop skeleton plus per-step object churn — pure-Python data-structure waste, not scoring math (no batchable dense math exists) and not networkx algorithms (rustworkx explicitly not indicated). Estimated combined headroom ≈2.5–4×. Stories 12.1–12.2 are behavior-identical (same candidates, same order — regression goldens stay green untouched); Story 12.3 batches RNG draws, which changes the seeded draw sequence and carries the epic's one documented golden rebake (Story 9.3 reconciliation precedent). Every story is judged against the Epic 11 benchmark baselines (`--benchmark-compare`); the epic closes with a fresh profile and an explicit Phase-4 go/no-go (extract-interface-first → PyO3 kernel is the designated branch if the target is missed). Setup-pipeline optimization stays out of scope — ~81% network wait, one-time cost per area. Promotes the Phase-3 recommendation in `research/steeproute-bottleneck-analysis-2026-07-03.md`; inserted via correct-course 2026-07-03 (see `sprint-change-proposal-2026-07-03-solver-optimization.md`); no epic renumber.

**FRs covered:** none new — performance work on existing behavior. Supports NFR1 (widens the margin under the ~10-minute design target) and preserves NFR4 (seeded determinism holds under 12.3's new draw scheme, with rebaked goldens).

### Story 12.1: Precompute static per-node adjacency for RCL construction

As a user,
I want the solver to stop rebuilding static graph data on every walk step,
So that queries run substantially faster with identical results.

**Acceptance Criteria:**

**Given** the contracted climb graph is immutable for the duration of a solve
**When** `run()` precomputes, once per solve, a per-node adjacency table of pre-built records (`Edge` object, blocking frozenset, static sort order) and `_build_rcl` consumes it — no networkx view construction, no `Edge` re-wrapping, no `blocking_ids` recomputation, no re-sorting per step
**Then** solver output is behavior-identical (same candidates in the same order for the same seed) and the full regression-golden suite passes untouched
**And** the benchmark suite shows a material throughput gain over the pinned Story 11.3 baseline (analysis attributes ~35–40% of the run to the eliminated work), recorded via `--benchmark-compare` in the story close-out
**And** solver public interfaces, validator, and exhaustive oracle are unchanged

### Story 12.2: Incremental θ-prefix metrics and cached distinctness sets

As a user,
I want prefix finalization and distinctness checks to stop recomputing unchanged values,
So that per-iteration overhead drops further with identical results.

**Acceptance Criteria:**

**Given** `_best_theta_prefix` currently re-sums the whole prefix per candidate (quadratic in walk length) and `_canonical_edge_set` is recomputed per pairwise comparison
**When** prefix scanning maintains running `Σlength / ΣD+ / ΣD−` sums, with the canonical `route_avg_gradient` retained as the final acceptance gate (admitted values stay bit-identical to the validator's, per the models.py contract), and each held solution's canonical edge set is computed once at insertion
**Then** the regression-golden suite passes untouched
**And** the benchmark suite shows a throughput gain over the post-12.1 baseline consistent with the ~15% combined attribution, recorded via `--benchmark-compare`

### Story 12.3: Batched RNG draws with documented golden rebake

As a user,
I want the per-step scalar RNG boundary overhead removed,
So that the last measured hotspot (~13% of the run) is captured.

**Acceptance Criteria:**

**Given** the hot path currently makes one scalar `Generator.integers` call per walk step — the profile's only native time, all boundary overhead
**When** RNG draws are batched/chunked so per-step scalar calls disappear from the hot path, preserving the determinism contract (same `--seed` + code + prepared data → identical output edge-sets, NFR4)
**Then** because the draw sequence changes, regression goldens are rebaked once via the documented `update-regression` workflow with commit-message rationale (Story 9.3 precedent)
**And** the GRASP-vs-exhaustive quality gate and metamorphic invariants pass on the new outputs
**And** the benchmark gain over the post-12.2 baseline is recorded via `--benchmark-compare`

### Story 12.4: Re-profile, benchmark reconciliation, and Phase-4 decision

As a developer,
I want a post-optimization profile and a consolidated benchmark comparison against the Epic 11 baselines,
So that the Phase-4 decision (PyO3 kernel or stop) is made on measurements, not projections.

**Acceptance Criteria:**

**Given** Stories 12.1–12.3 have landed with per-story benchmark records
**When** I capture a fresh py-spy profile of the same quality-params workload, plus one confirming capture on a larger area (the analysis's single-area caveat), and consolidate cumulative speedup vs the Story 11.3 baselines
**Then** a findings update in `_bmad-output/planning-artifacts/research/` records the new profile shape, the cumulative speedup, and whether the measured result lands inside the predicted 2.5–4× band
**And** the document closes with an explicit what-next recommendation covering the whole execution — ranked levers (solver residue, query-side stages 6–9, cache read, imports) each assessed Rust vs pure-Python vs leave-it, with the solver's extract-interface-first → PyO3 branch judged as one candidate under its end-to-end ceiling — follow-on stories are not planned in this epic *(broadened 2026-07-04: goal is whole-execution wall-clock, not solver throughput)*
**And** no production code changes in this story
