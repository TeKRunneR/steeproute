# Story 5.1: Rotated AreaSpec, argv, and regions plumbing

Status: review

<!-- App track (epics-app.md). Story key `app-5-1-*` is `app-`-prefixed to avoid
     collision with the CLI track's `5-1-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want a rotated area chosen on the map to reach the CLI and built rotated regions to come back with their true shape,
so that the App can drive CLI Epic 15 end-to-end.

## Acceptance Criteria

1. **`AreaSpec` carries the rotated rectangle.** The wire/persisted area model expresses center + full width + full height + bearing angle in addition to today's `radius_km` square shorthand. Exactly one spelling is accepted (radius XOR width+height, with the angle applying to either); a body giving both, neither, or one lone dimension fails validation (422). A `job.json` written before this story (center + `radius_km` only) still loads unchanged.

2. **argv emits the rotated area flags — both kinds.** `build_setup_argv` and `build_query_argv` translate a rotated/non-square `AreaSpec` into the CLI Epic 15 flag surface (`--width` / `--height` / `--angle`, full dimensions), so a region built or queried from the App is the box the user picked.

3. **The square path is byte-identical.** A square `AreaSpec` still produces exactly today's `--center <lat>,<lon> --radius <r>` argv (no `--angle`, no width/height), so existing setup/query jobs, cache keys, and integration assertions are unchanged.

4. **`GET /regions` reports each built region's true shape.** A region carries its true (possibly rotated) polygon in `[lat, lon]` order derived from the CLI's own `cache.area_polygon`, plus its dimensions and bearing. Any axis-aligned bbox the response still exposes is explicitly documented as an *envelope* / over-approximation, and a rotated entry no longer reports a misleading scalar radius (the inert `0.0`).

5. **`GET /regions/resolve` resolves a rotated selection.** The endpoint accepts a rotated selection with the same spelling rules as AC #1 (422 on a malformed shape) and returns its true polygon, envelope, and green/grey decision computed by the CLI's own orientation-aware containment (`cache.find_covering_entry`) — a selection tucked inside a rotated entry's envelope but outside the box itself resolves as *not* covered. No km→deg conversion or containment logic is added to JS: the server stays the single geometry authority.

6. **Run identity never claims a radius a shape doesn't have.** Every place the UI summarizes a job's area (run library card + detail, run watch, result view, live indicator) renders a rotated area by its dimensions and bearing rather than `r{radius}` / `r?`; a square renders exactly as today.

7. **Round-trip coverage on both seams.** Unit and integration tests pin rotated *and* square areas end to end through `cli_adapter`: argv flags for both job kinds, a seeded rotated cache entry through `GET /regions`, a rotated selection through `GET /regions/resolve` (covered + not-covered), a rotated `POST /jobs` persisted and re-read, and the malformed-shape 422s. Existing square tests pass untouched.

## Tasks / Subtasks

