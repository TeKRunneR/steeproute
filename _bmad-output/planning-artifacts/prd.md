---
stepsCompleted: ['step-01-init', 'step-02-discovery', 'step-02b-vision', 'step-02c-executive-summary', 'step-03-success', 'step-04-journeys', 'step-05-domain', 'step-06-innovation-skipped', 'step-07-project-type', 'step-08-scoping', 'step-09-functional', 'step-10-nonfunctional', 'step-11-polish', 'step-12-complete']
inputDocuments:
  - _bmad-output/brainstorming/brainstorming-session-2026-04-17-1047.md
workflowType: 'prd'
classification:
  projectType: cli_tool
  domain: scientific
  complexity: medium
  projectContext: greenfield
---

# Product Requirements Document - bmad-test

**Author:** Yann
**Date:** 2026-04-20

## Executive Summary

A Python CLI that finds the longest hiking and trail-running routes in a user-specified area of the French Alps, subject to a configurable average-slope floor (default ≥20%) and difficulty cap (default SAC T3). Given an area — a center point plus radius, or a GeoJSON polygon — the tool returns up to five genuinely distinct candidate routes, each rendered as a static HTML report with a map and elevation profile, along with summary metrics (length, D+/D−, average gradient).

The product serves a single user (the author) whose training and recreational goals favor cumulative vertical effort over distance, and whose practical need is finding such routes in Alpine regions they haven't yet explored on foot. Widely-used trail tools (Komoot, Strava, AllTrails, OSM routing) are built for navigation, popularity, or aesthetic suggestions — none perform effort-maximizing search under steepness constraints. This project fills that personal gap.

A secondary goal shapes scope and quality decisions: the project is intended as a portfolio artifact demonstrating what a senior generalist engineer and modern AI coding tools can produce together within a realistic personal time budget. Algorithmic substance is the evidence; the collaboration process is the headline.

### What Makes This Special

The product reframes route search as an **effort-maximization problem** rather than a navigation or length problem — a rephrasing that changes the entire solution space. Flat terrain becomes a liability rather than neutral; routes decompose naturally into "pick climbs, then connect them."

The returned top-5 enforces genuine distinctness between candidates via a segment-overlap threshold. This is a direct response to a failure mode observed during a prior implementation attempt, where naive top-N ranking produced five near-identical variations of a single route.

## Project Classification

- **Project Type:** CLI tool (Python)
- **Domain:** Scientific computing / geospatial optimization
- **Complexity:** Medium — regulatory and ecosystem complexity are zero (single user, no compliance, no integrations); algorithmic complexity is the central engineering challenge
- **Project Context:** Greenfield
- **User base:** N=1 by design — scope decisions prioritize author utility over general-audience usability

## Success Criteria

### User Success

The single user experiences the tool as useful when:

- **First-use success**: A newly-defined area produces at least one route whose shape — where it climbs, which trails it uses — is *useful as inspiration* for designing an actual outing in that area. The exact route is not expected to be run as-is; the tool's job is to surface where the vertical lives, not to produce a turn-by-turn plan.
- **Faithful terrain representation**: Reported steepness corresponds to real terrain. No phantom-climbs invented by cliff-adjacent polyline drift — when the tool claims a segment has 25% slope, that climb exists on the ground. (This matters precisely *because* the user is using routes as ideas, not as plans — an idea rooted in a phantom climb is actively misleading.)
- **Top-5 genuine distinctness**: Returned routes are visibly different at the map level without segment-by-segment comparison.
- **Repeat use**: The tool gets used more than once after completion — loose signal that the reframed problem statement actually matches the need. Not a tracked metric; just a sanity check against build-and-abandon.

### Project Goal Success (in lieu of Business Success)

The project has no business dimension (N=1, no revenue, no adoption target). Its non-user success signals are:

- **Portfolio credibility**: The GitHub repo is presentable as an interview artifact — working examples, coherent README, and a clear description of the senior + AI-tools collaboration process.
- **Interview use**: Referenced in at least one interview conversation within a reasonable window post-completion. Qualitative, not a KPI.
- **Completion within a reasonable budget**: ~40 dev hours across ~4 weekends as a design target. As a hobby project, modest overruns are expected and don't undermine the project's goals; the collaboration story survives ordinary schedule slippage.

### Technical Success

- **Query correctness**: All returned routes satisfy hard constraints (slope floor, difficulty cap, edge-reuse limit) by construction, verified by E2E tests on fixtures.
- **Solver quality**: GRASP output sanity-checked against exhaustive enumeration on a small controlled test area — not a claim of optimality, just a claim of non-embarrassing approximation quality.
- **Compute budget**: Typical query completes within the configured time budget on the cached Grenoble box. Queries exceeding the budget are killable, with best-so-far reported.
- **Test coverage**: Three layers present from day 1 — unit (pure primitives), integration (pipeline + cache), E2E (CLI + constraint verification). Specific coverage targets defined during architecture.
- **Cache correctness**: Repeat query reuses preprocessing when inputs are unchanged (area, solver parameters, DEM version, OSM extract date, code commit hash); any change triggers recomputation. See **Domain-Specific Requirements → Data Provenance & Versioning**.
- **Graceful degradation**: When top-5 distinctness cannot be satisfied, the tool returns N<5 with a clear explanation rather than silently returning near-duplicates.

