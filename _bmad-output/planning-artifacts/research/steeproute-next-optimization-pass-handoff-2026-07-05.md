# Next Optimization Pass — Handoff Plan

**Date:** 2026-07-05
**Author:** Claude (Fable 5) — written as a handoff for a lower-capability implementation agent
**Status:** Plan / not yet an epic. Bring into sprint via correct-course (Epic 13 still has 13-4 lazy-imports and 13-5 close-out in backlog; this plan would form the follow-on epic).

---

## 1. Goal and framing

Target: **setup + query in ≤ 10 minutes at radius 50, excluding download time.** This is an
aspirational design target, not a commitment. r6/r10 are test-budget areas and r20 is merely
what's practical today; the real ambition is whole alpine ranges (r50–100). Prior optimization
epics anchored on r6/r10 and concluded "setup is network-bound, low priority" — that conclusion
is **wrong at scale** (see §2) and is the main blind spot this pass corrects.

Second blind spot: the solver process runs at ~7% CPU on a 12-core machine (Intel Ultra 7 155U,
2P + 8E + 2LPE cores, 14 logical). Everything — setup and solver — is single-threaded today.

## 2. Measured baseline (do not re-measure to start; extend it)

### r20 stage trace (user run, 2026-07-05, center 45.260,5.788)

| Stage | Wall | Kind |
|---|---|---|
| **setup** osm-download | 289.2 s | ~148 s Overpass wait + ~141 s osmnx CPU (measured — see S5) |
| trail-filter | 18.4 s | CPU |
| polyline-smoothing | 32.9 s | CPU |
| resampling | 62.5 s | CPU |
| dem-resolve (16 tiles) | 134.1 s | network, sequential (~8.4 s/tile) |
| elevation-sampling | 215.2 s | CPU (local raster reads) |
| cache-write | 8.9 s | I/O |
| **setup total** | **761.3 s** | CPU-side processing ≈ 338 s (44%) |
| **query** load-prepared-area | 2.6 s | |
| elevation-reshape (6–7) | 24.4 s | CPU |
| trail-filter redux | 5.2 s | CPU |
| climb-detection | 0.7 s | CPU |
| climb-contraction | 5.6 s | CPU |
| solver (1M iters) | ~52.7 s | CPU, single core, ~19k iter/s |
| validate-render | 3.2 s | |
| **query total** | **100.6 s** | |

### r5 cProfile (this session, same center; full stage + function attribution preserved in
`research/profiling/setup-r5-cprofile-2026-07-05.txt` — re-running takes ~1 min at r5)

- Total 51.9 s. osm-download stage 26.6 s, **of which ≥ ~21 s is osmnx CPU, not network**:
  `simplify_graph` 11.4 s, `largest_component` 6.5 s, `truncate_graph_polygon` 4.2 s,
  `_create_graph` 3.7 s (some overlap). The Overpass wait itself was small at r5.
- elevation-sampling 17.2 s: `rasterio sample_gen` 14.1 s cumulative over **217,606 points**
  (~65 µs/point of pure per-point Python/rasterio overhead: per-point window build, crop,
  rowcol, read). This is the mechanism behind the 215 s at r20.
