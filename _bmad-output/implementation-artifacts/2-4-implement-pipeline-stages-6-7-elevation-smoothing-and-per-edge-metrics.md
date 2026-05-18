# Story 2.4: Implement pipeline stages 6–7 — elevation smoothing and per-edge metrics

Status: done

## Story

As a developer,
I want `pipeline/smoothing.py::median_smooth_elevation` (stage 6) and `pipeline/climbs.py::compute_edge_metrics` (stage 7) to close out the setup-side pipeline,
so that each edge carries `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient` in addition to its smoothed elevation samples — the attribute contract that downstream stages 8–9 and validation depend on.

## Acceptance Criteria

1. `pipeline/smoothing.py` defines `median_smooth_elevation(graph, window: int = ...) -> MultiDiGraph` (stage 6: moving-median on the elevation component of `vertices_resampled`). Each output edge's `vertices_resampled` entries keep their `(lat, lon)` exactly equal to the input's; only the third `elevation_m` component is smoothed. Endpoints (first and last vertex elevations) are pinned to input values — topology + elevation-at-nodes never drift. The window size is a module-scope named constant (no inline magic number per Architecture §Numerical and data discipline). Pure: no global state, no `print`, returns a new graph — input never mutated.

2. `pipeline/climbs.py` defines `compute_edge_metrics(graph) -> MultiDiGraph` (stage 7: per-edge `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`). On every output edge:
    - `length_m: float` — cumulative 2D ground-distance along `vertices_resampled` (using the same local-equirectangular pattern as `smoothing._resample_meters`, or distance along `geometry` — pick one and document). Always `> 0` for non-degenerate edges (stage 4 dropped degenerates).
    - `d_plus_m: float` — sum of **positive** `Δelev` between consecutive `vertices_resampled` entries; always `≥ 0`.
    - `d_minus_m: float` — sum of the **absolute values of negative** `Δelev`; always `≥ 0` (positive magnitude — matches the manifest-schema sample in Architecture §Cat 4 where `d_plus_m`/`d_minus_m` are both positive).
    - `avg_gradient: float` — `(d_plus_m + d_minus_m) / length_m` (total altitude change per horizontal meter; dimensionless). Always finite, always `≥ 0`.
    - Pure: no global state, no `print`, returns a new graph — input never mutated. Upstream attribute contract (`geometry`, `vertices_resampled`, `sac_scale`, `highway`, `osm_way_id`) is preserved unchanged.

3. `tests/unit/test_smoothing.py` (extended) covers `median_smooth_elevation` analytically on synthetic edges:
    - Flat profile (constant elevation) → output equals input within float tolerance.
    - Monotone profile (strictly increasing elevation) → smoothed values are monotone-non-decreasing; first and last elevations equal input exactly.
    - Spike profile (single high-elevation outlier in an otherwise flat run, with window large enough to cover it) → spike value is replaced by the median of its window (i.e. the flat baseline, not the spike).
    - The `(lat, lon)` components of `vertices_resampled` are untouched (assert exact equality with input on every vertex).

4. `tests/unit/test_climbs.py` (new) covers `compute_edge_metrics` analytically on synthetic edges built directly (no stages 1–5 needed for these — construct `vertices_resampled` by hand):
    - Flat profile → `d_plus_m == 0`, `d_minus_m == 0`, `avg_gradient == 0`; `length_m` matches the analytical 2D distance to documented tolerance.
    - Pure uphill, known slope → `d_plus_m` equals total Δelev, `d_minus_m == 0`, `avg_gradient` equals `Δelev / length_m` to float tolerance.
    - Pure downhill, known slope → `d_plus_m == 0`, `d_minus_m` equals `|Δelev|` (positive magnitude), `avg_gradient` matches.
    - Mixed up-then-down profile → both `d_plus_m > 0` and `d_minus_m > 0`; their sum equals total absolute elevation change.

5. An integration-style fixture test (in `tests/unit/test_climbs.py`, mirroring `test_dem.py::fixture_pipeline_through_stage5`) runs stages 1→2→3→4→5→6→7 on the real Grenoble fixture and asserts on every output edge:
    - Full attribute contract populated: `geometry`, `vertices_resampled`, `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`, `highway`, `osm_way_id`.
    - No NaN/Inf in any metric (all finite floats).
    - `length_m > 0`, `d_plus_m ≥ 0`, `d_minus_m ≥ 0`, `avg_gradient ≥ 0` for every edge.
    - Plausibility: a strong majority of edges (e.g. ≥ 95%) have `avg_gradient < 0.8` (80% — Alpine sanity cap, not a strict bound). Document the threshold rationale inline; this is a smoke check against axis-swap / unit / sign-flip bugs in stages 6–7, not a strict geometric guarantee.

