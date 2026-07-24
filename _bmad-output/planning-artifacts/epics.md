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

> **Archive note (2026-07-04):** Epics 1–12 are all `done` (see `sprint-status.yaml`). Their full text — story-by-story acceptance criteria — is archived verbatim at [`archive/epics-completed-1-12.md`](archive/epics-completed-1-12.md) and is no longer duplicated here. This file keeps only what's still load-bearing for planning: the requirements inventory, the FR/NFR coverage map, and a one-line-per-epic history table. New epics (13+) get their full detail appended below the table, same as always — only *completed* epics get folded into the archive.

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
| FR1 (area: rectangle, opt. rotated) | Epic 1 (square) / Epic 15 (rotated rect) | Area flag surface + custom click type; rotated model + `graph_from_polygon` in Epic 15 |
| FR2 (area-cap rejection) | Epic 1 (initial) / Epic 15 (true area) | Validation at CLI layer; `BadCLIArgError` path; true rectangle area (not disk proxy) in Epic 15 |
| FR3 (route-level slope floor θ) | Epic 1 (flag) / Epic 3 (initial) / Epic 4 (corrected to route-level) | Flag in `cli/_shared.py`; route-level `(D+ + D−)/length` floor enforced at solve + validate |
| FR3b (climb-detection slope) | Epic 4 (flag) / Epic 3 (climb detection) | New `--min-climb-slope`; running-avg `d_plus/length` in `detect_climbs` |
| FR4 (difficulty cap SAC) | Epic 1 (flag) / Epic 3 (enforcement) | Enforced per-edge in validator + pipeline filter |
| FR5 (L_connector) | Epic 1 (flag) / Epic 3 (initial) / Epic 5 (undirected reuse + connector tolerance) | Undirected base-segment reuse limit; short connectors `< --l-connector` exempt and reusable |
| FR6 (min climb length) | Epic 1 (flag) / Epic 3 (enforcement) | Enforced by climb detection (pipeline stage 8) |
| FR7 (J_max pairwise overlap) | Epic 1 (flag) / Epic 3 (enforcement) | Enforced by TopNTracker |
| FR8 (N result count) | Epic 1 (flag) / Epic 3 (enforcement) | TopNTracker capacity |
| FR9 (untagged trails policy) | Epic 1 (flag) / Epic 2 (enforcement) | Enforced in pipeline stage 2 (trail filter) |
| FR10 (vertical-effort objective + strict containment) | Epic 3 / Epic 15 (rotated containment) | GRASP + climb-graph construction; `shapely.contains` on rotated polygon in Epic 15 |
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

- NFR1 (compute budget ≤10min design target): Epic 7 — time-budget termination, stagnation, progress reporting surfaces elapsed; Epic 11 makes the target measurable (benchmark baselines + per-stage timing); Epic 12 raises solver throughput against those baselines; Epic 13 attacks the query-side share that dominates large-area whole-execution wall-clock post-Epic-12; Epic 14 extends the target toward large areas (r50 / whole-range) — vectorizing setup CPU stages and adding multi-core GRASP; Epic 16 attacks the next tier — object-graph churn and repeated derivation of immutable state (owned query filter, lean contracted graph, setup owned-data reuse, in-place osmnx ingestion, shared solver state), with the strongest wins byte-identical
- NFR2 (16 GB memory envelope): Epic 8 — validated during gallery generation; documented if notable; Epic 16 reduces query peak RSS (owned-graph reuse + lean contracted graph cut it 2.67→2.05 GB) and worker steady memory (shared solver state)
- NFR3 (Ctrl-C preserves output + cache valid): Epic 7
- NFR4 (seeded determinism, edge-set level): Epic 3
- NFR5 (atomic cache writes): Epic 2
- NFR6 (OSM/DEM actionable-error on source down): Epic 2
- NFR7 (Windows primary platform): Epic 1 — CI runs on Windows; all subsequent epics validated there
- NFR8 (Linux best-effort, macOS uncommitted): Epic 1 — CI may include Linux job; not gated

## Epic List

**Completed (see [`archive/epics-completed-1-12.md`](archive/epics-completed-1-12.md) for full story-level detail):**