- [x] Generalize the area models (AC: #1, #4, #5)
  - [x] `AreaSpec` gains `width_km`/`height_km`/`angle_deg` + a `model_validator` exactly-one-of rule
        (plus per-field `gt=0` / `allow_inf_nan=False` and the center range check); `radius_km`
        optional, still the square spelling. New `is_radius_shorthand` / `dimensions_km` properties
  - [x] New shared `AreaGeometry` base carries `polygon` + full dimensions + bearing;
        `RegionInfo` / `AreaResolution` inherit it. `RegionBounds` kept (same field name, the
        frontend reads it) and re-documented as an *envelope* / over-approximation
  - [x] Pre-5.1 `job.json` (center + `radius_km` only) loads unchanged — pinned in
        `test_app_store.py` and at the model level
- [x] Teach the argv seam the rotated flags (AC: #2, #3)
  - [x] One shared `_area_flags` used by `build_setup_argv` + `build_query_argv` (setup and query
        take the identical area surface, FR23) — `--width`/`--height` as full dimensions,
        `--angle` only when non-zero
  - [x] Square path asserted byte-identical (`--center … --radius …`, no `--angle`) for both kinds
- [x] Teach the regions seam the rotated shape (AC: #4, #5)
  - [x] `_geometry_fields` + `_polygon_latlon` derive everything from `cache.area_polygon` /
        `cache.area_bbox_wgs84`; `radius_km` is `None` for a non-square entry (was the inert `0.0`)
  - [x] New `to_cli_area` delegates to `cli/_shared.resolve_area` — one owner for the halving, the
        literal square construction, and the shape rule; `BadCLIArgError` → `ValueError` at the seam
  - [x] `resolve_area(AreaSpec, …)` + `GET /regions/resolve` accept the rotated spelling, sharing
        `AreaSpec`'s single rule (422 on a malformed shape)
- [x] Honest area summaries in the UI (AC: #6)
  - [x] `areaSummary` / `areaGeometry` in `static/js/format.js`, reused by `runs.js`,
        `run-watch.js`, `result.js`, `live-indicator.js`; `api.js`'s `resolveArea` gained
        optional rotated params (legacy 3-arg call from `map-home.js` unchanged)
- [x] Tests + verification (AC: #7)
  - [x] `test_app_models.py` (new), `test_app_argv.py`, `test_app_regions.py`, `test_app_store.py`,
        `test_app_api.py` — +56 tests
  - [x] Full offline suite `1211 passed`, `basedpyright` 0/0 on all changed files, live
        drive-through of the endpoints + the three area-summary screens

## Dev Notes

**Post-v1 App Epic 5, Story 5.1 — the plumbing half; CLI Epic 15 is shipped and is the interface.** The engine already accepts the shape: `resolve_area` in `cli/_shared.py` owns the flag surface (`--radius` XOR `--width`+`--height`, plus `--angle` on either), `Area` stores half-extents with a square shorthand, and `cache.py` derives every geometry from one rotated-aware polygon helper. This story wires the App's two affected seams to it [Source: _bmad-output/planning-artifacts/epics-app.md#Story 5.1; 15-3-cli-flag-surface-validation-and-render-overlay.md#Completion Notes List].

**Scope boundary — 5.2 owns the picker.** No new map handles, no rotation interaction, and no change to how the *selection* or the region overlays are drawn: `map-home.js` still sends `(center, radius_km)` and still renders `bounds`. That is why AC #4/#5 keep the bbox field in the response (documented as an envelope) instead of replacing it — the frontend must keep working untouched after this story, and Story 5.2 is what switches rendering to `L.polygon` and adds the dimension/rotation handles. Consequence to state plainly in the completion notes: until 5.2 lands, a *rotated* built region still draws as its (too large) envelope on the map [Source: epics-app.md#Story 5.2; epics-app.md#UX-DR1 (revised again)].

**Full dimensions on the wire, halved exactly once.** The CLI flags are full box dimensions; `Area` stores half-extents. Mirror the flags in `AreaSpec` (`width_km`/`height_km`, not half-extents) so argv is a pass-through and the frontend never halves — then halve at the single point that constructs a CLI `Area` (`regions.py`). 15.3's dev notes call this out as the one silent-doubling trap: getting it backwards doubles every rotated area with no error [Source: 15-3…md#Dev Notes; src/steeproute/cli/_shared.py:134-213; src/steeproute/models.py:28-87].

**Keep the square construction literal.** `--radius r` → `Area(center=…, radius_km=r)`, *not* equal explicit extents. A rotated/rectangular area passes the inert `radius_km=0.0` plus explicit half-extents. Both hash identically since 15.2, but the literal form is what keeps the fetch call, the manifest `area` block, and every square message provably unchanged — and AC #3's byte-identical argv is the App-side counterpart of that guarantee [Source: 15-3…md#Dev Notes; src/steeproute/cache.py:172-212].

**A rotated square is legal and is NOT `is_square`.** `Area.is_square` is `angle == 0 and half_width == half_height`, so `--radius R --angle A` is a real, expressible shape that takes the rotated code paths. Don't encode "angle implies extents" anywhere. Note also that a rotated area read back from the cache no longer records which spelling produced it, so `cache.format_area_flags` renders a rotated square as the equivalent `--width 2R --height 2R --angle A` [Source: src/steeproute/models.py:82-87; src/steeproute/cache.py:1276-1299].

**One rule source for the shape.** `cli/_shared.resolve_area` already encodes the whole combination rule and raises `BadCLIArgError`. Recommended: a thin `cli_adapter` helper that converts an `AreaSpec` into a CLI `Area` by delegating to it (the adapter package is *allowed* to import CLI internals — that is its entire purpose), with a light pydantic validator on `AreaSpec` so a malformed body 422s at the API boundary instead of surfacing as a failed job. Have `GET /regions/resolve` build an `AreaSpec` from its scalars so the endpoint and `POST /jobs` share that one validator rather than growing a second copy of the rule. Spelling is the dev's call; two divergent rule copies is the thing to avoid [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule; src/steeproute/cli/_shared.py:134-213].

**Envelope vs. region — the audit this story closes.** `cache.area_bbox_wgs84` is the min/max *envelope*, strictly larger than a rotated box; `cache.area_polygon` is the region. `regions._to_region_info` currently ships the envelope as the region and reads `entry.area.radius_km` (an inert `0.0` for a rotated entry) — the deferral note in that docstring names this story as its owner. Containment must never be tested against an envelope; use the cache's own `find_covering_entry` [Source: src/steeproute/app/cli_adapter/regions.py:64-81; src/steeproute/cache.py:1039-1057, 1028-1036, 1517-1535].

**Axis order flips at the boundary.** `cache.area_polygon` returns a shapely ring in `(lon, lat)` (RFC 7946) with the first vertex repeated last; Leaflet wants `[lat, lon]`. Pick one convention for the wire (`[lat, lon]`, matching `AreaSpec.center` and everything else the frontend consumes) and convert in `regions.py` only [Source: src/steeproute/cache.py:963-1025; src/steeproute/app/static/js/map-home.js:69-74].

**`params_schema` needs no change.** Story 15.3 already excluded `width`/`height`/`angle` from the introspected form (the map owns area selection) and added a test that re-derives the area flags from `cli.query`'s own options, so a future area flag can't leak onto the form. Don't re-open that seam [Source: src/steeproute/app/cli_adapter/params_schema.py:33-59].

**Worker/store are shape-agnostic.** `queue.py` hands `record.area` straight to the argv builders and `store.py` round-trips `JobRecord` through pydantic — so new optional fields need no worker or persistence change. Additive-only field additions are what keep pre-story records loadable (AC #1) [Source: src/steeproute/app/queue.py:70-82; src/steeproute/app/models.py:159-189].

**Frontend conventions (unchanged).** Vanilla ES modules, no build step, no new dependency, `api.js` stays the only URL holder, no inline handlers. Buildless assets are served `Cache-Control: no-cache` so a reload picks up JS edits; Python changes need a server restart. `format.js` is the established home for shared display helpers — reuse it for the area summary rather than repeating the string in four files [Source: architecture-app.md#Frontend conventions; app-4-1-map-selection-modes.md#Completion Notes List; src/steeproute/app/static/js/format.js].

**Mirror the CLI's human wording for the summary.** `cache._format_area_geometry` already renders a non-square area as `16x6 km box at 35°` and a square as `radius 2 km`. Matching that shape keeps the App and CLI describing the same box the same way [Source: src/steeproute/cache.py:1302-1315].

### Project Structure Notes

All edits; no new module. `cli_adapter/` remains the only App code importing `steeproute.*` [Source: architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── models.py                     ★ AreaSpec (rotated fields + shape validator),
│                                    RegionInfo / AreaResolution (polygon + dims),
│                                    RegionBounds (documented as envelope)
├── api.py                        ★ GET /regions/resolve rotated query surface
├── cli_adapter/
│   ├── argv.py                   ★ --width/--height/--angle for both kinds
│   ├── regions.py                ★ true polygon + rotated resolve (envelope-leak audit)
│   └── __init__.py               ☆ export any new helper through the public interface
└── static/js/
    ├── format.js                 ★ shared area-summary helper
    ├── runs.js, run-watch.js,    ★ use the helper instead of `r{radius}`
    │   result.js, live-indicator.js
    ├── api.js                    ☆ optional: let `resolveArea` pass the rotated
    │                                scalars through (its consumer is Story 5.2)
    └── map-home.js               — untouched (Story 5.2)

tests/
├── unit/test_app_argv.py         ★ rotated + square argv, both kinds
├── unit/test_app_regions.py      ★ seeded rotated entry: polygon, envelope, dims
├── unit/test_app_store.py        ☆ pre-story job.json still loads
└── integration/test_app_api.py   ★ rotated POST /jobs round-trip, 422 shapes,
                                     /regions + /regions/resolve rotated
```

### Testing

Per AGENTS.md: run `tests/unit` and `tests/integration` in **separate** invocations (per-directory `conftest.py`), keep the full offline suite green, and type-check with `uv run basedpyright <files>`. Seed rotated cache entries the way the existing region tests seed square ones — real `cache.write_entry` with an empty graph against a `tmp_path` cache root, so nothing builds or hits the network; `Area(center=…, radius_km=0.0, half_width_km=…, half_height_km=…, angle_deg=…)` is the rotated construction. Assert the region polygon against `cache.area_polygon` itself (with the axis flip) rather than hand-written coordinates, so the App can't drift from the cache's conversion. There is **no JS unit harness** (buildless) — cover the JS via served-markup assertions and a manual check; do not add a JS test runner. Note the known limitation before planning a visual check: the in-app Browser pane loads no external map tiles and screenshots of the map page time out, so map-visual verification is unreliable here — this story's surfaces are server-side and can be driven with `TestClient`/curl instead [Source: app-4-3…md#Testing; app-4-1…md#Debug Log References].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 5.1: Rotated AreaSpec, argv, and regions plumbing] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/epics-app.md#FR15] — rotated-rectangle map selection scope; arbitrary polygons out of scope
- [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule (post-v1 note, App Epic 5)] — seams 1 (argv) + 2 (regions) change, seam 3 (`params_schema`) does not; `AreaSpec` gains extents + angle; envelope-leak watch item
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md#Section 2 → Artifact Conflicts (App track)] — `RegionBounds` → true polygon; argv seam gains the flags; the envelope-leak audit is the real risk
- [Source: src/steeproute/cli/_shared.py:134-213] — `resolve_area`: the authoritative shape rule (XOR, both-dims-together, finite angle) and full-dimension → half-extent halving
- [Source: src/steeproute/cli/_shared.py:495-541] — the real `--radius` / `--width` / `--height` / `--angle` option surface both CLIs share
- [Source: src/steeproute/models.py:28-87] — `Area`: half-extents, `radius_km` square shorthand, `half_extents_km`, `is_square`
- [Source: src/steeproute/cache.py:1028-1057] — `area_polygon` (the region, `(lon, lat)`) vs `area_bbox_wgs84` (the envelope, over-approximation)
- [Source: src/steeproute/cache.py:1491-1535] — `list_prepared_areas` / `find_covering_entry` (orientation-aware containment; the App's coverage source)
- [Source: src/steeproute/cache.py:1276-1315] — `format_area_flags` / `_format_area_geometry`: the CLI's flag + human spellings to mirror
- [Source: src/steeproute/app/models.py:63-70, 191-232] — `AreaSpec`, `RegionBounds`, `RegionInfo`, `AreaResolution` (edit targets)
- [Source: src/steeproute/app/cli_adapter/argv.py:47-74, 77-165] — `build_setup_argv` / `build_query_argv` (area flags emitted at the top of each)
- [Source: src/steeproute/app/cli_adapter/regions.py:25-81] — `list_regions` / `resolve_area` / `_to_region_info` (the deferral note names this story)
- [Source: src/steeproute/app/api.py:166-192] — `GET /regions` + `GET /regions/resolve` handlers
- [Source: src/steeproute/app/static/js/runs.js:33-47, run-watch.js:55-60, result.js:71, live-indicator.js:31-32] — the four `r{radius}` display sites
- [Source: 15-3-cli-flag-surface-validation-and-render-overlay.md#Completion Notes List] — one resolver for both CLIs, byte-identical square path, App form guard already in place, overlay polygon precedent
- [Source: 15-2-rotated-setup-fetch-cache-schema-and-coverage.md#Completion Notes List] — cache schema bump + the App-side envelope-leak deferral to this story

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- **A 422 body can be unserializable.** `GET /regions/resolve?…&angle_deg=nan` initially returned a
  500: FastAPI parses `"nan"` into a real `float('nan')` (no `allow_inf_nan` guard at the query
  layer), `AreaSpec` rejects it, and echoing pydantic's `input` field back put a bare `nan` into the
  JSON response — `json` refuses to encode it. Fixed with
  `exc.errors(include_url=False, include_context=False, include_input=False)`; the parametrized
  malformed-shape test is what caught it.
- **Drive-through against an isolated store + cache** (scratchpad launcher, `:8011`, no network):
  seeded one square and one rotated cache entry plus a square and a rotated job record.
  `GET /regions` returned the rotated entry as four rotated vertices with `radius_km: null` and
  dims `16×6 @ 35°`, its envelope strictly bracketing the box (no vertex on an envelope corner);
  `GET /regions/resolve` reported `covered: true` for a 12×4 @ 35° selection inside it, `false` for
  a 0.2 km selection at its NW *envelope* corner, and 422 for both spellings at once. In the
  browser: the run library rendered `query · 16×6 km @ 35°` / `center 45.19, 5.72 · 16×6 km box at
  35°` and `setup · r10` / `radius 10 km`; run-watch rendered the same identity; no console errors.
  Also called the module functions directly in the page — `api.resolveArea(lat, lon, 5)` (the exact
  legacy 3-arg call `map-home.js` still makes) works unchanged alongside the new
  `{widthKm, heightKm, angleDeg}` form.
- Pre-existing, untouched: `uv run ruff check src tests` reports one `I001` (un-sorted imports) in
  `tests/unit/test_cache.py` from an earlier story. Confirmed present on `HEAD` with this story's
  changes stashed — not introduced here, and out of this story's scope.

### Completion Notes List

- **`AreaSpec` mirrors the CLI flag surface, not the CLI `Area`.** Full `width_km`/`height_km` on the
  wire (what `--width`/`--height` take and what a picker naturally has) with the halving to
  half-extents happening exactly once, inside `to_cli_area`. The exactly-one-of rule, positivity /
  finiteness, and the center range are enforced by the model, so a malformed body is a 422 at the
  boundary instead of a failed job — a strictly better outcome than before for a bogus center
  (previously accepted, then failed in the subprocess).
- **`to_cli_area` delegates to `cli/_shared.resolve_area` rather than re-deriving.** That buys three
  guarantees for free: the halving direction (the silent-doubling trap 15.3 warned about), the
  *literal* square `Area(center=…, radius_km=r)` construction that keeps a square's cache key / fetch
  / manifest byte-identical, and one owner for the shape rule. Its `BadCLIArgError` is re-raised as
  `ValueError` so the CLI error type stays inside `cli_adapter` and `api.py` keeps its no-`steeproute`
  import discipline.
- **Envelope-leak audit closed on the App side.** `RegionInfo`/`AreaResolution` now carry the true
  ring from `cache.area_polygon` as `[lat, lon]` vertices (axis flip + closing-vertex drop in one
  place, `_polygon_latlon`), and `radius_km` is `None` for any non-square entry instead of the inert
  `0.0` a rotated `Area` carries. `bounds` is retained under the same name — `map-home.js` reads it
  and is Story 5.2's file — but is now documented as an envelope everywhere it appears.
- **A rotated square reports dimensions, not a radius.** `radius_km` is derived from
  `Area.is_square` (which folds in the bearing), so `--radius 3 --angle 45` comes back as
  `6×6 km @ 45°`. Deliberate: geometry, not spelling, is what the map cares about, and a cache entry
  no longer records which spelling prepared it (same reasoning as `cache.format_area_flags`).
- **Known and expected until Story 5.2: a rotated built region still *draws* as its envelope.**
  `map-home.js` is untouched by design (it renders `bounds` via `L.rectangle`), so the data is now
  right while the overlay for a rotated region is still too large. 5.2 owns the `L.polygon` switch
  plus the dimension/rotation handles. `api.js`'s `resolveArea` was extended non-breakingly so 5.2
  has the transport it needs without touching the picker here.
- **`params_schema` untouched, as planned.** Story 15.3 already excluded `width`/`height`/`angle`
  from the introspected form and added a leak guard; the area stays map-owned.
- **Code-review fixes (2 findings, both applied).** (1) `GET /regions/resolve` caught only
  `AreaSpec`'s `ValidationError`, not the `ValueError` `to_cli_area` re-raises from the CLI
  resolver — the one path that exists precisely for an App/CLI rule divergence would have been an
  unhandled 500. Now caught and answered 422 with the CLI's own wording, pinned by a monkeypatched
  seam test. (2) `AreaSpec.is_radius_shorthand` was dead: every real branch tests
  `radius_km is not None` inline, and that inline form is what gives the type checker its narrowing
  (a property would force an `assert` at the argv branch), so the property was removed rather than
  wired in.
- **Validation.** Full offline suite `uv run --no-sync pytest --cov`: **1211 passed, 18 deselected,
  96% coverage** (2m33s) — +56 tests over 15.3's 1155. Per-directory App runs: unit 137,
  integration 69. `basedpyright` **0 errors, 0 warnings** on all 10 changed Python files (two extra
  per-file pyright relaxations added to `test_app_regions.py` for shapely's overloaded
  constructors). `ruff format --check` clean. No golden or CLI-side change: this story touches no
  `pipeline/**` or `models.py` file, so `pipeline_content_hash` and every cache key are unmoved.

### File List

- `src/steeproute/app/models.py` (modified — rotated `AreaSpec` + shape validator +
  `dimensions_km`; new `AreaGeometry` base; `RegionInfo`/`AreaResolution` inherit it;
  `RegionBounds` re-documented as an envelope)
- `src/steeproute/app/api.py` (modified — `GET /regions/resolve` rotated query surface, `AreaSpec`
  as the single shape rule, 422 mapping without echoing non-finite input, seam-`ValueError` → 422)
- `tests/unit/test_cache.py` (modified — pre-existing `ruff` `I001` import wrap, drive-by)
- `src/steeproute/app/cli_adapter/argv.py` (modified — shared `_area_flags` emitting
  `--radius` or `--width`/`--height` plus optional `--angle`, for both job kinds)
- `src/steeproute/app/cli_adapter/regions.py` (modified — `to_cli_area`, `_geometry_fields`,
  `_polygon_latlon`; `resolve_area` takes an `AreaSpec`; envelope-leak audit closed)
- `src/steeproute/app/cli_adapter/__init__.py` (modified — export `to_cli_area`)
- `src/steeproute/app/static/js/format.js` (modified — `areaSummary` / `areaGeometry` + `trimNumber`)
- `src/steeproute/app/static/js/api.js` (modified — `resolveArea` optional rotated params; `/regions`
  docstring)
- `src/steeproute/app/static/js/runs.js` (modified — card title + meta line use the area helpers)
- `src/steeproute/app/static/js/run-watch.js` (modified — identity line uses the area helpers)
- `src/steeproute/app/static/js/result.js` (modified — status line uses `areaSummary`)
- `src/steeproute/app/static/js/live-indicator.js` (modified — indicator uses `areaSummary`)
- `tests/unit/test_app_models.py` (new — `AreaSpec` shape rules, properties, legacy record)
- `tests/unit/test_app_argv.py` (modified — rotated/rectangular/rotated-square argv both kinds,
  square byte-identical guard)
- `tests/unit/test_app_regions.py` (modified — `to_cli_area` halving/literal-square/rotated-square,
  rotated entry polygon + envelope over-report, rotated coverage incl. the envelope-corner decline)
- `tests/unit/test_app_store.py` (modified — pre-5.1 `job.json` loads)
- `tests/integration/test_app_api.py` (modified — rotated job round-trip, malformed-shape 422s,
  rotated `GET /regions`, rotated `/regions/resolve` covered + declined + 422)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified — story status tracking)

## Change Log

| Date | Change |
|---|---|
| 2026-07-26 | Story drafted from epics-app.md (Story 5.1 / FR15) + the 2026-07-24 rotated-rectangle sprint-change-proposal, on top of the shipped CLI Epic 15. Plumbing only — picker handles are Story 5.2. Status → ready-for-dev. |
| 2026-07-26 | Code review (low effort, diff-only): 2 findings, both fixed — `GET /regions/resolve` now maps the seam's `ValueError` to 422 instead of an unhandled 500 (+ a monkeypatched regression test), and the unused `AreaSpec.is_radius_shorthand` property was removed. Also fixed a pre-existing `ruff` `I001` in `tests/unit/test_cache.py` from an earlier story. Full suite 1211 passed; basedpyright/ruff clean. |
| 2026-07-26 | Implemented the rotated plumbing: `AreaSpec` (full dimensions + bearing + exactly-one-of validator), shared `_area_flags` argv emission for both job kinds with the square path byte-identical, `to_cli_area` delegating to the CLI's own resolver, `GET /regions` + `/regions/resolve` carrying the true polygon with the envelope documented as an over-approximation, and honest area summaries across the four UI sites. +56 tests; full suite 1211 passed, basedpyright 0/0; endpoints and screens driven live against an isolated store/cache. Status → review. |