6. A `hypothesis` property-based test in `tests/unit/test_climbs.py` asserts `d_plus_m ≥ 0`, `d_minus_m ≥ 0`, `length_m > 0`, and `avg_gradient` finite for any non-degenerate synthetic input edge (≥ 2 distinct `vertices_resampled` entries, finite coords, finite elevations). Use `hypothesis.assume` on the same validity predicate as the stage's degenerate-edge guard (no drift between strategy filter and production check — same pattern as Story 2.2's `is_valid_polyline`).

7. All four CI gates pass on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov`. No new runtime deps anticipated (math + networkx + shapely already available). `pipeline/smoothing.py` and `pipeline/climbs.py` clear the 95% pure-logic coverage floor (Architecture §Cat 11e). Apply the same basedpyright per-file pragma as the other pipeline modules if needed.

## Tasks / Subtasks

- [x] **Task 1: Implement `median_smooth_elevation` in `pipeline/smoothing.py`** (AC: #1)
    - [x] Module-scope constant `ELEVATION_MEDIAN_WINDOW: int = 5` with rationale inline (smooths over ~50 m at the 10 m vertex spacing; absorbs 1-2 cell-scale DEM artifacts on the 5 m IGN grid without smearing ridge crests).
    - [x] Standard stage shape: `out = graph.copy(); ...; return out`. Builds a new `vertices_resampled` list per edge by replacing elevations with the windowed median (endpoints pinned).
    - [x] Uses `statistics.median` (stdlib) over the sliding window; boundary clamp mirrors `_moving_average` (`lo = max(0, i - half); hi = min(n, i + half + 1)`).
    - [x] Assumes stage-5 input contract: every edge has a non-empty `vertices_resampled` list. No defensive handling of missing/empty.
- [x] **Task 2: Create `pipeline/climbs.py` with `compute_edge_metrics`** (AC: #2)
    - [x] New module with full docstring covering all four metrics + `d_minus_m`-as-positive-magnitude convention + reference to Architecture §Cat 4 manifest sample. basedpyright per-file pragma matches the other pipeline modules.
    - [x] Standard stage shape. `length_m` from `vertices_resampled (lat, lon)` via local-equirectangular projection (cos-of-mean-latitude), same pattern as `smoothing._resample_meters`. `d_plus_m` / `d_minus_m` computed in a single pass over consecutive elevation deltas; `avg_gradient = (d_plus_m + d_minus_m) / length_m`.
    - [x] `_EARTH_RADIUS_M` redefined locally with a comment cross-referencing `smoothing._EARTH_RADIUS_M` (physical constant; each pipeline module stays self-contained).
- [x] **Task 3: Unit tests (analytical synthetic)** (AC: #3, #4)
    - [x] Extended `tests/unit/test_smoothing.py` with 8 stage-6 tests: module-constant guard, flat/monotone/spike/lat-lon-preservation analytical tests, pure-function non-mutation, attribute-contract preservation, short-polyline passthrough.
    - [x] Created `tests/unit/test_climbs.py` mirroring `test_smoothing.py`'s structure with `_single_edge_graph_with_elevation` helper + `_expected_length_m` independent reference + four AC-#4 analytical metric tests (flat, pure-uphill, pure-downhill, mixed up-down).
- [x] **Task 4: Integration-style fixture test through stages 1–7** (AC: #5)
    - [x] Module-scoped `fixture_pipeline_through_stage7` in `tests/unit/test_climbs.py` chains `normalize_edges → smooth_polylines → resample_edges → sample_elevation → median_smooth_elevation → compute_edge_metrics`. Skip-on-missing-fixture-file pattern from `test_dem.py`.
    - [x] Three integration assertions per AC #5: `test_fixture_pipeline_full_contract_populated`, `test_fixture_pipeline_metrics_are_finite_and_signed_correctly`, `test_fixture_pipeline_gradients_are_plausibly_alpine` (95% of edges below the 80% gradient cap). Threshold rationale documented inline.
- [x] **Task 5: Hypothesis property test** (AC: #6)
    - [x] `test_compute_edge_metrics_property_metric_invariants` — `max_examples=50`, generates `(lat, lon, elev)` triples with finite bounded components; `hypothesis.assume(is_valid_for_metrics(verts))` aligns the strategy filter with the stage's input contract (mirrors Story 2.2's `is_valid_polyline` pattern). `is_valid_for_metrics` promoted to public for this exact reason.
- [x] **Task 6: Verify CI** (AC: #7)
    - [x] All four gates green: `uv run ruff check` (All checks passed!), `uv run ruff format --check` (37 files already formatted), `uv run basedpyright` (0 errors, 0 warnings, 0 notes), `uv run pytest --cov` (264 passed, 1 deselected, 96% overall coverage).
    - [x] `pipeline/smoothing.py` at 99% (112 stmts, 1 missed — pre-existing `is_valid_polyline` defensive guard). `pipeline/climbs.py` at 95% (43 stmts, 2 missed — both in `is_valid_for_metrics` defensive branches; hypothesis strategy excludes the inputs that would hit them, mirroring Story 2.2's accepted-uncovered defensive guards).
    - [x] Live OSM test re-verified (`STEEPROUTE_USE_OS_TRUSTSTORE=1 uv run pytest -m live` → 1 passed). No regression.

### Review Findings

_From `bmad-code-review` 2026-05-18. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Acceptance Auditor returned 0 findings (all 7 ACs satisfied); Blind Hunter raised 14, Edge Case Hunter raised 15. After dedupe + triage: 5 patches, 3 defers, 14 dismissed (incl. 2 false-positive shallow-copy claims empirically disproved)._

**Patches (unambiguous fixes):**

- [x] [Review][Patch] **P1 (MED): Hypothesis strategy uses lat/lon ∈ [-1, 1] — near-equatorial, misses cos(lat) correction bugs and admits float-underflow producing Inf gradients** — `cos(0) ≈ 1`, so any missing or wrong cos-of-mean-latitude correction in `_cumulative_2d_distance_m` would pass the property test silently. Worse, with bounds at ±1 the strategy can place every vertex within a few floating-point ULPs of `verts[0]`, making `length_m` underflow and `avg_gradient → Inf` — which would fail the existing `math.isfinite` assertion. Narrowed to lat ∈ [40, 50] (cos ≈ 0.65-0.77), lon ∈ [-10, 10] — cos correction exercised, underflow unreachable at `max_size=10`. [tests/unit/test_climbs.py:267-274] [Source: blind+edge]

- [x] [Review][Patch] **P2 (MED): Spike test only asserts on i=3 — interior boundary-clamp behavior (i=1, 2, 4, 5) is untested** — With `window=5` and the spike at i=3 in a 7-vertex polyline, only the symmetric center window is exercised. The boundary-clamp branches in `_moving_median` (the asymmetric windows at i=1/2 and i=4/5) are the most error-prone code path; an off-by-one in `lo = max(0, i - half)` or `hi = min(n, i + half + 1)` would silently slip past. Extended to assert every index (endpoints pinned + every interior median = 1000) so boundary-clamp branches are covered. [tests/unit/test_smoothing.py:485-505] [Source: blind+edge]

- [x] [Review][Patch] **P3 (MED): Monotone test asserts `>=` which is tautological for median smoothing** — The median of any window centered on index i in a sorted sequence is automatically order-preserving against its neighbours' medians; `elevs[i+1] >= elevs[i]` is essentially free. Renamed `test_median_smooth_elevation_monotone_output_is_bounded_by_window` and tightened to assert each interior output is bounded by its input window's `[min, max]` — the actual median property — which catches "median returns a value outside the window" bugs the `>=` check missed. (Exact-equality assertion would have been wrong: at i=1 and i=n-2 the clamp produces even-count windows where `statistics.median` returns the mean of the two middle values, e.g. `(1010+1020)/2 = 1015` for the test input — not bit-exact equal to the center. The window-bound assertion is the correct generalization.) [tests/unit/test_smoothing.py:470-483] [Source: blind+edge]

- [x] [Review][Patch] **P4 (LOW): `flat_unchanged` test uses `math.isclose(abs_tol=1e-12)` for bit-identical floats** — Median of identical floats is bit-exact; the `lat`/`lon` preservation test in the same suite correctly uses `==`. Switched to `==`. [tests/unit/test_smoothing.py:451-468] [Source: blind]

- [x] [Review][Patch] **P5 (LOW): `is_valid_for_metrics` docstring claims "≥ 2 distinct (lat, lon)" but code checks "any vertex differs from verts[0]"** — Two different semantics. The current check accepts `[(0,0,e0), (1,1,e1), (0,0,e2)]` (out-and-back: `verts[1]` differs from `verts[0]`, but `verts[2]` doesn't). Refactored to check consecutive-pair distinctness, which is structurally what `length_m > 0` requires; updated docstring to match. [src/steeproute/pipeline/climbs.py:60-83] [Source: blind+edge]

**Deferred (real but owned elsewhere):**

- [x] [Review][Defer] **D1 (HIGH): `compute_edge_metrics` has no guard against `length_m == 0` / non-finite elevations** — Trusts stage 4 to drop degenerate edges, but `_resample_meters` only catches the "all coords identical" case; an out-and-back polyline `[(0,0), (1,1), (0,0)]` or a closed loop where `u == v` would pass stage 4 with `length_m ≈ 0` and stage 7 would raise a cryptic `ZeroDivisionError` with no edge context. Tightening P5's `is_valid_for_metrics` partially closes the hypothesis-test gap, but production has no runtime guard — by current project convention ("trust internal code, only validate at system boundaries"). Belongs to Story 2.5's orchestrator, which owns end-to-end stage contract enforcement. [src/steeproute/pipeline/climbs.py:56, 271] [Source: blind+edge]

- [x] [Review][Defer] **D2 (LOW): NaN elevation delta is silently absorbed by `_elevation_gain_loss`'s strict `>` / `<` branches** — `nan > 0` and `nan < 0` are both False; a vertex with NaN elevation would yield `d_plus_m = d_minus_m = 0` for its segments instead of raising. Stage 5 already raises `DEMCoverageError` on NaN sample-back, so the failure mode is latent — but a future caller wiring in a different elevation source could bypass that guard. Belongs to Story 2.5 (orchestrator-level contract enforcement) or Story 2.9 (DEM source-unavailable / sanity checks). [src/steeproute/pipeline/climbs.py:115-118] [Source: edge]

- [x] [Review][Defer] **D3 (LOW): Self-loop and parallel-edge handling never asserted in synthetic tests** — `compute_edge_metrics` iterates `out.edges(data=True, keys=True)` so parallel edges with the same `(u, v)` but different `key` are visited correctly by construction. But a self-loop with `u == v` where `vertices_resampled` are out-and-back coincident in 2D would tie back to D1. The committed fixture has no self-loops; real OSM data (closed-loop trails) can. Owned by Story 2.5's pipeline integration test (or whichever first ships a fixture with self-loops). [tests/unit/test_climbs.py — whole file] [Source: edge]

**Dismissed (noise / false positive / handled elsewhere):**

- [x] [Review][Dismiss] **`MultiDiGraph.copy()` is a shallow copy → stage 6/7 mutate input** — Empirically false. Confirmed at the REPL: `g.edges[0,1,0] is out.edges[0,1,0]` returns `False` after `out = g.copy()`. NetworkX's `Graph.copy()` calls `datadict.copy()` per edge, producing a new top-level dict. The non-mutation tests pass for the right reason. [blind, 2 findings]
- [x] [Review][Dismiss] `is_valid_for_metrics` is "dead code" in production — it's a public test-helper following the same precedent as Story 2.2's `is_valid_polyline`. Promote-to-public-for-test-alignment is the project's standard pattern. [blind]
- [x] [Review][Dismiss] `_EARTH_RADIUS_M` triple-defined (smoothing.py + climbs.py + test_climbs.py) → drift risk — physical constant (WGS84 equatorial radius), well-known, won't drift; story explicitly chose duplication for module self-containment. The test reference using its own constant decouples test from impl, which is correct test design. [blind]
- [x] [Review][Dismiss] No endpoint-pinning assertion at `_moving_median` call site → speculative future-refactor — endpoint pinning is asserted by 3 separate tests (monotone, spike, short_polyline_passthrough). Defensive assertion at the call site adds noise without correctness signal. [blind]
- [x] [Review][Dismiss] `length_m > 0` fixture assertion is "unreachable" if ZeroDivisionError fires first — the assertion documents the contract; under correct operation it holds. If a future bug surfaces, ZeroDivisionError is still a CI failure. [blind]
- [x] [Review][Dismiss] Gradient-plausibility test can't catch `d_plus_m`/`d_minus_m` swap — `test_compute_edge_metrics_pure_uphill_matches_analytical` and `pure_downhill` already enforce sign-correctness at the unit-test layer, which is the right layer for that property. [blind]
- [x] [Review][Dismiss] `osm_way_id=12345` reused across test helpers → collision risk — single-edge helpers; trivial concern with no observable failure mode. [blind]
- [x] [Review][Dismiss] Spike test uses `==` on a value from `statistics.median` → fragile — median of an odd-length integer-valued slice is bit-exact equal to the middle element. With `window=5` (odd) the production code never produces a non-exact median for the spike test's inputs. The fragility-under-even-window concern is gated by the `assert window % 2 == 1` precondition. [edge]
- [x] [Review][Dismiss] Multi-edge gradient test divides by zero if `n_edges == 0` → speculative — the fixture is committed and has many edges; the prior contract test asserts `number_of_edges() > 0`. A future fixture regeneration that produced zero edges would also fail other tests loudly. [edge]
- [x] [Review][Dismiss] `length_m` summation order drift vs `math.fsum` — sub-µm precision over edge-scale distances; speculative future-refactor concern. [edge]
- [x] [Review][Dismiss] `assert window >= 1 and window % 2 == 1` stripped under `python -O` — project does not ship with `-O`; CI doesn't use it. The `_moving_median` is module-private and called from one site that always passes a valid window. Speculative. [edge]
- [x] [Review][Dismiss] `_cumulative_2d_distance_m` has no guard against `len(verts) == 0` → speculative — private helper, single call site, which iterates `out.edges` and trusts the stage-5 contract that every edge has a non-empty `vertices_resampled`. Same trust-the-contract policy as the rest of `pipeline/`. [edge]
- [x] [Review][Dismiss] `short_polyline_passthrough` asserts list-equality on tuples-of-floats → speculative future-refactor (e.g. `Decimal`) — for float-typed tuples list-equality IS bit-exact equality. The current production code only constructs floats. [edge]

## Dev Notes

- **Stage 6 is `(lat, lon)`-preserving by design.** `vertices_resampled` tuples are `(lat, lon, elevation_m)` (Story 2.3 sets that order explicitly). Stage 6 *only* touches the elevation component; the 2D position is the output of stages 3–4 and must not drift. Build the new tuple as `(lat_in, lon_in, smoothed_elev)` per vertex — explicit and audited by the AC-#3 axis-preservation test.
- **`d_minus_m` sign convention is positive magnitude.** Architecture §Cat 4 manifest example carries both as positive (`"d_plus_m": 2417.3, "d_minus_m": 2415.1`). `avg_gradient = (d_plus_m + d_minus_m) / length_m` is "total absolute altitude change per horizontal meter" — physically a steepness proxy, not a signed slope. Story 2.5's orchestrator + downstream stages 8 (climb detection) and validation depend on this convention.
- **`length_m` source — pick one and stick to it.** After stage 4 the `geometry` and `vertices_resampled` `(lat, lon)` agree exactly (Story 2.3 verified this). Both yield the same `length_m` within float tolerance via the local-equirectangular pattern from `smoothing._resample_meters`. Computing from `vertices_resampled` is slightly more self-contained (stage 7 doesn't need to touch `geometry`); computing from `geometry` reuses the existing meter-aware code in `smoothing.py` more directly. Either is fine — document the choice in the function docstring.
- **Module placement.** Architecture §Project Structure assigns stage 6 to `pipeline/smoothing.py` (same file as stages 3–4) and stage 7 to `pipeline/climbs.py` (new file; will also host stage 8 climb detection in Story 3.2). Don't pre-create stage-8 scaffolding — empty placeholder functions add noise.
- **Standard stage signature.** `def stage(input_graph, config) -> output_graph` (Architecture §Cat 3a). `median_smooth_elevation(graph, window: int = ELEVATION_MEDIAN_WINDOW)` keeps the window as a config kwarg with the module-constant default — mirrors Story 2.2's `resample_edges(graph, spacing_m=RESAMPLE_SPACING_M)`. `compute_edge_metrics(graph)` takes no config (no tunable knobs for stage 7).
- **Pure-function discipline.** No I/O, no module-level mutable state, no `print`. Both new functions return a fresh `MultiDiGraph` via `out = graph.copy(); ...; return out`. (`graph.copy()` deep-copies attribute dicts — list-valued `vertices_resampled` is replaced wholesale by stage 6, not mutated in-place; that keeps the input graph genuinely untouched.)
- **No carry-forwards.** Story 2.3's two deferred items (zero-as-void on `nodata=None`, inverted-bounds GeoTIFF diagnostic) are owned by Stories 2.8/2.9, not this one. Story 2.2's deferred item (`n_intervals` upper bound) is owned by Stories 2.5/2.8. Stage 5 inherits non-degenerate edges from stages 3–4 already.
- **Out of scope:**
    - Pipeline orchestrator wiring stages 1→7 — Story 2.5.
    - Cache key composition and on-disk write — Stories 2.6–2.7.
    - CLI wiring (`steeproute-setup --center ... --radius ... --dem-path ...`) — Story 2.8.
    - Stage 8 climb detection (also in `pipeline/climbs.py`) — Story 3.2. Don't pre-add a `detect_climbs` skeleton.
    - DEM source-unavailable / OSM-age warning — Story 2.9.

### Project Structure Notes

- **Extended:** `src/steeproute/pipeline/smoothing.py` — add `median_smooth_elevation` (stage 6) + its module-scope window constant. No changes to existing stages 3–4.
- **New:** `src/steeproute/pipeline/climbs.py` — `compute_edge_metrics` (stage 7) only; stage 8 lands in Story 3.2.
- **New:** `tests/unit/test_climbs.py` — mirrors `test_smoothing.py` / `test_dem.py` patterns.
- **Extended:** `tests/unit/test_smoothing.py` — add stage-6 tests alongside the existing stages-3–4 tests.
- No new runtime or dev deps anticipated. `statistics.median` is stdlib; `hypothesis` already a dev dep from Story 2.2.

### Testing standards summary

- Layer: stage-6 + stage-7 tests live in `tests/unit/` (Architecture §Cat 11e). No new integration-test file in this story — Story 2.5 owns end-to-end stages-1–7 integration in `tests/integration/test_pipeline_end_to_end.py`.
- Real-data primary, synthetic where mechanically necessary (Architecture §Cat 11b hybrid): one fixture-driven contract test running the full pipeline through stage 7, plus synthetic per-AC unit tests for analytical correctness.
- Coverage floor: 95% on `pipeline/smoothing.py` and `pipeline/climbs.py` (both pure-logic per Architecture §Cat 11e).
- Naming: `test_<unit>_<scenario>` (Architecture §Test organization). E.g. `test_median_smooth_elevation_spike_is_replaced_by_window_median`, `test_compute_edge_metrics_uphill_d_plus_matches_analytical`.
- Conventions inherited from Stories 2.1–2.3: absolute imports, PEP 604 unions, no `Any`, basedpyright per-file pragma if external surfaces leak Unknown, ruff-formatted.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 2.4"]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 3 — Data pipeline architecture] — pipeline-stage table (stage 6 = `pipeline/smoothing.py`, stage 7 = `pipeline/climbs.py`), stage signature, edge-attribute contract (`length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient` in stage 7)
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 4 — Cache architecture (manifest schema sample)] — `d_plus_m` / `d_minus_m` both positive in the on-disk manifest, fixing the sign convention
- [Source: _bmad-output/planning-artifacts/architecture.md §Implementation Patterns — Numerical and data discipline] — module-scope named constants, explicit float tolerances
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11e — Coverage targets] — 95% pure-logic floor for `pipeline/`
- [Source: src/steeproute/pipeline/smoothing.py:142-167] — `_moving_average` boundary-clamp pattern to mirror for `_moving_median`
- [Source: src/steeproute/pipeline/smoothing.py:170-214] — `_resample_meters` local-equirectangular pattern for cumulative 2D distance
- [Source: src/steeproute/pipeline/dem.py:96-138] — stage shape and `vertices_resampled` axis ordering (`(lat, lon, elevation_m)`)
- [Source: tests/unit/test_smoothing.py:36-50] — `_single_edge_graph` helper pattern to extend with elevation
- [Source: tests/unit/test_dem.py:373-387] — `fixture_pipeline_through_stage5` module-scoped fixture pattern to extend through stages 6–7

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/confident-bell-e8da18`).

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` for the corporate Netskope TLS-intercepting proxy.

**No new deps.** `statistics.median` is stdlib; `hypothesis` already a dev dep from Story 2.2.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 37 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 264 passed, 1 deselected in ~21s; coverage 96% overall
                                     - pipeline/climbs.py    95% (43 stmts; 2 defensive guards in
                                       is_valid_for_metrics — hypothesis strategy excludes
                                       len<2 and non-finite inputs)
                                     - pipeline/smoothing.py 99% (112 stmts; 1 pre-existing
                                       defensive guard in is_valid_polyline, unchanged from Story 2.2)
```

