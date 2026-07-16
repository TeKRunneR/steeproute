# Story 1.6: Map home — pick an area and build a region

Status: done

<!-- App track (epics-app.md). Story key `app-1-6-*` is `app-`-prefixed to avoid
     collision with the CLI track's `1-6-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want a map where I pick a center and radius, see which regions are cached, and build an uncached one,
so that I can prepare a region for querying entirely from the app, as a deliberate step.

## Acceptance Criteria

1. **`GET /regions` (backend).** A new `GET /regions` endpoint returns the built regions read from the CLI's on-disk cache, through `cli_adapter.regions` — the **only** module that touches the cache layout (architecture Category 6 / the load-bearing rule). Each region carries at least `center`, `radius_km`, and `cache_key_hash`; snake_case, no envelope; an empty/absent cache returns `[]` (not an error). The read is **read-only** and uses the same default cache root the setup subprocess writes to (no private `--cache-dir`, mirroring `argv.py`).

2. **Map home renders.** The home page (`GET /`) is a full-bleed Leaflet map: click drops a center, a draggable handle sets the radius, and the current selection renders as an overlay. Built regions from `GET /regions` render as **green** overlays. The persistent global header + `#live-indicator` slot from Story 1.5 are kept unchanged.

3. **bbox geometry, not a disk.** `radius_km` is the **bbox half-side** of an axis-aligned WGS84 square, not a disk radius (`steeproute.models.Area`). The selection overlay and every built-region overlay are squares/rectangles matching that meaning; the green/grey decision uses **strict bbox containment** consistent with the CLI's `check_coverage` (`_select_smallest_containing`), so a selection shown green here will also pass query-side coverage in Epic 2. The km→deg conversion is **not** re-derived in JS.

4. **Deliberate two-step (grey selection).** When the selection is not contained by any built region, the primary action is **"Build this region"** — it `POST /jobs` with `kind=setup` and the selected area, then navigates to that job's Run-watch (`runWatchUrl`). **"Configure query"** is **disabled** with a "Build this region first" prompt (block, not auto-build — UX-DR1 / architecture Category 7 two-step).

5. **Green selection → Configure query (Epic 2 handoff).** When the selection is contained by a built region, "Configure query" is the enabled primary action and "Build this region" recedes. Configure query is a **placeholder affordance** here (the config form is Epic 2, Story 2.1) — same pattern as Story 1.5's View-routes placeholder; it does not open a working form in this story.

6. **Overlay reflects new builds.** After a setup job completes, returning to the map (reload/refetch of `GET /regions`) shows that area rendered green and offering Configure query. No live push required — a fetch on map load is sufficient.

7. **Frontend conventions.** All backend calls go through `api.js` (add `listRegions()` and a create-job call); no other JS file hardcodes a URL. New JS is kebab-case ES modules, no inline handlers, no framework/bundler; Leaflet is the already-vendored `/vendor/leaflet-1.9.4.*` (no CDN).

8. **Scope guard.** No config form / query kind / View-routes iframe (Epic 2), no run-library list (Story 3.1), no cancel-queued `DELETE` (Story 3.2). Area-size-cap enforcement (FR2) stays in the `steeproute-setup` subprocess — an over-cap selection surfaces as a `failed` job on Run-watch, not a new client-side check. All cache/argv knowledge stays inside `cli_adapter`.

## Tasks / Subtasks

