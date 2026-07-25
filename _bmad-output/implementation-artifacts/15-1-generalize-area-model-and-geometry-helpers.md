# Story 15.1: Generalize the Area model and geometry helpers

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want the `Area` type and its polygon/bbox helpers to represent a rotated rectangle (square and axis-aligned rectangle as special cases),
so that all downstream geometry derives from one model with no squareness assumption.

## Acceptance Criteria

1. `Area` carries a rotated-rectangle shape (center + two half-extents + a bearing angle). A single-radius square remains expressible and, once built, is indistinguishable downstream from a v1 `Area` — every existing `area.radius_km` read still resolves for square areas.
2. Deriving an `Area`'s WGS84 polygon computes the four corners in a local `cos(lat)` km frame, rotates them by the bearing angle, and converts back to lon/lat. For `angle=0` with equal half-extents the result **reproduces today's square ring exactly** (same vertex order, same closing vertex, byte-for-byte).
3. The axis-aligned-envelope helper (`area_bbox_wgs84`) returns the true min/max of the (possibly rotated) polygon and is named/documented as an **envelope** (an over-approximation), not "the region." For a square it returns today's bbox unchanged.
4. The km↔deg conversion stays single-sourced (`_DEG_PER_KM_LAT` / `_deg_per_km_lon`) so polygon and envelope can't skew apart, and the near-pole guard behavior is preserved.
5. Unit tests verify: rotation against hand-computed corner coordinates; a non-square axis-aligned rectangle; the degree-space-skew case (a box away from the equator where 1° lon ≠ 1° lat); and byte-identical equality of the `angle=0`/equal-extents ring with the pre-change square ring.
6. The full offline suite stays green with **no golden rebake** — square-path setup output is unchanged (osm fetch is untouched in this story).

## Tasks / Subtasks

