---
title: 'Auto-download DEM at setup (remove --dem-path)'
type: 'feature'
created: '2026-06-05'
status: 'done'
baseline_commit: c8a47a63d1d848383e55f566b0c5094a1c6d7de9
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `steeproute-setup` fetches OSM live from Overpass but requires a user-supplied `--dem-path` GeoTIFF (`cli/setup.py` hard-errors when it is missing). DEM data should be acquired automatically for the requested area, the same way OSM is.

**Approach:** Productionize the proven IGN Géoplateforme WMS download already used by `tests/fixtures/grenoble_small/regenerate_dem.py` into a `resolve_dem(area, cache_root)` helper. Remove `--dem-path` entirely; on a cache miss, setup downloads the DEM for the area (tile-and-mosaic at native 5 m), caches it per-area under the cache root, and feeds the resolved local path into the existing pipeline. Cache hits download nothing.

## Boundaries & Constraints

**Always:**
- Reuse the existing IGN WMS mechanism verbatim where possible: endpoint `https://data.geopf.fr/wms-r/wms`, layer `ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES`, `image/x-bil;bits=32` little-endian float32, WMS 1.3.0, `crs=CRS:84`, bbox order `west,south,east,north`, `truststore.inject_into_ssl()` for corporate TLS.
- Cover the OSM bbox **plus a padding ring** (mirror the fixture's 100 m) so strict-bounds `sample_elevation` never fails on simplification overshoot.
- Map every download failure (network, timeout, HTTP error, unexpected payload size) to `DataSourceUnavailableError("DEM source unreachable.", …)` so it surfaces as exit 2 with the existing stderr wording (NFR6).
- Keep `--dem-version` as the cache-key tag; default it to a stable IGN-layer constant when not supplied.
- Atomic cache write (temp file + replace); reuse an existing cached DEM unless `--force-refresh`.
- Keep `PipelineConfig.dem_path` and the orchestrator's `is_file` guard unchanged — only the *source* of that path changes.

**Ask First:**
- Any change to the cache-key composition or manifest schema beyond the `dem_version` default.

**Never:**
- Do not add runtime network I/O to the query CLI — this is setup-side only.
- Do not download on a cache hit.
- Do not silently degrade resolution: keep native ~5 m by tiling, not by downscaling.
- Do not introduce an insecure-TLS / skip-verify escape hatch.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First setup, no cached DEM | area, empty cache | WMS tiles fetched, mosaicked into one WGS84 float32 GeoTIFF under `<root>/steeproute/dem/`, pipeline runs | N/A |
| Cached DEM present | area previously prepared | `resolve_dem` returns existing file; no HTTP request | N/A |
| `--force-refresh` | DEM already cached | DEM re-downloaded and overwritten | N/A |
| Large area | grid dim > max tile px | split into multiple WMS GetMap tiles, stitched to one raster at 5 m | N/A |
| WMS unreachable / timeout / HTTP error | network failure | abort before writing | `DataSourceUnavailableError` → exit 2, stderr `error: DEM source unreachable` |
| Unexpected WMS payload size | malformed/short response | abort before writing | `DataSourceUnavailableError` naming expected vs got bytes |

</frozen-after-approval>

## Code Map

- `src/steeproute/cli/setup.py` -- remove `--dem-path` option/param + `is_file` check + `_derive_dem_version`; on cache miss call `resolve_dem(...)` to get the path; default `dem_version` to the IGN-layer constant.
- `src/steeproute/cli/_shared.py` -- delete the now-unused `dem_path_option`.
- `src/steeproute/pipeline/dem_download.py` -- **new.** `resolve_dem(area, cache_root, *, force_refresh=False) -> Path`; WMS BIL fetch, tile/mosaic, atomic GeoTIFF write, per-area cache key, error mapping. Owns `DEFAULT_DEM_VERSION`, WMS constants, `PADDING_M`, `TARGET_RES_M=5.0`, `MAX_TILE_PX`.
- `src/steeproute/cache.py` -- add `dem_cache_path_for(cache_root, dem_key) -> Path` (single-sources the `steeproute/dem/` layout, like `entry_dir_for`).
- `tests/fixtures/grenoble_small/regenerate_dem.py` -- reference for the WMS request shape (don't break it).
- `tests/e2e/test_steeproute_setup.py` -- drop `--dem-path`; patch `resolve_dem` to return the committed `dem.tif`; fix the `dem_version` assertion; replace the missing-`--dem-path` test.
- `pyproject.toml` -- add `truststore` and `numpy` to runtime `dependencies`; drop the now-redundant dev `truststore`.
- `_bmad-output/planning-artifacts/architecture.md` -- update DEM-source statements (DEM is auto-downloaded, not user-provided) and the `--dem-path` CLI-surface entry.

## Tasks & Acceptance

**Execution:**
- [x] `src/steeproute/pipeline/dem_download.py` -- implement `resolve_dem`: compute padded bbox in degrees from the area center (cos-latitude longitude scaling, generalizing the fixture's hardcoded constant), full pixel grid at ~5 m, tile into ≤`MAX_TILE_PX` blocks, fetch each via WMS BIL, validate per-tile byte count, mosaic into one `<f4` array, write atomic WGS84 GeoTIFF; per-area cache path via `cache.dem_cache_path_for`; reuse unless `force_refresh`; wrap failures as `DataSourceUnavailableError`.
- [x] `src/steeproute/cache.py` -- add `dem_cache_path_for`.
- [x] `src/steeproute/cli/setup.py` -- wire `resolve_dem` on the miss branch; remove `--dem-path` surface and `_derive_dem_version`; default `dem_version`.
- [x] `src/steeproute/cli/_shared.py` -- remove `dem_path_option`.
- [x] `pyproject.toml` -- dependency moves.
- [x] `tests/unit/test_dem_download.py` -- **new**, offline (monkeypatch `urlopen`): cover every I/O-matrix row — single-tile happy path, multi-tile mosaic placement, cache reuse (second call issues no request), `force_refresh` re-fetch, error mapping, bad payload size.
- [x] `tests/e2e/test_steeproute_setup.py` -- update for the new flow (patch `resolve_dem`, fix assertions, replace missing-flag test with auto-download + force-refresh wiring tests).
- [x] `tests/integration/test_dem_live.py` -- **new**, `@pytest.mark.live`: download a small Grenoble area from IGN, assert bounds cover the area and elevations land in a plausible Alpine band.
- [x] `_bmad-output/planning-artifacts/architecture.md` -- doc sync for the DEM-source change.
- [x] **Wider `--dem-path` removal ripple** (not anticipated in the original Code Map; all updated to drop the flag / patch `resolve_dem`): `src/steeproute/cache.py` coverage-error messages (`_no_prepared_cache_message` + `_partial_coverage_message` no longer suggest `--dem-path`); `tests/e2e/conftest.py`; `tests/e2e/test_source_unavailable.py` (DEM-unreachable now via WMS `urlopen`); `tests/e2e/test_coverage_check.py`; `tests/e2e/test_cli_smoke.py`; `tests/unit/test_cli_options.py`; `tests/unit/test_cli_help.py`; `tests/unit/test_check_coverage.py`; `tests/unit/test_area_parsing.py`.

**Acceptance Criteria:**
- Given the CLI, when `steeproute-setup --help` runs, then no `--dem-path` appears and only `--center`/`--radius` are required inputs.
- Given an uncached area, when setup runs (cache miss), then a DEM covering area+padding is downloaded exactly once, the pipeline completes, and the manifest `dem_version` equals the default IGN-layer tag.
- Given setup already ran for an area, when it runs again with the same flags, then it is a cache hit and `resolve_dem` performs no HTTP request.
- Given an area larger than one WMS tile, when its DEM is fetched, then multiple GetMap requests are issued and the stitched raster covers the full padded bbox at ~5 m.
- Given `--dem-version` differing between two runs, then two distinct cache entries are produced (existing behavior preserved).

## Design Notes

- **Why CLI-layer resolve, not orchestrator:** the cache key (and thus the hit/miss decision) is computed before any stage runs, and the DEM cache lives under `cache_root` which the orchestrator doesn't hold. Resolving in `cli/setup.py` on the miss branch keeps `run_setup_stages` pure and means hits never touch the network. `dem_version` is a constant tag, so it's available for the key without the file.
- **WMS request (mirror fixture):** `service=WMS, version=1.3.0, request=GetMap, layers=<LAYER>, styles=, crs=CRS:84, bbox=w,s,e,n, width, height, format=image/x-bil;bits=32`; body is `width*height*4` raw little-endian float32. Validate that length before trusting it.
- **Tiling:** full grid `W=H=round(2*half_side_m/5)`; for each tile compute its pixel window and the linearly-interpolated sub-bbox, fetch, place into `arr[y0:y1, x0:x1]`. All tiles share CRS:84, so one `from_bounds` over the full bbox writes the mosaic.

## Verification

**Commands:**
- `uv run pytest tests/unit/test_dem_download.py tests/e2e/test_steeproute_setup.py` -- expected: all pass.
- `uv run pytest` -- expected: full offline suite green (no `--dem-path` regressions).
- `uv run ruff check src tests && uv run basedpyright` -- expected: clean.
- `uv run pytest -m live tests/integration/test_dem_live.py` -- expected (network, manual): live IGN fetch passes plausibility bands.

## Suggested Review Order

**CLI wiring (entry point)**

- Start here: the cache-miss branch resolves the DEM via auto-download, then runs the pipeline.
  [`setup.py:160`](../../src/steeproute/cli/setup.py#L160)
- `dem_version` defaults to the IGN-layer tag (or `--dem-version`); feeds the cache key without the file.
  [`setup.py:114`](../../src/steeproute/cli/setup.py#L114)

**DEM download module**

- `resolve_dem`: padded bbox → grid → cache key → reuse-or-fetch-and-write. The whole contract.
  [`dem_download.py:100`](../../src/steeproute/pipeline/dem_download.py#L100)
- Earth-model constant deliberately matched to osmnx so the 100 m padding ring is a true margin.
  [`dem_download.py:90`](../../src/steeproute/pipeline/dem_download.py#L90)
- `dem_version` folded into the raster cache key so `--dem-version` re-downloads, not relabels.
  [`dem_download.py:171`](../../src/steeproute/pipeline/dem_download.py#L171)
- Tile-and-mosaic: per-tile sub-bbox interpolation, byte-count check, north-up array placement.
  [`dem_download.py:210`](../../src/steeproute/pipeline/dem_download.py#L210)
- WMS GetMap + Content-Type guard rejecting XML/HTML error docs of coincidental length.
  [`dem_download.py:251`](../../src/steeproute/pipeline/dem_download.py#L251)
- Atomic `.tmp` + `os.replace` GeoTIFF write with cleanup on failure.
  [`dem_download.py:307`](../../src/steeproute/pipeline/dem_download.py#L307)

**Cache layout & error messages**

- Single-source-of-truth for the `steeproute/dem/<key>.tif` layout, parallel to `entry_dir_for`.
  [`cache.py:339`](../../src/steeproute/cache.py#L339)
- Coverage-miss suggestion no longer mentions `--dem-path` (auto-download).
  [`cache.py:891`](../../src/steeproute/cache.py#L891)
- Orchestrator's missing-DEM guard reworded for the corrupt-cache case (no more `--dem-path`).
  [`__init__.py:124`](../../src/steeproute/pipeline/__init__.py#L124)

**Surface removal & dependencies**

- `--dem-path` option deleted; `--dem-version` help reworded.
  [`_shared.py:428`](../../src/steeproute/cli/_shared.py#L428)
- `truststore` + `numpy` promoted to runtime deps (corporate-TLS + mosaic array).
  [`pyproject.toml:45`](../../pyproject.toml#L45)

**Tests (peripherals)**

- Offline unit coverage for every I/O-matrix row (urlopen monkeypatched).
  [`test_dem_download.py:1`](../../tests/unit/test_dem_download.py#L1)
- Live IGN fetch sanity (plausible Alpine elevations, bounds cover area).
  [`test_dem_live.py:1`](../../tests/integration/test_dem_live.py#L1)
- Setup e2e updated: auto-download on miss + force-refresh wiring, no `--dem-path`.
  [`test_steeproute_setup.py:280`](../../tests/e2e/test_steeproute_setup.py#L280)
