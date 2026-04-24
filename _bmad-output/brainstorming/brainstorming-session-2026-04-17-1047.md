---
stepsCompleted: [1, 2]
inputDocuments: []
session_topic: 'Personal hiking / trail-running route finder that maximizes length subject to a minimum-average-steepness constraint, with map + elevation visualization; personal use, portfolio-publishable'
session_goals: 'Flesh out concept toward a PRD — sharpen problem, expand solution space, define MVP scope, surface risks & unknowns'
selected_approach: 'ai-recommended'
techniques_used: ['Question Storming', 'First Principles Thinking', 'Reverse Brainstorming', 'Resource Constraints']
ideas_generated: []
context_file: ''
---

# Brainstorming Session Results

**Facilitator:** Yann
**Date:** 2026-04-17

## Session Overview

**Topic:** Personal hiking / trail-running route finder that maximizes route length within a user-specified geographical area, subject to a minimum-average-steepness constraint (e.g. ≥20% average slope → ≥1000m D+ and ≥1000m D- per 10km), with anti-backtracking logic. Visualization via map + elevation profile. Personal tool, but intended to be portfolio-publishable.

**Goals:** Produce enough concept clarity to write a PRD next, covering:
- Problem sharpening
- Solution space (algorithmic, data, UX)
- MVP scope (v1 vs. later)
- Risks & unknowns

### Session Setup

Solo brainstorming session with Yann as originator of the concept. AI facilitator running a progressive flow from divergent framing to convergent MVP definition.

## Technique Selection

**Approach:** AI-Recommended Techniques

**Recommended Sequence:**

- **Phase 1 — Question Storming** *(deep)*: Surface every question a PRD would need to answer before generating answers. Expose hidden assumptions.
- **Phase 2 — First Principles Thinking** *(creative)*: Decompose the problem to fundamentals — what inputs, outputs, constraints, algorithmic primitives actually exist. Prevents inheriting UX/patterns from existing tools without intent.
- **Phase 3 — Reverse Brainstorming / Pre-mortem** *(creative)*: Stress-test by asking how it fails. Surface data, algorithmic, and portfolio-publishing risks.
- **Phase 4 — Resource Constraints** *(structured, optional)*: Force MVP scoping by imposing severe constraints ("one weekend", "one region", etc.).

**AI Rationale:** Session targets a PRD, so progression goes diverge → converge. Techniques were selected specifically for an algorithmic/data-driven problem where hidden assumptions (about trails, areas, backtracking, etc.) are the primary risk. Broad creativity techniques (SCAMPER, Mind Mapping) were rejected as too generic for this problem shape.

## Phase 1: Question Storming — Results

### PRD Facts (resolved)

**Scope & User**
- Personal use only, single user (Yann)
- French Alps as initial and sole geographic scope
- Supports hiking and trail-running use cases uniformly (difficulty cap handles the split)
- Difficulty cap: ≤ T3 (SAC scale), configurable

**Objective**
- Primary: **maximize D+ + D-** (total vertical effort), not length
- Subject to avg slope floor: `(D+ + D-) / length ≥ 0.20`, configurable
- Return **top-5 distinct routes**, not single optimum
- Approximate solutions acceptable ("decent, not provably optimal")

**Distinctness constraint**
- Metric: segment-overlap via length-weighted Jaccard ≤ ~30% between any two top-5 routes
- Specifically solves a previously observed failure mode (top-5 = near-duplicates of same route)

**Route constraints**
- Trails + roads allowed; no off-trail
- Start/end anywhere (loops and point-to-point both valid)
- Routes must be mostly within user area (soft containment), not strictly bounded
- Edge reuse allowed only for short connectors (parameterization open)
- Hard floor on avg slope, configurable

**Area definition**
- Center + radius OR polygon drawn on a map
- Size bounded implicitly by compute budget ("whatever fits in 10-15 min")

**Compute**
- 10-15 min per query max, with progress indicator
- Deterministic preferred, not required

**Output**
- Per route: length, D+, D-, avg ± slope, elevation profile (line + gradient color coding), map plot
- GPX export: nice-to-have
- No comparison views, no route editing, no in-app tuning of results

**Deployment**
- Web app preferred; CLI acceptable if simpler
- Portfolio target: GitHub repo + README + result examples
- No hosted public demo required
- No live data (closures, weather)