- [x] Generalize the `Area` dataclass in [models.py](src/steeproute/models.py) (AC: #1)
  - [x] Added rotated-rectangle fields `half_width_km` / `half_height_km` / `angle_deg` (all defaulted so a square is the positional-compatible case); `radius_km` stays a real field (default `0.0`) so every reader **and** every `Area(center=..., radius_km=...)` construction site is unchanged; `None` extents resolve to `radius_km` via the derived `half_extents_km` property (re-derives correctly under `dataclasses.replace`)
  - [x] Rewrote the docstring: geometric meaning, square/axis-aligned-rect special cases, `radius_km` as the square shorthand; added `is_square`
- [x] Generalize `_area_to_polygon` in [cache.py](src/steeproute/cache.py:854) to the km-frame rotate-and-convert-back algorithm (AC: #2, #4)
  - [x] `angle_deg == 0` fast path preserves vertex order + closing vertex → byte-identical to the pre-Epic-15 square ring (pinned by a regression test that rebuilds the historical formula); rotated branch places corners in the local `cos(lat)` km frame, applies a clockwise-from-north bearing, converts back
- [x] Generalize `area_bbox_wgs84` in [cache.py](src/steeproute/cache.py:882): `.bounds` already yields the true polygon envelope, so only the docstring changed — it now names the result an *envelope*/over-approximation and flags the envelope-leak audit targets (AC: #3)
- [x] `_bounds_geojson`: no change needed — it already derives its ring from `_area_to_polygon` (byte-identical for squares); `properties.mode`/canonicalization deliberately left to Story 15.2 (AC: #2)
- [x] Add unit tests for the geometry helpers (AC: #5)
  - [x] New `tests/unit/test_area_geometry.py`: square shorthand, non-square rect, 90°-symmetry, hand-computed 45° diamond corners, degree-space-skew (cos-lat) case, byte-identical square-ring regression, and rotated-envelope over-approximation
- [x] Ran the offline suite per-directory + `basedpyright`; confirmed no golden rebake (AC: #6)

## Dev Notes

- **Scope boundary — this story is the model + the two geometry helpers only.** Explicitly **out of scope** (later in Epic 15): `osmnx.graph_from_polygon` setup fetch, `_canonicalize_area` gaining a rotated area *mode*, the manifest/index schema-version bump, coverage/partial-coverage messaging and the consumer-side envelope-leak audit (Story 15.2); CLI flags, true-area cap validation, the report overlay, the rotated golden, and docs (Story 15.3). Do not pull those in — keep `_canonicalize_area` and `osm.py` untouched so this story is a safe, golden-neutral refactor. [Source: sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md#4.2]
- **Recommended `Area` shape** (spelling is yours to finalize): `center (lat, lon)` + `half_width_km` + `half_height_km` + `angle_deg` (bearing of the long axis, 0 = axis-aligned). `--radius` maps to `half_width == half_height == radius`, `angle == 0`. [Source: sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md#3]
- **The key design call: how `radius_km` survives.** Many sites read `area.radius_km` directly (cache canonicalization/index/coverage messaging, `output.py`, `argv.py`, `regions.py`, `osm.py`, `dem_download.py`, `pipeline/__init__.py`, `regression.py`) — see the grep in References. This story must not touch those. Recommended: expose `radius_km` as a read-only `@property` that returns the square half-side for a square area (equal extents, angle 0). That keeps `@dataclass(frozen=True, slots=True)` and leaves square-area callers byte-identical; a `slots=True` dataclass allows a `property` as long as the name isn't also a field. Alternatively keep `radius_km` as a stored field plus derived extents — simpler but redundant. Either way, non-square areas won't be *constructed* until 15.2/15.3, so a property that only resolves for squares is sufficient for this story.
- **Byte-identical square ring (AC #2) — why it matters and why goldens are still safe.** The golden "no rebake" guarantee comes from `osm.py` being untouched here (setup still fetches via `graph_from_point(dist_type="bbox")` off `radius_km`), *not* from the polygon. `_area_to_polygon` feeds coverage `.contains` and the `bounds.geojson` sidecar. Reproducing the exact ring avoids phantom coverage misses and sidecar churn. Current ring, for reference: `dlat = radius_km * _DEG_PER_KM_LAT`, `dlon = radius_km * _deg_per_km_lon(lat)`, corners `(lon±dlon, lat±dlat)` in the order SW, SE, NE, NW, SW. At `angle=0` equal-extents your km-frame path must yield these exact floats — pin it with an equality test against a hand-built current-style ring. [Source: cache.py:854-879]
- **Rotation math (~10 LOC, approximation-grade).** Lift corners into a local km frame (`x = lon_offset / _deg_per_km_lon(lat)`, `y = lat_offset / _DEG_PER_KM_LAT`, i.e. multiply half-extents by the per-km factors to get degree offsets), rotate the four `(±half_width, ±half_height)` corners by `angle_deg` in that km frame, convert back to lon/lat about `center`. Flat-earth is sub-percent at range scale — fidelity is not a goal ("which valleys to fetch," not survey accuracy). Do **not** rotate in raw degree space (1° lon ≠ 1° lat skews the box). [Source: architecture.md:268-282; sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md#"Technical Impact"]
- **Single-source the conversion.** Reuse `_DEG_PER_KM_LAT` (`1/111`) and `_deg_per_km_lon(lat)` (cos-lat compensation, with the `|lat|≥89.99°` equator fallback) — polygon and envelope must share one source or they skew apart and cause edge-case coverage misses. `output.py::_search_bbox` uses a *different* constant (`111.32`) and is a separate overlay path — leave it for Story 15.3. [Source: cache.py:822-892]
- **Testing standards.** Offline unit tests under `tests/unit/`; run per-directory (`uv run pytest tests/unit/...`) — don't mix `tests/unit` and `tests/integration` in one invocation (wrong `conftest.py`). Type-check with `uv run basedpyright <files>`. If `uv run` flakes with ~43 `test_cli_smoke.py` failures or a TLS `UnknownIssuer` error after this edit, run `uv sync --native-tls` once. [Source: AGENTS.md#Dev environment]

### Project Structure Notes

- `Area` lives in [models.py](src/steeproute/models.py) (shared by setup ingestion and query-side coverage); the geometry helpers live in [cache.py](src/steeproute/cache.py). No new module needed — generalize in place.
- `_area_to_polygon` is already the shared source for both entry-side (`_bounds_geojson`) and query-side (`check_coverage`) geometry, so generalizing it once covers both without divergence.

### References

- Epic + AC source: [epics.md](_bmad-output/planning-artifacts/epics.md:252) (Epic 15 / Story 15.1)
- Change proposal: [sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md) (§3 recommended shape, §"Technical Impact" rotation + envelope audit, §4.2 story split)
- Architecture: [architecture.md:268](_bmad-output/planning-artifacts/architecture.md) (rotated-rectangle decision), [architecture.md:332](_bmad-output/planning-artifacts/architecture.md) (canonicalization/schema — 15.2, for context only)
- `Area` model: [models.py:27](src/steeproute/models.py)
- Geometry helpers to generalize: [`_area_to_polygon`](src/steeproute/cache.py:854), [`area_bbox_wgs84`](src/steeproute/cache.py:882), conversion constants [cache.py:829](src/steeproute/cache.py:829)
- `radius_km` read sites (must stay working, do not touch): `git grep -n "\.radius_km" src/` — cache.py, output.py, cli/, app/cli_adapter/, pipeline/, regression.py

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- First run of the new suite hit one test-only failure (`pytest.approx` objects placed in a `set` → `TypeError: unhashable`); implementation was correct. Rewrote the diamond-corner assertion to match each generated corner against the expected vertices numerically. Also silenced shapely/`approx` `reportUnknownMemberType` warnings via a module pyright header (same pattern as `test_check_coverage.py`) and fixed a `tuple[float, ...]` param type.

### Completion Notes List

- **Model (`models.py`).** `Area` generalized to a rotated rectangle: `center`, `radius_km` (kept as the square shorthand / bbox half-side, now defaulted `0.0`), plus `half_width_km` / `half_height_km` (`None` → resolve to `radius_km`) and `angle_deg` (0 = axis-aligned). Two derived properties: `half_extents_km` (resolves the shorthand; computed on access so `dataclasses.replace(area, radius_km=…)` re-derives) and `is_square`. Every existing `area.radius_km` reader and `Area(center=…, radius_km=…)` construction site is untouched.
- **Geometry (`cache.py`).** `_area_to_polygon` now builds corners in a local `cos(lat)` km frame using the effective half-extents, rotates by a clockwise-from-north `angle_deg` bearing, and converts back with the shared `_DEG_PER_KM_LAT` / `_deg_per_km_lon` factors. The `angle_deg == 0` branch is an explicit byte-identical fast path (same vertex order and float ops as pre-Epic-15). `area_bbox_wgs84` needed no logic change (`.bounds` already yields the true envelope of a rotated polygon) — docstring now names it an *envelope* over-approximation and points at the 15.2/15.3 envelope-leak audit.
- **Scope held.** No change to `_canonicalize_area`, the manifest/index schema, `osm.py`, CLI flags, or the render overlay (Stories 15.2/15.3). `_bounds_geojson` unchanged.
- **Verification.** New `tests/unit/test_area_geometry.py` (10 tests) all pass. Full `tests/unit` (744) green; integration cache (`test_cache_coverage`/`test_cache_roundtrip`/`test_cache_atomic`, 13) green; **pinned regression goldens (`tests/e2e/test_pinned_regressions.py`, 4) green with no rebake**; e2e coverage-check (5) green. `basedpyright` clean on all three changed files (0 errors, 0 warnings). Editing `models.py` shifts `compute_pipeline_content_hash` (expected — no test pins its literal value; cache-key change-detection tests still pass).
- **Code-review fix (constructor guard).** Initial draft defaulted `radius_km` to `0.0`, which let `Area(center=…)` be constructed with no size at all (a silent zero-area polygon) where v1 raised `TypeError`. A model-level size guard was ruled out — `tests/unit/test_osm.py` constructs `Area(radius_km=0.0/-…/NaN)` on purpose so `osm._validate_area` can raise the friendly `BadCLIArgError`, so validation must stay at the boundary. Fix: made `radius_km` **required again** (no default). The v1 no-size guard is restored (verified: `Area(center=…)` → `TypeError`); bad-radius construction still works so the boundary-validation contract and its tests are untouched. Cost: non-square/rotated construction passes an inert `radius_km=0.0` alongside explicit extents (the extents drive geometry).

### File List

- `src/steeproute/models.py` (modified — `Area` generalized)
- `src/steeproute/cache.py` (modified — `_area_to_polygon` rotated, `area_bbox_wgs84` docstring)
- `tests/unit/test_area_geometry.py` (new — geometry helper + model tests)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status bookkeeping)

## Change Log

- 2026-07-25: Implemented Story 15.1 — generalized `Area` to a rotated rectangle (square as the special case) and the `_area_to_polygon` / `area_bbox_wgs84` geometry helpers; square path byte-identical, no golden rebake. Status → review.
- 2026-07-25: Code review (low-effort diff pass) fixed one finding — `radius_km` restored as a required field (no default) so `Area(center=...)` with no size raises `TypeError` again, matching v1; boundary-validation contract (`osm._validate_area`) untouched. Status → done.
