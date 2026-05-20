# Story 2.5: Implement pipeline orchestrator and integration-test stages 1–7 end-to-end on real fixture

Status: done

## Story

As a developer,
I want `pipeline/__init__.py::run_setup_stages(area, config) -> MultiDiGraph` wiring stages 1–7, an integration test running the orchestrator on the real Grenoble fixture, and the orchestrator-level guards for the inter-stage contract gaps Stories 2.1, 2.2, and 2.4 deferred here,
so that `steeproute-setup` has a single entry point and the stages-1–7 pipeline is end-to-end-validated against real Alpine terrain before the CLI and cache stories land.

## Acceptance Criteria

1. `pipeline/__init__.py::run_setup_stages(area: Area, config: PipelineConfig) -> nx.MultiDiGraph` wires the stages in order:
   `osm_load → filter_trails → smooth_polylines → resample_edges → sample_elevation → median_smooth_elevation → compute_edge_metrics`,
   plus the orchestrator-level guards listed in AC #3. Pure: no I/O beyond the caller-provided `config.dem_path`, no global state, no `print`, no module-level mutable state (Architecture §Key anti-patterns). Returns a fresh `MultiDiGraph`; per-stage non-mutation contract is preserved (each stage already does `out = graph.copy(); ...; return out`).

2. `PipelineConfig` is a `@dataclass(frozen=True, slots=True)` added to `models.py` (sibling to `Area`, per Architecture §Cat 1 and §Type hints). Minimum fields for this story:
   - `untagged_policy: str` — comes from `--untagged-trails`; no default.
   - `dem_path: pathlib.Path` — comes from `--dem-path`; no default.

   Smoothing / resampling / elevation-median windows stay at their existing module-scope constants — no per-call overrides this story. `difficulty_cap` is **not** a `PipelineConfig` field: stages 1–7 are parameter-independent over it per Architecture §Cat 3b (cache key does not include it), so the orchestrator calls `filter_trails(graph, untagged_policy, "T6")` — the most permissive cap — and query-side filtering is owned by Epic 3. Document the rationale in the orchestrator docstring.

3. Orchestrator-level inter-stage contract guards — these resolve the items routed to Story 2.5 in `deferred-work.md`:

    a. **Non-empty graph after stage 2** (Story 2.1 D2): if `filter_trails` returns a graph with zero edges, raise `PipelineContractError` (new — added to `errors.py`, one-line `PreExecutionError` subclass like `DEMCoverageError`). User message names `area` and `untagged_policy` and suggests widening the area or switching the untagged policy. Cleaner than letting downstream stages divide by zero. *(Story 2.9 handles network-availability errors separately; this guard covers "fetch succeeded but contained no trails".)*

    b. **Orphan-node prune after stage 2** (Story 2.1 D5): after `filter_trails`, drop nodes whose degree fell to 0. `filter_trails` keeps them because it only iterates edges. The orchestrator owns this policy call so individual stages stay single-purpose.

    c. **Post-stage-4 degenerate-edge prune** (Story 2.2 D1 partial / Story 2.4 D1 / Story 2.4 D3): after `resample_edges`, drop edges whose `geometry` covers less than `_PIPELINE_LENGTH_FLOOR_M` (module-scope `float` constant in `pipeline/__init__.py`, e.g. `1e-3` — well below sub-cm geometric resolution; rationale inline). Catches the out-and-back / coincident-2D / self-loop cases stage 4's "all coords identical" check misses, so stage 7's `length_m == 0` divide-by-zero stays unreachable. Use the same local-equirectangular pattern as `pipeline/smoothing._resample_meters` for the length probe (no new geo helper).

    d. **Post-stage-6 finite-elevation guard** (Story 2.4 D2): after `median_smooth_elevation`, assert every `vertices_resampled` elevation is finite. A non-finite elevation → `PipelineContractError` naming the offending edge. Stage 5 already fail-fasts on non-finite samples from the DEM; this guard catches contract-breaking input from a future caller that bypasses stage 5 (the failure-mode rationale in `deferred-work.md`).

    e. **`n_intervals` upper bound** (Story 2.2 D1 remainder): not landed in this story — `--area-cap` bounds polyline length upstream and there is no CLI override of `spacing_m` yet. Re-routed to Story 2.8, which exposes the CLI surface where the guard makes sense. Note this in `Dev Notes`.

