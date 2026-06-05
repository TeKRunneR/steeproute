---
title: 'Route report: signed-slope profile + search-area overlay'
type: 'feature'
created: '2026-06-04'
status: 'done'
context: ['_bmad-output/implementation-artifacts/spec-report-map-tiles-and-hover.md']
baseline_commit: '3d3e93315f848ba8d6269ab6c7d0401f99a31daf'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Two report-readability gaps. (1) The elevation profile's slope coloring is a single green→red ramp that clamps descents to zero (`route.html.j2:116`), so downhill steepness is invisible and the hover box shows no slope value at all. (2) There is no way to see a route's extent relative to the total searched area, so it's hard to tell whether the top-N routes cluster because the area is limited or because of the solver.

**Approach:** All client-side in `templates/route.html.j2` except one minimal plumbing change. (a) Replace `gradientColor` with a signed diverging scheme (blue ↓ / neutral flat / red ↑, scaled by steepness) and add a Chart.js tooltip line showing the slope — averaged over the two segments adjacent to the hovered point. (b) Pass the query `Area` to `render()` and draw its `2·radius_km` bbox as a thin stroked `L.rectangle` beneath the route. The initial view stays fitted to the route (the primary thing to inspect); the overlay sits beyond it and is seen by zooming/panning out — a secondary check.

## Boundaries & Constraints

**Always:** Keep the report self-contained — the self-containment grep (no `<script src>`/`<link>`/`<img src=http>`) must stay green; no new CDN-loaded or vendored asset. All new map/profile behavior stays inside the existing `{% if has_geometry %}` block. The search-area rectangle is stroke-only (no/low fill) so it covers minimal map area and never obscures the route; the route polyline stays visually on top. Existing map↔profile hover linking and the OpenTopoMap basemap are unchanged.

**Ask First:** Any change beyond the single `area: Area` parameter added to `render()` and its one call site — in particular touching the JSON sidecar schema, `_build_metadata`/provenance, or `SolverParams`.

**Never:** No clipping the overlay to the trail network and no convex hull — the user wants the raw query bbox, not "where trails exist". No change to route generation, validation, metrics, or the solver. No new Python dependency. No change to the JSON sidecar schema.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Hover an interior profile point | geometry present | Tooltip shows elevation + `slope: ±X%`, computed as the mean of the two adjacent segment slopes | N/A |
| Hover the first/last profile point | geometry present | Slope falls back to the single available adjacent segment | N/A |
| Steep vs gentle descent segments | slopes < 0 | Segments colored on the blue (down) ramp scaled by steepness, visibly distinct from each other and from ascents | N/A |
| Render any route | `area` = center + `radius_km` | `2·radius_km` square drawn as a thin stroked rectangle; route renders on top; initial view stays fitted to the route, so the rectangle may extend beyond the viewport | N/A |
| Degenerate route | `has_geometry` false | No map/profile/overlay emitted; no JS runs; no console error | guarded by `{% if has_geometry %}` |

</frozen-after-approval>

## Code Map

- `src/steeproute/templates/route.html.j2` -- main change: rewrite `gradientColor` (signed, two ramps) + add tooltip `callbacks` slope line; add the `L.rectangle` search-area overlay from injected bbox + fit bounds to box+route union.
- `src/steeproute/output.py` -- add `area: Area` param to `render()`; compute the bbox lat/lon deltas from `center`+`radius_km` and pass a `search_bbox` into the template context. `_build_metadata` and the JSON sidecar untouched.
- `src/steeproute/cli/query.py` -- pass the existing `area` into the `output.render(...)` call (~L218).
- `src/steeproute/models.py` -- read-only reference: `Area(center, radius_km)` where `radius_km` is the bbox half-side.
- `tests/unit/test_output.py` -- update the `_render` helper for the new `area` arg; assert overlay + slope-tooltip wiring; self-containment, basemap, and hover assertions stay green.

## Tasks & Acceptance

