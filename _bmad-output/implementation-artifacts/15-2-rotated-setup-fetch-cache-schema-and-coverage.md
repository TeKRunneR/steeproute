# Story 15.2: Rotated-aware setup fetch, cache schema, and coverage

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want setup to fetch and cache exactly the rotated rectangle and queries to resolve coverage against it,
so that off-axis valley is never pre-processed and cached areas are keyed correctly.

## Acceptance Criteria

1. For a non-square `Area`, setup stage 1 fetches via `osmnx.graph_from_polygon` over
   `_area_to_polygon(area)` (reusing osmnx's `truncate_graph_polygon` path), so the cached graph
   contains only edges inside the rotated rectangle. For a **square** `Area` the existing
   `graph_from_point(dist_type="bbox")` call is used **unchanged** — see Dev Notes on why substituting
   our polygon there would silently change the fetch. Both branches sit inside the same
   `DataSourceUnavailableError` wrap.
2. The fetch precondition (`osm._validate_area`) validates the *effective* geometry (extents finite
   and > 0, angle finite) instead of `radius_km` alone, keeping today's `--radius` wording for square
   areas.
3. `_canonicalize_area` dispatches on an area mode: a square emits today's `center_radius` dict
   byte-for-byte (existing square cache keys are unchanged), a rotated/non-square area emits a new
   mode carrying rounded center + half-extents + angle. Two areas differing only in angle or in one
   extent produce different cache keys.
4. The manifest schema version is bumped; the `area` block round-trips extents + angle, and
   pre-existing entries surface the established re-prepare-once path (no compat shim). Index rows
   carry the new geometry, and a pre-migration (square-only) index still resolves correctly as
   squares.
5. Coverage resolves against the rotated polygon: containment via `shapely.contains`, and
   smallest-containing selection ranks by **true rectangle area** rather than `radius_km` (which is
   inert for a rotated area). Index-row validation accepts rotated rows — a rotated entry is never
   silently dropped or treated as a square.
6. The empty-cache and partial-coverage messages derive from the area's geometry, not a scalar
   radius. Square-area messages stay byte-identical (existing assertions unchanged); a rotated area
   never produces a nonsense `--radius 0` suggestion.
7. The envelope-leak audit is completed for the CLI setup + query paths: no consumer on those paths
   treats `area_bbox_wgs84` / `polygon.bounds` as "the region", and coverage never over-reports for a
   rotated area. Leak sites owned by later stories are named with their owning story rather than left
   silent.
8. The `bounds.geojson` sidecar records the true geometry (and mode) for a rotated area; for a square
   area it is byte-identical to today.
9. The committed regression cache fixtures are migrated in place to the new schema (metadata only —
   `graph.pkl` untouched) and the pinned goldens pass with **no rebake**. Offline suite green;
   `basedpyright` clean.

## Tasks / Subtasks

- [x] Rotated fetch in [osm.py](src/steeproute/pipeline/osm.py:58) (AC: #1, #2)
  - [x] `osm_load` dispatches on `area.is_square`: square keeps the existing `graph_from_point`
        call, non-square goes to `graph_from_polygon(area_polygon(area), ...)`. Added
        `cache.area_polygon` as the public name for `_area_to_polygon` so the fetch shares the one
        ring derivation instead of re-implementing the km-frame math; imported lazily inside
        `osm_load` alongside the other deferred fetch-stack imports (Story 14.4 import-cost pattern)
  - [x] Square branch reads `half_extents_km[0]`, not `radius_km` — a square spelled as explicit
        equal extents carries an inert `radius_km=0.0` and would otherwise fetch `dist=0`
  - [x] Both calls sit inside the one `except (RequestException, OSError, ValueError)` wrap; a
        `fetch_call` label names whichever branch ran in the `detail` line
  - [x] `_validate_area` now guards the effective extents (`--width`/`--height`, finite and > 0) and
        the bearing (`--angle must be finite`). Dispatches on the **construction shape** (were the
        extents supplied?) rather than `is_square`, because `is_square` compares the two extents and
        `nan == nan` is False — a NaN `--radius` would otherwise be misreported as a width problem
- [x] Cache key + schema in [cache.py](src/steeproute/cache.py) (AC: #3, #4, #8)
  - [x] `_canonicalize_area` dispatches on shape: square emits the v1 `center_radius` dict
        byte-for-byte, non-square emits `center_extents_angle` with center + extents + angle rounded
        (`_AREA_ANGLE_DEG_DECIMALS = 3`, same principle as the existing precisions)
  - [x] `_MANIFEST_SCHEMA_VERSION` 2 → 3. `_GRAPH_PAYLOAD_VERSION` deliberately **stays at 2** —
        decision recorded in Completion Notes and in the constant's comment
  - [x] New shared wire helpers `_area_wire_dict` / `_area_from_wire` so `Manifest.to_dict`,
        `rebuild_index`, `Manifest.from_dict` and `_read_indexed_entries` cannot disagree on an
        area's shape. `_INDEX_SCHEMA_VERSION` stays at 1 (rationale in Completion Notes)
  - [x] `_read_indexed_entries` guard moved off the scalar `radius_km` onto `_has_usable_geometry`
        (effective extents finite and > 0, center and bearing finite)
  - [x] `_bounds_geojson` `properties` now mirrors the manifest's `area` vocabulary via
        `_area_wire_dict` (keeping GeoJSON `[lon, lat]` for `center`); the ring already came from
        `_area_to_polygon`, so it was already the true footprint
- [x] Coverage generalization in [cache.py](src/steeproute/cache.py:1053) (AC: #5, #6)
  - [x] New `_area_km2` (`2hw × 2hh`); `_select_smallest_containing` ranks on it instead of
        `radius_km`. Monotone in `radius_km` for squares, so square-only caches order as before
  - [x] New `_format_area_flags` (copy-pasteable command fragment) and `_format_area_geometry`
        (human prose), both square-byte-identical; `_no_prepared_cache_message`,
        `_partial_coverage_message` and `_diagnostic_detail` route through them
  - [x] `_partial_coverage_message`'s shrink arithmetic is now gated on *both* areas being square —
        the `entry.r - |Δ|` formula presumes concentric axis-aligned boxes, so against a rotated
        entry it falls through to the existing relocate-or-prepare advice rather than inventing a
        number
- [x] Envelope-leak audit sweep (AC: #7)
  - [x] Swept every `area_bbox_wgs84` / `.bounds` / `.radius_km` consumer. Fixed in-scope:
        `dem_download._padded_bbox` (was `radius_km`-based → padding-only box for a rotated area;
        now sizes off `max(half_extents_km)` as a documented envelope over-approximation) and
        `pipeline._assert_non_empty`'s error line (was `radius_km=0` for a rotated area; now
        shape-aware, square wording unchanged)
  - [x] Verified as **not** leaks: `dem_download.graph_dem_bounds` (production DEM sizing — reads
        post-truncation edge geometry, so it already shrinks with the rotated polygon) and
        `dem.py`'s `dataset.bounds` (the raster's own extent)
  - [x] Labelled the deferred ones in their docstrings with the owning story:
        `output._search_bbox` → 15.3, `cli/_shared.validate_area_size` → 15.3,
        `app/cli_adapter/regions._to_region_info` → App 5.1
- [x] Migrate the four committed cache fixtures (AC: #9)
  - [x] Migration is one line per fixture: `"schema_version": 2` → `3`. A square's `area` block is
        byte-identical across v2 and v3, so no field edits were needed and `index.json` (still
        schema v1, square rows) needed no edit either. `graph.pkl` untouched
  - [x] Migration note added to all four fixture READMEs explaining why it was done in place
  - [x] Updated the schema-literal pins: `tests/unit/test_cache.py` (now mirrors
        `_MANIFEST_SCHEMA_VERSION = 3` locally, and its legacy-version test is parametrized over
        both 1 and 2), `tests/unit/test_cache_key.py`, `tests/e2e/test_steeproute_setup.py`
        (re-prepare-once test parametrized over both superseded versions)
- [x] Tests (AC: #1–#9)
  - [x] `tests/unit/test_osm.py` (+7): square still calls `graph_from_point` with today's kwargs and
        never `graph_from_polygon`; three non-square shapes each fetch via `graph_from_polygon` with
        a ring equal to `area_polygon(area)`; polygon-branch failure wraps as
        `DataSourceUnavailableError`; bad extents and non-finite angle rejected
  - [x] `tests/unit/test_cache_key.py` (+7): square-via-extents shares the shorthand's key;
        angle-only and each-extent-only differences change the key; a rotated square ≠ the
        axis-aligned one; sub-precision rotated drift canonicalizes; rotated `to_dict` shape and
        round-trip
  - [x] `tests/unit/test_check_coverage.py` (+10): true-area ranking (incl. a hash-order trap and a
        mixed square/rotated cache); a query inside a rotated entry's envelope but outside the box
        is declined; rotated index row survives parsing; pre-migration row without `mode` reads as a
        square; rotated end-to-end `check_coverage`; four rotated-messaging assertions
  - [x] `tests/unit/test_cache.py` (+4): `bounds.geojson` ring equals `_area_to_polygon` and is
        strictly inside the envelope; rotated `properties`; `rebuild_index` write↔read agreement on a
        rotated row; `_gc_superseded_entries` treats two bearings as distinct areas
  - [x] Ran per-directory + the goldens; then the full offline suite (see Completion Notes)

## Dev Notes

- **Scope boundary.** This story is setup fetch + cache key/schema + coverage. **Out of scope:** CLI
  flags for the rotated shape, the true-area `--area-cap` check, the report overlay
  (`output._search_bbox`), the rotated regression golden, and docs — all Story 15.3; the App's
  `RegionBounds` / argv — App Story 5.1. Because 15.3 owns the flags, **nothing constructs a rotated
  `Area` from the CLI yet**: test rotated behavior by constructing `Area(...)` directly.
  [Source: epics.md#Epic 15]
- **The big landmine: do NOT feed `_area_to_polygon` into the square fetch.** `graph_from_point(dist_type="bbox")`
  → `bbox_from_point` → `bbox_to_poly` → `graph_from_polygon`, so the polygon path *is* the same
  mechanism. But `bbox_from_point` uses `EARTH_RADIUS_M = 6_371_009` (≈ 1/111.195 deg/km) while
  `_area_to_polygon` uses `_DEG_PER_KM_LAT = 1/111`. Our ring is ~0.18% larger, which can admit or
  drop edges at the boundary → a **golden rebake**. Keep the square branch on the existing call.
  [Source: `.venv/.../osmnx/utils_geo.py::bbox_from_point`, `osmnx/graph.py::graph_from_polygon`]
- **Rotated fetch narrows Overpass too.** `graph_from_polygon` buffers by 500 m and sends the
  polygon's exterior as an Overpass `poly:` filter (`_download_overpass_network`), not a bbox — so the
  download shrinks as well, which is *better* than the epic's conservative "bbox-oriented sources
  shrink less" note. Don't restate the pessimistic claim as measured fact; if you want a number,
  measure it. DEM needs no change: production sizes the raster from `graph_dem_bounds` (post-stage-4
  edge geometry), so it shrinks with the truncated graph; `_padded_bbox` is test-only.
- **The schema bump breaks the committed regression fixtures — plan for it.** `read_entry` →
  `Manifest.from_dict` rejects any non-current `schema_version`, and the four committed cache roots
  under `tests/e2e/fixtures/*/cache/` carry `schema_version: 2`. Left alone, every pinned golden fails
  with exit 2. Only `grenoble_small` can be rebuilt offline; belledonne/vercors/chartreuse need live
  Overpass + IGN and rebuilding them **would** change the graph and force a rebake. So migrate the
  manifests **in place** (metadata edit, `graph.pkl` untouched) — the same in-place conversion Story
  13.2 used. That keeps route output byte-identical.
- **`pipeline_content_hash` shifts anyway.** `_PIPELINE_CONTENT_GLOBS` covers `pipeline/**/*.py`, so
  editing `osm.py` changes every cache key. Combined with the manifest bump that is still **one**
  re-prepare event for real users — expected, not a defect. It does *not* affect the committed
  fixtures: `check_coverage` selects by geometric containment and reads `index.json`, never
  recomputing the key.
- **Decide explicitly: `_GRAPH_PAYLOAD_VERSION`.** Its comment says it "advances with
  `_MANIFEST_SCHEMA_VERSION` — the two describe one format." The graph payload format is *not*
  changing here. Recommendation: leave it at 2 and reword the comment (advancing it would force
  re-stamping all four committed pickles for no benefit). Record the call in Completion Notes.
- **Recommendation on `_INDEX_SCHEMA_VERSION`: don't bump it.** `index.json` is derived state that
  self-heals via `rebuild_index`, and every pre-migration row is a square by construction — so making
  the new geometry fields optional-defaulting-to-square reads old rows correctly and leaves the
  committed fixture `index.json` files valid byte-for-byte. Bumping it would make tests trigger an
  index rewrite inside the committed fixture tree.
- **Sharp edges in the coverage code.** `_read_indexed_entries` rejects a row with `radius_km <= 0`
  (returns `None` → rebuild → likely empty → "No prepared cache exists yet"); a rotated area carries
  the inert `radius_km=0.0`, so this guard must move to the effective geometry.
  `_select_smallest_containing`'s `min(..., key=(e.area.radius_km, hash))` would rank every rotated
  entry as size 0. `_partial_coverage_message` does scalar-radius arithmetic
  (`nearest.area.radius_km - dlat_km`) that is meaningless for a rotated entry. `_gc_superseded_entries`
  needs no change — it already compares via `_canonicalize_area`.
  [Source: cache.py:953-1013, cache.py:1053-1076, cache.py:1152-1196]
- **Message stability matters.** Square-area coverage messages are asserted in
  `tests/unit/test_check_coverage.py`, `tests/integration/test_cache_coverage.py`,
  `tests/e2e/test_coverage_check.py`, and `tests/e2e/test_cli_smoke.py`. Keep them byte-identical.
  For rotated areas the suggested `steeproute-setup` command needs flag spellings Story 15.3 defines
  — route it through **one** helper so 15.3 has a single place to align.
- **Testing standards.** Offline; run per-directory (mixing `tests/unit` and `tests/integration` in
  one invocation imports the wrong `conftest.py`). Type-check with `uv run basedpyright <files>`. If
  `uv run` flakes with ~43 `test_cli_smoke.py` failures or a TLS `UnknownIssuer` error, run
  `uv sync --native-tls` once. [Source: AGENTS.md#Dev environment]

### Project Structure Notes

- All CLI-side changes land in existing modules: [cache.py](src/steeproute/cache.py) (key, schema,
  index, coverage, sidecar) and [pipeline/osm.py](src/steeproute/pipeline/osm.py) (fetch). No new
  module.
- `osm.py` importing `cache._area_to_polygon` crosses from `pipeline/` into the cache layer; if that
  direction is unwanted, pass the polygon in from the orchestrator instead of reaching sideways —
  either is fine, but pick one deliberately and say which in Completion Notes.

### References

- Epic + AC source: [epics.md](_bmad-output/planning-artifacts/epics.md:290) (Epic 15 / Story 15.2)
- Change proposal: [sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md)
  (§"Technical Impact" envelope audit, §4.2 story split, §4.3 architecture edits)
- Architecture: [architecture.md:268](_bmad-output/planning-artifacts/architecture.md) (rotated fetch),
  [architecture.md:332](_bmad-output/planning-artifacts/architecture.md) (area mode, schema bump,
  envelope watch items)
- Previous story: [15-1-generalize-area-model-and-geometry-helpers.md](_bmad-output/implementation-artifacts/15-1-generalize-area-model-and-geometry-helpers.md)
  — `Area.half_extents_km` / `is_square`, and why `radius_km` stayed a required field
- Model: [models.py:27](src/steeproute/models.py) — `Area`
- Fetch: [osm_load](src/steeproute/pipeline/osm.py:58), [`_validate_area`](src/steeproute/pipeline/osm.py:124)
- Key/schema: [`_canonicalize_area`](src/steeproute/cache.py:155), [`Manifest`](src/steeproute/cache.py:189),
  [`rebuild_index`](src/steeproute/cache.py:716), [`_bounds_geojson`](src/steeproute/cache.py:791)
- Coverage: [`_read_indexed_entries`](src/steeproute/cache.py:953),
  [`_select_smallest_containing`](src/steeproute/cache.py:1053),
  [`_partial_coverage_message`](src/steeproute/cache.py:1152), [`check_coverage`](src/steeproute/cache.py:1209)
- Envelope-leak sites: [`area_bbox_wgs84`](src/steeproute/cache.py:919) consumers —
  [regions.py:53](src/steeproute/app/cli_adapter/regions.py) (App 5.1),
  [output.py:297](src/steeproute/output.py) `_search_bbox` (15.3),
  [pipeline/__init__.py:287](src/steeproute/pipeline/__init__.py) (radius in an error string)
- Committed cache fixtures to migrate: `tests/e2e/fixtures/{grenoble_small,belledonne,vercors,chartreuse}/cache/steeproute/`
  (+ each `README.md`); offline rebuild only for `grenoble_small`
  ([regenerate_cache.py](tests/e2e/fixtures/grenoble_small/regenerate_cache.py))
- Tests pinning schema literals: [test_cache.py:216](tests/unit/test_cache.py),
  [test_cache_key.py:162](tests/unit/test_cache_key.py),
  [test_steeproute_setup.py:240](tests/e2e/test_steeproute_setup.py) (also :314, :322)
- Golden harness: [regression.py:138](src/steeproute/regression.py) (FIXTURES),
  `tests/e2e/test_pinned_regressions.py`
- Golden policy: [AGENTS.md](AGENTS.md) §Solver / GRASP

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- `basedpyright` initially reported 8 errors in `_area_from_wire`: the `_is_real_number` guard
  returned plain `bool`, so an early-return `if not _is_real_number(x): return None` did not narrow
  the JSON-decoded `Any` and every `float(x)` looked like `float(Unknown | None)`. Retyped the helper
  as `TypeIs[float]` (Python 3.13 `typing.TypeIs`), which narrows on the negative branch. No runtime
  change.
- Collapsing the manifest `area` parse into the shared `_area_from_wire` lost a diagnostic:
  `"center": ["forty-five", "six"]` degraded from "coordinates are not numeric" to the generic
  "payload is malformed". Restored the specific message with a targeted numeric pre-check in
  `Manifest.from_dict`, keeping the shared parser free of caller-specific messaging.
- Four lambdas in the new osm tests tripped `reportUnknownLambdaType`. Replaced with a
  `_stub_osmnx_fetches` helper using real nested functions (the pattern the file already used for
  `_fake_graph_from_point`), which also removed the duplication between the two fetch-dispatch tests.

### Completion Notes List

- **Fetch dispatch (`pipeline/osm.py`).** `osm_load` branches on `Area.is_square`: square keeps the
  pre-Epic-15 `graph_from_point(dist_type="bbox")` call, non-square fetches
  `graph_from_polygon(area_polygon(area))`. **The square branch had to stay** — verified in the osmnx
  source that `graph_from_point` → `bbox_from_point` → `bbox_to_poly` → `graph_from_polygon`, but
  `bbox_from_point` derives its bbox with `EARTH_RADIUS_M = 6_371_009` (≈1/111.195 deg/km) while
  `_area_to_polygon` uses `1/111`. Passing our ring would have widened the fetch ~0.18% and could
  admit/drop boundary edges — a silent golden rebake. A test now pins the dispatch in both
  directions.
- **`radius_km` is not the square's size.** Three sites had to move off it even for squares, because
  a square spelled as explicit equal extents carries an inert `radius_km=0.0`: the fetch `dist`, the
  canonical hash dict, and the wire `area` block. All now read `half_extents_km`, which returns the
  same float for the classic `Area(center=..., radius_km=r)` — hence byte-identical output.
- **Schema decisions.** `_MANIFEST_SCHEMA_VERSION` 2 → 3 as the AC requires.
  `_GRAPH_PAYLOAD_VERSION` **stays at 2**: its comment claimed it "advances with
  `_MANIFEST_SCHEMA_VERSION`", but it tracks the *graph payload* format, which this story does not
  touch — advancing it would invalidate every on-disk pickle (and all four committed fixtures) for a
  format that did not change. Comment reworded. `_INDEX_SCHEMA_VERSION` **stays at 1**: `index.json`
  is derived state that `rebuild_index` regenerates, a rotated row is purely additive, and every
  pre-migration row is a square that `_area_from_wire` reads correctly (a missing `mode` defaults to
  `center_radius`). Bumping it would have forced tests to rewrite the committed fixture tree.
- **Fixture migration cost one line each.** Because a square's `area` block is byte-identical across
  v2 and v3, migrating the four committed regression caches was `"schema_version": 2` → `3` and
  nothing else — `graph.pkl` untouched, `index.json` untouched, `area` blocks untouched. Same
  in-place approach Story 13.2 used for the v1 → v2 payload change. **All 8 golden tests (fast +
  realistic tiers) pass and `git status tests/e2e/goldens/` is clean — no rebake.** This mattered:
  belledonne/vercors/chartreuse cannot be rebuilt offline, and a live rebuild would have fetched
  different OSM/DEM data and forced a rebake.
- **Coverage sharp edges, all three real.** `_read_indexed_entries`' `radius_km <= 0` guard would
  have rejected *every* rotated row (→ rebuild → empty → "No prepared cache exists yet" for a fully
  prepared cache); `_select_smallest_containing`'s `min` key ranked every rotated entry at size 0,
  handing the choice to a lexicographic hash coin-flip; `_partial_coverage_message` did
  scalar-radius arithmetic that is meaningless against a rotated entry. Containment itself needed no
  change — it already went through `_area_to_polygon`, and a test now pins that a query inside a
  rotated entry's *envelope* but outside the box is declined.
- **Envelope-leak audit.** Two in-scope fixes (`_padded_bbox`, `_assert_non_empty`'s message); two
  sites verified as correct-by-construction (`graph_dem_bounds` reads post-truncation graph geometry,
  so the production DEM already shrinks with the rotated polygon — better than the epic's
  conservative "ingestion shrinks less" note); three deferred sites labelled in-code with their
  owning story rather than left silent.
- **Overpass narrows too.** `graph_from_polygon` buffers 500 m and sends the polygon's exterior as an
  Overpass `poly:` filter (`_overpass._download_overpass_network`), not a bbox — so the OSM download
  shrinks for a rotated area as well. Recorded as an observation from reading the osmnx source, **not
  measured**; the epic's pessimistic framing is conservative but I did not benchmark it.
- **Known wrinkle, deliberately not fixed here.** `cache.area_polygon` now determines what a rotated
  area actually fetches, but `cache.py` is excluded from `_PIPELINE_CONTENT_GLOBS` — so a future edit
  to the rotation math would change rotated graphs without shifting `pipeline_content_hash`, leaving
  stale rotated entries keyed as valid. Latent and rotated-only (unreachable until 15.3 adds the
  flags), and the fix is a module move outside this story's tasks. Recommended fix: move the
  Area-derived geometry primitives into `models.py` (which *is* in the glob) and re-export from
  `cache.py`. Flagged as a follow-up task.
- **Provisional rotated flag spellings.** Messages emit `--width/--height/--angle` for rotated areas.
  Story 15.3 owns the real Click option names; both formatters are single-sourced
  (`_format_area_flags`, `_format_area_geometry`) so aligning them is one edit. Unreachable today —
  no CLI path constructs a rotated area yet.
- **`pipeline_content_hash` shifts** (this story edits three `pipeline/**` files), so real users'
  existing entries re-key. Combined with the manifest bump that is still **one** re-prepare event.
  The committed fixtures are unaffected: `check_coverage` resolves by geometric containment and never
  recomputes the key.
- **Verification.** Full offline suite `uv run --no-sync pytest --cov`: **1091 passed, 17 deselected,
  94% coverage, exit 0** (5m00s). Per-directory before that: unit 774, integration 204, e2e 109.
  Goldens: 8/8 across both tiers, no rebake. `basedpyright` clean (0 errors, 0 warnings) on all 12
  changed source and test files.
- **Code review (low-effort diff pass) — 3 findings, all applied.**
  1. **`_padded_bbox` under-sized a rotated envelope (real bug).** It bounded both axes with
     `max(half_extents_km)`, but a rotated rectangle's axis-aligned envelope is
     `hw·|cos θ| + hh·|sin θ|` east/west and `hw·|sin θ| + hh·|cos θ|` north/south — the per-axis
     maxima of `_area_to_polygon`'s rotated corners. At hw=8, hh=3, θ=10° the true east/west
     half-extent is ~8.40 km, so a uniform 8 km would have clipped the box the raster is meant to
     cover, contradicting the docstring I had written. Now computes both axes properly; `θ=0`
     reduces to each axis's own half-extent, so the square path stays byte-identical (pinned by a
     new test against the historical formula).
  2. **The bearing must not decide which flag a size error names (real bug).** `radius_shorthand`
     included `angle_deg == 0.0`, so a rotated *square* — a legitimate `--radius --angle`
     combination with no extents supplied — reported a bad radius as `--width must be > 0`, a flag
     the user never typed. The angle check moved out of the branch (it applies to every shape) and
     the shorthand test is now purely "were the extents supplied?".
  3. **Removed the duplicated mode dispatch.** `_area_wire_dict` and `_canonicalize_area` carried
     two copies of the same shape branch and field-name list, differing only in rounding — they
     could have drifted on which fields a mode carries. Both are now thin wrappers over one
     `_area_dict(area, *, rounded)`; output byte-identical either way.
  Re-verified after the fixes: unit **786**, integration 204, e2e 109; goldens 8/8 with `git status
  tests/e2e/goldens/` clean; `basedpyright` clean.

### File List

- `src/steeproute/cache.py` (modified — area modes, `_area_wire_dict`/`_area_from_wire`,
  manifest schema v3, `area_polygon`, `_area_km2`, `_has_usable_geometry`, geometry-derived messaging)
- `src/steeproute/pipeline/osm.py` (modified — `graph_from_polygon` dispatch, generalized
  `_validate_area`)
- `src/steeproute/pipeline/__init__.py` (modified — shape-aware `_assert_non_empty` message)
- `src/steeproute/pipeline/dem_download.py` (modified — `_padded_bbox` sizes off half-extents)
- `src/steeproute/output.py` (modified — `_search_bbox` docstring labels the 15.3 leak)
- `src/steeproute/cli/_shared.py` (modified — `validate_area_size` docstring labels the 15.3 leak)
- `src/steeproute/app/cli_adapter/regions.py` (modified — `_to_region_info` docstring labels the App
  5.1 leak)
- `tests/unit/test_osm.py` (modified — fetch-dispatch + validation tests, `_stub_osmnx_fetches`,
  rotated-shorthand flag-naming tests)
- `tests/unit/test_dem_download.py` (modified — `_padded_bbox` rotated-envelope + square-unchanged
  tests)
- `tests/unit/test_cache.py` (modified — rotated `bounds.geojson` / index / GC tests, schema pins)
- `tests/unit/test_cache_key.py` (modified — rotated keying + manifest round-trip tests)
- `tests/unit/test_check_coverage.py` (modified — rotated coverage + messaging tests)
- `tests/e2e/test_steeproute_setup.py` (modified — schema-literal pins, parametrized re-prepare test)
- `tests/e2e/fixtures/belledonne/cache/steeproute/areas/0fdac3e4201d1b2f/manifest.json` (migrated to
  schema v3)
- `tests/e2e/fixtures/vercors/cache/steeproute/areas/88bd11bc7d33b4ad/manifest.json` (migrated)
- `tests/e2e/fixtures/chartreuse/cache/steeproute/areas/82c54e5c5d39f462/manifest.json` (migrated)
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/areas/4c348169d4d0bb0c/manifest.json` (migrated)
- `tests/e2e/fixtures/belledonne/README.md` (migration note)
- `tests/e2e/fixtures/vercors/README.md` (migration note)
- `tests/e2e/fixtures/chartreuse/README.md` (migration note)
- `tests/e2e/fixtures/grenoble_small/README.md` (migration note)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status bookkeeping)

## Change Log

- 2026-07-25: Implemented Story 15.2 — rotated-aware setup fetch (`graph_from_polygon` for
  non-square areas, square path byte-identical), `center_extents_angle` cache-key mode, manifest
  schema v3 with the four committed regression fixtures migrated in place, and coverage generalized
  off the scalar-radius assumption (true-area ranking, rotated index rows, geometry-derived
  messaging). Envelope-leak audit completed: two in-scope fixes, three deferred sites labelled with
  their owning story. Full offline suite green (1091 passed); goldens pass with **no rebake**.
  Status → review.
- 2026-07-25: Code review (low-effort diff pass) — 3 findings, all fixed. Two real bugs
  (`_padded_bbox` under-sized a rotated area's axis-aligned envelope by using `max(hw, hh)` on both
  axes; `_validate_area` named the wrong flag for a rotated radius-shorthand area) plus one
  duplication cleanup (`_canonicalize_area` / `_area_wire_dict` collapsed onto a single
  `_area_dict`). Square-path output byte-identical throughout; goldens still pass with no rebake.
  Status → done.
