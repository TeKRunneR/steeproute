# Story 15.3: CLI flag surface, validation, and render overlay

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want CLI flags to specify a rotated rectangle (with radius still meaning a square) and the report
overlay to draw the true box,
so that the capability is usable and honestly visualized.

## Acceptance Criteria

1. Both `steeproute-setup` and `steeproute` accept the **same** area surface (FR23): `--center` plus
   either the square shorthand `--radius` or full-dimension `--width`/`--height`, with an optional
   `--angle` bearing usable with either spelling. Exactly-one-of is enforced at the boundary —
   neither supplied, both supplied, or only one of width/height all raise `BadCLIArgError` (exit 2)
   naming the offending flags, before any cache or network work.
2. A `--radius`-spelled invocation is byte-identical to pre-Epic-15: same `Area`, same cache key,
   same fetch call, same route output. `--radius` semantics, help text, and every existing
   square-path error message are unchanged.
3. Area validation (finite, positive extents; finite bearing; center range) runs on **both** CLIs
   from one shared resolver, so the query CLI no longer degrades a garbage area into a confusing
   coverage miss. Diagnostics name the flag the user actually typed (`--radius` vs `--width`).
4. The query-side area-cap check (FR2) uses the **true** rectangle area (`width × height`) instead of
   the `π·r²` disk proxy, takes the `Area` rather than a scalar, and rejects an oversize box with a
   descriptive `BadCLIArgError` (exit 2) whose message names the shape's own flags. This tightens the
   effective ceiling for squares (`4r²` > `πr²`) — a deliberate FR2 correction, not a regression.
5. The setup-side size ceiling generalizes off `--radius` to whichever dimensions were supplied,
   keeping today's `--radius` wording and threshold for a square.
6. The report overlay draws the true (possibly rotated) box, derived from the one shared
   `cache.area_polygon` ring rather than `output.py`'s separate projection constant. A square still
   renders as its box; the JSON sidecar is unchanged.
7. Coverage/diagnostic messaging spells the real Click option names — a `steeproute-setup` command
   suggested after a rotated coverage miss is copy-pasteable as-is.
8. A rotated-rectangle regression golden is added and committed; every existing square golden passes
   **untouched, with no rebake**.
9. The App's query-config form is unaffected: the new area flags never surface as form fields, and
   the App's existing `--center/--radius` argv keeps working.
10. Docs reflect the new surface (README area examples + key-parameter table, `docs/examples/README.md`
    reproduce commands, AGENTS.md quality-demo params note). Offline suite green; `basedpyright` clean.

## Tasks / Subtasks

