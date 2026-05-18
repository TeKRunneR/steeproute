# Story 2.2: Implement pipeline stages 3–4 — 2D polyline smoothing and resampling

Status: done

## Story

As a developer,
I want `pipeline/smoothing.py` to smooth each edge's 2D polyline and resample it to a uniform vertex spacing,
so that downstream DEM sampling hits consistent, drift-dampened positions rather than raw OSM vertices (which contributes to the cliff-bias artifacts the PRD Known-Limitations section discusses).

## Acceptance Criteria

1. `pipeline/smoothing.py` defines `smooth_polylines(graph) -> MultiDiGraph` (stage 3: moving-average smoothing on each edge's 2D polyline, preserving endpoints) and `resample_edges(graph, spacing_m: float = 10.0) -> MultiDiGraph` (stage 4: replaces each edge's `geometry` with a polyline whose interior vertices are at ~`spacing_m` spacing, endpoints unchanged). Both pure: no global state, no `print`/I/O, return a new graph — input never mutated. Edge attribute contract from Story 2.1 (`sac_scale`, `highway`, `osm_way_id`, `geometry`) is preserved across both stages on every output edge.
2. Smoothing window size and default resample spacing are **module-scope named constants** (no inline magic numbers per Architecture §Numerical and data discipline). Constants are referenced by both production code and tests so drift between them is impossible.
3. Resampled (and smoothed) edge endpoints **exactly match** input endpoints — topology preserved, no node-coordinate drift. Asserted on both synthetic crafted polylines and against every edge of the committed real-OSM fixture.
4. Coincident-endpoint / degenerate-geometry edges (zero-length `LineString`, `u == v`, or every coordinate identical) are handled deterministically: either skipped (edge dropped from the output graph) or substituted with a stable representation that downstream stages 5–7 won't divide-by-zero on. Pick one policy, document it in the function docstring, and cover with a unit test. (Carry-forward from Story 2.1 review — see Dev Notes.)
5. `tests/unit/test_smoothing.py` covers analytical correctness on synthetic polylines:
    - Straight-line input → unchanged by `smooth_polylines` (or differs only within float tolerance).
    - Noisy zigzag input → `smooth_polylines` reduces the polyline's max perpendicular distance from the straight-line baseline (L2 distance from u→v) compared to the input.
    - `resample_edges` produces vertices whose consecutive-pair spacing is within a documented tolerance band of `spacing_m` (interior-pair spacing — the final pair adjacent to the endpoint may differ).
    - Endpoint preservation: first and last coords of every output edge equal the corresponding input edge's first and last coords exactly.
