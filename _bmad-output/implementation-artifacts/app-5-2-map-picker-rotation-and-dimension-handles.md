# Story 5.2: Map picker rotation and dimension handles

Status: done

<!-- App track (epics-app.md). Story key `app-5-2-*` is `app-`-prefixed to avoid
     collision with the CLI track's `5-2-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want to draw, move, and pick rotated rectangles on the map,
so that I can align the search box to a diagonal range without leaving the app.

## Acceptance Criteria

1. **The selection is an editable rotated rectangle drawn as a true polygon.** In area-pick mode the selection renders as an `L.polygon` from the server-returned `polygon` (never `L.rectangle`, never the envelope), and carries a width handle, a height handle, and a rotation handle so both dimensions and the bearing are editable on the map. Every edit re-resolves through `GET /regions/resolve` and re-renders exactly what the server returns.

2. **A square is still expressible and still spelled as a radius.** The default drop (click → today's 10 km square) and any selection the user has given neither a second dimension nor a bearing send `radius_km` on the wire exactly as today — no `width_km`/`height_km`/`angle_deg` — so the shipped square flow (argv, cache key, coverage, existing green regions) is unchanged. A rectangular/rotated selection can be returned to a square without reloading the page.

3. **Built regions draw as their true shape.** `drawRegions` renders each region's `polygon`, so a rotated built region no longer over-draws as its axis-aligned envelope. No frontend code reads `bounds` for drawing, hit-testing, or any decision — this closes the last leg of the App-side envelope-leak audit that Story 5.1 left open by design.

4. **move-selection translates the rotated box rigidly.** Dragging repositions the whole selection — dimensions and bearing unchanged — with a rigid translation preview during the drag and a server re-resolve on release, as shipped for the square. All handles follow the box. Inert with no selection.

5. **select-region snaps to a built region's exact rotated geometry.** Clicking a green region in select-region mode sets the selection to that region's own shape (the radius spelling for a square entry, dimensions + bearing otherwise), renders it as covered, and enables "Configure query" directly — no manual reproduction of the geometry.

6. **Build and Configure carry the picked shape, and the readout names it honestly.** The `setup` job body and the query config form receive exactly the selection the map shows, in **one** spelling only (never both — a body carrying both is a 422), and the selection readout describes a rotated box by its dimensions and bearing rather than claiming a radius, reusing the existing `format.js` area helper.

7. **The server stays the single geometry authority.** No km→deg conversion, no containment test, and no polygon construction in JS: the polygon drawn is always the one `GET /regions` / `GET /regions/resolve` returned. The only client-side geometry is turning a handle position into an input scalar (a distance from Leaflet's own `map.distance`, a bearing from the center) plus the rigid translation preview.

8. **Frontend-only, conventions and modes held (scope guard).** No `models.py`, `api.py`, or `cli_adapter` change (Story 5.1 shipped the whole transport) and no area field on `params_schema` (the map owns the area). No new dependency and **no Leaflet plugin**; changes stay in `index.html`, `css/app.css`, `js/map-home.js`, with any backend call still going through `api.js`. Modes stay exclusive (a bare map click drops a center only in area-pick; region overlays are clickable only in select-region) and the existing map-home markup assertions stay green.

## Tasks / Subtasks

- [x] Draw selection and regions as true polygons (AC: #1, #3, #7)
  - [x] Replace the selection `L.rectangle(boundsToLatLngs(res.bounds))` with `L.polygon(res.polygon)` in `applyResolution`; same covered/needs-build styling.
  - [x] Replace `drawRegions`' `L.rectangle(boundsToLatLngs(r.bounds))` with `L.polygon(r.polygon)`; keep the `region-overlay` class and the mode-gated click.
  - [x] Drop `boundsToLatLngs` / every `bounds` read once nothing uses it.
- [x] Generalize the selection state to a shape (AC: #2, #6)
  - [x] Replace the single `radiusKm` module state with the picked shape (radius spelling XOR width+height+angle) and derive the `resolveArea` / `createJob` / `openConfigForm` payload from it — exactly one spelling per call.
  - [x] Readout: shape line via `areaGeometry(res)` from `format.js`; relabel the `#sel-radius` row so it isn't radius-specific.
- [x] Dimension and rotation handles (AC: #1, #2, #7)
  - [x] Width and height handles as draggable `divIcon`s at the box's two axis-edge midpoints (derived from the server polygon), each `dragend` setting its **full** dimension and re-resolving.
  - [x] Rotation handle whose `dragend` sets `angle_deg` (bearing clockwise from north, cos(lat)-compensated — see Dev Notes) and re-resolves.
  - [x] A way back to a square (e.g. a reset affordance that restores the radius spelling).
  - [x] Style the new handles in `app.css` alongside `.map-handle` / `.map-move-handle`; update `MODE_HINTS["area-pick"]`.
- [x] Generalize move-selection and select-region (AC: #4, #5)
  - [x] Translate the polygon vertices (`setLatLngs`) plus all handles by the drag delta; `dragend` re-resolves with the shape unchanged.
  - [x] Snap select-region to the clicked region's own spelling (`radius_km` when non-null, else dimensions + bearing).
  - [x] Extend `applyModeInteractivity` to enable/disable all three area-pick handles.
- [x] Verification (AC: all)
  - [x] Extend the map-home markup assertions in `test_app_api.py` for the new picker elements; keep the existing ones green.
  - [x] Drive the handlers with real Leaflet events against a seeded cache (see Testing), plus screenshot proof of the rotated polygon; the drag *feel* is left for the user to confirm.

### Review Findings

- [x] [Review][Patch] Rotation grip can land off-screen on a large or height-dominant box — resolved via user decision: shrunk `ROTATE_HANDLE_OFFSET` from `1.35` to `1.1` (option 2 of the choices presented) rather than pixel-space clamping. Reduces, doesn't eliminate, the off-screen risk on very large boxes; accepted as good-enough pending real drag-feel testing. Verified live: a 10 km-radius square now places the grip 1.10× the height handle's distance from center (was 1.35×). [map-home.js:23-27 (`ROTATE_HANDLE_OFFSET`)]
- [x] [Review][Patch] `.map-rotate-handle` CSS comment claimed the grip sits "outside the box's long edge"; reworded to "height-axis edge (not necessarily the longer edge)" — doc-only, no functional impact. [app.css:233-234]

## Dev Notes

**Post-v1 App Epic 5, Story 5.2 — the picker half; Story 5.1 shipped the whole transport.** `AreaSpec` already accepts `width_km`/`height_km`/`angle_deg`, `argv` already emits the CLI Epic 15 flags, `GET /regions` and `GET /regions/resolve` already return the true `polygon` plus dimensions and bearing, and `api.js`'s `resolveArea` already takes the rotated params (added non-breakingly for this story). Nothing server-side needs to change — this story consumes what exists [Source: app-5-1-rotated-areaspec-argv-and-regions-plumbing.md#Completion Notes List; src/steeproute/app/static/js/api.js:50-65].

**The cos(lat) skew is the one real trap — the bearing is *not* a screen angle.** The CLI rotates the box in a local `cos(lat)` km frame, so a bearing read off container/layer points (or off raw lat/lon deltas) is skewed: Web Mercator is conformal in *degree* space, where 1° lon ≠ 1° lat on the ground. Compensate when turning the rotation handle's position into `angle_deg` — `atan2(Δlon · cos(lat_center), Δlat)` in degrees, clockwise from north. Getting this wrong makes the box visibly lag or lead the cursor at Grenoble latitudes and worsens toward the poles. This is the App-side instance of the skew the change proposal flags [Source: src/steeproute/cache.py:963-1025; sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md#Technical Impact — the one real risk].

**Full dimensions on the wire; a handle sits at a half-extent.** `width_km`/`height_km` are **full** box dimensions (what `--width`/`--height` take). An edge-midpoint handle is one *half*-extent from the center, so `map.distance(center, handle)` must be doubled. This is the mirror of the silent-doubling trap Story 15.3/5.1 warned about — here the failure mode is a box silently half the intended size, with no error [Source: src/steeproute/app/models.py:64-131; src/steeproute/cli/_shared.py:134-213].

**Axis conventions, so the handles land on the right edges.** Before rotation, `width` is the east–west extent and `height` the north–south one; `angle_deg` is a clockwise-from-north bearing that swings the height axis toward the east. The wire polygon's four `[lat, lon]` vertices are in box-local ring order **SW, SE, NE, NW** (the rotated images of `(−w,−h), (+w,−h), (+w,+h), (−w,+h)`), so the plain midpoint of `polygon[1],polygon[2]` is the width-axis edge center and of `polygon[2],polygon[3]` the height-axis edge center — a midpoint of server-authored vertices, no geometry re-derived. Note a 180° bearing describes the same box, so folding the angle into `[0, 180)` is reasonable [Source: src/steeproute/cache.py:1008-1025; src/steeproute/app/cli_adapter/regions.py:94-104].

**Exactly one spelling per request, and `radius_km` can be `null` now.** `AreaSpec` rejects a body carrying both spellings and one carrying neither (422), so the payload builder must emit `radius_km` **or** `width_km`+`height_km` (+`angle_deg`), never a mix. Correspondingly, today's `radiusKm = res.radius_km` in `applyResolution` breaks on a rotated resolution — the server returns `radius_km: null` for any non-square shape, deliberately, rather than an inert `0.0` [Source: src/steeproute/app/models.py:89-131, 272-292; app-5-1…md#Completion Notes List].

**`bounds` is still on the wire — don't read it.** Both region and resolution responses still carry the axis-aligned envelope under the same field name, retained precisely so the pre-5.2 picker kept working; it is strictly larger than a rotated box and must never be drawn or tested against. Deleting the last frontend read of it is what closes the audit [Source: src/steeproute/app/models.py:251-292; src/steeproute/app/cli_adapter/regions.py:148-157].

**Reuse `format.js` for the area wording.** `areaGeometry` already renders `radius 10 km` / `16×6 km box at 35°` and `areaSummary` the compact `r10` / `16×6 km @ 35°`, both mirroring the CLI's own human wording and already used by four screens. Use them for the readout instead of formatting a fifth variant here [Source: src/steeproute/app/static/js/format.js:40-66].

**Handles are `divIcon`s, not plugins.** The vendored Leaflet ships JS/CSS only (no marker images) and the app is buildless with no dependency budget, so every handle is a draggable `L.divIcon` marker — the pattern already used by the radius handle and the move handle. `L.Polygon`, like `L.Rectangle`, is not draggable; the move-selection translation stays a marker-driven `setLatLngs` preview [Source: src/steeproute/app/static/js/map-home.js:66-67, 117-171; app-4-1-map-selection-modes.md#Dev Notes].

**Frontend conventions (unchanged).** Vanilla ES module, no inline handlers, `api.js` is the only URL holder, server is the source of truth (re-resolve, don't mirror geometry). Static assets are served `Cache-Control: no-cache` since Story 4.1, so a reload picks up JS edits — no server restart needed for a pure frontend change [Source: architecture-app.md#Frontend conventions; app-4-1…md#Completion Notes List].

### Project Structure Notes

Edits only — no new file, no backend touch [Source: architecture-app.md#Complete project tree]:

```
src/steeproute/app/static/
├── index.html        ★ readout labels (shape, not radius) + any reset affordance
├── css/app.css       ★ width / height / rotation handle styles
└── js/map-home.js    ★ polygon rendering, shape state, three handles,
                         generalized move + select-region

tests/
└── integration/test_app_api.py   ★ extend the map-home markup assertions
```

- `models.py`, `api.py`, `cli_adapter/**`, `config-form.js`, and `format.js` are **not** edit targets: Story 5.1 landed everything they needed. `openConfigForm(area)` already passes its area object through verbatim to `createJob` [Source: src/steeproute/app/static/js/config-form.js:154-176, 204].

### Testing

Per AGENTS.md: run `tests/unit` and `tests/integration` in **separate** invocations and keep the full offline suite green. There is **no JS unit harness** (buildless) — do not add one; JS is covered by the served-markup assertions in `test_app_api.py` plus a drive-through.

**Browser-pane verification, re-measured 2026-07-26** (this supersedes Story 4.1's and 5.1's pessimistic notes — the blocker there was a *hidden* pane, not the pane itself). The pane must be **open and visible on screen**: while hidden, `requestAnimationFrame` never fires, which stalls Leaflet's tile pipeline and leaves nothing composited, so screenshots fail and the map looks broken. With it visible, confirmed working on this very page: the basemap renders fully, and `computer{left_click}` drives a **real** map click (center dropped, readout updated). Confirmed **not** working: `computer{left_click_drag}` does not drag a Leaflet `divIcon` handle (it panned the map instead, radius unchanged) — `Draggable` needs intermediate `mousemove` events the one-shot gesture doesn't send.

So, concretely for this story:
- **Screenshot the visual outcomes** — the selection drawn as a rotated polygon, handle placement on the right edges, and a rotated built region drawn as its true box rather than its envelope. This is the story's whole point and it *is* observable; don't skip it.
- **Drag paths** (width/height/rotation handles, move-selection) still need real Leaflet events via a temporary debug hook: `marker.setLatLng(...)` then `marker.fire('dragend')` (handlers read `getLatLng()`). Remove the hook afterward. Note `javascript_tool` runs in an isolated world — it can read/write the DOM but cannot patch page globals.
- Drive against a seeded cache with one square and one **rotated** built entry (no network), asserting the resulting `GET /regions/resolve` query string and the rendered polygon: the default click still sends `radius_km` alone; a height-handle drag sends `width_km`+`height_km` and no radius; a rotation drag sends a plausible `angle_deg`; move-selection preserves the shape; select-region on the rotated entry snaps to its dimensions and bearing and enables Configure.
- Still ask the user to confirm the *feel* of the drag interaction (does the box track the cursor, is the bearing intuitive) — that is the one thing no harness shows [Source: app-4-1…md#Debug Log References; app-5-1…md#Testing].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 5.2: Map picker rotation and dimension handles] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/epics-app.md#FR15] — rotated-rectangle map selection; arbitrary polygons out of scope
- [Source: _bmad-output/planning-artifacts/epics-app.md#UX-DR1 (revised again)] — second dimension handle + rotation handle; `L.polygon` overlays; square still expressible; move translates rigidly; select-region snaps
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#Post-v1 update (2026-07-24, App Epic 5)] — conventional picker, no wireframe warranted
- [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule (post-v1 note, App Epic 5)] — argv + regions seams already done; `params_schema` excludes area fields
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md#Technical Impact] — the cos(lat) skew and the envelope-leak audit
- [Source: src/steeproute/app/static/js/map-home.js:69-105, 117-171, 187-217] — `boundsToLatLngs` / `applyResolution` / `ensureHandle` / `ensureCenterMarker` / `applyModeInteractivity` / `drawRegions` / the mode-gated map click: the code this story generalizes
- [Source: src/steeproute/app/static/js/api.js:50-65] — `resolveArea(lat, lon, radiusKm, {widthKm, heightKm, angleDeg})`, the transport already in place
- [Source: src/steeproute/app/models.py:64-131, 251-316] — `AreaSpec` shape rule + `dimensions_km`; `RegionBounds` as an envelope; `AreaGeometry` / `RegionInfo` / `AreaResolution` wire fields
- [Source: src/steeproute/app/api.py:167-224] — `GET /regions` and `GET /regions/resolve` (the rotated query surface and its 422s)
- [Source: src/steeproute/app/cli_adapter/regions.py:68-104] — `_geometry_fields` / `_polygon_latlon`: what the wire polygon is and its vertex order
- [Source: src/steeproute/cache.py:963-1057] — `_area_to_polygon` (km-frame rotation, bearing convention, ring order) vs `area_bbox_wgs84` (the envelope)
- [Source: src/steeproute/cli/_shared.py:134-213] — `resolve_area`: full dimensions halved once, literal square construction, one-spelling rule
- [Source: src/steeproute/app/static/index.html:26-66] — the picker panel, mode control, and readout this story edits
- [Source: tests/integration/test_app_api.py:573-595] — the map-home markup + no-cache assertions that must stay green
- [Source: app-4-1-map-selection-modes.md] — shipped mode semantics, `divIcon`-not-plugin rationale, and the Leaflet-event drive-through technique
- [Source: app-5-1-rotated-areaspec-argv-and-regions-plumbing.md#Completion Notes List] — what shipped server-side, and the "a rotated region still draws as its envelope until 5.2" hand-off

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- **Browser-pane verification actually worked this time — the earlier "screenshots
  time out" ceiling was a hidden pane, not a pane limitation.** Re-measured on
  Claude Code 2.1.219 immediately before this story: with the preview pane *open
  and visible*, the map renders fully and screenshots capture. While hidden the
  page is `visibilityState: "hidden"` and `requestAnimationFrame` never fires,
  which stalls Leaflet's tile pipeline (and this file's own rAF-gated
  `invalidateSize`) and leaves nothing composited. Claude cannot open the pane;
  the user must. Real map clicks *are* drivable (`computer{left_click}`); marker
  drags are **not** (`left_click_drag` panned the map instead — Leaflet's
  `Draggable` wants intermediate `mousemove`s), so handle drags were driven via a
  temporary `window.__dbg` hook firing real `marker.fire('dragend')`. Hook removed
  and its absence re-verified (`typeof window.__dbg === "undefined"`, no console
  errors).
- **A grey dashed hairline on a topo basemap is effectively invisible in a
  downscaled screenshot.** I twice could not tell whether the rotated selection
  had rendered, even though the DOM showed the path with its stroke and dash.
  Resolved by temporarily restyling the polygon bright red — which showed an
  unmistakable 20×6 km box at 35° running SW–NE across the Grenoble valley, i.e.
  exactly the diagonal-range shape the epic exists for. Worth remembering: verify
  a thin overlay by *inspecting the path* (class/stroke/bbox) or by temporarily
  fattening it, not by eyeballing a raster.
- **Handle-drag arithmetic checked against the trap the story flagged.** On a
  10 km-radius square the height handle sits exactly 10 km north of center (one
  half-extent ✓); dragging it to 3 km north produced `height_km: 6.01` (full) with
  `width_km: 20` untouched — no silent halving or doubling. Cross-checked the drawn
  ring with `map.distance`: edges measured 20.04/6.02/20.03/6.02 km against the
  requested 20 × 6.01.
- **The cos(lat) bearing compensation lands exactly on the server's frame.**
  Aiming the rotation handle at a computed true 35° bearing produced
  `angleDeg: 35` and a ring with four distinct latitudes (an axis-aligned box has
  two). An uncompensated screen-space `atan2` would have been off by the lon/lat
  skew at Grenoble's latitude.
- **Envelope-leak closure proven, not assumed.** Against an isolated cache
  (scratchpad launcher, `:8011`, offline, one rotated 16×6 @ 35° entry + one square
  r4 entry): the rotated overlay drew 4 vertices with **0** of them on its
  envelope corners, while the square drew 4 corners on its envelope (envelope ==
  box, correct). Before this story a rotated region drew as the envelope.
- **Wire payloads carry exactly one spelling.** Captured the `POST /jobs` body by
  temporarily wrapping `fetch` (rejecting the request so no real setup job was ever
  queued): a square selection sent `{center, radius_km: 1}` — byte-for-byte today's
  shape, no width/height/angle — and a rotated one sent
  `{center, width_km, height_km, angle_deg}` with no `radius_km`.
- Behavioural sweep, all confirmed live: fresh drop → radius spelling + reset
  hidden; reset → `radius 10 km` from a 20 km-wide box; move-selection → center
  moved, `shapeUnchanged: true`; select-region on a square → snap + Configure
  enabled; select-region on the *rotated* entry → snapped to `16×6 km box at 35°`,
  covered, Configure enabled.

### Completion Notes List

- **The picked shape is the single source of truth for the wire, and is never
  re-derived from the resolve response.** Two forms mirroring the CLI flag surface
  (`{kind:"radius", radiusKm}` / `{kind:"box", widthKm, heightKm, angleDeg}`), so
  exactly one spelling is ever sent. Deriving the spelling *back* from the response
  would have made it flip-flop under the user: the server reports a rotated square
  as dimensions with `radius_km: null` (`Area.is_square` folds in the bearing), so
  a round-trip would silently convert the radius spelling away.
- **A square keeps the radius spelling — deliberately, including on a width drag.**
  A fresh drop and a width-handle drag on a square both stay `radius_km`, exactly
  the shipped radius-handle behaviour; only a *second dimension* or a *bearing*
  promotes the selection to the box spelling. That is what keeps the square path's
  argv, cache key, and coverage bit-for-bit unchanged, and it means the common case
  is untouched by this story.
- **Handles are placed from the server's own ring, so rotation needs no client
  geometry.** The polygon arrives in box-local SW/SE/NE/NW order, so the width
  handle is the plain midpoint of SE→NE and the height handle of NE→NW — rotation
  is already baked in. The rotation grip is those points pushed 1.35× outward from
  the center (handle placement only, never the area).
- **Only two scalars are computed client-side, both inputs rather than geometry:** a
  half-extent from `map.distance` (Leaflet's own geodesy, doubled for the wire) and
  a bearing in the same local cos(lat) km frame the CLI rotates in, folded into
  [0, 180) since a 180° bearing is the same rectangle. Everything drawn is the
  polygon the server returned.
- **`bounds` is now read nowhere in the frontend.** `boundsToLatLngs` is gone and
  both overlays draw `polygon`, closing the App-side envelope leak that Story 5.1
  intentionally left open so the pre-5.2 picker kept working.
- **"Reset to square" keeps the width as the new side** (`radiusKm = widthKm / 2`),
  so the dimension the user sized first survives — predictable, and it restores the
  radius spelling rather than emitting equal explicit extents.
- **Frontend-only, as scoped.** No `models.py`, `api.py`, or `cli_adapter` change;
  `params_schema` untouched (the area stays map-owned). No new dependency, no
  Leaflet plugin — the two new handles are `divIcon`s like the shipped ones.
- **Validation.** Full offline suite `uv run --no-sync pytest --cov`: **1211 passed,
  18 deselected, 96% coverage** (4m44s) — unchanged count, since this story adds
  assertions to the existing map-home markup test rather than new test functions
  (there is no JS harness by established App convention). `ruff check`/`format`
  clean, `basedpyright` 0/0 on the changed test file. No Python source changed, so
  no golden, cache-key, or `pipeline_content_hash` movement is possible.

### File List

- `src/steeproute/app/static/js/map-home.js` (modified — shape state + one-spelling
  payload builder, `L.polygon` selection and region overlays, width/height/rotation
  handles with the cos(lat) bearing and half→full dimension conversion, rigid
  rotated translation in move-selection, region-spelling snap in select-region,
  reset-to-square; `boundsToLatLngs` and every `bounds` read removed)
- `src/steeproute/app/static/index.html` (modified — readout row is now "Shape"
  `#sel-shape` instead of the radius-only `#sel-radius`; `#reset-square-btn`;
  area-pick hint copy)
- `src/steeproute/app/static/css/app.css` (modified — `.map-handle-height`
  cursor, `.map-rotate-handle`, `.link-btn`)
- `tests/integration/test_app_api.py` (modified — map-home markup assertions for
  `#sel-shape` / `#reset-square-btn` and the retirement of `#sel-radius`)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified — story
  status tracking)

## Change Log

| Date | Change |
|---|---|
| 2026-07-26 | Story drafted from epics-app.md (Story 5.2 / FR15 / UX-DR1 revised again) + the 2026-07-24 rotated-rectangle sprint-change-proposal, on top of the shipped Story 5.1 plumbing and CLI Epic 15. Frontend-only: polygon rendering, dimension + rotation handles, generalized move/select-region. Status → ready-for-dev. |
| 2026-07-26 | Testing section revised after re-measuring the Browser pane: map screenshots and real map clicks work when the pane is visible (the earlier "screenshots time out" note was a hidden-pane artifact), so the visual outcomes this story exists for are directly verifiable. Marker drags remain undrivable via `left_click_drag` and still need `marker.fire('dragend')`. |
| 2026-07-26 | Implemented the rotated-rectangle picker: shape state driving a one-spelling wire payload, `L.polygon` for both the selection and built-region overlays (closing the App envelope leak), width/height/rotation handles placed off the server's own ring, cos(lat)-compensated bearing, rigid rotated move, region-spelling select-region snap, and reset-to-square. Square path deliberately unchanged (fresh drop and width drag keep `radius_km`). Verified live: handle arithmetic (10 km half-extent → `height_km: 6.01`, edges measured 20.04/6.02 km), `angleDeg: 35` exact, rotated overlay with 0 vertices on its envelope corners against an isolated seeded cache, and both `POST /jobs` bodies captured without queueing a job. Screenshot proof of a 20×6 km box at 35° over the Grenoble valley. Full suite 1211 passed; ruff/basedpyright clean. Status → review. |
| 2026-07-26 | Code review (low effort, single inline pass — Sonnet 5, per user's requested scaling-down of the skill's default 3-subagent fan-out). 2 findings, both resolved: rotation grip could land off-screen on a large/height-dominant box (`ROTATE_HANDLE_OFFSET` 1.35× → 1.1×, per user decision — reduces but doesn't eliminate the risk on very large boxes) and a stale CSS comment ("long edge" → "height-axis edge"). Re-verified: grip-to-center ratio measured 1.10× live, integration suite (67) + ruff/basedpyright clean. No blocking or correctness issues found. |