Live OSM test re-verified: `STEEPROUTE_USE_OS_TRUSTSTORE=1 uv run pytest -m live` → 1 passed.

### Completion Notes List

**Design decisions worth review attention:**

1. **`length_m` is computed from `vertices_resampled (lat, lon)`, not from `geometry`.** Both should agree to sub-‰ at this point in the pipeline (stage 5 sets `vertices_resampled` from `geometry` directly). Computing from `vertices_resampled` keeps stage 7 self-contained — it reads one field and writes four; it never touches `geometry`. The independent reference implementation in the test (`_expected_length_m`) uses the same local-equirectangular formula so the assertion is "stage 7 matches the analytical contract", not "stage 7 matches itself".

2. **`_EARTH_RADIUS_M` duplicated in `pipeline/climbs.py` instead of imported from `pipeline/smoothing.py`.** Each pipeline module is self-contained against a physical constant; importing an underscore-prefixed name across modules is uglier than the duplication. The value (6 378 137.0 m — WGS84 equatorial radius) is well-known and won't drift; a comment in `climbs.py` cross-references the smoothing.py definition so future maintainers see the relationship.

3. **`d_minus_m` is positive magnitude — explicit in the production docstring and asserted in `test_compute_edge_metrics_pure_downhill_uses_positive_magnitude_for_d_minus`.** Architecture §Cat 4 manifest sample carries both `d_plus_m` and `d_minus_m` as positive (2417.3 / 2415.1); `avg_gradient = (d_plus_m + d_minus_m) / length_m` is "total absolute altitude change per horizontal meter" — a steepness proxy, not a signed slope. Downstream stages 8 (climb detection) and validation depend on this convention.

