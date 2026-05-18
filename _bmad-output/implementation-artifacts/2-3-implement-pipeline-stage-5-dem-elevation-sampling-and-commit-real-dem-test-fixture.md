# Story 2.3: Implement pipeline stage 5 — DEM elevation sampling and commit real-DEM test fixture

Status: done

## Story

As a developer,
I want `pipeline/dem.py` to sample elevation from a local DEM GeoTIFF at every resampled vertex and attach the result as a `vertices_resampled` attribute on each edge, plus a committed real IGN DEM fixture covering the same area as the OSM fixture,
so that elevation data is in the graph for the downstream smoothing and metrics stages (6–7), and tests exercise real Alpine terrain characteristics rather than synthetic surfaces.

## Acceptance Criteria

1. `pipeline/dem.py` defines `sample_elevation(graph, dem_path) -> MultiDiGraph` (stage 5) using `rasterio`. Pure: no global state, no `print`, returns a new graph — input never mutated. Every output edge carries `vertices_resampled: list[tuple[float, float, float]]` whose entries are `(lat, lon, elevation_m)` (note: **lat first**, opposite of shapely's `geometry` convention which is `(lon, lat)`). The full attribute contract from Stories 2.1–2.2 (`geometry`, `sac_scale`, `highway`, `osm_way_id`) is preserved unchanged on every output edge.
2. CRS handling between the graph's WGS84 lon/lat and the DEM's native CRS is **explicit**: the DEM's CRS is read from the raster (`rasterio.open(...).crs`); vertices are transformed via `pyproj.Transformer` (transitive dep through `rasterio` / `osmnx`) before sampling, and elevation values are returned in meters. Module-scope named constants for the WGS84 EPSG (`EPSG:4326`) and any default DEM CRS (no inline magic strings per Architecture §Numerical and data discipline).
3. Vertices that fall outside the DEM raster's coverage raise a `PreExecutionError` subclass — `DEMCoverageError` (new, added to `errors.py`) — whose `user_message` names at least one offending edge (`u`, `v`, `key`) and the DEM's bounds. **No silent NaN**: any returned elevation must be a finite float. Sampling at a vertex over a DEM nodata pixel is treated the same as out-of-bounds.
4. `tests/fixtures/grenoble_small/dem.tif` is committed: an IGN RGE ALTI 5 m extract covering at least the same bbox as the OSM fixture (Le Sappey-en-Chartreuse, ±2 km bbox half-side around `45.260, 5.788`). The `tests/fixtures/grenoble_small/README.md` is extended with a `dem.tif` section documenting source, CRS, resolution, bounds, capture date, and the regeneration path. Committed `dem.tif` < 5 MB; the size guard added in Story 2.1's review is extended to cover the new fixture file.
5. `tests/unit/test_dem.py` runs `sample_elevation` over the smoothed-and-resampled real-OSM fixture (stages 1→2→3→4→5 chained) and asserts:
    - Every output edge has the full attribute contract (carried-through values match input exactly; `vertices_resampled` populated with the same number of entries as `len(geometry.coords)` per edge and matching `(lat, lon)` to the geometry within float tolerance).
    - At least one known-elevation assertion: 2–3 hand-picked coordinates inside the fixture bbox (e.g. trailheads, a summit visible on the IGN topo viewer) sampled directly via `sample_elevation` over a single-edge synthetic graph; asserted within a documented tolerance (e.g. ±10 m, with rationale).
    - DEM elevations across the fixture are physically plausible Alpine values (all finite, all within a sane range e.g. `200 m ≤ elev ≤ 3000 m` for Chartreuse; tighten if data supports it).
6. `tests/unit/test_dem.py` includes a CRS-transformation correctness test using a **synthetic in-memory GeoTIFF** with a non-WGS84 CRS (constructed via `rasterio.io.MemoryFile`, e.g. UTM or Lambert-93). The test seeds known elevation values at known projected coordinates, samples a graph edge whose WGS84 endpoints project to those coordinates, and asserts the recovered elevations equal the seeded values within float tolerance. This guarantees the CRS-aware transform path is exercised in CI even if `dem.tif` happens to ship in WGS84.
7. `tests/unit/test_dem.py` covers out-of-bounds behavior: a synthetic single-edge graph positioned deliberately outside the fixture's bounds raises `DEMCoverageError` whose `user_message` names the offending edge and the DEM bounds (a `pytest.raises(DEMCoverageError, match=...)` pattern is sufficient). A second test covers DEM-nodata cells (synthetic in-memory GeoTIFF with an embedded nodata pixel under a vertex) — same `DEMCoverageError` path.
8. All four CI gates pass on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov`. New runtime dep `rasterio` added to `pyproject.toml`. `pipeline/dem.py` clears the 95% pure-logic coverage floor (Architecture §Cat 11e). basedpyright per-file pragma matching the `pipeline/osm.py` / `pipeline/smoothing.py` precedent if rasterio surfaces `Unknown` types.

## Tasks / Subtasks

- [x] **Task 1: Acquire and commit `dem.tif` fixture** (AC: #4)
    - [x] Obtained IGN RGE ALTI HIGHRES (5 m native) via the IGN Géoplateforme WMS endpoint (`data.geopf.fr/wms-r/wms`) using `format=image/x-bil;bits=32` for raw IEEE-754 float32. Endpoint is open data — no API key needed. Documented in `tests/fixtures/grenoble_small/README.md`.
    - [x] Wrote a fully-automated `tests/fixtures/grenoble_small/regenerate_dem.py` — requests the bbox in WGS84 directly (IGN reprojects server-side), decodes the BIL payload as little-endian float32, writes a deflate-compressed float-predicted GeoTIFF. 840 × 840 px ≈ 5 m resolution; final size 754 KB.
    - [x] Bbox is Le Sappey ± 2.1 km (the OSM fixture's 2 km bbox plus a 100 m padding ring — `osmnx`'s `dist_type="bbox"` produces edges whose simplified geometries can extend slightly past the fetch bbox, and `sample_elevation` is strict-bounds-fail-fast).
    - [x] Extended fixture-size CI assertion in `tests/unit/test_dem.py::test_committed_dem_fixture_under_size_cap` (parallel pattern to Story 2.1's `test_committed_fixture_under_size_cap`).
- [x] **Task 2: Implement `sample_elevation`** (AC: #1, #2, #3)
    - [x] `src/steeproute/pipeline/dem.py` created with `sample_elevation(graph, dem_path) -> nx.MultiDiGraph`. Pure — `out = graph.copy(); ...; return out`. Mirrors the stage pattern from `pipeline/osm.py::filter_trails` and `pipeline/smoothing.py::smooth_polylines`.
    - [x] CRS read from `dataset.crs`; single `pyproj.Transformer.from_crs(WGS84_EPSG, dem_crs, always_xy=True)` per call; vertices batched via list-comprehension then transformed with one `transformer.transform(lons, lats)` call. `dataset.sample(zip(xs, ys), indexes=1)` returns the per-vertex elevation iterator.
    - [x] `vertices_resampled` tuples are `(lat, lon, elevation_m)` — swap explicit in the list-append at `dem.py:118`; docstring + module docstring + axis-ordering test all spell this out.
    - [x] `DEMCoverageError(PreExecutionError)` added to `src/steeproute/errors.py` (one-liner subclass; no extra fields). Raise message includes `(u, v, k)` and DEM bounds for OOB, and `(lat, lon)` for nodata.
    - [x] Nodata check: `dataset.nodata` read once; per-vertex `elev == float(nodata)` or non-finite → same `DEMCoverageError` path. Fail-fast on first offender.
    - [x] Module-scope `WGS84_EPSG: int = 4326`. basedpyright per-file pragma matches the existing pattern from `pipeline/osm.py` and `pipeline/smoothing.py`.
- [x] **Task 3: Unit tests against fixture + synthetic in-memory GeoTIFFs** (AC: #5, #6, #7)
    - [x] `tests/unit/test_dem.py` runs stages 1→2 (osmnx load + normalize_edges) → 3→4 (smooth + resample) → 5 (sample_elevation) over the real OSM fixture; asserts attribute contract on every edge + per-edge `vertices_resampled` (lat, lon) matches `geometry` (lon, lat) within float tolerance + plausibility band 300–2000 m for the Chartreuse Massif.
    - [x] Two parametrized known-elevation landmarks (Le Sappey village 1004 m ± 50 m; Isère valley edge 460 m ± 50 m) — tolerance rationale documented inline. The ~600 m elevation difference between the two anchors would expose any CRS or axis-swap bug.
    - [x] CRS-transformation correctness test uses a synthetic Lambert-93 (EPSG:2154) GeoTIFF: distinctive elevation per pixel, graph vertices in WGS84 projected to known cells, asserted elevations equal seeded values. Exercises the non-WGS84 CRS path even though the production `dem.tif` ships in WGS84.
    - [x] Out-of-bounds + nodata tests via synthetic GeoTIFFs (written to `tmp_path` since `MemoryFile` doesn't trivially give `rasterio.open` a path). Both paths converge to `DEMCoverageError`; `match=` asserts the edge tuple is named.
    - [x] Naming follows `test_<unit>_<scenario>` (Architecture §Test organization).
- [x] **Task 4: Wire runtime dep + verify CI** (AC: #8)
    - [x] `rasterio>=1.4,<2` added to `[project] dependencies`. `uv lock` resolved `affine 2.4.0`, `attrs 26.1.0`, `cligj 0.7.2`, `pyparsing 3.3.2`, `rasterio 1.5.0` as new transitive deps (`pyproj` and `numpy` were already pulled by `osmnx`).
    - [x] `uv run ruff check && uv run ruff format --check && uv run basedpyright && uv run pytest --cov` all green.
    - [x] Live test re-verified (`STEEPROUTE_USE_OS_TRUSTSTORE=1 uv run pytest -m live` → 1 passed) — no regression.

### Review Findings

_From `bmad-code-review` 2026-05-18. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Acceptance Auditor returned 0 findings; Blind Hunter raised 12, Edge Case Hunter raised 12. After dedupe (4 merges) + triage: 5 patches, 2 defers, 12 dismissed._

**Patches (unambiguous fixes):**

- [x] [Review][Patch] **P1 (HIGH): Bounds check is closed-closed but rasterio cells cover `[left, right) × [bottom, top)` — vertex at exact east/north edge silently samples nodata-fill (0.0 when `dataset.nodata is None`)** — Tightened to half-open on right/top to match rasterio's pixel convention: `bounds.left <= x < bounds.right and bounds.bottom < y <= bounds.top`. New test `test_sample_elevation_rejects_vertex_at_exact_east_edge_of_bounds` exercises the boundary case (vertex at exactly `bounds.right == 1.0` raises `DEMCoverageError`). [src/steeproute/pipeline/dem.py:114] [Source: blind+edge]
- [x] [Review][Patch] **P2 (HIGH): `dataset.crs is None` crashes inside pyproj rather than producing a clean `PreExecutionError`** — Added explicit guard at the top of the `with` block: if `dataset.crs is None`, raise `DEMCoverageError` with an actionable message + detail. New test `test_sample_elevation_raises_dem_coverage_error_when_dem_has_no_crs` writes a synthetic GeoTIFF with `crs=None` and asserts the clean error path. [src/steeproute/pipeline/dem.py:74-79] [Source: edge]
- [x] [Review][Patch] **P3 (MED): Exact float-equality nodata check is fragile for NaN-nodata and dtype-mismatched literals** — Refactored: `nodata_finite` precomputed at open time (`None` when nodata is missing or NaN); per-vertex check uses `not math.isfinite(elev)` (catches NaN/Inf, including NaN-nodata-sampled-back-as-NaN) OR `elev == nodata_finite` (catches finite sentinels). New test `test_sample_elevation_raises_dem_coverage_error_for_nan_nodata` verifies the NaN-nodata path. Inline comments document the contract. [src/steeproute/pipeline/dem.py:82-89, 126-136] [Source: blind+edge]
- [x] [Review][Patch] **P4 (LOW): Dead defensive branch `isinstance(xs, float)` for length-1 coords path** — Removed; collapsed to `xs_list = [float(x) for x in xs]` / `ys_list = [float(y) for y in ys]`. The misleading "length-1 returns scalars" comment is gone. [src/steeproute/pipeline/dem.py:106-107] [Source: blind+edge]
- [x] [Review][Patch] **P5 (LOW): OOB-test substring assertion `"1.000" in msg or "0.000" in msg` is tautological** — Dropped. The surrounding `"(0, 1, 0)" in msg` + `"outside DEM bounds" in msg` checks carry the contract. [tests/unit/test_dem.py:466-471] [Source: blind]

**Deferred (real but out of scope or owned elsewhere):**

- [x] [Review][Defer] **D1 (MED): Sample value `0.0` from a `nodata=None` raster on a void pixel is treated as valid elevation** — A user-supplied DEM whose author left nodata undeclared but used `0.0` as the void marker would have every void pixel become a legitimate sea-level elevation. The current contract — "no silent NaN" — does not promise to catch zero-as-void; the production fixture has `nodata=None` and bbox fully covered, so the failure mode is latent. Belongs to either Story 2.9 (DEM source-unavailable / DEM sanity at setup time) or a documentation update on `--dem-path` saying "your DEM must declare nodata correctly or have full coverage". [src/steeproute/pipeline/dem.py:109-117] [Source: edge]
- [x] [Review][Defer] **D2 (LOW): Inverted-bounds GeoTIFF (`bounds.left > bounds.right` or flipped origin) makes every vertex fail OOB with no useful diagnostic** — A malformed DEM where the affine transform has negative pixel width (or N/S flipped) would cause `bounds.left <= x <= bounds.right` to fail for every vertex; users get a wall of `DEMCoverageError` with no hint that the raster itself is upside-down. Cheap one-time sanity check at open: assert `bounds.right > bounds.left and bounds.top > bounds.bottom` else raise a clearer error. Belongs to whatever story first ships a CLI consumer of `--dem-path` (Story 2.8 or 2.9). [src/steeproute/pipeline/dem.py:73-98] [Source: edge]

**Dismissed (noise / false positive / handled elsewhere):**

- [x] [Review][Dismiss] `dataset.sample()` iterator length not asserted — already enforced by `zip(lons, lats, samples, strict=True)`; mismatch raises `ValueError`. [blind]
- [x] [Review][Dismiss] `vertices_resampled` axis-order test leans on lat-vs-lon magnitude — the axis swap is also verified by `test_fixture_pipeline_vertices_resampled_matches_geometry_coords` (test_dem.py:339-346) with exact equality between `verts[i][0] == lat` and `verts[i][1] == lon` per fixture edge. The synthetic test is supplementary, not load-bearing. [edge]
- [x] [Review][Dismiss] Edge tuple substring match in OOB test (`"(0, 1, 0)" in msg`) is brittle — adding structured fields to `DEMCoverageError` would be over-engineering; format-string contract is implicitly tested and edge-tuple substrings are unique enough that false positives are negligible. [blind]
- [x] [Review][Dismiss] Landmark tolerance ±50 m wide enough to mask small CRS misalignments — chosen for 5 m DEM cell ambiguity + village-reference-altitude-vs-point uncertainty; the Lambert-93 CRS-correctness test exercises the CRS path with `pytest.approx`-exact assertions, so CRS bugs would be caught there. Tightening the landmark tolerance would just be brittle. [blind]
- [x] [Review][Dismiss] Single-vertex landmark test offsets second point by `1e-5°` (~1 m, smaller than 5 m pixel) — both vertices land in the same pixel by intent; the test asserts only `verts[0]`. Cosmetic. [blind]
- [x] [Review][Dismiss] `78600.0` hardcoded longitude-meter conversion in regenerate_dem.py is latitude-specific — documented as "at 45° N"; the actual value at lat 45.260 is ≈78,077, but the resulting 0.6% non-square bbox is irrelevant for a one-off fixture. Comment is honest. [blind]
- [x] [Review][Dismiss] WMS payload check doesn't verify `Content-Type` — the `len(body) != WIDTH*HEIGHT*4` byte-exact check is already a strong signal (false positive would require an HTML error page of coincidentally compatible 2.82 MB size). [blind]
- [x] [Review][Dismiss] Transformer-comment-misleading-about-with-block — not actually a misleading comment in the diff; the structural pattern of opening the dataset and building the Transformer inside the same `with` is standard. [blind]
- [x] [Review][Dismiss] `DEMCoverageError` ordering in `errors.py` — pure file-narrative cosmetic; the subclass is in the right semantic family. [blind]
- [x] [Review][Dismiss] `dataset.sample()` lazy-consumed generator + speculative future-refactor risk — current code consumes the iterator fully inside the `with` block; speculative concern not worth pre-empting. [edge]
- [x] [Review][Dismiss] Landmark "Isère valley edge" at SE bbox corner is fragile against strict-bounds — ~100 m inside DEM bounds via the padding ring; if padding ever shrinks the test fails fast with `DEMCoverageError` rather than masquerading as an elevation-correctness failure. Failure mode is loud enough. [edge]
- [x] [Review][Dismiss] `out.edges(data=True, keys=True)` returns the live attribute dict and the code mutates it during iteration — safe today per networkx convention (mutating attribute-dict contents is permitted; the iteration is over the adjacency view). Speculative future-refactor concern. [edge]
- [x] [Review][Dismiss] `fixture_pipeline_through_stage5` module-scoped — no test mutates the graph today; speculative future test that would corrupt state is not worth pre-empting. [edge]

## Dev Notes

- **`vertices_resampled` axis ordering.** Architecture §Cat 3 specifies `(lat, lon, elevation_m)` — **lat first**. The upstream `geometry` `shapely.LineString` is `(lon, lat)` per shapely convention. Swap explicitly when building each tuple. This is the most likely silent-bug surface in the story; downstream stages 6–7 trust the tuple order.
- **IGN DEM source.** IGN RGE ALTI 5 m is open-data, but acquisition is not as turnkey as `osmnx`. Likely paths: IGN's [Géoservices download portal](https://geoservices.ign.fr/rgealti) (manual ZIP download per département/tile), or the IGN Géoplateforme WMS/WCS endpoints. Pick whichever is least friction for a one-off fixture capture; full automation in `regenerate.py` is nice-to-have, not required. The dev should document what they did so a future maintainer can reproduce. Whatever the source, the committed `dem.tif` must be < 5 MB after cropping to the OSM bbox (5 m resolution × ~4 km × ~4 km × float32 ≈ 2.5 MB raw, well under).
- **DEM CRS.** IGN RGE ALTI 5 m is published in Lambert-93 (EPSG:2154). Sampling requires transforming WGS84 graph coords into Lambert-93 before calling `rasterio.sample()`. Don't hardcode 2154 — read it from the raster (`dataset.crs`) so a future maintainer who ships a WGS84-reprojected DEM doesn't get silently wrong elevations. The CRS-transformation correctness test (AC #6) uses a synthetic non-WGS84 GeoTIFF to keep the CRS code path exercised even if `dem.tif` happens to ship in WGS84.
- **Stage signature & purity.** `sample_elevation(graph, dem_path: pathlib.Path) -> MultiDiGraph`. `dem_path` is the config parameter (Architecture §Cat 3 stage shape `def stage(input, config) -> output`). Return a new `MultiDiGraph`; never mutate the input. Mirror the `out = graph.copy(); ...; return out` pattern in `pipeline/osm.py::filter_trails`. Open the DEM once per call (not per edge), close it deterministically (`with rasterio.open(...) as dataset:`).
- **Out-of-bounds vs nodata.** Architecture §Cat 10 lists `PreExecutionError` subclasses but none cover DEM coverage explicitly. Add `DEMCoverageError(PreExecutionError)` to `errors.py` alongside `CacheNotFoundError` etc. (one-liner, no extra fields). Fail fast on the **first** out-of-bounds or nodata vertex encountered — don't accumulate; the user fixes the source data or the area, then re-runs. Naming one offender + the DEM bounds is enough actionable detail.
- **Carry-forwards (none new for this story).** Stages 3–4 (Story 2.2) drop degenerate edges before reaching here, so stage 5 can assume every input edge has a valid non-empty `geometry`. The `n_intervals` upper bound deferred from Story 2.2 is still owned by Story 2.5 / 2.8 and doesn't affect this story.
- **Out of scope:**
    - Stage 6 elevation moving-median (Story 2.4) — same `vertices_resampled` field, different function (`median_smooth_elevation`).
    - Stage 7 per-edge metrics `length_m / d_plus_m / d_minus_m / avg_gradient` (Story 2.4).
    - DEM version tag handling for the cache key (Story 2.6 — `--dem-version` flag plumbing).
    - DEM source-unavailable error mapping at setup time, parallel to OSM's (Story 2.9).
    - CLI wiring of `--dem-path` (Story 2.8).
    - Orchestrator wiring of stages 1 → 7 (Story 2.5).

### Project Structure Notes

- New production module: `src/steeproute/pipeline/dem.py`. Architecture §Project Structure already reserves it for stage 5.
- New test file: `tests/unit/test_dem.py`.
- New error subclass: `DEMCoverageError(PreExecutionError)` added to `src/steeproute/errors.py`.
- `pyproject.toml`: add `rasterio` to `[project] dependencies`. No new dev deps anticipated.
- New committed binary: `tests/fixtures/grenoble_small/dem.tif`. README extended with a `dem.tif` section. Either extend `regenerate.py` or add `regenerate_dem.py` — pick whichever is cleaner; the existing `regenerate.py` is focused on osmnx, so a separate script may keep the concerns clean.
- The Story 2.1 review patch P9 fixture-size assertion currently checks `osm_graph.graphml` only; extend it (or add a sibling test) to also assert `dem.tif < 5 MB`.

### Testing standards summary

- Layer: all stage-5 tests live in `tests/unit/` (Architecture §Cat 11e). No new integration test in this story — Story 2.5 owns end-to-end stages 1–7 integration.
- Real-data primary, synthetic where mechanically necessary (Architecture §Cat 11b hybrid fixture rule): fixture-driven contract + plausibility tests; synthetic in-memory GeoTIFFs only where the real fixture can't surface the case (CRS-transformation, out-of-bounds, nodata).
- Coverage floor: 95% on `pipeline/dem.py` (pure-logic module per Architecture §Cat 11e).
- Naming: `test_<unit>_<scenario>` (Architecture §Test organization).
- Conventions inherited from Stories 2.1–2.2: absolute imports, PEP 604 unions, no `Any` (or one short comment if unavoidable at the rasterio boundary), basedpyright per-file pragma if needed, ruff-formatted.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 2.3"]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 3 — Data pipeline architecture] — pipeline-stage table (stage 5 = `pipeline/dem.py`), stage signature, edge-attribute contract (`vertices_resampled` after stages 4–6)
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 10 — Error & exit-code architecture] — `PreExecutionError` subclass pattern; `user_message` + optional `detail` fields
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11e — Coverage targets] — 95% pure-logic floor for `pipeline/`
- [Source: _bmad-output/planning-artifacts/architecture.md §Implementation Patterns — Numerical and data discipline] — module-scope named constants
- [Source: _bmad-output/planning-artifacts/architecture.md §Key anti-patterns to avoid] — no top-level side effects, no silent broad except, no silent suppression
- [Source: _bmad-output/planning-artifacts/prd.md §Data pipeline] — DEM-resample + 2D polyline smoothing + moving-median on elevation as the cliff-bias mitigation stack
- [Source: src/steeproute/pipeline/smoothing.py] — stage-signature pattern (`out = graph.copy(); ...; return out`), basedpyright per-file pragma, module-scope constants, degenerate-edge handling already done upstream
- [Source: src/steeproute/pipeline/osm.py:38-63] — input-validation precondition pattern (`_validate_*` helper, raise `PreExecutionError` subclass) — same shape for DEM-coverage validation
- [Source: src/steeproute/errors.py] — existing `PreExecutionError` subclass family to extend with `DEMCoverageError`
- [Source: tests/unit/test_smoothing.py:25-46] — fixture-loading pattern (`osmnx.load_graphml` + `normalize_edges`)
- [Source: tests/fixtures/grenoble_small/README.md] — fixture metadata format to extend for `dem.tif`

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/gallant-archimedes-db77ca`).

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` for the corporate Netskope TLS-intercepting proxy.

**New runtime dep:** `rasterio 1.5.0` (transitive: `affine 2.4.0`, `attrs 26.1.0`, `cligj 0.7.2`, `pyparsing 3.3.2`). `pyproj` and `numpy` already pulled by `osmnx`.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 35 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 243 passed, 1 deselected in ~23s; coverage 95% overall
                                     - pipeline/dem.py 100%
```

