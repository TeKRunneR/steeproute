---
title: 'Route report: load basemap tiles + link map↔profile hover'
type: 'bugfix'
created: '2026-06-03'
baseline_commit: '5b16386b4c151f94a9deac8a5a1543bebd4f3c91'
status: 'done'
context: ['_bmad-output/implementation-artifacts/3-10-html-json-output-rendering-with-vendored-assets.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Generated route reports are unusable for validating routes: (1) the basemap is blank because `tile.openstreetmap.org` now returns 403 "referer required" when the report is opened from `file://` (no Referer is sent), and (2) there is no way to correlate a point on the map with its point on the elevation profile, making it hard to judge a route's shape.

**Approach:** Both fixes live in `templates/route.html.j2` (no `output.py`/data-model change). Swap the basemap to an OSM-derived, referer-tolerant tile provider (Carto). Add client-side hover linking between the Leaflet polyline and the Chart.js profile, exploiting that map coordinate `i` and profile point `i` are already the same vertex.

## Boundaries & Constraints

**Always:** Keep the report self-contained — the AC #3 grep (no `<script src>`/`<link>`/`<img src=http>`) must still pass; tile URLs stay JS-constructed strings. Keep the basemap OSM-derived (FR17) with correct attribution for both OSM and the chosen provider. All new behavior stays inside the existing `{% if has_geometry %}` block so degenerate routes are unaffected. Pure vendored/inline assets — no new CDN-loaded library.

**Ask First:** Adding any new vendored asset, runtime dependency, or `output.py` signature/data change (the plan asserts none is needed).

**Never:** No API-key-gated tile provider. No change to route generation, validation, metrics, or the JSON sidecar schema. No new Python dependency.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Report opened from `file://` | route with geometry | Basemap tiles load (no 403); polyline + profile render as before | N/A |
| Hover a point on the map polyline | geometry present | Nearest route vertex's point is highlighted on the profile (active point + tooltip) | mouseout clears highlight |
| Hover a point on the profile chart | geometry present | A marker appears on the map at the corresponding vertex | leaving the chart hides the marker |
| Degenerate route | `has_geometry` false | No map/profile section; no linking JS runs; no console error | guarded by `{% if has_geometry %}` |

</frozen-after-approval>

## Code Map

- `src/steeproute/templates/route.html.j2` -- the only production change: tile-layer provider/attribution (~L95) + new map↔profile hover-sync script inside the geometry block.
- `src/steeproute/output.py` -- read-only reference: `route_geojson` coords (`[lon,lat]`) and `profile_distances`/`profile_elevations` are index-aligned to the same vertex list (`_route_vertices`→`_geojson`/`_profile_series`). No change.
- `tests/unit/test_output.py` -- add basemap + hover-wiring assertions; existing self-containment test must stay green.
- `tests/integration/test_output_on_fixture.py` -- real-fixture render; unchanged behavior must still hold.

## Tasks & Acceptance

**Execution:**
- [x] `src/steeproute/templates/route.html.j2` -- replace the `L.tileLayer` OSM URL with Carto's OSM-derived raster basemap (`https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png`, `subdomains: "abcd"`, `maxZoom: 20`, `referrerPolicy: "strict-origin-when-cross-origin"`); attribution crediting both OpenStreetMap contributors and CARTO.
- [x] `src/steeproute/templates/route.html.j2` -- after the map + chart are built, add a sync script: keep a hidden Leaflet `circleMarker`; on polyline `mousemove` find the nearest vertex index and drive `chart.setActiveElements` + tooltip; on chart `onHover` move/show the map marker at `coords[index]`; clear both on `mouseout`/empty hover.
- [x] `tests/unit/test_output.py` -- assert the rendered HTML references `basemaps.cartocdn.com`, no longer references `tile.openstreetmap.org` as a tile URL, attribution mentions OpenStreetMap + CARTO, and the hover-sync wiring is present (`setActiveElements`, `circleMarker`, a `mousemove` handler, `onHover`).

**Acceptance Criteria:**
- Given a route with geometry opened from `file://`, when the report loads, then basemap tiles render with no 403 (manual check) and attribution credits OSM + CARTO.
- Given geometry is present, when the user hovers the map polyline, then the corresponding profile point is highlighted; and when hovering the profile, then a map marker marks the corresponding location.
- Given a degenerate route, when rendered, then no map/profile/linking code is emitted and nothing errors.
- Given the full suite, when CI runs, then all four gates are green and the self-containment grep still passes.

## Design Notes

Index alignment is the whole trick: `_route_vertices` produces the ordered vertex list that feeds *both* `_geojson` (map, `[lon,lat]`) and `_profile_series` (chart, one entry per vertex). So `geojson.coordinates[i]` ↔ chart data index `i` — no distance interpolation needed. Map→profile: scan the polyline latlngs for the nearest to `e.latlng`, use that index. Chart→map: `chart.getElementsAtEventForMode(e,'index',{intersect:false},true)` gives the index; convert `coords[i]` (`[lon,lat]`) to Leaflet `[lat,lon]`. Set dataset `pointHoverRadius` so the activated profile point is visible. Why a provider swap rather than a header tweak: a `file://` page has no origin, so no `referrerPolicy` value can produce the Referer `tile.openstreetmap.org` now demands — only a referer-tolerant provider fixes it for a local report.

## Verification

**Commands:**
- `uv run ruff check . ; uv run ruff format --check .` -- expected: clean.
- `uv run basedpyright` -- expected: 0/0/0.
- `uv run pytest` -- expected: all pass, including the self-containment and new template-wiring assertions.

**Manual checks:**
- Generate a report and open `route-1.html` in a browser: basemap tiles load (no 403); hovering the route highlights the matching profile point and vice versa.

## Suggested Review Order

**Basemap tile fix (#1)**

- Entry point: swaps referer-gated OSM tiles for OSM-derived Carto + dual attribution.
  [`route.html.j2:98`](../../src/steeproute/templates/route.html.j2#L98)

**Map↔profile hover linking (#2)**

- Profile→map: chart hover marks the route via the shared vertex index.
  [`route.html.j2:146`](../../src/steeproute/templates/route.html.j2#L146)

- Map→profile: nearest-vertex scan drives the chart's active point + tooltip.
  [`route.html.j2:157`](../../src/steeproute/templates/route.html.j2#L157)

**Tests**

- Asserts basemap provider/attribution swap.
  [`test_output.py:289`](../../tests/unit/test_output.py#L289)

- Asserts both-direction hover wiring is rendered.
  [`test_output.py:304`](../../tests/unit/test_output.py#L304)
