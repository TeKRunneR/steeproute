# Story 4.1: Map selection modes (area-pick / move / select-region)

Status: done

<!-- App track (epics-app.md). Story key `app-4-1-*` is `app-`-prefixed to avoid
     collision with the CLI track's `4-1-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want distinct map modes so I can drop a new area, reposition the whole selection, or click a built region to query it directly,
so that one mode never has to do double duty and querying an existing region doesn't require re-deriving its geometry by hand.

## Acceptance Criteria

1. **Visible, switchable mode control.** S1 map home shows a mode control offering three modes — **area-pick** (default), **move-selection**, and **select-region**. Switching modes is instant, changes no server state, and never discards the current selection.

2. **area-pick is exactly today's behavior (regression).** In area-pick mode, clicking the map drops a new center and the draggable handle sets the radius, identical to Epic 1 (Story 1.6) — the same click→`resolveArea`→render flow, server-authoritative bbox/coverage, Build / Configure-query button gating, and "Build this region first" prompt over grey.

3. **move-selection repositions the whole box.** In move-selection mode, dragging the current selection moves it as a unit — the center follows the drag, `radius_km` is unchanged — and on release the bbox + green/grey coverage re-resolve from the server (`GET /regions/resolve`), updating the readout and button states. With no current selection the mode is inert (nothing to move).

4. **select-region snaps to a built region.** In select-region mode, clicking a built (green) region overlay snaps the selection to that region's **exact** geometry from `GET /regions` (its `center`, `radius_km`, `bounds`), renders it as covered, and enables "Configure query" directly — no manual center/radius reproduction. Opening the config form from there passes that snapped area unchanged.

5. **Modes are exclusive — no double duty.** Region overlays are clickable **only** in select-region mode (inert in area-pick and move-selection); a bare map click drops a new center **only** in area-pick mode. Switching away from a mode leaves no lingering interaction bound.

6. **Frontend-only, conventions held (scope guard).** No backend change (all geometry already server-authored via `GET /regions` and `GET /regions/resolve`); no new dependency and **no Leaflet plugin** (buildless). Changes stay within `index.html`, `css/app.css`, and `map-home.js`; any backend call still goes through `api.js`. No config-form (4.2), town-label, or params-view (4.3) work here.

## Tasks / Subtasks

