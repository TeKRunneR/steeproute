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

**Status:** D2 + D5 resolved in Story 2.5 (orchestrator non-empty + orphan-prune guards). D8 resolved in Story 2.8 (`validate_setup_radius` in `cli/_shared.py` rejects `r <= 0` or `r > 50 km`). D4 resolved in Story 2.2 (stage-3 invalid-polyline drop). D1 resolved in Story 2.9 (`DataSourceUnavailableError` wrap around `osmnx.graph_from_point` in `pipeline/osm.py::osm_load`). D2 resolved-elsewhere by Story 2.5's `_assert_non_empty` guard in `pipeline/__init__.py::run_setup_stages`.

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | No error handling around `osmnx.graph_from_point` — raw `requests`/`osmnx` exceptions leak past `osm_load` | Story 2.9 | ✅ Resolved in Story 2.9 — `osm_load` wraps the `osmnx.graph_from_point(...)` call in `try/except (requests.exceptions.RequestException, OSError)` and re-raises as `DataSourceUnavailableError("OSM source unreachable.", detail=...)`. `run_entry_point` maps it to exit 2 with the stderr `error:` line; `--verbose` surfaces the wrapped exception `repr` on the detail line. [src/steeproute/pipeline/osm.py:60-90] |
| 2 | Empty-graph result from osmnx (no trails in area) — currently silent | Story 2.9 / Story 2.5 | ✅ Resolved-elsewhere — Story 2.5's `pipeline/__init__.py::_assert_non_empty` raises `PipelineContractError` with an actionable "widen --radius, switch --untagged-trails, or pick an area with more recorded trails" message immediately after `filter_trails`. The shape (empty trail set after policy filter) is categorically distinct from "OSM source unreachable" (which is what Story 2.9 covers), so leaving it as `PipelineContractError` matches Architecture §Cat 10's error-class taxonomy. [src/steeproute/pipeline/__init__.py:144-164] |
| 3 | Concurrent test runs mutate shared `osmnx.settings.useful_tags_way` | Future (test-infra) | Not running parallel tests today; revisit if/when `pytest-xdist` or similar is adopted. Mitigation: a session-scoped autouse fixture that restores `useful_tags_way` after each test. |
| 4 | Zero-length `LineString` when `u==v` or coincident endpoints in `normalize_edges` geometry synthesis | Story 2.2 | Stages 3-4 do polyline math (smoothing + resampling) where zero-length input may divide-by-zero or produce empty output. Decide there whether to skip self-loop edges, error out, or substitute a Point. |
| 5 | `filter_trails` returns `graph.copy()` then removes edges → isolated/orphan nodes retained in output | Story 2.5 | Node-pruning policy is an orchestrator-level call (some downstream stages may want orphans for diagnostic context, others want a clean subgraph). Decide once stages 3-7 are wired. |
| 6 | `out.copy()` in `filter_trails` may OOM on very large input graphs | Future (perf) | Premature optimization until benchmarks surface it. `--area-cap` mitigates indirectly by bounding input size. Could switch to `nx.subgraph_view` or `edge_subgraph` for streaming filtering if it becomes an issue. |
| 7 | Live-test drift tolerance ±10% on a 1208-edge fixture (~120 edges) may flap on bulk-edits in Le Sappey | Future (live-test maintenance) | Empirical — defer until observed. Mitigation if it flaps: widen the band, switch to a less-active area, or pin against a snapshot of Overpass's last-known-good state instead of live. |
| 8 | `radius_km` exceeding Overpass query limits → opaque osmnx error | Story 2.8 | ✅ Resolved in Story 2.8 — `validate_setup_radius(r)` in `cli/_shared.py` rejects `r <= 0` or `r > 50 km` at the CLI boundary with `BadCLIArgError`. |

---

## Deferred from: code review of 2-2-implement-pipeline-stages-3-4-2d-polyline-smoothing-and-resampling.md (2026-05-07)

**Status (2026-05-22 / Story 2.8):** D1 re-deferred to Future. Rationale: no `--spacing-m` CLI flag exists today, `spacing_m` is a module-scope constant in `pipeline/smoothing.py`, and the new `validate_setup_radius` ceiling (50 km half-side) caps polyline length upstream. The unbounded-`n_intervals` failure mode is structurally unreachable without a `--spacing-m` override. Land the cap together with `--spacing-m` if that flag ever ships.

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | No upper bound on `n_intervals` in `_resample_meters` — pathological `total / spacing_m` could blow memory/CPU | Future (`--spacing-m`-bundled) | For a hypothetical 1000-km polyline at 0.001-m spacing, `n_intervals ≈ 10⁹`. Today: `--area-cap` + `validate_setup_radius` cap polyline length upstream, spacing is the internal default constant — combination not reachable without a future `--spacing-m` override. Land the cap together with that flag. [src/steeproute/pipeline/smoothing.py:190] |