| Epic | Title | Outcome | FRs |
|---|---|---|---|
| 1 | Project Foundation & CLI Shell | Scaffolded installable project, full flag surface, 3-layer test structure, CI gates, exit-code-2 on bad args | FR1, FR2, FR20, FR30 (scaffolding); flag surface for FR3–9 |
| 2 | Data Preparation & Caching | `steeproute-setup` end-to-end, atomic cache, OSM-age warning, fail-fast on unprepared query area | FR23, FR24, FR25 |
| 3 | Query Pipeline, Solver, Validation & Report Rendering | Journey 1 happy path end-to-end; seeded reproducibility; validation banners; oracle/GRASP/metamorphic test stack | FR3–9 (enforcement), FR10, FR11, FR15–21, FR26–29, FR30 (exit 1) |
| 4 | Route-Level Slope-Floor Correction | θ corrected to a route-level `(D++D−)/length` floor; new `--min-climb-slope` flag. *Correct-course 2026-06-03* | FR3 (corrected), FR3b (new) |
| 5 | Undirected Segment-Reuse Semantics | Edge reuse made undirected on base segment; short connectors exempt & reusable. *Correct-course 2026-06-03* | FR5 (realized) |
| 6 | Route-Discovery & Elevation-Consistency Fixes | Junction-aware climb splitting, SAC-cap-aware contraction, undirected Jaccard distinctness, roads-as-connectors, one canonical elevation profile. *Correct-course 2026-06-07* | Realizes FR10/FR11 correctly; extends FR9 |
| 7 | Operational Robustness | Journeys 2/3: throttled progress, Ctrl-C best-so-far + exit 130, graceful degradation, stagnation detection, run summary | FR12–14, FR22, FR30 (exit 130) |
| 8 | Release Polish | Pinned regression goldens (2–3 Grenoble cutouts), `update-regression` workflow, README gallery + Known Limitations, CI thresholds tightened | none new (PRD success-criteria coverage) |
| 9 | Route-Discovery Quality (Climb Maximality & θ-Prefix Recovery) | Fixed non-maximal climb detection (review #7) and GRASP discarding θ-feasible prefixes (review #10); both golden tiers rebaked. *Correct-course 2026-06-18* | none new — corrects FR10/FR11 behavior |
| 10 | Practical Route Constraints (Junction Start & Descent Cap) | Opt-in `--start-at-junction` and `--max-descent-slope`, both default off, byte-identical default output, new flag-on goldens. *Correct-course 2026-06-25* | FR31, FR32 |
| 11 | Performance Instrumentation & Baseline | Setup-stage timing/progress (FR33); py-spy flamegraphs of GRASP run; ranked bottleneck list (solver ≈94% of wall-clock, loop-skeleton/object-churn); pytest-benchmark baselines pinned. *Post-v1 increment 2026-07-02* | FR33; supports NFR1 |
| 12 | Solver Performance Optimization (Phase 3 — Pure-Python Cheap Wins) | Precomputed adjacency, incremental θ-prefix + cached distinctness, batched RNG draws (one documented golden rebake), re-profile + Phase-4 go/no-go. *Correct-course 2026-07-03* | none new; supports NFR1, preserves NFR4 |

**Active / future epics** (full detail below, same as always):

## Epic 13: Query-Side Performance (Whole-Execution Wall-Clock)

Epic 12 exceeded its target (solver 5.6× vs the predicted 2.5–4×) and flipped the phase split: on the
large-area reference workload (Chartreuse r10, 40.0 s) the solver is now ~31% of wall-clock while query-side
work Epic 12 never touched dominates — stages 6–7 ~27% (headlined by ≈417 whole-graph Laplacian smoothing
passes per query), `filter_trails` redux + stages 8–9 ~13%, cache `read_entry` ~11%, imports/startup ~3–5 s
constant. The designated Phase-4 branch (PyO3 solver kernel) is declined on performance grounds
(Amdahl-capped ~1.4× end-to-end; it stays in `future-ideas.md` on learning value only). This epic works the
ranked query-side levers from `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` — all
pure-Python/numpy-shaped; plausible combined effect ~40 s → ~20 s on the large-area workload. Stories
13.1–13.2 are compute-shaped fixes; 13.3 is the recompute-avoidance cache-boundary design question,
deliberately sequenced after them because their outcome changes its cost-benefit; 13.4 is bounded startup
work; 13.5 closes with the measurement pattern Story 12.4 established. Measurement anchors: the reference
workloads pinned in the Phase-3 results doc (Chamrousse r6 12.19 s / Chartreuse r10 40.05 s, fixed
seeds/params). Goldens expected green throughout; 13.1 carries one contingent documented rebake (float
reordering). Inserted via correct-course 2026-07-04; no epic renumber.

**FRs covered:** none new — performance work on existing behavior. Supports NFR1 (whole-execution
wall-clock on large areas) and preserves NFR4 (seeded determinism).

### Story 13.1: Vectorize query-side elevation smoothing (stage 6)

As a user,
I want the global Laplacian elevation smoothing to stop iterating whole-graph passes in Python,
So that the dominant query-side cost on large areas drops without changing route results.

**Acceptance Criteria:**

**Given** stage 6 currently runs ≈ round(window²/6) whole-graph Laplacian passes (~417 at the 50 m default)
as per-node Python iteration on every query
**When** the diffusion is reformulated as sparse-matrix/array operations — same math, same iteration count,
same smoothed profile
**Then** results are numerically equivalent and the regression-golden suite passes untouched; if reordered
float arithmetic flips any golden edge-set, the story instead carries one documented rebake via
`update-regression` with the equivalence argument recorded (Story 9.3/12.3 precedent)
**And** measured stage 6–7 wall-clock on the Chartreuse r10 reference workload drops materially (analysis
attributes ~27% of the 40 s run), recorded in the story close-out
**And** solver, validator, and output interfaces are unchanged

### Story 13.2: Faster cache-entry deserialization

As a user,
I want prepared-area cache entries to load without per-edge geometry parsing and incremental graph rebuild,
So that large-area queries stop paying ~11% of wall-clock before any work starts.

**Acceptance Criteria:**

**Given** `read_entry` currently parses per-edge WKB geometry and rebuilds the graph edge-by-edge
**When** entry storage moves to an array-based / prebuilt-graph format with a manifest schema-version bump
(existing entries re-prepare once, per the Category 4b invalidation semantics)
**Then** the loaded graph is content-identical (same nodes, edges, attributes) and the full suite including
regression goldens passes untouched
**And** measured `read_entry` time on the Chartreuse r10 entry drops materially, recorded in the close-out
**And** architecture Category 4c (on-disk format) is updated to record the new decision

### Story 13.3: Query-side recompute avoidance (second-tier cache decision)

As a user,
I want repeat queries to stop re-running unchanged pipeline work,
So that the `filter_trails` redux and stages 8–9 (~13% on large areas) stop being paid on every invocation.

**Acceptance Criteria:**

**Given** the stage 1–5 cache is keyed independent of query knobs by design (Stories 6.1/6.3), so
`filter_trails` redux and stages 6–9 re-run per query
**When** the cache-boundary options are weighed with the post-13.1/13.2 phase split as input (e.g. a light
second cache tier keyed on the query knobs, or moving the stage-2 redux setup-side) and the chosen option is
implemented — or the story records a reasoned decision *not* to, if the remaining share no longer justifies
the added cache complexity
**Then** repeat-query wall-clock on the reference workloads reflects the decision, results identical,
goldens untouched
**And** if a second cache tier ships: writes are atomic (Category 4d pattern), its key includes every input
affecting the cached stages, and architecture Category 3b is updated

### Story 13.4: Lazy imports on the query path

As a user,
I want the constant ~3–5 s import/startup cost cut down,
So that small-area queries stop spending up to a third of their wall-clock before doing anything.

**Acceptance Criteria:**

**Given** imports/process startup cost ~3–5 s per invocation regardless of query size
**When** heavyweight imports are deferred off the query path's startup sequence (lazy/function-local where
measurement supports it)
**Then** measured cold-start-to-first-output on the Chamrousse reference workload drops materially, behavior
unchanged, full suite green
**And** `--help`/`--version` and error paths stay fast (CLI smoke tests unaffected)