Live OSM test re-verified: `STEEPROUTE_USE_OS_TRUSTSTORE=1 uv run pytest -m live` → 1 passed.

### Completion Notes List

**Design decisions / divergences worth review attention:**

1. **DEM source: IGN Géoplateforme WMS `image/x-bil;bits=32`, not file download.** The story Dev Notes listed both the IGN Géoservices ZIP-per-département portal and the Géoplateforme WMS/WCS as candidate paths. WMS with the raw BIL format won outright — the endpoint is open data (no API key), the request is a single HTTP GET, and IGN reprojects the response to the requested CRS server-side. `regenerate_dem.py` is therefore fully automated, parallel to `regenerate.py`. **Verified byte order empirically**: IGN documentation is silent on BIL byte order; tested big-endian first (garbage values), then little-endian (matched known Le Sappey-area elevations of ~1000–1500 m).

2. **DEM committed in WGS84, not Lambert-93.** IGN RGE ALTI is published natively in Lambert-93 (EPSG:2154), but the WMS service reprojects on request. We ship the WGS84 grid so the committed `dem.tif` is trivial to inspect against the OSM fixture's bounds. The production code is fully CRS-aware (`dataset.crs` read at sample time), and the CRS-transformation correctness test in `tests/unit/test_dem.py::test_sample_elevation_transforms_wgs84_coords_to_dem_crs_lambert93` uses a synthetic Lambert-93 in-memory GeoTIFF specifically so the non-WGS84 path stays exercised on every CI run.