4. `tests/integration/test_pipeline_end_to_end.py` (new) runs `run_setup_stages` over the committed Grenoble fixture (OSM `.graphml` + `dem.tif`) once per module via a module-scoped fixture, and asserts:

    - **Topology baseline**: output `number_of_nodes()` and `number_of_edges()` are within `±10%` of inline-recorded baselines. Baselines come from running the orchestrator on the fixture during dev — record them with a comment naming the source.
    - **Full attribute contract**: every output edge carries `geometry`, `vertices_resampled`, `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`, `highway`, `osm_way_id` (same nine attributes as Story 2.4's `test_fixture_pipeline_full_contract_populated`).
    - **Sign + finiteness sweep**: every edge has `length_m > 0`, `d_plus_m ≥ 0`, `d_minus_m ≥ 0`, `avg_gradient ≥ 0`, and all four are finite.
    - **Aggregate `length_m` plausibility**: `sum(length_m)` is within `±10%` of an inline-recorded total — the epic's "roughly matches known real trail length within ±10%" criterion. Measure once during dev and commit as a constant with rationale; the assertion is a smoke check against axis-swap / unit / sign-flip bugs at the orchestrator scale (sub-edge bugs are caught at the unit layer).
    - **Orphan-node prune verified**: `min(deg for _, deg in graph.degree()) >= 1`. The fixture has edges so degree-0 nodes would only appear if AC #3b is broken.

5. `tests/integration/test_pipeline_end_to_end.py` also covers AC #3's contract guards against crafted inputs — orchestrator-only tests where stages 1–4 (or 1–5) are run normally and the guard is exercised via a small monkeypatch or a directly-callable guard helper:

    - **Self-loop / coincident-2D edge drop** (AC #3c): craft a single-edge `MultiDiGraph` whose `geometry` is an out-and-back LineString (e.g. `[(0,0), (1,1), (0,0)]`) and run only the post-stage-4 guard helper. Assert the edge is dropped — no `ZeroDivisionError` reaches stage 7.
    - **Non-finite elevation → `PipelineContractError`** (AC #3d): craft a graph carrying a `vertices_resampled` with a `NaN` elevation post-stage-6 and run only the post-stage-6 guard helper. Assert `PipelineContractError` is raised and the offending edge `(u, v, k)` appears in `user_message`.
    - **Empty graph after filter → `PipelineContractError`** (AC #3a): craft a graph where `filter_trails` would return zero edges (e.g. all edges have `highway` outside `TRAIL_HIGHWAY_TAGS`); run only that guard. Assert `PipelineContractError` is raised and the message names `area` + `untagged_policy`.

   To make these synthetic tests clean, extract each guard as a small private helper in `pipeline/__init__.py` (e.g. `_drop_orphan_nodes`, `_drop_short_edges`, `_assert_finite_elevations`, `_assert_non_empty`). The orchestrator composes them with the stage functions; each helper is unit-testable in isolation. This keeps `run_setup_stages` itself a thin, readable wiring function.

6. The existing `fixture_pipeline_through_stage7` in `tests/unit/test_climbs.py` is **kept as-is**. It predates the orchestrator and its three assertions (`test_fixture_pipeline_*`) are independent of the orchestrator-level guards. Don't refactor it to call `run_setup_stages` — that's a sweep-of-everything that doesn't belong in this story.

7. All four CI gates pass on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov`. No new runtime or dev deps. `pipeline/__init__.py` clears the 95% pure-logic coverage floor (Architecture §Cat 11e). Live OSM test re-verified.

## Tasks / Subtasks

- [x] **Task 1: Add `PipelineConfig` to `models.py`** (AC: #2)
   - [x] Frozen-slots dataclass; two fields per AC #2 (`untagged_policy: str`, `dem_path: pathlib.Path`). Module docstring stays one-line per Architecture §Documentation discipline; `PipelineConfig` docstring documents the `difficulty_cap` omission rationale (§Cat 3b parameter-independence).
- [x] **Task 2: Add `PipelineContractError` to `errors.py`** (AC: #3)
   - [x] One-line subclass of `PreExecutionError`, mirroring `DEMCoverageError`. No extra fields.
- [x] **Task 3: Implement `pipeline/__init__.py::run_setup_stages` + guard helpers** (AC: #1, #2, #3)
   - [x] Module docstring expanded from the previous one-liner to the orchestrator-role description (pipeline-wide map, T6-most-permissive rationale, AC #3 guards listed). basedpyright per-file pragma matches the other `pipeline/` modules.
   - [x] `_PIPELINE_LENGTH_FLOOR_M = 1e-3` module-scope constant + `_SETUP_DIFFICULTY_CAP = "T6"` + `_EARTH_RADIUS_M = 6_378_137.0` (rationale inline cross-references the sibling pipeline modules' duplicates).
   - [x] Four private helpers per AC #5 (`_assert_non_empty`, `_drop_orphan_nodes`, `_drop_short_edges`, `_assert_finite_elevations`) plus a private `_polyline_length_m` for the short-edge probe. `_drop_short_edges` re-invokes `_drop_orphan_nodes` after the prune so the post-guard graph keeps its "every node has degree ≥ 1" invariant.
   - [x] `run_setup_stages(area, config)` composes the seven stages + the four guards in the exact order: `osm_load → filter_trails → _assert_non_empty → _drop_orphan_nodes → smooth_polylines → resample_edges → _drop_short_edges → sample_elevation → median_smooth_elevation → _assert_finite_elevations → compute_edge_metrics`. Public docstring with `Args:`/`Returns:`/`Raises:`.
- [x] **Task 4: Integration test against real fixture** (AC: #4)
   - [x] `tests/integration/test_pipeline_end_to_end.py` (new). Module-scoped `prepared_graph` fixture invokes `run_setup_stages` once with `pipeline.osm_load` patched (via `unittest.mock.patch` since pytest's `monkeypatch` is function-scoped) to read the committed `osm_graph.graphml` instead of hitting Overpass. Skip-on-missing-`dem.tif` mirrors prior fixture-pipeline tests.
   - [x] Inline baselines recorded by running the orchestrator on the committed fixtures during dev: `_BASELINE_NODES = 468`, `_BASELINE_EDGES = 1208`, `_BASELINE_TOTAL_LENGTH_M = 167_132.0`, `_DRIFT_TOLERANCE = 0.10`. Five assertions per AC #4: topology, full-attribute-contract, sign+finiteness, aggregate-length plausibility, no-orphan-nodes (`min(degree) ≥ 1`).
- [x] **Task 5: Orchestrator-guard unit-style tests in the same file** (AC: #5)
   - [x] Six guard tests (3 raise cases + 3 negative-case sanity checks): out-and-back self-loop drop + normal-edge keep for `_drop_short_edges`; NaN-elevation raise + all-finite-pass for `_assert_finite_elevations`; zero-edges raise + non-empty pass for `_assert_non_empty`. Both error-path tests assert the offending edge tuple (`(0, 1, 0)`) or area+policy appear in `user_message`. `_drop_orphan_nodes` is covered indirectly by AC #4's `min(degree) ≥ 1` assertion on the fixture.
- [x] **Task 6: Verify CI** (AC: #7)
   - [x] All four gates green: `uv run ruff check` (All checks passed!), `uv run ruff format --check` (38 files already formatted), `uv run basedpyright` (0 errors, 0 warnings, 0 notes), `uv run pytest --cov` (275 passed, 1 deselected — +11 from prior 264 — at 96% overall coverage).
   - [x] `pipeline/__init__.py` at 98% (61 stmts, 1 missed — the `len(coords) < 2 → 0.0` defensive guard in `_polyline_length_m`; unreachable because `shapely.LineString` construction enforces ≥ 2 coords. Same accepted-defensive pattern as Story 2.4's `is_valid_for_metrics`).
   - [x] `STEEPROUTE_USE_OS_TRUSTSTORE=1 UV_NATIVE_TLS=1 uv run pytest -m live` → 1 passed (no regression).

### Review Findings

_From `bmad-code-review` 2026-05-20. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Acceptance Auditor returned 0 findings — all 7 ACs satisfied. Blind Hunter raised 27, Edge Case Hunter raised 12. After dedupe + triage: 3 patches, 1 defer, 35 dismissed._

**Patches (unambiguous fixes):**

- [x] [Review][Patch] **P1 (MED): No non-empty re-assertion after `_drop_short_edges`** — Stage-3 (`smooth_polylines`) and stage-4 (`resample_edges`) already drop degenerate edges, and `_drop_short_edges` adds more pruning. A pathological fixture (e.g. all edges below the 1 mm floor — unrealistic but contract-relevant) would leave a zero-edge graph that then hits `sample_elevation` and downstream stages with no actionable error. Added a second `_assert_non_empty(graph, area, config.untagged_policy)` call after `_drop_short_edges` (reuses the same helper; existing error message accurately describes "Pipeline produced zero edges" at any stage). [src/steeproute/pipeline/__init__.py:122-126] [Source: blind+edge]

- [x] [Review][Patch] **P2 (LOW-MED): `config.dem_path` not fail-fasted at orchestrator entry** — The orchestrator runs `osm_load` (network), `filter_trails`, `smooth_polylines`, `resample_edges`, plus orphan + short-edge prunes (~seconds to minutes for a real area) before `sample_elevation` discovers the DEM file is missing/unreadable. Added `if not config.dem_path.is_file(): raise BadCLIArgError(...)` at the top of `run_setup_stages` with an actionable detail line. New test `test_run_setup_stages_fails_fast_on_missing_dem_path` (uses `tmp_path` for a non-existent path) confirms the guard fires before stage 1. [src/steeproute/pipeline/__init__.py:107-115] [Source: blind+edge]

- [x] [Review][Patch] **P3 (LOW): Integration-test fixture skip-check covers only `dem.tif`** — `_load_fixture_constants()` runs at module-import time and would fail loudly if `regenerate.py` is missing, but the `prepared_graph` fixture only checked `_DEM_FIXTURE_PATH.exists()`. If `osm_graph.graphml` were missing while `dem.tif` were committed, `osmnx.load_graphml` would raise an unhandled error inside the fixture. Extended the skip-check: `if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists(): pytest.skip(...)`. [tests/integration/test_pipeline_end_to_end.py:89-92] [Source: edge]

**Deferred (real but owned elsewhere):**

- [x] [Review][Defer] **D1 (LOW): `_drop_short_edges` / `_drop_orphan_nodes` mutate topology with no debug log** — Silently dropping edges or nodes is invisible until downstream behavior surprises. A `logger.debug("dropped %d short edges, %d orphan nodes", ...)` call would surface real OSM-fixture regressions. The right time to add this is when Story 2.8 wires the `--verbose` plumbing — `logging` configuration needs a sink first; adding logger calls now means they fire into the default `WARNING` root logger config and are invisible anyway. Routed to Story 2.8 alongside the rest of the CLI verbose wiring. [src/steeproute/pipeline/__init__.py:147-183] [Source: blind]

**Dismissed (noise / false positive / handled elsewhere):**

- [x] [Review][Dismiss] `_polyline_length_m` swaps lon/lat — false positive. Coords are built `(float(c[0]), float(c[1]))` from `shapely.LineString.coords` which yields `(lon, lat)`; the function correctly reads index 0 as lon and index 1 as lat. The Blind Hunter itself admitted "the naming is consistent here". The test fixture `(5.0, 45.0)` is `(lon, lat)` per shapely convention. [blind]
- [x] [Review][Dismiss] `_drop_short_edges` uses stale `data["geometry"]` after resampling — false positive. `resample_edges` (stage 4) explicitly writes a fresh `shapely.LineString` to `data["geometry"]`; `_drop_short_edges` runs immediately after stage 4 and reads the resampled geometry. `vertices_resampled` doesn't exist until stage 5. [blind]
- [x] [Review][Dismiss] `_assert_finite_elevations` ignores non-finite lat/lon — handled upstream. Stage-4's `is_valid_polyline` checks `math.isfinite(x) and math.isfinite(y)` before any polyline is admitted past stage 3; non-finite lat/lon cannot reach stage 6. The guard's documented purpose (Story 2.4 D2) was specifically "elevation finite check post-stage-6". [blind+edge]
- [x] [Review][Dismiss] `_drop_orphan_nodes` and `_drop_short_edges` call `graph.copy()` unconditionally (perf) — speculative perf. Each pipeline stage already does the same per Architecture §Cat 3a function-signature convention. Premature optimization until benchmarks surface it; same precedent as Story 2.1 D6 (deferred-work.md). [blind]
- [x] [Review][Dismiss] `PipelineConfig.untagged_policy: str` has no runtime validation — handled by stage 2. `filter_trails` raises `BadCLIArgError` on `untagged_policy not in {"include", "exclude"}` at the same effective fail-fast altitude. Re-validating at the orchestrator boundary is duplicate work for the same outcome. [blind+edge]
- [x] [Review][Dismiss] `_assert_non_empty` checks only after stage 2, not after `_drop_orphan_nodes` — covered by P1 patch (which adds the post-stage-4 + post-short-edge re-assertion); the orphan-prune branch is structurally covered by the topology test's `min_degree ≥ 1` assertion. [blind]
- [x] [Review][Dismiss] Earth-radius constant uses equatorial (6 378 137 m) instead of mean radius (6 371 008 m) — matches sibling-module convention. `pipeline/smoothing.py` and `pipeline/climbs.py` both use the equatorial constant; the ~0.3% bias is below all relevant test tolerances and changing this module alone would create inconsistency. [blind]
- [x] [Review][Dismiss] `_PIPELINE_LENGTH_FLOOR_M = 1e-3` is a magic number with no rationale — factually false. The constant has a 9-line inline rationale (`pipeline/__init__.py:62-72`) explaining the 1 mm choice (6 OOM below resample spacing, float-underflow unreachable, catches Story 2.4 D3 self-loops). [blind]
- [x] [Review][Dismiss] No contract check on stage-7 output — covered by integration test (`test_run_setup_stages_full_attribute_contract` + `test_run_setup_stages_sign_and_finiteness_invariants`); the orchestrator's return type IS the stage-7 contract. Runtime guards on each pipeline output are not the project convention per Architecture §key anti-patterns. [blind]
- [x] [Review][Dismiss] `# pyright: reportUnknown* = false` blanket-disabled — project convention per Architecture §Type hints + Story 2.1 Review D2 (per-file pragmas accepted at external boundaries). Same pragma set as `pipeline/osm.py`, `smoothing.py`, `dem.py`, `climbs.py`. [blind]
- [x] [Review][Dismiss] Helper functions have no type annotations — factually false. All five helpers are fully annotated (`_assert_non_empty(graph: nx.MultiDiGraph, area: Area, untagged_policy: str) -> None`, etc.); the Blind Hunter saw an elided diff summary that stripped types for brevity. [blind]
- [x] [Review][Dismiss] `unittest.mock.patch` in a module-scoped fixture is fragile — works as written. The `with patch(...): return run_setup_stages(...)` block executes `run_setup_stages` fully (calling the patched `osm_load`) before returning; by the time the `with` tears down, the graph is built and cached. 11/11 tests pass empirically. [blind]
- [x] [Review][Dismiss] ±10% tolerance on topology baselines is too lax — matches spec AC #4 verbatim ("topology baseline ±10%") and the live-OSM drift band from Story 2.1. Tightening would create CI flakes on routine fixture regeneration. [blind]
- [x] [Review][Dismiss] `_BASELINE_TOTAL_LENGTH_M = 167_132.0` provenance undocumented — inline comment documents it (`~167 km of trail across the 16 km² bbox`) + the file's top docstring + Completion Note #7 explain the dev-time measurement provenance. [blind]
- [x] [Review][Dismiss] Tests import private helpers — explicit project decision per Completion Note #6. Story 2.4 promoted `is_valid_for_metrics` to public because it was a standalone predicate; the four orchestrator guards are pipeline-stage-shaped (Architecture §Boundaries: "outside code calls orchestrator functions… never individual stages"). Test-side `reportPrivateUsage=false` pragma matches the architecture intent. [blind]
- [x] [Review][Dismiss] Self-loop test `1e-9` degrees suspiciously close to threshold — false alarm. 1e-9° ≈ 0.11 mm at the equator; total out-and-back distance ≈ 0.22 mm; floor is 1 mm. 5× safety margin. True-haversine swap would change the result by <0.1% over 0.22 mm. [blind]
- [x] [Review][Dismiss] `PipelineConfig` docstring hard-codes the architecture decision — speculative future refactor. If smoothing constants ever become tunable, that change updates the docstring as part of the refactor. Standard maintenance discipline; no current drift. [blind]
- [x] [Review][Dismiss] `PipelineConfig` is `frozen=True` but `pathlib.Path` is mutable filesystem state — false positive. Same applies to every config that references external files (cache paths, fixtures, output dirs). `frozen=True` is about the dataclass instance, not the universe. [blind]
- [x] [Review][Dismiss] `_SETUP_DIFFICULTY_CAP: str = "T6"` magic string with no validation — covered by stage 2. `filter_trails` calls `parse_difficulty_cap` which raises `BadCLIArgError` on unknown caps; if the SAC enum changes, that validation fires immediately on every orchestrator call. [blind]
- [x] [Review][Dismiss] Module top docstring says "stages 1-9" but only 1-7 implemented — accurate as written. The module is the orchestrator home for stages 1-9 (Architecture §Project tree); stage 8 + 9 wire in Epic 3. The docstring explicitly notes "Stage 8 (climb detection) and stage 9 (climb-graph contraction) wire on the query side in Epic 3; their orchestrator entry point is not in this story." [blind]
- [x] [Review][Dismiss] `PipelineContractError` extends `PreExecutionError` — taxonomy concern — taxonomy correct. `PreExecutionError` maps to exit code 2 ("tool cannot produce any output"); an inter-stage contract violation prevents the tool from producing output, so the tier is correct. Same shape as `DEMCoverageError` (the precedent). [blind]
- [x] [Review][Dismiss] Error message uses `radius_km:g` formatting — speculative. `:g` for `1.5` produces `"1.5"`, not scientific notation; scientific notation only kicks in for `< 1e-4` or `> 1e+16`, and `--area-cap` bounds the radius into the well-behaved range. [blind]
- [x] [Review][Dismiss] `_assert_finite_elevations` not vectorized via numpy — speculative perf. ~20-60k `math.isfinite` calls per orchestrator run runs in milliseconds; the function is called once per pipeline. Premature optimization. [blind]
- [x] [Review][Dismiss] No orchestrator happy-path test on a tiny in-memory graph — covered by the fixture-driven integration tests (with `osm_load` patched, the orchestrator runs end-to-end on real Alpine data). Adding a synthetic-DEM happy-path test would require fabricating a working in-memory GeoTIFF — out of scope for the orchestrator and already exercised by `tests/unit/test_dem.py`'s in-memory rasters. [blind]
- [x] [Review][Dismiss] Orphan nodes after `smooth_polylines` / `resample_edges` not pruned — covered. `_drop_short_edges` (which runs immediately after stage 4) calls `_drop_orphan_nodes` as its final step, so any orphan created by stages 3-4 OR by the short-edge prune is cleaned up in a single pass. `test_run_setup_stages_no_orphan_nodes` validates the invariant on the fixture. [edge]
- [x] [Review][Dismiss] Edge lacks 'geometry' key or geometry not a LineString — handled upstream. `smooth_polylines._extract_coords` raises `TypeError` on non-LineString geometry, fail-fast at stage 3 before `_drop_short_edges` runs. [edge]
- [x] [Review][Dismiss] Edge lacks 'vertices_resampled' key — handled upstream. `sample_elevation` (stage 5) writes `vertices_resampled` on every edge it touches; `_assert_finite_elevations` runs after stage 6 which reads-and-rewrites the same attribute. The KeyError path is structurally unreachable. [edge]
- [x] [Review][Dismiss] NaN lat/lon in coords poisons `_polyline_length_m` to NaN, comparison `NaN < 1e-3` is False, edge survives — same as the lat/lon-finite dismiss above. Stage-4 `is_valid_polyline` filters non-finite coords before `_drop_short_edges` sees them. [edge]
- [x] [Review][Dismiss] `config.untagged_policy` None or non-string — handled by `filter_trails`'s set-membership check (a non-string raises TypeError; None raises BadCLIArgError). Dataclass type hint is documentary; runtime validation belongs at the stage boundary, not the dataclass. [edge]
- [x] [Review][Dismiss] `regenerate.py` absent at collection time — speculative. The file is committed as part of the fixture infrastructure (Story 2.1 / 2.3) and removing it would also break `test_osm_live.py`. Out of orchestrator scope. [edge]
- [x] [Review][Dismiss] Module-scoped mock-patch teardown — same as the fragile-patch dismiss above. Tests pass; the pattern works because `with patch(...): return ...` completes the patched call inside the context. [blind]
- [x] [Review][Dismiss] Test asserts via `pytest.raises(... match=r"\(0, 1, 0\)")` rather than direct `user_message` substring — pytest.raises matches against `str(exc)` which equals `user_message` for `PreExecutionError` subclasses (because `super().__init__(user_message)`). Equivalent to a substring assertion. [audit-borderline]
- [x] [Review][Dismiss] AC #3c parenthetical "(no new geo helper)" tensions with `_polyline_length_m` — borderline reading. The spec's intent (Dev Notes line 80, 170) explicitly accepts module-self-contained duplication of the local-equirectangular pattern; the helper is private to `pipeline/__init__.py` and follows the precedent of `pipeline/climbs.py::_cumulative_2d_distance_m`. [audit-borderline]
- [x] [Review][Dismiss] `_assert_non_empty` message embeds `area` via `center=(...)` + `radius_km=...` rather than the literal string "area" — semantically equivalent. The center + radius IS the area; test asserts both `(45.0, 5.0)` and `include` appear in the message. [audit-borderline]
- [x] [Review][Dismiss] `_drop_short_edges` and `_drop_orphan_nodes` mutate topology with no count returned — same concern as silent edge dropping (deferred to D1 for the logging side); the count-return shape would change the helper API for purely diagnostic value. [blind]

## Dev Notes

- **Why the orchestrator is the right place for these guards.** Each individual stage stays "pure transform under a stated input contract". The orchestrator is the only module that knows the full inter-stage pipeline shape, so it's the natural place to enforce inter-stage contracts. This keeps stage modules small and re-testable in isolation; future callers wiring stages differently can compose their own contract layer.
- **Why orphan-node prune lives here, not in stage 2.** `filter_trails` is a pure edge filter — adding node-side bookkeeping would couple it to a policy call (what counts as orphan? all-time? after-this-pass?) that varies per consumer. The orchestrator decides "for the stages-1–7 setup pipeline, an orphan is an orphan; drop it" without bleeding policy into stage 2. Mirrors Story 2.1's Review-D5 routing.
- **Why `_PIPELINE_LENGTH_FLOOR_M = 1e-3` (or thereabouts).** Stage 4's `_resample_meters` only rejects edges where all coords are bit-identical. An out-and-back polyline like `[(0,0), (1e-12, 1e-12), (0,0)]` resamples to ~bit-zero length but passes the bit-identical check. `1e-3 m` (1 mm) is six orders of magnitude below the 10 m resample spacing — no legitimate trail edge has length below it, and the float-underflow regime where `length_m → 0` and `avg_gradient → ∞` becomes unreachable. Module-scope per Architecture §Numerical and data discipline.
- **Why we pass `difficulty_cap="T6"` to `filter_trails` from the orchestrator.** Stages 1–7 are cached parameter-independent over `difficulty_cap` (Architecture §Cat 3b; the field is not in the cache key per §Cat 4b). The cached graph must contain everything within SAC bounds so query-side can sub-filter at any cap ≤ T6 without re-running stages 1–7. T6 = `difficult_alpine_hiking` is the maximum recognized rank in `pipeline/osm.SAC_SCALE_RANK`, so passing it as the cap keeps every recognized-SAC trail edge.
- **What we are not doing this story:**
   - **Cache write / read / index** — Story 2.7. `run_setup_stages` returns a `MultiDiGraph`; nothing here writes to disk.
   - **CLI wiring (`steeproute-setup --center ...`)** — Story 2.8. `cli/setup.py::cli` keeps its stub.
   - **Source-unavailable errors (Overpass / IGN down) + OSM-age warning** — Story 2.9.
   - **`n_intervals` upper bound** — Story 2.8 (right after CLI exposes `--spacing-m`, if it does).
   - **Refactoring `tests/unit/test_climbs.py::fixture_pipeline_through_stage7` to call `run_setup_stages`** — explicitly out of scope per AC #6.
   - **Provenance helpers / `cache.py` skeleton** — Story 2.6.
- **Out-and-back self-loop test input**: build the `MultiDiGraph` directly (no osmnx round-trip needed for the synthetic test). Stage 4 is the right entry point to feed it through — its `geometry` survives the moving-average smoother of stage 3 because endpoints are pinned. The new guard helper at AC #3c then catches it.
- **Carry-forwards from prior story reviews**: Stories 2.1 D2 + D5, 2.2 D1 (partially — `n_intervals` deferred again), 2.4 D1 + D2 + D3 are all resolved here. Story 2.3's deferreds (D1 `0.0`-as-void, D2 inverted-bounds GeoTIFF) target Stories 2.9 and 2.8 respectively — out of scope here.

### Project Structure Notes

- **Extended**: `src/steeproute/models.py` — add `PipelineConfig` dataclass.
- **Extended**: `src/steeproute/errors.py` — add `PipelineContractError`.
- **Extended**: `src/steeproute/pipeline/__init__.py` — currently a one-line docstring; gains the orchestrator + four guard helpers + module constant. Matches the project-tree assignment in Architecture §Project structure ("orchestrator: wires stages 1–7 (setup) and 8–9 (query)").
- **New**: `tests/integration/test_pipeline_end_to_end.py` — already named in Architecture §Project tree as the integration-tier home for end-to-end pipeline tests.
- **Untouched**: `src/steeproute/cli/setup.py` (CLI wiring → Story 2.8); `src/steeproute/cache.py` (cache I/O → Stories 2.6–2.7); `tests/unit/test_climbs.py::fixture_pipeline_through_stage7` (explicitly out of scope, AC #6).

### Testing standards summary

- Layer: orchestrator + guard tests live in `tests/integration/` (Architecture §Cat 11e — pipeline orchestration spans stages). Single new file: `test_pipeline_end_to_end.py`.
- Real-data primary, crafted-input where a guard is unreachable via the real fixture. The committed Grenoble fixture has no self-loops, no DEM voids, and no empty-after-filter edge cases, so AC #5 covers those branches against synthetic inputs.
- Coverage floor: 95% on `pipeline/__init__.py` per Architecture §Cat 11e (pure-logic module).
- Naming: `test_<unit>_<scenario>` per Architecture §Test organization. E.g. `test_run_setup_stages_full_attribute_contract_on_fixture`, `test_drop_short_edges_removes_out_and_back_self_loop`.
- Conventions inherited from earlier stories: absolute imports, PEP 604 unions, no `Any`, basedpyright per-file pragma if external surfaces leak `Unknown`, ruff-formatted.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 2.5"]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 3 — Data pipeline architecture] — orchestrator role; `(area, config) -> MultiDiGraph` shape; §3b parameter-independence of stages 1–7 over `difficulty_cap`
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 4b — Cache key composition] — `difficulty_cap` is **not** in the cache key (the rationale for T6-most-permissive in AC #2)
- [Source: _bmad-output/planning-artifacts/architecture.md §Boundaries — Pipeline boundary] — "outside code calls orchestrator functions in `pipeline/__init__.py`, never individual stages"
- [Source: _bmad-output/planning-artifacts/architecture.md §Internal data flow — Setup CLI] — stage chain to wire (`osm_load → filter_trails → smooth_polylines → resample_edges → sample_elevation → median_smooth_elevation → compute_edge_metrics`)
- [Source: _bmad-output/planning-artifacts/architecture.md §Implementation Patterns — Numerical and data discipline] — module-scope constants for `_PIPELINE_LENGTH_FLOOR_M`
- [Source: _bmad-output/implementation-artifacts/deferred-work.md §"code review of 2-1..."] — items D2 (empty graph) + D5 (orphan nodes) target Story 2.5
- [Source: _bmad-output/implementation-artifacts/deferred-work.md §"code review of 2-2..."] — item D1 (`n_intervals` upper bound) re-routed to Story 2.8
- [Source: _bmad-output/implementation-artifacts/deferred-work.md §"code review of 2-4..."] — items D1 (length_m == 0 / non-finite elev guard), D2 (NaN-delta), D3 (self-loop test coverage) target Story 2.5
- [Source: src/steeproute/pipeline/osm.py] — `osm_load`, `filter_trails`, `SAC_SCALE_RANK`, `TRAIL_HIGHWAY_TAGS`
- [Source: src/steeproute/pipeline/smoothing.py:246-290] — `_resample_meters` cumulative-2D-distance pattern to reuse for the length-floor probe
- [Source: src/steeproute/pipeline/dem.py:45-71] — `sample_elevation` signature + raises shape
- [Source: src/steeproute/pipeline/climbs.py:36-61] — `compute_edge_metrics` (the stage the orchestrator-level length-floor guard protects from `ZeroDivisionError`)
- [Source: src/steeproute/errors.py] — `PreExecutionError` base + `DEMCoverageError` precedent for `PipelineContractError`
- [Source: tests/unit/test_dem.py:373-387 + tests/unit/test_climbs.py:190-210] — module-scoped fixture pattern (skip-on-missing-`dem.tif`) to reuse in the new integration test
- [Source: tests/integration/test_osm_live.py] — `@pytest.mark.live` precedent (no new live test needed this story; existing one re-verifies as the CI gate)
- [Source: tests/fixtures/grenoble_small/README.md] — fixture parameters + size guarantees

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/agitated-mahavira-c17607`).

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` for the corporate Netskope TLS-intercepting proxy.

**No new deps.** Orchestrator is pure glue + a dataclass + an error subclass + an in-file local-equirectangular length probe; no runtime or dev imports beyond what stages 1-7 already pull in.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 38 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 275 passed, 1 deselected in ~25s; coverage 96% overall
                                     - pipeline/__init__.py 98% (61 stmts, 1 missed — the
                                       len(coords) < 2 → 0.0 defensive guard in
                                       _polyline_length_m, unreachable because
                                       shapely.LineString construction enforces ≥ 2 coords)
                                     - pipeline/climbs.py    95% (unchanged from Story 2.4)
                                     - pipeline/dem.py      100% (unchanged from Story 2.3)
                                     - pipeline/smoothing.py 99% (unchanged from Story 2.4)
                                     - models.py            100% (PipelineConfig + Area)
                                     - errors.py            100% (PipelineContractError added)
```

Live OSM test re-verified: `STEEPROUTE_USE_OS_TRUSTSTORE=1 UV_NATIVE_TLS=1 uv run pytest -m live` → 1 passed.

### Completion Notes List

**Design decisions worth review attention:**

1. **`run_setup_stages` patches `pipeline.osm_load` in tests rather than re-shaping the orchestrator API.** The orchestrator's contract is `(area, config) -> MultiDiGraph` per Architecture §Cat 3 — adding a graph-loader injection would over-shape it for testability. The committed fixture is loaded by `osmnx.load_graphml` + `normalize_edges` in the integration-test fixture and `unittest.mock.patch` (used as a context manager, since pytest's `monkeypatch` is function-scoped) swaps out `osm_load` for the duration of the module-scoped `prepared_graph` fixture. Stages 2-7 + the four guards run their real production code on real Alpine terrain.

2. **`_drop_short_edges` re-invokes `_drop_orphan_nodes` after the prune.** A short-edge drop can newly orphan nodes (the only incident edge was the dropped one), so the post-guard graph would otherwise violate the "every node has degree ≥ 1" invariant the AC #4 fixture test asserts. Calling the existing orphan-prune helper is one line and keeps the invariant intact across the full pipeline.

3. **`_PIPELINE_LENGTH_FLOOR_M = 1e-3` (1 mm).** Six orders of magnitude below the 10 m resample spacing — no legitimate trail edge sits below it, and the float-underflow regime where `length_m → 0` and `avg_gradient → ∞` becomes unreachable downstream in stage 7. The synthetic `[(5,45), (5+1e-9, 45+1e-9), (5,45)]` out-and-back test (≈ 0.1 mm at this latitude) confirms the floor catches the Story 2.4-D3 case.

4. **`_EARTH_RADIUS_M` duplicated again, not imported.** Same precedent as `pipeline/climbs.py::_EARTH_RADIUS_M` and `pipeline/smoothing.py::_EARTH_RADIUS_M`: each pipeline module is self-contained against a physical constant. The cross-reference comments make the relationship discoverable without adding an import-graph dependency for a six-digit float.

5. **`difficulty_cap` is intentionally absent from `PipelineConfig`.** Per Architecture §Cat 3b stages 1-7 are cached parameter-independent over it; the orchestrator pins it to `"T6"` internally via `_SETUP_DIFFICULTY_CAP` so the cached graph contains every trail edge within SAC bounds. Query-side filtering (Epic 3) re-applies the user's chosen cap on cache-hit. Adding `difficulty_cap` to `PipelineConfig` would have invited a cache-key shape mistake that §Cat 4b explicitly rules out.

6. **Test-side `reportPrivateUsage=false` pragma, not promoting helpers to public.** Story 2.4 promoted `is_valid_for_metrics` to public because it was a standalone predicate shared between production and a hypothesis strategy. The four orchestrator guard helpers here are pipeline-stage-shaped — Architecture §Boundaries says "outside code calls orchestrator functions in `pipeline/__init__.py`, never individual stages". Keeping them underscore-prefixed (private API) and relaxing the warning in the test file matches the architecture's intent.

7. **Fixture baselines pinned at `_DRIFT_TOLERANCE = 0.10`.** Same band the live OSM test uses for fixture drift. The Le Sappey 2 km bbox is dense terrain (468 nodes, 1208 edges, ~167 km of trail) and currently none of the orchestrator guards activate — the fixture has no self-loops, no NaN elevations, no edges that filter_trails drops at T6 — so the orchestrator output matches the input shape exactly. The ±10% band absorbs routine fixture regeneration drift.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `run_setup_stages` in `src/steeproute/pipeline/__init__.py` wires the seven stages with the four guards in the exact order in the module docstring. Pure: no `print`, no global state, no I/O beyond `config.dem_path` (`sample_elevation`); each stage already does `out = graph.copy(); ...; return out`. Output exercised by AC #4 tests. ✅
2. AC #2 — `PipelineConfig` in `src/steeproute/models.py:28-46` with the two required fields (`untagged_policy: str`, `dem_path: pathlib.Path`) and `@dataclass(frozen=True, slots=True)`. Class docstring documents the `difficulty_cap` omission. Orchestrator pins `_SETUP_DIFFICULTY_CAP = "T6"`. ✅
3. AC #3 — `PipelineContractError` in `src/steeproute/errors.py:40-41`. All four guards present in `pipeline/__init__.py`: `_assert_non_empty` (a), `_drop_orphan_nodes` (b), `_drop_short_edges` (c), `_assert_finite_elevations` (d). `n_intervals` upper bound (e) explicitly not landed — Dev Notes re-defer to Story 2.8. ✅
4. AC #4 — Five fixture tests in `tests/integration/test_pipeline_end_to_end.py` covering topology baseline, full attribute contract, sign + finiteness, aggregate length plausibility, no-orphan-nodes. ✅
5. AC #5 — Six guard tests (3 raise + 3 sanity) in the same file. The `(0, 1, 0)` and area/policy strings are asserted in error-path messages. ✅
6. AC #6 — `tests/unit/test_climbs.py::fixture_pipeline_through_stage7` is untouched (verified via `git diff`). ✅
7. AC #7 — All four CI gates green; coverage floors held; live OSM re-verified. ✅

### File List

**New:**
- `src/steeproute/pipeline/__init__.py` — rewritten from a one-line stub to the full orchestrator + `_assert_non_empty` + `_drop_orphan_nodes` + `_drop_short_edges` + `_assert_finite_elevations` + `_polyline_length_m` helpers, with module-scope `_SETUP_DIFFICULTY_CAP`, `_EARTH_RADIUS_M`, `_PIPELINE_LENGTH_FLOOR_M`. Full Google-style docstring on `run_setup_stages`.
- `tests/integration/test_pipeline_end_to_end.py` — 11 tests: 5 fixture-driven (AC #4) + 6 guard-helper (AC #5). Module-scoped `prepared_graph` fixture patches `osm_load` for the committed-fixture run.

**Modified:**
- `src/steeproute/models.py` — `PipelineConfig` dataclass added (sibling to `Area`); `pathlib` import added.
- `src/steeproute/errors.py` — `PipelineContractError(PreExecutionError)` added.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.5 `backlog → ready-for-dev → in-progress → review`; dated comments added.

**Untouched (intentionally):**
- `src/steeproute/pipeline/osm.py`, `smoothing.py`, `dem.py`, `climbs.py` — orchestrator wires the existing public stage functions; no changes needed.
- `src/steeproute/cli/setup.py` — `--center / --radius / --untagged-trails / --dem-path → run_setup_stages → cache.write_entry` wiring lands in Story 2.8.
- `tests/unit/test_climbs.py::fixture_pipeline_through_stage7` — explicit non-refactor per AC #6.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-20 | Yann (Claude Opus 4.7) | bmad-code-review applied: 3 patches landed (P1 MED post-`_drop_short_edges` non-empty re-assertion — second `_assert_non_empty` call covers the case where stages 3-4 + the short-edge prune drop all edges, preventing cryptic `ZeroDivisionError` from stage 7 on pathological fixtures; P2 LOW-MED `config.dem_path` fail-fast at the top of `run_setup_stages` — raises `BadCLIArgError` before stage 1 if the file doesn't exist, saving the OSM-fetch + stages 3-4 cost on bad input; orchestrator now imports `BadCLIArgError` alongside `PipelineContractError`; new test `test_run_setup_stages_fails_fast_on_missing_dem_path`; P3 LOW integration-test `prepared_graph` skip-check extended to also cover `_OSM_FIXTURE_PATH`). 1 item deferred to Story 2.8 (silent edge-drop debug logging — needs `--verbose` plumbing first). 35 dismissed inline in story Review Findings (Acceptance Auditor returned 0 findings — all 7 ACs satisfied; Blind Hunter 27 + Edge Case Hunter 12 raw findings triaged). All four CI gates green post-review: ruff, ruff format, basedpyright 0/0/0, pytest 276 passed (+1 from prior 275 — P2's new fail-fast test) at 96% overall coverage; `pipeline/__init__.py` 98% (64 stmts, 1 missed — same defensive `len(coords) < 2` guard, unchanged). Live OSM re-verified — no regression. | _pending_ |
| 2026-05-20 | Yann (Claude Opus 4.7) | Story 2.5 implemented: pipeline orchestrator `pipeline/__init__.py::run_setup_stages(area, config) -> MultiDiGraph` wires stages 1→7 with four orchestrator-level inter-stage contract guards. New `PipelineConfig` dataclass in `models.py` (`untagged_policy`, `dem_path`; `difficulty_cap` deliberately absent per Architecture §Cat 3b — orchestrator pins it to `"T6"` internally). New `PipelineContractError(PreExecutionError)` in `errors.py`. Resolves the items deferred to Story 2.5 in `deferred-work.md`: Story 2.1 D2 (empty-graph guard) + D5 (orphan-node prune), Story 2.4 D1 (length_m == 0 guard via `_PIPELINE_LENGTH_FLOOR_M = 1e-3`) + D2 (finite-elev guard post-stage-6) + D3 (out-and-back self-loop synthetic test). Re-defers Story 2.2 D1 (`n_intervals` upper bound) to Story 2.8 — CLI exposure is where the guard fits. New `tests/integration/test_pipeline_end_to_end.py` with 11 tests: 5 fixture-driven (`run_setup_stages` over committed Grenoble fixtures with `osm_load` patched to read `osm_graph.graphml`) covering topology baseline ±10% (468 nodes, 1208 edges), full 9-attribute contract, sign + finiteness sweep, aggregate `sum(length_m)` ±10% of 167 km baseline, no-orphan-nodes; 6 guard-helper tests (3 raise + 3 sanity) covering AC #3a/c/d directly via crafted single-edge graphs (out-and-back self-loop drop, NaN-elevation raise, zero-edges raise). `tests/unit/test_climbs.py::fixture_pipeline_through_stage7` kept untouched per AC #6 (explicit non-refactor). No new runtime/dev deps. All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 275 passed (+11 from prior 264) at 96% overall coverage; `pipeline/__init__.py` 98% (1 line uncovered — defensive `len(coords) < 2` guard in `_polyline_length_m`, unreachable because `shapely.LineString` construction enforces ≥ 2 coords; same accepted-defensive pattern as Story 2.4's `is_valid_for_metrics`); `models.py` 100%, `errors.py` 100%. Live OSM test re-verified — no regression. | _pending_ |