6. `tests/unit/test_smoothing.py` also runs `smooth_polylines` then `resample_edges` over the committed real-OSM fixture (`tests/fixtures/grenoble_small/osm_graph.graphml`) and asserts the attribute contract is preserved on every output edge: `geometry` is a non-empty `shapely.LineString`, `sac_scale` / `highway` / `osm_way_id` carried through unchanged.
7. A `hypothesis` property-based test in `tests/unit/test_smoothing.py` asserts that for any valid input polyline (≥ 2 distinct points, finite coords), the resampled output has its first and last coordinates exactly equal to the input's first and last coordinates.
8. All four CI gates pass on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov`. `hypothesis` added to dev deps.

## Tasks / Subtasks

- [x] **Task 1: Implement `smooth_polylines` and `resample_edges`** (AC: #1, #2, #3, #4)
    - [x] Create `src/steeproute/pipeline/smoothing.py`. Stage signature `def stage(input_graph) -> output_graph` per Architecture §Cat 3 — return a new `MultiDiGraph`, never mutate input (mirror the pattern in `pipeline/osm.py::filter_trails`).
    - [x] Module-scope constants for the moving-average window size and default resample spacing (`spacing_m = 10.0`). No inline magic numbers anywhere in this module.
    - [x] Endpoints preserved exactly: first and last coordinates of every output `LineString` equal the input's first and last (regardless of any meter-aware transform used internally). This includes the smoothing pass — moving-average naturally fixes endpoints if the window is applied symmetrically.
    - [x] Decide and document the degenerate-edge policy (AC #4): zero-length geometry, coincident endpoints, all-identical-coords. Skip-and-drop is the lowest-risk default (matches `filter_trails`' "drop edges that fail a contract" pattern) and lets stages 5–7 assume non-degenerate geometry. Whatever you pick, the docstring states it and a test exercises it.
    - [x] basedpyright per-file pragma at module top mirroring `pipeline/osm.py`'s header — networkx + osmnx + shapely operations surface as Unknown otherwise. Same pattern in `tests/unit/test_smoothing.py`.
- [x] **Task 2: Unit tests against synthetic + fixture** (AC: #5, #6, #7)
    - [x] `tests/unit/test_smoothing.py` — analytical synthetic tests (straight line invariant, zigzag-noise reduction by L2-distance-from-baseline, resample spacing within tolerance, endpoint preservation).
    - [x] Module- or session-scoped fixture loading the committed real-OSM graph the same way `tests/unit/test_osm.py` does it (use `osmnx.load_graphml` + the existing `normalize_edges` helper). Run stages 3 then 4 on the fixture and assert attribute-contract preservation on every output edge.
    - [x] `hypothesis` property test for endpoint preservation under `resample_edges`. Strategy: build a `shapely.LineString` from a `hypothesis.strategies.lists` of finite (lat, lon) tuples ≥ 2 distinct points. Cap point count modestly (e.g. `max_size=20`) to keep test runtime sane.
    - [x] Test naming `test_<unit>_<scenario>` per Architecture §Test organization (e.g. `test_smooth_polylines_straight_line_unchanged`, `test_resample_edges_endpoints_match_input`).
- [x] **Task 3: Wire dev dep + verify CI** (AC: #8)
    - [x] Add `hypothesis` to `[dependency-groups].dev` in `pyproject.toml`. `uv lock` + `uv sync`.
    - [x] `uv run ruff check && uv run ruff format --check && uv run basedpyright && uv run pytest --cov`. All green.

### Review Findings

_From `bmad-code-review` 2026-05-07. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor)._

**Decisions resolved 2026-05-07:**

- [x] [Review][Decision] **D1: Silent drop of non-LineString geometry in `_extract_coords`** → resolved as **option 3** (raise exception). Replace the silent `return []` for non-LineString with a `TypeError` naming the offending edge and the actual geometry type. This is a fail-fast contract guard at the stage boundary; aligns with the Architecture §Key anti-patterns "silent broad except" prohibition's spirit (silent acceptance is symmetric to silent suppression). osmnx 2.x reliably produces LineString for our pipeline, so the path stays dormant against the fixture; if upstream ever produces a different geometry type, the failure is loud and located. Patch P0 below. [Source: blind+edge]

**Patch (unambiguous fixes):**

- [x] [Review][Patch] **P0 (from D1): Raise `TypeError` on non-LineString geometry instead of silent drop** — replace `_extract_coords`'s `return []` for non-LineString input with `raise TypeError(f"pipeline.smoothing: edge geometry must be a shapely.LineString, got {type(geom).__name__}")`. Update the call sites in `smooth_polylines` / `resample_edges` to either (a) let the exception propagate (pure fail-fast) or (b) catch and wrap with edge-key context (`raise TypeError(...) from e` after appending the offending edge tuple) — pick (a) for minimal surface; the message already names the geometry type, and the orchestrator stack will show the iteration point. Update the docstrings to state that non-LineString geometry is a contract violation. Add a unit test that constructs an edge with a `shapely.MultiLineString` (or `None`) geometry and asserts `TypeError` is raised. [src/steeproute/pipeline/smoothing.py:127-131] [Source: D1 resolution]
- [x] [Review][Patch] **P1 (HIGH): `_extract_coords` raises `ValueError` on 3D LineString** — `for x, y in geometry.coords` unpacks coord tuples; if a `shapely.LineString` carries z-components (3-tuples), unpacking raises `ValueError: too many values to unpack`. Story 2.1's `normalize_edges` produces 2D LineStrings, but no defense-in-depth here means a future change upstream could crash the whole stage abruptly. Strip to 2D consistently: `[(float(c[0]), float(c[1])) for c in geometry.coords]`. [src/steeproute/pipeline/smoothing.py:127-131] [Source: edge]
- [x] [Review][Patch] **P2 (MED): AC #6 fixture contract test only checks key presence, not "carried through unchanged"** — AC #6 explicitly says `sac_scale` / `highway` / `osm_way_id` are "carried through unchanged"; the fixture test asserts only key existence, so a buggy stage that silently rewrote `highway` from `"path"` to `None` would still pass. Snapshot input edge attrs before stages 3 → 4 and compare to output values per edge. [tests/unit/test_smoothing.py:514-516] [Source: auditor]
- [x] [Review][Patch] **P3 (LOW): `_moving_average` docstring says "symmetric" but isn't for window > 3** — Boundary clamping (`lo = max(0, i - half)`, `hi = min(n, i + half + 1)`) produces asymmetric windows near endpoints whenever window > 3. Currently dormant (`SMOOTHING_WINDOW = 3`), but the docstring promise will silently break if the constant is bumped. Either tighten the docstring to acknowledge boundary clamping, or require `window` to be odd ≥ 1 with an `assert`. [src/steeproute/pipeline/smoothing.py:144-162] [Source: blind+edge]
- [x] [Review][Patch] **P4 (LOW): Hypothesis filter doesn't align with `_is_valid_polyline`** — Strategy filter uses `round(_, 9)` for distinctness, production check uses raw float equality. The mismatch lets some "valid by strategy" inputs reach the early-return branch (`if (0, 1, 0) not in out.edges: return`) instead of the property assertion, silently weakening the test. Replace the filter with `hypothesis.assume(_is_valid_polyline(coords))` so strategy and property test agree on what's valid. [tests/unit/test_smoothing.py:540-556] [Source: blind+edge+auditor]
- [x] [Review][Patch] **P5 (LOW): AC #5 spacing tolerance asserted but never documented** — AC #5 third bullet calls for "documented tolerance band". Test uses `rel_tol=1e-3` and `abs_tol=0.5` without explaining why; production module docstring is silent on the spacing-tolerance contract. Add a one-line note in the production docstring (uniform-by-construction; round-trip drift typically < 1‰) and a one-line comment in the test naming the rationale. [src/steeproute/pipeline/smoothing.py:30-41; tests/unit/test_smoothing.py:356-374] [Source: auditor]
- [x] [Review][Patch] **P6 (LOW): Float-precision drift can produce `t > 1` in `_resample_meters` tail interpolation** — When accumulated `actual_spacing * i` slightly exceeds `cumulative[-1]` due to roundoff, the segment-walk clamps to the last segment but `t = (d - cumulative[seg]) / seg_len` can compute > 1, extrapolating the interior vertex past v's projected location (a non-monotone "bulge" at the tail). Final vertex is still pinned to `coords[-1]`, but interior monotonicity could break under unlucky float arithmetic. Clamp: `t = max(0.0, min(1.0, ...))`. [src/steeproute/pipeline/smoothing.py:197-203] [Source: edge]

**Deferred (real but out of scope or owned elsewhere):**

- [x] [Review][Defer] **No upper bound on `n_intervals` — pathological `total / spacing_m` could blow memory/CPU** — For a hypothetical 1000-km polyline at 0.001-m spacing, `n_intervals ≈ 10⁹`. No path produces this combination today (`--area-cap` bounds polyline length upstream; spacing is the internal default constant). Add a sanity ceiling when CLI exposes spacing override or in Story 2.5's orchestrator. [src/steeproute/pipeline/smoothing.py:190] [Source: edge]

**Dismissed (noise / false positive / handled elsewhere):**

- [x] No type-guard for `MultiDiGraph` (DiGraph would unpack-fail) — internal-pipeline contract; CLAUDE.md "trust internal code, only validate at system boundaries". [edge]
- [x] Antimeridian crossing produces nonsense — Grenoble Alps fixture at lon ≈ 5.85°; `osm_load` already validates lon ∈ [-180, 180] for area center. [edge]
- [x] Polar-latitude collapse (`cos(mean_lat) → 0`) — Grenoble at lat ≈ 45°; not reachable. [edge]
- [x] Test `interior-pair spacing` covers all pairs (AC parenthetical anticipated tail-pair drift) — implementation makes all pairs uniform by construction (`total / n_intervals`); loosening would just permit drift. [auditor]
- [x] Fixture endpoint test compares to node coords, not "input edge endpoints" — Acceptance Auditor concedes this is a stronger property; matches the AC's "no node-coordinate drift" rationale. [auditor]
- [x] Zero-length sub-segment mid-polyline biases interpolated vertex — both reviewers concluded "subtle but correct"; vertex lands on the same point either way. [blind+edge]
- [x] Test builds `LineString` from NaN coords (shapely undefined behavior) — test suppresses the warning, behavior verified; coupling acceptable. [blind]
- [x] No fixture checksum verification — fixture-size CI gate from Story 2.1 catches bloat; corrupted-file failure mode is loud enough via `osmnx.load_graphml`. [blind]
- [x] `graph.copy()` shallow-copies edge attribute dicts; list-valued Story 2.1 attrs aliased — current code only replaces `data["geometry"]`, doesn't mutate list values; test verifies non-mutation; future-stage concern. [blind+edge]
- [x] Multigraph parallel edges sharing same geom dict — contrived; standard osmnx output doesn't share dicts across edges. [edge]

## Dev Notes

- **Coordinate units & meter-aware operations.** Edge `geometry` from stages 1–2 is `shapely.LineString` in **WGS84 lon/lat** (osmnx convention). `spacing_m=10.0` is metric — degree-distance is not equal to meter-distance and varies with latitude. The dev needs meter-aware spacing without permanently re-projecting the graph (Story 2.3 keeps WGS84 in the graph and handles CRS at the DEM boundary). Two reasonable approaches:
    - **Per-edge local projection** — for each edge, project its coords to UTM (zone derived from edge's mean longitude) via `pyproj.Transformer` (transitive dep through osmnx), do meter-based smoothing/resampling, project back. Adds a transform per edge but keeps the graph in WGS84 throughout.
    - **`osmnx.projection.project_graph`** — project the whole graph to UTM up front, run smoothing + resampling in meters on the projected geometry, then `project_graph(..., to_latlong=True)` back. Single round-trip but mutates node coords too (osmnx replaces `x`/`y` with projected values and preserves the back-projection). Read the osmnx 2.x docs to confirm both directions round-trip cleanly enough that endpoints stay exact.
    - Pick whichever is cleaner; document the choice in a one-line module docstring. Endpoint-exactness (AC #3) drives the test — float wobble from a round-trip projection might surface there, in which case prefer the per-edge approach with explicit endpoint pinning.
- **Carry-forward — degenerate-edge handling (deferred from Story 2.1).** Story 2.1's `normalize_edges` synthesizes `LineString([u_xy, v_xy])` for edges where osmnx omits geometry, including the `u == v` self-loop case (zero-length). Stages 3–4 do polyline math (perpendicular-distance, vertex-spacing division) where zero-length input may divide-by-zero or produce empty output. Story 2.1 review explicitly defers the policy decision to this story (see [deferred-work.md §"Deferred from: code review of 2-1-…"](_bmad-output/implementation-artifacts/deferred-work.md) item 4). Default recommendation: skip degenerate edges from the output graph (drop them, mirroring `filter_trails`' edge-dropping pattern). Cover with `test_smooth_polylines_drops_degenerate_geometry` or equivalent.
- **Stage signature note.** The epic's stage 4 signature in epics.md is `resample_edges(graph, spacing_m=10.0)`. Architecture §Cat 3 stage convention is `def stage(input, config) -> output` — `spacing_m` is the config parameter. Keep it as a positional kwarg with default; orchestrator wiring (Story 2.5) can override per-call when a config object lands. Stage 3 (`smooth_polylines`) takes only the graph in the epic; if window size needs to be configurable later, it can be added without breaking AC #2 (the constant lives in the module either way).
- **Pure function discipline.** No `print`, no I/O, no module-level side effects (Architecture §Key anti-patterns). Caller (orchestrator in Story 2.5) owns inputs; this module returns fresh graphs. Mirror the `pipeline/osm.py::filter_trails` shape: `out = graph.copy(); ...; return out`. (`graph.copy()` deep-copies node + edge attrs — `LineString` instances are immutable enough that the shallow copy is safe to overwrite.)
- **Hypothesis library.** New dev dep. Standard idiom — `from hypothesis import given, strategies as st`. No need for `@settings` overrides unless a property test runs slow (cap `max_size` instead). Hypothesis adds a `.hypothesis/` cache directory — should already be excluded by ruff/pytest since it's hidden, but verify it doesn't pollute `git status`; add to `.gitignore` if needed (one line, follow the `cache/` + `.review-tmp/` precedent set in Story 2.1's review).
- **Out of scope:**
    - Stage 5 DEM sampling (Story 2.3) — `vertices_resampled` attribute is added there, not here. After stage 4, edges still have `geometry` only.
    - Elevation moving-median (stage 6, Story 2.4) — same module (`smoothing.py`) but different function (`median_smooth_elevation`).
    - Per-edge metrics (stage 7, Story 2.4).
    - Pipeline orchestrator wiring (Story 2.5).
    - CRS handling between WGS84 and DEM-native CRS (Story 2.3).

### Project Structure Notes

- New production module: `src/steeproute/pipeline/smoothing.py`. Architecture project tree (§Project Structure) reserves it for "stages 3–4, 6"; this story implements 3–4 only — leave room (no premature `median_smooth_elevation` placeholder).
- New test file: `tests/unit/test_smoothing.py`.
- `pyproject.toml`: add `hypothesis` to `[dependency-groups].dev`. No new runtime deps (pyproj is transitive through osmnx; shapely already runtime).
- Fixture is reused, not duplicated — `tests/fixtures/grenoble_small/osm_graph.graphml` is committed by Story 2.1.

### Testing standards summary

- Layer split (Architecture §Cat 11e): all tests for stages 3–4 live in `tests/unit/`. No new integration test in this story — Story 2.5 owns the end-to-end stages-1–7 fixture run.
- Fixture loading: use `osmnx.load_graphml` + `pipeline.osm.normalize_edges` (the same pattern as `tests/unit/test_osm.py`) so the in-test graph object matches what `osm_load` produces.
- Naming: `test_<unit>_<scenario>` (Architecture §Test organization).
- Conventions inherited from Story 2.1: absolute imports, PEP 604 unions, no `Any` (or one short comment if unavoidable at the osmnx/shapely boundary), basedpyright per-file pragma for the unknown-types-from-osmnx footprint, ruff-formatted.
- Coverage: pure-logic module → 95% floor (Architecture §Cat 11e). `pipeline/smoothing.py` is pure logic and CI's coverage `omit` doesn't list it.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 2.2"]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 3 — Data pipeline architecture] — pipeline-stage table, stage signature `def stage(input, config) -> output`, edge-attribute contract
- [Source: _bmad-output/planning-artifacts/architecture.md §Implementation Patterns — Numerical and data discipline] — module-scope named constants, no inline magic numbers
- [Source: _bmad-output/planning-artifacts/architecture.md §Key anti-patterns to avoid] — no top-level side effects, no module-level mutable state
- [Source: _bmad-output/planning-artifacts/architecture.md §Test organization, §Cat 11e] — three-layer split, naming, 95% pure-logic coverage floor
- [Source: _bmad-output/planning-artifacts/prd.md §Data pipeline] — "DEM-resample + 2D polyline smoothing + moving-median on elevation" as the cliff-bias mitigation stack; explains *why* this story exists
- [Source: _bmad-output/implementation-artifacts/deferred-work.md §"Deferred from: code review of 2-1-…" item 4] — coincident-endpoint / zero-length-LineString handling owned by this story
- [Source: src/steeproute/pipeline/osm.py] — stage-signature pattern (`out = graph.copy(); ...; return out`), basedpyright per-file pragma, module-scope constants
- [Source: tests/unit/test_osm.py:25-46] — fixture-loading + module-scope-fixture pattern to mirror

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/pensive-archimedes-441c58`).

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` for the corporate Netskope TLS-intercepting proxy.

**New dev dep:** `hypothesis 6.152.4` (transitive: `sortedcontainers 2.4.0`). No new runtime deps — equirectangular projection is `math.cos`/`math.radians` only; `pyproj` (transitive via osmnx) was not needed.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 32 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 224 passed, 1 deselected in 21.91s; coverage 95% overall
                                     - pipeline/smoothing.py 98% (87 stmts; 2 defensive guards uncovered:
                                       non-LineString geometry branch and len < 2 branch — both
                                       belt-and-braces against contract violations from upstream)
```