3. **DEM bbox padded by 100 m past the OSM fixture's 2 km bbox.** `osmnx`'s `dist_type="bbox"` includes ways whose simplified geometries can extend slightly past the fetch bbox (one fixture edge does land exactly on the eastern bound: vertex at `(5.81344529..., 45.26...)`). The strict-bounds-fail-fast contract on `sample_elevation` would flag those vertices unless we either loosen the check or extend the DEM. Padding is the cleaner fix — the production contract stays strict, and the trade-off is +60 KB of fixture and 84 extra columns/rows of pixels.

4. **`vertices_resampled` axis order is `(lat, lon, elev)` — lat first.** Architecture §Cat 3 specifies this ordering, but `shapely.LineString.coords` returns `(lon, lat)`. The swap is performed in `sample_elevation` at the list-append (`dem.py:118`) and explicitly tested in `test_sample_elevation_vertices_resampled_axis_order_is_lat_lon_elev`. The test picks coords with `lat ≈ 45.x` and `lon ≈ 5.x` so that the two are easily distinguishable by magnitude — a regression that silently transposed the axes would fail this assertion loudly.

5. **`DEMCoverageError` is a new `PreExecutionError` subclass.** No existing subclass fit semantically — `CacheNotFoundError` is for cache-coverage misses, `DataSourceUnavailableError` is for OSM/DEM endpoints being down at fetch time. `DEMCoverageError` covers "the DEM is present and readable but doesn't cover this vertex" (or "the DEM has a nodata cell here"). Both OOB and nodata paths converge to the same error so callers don't need to discriminate.