### Measurable Outcomes

| Outcome | Measurement | Target | Source |
|---|---|---|---|
| Constraint satisfaction on returned routes | E2E test pass rate | 100% on fixtures | objective |
| Solver quality vs exhaustive on toy area | (GRASP best) / (exhaustive best) | initial target set during architecture | methodology from brainstorming |
| Query compute time | Wall-clock median across sample areas | ≤ 10 min default | brainstorming |
| Cache hit speedup | Cold-run time vs cached-run time | target refined during architecture | methodology from brainstorming |
| Gallery in README | Pre-computed example count | 3–5 regions | brainstorming |
| Test coverage | pytest-cov on core modules | target refined during architecture | methodology from brainstorming |
| Dev budget | Tracked hours | ≤ ~40h target, overrun tolerance revisited mid-build | brainstorming |

## Product Scope

### MVP (Phase 1)

**Input/output**

- Python CLI accepting `--center lat,lon --radius km`
- Pre-cached ~100km box around Grenoble as the operational region; dynamic query area within it
- Output: per-route static HTML with Leaflet map + Chart.js elevation profile (gradient-colored); route metadata (length, D+, D−, avg gradient)

**Core solver**

- GRASP on a contracted climb-graph; top-5 with Jaccard distinctness filter
- Hard constraints: slope floor θ, SAC difficulty cap, edge-reuse cap
- **Strict containment**: returned routes must stay entirely within the specified search area. Soft containment (allowing short excursions for connectors) is deferred to Phase 2.
- Progress reporting (iterations, best-so-far D++D−, elapsed, rough ETA) with manual kill
- Hard area-size cap (~500 km² initial, tune empirically)

**Data pipeline**

- OSM via `osmnx`, configurable untagged-trails policy (default **include**); a curated set of minor road types (e.g. residential, unclassified, service) is admitted as **connectors** (no SAC grade, never climbs) so routes can cross short paved gaps between trails — the vertical-effort objective self-limits road use
- IGN RGE ALTI 5m DEM sampled via `rasterio`
- Elevation mitigation: DEM-resample + 2D polyline smoothing + moving-median on elevation
- Caching keyed on all inputs affecting output (see **Domain-Specific Requirements → Data Provenance & Versioning**)

**Configurable parameters (defaults)**

- `θ` (route-level avg-slope floor, `(D+ + D−)/length`) = 0.20
- `min_climb_slope` (climb-detection slope threshold, `d_plus/length`) = 0.20
- `difficulty_cap` (SAC scale) = T3
- `L_connector` (short-connector reuse-exemption length threshold) = 200m
- `min_climb_ground_length` (minimum climb length, measured as projected 2D distance along the trail) = 300m
- `J_max` (top-5 Jaccard overlap ceiling) = 0.30
- `N` (result count) = 5
- `area_cap` ≈ 500 km²
- `untagged_trails_policy` = include

**Quality**

- Unit + integration + E2E tests from day 1 (not retrofitted)
- Package structure, type hints
- Dev-time diagnostic visualization of detected climbs (tuning aid)

**Portfolio presentation**

- README with 3–5 pre-computed example results (map screenshots + elevation profile PNGs)
- README framing details (agentic-dev narrative vs algorithmic narrative) deferred — not a PRD concern

### Growth (Phase 2)