- resampling 4.3 s / smoothing 1.9 s / trail-filter 0.9 s — all pure-Python per-vertex loops.
- `MultiDiGraph.copy`: 11 calls, 6.6 s cumulative (ours + osmnx's internal ones).

### Scaling observed r5 → r20 (bbox area ×16)

trail-filter ×21, smoothing ×17, resampling ×14.5, elevation-sampling ×12.5 — consistent with
~linear in vertex/edge count (≈ r²). Naive r20 → r50 extrapolation (×6.25, **estimate**):
setup CPU processing ≈ 35 min, dem-resolve sequential ≈ 14 min (100 tiles), query non-solver
≈ 4–5 min. That is what this pass must remove.

### Sizes (measured)

- r20 cache entry: `graph.pkl` ≈ 166 MB (r50 ≈ 1 GB on disk, estimate; in-RAM Python-object
  footprint unmeasured — a real risk at r50, see Q4).
- r20 DEM GeoTIFF: 69 MB compressed ≈ 8000×8000 px float32 (256 MB as array). r50 ≈
  20,000×20,000 px ≈ **1.6 GB as a float32 array** — both the download mosaic buffer
  (`_fetch_mosaic` already allocates this) and any full-band read in S1 must account for it.

## 3. Already done / already ruled out — do NOT redo

From Epics 12–13 (see `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md`,
story docs 12-1…13-3):

- Solver: adjacency precompute (12.1), incremental θ-prefix + cached distinctness (12.2),
  batched RNG (12.3) → cumulative 5.7× throughput. Residual is diffuse pure-Python loop work;
  **no extractable sub-hotspot remains** short of compiling or parallelizing the loop.
- Query: vectorized Laplacian smoothing, bit-identical (13.1); cache schema v2 ragged-array
  geometry (13.2); **second-tier query cache = decided no-go** (13.3) — don't re-propose.
- Ruled out: rustworkx (no networkx-algorithm time left), PyO3/Rust kernel *on performance
  grounds* (Amdahl ceiling; learning-value is a separate, still-open rationale), in-solver
  numpy batch scoring (no batchable math).
- Still in Epic 13 backlog: 13-4 lazy imports (~3–5 s/process constant), 13-5 re-measure.

## 4. Cross-cutting constraints (read before implementing anything)

1. **Pipeline content hash.** `cache.compute_pipeline_content_hash()` hashes
   `src/steeproute/pipeline/*.py` + `models.py` source bytes. **Any byte change** to those files
   changes every cache key: all user caches re-prepare once, and the committed fixture cache
   roots (grenoble_small, chartreuse, vercors, belledonne) go stale — tests that read them
   will miss. Regeneration scripts exist (`tests/e2e/fixtures/grenoble_small/regenerate_cache.py`,
   `tests/fixtures/grenoble_small/regenerate*.py`; find the equivalents for the other roots).
   **Batch all pipeline-touching stories into as few content-hash changes as possible** —
   each change costs one fixture-regen cycle in the repo and one ~12-min re-prepare of the
   user's r20 trial cache.
2. **Bit-identity and goldens.** Regression goldens pin route JSONs byte-for-byte. Precedents:
   13.1 achieved bit-identical vectorization by replicating CPython 3.12+'s *compensated*
   builtin `sum()`; 12.3 did one documented golden rebake when the RNG stream changed. Rules of
   thumb for the vectorization work below:
   - Python `total += x` loops and `np.cumsum` are both sequential naive left-folds →
     expected bit-identical (verify with an equality assertion against the old code on the
     grenoble_small fixture before deleting the old path).
   - builtin `sum(iterable)` since 3.12 is Neumaier-compensated → `np.sum` (pairwise) does
     **not** match it; either replicate compensation (13.1 pattern) or accept a documented rebake.
   - Prefer bit-identical where cheap. Where it isn't, do ONE batched rebake with the fixture
     regen, and say so in the story doc. Never rebake silently.
3. **Determinism (FR29/NFR4).** Nothing may introduce RNG or ordering dependence outside the
   seeded generator. For solver parallelism see Q1's specific scheme.
4. **Verification protocol.** Per-stage stage lines (`StageProgress`) are the coarse metric;
   `tests/benchmarks/` (pytest-benchmark, `uv run pytest tests/benchmarks -m benchmark`,
   autosave/compare workflow in README) pins per-stage CPU baselines on committed fixtures.
   Add a benchmark for any stage you optimize *before* optimizing it. Full suite ~4:15;
   markedly slower means a test is hitting the network.
5. `uv` on Windows: after a commit or `pyproject.toml` edit, run `uv sync --native-tls` once,
   then `uv run --no-sync ...` (corporate-TLS build flake).

## 4b. Why 7% CPU everywhere, and the two remedies

The whole program — both CLIs, every stage — is single-threaded CPython: one busy logical
core out of 14 ≈ 7%. Setup behaves the same (7% during CPU stages, ~0% during network waits).
Two distinct remedies, and the order matters:

1. **Vectorization** (S1, S2, Q2): rewrite per-vertex Python loops as numpy array ops. Still
   one core, but the loop body moves into C — typically 10–100× on exactly these shapes. This
   is the first move for all pipeline stages because it's simpler, deterministic, and likely
   sufficient there.
2. **Multi-core parallelism** — processes, not threads (the GIL serializes Python bytecode;
   threads only help for I/O waits). Reserved for work that stays expensive after
   vectorization or *can't* be vectorized:
   - the GRASP loop (Q1): a sequential stochastic graph walk with data-dependent branching —
     not vectorizable, but restarts are independent → near-ideal multiprocess shape;
   - DEM tile fetch (S4): network wait → plain threads suffice;
   - pipeline stages are per-edge independent, so they can also be chunked across processes.
     This is a **when/what decision, not a whether**: at r50 the vectorized stages are
     expected to be small next to osmnx CPU and the solver (Amdahl — parallelize the biggest
     serial block first), but the r100 ambition makes even vectorized stages minutes again.
     Decide per stage at the §8 probe from the measured post-vectorization residuals. Known
     costs, neither prohibitive: worker memory duplication (N workers × chunk + parent graph —
     bites exactly when r100 is memory-tight; mitigate with `multiprocessing.shared_memory`
     for the flat arrays) and a complexity/debuggability tax per parallelized stage.

Do not parallelize the current Python loops directly: that multiplies a slow thing by ~6×
where vectorization replaces it with a ~30× faster thing — and the parallel harness would be
rebuilt anyway, since chunking numpy arrays looks nothing like chunking edge-dict loops.

## 5. Work items — setup side

### S1. Vectorize elevation sampling (stage 5) — biggest single win

**Where:** `src/steeproute/pipeline/dem.py` `sample_elevation` (per-edge `transformer.transform`
on Python lists, per-vertex Python bounds check, per-point `dataset.sample`).
**Measured cost:** 215.2 s @ r20; ~65 µs/point × ~3.5 M points (r20, estimated from r5's 218k).

**How:**
1. One pass over edges collecting all coords into flat numpy `lons`/`lats` arrays + per-edge
   offsets (same ragged pattern as 13.2 / `graph_smooth_elevation`).
2. One vectorized `pyproj.Transformer.transform(lons, lats)` call for the whole graph.
3. Rows/cols via the inverse affine transform, vectorized; replicate `rasterio`'s
   `dataset.sample` nearest-pixel semantics **exactly** (floor of inverse-affine; check
   `rasterio.transform.rowcol` rounding — op `math.floor`). Values by fancy-indexing a band
   read.
4. Bounds + nodata checks as vectorized masks; on violation, locate the first offending index
   and raise `DEMCoverageError` with the **same message shape** (find the owning edge from the
   offsets array).
5. Memory: full-band read is 256 MB @ r20 (fine) but ~1.6 GB @ r50. Either accept and document,
   or read in row-band windows after grouping points by row range. Decide by measuring on a
   real r50 raster (see §8) — don't guess.

**Verify:** assert new elevations bit-equal old ones over every vertex of the grenoble_small
fixture; stage benchmark before/after. Expected result: stage drops from minutes to seconds
(estimate — the ~65 µs/point overhead is >95% of the stage; vectorized transform + indexing of
a few million points is sub-second each).

### S2. Vectorize polyline smoothing + resampling (stages 3–4)

**Where:** `src/steeproute/pipeline/smoothing.py` `_moving_average`, `_resample_meters` (and
`pipeline/__init__.py` `_polyline_length_m`).
**Measured cost:** 32.9 + 62.5 s @ r20 (plus `_drop_short_edges` 1.7 s @ r5 inside resampling).

**How (per edge, numpy):** extract coords via `shapely` array interface
(`np.asarray(geom.coords)` — avoid the per-point Python tuple round-trip), window-3 moving
average as array slices, projection as array multiply, segment lengths `np.hypot(np.diff(...))`,
cumulative via `np.cumsum` (naive-fold parity, verify), interior sample positions via
`np.searchsorted` + vectorized lerp replicating the existing clamp. Watch the two builtin-`sum`
call sites (`mean_lat`, `_moving_average` window mean) — compensated-sum parity issue (§4.2).
Rebuild `LineString`s via `shapely.LineString(ndarray)` or batched `shapely.linestrings(...)`.

**Verify:** coordinate arrays bit-equal (or documented rebake batched with S1/S3); benchmarks.

### S3. Eliminate copy-then-remove graph churn

**Where:** `filter_trails` (`pipeline/osm.py:141`), `_drop_orphan_nodes` /
`_drop_short_edges` (`pipeline/__init__.py`), same pattern query-side (Q3).
**Measured cost:** trail-filter 18.4 s @ r20 is dominated by `graph.copy()` + edge removal;
each full-graph copy measured ~0.6 s at r5 scale (~×16 at r20).

**How:** build a new `MultiDiGraph` from the *kept* edges instead of copying everything then
removing; share edge-data dicts (read-only convention already used by `contract_climbs` —
document it). Fold orphan/short-edge guards into the producing stage's single pass, or let the
orchestrator own ONE working copy that internal stages mutate (public API purity preserved at
the `run_setup_stages`/`build_graph_geometry` boundary). Stages 3/4/5 also each `graph.copy()`;
with an orchestrator-owned working graph those go away too.

### S4. Parallelize DEM tile fetch

**Where:** `pipeline/dem_download.py` `_fetch_mosaic` — strictly sequential `urlopen` per tile.
**Measured cost:** 134 s @ r20 (16 tiles, ~8.4 s/tile). r50 needs ~100 tiles → ~14 min
sequential (estimate). This is download time — outside the 10-min goal — but it's the easiest
big win in total wall-clock.

**How:** `ThreadPoolExecutor` (start `max_workers=4`, module constant), each task fetches one
tile and returns `(y0, y1, x0, x1, bytes)`; parent validates + writes into the mosaic array.
Output array is completion-order-independent → deterministic. Keep `tile i/N` progress on
completion. **Unknown:** IGN Géoplateforme throttling/fair-use behavior under concurrency —
test at r20 first, back off if 429/errors appear.

### S5. Shrink the osmnx CPU inside the osm-download stage

**Measured (2026-07-05, warm-cache `osm_load` re-run at r20 — Overpass response served from
the osmnx HTTP cache, zero network):** the 289 s cold stage splits into **~141 s osmnx CPU +
~148 s Overpass wait**. The osmnx log timestamps attribute the CPU:

| Phase (osmnx internal) | Wall @ r20 |
|---|---|
| read cached 91 MB response + build 806k-node / 1.64M-edge raw graph | ~15 s |
| truncate to polygon (pass 1: nodes GDF + removal) | ~25 s |
| largest weakly connected component (pass 1) | ~29 s |
| `simplify_graph` (761k → 133k nodes) | **~54 s** |
| truncate pass 2 + largest-component pass 2 | ~13 s |
| **total CPU** | **~141 s** |

Extrapolated ~r²: **~15 min of osmnx CPU at r50 (estimate)** — after S1–S3 land this becomes
the dominant setup CPU cost, so it IS worth attacking. The Overpass wait (~148 s at r20) is
irreducible on our side (public endpoint) and excluded from the goal.

Candidate levers, in order of increasing invasiveness:
- Check whether the bbox → polygon → `truncate_graph_polygon` path (two truncate passes +
  two largest-component passes, ~67 s combined at r20) can be reduced when the input is a
  plain bbox — e.g. call lower-level osmnx APIs (`graph_from_bbox` variants / `truncate`
  module) so truncate/component run once, or prove the second pass is redundant for bbox input.
- `retain_all=True` skips the largest-component passes (~35 s at r20) — **behavior change**
  (keeps disconnected fragments; wastes solver iterations on unreachable islands); not
  recommended without golden evaluation.
- Replace osmnx ingestion with a purpose-built Overpass-JSON → graph parser (payoff ceiling
  measured: ~141 s at r20, est. ~15 min at r50; plus it avoids materializing the full raw
  graph — a memory risk at r100). What this means concretely: keep the same Overpass request
  and HTTP cache; parse the JSON ourselves (node id → coord array; ways → node-id sequences +
  tags); find real intersections (nodes shared between kept ways / way endpoints); split ways
  there and emit each chain directly as an edge with coordinate-array geometry. The measured
  motivation: osmnx builds all 806k nodes as Python graph objects, then its 54 s
  `simplify_graph` collapses ~85% of them back into polylines — but a way is *already* a
  chain, so a direct parser never materializes them; and the ~38 s of GeoDataFrame truncation
  becomes one vectorized bbox mask. Must replicate the contract we rely on: bidirectional
  edges for two-way trails (oneway handling), `sac_scale`/`highway` retention
  (`useful_tags_way`), `osm_way_id` scalar-or-list semantics, edge-key conventions, and the
  largest-component policy. Golden rebake + fixture regen expected (graph content will differ
  subtly). Fully offline-developable against the cached 91 MB response, with osmnx's output as
  the diff reference. Consider seriously if the cheaper levers above don't move the needle.

**r50 risk to check at the §8 probe:** the r20 Overpass response is 91 MB / 806k nodes /
108k ways; at r50 expect ~×6 (estimate) — watch `osmnx.settings.overpass_memory` /
`requests_timeout`, server-side rejection, and parse RAM.

### Not worth touching now (setup)
`cache-write` 8.9 s @ r20 (~56 s @ r50, estimate) — revisit only if it shows up after S1–S3;
a schema change here would collide with Q4 anyway.

## 6. Work items — query side

### Q1. Parallel GRASP restarts (multiprocessing) — the 7%-CPU fix

**Where:** `solver/grasp.py` + `cli/query.py`. GRASP iterations are independent restarts;
this is embarrassingly parallel and was explicitly never pursued (12.4 noted it).

**Design (keep v1 simple):**
- New `--workers N` flag → `SolverParams.workers`, **default 1 = today's exact behavior**
  (goldens and NFR4 untouched; no rebake).
- For N > 1: `ProcessPoolExecutor` (Windows spawn — guard the entry point). Per worker:
  the contracted graph + params with `iter_budget // N` (+ remainder to worker 0) + an RNG from
  `np.random.SeedSequence(seed).spawn(N)[i]`. Each worker runs a normal `GraspSolver.run()` and
  returns `(top_n_solutions, convergence_status, convergence_iteration)`.
- Merge: feed all returned solutions into a fresh `TopNTracker(n, j_max, segment_map)` in
  worker-id order, then by each worker's own admission order → deterministic per
  `(seed, workers)`. Document: results differ from `workers=1` runs by design, but are
  reproducible for fixed `(seed, workers)`.
- `--stagnation-iters` / `--time-budget` apply per worker (document this interpretation).
- Progress: v1 may simply print per-worker throughput on a coarse interval via a
  `multiprocessing.Queue`, or aggregate iteration counts; don't gold-plate.
- **Measure first:** per-worker startup cost = process spawn + pickling the ContractedGraph
  (size unmeasured; it's the contracted graph, far smaller than graph.pkl). If it's seconds,
  it amortizes over any real budget.

**Expected:** near-linear in P-cores, sub-linear on E-cores — actual scaling on the 155U is
**unknown, measure** (guess: 4–6× effective). At r50 this mostly buys *search quality per
wall-second*, since iter budgets will want to grow with area.

**Rejected alternatives:** threads (GIL-bound pure-Python loop), Python 3.14 free-threading
(wheel availability risk for rasterio/GDAL stack; revisit later).

### Q2. Vectorize stage 7 metrics + deadband (finishes what 13.1/13.3 started)

**Where:** `pipeline/climbs.py` `compute_edge_metrics` (`_cumulative_2d_distances`,
`_elevation_gain_loss`, `_max_windowed_descent_grad`), `smoothing.graph_deadband_elevation`.
**Measured cost:** elevation-reshape = 24.4 s @ r20 total. Sub-attribution at r20 is unknown —
add temporary sub-timers first. At r10 (13.3): copy ~3 s, Jacobi ~0.3 s, metrics ~3.3 s.
13.3 explicitly recorded "vectorize `compute_edge_metrics` + `filter_trails`" as the follow-on.

**How:** per edge, verts → `(n,3)` array once; distances as in S2; gain/loss via
`np.diff` + masked `np.cumsum[...][-1]` (bit-parity with the sequential loop — verify);
windowed descent via `np.searchsorted(cum, cum + _DESCENT_WINDOW_M)` replicating the
two-pointer boundary semantics exactly (plus the shorter-than-window fallback). The deadband
transform is sequential hysteresis — leave it Python unless the sub-timers say otherwise.
The `graph.copy()` purity cost inside `operationalize_graph` falls to S3-style handling
(copy once, not three times: smooth → deadband → metrics currently copy each).

**Resolved 2026-07-08 (outside the story sequence, in response to user-observed regression):**
`operationalize_graph` now makes one working copy and threads `graph_smooth_elevation` /
`graph_deadband_elevation` / `compute_edge_metrics` through it via a new keyword-only
`inplace=True` (each function stays pure by default). Measured on a real r20 cache entry
(130k nodes / 323k edges, `--elevation-deadband 1.0`): elevation-reshape 38.6 s → 24.1 s
(saved 14.5 s), verified bit-identical to the old copy-per-stage sequence (0 mismatched
edges) with the input graph still untouched. No golden rebake (bit-identical); pipeline
content hash shifts as usual for a `pipeline/` edit (one-time cache re-prepare). The
deadband hysteresis loop itself is still un-vectorized — that part is unchanged and stays
blocked on the deferred array-edge contract (Q4) per the note above.

### Q3. Query-side copy churn + contraction

`filter_trails` redux (5.2 s @ r20) is fixed by S3 (same function). `contract_climbs`
(5.6 s @ r20, `pipeline/graph.py`) has no profile attribution yet — profile once before
touching; likely suspects are the per-edge `**data` re-dict and `_next_key_for` scans, but
**that's a guess; measure**.

### Q4. Structural option: `vertices_resampled` as numpy arrays end-to-end

Today every edge carries a Python `list[tuple[float, float, float]]` — the root cause of slow
copies, slow pickles, per-vertex loops, and the unmeasured-but-worrying RAM footprint at r50
(166 MB pickle @ r20 → ~1 GB @ r50 on disk; Python-object inflation on top). Switching the
edge contract to `(n,3)` float64 arrays (cache schema v3, fixture regen, touches smoothing /
deadband / metrics / render / validator) makes S2/Q2 trivial and cuts memory severalfold.

**Recommendation:** do NOT start here. Land S1–S3/Q2 first (they don't need it), then run the
r50 measurement (§8); adopt Q4 only if memory or residual copy/pickle cost still binds.
Note 13.2 measured array-based `vertices_resampled` *rebuild-to-lists* as slower — that
finding doesn't apply if arrays become the contract and are never rebuilt into lists.

### Q5. Lazy imports — already story 13-4, unchanged (~3–5 s constant per process; matters for
small-area interactive use, irrelevant to the r50 goal).

## 7. Suggested sequencing

1. **S1** (self-contained, biggest single win).
2. **S2 + S3 (+ Q2, Q3 filter part)** as one batch — same files, ONE content-hash change,
   one fixture-regen, at most one golden rebake.
3. **S4** (independent of everything; network etiquette test at r20).
4. **Q1** solver parallelism. `solver/` and `cli/` are NOT content-hashed
   (`_PIPELINE_CONTENT_GLOBS = ("pipeline/**/*.py", "models.py")`, verified) — but
   `models.py` IS, and adding a `workers` field to `SolverParams` would invalidate every
   cache by itself. Either batch that models.py edit with step 2's content-hash change, or
   plumb `workers` outside `SolverParams` (CLI-layer orchestration needs it, the solver
   doesn't — each worker can receive a plain per-worker `iter_budget`).
5. **S5** (the r20 CPU/network split is already measured — start at the truncate/component
   lever) + **§8 r50 probe** → decide S5-deep (custom parser) and Q4.
6. From the probe's residuals: parallelize whatever is still material (per-stage chunking,
   §4b) — expect this to matter for the r100 ambition even if r50 fits without it — and/or
   the S5 parser.
7. Re-measure epic-close-out style (13-5 pattern): fresh r20 trace + r50 trace, reconcile
   against this document's baseline table.

## 8. The r50 probe (do this once S1–S4 land)

One real `steeproute-setup --radius 50` + query run, recording: stage lines, peak RSS
(Task Manager or `Get-Process`), Overpass behavior (timeout? response size? needs
`osmnx.settings.timeout` / `memory` bumps?), IGN behavior at ~100 tiles, DEM array memory,
solver iter/s on the bigger contracted graph. Every "unknown — measure" above funnels here.
Budget arithmetic if the estimates hold (all labeled estimates, none verified): setup
processing ~2–4 min, dem-resolve ~2–4 min (excluded from goal), query non-solver ~1 min,
solver = user-chosen budget × (4–6× parallel speedup) — the 10-minute goal looks plausible
but is **not demonstrated until this probe runs**.

## 9. Failure modes for the implementing agent to avoid

- Anchoring on r6/r10 numbers and declaring victory — the goal lives at r50.
- Optimizing a stage without a before/after benchmark on committed fixtures.
- Letting `np.sum` silently replace builtin `sum()` (goldens drift by 1 ULP — 13.1 war story).
- Multiple separate pipeline-file edits → repeated fixture-regen/rebake cycles.
- Making `--workers > 1` the default or letting it touch the seeded single-worker path.
- Parallel DEM fetch without checking IGN's response to concurrency.
- Putting unmeasured claims in docs — every number in a story doc is either measured (say
  where) or labeled estimate/unknown.
