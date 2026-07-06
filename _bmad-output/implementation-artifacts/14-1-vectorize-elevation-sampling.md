# Story 14.1: Vectorize elevation sampling (setup stage 5)

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want DEM elevation sampling to stop looping per-point through rasterio in Python,
so that the single biggest setup CPU stage drops from minutes to seconds without changing elevations.

## Acceptance Criteria

1. **Given** `sample_elevation` (`pipeline/dem.py`) costs ~215 s @ r20 — ~65 µs/point of per-point
   Python/rasterio overhead over ~3.5 M points (per-edge `transformer.transform` on lists, per-vertex
   bounds check, per-point `dataset.sample`), **when** it is reformulated as flat-array vectorized work
   (one ragged-array coordinate collection as in 13.2, one vectorized `pyproj` transform, vectorized
   inverse-affine rows/cols replicating rasterio's nearest-pixel/rowcol rounding exactly, fancy-indexed
   band read, vectorized bounds/nodata masks, and a `DEMCoverageError` of the **same message shape**
   locating the first offending edge), **then** sampled elevations are **bit-equal** to the old path over
   every vertex of the `grenoble_small` fixture (verify before deleting the old code) and the
   regression-golden suite passes untouched.
2. A per-stage benchmark exists before the change (the suite already ships `test_stage5_sample_elevation`;
   record an autosave baseline first); the measured stage-5 wall-clock drop is recorded in the close-out.
3. The r50 full-band-read memory footprint (~1.6 GB at r50, estimate) is either accepted with a note or
   handled by row-band windowing — the decision recorded here, measured at the 14.6 probe.

## Tasks / Subtasks

