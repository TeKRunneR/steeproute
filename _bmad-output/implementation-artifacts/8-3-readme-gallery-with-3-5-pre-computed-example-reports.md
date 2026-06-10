# Story 8.3: README gallery with 3–5 pre-computed example reports

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a reader of the GitHub repo,
I want a visible gallery in the README showing 3–5 Grenoble-area example query results — map screenshot + elevation profile PNG + link to the interactive HTML report for each —
so that portfolio credibility is established: a visiting reviewer can see the tool works and produces the kind of route ideas the PRD describes.

## Acceptance Criteria

1. **3–5 gallery regions generated with real runs.** Pick 3–5 Grenoble-area query regions demonstrating distinct terrain character. These are gallery regions, not test fixtures — full-size queries (e.g. 5–10 km radius), distinct from the 8.2 regression cutouts (`belledonne`/`vercors`/`chartreuse` are 2 km test fixtures). For each region run real `steeproute-setup` + `steeproute` with a fixed `--seed` and documented params, and commit the generated HTML reports to `docs/examples/<region_name>/route-*.html`.

2. **README Gallery section.** `README.md` gains a `## Gallery` section with one row per region containing: thumbnail map PNG, thumbnail elevation-profile PNG (both captured from the region's route-1 report), a one-line description (e.g. `Chamrousse ridgeline · 10 km radius · ~12 min query`), and a clickable link to the full HTML report in `docs/examples/`.

3. **Self-containment test.** `tests/e2e/test_gallery_self_contained.py` asserts every `.html` file under `docs/examples/` is self-contained, reusing the Story 3.10 grep (no `<script src=`, no `<link`, no `<img src=http(s)://`). JS-constructed tile URLs inside inline scripts remain exempt, exactly as in 3.10. The test runs offline against committed files, is not `live`-marked (so default CI enforces it), and fails rather than passes vacuously if `docs/examples/` contains no HTML files.

4. **Regeneration documentation.** `docs/examples/README.md` documents, per region: the exact `steeproute-setup` and `steeproute` commands (center, radius, seed, any non-default params) so any gallery file can be regenerated, plus query wall-clock and the screenshot-capture method used.

5. **PNG budget.** Total PNG assets committed to the repo stay under 5 MB.

6. **Memory recorded (NFR2 reality check).** During gallery generation, approximate peak memory usage per query is recorded (in `docs/examples/README.md`). If any region exceeds 12 GB, it is flagged explicitly for Story 8.4's Known Limitations section.

## Tasks / Subtasks

- [x] Select 3–5 full-size gallery regions with distinct terrain character (AC: #1)
  - [x] Confirm they differ from the 8.2 regression cutouts (different areas and/or full-size radii)
- [x] Run `steeproute-setup` + `steeproute` per region with fixed seed + documented params (AC: #1, #6)
  - [x] Use a throwaway runtime cache dir (do NOT commit caches — only `docs/examples/` output is committed)
  - [x] Record wall-clock (from the FR22 run summary) and approximate peak memory per query
- [x] Commit `route-*.html` per region under `docs/examples/<region_name>/` (AC: #1)
- [x] Capture map PNG + elevation-profile PNG from each region's route-1 report; downscale thumbnails to hold the 5 MB budget (AC: #2, #5)
- [x] Write the `## Gallery` README section (AC: #2)
- [x] Write `docs/examples/README.md` with per-region regeneration commands + recorded measurements (AC: #4, #6)
- [x] Implement `tests/e2e/test_gallery_self_contained.py` (AC: #3)
- [x] Verify: PNG total < 5 MB, full suite green, gallery links/images render on the GitHub-flavored README (AC: #2, #3, #5)

## Dev Notes

### Prerequisites and stale reference

- The epics "Given Stories 3.11 and 6.5" predates the correct-course renumbering; the real prerequisites (query CLI end-to-end, run summary, report polish) are all done — Epics 3, 6, 7 complete. No blocker.

### Generation runs are real and need network

- `steeproute-setup` fetches OSM via Overpass and auto-downloads DEM from the IGN Géoplateforme WMS (no `--dem-path` flag). Same environment note as 8.1/8.2: external TLS works via `uv sync --native-tls` / vendored `truststore`. Full-size radii mean meaningfully longer setup + query than the 8.2 cutouts — that's the point (the one-line description advertises query time).
- **Prepare slightly larger than you query.** `check_coverage` uses strict containment, so a query at the same center/radius as the setup run can fail-fast as "unprepared" — 8.2 hit this and settled on a seed/query radius split (prepared 2.0 km, queried 1.5 km), mirroring `grenoble_small`. Do the same here (e.g. setup 10.5 km, query 10 km) and document both radii.
- Query output dir: `.gitignore` ignores `/results*/` and `cache/`; either `--output-dir docs/examples/<region>` directly or generate elsewhere and copy. The HTML/JSON sidecars under `docs/examples/` are committed; runtime caches are not.
- The gallery is documentation, not a golden: regeneration with live OSM data won't be byte-identical later. Pin the seed and document commands verbatim — that satisfies the AC; no hash discipline here (that's 8.1/8.2 territory).

### Report weight — bound what you commit

- Each HTML report inlines ~370 KB of vendored Leaflet+Chart.js plus geometry, so 5 regions × N=5 routes ≈ 10+ MB of committed HTML. The 5 MB budget is PNG-only, but keep repo growth sane: consider `--n 3` per region (documented param) or committing fewer routes per region. The Gallery row links region's route-1; extra routes are optional context.

### Self-containment test — reuse the 3.10 check verbatim

- Copy the regexes from [tests/unit/test_output.py:341-352](tests/unit/test_output.py:341): assert no `<script ... src=`, no `<link`, no `<img src="http(s)://`. Inline script *bodies* legitimately contain URL strings (OpenTopoMap tile template, attribution links) — the assertion targets resource-loading tags only ([3-10 story AC #3]). Scan committed files on disk; no rendering, no fixtures, fully offline. Parametrize over `sorted(Path("docs/examples").rglob("*.html"))` and assert the collection is non-empty so path drift can't make the test pass vacuously.
- Not `live`-marked ⇒ the existing CI `uv run pytest` enforces it with no workflow edit ([pyproject.toml] `addopts = ["-m", "not live"]`).

### Screenshots and memory — keep it lightweight

- PNG capture: open route-1.html in a browser and screenshot the map pane and the elevation-profile chart (a Chart.js canvas). Manual capture is fine for a one-time doc artifact — no new dependency; document the method in `docs/examples/README.md`.
- Peak memory: approximate is explicitly acceptable. On Windows, `Get-Process` `PeakWorkingSet64` on the query process (or an equivalent one-liner wrapper) is enough; no psutil dependency. Record per region; >12 GB ⇒ flag for 8.4 (NFR2 says 16 GB envelope).

### Scope boundary with 8.4

- 8.4 owns `## Known Limitations`, `## Quickstart`, and `test_readme_references_gallery.py`. Here, add the Gallery section (and feel free to drop the copier "fill me in" placeholder blurb at the top of README.md) — don't restructure the rest.

### Project Structure Notes

- **New:** `docs/examples/<region_name>/route-*.html` (+ optional `route-*.json` sidecars), `docs/examples/<region_name>/*.png` (map + profile thumbnails), `docs/examples/README.md`, `tests/e2e/test_gallery_self_contained.py`.
- **Modified:** `README.md` (Gallery section).
- `docs/examples/` is the gallery home per Architecture (project tree lines 853, 876: "docs/examples/ only for the report gallery, not a site"). Existing `docs/*.md` (installation/development/publishing) are untouched.
- Query flag surface for the documented params lives in [src/steeproute/cli/_shared.py:286-519](src/steeproute/cli/_shared.py:286) — `--center`, `--radius`, `--seed`, `--n`, `--theta`, `--min-climb-slope`, `--difficulty-cap`, `--l-connector`, `--iter-budget`, etc. Defaults are fine for the gallery; document whatever you run, verbatim.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 8.3] (lines 966–980) — story + ACs; Epic 8 intro (931–933); NFR2 mapping (line 191).
- [Source: _bmad-output/planning-artifacts/prd.md] — gallery 3–5 regions success metric (line 76); portfolio credibility (line 55).
- [Source: _bmad-output/planning-artifacts/architecture.md:582-591] — self-contained HTML rationale; [781, 853, 876] — README gallery + `docs/examples/` in the project tree.
- [Source: tests/unit/test_output.py:341-367] — the self-containment regexes to reuse + the OpenTopoMap basemap note.
- [Source: _bmad-output/implementation-artifacts/3-10-html-json-output-rendering-with-vendored-assets.md] — AC #3: tile URLs are JS-constructed and out of grep scope.
- [Source: _bmad-output/implementation-artifacts/8-2-pin-2-3-grenoble-regression-fixtures-with-zero-tolerance-ci-gate.md] — the three regression cutouts (what the gallery must differ from), real-setup data-acquisition notes, native-TLS environment note.
- [Source: src/steeproute/cli/setup.py:18-21] — DEM is IGN WMS auto-download; no `--dem-path`.
- [Source: pyproject.toml] — `live` marker + `addopts = ["-m", "not live"]`; unmarked e2e tests run in default CI.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- **Headless profile-canvas truncation (root cause + fix).** Capturing the Chart.js elevation profile in headless Chrome intermittently produced a line covering only part of the route (e.g. Saint-Nizier drew to 0.67 km of a 1.3 km route) while the axis spanned the full extent. Verified via in-page dumps that the chart's *logical* state was always complete (full labels/data/scale) — only the painted pixels were partial: the load animation freezes mid-draw in headless, and even `update('none')` defers the repaint to a `requestAnimationFrame` that may not fire. Fix in `_capture_canvas`: disable animation, `update('none')`, then force a synchronous `chart.draw()` before `toDataURL`. `resize()` must NOT be called — it restarts an animated resize and reintroduces the truncation. The map (a tile `<div>` + SVG) clip-captures fine; only the canvas needed the export path.
- **`--force-device-scale-factor=2` lost the canvas line entirely** (scale-2 profile blank). Settled on `--scale 1.0`: map clip ~560 KB, profile ~35 KB, total gallery PNGs ~1.6 MB (scale 1.25 reached 4.0 MB — under budget but too tight a margin).
- **Saint-Nizier setup `DEMCoverageError` at radius 6.5.** A trail vertex landed exactly on the padded DEM's south edge. Resolved by preparing that region at radius 7.0 (more DEM padding); query stays at 6.5. Documented in `docs/examples/README.md`.
- **Peak-memory measurement.** External `Start-Process` polling measured the `steeproute.exe` launcher stub (~15 MB), not the worker; PowerShell also mangled `python -c` argument quoting. Switched to in-process `GetProcessMemoryInfo` (throwaway wrapper, not committed) for accurate per-query peak working set.

### Completion Notes List

- **Three full-size gallery regions (seed 42, `--n 3`, `--difficulty-cap T4`, `--iter-budget 200000`, `--stagnation-iters 10000`, `--elevation-deadband 1`, `--j-max 0`, `--theta 0.20` default, defaults otherwise):** `chamrousse` (Chamrousse, Belledonne — query 6 km, 3/3 routes, route-1 10.7 km/+1018 m/26%, ~7 s, ~261 MB), `saint-nizier` (Saint-Nizier-du-Moucherotte, Vercors — query 6.5 km, 3/3, route-1 7.5 km/+1042 m/24%, ~32 s, ~792 MB), `col-de-porte` (Col de Porte / Charmant Som, Chartreuse — query 6 km, 3/3, route-1 11.0 km/+1390 m/22%, ~7 s, ~294 MB). All converged on stagnation, zero validation failures. Distinct areas from the 8.2 regression cutouts (which are 2 km test fixtures, not full-size gallery regions).
- **Parameter tuning (per user review — the early runs were wrong).** First pass used `--iter-budget 400` + `--difficulty-cap T3` and produced weak/short top routes (Saint-Nizier route-1 was a 1.3 km micro-loop). The user's real-use values: `--iter-budget 200000 --stagnation-iters 10000` (GRASP needs a large budget to converge; stops on stagnation well before the cap), `--difficulty-cap T4` (T3 filters out steep alpine terrain), `--elevation-deadband 1` (strips sub-metre DEM noise from D+/D− — this changes the optimal route, not just cosmetics), and `--j-max 0` (returned routes fully disjoint; does not affect route 1). `--theta 0.20` is deliberately left limiting (the steepness floor is the whole point) — not lowered.
- **AC #1/#2:** `route-1..3.html` (+ JSON sidecars) committed per region under `docs/examples/`; README `## Gallery` has one row per region with map thumbnail, profile thumbnail, one-line description (area · radius · query time + route-1 stats), and a link to the full report. The gallery shows the top route (route 1) of each generation; this is stated in both READMEs.
- **README usability (per user review).** The earlier README had a gallery but no explanation of the tool. Added an intro with accurate **coverage** (OSM trails ≈ worldwide; IGN RGE ALTI elevation ⇒ France only — corrected the false "Grenoble Alps only" framing) and a `## Usage` section: install, the setup→query workflow with a worked Chamrousse example, and a key-parameter table with suggested values. (This pulls part of Story 8.4's planned `## Quickstart` forward, per Yann's call; 8.4 still owns Known Limitations and can fold/refine Usage into Quickstart.) Also dropped the copier placeholder and the "real output"-style filler phrasing.
- **AC #3:** `tests/e2e/test_gallery_self_contained.py` reuses the Story 3.10 resource-tag grep over every `docs/examples/**/*.html`, with a non-empty guard so it fails (not passes vacuously) on an empty gallery. Not `live`-marked → enforced by the default CI `pytest`. Passes.
- **AC #4:** `docs/examples/README.md` documents per-region centers, setup/query radii, exact regen commands, seed, full param set, timings, the `gallery_capture.py` screenshot command, and the Saint-Nizier radius note.
- **AC #5:** total committed PNGs = **1.97 MB** (< 5 MB).
- **AC #6:** peak working set recorded per query; max **~792 MB** (Saint-Nizier) — far below 12 GB, so **no NFR2 caveat is needed in Story 8.4's Known Limitations** (noted in the regen README).
- **Tooling:** `devtools/gallery_capture.py` is a committed dev/maintenance tool (the documented PNG regen path) — a stdlib-only CDP driver over a minimal WebSocket client, no new dependency (`requests` is already vendored via osmnx). Clean: ruff, ruff format, basedpyright 0/0/0.
- **Validation:** full suite **773 passed, 2 deselected** (772 at 8.2 close-out + 1 new gallery test); `grenoble_small` and the 8.2 goldens untouched.

### File List

- `README.md` (modified — intro with coverage, `## Usage` section, `## Gallery` section; copier placeholder removed)
- `tests/e2e/test_gallery_self_contained.py` (new — gallery HTML self-containment gate)
- `devtools/gallery_capture.py` (new — headless-Chrome CDP screenshot driver for the gallery PNGs)
- `docs/examples/README.md` (new — regeneration commands + measurements)
- `docs/examples/chamrousse/` (new — `route-1..3.{html,json}`, `route-1-map.png`, `route-1-profile.png`)
- `docs/examples/saint-nizier/` (new — `route-1..3.{html,json}`, `route-1-map.png`, `route-1-profile.png`)
- `docs/examples/col-de-porte/` (new — `route-1..3.{html,json}`, `route-1-map.png`, `route-1-profile.png`)
