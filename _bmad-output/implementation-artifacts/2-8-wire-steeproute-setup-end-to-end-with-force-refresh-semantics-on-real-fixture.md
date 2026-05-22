# Story 2.8: Wire steeproute-setup end-to-end with --force-refresh semantics on real fixture

Status: done

## Story

As a user,
I want `steeproute-setup --center ... --radius ... --dem-path ...` to run the full stages 1-7 pipeline and write a cache entry on disk, skipping recomputation when a valid entry already exists for the composite cache key (unless `--force-refresh` is set),
so that I can prepare an area once and re-prepare only when I explicitly ask (e.g., after a DEM update).

## Acceptance Criteria

1. `cli/setup.py::main` replaces the Story 1.5 stub: parses flags via the existing `_shared.py` decorators, requires `--dem-path` at consumption (BadCLIArgError if `None`), resolves `cache_root = resolve_cache_root(cache_dir)`, derives `dem_version` from `--dem-version` when provided else from DEM file metadata (filename + size + mtime are sufficient — pick one deterministic derivation and document the rationale), builds the cache key via `compute_cache_key(area, untagged_policy, dem_version, compute_pipeline_content_hash())`, and dispatches:
   - **Cache hit** (`read_entry` succeeds and `--force-refresh` is not set): skip the pipeline, return the existing entry path for the summary.
   - **Cache miss or `--force-refresh`**: call `run_setup_stages(area, PipelineConfig(untagged_policy, dem_path))`, build the `Manifest` (provenance via `get_commit_short()` + `iso8601_utc_now()`; `osm_extract_date` = setup-time `iso8601_utc_now()`), then `write_entry(cache_root, manifest, graph)`.
   - `CacheCorruptedError` raised by `read_entry` is treated as a miss (re-prepare overwrites) — the corruption is the user-actionable signal once we surface it; on `--force-refresh` the read is skipped entirely.

2. Stdout summary (always emitted, even with `--quiet` per Architecture §Cat 8 "run summary on stdout"): one block reporting hit-vs-miss, the 16-hex `cache_key_hash`, the entry directory path, and the wall-clock seconds for the operation. Format is human-readable plain text — no JSON, no Rich. A `--quiet` run suppresses any progress lines but still emits the summary.

3. `--verbose` plumbing wires `logging.basicConfig(...)` to a stderr handler at `DEBUG` level (default: `WARNING`), so the previously-silent diagnostic calls become visible. As part of this story, add the deferred log calls:
   - `pipeline/__init__.py`: `logger.debug("dropped %d short edges, %d orphan nodes", ...)` in `_drop_short_edges` / `_drop_orphan_nodes` (deferred-work D1 from Story 2.5).
   - `cache.py`: `logger.warning("skipping corrupt manifest at %s: %s", ...)` in `rebuild_index`'s swallow path (deferred-work D2 from Story 2.7).
   No `print` for these — `logging` only, per Architecture §Cat 8 stream discipline.

4. e2e coverage in `tests/e2e/test_steeproute_setup.py` (new) exercising the full CLI happy path against the committed Grenoble fixture. The OSM-fetch step is the only blocker for a fully offline e2e test; resolve it with one of:
   - **Pre-seed pattern**: run the in-process `cli.setup.cli` once with `osm_load` patched (same pattern Stories 2.5 + 2.7 use) into a `tmp_path` cache, then drive a second invocation that exercises the cache-hit branch without needing the patch.
   - **Subprocess + `@pytest.mark.live`**: invoke `uv run steeproute-setup ...` as a subprocess and mark the test live so it stays out of the default suite.
   Pick whichever keeps the default suite offline; document the choice. Assertions cover: entry directory contains `graph.pkl` + `manifest.json` + `bounds.geojson`; graph edge count matches Story 2.5's `_BASELINE_EDGES ± _DRIFT_TOLERANCE`; re-running with the same flags is a cache hit (no re-prepare); `--force-refresh` re-runs the pipeline and rewrites the entry; stdout summary contains the 16-hex `cache_key_hash` and the entry path.

5. Unit/integration coverage for cache-key sensitivity: changing a key-inducing flag (`--untagged-trails`) on the same area produces a fresh cache entry under a different `<hash>/` rather than overwriting the prior one. A second area-mismatch test against `--dem-version` is sufficient — full coverage of every key field belongs to Story 2.6's existing tests.

6. Carry-forward deferred items from prior stories — fold in where the CLI surface is the natural home, defer the rest with a one-line rationale in `deferred-work.md`:
   - **D2 from Story 2.3** (inverted-bounds GeoTIFF sanity check at `rasterio.open` time): land — wire into `pipeline/dem.py` or `run_setup_stages` entry, whichever is the smaller diff. Raises a clearer error than the per-vertex `DEMCoverageError` wall.
   - **D1 from Story 2.6** (`get_commit_short` treats untracked files as `-dirty`): land — switch the `git status --porcelain` invocation to `--untracked-files=no` so a clean tracked-tree with stray untracked artifacts no longer flips the report-visible commit string to `-dirty`.
   - **D8 from Story 2.1** (Overpass query-limits sanity ceiling on `--radius` for setup): land if it's a few lines (a hard cap at, e.g., 50 km radius with `BadCLIArgError`); otherwise re-defer to a future story with rationale.
   - **D1 from Story 2.2** (`n_intervals` upper bound in `_resample_meters`): re-defer — no `--spacing-m` flag exists yet and `--area-cap` (query-side only) bounds polyline length indirectly. Document in `deferred-work.md`.