---

## Deferred from: code review of 2-3-implement-pipeline-stage-5-dem-elevation-sampling-and-commit-real-dem-test-fixture.md (2026-05-18)

**Status:** D2 resolved in Story 2.8 (inverted-bounds sanity check in `sample_elevation` raises a clearer `DEMCoverageError` instead of a per-vertex wall). D1 re-deferred to Future during Story 2.9 (rationale below).

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `0.0`-as-void on a user-supplied DEM with `nodata=None` is silently accepted as a legitimate elevation | Future (Epic 5 — README data-prep docs) | Re-deferred during Story 2.9. A `nodata is None` setup-time heuristic would false-positive on legitimately-sea-level coastal DEMs (where `0.0` is a real elevation, not a void marker). The right surface is documentation, not a runtime check: a `--dem-path` help-text note + a README data-prep section saying "nodata must be properly declared in the GeoTIFF header" tells users the contract. **Not a Story 2.9 deliverable** — land this doc work alongside Epic 5's README data-prep section. [src/steeproute/pipeline/dem.py:109-117] |
| 2 | Inverted-bounds GeoTIFF (`bounds.left > bounds.right`, flipped origin) produces a wall of unhelpful `DEMCoverageError`s | Story 2.8 (CLI consumer of `--dem-path`) | ✅ Resolved in Story 2.8 — `sample_elevation` checks `bounds.right > bounds.left and bounds.top > bounds.bottom` immediately after `rasterio.open` and raises a `DEMCoverageError` whose `user_message` names the inverted bounds. [src/steeproute/pipeline/dem.py:73-98] |

---

## Deferred from: code review of 2-5-implement-pipeline-orchestrator-and-integration-test-stages-1-7-end-to-end-on-real-fixture.md (2026-05-20)

**Status:** D1 resolved in Story 2.8 (`--verbose` plumbing + `logger.debug(...)` calls in `_drop_short_edges` / `_drop_orphan_nodes`).

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `_drop_short_edges` / `_drop_orphan_nodes` mutate topology with no debug log | Story 2.8 (CLI verbose wiring) | ✅ Resolved in Story 2.8 — `configure_cli_logging(verbose=...)` in `cli/_shared.py` flips the stderr logger to DEBUG when `--verbose` is set; the pipeline now emits `pipeline: dropped %d orphan nodes` and `pipeline: dropped %d short edges` debug lines on each non-zero prune. [src/steeproute/pipeline/__init__.py:147-183] |

---

## Deferred from: code review of 2-7-implement-atomic-cache-write-read-and-index-maintenance.md (2026-05-20)

**Status:** D2 resolved in Story 2.8 (`rebuild_index` now emits a `logger.warning` for each skipped corrupt manifest). D1 resolved in Story 2.10 (`check_coverage` opportunistically rebuilds when the index is missing / unparseable / schema-incompatible, and also when it parses cleanly as empty while `areas/` contains valid entries — exactly the interrupted-write window).

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | KeyboardInterrupt between manifest commit and `rebuild_index` leaves stale `index.json` | Story 2.10 (`check_coverage` opportunistic rebuild) | ✅ Resolved in Story 2.10 — `check_coverage` reads the index via `_read_indexed_entries`, which returns `None` on missing / unparseable / schema-incompatible / structurally-malformed payloads. `check_coverage` then calls `rebuild_index(cache_root)` and re-reads. Additionally, when the index parses cleanly as empty but `_areas_has_valid_entries(cache_root)` reports a valid `*/manifest.json` on disk (the literal Story 2.7 D1 scenario), the same rebuild-and-retry runs. Pinned by `tests/integration/test_cache_coverage.py::test_check_coverage_rebuilds_when_index_lists_zero_but_areas_has_entries` plus the parametrized malformed-payload tests in `tests/unit/test_check_coverage.py::test_check_coverage_malformed_index_triggers_rebuild`. [src/steeproute/cache.py:_read_indexed_entries + check_coverage] |
| 2 | `rebuild_index` swallows `CacheCorruptedError` silently — no log, no counter | Story 2.8 (CLI verbose wiring) | ✅ Resolved in Story 2.8 — `rebuild_index` now calls `logger.warning("cache.rebuild_index: skipping corrupt manifest at %s: %s", ...)` for each skipped entry; visible on stderr at WARNING level (always) and indirectly via `--verbose`. [src/steeproute/cache.py:432-438] |