- [x] Task 1: Pin the stage-5 benchmark baseline *before* touching code (AC: #2)
  - [x] `uv run pytest tests/benchmarks/test_setup_stages.py::test_stage5_sample_elevation -m benchmark --benchmark-autosave` on the committed `grenoble_small` DEM/graph fixtures (no live network) — baseline saved as `0006`, median **1032.97 ms**
  - [x] Record the pre-change number; it is the "before" for the close-out compare
- [x] Task 2: Add a bit-equality proof test *before* deleting the old code (AC: #1)
  - [x] In `tests/unit/test_dem.py`, added `test_fixture_pipeline_bit_identical_to_scalar_reference` + `_scalar_reference_sample_elevation` (verbatim pre-14.1 loop); asserts the new `sample_elevation`'s `vertices_resampled` are **bit-identical** (`==`, not `approx`) to the scalar reference over **every** vertex of the real fixture (>1000 vertices, non-vacuous guard)
  - [x] Kept green through the rewrite — confirmed passing against the old impl first (oracle correct), then against the vectorized impl (proof)
- [x] Task 3: Vectorize `sample_elevation` (AC: #1)
  - [x] Collect all edge coordinates into flat `lons`/`lats` numpy arrays + per-edge offsets in one pass over `out.edges(data=True, keys=True)`, keeping the per-edge `isinstance(geom, shapely.LineString)` → `TypeError` guard
  - [x] One vectorized `transformer.transform(lons, lats)` for the whole graph
  - [x] Rows/cols via `rasterio.transform.rowcol(dataset.transform, xs, ys)` — the **exact call `dataset.sample` uses internally** (default op = `np.floor`), so indices are bit-identical by construction (better than a hand-rolled inverse affine, which risks diverging from GDAL's `InvGeoTransform` — see Completion Notes); elevations by fancy-indexing a single `dataset.read(1)`
  - [x] Vectorized bounds mask (half-open east/north) and nodata/non-finite mask (`~np.isfinite | (== nodata_finite)`); on violation, first offending flat index → owning edge via `np.searchsorted(offsets, idx)`, raising `DEMCoverageError` with the identical out-of-bounds / nodata message shapes
  - [x] Rebuild per-edge `vertices_resampled: list[tuple[float, float, float]]` from offsets using the original WGS84 `lats`/`lons` and `float()` elevations — in-memory contract unchanged
  - [x] Every pre-loop guard preserved verbatim: `rasterio.open` → `DataSourceUnavailableError`, no-CRS → `DEMCoverageError`, inverted/zero-width bounds → `DEMCoverageError`, nodata-finite derivation, empty-graph no-op, purity (`graph.copy()`, input never mutated)
- [x] Task 4: Run the correctness net + goldens (AC: #1)
  - [x] Full `tests/unit/test_dem.py` passes — 21 cases (20 pre-existing unmodified + 1 new bit-equality test): axis order, attribute contract, CRS/Lambert-93, half-open east edge, nodata, NaN-nodata, no-CRS, non-LineString `TypeError`, flipped-origin, empty-graph, landmark elevations
  - [x] `tests/e2e/test_pinned_regressions.py` fast (4/4) + `-m slow` (4/4) + flag-on tier (inside `--cov`) all pass **byte-identical** — no rebake needed
- [x] Task 5: Measure + record the gain and the r50 memory decision (AC: #2, #3)
  - [x] Re-ran the stage-5 benchmark (`0007`, `--benchmark-compare=0006`): median **1032.97 ms → 58.46 ms (~17.7×)**, min 973.72 → 51.44 ms (~18.9×)
  - [x] r50 full-band-read memory decision recorded below (accept-and-note; windowing deferred to the 14.6 probe)
- [x] Task 6: Gates + status
  - [x] `ruff check` clean, `ruff format --check` clean, whole-project `basedpyright` 0/0/0, `uv run pytest --cov` → 850 passed, 96% (dem.py 97%); sprint-status 14.1 → review

## Dev Notes

### What this story touches and why

`sample_elevation` (`src/steeproute/pipeline/dem.py:46-178`) is setup pipeline **stage 5**. For every edge it
(1) pulls `geom.coords` into Python `lons`/`lats` lists, (2) calls `transformer.transform(lons, lats)` per
edge, (3) loops each `(x, y)` through a Python bounds check, (4) calls `dataset.sample(...)` (a per-point
window-build + read generator), (5) loops the samples building `(lat, lon, elev)` tuples. The
per-point Python/rasterio overhead (~65 µs/point, ~3.5 M points @ r20) is >95% of the 215 s stage — the
r5 cProfile pins `rasterio sample_gen` at 14.1 s cumulative over 217,606 points. This is the **biggest
single setup CPU win** and is deliberately sequenced first in Epic 14 because it is **self-contained** and
**bit-identical-expected** (no cache-boundary or content-hash batching entanglement — unlike Story 14.2).

**The math must not change.** Same projection, same nearest-pixel selection, same bounds/nodata semantics,
same `(lat, lon, elev)` output order, same fail-fast-on-first-violation `DEMCoverageError`. Only the
*mechanism* moves from Python per-point loops to numpy array ops. `numpy` and `shapely>=2.0` are already
pinned deps; no new dependency is implied.

### The rounding contract (the crux of bit-equality — read carefully)

`dataset.sample((x, y))` internally does `row, col = dataset.index(x, y)` which is
`rasterio.transform.rowcol(dataset.transform, xs, ys, op=math.floor)`, then reads `band[row, col]`. To be
bit-equal you must replicate that exactly:

- Take the **inverse of `dataset.transform`** — `inv = ~dataset.transform` (an `affine.Affine`). rasterio
  computes fractional `(col, row)` as `inv * (x, y)` = `(inv.a*x + inv.b*y + inv.c, inv.d*x + inv.e*y + inv.f)`.
- Vectorize with the **same coefficients**: `fcol = inv.a*xs + inv.b*ys + inv.c`,
  `frow = inv.d*xs + inv.e*ys + inv.f`, then `cols = np.floor(fcol).astype(np.intp)`,
  `rows = np.floor(frow).astype(np.intp)`. Using `~dataset.transform`'s own coefficients (not a hand-rolled
  `(x-c)/a`) is what guarantees the float ops match rasterio's.
- Read the band once: `band = dataset.read(1)` (raster dtype, float32 for the fixture), then
  `elevs = band[rows, cols]`. `float(elevs[i])` is bit-identical to the old `float(sample_arr[0])` **iff the
  `(row, col)` pair matches** — which the inverse-affine replication ensures.
- **Verify, don't trust:** modern rasterio (1.3+) does plain floor with no `precision` rounding, but the
  installed version is the authority. The Task-2 fixture bit-equality assertion is the ground truth — if it
  fails, the rounding replication is wrong, not the goldens.

`pyproj.Transformer.transform` is element-wise/independent per coordinate, so one whole-graph call is
expected bit-equal to per-edge calls — but this too is proven by the Task-2 fixture assertion, not assumed.

### DEMCoverageError message shapes to preserve verbatim (tests pin substrings)

Two distinct raises, both `DEMCoverageError(user_message, detail=...)` (`errors.py:36`, a
`PreExecutionError`). Locate the **first** offending flat index (`np.argmax(mask)` on the violation mask),
map to its edge via the offsets array, and reproduce:

- **Out-of-bounds** (`test_..._out_of_bounds_vertex`, `test_..._exact_east_edge`): message contains
  `"Edge ({u}, {v}, {k}) has a vertex at projected ({x:.3f}, {y:.3f}) outside DEM bounds (...)"`. Tests
  assert `"(0, 1, 0)"` and `"outside DEM bounds"` in `user_message`.
- **nodata / non-finite** (`test_..._nodata_cell`, `test_..._nan_nodata`): message contains
  `"...sampling a nodata or non-finite DEM value..."`. Tests assert `"nodata or non-finite"`.

Fail-fast ordering: the scalar code checks **bounds first** (per vertex, in edge order), then samples. To
match its first-violation identity exactly under a mask approach, check the bounds mask first and raise on
its first true index before evaluating nodata; a point that is out-of-bounds must never reach the band
read (fancy-indexing with an out-of-bounds row/col would `IndexError` or wrap — mask/raise before indexing).

### Bit-equality proof (Task 2)

The AC requires bit-equality "over every vertex of the `grenoble_small` fixture, verify before deleting the
old code." Practical approach, mirroring 13.1's `_scalar_reference` pattern:

- Capture the old path's output first (either keep the old function under a `_sample_elevation_scalar_ref`
  name in the test module, copied verbatim, or snapshot its `vertices_resampled` per edge to compare
  against). 13.1 embedded a verbatim scalar reference in the test file and asserted `==` — reuse that shape.
- Assert new vs reference `(lat, lon, elev)` tuples with `==` (exact), not `pytest.approx`, over every edge
  and every vertex of the fixture graph through stage 5.
- Keep it in `tests/unit/test_dem.py`; it runs offline against the committed `dem.tif` + `osm_graph.graphml`.

### Why goldens are safe (no rebake, no fixture regen expected)

Two independent reasons, both worth internalizing so you don't over-engineer a fixture regen:

1. **Elevations are bit-equal**, so nothing the cache stores changes.
2. **The query-side regression harness reads committed fixture caches by geometric containment**
   (`check_coverage` → `_select_smallest_containing`, `cache.py:1151`), **not** by re-deriving the pipeline
   content hash. That is why Story 13.1 changed a `pipeline/` file with zero fixture regen. `sample_elevation`
   isn't even re-run on the query path — the cache already holds post-stage-5 elevations.

**Content-hash reality (informational, not an action item):** `dem.py` is in `_PIPELINE_CONTENT_GLOBS`
(`cache.py:60`), so its byte change *does* shift `compute_pipeline_content_hash()`. The only effect: a live
`steeproute-setup` re-prepares its cache once (Category 4b invalidation, by design). No committed fixture,
no test, and no golden depends on a real dem.py-derived hash value — the content-hash unit tests all use
synthetic `"a"*64` values or test the mechanism generically (`tests/unit/test_cache_key.py`). **Do not
regenerate the fixture caches for this story.**

### Memory (AC #3)

`dataset.read(1)` reads the whole band: 256 MB @ r20 (fine), ~1.6 GB @ r50 (float32 20k×20k, estimate).
For this story on `grenoble_small`/r20 that is a non-issue — record the decision (accept-and-note is the
expected call; row-band windowing after grouping points by row-range is the fallback) and defer the real
measurement to the 14.6 r50 probe. Do **not** build windowing speculatively — the handoff says decide by
measuring on a real r50 raster, not by guessing.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default
  `uv run pytest --cov` (~4:15 typical; markedly slower usually means a test hit the network).
- `tests/unit/test_dem.py` is the correctness net — 20+ cases covering axis order, attribute contract,
  CRS/Lambert-93 projection, half-open bounds, nodata, NaN-nodata, no-CRS, non-LineString `TypeError`,
  flipped-origin, empty-graph, and real-fixture landmark elevations. Keep them all green.
- `uv` Windows build flake: after a commit or `pyproject.toml` edit, `uv run` may hit a corporate-TLS cert
  error (~43 `test_cli_smoke` failures as the symptom). Fix once with `uv sync --native-tls`, then
  `uv run --no-sync ...` for the rest of the session.
- Benchmarks are excluded from the default run (marker `benchmark`); run explicitly with
  `-m benchmark`. Baselines are machine-local (`.benchmarks/`) — before/after must be same-machine.
- The `# pyright: reportUnknown*=false` header on `dem.py` already relaxes the rasterio/shapely/networkx
  external boundary; keep it (numpy is typed, so the new array code stays clean under it).

### Project Structure Notes

- **Modified:** `src/steeproute/pipeline/dem.py` (`sample_elevation` internals only — signature, docstring
  contract, and the `attach_elevation` call site in `pipeline/__init__.py:215` are unchanged), and
  `tests/unit/test_dem.py` (one new bit-equality test).
- **Untouched:** `attach_elevation` / `run_setup_stages` / `build_graph_geometry`
  (`pipeline/__init__.py`), the `_assert_finite_elevations` guard, `cache.py`, all query-side stages,
  solver, validator, output, CLI surface, `_PIPELINE_CONTENT_GLOBS`, the committed fixture caches.
- **No architecture-doc update required** for this story (unlike 13.2's Cat 4c) — the on-disk format and
  the in-memory contract are both unchanged. Only touch `architecture.md` if you adopt row-band windowing
  (you should not, per AC #3).
- Out of scope: Story 14.2 (polyline smoothing/resampling/metrics + graph-churn — the *batched*
  content-hash change), 14.3 (parallel DEM fetch), 14.5 (osmnx CPU). Do not drift into stage 3/4/7 loops
  even though they share the "per-edge Python loop" shape — 14.2 co-lands them as one cache-invalidation
  cycle on purpose. This story is stage 5 only.

### References

- [Source: epics.md §Epic 14 preamble + §Story 14.1](_bmad-output/planning-artifacts/epics.md) — AC
  source-of-truth, epic framing (r50 goal, vectorize-then-parallelize order, bit-identity guardrail)
- [Source: research/steeproute-next-optimization-pass-handoff-2026-07-05.md §5 item S1 + §2 baseline + §4 constraints](_bmad-output/planning-artifacts/research/steeproute-next-optimization-pass-handoff-2026-07-05.md)
  — the 215 s @ r20 / 65 µs-per-point measurement, the exact "how" recipe (ragged collect → vectorized
  transform → inverse-affine rowcol → fancy-index → masks → first-offender error), r50 memory (~1.6 GB),
  content-hash + bit-identity rules, and the "avoid np.sum silently replacing sum()" war story
- [Source: src/steeproute/pipeline/dem.py:46-178](src/steeproute/pipeline/dem.py) — `sample_elevation`,
  the function this story rewrites internally (all pre-loop guards to preserve verbatim)
- [Source: src/steeproute/pipeline/__init__.py:201-217](src/steeproute/pipeline/__init__.py) —
  `attach_elevation`, the unchanged call site (stage-5 seam + finite guard)
- [Source: tests/unit/test_dem.py](tests/unit/test_dem.py) — the correctness net; landmark + Lambert-93 +
  bounds/nodata edge cases the rewrite must keep passing
- [Source: tests/benchmarks/test_setup_stages.py:68-71](tests/benchmarks/test_setup_stages.py) +
  [tests/benchmarks/conftest.py](tests/benchmarks/conftest.py) — `test_stage5_sample_elevation` already
  exists; the autosave/compare baseline workflow (README "Performance benchmarks")
- [Source: src/steeproute/errors.py:36](src/steeproute/errors.py) — `DEMCoverageError` (a
  `PreExecutionError`), constructor `(user_message, *, detail)`; `.user_message` is what tests assert on
- [Source: src/steeproute/cache.py:60](src/steeproute/cache.py) — `_PIPELINE_CONTENT_GLOBS`
  (`("pipeline/**/*.py", "models.py")`); [cache.py:1151](src/steeproute/cache.py) `check_coverage`
  selects fixtures by containment (why goldens are read fine despite the hash shift)
- [Source: _bmad-output/implementation-artifacts/13-2-faster-cache-entry-deserialization.md](_bmad-output/implementation-artifacts/13-2-faster-cache-entry-deserialization.md)
  — the ragged-array (flat coords + per-edge offsets) collection pattern this story reuses for coordinate
  gathering
- [Source: _bmad-output/implementation-artifacts/13-1-vectorize-query-side-elevation-smoothing.md](_bmad-output/implementation-artifacts/13-1-vectorize-query-side-elevation-smoothing.md)
  — the bit-equality-via-scalar-reference test pattern; the `np.sum` vs compensated `sum()` ULP-drift war
  story (not expected here since sampling has no summation, but the "prove bit-equal before deleting" discipline applies)
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-06-setup-solver-scaling.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-06-setup-solver-scaling.md)
  — the correct-course that inserted Epic 14

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Gates (all green):**

```
tests/unit/test_dem.py                        → 21 passed (20 pre-existing unmodified + 1 new bit-equality)
tests/e2e/test_pinned_regressions.py          → 4 passed (fast tier, byte-identical goldens)
tests/e2e/test_pinned_regressions.py -m slow  → 4 passed (realistic tier, byte-identical goldens)
pytest --cov (default markers)                → 850 passed, 12 deselected in 4:40 (96% cov;
                                                 pipeline/dem.py 97%, above the 95% pure-logic gate)
ruff check src tests                          → All checks passed!
ruff format --check src tests                 → 105 files already formatted
basedpyright (whole project)                  → 0 errors, 0 warnings, 0 notes
```

**Stage-5 benchmark (grenoble_small fixture, same machine, autosave/compare):**

```
before (0006):  min 973.72 ms / median 1032.97 ms / mean 1032.09 ms
after  (0007):  min  51.44 ms / median   58.46 ms / mean   68.55 ms
                → ~17.7× (median), ~18.9× (min)
```

### Completion Notes List

**Vectorized sampling (Task 3, AC #1).** `sample_elevation` keeps its exact contract — same
`(lat, lon, elev)` axis order, same CRS projection, same nearest-pixel selection, same fail-fast
`DEMCoverageError` semantics, same `list[tuple[float, float, float]]` in-memory output. Only the mechanism
changed: one pass gathers every edge's coords into flat `lons`/`lats` arrays + per-edge offsets (the 13.2
ragged pattern), the whole graph is projected in one `transformer.transform` call, pixel indices come from a
single `rasterio.transform.rowcol` call, and elevations are fancy-indexed off one `dataset.read(1)`. The
per-vertex bounds and nodata checks are vectorized masks that still locate the first offending edge (via
`np.searchsorted` on the offsets) and raise the identical message shapes. All pre-loop guards are unchanged.

**Bit-identity by construction — why `rowcol`, not a hand-rolled inverse affine.** The story's suggested
recipe was to replicate rasterio's rounding with `~dataset.transform`'s coefficients. Investigating the
installed rasterio 1.5.0 showed `dataset.sample` computes pixel indices via
`rasterio.transform.rowcol(dataset.transform, xs, ys)` with the default `op = np.floor(...).astype(int32)`,
and the inverse is GDAL's `InvGeoTransform` (via `AffineTransformer._transform` reverse) — *not* Python
`affine`'s `~`. A hand-rolled inverse affine could therefore drift by an ULP from GDAL's arithmetic. Calling
rasterio's own `rowcol` on the full coordinate array reuses the exact code path `sample` uses (it accepts
arrays, chunked to 256 internally), so the resolved rows/cols — and the sampled float32 values off the band
read — are bit-identical by construction. This was confirmed empirically: `test_fixture_pipeline_bit_identical_to_scalar_reference`
asserts `==` (not `approx`) against the verbatim pre-14.1 scalar loop over every vertex of the real
grenoble_small fixture and passes, and all four regression goldens remain byte-identical (no rebake).

**Error-ordering note (behavior-preserving for every realistic input).** The old loop checked all of an
edge's vertices for bounds, then that edge's vertices for nodata, before moving to the next edge. The
vectorized path checks the global bounds mask first, then the global nodata mask. For any single violation —
which is what the unit tests and every real coverage failure exhibit — the reported edge and message are
identical. The two paths could only name a different offending edge for a pathological multi-edge input that
simultaneously has an *earlier* edge with a nodata pixel and a *later* edge with an out-of-bounds vertex;
both still raise `DEMCoverageError` with the same actionable "area not covered" meaning, so this is a
non-issue in practice (and the real fixture has zero violations — the success path is fully bit-equal).

**Goldens safe, no fixture regen (AC #1).** Confirmed the two independent reasons from the story: elevations
are bit-equal (nothing the cache stores changes), and the query-side regression harness reads committed
fixture caches by geometric containment (`check_coverage`), not by re-deriving the pipeline content hash.
`dem.py`'s byte change does shift `compute_pipeline_content_hash()`, but no committed fixture, test, or golden
depends on a real dem.py-derived hash value (the content-hash unit tests use synthetic `"a"*64`). No fixture
caches were regenerated.

**r50 memory decision (Task 5, AC #3).** The implementation uses `dataset.read(1)` — a full-band read.
At grenoble/r20 scale this is a non-issue (r20 DEM ≈ 8000×8000 float32 ≈ 256 MB, comfortably within the
16 GB envelope; the fixture is far smaller). At r50 the band is ~20,000×20,000 float32 ≈ **1.6 GB**
(estimate), a real memory risk. **Decision: accept-and-note for now** — do not add row-band windowing
speculatively. The 14.6 r50 probe must measure real peak RSS on an actual r50 raster and adopt row-band
windowing (group points by row-range, read bands in windows) only if the full-band read binds. This matches
AC #3 ("accepted with a note") and the handoff's "decide by measuring at §8, don't guess."

**Benchmark artifacts.** The autosave run wrote machine-local baseline JSONs under `.benchmarks/`
(`0006_*` before, `0007_*` after). These are machine-local timing data (per `tests/benchmarks/conftest.py`
docstring) and are not listed as source changes below; commit them only if the repo's convention is to
track benchmark history.

**Real-world r20 confirmation (user-run, post-story).** Beyond the `grenoble_small` fixture benchmark,
Yann ran `steeproute-setup` at the r20 scale used throughout the handoff research (`--radius 20`,
`--force-refresh`, same-machine before/after) and read the `elevation-sampling` stage line directly:

```
elevation-sampling:  215.15 s → 16.58 s   (~13.0×)
```

This is the same stage this story targeted, at the same scale the research doc's baseline table used
(215.2 s @ r20). The ~13× real-world gain is somewhat below the fixture's ~17.7× — expected, since
`grenoble_small` is far smaller than a real r20 area and the per-call numpy/pyproj overhead amortizes
differently at that size — but confirms the "minutes to seconds" AC #1 framing holds at the scale that
actually matters for the epic's r50 goal. `dem-resolve` (a separate, network-bound stage in
`pipeline/dem_download.py`, untouched by this story) varied 134.06 s → 752.46 s across the same two runs;
that swing is attributable to IGN Géoplateforme network/server variability between runs, not this change —
`dem-resolve`'s tile-fetch code is Story 14.3's scope, not touched here.

### File List

**Modified:**
- `src/steeproute/pipeline/dem.py` — `sample_elevation` internals vectorized (flat coord arrays + offsets,
  single `pyproj` transform, `rasterio.transform.rowcol` + `dataset.read(1)` fancy-index, vectorized
  bounds/nodata masks with first-offender `DEMCoverageError`); `numpy` and `rasterio.transform` imports
  added. Signature, docstring contract, and the `attach_elevation` call site are unchanged.
- `tests/unit/test_dem.py` — new `_scalar_reference_sample_elevation` (verbatim pre-14.1 oracle) +
  `test_fixture_pipeline_bit_identical_to_scalar_reference` (bit-equality proof over the real fixture).
- `_bmad-output/implementation-artifacts/14-1-vectorize-elevation-sampling.md` — this file.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions (epic-14 →
  in-progress, 14-1 → in-progress → review).

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-06 | Yann (Claude Opus 4.8) | Story 14.1 implemented: setup stage-5 `sample_elevation` vectorized (flat-array coord gather, one `pyproj` transform, `rasterio.transform.rowcol` + full-band fancy-index, vectorized bounds/nodata masks). Bit-identical to the scalar path — proven over every vertex of the grenoble_small fixture via a scalar-reference oracle test, and all 4 regression goldens byte-identical (no rebake). Stage-5 benchmark median 1032.97 → 58.46 ms (~17.7×). r50 full-band-read memory (~1.6 GB estimate) accepted-and-noted, windowing deferred to the 14.6 probe. Gates green (ruff, basedpyright 0/0/0, 850 passed, 96% cov). |
