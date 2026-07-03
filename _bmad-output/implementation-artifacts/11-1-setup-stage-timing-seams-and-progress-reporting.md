# Story 11.1: Setup-stage timing seams and progress reporting (FR33)

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want `steeproute-setup` to tell me which stage it's running, how long each completed stage took, and progress within long stages,
so that a multi-minute setup run is visibly working rather than apparently hung.

## Acceptance Criteria

1. **Reusable stage-timing seam.** One seam (context manager or decorator, home `progress.py`) that emits a stage-start line and a stage-complete line with elapsed time via an injectable render callback, uses an injectable monotonic clock (the `throttle` precedent), and records each stage's elapsed seconds into a machine-readable per-stage collection the caller can read afterward — Story 11.2's attribution reuses it rather than re-instrumenting (T1). With no callback installed the seam is a timing-only no-op (no output, no behavior change).

2. **Every setup stage wrapped.** A real cache-miss `steeproute-setup` run prints a per-stage timeline on stdout covering: OSM download, trail filter, polyline smoothing, resampling, DEM resolve (download/mosaic), elevation sampling, and cache write — stage times accounting for the run's wall-clock (the T3 deliverable). Cache-hit runs are unaffected (no pipeline, no stage lines).

3. **Within-stage progress for long stages.** The DEM tile-fetch loop emits `tile i/N` progress lines; the blocking OSM/Overpass download is preceded by an honest start line (single request, typically takes minutes — no fake progress possible).

4. **Stream discipline (Architecture Cat 8).** All progress via `print()` to stdout, never `logging`; errors/warnings stay on stderr. `--quiet` (already parsed, currently unused in setup) suppresses every progress line while the end-of-run summary and stderr are preserved.

5. **osmnx HTTP cache fixed and verified (T2).** osmnx 2.1.0 defaults to `use_cache=True` but `cache_folder="./cache"` — CWD-relative, so today's cache lands wherever the user happens to run from. Point `osmnx.settings.cache_folder` at a persistent directory under the resolved steeproute cache root (honoring `--cache-dir`), keep `use_cache` on, and assert the outcome with a test (or record it in close-out).

6. **Behavior-preserving.** Existing e2e setup tests stay green, extended to assert stage lines present by default and absent under `--quiet`; full default suite green; regression goldens untouched (no rebake).

## Tasks / Subtasks