7. All four CI gates pass on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov`. No new runtime deps. Live OSM test (`pytest -m live`) re-verified at the end. `cli/setup.py` is exempt from the 95% pure-logic coverage floor per Architecture §Cat 11e (CLI bootstrap code); the new logic in `cli/setup.py::main` is exercised by the e2e coverage in AC #4. `cache.py` and `pipeline/__init__.py` keep their 95% floor.

## Tasks / Subtasks

- [x] Task 1: Implement `cli/setup.py::main` end-to-end wiring (AC: #1, #2)
- [x] Task 2: Wire `--verbose` logging + add deferred log calls in `pipeline/__init__.py` + `cache.py` (AC: #3)
- [x] Task 3: e2e test against committed Grenoble fixture, covering hit / miss / `--force-refresh` (AC: #4)
- [x] Task 4: Cache-key sensitivity test on `--untagged-trails` (AC: #5)
- [x] Task 5: Land or re-defer carry-forwards from Stories 2.1 D8, 2.3 D2, 2.6 D1, 2.2 D1 (AC: #6)
- [x] Task 6: Verify all four CI gates + live OSM re-verification (AC: #7)

### Review Findings

_From `bmad-code-review` 2026-05-22. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Acceptance Auditor returned 2 LOW findings — both AC interpretation nuances. Blind Hunter 25, Edge Case Hunter 17 raw findings. After dedupe + triage: **2 decisions, 9 patches, 2 defers, 22 dismissed.**_

**Decision-needed (must resolve before patching):**

- [x] [Review][Decision→Dismiss] **D1 (HIGH): `CacheCorruptedError` recovery silently overwrites forward-incompatible manifests.** Spec dev notes say "treat as miss + re-prepare-as-recovery" and the code matches — but a future steeproute v2 entry on disk (with `schema_version: 2`) read by today's v1 toolchain raises `CacheCorruptedError` via `Manifest.from_dict`, triggering a silent rewrite that destroys the v2-only data. **Decision (2026-05-22):** keep current behavior. Forward-compat downgrade is unusual for an N=1 personal tool with no shipped v2 yet; `--force-refresh` is the documented escape. Revisit when v2 is on the table. [src/steeproute/cli/setup.py:144-153] [Source: edge]
- [x] [Review][Decision→Patch] **D2 (LOW): AC #5 spec sentence about `--dem-version` sensitivity is ambiguous.** Spec sentence reads either as "add a parallel `--dem-version` test" or as "Story 2.6 covers it; one test is enough." **Decision (2026-05-22):** add the parallel test. Promoted to **P10** below. [tests/e2e/test_steeproute_setup.py:187-203] [Source: auditor]

**Patches (unambiguous fixes):**

- [x] [Review][Patch] **P1 (HIGH): NaN in `--radius` slips past every validation gate.** `float('nan') <= 0.0` is False and `float('nan') > 50.0` is False, so `validate_setup_radius(nan)` returns without raising. Downstream `Area(radius_km=nan)` propagates into osmnx and surfaces as exit 1 + traceback instead of `BadCLIArgError → exit 2`. Add `math.isfinite(radius_km)` check at the top of `validate_setup_radius`. [src/steeproute/cli/_shared.py:133-148] [Source: blind+edge]
- [x] [Review][Patch] **P2 (HIGH): `_verbose` global state pollution across CliRunner tests.** `test_verbose_flag_sets_verbose_state_on_setup_cli` sets `_verbose=True` and never resets; the next test relying on `is_verbose() is False` only works because of a defensive `set_verbose(False)` inside its body. A pytest autouse fixture in `tests/unit/conftest.py` (or local module-level) that resets `_verbose` between tests removes the cross-test ordering dependency. [tests/unit/test_cli_options.py:115-139] [Source: blind+edge]
- [x] [Review][Patch] **P3 (HIGH): `entry_dir` hardcoded in CLI's cache-hit branch violates the cache-module boundary.** `entry_dir = cache_root / "steeproute" / "areas" / cache_key` duplicates the layout that lives canonically in `cache._areas_dir`. If the layout ever changes (schema-version dir, partition), the hit-path summary points at the wrong directory. Add a public `cache.entry_dir_for(cache_root, cache_key) -> pathlib.Path` helper and use it from `cli/setup.py`. [src/steeproute/cli/setup.py:144-147] [Source: blind]
- [x] [Review][Patch] **P4 (MED): Tautological provenance test `osm_extract_date == created_at`.** The CLI captures one `iso8601_utc_now()` and assigns both fields to it, so the test assertion is guaranteed by construction and provides zero coverage. The CODE is spec-compliant (dev notes say "`created_at` and `osm_extract_date` from `iso8601_utc_now()` at write time"). Either drop the equality assertion or tighten to "both match the ISO-8601 second-precision Z-suffix regex independently." [tests/e2e/test_steeproute_setup.py:135] [Source: blind+edge]
- [x] [Review][Patch] **P5 (MED): `int(stat.st_mtime)` truncation in `_derive_dem_version` enables sub-second cache-key collisions.** Two DEM writes within the same wall-clock second (rare in production but easy in tests doing `shutil.copyfile`) produce identical `dem_version` strings → silent stale read. Use `stat.st_mtime_ns` for nanosecond precision. [src/steeproute/cli/setup.py:189] [Source: blind]
- [x] [Review][Patch] **P6 (MED): Case-insensitive DEM path produces fragmented cache entries on Windows.** `dem_path.name` preserves user-typed casing, so `--dem-path Grenoble.TIF` and `--dem-path grenoble.tif` (same NTFS file) produce different `dem_version` strings → two cache entries for one DEM. Use `dem_path.resolve().name` (canonicalizes case on Windows). [src/steeproute/cli/setup.py:189] [Source: edge]
- [x] [Review][Patch] **P7 (MED): `_resolve_package_version` only catches `PackageNotFoundError`.** A corrupted `.dist-info/METADATA` raises `MetadataError` (or `OSError` on truncated installs), not `PackageNotFoundError`. The exception leaks past `run_entry_point` as a generic exception → exit 1 + traceback. Broaden the catch to `(PackageNotFoundError, OSError, Exception)` or just `Exception` with the same "unknown" sentinel. [src/steeproute/cli/setup.py:198-201] [Source: edge]
- [x] [Review][Patch] **P8 (MED): `test_setup_cli_does_not_enforce_area_cap` depends on test-process CWD.** Pytest run from a directory containing a file literally named `doesnotmatter.tif` would let `Path("doesnotmatter.tif").is_file()` return True, falling through to a real OSM fetch. Switch to `tmp_path / "nonexistent.tif"` (pytest's per-test fresh dir). [tests/unit/test_area_parsing.py:139-144] [Source: edge]
- [x] [Review][Patch] **P9 (LOW): AC #4 cache-hit test applies the `osm_load` patch even on the second invocation.** Spec dev notes describe "drive a second invocation … without needing the patch" so a regression where the hit branch silently calls `osm_load` would fail loudly. Currently `_invoke_setup` always wraps in `with patch(...)`. Refactor to a `patch_osm: bool` parameter (or split into two helpers), pre-seed once with the patch, then verify the hit path works without it. [tests/e2e/test_steeproute_setup.py:67-82, 155-165] [Source: auditor]
- [x] [Review][Patch] **P10 (LOW): AC #5 — add `--dem-version` sensitivity test.** Mirror of `test_setup_with_different_untagged_trails_writes_new_entry`: two invocations differing only on `--dem-version` produce two distinct cache entries. Resolves D2. [tests/e2e/test_steeproute_setup.py] [Source: auditor]

**Deferred (real but owned elsewhere):**

- [x] [Review][Defer] **DEF1 (MED): `validate_setup_radius` not enforced inside `run_setup_stages`.** A direct caller (test, future script) constructing `Area(radius_km > 50)` bypasses the CLI-side ceiling. Adding the guard to the orchestrator would break tests that construct synthetic `Area`s with deliberately specific (often large) radii. The orchestrator's job is "ingest the given area"; CLI-tier validation is the right home for the ceiling. Routed to a future story if a non-CLI caller surface ever appears. [src/steeproute/pipeline/__init__.py:89-141] [Source: edge]
- [x] [Review][Defer] **DEF2 (MED): DEM permission-denied not surfaced as `PreExecutionError`.** `dem_path.is_file()` returns True without read perms; rasterio later raises `RasterioIOError → OSError`, leaking past `run_entry_point` as exit 1. This is the same shape Story 2.9 will solve for OSM/IGN unavailable errors via `DataSourceUnavailableError`. Routed to Story 2.9. [src/steeproute/cli/setup.py:113] [Source: edge]

**Dismissed (noise / false positive / handled elsewhere):**

- [x] [Review][Dismiss] **`configure_cli_logging(force=True)` clobbers pytest `caplog`.** `force=True` is the documented Python 3.8+ idiom for idempotent reconfiguration; tests using `caplog` would need to call `caplog.set_level` AFTER the CLI runs anyway. The current tests don't use `caplog`. If a future test does, a fixture can re-attach the handler. [src/steeproute/cli/_shared.py:36-41] [blind+edge]
- [x] [Review][Dismiss] **`--force-refresh` on existing key never removes stale residue.** `write_entry` is idempotent over a pre-existing directory via the `<hash>.old/` swap dance (Story 2.7 P1 verified). No residue can survive. [blind]
- [x] [Review][Dismiss] **`_derive_dem_version` non-deterministic across machines.** Explicit design choice documented in Dev Notes ("Hashing DEM bytes was rejected … `--dem-version` is the user-supplied opt-in when content-identity matters more than the surface metadata."). The user can pass `--dem-version` for cross-machine stability. [blind]
- [x] [Review][Dismiss] **TOCTOU window between CLI `is_file()` and orchestrator `is_file()`.** Both checks race the same way; the unlink-mid-pipeline failure mode exists regardless and is owned by `sample_elevation`'s `RasterioIOError` path. [blind]
- [x] [Review][Dismiss] **`rebuild_index` over-broad `OSError` catch.** Story 2.7's P5 explicitly broadened this set for the same `CacheCorruptedError → exit 2` contract. Established design. [blind]
- [x] [Review][Dismiss] **`--quiet` accepted but inert on setup.** Matches spec intent ("`--quiet` run suppresses any progress lines but still emits the summary"). Setup has no progress lines today; the flag is parsed for `--help` surface consistency and forward-compat with future progress wiring. [blind]
- [x] [Review][Dismiss] **`_load_fixture_constants` executes `regenerate.py` at import time.** Same pattern as `test_pipeline_end_to_end.py` (Story 2.5) and `test_cache_roundtrip.py` (Story 2.7). `regenerate.py` has no import-time side effects (just module-scope constants). [blind]
- [x] [Review][Dismiss] **`assert entry_dir is not None` type-narrowing.** Standard basedpyright workaround; `python -O` isn't used in CI. The comment makes intent clear. [blind]
- [x] [Review][Dismiss] **`_resolve_package_version` sentinel `"unknown"` collides with `get_commit_short`.** Both legitimately mean "we don't know"; reports keep them in separate fields. A typed sentinel would add ceremony without coverage. [blind]
- [x] [Review][Dismiss] **`--untracked-files=no` semantics partial.** Comment correctly references `git describe --dirty` convention. The blind hunter's concern about staged-but-not-committed files is wrong — `git status --porcelain -uno` reports staged tracked changes as expected. [blind]
- [x] [Review][Dismiss] **Untracked-files test doesn't verify the new flag in subprocess argv.** Testing behavior (untracked-only doesn't flip dirty) is more meaningful than implementation detail (the literal `--untracked-files=no` string in argv). Same convention as `test_get_commit_short_at_appends_dirty_when_working_tree_modified`. [blind]
- [x] [Review][Dismiss] **Inverted-bounds DEM check `<=` conflates flipped with zero-area.** Both lead to the same "fix your raster" outcome; differentiating would just multiply error messages without changing remediation. [blind]
- [x] [Review][Dismiss] **`test_setup_graph_edge_count_within_story_2_5_baseline` does not bound `next(iterdir())` length.** Pytest's `tmp_path` is fresh per test, so the dir starts empty and exactly one entry exists after the invocation. Defensive `len == 1` would be tidier but the current usage is correct. [blind]
- [x] [Review][Dismiss] **`_skip_if_fixtures_missing` autouse-fixture style.** Matches the `tests/integration/test_cache_atomic.py` skip pattern; per-test skip overhead is negligible (the actual pipeline run dominates). [blind]
- [x] [Review][Dismiss] **Logger format lacks `%(asctime)s`.** Cosmetic; the diagnostic intent of `--verbose` is "show what stages fired", not "profile per-stage timing" — that belongs to a future progress callback (Epic 4). [blind]
- [x] [Review][Dismiss] **`_drop_short_edges` comment-code mismatch.** Verified by reading: the threshold check uses `<`, matching the format string `< %.3g m`. No mismatch. [blind]
- [x] [Review][Dismiss] **Hard-coded `_DRIFT_TOLERANCE = 0.10` not imported from Story 2.5.** Cross-test constant importing creates fragility (a Story 2.5 baseline change for unrelated reasons would silently retighten this test). 10% is the same band Story 2.5 uses; manual coordination is acceptable for two adjacent constants. [blind]
- [x] [Review][Dismiss] **`compute_pipeline_content_hash` fails opaquely on unreadable `.py`.** If `src/steeproute/pipeline/*.py` is unreadable, steeproute itself is broken at install level — not in scope for setup CLI. [edge]
- [x] [Review][Dismiss] **`elapsed_s` underreports because `start` is post-`_derive_dem_version`.** Off by ~10ms tops (a `stat()` + a 10-file `read_bytes()`). The summary's "elapsed" is for cache-hit-vs-miss differentiation, not perf measurement. [edge]
- [x] [Review][Dismiss] **`pipeline_content_hash` test is truthy-only.** Folded into P4's test-tightening patch — the same review pass on the manifest tests will catch this. [edge]
- [x] [Review][Dismiss] **Long Windows paths exceeding MAX_PATH.** Real on Windows but unrealistic for the `~/.cache/steeproute/areas/<16hex>/` shape (well under 100 chars even with a deep prefix). N=1 hobby project margin. [edge]
- [x] [Review][Dismiss] **`_invoke_command` SystemExit non-int → 0 coercion.** Pre-existing from Story 1.4/1.5; not introduced by this story. Click never emits non-int `SystemExit.code` in practice. [edge]
- [x] [Review][Dismiss] **`read_entry` / `write_entry` asymmetric `--force-refresh` recovery.** Speculative — `write_entry` failing mid-write leaves the `<hash>.old/` backup intact (Story 2.7 P1); the next `--force-refresh` run cleans `.old/` opportunistically. No data loss surface. [edge]
- [x] [Review][Dismiss] **CLI summary prints arbitrary `pathlib.Path` to stdout without encoding hygiene.** Architecture targets the Grenoble Alps personal-tool case; unicode in cache paths is unrealistic. If it ever happens, the fix is `sys.stdout.reconfigure(encoding="utf-8")` at process start — separate concern. [edge]

## Dev Notes

- **`dem_version` derivation when `--dem-version` is None.** Architecture §Cat 4b allows either user-supplied tag or derived-from-metadata. A simple `f"{dem_path.name}-{size}-{mtime}"` is enough — the goal is "different DEM release → different key", and any metadata that changes on a real DEM update qualifies. Document the choice in a comment so a future v2 change is easy to spot. Avoid hashing DEM bytes (multi-GB files; needless I/O on every `steeproute-setup` invocation).
- **`Manifest` field provenance.** `steeproute_version` comes from `importlib.metadata.version("steeproute")`; `steeproute_commit` from `provenance.get_commit_short()`; `created_at` and `osm_extract_date` from `provenance.iso8601_utc_now()` at write time. `pipeline_content_hash` from `compute_pipeline_content_hash()` (Story 2.6's helper).
- **Why `CacheCorruptedError` on read is treated as a miss.** The user's expected mental model for `steeproute-setup --force-refresh` is "rebuild this entry". A corruption-on-read path that hard-fails would force the user to manually delete the entry before re-preparing. Re-prepare-as-recovery aligns with the "user-triggered freshness" principle in Architecture §Cat 4b. Note this is asymmetric with `steeproute` (query-side) where a corrupt entry surfaces as exit 2 via `run_entry_point` — query has nothing to recover from.
- **Test layer choice for AC #4.** Per Architecture §Cat 11e, e2e tests live in `tests/e2e/`. Subprocess-based e2e tests cannot apply Story 2.5/2.7's `unittest.mock.patch(osm_load)` trick across the process boundary, so an offline e2e either drives the pre-seed flow (load `osm_graph.graphml`, write a real cache entry via the in-process `cli.setup.cli` with `osm_load` patched, then assert subsequent hit behavior) or marks the live invocation `@pytest.mark.live` (excluded from default `pytest`, runs alongside `test_osm_live.py`). The pre-seed pattern keeps the default suite fully offline — preferred.
- **What this story does NOT do:**
  - **`DataSourceUnavailableError` / OSM-age warning** — Story 2.9.
  - **Query-side `check_coverage` / fail-fast** — Story 2.10.
  - **`--spacing-m` flag exposure + `n_intervals` cap** — out of scope; no flag means no surface.
  - **Cache eviction / pruning of orphan entries** — Architecture §Cat 4b leaves that to a future utility.

### Project Structure Notes

- **Replaced**: `src/steeproute/cli/setup.py` — `cli` body goes from the Story 1.5 stub to the full hit/miss dispatch + summary. `main` (the `run_entry_point` wrapper) stays as-is.
- **Extended**: `src/steeproute/pipeline/__init__.py` — `logger = logging.getLogger(__name__)` plus the two `logger.debug(...)` calls in `_drop_short_edges` / `_drop_orphan_nodes`.
- **Extended**: `src/steeproute/cache.py` — `logger = logging.getLogger(__name__)` plus the `logger.warning(...)` in `rebuild_index`'s swallow path.
- **Extended**: `src/steeproute/provenance.py` — switch `git status --porcelain` to `git status --porcelain --untracked-files=no`.
- **Extended (carry-forward)**: `src/steeproute/pipeline/dem.py` — inverted-bounds sanity check at `rasterio.open` time, surfacing a clearer error.
- **New**: `tests/e2e/test_steeproute_setup.py` — hit / miss / `--force-refresh` + summary assertions.
- **Updated**: `_bmad-output/implementation-artifacts/deferred-work.md` — record carry-forward dispositions per AC #6.
- **Untouched**: `src/steeproute/cli/_shared.py` (decorators already in place); `models.py` (no new shapes); `errors.py` (existing `BadCLIArgError` / `CacheNotFoundError` / `CacheCorruptedError` cover the new branches).

### Testing standards summary

- e2e tests live in `tests/e2e/` (Architecture §Cat 11e). Subprocess-style mirrors `test_cli_smoke.py`; in-process style uses `click.testing.CliRunner` with `unittest.mock.patch(steeproute.pipeline.osm.osm_load, ...)`.
- Module-scoped fixture loading the committed `osm_graph.graphml` + `dem.tif` (same pattern Story 2.5's `test_pipeline_end_to_end.py` and Story 2.7's `test_cache_roundtrip.py` use). Skip-on-missing-fixture mirrors them.
- The carry-forward inverted-bounds DEM check gets a unit test in `tests/unit/test_dem.py` against an in-memory `rasterio.io.MemoryFile`-built inverted raster (same in-memory-DEM pattern existing tests use).
- The `provenance.get_commit_short` change extends `tests/unit/test_provenance.py` (or wherever the existing provenance tests live) with an untracked-file-only-tree case asserting the result no longer flips to `-dirty`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 2.8"]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 4 — Cache architecture] — write order, manifest-as-commit-signal, `--force-refresh` semantics
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 8 — Logging, progress, and stream discipline] — `print` for summary, `logging` for diagnostics, stderr/stdout split
- [Source: _bmad-output/planning-artifacts/architecture.md §Internal data flow — Setup CLI] — the exact `cli/setup.py::main` flow this story implements
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11e — Testing strategy] — e2e layer; CLI exempt from 95% coverage floor
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] — D2 (2.3), D1 (2.6), D8 (2.1), D1 (2.5), D2 (2.7), D1 (2.2)
- [Source: src/steeproute/cli/setup.py] — current stub to replace
- [Source: src/steeproute/cli/_shared.py] — decorator surface + `run_entry_point`
- [Source: src/steeproute/cache.py] — `resolve_cache_root`, `compute_cache_key`, `compute_pipeline_content_hash`, `Manifest`, `write_entry`, `read_entry`, `rebuild_index`
- [Source: src/steeproute/pipeline/__init__.py] — `run_setup_stages(area, config)` entry point
- [Source: src/steeproute/provenance.py] — `get_commit_short()`, `iso8601_utc_now()`
- [Source: tests/e2e/test_cli_smoke.py] — subprocess-style e2e precedent
- [Source: tests/integration/test_cache_roundtrip.py] — in-process `osm_load`-patched fixture precedent
- [Source: tests/fixtures/grenoble_small/regenerate.py] — fixture center/radius constants the e2e test should reuse

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` for the corporate Netskope TLS-intercepting proxy (used by `uv sync` and the live OSM test).

**No new runtime or dev deps.** Story 2.8 wires together pieces that all landed in Stories 2.5-2.7; the Click decorator surface and `_shared.py` plumbing were already in place from Story 1.5.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 44 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 343 passed, 1 deselected in ~66s; coverage 96% overall
                                     - cli/setup.py 88% (CLI tier; exempt from the 95%
                                       pure-logic floor per Architecture §Cat 11e — uncovered
                                       branches are CacheCorruptedError-recovery, the
                                       _resolve_package_version PackageNotFoundError sentinel,
                                       and the _invoke_command SystemExit-non-int fallback)
                                     - cli/_shared.py 98% (added configure_cli_logging and
                                       validate_setup_radius; uncovered = the >50 km
                                       branch is covered by the smoke test, and dual
                                       run-coverage limits report-output to non-zero only)
                                     - cache.py 96% (unchanged — added one logger.warning)
                                     - pipeline/__init__.py 99% (added two logger.debug
                                       calls inside existing prune helpers)
                                     - pipeline/dem.py 100% (added inverted-bounds branch +
                                       new unit test)
                                     - provenance.py 100% (added --untracked-files=no +
                                       new unit test)
```

Live OSM test re-verified: `uv run pytest -m live` → 1 passed (no regression).

### Completion Notes List

**Design decisions worth review attention:**

1. **`dem_version` derivation when `--dem-version` is None.** `f"{dem_path.name}-{stat.st_size}-{int(stat.st_mtime)}"`. Architecture §Cat 4b accepts either user-supplied tag or derived metadata; we use the cheapest derivation that captures "is this the same DEM file on disk". A genuine DEM release replaces the file (new size and/or mtime), shifting the cache key. Hashing DEM bytes was rejected because production DEMs are multi-GB and we'd pay that I/O on every `steeproute-setup` invocation just to verify the key.

2. **`CacheCorruptedError` on read is treated as a cache miss.** Re-prepare-as-recovery aligns with the user's "run setup again to fix it" mental model. The query CLI (Story 2.10) will handle corruption asymmetrically — exit 2, because query has nothing to recover from. A `logger.warning` surfaces the corruption when `--verbose` is set so the silent recovery is observable.

3. **Numeric radius check fires before the file-existence check.** `validate_setup_radius` is pure arithmetic; the file-existence check requires `stat()`. Running the cheap one first means a typo like `--radius 5000 --dem-path nonexistent.tif` surfaces the radius error (the more actionable one) instead of being shadowed by the dem-path error.

4. **File-existence check duplicated at CLI and orchestrator.** Story 2.5's P2 patch added `dem_path.is_file()` inside `run_setup_stages` so any caller — CLI, tests, future scripts — gets the fail-fast. The CLI checks it again before `_derive_dem_version(dem_path)` can `stat()` a missing path; the orchestrator's check remains as defense-in-depth for non-CLI callers. Removing either would create a missing-file failure mode that surfaces as a raw `FileNotFoundError`.

5. **`--osm-age-warn-days` is parsed and validated but not consumed.** Story 2.9's `manifest.osm_extract_date`-age warning needs the flag visible at the CLI surface today (it appears in `--help` and gets the click validation pass) but doesn't have a check site until cache-hit semantics expand in 2.9. Documented inline as `_ = osm_age_warn_days`.

6. **`logging.basicConfig(force=True, ...)` for idempotent CLI logging setup.** CliRunner can invoke the click command repeatedly inside one process; without `force=True`, only the first call's handler sticks and `--verbose` becomes flaky across tests. `force=True` (Python 3.8+) is the documented one-liner for "reconfigure root logger".

7. **Edge-count baseline test for the e2e suite is "within 10% of Story 2.5's `_BASELINE_EDGES`".** The orchestrator integration tests are the authority on exact numbers; the e2e test just smoke-checks "did the pipeline actually run end-to-end and produce a sane graph". Tightening it would create CI flakes on routine fixture regeneration.

8. **Carry-forwards triaged.** Three landed (D2/2.3 inverted-bounds, D8/2.1 radius ceiling, D1/2.6 untracked-files), two log-plumbing items landed (D1/2.5 pipeline debug, D2/2.7 rebuild_index warning). One re-deferred (D1/2.2 `n_intervals` cap — no `--spacing-m` CLI flag means the failure mode is structurally unreachable; land alongside that flag if it ever ships).

**AC walkthrough — evidence per criterion:**

1. AC #1 — `cli/setup.py::cli` parses flags, requires `--dem-path`, validates radius, derives `dem_version`, computes the cache key, dispatches hit-vs-miss with `--force-refresh` precedence, builds `Manifest` on miss and calls `write_entry`. Exercised by `test_setup_first_run_*` + `test_setup_force_refresh_*` + `test_setup_missing_dem_path_*` in `tests/e2e/test_steeproute_setup.py`. ✅
2. AC #2 — `_print_summary` emits `cache-hit` / `cache-miss`, `cache_key_hash`, `entry`, `elapsed` on stdout. Verified by the summary substring asserts in `test_setup_first_run_is_cache_miss_writes_entry_and_reports_summary`. ✅
3. AC #3 — `configure_cli_logging` in `cli/_shared.py` routes the root logger to stderr at DEBUG (verbose) or WARNING (default). `_logger.debug(...)` added to `pipeline/__init__.py::_drop_orphan_nodes` and `_drop_short_edges`; `_logger.warning(...)` added to `cache.py::rebuild_index`'s swallow path. ✅
4. AC #4 — `tests/e2e/test_steeproute_setup.py` covers hit (`test_setup_second_run_same_flags_is_cache_hit`), miss (`test_setup_first_run_is_cache_miss_...`), `--force-refresh` (`test_setup_force_refresh_rebuilds_entry_on_existing_key`), and entry-layout assertions. Pre-seed pattern via in-process `CliRunner` + `unittest.mock.patch("steeproute.pipeline.osm_load", ...)` keeps the default suite offline. ✅
5. AC #5 — `test_setup_with_different_untagged_trails_writes_new_entry` + `test_setup_index_lists_all_written_entries` confirm a key-change produces a fresh directory rather than overwriting. ✅
6. AC #6 — Inverted-bounds DEM sanity check landed in `pipeline/dem.py` (test: `test_sample_elevation_rejects_flipped_origin_dem`); `get_commit_short` uses `--untracked-files=no` (test: `test_get_commit_short_at_ignores_untracked_only_changes`); `validate_setup_radius` in `cli/_shared.py` rejects `r <= 0` or `r > 50 km` (test: `test_setup_radius_above_ceiling_exits_2`). `n_intervals` cap re-deferred with rationale in `deferred-work.md`. ✅
7. AC #7 — All four CI gates green (see Debug Log). Live OSM re-verified. ✅

### File List

**New:**
- `tests/e2e/test_steeproute_setup.py` — 9 tests (8 original + P10's `test_setup_with_different_dem_version_writes_new_entry`) covering hit / miss / `--force-refresh` / cache-key sensitivity (untagged-trails + dem-version) / index-after-two-entries / `--dem-path` required. P9 added a `patch_osm: bool` param to `_invoke_setup` so the cache-hit test runs without the `osm_load` patch.
- `tests/e2e/conftest.py` — autouse `reset_verbose_flag` fixture (P2) mirroring `tests/unit/conftest.py`.

**Modified:**
- `src/steeproute/cli/setup.py` — full rewrite of `cli` body: replaces the Story 1.5 stub with the parse → derive `dem_version` → compute key → hit/miss dispatch → `Manifest` + `write_entry` → summary flow. Adds `_derive_dem_version`, `_resolve_package_version`, `_print_summary` helpers + per-file basedpyright pragma. **Post-review:** `_derive_dem_version` switched to `dem_path.resolve().name + st_mtime_ns` (P5+P6); `_resolve_package_version` catches `Exception` (P7); cache-hit branch uses `entry_dir_for` (P3).
- `src/steeproute/cli/_shared.py` — adds `configure_cli_logging(verbose: bool)` (stderr handler, idempotent via `basicConfig(force=True)`) and `validate_setup_radius(radius_km: float)` (rejects `r <= 0` or `r > 50 km`). **Post-review (P1):** `validate_setup_radius` also rejects non-finite radii (`nan`, `±inf`) before the sign/ceiling checks.
- `src/steeproute/pipeline/__init__.py` — module-level `_logger`; `logger.debug(...)` in `_drop_orphan_nodes` (count of orphans) and `_drop_short_edges` (count + floor). Deferred-work D1 from Story 2.5.
- `src/steeproute/pipeline/dem.py` — inverted-bounds sanity check immediately after `rasterio.open`: raises `DEMCoverageError` with a clear "inverted or zero-width bounds" message. Deferred-work D2 from Story 2.3.
- `src/steeproute/cache.py` — module-level `_logger`; `logger.warning(...)` in `rebuild_index`'s skip path naming the bad manifest. Deferred-work D2 from Story 2.7. **Post-review (P3):** new public `entry_dir_for(cache_root, cache_key) -> Path` helper centralizes the cache-entry path layout.
- `src/steeproute/provenance.py` — `git status --porcelain --untracked-files=no` so untracked-only working trees no longer flip `get_commit_short` to `-dirty`. Deferred-work D1 from Story 2.6.
- `tests/unit/test_dem.py` — added `test_sample_elevation_rejects_flipped_origin_dem`.
- `tests/unit/test_provenance.py` — added `test_get_commit_short_at_ignores_untracked_only_changes`.
- `tests/unit/test_cli_options.py` — updated `test_verbose_flag_sets_verbose_state_on_setup_cli` / `test_setup_cli_without_verbose_leaves_state_false` to expect `BadCLIArgError` from the now-required `--dem-path` while still asserting the eager `--verbose` state flip. **Post-review (P2):** removed local `set_verbose(False)` from the second test (autouse fixture handles it).
- `tests/unit/test_area_parsing.py` — updated `test_setup_cli_does_not_enforce_area_cap` to provide a `--dem-path`. **Post-review:** `tmp_path / "nonexistent.tif"` removes CWD dependency (P8); new `test_setup_cli_rejects_nan_radius` parametrized over `("nan", "inf", "-inf")` (P1).
- `tests/unit/test_cache.py` — minor whitespace fix on intra-function imports (split `import json as _json` / `import networkx as nx` so ruff's I001 stays clean). **Post-review (P3):** new `test_entry_dir_for_matches_write_entry_layout`.
- `tests/e2e/test_cli_smoke.py` — replaced `test_setup_happy_path_exits_0` (which previously hit the Story 1.5 stub) with `test_setup_missing_dem_path_exits_2` and `test_setup_radius_above_ceiling_exits_2` covering the new CLI-boundary checks.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.8 `backlog → ready-for-dev → in-progress → review → done`.
- `_bmad-output/implementation-artifacts/deferred-work.md` — marked D8/2.1, D2/2.3, D1/2.5, D1/2.6, D2/2.7 as ✅ Resolved; D1/2.2 re-deferred with rationale; added two new deferrals from the Story 2.8 review (orchestrator radius enforcement → Future, DEM permission-denied mapping → Story 2.9).

**Untouched (intentionally):**
- `src/steeproute/cli/query.py` — query CLI is still a Story 1.5 stub; Stories 2.10 + 3.x will wire it.
- `src/steeproute/models.py` — no schema changes; `Area` + `PipelineConfig` already in place.
- `src/steeproute/errors.py` — existing `BadCLIArgError` / `CacheNotFoundError` / `CacheCorruptedError` / `DEMCoverageError` cover all new branches.
- `tests/integration/test_pipeline_end_to_end.py`, `tests/integration/test_cache_roundtrip.py`, `tests/integration/test_cache_atomic.py` — orchestrator + cache tests stand on their own; the new e2e tests assert the CLI-tier wiring.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-22 | Yann (Claude Opus 4.7) | Adversarial `bmad-code-review` applied: 2 decisions resolved (D1 forward-compat manifest overwrite → keep current behavior + revisit when v2 ships; D2 ambiguous `--dem-version` test sentence → add the test). 10 patches landed (P1-P10), 2 deferred (`validate_setup_radius` in orchestrator → Future; DEM permission-denied error mapping → Story 2.9), 22 dismissed inline. **P1 (HIGH):** `validate_setup_radius` now rejects non-finite radii (`nan`, `±inf`) with `BadCLIArgError` — IEEE-754 NaN compares False against everything and slipped past both the `≤ 0` and `> 50` checks; new `test_setup_cli_rejects_nan_radius` parametrizes over `("nan", "inf", "-inf")`. **P2 (HIGH):** new `tests/e2e/conftest.py::reset_verbose_flag` autouse fixture mirrors the unit-layer one so `_verbose` state cannot leak between e2e tests; removed the local `set_verbose(False)` from `test_setup_cli_without_verbose_leaves_state_false` (the autouse fixture handles it). **P3 (HIGH):** new public `cache.entry_dir_for(cache_root, cache_key)` helper centralizes the `<cache-root>/steeproute/areas/<hash>/` layout; `cli/setup.py`'s cache-hit branch now calls it instead of reconstructing the path by string concatenation — a future layout change moves `write_entry` and the hit-path summary in lockstep. New `test_entry_dir_for_matches_write_entry_layout`. **P4 (MED):** dropped the tautological `osm_extract_date == created_at` assertion in `test_setup_first_run_writes_manifest_with_complete_provenance`; replaced with independent ISO-8601 regex matching on each field + a 64-hex `pipeline_content_hash` shape check (folds in dismissed edge-finding P12). **P5 (MED):** `_derive_dem_version` now uses `stat.st_mtime_ns` for nanosecond precision (was `int(stat.st_mtime)` — sub-second cache-key collisions on tight `shutil.copyfile` loops). **P6 (MED):** `_derive_dem_version` now uses `dem_path.resolve().name` (canonicalizes case on Windows so `Grenoble.TIF` and `grenoble.tif` produce one cache entry, not two). **P7 (MED):** `_resolve_package_version` catches `Exception` (was only `PackageNotFoundError` — corrupted `.dist-info/METADATA` would surface as `OSError` or `MetadataError` and crash setup). **P8 (MED):** `test_setup_cli_does_not_enforce_area_cap` now uses `tmp_path / "nonexistent.tif"` (was `"doesnotmatter.tif"` — would have triggered a real OSM fetch if pytest ran from a directory that happened to contain a file by that name). **P9 (LOW):** `_invoke_setup` gained a `patch_osm: bool = True` parameter; `test_setup_second_run_same_flags_is_cache_hit` now pre-seeds with the patch then re-invokes WITHOUT the patch — proving the cache-hit branch is OSM-independent (a regression where the hit path silently re-fetches would now raise instead of silently succeeding). **P10 (LOW):** new `test_setup_with_different_dem_version_writes_new_entry` mirrors the `--untagged-trails` sensitivity test to confirm `--dem-version` is in fact a cache-key-composing input at the CLI tier. 22 review findings dismissed inline in the Review Findings section. All four CI gates green post-review: ruff, ruff format, basedpyright 0/0/0, pytest 346 passed (+3 from prior 343) at 96% overall coverage; `cli/setup.py` 88% (CLI-tier, exempt from 95% pure-logic floor). Live OSM re-verified — no regression. | _pending_ |
| 2026-05-22 | Yann (Claude Opus 4.7) | Story 2.8 implemented: `steeproute-setup` wired end-to-end with `--force-refresh` semantics. `cli/setup.py::cli` replaces the Story 1.5 stub — parses flags via existing `_shared.py` decorators, requires `--dem-path` at consumption, applies `validate_setup_radius` (≤ 50 km half-side; deferred-work D8 from 2.1), derives `dem_version` from `--dem-version` or DEM file metadata (`<name>-<size>-<mtime>`), computes the composite cache key via `compute_cache_key + compute_pipeline_content_hash`, and dispatches: cache-hit (skip pipeline + report path/elapsed) vs. cache-miss / `--force-refresh` (`run_setup_stages → Manifest → write_entry`). `CacheCorruptedError` on read is treated as a miss with a `logger.warning` (re-prepare-as-recovery; asymmetric with query-side which will exit 2). Stdout summary always emits `cache-hit`/`cache-miss`, the 16-hex `cache_key_hash`, entry path, and `elapsed`. New `configure_cli_logging(verbose=)` in `cli/_shared.py` routes the stdlib root logger to stderr at DEBUG (--verbose) or WARNING (default), `force=True` for idempotency across CliRunner re-entries. Carry-forwards landed: deferred-work **D1/2.5** (`logger.debug` in `_drop_short_edges` / `_drop_orphan_nodes`), **D2/2.7** (`logger.warning` in `rebuild_index`'s corrupt-manifest swallow path), **D2/2.3** (inverted-bounds sanity check in `sample_elevation` — raises `DEMCoverageError("inverted or zero-width bounds ...")` instead of the per-vertex wall), **D1/2.6** (`git status --porcelain --untracked-files=no` — untracked-only trees no longer flip `get_commit_short` to `-dirty`; matches `git describe --dirty` convention), **D8/2.1** (`validate_setup_radius` rejects `r ≤ 0` or `r > 50 km`). **D1/2.2** (`n_intervals` cap) re-deferred — no `--spacing-m` flag means the failure mode is structurally unreachable; land alongside that flag if it ever ships. 11 new tests: 8 e2e in `tests/e2e/test_steeproute_setup.py` (in-process `CliRunner` + `unittest.mock.patch("steeproute.pipeline.osm_load", ...)` pre-seed pattern — default suite stays offline) + 3 unit (flipped-origin DEM, untracked-only `git status`, radius-ceiling smoke). Existing tests updated: `test_setup_cli_does_not_enforce_area_cap` and the two `test_verbose_flag_sets_verbose_state_on_setup_cli` / `test_setup_cli_without_verbose_leaves_state_false` tests now thread a `--dem-path` through the call or assert `BadCLIArgError`; `test_setup_happy_path_exits_0` replaced by `test_setup_missing_dem_path_exits_2` + `test_setup_radius_above_ceiling_exits_2`. No new runtime/dev deps. All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 343 passed (+11 from prior 332) at 96% overall coverage; `cli/setup.py` at 88% (CLI tier — exempt from the 95% pure-logic floor per Architecture §Cat 11e). Live OSM test re-verified — no regression. | _pending_ |