### Story 13.5: Re-measure and epic close-out

As a developer,
I want a post-epic profile and consolidated wall-clock comparison on both reference workloads,
So that the epic's effect on whole-execution wall-clock is recorded from measurements and the what-next
decision is evidence-based.

**Acceptance Criteria:**

**Given** Stories 13.1–13.4 have landed with per-story measurements
**When** I capture fresh py-spy profiles of both reference workloads (same seeds/params as the 2026-07-04
captures) and consolidate before/after wall-clock
**Then** a findings update in `_bmad-output/planning-artifacts/research/` records the new phase split and the
cumulative effect vs the 12.19 s / 40.05 s anchors, assessed against the plausible ~20 s large-area outcome
**And** the document closes with an explicit what-next recommendation (further work via correct-course, or
stop)
**And** no production code changes in this story

## Epic 14: Setup + Solver Scaling toward r50

Every prior optimization epic anchored on r6/r10; the measured r20 baseline in
`research/steeproute-next-optimization-pass-handoff-2026-07-05.md` shows two blind spots that only
bite at the whole-alpine-range goal (r50–100, design target: setup + query ≤ 10 min at r50).
(1) Setup is NOT network-bound at scale — ~44% of the 761 s r20 setup is CPU (elevation-sampling
215 s, resampling 62 s, smoothing 33 s, trail-filter 18 s, plus ~141 s of osmnx CPU inside
osm-download). (2) The whole program is single-threaded CPython (~7% of a 14-core machine). Two
remedies, in order: vectorize the per-vertex Python loops (deterministic, likely sufficient for the
pipeline stages), then multi-core via processes where work stays expensive or can't be vectorized
(GRASP restarts; threads suffice for DEM fetch I/O). This epic takes the handoff's definite, measured
levers and the r50 probe; the explicitly-gated deep work (S5 custom Overpass parser, Q4 array
contract, per-stage parallelization) is deferred to a post-probe correct-course per the handoff's own
"decide at the §8 probe" guidance. Bit-identity is the default guardrail; content-hash changes are
batched (14.2 co-lands the pipeline vectorization); one contingent documented rebake allowed at 14.5.
Story 13-4 (lazy imports) stays parked (small-area interactive, orthogonal to r50); 13-5 (re-measure)
is subsumed by 14.6. Inserted via correct-course 2026-07-06; no epic renumber.

**FRs covered:** none new — performance work on existing behavior. Supports NFR1 (extends the ≤10-min
design target toward large areas / r50) and preserves NFR4 (seeded determinism; `--workers 1` default
leaves the existing contract unchanged, parallel mode is deterministic per `(seed, workers)`).

### Story 14.1: Vectorize elevation sampling (setup stage 5)

As a user,
I want DEM elevation sampling to stop looping per-point through rasterio in Python,
So that the single biggest setup CPU stage drops from minutes to seconds without changing elevations.

**Acceptance Criteria:**

