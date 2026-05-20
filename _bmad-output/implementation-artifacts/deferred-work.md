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

---

## Deferred from: lightweight review of 1-6-validate-area-specification-at-cli-boundary.md (2026-05-04)

**Target story: whichever epic first consumes `--radius` as a real value** (likely Epic 2 — `steeproute-setup` area-polygon construction, or Epic 3 — query-side area resolution).

**Status: ✅ Resolved in Story 2.1 (2026-05-06).** `osm_load(area)` now raises `BadCLIArgError` for `radius_km <= 0`. CLI wiring in Story 2.8 inherits the guard.

| # | Finding | File | Detail |
|---|---------|------|--------|
| 1 | Negative `--radius` silently accepted | `src/steeproute/cli/_shared.py` (`validate_area_size`) | `click.FLOAT` does not enforce non-negativity, and `validate_area_size` checks `π·r² > area_cap` which is symmetric in `r` (negative radius → positive area, may be < cap). So `steeproute --center 45,6 --radius -10` exits 0 today. Out of scope for Story 1.6 (AC #2 is strictly about cap-exceeding, not radius validity). The right enforcement site is wherever `--radius` first becomes a real geometric value (area polygon construction). Either: (a) add `radius > 0` check to `validate_area_size` and rename/split, or (b) reject in the consuming code with a context-specific error. Pick once we know the consumer. |

---

## Deferred from: code review of 2-1-implement-pipeline-stages-1-2-osm-ingestion-trail-filtering-and-commit-real-osm-test-fixture.md (2026-05-07)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | No error handling around `osmnx.graph_from_point` — raw `requests`/`osmnx` exceptions leak past `osm_load` | Story 2.9 | Story 2.9 ACs explicitly cover the `DataSourceUnavailableError` → exit-2 mapping for both OSM and DEM source failures. |
| 2 | Empty-graph result from osmnx (no trails in area) — currently silent | Story 2.9 / Story 2.5 | Either Story 2.9's source-unavailable handling can expand to "empty result", or Story 2.5's orchestrator asserts non-empty before downstream stages. |
| 3 | Concurrent test runs mutate shared `osmnx.settings.useful_tags_way` | Future (test-infra) | Not running parallel tests today; revisit if/when `pytest-xdist` or similar is adopted. Mitigation: a session-scoped autouse fixture that restores `useful_tags_way` after each test. |
| 4 | Zero-length `LineString` when `u==v` or coincident endpoints in `normalize_edges` geometry synthesis | Story 2.2 | Stages 3-4 do polyline math (smoothing + resampling) where zero-length input may divide-by-zero or produce empty output. Decide there whether to skip self-loop edges, error out, or substitute a Point. |
| 5 | `filter_trails` returns `graph.copy()` then removes edges → isolated/orphan nodes retained in output | Story 2.5 | Node-pruning policy is an orchestrator-level call (some downstream stages may want orphans for diagnostic context, others want a clean subgraph). Decide once stages 3-7 are wired. |
| 6 | `out.copy()` in `filter_trails` may OOM on very large input graphs | Future (perf) | Premature optimization until benchmarks surface it. `--area-cap` mitigates indirectly by bounding input size. Could switch to `nx.subgraph_view` or `edge_subgraph` for streaming filtering if it becomes an issue. |
| 7 | Live-test drift tolerance ±10% on a 1208-edge fixture (~120 edges) may flap on bulk-edits in Le Sappey | Future (live-test maintenance) | Empirical — defer until observed. Mitigation if it flaps: widen the band, switch to a less-active area, or pin against a snapshot of Overpass's last-known-good state instead of live. |
| 8 | `radius_km` exceeding Overpass query limits → opaque osmnx error | Story 2.8 | Setup-side `--area-cap` (or an equivalent radius cap) hasn't landed yet. Wire a sanity ceiling when the setup CLI gains its area-cap option in 2.8. |

---

## Deferred from: code review of 2-2-implement-pipeline-stages-3-4-2d-polyline-smoothing-and-resampling.md (2026-05-07)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | No upper bound on `n_intervals` in `_resample_meters` — pathological `total / spacing_m` could blow memory/CPU | Story 2.5 / 2.8 | For a hypothetical 1000-km polyline at 0.001-m spacing, `n_intervals ≈ 10⁹`. Today: `--area-cap` bounds polyline length upstream, spacing is the internal default constant — combination not reachable. Add a sanity ceiling when CLI exposes a spacing override (Story 2.8) or in the orchestrator (Story 2.5). [src/steeproute/pipeline/smoothing.py:190] |

---

## Deferred from: code review of 2-3-implement-pipeline-stage-5-dem-elevation-sampling-and-commit-real-dem-test-fixture.md (2026-05-18)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `0.0`-as-void on a user-supplied DEM with `nodata=None` is silently accepted as a legitimate elevation | Story 2.9 (DEM source / setup-time sanity) or `--dem-path` docs | A DEM whose author left nodata undeclared but used `0.0` as a void marker would yield bogus sea-level elevations for void cells. The contract "no silent NaN" doesn't promise to catch this. The production fixture has `nodata=None` but is fully covered; the failure mode is latent for user-supplied DEMs. Either Story 2.9 should add a setup-time DEM-coverage assertion, or document on `--dem-path` that nodata must be properly declared. [src/steeproute/pipeline/dem.py:109-117] |
| 2 | Inverted-bounds GeoTIFF (`bounds.left > bounds.right`, flipped origin) produces a wall of unhelpful `DEMCoverageError`s | Story 2.8 (CLI consumer of `--dem-path`) | A malformed DEM with negative pixel width or N/S-flipped origin would cause every vertex to fail the OOB guard with no hint that the raster is upside-down. Add a one-time sanity check at `rasterio.open` time: `assert bounds.right > bounds.left and bounds.top > bounds.bottom` else raise a clearer error. [src/steeproute/pipeline/dem.py:73-98] |

---

## Deferred from: code review of 2-5-implement-pipeline-orchestrator-and-integration-test-stages-1-7-end-to-end-on-real-fixture.md (2026-05-20)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `_drop_short_edges` / `_drop_orphan_nodes` mutate topology with no debug log | Story 2.8 (CLI verbose wiring) | The orchestrator drops degenerate edges and orphan nodes silently — invisible until downstream behavior surprises. A `logger.debug("dropped %d short edges, %d orphan nodes", ...)` call would surface real OSM-fixture regressions. The right time to add this is when Story 2.8 wires the `--verbose` plumbing — `logging` configuration needs a sink first; adding logger calls now means they fire into the default `WARNING` root logger config and are invisible anyway. Add the debug logs alongside the CLI verbose wiring. [src/steeproute/pipeline/__init__.py:147-183] |

---

## Deferred from: code review of 2-7-implement-atomic-cache-write-read-and-index-maintenance.md (2026-05-20)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | KeyboardInterrupt between manifest commit and `rebuild_index` leaves stale `index.json` | Story 2.10 (`check_coverage` opportunistic rebuild) | Architecture §Cat 4d says manifest is the commit signal; the index is derived state. If a user `Ctrl-C`s after `manifest.json`'s `os.replace` lands but before `write_entry`'s final `rebuild_index` call runs, the entry is readable via `read_entry(cache_key)` but `index.json` doesn't list it. Next `write_entry` (any key) fixes it, but a `steeproute` query invocation before the next setup would hit the stale index — which is the coverage-check path. Right time to add an opportunistic `rebuild_index` call on the read path is Story 2.10's `check_coverage` (it already walks `areas/*/manifest.json` semantically; sharing the rebuild is cheap). [src/steeproute/cache.py:301-360] |
| 2 | `rebuild_index` swallows `CacheCorruptedError` silently — no log, no counter | Story 2.8 (CLI verbose wiring) | A cache directory entirely full of corrupt manifests yields a successful empty-index rebuild indistinguishable from "no entries". A `logger.warning("skipping corrupt manifest at %s: %s", ...)` would surface this for `--verbose` users, but logging infrastructure isn't wired yet — same reason Story 2.5's `_drop_*` debug logs were deferred to Story 2.8. Add alongside the rest of the CLI verbose plumbing. [src/steeproute/cache.py:432-438] |

## Deferred from: lightweight review of 2-6-implement-cache-key-hashing-manifest-schema-and-provenance-helpers.md (2026-05-20)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `get_commit_short` treats untracked files as `-dirty` | Story 2.8 (CLI consumer of the commit string) | `git status --porcelain` includes untracked files by default. After a typical `bmad-dev-story` run that leaves story / planning artifacts in the working tree (or any local-only dev-tooling files outside `.gitignore`), the commit string flips to `-dirty` even though no tracked file was modified. Architecture's "dirty flag if working tree modified" is ambiguous on untracked. The right time to decide is Story 2.8, when the CLI surface starts emitting the commit string in user-visible places — either filter via `--untracked-files=no` (treat only tracked-file changes as dirty) or accept the current behavior with a docs note. [src/steeproute/provenance.py:48-54] |

## Deferred from: code review of 2-4-implement-pipeline-stages-6-7-elevation-smoothing-and-per-edge-metrics.md (2026-05-18)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `compute_edge_metrics` has no guard against `length_m == 0` or non-finite elevations | Story 2.5 (orchestrator-level stage-contract enforcement) | Stage 7 trusts stage 4 to drop degenerate edges, but `_resample_meters` only catches "all coords identical" — an out-and-back polyline `[(0,0), (1,1), (0,0)]` or a closed loop where `u == v` would pass stage 4 with effective `length_m ≈ 0` and stage 7 would raise a cryptic `ZeroDivisionError` with no edge context. Tightening `is_valid_for_metrics` (Story 2.4 patch P5) closes the hypothesis-test gap, but production runtime has no guard — by current "trust internal code, only validate at system boundaries" convention. The orchestrator that wires stages 1–7 is the right place to enforce inter-stage contracts: either raise a clean `PreExecutionError`-subclass naming the offending edge, or filter such edges before stage 7 sees them. [src/steeproute/pipeline/climbs.py:56, 271] |
| 2 | NaN elevation delta is silently absorbed by `_elevation_gain_loss`'s strict `>` / `<` branches | Story 2.5 (orchestrator) or Story 2.9 (DEM sanity) | `nan > 0` and `nan < 0` are both False; a vertex with NaN elevation would yield `d_plus_m = d_minus_m = 0` for its segments instead of raising. Stage 5 already raises `DEMCoverageError` on NaN-sample, so the failure mode is latent — but a future caller wiring a different elevation source could bypass that guard. Either Story 2.5's orchestrator asserts `all(math.isfinite(elev) for ...)` post-stage-6, or Story 2.9 expands DEM-sanity scope to cover this. [src/steeproute/pipeline/climbs.py:115-118] |
| 3 | Self-loops and parallel edges never asserted in stage-7 synthetic tests | Story 2.5 (pipeline integration) or whichever story first ships a fixture with self-loops | `compute_edge_metrics` iterates `out.edges(data=True, keys=True)` so parallel-edge keys are visited correctly by construction, but a self-loop edge (`u == v`) where `vertices_resampled` is out-and-back coincident in 2D would tie back to deferred item #1 above. The committed Grenoble fixture has no self-loops; real OSM data (closed-loop trails) can. Add a synthetic self-loop test once the orchestrator decides on a policy (drop / accept / error). [tests/unit/test_climbs.py — whole file] |