4. **`ELEVATION_MEDIAN_WINDOW = 5`** smooths over ~50 m at the 10 m vertex spacing. That window absorbs single-pixel DEM artifacts (the 5 m IGN RGE ALTI grid produces 1-2 cell-scale spikes near steep terrain) while preserving ridge-scale relief. The boundary-clamp pattern from `_moving_average` is reused so window=5 is asymmetric near endpoints but symmetric for interior vertices ≥ 2 from each edge.

5. **`is_valid_for_metrics` promoted to public for the hypothesis test.** Same precedent as Story 2.2's `is_valid_polyline`. Lets the strategy filter (`hypothesis.assume`) and the production input-contract use the same predicate — no drift. The two uncovered defensive branches (`len < 2`, non-finite coord) are exercised only when hypothesis violates the contract; the strategy excludes those by construction, so they stay uncovered (95% coverage, matching the floor — same accepted pattern as Story 2.2's 98% on `smoothing.py`).

6. **`(lat, lon)` exact-equality assertion in stage 6.** `test_median_smooth_elevation_preserves_lat_lon_exactly` uses raw `==` on the lat/lon components (not `math.isclose`). Stage 6 is `(lat, lon)`-passthrough by design — there's no projection or roundtrip, just `(lat, lon, new_elev)`. Any future regression that accidentally introduced a float operation on the 2D components would fail this loudly.