**Given** `sample_elevation` (`pipeline/dem.py`) costs ~215 s @ r20 — ~65 µs/point of per-point
Python/rasterio overhead over ~3.5 M points (per-edge `transformer.transform` on lists, per-vertex
bounds check, per-point `dataset.sample`)
**When** it is reformulated as flat-array vectorized work (one ragged-array coordinate collection as
in 13.2, one vectorized `pyproj` transform, vectorized inverse-affine rows/cols replicating
rasterio's nearest-pixel/rowcol rounding exactly, fancy-indexed band read, vectorized bounds/nodata
masks, and a `DEMCoverageError` of the same message shape locating the first offending edge)
**Then** sampled elevations are bit-equal to the old path over every vertex of the `grenoble_small`
fixture (verify before deleting the old code) and the regression-golden suite passes untouched
**And** a per-stage benchmark is added before the change; measured stage-5 wall-clock drop is recorded
in the close-out
**And** the r50 full-band-read memory footprint (~1.6 GB at r50, estimate) is either accepted with a
note or handled by row-band windowing — the decision recorded, measured at the 14.6 probe

### Story 14.2: Vectorize + de-churn the per-edge pipeline loops (one content-hash batch)

As a user,
I want the per-vertex smoothing/resampling/metrics loops and the copy-then-remove graph churn
replaced with array ops and single-pass graph builds,
So that the remaining setup and query pipeline CPU drops, landed as one cache-invalidation cycle.

**Acceptance Criteria:**

**Given** polyline smoothing + resampling (S2, ~95 s @ r20), copy-then-remove churn in `filter_trails`
/ orphan / short-edge guards and per-stage `graph.copy()` (S3, trail-filter ~18 s @ r20 + repeated
full-graph copies), and query-side stage-7 metrics + deadband (Q2, part of elevation-reshape ~24 s
@ r20) are all per-edge Python loops over the same edge geometry
**When** they are vectorized per edge via the shapely array interface (moving-average, segment lengths
`np.hypot(np.diff)`, `np.cumsum` with naive-fold parity verified, `np.searchsorted` lerp resampling,
`np.diff`-based gain/loss and windowed-descent replicating the two-pointer boundary semantics), the
copy-then-remove churn is replaced by building a new graph from kept edges (or one
orchestrator-owned working copy), and `contract_climbs` (Q3, 5.6 s @ r20) is profiled first and
optimized only if the profile shows a material extractable cost — all co-landed as a **single**
content-hash change with one fixture-regen
**Then** coordinate arrays, edge metrics, and deadband output are bit-equal to the old paths on the
`grenoble_small` fixture (or, where a compensated-`sum` site prevents it, one documented rebake
batched with this story); the full suite including goldens passes; public API purity is preserved at
the `run_setup_stages` / `build_graph_geometry` / `operationalize_graph` boundaries
**And** per-stage benchmarks are added before the change; measured drops for stages 3–4, trail-filter,
and stage-7 metrics are recorded in the close-out

### Story 14.3: Parallelize DEM tile fetch (setup)

As a user,
I want DEM tiles fetched concurrently instead of one urlopen at a time,
So that large-area DEM download wall-clock collapses without changing the assembled mosaic.

**Acceptance Criteria:**

**Given** `_fetch_mosaic` (`pipeline/dem_download.py`) fetches tiles strictly sequentially (~134 s @
r20, 16 tiles; ~14 min @ r50 for ~100 tiles, estimate)
**When** fetching moves to a `ThreadPoolExecutor` (module-constant `max_workers`, start at 4), each
task returning `(y0, y1, x0, x1, bytes)` and the parent validating + writing into the mosaic array —
output completion-order-independent, so the assembled mosaic is byte-identical to the sequential path
**Then** the mosaic is verified identical to the sequential result; `tile i/N` progress still emits on
completion (FR33 stream discipline preserved); IGN Géoplateforme behavior under concurrency is tested
at r20 first, backing off `max_workers` if 429/errors appear (result recorded)
**And** architecture Cat 3/Cat 8 is noted (setup's first fetch concurrency; progress semantics
unchanged)

### Story 14.4: Parallel GRASP restarts (`--workers`, default 1)

As a user,
I want to run independent GRASP restarts across cores,
So that the solver stops pinning one logical core and search quality per wall-second scales with cores.

**Acceptance Criteria:**