### Completion Notes List

**Design decisions / divergences worth review attention:**

1. **Coordinate handling: per-edge local equirectangular projection** (story Dev Notes flagged the choice). Each edge's polyline is projected to local meters using `cos(mean_lat)` longitude scaling and the WGS84 equatorial-radius lat scaling (`_EARTH_RADIUS_M = 6_378_137.0`). Meter-aware smoothing/resampling runs in the projected XY space; output is projected back to lon/lat. Endpoints are pinned exactly to the input's first/last coords after the round-trip — float wobble from the inverse projection is bypassed at the boundary. Accuracy is ~0.1% over edge-scale distances (tens of meters to a few km), more than enough for 10-m spacing on Alpine trails. This avoided pulling in `pyproj.Transformer` per edge (heavy) and avoided `osmnx.projection.project_graph` (mutates node x/y graph-wide, harder to roll back).

2. **Degenerate-edge policy: drop from output** (carry-forward from Story 2.1 review item 4). `_is_valid_polyline` rejects: fewer than 2 coords, any non-finite coord, or all coords identical. Dropped edges are removed from the output graph; orphan-node pruning is left to Story 2.5's orchestrator (matches deferred-work.md item 5 from Story 2.1). Documented in both `smooth_polylines` and `resample_edges` docstrings; tested via `test_*_drops_zero_length_edge`, `test_*_drops_edge_with_all_identical_coords`, `test_*_drops_edge_with_non_finite_coord`. `resample_edges` also rejects non-positive or non-finite `spacing_m` at the API boundary (`ValueError`, not `BadCLIArgError` — this is an internal contract, not a CLI arg; CLI plumbing in Story 2.5/2.8 will surface user-facing errors at its own boundary).