## Deferred from: code review of 2-8-wire-steeproute-setup-end-to-end-with-force-refresh-semantics-on-real-fixture.md (2026-05-22)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `validate_setup_radius` not enforced inside `run_setup_stages` | Future (if a non-CLI caller surface appears) | A direct caller (test, future script) constructing `Area(radius_km > 50)` bypasses the CLI-side ceiling. Adding the guard to the orchestrator would break tests that construct synthetic `Area`s with deliberately specific (often large) radii. CLI-tier validation is the right home; revisit if and when a non-CLI consumer of `run_setup_stages` materializes. [src/steeproute/pipeline/__init__.py:89-141] |
| 2 | DEM `permission denied` not surfaced as `PreExecutionError` (rasterio leaks as exit 1) | Story 2.9 (`DataSourceUnavailableError` mapping) | ✅ Resolved in Story 2.9 — `sample_elevation` wraps `rasterio.open(dem_path)` in `try/except (rasterio.errors.RasterioIOError, OSError)` and re-raises as `DataSourceUnavailableError("DEM source unreachable.", detail=...)`. Covers permission-denied, corrupt-header, truncated-file, and network-filesystem hiccup cases. The existing `DEMCoverageError` paths inside the `with` block (CRS / bounds / nodata) are unchanged — "DEM opened but the data is wrong shape" stays categorically distinct from "DEM source unreachable" per Cat 10. [src/steeproute/pipeline/dem.py:72-92] |

## Deferred from: lightweight review of 2-6-implement-cache-key-hashing-manifest-schema-and-provenance-helpers.md (2026-05-20)

**Status:** D1 resolved in Story 2.8 (`git status --porcelain --untracked-files=no` — untracked-only working trees no longer flip `get_commit_short` to `-dirty`).

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `get_commit_short` treats untracked files as `-dirty` | Story 2.8 (CLI consumer of the commit string) | ✅ Resolved in Story 2.8 — `_get_commit_short_at` passes `--untracked-files=no` to `git status --porcelain` so only tracked-file modifications flip the `-dirty` suffix; matches `git describe --dirty` convention. [src/steeproute/provenance.py:48-54] |

## Deferred from: code review of 2-4-implement-pipeline-stages-6-7-elevation-smoothing-and-per-edge-metrics.md (2026-05-18)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `compute_edge_metrics` has no guard against `length_m == 0` or non-finite elevations | Story 2.5 (orchestrator-level stage-contract enforcement) | Stage 7 trusts stage 4 to drop degenerate edges, but `_resample_meters` only catches "all coords identical" — an out-and-back polyline `[(0,0), (1,1), (0,0)]` or a closed loop where `u == v` would pass stage 4 with effective `length_m ≈ 0` and stage 7 would raise a cryptic `ZeroDivisionError` with no edge context. Tightening `is_valid_for_metrics` (Story 2.4 patch P5) closes the hypothesis-test gap, but production runtime has no guard — by current "trust internal code, only validate at system boundaries" convention. The orchestrator that wires stages 1–7 is the right place to enforce inter-stage contracts: either raise a clean `PreExecutionError`-subclass naming the offending edge, or filter such edges before stage 7 sees them. [src/steeproute/pipeline/climbs.py:56, 271] |
| 2 | NaN elevation delta is silently absorbed by `_elevation_gain_loss`'s strict `>` / `<` branches | Story 2.5 (orchestrator) or Story 2.9 (DEM sanity) | ✅ Resolved-elsewhere by Story 2.5's `_assert_finite_elevations` guard in `pipeline/__init__.py::run_setup_stages` — post-stage-6 elevations are asserted finite before `compute_edge_metrics` is called, so a non-finite elevation surfaces as `PipelineContractError` naming the offending edge rather than silently absorbed. The "future caller wiring a different elevation source" case is a deliberate "trust internal code at module boundaries" convention call (Architecture §Data discipline); not in scope for Story 2.9's source-unavailable wrap (which sits at the DEM ingestion boundary, not the metrics-computation boundary). [src/steeproute/pipeline/__init__.py:140] |
| 3 | Self-loops and parallel edges never asserted in stage-7 synthetic tests | Story 2.5 (pipeline integration) or whichever story first ships a fixture with self-loops | `compute_edge_metrics` iterates `out.edges(data=True, keys=True)` so parallel-edge keys are visited correctly by construction, but a self-loop edge (`u == v`) where `vertices_resampled` is out-and-back coincident in 2D would tie back to deferred item #1 above. The committed Grenoble fixture has no self-loops; real OSM data (closed-loop trails) can. Add a synthetic self-loop test once the orchestrator decides on a policy (drop / accept / error). [tests/unit/test_climbs.py — whole file] |