- Simulated-annealing polish pass on top candidates
- Cliff gradient *penalty* in ranking (v1 visualizes cliff proximity only, doesn't penalize)
- **Soft containment of search area** — allow routes to briefly leave the user-specified area for short connectors (useful when a connector trail dips outside). Phase 1 uses strict containment.
- GPX export (tentatively in v1 if budget permits, else v2)
- Access-aware endpoint preference (avoid trailheads at nowhere-useful nodes)
- Multi-region support beyond the Grenoble box

### Vision (Phase 3)

- Web app with interactive map-draw area selection
- Comparison views of top-5 side by side
- Richer route characterization / visualization to distinguish routes that are *runnable in good conditions* from those merely *feasible on paper* (e.g. exposure/cliff overlay, sustained-grade vs staircase-grade summaries, traversability indicators)
- Hosted public demo
- Snow-likelihood overlay aggregating recent webcam / observation / satellite-derived snow coverage on trail segments — possibly better as a separate app rather than part of this one; captured here so the idea isn't lost

## User Journeys

### Persona — the sole user (N=1)

Yann: senior engineer and trail runner based in Grenoble. Typical trigger: planning a weekend outing in an Alpine area he hasn't run before; has a rough rectangle in mind but doesn't know the local trail network. Works from a laptop, terminal, willing to wait 10+ minutes for a query. Success = finds at least one route *concept* that surfaces useful information about where steep terrain sits in the area.

**Intended use is exploratory, not prescriptive.** Routes returned by the tool are expected to be impractical to run as-is (awkward trailheads, point-to-point when a loop is wanted, too many chained climbs for a simple Sunday outing). The user treats the top-5 as **ideas of where the vertical lives** and then designs their actual outing manually, borrowing pieces from the tool's output. This framing shapes what matters in the HTML reports: spatial context (maps) is as important as route shape (elevation profile), and reported steepness must correspond to real terrain — a climb that exists only because of DEM-sampling drift is actively misleading.

The journeys below cover three distinct use patterns, all for this user.

---

### Journey 1 — "Sunday outing in a new area" (happy path)

**Context.** Saturday evening. Yann is meeting friends near Briançon tomorrow afternoon and wants to fit a morning run beforehand. Rough area in mind around a specific trailhead, doesn't know the surrounding trail network.

**Interaction.**
1. Opens terminal, invokes CLI with `--center lat,lon --radius 8` and default parameters.
2. Tool announces: preprocessing graph (cache miss — fresh area), then starts GRASP. Progress prints every N iterations: iteration count, best D+ + D− so far, elapsed, rough ETA.
3. ~9 minutes later: tool finishes and writes five HTML reports into a results directory.
4. Opens report #1 in browser: Leaflet map with route polyline on OSM basemap; Chart.js elevation profile colored by gradient; metadata box with length, D+, D−, avg gradient.
5. Flips through reports 2–5. Not asking "which one do I run Sunday" — the routes are likely impractical as-is (awkward trailheads, point-to-point where a loop is wanted, too many chained climbs for a simple outing). Instead, reads them as **exploration of where the vertical lives in this area**. Report #3 surfaces a two-summit ridgeline he hadn't noticed; report #1 exposes a valley-side climb he wouldn't have guessed. Screenshots both, opens a topo map, sketches his actual Sunday route borrowing pieces from them — a loop starting near a known parking area.

**Capabilities revealed**: area specification via `--center/radius`; progress reporting with iteration / best-so-far / ETA; static HTML report per route; map + elevation profile + metadata; spatial context in the map readable enough that the user can see *where* the climbs sit in the area, not just the route line; enough visual distinctness between routes to pick one at a glance; predictable output path and file naming.

---

### Journey 2 — "Sparse area, tuning diversity" (edge case: graceful degradation + iterative re-query)

**Context.** Yann wants to explore a thin-network area near Pelvoux. He suspects the trail density is limited but wants to see what the tool surfaces.

**Interaction.**
1. Runs CLI, 15km radius, default params.
2. GRASP runs ~6 minutes, then reports: *"Only 3 distinct routes satisfy J_max ≤ 0.30. Returning 3 routes; additional candidates would have excessive segment overlap."* The three HTML reports are produced.
3. Reviews the three. Thinks: genuinely sparse, but wants to see what relaxing diversity would add.
4. Re-runs with `--j-max 0.45`. Cache hits on preprocessing → starts GRASP immediately. Returns 5 routes, but with visible duplication on the map.
5. Decides the stricter 3 are more useful than the looser 5. Keeps 3.

**Capabilities revealed**: graceful degradation with a clear explanatory message when N<5 distinct routes are achievable; cache behavior that makes iterative re-querying fast when only the solver parameters change; all relevant parameters exposed as CLI flags; output framing that distinguishes a "constrained" result from a "relaxed" one (or at least makes the J_max used obvious in each report).

---

### Journey 3 — "Compute budget exceeded" (edge case: early termination)

**Context.** Yann gets ambitious and queries a 30km-radius area at default parameters, Friday night.

**Interaction.**
1. Runs CLI. Preprocessing is slower than usual (large graph).
2. GRASP starts. Progress: iterations climbing, best-so-far D++D− inching up slowly, ETA well past his willingness to wait.
3. Around 15 minutes in, hits Ctrl-C.
4. Tool catches the interrupt, writes out the best-so-far top-5 into the results directory with a clear flag in each report's metadata: *"early termination at iteration N, solver not converged"*.
5. Reviews the early-terminated reports. Sees potential, decides to re-run with a smaller area next time. The preserved best-so-far output let him make the decision rather than losing progress.

**Capabilities revealed**: graceful handling of manual interrupt; best-so-far output preserved on early termination with clear not-converged flagging; cache state remains usable (not corrupted) after interrupted preprocessing; progress reporting detailed enough that the user can judge whether to wait or kill.

---

### Journeys Not Applicable

The step template anticipates admin, support, and API-consumer journeys. None apply:

- **Admin / Operations**: no administration layer in an N=1 product.
- **Support / Troubleshooting**: the sole user is also the developer; troubleshooting happens at dev time, not in a separate user journey.
- **API consumer**: no programmatic API; CLI is the only entry point.

If any of these become relevant under v2 (the web-app vision), journeys will be added then.

### Journey Requirements Summary

Capabilities the v1 product must deliver, consolidated from the three journeys:

- **CLI with area specification** (`--center/radius`) and flags for every configurable parameter (`θ`, `difficulty_cap`, `L_connector`, `min_climb_ground_length`, `J_max`, `N`, `area_cap`, `untagged_trails_policy`).
- **Progress reporting** during GRASP: iteration count, best-so-far D++D−, elapsed time, rough ETA — printed at a reasonable cadence.
- **Graceful manual interrupt** (Ctrl-C): best-so-far top-5 written to disk with a "not-converged" flag in metadata; cache state remains valid.
- **Graceful degradation**: when fewer than N distinct routes exist under the current J_max, return the feasible subset with an explanation rather than silently loosening the constraint.
- **Cache behavior**: fast re-query when only solver parameters change (preprocessing cached); atomic cache writes so interrupted preprocessing doesn't corrupt the cache.
- **HTML report per route**: Leaflet map + Chart.js gradient-colored elevation profile + metadata (length, D+, D−, avg gradient, parameters used, convergence status). Map spatial context matters as much as the route line itself — the user reads maps to see *where* vertical sits, not only *which path* was chosen.
- **Predictable output naming** across runs so the user can compare runs easily.

## Domain-Specific Requirements

### Reproducibility & Determinism

Every generated report is reproducible. GRASP uses a seeded random source, configurable via `--seed N` (default unseeded). Seed is recorded in each HTML report's metadata **and** in a machine-readable JSON sidecar alongside each report — the sidecar supports automated regression diffing, the HTML metadata supports human inspection.

**Why it matters for N=1**: enables debugging regressions, reproducing results from older reports six months later, and using generated reports as portfolio evidence (future-you is the user).

**Scope of the determinism guarantee**: same seed + same inputs + same code version → identical route edge-set. Bit-exact floating-point values (elevation gain, etc.) across platforms or Python versions are NOT guaranteed; documented as a known limitation.

### Data Provenance & Versioning

Every generated report is traceable to the exact data and code that produced it. HTML report metadata records: DEM version, OSM extract date, code commit hash, solver parameter hash.

**On version mismatch** (cached data pinned to an older DEM/OSM, or code revision changing the solver): cache is invalidated with a visible warning; the tool does not silently serve results computed under stale inputs.

### Validation & Quality Commitments

The tool must provide ongoing evidence of its own correctness and quality:

- **Constraint validation**: every returned route is validated against declared constraints (slope floor, difficulty cap, edge-reuse cap, Jaccard distinctness, graph membership) before being presented. No route reaches the user without passing validation — or, failing that, being clearly marked as a validation failure (see below).
- **Heuristic-quality bound**: the solver's quality is bounded by at least one automated, reproducible comparison against a known-optimal reference on a small instance, reproduced in CI.
- **Regression protection**: an automated regression suite prevents silent quality degradation between code versions.

Implementation strategy for these commitments — specific test modalities, fixture design, thresholds — is captured in **Appendix A** and finalized during the architecture phase. The commitments above are the product requirements; the appendix is notes.

### Validation-Failure Behavior

When constraint validation catches a violation at runtime, the tool must surface the violation clearly rather than suppress the output:

- The HTML report for the affected route includes a prominent "VALIDATION FAILED" banner naming the violated constraint(s).
- Console output flags the affected route with a descriptive error.
- CLI exits with a non-zero status indicating at least one validation failure occurred.

Failed-validation routes **remain written to disk** (flagged as such) so the user can inspect them and investigate the root cause. They are not counted as valid results in any summary metadata, but they are not hidden either. Directly fixes the previous attempt's failure mode where bad routes slipped through undetected, while preserving the ability to debug what went wrong.

### Performance Envelope

Default-configured queries (Grenoble box, 10km radius, default parameters) should complete within ~10 minutes wall-clock on commodity laptop hardware. This is a **design budget**, not an SLO — it forces tradeoffs during architecture (graph contraction aggressiveness, iteration budget, caching strategy) but isn't a shipping gate. Budget is revisited during implementation if the problem shape differs from expectations.

### Error-Model Documentation

README contains a first-class "Known Limitations" section covering two classes of error:

- **Data-level error**: the DEM / polyline-drift interaction and the resulting cliff-bias risk. Phantom steepness near cliffs is possible; users should cross-check cliff-proximate routes against topo maps before using them as route ideas.
- **Solver-level error**: GRASP is a heuristic, not an optimal solver. The heuristic-quality bound from validation (above) provides one empirical anchor; it does NOT generalize to a claim of optimality on real-scale queries. The tool finds "a good route," not "*the* route."

Optional for v1 (budget permitting): HTML report metadata flags a warning when a route's DEM sampling passes near high-horizontal-gradient cells — lighter than the cliff-detection-with-penalty deferred to v2.

## CLI Tool Specific Requirements

The tool is named **`steeproute`**.

### Project-Type Overview

**Scriptable (non-interactive) Python CLI.** No REPL, no interactive prompts, no subcommands. A single invocation runs a single query; the query produces a set of static output artifacts (HTML reports + JSON sidecars) and exits. Progress is printed during the run; errors go to stderr. Suitable for direct terminal use and for wrapping in shell scripts.

### Technical Architecture Considerations

- **Synchronous, single-process entry point.** Parallelism within the solver is an architecture-phase decision; the CLI surface is unaffected by that choice.
- **No network I/O at runtime.** DEM and OSM data are prepared ahead of time (see Domain-Specific Requirements → Data Provenance). A CLI run operates on local data and produces local files.
- **No persistent state beyond the cache** (see Domain-Specific Requirements → Data Provenance).

### Command Structure

Single command, no subcommands. Canonical invocation:

```
steeproute [AREA] [CONSTRAINTS] [OUTPUT] [SOLVER] [META]
```

Area specification is required:

- `--center LAT,LON --radius KM`

Example:

```bash
steeproute --center 45.0716,6.1079 --radius 10 --output-dir ./results/
steeproute --center 44.68,6.49 --radius 15 --theta 0.18 --seed 42
```

### Output Formats

Three output surfaces:

**Per-route HTML reports** (primary user-facing artifact, one per returned route):

- Standalone static HTML; no server required, no external CDN at runtime (assets bundled).
- Embedded Leaflet map with route polyline on OSM basemap.
- Embedded Chart.js elevation profile with gradient-color coding.
- Metadata block: length, D+, D−, avg gradient, parameters used, seed, DEM version, OSM extract date, code commit hash, convergence status.
- Validation-failure banner when applicable (see Domain-Specific Requirements → Validation-Failure Behavior).

**Per-route JSON sidecar** (machine-readable, one per route):

- Alongside each HTML report, same base name with `.json` extension.
- Contains: raw edge sequence, canonical geographic coordinates, full run metadata, parameter hash.
- Always emitted (not opt-in) to support automated regression diffing and cross-run comparison.

**Stdout / stderr**:

- **Stdout**: progress lines during the run (iteration count, best-so-far D++D−, elapsed time, ETA); final run summary (query params, routes returned vs. N requested, graceful-degradation explanation, validation-failure count, wall-clock total).
- **Stderr**: errors and warnings.
- `--quiet` suppresses progress; the final summary and errors are still emitted.

**Exit codes**:

- `0` — success, all routes valid.
- `1` — at least one returned route failed constraint validation (routes still written to disk, flagged).
- `2` — run failed before producing any output (bad CLI args, missing data, unrecoverable error).
- `130` — interrupted by the user (Ctrl-C); best-so-far preserved.

### Config Schema

All configuration via CLI flags. No config file in v1 (N=1, flag count manageable). Canonical catalog:

**Area (required)**

| Flag | Type | Description |
|---|---|---|
| `--center LAT,LON` + `--radius KM` | float pair + float | Center + radius area mode |

**Constraints (defaults from brainstorming)**

| Flag | Default | Description |
|---|---|---|
| `--theta` | 0.20 | Route-level average-slope floor, `(D+ + D−)/length` |
| `--min-climb-slope` | 0.20 | Min running-average uphill slope (`d_plus/length`) for a segment to count as a climb |
| `--difficulty-cap` | T3 | SAC difficulty ceiling |
| `--l-connector` | 200m | Short-connector reuse-exemption threshold: connectors shorter than this may be reused (bidirectional); all other segments are once-per-route, undirected |
| `--min-climb-ground-length` | 300m | Minimum climb 2D arc length |
| `--j-max` | 0.30 | Top-N pairwise Jaccard ceiling |
| `--n` | 5 | Target result count |
| `--area-cap` | ~500 km² | Hard area-size cap |
| `--untagged-trails` | `include` | Policy for OSM trails without sac_scale |
| `--elevation-smoothing` | (meters; tuned) | Strength of the global elevation smoothing (graph-Laplacian diffusion), in meters — one canonical profile feeds solver, metric box, and plotted curve |
| `--elevation-deadband` | 0 (off) | Hysteresis floor (m): flattens sub-floor up/down reversals out of the elevation profile, reshaping which segments clear the slope thresholds (a route-selection control, not a noise reducer) |

**Solver**

| Flag | Default | Description |
|---|---|---|
| `--seed` | unseeded | Random seed for GRASP |
| `--iter-budget` | TBD (architecture) | Max GRASP iterations |
| `--time-budget` | 10 min | Wall-clock budget (soft) |

**Output / meta**

| Flag | Default | Description |
|---|---|---|
| `--output-dir` | `./results/` | Output directory for HTML + JSON |
| `--progress-interval` | TBD (architecture) | Seconds between progress prints |
| `--verbose` / `--quiet` | off | Log verbosity |
| `--version`, `--help` | — | Standard meta |

**Flag validation**: all flag values are parsed and validated at CLI start. Invalid values produce exit code `2` with a message pointing to the offending flag. No silent coercion.

### Scripting Support

Designed to behave predictably when driven by shell scripts or CI:

- **Stable output filename pattern** (e.g. `route-1.html`, `route-1.json`, `route-2.html`, …): downstream scripts can consume results without discovery.
- **Exit codes** above enable shell-conditional logic.
- **JSON sidecar** always produced, supporting `jq`-style post-processing.
- **Stream separation**: errors on stderr, results/progress on stdout.
- **`--quiet`** mode suppresses progress output for batch use.
- **Idempotency**: same invocation + same seed + same data versions → identical output files (see Domain-Specific Requirements → Reproducibility).

### Data Preparation (`steeproute-setup`)

The primary CLI `steeproute` operates on pre-prepared local data and does not perform network I/O at runtime. A companion CLI `steeproute-setup` handles data preparation:

```
steeproute-setup --center LAT,LON --radius KM
```

It accepts the same area-specification flags as `steeproute`, plus `--output-cache-dir` (default: standard user cache directory). It downloads OSM trail data and DEM elevation samples for the specified area, preprocesses them into the graph structure used by `steeproute`, and writes the result into the local cache.

`steeproute-setup` has no `--area-cap` equivalent — preparing large regions is a deliberate user choice.

Exit codes mirror `steeproute`: `0` on success, `2` on pre-execution error (bad flags, network failure, source unavailable), `130` on manual interrupt.

### Explicitly Out of Scope for v1

- **Shell completion** (bash/zsh): nice-to-have, deferred to v2. Personal tool, flag list is manageable from memory and `--help`.
- **Interactive mode / REPL**: not applicable.
- **Subcommand hierarchy**: not applicable — single operation, single invocation.
- **Visual design / UX principles / touch interactions**: explicitly skipped per project-type config.
- **Config file** (YAML/TOML): deferred; reconsider if flag count grows past ~25 or if parameter presets become useful.

Post-v1 feature ideas are collected in a running backlog at [future-ideas.md](future-ideas.md) — promoted into epics/stories (or a correct-course) if and when picked up.

### Implementation Considerations (Deferred)

Flagged here for visibility, not decided:

- `--iter-budget` and `--progress-interval` defaults depend on empirical measurement of GRASP iteration cost on real Grenoble queries. Set during implementation.
- `--time-budget` enforcement semantics (checked between iterations? between restarts?) defined during implementation.
- CLI framework choice (`click` vs. `argparse` vs. `typer`) affects implementation ergonomics but not the flag surface. Defer.

## Project Scoping & Phased Development

### MVP Strategy & Philosophy

**Classic problem-solving MVP.** The hypothesis under validation is: *does an effort-maximizing route search under a steepness constraint surface genuinely useful route ideas in areas the user doesn't know?* Not "is the UI polished" (N=1, no UI), not "does it scale" (single operational region, Grenoble box), not "does it attract users" (N=1). The MVP proves the core reframe works in practice, and nothing else.

**Resource commitment:** one developer (author, solo), senior generalist with no graph-algorithms specialization. AI coding tools assumed as primary collaborator. Budget: ~40 hours across ~4 weekends as a design target. No third-party dependencies requiring contracts, no paid infrastructure, no hosting.

### MVP Feature Set (Phase 1)

Defined in **Product Scope → MVP (Phase 1)** and detailed in the **User Journeys** section. Summary: `steeproute` CLI that runs effort-maximizing route search on a pre-cached Grenoble box, produces up-to-5 distinct routes as static HTML + JSON sidecars, with validation commitments and provenance metadata. Not duplicated here.

### Phase 2 (Growth) & Phase 3 (Vision)

Defined in **Product Scope → Growth (Phase 2)** and **Vision (Phase 3)**. Not duplicated here.

### Risk Mitigation Strategy

Consolidated from the brainstorming risk register and PRD commitments. Top risks and how this plan addresses each:

| Risk | Severity | Mitigation |
|---|---|---|
| **Scope creep.** Feature creep balloons the work beyond what's enjoyable or finishable. | High | Explicit IN/OUT list in Product Scope. Cut order defined below when specific features start consuming disproportionate time. |
| **Data-pipeline plumbing eats all dev time.** Geospatial I/O, DEM sampling, graph construction consume hours that were meant for the solver. | High | Lean on established libraries (`osmnx`, `rasterio`, `networkx`, `shapely`). Treat the pipeline as plumbing, not craft. |
| **Validation rigor insufficient.** Previous attempt's exact failure mode — can't tell good output from bad output. | High | Explicit Validation & Quality Commitments (constraint validation, heuristic-quality bound, regression protection). Appendix A captures strategy detail for architecture phase. |
| **Cliff-bias in top-5.** Phantom elevation from GPS-drift-induced DEM sampling error over-ranks cliff-adjacent routes. | High | v1: README "Known Limitations" + optional per-result high-gradient flag in HTML metadata. Full cliff detection + penalty deferred to v2. |
| **Parameter tuning affects entire solution space.** `θ`, `min_climb_ground_length`, `L_connector`, `J_max` interact in non-obvious ways. | High | All parameters configurable via CLI flags with documented defaults. Dev-time diagnostic visualization of detected climbs (scope TBD in architecture). |
| **Compute budget blown on large areas.** Preprocessing or solving on oversized areas consumes hours without convergence. | High | Hard `--area-cap` flag (~500 km² default). Progress reporting. Manual kill preserves best-so-far. Soft `--time-budget`. |
| **OSM trail graph disconnects.** Some Alpine areas have disconnected trail components, causing silent route oddities. | Medium | Connected-component analysis at preprocessing time; component sizes reported to the user. |

**Market risks:** not applicable (N=1, no market).

**Resource framing.** The 40h budget is a planning aid, not a deadline. This is a hobby project; modest overruns are normal and don't invalidate the project's goals. The cut order below is a tradeoff guide for when specific features start consuming disproportionate time (or motivation wanes), not a deadline-enforcement tool.

### Cut Order (Tradeoff Guide)

When a specific feature starts consuming disproportionate time or motivation for the project flags, features can be dropped in this order (first to go, last to go):

1. The optional high-gradient flag in HTML report metadata (cliff-proximity warning). Defer to v2.
2. GPX export, if it was added (brainstorming tentatively included it budget-permitting).
3. The optional diagnostic dashboard `(e)` from Appendix A.
4. Reduce README gallery from 3–5 examples to the minimum 3.

**Do NOT cut:**

- Constraint validation and validation-failure behavior.
- Reproducibility (seed handling, JSON sidecars).
- Data provenance metadata.
- The regression test suite.
- Core solver correctness.

These are load-bearing for portfolio credibility. A feature-lean v1 is defensible; a v1 that can't vouch for its own correctness isn't.

## Functional Requirements

### Area Specification & Invocation

- **FR1**: User can specify a search area via center point and radius.
- **FR2**: System rejects search areas exceeding the configured area-size cap with a descriptive error.

### Route Search & Solver

- **FR3**: User can configure the **route-level** average-slope floor — the minimum ratio of total vertical change to total length, `(D+ + D−) / length`, that a returned route as a whole must satisfy.
- **FR3b**: User can configure the **climb-detection slope threshold** — the minimum running-average uphill slope (`d_plus / length`) for a contiguous trail segment to qualify as a climb. Distinct from the route-level floor (FR3): this governs which segments become climbs (pipeline stage 8), while FR3 governs the whole route. (Numbered FR3b to avoid renumbering FR4–FR30; final numbering at PM discretion.)
- **FR4**: User can configure the SAC difficulty ceiling for eligible route segments.
- **FR5**: User can configure the short-connector length threshold below which a linking segment is exempt from the once-per-route reuse limit — short connectors may be reused and traversed in both directions, while every other segment may be used at most once regardless of direction.
- **FR6**: User can configure the minimum ground-length threshold for a segment to count as a climb.
- **FR7**: User can configure the pairwise segment-overlap ceiling for top-N distinctness.
- **FR8**: User can configure the target result count.
- **FR9**: User can configure the policy for untagged OSM trails (include or exclude).
- **FR10**: System searches for routes maximizing total vertical effort (D+ + D−) subject to the configured constraints, with returned routes strictly contained within the specified search area (soft containment deferred to Phase 2).
- **FR11**: System returns up to N distinct routes, where distinctness is defined by a pairwise segment-overlap ceiling measured on the undirected base-trail-segment identity (the same identity as the FR5 reuse limit — two routes differing only in the direction they walk a shared trail are not counted as distinct).
- **FR12**: System gracefully returns fewer than N routes with a clear explanation when the distinctness constraint cannot be satisfied.

### Progress & Interrupt Handling

- **FR13**: System emits progress information during the search — at minimum: iteration count, best-so-far objective, elapsed time, rough ETA.
- **FR14**: System responds to manual interrupt (Ctrl-C) by writing best-so-far results to disk and exiting with a dedicated interrupt exit code.

### Result Output

- **FR15**: System produces one static HTML report per returned route.
- **FR16**: System produces one machine-readable JSON sidecar per returned route alongside the HTML report.
- **FR17**: HTML reports include an interactive map showing the route polyline on an OSM-derived basemap.
- **FR18**: HTML reports include an elevation profile with gradient-color coding along the route.
- **FR19**: Each report records metadata including length, D+, D−, average gradient, all solver parameters used, seed, DEM version, OSM extract date, code commit hash, and convergence status.
- **FR20**: User can configure the output directory.
- **FR21**: System uses a stable, predictable filename pattern for output artifacts across runs.
- **FR22**: System prints a run summary to stdout upon completion including parameters, routes returned vs. N requested, validation-failure count, and wall-clock total.

### Data Preparation

- **FR23**: The project provides a separate CLI, `steeproute-setup`, for preparing OSM and DEM data for a specified area. It accepts the same area-specification flags as `steeproute`.
- **FR24**: `steeproute` fails fast with a descriptive error if the requested query area is not covered by prepared data, instructing the user to run `steeproute-setup` first.
- **FR25**: Preprocessed data is locally cached and reused across runs; the cache is invalidated when any input affecting output changes (DEM version, OSM extract date, area boundaries, or relevant solver parameters).

### Result Validation

- **FR26**: System validates every returned route against all declared constraints (slope floor, difficulty cap, edge-reuse limit, Jaccard distinctness, graph membership) before presenting it to the user.
- **FR27**: When a returned route fails constraint validation, the affected HTML report displays a prominent VALIDATION FAILED banner identifying the violated constraint(s).
- **FR28**: When any route fails constraint validation, the system exits with a dedicated non-zero code while still writing all results (including failed ones) to disk.

### Scripting & Reproducibility

- **FR29**: User can supply an explicit random seed that, together with identical inputs and code version, produces identical output route edge-sets; the seed used is recorded in each HTML report's metadata and in each JSON sidecar.
- **FR30**: System uses distinct exit codes for success, validation failure, pre-execution error, and user interrupt.

## Non-Functional Requirements

The following quality attributes constrain the product. Categories deemed not applicable are listed explicitly at the end of this section so their omission is intentional rather than oversight.

### Performance

- **Compute budget**: design target of ≤ 10 minutes wall-clock for a typical query (Grenoble box, 10km-radius area, default parameters) on commodity laptop hardware. Detailed in **Domain-Specific Requirements → Performance Envelope**. Not a hard SLO.
- **Memory**: typical query runs comfortably on a commodity 16 GB laptop. Operational region size (e.g. the pre-cached Grenoble box at 5m DEM resolution) is the primary memory-pressure driver; if memory becomes a constraint, the lever is operational region size, not algorithmic tweaks.

### Reliability

- **Graceful interrupt**: Ctrl-C during a run preserves best-so-far output and leaves the cache in a valid, reusable state.
- **Determinism under seed**: same `--seed` + same code version + same prepared data → identical output route edge-sets. Bit-exact reproducibility (exact floating-point elevation sums, etc.) is explicitly **not** guaranteed; documented as a known limitation. See **Domain-Specific Requirements → Reproducibility & Determinism**.
- **Cache integrity**: cache writes are atomic. An interrupted preprocessing or search run does not leave the local cache in a corrupted state.

### Integration

- **Data sources**: `steeproute-setup` integrates with OpenStreetMap and a high-resolution DEM source at setup time. When a source is temporarily unavailable, `steeproute-setup` exits with a clear, actionable error rather than hanging or silently producing partial data.
- **Output integration**: JSON sidecars and documented exit codes enable downstream consumption by shell pipelines and CI. Captured as FRs in **Scripting & Reproducibility** (FR29–FR30) and detailed in **CLI Tool Specific Requirements → Scripting Support**.

### Portability

- **Primary platform**: Windows. The tool is developed and tested on Windows.
- **Secondary platform**: Linux is expected to work (no platform-specific code deliberately introduced), but is not actively tested. Linux-only issues are not a v1 quality gate.
- **Not targeted**: macOS. Not part of the v1 quality contract; may work incidentally but is not a commitment.
- Edge-set reproducibility under seed holds within a single platform; bit-exact floating-point reproducibility across platforms or Python versions is explicitly not guaranteed (see Reliability).

### Categories Explicitly Not Applicable

- **Security**: N=1 personal tool, no authentication layer, no network I/O at runtime, no sensitive data, no PII, no regulated information. Standard developer hygiene (don't commit secrets) applies to the project but does not rise to NFR level.
- **Scalability**: single user, single machine, no traffic patterns, no multi-tenancy considerations.
- **Accessibility**: CLI for sole-author use; static HTML output is for the author's own consumption, not broad audiences; WCAG does not apply. If the Vision-phase public web app ever ships, accessibility reopens.

## Appendix A — Validation Strategy Notes (Informational)

Captures design thinking from a multi-agent review of the Validation & Quality Commitments in the main body. **Non-binding.** The product requirements are the outcome commitments in Domain-Specific Requirements; this appendix is options-with-tradeoffs, intended as a starting reference for the architecture and testing-plan phases.

### Testing modalities considered

**(a) Constraint invariants — post-solve assertions on every returned route.**
Design refinement: implement as the construction contract of the `Route` output type (validated at instantiation, runtime postcondition) rather than as a separate test harness, so no code path can skip them. Concrete shape: `assert_solution_valid(sol, graph, params)` invoked at solver finalization; tests assert it is wired in.

**(b) Metamorphic tests — logical invariants across transformed inputs.**
Scope-disciplined list (resist expansion without explicit justification):

- Relax θ (slope floor) → best objective monotone non-decreasing.
- Relax J_max → best objective monotone non-decreasing.
- Relax difficulty cap → best objective monotone non-decreasing.
- Increase GRASP iteration budget → best objective monotone non-decreasing.
- Scale elevation by k → objective scales by k.
- Adding an edge → best objective non-decreasing.
- Graph isomorphism (relabel node IDs) → identical objective (catches ID-order bugs).
- Duplicate-seed run → identical result (reproducibility cross-check).

**(c) Exhaustive comparison on a small instance — ground-truth reference via brute-force enumeration.**
Framing caution: interpret this as a **regression signal** ("GRASP hasn't regressed between commits on this fixture") not a **quality signal** ("GRASP achieves N% of optimal, therefore good"). The ratio does not transfer from a toy instance to real-scale Alpine queries.

**(d) Regression tests on pinned real queries — 2–3 representative areas with fixed seed.**
Hash scheme: `(objective, D+, D−, edge_count, canonical_edge_sequence_hash)`. Hashing the canonical edge-set in addition to stats is important — stats can collide while the underlying route silently changes. Tolerance: none — seeded GRASP is deterministic; any drift is behavior change worth noticing. Update process: goldens updated deliberately via an explicit command (e.g. `make update-regression`) with commit-message rationale, to prevent tests drifting into rubber-stamp status.

**(e) Diagnostic run statistics — iterations-to-best, fraction-of-feasible iterations, route-length distribution, etc.**
Not a test, a dev aid. Optional.

### Oracle correctness

The exhaustive enumerator used in (c) must itself be tested — otherwise GRASP is validated against an unvalidated oracle. Include 2–3 tiny hand-verified graphs (5 nodes, known optimum) as unit tests for the enumerator.

### Property-based tests

Consider `hypothesis` on graph-construction primitives (edge contraction, elevation assignment, Jaccard computation). Cheap, high-ROI, catches boring bugs before they corrupt solver validation.

### Toy-area fixture — programmatic vs handcrafted

Decision deferred to architecture phase.

**For programmatic / synthetic** (majority view from the design review):

- Parameterizable — enables sweeping variants (dense/sparse edges, elevation variance, cycle sizes).
- No coupling to DEM/OSM snapshot stability; regeneratable forever in CI.
- At 30 nodes, "realistic topology" isn't achievable anyway. Real topology is validated by (d) on real queries.

**For handcrafted / real-data** (minority view):

- Programmatic generation tends to produce graphs that satisfy the author's assumptions and miss the weird topology that breaks solvers in practice.

**Possible compromise**: programmatic as the primary CI fixture; one tiny real-data fixture as an additional regression pin (folds into d).

### Open question for architecture phase

Concrete CI threshold for the GRASP-ratio check on the toy instance: e.g. "fail CI if GRASP best / exhaustive best < 0.90", or drop the gated check. Without a threshold the comparison is decorative.