**Given** GRASP iterations are independent restarts (embarrassingly parallel; Cat 5a designed the loop
to be `ProcessPoolExecutor`-convertible and the RNG for `SeedSequence.spawn`) and the solver runs
single-core today (~53 s @ 1M iters)
**When** a `--workers N` flag is added (**default 1 = today's exact behavior, no rebake**), plumbed at
the CLI/orchestration layer so it touches neither `SolverParams`/`models.py` nor `pipeline/` (no cache
invalidation); for N>1 a `ProcessPoolExecutor` (Windows-spawn guarded) gives each worker the
contracted graph, `iter_budget // N` (+ remainder to worker 0), and an RNG from
`SeedSequence(seed).spawn(N)[i]`, and results merge through a fresh `TopNTracker` in worker-id then
admission order
**Then** `--workers 1` output is byte-identical to pre-epic (goldens and NFR4 untouched); N>1 output
is deterministic and reproducible per `(seed, workers)`, documented as differing-by-design from N=1;
per-worker startup (spawn + contracted-graph pickle) is measured and reported
**And** architecture Cat 5a is updated from conditional-future to realized, `--workers` is added to the
flag-surface table, and the `(seed, workers)` determinism contract + `--stagnation-iters`/`--time-budget`
per-worker interpretation are recorded

### Story 14.5: Reduce osmnx ingestion CPU — cheap levers only

As a user,
I want the osmnx CPU inside the osm-download stage reduced by the low-risk levers,
So that setup's post-vectorization dominant CPU cost shrinks without a from-scratch parser.

**Acceptance Criteria:**

**Given** ~141 s of the 289 s r20 osm-download stage is osmnx CPU (`simplify_graph` ~54 s, two
truncate + two largest-component passes ~67 s combined, raw-graph build ~15 s), extrapolating to
~15 min @ r50 (estimate), and this becomes the dominant setup CPU cost once 14.1/14.2 land
**When** the cheap levers are investigated in order — whether the bbox→polygon→`truncate_graph_polygon`
double-pass can be reduced to one for plain-bbox input via lower-level osmnx APIs, and whether the
second truncate/component pass is redundant — and any safe reduction is applied
**Then** the assembled graph is bit-identical where the lever is behavior-preserving (verified on
fixtures); if a lever shifts ingestion output, **one** documented golden rebake + fixture regen is
taken with the equivalence argument recorded (never silent), and Cat 4c is noted only if on-disk
content changes
**And** the custom Overpass→graph parser (S5-deep) is explicitly **out of scope** — recorded as a
candidate for the post-probe correct-course, to be justified from 14.6's residuals
**And** `retain_all=True` is not adopted (behavior change: keeps unreachable islands, wastes solver
iterations) without golden evaluation

### Story 14.6: r50 probe, re-measure, and what-next decision

As a developer,
I want one real r50 setup+query run plus a consolidated before/after against the r20 baseline,
So that every "unknown — measure" is resolved and the deferred deep work is scoped from evidence.

**Acceptance Criteria:**

**Given** Stories 14.1–14.5 have landed with per-stage benchmarks and measured drops
**When** I run one real `steeproute-setup --radius 50` + query, recording stage lines, peak RSS,
Overpass behavior (timeout/response size/settings bumps), IGN behavior at ~100 tiles, DEM array
memory, and solver iter/s + parallel speedup on the bigger contracted graph, and produce a fresh r20
trace reconciled against the handoff's baseline table
**Then** a findings update in `research/` records the new r20 and first r50 phase splits, the
cumulative effect vs the 761 s / 100.6 s r20 anchors, and the 10-min-at-r50 goal assessed from
measurement (not extrapolation)
**And** the document closes with an explicit, evidence-based recommendation on the deferred deep work —
S5 custom parser, Q4 array-contract (schema v3), and per-stage multiprocess parallelization — routing
whichever are justified through a follow-on correct-course, or recording a reasoned stop
**And** no production code changes in this story

## Epic 15: Rotated-Rectangle Search Areas

Generalizes the search area from a centered square to a **rotated rectangle** so it can hug a
diagonally-oriented range (Belledonne runs SW–NE) and keep off-axis **valley** out of the expensive,
cache-once setup phase — where a north-aligned box would otherwise force full pre-processing of wedges
the solver's `--theta` slope floor rejects anyway. Axis-aligned rectangle and square are the `angle=0` /
equal-extents cases of one unified model — no separate rectangle increment. The solver, validator, climb
detection, and contraction are **geometry-blind and untouched** (the graph *is* the box); the change
lives in the `Area` model, setup fetch, cache key/schema, coverage, CLI flags, validation, and the
render overlay. **Payoff is honest and scoped:** the dominant setup cost is the per-vertex CPU stages
(Epic 14: elevation sampling/resampling/smoothing/metrics), which scale with the area *retained after
truncation to the rotated polygon* and shrink proportionally; OSM (Overpass) and DEM tile fetch are
bbox-oriented sources driven by the rotated box's axis-aligned bounding box, so they shrink less — large
win on the CPU-bound majority, partial win on ingestion. **Backward-compat guardrail:** existing
`--center/--radius` runs stay byte-identical (no golden rebake); the rotated shape gets its own
regression golden (per the AGENTS.md solver/golden policy). Arbitrary free-form polygons remain out of
scope. Inserted via correct-course 2026-07-24 (`sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md`);
no epic renumber.

**FRs covered:** FR1 (generalized to rotated rectangle), FR2 (true-area cap), FR10 (rotated containment).
Supports the whole-range ambition behind NFR1/NFR2 by trimming pre-processed area.

### Story 15.1: Generalize the Area model and geometry helpers

As a developer,
I want the `Area` type and its polygon/bbox helpers to represent a rotated rectangle (square and
axis-aligned rectangle as special cases),
So that all downstream geometry derives from one model with no squareness assumption.

**Acceptance Criteria:**

**Given** an `Area` with center + half-extents + rotation angle,
**When** its polygon is derived,
**Then** corners are computed in a local `cos(lat)` km frame, rotated, and converted back to WGS84; and
`angle=0` with equal extents reproduces today's square ring exactly.

**Given** any `Area`,
**When** the axis-aligned-envelope helper is called,
**Then** it returns the true min/max of the (possibly rotated) polygon and is named/documented as an
*envelope* (over-approximation), not "the region".

**Given** the square shorthand (a single radius),
**When** an `Area` is built from it,
**Then** it maps to equal half-extents at `angle=0` and is indistinguishable from a v1 `Area` downstream.

**Given** the geometry helpers,
**When** unit-tested,
**Then** rotation is verified against known corner coordinates and the degree-space-skew case is covered.

### Story 15.2: Rotated-aware setup fetch, cache schema, and coverage

As a user,
I want setup to fetch and cache exactly the rotated rectangle and queries to resolve coverage against it,
So that off-axis valley is never pre-processed and cached areas are keyed correctly.

**Acceptance Criteria:**

**Given** a rotated `Area`,
**When** setup fetches OSM,
**Then** it uses `osmnx.graph_from_polygon` over the rotated ring (reusing osmnx's existing
`truncate_graph_polygon` path), and the cached graph contains only edges within the rotated rectangle.

**Given** the cache key,
**When** an `Area` is canonicalized,
**Then** a new area **mode** encodes center + half-extents + angle (rounded, alongside the existing
`center_radius` mode), and two areas differing only in angle or extent produce different keys.

**Given** the manifest/index schema,
**When** the new fields are added,
**Then** the schema version is bumped and pre-existing entries re-prepare once (existing invalidation
semantics; no compat shim).

**Given** a query area,
**When** coverage is checked,
**Then** containment tests the rotated polygon via `shapely.contains`, the partial-coverage / "try a
bigger area" messaging is corrected off the scalar-radius assumption, and every bbox-envelope shortcut
(`area_bbox_wgs84` et al.) is audited so coverage does not over-report.

**Given** an existing square entry prepared after the migration,
**When** it is queried,
**Then** results are unchanged from v1.

### Story 15.3: CLI flag surface, validation, and render overlay

As a user,
I want CLI flags to specify a rotated rectangle (with radius still meaning a square) and the report
overlay to draw the true box,
So that the capability is usable and honestly visualized.

**Acceptance Criteria:**

**Given** the `steeproute-setup` and `steeproute` CLIs,
**When** area flags are parsed,
**Then** a rotated rectangle can be specified (width / height / angle) and `--radius` still produces a
centered square; both CLIs accept the identical area surface (FR23).

**Given** the area-cap check,
**When** it validates,
**Then** it uses the true rectangle area (`width × height`), rejecting oversize boxes with a descriptive
`BadCLIArgError` (exit 2).

**Given** a rendered report,
**When** the search-area overlay draws,
**Then** it draws the rotated rectangle, not an axis-aligned proxy.

**Given** the regression suite,
**When** it runs,
**Then** existing square goldens pass untouched (no rebake) and at least one rotated-rectangle golden is
added.

**Given** the docs,
**When** updated,
**Then** the quality-demo params note and README area examples reflect the new flag surface.

## Epic 16: Ownership-Oriented Performance Pass

This epic works the measured end-to-end review in
`research/steeproute-performance-review-gpt-5-6-2026-07-24.md` (reference commit `4380970`, r20
reference workload: center 45.260,5.788, 20 km half-side). Its thesis is **ownership**: the largest
remaining wins are not more NumPy math but ceasing to copy/rebuild immutable graph state — don't copy
a graph the caller has finished with, don't rebuild a graph merely to strip two attributes, don't
re-derive graph-wide sets per route or per worker, don't reconstruct cache data no query consumer
reads. It is also the resumption the parked Story 14.6 anticipated ("revisit via correct-course when
resumed") — a fresh measured review that resolves what-next, anchored at r20 rather than the
originally-imagined r50 probe. Per the 2026-07-24 correct-course decision the **full review is
promoted**, including the two items previously gated to a post-probe correct-course that this review
now supplies evidence for: the schema-v3 geometry-optional cache contract (deferred "Q4", Story 16.3)
and the shared-memory-array solver state (the deferred structural fix,
`research/steeproute-shared-memory-array-solver-design-2026-07-08.md`, Story 16.6). Two deferred
levers remain **out of scope** — the custom Overpass→graph parser (S5-deep, which this review's
Batch C explicitly is *not*) and per-stage multiprocess pipeline parallelization — neither is
addressed by this review; their pickup still routes through a future correct-course.

**Confidence is tiered and the stories say so.** Batch A (16.1) is **proven** — a real-CLI
80.02 → 67.33 s (−15.9%) result with SHA-256-identical output across all 20 files and peak RSS
2.67 → 2.05 GB (−23.4%). Batch B/C (16.2–16.4) have measured isolated components but their acceptance
numbers must come from real-stage replays, not component extrapolation. The shared-array rewrite
(16.6) is a **design, not a measured implementation**, and carries an explicit POC / bit-identity
gate before it can become a default. Bit-identity is the default guardrail throughout;
cache-content-changing stories batch their regeneration to pay one invalidation event; `--workers 1`
behavior stays byte-identical and any golden change is a **single documented rebake, never silent**
(AGENTS.md golden policy). Inserted via correct-course 2026-07-24
(`sprint-change-proposal-2026-07-24-ownership-oriented-performance.md`); no epic renumber.

**FRs covered:** none new — performance work on existing behavior. Supports NFR1 (whole-execution
wall-clock; extends the ≤10-min design target toward r50) and NFR2 (query peak-RSS reduction);
preserves NFR4 (seeded determinism; `--workers 1` default leaves the existing contract unchanged).

### Story 16.1: Query orchestration batch — owned filter, lean contracted graph, one validation context

As a user,
I want the query to stop duplicating graph state it already owns — rebuilding the graph to filter it,
rebuilding it again to strip two attributes, and rescanning it once per route to validate,
So that whole-query wall-clock and peak memory drop with byte-identical output.

**Acceptance Criteria:**

**Given** the query-side `filter_trails` rebuilds a full graph from kept nodes/edges,
`run_parallel_grasp` builds a second "lean" graph via `solver_graph_view` solely to drop `geometry` /
`vertices_resampled`, and `_validate_edges` recomputes `non_exempt_base_segment_ids` (a full
~327k-edge scan) once per returned route
**When** (1a) the query filters the already-operationalized graph in place via an explicit consuming
variant (public copying default preserved for tests/external callers), (1b) the contracted graph is
made lean at construction — or stripped in the CLI immediately after — so `solver_graph_view` is
skipped when the graph advertises the lean contract, and (1c) a single validation context (non-exempt
IDs + base-segment map + per-route metrics) is built once in `validate` and passed to every route/set
check, with `validate_route`'s standalone API preserved by building a context when none is supplied
**Then** output is byte-identical to the pre-epic path on the exact r20 command and committed fixtures
(SHA-256 over all HTML + JSON), the full suite including regression goldens passes untouched, and
rendering/validation semantics are unchanged (renderer expands routes against
`operational_graph` / `vertices_resampled`; validator reads only metrics/tags)
**And** CLI-reported total, external process wall, and peak RSS are recorded before/after on r20
(review anchors: ~80.0 → 67.3 s CLI, ~2.67 → 2.05 GB peak RSS — reproduce the shape, do not promise
the exact number across machines)

### Story 16.2: Setup owned-data cleanup + smoothing/resampling fusion (one content-hash batch)

As a user,
I want setup to stop copying graphs it is about to discard and stop rebuilding an intermediate graph
between smoothing and resampling,
So that setup CPU and peak memory drop, landed as one cache-invalidation cycle.

**Acceptance Criteria:**

**Given** elevation sampling copies the whole graph (~5.4 s @ r20) and extracts per-edge geometry via
per-edge concatenation (~3.8 s vs ~1.5 s with the bulk Shapely coordinate API), smoothing builds a
327k-edge intermediate graph that resampling immediately flattens again (~7.4 s / ~7.8 s of profiled
rebuild), and `_graph_to_payload` copies the whole graph before popping geometry at cache write
(~5.4 s)
**When** a consuming/internal path is added to each (public `inplace=False`/copying default
preserved): `sample_elevation` consumes the owned graph and uses one
`shapely.get_coordinates(..., return_index=True)` call (retaining the current rasterio row/col logic
and first-bad-edge error ordering); smoothing → resampling are fused so coordinates stay flat across
both stages and the graph is built once (`_collect_linestrings` → smooth → resample →
`_build_from_flat`, preserving current operation order); and cache write pops geometry from the owned
graph — all co-landed as a **single** content-hash change with one fixture regen
**Then** coordinates, sampled elevations, and edge metrics are bit-equal to the old paths on the
`grenoble_small` fixture (verified before deleting old code), or where a compensated-`sum` site
prevents it, one documented rebake batched with this story; the full suite including goldens passes;
public API purity is preserved at the stage-function boundaries
**And** per-stage benchmarks are added before the change and measured drops (elevation, smoothing,
resampling, cache write) are recorded in the close-out from a **real setup replay**, not
isolated-component extrapolation

### Story 16.3: Geometry-optional query load and schema-v3 cache (promoted Q4)

As a user,
I want the query to stop reconstructing per-edge geometry it never reads and, where proven safe, the
cache to stop storing post-stage-5 geometry at all,
So that query load and cache size drop.

**Acceptance Criteria:**

**Given** the schema-v2 payload stores ~2.86 M geometry coordinates (~47 MB) inline and `read_entry`
rebuilds ~327k `LineString`s on load (~0.9 s), yet query stages 6–9, contraction, solver, validator,
and output all read `vertices_resampled` and metrics/tags — never the reconstructed `geometry`
**When** (option 1) `read_entry` accepts `with_geometry=False` on the query path to skip
reconstruction immediately, and (option 3, only after proving no supported query/render consumer reads
post-stage-5 geometry) a schema v3 omits geometry from the query-consumed cache entirely — coordinated
with 16.2 so the schema bump and fixture regen are **one** event if they land together
**Then** the query-loaded graph is content-identical for every consumer that runs (same
nodes/edges/attrs/`vertices_resampled`/metrics), the full suite including goldens passes, and setup
still produces geometry where it is genuinely needed during preparation
**And** measured `read_entry` time (and, for schema v3, on-disk entry size) drops on the r20 entry are
recorded; architecture Category 4c (on-disk format) records the decision, and the reversal of Story
13.2's reconstruction assumption is noted — the query does not need reconstructed geometry, so the
*assumption*, not the earlier measurement, is what changed

### Story 16.4: osmnx in-place component / consume ingestion adapter

As a user,
I want osmnx's largest-component and truncate/simplify steps to stop double-traversing and copying
graphs that are owned intermediates,
So that warm setup ingestion CPU drops with a bit-identical graph.

**Acceptance Criteria:**

**Given** osmnx 2.1.0's `largest_component` traverses the graph twice and copies the retained
component (~33.6 s across two calls @ r20 warm), and `truncate_graph_polygon` / `simplify_graph` each
begin with a full copy of what is, inside the `graph_from_point/polygon` pipeline, an owned temporary
**When** a version-guarded ingestion adapter computes the largest weakly-connected component in one
traversal and removes rejected nodes from the owned graph in place (and, where measured to help,
consumes the truncation/simplification inputs) — scoped as a tightly version-pinned adapter around
osmnx's lower-level calls, or upstreamed, **never** an unscoped permanent monkeypatch
**Then** a Story-14.5-style exact old/new diff harness on the cached r20 Overpass response gates the
change: node IDs, edge `(u,v,key)` IDs, relevant attrs, geometry coordinate sequences, and iteration
order must all match; the real graph retains the same node/edge counts (131,793 / 327,911 in the POC);
goldens pass untouched (no rebake expected — graph identity)
**And** warm-ingestion wall-clock and peak RSS are recorded (review anchor: ~132 → 99 s warm
`osm_load`); the osmnx version pin and the private-API risk are documented, and Category 4c is noted
only if on-disk content changes

### Story 16.5: Solver static-context reuse + pure-Python loop cleanup

As a user,
I want each worker / migration round to stop rebuilding immutable solver state and the hot loop to
stop doing discardable work,
So that solver startup and per-iteration cost drop with exactly equal solutions.

**Acceptance Criteria:**

**Given** every new `GraspSolver` rebuilds `base_segment_id_map`, `non_exempt_base_segment_ids`, and
the sorted/junction node pool (adjacency is already cached across rounds), and the hot loop sums an
objective `run` discards, allocates a Jaccard union set, and re-sorts held-route edge IDs on every
`_worst_held`
**When** a `SolverStaticContext` (node pool, segment map, non-exempt IDs, adjacency) is built once and
reused across migration rounds within each worker (and the parent's segment map is reused for
validation), and the five pure-Python loop changes land and are **benchmarked separately** so each
earns its recorded gain: drop the discarded objective sum, exact `j_max==0` (`frozenset.isdisjoint`)
and `j_max==1` fast paths, Jaccard union computed as `|a|+|b|-|∩|` without allocating a union set, and
cached per-solution sort keys
**Then** solutions are exactly equal (`list[Solution]` equality) to the pre-change path across all
quality-gate seeds and the real fixture — not merely equal objective totals; the canonical
`_route_slope_ok(prefix)` gate is **retained** (Python 3.13 `sum` uses compensated summation, so the
manual cumulative arrays are not generally bit-identical — do not remove the gate on the strength of
Story 12.2's prose)
**And** single-process 100k-iter throughput is recorded (review anchor: ~13.58 → 11.43 s, −15.8%) and
the four-worker end-to-end effect is measured at close-out

### Story 16.6: Shared-memory array solver state (structural, POC-gated)

As a developer,
I want one canonical solver state built in the parent and read directly by workers,
So that O(workers) adjacency / object-graph construction and O(workers × graph) steady memory stop
scaling with worker count.

**Acceptance Criteria:**

**Given** each of N workers independently unpickles its own graph and builds identical adjacency
(cProfile attributes adjacency construction on par with useful search at modest budgets), prior
measurement shows throughput flattening and OOM at higher worker counts on the object graph, and this
story starts from a **design** (`research/steeproute-shared-memory-array-solver-design-2026-07-08.md`),
not a measured implementation
**When** the design is implemented in staged order: first place the ~73 MB pickled lean-graph blob in
shared memory, passing only a descriptor to worker initializers (avoids copying the same bytes through
N spawn pipes); then build one canonical CSR-style solver state in the parent that workers read
directly
**Then** the array path is proven **bit-identical** to the current object-worker path before it
becomes a default: `--workers 1` stays exactly today's behavior, and for N>1 the array implementation
is compared against the current worker implementation over all quality-gate seeds and the real fixture
(full solution comparison, not final-objective totals); goldens and NFR4 untouched
**And** worker startup (spawn + state transfer) and peak RSS vs the object path are measured and
recorded; architecture Category 5a is updated; if r50 is no longer a target when this is picked up,
the story records that and stops after the measured shared-blob step

### Story 16.7: r20/r50 re-measure and what-next close-out

As a developer,
I want a consolidated before/after across setup + query and a fresh phase split,
So that the epic's end-to-end effect is recorded from measurement and any residual deep work is scoped
from evidence.

**Acceptance Criteria:**

**Given** Stories 16.1–16.6 have landed with per-story / per-stage benchmarks
**When** I capture a consolidated real r20 setup + query trace (stage lines, CLI-reported and external
process wall, peak RSS) against the review's r20 anchors (setup 299.28 s; query 80.02 s CLI /
90.82 s external / 2.67 GB peak RSS) and, if r50 is still a target, one real r50 setup + query run
**Then** a findings update in `_bmad-output/planning-artifacts/research/` records the new phase splits
and cumulative effect, honestly separating demonstrated combined wins (query batch −12.69 s CLI; warm
ingestion −32.85 s) from components not yet proven as combined stage results
**And** the document closes with an explicit, evidence-based recommendation on the still-deferred deep
levers — the custom Overpass parser (S5-deep) and per-stage multiprocess pipeline parallelization —
routing whichever are justified through a follow-on correct-course, or recording a reasoned stop
**And** no production code changes in this story