- [x] Shared area flag surface + resolver in [cli/_shared.py](src/steeproute/cli/_shared.py:334) (AC: #1, #2, #3)
  - [x] Added `width_option` / `height_option` / `angle_option`; `--radius` is now
        `default=None` (not `required`), with a comment recording that click cannot express
        exactly-one-of so the resolver owns the whole rule — and that this keeps "no area at all"
        a `BadCLIArgError → exit 2` rather than click's Usage/Error block
  - [x] `resolve_area(...) -> Area`: combination rule → bearing → size (named by the spelling
        typed) → center. Halves `--width`/`--height` into half-extents; builds a square as the
        literal `Area(center=..., radius_km=r)`. `_validate_extent` reuses `validate_setup_radius`'s
        exact wording, so every pre-existing `--radius` diagnostic is byte-identical
  - [x] **Decision: `osm._validate_area` stays** as setup's backstop for direct pipeline callers,
        unmerged. Rationale in `_is_radius_shorthand`'s docstring and Completion Notes — the only
        neutral home is inside `_PIPELINE_CONTENT_GLOBS`, so de-duplicating one predicate would
        re-key every cache entry on disk
- [x] True-area cap + setup ceiling (AC: #4, #5)
  - [x] `validate_area_size(area, area_cap_km2)` measures `cache.area_km2`; `_area_km2` and
        `_format_area_flags` promoted to public `area_km2` / `format_area_flags` (mirroring how
        15.2 published `area_polygon`) so the cap message names the shape's own flags
  - [x] `validate_setup_radius` → `validate_setup_area(area)`: per-dimension ceiling, square
        wording/threshold byte-identical, rectangle reported in full dimensions against the same
        span (`2 × 50` km)
  - [x] Moved assertions: `test_validate_area_size_passes_just_below_cap` (now `sqrt(cap/4)`),
        `test_query_cli_accepts_radius_just_below_custom_cap`. `test_cli_smoke.py:139` needed no
        change (radius 30 exceeds the cap under both formulas — comment refreshed)
- [x] Wire both CLIs (AC: #1, #2, #4)
  - [x] [cli/setup.py](src/steeproute/cli/setup.py:96) and [cli/query.py](src/steeproute/cli/query.py:114)
        take the same four area decorators and call the same resolver; both dropped their local
        `Area(...)` construction
  - [x] Extended `SETUP_FLAGS` / `QUERY_FLAGS`, left `QUERY_ONLY_FLAGS` alone, and added a
        set-equality test that fails if an area flag lands on one CLI but not the other (FR23)
- [x] Rotated overlay in [output.py](src/steeproute/output.py:288) + [route.html.j2](src/steeproute/templates/route.html.j2:114) (AC: #6)
  - [x] `_search_bbox` → `_search_polygon`, deriving `[[lat, lon], ...]` from
        `cache.area_polygon` (drops the closing vertex; converts RFC-7946 `(lon, lat)` to Leaflet
        order). Template now `L.polygon`; `render`'s docstring and the call-site comment updated
  - [x] `test_search_area_overlay_wired` rewritten for the polygon, plus a rotated test that parses
        the emitted ring and asserts no corner sits on an envelope corner
- [x] Align the messaging spellings in [cache.py](src/steeproute/cache.py:1275) (AC: #7)
  - [x] 15.2's provisional `--width/--height/--angle` **are** the final names, so no wording moved —
        docstring updated from "Story 15.3 owns the real names" to record that they now are, plus
        the one caveat (a `--radius --angle` square renders as the equivalent width/height form).
        Pinned end to end by a new rotated coverage-miss CLI test
- [x] Rotated regression golden in [regression.py](src/steeproute/regression.py:64) (AC: #8)
  - [x] `Fixture` gained `width_km`/`height_km`/`angle_deg`; new public `area_args(fixture)` builds
        the fragment. A test pins that every square fixture still emits exactly
        `--radius <value>`, and another that no two fixtures share a golden path
  - [x] **The story's premise was wrong and the plan changed** — the query area selects a cache
        entry but never clips the search, so a rotated query over a square cache reproduces the
        square golden exactly. Added a genuinely **rotated prepared entry** (3.4 × 2.0 km @ 45°)
        to the `grenoble_small` cache root instead, built offline via the extended
        `regenerate_cache.py`; the fixture queries it at 3.0 × 1.6 km @ 45°
  - [x] Baked both tiers. `git status tests/e2e/goldens/` shows only the two new files; all 10
        golden tests pass
- [x] App-side guard (AC: #9)
  - [x] `width`/`height`/`angle` added to `_EXCLUDED_FIELDS`, plus a test that re-derives the area
        flags from `cli.query`'s own option surface so a *future* area flag can't leak onto the
        form either. `argv.py` and `regions.py` untouched (App 5.1)
- [x] Docs + verification (AC: #10)
  - [x] README gained a "Non-square search areas" section + three key-parameter rows;
        `docs/examples/README.md` notes the alternative spelling; AGENTS.md records the cap
        correction and the area surface
  - [x] Full offline suite + both golden tiers + `basedpyright` (see Completion Notes)

## Dev Notes

- **Scope boundary.** This story closes CLI Epic 15. **Out of scope:** the App's `AreaSpec`/argv and
  `RegionBounds`/`regions._to_region_info` envelope leak — App Story 5.1. Touch `params_schema.py`
  only to keep the new flags off the form. [Source: epics.md#Epic 15, epics-app.md#Epic 5]
- **`--width`/`--height` are full dimensions, not half-extents.** 15.2's `_format_area_flags` already
  emits `2 × half_extent`, and users think in box size. Halve at the boundary; `Area` stores
  half-extents. Getting this backwards silently doubles every rotated area.
- **Keep the square construction literal.** `--radius r` → `Area(center=..., radius_km=r)`, not
  equal extents. 15.2 made both hash the same (`_canonicalize_area` reads `half_extents_km`), so
  either *would* work — but the literal form is what keeps the fetch call, the manifest `area` block,
  and every square message provably byte-identical. A rotated area passes the inert `radius_km=0.0`
  plus explicit extents. [Source: 15-2 Completion Notes, models.py:27]
- **`--angle` with `--radius` is legal.** A rotated square is a real shape, and 15.2's code review
  specifically fixed `osm._validate_area` so a bad radius under a non-zero bearing still reports
  `--radius`. Don't reintroduce "angle implies extents" anywhere. Also note `Area.is_square` is
  `angle == 0 and hw == hh`, so a rotated square is *not* `is_square` — it takes the
  `graph_from_polygon` fetch path, which is correct.
- **The area-cap change is a real behavior change for squares.** `π·r²` → `4·r²` lowers the radius the
  default `--area-cap 500` admits from ~12.6 km to ~11.2 km. Intended (FR2 as revised), but it moves
  existing assertions and could bite a user's habitual command — say so in the completion notes.
  Goldens are unaffected (`--area-cap` isn't pinned and the fixtures query at 1.5 km). The App's
  `area_cap` quality default (100 000 km²) and the reason `0` can't disable the check both still
  hold. [Source: prd.md FR2 via sprint-change-proposal §4.1; params_schema.py:44-58]
- **Query-side validation is thinner than it looks.** `osm._validate_area` is **setup-only** — the
  query CLI's sole area guard today is `validate_area_size`, which lets a NaN radius through
  (`nan > cap` is False) into a confusing coverage miss. Routing both CLIs through one resolver is
  what makes AC #3 true; don't assume the query path already validates.
- **Overlay: one projection source.** `output._search_bbox` carries its own `111.32` deg/km constant
  while `cache._area_to_polygon` uses `1/111`. Deriving the overlay from `cache.area_polygon` removes
  the second source but shifts a square's drawn rectangle by ~0.3% — visual-only. The JSON sidecar
  never carried the bbox, so no golden or sidecar impact. Note the ring is `(lon, lat)` (RFC 7946)
  while Leaflet wants `[lat, lon]`. [Source: output.py:288, cache.py:963]
- **Don't regenerate the gallery.** The committed `docs/examples/**/route-*.html` keep the old
  axis-aligned overlay markup; `test_gallery_self_contained.py` only checks for external references,
  so they stay green. Re-baking the gallery is not in this story.
- **Rotated golden mechanics.** `params_hash` covers only `pinned_params`, and the area is a `Fixture`
  field — so adding a rotated fixture cannot move an existing golden, provided the four existing
  fixtures still emit exactly `--center … --radius …`. Recommendation: register it in `FIXTURES` so
  both the fast CI gate and the `REALISTIC_FIXTURES` tier cover it (two goldens); if the realistic
  bake proves disproportionate, record the call instead of silently skipping it.
  [Source: AGENTS.md §Solver / GRASP; regression.py:64-217]
- **Testing standards.** Offline; run per-directory (mixing `tests/unit` and `tests/integration` in
  one invocation imports the wrong `conftest.py`). Type-check with `uv run basedpyright <files>`. If
  `uv run` flakes with ~43 `test_cli_smoke.py` failures or a TLS `UnknownIssuer` error, run
  `uv sync --native-tls` once. [Source: AGENTS.md#Dev environment]

### Project Structure Notes

- All changes land in existing modules: `cli/_shared.py` (options + resolver + validation),
  `cli/setup.py`, `cli/query.py`, `output.py` + `templates/route.html.j2`, `cache.py` (public
  `area_km2`, message spellings), `regression.py`, `app/cli_adapter/params_schema.py`. No new module.
- `cli/_shared.py` already imports from `steeproute.cache`, so reaching for `area_km2` /
  `area_polygon` adds no new dependency direction.
- 15.2 flagged a latent follow-up: `cache.area_polygon` now determines what a rotated area fetches,
  but `cache.py` is outside `_PIPELINE_CONTENT_GLOBS`. This story makes rotated areas *reachable*, so
  it becomes live. Fixing it (moving the Area geometry primitives into `models.py`, which is in the
  glob) is not in these tasks — decide explicitly whether to pull it in and record the call.

### References

- Epic + AC source: [epics.md](_bmad-output/planning-artifacts/epics.md:334) (Epic 15 / Story 15.3)
- Change proposal: [sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md)
  (§4.1 FR1/FR2 revisions, §4.2 story split, §"Technical Impact" envelope audit)
- Architecture: [architecture.md:268](_bmad-output/planning-artifacts/architecture.md) (rotated fetch),
  [architecture.md:332](_bmad-output/planning-artifacts/architecture.md) (envelope watch items incl.
  the report overlay)
- Previous stories: [15-2](_bmad-output/implementation-artifacts/15-2-rotated-setup-fetch-cache-schema-and-coverage.md)
  (provisional flag spellings, envelope-leak sites labelled `15.3`, `_GRAPH_PAYLOAD_VERSION`/
  `_INDEX_SCHEMA_VERSION` decisions), [15-1](_bmad-output/implementation-artifacts/15-1-generalize-area-model-and-geometry-helpers.md)
  (`Area` shape, why `radius_km` stayed required)
- Model: [models.py:27](src/steeproute/models.py) — `Area`, `half_extents_km`, `is_square`
- Flags + validation: [cli/_shared.py:112](src/steeproute/cli/_shared.py) `validate_area_size`,
  [:147](src/steeproute/cli/_shared.py) `validate_setup_radius`, [:334](src/steeproute/cli/_shared.py)
  area options; setup-side backstop [`_validate_area`](src/steeproute/pipeline/osm.py:155)
- Geometry + messaging: [`area_polygon`](src/steeproute/cache.py:1028),
  [`_area_km2`](src/steeproute/cache.py:1170), [`_format_area_flags`](src/steeproute/cache.py:1275),
  [`_format_area_geometry`](src/steeproute/cache.py:1297)
- Overlay: [`_search_bbox`](src/steeproute/output.py:288), [route.html.j2:114](src/steeproute/templates/route.html.j2)
- Golden harness: [regression.py:64](src/steeproute/regression.py) (`Fixture`),
  [:293](src/steeproute/regression.py) (`run_fixture` argv),
  [test_pinned_regressions.py](tests/e2e/test_pinned_regressions.py)
- Fixture caches (all square, 2.0 km half-side): `tests/e2e/fixtures/{grenoble_small,belledonne,vercors,chartreuse}/`
- App seams: [params_schema.py:40](src/steeproute/app/cli_adapter/params_schema.py) `_EXCLUDED_FIELDS`,
  [argv.py:61](src/steeproute/app/cli_adapter/argv.py) (unchanged — App 5.1)
- Docs to update: [README.md:56](README.md) (quickstart + key-parameter table),
  [docs/examples/README.md:51](docs/examples/README.md), [AGENTS.md](AGENTS.md) §Solver / GRASP

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- The first rotated-golden probe returned objectives **identical** to the committed square golden on
  all four fixture caches, at both 30° and 45°. Not a bug — `cli/query.py` uses the query `Area` for
  exactly two things (`check_coverage` entry selection and the report overlay); it never clips the
  graph. See the Completion Notes; the fixture design changed as a result.
- `basedpyright` flagged two test-side issues: a `**kwargs` dict widened to `dict[str, float]` at a
  `_resolve(...)` call (replaced with an explicit tuple unpack), and `reportPrivateUsage` on the new
  `_area_args` helper — resolved by making it public (`regression.area_args`), which it should be
  anyway now that it has its own tests.

### Completion Notes List

- **One resolver, two CLIs (`cli/_shared.py`).** `resolve_area` owns the whole area surface:
  exactly-one-of (`--radius` XOR `--width`+`--height`), the bearing, the sizes, and the center. Both
  CLIs call it and neither constructs an `Area` any more. `--radius` had to lose `required=True`
  because click cannot express exactly-one-of; the resolver's own check replaces it, so "no area at
  all" still exits 2 with an actionable message instead of click's Usage block.
- **Byte-identical square path.** `--radius r` builds the literal `Area(center=…, radius_km=r)` (not
  equal explicit extents), and `_validate_extent` carries `validate_setup_radius`'s exact strings, so
  every pre-existing square diagnostic and the whole cache-key/fetch/manifest chain are unchanged.
  **This story touches no file in `_PIPELINE_CONTENT_GLOBS`** (`pipeline/**`, `models.py`), so unlike
  15.1/15.2 it does not shift `pipeline_content_hash` — zero cache-key churn.
- **Query-side validation was genuinely missing.** The query CLI's only area guard was
  `validate_area_size`, and `nan > cap` is False — a NaN radius sailed through into a confusing
  coverage miss. Routing both CLIs through one resolver closes that; the query CLI now rejects
  malformed areas the same way setup always did.
- **FR2 cap correction is a real behavior change for squares.** `π·r²` → `4·r²` means the default
  `--area-cap 500` now admits a radius of 11.18 km instead of 12.61 km. Intended (the prepared region
  is the box, not the inscribed disk), but a user with a habitual `--radius 12` command will now be
  rejected and needs `--area-cap`. Two test assertions moved with it; `test_cli_smoke.py`'s radius-30
  case exceeds the cap under both formulas, so it needed no change. Goldens are unaffected —
  `--area-cap` is not pinned and the fixtures query at 1.5 km.
- **`osm._validate_area` deliberately left in place, duplicated.** It is setup's backstop for direct
  pipeline callers and is pinned by `tests/unit/test_osm.py`; on the CLI path it is now unreachable
  with bad input because the resolver runs first. Not merged because the only neutral home for a
  shared predicate is inside `_PIPELINE_CONTENT_GLOBS`, so de-duplicating it would re-key every cache
  entry on disk — a poor trade for one predicate. Both copies carry a cross-reference; the rule
  ("were the extents supplied?", never `is_square`, never the bearing) is stated in both.
- **Overlay closes the last envelope leak.** `_search_polygon` derives the ring from
  `cache.area_polygon`, so the report draws the region coverage actually tested against, and
  `output.py`'s private `111.32` deg/km constant is gone (a square's drawn box shifts ~0.3%;
  visual-only, the JSON sidecar never carried it). Verified end to end through the real CLI on the
  rotated fixture: the emitted ring's side lengths are exactly 3.0 / 1.6 / 3.0 / 1.6 km.
- **The rotated golden needed a rotated *prepared* entry — the story's plan didn't work.** The story
  assumed a rotated query over an existing square cache would do; it does not. The query area only
  picks a cache entry, so that golden would have duplicated `grenoble_small`'s route set and pinned
  nothing. What Epic 15 actually changes is setup-side truncation to the rotated ring, so
  `grenoble_small/regenerate_cache.py` now prepares **two** entries into one cache root, its offline
  stage-1 stand-in applying osmnx's own `truncate_graph_polygon` + `largest_component` (the two steps
  `graph_from_polygon` finishes with) for a non-square area. The rotated entry keeps ~43% of the
  square's area (645 KB vs 1.5 MB) and yields a demonstrably different route set
  (912/833/614/548/417 vs 1061/953/952/937/845).
- **The committed square entry was not rewritten.** Only `index.json` gained a row; the square
  `manifest.json` and `graph.pkl` are byte-for-byte as committed (same in-place philosophy as 15.2's
  schema migration). Confirmed the added row cannot change square entry selection: the rotated box is
  smaller by true area but does not contain the 3×3 km square query, so `_select_smallest_containing`
  still returns the square entry — and all four pre-existing goldens pass untouched.
- **App form guard.** `params_schema` is a live introspection of `cli.query`, so the three new flags
  would have rendered as stray numeric fields on the query config form. Added to `_EXCLUDED_FIELDS`,
  and the new test re-derives the area flags from the CLI's own options so a future area flag fails
  here rather than in the UI. `argv.py` still emits `--center/--radius` and is untouched (App 5.1).
- **Verification.** Full offline suite `uv run --no-sync pytest --cov`: **1155 passed, 18 deselected,
  96% coverage, exit 0** (2m28s) — 64 tests added over 15.2's 1091. Per-directory: unit 841,
  integration 204, e2e 110. Goldens: **10/10 across both tiers**, `git status tests/e2e/goldens/`
  shows only the two new rotated files — **no rebake**. `basedpyright` clean (0 errors, 0 warnings)
  on all 13 changed source and test files.
- **Known follow-up, unchanged from 15.2 and now live.** `cache.area_polygon` determines what a
  rotated area fetches, but `cache.py` is outside `_PIPELINE_CONTENT_GLOBS` — so an edit to the
  rotation math would change rotated graphs without shifting `pipeline_content_hash`, leaving stale
  rotated entries keyed as valid. Rotated areas are reachable from the CLI as of this story, so the
  latency is gone. Not fixed here (the fix is a module move, outside this story's tasks); 15.2's
  recommendation stands: move the Area-derived geometry primitives into `models.py` and re-export.

### File List

- `src/steeproute/cli/_shared.py` (modified — `--width`/`--height`/`--angle` options, `--radius` no
  longer required, `resolve_area`, `_validate_extent`/`_validate_center`/`_is_radius_shorthand`,
  true-area `validate_area_size`, `validate_setup_radius` → `validate_setup_area`)
- `src/steeproute/cli/setup.py` (modified — shared area decorators + resolver, `validate_setup_area`)
- `src/steeproute/cli/query.py` (modified — shared area decorators + resolver, `Area`-based cap check)
- `src/steeproute/cache.py` (modified — `_area_km2` → public `area_km2`, `_format_area_flags` →
  public `format_area_flags`, docstrings de-provisionalized)
- `src/steeproute/output.py` (modified — `_search_bbox` → `_search_polygon` off `area_polygon`)
- `src/steeproute/templates/route.html.j2` (modified — `L.rectangle` → `L.polygon`)
- `src/steeproute/regression.py` (modified — `Fixture` area spelling, public `area_args`,
  `grenoble_small_rotated` fixture)
- `src/steeproute/app/cli_adapter/params_schema.py` (modified — area flags excluded from the form)
- `tests/unit/test_area_parsing.py` (modified — `resolve_area`, true-area cap, setup ceiling, both-CLI
  surface, rotated coverage-miss suggestion; +30 tests)
- `tests/unit/test_output.py` (modified — polygon overlay + rotated-overlay tests)
- `tests/unit/test_cli_help.py` (modified — new flags on both CLIs + FR23 set-equality test)
- `tests/unit/test_canonical_edge_hash.py` (modified — `area_args` + golden-path-uniqueness tests)
- `tests/unit/test_app_params_schema.py` (modified — area-flag leak guard)
- `tests/e2e/fixtures/grenoble_small/regenerate_cache.py` (modified — prepares square + rotated
  entries; shape-aware offline stage-1 stand-in)
- `tests/e2e/fixtures/grenoble_small/README.md` (modified — two-entry documentation)
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/index.json` (modified — rotated entry row)
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/areas/d0bb61a840431553/` (new — rotated
  prepared entry: `manifest.json`, `graph.pkl`, `bounds.geojson`)
- `tests/e2e/goldens/grenoble_small_rotated.json` (new — fast-tier rotated golden)
- `tests/e2e/goldens/grenoble_small_rotated.realistic.json` (new — realistic-tier rotated golden)
- `README.md` (modified — non-square areas section, key-parameter rows)
- `docs/examples/README.md` (modified — rotated spelling note)
- `AGENTS.md` (modified — area surface + true-area cap in the quality-demo params note)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status bookkeeping)

## Change Log

- 2026-07-26: Implemented Story 15.3 — shared `--radius` | `--width`/`--height` + `--angle` area
  surface on both CLIs via one `resolve_area`, FR2 cap moved onto the true rectangle area, setup
  ceiling generalized per dimension, report overlay switched to the true (possibly rotated) polygon
  off `cache.area_polygon`, and a rotated-rectangle regression golden added over a newly prepared
  rotated cache entry. Square path byte-identical throughout; **no golden rebake** and no
  `pipeline_content_hash` shift. Full offline suite green (1155 passed). Status → review.
- 2026-07-26: Code review (low-effort diff pass) — 2 findings, both fixed. Two stale "~178 km
  radius" / disk-area figures (AGENTS.md, and its mirror in `params_schema.py`) left over from the
  `π·r²` → `4·r²` cap correction, corrected to ~158 km; and `_SETUP_CEILING_DETAIL`'s wording
  ("very large radii", "bounding box from Overpass") generalized to cover the `--width`/`--height`
  rejection path it is now shared with. No behavior change; unit suite and `basedpyright` re-verified
  clean. Status → done.