7. **Gradient-plausibility threshold: 95% of edges < 80% gradient.** Smoke-only sanity check (not a strict geometric bound). 80% (4 in 5) is well above any sustained-trail gradient, but a 10 m edge with 8 m of relief is conceivable in genuine alpine terrain. The 95% threshold catches sign-flip / axis-swap / unit bugs in stages 6-7 without flapping on a few legitimately steep edges. Runs cleanly on the Grenoble fixture today.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `median_smooth_elevation` returns a new graph (`test_median_smooth_elevation_does_not_mutate_input`); `vertices_resampled (lat, lon)` exactly preserved (`test_median_smooth_elevation_preserves_lat_lon_exactly`); endpoints pinned (`test_median_smooth_elevation_monotone_remains_monotone`); module-scope `ELEVATION_MEDIAN_WINDOW: int = 5` asserted by `test_elevation_median_window_is_module_constant`. ✅
2. AC #2 — `compute_edge_metrics` returns a new graph (`test_compute_edge_metrics_does_not_mutate_input`); all four metrics attached as finite floats with sign invariants; attribute contract preserved (`test_compute_edge_metrics_preserves_attribute_contract`). ✅
3. AC #3 — Four stage-6 analytical tests (flat / monotone / spike / lat-lon preservation) all green. ✅
4. AC #4 — Four stage-7 analytical tests (flat / pure-uphill / pure-downhill / mixed up-down) all green; `length_m` matches independent equirectangular reference to `rel_tol=1e-6`. ✅
5. AC #5 — `fixture_pipeline_through_stage7` chains all seven stages on the Grenoble fixture; three integration assertions (full-contract, finite-and-signed, plausible-Alpine-gradients) all green. ✅
6. AC #6 — `test_compute_edge_metrics_property_metric_invariants` with 50 examples; `hypothesis.assume(is_valid_for_metrics(verts))` aligns strategy and production. ✅
7. AC #7 — All four CI gates green (see Debug Log References); coverage floors held; live OSM re-verified. ✅

