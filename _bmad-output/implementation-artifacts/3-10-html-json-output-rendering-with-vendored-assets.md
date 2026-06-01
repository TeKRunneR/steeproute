# Story 3.10: HTML + JSON output rendering with vendored assets

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want `output.py::render(...)` producing one self-contained HTML report + one JSON sidecar per validated route, with Leaflet + Chart.js inlined from vendored assets,
so that FR15–21 and FR29 (seed recording) are fulfilled and reports are portable files with zero runtime CDN dependency for their library assets.

## Acceptance Criteria

1. `output.py::render(...)` renders one `route-<i>.html` + one `route-<i>.json` per route for `i` in `1..N` (FR15, FR16, FR21), using Jinja2 with `templates/route.html.j2` and `json.dumps` for the sidecar. Both files per route are **atomic-written** (`.tmp` sibling + `os.replace()`), existing files overwritten in place (idempotent re-runs), and writes are confined to `output_dir` (no network I/O). `jinja2` is added to `pyproject.toml` runtime `dependencies`.

2. **Geometry resolution** (the key design decision — see Dev Notes): for each route, the renderer resolves the ordered WGS84 vertex sequence (`(lat, lon, elevation)`) from the graph — super-edges expanded via `ContractedGraph.super_edge_to_base` then looked up in the base operational graph's `vertices_resampled`; connectors read directly. `render`'s signature gains whatever parameter(s) make this reachable at the CLI layer (Architecture's literal 5-arg signature is insufficient because `Edge` carries no geometry — document the deviation).

3. **HTML self-containment:** Leaflet (`leaflet-1.9.4.min.{js,css}`) and Chart.js (`chart-4.4.0.min.js`) are inlined as `<script>` / `<style>` blocks from `src/steeproute/templates/assets/`. A test greps the rendered HTML and asserts **zero `src=` or `href=` attributes referencing external URLs**. The OSM basemap is a Leaflet tile layer (tile URLs constructed in JS, fetched live only when the report is opened) — this is acceptable and not in scope of the grep.

4. **Metadata block — HTML and JSON mirror each other** (Architecture §Cat 9): every metadata field appears in **both** surfaces — all 12 `SolverParams` fields, full `ProvenanceInfo` (`steeproute_version`, `git_commit_short` with `-dirty` suffix, `osm_extract_date`, `dem_version`, `pipeline_content_hash`), `convergence_status`, per-route metrics (length, D+, D−, avg gradient), the validation summary (pass/fail + violated constraints with details), and the pinned vendored-asset versions. `seed` is recorded in both (FR29). The JSON sidecar additionally carries the route's edge-identity sequence (`(node_u, node_v, key)` triples — there is no synthetic int edge id in the model) and the WGS84 `vertices`.

5. **Validation-failure banner** renders conditionally per Architecture §Cat 6b: present when `route.validation.passed is False` **OR** any `PairwiseViolation` in the set references this route's index; absent otherwise. When present it shows a prominent `VALIDATION FAILED` banner naming the violated constraint(s) with their `detail`/`numeric` (FR27), surfacing both per-route `ConstraintViolation`s and the affecting `PairwiseViolation`s.

6. **Pinned asset versions** (`leaflet-1.9.4`, `chart-4.4.0`) are module-scope named constants in `output.py`, surfaced in the metadata block; the three vendored asset files are committed under `src/steeproute/templates/assets/` and ship in the wheel (package data — load via `importlib.resources`, not `__file__`-relative paths).

7. `tests/unit/test_output.py` invokes `render(...)` against crafted small fixtures (a `ValidatedRouteSet` + graph carrying synthetic `vertices_resampled`) and asserts: every metadata field present in both HTML and JSON; banner present-iff-failing / absent-when-clean; the self-containment grep; the `route-<i>.{html,json}` filename pattern; and that a mid-render `monkeypatch`-raised `KeyboardInterrupt` leaves no half-written `.html`/`.json` file. `tests/integration/test_output_on_fixture.py` runs the real Grenoble pipeline → solver → validator → `render(...)` and asserts the files exist, each HTML parses, and map + elevation-profile sections are present.

8. All four CI gates green on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. Coverage floors hold (`output.py` under the 80% overall floor; `templates/` excluded from coverage per Architecture §Cat 11e). `jinja2` is the only new runtime dependency.

## Tasks / Subtasks

- [x] Task 1: Vendor assets + add the dependency. (AC: #1, #3, #6)
  - [x] Download `leaflet-1.9.4.min.js`, `leaflet-1.9.4.min.css`, `chart-4.4.0.min.js` from their official releases into `src/steeproute/templates/assets/` and commit them; confirm versions.
  - [x] Add `jinja2` to `pyproject.toml` runtime `dependencies`; confirm the wheel build includes `templates/` package data (`uv build` + inspect, or rely on hatchling default + an `importlib.resources` smoke check).
- [x] Task 2: Write `templates/route.html.j2` — map section (Leaflet, polyline as embedded GeoJSON), gradient-colored elevation profile (Chart.js), metadata block, conditional validation banner; `{% include %}` the inlined assets. (AC: #3, #4, #5)
- [x] Task 3: Implement `output.py::render(...)` — iterate routes, resolve geometry (Task per AC #2), build the shared metadata dict, render HTML via Jinja2 + JSON via `json.dumps`, atomic-write both. (AC: #1, #2, #4, #6)
  - [x] Reuse the atomic-write pattern: `cache.write_json_atomic` for the sidecar; for HTML, add/lift a sibling `write_text_atomic` helper rather than reimplementing `os.replace()` inline.
- [x] Task 4: `tests/unit/test_output.py` — crafted fixtures; metadata-in-both, conditional banner, self-containment grep, filename pattern, interrupt-safety. (AC: #7)
- [x] Task 5: `tests/integration/test_output_on_fixture.py` — real-fixture render, files parse, map + profile present. (AC: #7)
- [x] Task 6: Run all four gates on Windows; confirm coverage floors and that `jinja2` is the only new dep. (AC: #8)

### Review Findings

_Adversarial code review (3 layers: blind hunter, edge-case hunter, acceptance auditor) run 2026-06-01. 4 patch + 2 defer + ~18 dismissed (deduped). No decision-needed; all 8 ACs functionally satisfied by the code. Headline: the geometry resolver crashes on a validation-failed route that FR28 requires rendering — a near-term blocker for Story 3.11's validation-failure-path E2E test._

- [x] [Review][Patch] **(HIGH)** Geometry resolution raises `KeyError` on a route edge absent from `base_graph` (or lacking the `vertices_resampled` attribute). A `graph_membership`-failed route — which FR28 requires rendering *with a banner* — instead aborts `render` for **all** routes. Story 3.11's planned `test_validation_failure_path.py` injects a Solution referencing a non-existent edge, so this is directly reachable, not theoretical. **Fixed:** `_edge_vertices` now returns `[]` on a missing edge/attribute (try/except `KeyError` + `.get`), the render loop passes `has_geometry = len(vertices) >= 2`, and the template guards the map + elevation-profile sections and their init `<script>` behind `{% if has_geometry %}` (degenerate routes show a "geometry unavailable" note). Regression test `test_route_with_unresolvable_edge_still_renders`. [src/steeproute/output.py:_edge_vertices] [src/steeproute/templates/route.html.j2] [source: edge+blind]
- [x] [Review][Patch] **(MED)** AC #4/#7 test gap: `n` and `area_cap` (2 of the 12 `SolverParams`) render correctly but are not asserted present in both HTML + JSON. **Fixed:** added `"29"` (n) + `"555.0"` (area_cap) to `_EXPECTED_METADATA_STRINGS`, and the HTML side now asserts against a `_html_body` helper that strips the inlined `<script>`/`<style>` blobs so the presence check targets the rendered metadata, not the 350 KB of library text. [tests/unit/test_output.py] [source: auditor]
- [x] [Review][Patch] **(MED)** AC #4 test gap: per-route metrics + validation summary present in both surfaces in code but not asserted in the **HTML**. **Fixed:** `test_metrics_and_validation_summary_render_in_html` asserts length/D+/avg-gradient/"passed" in the stripped HTML body; `test_json_sidecar_structure` now also asserts the full `metrics` dict in JSON. [tests/unit/test_output.py] [source: auditor]
- [x] [Review][Patch] **(LOW)** AC #1 names "idempotent overwrite (re-runs)" but no test re-renders into a populated `output_dir`. **Fixed:** `test_rerender_overwrites_existing_files_in_place` re-renders and asserts route-1 reflects the second render. [tests/unit/test_output.py] [source: auditor]
- [x] [Review][Defer] **(MED)** A `NaN`/non-finite coordinate or elevation serializes as a bare `NaN`/`Infinity` token (invalid JSON) in the sidecar and breaks the in-browser map/profile. — deferred: same cross-cutting finiteness contract deferred in Stories 3.5/3.6/3.9; `pipeline/dem.py` guarantees finite elevations today, so it's structurally unreachable via the real pipeline. [src/steeproute/output.py:_profile_series] [source: edge]
- [x] [Review][Defer] **(LOW)** `output_dir` existing as a regular file makes `output_dir.mkdir(...)` raise an unfriendly `FileExistsError`/`NotADirectoryError`. — deferred: user-supplied-path validation belongs at the CLI boundary (Story 3.11), not in `render`. [src/steeproute/output.py:render] [source: edge]

## Dev Notes

- **Design decision — geometry resolution & the `render` signature (confirm before locking).** `Edge` deliberately carries no geometry (`models.py:60` — "Geometry and resampled vertices stay graph-side; consumers reach them via `super_edge_to_base`"). But `render` must produce a polyline + per-vertex elevation profile, and the architecture's literal `render(validated_set, params, provenance, convergence, output_dir)` signature (§Cat 9) passes **no graph** — it cannot reach geometry as written. Resolution path the geometry actually lives on:
  - **Connectors:** surviving connectors carry the full base edge-data dict, including `vertices_resampled` (`pipeline/graph.py:94-106`) — readable directly off the contracted graph by `(node_u, node_v, key)`.
  - **Super-edges:** carry **only metrics** (`graph.py:121-130`); their geometry is the concatenation of their climb's base edges. `super_edge_to_base[(u,v,k)]` gives the base `Edge` tuple, but those `Edge` dataclasses also lack geometry — so each must be looked up in the **base operational graph** (`prepared.graph`, the post-stage-7 `MultiDiGraph` whose edges carry `vertices_resampled` as `(lat, lon, elevation_m)`, **lat-first** per `pipeline/dem.py:13-16`).
  - **Recommended:** extend `render` to also take the base operational graph and the `ContractedGraph` (or just its `super_edge_to_base`). Both are in hand at the CLI layer — Story 3.11 already holds `prepared.graph` and builds the contracted graph. This deviates from the architecture's 5-arg signature; documented deviation, and far less invasive than retrofitting geometry onto the now-done `Edge`/validator (Story 3.9). **Flagged to {user_name} below.**
- **`Route.edges` are contracted-graph edges** (super-edges + connectors), identified by `(node_u, node_v, key)` — the same identity the validator and `solver/distinctness.py` use. Do not assume they are base edges.
- **Axis ordering:** `vertices_resampled` is `(lat, lon, elevation_m)` (lat-first), while shapely `geometry` is `(lon, lat)`. Leaflet expects `[lat, lon]`; GeoJSON expects `[lon, lat]`. Pick one path and be explicit — getting this wrong silently transposes the map.
- **Self-containment caveat (Leaflet CSS):** Leaflet's CSS references relative `images/` (marker icons, `layers.png`) via `url(...)`. Rendering a polyline-only map (no markers, no layer control) avoids needing them; any residual relative refs are cosmetic, not external URLs, so they don't fail the AC #3 grep. The basemap **tiles** are a live network fetch when the report is opened — expected; "no CDN at runtime" (PRD/§Cat 9) means the **library assets** are inlined, not that the map works offline.
- **Atomic writes:** reuse `cache.write_json_atomic` (`cache.py:336`) for the JSON sidecar — it already does `.tmp` + `os.replace()` + orphan cleanup. HTML has no helper; add a small `write_text_atomic(path, text)` following the same pattern (lift it next to `write_json_atomic` so the `os.replace` pattern stays single-sourced). `output_dir` is user-supplied — keep the `.tmp` sibling in the same directory so `os.replace` stays atomic (no cross-device rename).
- **Metadata single-sourcing:** `SolverParams` field order already matches the §Cat 9 metadata list 1:1 (`models.py:124-163`) — iterate its fields rather than hand-listing. Build the metadata dict once and feed it to both the Jinja2 context and `json.dumps` so HTML and JSON cannot drift (AC #4's mirror requirement).
- **`convergence` value** is `Literal["converged", "budget-exhausted", "interrupted"]` (§Cat 9); Epic 3's CLI (Story 3.11) passes a fixed value — full convergence semantics land in Epic 4. Surface it verbatim in metadata.
- **Validation summary is data, not exceptions** (§Cat 6c): read `route.validation` + `validated_set.set_violations`; never raise on a failed route — failed routes are still written (FR28), exit-code logic is Story 3.11's job.

### Project Structure Notes

- **Implement:** `src/steeproute/output.py` (currently a one-line placeholder docstring — replace it); `src/steeproute/templates/route.html.j2`; `src/steeproute/templates/assets/{leaflet-1.9.4.min.js,leaflet-1.9.4.min.css,chart-4.4.0.min.js}` (new package-data dirs).
- **New tests:** `tests/unit/test_output.py`, `tests/integration/test_output_on_fixture.py`.
- **Reuse (do not duplicate):**
  - `steeproute.models` — `ValidatedRouteSet`, `Route`, `RouteMetrics`, `RouteValidation`, `ConstraintViolation`, `PairwiseViolation`, `SolverParams`, `ProvenanceInfo`, `ContractedGraph`, `Edge` (Story 3.1) — consume, do not redefine.
  - `steeproute.cache.write_json_atomic` — atomic JSON write; add a sibling `write_text_atomic` for HTML rather than reinventing `os.replace()`.
  - `steeproute.provenance` — `get_commit_short` (already yields the `-dirty` suffix); `iso8601_utc_now` if a render timestamp is wanted.
  - `pipeline/graph.py` super-edge / connector contract and `pipeline/dem.py` `vertices_resampled` axis ordering — for geometry resolution.
- **Networkx boundary:** reading edge data off `graph.graph` / `prepared.graph` (`MultiDiGraph` typed `Any`) surfaces Unknown types — mirror the `# pyright:` pragma header used in `cli/query.py:1-3`, `grasp.py`, and `pipeline/`.
- **Coverage:** `templates/` is excluded (§Cat 11e, line 1031/1038); `output.py` is **not** a 95% pure-logic module — it holds the 80% overall floor. The integration smoke test is what exercises the template.

### Testing standards summary

- Unit tests in `tests/unit/`, integration in `tests/integration/`; naming `test_<unit>_<scenario>` (Architecture §"Test organization"). No `pytest.skip`/`xfail`.
- **Unit layer uses crafted fixtures** (small `ValidatedRouteSet` + a tiny `ContractedGraph`/base graph carrying synthetic `vertices_resampled`) — fast, deterministic, exercises every metadata field, both banner branches, and interrupt-safety. This is the lean interpretation of the epic's "validated set built from real fixture output": the **integration** test is what runs the real Grenoble fixture.
- **Integration layer** reuses the fixture wiring from `tests/integration/test_validator_on_fixture.py` (`osm_load` patched → `detect_climbs` → `contract_climbs` → `GraspSolver.run()` → `validate(...)`), then calls `render(...)` into a `tmp_path` and asserts files/structure.
- Assert HTML parses (`html.parser` / stdlib is enough; no new test dep) and the map + profile sections are present (e.g. presence of the Leaflet/Chart.js init markers).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.10"](../planning-artifacts/epics.md) — render signature, vendored-inline assets, metadata-in-both, self-containment grep, banner logic, filename pattern, interrupt-safety, pinned versions
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 9](../planning-artifacts/architecture.md) — Jinja2 + vendored-asset strategy, `render` interface, metadata block contents, geometry-in-HTML/JSON, template/asset source tree (lines 810-815)
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 6b](../planning-artifacts/architecture.md) — banner logic (`show_banner` condition) + per-route vs. set-level violation split
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 11e](../planning-artifacts/architecture.md) — coverage floors; `templates/` excluded, smoke-test-only
- [Source: _bmad-output/planning-artifacts/prd.md §FR15–FR21, FR29](../planning-artifacts/prd.md) — HTML per route, JSON sidecar, map, gradient profile, metadata list, output dir, filename pattern, seed reproducibility
- [Source: src/steeproute/models.py:60-292](../../src/steeproute/models.py) — `Edge` (no geometry), `SolverParams` (§Cat 9 field order), `ProvenanceInfo`, `ValidatedRouteSet`/`Route`/`RouteValidation`/`PairwiseViolation` shapes
- [Source: src/steeproute/pipeline/graph.py:45-138](../../src/steeproute/pipeline/graph.py) — connectors carry `vertices_resampled`; super-edges carry metrics only; `super_edge_to_base` back-mapping
- [Source: src/steeproute/pipeline/dem.py:13-16](../../src/steeproute/pipeline/dem.py) — `vertices_resampled` = `(lat, lon, elevation_m)`, lat-first vs. shapely `(lon, lat)`
- [Source: src/steeproute/cache.py:336-359](../../src/steeproute/cache.py) — `write_json_atomic` (`.tmp` + `os.replace()` pattern to reuse)
- [Source: src/steeproute/provenance.py](../../src/steeproute/provenance.py) — `get_commit_short` (`-dirty` suffix), `iso8601_utc_now`
- [Source: tests/integration/test_validator_on_fixture.py:91-123](../../tests/integration/test_validator_on_fixture.py) — real-fixture wiring to reuse for the integration render test

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. One new runtime dep: `jinja2 3.1.6` (pulls `markupsafe` transitively). Vendored assets fetched once from unpkg (Leaflet 1.9.4) + jsDelivr (Chart.js 4.4.0) and committed.

**Final pass (all green, repo-wide):**

```
uv run ruff check .                  → All checks passed!
uv run ruff format --check .         → 70 files already formatted
uv run basedpyright                  → 0 errors, 0 warnings, 0 notes
uv run pytest                        → 617 passed, 1 deselected in 76 s
                                       (was 608 after 3.9; +9 = 8 unit + 1 integration)
uv build --wheel                     → wheel contains templates/route.html.j2 + all 3 assets
```

### Completion Notes List

**Geometry-resolution design decision (the open question flagged at story creation) — resolved as recommended.** `render` takes the **base operational graph + the `ContractedGraph`** in addition to the Architecture §Cat 9 sketch's five args, because `Edge` carries no geometry and a route's edges are contracted-graph edges. Super-edges are expanded via `contracted.super_edge_to_base` to their base edges; every base edge and plain connector is then resolved against `base_graph`'s `vertices_resampled` (`(lat, lon, elev)`, lat-first). Both graphs are in hand at the CLI layer (Story 3.11), so this is free to wire and far less invasive than retrofitting geometry onto the now-done `Edge`/validator. Final signature: `render(validated_set, base_graph, contracted, params, provenance, convergence, output_dir)`.

**Design decisions worth review attention:**

1. **Assets inlined via `| safe` context variables, not `{% include %}`.** The Architecture sketch said `{% include %}`, but minified Leaflet/Chart.js can contain `{{`/`{%` sequences that Jinja would try to parse. Instead the asset text is read once (`importlib.resources`) and passed as template variables rendered with `| safe`. Same self-contained output, robust against the minified payload. The template still uses `PackageLoader("steeproute", "templates")` so it resolves from an installed wheel.
2. **Self-containment test targets resource tags, not raw substrings.** The vendored Leaflet JS legitimately contains an `href="https://leafletjs.com"` *string literal* (its built-in attribution), so a naive `grep 'href="http'` on the rendered HTML would false-positive on the inlined `<script>` body. The test asserts no external-loading tag instead: no `<script src>`, no `<link>`, no `<img src=http>`. The OSM basemap tile URL lives in a JS string (`L.tileLayer("https://...")`) — fetched live only when the report is opened, exactly as intended ("no CDN" = library assets inlined, not an offline map).
3. **Atomic writes single-sourced.** Lifted a generic `write_text_atomic(path, text)` into `cache.py` (next to `write_json_atomic`, which now delegates to it) so the `.tmp` + `os.replace()` pattern lives in one place; `output.py` reuses both. These are output-dir files, not cache files, so this doesn't cross the §Boundaries cache-write boundary.
4. **Metadata single-sourced across HTML + JSON.** `_build_metadata` builds the block once (params via `asdict`, provenance with the `-dirty` suffix folded in, convergence, asset versions) and both the Jinja2 context and the JSON sidecar consume the same dict — they cannot drift (AC #4).
5. **JSON edge identity.** The sidecar's `edges` are `(node_u, node_v, key)` triples (the model's canonical identity) — there is no synthetic int edge id in the data model, so the §Cat 9 `edge_ids` field is realized as these triples. `vertices` are `[lat, lon, elev]` lists.

**AC walkthrough:**

1. AC #1 — `render` writes `route-<i>.{html,json}` for `i in 1..N`, Jinja2 + `json.dumps`, atomic (`write_text_atomic`/`write_json_atomic`), idempotent overwrite, output-dir-confined; `jinja2` added to deps. ✅
2. AC #2 — geometry resolved from `base_graph` + `super_edge_to_base`; signature extended (documented deviation). ✅
3. AC #3 — Leaflet + Chart.js inlined; self-containment test green (no external resource tags). ✅
4. AC #4 — all 12 params, full provenance (`-dirty`), convergence, metrics, validation summary, asset versions mirrored in HTML + JSON; `seed` in both (FR29). ✅
5. AC #5 — banner shows iff `not passed` OR a `PairwiseViolation` references the route; `VALIDATION FAILED` + constraint detail/numeric + pairwise copy. ✅
6. AC #6 — `LEAFLET_VERSION`/`CHARTJS_VERSION` module constants surfaced in metadata; 3 assets committed; wheel-bundled; loaded via `importlib.resources`. ✅
7. AC #7 — `test_output.py` (8 unit: metadata-in-both, banner branches, self-containment, filename pattern, interrupt-safety, JSON structure) + `test_output_on_fixture.py` (real Grenoble render parses, map + profile present). ✅
8. AC #8 — ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 617 passed ✅; `jinja2` the only new runtime dep. ✅

### File List

**New:**
- `src/steeproute/templates/route.html.j2` — Jinja2 report template (map + gradient elevation profile + metadata block + conditional banner; inlined assets).
- `src/steeproute/templates/assets/leaflet-1.9.4.min.js` — vendored (committed).
- `src/steeproute/templates/assets/leaflet-1.9.4.min.css` — vendored (committed).
- `src/steeproute/templates/assets/chart-4.4.0.min.js` — vendored (committed).
- `tests/unit/test_output.py` — 8 unit tests on crafted fixtures.
- `tests/integration/test_output_on_fixture.py` — 1 real-fixture render test.

**Modified:**
- `src/steeproute/output.py` — replaced placeholder with `render(...)` + geometry-resolution / metadata / GeoJSON / profile helpers.
- `src/steeproute/cache.py` — added `write_text_atomic`; `write_json_atomic` now delegates to it.
- `pyproject.toml` — added `jinja2>=3,<4` to runtime dependencies.
- `uv.lock` — `jinja2` + `markupsafe` resolved.
- `_bmad-output/implementation-artifacts/3-10-...md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review → done`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked to `done`; `last_updated: 2026-06-01`.
- `_bmad-output/implementation-artifacts/deferred-work.md` — 2 deferred review findings (NaN-finiteness, output-dir-is-a-file) logged under the 2026-06-01 code-review heading.

**Post-review (patches applied 2026-06-01):**
- `src/steeproute/output.py` — `_edge_vertices` tolerates a missing edge / `vertices_resampled` (returns `[]`); render passes `has_geometry`.
- `src/steeproute/templates/route.html.j2` — map + profile sections and init script guarded behind `{% if has_geometry %}`.
- `tests/unit/test_output.py` — +3 tests (metrics/validation-in-HTML, idempotent re-render, unresolvable-edge renders); `_html_body` asset-stripping helper; `n`/`area_cap` added to the metadata-mirror assertion; now 11 unit tests.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-01 | Yann (Claude Opus 4.8) | Code review (3 adversarial layers: blind hunter, edge-case hunter, acceptance auditor) — deduped to 4 patch + 2 defer + ~18 dismissed; no decision-needed; all 8 ACs functionally satisfied. **All 4 patches applied.** **(HIGH)** geometry resolver no longer crashes on a route edge missing from the graph — `_edge_vertices` returns `[]`, template guards map/profile behind `has_geometry`; unblocks Story 3.11's validation-failure-path E2E test (FR28: failed routes still render). **(MED×2 + LOW)** closed AC #4/#7 test gaps — `n`/`area_cap` now asserted in both surfaces, metrics+validation-summary asserted in HTML (via a `_html_body` asset-stripping helper), idempotent-overwrite test added. 2 defers logged to `deferred-work.md` (NaN-finiteness contract — same cross-cutting item as 3.5/3.6/3.9; output-dir-is-a-file — CLI boundary). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 620 passed (was 617; +3). Status → done. | _pending_ |
| 2026-06-01 | Yann (Claude Opus 4.8) | Story 3.10 implemented: HTML + JSON report rendering with vendored assets (`output.py`, `templates/route.html.j2`, FR15–21/FR29, Architecture §Cat 9). `render` writes self-contained `route-<i>.{html,json}` per route — Leaflet 1.9.4 + Chart.js 4.4.0 inlined, atomic writes, conditional VALIDATION FAILED banner, HTML+JSON-mirrored metadata. Geometry resolved from the base graph + `super_edge_to_base` (signature extended past the §Cat 9 sketch; documented). Added `write_text_atomic` to `cache.py`; added `jinja2` runtime dep. **New:** `route.html.j2`, 3 vendored assets, `tests/unit/test_output.py` (8), `tests/integration/test_output_on_fixture.py` (1). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 617 passed (was 608; +9); wheel bundles the template + assets. Status → review. | _pending_ |