- [x] `cli_adapter/regions.py` — the cache-read seam (AC: #1, #3)
  - [x] Add `list_regions(cache_root: pathlib.Path | None = None) -> list[RegionInfo]`: resolve the default cache root via `steeproute.cache.resolve_cache_root()` when `None` (injectable for tests), read the prepared areas, map each to a `RegionInfo`. Read-only — never writes the cache.
  - [x] Do **not** re-parse `index.json` by hand: reuse `steeproute.cache`'s index reader. Added a thin **public** accessor `cache.list_prepared_areas(cache_root) -> list[CoverageEntry]` (wraps the private index reader + rebuild recovery) plus `cache.area_bbox_wgs84(area)` for the shared km→deg bbox, keeping `cache.py` the single source of layout truth.
  - [x] Export `list_regions` from `cli_adapter/__init__.py`.
- [x] `RegionInfo` model (AC: #1)
  - [x] Added `RegionInfo` + `RegionBounds` to `models.py`: `cache_key_hash`, `center`, `radius_km`, and the precomputed WGS84 `bounds` the frontend renders/tests verbatim. snake_case; App-side types.
- [x] `GET /regions` route (AC: #1, #8)
  - [x] Added to `api.py`: calls `cli_adapter.list_regions(cache_root=…)`, returns the list directly (200, snake_case, no envelope). Cache root threaded through `create_app`/lifespan → `app.state.regions_cache_root` (injectable for tests; `None` = CLI default root).
- [x] Map-home frontend (AC: #2–#7)
  - [x] Reworked `static/index.html` into the S1 map home: full-bleed `#map` + action panel (Build / Configure query) + selection readout, header + `#live-indicator` kept, loads `js/map-home.js` (+ `live-indicator.js`).
  - [x] `static/js/map-home.js` — Leaflet over `/vendor` + OpenTopoMap tiles (mirroring the CLI report), click-to-drop center + draggable divIcon radius handle → square-bbox selection, fetch + render built regions green, green/grey via strict bbox containment, Build (`createJob` → `runWatchUrl`) and Configure placeholder/disabled states. `invalidateSize()` after layout so click→latlng is accurate.
  - [x] `static/js/api.js` — added `listRegions()` and `createJob(body)`; still the only URL holder.
  - [x] `static/css/app.css` — full-bleed flex map layout + panel/overlay/handle/button styles.
- [x] Tests (AC: #1, #2)
  - [x] `tests/unit/test_app_regions.py`: crafted cache root via real `write_entry` → `list_regions(cache_root=…)` returns the expected `RegionInfo`s; empty/absent cache → `[]` (no side effects); missing-index recovery. Injectable `cache_root` → no real build.
  - [x] `tests/integration/test_app_api.py`: `GET /regions` returns 200 + the region (snake_case) over a crafted cache, and `[]` when empty; `GET /` serves the map-home markup; `map-home.js` served from the static mount. Existing tests stay green.

## Dev Notes

**This is step 5 of the architecture's implementation sequence** — the map picker + cached-region overlay + build button, on top of the proven job runner (1.3), SSE plumbing (1.4), and run-watch/Stop/live-indicator (1.5) [Source: architecture-app.md#Decision Impact Analysis]. It closes Epic 1: FR1 (map + overlay) and FR2 (deliberate two-step) land here; the runner, progress, and watch surface already exist and are only *consumed* by the Build action.

**The one new backend seam is `cli_adapter/regions.py`** — the last of the adapter's four seams to be built (argv 1.3, progress_parse 1.4, regions here; params_schema is Epic 2) [Source: architecture-app.md#The load-bearing rule; src/steeproute/app/cli_adapter/__init__.py]. Everything the App knows about the cache lives here.
- Reuse, don't reinvent: `steeproute.cache` is already "the sole reader/writer of the cache directory" and owns `resolve_cache_root`, the index reader, and the km→deg geometry (`_area_to_polygon`, `_bounds_geojson`) [Source: src/steeproute/cache.py:336-349,791-880,895-955]. There is no public "list all prepared areas" today — add a thin public wrapper in `cache.py` rather than calling the private `_read_indexed_entries` from the adapter or hand-parsing `index.json` (which would create a second source of layout truth).
- Use the **default** cache root — the setup subprocess writes there (`argv.py` deliberately omits `--cache-dir`), so a private root would make built regions invisible to the map [Source: src/steeproute/app/cli_adapter/argv.py:9-12; architecture-app.md#Category 6].

**bbox is the biggest correctness trap.** `radius_km` is a **square half-side**, not a disk radius — Stage 1 fetches `dist_type="bbox"` [Source: src/steeproute/models.py:27-46; src/steeproute/cache.py:791-819]. Draw regions and the selection as rectangles/squares, and decide green/grey with strict bbox containment (a selection is queryable iff its bbox ⊆ some built region's bbox), matching `check_coverage`'s `_select_smallest_containing` (`shapely.contains`, identical bboxes qualify) [Source: src/steeproute/cache.py:995-1018,1151-1225]. If green/grey diverges from that rule, a selection shown "green" could still fail coverage when Epic 2 runs the real query. Cheapest way to stay exact: have `regions.py` return each region's bbox bounds (computed from the shared conversion) and render/contain against those in JS rather than re-implementing km→deg.

**Frontend conventions (established Story 1.5).** Vanilla ES modules; `fetch` + Leaflet; `api.js` is the only URL holder; kebab-case; no inline handlers; server is the source of truth (refetch `/regions` on load, don't mirror state) [Source: architecture-app.md#Frontend conventions; src/steeproute/app/static/js/api.js]. The header markup is duplicated per page by design (buildless) — the map-home rework keeps the same `<header>` + `#live-indicator` slot so the indicator behaves identically [Source: src/steeproute/app/static/index.html:12-22]. Leaflet is served at `/vendor/leaflet-1.9.4.*` [Source: src/steeproute/app/main.py:37-38,107-110].

**Configure query is a placeholder (Epic 2).** The config form (S2) is Story 2.1; over a green selection "Configure query" is enabled as a placeholder link/button that does not open a working form yet — the same handoff pattern Story 1.5 used for View-routes [Source: epics-app.md#Story 1.6; app-1-5-watch-a-running-job.md — terminal-footer placeholder]. Over grey it is disabled with the "Build this region first" prompt (the settled block-not-offer resolution of the Cluster-D open question) [Source: ux-design-specification.md#F2; epics-app.md#UX-DR1].

**Build reuses the existing `POST /jobs` path** — `kind=setup` + `area` is exactly what Story 1.3 wired and 1.5 exercised; no new job mechanics, just a UI caller that navigates to `runWatchUrl(job.id)` on the 201 [Source: src/steeproute/app/api.py:73-95; src/steeproute/app/static/js/api.js:52-54].

### Project Structure Notes

Target tree — this story creates the **starred** files (rest are prior/later) [Source: architecture-app.md#Complete project tree]:

```
src/steeproute/
├── cache.py                          ★ (edit) add a public list-prepared-areas accessor
└── app/
    ├── api.py                        ★ (edit) GET /regions
    ├── models.py                     ★ (edit) RegionInfo
    ├── cli_adapter/
    │   ├── __init__.py               ★ (edit) export list_regions / RegionInfo
    │   └── regions.py                ★ cache-manifest read → RegionInfo list (seam 2)
    └── static/
        ├── index.html                ★ (edit) placeholder shell → S1 map home
        ├── css/app.css               ★ (edit) full-bleed map + panel styles
        └── js/
            ├── api.js                ★ (edit) listRegions() + create-job call
            └── map-home.js           ★ Leaflet picker + overlay + build/configure
```

- Only `cli_adapter/regions.py` (and its one `cache.py` accessor) may import `steeproute.*` internals; `map-home.js` goes through `api.js` [Source: architecture-app.md#Architectural Boundaries].

### Testing

Per AGENTS.md: `uv run basedpyright <files>`; run `tests/unit` and `tests/integration` in **separate** invocations. App tests use FastAPI's `TestClient` (context manager runs `lifespan`) with a tmp store root; region tests use an injectable `cache_root` so no real build/network runs [Source: architecture-app.md#Development workflow; tests/integration/test_app_api.py:66-73]. There is no JS unit harness (buildless) — the frontend is covered by asset-served/markup assertions here and manual `run`-skill / browser verification; do not add a JS test runner [Source: app-1-5-watch-a-running-job.md#Testing]. Existing `test_app_api.py` / `test_app_sse.py` and the CLI cache suite (`test_check_coverage.py`, `test_cache_coverage.py`) must stay green — the new `cache.py` accessor must not alter existing coverage behavior.

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 1.6: Map home — pick an area and build a region] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 6 — Cache-coverage detection] — read the CLI cache manifest behind one adapter module
- [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule: one CLI-adapter boundary] — seam 2 (regions) is the only cache-reading code
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 8 — API surface] — `GET /regions` for the overlay
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 10 — Frontend architecture] — buildless map home, flat nav, reused Leaflet
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#F2 — First time on an uncached area] — the deliberate two-step; disabled + "Build this region first"
- [Source: _bmad-output/planning-artifacts/epics-app.md#UX-DR1] — S1 context-sensitive primary action; block-not-offer over grey
- [Source: src/steeproute/models.py:27-46] — `Area`: `radius_km` is a bbox half-side, not a disk radius
- [Source: src/steeproute/cache.py:336-349] — `resolve_cache_root` (default root the setup subprocess writes to)
- [Source: src/steeproute/cache.py:895-1018,1151-1225] — index reader + strict-containment coverage semantics to mirror for green/grey
- [Source: src/steeproute/cache.py:791-880] — `_bounds_geojson` / `_area_to_polygon` km→deg conversion (reuse; don't re-derive in JS)
- [Source: src/steeproute/app/api.py:73-101] — `POST /jobs` (kind=setup) the Build action reuses + the thin route pattern for `GET /regions`
- [Source: src/steeproute/app/cli_adapter/__init__.py] — the four-seam boundary + public-interface export pattern
- [Source: src/steeproute/app/cli_adapter/argv.py:9-12,26-34] — default-cache-root rationale + `resolve_*` injectable pattern to mirror
- [Source: src/steeproute/app/static/index.html] — the shell (header + `#live-indicator`) this story reworks into the map home
- [Source: src/steeproute/app/static/js/api.js] — the single URL holder to extend
- [Source: _bmad-output/implementation-artifacts/app-1-5-watch-a-running-job.md] — placeholder-affordance pattern + frontend conventions this story continues

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Map click landed at the wrong longitude until `invalidateSize`.** In the first browser drive-through a click at the map's true visual center dropped a center at lon 6.05 instead of 5.72 (latitude was correct). Leaflet had cached a stale pixel size from before the flexbox finished sizing `#map`, so its pixel→latlng transform was off on the horizontal axis only (the picker panel is on the right). Fixed by `requestAnimationFrame(() => map.invalidateSize())` after map creation; re-drive then showed the true center (45.1899, 5.7198).
- **Handle uses a `divIcon`, not a marker image.** The vendored Leaflet ships JS/CSS only (the CLI report draws no markers), so `L.marker` with the default icon would 404 on `marker-icon.png`. The center is a non-interactive point and the radius handle is a draggable `L.divIcon` (HTML/CSS), neither of which needs an image asset.
- **`list_prepared_areas` deliberately does not create the cache tree on a bare read.** Unlike `check_coverage` (which rebuilds unconditionally), it returns `[]` when `areas/` is absent so a `GET /regions` on a fresh machine leaves no side effects — asserted by `test_list_regions_absent_cache_has_no_side_effects`.
- **Browser drive-through (seeded cache + fake setup subprocess).** Verified end-to-end against a temp cache seeded with a Grenoble r12 region and a fake `setup` (echo a stage line, then sleep — no network): green overlay rendered from `GET /regions`; a click inside → "cached — ready to query", Configure enabled / Build disabled; Configure → "Configure query arrives in Epic 2." placeholder; a click outside → "needs build", Build enabled, Configure disabled titled "Build this region first"; **Build** POSTed a `setup` job with the exact selected area (center 45.16,5.43 · r10), navigated to `/runs/{id}`, and the header live-indicator showed `● setup running · r10` linking back.

### Completion Notes List

- **Backend seam (`cli_adapter/regions.py`).** `GET /regions` reads built regions through the adapter only. Rather than re-parse `index.json`, added two thin **public** helpers to `cache.py` — `list_prepared_areas(cache_root)` (returns `CoverageEntry(cache_key_hash, area)`, wrapping the private index reader + the same rebuild-recovery `check_coverage` uses) and `area_bbox_wgs84(area)` (the shared km→deg bbox) — so `cache.py` stays the single source of cache-layout truth and the frontend never re-derives geometry. Uses the CLI default cache root (the location `steeproute-setup` writes to); `cache_root` is injectable purely for tests.
- **Models.** `RegionInfo` + `RegionBounds` (snake_case, App-side) carry the entry hash, center, `radius_km` (a bbox half-side, not a disk radius), and the precomputed WGS84 bbox.
- **API.** `GET /regions` returns the list directly (200, no envelope); empty/absent cache → `[]`. `create_app`/lifespan thread an injectable `cache_root` onto `app.state.regions_cache_root` (mirrors the existing `store_root`/`build_argv` injection); `None` = the real default root.
- **Frontend (map home).** `index.html` reworked from the skeleton into the S1 map: full-bleed Leaflet, click-to-drop center + draggable radius handle, green built-region overlays, green/grey selection by **strict bbox containment** (mirrors the CLI's `_select_smallest_containing`, inclusive of shared edges) so a green selection matches query-side coverage. Build enqueues a `setup` job and navigates to its run-watch; Configure query is a placeholder over green (Epic 2) and disabled with a "Build this region first" prompt over grey (the settled block-not-offer two-step). `api.js` gained `listRegions()` + `createJob()` and stays the only URL holder.
- **Scope held (AC #8):** no config form / query kind / View-routes iframe (Epic 2), no run-library list (3.1), no cancel-queued `DELETE` (3.2). Area-cap enforcement stays in the setup subprocess. All cache/argv knowledge stays inside `cli_adapter`.
- **Validation:** `basedpyright` on the changed backend files 0/0; `ruff` clean (+ format). New tests: 4 unit (`test_app_regions.py`) + 3 integration (`test_app_api.py`) plus `map-home.js` added to the served-modules assertion. Full offline suite **970 passed, 17 deselected** (~2m47s), no regressions (963 → 970). Frontend has no unit harness (buildless) — covered by asset/markup assertions + the browser drive-through above.
- **Code-review fix (low-effort pass, 1 finding, fixed).** *Client re-derived the km→deg conversion + containment, contradicting AC #3.* `map-home.js` originally hand-copied `cache._DEG_PER_KM_LAT`/`_deg_per_km_lon` and re-implemented bbox containment in JS — an unenforceable cross-language duplication (no JS test harness to pin the two copies) that risked the overlay's green/grey decision silently drifting from the CLI's query-side `check_coverage`. Fix: made the server the single geometry authority. Added `GET /regions/resolve` (→ `cli_adapter.resolve_area` → new public `cache.find_covering_entry`, which reuses `check_coverage`'s own `_select_smallest_containing`), returning the exact bbox + `covered` + `cache_key_hash`. `map-home.js` now passes the picked `(center, radius_km)` through and renders what the server returns — zero km→deg or containment logic in JS (the one km value comes from Leaflet's own `map.distance`, library geodesy). 5 new tests (3 unit `resolve_area` + 2 integration `/regions/resolve`, incl. `radius_km<=0` → 422). Re-verified the full pick→resolve→build flow in the browser. Full suite green.

### File List

- `src/steeproute/cache.py` (modified) — public `list_prepared_areas` + `CoverageEntry` + `area_bbox_wgs84`
- `src/steeproute/app/models.py` (modified) — `RegionInfo` + `RegionBounds`
- `src/steeproute/app/cli_adapter/regions.py` (new) — seam 2: cache-manifest read → `RegionInfo` list
- `src/steeproute/app/cli_adapter/__init__.py` (modified) — export `list_regions`
- `src/steeproute/app/api.py` (modified) — `GET /regions` + `_regions_cache_root`
- `src/steeproute/app/main.py` (modified) — injectable `cache_root` → `app.state.regions_cache_root`
- `src/steeproute/app/static/index.html` (modified) — skeleton shell → S1 map home
- `src/steeproute/app/static/js/map-home.js` (new) — Leaflet picker + region overlay + build/configure
- `src/steeproute/app/static/js/api.js` (modified) — `listRegions()` + `createJob()`
- `src/steeproute/app/static/css/app.css` (modified) — full-bleed map layout + panel/handle/button styles
- `tests/unit/test_app_regions.py` (new) — `list_regions` over a crafted cache
- `tests/integration/test_app_api.py` (modified) — `GET /regions` + map-home markup/JS served
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-15 | Story drafted from epics-app.md + architecture-app.md + ux-design-specification.md, on top of Story 1.5's frontend + the CLI cache. Status → ready-for-dev. |
| 2026-07-15 | Implemented `GET /regions` (new `cli_adapter/regions.py` + public `cache.list_prepared_areas`/`area_bbox_wgs84`), `RegionInfo`/`RegionBounds`, and the S1 map home (Leaflet picker, green/grey overlays, Build→run-watch, Configure placeholder). 7 new tests; full suite green (970). Browser-verified the pick→build→watch flow end-to-end. Status → review. |
| 2026-07-16 | Code review (low effort): fixed 1 finding — the client re-derived km→deg + containment (AC #3 violation, undrift-testable). Added server-authoritative `GET /regions/resolve` (`cli_adapter.resolve_area` + public `cache.find_covering_entry`) and reworked `map-home.js` to render server-computed bbox/coverage with zero JS geometry. +5 tests; full suite green. |
| 2026-07-16 | Code review passed, no further findings. Status → done. |