**v1 definition** (user's words): "Define an area → compute top-5 steep routes → visualize them."

### Open questions advanced to Phase 2

- **A.** Algorithmic core — how to approximate NP-hard longest-walk variant
- **B.** Data sources for Alps — trail graph and DEM selection
- **C.** GPS-noise elevation smoothing — two independent error sources (bad Z on good XY; good Z on drifted XY)
- **D.1** Short-connector definition — what makes a segment reusable
- **D.2** "Mostly within area" soft containment formalization
- **E.** Viz stack — map + elevation profile library choices

### Retired questions (no longer PRD-relevant)

- Route direction preference — invariant under D++D- objective
- Min/max route length — not bounded, just ranked
- Comparison views, route editing, live data, hosted public demo — out of scope

## Phase 2: First Principles Thinking — Results

### Problem formalization

Route = a **walk** `W = (e₁, ..., eₖ)` through the trail/road graph G(V,E).

```
Maximize:    Σ (D+(eᵢ) + D-(eᵢ))                [total vertical effort]
Subject to:  Σ (D+(eᵢ) + D-(eᵢ)) / Σ L(eᵢ) ≥ θ   [avg slope floor]
             difficulty(eᵢ) ≤ T3
             count(e in W) ≤ K(L(e))             [edge-reuse cap]
             W mostly within user area           [soft containment]
             top-5 set: pairwise Jaccard overlap ≤ J_max
```

### Structural insights

- **Flat edges are a liability, not neutral**: any edge with gradient < θ drags the route toward ineligibility. The algorithm has incentive to minimize flat-edge usage.
- **The problem decomposes into "pick climbs, then connect them"**: optimal routes look like chains of steep segments linked by minimal connectors.
- **Edge-reuse cap is what makes the problem finite**: without it, D+ + D- is unbounded. Proposed parameterization: `K(e) = 2 if L(e) ≤ L_connector (≈200m), else K(e) = 1`.

### Problem class

Orienteering Problem (Selective TSP) on a contracted graph where nodes are climb endpoints and edges are either climbs themselves or shortest-path connectors between them. NP-hard but well-studied with mature heuristics.

### Committed algorithmic approach

**GRASP** (Greedy Randomized Adaptive Search Procedure) as the core solver.

- Run many randomized-greedy insertions, parallel across CPU cores
- Track top-5 distinct solutions (diversity filter by Jaccard ≤ `J_max`)
- Anytime algorithm — satisfies the progress-indicator + compute-budget requirement
- Optional simulated-annealing polish pass on top candidates

Rationale: simplicity, anytime behavior, parallelism, natural diversity via random restarts, good literature performance on orienteering instances, and a clean portfolio story.

### Committed data pipeline

1. OSM → filter `highway=path|footway|track|bridleway|service|residential`; filter `sac_scale ≤ demanding_mountain_hiking`; clip to area
2. Resample each edge's geometry every ~10m
3. Sample elevations from **IGN RGE ALTI 5m** DEM at each resampled vertex
4. Smooth elevation profile (moving-median, window ~5)
5. Compute per-edge L, D+, D-, gradient
6. Detect "climbs" (contiguous segments with gradient ≥ θ, length ≥ `min_climb_length`)
7. Build contracted graph
8. Cache by (area, θ, T3 cap, DEM version)
9. Query → GRASP → expand top-5 back to raw geometry → render

### GPS-noise error model (corrected after pushback)

Two independent error sources:

- **Source 1 — bad Z on good XY:** altimeter/contribute-time errors attached to otherwise-correct geometry. **DEM-resampling fixes this fully.**
- **Source 2 — good Z on drifted XY:** OSM polyline itself is GPS-drifted (±3m typical); DEM-sampling at wrong horizontal position → wrong elevation. DEM-resampling does **not** fix this. Magnitude = horizontal_drift × local_vertical_gradient; negligible on moderate slopes, significant near cliffs (10-15m spikes, possibly sustained if drift is persistent).

Ranking bias: phantom D+ inflates near cliffs → algorithm may systematically over-rank cliff-adjacent routes. Bounded but real.

**Mitigation stack for v1:**

- Layer 1: DEM-resample (fixes Source 1)
- Layer 2: 2D polyline smoothing (Ramer-Douglas-Peucker ~5m tolerance or moving-average)
- Layer 3: Moving-median on elevation profile (window ~5)
- Layer 4 *(deferred to v2)*: cliff detection via DEM perpendicular gradient, flag/penalize exposed routes

### PRD parameters surfaced

| Parameter | Default | Role |
|---|---|---|
| `θ` (avg slope floor) | 0.20 | Eligibility |
| `difficulty_cap` (SAC) | T3 | Eligibility |
| `L_connector` (reuse threshold) | 200m | Anti-degenerate |
| `min_climb_length` | 300m (tentative) | Climb detection |
| `J_max` (top-5 overlap) | 0.30 | Diversity |
| DEM resolution | 5m (RGE ALTI) | Fixed for France |
| Resample step | 10m | Geometry resolution |
| `N` (result count) | 5 | Output |
| Compute budget | 10 min default | Query time |

### Open questions advanced to Phase 3

- **D.1** `L_connector` value and whether length-based or reuse-count-based
- **D.2** Soft-containment formalization — how to penalize excursions outside user area
- **E.** Viz library choices — still open
- **F.** Cliff-exposure handling — accept bias in v1 or invest in detection

## Phase 3: Reverse Brainstorming (Pre-mortem) — Results

### Risk register + v1 decisions

| ID | Risk | Severity | Decision |
|---|---|---|---|
| D1 | `sac_scale` untagged trails in parts of Alps | 🔴 | Policy is a **config toggle**. Default conservative (exclude untagged). Diagnostic log of omitted edges. No fallback proxy. |
| D2 | Cliff bias in top-5 (Source-2 error systematically over-ranks cliff-adjacent routes) | 🔴 | **v1 mitigation:** overlay DEM gradient visually on the map so cliff proximity is visible in result inspection. Full detection + penalty deferred to v2. |
| D3 | IGN RGE ALTI availability / border gaps | 🟡 | Cache tiles locally once per region. COPDEM 10m as cross-border fallback. |
| D4 | OSM trail graph disconnects | 🟡 | Connected-component analysis at preprocessing. Report component sizes. |
| A1 | Jaccard diversity filter can't reach N=5 in constrained areas | 🟡 | Graceful degradation: return N<5 with explanation, or progressively relax `J_max`. |
| A2 | Climb detection tuning affects entire solution space | 🔴 | **`min_climb_length`, `θ`, `L_connector` all configurable.** `min_climb_length` defined as **2D polyline arc length** (default 300m). Dev-time visualization of detected climbs as tuning aid. |
| A3 | Degenerate "barbell" routes | 🟡→🟢 | **Accepted for v1.** Re-examine if observed in practice. |
| A4 | GRASP quality ceiling (no formal optimality guarantee) | 🟢 | Benchmark against exhaustive search on small toy area as sanity check. User already accepted approximate. |
| P1 | Compute budget blown on large areas | 🔴 | **Hard cap on area size** (tune empirically, initial ~500 km²). **Progress reporting + manual kill** (iterations, best-so-far D++D-, elapsed, rough ETA). **Strong anytime guarantee dropped** — GRASP is naturally iteration-parallel so best-so-far is free, but we won't promise graceful early-stop. |
| P2 | Preprocessing time dominates repeat runs | 🟡 | Aggressive cache keyed on `(area_hash, θ, T3_cap, DEM_version)`. Recompute only when parameters change. |
| S1 | Scope creep kills v1 | 🔴 | Phase 4 is the direct mitigation. |
| S2 | Data-pipeline plumbing eats all dev time | 🔴 | Lean on libraries: `osmnx`, `rasterio`, `networkx`, `shapely`. Pipeline is plumbing, not craft. |
| S3 | Build-it-and-abandon | 🟢 | Accepted. Portfolio value is independent of daily use. |
| PO1 | GitHub-only presentation hard to evaluate | 🟡 | README opens with a gallery of pre-computed example results (3-5 regions, GPX + screenshots). |
| PO2 | Looks like "another trail app" | 🟡 | README leads with the *problem structure* (NP-hard orienteering variant, GRASP, DEM-aware) not the product. |
| PO3 | Research-script code quality | 🟡 | Proper package structure, type hints, tests on geometric/elevation primitives. Incremental, not at the end. |
| C1 | Routes end at nowhere-useful nodes (no access) | 🟡 | v2. User visually inspects top-5 for accessibility. |
| C2 | Cross-border routes (Swiss/Italian) | 🟢 | Clip to France in v1, or COPDEM fallback. |
| C3 | "Maximum vertical effort" correlates with dangerous terrain | 🟡 | Document the assumption in README. User verifies every route against topo map before going. |

### Key decisions confirmed in Phase 3

- **`min_climb_length` is measured in 2D polyline arc length** (not horizontal distance, not D+). Implicit coupling with θ: at θ=0.20 and L=300m, each climb has ≥60m D+ by definition.
- **Anytime guarantee downgraded** to "progress-visible, manually-killable". GRASP iteration parallelism makes best-so-far cheap.
- **Cliff bias mitigation for v1** = *visualize cliff proximity*, not penalize. Hands the judgment to the user.
- **All detection parameters configurable** (θ, `min_climb_length`, `L_connector`, `J_max`, area cap).

## Phase 4: Resource Constraints (MVP) — Results

### v1 scope statement (final)

> A Python CLI that, given a `--center lat,lon --radius km` or a `--polygon` GeoJSON path within a pre-cached ~100km box around Grenoble, produces **up to 5** genuinely distinct hiking routes (fewer if the Jaccard diversity constraint cannot be satisfied with decent candidates) maximizing **D+ + D-** subject to a configurable average-slope floor (default `θ`=20%) and SAC-scale cap (default T3, untagged-trails policy configurable). Each route is rendered into a static HTML report containing a Leaflet map overlay and a Chart.js elevation profile with gradient color coding. The implementation uses **GRASP** on a contracted climb-graph built from **OSM trails** + **IGN RGE ALTI 5m** DEM samples, with aggressive caching keyed on `(area_hash, θ, T3_cap, DEM_version)`. Progress (iterations, best-so-far D++D-, elapsed, rough ETA) is reported; the user can manually kill long runs. **Automated tests (unit, integration, E2E) are a hard requirement from day 1** — specific coverage targets and fixtures defined alongside the architecture.

### What's IN v1

**Core:**
- Python CLI, input via `--center/radius` or `--polygon`, output to static HTML report
- Pre-cached data box around Grenoble (~100km), dynamic query area within it
- Up to 5 routes (target, not floor); graceful degradation with explanation if fewer
- Maximize D+ + D- under hard avg-slope floor + T3 cap
- GRASP solver on contracted climb-graph
- Jaccard diversity filter
- Progress reporting + manual kill; no strong anytime guarantee
- Hard cap on area size (initial ~500 km², tune empirically)

**Data:**
- OSM via `osmnx`, filter `highway=path|footway|track|bridleway|service|residential`, `sac_scale` filter with configurable untagged-trails policy (default exclude)
- IGN RGE ALTI 5m DEM, downloaded once for the box, sampled via `rasterio`
- Elevation mitigation stack: DEM-resample, 2D polyline smoothing, moving-median on elevation profile
- Cache preprocessed graph by `(area_hash, θ, T3_cap, DEM_version)`

**Configurable parameters (with defaults):**
- `θ` = 0.20, `difficulty_cap` = T3, `L_connector` = 200m, `min_climb_length` = 300m (**2D polyline arc length**), `J_max` = 0.30, `N` = 5, area cap ≈ 500 km², untagged-trails policy = exclude

**Viz (per route in HTML report):**
- Leaflet map with route polyline
- Chart.js elevation profile with gradient color coding
- Route metadata: length, D+, D-, avg gradient ± signs

**Tests — policy (specifics deferred to architecture):**
- Testing is a hard requirement from day 1, not retrofitted
- Expected coverage layers: **unit** (pure-functional primitives — geometry, elevation math, scoring), **integration** (multi-stage pipeline behavior, cache round-trip, solver correctness on controlled inputs), **E2E** (CLI smoke + constraint-validation on generated routes)
- Concrete fixtures, module boundaries, and assertion targets are defined during architecture/implementation, not here — premature without knowing actual module shape
- Rationale: agentic implementation by Claude requires autonomous self-verification; tests are the primary signal that code is correct without a human in the loop each step

**Portfolio:**
- GitHub repo + README opening with gallery of 3-5 pre-computed example results (GPX + map screenshots + elevation profile PNGs)
- README leads with problem structure (NP-hard orienteering variant, GRASP, DEM-aware), not the product
- Proper package structure, type hints, tests — from the start

### What's OUT of v1

- Web app / interactive map-draw area selection (CLI-only)
- Simulated-annealing polish pass on top candidates
- Cliff gradient overlay + penalty (visualize only in v1; penalty in v2)
- Multi-region / general-Alps support (Grenoble box only in v1)
- GPX export (5h slack permitting; otherwise v2)
- Access-aware start/end preference
- Live data, trail closures, weather, seasonality
- Comparison views, route editing, hosted public demo

### Budget commitment

~40 dev hours total across 4 weekends. If preprocessing plumbing overruns, cut cliff visualization and GPX export before touching tests or GRASP quality.

### Libraries to lean on (S2 mitigation)

- `osmnx` — OSM → graph
- `rasterio` — DEM I/O and sampling
- `networkx` — graph primitives
- `shapely` — geometry operations
- `leaflet` (front-end) + `chart.js` — static HTML viz
- `pytest` — test runner
- `click` or `argparse` — CLI

Treat data pipeline as plumbing, not craft. Algorithmic depth is the portfolio value-add, not pipeline engineering.


