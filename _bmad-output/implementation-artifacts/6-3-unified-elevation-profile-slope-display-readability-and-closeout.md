# Story 6.3: Unified elevation profile, slope-display readability, and closeout

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the metric box, the value the solver optimizes, and the plotted elevation curve to agree, the elevation deadband to reshape the actual profile, and the displayed slope and colors to be readable,
so that a route's reported D+/D− matches its curve and the profile is trustworthy at a glance.

## Acceptance Criteria

1. **One canonical elevation profile.** `pipeline/smoothing.py` gains two pure functions (new graph, no input mutation): `graph_smooth_elevation` — a **global graph-Laplacian diffusion** (Jacobi iterations; each graph *node* is a single shared elevation variable, so all edges meeting at a node agree at that node) with strength in **meters**, decoupled from the 10 m resample spacing; and `graph_deadband_elevation` — a **profile transform** that flattens sub-floor up/down reversals, preserves turning points, interpolates between them, and pins endpoints to the shared node value.

2. **Cache boundary moves; one graph feeds everything.** Setup now caches **raw post-stage-5 elevation**: `pipeline/__init__.py::run_setup_stages` no longer runs `median_smooth_elevation` or `compute_edge_metrics`. `cli/query.py`, after `filter_trails`, reshapes the whole graph **once** — `graph_smooth_elevation` → `graph_deadband_elevation` → `compute_edge_metrics` (naive sum) — then runs `detect_climbs` → `contract_climbs` → solver on it, and passes that **same** reshaped graph to `output.render`. Box D+/D−, the solver objective, and the plotted curve are all the naive up/down sum of this one profile.

3. **`compute_edge_metrics` stays a pure naive sum** — no `deadband_m` parameter is added anywhere (the deadband lives in the profile geometry, not in the sum). The function is unchanged except for being invoked query-side.

4. **Flags.** `--elevation-smoothing` (meters) and `--elevation-deadband` (meters, default `0` = off) are defined in `cli/_shared.py` and stacked on the query CLI. `steeproute --help` lists both with defaults and one-line help.

5. **Dead code removed.** The per-edge `median_smooth_elevation` (and its `_moving_median` helper if unused elsewhere) is deleted. The display reads the single canonical profile — confirm no separate render-side continuous-smoothing pass exists (current `output.py`/template read `vertices_resampled` directly); remove it if one is present.

6. **Display readability.** The diverging slope-color clamp is raised from `0.30` to `tan(30°) ≈ 0.58`; the displayed slope is computed over a longer baseline (±2–3 vertices ≈ 30–50 m) instead of one ~10 m segment (also tames sub-5 m end-segment spikes); cumulative D+/D− is added to the profile hover and reaches the box totals at the final vertex.

7. **Regression test (fails on pre-fix code).** Over a route, assert box D+/D− equals the plotted-curve cumulative at the final vertex (gap ≤ tolerance), and that max per-segment `|ΔElev|` never exceeds the raw-DEM maximum (no manufactured spikes).

8. **Closeout green.** The 8 metamorphic invariants (Story 3.8, `tests/integration/test_metamorphic.py`) re-validate under the new contraction/distinctness/smoothing — especially `scale_elevation`, `relax_theta`, and node-relabel isomorphism (the canonical profile + base-segment identity must stay relabel-invariant). The full suite (unit + integration + e2e) passes on the primary Windows platform. PRD/architecture/epics already reflect the two flags + canonical profile (applied at correct-course) — verify, don't re-author.

9. **Purity preserved.** The new smoothing/deadband functions and the query-side reshaping step do not mutate their inputs.

10. **Human-review checkpoint (`bmad-checkpoint-preview`)** on the real trigger area confirms box==curve, no manufactured slope spikes, genuine steep terrain preserved, and readable color/baseline display — before the story is marked done.

## Tasks / Subtasks