- [x] Task 1: Stage-timing seam in `progress.py` (AC: #1). Context manager (e.g. `stage_timer(name, *, on_line=None, clock=time.monotonic, timings=None)`) emitting start/complete lines through the callback and recording elapsed into a caller-owned dict. Unit tests with injected fake clock (mirror `tests/unit/test_progress_helpers.py`).
- [x] Task 2: Thread an optional progress callback through the setup orchestrator (AC: #2). `build_graph_geometry` / `attach_elevation` in `pipeline/__init__.py` gain a None-able callback param (the `GraspSolver.progress_callback` pattern); `cli/setup.py` wraps its own steps (DEM resolve, cache write) and owns the timings dict.
- [x] Task 3: DEM `tile i/N` within-stage progress (AC: #3). Thread the callback `resolve_dem` → `_fetch_mosaic`; total = `len(y_ranges) × len(x_ranges)`; only fires on actual download (DEM cache hit skips it).
- [x] Task 4: Honest OSM start line before `osmnx.graph_from_point` (AC: #3).
- [x] Task 5: Wire `--quiet` in `cli/setup.py` (AC: #4). Install the renderer or `None`; remove the `_ = quiet` placeholder; summary always printed.
- [x] Task 6: Fix + verify the osmnx cache (AC: #5). Set `osmnx.settings.cache_folder` under the resolved cache root (e.g. `<cache_root>/osmnx/`) at CLI level before the pipeline runs; test asserts the setting.
- [x] Task 7: Extend e2e tests + full validation (AC: #6). Stage lines present by default / absent under `--quiet` in `tests/e2e/test_steeproute_setup.py`; all four CI gates green; goldens untouched.

## Dev Notes

### Recommendation (read first)

Purely additive observability — no algorithmic change, no new flags, no cache-content change. The one genuine design decision is **where the seams live**, constrained by the architecture's pipeline boundary ("pipeline stages are pure functions; outside code calls orchestrator functions"):

- **Don't** put prints inside stage functions (`osm.py`, `smoothing.py`, `dem.py`). **Do** inject a None-able callback into the orchestrator functions and wrap stage calls there — the exact pattern `GraspSolver(progress_callback=...)` already established (Epic 7): solver/pipeline stays interval- and output-agnostic, the CLI owns rendering and `--quiet`, tests inject a collecting callback.
- The timings collector should be a plain dict (`dict[str, float]`, stage name → elapsed seconds) created in `cli/setup.py` and passed down — that satisfies the epic's "per-stage dict on the run result" machine-readability without inventing a result object.
- Keep the seam **pure-logic testable**: injectable clock like `throttle(…, clock=time.monotonic)` so unit tests assert emitted lines and recorded timings deterministically.

### Setup stage map (current code, not the architecture's nominal 1–7)

Architecture §Cat 3b says setup = stages 1–7, but stages 6–7 moved query-side in Story 6.3, and commit `6deac05` (2026-07-02) reordered the setup path to size the DEM from graph geometry. What `cli/setup.py` actually runs on a cache miss ([setup.py:160-188](src/steeproute/cli/setup.py)):

1. `build_graph_geometry(area, untagged_trails)` — stages 1–4: `osm_load` → `filter_trails` → `smooth_polylines` → `resample_edges` ([pipeline/__init__.py:149-174](src/steeproute/pipeline/__init__.py))
2. `resolve_dem(graph_dem_bounds(graph), …)` — DEM download/mosaic ([dem_download.py:115-160](src/steeproute/pipeline/dem_download.py))
3. `attach_elevation(graph, dem_path)` — stage 5 ([pipeline/__init__.py:177-186](src/steeproute/pipeline/__init__.py))
4. `write_entry(...)` — atomic cache write

Wrap all of these. Stage granularity inside `build_graph_geometry` (4 separate stages vs. one "graph geometry" block) is the dev's call — per-stage is better for 11.2's attribution, and the stage functions are already separate calls in the orchestrator, so per-stage costs nothing. The orchestrator's guard helpers (`_assert_non_empty`, `_drop_short_edges`, …) are microseconds — fold them into their preceding stage, don't give them their own lines.

### Within-stage progress specifics

- **DEM tiles:** nested loop at [dem_download.py:287-289](src/steeproute/pipeline/dem_download.py) (`for y0,y1 in _tile_ranges(height): for x0,x1 in _tile_ranges(width)`), `_MAX_TILE_PX = 2048` at ~5 m/px — a 10 km radius area is ~4 tiles, larger areas up to ~25. Compute N up front (`len(list(...)) × len(list(...))`), emit `tile i/N` before each `_wms_get_bil`. Thread the same callback; None → silent.
- **OSM/Overpass:** one blocking `osmnx.graph_from_point` call ([osm.py:82](src/steeproute/pipeline/osm.py)) — no incremental progress is possible, hence the epic's "honest start line" (e.g. `stage 1/…: downloading OSM trail network (single Overpass request, typically takes minutes)`). The stage-complete line then reports the real elapsed.

### osmnx cache (T2) — verified finding, fix required

Verified against the installed osmnx 2.1.0: `settings.use_cache` is `True` but `settings.cache_folder` is `"./cache"` — relative to CWD. So Overpass responses ARE cached today, but into a stray `cache/` folder in whatever directory the user runs from: not persistent in any meaningful sense, and it litters. Fix: in `cli/setup.py`, after `resolve_cache_root(cache_dir)` ([cache.py:318-331](src/steeproute/cache.py) — `platformdirs.user_cache_dir("steeproute")`, `--cache-dir` override), set `osmnx.settings.cache_folder = <cache_root>/osmnx` before any pipeline call. Setting it CLI-side keeps `pipeline/osm.py` pure and honors `--cache-dir` for free (precedent: `_ensure_sac_scale_in_useful_tags` already mutates osmnx settings, [osm.py:193-200](src/steeproute/pipeline/osm.py), but that one is pipeline-internal because it's correctness-critical to the fetch itself). Test: unit-level assert on the setting after the CLI wiring runs, no network needed.

### `--quiet` and streams

`quiet_option` is already stacked on the setup command and explicitly unused (`_ = quiet  # Setup has no progress lines to suppress`, [setup.py:227](src/steeproute/cli/setup.py)) — this story deletes that placeholder. Contract (Cat 8, same as query side): `--quiet` suppresses stdout progress only; the existing end-of-run summary (`steeproute-setup: cache-miss / cache_key_hash / entry / elapsed`) still prints; errors/warnings stay on `logging`→stderr. Progress lines are `print()` only — the "Progress via `logging.info`" anti-pattern is explicitly forbidden by the architecture.

### Behavior-preservation notes

- Editing `pipeline/__init__.py` / `osm.py` / `dem_download.py` bumps `pipeline_content_hash` — but that only re-keys **fresh** setups; committed regression-fixture caches load by geographic containment (Story 10.1/10.2 precedent), and goldens run query-side. No rebake, no fixture regeneration.
- e2e setup tests assert with `in result.output`, so additive stage lines don't break them; the session-scoped `seeded_cache` fixture in `tests/e2e/conftest.py` runs setup in-process (CliRunner) and tolerates extra stdout.
- Callback-absent paths must stay zero-overhead no-ops (the Story 7.1 discipline: progress is a pure side-effect; here there's no RNG to protect, but the principle keeps `--quiet` timing-identical).

### Out of scope (don't drift)

- Profiling, flamegraphs, the bottleneck-list document — Story 11.2 (it *reuses* this story's timings dict).
- `tests/benchmarks/` / pytest-benchmark — Story 11.3.
- Any speedup work (DEM tile concurrency, windowed raster reads) — Phase 3, unscoped until 11.2's list exists.
- Query-side (`steeproute`) progress — already done (Epic 7); don't touch `GraspSolver` or the query renderer.

### Project Structure Notes

- **Modified:** `src/steeproute/progress.py` (seam — joins `ProgressEvent`/`throttle`/`estimate_remaining`), `src/steeproute/pipeline/__init__.py` (callback params on `build_graph_geometry`/`attach_elevation`), `src/steeproute/pipeline/dem_download.py` (callback through `resolve_dem`→`_fetch_mosaic`, tile i/N), `src/steeproute/pipeline/osm.py` (honest start line via callback, if emitted at stage level it may need no change at all), `src/steeproute/cli/setup.py` (renderer, `--quiet` wiring, timings dict, osmnx cache_folder).
- **Tests:** extend `tests/unit/test_progress_helpers.py` (seam), `tests/e2e/test_steeproute_setup.py` (stage lines default/quiet); new unit test for the osmnx cache setting; possibly `tests/unit/test_dem_download.py` (tile-progress callback).
- No new dependencies (py-spy/pytest-benchmark arrive in 11.2/11.3).

### Testing standards summary

- Unit seams: injected fake clock, exact assertions, no wall-clock flake ([test_progress_helpers.py](tests/unit/test_progress_helpers.py) precedent).
- e2e via CliRunner, offline (OSM/DEM fixture patches at [test_steeproute_setup.py:109-113](tests/e2e/test_steeproute_setup.py)).
- `progress.py` held to 100% in 7.1 — keep it there; pipeline modules are on the 95% pure-logic floor.
- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0 (story 10.2 fixed it to genuinely clean — don't regress it), `pytest --cov`. Run `tests/unit` and `tests/integration` in separate pytest invocations (`from conftest import` collision).
- Build-flake recovery: stale editable build after a commit/pyproject edit → `uv sync --native-tls` once, then `uv run --no-sync`.

### References

- [Source: epics.md §Story 11.1 + §FR33 + §T1–T3](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; FR33 lives in epics.md (post-v1 increment), not the PRD
- [Source: research/technical-steeproute-performance-tuning-research-2026-07-02.md §Phase 0 + §Setup-Pipeline Architecture](_bmad-output/planning-artifacts/research/technical-steeproute-performance-tuning-research-2026-07-02.md) — seam-shared-with-profiling rationale; osmnx cache verification requirement
- [Source: architecture.md §Category 8](_bmad-output/planning-artifacts/architecture.md) — stream discipline, `print()` not `logging`, `--quiet` semantics, injected-callback pattern
- [Source: src/steeproute/cli/setup.py:160-232](src/steeproute/cli/setup.py) — orchestration sequence, `_print_summary`, unused `quiet`
- [Source: src/steeproute/pipeline/__init__.py:100-189](src/steeproute/pipeline/__init__.py) — `run_setup_stages`/`build_graph_geometry`/`attach_elevation` (post-6deac05 shape)
- [Source: src/steeproute/pipeline/dem_download.py:263-310](src/steeproute/pipeline/dem_download.py) — `_tile_ranges`/`_fetch_mosaic` tile loop
- [Source: src/steeproute/progress.py](src/steeproute/progress.py) — `throttle`/injectable-clock precedent the seam should mirror
- [Source: src/steeproute/cache.py:318-331](src/steeproute/cache.py) — `resolve_cache_root` (where the osmnx cache dir hangs)
- [Source: _bmad-output/implementation-artifacts/7-1-progressevent-throttled-callback-cli-renderer-with-quiet-verbose.md](_bmad-output/implementation-artifacts/7-1-progressevent-throttled-callback-cli-renderer-with-quiet-verbose.md) — injected-callback + quiet wiring precedent (query side)
- [Source: _bmad-output/implementation-artifacts/10-2-direction-aware-descent-slope-cap.md](_bmad-output/implementation-artifacts/10-2-direction-aware-descent-slope-cap.md) — previous story: gate status (basedpyright now genuinely 0/0/0), cache-loads-by-geography fact, build-flake recovery

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`, `uv run --no-sync` throughout (no build flake encountered this session). No new dependencies.

**Test runs:**

```
pytest (default markers, full)                      → 842 passed, 6 deselected
pytest tests/e2e/test_pinned_regressions.py -m slow → 4 passed (realistic-tier goldens match WITHOUT rebake)
ruff check src tests                                → All checks passed!
ruff format --check src tests                       → 101 files already formatted
basedpyright (whole project)                        → 0 errors, 0 warnings, 0 notes
coverage (changed modules)                          → progress.py 100%, dem_download.py 97%,
                                                      pipeline/__init__.py 92%*, cache.py 91%*
                                                      (*subset run; no NEW line uncovered — misses are
                                                       pre-existing paths exercised by integration tests)
```

**Pre-existing gate fix:** `ruff format --check` was already red at HEAD — `tests/unit/test_climbs.py` carried format drift from Story 10.2's close-out. Reformatted (1 file) since the gate must be green; no logic change.

### Completion Notes List

**Stage-timing seam (Task 1).** `progress.StageProgress` — a small dataclass holding `on_line` (render sink), `clock` (injectable, `time.monotonic` default), and `timings` (`dict[str, float]`, stage name → elapsed seconds, insertion-ordered). `with progress.stage(name, note=...)` emits `stage: {name} ...` / `stage: {name}: {elapsed:.2f} s` and records the timing; `progress.line(text)` renders within-stage progress indented. With `on_line=None` the seam times but emits nothing (the `--quiet` install). A failing stage body emits no done line and records no timing — the exception propagates to `run_entry_point`'s stderr path. 6 unit tests with the `_FakeClock` pattern; `progress.py` at 100% coverage.

**Orchestrator threading (Task 2).** `build_graph_geometry` / `attach_elevation` / `run_setup_stages` gain keyword-only `progress: StageProgress | None = None` (the `GraspSolver.progress_callback` pattern — stage functions stay pure and seam-free; only the orchestrator observes). Stages map to `osm-download`, `trail-filter`, `polyline-smoothing`, `resampling`, `elevation-sampling`; the contract guards are folded into their preceding stage (microseconds — separate lines would be noise). `cli/setup.py` owns the seam, wrapping its own two steps as `dem-resolve` and `cache-write` — a real cache-miss run prints a 7-stage timeline (T3), and `progress.timings` is the machine-readable breakdown Story 11.2 reads.

**DEM tile progress (Task 3).** `resolve_dem` → `_fetch_mosaic` thread the seam; the tile loop materializes its row/col ranges, computes `total = rows × cols`, and emits `  tile i/N` before each blocking WMS request. DEM cache hit short-circuits before the loop → no phantom progress (tested).

**Honest OSM line (Task 4).** The `osm-download` stage start line carries `note="one Overpass request; typically takes minutes"` — no fake within-stage progress is possible for a single blocking request; the done line reports the real elapsed.

**`--quiet` wiring (Task 5).** `cli/setup.py` installs `StageProgress(on_line=None if quiet else print)`; the `_ = quiet` placeholder is gone and `_print_summary` dropped its unused `quiet` param (summary always prints, §Cat 8). Stage lines are `print()`-only — never `logging`.

**osmnx cache fix (Task 6, T2).** Verified against installed osmnx 2.1.0: `use_cache=True` but `cache_folder="./cache"` (CWD-relative — Overpass responses were cached into stray `cache/` folders wherever setup ran). `cli/setup.py::_configure_osmnx_cache` now points `osmnx.settings.cache_folder` at `cache.osmnx_cache_dir_for(cache_root)` (`<cache-root>/steeproute/osmnx/`, honoring `--cache-dir`) and re-asserts `use_cache = True` right after `resolve_cache_root`. E2e test asserts both settings post-run.

**Tests + validation (Task 7).** 4 new e2e tests: per-stage timeline present on cache-miss (all 7 done-lines matched by regex, honest OSM note asserted), `--quiet` suppresses every progress line while summary survives, cache-hit prints no stage lines, osmnx cache settings persistent. 2 new dem_download unit tests (tile lines on multi-tile fetch, none on cache hit). All existing tests untouched and green; both golden tiers match without rebake — behavior-preserving as specced (editing pipeline files bumps `pipeline_content_hash` for fresh setups only; committed fixture caches load by geography).

### File List

**Modified (src):**
- `src/steeproute/progress.py` — new `StageProgress` seam; module docstring now covers both consumers.
- `src/steeproute/pipeline/__init__.py` — keyword-only `progress` param on `run_setup_stages` / `build_graph_geometry` / `attach_elevation`; stage wraps.
- `src/steeproute/pipeline/dem_download.py` — `progress` param on `resolve_dem` / `_fetch_mosaic`; `tile i/N` emission.
- `src/steeproute/cache.py` — `_OSMNX_SUBDIR` + `osmnx_cache_dir_for` layout helper.
- `src/steeproute/cli/setup.py` — seam creation + `--quiet` install, `dem-resolve`/`cache-write` stage wraps, `_configure_osmnx_cache`, summary cleanup, docstring sync.

**Modified (tests):**
- `tests/unit/test_progress_helpers.py` — 6 `StageProgress` tests (fake clock).
- `tests/unit/test_dem_download.py` — tile-progress + cache-hit-silent tests.
- `tests/e2e/test_steeproute_setup.py` — 4 new tests (timeline, quiet, cache-hit, osmnx cache).
- `tests/unit/test_climbs.py` — pre-existing format drift fixed (`ruff format`, no logic change).

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-03 | Yann (Claude Fable 5) | Story 11.1 implemented (FR33 / T1–T3): `StageProgress` stage-timing seam in `progress.py` (injectable clock, machine-readable `timings` dict for Story 11.2); every setup stage wrapped (`osm-download` → `trail-filter` → `polyline-smoothing` → `resampling` → `dem-resolve` → `elevation-sampling` → `cache-write`) with start/elapsed lines on stdout; `tile i/N` within the DEM fetch; honest "one Overpass request; typically takes minutes" annotation on the blocking OSM download; `--quiet` now wired in setup (suppresses progress, keeps summary, §Cat 8). T2: osmnx 2.1.0's CWD-relative `./cache` repointed to a persistent `<cache-root>/steeproute/osmnx/` with `use_cache` asserted on. Behavior-preserving: 842 passed, slow-tier goldens 4/4 without rebake, all four gates green (incl. fixing pre-existing `test_climbs.py` format drift). |