6. **basedpyright per-file pragma extended.** `tests/unit/test_dem.py` adds `reportArgumentType=false` and `reportMissingParameterType=false` beyond the 5-rule pattern from `pipeline/osm.py`/`test_osm.py` because `rasterio.open(path, "w", **profile)` spreads dynamic kwargs (some of which are typed as `bool`) and the `transform` helper-parameter is left untyped (`affine.Affine` from `from_origin` would need a stub-aware import path). The production module (`src/steeproute/pipeline/dem.py`) sticks with the standard 5-rule pragma.

7. **No new deferred items.** The single deferred item from Story 2.2's review (`n_intervals` upper bound) is still owned by Story 2.5 / 2.8 and doesn't surface here. Stage 5 inherits a graph from stages 3-4 that already drops degenerate edges, so there's no degenerate-input handling needed.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `pipeline/dem.py::sample_elevation` returns a new graph; `test_sample_elevation_does_not_mutate_input` verifies the input is untouched. `vertices_resampled: list[tuple[float, float, float]]` with `(lat, lon, elev_m)` ordering covered by `test_sample_elevation_vertices_resampled_axis_order_is_lat_lon_elev` + `test_sample_elevation_adds_vertices_resampled_with_one_entry_per_geometry_coord`. Full attribute contract preservation covered by `test_sample_elevation_preserves_attribute_contract` (synthetic) and `test_fixture_pipeline_preserves_attribute_contract` (real fixture). ✅
2. AC #2 — `dataset.crs` read at runtime, never hardcoded. `WGS84_EPSG: int = 4326` module-scope constant. Per-call `pyproj.Transformer.from_crs(...)` reused across all edges. CRS-aware path exercised by `test_sample_elevation_transforms_wgs84_coords_to_dem_crs_lambert93` (synthetic Lambert-93 GeoTIFF). ✅
3. AC #3 — `DEMCoverageError(PreExecutionError)` added to `errors.py`. OOB path raises with `(u, v, k)` + DEM bounds (`test_sample_elevation_raises_dem_coverage_error_for_out_of_bounds_vertex`). Nodata path same error class with `(lat, lon)` of offender (`test_sample_elevation_raises_dem_coverage_error_for_nodata_cell`). Every returned elevation verified finite (`test_sample_elevation_returns_finite_floats`). ✅
4. AC #4 — `tests/fixtures/grenoble_small/dem.tif` committed (754 KB, well under 5 MB). README extended with full provenance metadata. `regenerate_dem.py` fully automated via IGN WMS. Size-cap CI assertion in `test_committed_dem_fixture_under_size_cap`. ✅
5. AC #5 — `test_fixture_pipeline_preserves_attribute_contract` + `test_fixture_pipeline_vertices_resampled_matches_geometry_coords` + `test_fixture_pipeline_elevations_are_finite_and_plausible` cover the full-pipeline contract over every fixture edge. Two parametrized known-elevation landmark assertions (Le Sappey village 1004 m ± 50 m; Isère valley edge 460 m ± 50 m). ✅
6. AC #6 — `test_sample_elevation_transforms_wgs84_coords_to_dem_crs_lambert93` builds a 3×3 EPSG:2154 GeoTIFF, places WGS84 graph vertices that project to known cells, asserts seeded values recovered exactly. ✅
7. AC #7 — `test_sample_elevation_raises_dem_coverage_error_for_out_of_bounds_vertex` + `test_sample_elevation_raises_dem_coverage_error_for_nodata_cell` — both via synthetic GeoTIFFs, both assert `DEMCoverageError` with the edge tuple named. ✅
8. AC #8 — All four CI gates green; `rasterio>=1.4,<2` added to runtime deps; `pipeline/dem.py` at 100% coverage (above the 95% pure-logic floor). ✅