3. **`SMOOTHING_WINDOW = 3`**: minimal symmetric moving average. Window=3 means each interior vertex becomes the mean of itself and its two neighbours; endpoints pinned. Larger windows are a tunable knob but 3 already passes the AC #5 zigzag-reduction test and stays close to the input shape (the cliff-bias mitigation is the *combination* of stages 3+4+6, not stage 3 alone). The `_moving_average` helper uses `window // 2` as the half-width so a future bump to e.g. WINDOW=5 needs no logic changes — just bump the constant.

4. **Hypothesis property test scope**: 50 examples, `max_size=10`, coord range `[-1.0, 1.0]`, `spacing_m=1000.0`. Constrained to keep test runtime modest while still hitting non-trivial polylines. Filter ensures ≥ 2 distinct points after rounding (so hypothesis-generated near-duplicates from float jitter don't degenerate to a no-op). The property is endpoint-exact preservation under `resample_edges`; it has held over 50 examples per run.

5. **`.hypothesis/` already in `.gitignore`** (line 64, predates this story). Added `.hypothesis` to `[tool.pytest.ini_options].norecursedirs` so the hypothesis pytest plugin stops emitting its "you're shadowing our default ignores" UserWarning. No effect on test behavior.

6. **Coverage on `pipeline/smoothing.py` is 98%, above the 95% pure-logic floor.** The two uncovered lines are defensive guards in `_extract_coords` (returns `[]` for non-LineString geometry) and `_is_valid_polyline` (returns `False` for `len < 2`). Both are unreachable under the Story 2.1 attribute contract; left in as belt-and-braces in case a future stage produces non-conformant edges. Padding tests just to hit them would not improve correctness signal.

7. **Stage signature**: `smooth_polylines(graph)` takes only the graph (no config) — matches the epic spec. `resample_edges(graph, spacing_m=10.0)` keeps `spacing_m` as a kwarg with default; the orchestrator (Story 2.5) can override at the call site without breaking the AC #2 module-constant claim (the constant is the default value referenced by both production code and tests, no drift possible).

**AC walkthrough — evidence per criterion:**

1. AC #1 — `pipeline/smoothing.py` defines `smooth_polylines(graph) -> MultiDiGraph` and `resample_edges(graph, spacing_m=10.0) -> MultiDiGraph`. Both pure (verified by `test_*_does_not_mutate_input`). Attribute contract preserved (verified by `test_*_preserves_attribute_contract` + `test_fixture_smoothed_then_resampled_preserves_contract` over the real fixture). ✅
2. AC #2 — `SMOOTHING_WINDOW: int = 3` and `RESAMPLE_SPACING_M: float = 10.0` at module scope. `test_smoothing_window_is_module_constant` and `test_resample_spacing_default_is_module_constant` assert presence + values. The default kwarg `resample_edges(spacing_m=RESAMPLE_SPACING_M)` ties production and test to the same constant — drift impossible. ✅
3. AC #3 — Endpoints exactly preserved: `test_smooth_polylines_preserves_endpoints_exactly`, `test_resample_edges_endpoints_match_input_exactly`, plus the fixture-wide `test_fixture_pipeline_endpoints_match_node_coords` asserting `coords[0] == nodes[u]` and `coords[-1] == nodes[v]` on every fixture edge after stages 3 → 4. ✅
4. AC #4 — Degenerate edges dropped, policy documented in both function docstrings. Tests: `test_smooth_polylines_drops_zero_length_edge`, `test_resample_edges_drops_zero_length_edge`, `test_smooth_polylines_drops_edge_with_all_identical_coords`, `test_resample_edges_drops_edge_with_non_finite_coord`, `test_smooth_polylines_keeps_valid_edges_when_one_is_degenerate` (multi-edge graph: dropping one degenerate doesn't affect surviving edges). ✅
5. AC #5 — `test_smooth_polylines_straight_line_unchanged` (equally-spaced collinear input → output equal within `abs_tol=1e-12`), `test_smooth_polylines_zigzag_reduces_perpendicular_drift` (max |y| reduced from 1e-5 to ~3.3e-6 on the 6-point zigzag), `test_resample_edges_uniform_spacing_within_tolerance` (consecutive distances within `rel_tol=1e-3` of mean and within `abs_tol=0.5 m` of 10 m for a ~111 m polyline). Endpoint preservation tested separately under AC #3. ✅
6. AC #6 — `test_fixture_smoothed_then_resampled_preserves_contract` runs stages 3 → 4 over the committed real-OSM fixture; asserts `geometry` is non-empty `LineString` and contract keys present on every output edge. ✅
7. AC #7 — `test_resample_edges_property_endpoints_exact` is `@given`-decorated with `max_examples=50`. Filter ensures ≥ 2 distinct points after rounding to handle hypothesis's float-precision near-duplicates. Property holds across all generated examples in CI. ✅
8. AC #8 — All four CI gates green (see Debug Log References). `hypothesis>=6.0` added to `[dependency-groups].dev`. ✅

### File List

**New:**
- `src/steeproute/pipeline/smoothing.py` — `smooth_polylines` (stage 3) + `resample_edges` (stage 4) + helpers `_extract_coords`, `_is_valid_polyline`, `_moving_average`, `_resample_meters`. Module-scope constants `SMOOTHING_WINDOW`, `RESAMPLE_SPACING_M`, `_EARTH_RADIUS_M`. ~85 logical lines + docstrings.
- `tests/unit/test_smoothing.py` — 22 tests: 2 module-constant guards, 5 smoothing analytical/contract tests, 8 resampling analytical/contract tests, 4 degenerate-edge tests, 1 multi-edge isolation test, 2 fixture-driven contract tests, 1 hypothesis property test.

**Modified:**
- `pyproject.toml` — added `hypothesis>=6.0` to `[dependency-groups].dev`; added `.hypothesis` to `[tool.pytest.ini_options].norecursedirs` (silences the hypothesis pytest plugin's warning about shadowed default ignores).
- `uv.lock` — regenerated; `+ hypothesis 6.152.4`, `+ sortedcontainers 2.4.0`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.2 status `ready-for-dev → in-progress → review`; dated comments added.

**Untouched (intentionally):**
- `src/steeproute/pipeline/__init__.py` — orchestrator wiring lands in Story 2.5; placeholder docstring already covers stages 1-9.
- `src/steeproute/pipeline/osm.py` — Story 2.1 surface; this story only consumes its `normalize_edges` helper from tests, no edits.
- `src/steeproute/models.py` — no new dataclasses needed (`spacing_m` is a kwarg, not a config object).

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-07 | Yann (Claude Opus 4.7) | Story 2.2 implemented: pipeline stages 3–4 (`smooth_polylines` + `resample_edges`) in `src/steeproute/pipeline/smoothing.py`. Per-edge local equirectangular projection (`cos(mean_lat)` lon scaling) for meter-aware spacing without `pyproj`/`project_graph` round-trips. Endpoints pinned exactly across both stages. Degenerate edges (zero-length, all-identical coords, non-finite coords) dropped from the output graph — closes deferred item 4 from Story 2.1's review. 22 unit tests (analytical, fixture-driven, multi-edge, hypothesis property test for endpoint exactness with 50 examples). New dev dep `hypothesis>=6.0`; no new runtime deps. All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 224 passed (22 new + 202 prior, +1 live deselected) at 95% overall coverage; pipeline/smoothing.py at 98%. | _pending_ |
| 2026-05-07 | Yann (Claude Opus 4.7) | bmad-code-review applied: 1 decision resolved (D1: silent drop of non-LineString geometry → raise `TypeError` fail-fast); 7 patches landed (P0+P1: `_extract_coords` raises `TypeError` for non-LineString and strips 3D LineStrings to 2D; P2: fixture contract test snapshots input attrs and verifies "carried through unchanged"; P3: `_moving_average` docstring rewritten + `assert window % 2 == 1`; P4: `_is_valid_polyline` promoted to public `is_valid_polyline` so the hypothesis test uses `assume(is_valid_polyline(coords))` instead of a rounding-based filter; P5: spacing-tolerance contract documented in module docstring + test comment; P6: `t = max(0.0, min(1.0, t))` clamp in `_resample_meters` against tail-bulge from float drift). 1 item deferred (`n_intervals` upper bound — owned by Story 2.5/2.8). 10 items dismissed as noise/false-positive/handled. 3 new tests added (TypeError on non-LineString, TypeError on missing geometry, 3D LineString → 2D strip). All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 227 passed (+3 from review) at 95% overall coverage; pipeline/smoothing.py at 99%. | _pending_ |
