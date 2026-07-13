---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
inputDocuments:
  - _bmad-output/planning-artifacts/prd.md
  - _bmad-output/brainstorming/brainstorming-session-2026-04-17-1047.md
workflowType: 'architecture'
project_name: 'bmad-test'
user_name: 'Yann'
date: '2026-04-22'
lastStep: 8
status: 'complete'
completedAt: '2026-04-23'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements (30 total, 7 categories):**

- Area Specification & Invocation (FR1–2): center/radius area input with hard area-cap rejection.
- Route Search & Solver (FR3–12): eight tunable constraint/parameter knobs, strict containment, top-N with distinctness filter and graceful degradation when fewer than N distinct routes exist.
- Progress & Interrupt Handling (FR13–14): live progress emission and Ctrl-C path that preserves best-so-far and exits with a dedicated code.
- Result Output (FR15–22): per-route HTML (Leaflet + gradient-colored Chart.js profile) + JSON sidecar + stable filename pattern + run summary on stdout.
- Data Preparation (FR23–25): separate `steeproute-setup` CLI; `steeproute` must fail fast if prepared data doesn't cover the query area; cache invalidates on any input change affecting output.
- Result Validation (FR26–28): every returned route validated against all declared constraints before being presented; failed routes are still written to disk, flagged in the HTML banner, and the process exits non-zero.
- Scripting & Reproducibility (FR29–30): explicit seed produces identical edge-sets; distinct exit codes for success / validation failure / pre-execution error / interrupt.

**Non-Functional Requirements:**

- **Performance**: design target of ≤10 min wall-clock for typical queries (Grenoble 10km-radius, default params) on a 16 GB commodity laptop. Soft budget, not an SLO — budget-breaking is allowed, silent budget-breaking is not.
- **Reliability**: graceful Ctrl-C with best-so-far output + valid cache state; seeded determinism at the edge-set level (bit-exact floats explicitly NOT guaranteed); atomic cache writes.
- **Integration**: OSM + high-res DEM consumed at setup time only; actionable error (not hang) when a source is temporarily unavailable during setup. JSON sidecars + documented exit codes enable shell/CI downstream consumption.
- **Portability**: Windows is the primary development/test platform; Linux is expected to work but is not actively tested; macOS is not a v1 commitment.
- **Explicitly not applicable**: Security (N=1, no auth, no network at runtime, no sensitive data), Scalability (single user/single machine), Accessibility (private author-only reports; reopens if the v3 public web app ever ships).

**Scale & Complexity:**

- **Algorithmic complexity: high** — NP-hard orienteering variant, heuristic solver with quality-bound obligations, DEM/polyline interaction error model with documented cliff bias, multi-modal validation strategy (invariants, metamorphic, exhaustive-on-toy, pinned regression).
- **Ecosystem complexity: low** — single user, no auth, no compliance, no multi-tenancy, no runtime integrations, no hosting.
- **Primary domain**: scientific-computing CLI / geospatial optimization.
- **Complexity level**: medium overall (high algorithmic × low ecosystem).
- **Estimated top-level architectural components**: ~6 (data-prep CLI, query CLI, data pipeline, graph/climb preprocessing, GRASP solver + validation, HTML/JSON output rendering) — refined in later steps.

### Technical Constraints & Dependencies

**Already committed in the PRD / brainstorming (inputs, not open questions):**

- **Language/runtime**: Python. Synchronous, single-process CLI entry point for both `steeproute` and `steeproute-setup`.
- **Topology**: two separate CLIs sharing a local cache. `steeproute` performs no network I/O at runtime; all network/prep is handled by `steeproute-setup`.
- **Data sources**: OpenStreetMap (trails), IGN RGE ALTI 5m DEM (elevation).
- **Core libraries**: `osmnx` (OSM → graph), `rasterio` (DEM I/O + sampling), `networkx` (graph primitives), `shapely` (geometry), `pytest` (tests). Leaflet + Chart.js in static HTML output (no CDN at runtime, assets bundled).
- **Algorithmic approach**: GRASP on a contracted climb-graph; top-N with length-weighted Jaccard distinctness filter; simulated-annealing polish explicitly deferred to v2.
- **Elevation error mitigation stack**: DEM-resample + 2D polyline smoothing + moving-median on elevation profile.
- **Output contract**: per-route static HTML (Leaflet map + Chart.js gradient-colored profile + metadata banner) + per-route JSON sidecar, stable filename pattern, run summary on stdout, errors on stderr, exit codes {0, 1, 2, 130}.
- **Cache key**: must include all inputs affecting output — DEM version, OSM extract date, area boundaries, relevant solver parameters, and code commit hash.
- **Provenance surface**: HTML metadata + JSON sidecar must carry the seed, DEM version, OSM extract date, code commit hash, parameter hash, and convergence status.

**Deferred to this architecture phase (open decisions flagged by the PRD):**

- CLI framework selection (`click` vs `argparse` vs `typer`) — affects ergonomics, not surface area.
- `--iter-budget` default, `--progress-interval` default, `--time-budget` enforcement semantics (checked between iterations vs. between restarts).
- GRASP parallelism strategy within the solver (processes / threads / none).
- Diagnostic-visualization scope (climb detection tuning aid).
- Cache-hit speedup target.
- **Testing & solver-validation strategy** — test layer boundaries (unit / integration / E2E), coverage targets per layer, toy-area fixture approach (programmatic vs. handcrafted vs. hybrid), exhaustive-enumerator oracle design, metamorphic-invariant set and where it runs, pinned-regression goldens scheme and update process, CI threshold for GRASP-vs-exhaustive ratio (or dropping the gate), property-based tests on geometric/elevation primitives. **Starting material: PRD Appendix A** (non-binding notes parked during PRD creation for this phase to pick up).

### Cross-Cutting Concerns Identified

These concerns touch multiple components and must be designed coherently rather than per-module:

- **Provenance & versioning chain** — the same `(DEM version, OSM extract date, area, solver params, code commit hash)` tuple feeds the cache key, the HTML metadata block, and the JSON sidecar. Any module boundary that drops a field breaks reproducibility and cache correctness.
- **Determinism under seed** — the seed threads from CLI parsing through the GRASP solver and must be recorded in every output artifact (HTML + JSON). Architecture must keep the RNG source explicit, not ambient.
- **Runtime route validation (FR26–28)** — every returned route must be validated against all declared constraints before being presented; failed routes remain on disk with a prominent banner, and the process exits non-zero. Implementation pattern (e.g. a `Route` construction postcondition, a separate validator stage, a combination) is an open design decision for a later step. Touches solver finalization, output rendering, and the CLI exit-code logic.
- **Interrupt-safety & cache atomicity** — Ctrl-C during preprocessing or solving must leave the cache valid (no partial writes) and must write best-so-far output with a "not-converged" flag. Combined: preprocessing writes go through atomic rename/temp-directory patterns; solver holds best-so-far in memory for an interrupt handler to flush.
- **Output stream discipline** — progress to stdout, errors/warnings to stderr, `--quiet` suppresses progress but preserves final summary and errors. Affects logging architecture and anything that might leak formatter output.
- **Exit-code contract** — four distinct exit codes (0, 1, 2, 130) with clear rules about when each fires. Exit-code decisions are centralized in the CLI entry-point, but the signals feeding them (validation failure count, interrupt flag, pre-execution errors) originate across multiple modules.
- **Cache invalidation discipline** — the cache must be keyed on *all* inputs affecting output (five-tuple above); anything missing produces silent staleness. The code-commit-hash dimension couples cache validity to code versioning and wants a clear policy (dev-vs-release build behavior).
- **Boundary between `steeproute` and `steeproute-setup`** — area coverage check (FR24: fail fast if setup hasn't run) is a contract between the two CLIs. Both need a shared notion of what "prepared data for area X" means.

## Starter Template Evaluation

### Primary Technology Domain

Python 3.13 CLI tool — scientific-computing / geospatial optimization — local-only, no runtime network I/O, no hosting. Two distributable CLI entry points (`steeproute`, `steeproute-setup`) installed into a local environment. No PyPI publishing. Project is managed by `uv` (already committed; present in repo state).

### Starter Options Considered

Four options evaluated:

1. **Plain `uv init --package` (no third-party template)** — uv's native package-layout mode produces `src/<pkg>/`, `[project.scripts]`, proper build backend.
2. **[copier-uv](https://github.com/pawamoy/copier-uv)** — full opinionated Python-uv Copier template (ruff + mypy + pytest + mkdocs + GH Actions + release automation + pre-commit).
3. **[simple-modern-uv](https://github.com/jlevy/simple-modern-uv)** — minimal uv Copier template (ruff + BasedPyright + pytest + pytest-sugar + GH Actions). Lightest external option.
4. **Hypermodern Python Cookiecutter** (Jolowicz) and **cookiecutter-pypackage** (Feldroy) — rejected outright: Poetry-based or pre-uv, conflict with the uv commitment.

### Selected Starter: simple-modern-uv (Joshua Levy, Copier template)

**Rationale for Selection.**

Chosen partly for fit and partly as a testbed for learning the Copier template workflow in general. Among the external templates, simple-modern-uv is the lightest — no mkdocs site, no pre-commit, no release-automation beyond PyPI publishing — while still making sensible modern decisions (ruff, BasedPyright, pytest + pytest-sugar, GitHub Actions CI). The PyPI-publishing scaffolding is inert for this project (we don't publish) but easy to ignore.

**Initialization Command:**

```bash
# Applied via Copier to the current project directory:
copier copy gh:jlevy/simple-modern-uv .

# Copier will prompt for project name, author, Python version, etc.
# Answers are recorded in .copier-answers.yml for future template updates.
```

The current `uv init` output (plain `main.py`, stub README, minimal `pyproject.toml`) is disposable — Copier will overwrite it. Git history, the PRD, the `_bmad/` folder, `_bmad-output/`, and `.claude/` are untouched by the template.

**Architectural Decisions Provided by Starter:**

**Language & Runtime:**
Python 3.13 (already pinned via `.python-version`). uv-managed virtual environment. `uv.lock` committed for reproducible installs.

**Project Layout:**
`src/<package>/` layout with `[project.scripts]` entries. We'll extend the scripts table to include both `steeproute` and `steeproute-setup` entry points (standard pyproject.toml edit — not a template-level concern).

**Build Tooling:**
uv native build backend (hatchling under the hood). Dynamic versioning via Git tags — inert for this project since we're not publishing to PyPI, but harmless.

**Linting & Formatting:**
**ruff** — both linting and formatting (black-compatible). Locks in a 2026-standard choice we would have picked anyway.

**Type Checking:**
**BasedPyright** — note this is the template's current default (migrated from mypy in a recent version). This is a locked-in decision that the starter makes for us. If there's a reason to prefer mypy or Astral's `ty`, we override it here or early in implementation. Otherwise accept and move on.

**Testing Framework:**
pytest + pytest-sugar (cosmetic output improvements). PRD-committed. Specific layout (flat `tests/` vs. sub-layered) decided in the testing-strategy step.

**CI / Automation:**
GitHub Actions workflows for CI and publishing. Publishing workflow is inert (no PyPI target); CI workflow is the scaffold we'll populate in the testing-strategy step.

**Development Experience:**
Dynamic versioning, uv-based command runner, pytest-sugar's prettier errors. No pre-commit hooks, no docs site.

**What the starter explicitly does NOT decide (deferred to later steps):**

- CLI framework (`click` / `argparse` / `typer`).
- The second `[project.scripts]` entry (`steeproute-setup`) — added via a simple pyproject.toml edit, not template-level.
- CI job matrix detail (which platforms, which test layers run in CI).
- Test directory sub-layering.
- Logging library choice.
- Whether to disable the PyPI-publish workflow outright or leave it as inert scaffolding.

**Note:** Applying this template is the first implementation story. The existing `uv init` stub files are disposable.

## Core Architectural Decisions

_Decisions made in collaborative step-by-step facilitation. This section grows incrementally as categories are finalized; frontmatter advances to `stepsCompleted: [1, 2, 3, 4]` only after all categories close._

### Category 1 — Module & package structure

**Decision:** Single `steeproute` package using a **selective sub-package** layout. Sub-packages for concerns with meaningful internal complexity (`cli/`, `pipeline/`, `solver/`); flat modules for the rest.

**Rationale:** Leaves room for the complex concerns (two-CLI argument sharing, multi-stage pipeline, GRASP internals) to grow without imposing structural overhead on simpler ones. Matches the hobby-project budget framing — structure where it pays off, flatness elsewhere.

**Layout:**

```
src/steeproute/
├── __init__.py
├── cli/                       # sub-package: two CLIs share args + exit-code policy
│   ├── __init__.py
│   ├── query.py               # entry point for `steeproute`
│   ├── setup.py               # entry point for `steeproute-setup`
│   └── _shared.py             # area parsing, version flags, exit-code wrapper
├── pipeline/                  # sub-package: multi-stage data preparation
│   ├── __init__.py
│   ├── osm.py                 # osmnx loading + trail filtering
│   ├── dem.py                 # rasterio DEM sampling
│   ├── smoothing.py           # 2D polyline smoothing + elevation moving-median
│   ├── climbs.py              # climb detection
│   └── graph.py               # contracted climb-graph construction
├── solver/                    # sub-package: GRASP internals
│   ├── __init__.py
│   ├── grasp.py               # construction + restart loop
│   ├── anytime.py             # best-so-far + interrupt hooks
│   └── distinctness.py        # TopNTracker + Jaccard filter
├── validator.py               # flat: FR26–28 runtime route validation
├── cache.py                   # flat: I/O, key hashing, atomic writes
├── output.py                  # flat: HTML + JSON rendering
├── templates/                 # package data: Jinja2 templates + vendored JS assets
│   ├── route.html.j2
│   └── assets/
├── progress.py                # flat: progress reporting abstraction
├── errors.py                  # flat: exception hierarchy
├── models.py                  # flat: Route, Climb, ContractedGraph dataclasses
└── provenance.py              # flat: commit hash, OSM/DEM version resolution
```

Exact file names within `pipeline/` and `solver/` are placeholders; adjust during implementation. Flat modules graduate to sub-packages only when a single file becomes painful — promote reactively, not preemptively.

**Decision not taken:** separate top-level packages for each CLI (`steeproute-core`, `steeproute-cli`, `steeproute-setup-cli`). One `steeproute` package with two entry points inside is the Pythonic fit.

### Category 2 — CLI framework + two-binary structure

**Decision:** **click** (version 8.x) for both CLIs.

**Rationale:** Mature, decorator-driven, ergonomic for the shared-flag pattern via reusable option decorators. Custom types (`--center LAT,LON` as a tuple) are straightforward via `click.ParamType`. Exit-code discipline plays well with `click.exceptions.Exit`. Alternatives (`argparse`, `typer`) considered — `argparse` loses on custom-type verbosity; `typer`'s type-hint-driven sweet spot is one-app-many-commands, which isn't this project's shape.

**Structure:**

- Both CLIs live under `cli/` as `query.py` and `setup.py`, each with a click-decorated `main` function.
- **Shared options** (area parsing, `--verbose`/`--quiet`, `--version`) defined as reusable decorators in `cli/_shared.py` and composed onto each command.
- **Exit-code policy** lives in a wrapper in `cli/_shared.py`:

  ```python
  # conceptual
  def run_entry_point(main_fn):
      try:
          exit(main_fn())  # main_fn returns exit code or raises
      except KeyboardInterrupt:
          exit(EXIT_INTERRUPTED)  # 130
      except PreExecutionError as e:
          stderr_print(e); exit(EXIT_PREEXEC_ERROR)  # 2
  ```

- **`[project.scripts]` entries** in `pyproject.toml`:

  ```toml
  [project.scripts]
  steeproute = "steeproute.cli.query:main"
  steeproute-setup = "steeproute.cli.setup:main"
  ```

### Category 3 — Data pipeline architecture

**Decision summary:**

- **3a**: Pure-function pipeline with a thin orchestrator in `pipeline/__init__.py`.
- **3b**: `steeproute-setup` runs stages 1–7 (parameter-independent); `steeproute` runs stages 8–9 + solver (parameter-dependent).
- **3c**: Primary data structure is `networkx.MultiDiGraph` with a documented edge-attribute contract; structured stage I/O uses dataclasses from `models.py`.

**Pipeline stages:**

| # | Stage | Module |
|---|---|---|
| 1 | OSM load via `osmnx` | `pipeline/osm.py` |
| 2 | Trail filter (sac_scale, highway types, untagged-policy); admits a curated minor-road set as connectors (no SAC grade, never climbs; tightened multi-tag handling so major roads don't leak in) | `pipeline/osm.py` |
| 3 | 2D polyline smoothing per edge | `pipeline/smoothing.py` |
| 4 | Resample each edge to ~10m vertex spacing | `pipeline/smoothing.py` |
| 5 | DEM elevation sampling via `rasterio` | `pipeline/dem.py` |
| 6 | Elevation smoothing — global graph-Laplacian diffusion over the whole vertex field (each graph node a single shared variable) + optional deadband as a profile transform; **runs query-side** (see 3b) | `pipeline/smoothing.py` |
| 7 | Per-edge metrics (L, D+, D−, gradient) | `pipeline/climbs.py` |
| 8 | Climb detection (parameter-dependent: `min_climb_slope`, `min_climb_ground_length`) | `pipeline/climbs.py` |
| 9 | Climb-graph contraction (climbs → super-edges; **all** connectors retained and tagged with an undirected `base_segment_id` + a `reusable` flag = `length_m < l_connector`; super-edges carry the base-segment-id set of the edges they contract; each node tagged `is_road_trail_junction` = incident to both a connector and a trail — feeds FR31) | `pipeline/graph.py` |

**Stage boundary style (3a):** each stage is `def stage(input_graph, config) -> output_graph`. Orchestrator wires them: `g = osm_load(area); g = filter_trails(g, cfg); g = smooth_polylines(g); ...`. Pure functions → clean unit-test targets with fixture inputs, BasedPyright-friendly, easy to cache at any boundary.

**CLI split (3b):** stages 1–5 are parameter-independent (depend only on area + DEM version + OSM extract date + untagged-trails-policy) — these run in `steeproute-setup` and their output is cached; the cached `vertices_resampled` hold **raw** sampled elevations. Stages 6–9 run in `steeproute` on every query: stage 6 (elevation smoothing + optional deadband) and stage 7 (per-edge metrics) depend on `--elevation-smoothing` / `--elevation-deadband`; stages 8–9 depend on `min_climb_slope`, `min_climb_ground_length`, `L_connector` — all fast enough to not need caching. The route-level slope floor `θ` is applied later still, at solve/validate time. Because smoothing is recomputed query-side from the cached raw elevations, the cache stays **smoothing-independent**: sweeping `--elevation-smoothing` re-does stages 6–9 + solve only and never re-prepares. (Moving stages 6–7 query-side changes `pipeline_content_hash`, so existing caches re-prepare once when this ships — a one-time cost.)

**Second-tier query-side cache: considered and declined (Story 13.3, 2026-07-04).** After Stories 13.1/13.2, fresh stage-line measurement on the reference workloads put the per-query recompute at: elevation-reshape (stages 6–7) 6.76 s, trail-filter redux 2.23 s, climb-detection 0.28 s, climb-contraction 2.56 s on Chartreuse r10 (30.7 s wall); 0.90 / 0.08 / 0.05 / 0.33 s on Chamrousse r6 (12.3 s wall). A tier caching the post-stage-9 `ContractedGraph` keyed on the eight non-solver knobs would net only ~4 s (13%) at r10 — `output.render` reads geometry from the full operational graph, so stages 6–7 still run — and ~0.3 s at r6. Capturing the headline ~9.5 s requires also persisting the operational graph (~70 MB per knob combination), plus a base-content-identity key component (`--force-refresh` reuses the same `cache_key_hash` over fresh OSM), GC, and a serialization penalty on every miss. Two further costs tipped the decision: (1) the regression harness runs the query CLI against the **committed** fixture cache roots, so an always-on tier would write into the repo tree during tests and make repeat golden runs validate cache reads instead of pipeline behavior; (2) most of the cacheable time is compute-shaped after all — `compute_edge_metrics` (~3.3 s) and `filter_trails` (~2.2 s) are unvectorized per-edge Python loops amenable to the Story 13.1 treatment, which would benefit every run (hit or miss) and halve the tier's value after the fact. Only contraction (~2.6 s) is genuinely cache-or-nothing. **Decision: no second cache tier; the recompute-per-query design stands.** Follow-on lever (via correct-course if pursued): vectorize stage 7 metrics and the stage-2 redux instead. **Scale caveat:** r6/r10 are test-budget areas, not the ceiling of intent — the long-term ambition is whole-range areas (radius 50–100 km), where the recomputed block would grow to minutes per query and recompute avoidance becomes clearly worth its complexity. This decision holds within the current area envelope (default `--area-cap` 500 km² ≈ r12.6); any whole-range epic should revisit it as one of its components.

**DEM tile fetch concurrency (Story 14.3, 2026-07-07; flag added 2026-07-07).** Setup stage 5's DEM download (`pipeline/dem_download.py` `_fetch_mosaic`) fetches its WMS tiles concurrently on a `ThreadPoolExecutor` — the pipeline's **first fetch concurrency** beyond the cache-write atomics (Cat 4d). Threads, not processes: tile download is network-wait-bound (the GIL releases during `urlopen`), unlike the CPU-bound GRASP parallelism (Cat 5a). Each worker returns raw BIL bytes; the parent thread validates the byte count, reshapes, and writes into the tile's disjoint `arr[y0:y1, x0:x1]` slice, so the assembled mosaic is **byte-identical regardless of completion order and regardless of worker count** (verified live: 16-tile Grenoble fetch, sequential vs 4-worker, `np.array_equal` true, 4.8× wall-clock). IGN Géoplateforme handled 4-way concurrency cleanly at the r20 tile-count profile (no 429 / no connection reset).

**Scope revision: `--dem-fetch-workers` (2026-07-07).** The story originally kept the concurrency ceiling as a module constant (`_MAX_FETCH_WORKERS`, not a CLI flag), reasoning it was an IGN-etiquette property of the source rather than a user-tunable knob. Revised at the user's request: the "4 is safe" conclusion rests on exactly one live validation run, and the user wants to adjust it without a code change if IGN's real-world tolerance differs (higher throughput on a permissive day, or a lower ceiling if IGN starts throttling). `--dem-fetch-workers` (default: `DEFAULT_DEM_FETCH_WORKERS = 4`) now threads through `resolve_dem`'s `fetch_workers` kwarg to `_fetch_mosaic`'s `ThreadPoolExecutor(max_workers=...)`; `validate_dem_fetch_workers` (`cli/_shared.py`) rejects non-positive values as `BadCLIArgError` (exit 2) before any network work, matching the `--iter-budget`/`--n` `>= 1` guard pattern. Byte-identity is unaffected by this change (proven above); only wall-clock and etiquette risk vary with the setting, and raising it beyond the validated 4 is the user's call, not a codebase invariant.

**Post-review hardening: per-tile retry + fail-fast cancellation (2026-07-07).** Code review of Story 14.3 found the pool's failure path was worse than the sequential code it replaced: (1) a *transient* failure (timeout, reset, HTTP 429/5xx, truncated read) on any one tile failed the entire fetch immediately, with no retry — the sequential path had the same gap, but concurrency raises the odds of hitting it once per batch instead of once per tile; (2) on any terminal failure, `with ThreadPoolExecutor(...)` exit (`shutdown(wait=True)`, the default) drains every **already-queued** tile before propagating the error — worse than sequential, since it fires requests for tiles no one will use, against a server that just showed distress. Fix: `_fetch_tile` now retries transient failures up to `_TILE_MAX_ATTEMPTS` (default 3) with exponential backoff + full jitter before mapping to `DataSourceUnavailableError`; deterministic failures (bad content-type, wrong byte count) still fail immediately, unretried. `_fetch_mosaic`'s `as_completed` loop now shuts the pool down with `cancel_futures=True` on any exception (including `KeyboardInterrupt`), dropping not-yet-started tiles instead of draining them — only the ≤ `max_workers` tiles already in flight finish. `_HTTP_TIMEOUT_S` dropped from 120 s to 30 s per attempt now that retries backstop transient blips (worst case for a dead tile: ~30 s × 3 + backoff ≈ 100 s, versus the old single 120 s hang). The retry count, backoff base, and per-request timeout are overridable via `STEEPROUTE_DEM_FETCH_RETRIES` / `STEEPROUTE_DEM_FETCH_BACKOFF_S` / `STEEPROUTE_DEM_HTTP_TIMEOUT_S` (malformed values log a warning and fall back to the default rather than crashing at import) — these are process-local tuning knobs for `steeproute-setup`'s own network behavior, not inter-CLI configuration (Cat 7's "no env vars" decision is scoped to state shared *between* `steeproute` and `steeproute-setup`, which this isn't).

**Edge-attribute contract (3c):** every edge in the pipeline graph carries:

- `geometry` — `shapely.LineString`
- `vertices_resampled` — list of `(lat, lon, elevation_m)` tuples; cached with **raw** post-stage-5 elevations, smoothed query-side (stage 6) into the single canonical profile
- `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient` — computed query-side in stage 7 as the naive up/down sum of that one canonical profile (the metric box, the solver objective, and the plotted curve all derive from it — no separate per-edge vs. continuous smoothing)
- `sac_scale`, `highway`, `osm_way_id` — source attributes from OSM
- `max_windowed_descent_grad` — steepest uphill-measured running-average gradient over a fixed distance window (`pipeline.climbs._DESCENT_WINDOW_M`, a module constant — kept off the CLI so the metric stays parameter-independent) along the base segment; computed in stage 7, direction-agnostic, governs FR32 descent feasibility (compared against `--max-descent-slope` only when that flag is set)

Structured stage inputs/outputs (beyond simple tuples) use dataclasses declared in `models.py`. No custom graph wrapper.

### Category 4 — Cache architecture

**Decision summary:**

- **4a**: One directory per prepared area; `index.json` as a coverage-lookup convenience file.
- **4b**: Single cache key over (area bounds, untagged-trails-policy, DEM version, pipeline source content hash). OSM extract date is recorded as metadata but not part of the key. Freshness is user-triggered via `--force-refresh` (or manual entry deletion).
- **4c**: pickled ragged-array payload for the graph (graph-minus-geometry + bulk coordinate arrays; schema v2, Story 13.2), JSON manifest, geojson bounds sidecar.
- **4d**: `manifest.json` written last as the atomic commit signal; `.tmp/` directories for in-progress writes.
- **4e**: Strict containment check against prepared bounds; miss → exit 2 with actionable message.
- **4f (added):** OSM-age warning when cache entry's OSM extract date exceeds threshold (default 90 days, `--osm-age-warn-days` override), non-blocking.

**Directory layout (4a):**

```
<cache-root>/steeproute/           # resolved via platformdirs
├── index.json                     # summary of prepared areas, for FR24 coverage check
└── areas/
    └── <cache-key-hash>/
        ├── manifest.json          # full entry metadata (written LAST, atomically)
        ├── graph.pkl              # networkx MultiDiGraph (stages 1–7 output)
        └── bounds.geojson         # area polygon, for debug viz + diagnostics
```

`<cache-key-hash>` is a 16-hex-char truncation of the SHA256 computed in 4b. `index.json` is rebuilt from manifests after any entry change (derived; can be regenerated by scanning manifests).

**Cache key composition (4b):**

| Component | Source | Purpose |
|---|---|---|
| Area bounds (canonicalized) | user: `--center/--radius` | Different area → different entry |
| Untagged-trails-policy | user: `--untagged-trails` | Different policy → different graph |
| DEM version tag | user: `--dem-version` or a stable IGN-layer default tag (DEM is auto-downloaded) | Different DEM release → different elevations |
| Pipeline source content hash | SHA256 of `src/steeproute/pipeline/**/*.py` + `src/steeproute/models.py` | Pipeline code change → effectively invalidates all entries |

One SHA256 over canonical JSON of the above → the entry's `<cache-key-hash>`.

**Area canonicalization for hashing:** center/radius mode rounds to (6-decimal lat/lon, 3-decimal radius_km) before hashing, so floating-point noise doesn't produce phantom misses.

**OSM extract date:** recorded in `manifest.json` as the timestamp of setup's OSM download. Not part of the cache key (would invalidate every run — `osmnx` always fetches live). Freshness handled via `steeproute-setup --force-refresh` (rebuilds the entry regardless of key match).

**Invalidation semantics:** in a keyed cache, most "input changes" don't invalidate — they map to a different entry. The old entry stays valid for its original inputs. Only pipeline source changes effectively orphan all existing entries (they become unreachable because nothing looks for their old key). Orphan entries stay on disk; a future manual utility may prune them.

**Provenance in reports (separate from cache key):** HTML + JSON carry `steeproute_version`, `git_commit_short`, and a `-dirty` flag if the working tree is modified at run time. Human-readable string for future-reference, not a hash.

**On-disk format (4c):** `graph.pkl` is a pickled payload dict — the MultiDiGraph with per-edge `geometry` stripped, alongside one flat coordinate array + per-edge offsets in edge-iteration order (schema v2, Story 13.2; manifest `schema_version` is the format signal). Rationale: unpickling shapely geometries reconstructs each LineString via a per-object WKB parse (~60% of `read_entry` on a large entry), while `shapely.from_ragged_array` rebuilds them in bulk ~20× faster; everything else (networkx skeleton, `vertices_resampled` list-of-tuples) measured *faster* through pickle than through any array reconstruction, so only geometry moved out (measured 2.5 s → 1.1 s on the 60k-node/152k-edge Chartreuse r10 entry). Tradeoffs accepted, unchanged from v1: Python-version-sensitive (we pin 3.13, cross-version reads fail loudly at `pickle.load`), not human-inspectable (hence `bounds.geojson` + `manifest.json` for diagnostics). v1 (raw pickled graph) entries re-prepare once — `cache.py` is excluded from the pipeline content hash, so the format change does not shift cache keys; the manifest version bump alone invalidates. Full columnar decomposition (Parquet) remains rejected as premature.

**`manifest.json` schema:**

```json
{
  "schema_version": 2,
  "area": {"mode": "center_radius", "center": [45.07, 6.11], "radius_km": 50},
  "untagged_policy": "include",
  "dem_version": "ign_rge_alti_5m_2024-12",
  "pipeline_content_hash": "a3f1...",
  "osm_extract_date": "2026-04-22T14:30:00Z",
  "cache_key_hash": "7f2b1c9a",
  "steeproute_version": "0.1.0",
  "steeproute_commit": "e9a2bc4-dirty",
  "created_at": "2026-04-22T14:30:00Z"
}
```

**Atomic write pattern (4d):** rule — `manifest.json` is the commit signal; an entry is considered valid only if `manifest.json` exists in the entry directory. Write order:

1. Write `graph.pkl.tmp` and `bounds.geojson.tmp` into `<cache-root>/steeproute/areas/<hash>.tmp/`.
2. `os.replace()` each to its final name inside the `.tmp/` directory (file-level atomicity, cross-platform).
3. Rename `<hash>.tmp/` to `<hash>/` (directory rename; on Windows, uses `os.replace()` which requires the target not exist — re-prepare case swaps via `<hash>.old`).
4. `os.replace()` the final `manifest.json` in place (written last, inside the entry).
5. Rebuild `index.json` atomically via `index.json.tmp` → `os.replace()`.

Ctrl-C mid-write leaves `.tmp/` orphans; readers ignore entries without `manifest.json`. Cleanup of stale `.tmp/` happens opportunistically at the start of the next `steeproute-setup` run.

**FR24 coverage check (4e):** at `steeproute` startup —

1. Compute query area polygon from `--center/--radius`.
2. Read `index.json`. Missing/empty → exit 2: *"No prepared areas found. Run `steeproute-setup` first."*
3. For each indexed area, check strict containment of the query polygon (`shapely.contains`).
4. If multiple containing areas exist, pick the smallest (minimum graph-load cost).
5. No containing area → exit 2 with a specific message naming the nearest prepared area(s) and giving an actionable command.

**OSM-age warning (4f):** at cache hit, compare `manifest.osm_extract_date` to current UTC time. If age > `--osm-age-warn-days` (default 90), emit a stderr warning suggesting `--force-refresh`. Non-blocking, non-fatal.

### Category 5 — Solver architecture

**Decision summary:**

- **5a**: Single-process for v1; solver loop designed to be parallelizable later. Revisit if measurement shows 10-minute budget delivers poor objective values. **Realized in Story 14.4** (`--workers N`, default 1) — see the "Parallelism (5a)" note below.
- **5b**: Solver is a class with injected RNG, progress callback, and prepared graph; exposes `run()` and `best_so_far`. Interrupt handling at the CLI layer, not inside the solver.
- **5c**: `numpy.random.Generator` seeded explicitly (no ambient state).
- **5d**: Top-N + Jaccard distinctness is a separate `TopNTracker` component, orthogonal to the GRASP loop.
- **5e**: Four termination conditions — iter-budget, time-budget, **stagnation**, KeyboardInterrupt. `convergence_status` takes three values in reports: `converged` / `budget-exhausted` / `interrupted`.

**Parallelism (5a):** GRASP iterations are embarrassingly parallel in principle, but the PRD's 10-minute budget is a design target, not an SLO. Single-process v1 keeps code, tests, and interrupt handling simple; the iteration loop is shaped as `for seed_i in seeds: run_iteration(seed_i)`, trivially convertible to `ProcessPoolExecutor` later. If measurement shows insufficient iteration count in budget, parallelism becomes a follow-on story.

**Parallelism realized (Story 14.4):** `--workers N` (default 1) fans independent GRASP restarts across processes. It is plumbed **entirely at the CLI/orchestration layer** (`solver/parallel.py` + `cli/query.py`) and deliberately does **not** enter `SolverParams`/`models.py` — `models.py` is content-hashed (`_PIPELINE_CONTENT_GLOBS`), so a `workers` field would invalidate every prepared cache for a knob the solver never reads. The only per-worker `SolverParams` change is a smaller `iter_budget` via `dataclasses.replace`. Contract:

- **`--workers 1` is byte-identical to pre-14.4** — the CLI keeps the unchanged single-process `GraspSolver(...).run()` path (goldens + NFR4 untouched, no rebake); the parallel machinery is never entered at the default.
- **N>1**: a `ProcessPoolExecutor` pinned to the **spawn** start method (Windows-safe; spawn forced everywhere so Linux CI runs the same pickling/fresh-import path) gives each worker a **lean graph view** (see below), `iter_budget // N` (+ remainder to worker 0, clamped so no worker gets 0), and an RNG from `np.random.SeedSequence(seed).spawn(N)[i]`. Results merge through one fresh `TopNTracker(n, j_max, segment_map)` in **worker-id order** (collected into id-indexed slots, never completion order — the tracker is order-sensitive under overlaps) then each worker's returned `current_top()` order.
- **Worker graph payload (the thing that makes it actually faster)**: the full contracted graph is ~204 MB at r20 because every edge carries its `vertices_resampled` polyline + shapely `geometry` — pure rendering payloads the **solver never reads**. Shipping that to each worker under spawn erased the speedup (measured: zero gain, the ~204 MB pickle/unpickle × N swamped the solve). Workers therefore receive `solver_graph_view(contracted)` — the same graph with `HEAVY_EDGE_ATTRS = {vertices_resampled, geometry}` stripped (~72 MB at r20), serialized **once** to bytes in the parent and handed to each worker as a cheap buffer copy. GRASP output is byte-identical on the lean view (it reads none of the stripped attrs); the parent keeps the full graph for validation/render.
- **Island-model elite migration (`--merge-interval`, default 250 000 total iters)**: `iter_budget` is split into rounds of ~`merge-interval` iterations; after each round the workers' top-Ns are merged and the merged elite **seeds every worker's tracker** for the next round (`GraspSolver(initial_solutions=...)`), so workers cooperate toward one shared top-N instead of drifting into independent, redundant local optima. This **bounds the parallel downside (variance)** so the merged result reliably matches/beats single-process — measured r20/1M: single 20991, independent-islands (no migration) 20540, migration (4 rounds) **21100** (beats single). `--merge-interval 0` disables migration (one final merge). Construction is memoryless random-restart, so seeding the elite changes only what a worker *keeps*, never what it generates — FR29-safe.
- **Per-round cost — measured, not free.** The `_merge` itself is ~2 ms (segment-map precomputed once), but each round otherwise re-runs each worker's per-graph precompute — dominated by `_build_adjacency` (~8 s on the r20 graph) plus the `__init__` precompute (~1.6 s). The adjacency is a pure function of graph + filter params (identical every round), so it is **built once per worker process and reused** (`GraspSolver(adjacency=...)` + the `_worker_adjacency` process cache); byte-identical (verified). This cut the round-boundary stalls from ~7 s to ~1–2 s (the residual = the synchronous-migration barrier wait for the slowest worker + the not-yet-cached ~1.6 s `__init__` precompute). The lean graph is still loaded once per worker process (pool `initializer`), never per round.
- **Determinism**: N>1 output is deterministic and reproducible per `(seed, workers, merge_interval)`, but **differs by design from `--workers 1`** (independent `SeedSequence(seed).spawn(N × rounds)` streams + partitioned budget). `seed=None` is non-deterministic in both modes. The run summary reports the true merged `total_objective` (comparable across worker counts); the live progress line shows only `best_worker_objective` (the leading worker's running sum), which understates the merge.
- **`--time-budget` / `--stagnation-iters` apply per worker per round** (only `iter_budget` is divided). Aggregated `convergence_status` is `converged` iff *every* worker converged, else `budget-exhausted`; aggregated `convergence_iteration` is the max across workers.
- **Interrupt (Cat 5b) for N>1**: Ctrl-C cancels not-yet-started workers (`cancel_futures=True`) and renders the top-N salvaged from workers that had **already returned**, tagged `interrupted` (exit 130); if none returned, warns + exit 130. In-flight workers' partial best-so-far cannot cross the process boundary — a documented degradation from the single-process live-flush (Story 7.3), which N=1 retains unchanged. This render is not delayed by worker teardown (see below).
- **Progress**: per-iteration progress can't cross to workers, so each worker pushes a throttled `(worker_id, iteration, best_objective)` snapshot onto a plain `multiprocessing.Queue` (passed via the pool `initializer`, not a `Manager` proxy — a plain queue cannot cross `ProcessPoolExecutor.submit()` args, only the initializer channel, confirmed empirically 2026-07-14); a parent daemon thread aggregates the latest per-worker snapshots and emits one `progress:` line per `--progress-interval` (`--quiet`-suppressed).
- **Non-blocking teardown (2026-07-14).** The pool is shut down `wait=False` (`cancel_futures=True`) rather than via the `with`-block's implicit `wait=True`: each worker frees a large heap (lean graph + adjacency table) at exit — a measured ~8 s wall at r20 — and blocking the parent on that before `validate-render` starts was pure dead time. `concurrent.futures`' atexit hook still joins the workers before the CLI process exits, so nothing is orphaned; worker teardown now overlaps the caller's validation/render instead of preceding it.
- **Setup-failure hardening (2026-07-14).** `ParallelGraspFailed` now also covers failures in parallel-specific setup that happens before any worker is spawned — serializing the lean graph view (`pickle.dumps`, can OOM on a large graph) and creating the progress queue (can hit an OS handle/semaphore limit) — routing them to the same single-process fallback as a `BrokenProcessPool`, instead of a raw uncaught traceback. Budget math / `base_segment_id_map` above that guard are shared with the single-process solver and are not parallel-specific, so failures there are left to propagate.
- **Measured speedup (Story 14.4 close-out, r20 Grenoble, 4 workers on the 14-logical-core 155U, 1M iters)**: `--workers 1` ≈ 189 s total / ~113 s solve; `--workers 4` ≈ 131 s total / ~56 s solve → **~2× on the solve**, ~1.44× on total wall (the fixed ~75 s of unparallelized setup+render dilutes it). Not the theoretical 4× because: E-cores are slower than P-cores, setup is single-threaded, and there is a fixed ~15–20 s per-run startup (lean-graph build + spawn + 72 MB unpickle × N + per-worker adjacency build) that makes parallelism break even around ~300 k iters and win beyond. The r50 measurement is the 14.6 probe. **Re-measured post-hardening (2026-07-14)**, same command: lazy pipeline imports (below) cut per-worker startup from ~6.4 s to ~2.2 s and non-blocking teardown removed the ~8 s post-solve dead tail — user-confirmed **108 s** total wall (from a 120–140 s regression introduced transiently by an unrelated `.venv` reinstall, itself traced to the same session). Residual run-to-run variance (~15–30 s) attributed to the machine's power/thermal state, outside this story's scope.
- **Lazy OSM/DEM fetch-stack imports (2026-07-14, follow-up perf fix — not scoped to 14.4 originally but landed here since it was found chasing this story's wall-clock).** `pipeline/osm.py` and `pipeline/dem.py` imported `osmnx`/`requests`/`truststore`/`rasterio`/`pyproj` at module level even though only their own fetch/sampling functions use them; `pipeline/__init__` eagerly imports both modules, so every spawned parallel-solve worker (which only needs `max_sac_rank`/`parse_difficulty_cap`/`is_junction_node` transitively) paid ~4 s per process importing osmnx → geopandas → pandas, rasterio, and pyproj it never touches. Moved to function-local imports inside `osm_load`/`_ensure_sac_scale_in_useful_tags`/`sample_elevation`; measured per-worker import drop ~6.4 s → ~2.2 s.

**Solver shape (5b):**

```python
class GraspSolver:
    def __init__(
        self,
        graph: ContractedGraph,
        params: SolverParams,
        rng: numpy.random.Generator,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ): ...

    def run(self) -> list[Solution]: ...        # main loop; may raise KeyboardInterrupt

    @property
    def best_so_far(self) -> list[Solution]: ... # always-readable current top-N
```

**Interrupt handling lives at the CLI layer**, not inside the solver:

```python
# cli/query.py (conceptual)
def main():
    solver = build_solver(...)
    try:
        solutions = solver.run()
        convergence = "converged" if solver.stagnated else "budget-exhausted"
    except KeyboardInterrupt:
        solutions = solver.best_so_far
        convergence = "interrupted"
    validated = validator.validate(solutions, graph, params)
    output.render(validated, convergence=convergence)
    sys.exit(exit_code_from(validated, convergence))
```

The solver raises `KeyboardInterrupt` freely; the CLI catches it, reads `best_so_far`, writes outputs. Solver stays oblivious to signals — simpler to test.

**RNG (5c):** `numpy.random.default_rng(seed)` passed into the solver constructor. Explicit, not global; BasedPyright-friendly; compatible with `SeedSequence.spawn()` for future parallel streams.

**TopNTracker (5d):**

```python
class TopNTracker:
    def __init__(self, n: int, j_max: float): ...
    def consider(self, solution: Solution) -> bool: ...  # True if admitted
    def current_top(self) -> list[Solution]: ...
    def total_objective(self) -> float: ...              # for stagnation detection
```

GRASP iteration feeds each candidate through `tracker.consider(...)`. Jaccard-overlap policy is the tracker's concern, not the solver's — testable independently against known-conflicting route pairs.

**Opt-in construction constraints (Epic 10, FR31/FR32):** two optional, default-off constraints narrow the construction feasibility set when their flags are supplied. `--start-at-junction` restricts seed nodes to `is_road_trail_junction` nodes (annotated at Stage 9). `--max-descent-slope` forbids extending the walk by a *descending* traversal whose `max_windowed_descent_grad` exceeds the cap (uphill traversal unconstrained; a super-edge taken in reverse is a descent). Both must stay deterministic (FR29) and apply identically in the exhaustive oracle so GRASP and the oracle enumerate one shared feasible set. With both flags unset, construction is byte-identical to today.

**Termination (5e):** the solver's `run()` loop exits on any of:

| Condition | Trigger | `convergence_status` |
|---|---|---|
| Iteration budget | `--iter-budget` count reached | `budget-exhausted` |
| Wall-clock budget | `--time-budget` elapsed (checked between iterations) | `budget-exhausted` |
| Stagnation | Top-N total objective unchanged for `--stagnation-iters` consecutive iterations | `converged` |
| KeyboardInterrupt | SIGINT propagated from user | `interrupted` |

**Stagnation definition:** "improvement" = change in `tracker.total_objective()` (sum of `D+ + D−` across all N routes). Captures both better-single-route and better-distinct-alternative outcomes. Stagnation check only activates after the first N+1 iterations (implied by the window itself). Setting `--stagnation-iters 0` disables the check.

**New CLI flag surface:** `--stagnation-iters N` (default tuned empirically during implementation; name TBD).

**`convergence_status` contract:** three values surfaced in HTML metadata and JSON sidecars. Exit code is computed separately (validation-driven per Category 6).

### Category 6 — Validation architecture

**Decision summary:**

- **6a**: Validation runs as a distinct stage between the solver and output rendering, orchestrated at the CLI layer. Not a `Route`-construction postcondition.
- **6b**: Per-route violations attached to `Route.validation`; set-level (Jaccard) violations live on the wrapping `ValidatedRouteSet` and cross-reference the affected route pair.
- **6c**: Exit-code computation reads from the validated set _after_ all outputs are written; validation failures don't suppress disk writes (FR28).
- **6d**: Validator module exposes per-route, set-level, and orchestrator functions for independent testability.

**Why not a construction postcondition (6a):** the PRD requires failed routes to be _produced and flagged_, not rejected (FR27–28). Making `Route.__init__` throw on invalid inputs would fight that requirement. Making `Route.__init__` always succeed and carry a validation result is equivalent to an explicit separate validation step with extra indirection. Cleaner to keep validation explicit and orthogonal.

**Data shapes (6b):**

```python
# models.py
@dataclass
class Route:
    edges: list[Edge]
    metrics: RouteMetrics           # length, D+, D-, avg_gradient
    validation: RouteValidation     # per-route result

@dataclass
class RouteValidation:
    passed: bool
    violations: list[ConstraintViolation]

@dataclass
class ConstraintViolation:
    constraint_id: str              # "slope_floor", "difficulty_cap", ...
    detail: str                     # human-readable for HTML banner
    numeric: dict[str, float]       # e.g. {"observed": 0.18, "required": 0.20}

@dataclass
class ValidatedRouteSet:
    routes: list[Route]
    set_violations: list[PairwiseViolation]  # Jaccard violations

@dataclass
class PairwiseViolation:
    route_index_a: int
    route_index_b: int
    jaccard_observed: float
    jaccard_max: float
```

**Why the per-route vs. set-level split:** per-route violations attach cleanly to their route (renderer reads `route.validation` for banner decisions). Jaccard violations don't have a single "home route" — they're about pairs. Storing them on the wrapping `ValidatedRouteSet` lets the renderer surface pairwise violations in both affected routes without lying about ownership.

**Banner logic in the output renderer:**

```python
for i, route in enumerate(validated_set.routes):
    pairwise_affecting_me = [
        pv for pv in validated_set.set_violations
        if i in (pv.route_index_a, pv.route_index_b)
    ]
    show_banner = (not route.validation.passed) or bool(pairwise_affecting_me)
    # render banner content from route.validation.violations + pairwise_affecting_me
```

**Exit-code coupling (6c):**

```python
any_per_route_failure = any(not r.validation.passed for r in vset.routes)
any_pairwise_failure = bool(vset.set_violations)
if any_per_route_failure or any_pairwise_failure:
    exit_code = EXIT_VALIDATION_FAILURE  # 1
```

Computed _after_ all HTML + JSON writes complete, so disk state is correct regardless of exit code.

**Validator module interface (6d):** `validator.py` (flat module) exposes:

```python
def validate_route(route: Route, graph: ContractedGraph, params: SolverParams) -> RouteValidation: ...
def validate_set(routes: list[Route], params: SolverParams) -> list[PairwiseViolation]: ...
def validate(solutions: list[Solution], graph: ContractedGraph, params: SolverParams) -> ValidatedRouteSet: ...
```

CLI calls `validate(...)`; the underlying per-route and set-level functions are independently testable against crafted-violating and clean-input fixtures.

**Constraints enforced:**

| Constraint | Source | Scope |
|---|---|---|
| Route-level slope floor ≥ θ, `(D+ + D−)/length` | `--theta` | per-route (whole route) |
| Climb-detection slope ≥ `min_climb_slope`, `d_plus/length` | `--min-climb-slope` | per-climb (stage 8) |
| Difficulty cap ≤ SAC scale | `--difficulty-cap` | per-route (per-edge) |
| Edge-reuse limit (undirected, base-segment; short connectors `< --l-connector` exempt) | `--l-connector` | per-route |
| Graph membership (every edge in operational graph) | derived | per-route (sanity) |
| Pairwise Jaccard ≤ J_max (keyed on the undirected base-segment identity, same as reuse) | `--j-max` | set-level |
| Start endpoint is a road/trail junction (only when flag set) | `--start-at-junction` | per-route (FR31) |
| No segment descended above the cap (windowed uphill slope; only when flag set; uphill unconstrained) | `--max-descent-slope` | per-route (FR32) |

### Category 7 — Inter-CLI contract

**Decision summary:**

- The local cache directory is the **only** shared state between `steeproute` and `steeproute-setup`. No env vars, no side-channel config **used to communicate between the two CLIs** — e.g. `steeproute` must never infer anything about a `steeproute-setup` run (or vice versa) via an environment variable, only via the cache contract below. This does not forbid a single CLI reading an env var to tune its own internal, process-local behavior (e.g. `pipeline/dem_download.py`'s `STEEPROUTE_DEM_*` network-retry knobs, Story 14.3 post-review hardening) — that carries no inter-CLI contract and isn't what this decision is about.
- `index.json` and each entry's `manifest.json` are the **versioned contract surfaces** between the two CLIs.
- No file locks, no RPC, no shared library for v1. Concurrency is handled by file-atomic writes (Category 4d).

**Contract details:**

- Both CLIs resolve the cache root identically via `platformdirs.user_cache_dir("steeproute")`, overridable with a `--cache-dir` flag (consistent name across both CLIs).
- `index.json` and `manifest.json` each include a `schema_version: int` field. Schema changes require coordinated updates across both CLIs. Consumers (`steeproute`) reading a newer-schema manifest must either (a) skip unknown fields safely, or (b) fail with a descriptive version-mismatch error (exit 2) — no silent partial reads.
- `steeproute` never writes to the cache. `steeproute-setup` is the only writer. This asymmetry simplifies concurrent-read semantics.
- Two concurrent `steeproute-setup` runs targeting **different** areas are safe (independent entry directories). Two targeting the **same** area race on the final directory rename; the loser's write is fully overwritten (no partial merge possible because of the atomic-rename pattern). Acceptable for N=1.
- Lock file (`.lockfile` with `fcntl`/`msvcrt`) considered and rejected for v1 — N=1 user is unlikely to run concurrent setup on the same area; the atomic pattern is robust to the rare race. Reintroducible later as a contained change if needed.

### Category 8 — Logging, progress, and stream discipline

**Decision summary:**

- **stdlib `logging`** for errors/warnings (stderr); **plain `print(...)`** for progress lines and run summary (stdout).
- No `rich`, no `structlog`. Keep dependencies lean and interrupt-safety simple.
- Solver emits **`ProgressEvent`** objects via an **injected callback**, time-throttled by `--progress-interval`. CLI installs the rendering callback; tests can inject a collecting callback.
- Stream routing: stdout for progress + final summary; stderr for errors + warnings. `--quiet` suppresses stdout progress (keeps final summary + stderr). `--verbose` increases stderr logging verbosity; no effect on stdout.

**ProgressEvent shape** (dataclass in `progress.py`):

```python
@dataclass
class ProgressEvent:
    iteration: int
    elapsed_s: float
    best_objective: float       # D+ + D- summed across current top-N
    estimated_remaining_s: float | None
    stagnation_counter: int     # iterations since last improvement
```

**Solver integration:**

```python
solver = GraspSolver(
    graph=...,
    params=...,
    rng=...,
    progress_callback=cli_progress_callback,  # None-able
)
```

Solver invokes the callback no more often than `--progress-interval` seconds (tracked against wall-clock; first call fires after the interval elapses from start).

**Rendering callback** (in `cli/query.py`): formats the event as a single-line progress string and `print()`s to stdout. Suppresses entirely if `--quiet` was passed (callback is installed as `None` in that case, or installed as a no-op).

**Run summary** (emitted at process end, regardless of `--quiet`): one block on stdout naming parameters used, routes returned vs. N requested, validation-failure count, graceful-degradation explanation if any, wall-clock total. Fulfills FR22.

**Rationale on library choice:** `logging` is BasedPyright-friendly and understood by everyone. `rich` was tempting for progress UI but (a) adds a dependency, (b) its live-display machinery complicates Ctrl-C handling (cursor state, terminal restore), (c) we don't need its rendering power for single-line progress updates. Plain `print` is sufficient and trivially interrupt-safe.

**Within-stage progress under concurrency (Story 14.3):** `StageProgress` is **not thread-safe** and is only ever called from the main/parent thread. When the DEM tile fetch became concurrent (Cat 3), the `tile i/N` line stayed a **monotonic completion counter** emitted from the parent thread as each future resolves. Post-review (2026-07-07), a leading `tile 0/N` line was added *before* the first request is submitted — under the sequential path the pre-request emit meant the user saw "working" during even a single tile's wait, and the concurrent completion-counter rewrite had dropped that for the single/first-tile case (nothing printed until the one tile finished). The printed sequence is now `tile 0/N … tile N/N`. Workers only fetch bytes; they never touch the progress seam. Progress remains a pure display side-effect that never influences control flow or determinism.

### Category 9 — HTML report + JSON sidecar generation

**Decision summary:**

- **Jinja2** as the template engine.
- **Vendored** Leaflet + Chart.js assets shipped inside `steeproute/templates/assets/`, **inlined** into each HTML report at render time (self-contained HTML files).
- Stable filename pattern `route-<i>.{html,json}` (FR21); existing files overwritten in place.
- HTML + JSON carry the **same metadata** in parallel; HTML renders it as a visible metadata block, JSON carries it machine-readably.
- Route geometry embedded as GeoJSON for Leaflet; JSON sidecar carries both the graph edge sequence and WGS84 coordinates for downstream tooling.

**Template engine:** Jinja2 — ubiquitous, well-documented, good enough for the static report shape. No fancy templating needed (inheritance is useful for header/footer; filters are useful for number formatting).

**Asset strategy:** Leaflet and Chart.js are shipped as pinned versions inside `src/steeproute/templates/assets/` (e.g. `leaflet-1.9.4.min.js`, `chart-4.4.0.min.js`, `leaflet-1.9.4.min.css`). The Jinja2 template `{% include %}`s them inline as `<script>...</script>` blocks.

- Produces self-contained HTML files: no external CDN fetched at runtime (PRD requirement), no relative asset paths the user can accidentally break when moving files. Reports can be emailed, archived, or served from any static host.
- Per-file size: ~400–600 KB (Leaflet ~150 KB + Chart.js ~250 KB + route geometry). For 5 routes, ~2–3 MB total per run. Negligible for a local tool.
- Pinned version numbers recorded in each report's metadata block (for 6-month-later debugging of "why does this report look different?").
- **Alternative considered (rejected):** download CDN snapshots on first run. Violates "no network I/O at runtime," adds a bootstrap failure mode.

**Output module interface:**

```python
# output.py
def render(
    validated_set: ValidatedRouteSet,
    params: SolverParams,
    provenance: ProvenanceInfo,
    convergence: Literal["converged", "budget-exhausted", "interrupted"],
    output_dir: Path,
) -> None: ...
```

Iterates over routes; for each, renders HTML via Jinja2 and JSON via `json.dumps`; atomic-writes both (`.tmp` + `os.replace()`).

**Filename pattern:** `route-1.html`, `route-1.json`, `route-2.html`, etc. Existing files in `--output-dir` with matching names are overwritten without prompt (idempotent re-runs).

**Metadata block contents (HTML + JSON mirror each other):**

- All solver parameters used (`theta`, `min_climb_slope`, `difficulty_cap`, `l_connector`, `min_climb_ground_length`, `j_max`, `n`, `area_cap`, `untagged_policy`, `seed`, `iter_budget`, `time_budget`, `stagnation_iters`)
- Provenance: `steeproute_version`, `git_commit_short` + `-dirty` suffix, OSM extract date, DEM version, pipeline content hash (short)
- `convergence_status` (one of three values)
- Route metrics (length, D+, D−, avg gradient)
- Validation summary (pass/fail, list of violated constraints with details)

**Geometry in HTML:** route polyline rendered as GeoJSON embedded in a `<script>` tag, consumed by Leaflet.
**Geometry in JSON sidecar:** both the internal edge sequence (`edge_ids: list[int]`) and the WGS84 vertex coordinates (`vertices: list[[lat, lon, elevation]]`) for downstream tools that don't want to re-resolve edge IDs against the graph.

**Validation banner** (per Category 6b's banner logic): rendered conditionally at the top of the HTML when `route.validation` failed or a `PairwiseViolation` references this route.

### Category 10 — Error model

**Decision summary:**

- Exception hierarchy rooted at **`SteeprouteError`** in `errors.py`.
- **`PreExecutionError`** subclass for exit-code-2 conditions (bad args, cache miss, data source down, unrecoverable solver internal failure).
- **Validation failures are _not_ exceptions** — they're data state on `ValidatedRouteSet`. Exit code 1 is computed from data, not raised.
- **KeyboardInterrupt** propagates freely through the solver; caught once at the CLI wrapper in `cli/_shared.py`.
- stderr formatting: `PreExecutionError` carries a `user_message` (always printed) and optional `detail` (printed only when `--verbose` is set).

**Hierarchy:**

```python
# errors.py
class SteeprouteError(Exception):
    """Base class. Never raised directly."""

class PreExecutionError(SteeprouteError):
    """Maps to exit code 2. Raised when the tool cannot produce any output."""
    user_message: str
    detail: str | None = None

class BadCLIArgError(PreExecutionError): ...
class CacheNotFoundError(PreExecutionError): ...      # FR24 coverage miss
class CacheCorruptedError(PreExecutionError): ...     # manifest OK but graph.pkl unreadable
class DataSourceUnavailableError(PreExecutionError):  # steeproute-setup: Overpass/IGN down
    ...

class SolverError(PreExecutionError):
    """Unexpected solver-internal failure — best-so-far may be empty; treat as pre-exec tier."""
```

**Exit-code mapping (consolidated):**

| Condition | Exit code | Mechanism |
|---|---|---|
| Success, all returned routes valid | 0 | Default; nothing raised |
| Validation failure on ≥1 route (per-route or pairwise) | 1 | Computed from `ValidatedRouteSet` in CLI after output writes |
| Pre-execution / unrecoverable failure | 2 | `PreExecutionError` subclass raised, caught in `cli/_shared.py` |
| User interrupt (Ctrl-C) | 130 | KeyboardInterrupt propagated; caught in `cli/_shared.py`; best-so-far written |

**stderr formatting pattern:**

```
error: {user_message}                             # always printed for PreExecutionError
        {detail}                                  # only if --verbose
```

**What's _not_ an exception** (handled as data / log events):

- Graceful degradation (FR12, fewer than N distinct routes) — normal outcome, appears in the final run summary with a clear explanation.
- OSM-age warning (Category 4f) — `logging.warning(...)` to stderr, non-blocking.
- Validation failure (FR26–28) — data state on `ValidatedRouteSet`, rendered as HTML banner + contributing to exit code.

**Wrapper in `cli/_shared.py`:**

```python
def run_entry_point(main_fn: Callable[[], int]) -> NoReturn:
    try:
        code = main_fn()                          # may return 0 or 1
    except PreExecutionError as e:
        sys.stderr.write(f"error: {e.user_message}\n")
        if verbose and e.detail:
            sys.stderr.write(f"        {e.detail}\n")
        code = 2
    except KeyboardInterrupt:
        # best-so-far writing is done inside main_fn's try-block; by the time
        # KeyboardInterrupt reaches here, outputs are already on disk.
        code = 130
    sys.exit(code)
```

## Implementation Patterns & Consistency Rules

_Rules that fill gaps left by tooling (ruff, BasedPyright) — concerns AI agents would otherwise implement inconsistently. These sit on top of decisions in the Core Architectural Decisions section; they don't re-open them._

### Python code conventions

| Rule | Rationale |
|---|---|
| `logger = logging.getLogger(__name__)` at module top in any module that logs | Standard pattern; gives hierarchical log-level control via the `steeproute.*` tree. Tests can mute sub-trees. |
| All structured data uses `@dataclass`, never loose `dict`s or `TypedDict`. `frozen=True, slots=True` for anything that crosses module boundaries | BasedPyright-friendly; immutability prevents subtle mutation bugs; `slots` saves memory for hot-path data (`Route`, `Solution`, `Edge`). |
| Absolute imports only (`from steeproute.solver.distinctness import TopNTracker`). No relative imports | Easier to refactor; tools resolve better; clearer when reading. |
| Module-internal names prefixed with `_`. `__all__` only when curating a deliberately-restricted export set — not out of habit | The `_` prefix tells agents what's safe to rename. |
| No top-level side effects in importable modules (no `print`s, no I/O, no config reads). Side effects live in `cli/query.py::main` and `cli/setup.py::main` | Tests can import modules freely; no surprise behavior. |

### Type hints and data

| Rule | Rationale |
|---|---|
| PEP 604 union syntax (`int \| None`), never `Optional[int]` or `Union[int, None]` | Python 3.13 — no compat concerns. Consistency; BasedPyright prefers it. |
| Built-in generics (`list[X]`, `dict[str, Y]`), never `typing.List` / `typing.Dict` | Modern idiom; fewer imports. |
| Avoid `Any` except at explicit external boundaries (OSM response parsing, pickled-data loading). Each `Any` gets a short inline comment explaining why | `Any` silently escapes type coverage. |
| Immutable defaults only: no `def f(x=[])`. Use `x: list[int] \| None = None` with internal defaulting | Standard anti-footgun. |

### Serialization conventions

| Rule | Rationale |
|---|---|
| JSON field names use `snake_case` in all sidecars and cache manifests | Python-native convention; `jq` filters read cleanly; matches Python attribute names 1:1. |
| Datetimes serialized as ISO 8601 UTC with `Z` suffix (`2026-04-22T14:30:00Z`), via a helper in `provenance.py` | Unambiguous, universally parseable, string-sortable. |
| Floats rounded at the serialization boundary to field-appropriate precision (6 decimals for lat/lon, 2 for metric distances, 3 for gradients) — never at computation time | Hot-path stays full-precision; surface values stay readable. |
| All atomic JSON writes go through a single helper in `cache.py`: `write_json_atomic(path, obj)` handling `.tmp` + `os.replace()` | Single point of atomicity guarantee; no per-site reimplementation. |

### Logging and errors

| Rule | Rationale |
|---|---|
| Log level discipline: `DEBUG` internal diagnostics; `INFO` reserved for `--verbose` significant events; `WARNING` non-fatal user-actionable (OSM-age, degenerate inputs); `ERROR` fatal pre-exec conditions | Avoid WARNING-spam; reserve for actionable signals. |
| Exception chaining: `raise NewError(...) from original` when re-raising. Bare re-raises of wrapped exceptions are forbidden | Preserves debugging context; ruff flags bare re-raises. |
| `PreExecutionError` subclasses carry `user_message` (required) and `detail` (optional). No additional fields unless a subclass structurally needs them | Consistent rendering in `cli/_shared.py` wrapper. |
| Progress output uses `print()` only — not `logging.info`. `logging` is strictly stderr-diagnostic | Keeps stream discipline (Category 8) explicit; no accidental handler leaks. |

### Test organization

| Rule | Rationale |
|---|---|
| Three sub-directories under `tests/`: `tests/unit/`, `tests/integration/`, `tests/e2e/` — matching the PRD's three test layers | Clear test-layer separation; CI runs subsets. |
| Test file naming mirrors the module under test: `tests/unit/test_distinctness.py` tests `solver/distinctness.py` | Finding a module's test is mechanical. |
| Shared fixtures in `tests/conftest.py` (top-level) or `tests/<layer>/conftest.py` (layer-specific). No fixture files scattered elsewhere | pytest convention; agents can find fixtures without grep. |
| Test function naming: `test_<unit>_<scenario>` — e.g. `test_jaccard_exceeds_threshold_is_rejected` | Reads like sentences; discovery-friendly. |

### Numerical and data discipline

| Rule | Rationale |
|---|---|
| Named constants at module scope for all magic numbers affecting output (slope-floor defaults, smoothing window sizes, DEM resample step). Never inline in function bodies | Tunable from one place; visible in review. |
| Float comparisons use explicit tolerances: `math.isclose(a, b, abs_tol=...)`. Never `a == b` on floats | Numerical correctness in tests and validation. |
| Graph edge ordering: when serializing an edge-set for canonical hashing (Jaccard, cache keys), sort edges by `(node_u_id, node_v_id, key)` tuple | Deterministic hashes regardless of iteration order. |

### Documentation discipline

| Rule | Rationale |
|---|---|
| Module docstrings: one short line stating the module's role. Longer only on modules with non-obvious conceptual content (solver, cache, validator) | One-line signal; no pattern-matching boilerplate. |
| Function docstrings: required on the public API of a sub-package's `__init__.py` or a flat module. Skipped on module-internal helpers | Code readability beats over-documentation. |
| Google-style docstrings (`Args:`, `Returns:`, `Raises:`) | One consistent style; widely understood. |

### Key anti-patterns to avoid

Flagged explicitly so agents don't drift into them:

- **Singletons or module-level mutable state.** All state lives on objects passed explicitly (solver instance, tracker, validated set).
- **Silent broad `except Exception:` catches.** Any broad catch logs the original and either re-raises with `from e` or wraps in a `SteeprouteError` subclass.
- **Environment-variable reads inside the codebase**, except `cli/_shared.py` using `platformdirs` to resolve cache location. All runtime configuration enters through CLI flags.
- **Inline file-path string building.** Use `pathlib.Path` operations exclusively.
- **Re-implementing atomic writes.** Always via the `cache.py` helper.
- **Progress via `logging.info`.** Progress goes to stdout via `print`; logging is stderr-bound.

## Project Structure & Boundaries

### Complete project tree

```
steeproute/                              # repo root (currently `bmad-test/`; renamed by Copier)
├── README.md                            # replaces copier-generated one; includes gallery
├── pyproject.toml                       # [project.scripts] for steeproute + steeproute-setup
├── uv.lock                              # committed — reproducible installs
├── .python-version                      # 3.13
├── .gitignore                           # inherited from template + project additions
├── .copier-answers.yml                  # template provenance; kept tracked
├── .github/
│   └── workflows/
│       ├── ci.yml                       # test + lint + type-check (from template; populated in testing-strategy step)
│       └── publish.yml                  # PyPI publish — INERT; left as-is, never triggered
├── src/
│   └── steeproute/
│       ├── __init__.py
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── query.py                 # `steeproute` entry point
│       │   ├── setup.py                 # `steeproute-setup` entry point
│       │   └── _shared.py               # shared click decorators, run_entry_point wrapper
│       ├── pipeline/
│       │   ├── __init__.py              # orchestrator: wires stages 1–7 (setup) and 8–9 (query)
│       │   ├── osm.py                   # stages 1–2
│       │   ├── smoothing.py             # stages 3–4, 6
│       │   ├── dem.py                   # stage 5
│       │   ├── climbs.py                # stages 7–8
│       │   └── graph.py                 # stage 9
│       ├── solver/
│       │   ├── __init__.py
│       │   ├── grasp.py                 # GRASP main loop
│       │   ├── anytime.py               # best-so-far tracking; interrupt-safety hooks
│       │   └── distinctness.py          # TopNTracker + Jaccard filter
│       ├── templates/                   # package data
│       │   ├── route.html.j2            # Jinja2 report template
│       │   └── assets/
│       │       ├── leaflet-1.9.4.min.js
│       │       ├── leaflet-1.9.4.min.css
│       │       └── chart-4.4.0.min.js
│       ├── validator.py                 # runtime route validation (FR26–28)
│       ├── cache.py                     # cache I/O, key hashing, atomic writes, coverage check
│       ├── output.py                    # HTML + JSON rendering
│       ├── progress.py                  # ProgressEvent dataclass + helpers
│       ├── errors.py                    # SteeprouteError hierarchy
│       ├── models.py                    # Route, RouteValidation, ValidatedRouteSet, Edge, Solution, ProvenanceInfo
│       └── provenance.py                # commit hash + dirty flag; OSM/DEM version resolution; datetime helpers
├── tests/
│   ├── conftest.py                      # cross-layer shared fixtures (rare)
│   ├── unit/
│   │   ├── conftest.py                  # crafted graphs, fake RNG seeds, known-violating routes
│   │   ├── test_distinctness.py
│   │   ├── test_smoothing.py
│   │   ├── test_climbs.py
│   │   ├── test_graph.py
│   │   ├── test_validator.py
│   │   ├── test_cache.py
│   │   ├── test_models.py
│   │   ├── test_progress.py
│   │   └── test_provenance.py
│   ├── integration/
│   │   ├── conftest.py                  # toy-area fixtures (programmatic per Appendix A)
│   │   ├── test_pipeline_end_to_end.py
│   │   ├── test_cache_roundtrip.py      # write → read → hash-match; atomic-write crash-safety
│   │   ├── test_solver_on_toy_graph.py  # GRASP vs. exhaustive oracle (Appendix A(c))
│   │   ├── test_metamorphic.py          # invariants from Appendix A(b)
│   │   └── test_oracle_correctness.py   # oracle correctness (Appendix A)
│   └── e2e/
│       ├── conftest.py                  # pinned real-area fixtures
│       ├── goldens/                     # hash-tuple goldens for pinned regressions
│       │   ├── grenoble_10km.json
│       │   └── pelvoux_8km.json
│       ├── test_cli_smoke.py            # both CLIs end-to-end
│       ├── test_pinned_regressions.py   # Appendix A(d) golden hash comparisons
│       └── test_validation_failure_paths.py
├── docs/
│   └── examples/                        # pre-computed report gallery linked from README (3–5 regions)
├── _bmad/                               # BMAD workflow state (kept in repo)
├── _bmad-output/                        # PRD, brainstorming, architecture
│   ├── brainstorming/
│   └── planning-artifacts/
│       ├── prd.md
│       └── architecture.md
└── .claude/                             # Claude Code config + skills
```

**Runtime-resolved paths (not in repo):**

- **Cache root**: `platformdirs.user_cache_dir("steeproute")` — e.g. `%LOCALAPPDATA%\steeproute\Cache\` on Windows. Overridable via `--cache-dir`.
- **Output dir**: user's `--output-dir`, default `./results/`.
- **DEM files**: auto-downloaded by `steeproute-setup` from the IGN Géoplateforme WMS (RGE ALTI HIGHRES) for the requested area, cached under `<cache-root>/steeproute/dem/`; not checked into the repo. No `--dem-path` flag — DEM acquisition mirrors the live OSM fetch.

### Template files retained vs. dropped

| Template item | Action |
|---|---|
| PyPI publish workflow (`.github/workflows/publish.yml`) | **Leave inert.** Gated on release-tag events; nothing triggers it. Deleting would require template drift. |
| Dynamic versioning via git tags | **Leave active.** Populates `pyproject.toml` version from the latest tag. Harmless; without tags, version stays `0.1.0`. |
| Template-generated `README.md` | **Replace** with a project-specific README (first implementation story). |
| Template `docs/` site scaffolding (if any) | **Drop.** We use `docs/examples/` only for the report gallery, not a site. |

### FR → module mapping

| FR | Where it lives |
|---|---|
| FR1 — area via center/radius | `cli/_shared.py` (flag parsing), `cli/query.py`, `cli/setup.py` |
| FR2 — area-cap rejection | `cli/_shared.py` (validation), `errors.py` (`BadCLIArgError`) |
| FR3–9 — solver constraint/parameter flags | `cli/_shared.py` (click option decorators) |
| FR10 — vertical-effort maximization + strict containment | `solver/grasp.py` + `pipeline/graph.py` |
| FR11 — top-N with distinctness | `solver/distinctness.py` (TopNTracker) |
| FR12 — graceful degradation | `solver/distinctness.py` + `cli/query.py` (summary) |
| FR13 — progress | `progress.py` + `solver/grasp.py` (throttled callback) + `cli/query.py` (renderer) |
| FR14 — Ctrl-C best-so-far | `solver/anytime.py` + `cli/query.py` (try/except wrapper) |
| FR15 — HTML per route | `output.py` + `templates/route.html.j2` |
| FR16 — JSON sidecar | `output.py` |
| FR17 — Leaflet map | `templates/route.html.j2` + `templates/assets/leaflet-*` |
| FR18 — gradient-colored elevation profile | `templates/route.html.j2` + `templates/assets/chart-*` |
| FR19 — report metadata | `output.py` + `provenance.py` + `models.py` |
| FR20 — configurable output dir | `cli/_shared.py` (flag) |
| FR21 — stable filename pattern | `output.py` (`route-<i>.{html,json}`) |
| FR22 — run summary on stdout | `cli/query.py` |
| FR23 — `steeproute-setup` | `cli/setup.py` |
| FR24 — fail-fast on unprepared area | `cache.py` (coverage check) + `cli/query.py` (exit 2) |
| FR25 — local cache | `cache.py` |
| FR26 — runtime validation | `validator.py` |
| FR27 — validation-failure banner | `output.py` + `templates/route.html.j2` |
| FR28 — exit code + write-to-disk | `cli/query.py` + `output.py` |
| FR29 — seed reproducibility | `solver/grasp.py` (RNG threading) + `output.py` + `cli/_shared.py` (`--seed`) |
| FR30 — exit codes | `cli/_shared.py` (`run_entry_point`) + `errors.py` |
| FR31 — start-at-junction (opt-in) | `pipeline/graph.py` (junction annotation) + `solver/grasp.py` (seed restriction) + oracle + `validator.py` |
| FR32 — direction-aware descent cap (opt-in) | metrics stage (`max_windowed_descent_grad`) + `solver/grasp.py` (descent feasibility) + oracle + `validator.py` |

### Internal data flow

**Query CLI** (`steeproute`):

```
cli/query.py::main
  → cli/_shared.py (parse area, flags, seed)
  → cache.py (load_for_area) ── returns prepared graph (stages 1–7 output)
  → pipeline.__init__ (run_query_stages 8–9) ── returns contracted climb-graph
  → solver/grasp.py (GraspSolver.run) ── returns list[Solution]   [may raise KeyboardInterrupt]
  → validator.py (validate) ── returns ValidatedRouteSet
  → output.py (render) ── writes HTML + JSON per route, atomic
  → cli/query.py (compute exit code from ValidatedRouteSet + convergence status)
```

**Setup CLI** (`steeproute-setup`):

```
cli/setup.py::main
  → cli/_shared.py (parse area, flags)
  → pipeline.__init__ (run_setup_stages 1–7) ── builds MultiDiGraph with per-edge attributes
      ↳ pipeline/osm.py       (stages 1–2: OSM load + filter)
      ↳ pipeline/smoothing.py (stage 3: polyline smoothing)
      ↳ pipeline/smoothing.py (stage 4: resample)
      ↳ pipeline/dem.py       (stage 5: DEM sample)
      ↳ pipeline/smoothing.py (stage 6: elevation smoothing)
      ↳ pipeline/climbs.py    (stage 7: per-edge metrics)
  → cache.py (write_entry) ── atomic write of graph.pkl + manifest + index update
  → cli/setup.py (summary)
```

**External data sources** (setup CLI only): OSM via `osmnx`/Overpass; DEM auto-downloaded from the IGN Géoplateforme WMS (`pipeline/dem_download.py`). No external runtime I/O in the query CLI.

### Boundaries

For this single-process CLI, "boundaries" collapse to a few module-interface contracts worth naming explicitly:

- **Cache boundary.** `cache.py` is the sole reader and writer of the cache directory. Everything else uses its API (`load_for_area`, `write_entry`, `check_coverage`, `write_json_atomic`). No other module does `os.replace()` on cache files.
- **Template boundary.** `templates/` is consumed only by `output.py`. The Jinja2 environment is constructed there and not shared.
- **Solver boundary.** `solver/` owns `Solution` (internal) and `TopNTracker`; callers see `list[Solution]` opaquely until the validator converts them to `Route`.
- **Pipeline boundary.** `pipeline/` stages are pure functions; outside code calls orchestrator functions in `pipeline/__init__.py`, never individual stages.

### Category 11 — Testing strategy

_Closes the testing-strategy gap flagged in Step 2 as deferred-to-architecture with PRD Appendix A as starting material._

**Decision summary:**

- **11a**: Commit Appendix A modalities (a) constraint invariants, (b) metamorphic tests, (c) exhaustive comparison on toy, (d) pinned regression goldens, plus property-based tests on geometric/elevation primitives. Skip (e) diagnostic statistics for v1.
- **11b**: Hybrid fixture approach — programmatic primary + handcrafted oracle + pinned real-data regression.
- **11c**: CI gates — GRASP/exhaustive ratio ≥ 0.80 on seeded toy fixture; zero-tolerance regression goldens; invariants/metamorphic/oracle pass-required.
- **11d**: 5-field hash tuple per route including canonical-edge-sequence SHA256; explicit `update-regression` workflow.
- **11e**: Structural test requirements + 80% / 95% coverage floors (pure-logic modules hold the higher bar).

**Appendix A modalities (11a):**

| Modality | In v1 | Lives in |
|---|---|---|
| (a) Constraint invariants (post-solve assertions) | ✅ | Implemented by `validator.py` (Category 6); tests assert wiring + specific violation surfacing |
| (b) Metamorphic tests — 8 invariants from Appendix A | ✅ | `tests/integration/test_metamorphic.py` |
| (c) Exhaustive comparison on toy graph | ✅ | `tests/integration/test_solver_on_toy_graph.py` + `test_oracle_correctness.py` |
| (d) Pinned real-query regression goldens | ✅ | `tests/e2e/test_pinned_regressions.py` + `tests/e2e/goldens/` |
| (e) Diagnostic run statistics | ❌ (skip) | Dev aid only; implementable later as `--diagnose` or standalone script |
| Property-based tests (`hypothesis`) on geometric/elevation primitives | ✅ | `tests/unit/` alongside regular unit tests |

**Fixture approach (11b):**

- **Programmatic toy graph generator** — primary fixture. Parameterizable: node count ~20–30, variable edge density, configurable terrain (gradients, lengths, cliff-free). Lives in `tests/integration/conftest.py` as a factory fixture. Used by (b), (c), and solver-component unit tests. No coupling to real OSM/DEM snapshots; regeneratable forever in CI.
- **Handcrafted oracle fixtures** — 1–3 hand-built tiny graphs (5–8 nodes) with known optimal routes by inspection. Used by `test_oracle_correctness.py` to verify the brute-force enumerator itself. Addresses Appendix A's concern that programmatic generators satisfy author assumptions.
- **Pinned real-data regression fixtures** — 2–3 small Grenoble-area cutouts (5km-radius around specific points) with fixed seeds. Stored as prepared cache entries (committed into `tests/e2e/fixtures/` or a dedicated location) plus golden-hash files in `tests/e2e/goldens/`. Used by (d).

**CI gates (11c):**

| Gate | Behavior on failure |
|---|---|
| GRASP/exhaustive ratio on seeded toy ≥ 0.80 | CI fails. Threshold tightens once baseline is established (target ~0.85–0.90). |
| Pinned regression goldens match exactly | CI fails. Zero tolerance (seeded GRASP is deterministic; drift = change worth noticing). |
| Constraint invariants, metamorphic tests, oracle correctness | CI fails. Pass-required, no threshold. |
| Coverage thresholds (see 11e) | CI fails. |

`pytest.skip`/`xfail` are not permitted as silent workarounds for these gates. If a gate needs to be disabled temporarily, it requires an explicit issue reference and commit-message rationale.

**Regression golden hash scheme (11d):**

Per-route golden tuple:

```json
{
  "fixture_name": "grenoble_10km",
  "seed": 42,
  "route_index": 1,
  "objective": 4832.5,
  "d_plus_m": 2417.3,
  "d_minus_m": 2415.1,
  "edge_count": 127,
  "canonical_edge_sequence_hash": "a3f1b29c..."
}
```

Stored as `tests/e2e/goldens/<fixture_name>.json` (one file per fixture, containing the top-N golden tuples).

**Canonical edge sequence hash:** edges in the route sorted by `(node_u_id, node_v_id, key)` tuple (consistent with the rule in Implementation Patterns §Numerical and data discipline), joined with a separator, SHA256. Captures graph-level edge identity, not just aggregate metrics — `(objective, D+, D-, edge_count)` can collide while the underlying route silently changes.

**Golden update workflow:**

```
uv run update-regression [--fixture FIXTURE_NAME | --all]
```

Re-runs the solver on the named fixture(s), writes new goldens to disk. Updates require an explicit commit message rationale to prevent rubber-stamping.

**Coverage targets (11e):**

**Structural requirements** (no exceptions — CI enforces by requiring specific test files to exist and pass):

- Every pure function in `pipeline/` has unit tests covering happy path + ≥1 edge case.
- `solver/distinctness.py` (`TopNTracker`) has unit tests for: admission, rejection-by-worse, rejection-by-Jaccard, substitution.
- `validator.py` has unit tests per constraint (crafted-violating + crafted-clean fixtures).
- `cache.py` has integration tests for: write-then-read roundtrip, atomic-write crash safety (simulated mid-write abort), coverage check on containing vs. non-containing areas.
- `output.py` has unit tests asserting every metadata field appears in both HTML and JSON outputs.
- Both CLIs have smoke tests covering the full happy path (exit 0).
- Validation-failure paths have e2e tests asserting exit code 1 + banners + disk writes.

**Coverage percentage (soft floor):**

- `pytest-cov` tracks coverage on `src/steeproute/**/*.py`, excluding `templates/` and `cli/` (bootstrap/templated code).
- **Minimum 80% overall**; **95% on pure-logic modules** (`pipeline/`, `solver/distinctness.py`, `validator.py`, `cache.py`).
- CI fails if either threshold is missed. Coverage report uploaded as a CI artifact per run.

**Explicitly NOT a coverage target:**

- Anything in `cli/` beyond smoke tests (argument-parsing glue is CLI-framework-tested).
- `templates/` (Jinja2 templates — exercised by smoke tests only).
- `output.py`'s Jinja2 integration (exercised by smoke + e2e tests only).

## Architecture Validation Results

### Coherence — ✅

All decisions cross-reference consistently. Spot-checked couplings:

- `cli/_shared.py`'s `run_entry_point` wrapper is referenced by Category 2, Category 5b, and Category 10 with consistent semantics.
- Category 3's pipeline split (`setup` = stages 1–7, `query` = 8–9) aligns with Category 4's cache scope.
- Category 4's `pipeline_content_hash` covers exactly the modules Category 3 assigns to setup stages.
- Category 5's `convergence_status` three-value contract feeds Category 9's HTML metadata.
- Category 6's `ValidatedRouteSet` feeds Category 9's renderer and Category 10's exit-code computation.
- Category 11's test targets align with Categories 1, 6, 9's module assignments.

**Minor concern flagged:** directory-rename race on Windows (Category 4d's swap pattern) has a brief window between steps 2 and 3 where a concurrent reader sees no `<hash>/` directory. For N=1 the probability is near-zero; documented as a known edge case. Hardening (advisory locking, retry-on-transient) is a contained change if it ever bites.

### Requirements coverage — ✅

**Functional requirements:** all 30 FRs have explicit module homes (see FR→module mapping in Project Structure). Spot-checked each against decisions — no FR mentioned but unsupported.

**One PRD clarification:** polygon area mode (`--polygon`) was mentioned in brainstorming's v1 scope statement but the PRD body scoped v1 to `--center/--radius` only (FR1). Architecture correctly treats center/radius as the sole v1 input mode. PRD body is authoritative.

**Non-functional requirements:**

- **Performance**: `--time-budget`, stagnation termination, progress reporting + manual kill provide budget-visibility mechanisms. 16 GB memory envelope is a measurement concern (not architecturally enforced) per PRD's explicit stance.
- **Reliability**: atomic cache writes, best-so-far preservation, seeded RNG, cache integrity via manifest-as-commit-signal.
- **Integration**: `DataSourceUnavailableError` for Overpass/IGN failures at setup; no runtime network I/O.
- **Portability**: Windows-primary atomic-write pattern documented; Linux expected to work incidentally.
- **Not applicable**: Security, Scalability, Accessibility — excluded intentionally in PRD and not reopened by architecture.

### Implementation readiness — ✅

Architecture is complete enough for implementation of all components, including CI gates:

- CLI framework, argument surface, entry points — decided (Cats 2, 6, 10).
- Module layout, file structure, boundaries — decided (Cats 1, Step 6).
- Data pipeline — decided (Cat 3).
- Cache architecture — decided (Cat 4).
- Solver architecture — decided (Cat 5).
- Validation — decided (Cat 6).
- Inter-CLI contract — decided (Cat 7).
- Logging / progress / streams — decided (Cat 8).
- Output rendering — decided (Cat 9).
- Error model — decided (Cat 10).
- Testing strategy — decided (Cat 11).
- Implementation patterns — decided (Step 5).

### Architecture-owned additions to the flag surface

Architecture introduced CLI flags not enumerated in the PRD, fulfilling architectural needs the PRD's "Technical Considerations (Deferred)" section anticipated:

| Flag | Introduced in | Purpose |
|---|---|---|
| `--stagnation-iters N` | Cat 5e | Early-termination window for GRASP |
| `--cache-dir PATH` | Cat 7 | Override cache root (useful for tests) |
| `--force-refresh` | Cat 4b | Rebuild cache entry despite key match |
| `--osm-age-warn-days N` | Cat 4f | OSM-staleness warning threshold |
| `--dem-version TAG` | Cat 4b | Explicit DEM version tag for cache keying (overrides the IGN-layer default) |
| `--start-at-junction` | Epic 10 (FR31) | Constrain route start endpoint to a road/trail junction (opt-in) |
| `--max-descent-slope` | Epic 10 (FR32) | Direction-aware descent-slope cap, windowed (opt-in) |
| `--dem-fetch-workers N` | Story 14.3 (Cat 3) | Override DEM tile-fetch concurrency (default: 4). Story 14.3 originally kept this as a module constant (an IGN-etiquette ceiling, not a user knob); revised at the user's request since IGN's real-world tolerance is still only validated at the one value tried — exposing it lets a user adjust without a code change if that assumption doesn't hold in practice. |
| `--workers N` | Story 14.4 (Cat 5a) | Query-side: number of processes for parallel GRASP restarts (default: 1 = byte-identical single-process path). N>1 splits the iteration budget across cores; reproducible per `(seed, workers, merge_interval)` but differs by design from N=1. CLI-layer orchestration only — never enters `SolverParams`, so no cache impact. |
| `--merge-interval N` | Story 14.4 (Cat 5a) | Query-side, parallel only: island-model elite-migration cadence — merge workers' top-Ns and re-seed every N total iterations (default 250 000; 0 = independent workers, one final merge). Lower = more cooperation → parallel reliably matches/beats single-process. CLI-layer only, no cache impact. |

Total flag surface after additions ~25–28 across both CLIs (Story 14.4 adds `--workers` + `--merge-interval` query-side) — above the PRD's threshold of ~25 for reconsidering a config file (noted; not acted on for this personal-scope tool).

**Environment-variable overrides (post-Story-14.3 hardening, 2026-07-07).** `pipeline/dem_download.py` reads three process-local tuning knobs from the environment at import: `STEEPROUTE_DEM_HTTP_TIMEOUT_S` (per-request WMS socket timeout, default 30), `STEEPROUTE_DEM_FETCH_RETRIES` (per-tile transient-failure retry attempts, default 3), `STEEPROUTE_DEM_FETCH_BACKOFF_S` (exponential-backoff base, default 0.5). These are the first environment-variable configuration surface in the codebase, deliberately kept out of the CLI flag table above: they're operational tuning for `steeproute-setup`'s own network behavior against a third-party WMS (akin to a retry/timeout sidecar config), not per-invocation user choices, so an env var avoids `--help` clutter for a knob most users never touch. This does not revisit Cat 7's "no env vars" decision — that decision scopes shared state *between* `steeproute` and `steeproute-setup`; these variables are read by `steeproute-setup` alone and carry no inter-CLI contract. A malformed value logs a warning and falls back to the default rather than crashing at import (before the CLI's `BadCLIArgError` tier exists to handle it cleanly).

### Nice-to-have items (deferred to implementation)

Not blocking; implementation-detail decisions:

- **`index.json` corruption recovery**: if missing or unparseable at read time, `cache.py` walks `areas/` and rebuilds in-memory (not persisted until next write).
- **First-run bootstrap**: `cache.py` handles absent cache root and `areas/` directory via `mkdir -p` semantics.
- **`--stagnation-iters`, `--iter-budget`, `--progress-interval` defaults**: tuned empirically during implementation against real Grenoble queries.

### Overall readiness assessment

**Status:** READY FOR IMPLEMENTATION.

**Confidence level: high.** All FRs and NFRs covered; all cross-cutting concerns addressed; testing-strategy gap closed; flag surface consistent; implementation patterns filled in. No blocking gaps.

**Key strengths:**

- Clear separation between parameter-independent (cached) and parameter-dependent (query-time) pipeline work — directly delivers Journey 2's fast-re-query behavior.
- Validation failures as data state rather than exceptions — matches PRD's requirement that failed routes land on disk with banners.
- Interrupt handling centralized at CLI layer — solver stays oblivious to signals.
- Testing strategy grounded in Appendix A's options with an explicit compromise on fixtures.
- Manifest-as-commit-signal pattern gives atomic cache writes without cross-platform directory-rename complications in the common case.

**Areas for future enhancement (not v1 blockers):**

- Simulated-annealing polish on top GRASP candidates (PRD Phase 2).
- Cliff-gradient penalty in ranking (PRD Phase 2).
- ~~Parallelism in the GRASP solver (Cat 5a, conditional on measurement).~~ **Realized in Story 14.4** (`--workers N`) — see Cat 5a "Parallelism realized".
- CI threshold tightening (0.80 → 0.85–0.90) once baseline is established.
- Directory-rename race hardening on Windows (advisory locking, retry-on-transient) if the rare race materializes in practice.

### Implementation handoff

**First implementation story:** apply the Copier template to migrate from the current `uv init` scaffold to the `simple-modern-uv` layout, then layer the `src/steeproute/` package structure on top.

```bash
copier copy gh:jlevy/simple-modern-uv .
# Answer prompts: project_name=steeproute, author=Yann Fontana, etc.
# After generation: add [project.scripts] entries for steeproute + steeproute-setup
# Migrate main.py to src/steeproute/cli/query.py and cli/setup.py placeholders
# Create the sub-package skeletons from Step 6's project tree
```

**AI agent handoff guidance:**

- All decisions in the Core Architectural Decisions section are authoritative; do not re-decide.
- Implementation Patterns & Consistency Rules fill gaps ruff/BasedPyright don't catch; follow them.
- Project Structure's module assignments are the target layout — new code goes into the module indicated by the FR→module mapping.
- For any architectural question not answered in this document, prefer proposing the smallest defensible implementation that matches documented patterns, not expanding scope.
