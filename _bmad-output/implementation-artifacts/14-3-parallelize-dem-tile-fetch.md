# Story 14.3: Parallelize DEM tile fetch (setup)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want DEM tiles fetched concurrently instead of one `urlopen` at a time,
so that large-area DEM download wall-clock collapses without changing the assembled mosaic.

## Acceptance Criteria

1. **Given** `_fetch_mosaic` (`pipeline/dem_download.py`) fetches tiles strictly sequentially — a nested
   `for (y0,y1) in row_ranges: for (x0,x1) in col_ranges:` loop, one blocking `urlopen` per tile
   (~134 s @ r20: 16 tiles × ~8.4 s/tile; ~14 min @ r50 for ~100 tiles, estimate),
   **when** fetching moves to a `concurrent.futures.ThreadPoolExecutor` (module-constant `max_workers`,
   **start at 4**), each task fetching one tile and returning `(y0, y1, x0, x1, bytes)`, and the **parent
   thread** validating the byte count + reshaping + writing into the pre-allocated mosaic array,
   **then** because every tile writes to a disjoint `arr[y0:y1, x0:x1]` slice and each tile's sub-bbox is
   computed identically to today, the assembled mosaic is **byte-identical** to the sequential path
   regardless of completion order (proven by an offline test that forces out-of-order completion).
2. `tile i/N` within-stage progress still emits (FR33 / §Cat 8 stream discipline preserved), now as a
   **monotonic completion counter** printed from the parent thread as each future completes — the printed
   sequence stays `tile 1/N … tile N/N`. Every network/payload failure still maps to
   `DataSourceUnavailableError("DEM source unreachable.", …)` with the same message shapes (the worker's
   exception propagates unchanged through `future.result()`).
3. IGN Géoplateforme behavior under concurrency is validated at **r20 first** (real `steeproute-setup
   --radius 20 --force-refresh`, or an equivalent multi-tile live fetch); if 429s / connection errors
   appear, `_MAX_FETCH_WORKERS` is backed off and the observed result is **recorded in the close-out**.
   Architecture Cat 3 / Cat 8 gains a light note (setup's first fetch concurrency beyond the cache-write
   atomics; progress semantics unchanged).

## Tasks / Subtasks

> **Scope revision (recorded 2026-07-07, requested by Yann).** Task 5 and the "Scope guardrails" section
> below originally decided `--dem-fetch-workers` was **not** a CLI flag — `_MAX_FETCH_WORKERS` was kept as a
> module constant on the reasoning that the etiquette-safe concurrency ceiling is a property of the IGN
> Géoplateforme source, not the user's machine. That reasoning still holds, but Yann pointed out the
> conclusion rests on exactly **one** live validation run (Task 4, r20 tile-count/concurrency profile, no
> 429s) — real-world tolerance could differ (better or worse) outside that one observation, and there was no
> way to react without a code change. Overriding the original decision: added `--dem-fetch-workers` (Task 6
> below), default unchanged at 4, so the value is adjustable without a release. This does not change AC #1's
> byte-identity guarantee (proven independent of worker count) or AC #3's live-validation finding (4 is safe
> at the profile tested) — it only removes the "must be a code change" constraint from the original design.