- [x] Add the two canonical-profile functions to `pipeline/smoothing.py` (AC: #1, #9)
  - [x] `graph_smooth_elevation(graph, strength_m=...)` — graph-Laplacian Jacobi diffusion, shared per-node variable, strength in meters; module-scope named constant for the default
  - [x] `graph_deadband_elevation(graph, deadband_m=...)` — profile transform; flatten sub-floor reversals, keep turning points, interpolate, pin endpoints
  - [x] Do **not** repeat the two failed per-edge approaches (see Dev Notes)
- [x] Move stages 6–7 query-side and feed one graph everywhere (AC: #2, #3, #9)
  - [x] Drop `median_smooth_elevation` + `compute_edge_metrics` from `run_setup_stages`; keep `_assert_finite_elevations` on raw elevation
  - [x] Add `operationalize_graph` (single home for the query-side smooth → deadband → naive-sum metrics, reused by tests); call it in `cli/query.py` before `filter_trails`/`detect_climbs`/`contract_climbs`/solver; pass the same operational graph to `output.render`
  - [x] Leave `compute_edge_metrics` a pure naive sum (no `deadband_m` param)
- [x] Add `--elevation-smoothing` / `--elevation-deadband` flags (AC: #4)
- [x] Remove dead per-edge smoothing + any render-side continuous pass (AC: #5)
- [x] Display readability in `output.py` / `templates/route.html.j2` (AC: #6)
  - [x] Clamp → `tan(30°) ≈ 0.58`; slope over ±2-vertex baseline; cumulative D+/D− in hover
- [x] Box==curve regression test, failing on pre-fix code (AC: #7)
- [x] Closeout: re-validate metamorphic invariants, help-text assertion, full Windows suite, verify planning docs (AC: #8)
- [x] Human-review checkpoint on the real trigger area (AC: #10) — user confirmed 2026-06-08 (box==curve, no manufactured spikes, steep terrain preserved, readable color/baseline)

### Review Findings

Adversarial code review 2026-06-08 (Blind Hunter + Edge Case Hunter + Acceptance Auditor). Auditor: all 10 ACs satisfied, no violations; cache-boundary metric-reader sweep clean (no consumer reads `length_m`/`d_plus_m`/… off the raw cached graph). 3 patch findings, 1 deferred, ~10 dismissed as noise.

- [x] [Review][Patch] New flags bypass finiteness/range validation — `--elevation-smoothing nan|inf` crashed with a raw traceback (exit 1) / `--elevation-deadband nan|inf` silently flattened the profile. **Fixed:** `validate_solver_options` now finiteness- and `>= 0`-checks both flags (threaded in from `query.py`); verified `--elevation-smoothing nan` and `--elevation-deadband inf` now exit 2 with `error:`. Added 6 rejection cases to `test_area_parsing.py` [src/steeproute/cli/_shared.py, src/steeproute/cli/query.py]
- [x] [Review][Patch] Weakened smoothing test — **Fixed:** added an assertion that the 1060 m interior spike is pulled down by > 20 m (real attenuation), alongside the existing `<= raw_max` low-pass bound [tests/unit/test_smoothing.py]
- [x] [Review][Patch] `fixture_pipeline_through_stage7` didn't mirror `operationalize_graph` (skipped the deadband step) — **Fixed:** now calls `operationalize_graph` at production defaults [tests/unit/test_climbs.py]
- [x] [Review][Defer] No upper bound on the diffusion iteration count — a huge finite `--elevation-smoothing` (e.g. `100000`) yields `iters ≈ 1.7e7` and an unbounded hang; once finiteness is validated only absurd finite values trigger it. Low priority for an N=1 tool [src/steeproute/pipeline/smoothing.py] — deferred

## Dev Notes

**This is the Epic 6 closeout story** and the largest single piece — it reshapes the *one* canonical elevation profile that the metric box, solver objective, and plotted curve all read. Items 8 (smoothing), 5 (deadband), 6 (slope display) of the correct-course brief. Prototyped on spike `spike/smoothing-consistency` — re-implement cleanly, **do not merge the spike**.

**The core defect.** Pre-fix, the solver/box used a per-edge elevation smoothing pinned to raw DEM at node boundaries while the display used a separate continuous whole-route smoothing → box and curve disagreed by ~58–78 m. The deadband made it worse: it reshaped the metric at sum-time but never touched the displayed vertices. The fix is structural: **one global profile, smoothed and deadbanded once, feeds box + objective + curve as a naive sum.**

**Two approaches already failed — do not repeat** (from brief Item 8):
- Per-edge "average neighbouring edges' context at each node" → a jump at every junction on the real junction-dominated graph.
- Per-edge moving-average with pinned endpoints → manufactures ~1000% slope spikes and can't smooth across 2-vertex edges.
The global graph-Laplacian (shared node variable) fixes both, because the smoothing variable lives on the graph node, not per-edge.

**Why query-side.** Smoothing/deadband become free query knobs (`--elevation-smoothing`, `--elevation-deadband`) and the cache stays smoothing-independent. The cost: moving stages 6–7 out of `run_setup_stages` changes `compute_pipeline_content_hash` ([cache.py:140]) → prepared areas **re-prepare once** on next `steeproute-setup` (the same one-time cost roads `6.2` already incurred). No cache-key schema change. Setup ends at `sample_elevation` + `_assert_finite_elevations` (raw elevation); the cached `graph.pkl` carries `vertices_resampled` without the `length_m`/`d_plus_m`/`d_minus_m`/`avg_gradient` metrics — query computes those.

**Display (`output.py` + `templates/route.html.j2`).** Current state (verified): `output.py::_profile_series` ([output.py:312]) builds `(distances, elevations)` straight from `vertices_resampled` with **no** smoothing pass, and the template has no smoothing JS — so AC #5's "render-side continuous pass" is already absent; just confirm and don't reintroduce one. The slope-color clamp is the JS constant `CLAMP = 0.30` in `route.html.j2` (gradient-color function, ~line 132) → raise to `tan(30°) ≈ 0.58`. Per-segment slope is computed in the chart `segment.borderColor` callback and the hover tooltip (~lines 154–196) — widen the baseline to ±2–3 vertices there. Add cumulative D+/D− to the hover (it should reach the box totals at the final vertex — a one-glance consistency check).

**Architecture conventions (must follow):** named module-scope constants over inline magic literals (mirror `ELEVATION_MEDIAN_WINDOW`); pure pipeline functions (`def stage(graph, ...) -> new_graph`, no input mutation); `frozen=True, slots=True` dataclasses; no loose `dict` data shapes; networkx edge data read-only downstream. Smoothing strength is in **meters** and must be converted to a vertex/iteration count internally (decouple from the 10 m resample spacing) — see open tuning item in the proposal.

**Human-review checkpoint repro** (run after the regression test is green; same trigger area as 6.1): `--center 45.260,5.788 --radius 4 --cache-dir ./.trial-cache --seed 44 --l-connector 50 --j-max 0 --difficulty-cap t4 --n 10 --iter-budget 200000` (add `--elevation-deadband` to exercise the new transform). Confirm box D+/D− matches the plotted curve, no manufactured slope spikes, genuine steep terrain preserved, color/baseline reads correctly. Re-prepare the trial cache first (cache boundary moved).

### Project Structure Notes

- Code touched: `pipeline/smoothing.py` (two new functions; remove `median_smooth_elevation`/`_moving_median`), `pipeline/__init__.py` (drop stages 6–7 from `run_setup_stages`), `cli/query.py` (query-side smooth→deadband→metrics; same graph to render), `cli/_shared.py` (two flags), `output.py` + `templates/route.html.j2` (display). `pipeline/climbs.py::compute_edge_metrics` is **invoked** query-side but otherwise unchanged.
- Setup-side change → `pipeline_content_hash` changes → one-time cache re-prepare (documented; no schema change). This is the second and last cache-moving change in Epic 6.
- Tests: new box==curve regression test (route-level; small topology-specific fixture, plus a cheap pinned real-area assertion if practical — feeds the Epic 8 fixture set); `tests/integration/test_metamorphic.py` re-validation; CLI help/smoke assertion for the two flags. Setup/cache tests that assumed metrics in the cached graph need re-pinning to the new raw-elevation boundary.
- Planning docs (PRD §Config Schema, Architecture stages 2/3b/6, FR11 wording) were already updated during the 2026-06-07 correct-course — verify, no new planning edits.

### References

- [Sprint change proposal — route discovery](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-07-route-discovery.md) §4B-B5/B6, §4C (6.3 closeout), §4D (checkpoint), §2 (cache boundary move), §3 (failed approaches — do not repeat)
- [Correct-course brief](_bmad-output/planning-artifacts/correct-course-brief-2026-06-05-route-discovery.md) Items 8, 5, 6
- [Epic 6 / Story 6.3](_bmad-output/planning-artifacts/epics.md:827)
- Code: [median_smooth_elevation](src/steeproute/pipeline/smoothing.py:129), [ELEVATION_MEDIAN_WINDOW](src/steeproute/pipeline/smoothing.py:50), [compute_edge_metrics](src/steeproute/pipeline/climbs.py:52), [run_setup_stages stages 6–7](src/steeproute/pipeline/__init__.py:143), [query call sequence](src/steeproute/cli/query.py:215), [_profile_series](src/steeproute/output.py:312), [option decorator pattern](src/steeproute/cli/_shared.py:252), [compute_pipeline_content_hash](src/steeproute/cache.py:140)
- Previous stories: [6.1 — route-discovery bug fixes](_bmad-output/implementation-artifacts/6-1-route-discovery-bug-fixes-junction-split-sac-cap-undirected-distinctness.md) (checkpoint repro + query-side `filter_trails` ordering), [6.2 — roads as connectors](_bmad-output/implementation-artifacts/6-2-roads-as-connectors.md) (the prior `pipeline_content_hash` cache re-prepare precedent)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Amelia / dev-story)

### Debug Log References

Full suite: `uv run pytest` → 714 passed, 2 deselected (`@pytest.mark.live`), ~306s. Lint (`ruff check src tests`), format (`ruff format --check`, changed files), and type-check (`basedpyright src/steeproute` + changed test files) all clean.

### Completion Notes List

- **Canonical-profile functions (`pipeline/smoothing.py`).** Added `graph_smooth_elevation(graph, strength_m)` — a global graph-Laplacian Jacobi diffusion where **each graph node is one shared elevation variable** (its neighbours are the adjacent vertex of every incident edge), so edges meeting at a node agree there and joins stay consistent (the structural basis of box==curve). Added `graph_deadband_elevation(graph, deadband_m)` — a profile transform that flattens sub-floor reversals and pins endpoints. Both pure. Removed the per-edge `median_smooth_elevation` + `_moving_median` + `ELEVATION_MEDIAN_WINDOW`.
- **Strength in meters (AC #1, decoupled from spacing).** `strength_m` → vertex window `strength_m / RESAMPLE_SPACING_M` → Jacobi iterations `round(window²/6)` (the `σ≈√(iters/2)` Gaussian-equivalence of a `λ=0.5` step). `ELEVATION_SMOOTHING_DEFAULT_M = 50.0` **replaces the removed median's ~50 m smoothing** so default cliff-bias mitigation is preserved; `ELEVATION_DEADBAND_DEFAULT_M = 0.0` (off, matching pre-6.3 behaviour). A strength at/below the spacing (window ≤ 1) is a no-op.
- **Cache boundary moved (AC #2).** `run_setup_stages` now ends at `sample_elevation` + `_assert_finite_elevations` (raw post-stage-5 elevation); it no longer runs median smoothing or metrics. New `operationalize_graph(graph, *, elevation_smoothing_m, elevation_deadband_m)` in `pipeline/__init__.py` is the single home for the query-side stages 6-7 (smooth → deadband → naive-sum metrics) — called by `cli/query.py` and reused by the six fixture tests so production and tests share one reshaping. `compute_edge_metrics` is unchanged (naive sum, **no `deadband_m` param** — the deadband lives in the profile geometry, per the story's explicit "trap" warning). Moving the code changes `compute_pipeline_content_hash`, so prepared areas re-prepare once on next `steeproute-setup` (no cache-key schema change).
- **One graph feeds everything (box==curve).** `cli/query.py`: `operationalize_graph(prepared.graph, …)` → `filter_trails` → `detect_climbs` → `contract_climbs` → solver, and the **same** operational graph to `output.render`. Verified: super-edge `d_plus_m = Σ base d_plus_m` (graph.py) and `route.metrics.d_plus_m = Σ edge.d_plus_m` (validator), and shared-node consistency zeroes the join deltas — so box D+/D− equals the plotted-curve cumulative exactly.
- **Display (AC #6).** `route.html.j2`: slope-color clamp `0.30 → tan(30°) ≈ 0.58`; displayed slope computed over a ±2-vertex baseline (`baselineSlope`, ~40 m) in both the segment coloring and the hover (tames sub-5 m end-segment spikes); cumulative D+/D− added to the hover (reaches the box totals at the final vertex). `output.py` needed **no** render-side smoothing removal — there was no continuous pass in current `main` (the brief's "render-side continuous smoothing" only ever lived on the spike); confirmed `_profile_series` reads `vertices_resampled` directly, so AC #5's removal was a verify-and-don't-reintroduce.
- **Regression test (AC #7) — `tests/integration/test_elevation_consistency.py`.** Runs the real query-side chain with both smoothing (50 m) **and** deadband (8 m) on, and asserts (1) box D+/D− == plotted-curve cumulative at the final vertex (≤ 0.05 m) and (2) max per-segment |ΔElev| in the reshaped profile ≤ raw-DEM max (no manufactured spikes). Constructed to fail on the pre-fix sum-time-deadband design (deadband changed the box but not the curve → tens-of-meters gap) and on any per-edge method that dumps a node offset into one segment.
- **Closeout (AC #8).** The 8 metamorphic invariants (`test_metamorphic.py`) pass unchanged — they build `ContractedGraph` directly and bypass the elevation pipeline, so the smoothing change is inert there (re-validated, esp. `scale_elevation`/`relax_theta`/node-relabel). CLI help asserts the two new flags (`test_cli_help.py` + `test_cli_smoke.py`). Planning docs (PRD config schema, architecture stages 2/3b/6, FR11 wording) were applied during the 2026-06-07 correct-course — verified, no new planning edits. Full Windows suite green.
- **Test re-pinning (cache-boundary blast radius).** Six integration fixture tests that did `run_setup_stages → detect_climbs` now wrap with `operationalize_graph`. `test_pipeline_end_to_end.py` split into a raw-setup-contract assertion (no metrics in the cached graph) + an `operational_graph` fixture for the metric/sign/length tests. `test_cache_roundtrip.py::_expected_edge_attributes` trimmed to the raw 5-attribute contract. `test_climbs.py`'s local stage-1-7 fixture switched to `graph_smooth_elevation`. `test_smoothing.py`'s median section replaced with `graph_smooth_elevation`/`graph_deadband_elevation` tests (incl. the shared-node-consistency and low-pass-no-spike properties). The `test_climb_detection_fixture.py` baselines (count 50, D+ 8065.5) still hold within ±10% under the new smoothing.
- **AC #10 (human-review checkpoint) — DONE (2026-06-08).** User re-prepared the trial cache (the OSM fetch needed the separately-shipped truststore fix to clear corporate TLS interception) and ran the query on the real trigger area (`steeproute --center 45.260,5.788 --radius 4 --cache-dir ./.trial-cache --seed 44 --l-connector 50 --j-max 0 --difficulty-cap t4 --n 10 --iter-budget 200000 --elevation-deadband 8`), confirming box D+/D− matches the plotted curve, no manufactured slope spikes, genuine steep terrain preserved, and readable color/baseline display.

### File List

- `src/steeproute/pipeline/smoothing.py` — added `graph_smooth_elevation` + `graph_deadband_elevation` (+ `_deadband_profile`) and `ELEVATION_SMOOTHING_DEFAULT_M`/`ELEVATION_DEADBAND_DEFAULT_M`/`_DIFFUSION_LAMBDA`; removed `median_smooth_elevation`/`_moving_median`/`ELEVATION_MEDIAN_WINDOW`
- `src/steeproute/pipeline/__init__.py` — `run_setup_stages` ends at stage 5 (raw elevation, no metrics); new `operationalize_graph` (query-side stages 6-7); docstrings + `_assert_finite_elevations` updated to post-stage-5
- `src/steeproute/cli/_shared.py` — `elevation_smoothing_option` / `elevation_deadband_option` (meters); import of the two default constants
- `src/steeproute/cli/query.py` — call `operationalize_graph` before `filter_trails`; render off the operational graph; new flag decorators + kwargs
- `src/steeproute/templates/route.html.j2` — clamp `tan(30°)`; `baselineSlope` (±2 vertices) for segment color + hover; cumulative D+/D− in hover
- `src/steeproute/output.py` — `_profile_series` docstring updated (canonical profile / box==curve); no behavioural change
- `tests/unit/test_smoothing.py` — replaced median tests with `graph_smooth_elevation` / `graph_deadband_elevation` tests
- `tests/unit/test_climbs.py` — local stage-1-7 fixture uses `graph_smooth_elevation`
- `tests/unit/test_cli_help.py`, `tests/e2e/test_cli_smoke.py` — assert the two new flags (query-only)
- `tests/integration/test_pipeline_end_to_end.py` — raw-setup contract + `operational_graph` fixture for metric tests
- `tests/integration/test_cache_roundtrip.py` — `_expected_edge_attributes` trimmed to the raw contract
- `tests/integration/{test_climb_detection_fixture,test_graph_contraction_fixture,test_grasp_on_fixture,test_grasp_reproducible,test_validator_on_fixture,test_output_on_fixture}.py` — wrap `run_setup_stages` with `operationalize_graph`
- `tests/integration/test_elevation_consistency.py` — new box==curve + no-manufactured-spikes regression test

## Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-06-08 | 0.1 | Story drafted (create-story) |
| 2026-06-08 | 1.0 | Implemented unified elevation profile (graph-Laplacian smoothing + deadband-as-transform, query-side), cache-boundary move (stages 6-7 → `operationalize_graph`), `--elevation-smoothing`/`--elevation-deadband` flags (meters), display readability (tan(30°) clamp, baseline slope, cumulative D+/D− hover). Box==curve regression test added; 8 metamorphic invariants re-validated; affected fixture tests re-pinned. Full suite 714 passed; lint/format/type-check clean. Status → review. AC #10 human-review checkpoint pending user. |
| 2026-06-08 | 1.1 | AC #10 human-review checkpoint confirmed by user on the real trigger area. Adversarial code review (3 layers): all 10 ACs satisfied, cache-boundary metric-reader sweep clean. Applied 3 patches — finiteness/`>=0` validation for the two new flags (was exit-1 traceback on `nan`/`inf`), strengthened the smoothing-attenuation test, fixed the `test_climbs` fixture to mirror `operationalize_graph`. 1 deferred (unbounded diffusion iters on absurd finite strength). Full suite 721 passed; lint/format/type-check clean. Status → done. |