### File List

**New:**
- `src/steeproute/pipeline/dem.py` — `sample_elevation` (stage 5). 37 logical lines + docstrings.
- `tests/unit/test_dem.py` — 19 tests: 6 synthetic-WGS84 contract/axis/no-mutate/empty/finite tests, 1 CRS-transformation test (Lambert-93), 5 error-path tests (OOB + nodata + CRS-None + NaN-nodata + exact-east-edge), 1 type-guard test, 1 fixture-size assertion, 3 fixture-pipeline contract tests, 2 parametrized known-landmark tests.
- `tests/fixtures/grenoble_small/dem.tif` — committed binary, 754 KB, 840×840 float32 WGS84 grid covering Le Sappey ± 2.1 km, elevations 356–1637 m (Chartreuse Massif).
- `tests/fixtures/grenoble_small/regenerate_dem.py` — fully automated regeneration via IGN Géoplateforme WMS BIL endpoint.

**Modified:**
- `src/steeproute/errors.py` — added `DEMCoverageError(PreExecutionError)`.
- `tests/fixtures/grenoble_small/README.md` — added `dem.tif` section + WGS84-on-disk rationale + padding-ring rationale + lead-in updated to mention DEM raster.
- `pyproject.toml` — added `rasterio>=1.4,<2` to `[project] dependencies`.
- `uv.lock` — regenerated; `+ rasterio 1.5.0` + transitives.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.3 `backlog → ready-for-dev → in-progress → review`; dated comments added.

