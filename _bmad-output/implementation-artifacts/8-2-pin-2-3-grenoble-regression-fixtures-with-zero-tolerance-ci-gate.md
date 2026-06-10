# Story 8.2: Pin 2–3 Grenoble regression fixtures with zero-tolerance CI gate

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want 2–3 committed Grenoble-area regression fixtures — each a pre-prepared queryable cache + a golden hash tuple at a fixed seed + fixed params — running under the Story 8.1 harness in CI with zero tolerance,
so that representative real-world query regressions are caught immediately (deterministic GRASP ⇒ any drift is a behavior change worth noticing, Architecture §Cat 11c).

## Acceptance Criteria

1. **2–3 real Grenoble cutouts pinned in the registry.** Pick 2–3 distinct small Grenoble-area cutouts chosen for trail-density and terrain-character variety (e.g. a Chartreuse-style, a Belledonne-style, a Vercors-style area), prepare each via real `steeproute-setup`, and add each as a `Fixture` entry to `FIXTURES` in `src/steeproute/regression.py`. Each entry pins seed + the full behavior-affecting param set explicitly (same discipline as `grenoble_small`). No change to the comparison logic, hash core, or schema — 8.1 was built so 8.2 extends the registry only (8.1 AC #3).

2. **Committed queryable caches.** Each fixture's prepared cache lives at `tests/e2e/fixtures/<region_name>/cache/` as a full cache root (`steeproute/index.json` + `areas/<hash>/{graph.pkl,bounds.geojson,manifest.json}`) the query CLI runs against with a plain `--cache-dir`, offline, no patching. Each committed cache stays under ~10 MB (documented per fixture).

3. **Committed goldens.** Each fixture has a committed golden at `tests/e2e/goldens/<region_name>.json` in the 8.1 schema, generated via `uv run update-regression --fixture <region_name>`. The golden update commit states an explicit rationale (per the discipline documented in 8.1).

4. **Zero-tolerance enforcement in CI.** `tests/e2e/test_pinned_regressions.py` (already parametrized over `FIXTURES`) asserts an exact match on all five hash fields per route for every registered fixture. These tests are not `live`-marked, so they run in the default `uv run pytest` invocation that CI already executes — the gate is live for the new fixtures with no test-logic edit. `pytest.skip` / `xfail` on pinned-regression tests remains forbidden (Architecture §Cat 11c).

5. **Per-fixture README.** `tests/e2e/fixtures/<region_name>/README.md` documents: center / radius, DEM source, params + seed, last-updated date + commit reference, committed cache size, and the exact command to regenerate. Mirrors the `grenoble_small` README.

6. **CI timing budget honored.** Each fixture's regression test completes in under 30 s in CI; total pinned-regression CI time stays under 2 minutes. Radius and iteration budget are chosen to hold this budget while keeping runs deterministic (iteration-based termination, FR29).

## Tasks / Subtasks

- [x] Select 2–3 cutouts and prepare each cache via real `steeproute-setup` (AC: #1, #2)
  - [x] Run `steeproute-setup --center <lat,lon> --radius 2.0 --cache-dir tests/e2e/fixtures/<region>/cache` (network: Overpass + IGN WMS) for belledonne / vercors / chartreuse
  - [x] Confirm each committed cache is < ~10 MB (358 KB–1.2 MB after dropping the uncommitted `dem/`); confirm the 8.1 `.gitignore` negation tracks the new `cache/` dirs (`git add -n`)
- [x] Add a `Fixture` entry per region to `FIXTURES`, pinning seed + full param set (AC: #1)
- [x] Generate and commit each golden via `uv run update-regression --fixture <region>` (AC: #3)
- [x] Write each fixture's `README.md` (AC: #5)
- [x] Verify timing budget and zero-tolerance gate (AC: #4, #6)
  - [x] Time the full pinned-regression set; all 4 fixtures pass in 1.93 s (slowest 0.50 s — no tuning needed)
  - [x] Run the full suite; 772 passed, `grenoble_small` golden untouched

## Dev Notes

### What 8.1 already built (reuse, do not rebuild)

- The harness, canonical-hash core, golden schema, `update-regression` entry point, and the parametrized test all exist in [src/steeproute/regression.py](src/steeproute/regression.py) and [tests/e2e/test_pinned_regressions.py](tests/e2e/test_pinned_regressions.py). 8.2 is registry + data + docs only.
- Copy the `grenoble_small` `Fixture` entry as the template for each new region ([regression.py:92](src/steeproute/regression.py:92)): pin `--theta`, `--min-climb-slope`, `--difficulty-cap`, `--l-connector`, `--min-climb-ground-length`, `--elevation-smoothing`, `--elevation-deadband`, `--j-max`, `--n`, `--untagged-trails`, `--iter-budget`, `--stagnation-iters`, and `--time-budget` (pinned high so the wall-clock terminator never binds — termination stays iteration-based and deterministic). `seed=42`.
- The committed-cache `.gitignore` exception is already in place from 8.1 (`!tests/e2e/fixtures/**/cache/`). New region cache dirs are covered, but verify with `git add -n` before committing — the runtime `cache/` rule is broad.

### Data acquisition — this is the gating risk

- Unlike `grenoble_small` (seeded offline from committed source OSM/DEM fixtures), these cutouts come from **real** `steeproute-setup` runs: OSM via Overpass, DEM auto-downloaded from the IGN Géoplateforme WMS ([cli/setup.py:18-21](src/steeproute/cli/setup.py:18), [pipeline/dem_download.py] `resolve_dem`). There is no `--dem-path` flag. The dev environment reaches external TLS through vendored `truststore` (the same path that needed `uv sync --native-tls` in 8.1); confirm setup can complete a real download before committing to all three regions.
- The committed **cache** contains the prepared graph (`graph.pkl`), not the raw DEM raster — the DEM is sampled into edge attributes during setup. So the ~10 MB budget is essentially the pickled graph size; smaller radius / sparser area keeps it down.
- **Reproducibility approach (decide and document in the README):** the committed cache is itself the reproducible artifact; document the exact `steeproute-setup` command + seed so it can be rebuilt with network access. `graph.pkl` is a pickled networkx graph, so it is sensitive to networkx/Python upgrades — the regression test will surface any pickle incompatibility, and a rebuild then needs network (the `grenoble_small` offline-regenerate path is not available here unless source OSM/DEM are also committed, which would balloon the repo). See open questions.

### Determinism and the golden round-trip

- Pin `seed=42` and the full param set so the run is reproducible (FR29). `run_fixture` runs the real query CLI in-process against the committed cache and reads the real `route-*.json` sidecars — exit 0 and exit 1 (some route failed validation) are both fine; the golden pins whatever the run deterministically produces ([regression.py:187](src/steeproute/regression.py:187)).
- Generate the golden only after the fixture's params are final. If you re-tune radius or `--iter-budget` to fit the timing budget, the golden changes — re-run `update-regression` and commit with a rationale.
- `min_routes` defaults to 1; consider setting it to `--n` (or near it) per fixture so a run that silently collapses to near-zero routes can't be baked into a fresh golden ([regression.py:64-89](src/steeproute/regression.py:64)).

### Project Structure Notes

- **New:** `tests/e2e/fixtures/<region_name>/cache/**` and `tests/e2e/fixtures/<region_name>/README.md` (×2–3), `tests/e2e/goldens/<region_name>.json` (×2–3).
- **Modified:** `src/steeproute/regression.py` (`FIXTURES` += 2–3 entries).
- No CI workflow edit: [.github/workflows/ci.yml:51](.github/workflows/ci.yml:51) already runs `uv run pytest` and the regression tests are not `live`-marked ([pyproject.toml:225-231](pyproject.toml:225)), so they are enforced today. Coverage-floor tightening is Story 8.5, not here.
- The Architecture project tree's example filenames (`grenoble_10km.json`, `pelvoux_8km.json`) are illustrative — use real region names ([architecture.md:995](_bmad-output/planning-artifacts/architecture.md:995)).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 8.2] (lines 950–964) — story + ACs; [#Story 8.1] (935–948) for the 8.1/8.2 scope split; Epic 8 intro (931–933).
- [Source: _bmad-output/implementation-artifacts/8-1-regression-golden-test-harness-and-update-regression-workflow.md] — harness design, the `.gitignore` exception, the offline-vs-network regeneration tradeoff, and that 8.2 owns the cutouts + CI gate.
- [Source: _bmad-output/planning-artifacts/architecture.md] — §Cat 11c CI gates + no-skip discipline (957, 980–987); §Cat 11b pinned real-data fixtures (976); §Cat 11d hash scheme + golden schema (989–1016).
- [Source: src/steeproute/regression.py:64-117] — `Fixture` dataclass + `FIXTURES`; [187-233] `run_fixture`; [274-294] `update-regression` `main`.
- [Source: src/steeproute/cli/setup.py:18-21, 156-180] — DEM is IGN WMS auto-download; no `--dem-path`; cache root layout.
- [Source: tests/e2e/fixtures/grenoble_small/README.md] — per-fixture README template.
- [Source: pyproject.toml:225-231] — `live` marker + `addopts = ["-m", "not live"]`; regression tests are unmarked, so default CI runs them.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- **DEM cache dir excluded from the committed fixture.** Real `steeproute-setup` caches the downloaded DEM raster at `cache/steeproute/dem/<hash>.tif` under the cache root. The query side reads elevation from the prepared `graph.pkl` and never touches `dem/`, so it was removed from all three fixtures (matching the clean `grenoble_small` cache, which has no `dem/`). This dropped committed sizes from ~1.1–1.9 MB to 358 KB–1.2 MB and keeps the queryable cache minimal. Verified the regression query still passes after removal.

### Completion Notes List

- **Three real cutouts prepared, all queryable offline.** `belledonne` (45.186753, 5.961482), `vercors` (45.148755, 5.639232), `chartreuse` (45.374716, 5.772793), each from a real `steeproute-setup --radius 2.0` run (Overpass OSM + IGN RGE ALTI DEM, layer `ign-rgealti-highres`). Prepared 2026-06-10 on commit `46332cb`. Setup wall-clock: 7.5 s / 12 s / 48 s (Chartreuse is the densest).
- **Query radius 1.5 km (seed 2.0 km).** `check_coverage` uses *strict* `shapely.contains`, so the regression query runs at 1.5 km to be strictly contained in the 2.0 km prepared bbox — mirrors the proven `grenoble_small` seed/query split. Smaller query graphs also keep the CI budget trivially safe.
- **Registry-only code change.** Added three `Fixture` entries to `FIXTURES` in `regression.py`; factored the shared pinned param set into `_PINNED_PARAMS` (identical to the 8.1 `grenoble_small` set). No change to the comparison logic, hash core, or schema (8.1 AC #3). `seed=42`. Each fixture produced the full N=5 routes.
- **Zero-tolerance gate live in CI with no workflow edit.** The regression tests are not `live`-marked, so CI's existing `uv run pytest` enforces them. `test_pinned_regressions.py::...[belledonne|vercors|chartreuse]` each assert exact match on all five hash fields. Per-fixture durations 0.21–0.50 s; full pinned-regression set 1.93 s — far under the 30 s/fixture and 2 min budgets (AC #4, #6).
- **Per-fixture READMEs** document center/radius, DEM source, params+seed, committed size, prepared date+commit, and the (network-required) regeneration command. Regeneration needs network (no committed offline source for these real cutouts — unlike `grenoble_small`); flagged in each README.
- **Validation:** `ruff format`/`ruff check` clean on `regression.py`; `basedpyright` 0/0/0; full suite **772 passed / 2 deselected** (769 at 8.1 close-out + 3 new parametrized cases), `grenoble_small` golden untouched.

### File List

- `src/steeproute/regression.py` (modified — `_PINNED_PARAMS` + three `Fixture` entries: belledonne, vercors, chartreuse)
- `tests/e2e/fixtures/belledonne/cache/**` (new — queryable cache root)
- `tests/e2e/fixtures/belledonne/README.md` (new)
- `tests/e2e/fixtures/vercors/cache/**` (new — queryable cache root)
- `tests/e2e/fixtures/vercors/README.md` (new)
- `tests/e2e/fixtures/chartreuse/cache/**` (new — queryable cache root)
- `tests/e2e/fixtures/chartreuse/README.md` (new)
- `tests/e2e/goldens/belledonne.json` (new — committed golden, 5 routes, seed 42)
- `tests/e2e/goldens/vercors.json` (new — committed golden, 5 routes, seed 42)
- `tests/e2e/goldens/chartreuse.json` (new — committed golden, 5 routes, seed 42)