## Deferred from: code review of 2-9-handle-source-unavailable-errors-and-emit-osm-age-warnings.md (2026-05-22)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | Cross-test osmnx-patch contamination if pytest-xdist is ever adopted | Future (test-infra) | `patch("steeproute.pipeline.osm.osmnx.graph_from_point", ...)` is process-global. Same concern as Story 2.1 finding 3 (`useful_tags_way` mutation). Mitigation lands when parallel test infra lands. [tests/e2e/test_source_unavailable.py:453-455] |
| 2 | `_invoke_with_wrapper` re-implements `run_entry_point`'s exit-code + stderr-write logic in the test layer — drift risk | Future (test-design) | Acknowledged in story Dev Notes #6. Subprocess-based smoke tests in `test_cli_smoke.py` cover the binary format. If `run_entry_point`'s formatting changes, smoke tests catch it; in-process tests would assert on the stale format and pass falsely. Documented tradeoff. [tests/e2e/test_source_unavailable.py:121-138] |
| 3 | `--osm-age-warn-days` accepts zero and negative values | Future (general CLI flag-validation pass) | Same gap as other `click.INT` flags on the project. A user passing `--osm-age-warn-days 0` gets warnings on every cache-hit; `-1` does the same. A general validator pattern analogous to `validate_setup_radius` should land across all numeric flags. Pre-existing scope, not Story 2.9-specific. [src/steeproute/cli/_shared.py:358-364] |
| 4 | Test rigor on `--verbose` detail-line assertions and warn-before-summary ordering | Future (incremental test rigor) | `"ConnectionError" in stderr` would pass even if the substring landed on the user_message line instead of the detail line. The "warning emitted before `_print_summary`" contract (AC #3) is not pinned by the e2e test — only that both substrings appear. Tighten as the CLI surface grows. [tests/e2e/test_source_unavailable.py:304,329] |
| 5 | DEM mid-read errors inside `with rasterio.open(...) as dataset:` block bypass the source-unavailable wrap | Future (GeoTIFF-integrity story, if ever) | The Story 2.9 wrap covers only `rasterio.open(dem_path)`. A truncated GeoTIFF whose header parses but whose data block fails on `dataset.sample(...)` leaks as `RasterioIOError → exit 1 + traceback`. **Rationale for deferral:** open-time wrap is sufficient for the realistic threat model (typo, permission denied, header corruption); mid-read corruption on a partially-truncated DEM is unlikely for the personal-tool case (DEMs are local cache, not network-streamed). Routed to a future GeoTIFF-integrity story if it ever materializes. [src/steeproute/pipeline/dem.py:80-176] |

## Deferred from: code review of 3-2-pipeline-stage-8-climb-detection.md (2026-05-25)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | Self-loop edges (`node_u == node_v`) uncovered | Future (when a fixture surfaces one) | Stage-4's `_drop_short_edges` doesn't explicitly forbid self-loops, only sub-1 mm edges. A self-loop with a real polyline (closed-loop trail) can survive. If it qualifies as seed, `head` lands on itself; `out_edges(head)` includes the seed (skipped via `in candidate`) plus any other out-edges. Behavior is well-defined but odd. Document expected behavior + add a test once a real fixture surfaces one (committed Le Sappey has none). [tests/unit/test_climb_detection.py — missing test] |
| 2 | Integration test has no positive assertion the OSM-load patch took effect | Future (test-architecture pass) | `patch("steeproute.pipeline.osm_load", ...)` silently no-ops if the symbol path changes; same fragile pattern as `test_pipeline_end_to_end.py`. A silent patch miss manifests as a network-dependent CI failure (live OSM fetch), not a wrong-result false-positive — visible enough that it isn't urgent. Defer to a broader test-architecture review across the integration suite. [tests/integration/test_climb_detection_fixture.py:526] |

## Deferred from: code review of 2-10-implement-query-side-fail-fast-on-unprepared-area.md (2026-05-25)

| # | Finding | Target | Detail |
|---|---------|--------|--------|
| 1 | `_format_number` emits scientific notation for very small (<1e-4) or very large (≥1e6) values | Future (UX polish) | `f"{1234567.89:g}"` → `'1.23457e+06'`; `f"{0.00001:g}"` → `'1e-05'`. Partial-coverage "smaller --radius (<= 1e-05)" suggestions read as absurd. Threshold against a minimum radius (e.g., 0.1 km) and fall through to center-hint below that. UX polish, not correctness. [src/steeproute/cache.py:_format_number] |
| 2 | `_partial_coverage_message` can suggest `--radius` equal to the entry's radius | Future (UX polish) | For query r=2.0001 against entry r=2.0 at same center, `r_new = min(2.0, 2.0) = 2.0`. Message says "smaller --radius (<= 2)" when the answer is "same --radius". Add an equal-radius branch with clearer wording. [src/steeproute/cache.py:_partial_coverage_message] |
| 3 | `_select_smallest_containing` is O(n) shapely polygon constructions per call with no caching | Future (perf) | Each call rebuilds every entry's polygon. For N=1 personal tool this is fine; flagged as a future-perf cliff if `index.json` ever grows large. [src/steeproute/cache.py:_select_smallest_containing] |
| 4 | `_find_nearest` ties on duplicate hashes are stability-dependent | Future (defensive) | Index parser doesn't deduplicate. If two `areas/` subdirs ever held the same `cache_key_hash`, `min` returns the first encountered. Not reachable via normal flow today. [src/steeproute/cache.py:_find_nearest] |
| 5 | Lifted helper's logger name change is observable to user logging configs filtering by name | By design (lift artifact) | Setup-side warnings previously logged to `steeproute.cli.setup`; now `steeproute.cli._shared`. Users filtering by old name miss the warnings. By design of the lift; documented in dev notes. [src/steeproute/cli/_shared.py] |
| 6 | `test_query_multi_containment_picks_smallest_radius` fragile against any non-entry directory under `areas/` | Future (test rigor) | Asserts `len(areas) == 2` after `iterdir()`. A leftover `.tmp/`/`.old/` from an interrupted prior write would break the test before the actual assertion. Filter the iterator by `not name.endswith('.tmp')` etc. [tests/e2e/test_coverage_check.py:246-253] |
| 7 | Test assertions on float→string formatting are trailing-zero-parity-dependent | Future (test rigor) | `f"{_CENTER_LAT}"` with `CENTER_LAT=45.260` produces `"45.26"` matching `_format_number(45.260)`. If the fixture changes precision, the assertion fails subtly. Pin to a literal expected string. [tests/e2e/test_coverage_check.py:221-222] |
| 8 | `_diagnostic_detail` produces unbounded output for large indices | Future (UX, when measured) | `detail=` of `_partial_coverage_message`'s `CacheNotFoundError` lists every prepared area. With N=1 personal tool this is fine. Truncate at e.g. 20 entries with a "... and N more" suffix when the index grows. [src/steeproute/cache.py:_diagnostic_detail] |
| 9 | Permission-denied during `rebuild_index` from `check_coverage` loops to uncaught `OSError` → exit 1 | Future (defensive) | Edge case (Linux `chmod 000` cache dir, Windows ACL lock). `run_entry_point` only catches `PreExecutionError`. FR24 contract assumes a usable cache dir; defer until a real user hits this. [src/steeproute/cache.py:check_coverage] |
| 10 | `cache.py` hardcodes CLI command names in error messages | By design (FR24 surface) | `_no_prepared_cache_message` embeds `"steeproute-setup --center ..."`. Cleanest split would require a presentation layer between cache and CLI, which is over-engineering for v1. `cache.py` is documented as the FR24 surface per architecture §Cat 4e. [src/steeproute/cache.py:_no_prepared_cache_message] |
| 11 | Unit tests use hardcoded cache_key_hashes that aren't real `compute_cache_key` outputs | Future (test rigor) | `"11"*8`, `"aa"*8`, etc. are stand-ins for placement in `areas/<hash>/`. A bug in `compute_cache_key` ↔ `check_coverage` flow would not be caught at the unit tier. The integration + e2e tiers exercise real cache keys, so the gap isn't crash-level. [tests/unit/test_check_coverage.py:_seed_entry, tests/integration/test_cache_coverage.py:_seed_entry] |
| 12 | `_areas_has_valid_entries` is misleadingly named (checks structural presence of `*/manifest.json`, not validity) | Future (rename) | Docstring explicitly admits "we don't validate the manifest contents here." Cleaner name: `_areas_has_manifest_files`. Bikesheddy; defer. [src/steeproute/cache.py:_areas_has_valid_entries] |
| 13 | OSM-age warning's "for this area" is vague when the user has multiple cache entries | Future (UX polish) | Story 2.10 D1 resolution: kept identical shared template across setup + query CLIs. Trade-off accepted: the message doesn't echo any cache-entry identifier (center/radius/hash), so a user with multiple prepared areas can't tell which one is stale from the warning alone. Add center/radius/hash echo in a future UX-polish story. [src/steeproute/cli/_shared.py:_OSM_AGE_WARNING_TEMPLATE] |