**Untouched (intentionally):**
- `src/steeproute/pipeline/__init__.py` — orchestrator wiring lands in Story 2.5.
- `src/steeproute/cli/setup.py` — `--dem-path` CLI wiring lands in Story 2.8.
- `src/steeproute/pipeline/smoothing.py` — stage-6 `median_smooth_elevation` lands in Story 2.4.
- `src/steeproute/pipeline/osm.py` — Story 2.1 surface; no changes needed.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-18 | Yann (Claude Opus 4.7) | Story 2.3 implemented: pipeline stage 5 (`sample_elevation`) in `src/steeproute/pipeline/dem.py`. Reads DEM CRS from the raster header and reprojects WGS84 graph vertices via `pyproj.Transformer` before sampling. `vertices_resampled` entries are `(lat, lon, elev_m)` — explicit swap from shapely's `(lon, lat)`. Strict-bounds-fail-fast: out-of-bounds or nodata vertices raise the new `DEMCoverageError(PreExecutionError)` naming the offending edge + DEM bounds. Real-DEM fixture committed at `tests/fixtures/grenoble_small/dem.tif` (754 KB, IGN RGE ALTI HIGHRES via Géoplateforme WMS BIL endpoint, padded 100 m beyond the OSM fixture bbox so trail-edge vertices land inside). `regenerate_dem.py` fully automated; same `truststore`-via-OS-trust-store pattern as `regenerate.py`. 16 unit tests (6 synthetic-WGS84, 1 Lambert-93 CRS-correctness, 2 error-path, 1 type-guard, 3 fixture-pipeline contract, 2 parametrized known-landmark, 1 fixture-size cap). New runtime dep `rasterio>=1.4,<2`. All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 243 passed (+16 from prior 227) at 95% overall coverage; `pipeline/dem.py` 100%. | _pending_ |
| 2026-05-18 | Yann (Claude Opus 4.7) | bmad-code-review applied: 5 patches landed (P1 HIGH bounds check tightened to half-open on east/north edges per rasterio's pixel convention; P2 HIGH `dataset.crs is None` guard raises `DEMCoverageError` instead of bubbling `pyproj.CRSError`; P3 MED nodata check refactored — NaN-nodata handled via `not math.isfinite(elev)`, finite sentinels via precomputed `nodata_finite` exact equality; P4 LOW dead defensive `isinstance(xs, float)` branch removed; P5 LOW tautological `"1.000" in msg` test assertion dropped). 2 items deferred to `deferred-work.md` (0-as-void on `nodata=None` user-supplied DEM → Story 2.9 or `--dem-path` docs; inverted-bounds GeoTIFF diagnostic → Story 2.8). 12 dismissed. 3 new tests added (CRS-None synthetic, NaN-nodata synthetic, exact east-edge boundary). All four CI gates green post-review: ruff, ruff format, basedpyright 0/0/0, pytest 246 passed (+3 from review) at 95% overall coverage; `pipeline/dem.py` 100%. | _pending_ |