- [x] Mode control UI (AC: #1)
  - [x] Add a mode control to the picker panel in `index.html` (a radio group), area-pick preselected; style in `css/app.css` to match the existing panel.
  - [x] Track the active mode as local state in `map-home.js`; update the `#picker-hint` copy per mode.
- [x] Gate existing interactions by mode (AC: #2, #5)
  - [x] Make the `map.on("click")` center-drop fire **only** in area-pick mode; leave the area-pick path otherwise untouched.
  - [x] Ensure switching modes rebinds/unbinds cleanly (`applyModeInteractivity` toggles handle drag, adds/removes the move handle, toggles the overlay cursor class).
- [x] move-selection (AC: #3)
  - [x] Let the user drag the whole selection via a draggable center divIcon; center follows, `radius_km` fixed; re-resolve via the existing `resolveAndRender` on release. Inert when no selection exists.
- [x] select-region (AC: #4, #5)
  - [x] Make green region overlays clickable in select-region mode only; on click, snap the selection to the clicked region's geometry (server-authored) and enable Configure query.
- [x] Verification (AC: all)
  - [x] Browser drive-through against the real cache (4 Grenoble regions); extended the `test_app_api.py` map-home markup assertions for the mode control (kept the existing ones green).

## Dev Notes

**This is post-v1 App Epic 4, Story 4.1 — additive on the shipped map home (Story 1.6); no rollback.** Area-pick must stay byte-for-byte the delivered behavior; the two new modes layer beside it. Frontend-only: the server already authors all geometry, so there is no `cli_adapter`/API/model change here [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-17-app-ux-improvements.md#Technical impact (per story)].

**Server stays the single geometry authority — do not re-derive km→deg or containment in JS.** This was the one code-review finding on Story 1.6: the client must pass `(center, radius_km)` through and render what the server returns. Reuse `resolveArea` / the existing `resolveAndRender` + `applyResolution` path for both new modes so coverage can't drift from query-side `check_coverage` [Source: src/steeproute/app/static/js/map-home.js:66-99; app-1-6-map-home-pick-an-area-and-build-a-region.md#Completion Notes List (code-review fix)].
- **move-selection release** → set the module `center` to the dragged position, then `resolveAndRender(radiusKm, { moveHandle: true })` — same call the radius-handle `dragend` already makes [Source: src/steeproute/app/static/js/map-home.js:110-113].
- **select-region click** → set `center` to the region's `center`, then `resolveAndRender(region.radius_km, { moveHandle: true })`; the region is built so the result is `covered`, which already enables Configure and disables Build in `applyResolution` [Source: src/steeproute/app/static/js/map-home.js:66-89].

**Dragging a whole rectangle needs the native path — Leaflet `L.Rectangle` is not draggable and no plugin may be added (buildless, NFR5).** The idiomatic no-plugin approach mirrors the existing radius handle: a **draggable center `divIcon` marker** whose `drag` translates the selection rectangle live and whose `dragend` re-resolves — the same `L.divIcon` + `dragend` pattern already used for the radius handle (the vendored Leaflet ships JS/CSS only, so any marker must be a `divIcon`, never an image marker) [Source: src/steeproute/app/static/js/map-home.js:53,101-114; app-1-6…#Debug Log References (divIcon, not marker image)]. This is a hint, not a mandate — any buildless native solution that satisfies AC #3 is fine.

**Region overlays currently carry no data or click handler.** `drawRegions` draws each `RegionInfo` as a plain `L.rectangle` with class `region-overlay`; to support select-region, keep the per-overlay region record (center/radius_km/bounds) so its click can snap the selection [Source: src/steeproute/app/static/js/map-home.js:116-125]. Guard against the region click also triggering the generic map click — mode-gating the map click (AC #5) is the clean fix; `L.DomEvent.stopPropagation` on the overlay is a belt-and-suspenders option.

**`RegionInfo` shape** (from `GET /regions`, already fetched by `loadRegions`): `{ cache_key_hash, center:[lat,lon], radius_km, bounds:{south,west,north,east} }` — no re-fetch needed, snap off the already-loaded list [Source: src/steeproute/app/models.py:198-208; src/steeproute/app/static/js/api.js:36-38].

**Frontend conventions (unchanged since Story 1.5/1.6).** Vanilla ES module, no inline handlers, `api.js` is the only URL holder, server is the source of truth (re-fetch/re-resolve, don't mirror geometry). `invalidateSize()` after layout stays as-is [Source: _bmad-output/planning-artifacts/architecture-app.md#Frontend conventions; src/steeproute/app/static/js/map-home.js:42].

### Project Structure Notes

Target tree — this story **edits** the starred files only; no new files, no backend touch [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree]:

```
src/steeproute/app/static/
├── index.html        ★ (edit) add the mode control to the picker panel
├── css/app.css       ★ (edit) style the mode control
└── js/map-home.js    ★ (edit) mode state + gate click / add move + select-region
```

- Region overlays and coverage geometry already come from `GET /regions` / `GET /regions/resolve`; no `cli_adapter`, `api.py`, or `models.py` change [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-17-app-ux-improvements.md#4.1 (map modes)].

### Testing

Per AGENTS.md: run `tests/unit` and `tests/integration` in **separate** invocations; the full offline suite must stay green. There is **no JS unit harness** (buildless) — the frontend is covered by the served-markup assertions in `test_app_api.py` plus a `run`-skill / browser drive-through; do **not** add a JS test runner [Source: app-1-6…#Testing]. The existing `id="build-btn"` / `id="configure-btn"` / `map-home.js` markup assertions must remain valid after the `index.html` edit [Source: tests/integration/test_app_api.py:520-522,358]. Drive-through (seeded cache with a built region + no network) should exercise: default area-pick click-drop still works; move-selection drag repositions + re-resolves; select-region click on the green overlay snaps + enables Configure; region overlays inert and map click inert outside their owning modes.

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 4.1: Map selection modes (area-pick / move / select-region)] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/epics-app.md#FR11] — map selection modes; modes separate so area-pick never doubles as region-select
- [Source: _bmad-output/planning-artifacts/epics-app.md#UX-DR1 (revised)] — area-pick stays default/unchanged; overlays inert except in select-region
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-17-app-ux-improvements.md#Section 2 — Impact Analysis] — frontend-only, no backend change; geometry from GET /regions
- [Source: src/steeproute/app/static/js/map-home.js] — the file this story edits: click handler, `resolveAndRender`/`applyResolution`, `ensureHandle` divIcon/dragend, `drawRegions`
- [Source: src/steeproute/app/static/index.html:26-48] — the picker panel the mode control is added to
- [Source: src/steeproute/app/static/js/api.js:36-53] — `listRegions()` / `resolveArea()` (the only URL holders reused; no new endpoint)
- [Source: src/steeproute/app/models.py:185-209] — `RegionInfo` / `RegionBounds` shape returned by `GET /regions`
- [Source: app-1-6-map-home-pick-an-area-and-build-a-region.md] — shipped map-home behavior, divIcon handle rationale, and the server-authoritative-geometry code-review fix this story must preserve

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Frontend-only, no backend touch.** Confirmed the three modes need nothing new server-side: `GET /regions` already returns each built region's exact `center`/`radius_km`/`bounds`, and `GET /regions/resolve` owns all km→deg + coverage. `map-home.js` re-derives no geometry — move-selection shifts the existing server-authored bbox rigidly by the drag delta for a live preview, then re-resolves on release.
- **Move-selection uses a draggable center divIcon (no Leaflet plugin).** `L.Rectangle` isn't draggable and the app is buildless, so — mirroring the shipped radius handle — a `map-move-handle` divIcon marker is added only in move-selection mode; its `drag` translates the rectangle + radius handle live, its `dragend` sets `center` and calls the same `resolveAndRender(radiusKm, {moveHandle:true})` the radius handle already uses.
- **Exclusivity via one gate + `applyModeInteractivity`.** The `map.on("click")` center-drop now returns early unless `mode === "area-pick"`; region overlays' click handler returns early unless `mode === "select-region"` (plus `L.DomEvent.stopPropagation`). `applyModeInteractivity` enables/disables radius-handle dragging, adds/removes the move handle, toggles `select-region-active` on the map container (drives the overlay pointer cursor), and updates the hint.
- **Browser drive-through (real default cache — 4 Grenoble regions, no network).** Ran `steeproute-app`, opened the in-app Browser pane. Verified live: mode control renders with area-pick default; switching modes updates the hint, toggles `select-region-active`, and creates/removes the move handle (only in move-selection, only with a selection). **select-region:** clicking a green overlay snapped the selection to region 7716561f (center 45.2600, 5.7880 · r1.0 km), coverage "cached — ready to query", **Configure query enabled / Build disabled** — the core new behavior, confirmed end-to-end.
- **Environment ceiling on two interactions.** The Browser pane runs Leaflet in `leaflet-touch` mode, where map-level clicks and marker drags come from Leaflet's own pointer/tap synthesis, not raw DOM events — so synthetic `MouseEvent`/`PointerEvent` don't drive them (direct vector-layer clicks like the region overlays, and radio `change`, do fire). Real clicks via the `computer` tool need a screenshot for coordinate calibration, and screenshots time out on this map page (known pane limitation).
- **Follow-up after user report ("clicks always drop a new box in every mode").** The initial hand-off left the map-click regression (AC #2) and move drag (AC #3) undriven, so I proved the guard logic directly: temporarily exposed the Leaflet map (`window.__dbg`) and fired **real** `map.fire('click', {latlng})` through the actual handler. Result — mode updates on radio change; a click in move-selection / select-region leaves `center` null and creates no box (guard works); a click in area-pick sets `center` (box drops); a region-overlay click in select-region snaps. **The on-disk code was correct.** The user's symptom was a **stale browser-cached `map-home.js`** (the pre-4.1 version, no mode logic) served alongside fresh HTML — `StaticFiles` sent no `Cache-Control`, so the browser's heuristic cache served the old JS without revalidating. Root cause + fix below; debug hook removed after confirming.
- **Fix — no-cache for buildless assets (`main.py`).** Assets carry no content hash, so a `_NoCacheStaticFiles` subclass and a `_page()` helper now serve the app's own JS/CSS and HTML shells with `Cache-Control: no-cache` (revalidate each load; the existing ETag makes an unchanged file a cheap 304). The immutable vendored Leaflet bundle keeps ordinary caching. Verified over the wire: `/static/js/map-home.js` and `/` return `cache-control: no-cache`, `/vendor/...` unchanged.

### Completion Notes List

- **Three exclusive selection modes (FR11 / UX-DR1), frontend-only.** `index.html` gains a `#mode-control` radio group (area-pick default); `map-home.js` tracks `mode` and gates every interaction through it; `app.css` styles the control, the `map-move-handle` divIcon, and the `select-region-active` overlay cursor.
- **area-pick unchanged (AC #2).** Same click→`resolveArea`→render flow, button gating, and "Build this region first" prompt — the only change is the mode guard at the top of the click handler (no-op when area-pick is active).
- **move-selection (AC #3).** Draggable center divIcon translates the whole selection (center follows, radius fixed) with a rigid-shift live preview, re-resolving from the server on release; inert with no selection (the marker isn't created).
- **select-region (AC #4).** Green overlays are clickable only in this mode; a click snaps to the region's exact server geometry and enables Configure query directly. Verified live.
- **Server stays the geometry authority (Story 1.6 code-review invariant held).** No km→deg or containment logic added to JS; both new modes route through the existing `resolveAndRender`/`GET /regions/resolve` path.
- **Caching fix (scope deviation from AC #6, documented).** AC #6 scoped this frontend-only, but a user report showed the shipped JS never reached the browser: `main.py` served static assets with no cache policy, so a stale cached `map-home.js` masked the change. Fixed in `main.py` (`_NoCacheStaticFiles` + `_page()` → `Cache-Control: no-cache` on the app's own assets/pages; vendored Leaflet unchanged). This is a pre-existing infra gap Story 4.1 surfaced; the fix is what makes this (and every future) frontend change actually take effect. **To pick it up, the running server must be restarted** (a `main.py` change, unlike static files, isn't hot-served) — then a single reload clears the stale JS.
- **Validation.** `tests/integration/test_app_api.py` **47 passed** (mode-control markup assertions + new `test_frontend_assets_served_no_cache`); `tests/unit/test_app_store.py` + `test_app_queue.py` + `test_app_regions.py` **27 passed**; `basedpyright` on `main.py` 0/0; ruff clean. No JS unit harness (buildless), per the established App convention — frontend logic covered by the real-Leaflet-click drive-through above + markup assertions.

### File List

- `src/steeproute/app/static/index.html` (modified) — `#mode-control` radio group added to the picker panel
- `src/steeproute/app/static/css/app.css` (modified) — mode-control, `.map-move-handle`, and `.select-region-active` overlay-cursor styles
- `src/steeproute/app/static/js/map-home.js` (modified) — mode state + gated click + `applyModeInteractivity` + move handle (`ensureCenterMarker`/`removeCenterMarker`) + select-region overlay clicks
- `src/steeproute/app/main.py` (modified) — `_NoCacheStaticFiles` + `_page()` helper: `Cache-Control: no-cache` on the app's own JS/CSS/HTML so buildless assets are never served stale (fix for the user-reported stale-JS symptom)
- `tests/integration/test_app_api.py` (modified) — extended the map-home markup test (mode control + three values); added `test_frontend_assets_served_no_cache`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-17 | Story drafted from epics-app.md (Story 4.1 / FR11 / UX-DR1) + the 2026-07-17 sprint-change-proposal, on top of the shipped Story 1.6 map home. Frontend-only, three exclusive modes. Status → ready-for-dev. |
| 2026-07-17 | Implemented the three selection modes in `index.html` / `app.css` / `map-home.js` (mode control, gated map click, move-selection drag handle, select-region overlay snap), all frontend-only with the server as the single geometry authority. Extended the map-home markup test; 46 integration + 14 unit passed. Browser-verified mode wiring + select-region snap live against the real cache. Status → review. |
| 2026-07-17 | User reported clicks always dropped a new box in every mode. Verified via real `map.fire('click')` through the handler that the on-disk code is correct (guard blocks non-area-pick clicks); root cause was a stale browser-cached `map-home.js` (`StaticFiles` sent no `Cache-Control`). Fixed in `main.py` (`_NoCacheStaticFiles` + `_page()` → `no-cache` on the app's own buildless assets/pages; vendored Leaflet unchanged) + regression test. 47 integration + 27 unit passed; basedpyright/ruff clean. Requires a server restart to take effect. |
| 2026-07-17 | User confirmed all three modes work after a browser refresh. Code review (low effort, diff-only): no correctness bugs found across `main.py`/`map-home.js`/`index.html`/`app.css` after two passes (incl. mode-switch state races and the largest changed file). Status → done. |