- [x] Task 1: Prove the byte-identity guardrail *before* changing the fetch (AC: #1)
  - [x] Confirmed the existing offline suite (`tests/unit/test_dem_download.py`) green as-is (12 passed)
        against the sequential `_fetch_mosaic` — the ground-truth reference.
  - [x] Made the tests' fake `urlopen` **thread-safe and order-independent** *first*: `_make_fake_urlopen`
        now records under a `threading.Lock` and fills with zeros (value not relied on); new
        `_make_positional_fake_urlopen` derives each tile's fill from its **request sub-bbox** (inverting
        `_fetch_mosaic`'s interpolation to `row_idx*n_cols + col_idx`), so placement is order-independent.
        Verified the rewritten fakes pass against the **sequential** code (14 passed) before touching production.
- [x] Task 2: Parallelize `_fetch_mosaic` with a `ThreadPoolExecutor` (AC: #1, #2)
  - [x] Added module constant `_MAX_FETCH_WORKERS: int = 4` with the IGN-etiquette / threads-for-I/O rationale.
  - [x] Extracted `_fetch_tile(y0,y1,x0,x1,west,south,east,north,width,height) -> tuple[int,int,int,int,bytes]`
        computing the sub-bbox with the identical formula and calling `_wms_get_bil`.
  - [x] `_fetch_mosaic`: `truststore.inject_into_ssl()` once in the parent; build the tile list;
        `max_workers=min(_MAX_FETCH_WORKERS, total_tiles)`; submit all; `as_completed` → `future.result()`
        (re-raises worker `DataSourceUnavailableError` unchanged) → validate byte count (same message) →
        `np.frombuffer(...).reshape(...)` into `arr[y0:y1,x0:x1]` → increment counter → emit `tile {i}/{N}`.
  - [x] Kept `arr = np.empty(...)` — tiles partition the array, every pixel written exactly once.
  - [x] All validation / array writes / progress stay in the parent thread; workers only fetch bytes.
- [x] Task 3: Offline tests (AC: #1, #2)
  - [x] Updated `test_multi_tile_mosaic_places_tiles_north_up` to use the positional fake (NW=0 / SE=N-1 /
        unique={0..N-1} now prove order-independence).
  - [x] Added `test_multi_tile_mosaic_byte_identical_under_out_of_order_completion` — `delay_by_value` makes
        the NW tile complete *last* (reverse submission order); mosaic invariant holds unchanged.
  - [x] `test_multi_tile_fetch_emits_tile_progress_lines` still green — completion-counter semantics keep
        `["  tile 1/N", …, "  tile N/N"]`.
  - [x] Existing failure-mapping tests still pass; added `test_mid_batch_tile_failure_maps_to_data_source_unavailable`
        (one failing tile among many → `DataSourceUnavailableError` via `future.result()`, no partial raster).
  - [x] Cache-hit / `force_refresh` / single-tile paths confirmed untouched (14 passed).
- [x] Task 4: Live concurrency-etiquette check + record (AC: #3)
  - [x] Ran a live IGN probe at the **r20 concurrency profile** (16 tiles, 4 workers) via a forced-small-tile
        Grenoble fetch: sequential 6.94 s → 4-worker 1.44 s (**4.81×**), mosaics `np.array_equal` **True** on
        real IGN data, elevations plausible (997–1235 m), **no 429 / no connection error / all BIL**.
  - [x] Recorded in Debug Log. `_MAX_FETCH_WORKERS=4` confirmed safe — no back-off needed. Full-payload r20
        wall-clock lands at the 14.6 probe (per 14.1/14.2 precedent; the probe used small payloads at the
        identical 16-tile/4-connection concurrency, which is the actual etiquette risk).
- [x] Task 5: Doc-sync + gates (AC: #3)
  - [x] Architecture Cat 3 (pipeline) + Cat 8 (progress) notes added: setup's first fetch concurrency
        (`ThreadPoolExecutor`, `_MAX_FETCH_WORKERS`, threads-for-I/O), completion-order-independent mosaic,
        `tile i/N` completion-counter semantics unchanged, `StageProgress` parent-thread-only.
  - [x] Gates: `ruff check` clean, `ruff format` clean, whole-project `basedpyright` **0/0/0**,
        `uv run python -m pytest --cov` **857 passed** / 96% (dem_download.py 97% > 95% gate).
- [x] Task 6: Add `--dem-fetch-workers` CLI flag (scope revision, post-review)
  - [x] Renamed `_MAX_FETCH_WORKERS` → public `DEFAULT_DEM_FETCH_WORKERS` (still 4); `resolve_dem` gained
        `fetch_workers: int | None = None`, threaded to `_fetch_mosaic`'s new `max_workers` param
        (`None` → `DEFAULT_DEM_FETCH_WORKERS`); `min(workers, total_tiles)` unchanged.
  - [x] Added `dem_fetch_workers_option` (`_shared.py`, direct default + `show_default=True`, matching
        `elevation_smoothing_option`'s style) and `validate_dem_fetch_workers` (`>= 1` guard, matching
        `--iter-budget`); wired into `cli/setup.py` (`validate_dem_fetch_workers` called alongside
        `validate_setup_radius`; `fetch_workers=dem_fetch_workers` passed to `resolve_dem`).
  - [x] Tests: `test_area_parsing.py` (reject 0/negative → `BadCLIArgError`; wiring test proving the value
        reaches `resolve_dem`'s `fetch_workers` kwarg); `test_dem_download.py` (byte-identity across worker
        counts 1/default/64; a lock-guarded concurrency-tracking fake proving `fetch_workers=1` genuinely
        caps peak concurrent requests at 1, not a cosmetic no-op); `test_cli_options.py` (decorator
        registered); `test_cli_help.py` + `test_cli_smoke.py` (flag listed in `--help`; `0` → exit 2 e2e).
  - [x] Architecture Cat 3 flag-surface table + DEM-fetch-concurrency note updated to record the revision
        and its rationale.
  - [x] Gates re-run green: `ruff check`/`format` clean, `basedpyright` 0/0/0, `865 passed` / 96% cov.
- [x] Task 7: Post-review hardening — per-tile retry + fail-fast cancellation (2026-07-07, `/code-review`)
  - [x] `/code-review` (medium effort) on the working-tree diff found: (1) a transient tile failure (timeout/
        reset/429/5xx/truncated read) failed the whole fetch with no retry; (2) on any failure, the pool's
        default `shutdown(wait=True)` drained every already-queued tile before propagating — firing requests
        for tiles no one would use, against a server that just showed distress.
  - [x] New internal `_TransientDEMError`; `_wms_get_bil`'s network-except branches raise it (content-type /
        byte-count checks still raise `DataSourceUnavailableError` directly — deterministic, not retried).
  - [x] `_fetch_tile` retries `_TransientDEMError` up to `_TILE_MAX_ATTEMPTS` (default 3) with exponential
        backoff + full jitter; retry-exhausted maps to `DataSourceUnavailableError` unchanged user-message.
  - [x] `_fetch_mosaic`'s `as_completed` loop wrapped in `try/except BaseException` →
        `pool.shutdown(cancel_futures=True)` before re-raising, so not-yet-started tiles are dropped on any
        failure (including `KeyboardInterrupt`); only the ≤ `max_workers` in-flight tiles still finish.
  - [x] Added a leading `tile 0/N` progress line before the first request, restoring "working, not stuck"
        for the single-tile / first-tile case that the completion-counter rewrite had silenced.
  - [x] `_HTTP_TIMEOUT_S` dropped 120 s → 30 s now that retries backstop transient blips.
  - [x] New env-var overrides (first in the codebase; process-local to `steeproute-setup`, not inter-CLI
        state — see Cat 7 scoping note in architecture.md): `STEEPROUTE_DEM_HTTP_TIMEOUT_S`,
        `STEEPROUTE_DEM_FETCH_RETRIES`, `STEEPROUTE_DEM_FETCH_BACKOFF_S`; malformed values warn-and-default
        via new `_env_int`/`_env_float` helpers rather than crashing at import.
  - [x] Tests: `test_transient_tile_failure_is_retried_then_succeeds`,
        `test_tile_failure_gives_up_after_exactly_max_attempts`,
        `test_terminal_tile_failure_cancels_pending_tiles`; updated the two existing failure-path tests to
        zero `_TILE_BACKOFF_BASE_S` (no real sleeps) and the progress test for the leading `tile 0/N`.
  - [x] Architecture Cat 3 / Cat 8 notes + a new "Environment-variable overrides" note added; this story's
        Dev Notes ("Error propagation", "Progress semantics", "Concurrency etiquette") annotated in place
        with dated updates rather than rewritten, so the original design reasoning stays legible.
  - [x] Gates re-run green: `ruff check` clean, `basedpyright` (changed files) 0/0/0, 19/19
        `test_dem_download.py` passed, 173/173 across the four affected unit-test files, 38/38 relevant
        `test_cli_smoke.py` cases.

## Dev Notes

### What this story is — and the one non-obvious trap

Mechanically small: replace the sequential double-loop in `_fetch_mosaic` (`dem_download.py:299-324`) with
a `ThreadPoolExecutor`. Threads (not processes) are correct here — this is **network-wait-bound I/O**, so the
GIL is released during `urlopen` and plain threads give the full speedup without pickling/spawn cost (handoff
§S4, §6.2). Do **not** reach for `ProcessPoolExecutor` (that's 14.4's tool, for CPU-bound GRASP).

**The trap is in the tests, not the production code.** The current fake `urlopen` fills each tile with
`value = len(call_log)` and appends to a shared list — a read-modify-write that races under threads and
produces duplicate fill values, breaking `test_multi_tile_mosaic_places_tiles_north_up`'s
`set(np.unique(band)) == {0..N-1}` assertion **intermittently**. Fix the fake *first* (Task 1): derive each
tile's fill deterministically from its **request sub-bbox** (parse the `bbox` query param), so it is
thread-safe and completion-order-independent. This is the single most likely way to ship a flaky test.

### Byte-identity — why it holds and why there's no rebake

The mosaic is byte-identical to the sequential path for a concrete reason: **tiles partition `arr` into
disjoint `[y0:y1, x0:x1]` slices**, each computed from the exact same sub-bbox formula as today, and each
written exactly once. Completion order changes *when* a slice is filled, never *what* it's filled with.
Prove it with an out-of-order-completion test (Task 3) using `np.array_equal` against a sequential reference.

Content-hash reality (same lens as 14.1/14.2): `dem_download.py` **is** under
`_PIPELINE_CONTENT_GLOBS = ("pipeline/**/*.py", "models.py")` (`cache.py:60`), so editing it **does** shift the
pipeline content hash → real user *setup* caches re-prepare once (by design, Category 4b). But:

- **No golden rebake, no fixture regen.** The regression harness reads committed fixture caches by
  **geometric containment** (`check_coverage`), not by re-deriving the content hash, and the DEM output is
  byte-identical — so goldens stay byte-identical exactly like 14.1/14.2. Do **not** speculatively regenerate.
- **The DEM raster cache is unaffected.** It's keyed separately by `_dem_cache_key` (bbox + grid + layer +
  format + `dem_version`) under `<cache-root>/steeproute/dem/`, **not** by the pipeline content hash. A
  re-prepare re-runs the stages but stage-5 reads the same cached, byte-identical raster.

This is a byte-identical change — the only real-world effect is the standard one-time setup re-prepare.

### Error propagation across the pool

**Updated 2026-07-07 (post-review hardening) — supersedes the original "map every failure directly" design
below.** Code review found two problems with the original error path: (1) a *transient* failure (timeout,
reset, HTTP 429/5xx, truncated read) on any single tile failed the whole fetch immediately, no retry — the
sequential path had the identical gap, but concurrency makes hitting it once-per-batch far more likely than
once-per-tile; (2) on any failure, the default `shutdown(wait=True)` on `with ThreadPoolExecutor(...)` exit
**drains every already-queued tile** before propagating — worse than sequential, since it fires requests
for tiles no one will use, against a server that just showed distress.

Fix: `_wms_get_bil` now raises the new internal `_TransientDEMError` for the retryable set (truncated read,
`URLError`/`OSError` — HTTP 429/5xx included, since `HTTPError` subclasses `URLError`); the content-type and
byte-count checks still raise `DataSourceUnavailableError` directly (deterministic, not retried — retrying a
guaranteed-identical response only delays the real error). `_fetch_tile` wraps the request in a retry loop
(`_TILE_MAX_ATTEMPTS`, default 3, exponential backoff + full jitter) and only maps a retry-exhausted
`_TransientDEMError` to `DataSourceUnavailableError("DEM source unreachable.", …)` — the **user-facing
message is unchanged**, only the internal `detail` now names the attempt count. `future.result()` in the
parent still re-raises this identical exception object, so the exit-2 tier (NFR6, §Cat 10) is unaffected.
The `as_completed` loop in `_fetch_mosaic` is now wrapped in `try/except BaseException` that calls
`pool.shutdown(cancel_futures=True)` before re-raising — not-yet-started tiles are dropped; only the
≤ `max_workers` tiles already in flight finish (bounded, same as before). `BaseException` (not `Exception`)
so `KeyboardInterrupt` also cancels rather than drains.

**Original design (superseded above, kept for history):** `_wms_get_bil` mapped every failure directly to
`DataSourceUnavailableError`, and the pool's default `shutdown(wait=True)` drain-the-queue behavior on
failure was accepted as "bounded, acceptable on a failure path" — review found the queue drain is unbounded
in the number of *additional* requests fired (not just the ≤ `max_workers` in-flight ones), which is the
actual defect the update above fixes.

Keep the **payload-size validation in the parent** (as the AC directs): a worker returns raw `bytes`, the
parent checks `len(body) == tile_w*tile_h*4` and raises the same `DataSourceUnavailableError` with the same
`{len} bytes for a {tile_w}x{tile_h} float32 tile (expected {expected})` detail. This keeps the numpy
write + validation single-threaded and the message shape identical.

### Progress semantics (FR33 / §Cat 8)

Today `progress.line(f"tile {i}/{total}")` fires **before** each blocking request (announce "working"). Under
concurrency, emit it **on completion** from the parent as each future resolves, with `i` a **monotonic
completion counter** (1..N), not the tile's grid index. The printed sequence is therefore still
`tile 1/N … tile N/N` and `test_multi_tile_fetch_emits_tile_progress_lines` (asserts exactly that, indented
`"  tile i/N"`) stays green. `StageProgress.line` is **not thread-safe** — only ever call it from the parent
loop, never inside a worker. Progress is a pure display side-effect and never touches determinism (`progress.py`
module docstring; FR29 unaffected).

**Updated 2026-07-07 (post-review hardening).** Moving the emit from *before-request* to *on-completion*
regressed the single-tile (and first-tile-of-many) case: nothing prints until that tile's request finishes,
so the sequential path's "at least you see `tile 1/N` while it hangs" guarantee was silently lost for the
one-tile fetch. Fix: emit a leading `tile 0/N` before any future is submitted, so the seam is never silent
during a wait. The printed sequence is now `tile 0/N … tile N/N`;
`test_multi_tile_fetch_emits_tile_progress_lines` was updated accordingly (asserts `range(0, total + 1)`).

### Concurrency etiquette — the actual risk this story retires (AC #3)

The one genuine unknown (handoff §S4, §9): **IGN Géoplateforme's response to concurrent requests.** The
fixture is a single tile, so this can only be validated live at real scale. Run r20 (16 tiles) with
`--force-refresh`, watch for 429 / resets / truncation, and record it. Start at `max_workers=4` (conservative);
back off to 2 (and note it) if IGN pushes back. This is the story's risk-retirement deliverable — don't skip
the recording even if 4 works cleanly (a clean result is itself the finding). `_HTTP_TIMEOUT_S = 120` per tile
already bounds a stuck connection.

**Updated 2026-07-07 (post-review hardening):** `_HTTP_TIMEOUT_S` dropped **120 s → 30 s** (overridable via
`STEEPROUTE_DEM_HTTP_TIMEOUT_S`) now that per-tile retries (`_TILE_MAX_ATTEMPTS`, see "Error propagation"
above) backstop transient blips — a single heroic per-request timeout is no longer needed. Worst case for a
persistently dead tile is now ~30 s × 3 attempts + jittered backoff ≈ 100 s, versus the old single 120 s hang.

### Scope guardrails

- **Only `_fetch_mosaic` (+ `resolve_dem`'s new kwarg) changes** in the pipeline, plus a small `_fetch_tile`
  helper and the `DEFAULT_DEM_FETCH_WORKERS` constant. `_wms_get_bil`, `_grid_dims`, `_tile_ranges`,
  `_dem_cache_key`, `graph_dem_bounds`, `_write_geotiff_atomic`, `_padded_bbox` are **untouched**.
- **Not this story:** `--workers` GRASP parallelism (14.4 — `ProcessPoolExecutor`, CLI-layer, invalidates
  nothing), osmnx CPU levers (14.5), the r50 probe (14.6).
- ~~Do not add a `--dem-workers` CLI flag~~ — **superseded by the scope revision above (Task 6):** the AC's
  module-constant framing was right that the value is an IGN-etiquette ceiling, not a raw performance knob,
  but Yann overrode the "therefore no flag" conclusion since only one live data point (Task 4) backs the
  "4 is safe" claim. `--dem-fetch-workers` now exists; the default (4) and the underlying etiquette reasoning
  are unchanged — only the "must edit code to change it" constraint is lifted.
- Python is pinned `>=3.13` — `concurrent.futures` + `as_completed` are stdlib, no new dependency.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` **0/0/0**, default
  `uv run pytest --cov`. `pipeline/` is gated at **95%** coverage — keep the new fetch path covered.
- Offline DEM tests live in `tests/unit/test_dem_download.py` (never touches the network — `urlopen` is
  monkeypatched); the live fetch is `tests/integration/test_dem_live.py` (`-m live`, skipped in default CI).
  Default collection excludes `live`/`slow`/`benchmark` (`pyproject.toml:236`).
- **`uv` Windows build flake:** after a commit or `pyproject.toml` edit, `uv run` may hit a corporate-TLS cert
  error (~43 `test_cli_smoke` failures as the symptom). Fix once with `uv sync --native-tls`, then
  `uv run --no-sync …` for the rest of the session (14.1/14.2 hit this).
- The `# pyright: reportUnknown*` header on `dem_download.py` relaxes the rasterio boundary; keep it. The
  new `concurrent.futures` code is fully typed and needs no new suppressions.

### Project Structure Notes

- **Modified (production):** `src/steeproute/pipeline/dem_download.py` — `_fetch_mosaic` parallelized
  (`ThreadPoolExecutor`, `as_completed`, parent-side validate/write/progress); new `_fetch_tile` helper;
  public `DEFAULT_DEM_FETCH_WORKERS` constant (renamed from `_MAX_FETCH_WORKERS`). `resolve_dem` gained
  `fetch_workers: int | None = None`; `_fetch_mosaic` gained `max_workers: int | None = None`.
  `src/steeproute/cli/_shared.py` — new `dem_fetch_workers_option`, new `validate_dem_fetch_workers`.
  `src/steeproute/cli/setup.py` — `--dem-fetch-workers` wired to `resolve_dem(fetch_workers=...)`.
- **Modified (tests):** `tests/unit/test_dem_download.py` — thread-safe/order-independent fake `urlopen`;
  updated mosaic-placement test; new out-of-order-completion byte-identity test; new mid-batch-failure test;
  new `fetch_workers`-override byte-identity + concurrency-cap tests. `tests/unit/test_area_parsing.py` —
  new `--dem-fetch-workers` validation + CLI-wiring tests. `tests/unit/test_cli_options.py` — new decorator
  in `ALL_DECORATORS`. `tests/unit/test_cli_help.py` + `tests/e2e/test_cli_smoke.py` — flag added to
  `SETUP_FLAGS`; new e2e exit-2 test.
- **Modified (docs):** `_bmad-output/planning-artifacts/architecture.md` — Cat 3 / Cat 8 concurrency note +
  flag-surface table row + scope-revision note; `sprint-status.yaml` (14-3 status transitions).
- **Content hash:** shifts (file is in `pipeline/`), byte-identical output regardless of worker count → no
  golden rebake / no fixture regen; user setup caches re-prepare once (Category 4b), DEM raster cache
  unaffected.
- **Untouched:** everything else in `pipeline/`, `cache.py`, solver, validator, output, `models.py`,
  `_PIPELINE_CONTENT_GLOBS`. No on-disk format change. Query CLI (`cli/query.py`) untouched — this flag is
  setup-only.

### References

- [Source: epics.md §Story 14.3](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth: threads for
  I/O, module-constant `max_workers` start-at-4, `(y0,y1,x0,x1,bytes)` task shape, parent validates + writes,
  completion-order-independent byte-identical mosaic, `tile i/N` on completion, r20 etiquette test, Cat 3/8 note.
- [Source: research/steeproute-next-optimization-pass-handoff-2026-07-05.md §S4 (lines 204-215), §2
  (per-tile-cost 8.4 s/tile @ r20), §6.2 (threads-not-processes for network wait), §9 (concurrency unknown)](_bmad-output/planning-artifacts/research/steeproute-next-optimization-pass-handoff-2026-07-05.md)
  — measured 134 s @ r20, ThreadPoolExecutor recipe, back-off-if-429 guidance, "test IGN at r20 first".
- [Source: src/steeproute/pipeline/dem_download.py:276-325](src/steeproute/pipeline/dem_download.py) —
  `_fetch_mosaic` sequential loop (the target) + the sub-bbox interpolation to preserve bit-for-bit;
  [dem_download.py:328-386](src/steeproute/pipeline/dem_download.py) `_wms_get_bil` (already maps all failures
  to `DataSourceUnavailableError` — reuse unchanged); [dem_download.py:96,267-273](src/steeproute/pipeline/dem_download.py)
  `_MAX_TILE_PX` / `_tile_ranges` (tiling decomposition, unchanged).
- [Source: tests/unit/test_dem_download.py:71-155](tests/unit/test_dem_download.py) — `_make_fake_urlopen`
  (the shared-counter fill that must be made thread-safe/order-independent), the multi-tile placement test,
  and the `tile i/N` progress test to preserve.
- [Source: src/steeproute/progress.py:85-133](src/steeproute/progress.py) — `StageProgress.line` (indents
  `"  {text}"`; not thread-safe → call only from parent) and the module docstring (progress is a pure
  side-effect, never touches determinism).
- [Source: architecture.md §Cat 3 (line 234), §Cat 8 (line 544), §Cat 10 error tiers, Cat 4b re-prepare](_bmad-output/planning-artifacts/architecture.md)
  — the pipeline-stage table, stream discipline, `DataSourceUnavailableError` exit-2 tier, and the
  content-hash re-prepare semantics.
- [Source: _bmad-output/implementation-artifacts/14-2-vectorize-and-de-churn-pipeline-loops.md §Content-hash
  reality](_bmad-output/implementation-artifacts/14-2-vectorize-and-de-churn-pipeline-loops.md) — the
  "content-hash shifts but goldens stay byte-identical via geometric containment, no speculative regen" pattern.
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-06-setup-solver-scaling.md §2, §4.1
  (Story 14.3), §5](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-06-setup-solver-scaling.md)
  — the correct-course that inserted Epic 14; confirms 14.3 is independent, network-etiquette-tested at r20,
  and Cat 3/8 doc-synced.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Gates (all green):**

```
tests/unit/test_dem_download.py                 → 14 passed (12 pre-existing + 2 new)
uv run python -m pytest --cov (default markers) → 857 passed, 15 deselected, 96% overall
                                                   (dem_download.py 97% — > 95% pipeline gate)
ruff check src tests                            → All checks passed!
ruff format src tests                           → clean (2 files reformatted during dev)
basedpyright (whole project)                    → 0 errors, 0 warnings, 0 notes
```

Note: `uv run pytest` / `uv run basedpyright` fail on this machine's uv 0.9.26 with "Failed to
canonicalize script path"; `uv run python -m pytest` / `-m basedpyright` work and were used throughout.

**Live IGN concurrency-etiquette probe (AC #3), real `data.geopf.fr` WMS:**

Forced `_MAX_TILE_PX=32` over a Grenoble r0.2 bbox → **16 tiles**, the exact tile count and 4-worker
concurrency profile of a real r20 setup, but with ~KB payloads so it runs in seconds:

```
grid=120x120px, tiles=16
sequential (1 worker):    6.94 s
parallel   (4 workers):   1.44 s   speedup=4.81x
mosaics byte-identical: True         (np.array_equal on real IGN data)
elevation range: 997.4..1234.7 m (all finite)
IGN behavior @ 4-way concurrency: no 429 / no connection error / all BIL
```

IGN Géoplateforme served 4 simultaneous connections cleanly — `_MAX_FETCH_WORKERS=4` confirmed safe, no
back-off. The full-payload r20 wall-clock (2048px tiles, ~16 MB each) is deferred to the 14.6 probe per
the 14.1/14.2 precedent; the etiquette risk (number of simultaneous connections) is identical at any
payload size and is retired here.

**Scope-revision re-run (Task 6, `--dem-fetch-workers`):**

```
uv run python -m pytest tests/unit/test_dem_download.py tests/unit/test_area_parsing.py \
  tests/unit/test_cli_options.py tests/unit/test_cli_help.py -q   → 170 passed
uv run python -m pytest tests/e2e/test_cli_smoke.py -q            → 45 passed
uv run python -m pytest --cov (default markers)                  → 865 passed, 15 deselected, 96% overall
                                                                     (dem_download.py 97%, cli/_shared.py 99%,
                                                                     cli/setup.py 91% — pipeline gate holds)
ruff check src tests                                              → All checks passed!
ruff format --check src tests                                     → 106 files already formatted
uv run python -m basedpyright                                     → 0 errors, 0 warnings, 0 notes
```

Same `uv run pytest` / `uv run basedpyright` script-path flake noted above recurred this session;
`uv run python -m pytest` / `-m basedpyright` worked throughout, no `uv sync --native-tls` needed this time.

### Completion Notes List

**Threads, not processes — and why the mosaic is byte-identical.** Tile fetch is network-wait-bound, so a
`ThreadPoolExecutor` (GIL released during `urlopen`) gives the full speedup with no pickle/spawn cost — the
opposite of 14.4's CPU-bound GRASP parallelism. Byte-identity is structural, not lucky: tiles partition the
mosaic into disjoint `arr[y0:y1, x0:x1]` slices, each sub-bbox computed with the exact same interpolation as
the old sequential loop, each written exactly once. Completion order changes only *when* a slice is filled,
never *what*. Proven three ways: the reverse-completion unit test (`np.array_equal`), the live probe
(`np.array_equal` on real IGN data), and the full golden suite.

**The real risk was in the tests, not the production code.** The old fake `urlopen` filled each tile with
`value = len(call_log)` — a shared-counter read-modify-write that races under threads and would make the
mosaic-uniqueness assertion flaky. Fixed *first* (Task 1): `_make_positional_fake_urlopen` derives each
tile's value from its request sub-bbox (order-independent by construction), and `_make_fake_urlopen` now
records under a lock. Verified against the sequential code before parallelizing.

**Error propagation.** A worker's `DataSourceUnavailableError` (raised inside `_wms_get_bil`) is re-raised
unchanged by `future.result()` in the parent, so the exit-2 tier (NFR6) and error-message shapes are
preserved — no `concurrent.futures` wrapper leaks. The `with ThreadPoolExecutor(...)` exit waits out the
≤ `max_workers` in-flight tiles on the failure path (bounded). New `test_mid_batch_tile_failure_*` locks this in.

**Progress stayed a parent-thread completion counter.** `tile i/N` now fires as each future resolves, from
the parent only (`StageProgress` is not thread-safe). The printed sequence is unchanged (`tile 1/N … tile
N/N`), so the existing progress test passes untouched. Workers never touch the progress seam.

**No golden rebake / no fixture regen.** `dem_download.py` is under `_PIPELINE_CONTENT_GLOBS`, so the pipeline
content hash shifts and real user *setup* caches re-prepare once (by design, Cat 4b). But the regression suite
reads committed DEM fixtures (`dem.tif`) and never exercises `_fetch_mosaic`, and the DEM output is
byte-identical regardless — so all goldens pass unchanged (confirmed in the 857-passed default run, which
includes the fast e2e regressions). The DEM raster cache (keyed separately by bbox, not content hash) is
unaffected.

**Scope revision: `--dem-fetch-workers` added post-review.** The story originally decided against a CLI flag
(module constant only) on the reasoning that concurrency here bounds *server etiquette*, not local resources.
That distinction is still true and is preserved in the flag's help text and the architecture note — a flag
doesn't turn this into a general performance knob, it just lets the one live-validated data point (Task 4,
r20 profile, 4 workers, no 429s) be revised without a release if IGN's real tolerance differs. Implementation
mirrors the existing `--iter-budget`/`--n` validation pattern (`validate_dem_fetch_workers`, `>= 1` guard,
`BadCLIArgError` → exit 2) and the existing `--elevation-smoothing`-style option shape (direct default +
`show_default=True`, not the `None`-sentinel `--dem-version` pattern, since there's no second consumer of the
default value elsewhere). `resolve_dem`'s `fetch_workers` param is optional (`None` → module default), so
every other caller (tests, `test_dem_live.py`) is unaffected. New tests prove two things a naive
implementation could get wrong: (1) the mosaic stays byte-identical across worker counts 1/4/64 — concurrency
truly only affects wall-clock — and (2) `fetch_workers=1` is not a cosmetic no-op: a lock-guarded counter in
the test fake proves peak concurrent in-flight requests is genuinely capped at 1.

### File List

**Modified (production):**
- `src/steeproute/pipeline/dem_download.py` — `_fetch_mosaic` parallelized (`ThreadPoolExecutor` +
  `as_completed`, parent-side validate/reshape/write/progress); new `_fetch_tile` worker helper; public
  `DEFAULT_DEM_FETCH_WORKERS = 4` constant (renamed from `_MAX_FETCH_WORKERS`); `concurrent.futures` import.
  `resolve_dem` gained `fetch_workers: int | None = None`; `_fetch_mosaic` gained `max_workers: int | None
  = None`. `_wms_get_bil` / `_tile_ranges` and all other helpers unchanged.
- `src/steeproute/pipeline/dem_download.py` (post-review hardening, 2026-07-07) — new `_TransientDEMError`;
  new `_env_int`/`_env_float` helpers; `_wms_get_bil`'s network-except branches raise `_TransientDEMError`
  instead of `DataSourceUnavailableError` directly; `_fetch_tile` gained a retry loop
  (`_TILE_MAX_ATTEMPTS`/`_TILE_BACKOFF_BASE_S`); `_fetch_mosaic`'s `as_completed` loop wrapped in
  `try/except BaseException` → `pool.shutdown(cancel_futures=True)`; leading `tile 0/N` progress emit;
  `_HTTP_TIMEOUT_S` 120 → 30 (env-overridable); new `random`/`time` imports.
- `src/steeproute/cli/_shared.py` — new `dem_fetch_workers_option` (click option, default
  `DEFAULT_DEM_FETCH_WORKERS`); new `validate_dem_fetch_workers` (`>= 1` guard → `BadCLIArgError`); new
  import of `DEFAULT_DEM_FETCH_WORKERS` from `pipeline.dem_download`.
- `src/steeproute/cli/setup.py` — `@dem_fetch_workers_option` added to `cli`; `dem_fetch_workers: int` param;
  `validate_dem_fetch_workers(dem_fetch_workers)` called alongside `validate_setup_radius`;
  `fetch_workers=dem_fetch_workers` passed to `resolve_dem(...)`.

**Modified (tests):**
- `tests/unit/test_dem_download.py` — thread-safe `_make_fake_urlopen` (lock + zero fill); new
  `_make_positional_fake_urlopen` (order-independent, `delay_by_value` / `fail_on_value` hooks); updated
  `test_multi_tile_mosaic_places_tiles_north_up`; new `test_multi_tile_mosaic_byte_identical_under_out_of_order_completion`,
  `test_mid_batch_tile_failure_maps_to_data_source_unavailable`,
  `test_fetch_workers_override_yields_identical_mosaic`, `test_fetch_workers_caps_concurrent_requests`;
  `threading` / `time` imports.
- `tests/unit/test_area_parsing.py` — new `test_setup_cli_rejects_non_positive_dem_fetch_workers`,
  `test_setup_cli_threads_dem_fetch_workers_to_resolve_dem`.
- `tests/unit/test_cli_options.py` — `dem_fetch_workers_option` added to imports + `ALL_DECORATORS`.
- `tests/unit/test_cli_help.py` — `--dem-fetch-workers` added to `SETUP_FLAGS`.
- `tests/e2e/test_cli_smoke.py` — `--dem-fetch-workers` added to `SETUP_FLAGS`; new
  `test_setup_zero_dem_fetch_workers_exits_2`.
- `tests/unit/test_dem_download.py` (post-review hardening, 2026-07-07) — new
  `test_transient_tile_failure_is_retried_then_succeeds`,
  `test_tile_failure_gives_up_after_exactly_max_attempts`,
  `test_terminal_tile_failure_cancels_pending_tiles`; `_TILE_BACKOFF_BASE_S` zeroed in the two existing
  failure-path tests; `test_multi_tile_fetch_emits_tile_progress_lines` updated for the leading `tile 0/N`.

**Modified (docs):**
- `_bmad-output/planning-artifacts/architecture.md` — Cat 3 DEM-fetch-concurrency note + Cat 8 within-stage-
  progress-under-concurrency note + scope-revision note; flag-surface table row for `--dem-fetch-workers`.
- `_bmad-output/implementation-artifacts/14-3-parallelize-dem-tile-fetch.md` — this file.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 14-3 → in-progress → review.

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-07 | Yann (Claude Opus 4.8) | Story 14.3 implemented. Parallelized DEM tile fetch in `_fetch_mosaic` via `ThreadPoolExecutor` (`_MAX_FETCH_WORKERS=4`, threads for I/O): workers return raw BIL bytes, parent validates + writes disjoint mosaic slices → byte-identical regardless of completion order. Fixed the tests' racy shared-counter fake first (position-derived, order-independent). Live IGN probe at the r20 concurrency profile (16 tiles / 4 workers): 6.94→1.44 s (4.81×), mosaics byte-identical on real data, no 429 / no error — `_MAX_FETCH_WORKERS=4` confirmed safe. `tile i/N` kept as a parent-thread completion counter (FR33 unchanged). Architecture Cat 3/Cat 8 noted. No golden rebake / no fixture regen (regression suite reads committed DEM fixtures; content-hash shift re-preps user caches once, by design). Gates green (ruff, basedpyright 0/0/0, 857 passed, 96% cov). |
| 2026-07-07 | Yann (Claude Sonnet 5) | Scope revision (Task 6): added `--dem-fetch-workers` CLI flag, overriding the story's original "module constant, not a flag" decision at Yann's request — the "4 is safe" conclusion rests on one live validation run, and the value should be adjustable without a code change if IGN's real-world tolerance differs. Renamed `_MAX_FETCH_WORKERS` → public `DEFAULT_DEM_FETCH_WORKERS` (still 4, unchanged); threaded `fetch_workers` through `resolve_dem`/`_fetch_mosaic`; added `validate_dem_fetch_workers` (`>= 1` guard, `--iter-budget` pattern) and `dem_fetch_workers_option` (`--elevation-smoothing`-style direct default). New tests prove byte-identity holds across worker counts and that `fetch_workers=1` genuinely caps concurrency (not cosmetic). Architecture Cat 3 note + flag-surface table updated. Gates green (ruff, basedpyright 0/0/0, 865 passed, 96% cov). |
| 2026-07-07 | Yann (Claude Sonnet 5) | Task 7, post-review hardening: `/code-review` (medium) on the working-tree diff found the failure path was worse than the sequential code it replaced — no retry on transient failures, and the pool's default shutdown drained every queued tile on any failure instead of just the in-flight ones. Fixed by splitting `_wms_get_bil`'s failures into a new internal `_TransientDEMError` (network/timeout/truncated-read — retried) vs. `DataSourceUnavailableError` (bad content-type/byte-count — not retried, same as before); `_fetch_tile` retries transient failures up to `_TILE_MAX_ATTEMPTS` (default 3, jittered exponential backoff) before mapping to the unchanged user-facing `DataSourceUnavailableError` message; `_fetch_mosaic` now cancels not-yet-started tiles (`pool.shutdown(cancel_futures=True)`) on any failure, including `KeyboardInterrupt`. Added a leading `tile 0/N` progress line so the single-tile fetch isn't silent during its wait (a regression the original completion-counter rewrite introduced). Dropped `_HTTP_TIMEOUT_S` 120 s → 30 s now that retries backstop transient blips. Added the codebase's first environment-variable configuration surface (`STEEPROUTE_DEM_HTTP_TIMEOUT_S` / `_FETCH_RETRIES` / `_FETCH_BACKOFF_S`, process-local to `steeproute-setup`, out of scope of Cat 7's inter-CLI "no env vars" decision). Architecture Cat 3/Cat 8 notes updated plus a new "Environment-variable overrides" note; this story's Dev Notes annotated in place (dated updates, original reasoning kept for history) rather than rewritten. New/updated tests: `test_transient_tile_failure_is_retried_then_succeeds`, `test_tile_failure_gives_up_after_exactly_max_attempts`, `test_terminal_tile_failure_cancels_pending_tiles`, plus backoff-zeroing and progress-sequence fixes to three existing tests. Gates green: `ruff check` clean, `basedpyright` (changed files) 0/0/0, 173/173 unit tests across the four affected files, 38/38 relevant e2e smoke cases. |