**Execution:**
- [x] `src/steeproute/output.py` -- add an `area: Area` parameter to `render()`; from `center`+`radius_km` compute the bbox edges (south/west/north/east) and pass a `search_bbox` into the template context. Leave `_build_metadata` and the JSON sidecar unchanged.
- [x] `src/steeproute/cli/query.py` -- pass the existing `area` into the `output.render(...)` call.
- [x] `src/steeproute/templates/route.html.j2` -- replace `gradientColor(g)` with a signed diverging mapper: descending → blue ramp, flat → neutral, ascending → red ramp, each scaled by `|slope|` clamped to ~0.30. Keep per-segment slope as the coloring input (straightforward — no smoothing yet).
- [x] `src/steeproute/templates/route.html.j2` -- add Chart.js tooltip `callbacks` emitting a `slope: ±X%` line whose value is the average of the two segments adjacent to the hovered vertex (single segment at the endpoints).
- [x] `src/steeproute/templates/route.html.j2` -- after the polyline, add the search-area overlay: `L.rectangle` from `search_bbox` with a visible stroke and no/low fill, layered so the route stays on top. Leave the existing `fitBounds(line.getBounds(), ...)` as-is — the initial view stays fitted to the route, not the box.
- [x] `tests/unit/test_output.py` -- update the `_render` helper signature for the new `area` arg; assert the rendered HTML contains the overlay (`L.rectangle` + the injected bbox values) and the slope-tooltip wiring; verify self-containment, basemap, and existing hover assertions still pass.

**Acceptance Criteria:**
- Given a route with geometry, when the report renders, then a thin stroked rectangle marks the `2·radius_km` search bbox and the route renders on top, while the initial view stays fitted to the route (the rectangle is reached by zooming/panning out).
- Given the profile, when one descent is steeper than another, then their colors differ (downhill steepness is now visible), and ascents vs descents use visibly different hues.
- Given a hovered profile point, when the tooltip shows, then it includes a signed slope percentage equal to the mean of the two adjacent segments (single segment at the ends).
- Given the full suite, when CI runs, then all gates including the self-containment grep are green.

## Spec Change Log

## Design Notes

- **Area is a square, not a disk:** `radius_km` is the bbox half-side (`osmnx ... dist_type="bbox"`), so the overlay is a rectangle. An equirectangular delta is fine for a visual-only marker — it need not byte-match osmnx's bbox since it drives no computation: `dlat = radius_km/111.32`, `dlon = radius_km/(111.32·cos(lat))`.
- **Initial view stays fitted to the route**, not the box — inspecting a single route is the primary use; seeing the searched extent is a secondary check done by zooming/panning out. The box edges therefore fall off-screen on open, which is intended. A stroke-only rectangle covers minimal area when it is in view.
- **Diverging color** (slope `s`, clamp `c=0.30`): up → `t=min(s,c)/c`, neutral→red; down → `t=min(-s,c)/c`, neutral→blue; `|s|` near 0 → neutral. Keep hues legible on the white background.
- **Tooltip slope at vertex `i`** = mean of `segBefore=(e[i]-e[i-1])/(d[i]-d[i-1])` and `segAfter=(e[i+1]-e[i])/(d[i+1]-d[i])`; at `i=0` use `segAfter` only, at the last point use `segBefore` only. Coloring stays per-segment for now — smoothing is deferred until real examples are eyeballed (user's call).

## Verification

**Commands:**
- `uv run ruff check . ; uv run ruff format --check .` -- expected: clean.
- `uv run basedpyright` -- expected: 0/0/0.
- `uv run pytest` -- expected: all pass, including the self-containment and updated `test_output.py` assertions.

**Manual checks:**
- Generate a report and open `route-1.html`: a thin rectangle marks the search area with the route clearly on top; descents show graded blue and climbs graded red; hovering a profile point shows a signed slope %.

## Suggested Review Order

**Search-area overlay (the one boundary-crossing change)**

- Entry point: the new `area` param threaded into `render()` — the only signature change.
  [`output.py:60`](../../src/steeproute/output.py#L60)
- Equirectangular bbox math; visual-only, documented as not needing osmnx parity.
  [`output.py:276`](../../src/steeproute/output.py#L276)
- Computed once before the route loop and injected into the template context.
  [`output.py:96`](../../src/steeproute/output.py#L96)
- Production call site passes the existing `area` in the matching slot.
  [`query.py:221`](../../src/steeproute/cli/query.py#L221)
- Rectangle added before the polyline (route stays on top); `fitBounds` left on the route.
  [`route.html.j2:110`](../../src/steeproute/templates/route.html.j2#L110)

**Elevation-profile slope readability**

- Diverging color: signed slope → blue (down) / neutral / red (up), clamped to 0.30.
  [`route.html.j2:128`](../../src/steeproute/templates/route.html.j2#L128)
- Tooltip slope line: mean of the two adjacent segments, single segment at the ends.
  [`route.html.j2:178`](../../src/steeproute/templates/route.html.j2#L178)

**Tests**

- New assertions for the overlay + slope-tooltip wiring; `_render` helper gains the `area` arg.
  [`test_output.py:348`](../../tests/unit/test_output.py#L348)