### File List

**New:**
- `src/steeproute/pipeline/climbs.py` — `compute_edge_metrics` (stage 7) + `is_valid_for_metrics` public predicate + private `_cumulative_2d_distance_m` / `_elevation_gain_loss` helpers. ~50 logical lines + docstrings.
- `tests/unit/test_climbs.py` — 10 tests: 4 AC-#4 analytical metric tests, 2 pure-function/contract tests, 3 AC-#5 fixture pipeline tests, 1 AC-#6 hypothesis property test.

**Modified:**
- `src/steeproute/pipeline/smoothing.py` — added `median_smooth_elevation` (stage 6) + private `_moving_median` helper + module-scope `ELEVATION_MEDIAN_WINDOW: int = 5`. Updated module docstring to mention stage 6.
- `tests/unit/test_smoothing.py` — added 8 stage-6 tests (module-constant guard, flat / monotone / spike / lat-lon-preservation, no-mutate, attribute-contract, short-polyline passthrough) + extended imports.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.4 `backlog → ready-for-dev → in-progress → review`; dated comments added.

**Untouched (intentionally):**
- `src/steeproute/pipeline/__init__.py` — orchestrator wiring lands in Story 2.5.
- `src/steeproute/cli/setup.py` — `--dem-path` CLI wiring lands in Story 2.8.
- `src/steeproute/models.py` — no new dataclasses needed (per-edge metrics live on the graph itself, not on a structured stage I/O).

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-18 | Yann (Claude Opus 4.7) | Story 2.4 implemented: pipeline stage 6 (`median_smooth_elevation` in `src/steeproute/pipeline/smoothing.py`) — moving-median on the elevation component of `vertices_resampled`, with `(lat, lon)` passed through bit-for-bit and endpoint elevations pinned. New module-scope constant `ELEVATION_MEDIAN_WINDOW: int = 5`. Pipeline stage 7 (`compute_edge_metrics` in new `src/steeproute/pipeline/climbs.py`) — attaches `length_m` (cumulative 2D distance via local-equirectangular projection over `vertices_resampled (lat, lon)`), `d_plus_m` (sum of positive elevation deltas), `d_minus_m` (sum of `\|negative deltas\|`, positive magnitude per Architecture §Cat 4 manifest sample), and `avg_gradient = (d_plus_m + d_minus_m) / length_m`. `is_valid_for_metrics` predicate promoted public for hypothesis-test alignment. 18 new tests (8 stage-6 in `test_smoothing.py`; 10 stage-7 in new `test_climbs.py`, including 4 analytical + 3 integration-style stages-1-7 fixture + 1 hypothesis property test). No new runtime or dev deps. All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 264 passed (+18 from prior 246) at 96% overall coverage; `pipeline/climbs.py` at 95%, `pipeline/smoothing.py` at 99%. Live OSM test re-verified — no regression. | _pending_ |
| 2026-05-18 | Yann (Claude Opus 4.7) | bmad-code-review applied: 5 patches landed (P1 MED hypothesis lat/lon strategy narrowed from `[-1, 1]` to lat ∈ `[40, 50]` + lon ∈ `[-10, 10]` so cos-of-mean-latitude correction is meaningfully exercised and float-underflow → Inf-gradient regime is unreachable; P2 MED spike test extended to assert every interior index — boundary-clamp branches in `_moving_median` are now covered; P3 MED monotone test renamed `..._output_is_bounded_by_window` and tightened to assert each interior median is bounded by its input window's `[min, max]` — the actual median property the prior `>=` check trivialized; P4 LOW `flat_unchanged` switched from `math.isclose(abs_tol=1e-12)` to `==` since median of identical floats is bit-exact; P5 LOW `is_valid_for_metrics` predicate refactored from "any vertex differs from `verts[0]`" to "any consecutive `(lat, lon)` pair differs" — matches the docstring contract and structurally guarantees `length_m > 0`). 3 items deferred to Story 2.5 (production-side `length_m == 0` / non-finite-elev guard for loop-back & coincident-2D polylines; NaN-delta absorbed by strict `>` / `<` branches in `_elevation_gain_loss`; self-loop synthetic-test coverage gap). 14 dismissed — including 2 false-positive shallow-copy claims empirically disproved at the REPL (`networkx.MultiDiGraph.copy()` produces distinct edge dicts; `g.edges[0,1,0] is out.edges[0,1,0]` returns False). Acceptance Auditor returned 0 findings — all 7 ACs satisfied. All four CI gates green post-review: ruff, ruff format, basedpyright 0/0/0, pytest 264 passed (same count — all patches were test tightenings or in-place refactors) at 96% overall coverage; `pipeline/climbs.py` 95% (down 1 stmt to 42 after predicate refactor), `pipeline/smoothing.py` 99%. Live OSM re-verified — no regression. | _pending_ |
