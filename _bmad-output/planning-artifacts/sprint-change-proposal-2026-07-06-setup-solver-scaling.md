# Sprint Change Proposal — Setup + Solver Scaling toward r50

**Date:** 2026-07-06
**Trigger:** Epic 13 closed; the next-optimization-pass handoff (`research/steeproute-next-optimization-pass-handoff-2026-07-05.md`) delivered a measured r20 baseline and a ranked setup-side + solver-parallelism lever list aimed at the whole-alpine-range ambition
**Mode:** Batch
**Scope classification:** Moderate (backlog addition — new epic, no existing-work changes)

---

## 1. Issue Summary

Epic 13 optimized the query-side compute share the Epic-12 solver work exposed, and closed with the query pipeline in good shape at test-budget scale (r6/r10). But every optimization epic so far anchored on r6/r10, and two blind spots survive that only bite at the scale that is the *actual* goal — whole alpine ranges (r50–100), with **setup + query ≤ 10 min at r50** as the aspirational design target:

1. **"Setup is network-bound, low priority" is wrong at scale.** The measured r20 trace (`research/steeproute-next-optimization-pass-handoff-2026-07-05.md` §2) puts setup at **761 s**, of which **~338 s (44%) is CPU-side processing**, not network — elevation-sampling 215 s, resampling 62 s, smoothing 33 s, trail-filter 18 s, plus ~141 s of osmnx CPU hidden inside the osm-download stage. Naive r20→r50 extrapolation (×6.25, estimate) puts setup CPU processing near ~35 min. Prior epics never looked because they measured r6/r10.
2. **Everything runs at ~7% CPU.** The whole program — both CLIs, every stage — is single-threaded CPython: one busy logical core out of 14 on the reference machine. The solver alone is ~53 s single-core at 1M iters.

The handoff resolves the mechanism for each hotspot (per-point rasterio overhead, per-vertex Python loops, copy-then-remove graph churn, sequential tile fetch, osmnx materializing then re-collapsing 806k nodes, single-core GRASP) and prescribes two remedies in order: **vectorize the per-vertex Python loops first** (simpler, deterministic, likely sufficient for pipeline stages), then **multi-core parallelism via processes** where work stays expensive or can't be vectorized (GRASP restarts, DEM fetch). The sprint plan has no epic for this; this proposal promotes the handoff's **definite, measured** work into **Epic 14** and defers its **explicitly-gated deep work** to a post-probe correct-course.

Decisions recorded during this correct-course (Yann, 2026-07-06):

1. **Epic 14 scope = definite work + the r50 probe** — S1–S4, Q1, Q2, Q3, the cheap S5 levers, the §8 r50 probe, and a re-measure close-out.
2. **The gated deep work is deferred, not planned now.** The custom Overpass→graph parser (S5-deep), the schema-v3 numpy-array edge contract (Q4), and per-stage multiprocess parallelization (§7 step 6) are the handoff's own "do NOT start here / decide at the §8 probe" items. They route through a follow-on correct-course once the probe supplies the residuals that justify them — mirroring the Epic 12 → 13 → 14 rhythm.
3. **Story 13-4 (lazy imports) stays parked.** It is a small-area *interactive* win (~3–5 s constant per process), orthogonal to the r50 goal per the handoff §6 Q5. Not folded into Epic 14. Story 13-5 (re-measure) is subsumed by Epic 14's close-out (14.6).

## 2. Impact Analysis

- **Epic impact:** Additive only. Epics 1–13 all `done` and untouched; backlog is empty (story 8-5 stays deferred). New Epic 14 appended; no renumbering. Epic 13's deferred tail is reconciled: **13-5 is subsumed by 14.6**; **13-4 remains parked** (orthogonal small-area interactive item).
- **Story impact:** Six new stories (14.1–14.6); no existing story changes.
- **PRD:** No conflict, no edits. No new FRs — performance work on existing behavior. Supports NFR1 (extends the design target to large areas / r50, still "design target, not an SLO") and preserves NFR4. The new `--workers` knob is a parallelism flag, not an FR — it belongs on the architecture flag surface (precedent: `--stagnation-iters`), and NFR4's contract holds unchanged for its default (`--workers 1`).
- **Architecture:** No upfront edits, but three recorded decisions are touched, carried as doc-sync in story ACs:
  - **Cat 5a (solver parallelism)** — 14.4 *realizes* the parallelism Cat 5a explicitly designed for ("trivially convertible to `ProcessPoolExecutor` later"; RNG "compatible with `SeedSequence.spawn()` for future parallel streams"). Update from conditional-future to realized; record the `(seed, workers)` determinism contract and the worker-order merge.
  - **Flag surface table** — add `--workers` (Cat 5a). Total stays within the ~25 config-file-reconsideration threshold.
  - **Cat 3 (pipeline) / Cat 8 (progress)** — 14.3 makes DEM tile fetch concurrent (`ThreadPoolExecutor`); progress stays `tile i/N` on completion, output completion-order-independent. Light note that setup gains its first concurrency beyond the cache-write atomics.
  - No on-disk cache format change in this epic (Q4's schema-v3 contract is deferred); on-disk format (Cat 4c) is untouched unless an S5 rebake forces it, in which case 14.5 records it.
- **UX:** N/A (CLI-only).
- **Technical/secondary:**
  - **Bit-identity is the default target** for S1/S2/Q2 (assert new == old over every vertex of the `grenoble_small` fixture before deleting the old path). Watch builtin `sum()` compensated-summation parity (13.1 war story) at the flagged call sites.
  - **One contingent golden rebake** is possible at 14.5 (S5 cheap levers can shift osmnx ingestion output subtly); if so, one batched rebake via `update-regression` with the equivalence argument recorded — never silent.
  - **Content-hash discipline:** `_PIPELINE_CONTENT_GLOBS = ("pipeline/**/*.py", "models.py")` — any byte change re-keys all caches and stales the four committed fixture roots (grenoble_small, chartreuse, vercors, belledonne; regen scripts exist). Stories are batched to minimize regen cycles: 14.2 deliberately co-lands S2+S3+Q2+Q3 as **one** content-hash change; 14.4 plumbs `--workers` at the CLI layer so it touches neither `models.py` nor `pipeline/` and invalidates nothing.
  - **Verification protocol:** add a `tests/benchmarks/` per-stage benchmark *before* optimizing each stage; extend the r20 baseline table rather than re-measuring from scratch. The **r50 probe (14.6)** is the new measurement anchor — every "unknown — measure" in the handoff funnels there.

## 3. Recommended Approach

**Direct Adjustment** — add Epic 14 within the existing plan. Effort: Low (planning) / Medium–High (implementation — 6 stories, two of them large, and the project's first multi-core work). Risk: Low–Medium — bit-identity guardrails and `--workers 1`-default keep goldens and NFR4 intact; the residual risks are all flagged with mitigations in the handoff: (a) an osmnx-ingestion golden rebake at 14.5, (b) IGN Géoplateforme throttling under concurrent DEM fetch (14.3 tests at r20, backs off on 429), (c) parallel-determinism plumbing (14.4 uses `SeedSequence.spawn` + worker-order merge, default off). Rollback and MVP-review paths are not applicable: nothing shipped is wrong, and this is a post-v1 increment.

Story order is load-bearing and follows the handoff §7: **14.1** (self-contained biggest single win) → **14.2** (the one batched content-hash change) → **14.3** (independent; network-etiquette test) → **14.4** (solver parallelism; no content-hash touch) → **14.5 + 14.6** (cheap S5 levers, then the r50 probe that the deferred deep decisions consume). Plausible combined effect per the handoff (all labeled estimate, none demonstrated until 14.6): setup CPU processing from tens of minutes toward ~2–4 min, solver throughput ×4–6 on the reference machine, the 10-minute r50 goal "plausible but not demonstrated."

## 4. Detailed Change Proposals

### 4.1 `epics.md` — append Epic 14 section (under "Active / future epics")

```markdown
## Epic 14: Setup + Solver Scaling toward r50

Every prior optimization epic anchored on r6/r10; the measured r20 baseline in
`research/steeproute-next-optimization-pass-handoff-2026-07-05.md` shows two blind spots that only
bite at the whole-alpine-range goal (r50–100, design target: setup + query ≤ 10 min at r50).
(1) Setup is NOT network-bound at scale — ~44% of the 761 s r20 setup is CPU (elevation-sampling
215 s, resampling 62 s, smoothing 33 s, trail-filter 18 s, plus ~141 s of osmnx CPU inside
osm-download). (2) The whole program is single-threaded CPython (~7% of a 14-core machine). Two
remedies, in order: vectorize the per-vertex Python loops (deterministic, likely sufficient for the
pipeline stages), then multi-core via processes where work stays expensive or can't be vectorized
(GRASP restarts; threads suffice for DEM fetch I/O). This epic takes the handoff's definite, measured
levers and the r50 probe; the explicitly-gated deep work (S5 custom Overpass parser, Q4 array
contract, per-stage parallelization) is deferred to a post-probe correct-course per the handoff's own
"decide at the §8 probe" guidance. Bit-identity is the default guardrail; content-hash changes are
batched (14.2 co-lands the pipeline vectorization); one contingent documented rebake allowed at 14.5.
Story 13-4 (lazy imports) stays parked (small-area interactive, orthogonal to r50); 13-5 (re-measure)
is subsumed by 14.6. Inserted via correct-course 2026-07-06; no epic renumber.

**FRs covered:** none new — performance work on existing behavior. Supports NFR1 (extends the ≤10-min
design target toward large areas / r50) and preserves NFR4 (seeded determinism; `--workers 1` default
leaves the existing contract unchanged, parallel mode is deterministic per `(seed, workers)`).

### Story 14.1: Vectorize elevation sampling (setup stage 5)

As a user,
I want DEM elevation sampling to stop looping per-point through rasterio in Python,
So that the single biggest setup CPU stage drops from minutes to seconds without changing elevations.

**Acceptance Criteria:**

**Given** `sample_elevation` (`pipeline/dem.py`) costs ~215 s @ r20 — ~65 µs/point of per-point
Python/rasterio overhead over ~3.5 M points (per-edge `transformer.transform` on lists, per-vertex
bounds check, per-point `dataset.sample`)
**When** it is reformulated as flat-array vectorized work (one ragged-array coordinate collection as
in 13.2, one vectorized `pyproj` transform, vectorized inverse-affine rows/cols replicating
rasterio's nearest-pixel/rowcol rounding exactly, fancy-indexed band read, vectorized bounds/nodata
masks, and a `DEMCoverageError` of the same message shape locating the first offending edge)
**Then** sampled elevations are bit-equal to the old path over every vertex of the `grenoble_small`
fixture (verify before deleting the old code) and the regression-golden suite passes untouched
**And** a per-stage benchmark is added before the change; measured stage-5 wall-clock drop is recorded
in the close-out
**And** the r50 full-band-read memory footprint (~1.6 GB at r50, estimate) is either accepted with a
note or handled by row-band windowing — the decision recorded, measured at the 14.6 probe

### Story 14.2: Vectorize + de-churn the per-edge pipeline loops (one content-hash batch)

As a user,
I want the per-vertex smoothing/resampling/metrics loops and the copy-then-remove graph churn
replaced with array ops and single-pass graph builds,
So that the remaining setup and query pipeline CPU drops, landed as one cache-invalidation cycle.

**Acceptance Criteria:**

**Given** polyline smoothing + resampling (S2, ~95 s @ r20), copy-then-remove churn in `filter_trails`
/ orphan / short-edge guards and per-stage `graph.copy()` (S3, trail-filter ~18 s @ r20 + repeated
full-graph copies), and query-side stage-7 metrics + deadband (Q2, part of elevation-reshape ~24 s
@ r20) are all per-edge Python loops over the same edge geometry
**When** they are vectorized per edge via the shapely array interface (moving-average, segment lengths
`np.hypot(np.diff)`, `np.cumsum` with naive-fold parity verified, `np.searchsorted` lerp resampling,
`np.diff`-based gain/loss and windowed-descent replicating the two-pointer boundary semantics), the
copy-then-remove churn is replaced by building a new graph from kept edges (or one
orchestrator-owned working copy), and `contract_climbs` (Q3, 5.6 s @ r20) is profiled first and
optimized only if the profile shows a material extractable cost — all co-landed as a **single**
content-hash change with one fixture-regen
**Then** coordinate arrays, edge metrics, and deadband output are bit-equal to the old paths on the
`grenoble_small` fixture (or, where a compensated-`sum` site prevents it, one documented rebake
batched with this story); the full suite including goldens passes; public API purity is preserved at
the `run_setup_stages` / `build_graph_geometry` / `operationalize_graph` boundaries
**And** per-stage benchmarks are added before the change; measured drops for stages 3–4, trail-filter,
and stage-7 metrics are recorded in the close-out

### Story 14.3: Parallelize DEM tile fetch (setup)

As a user,
I want DEM tiles fetched concurrently instead of one urlopen at a time,
So that large-area DEM download wall-clock collapses without changing the assembled mosaic.

**Acceptance Criteria:**

**Given** `_fetch_mosaic` (`pipeline/dem_download.py`) fetches tiles strictly sequentially (~134 s @
r20, 16 tiles; ~14 min @ r50 for ~100 tiles, estimate)
**When** fetching moves to a `ThreadPoolExecutor` (module-constant `max_workers`, start at 4), each
task returning `(y0, y1, x0, x1, bytes)` and the parent validating + writing into the mosaic array —
output completion-order-independent, so the assembled mosaic is byte-identical to the sequential path
**Then** the mosaic is verified identical to the sequential result; `tile i/N` progress still emits on
completion (FR33 stream discipline preserved); IGN Géoplateforme behavior under concurrency is tested
at r20 first, backing off `max_workers` if 429/errors appear (result recorded)
**And** architecture Cat 3/Cat 8 is noted (setup's first fetch concurrency; progress semantics
unchanged)

### Story 14.4: Parallel GRASP restarts (`--workers`, default 1)

As a user,
I want to run independent GRASP restarts across cores,
So that the solver stops pinning one logical core and search quality per wall-second scales with cores.

**Acceptance Criteria:**

**Given** GRASP iterations are independent restarts (embarrassingly parallel; Cat 5a designed the loop
to be `ProcessPoolExecutor`-convertible and the RNG for `SeedSequence.spawn`) and the solver runs
single-core today (~53 s @ 1M iters)
**When** a `--workers N` flag is added (**default 1 = today's exact behavior, no rebake**), plumbed at
the CLI/orchestration layer so it touches neither `SolverParams`/`models.py` nor `pipeline/` (no cache
invalidation); for N>1 a `ProcessPoolExecutor` (Windows-spawn guarded) gives each worker the
contracted graph, `iter_budget // N` (+ remainder to worker 0), and an RNG from
`SeedSequence(seed).spawn(N)[i]`, and results merge through a fresh `TopNTracker` in worker-id then
admission order
**Then** `--workers 1` output is byte-identical to pre-epic (goldens and NFR4 untouched); N>1 output
is deterministic and reproducible per `(seed, workers)`, documented as differing-by-design from N=1;
per-worker startup (spawn + contracted-graph pickle) is measured and reported
**And** architecture Cat 5a is updated from conditional-future to realized, `--workers` is added to the
flag-surface table, and the `(seed, workers)` determinism contract + `--stagnation-iters`/`--time-budget`
per-worker interpretation are recorded

### Story 14.5: Reduce osmnx ingestion CPU — cheap levers only

As a user,
I want the osmnx CPU inside the osm-download stage reduced by the low-risk levers,
So that setup's post-vectorization dominant CPU cost shrinks without a from-scratch parser.

**Acceptance Criteria:**

**Given** ~141 s of the 289 s r20 osm-download stage is osmnx CPU (`simplify_graph` ~54 s, two
truncate + two largest-component passes ~67 s combined, raw-graph build ~15 s), extrapolating to
~15 min @ r50 (estimate), and this becomes the dominant setup CPU cost once 14.1/14.2 land
**When** the cheap levers are investigated in order — whether the bbox→polygon→`truncate_graph_polygon`
double-pass can be reduced to one for plain-bbox input via lower-level osmnx APIs, and whether the
second truncate/component pass is redundant — and any safe reduction is applied
**Then** the assembled graph is bit-identical where the lever is behavior-preserving (verified on
fixtures); if a lever shifts ingestion output, **one** documented golden rebake + fixture regen is
taken with the equivalence argument recorded (never silent), and Cat 4c is noted only if on-disk
content changes
**And** the custom Overpass→graph parser (S5-deep) is explicitly **out of scope** — recorded as a
candidate for the post-probe correct-course, to be justified from 14.6's residuals
**And** `retain_all=True` is not adopted (behavior change: keeps unreachable islands, wastes solver
iterations) without golden evaluation

### Story 14.6: r50 probe, re-measure, and what-next decision

As a developer,
I want one real r50 setup+query run plus a consolidated before/after against the r20 baseline,
So that every "unknown — measure" is resolved and the deferred deep work is scoped from evidence.

**Acceptance Criteria:**

**Given** Stories 14.1–14.5 have landed with per-stage benchmarks and measured drops
**When** I run one real `steeproute-setup --radius 50` + query, recording stage lines, peak RSS,
Overpass behavior (timeout/response size/settings bumps), IGN behavior at ~100 tiles, DEM array
memory, and solver iter/s + parallel speedup on the bigger contracted graph, and produce a fresh r20
trace reconciled against the handoff's baseline table
**Then** a findings update in `research/` records the new r20 and first r50 phase splits, the
cumulative effect vs the 761 s / 100.6 s r20 anchors, and the 10-min-at-r50 goal assessed from
measurement (not extrapolation)
**And** the document closes with an explicit, evidence-based recommendation on the deferred deep work —
S5 custom parser, Q4 array-contract (schema v3), and per-stage multiprocess parallelization — routing
whichever are justified through a follow-on correct-course, or recording a reasoned stop
**And** no production code changes in this story
```

### 4.2 `epics.md` — NFR1 coverage line update

```
OLD:
- NFR1 (compute budget ≤10min design target): Epic 7 — time-budget termination, stagnation, progress
  reporting surfaces elapsed; Epic 11 makes the target measurable (benchmark baselines + per-stage
  timing); Epic 12 raises solver throughput against those baselines; Epic 13 attacks the query-side
  share that dominates large-area whole-execution wall-clock post-Epic-12

NEW:
- NFR1 (compute budget ≤10min design target): Epic 7 — time-budget termination, stagnation, progress
  reporting surfaces elapsed; Epic 11 makes the target measurable (benchmark baselines + per-stage
  timing); Epic 12 raises solver throughput against those baselines; Epic 13 attacks the query-side
  share that dominates large-area whole-execution wall-clock post-Epic-12; Epic 14 extends the target
  toward large areas (r50 / whole-range) — vectorizing setup CPU stages and adding multi-core GRASP
```

### 4.3 `sprint-status.yaml` — reconcile Epic 13 tail and append Epic 14

```yaml
  # Epic 13 tail reconciled by correct-course 2026-07-06:
  13-4-lazy-imports-on-the-query-path: deferred   # parked: small-area interactive, orthogonal to r50 (Epic 14)
  13-5-re-measure-and-epic-close-out: deferred    # subsumed by 14-6

  epic-14: backlog        # Setup + Solver Scaling toward r50 (correct-course 2026-07-06)
  14-1-vectorize-elevation-sampling: backlog
  14-2-vectorize-and-de-churn-pipeline-loops: backlog
  14-3-parallelize-dem-tile-fetch: backlog
  14-4-parallel-grasp-restarts: backlog
  14-5-reduce-osmnx-ingestion-cpu-cheap-levers: backlog
  14-6-r50-probe-re-measure-and-what-next: backlog
  epic-14-retrospective: optional
```

(The 13-4/13-5 lines already exist as `deferred`; only their trailing comments change to point at Epic 14.)

### 4.4 `future-ideas.md` — record the deferred deep work

```
Append under the performance-tuning pointer:

**Deferred to post-probe correct-course (2026-07-06):** three deep levers from
`research/steeproute-next-optimization-pass-handoff-2026-07-05.md` are intentionally NOT in Epic 14 —
the custom Overpass-JSON→graph parser (S5-deep), the schema-v3 numpy-array edge contract (Q4), and
per-stage multiprocess parallelization (§7 step 6). Each is gated on the Epic 14 r50 probe (Story
14.6) supplying the residual costs that justify it; pickup routes through its own correct-course.
```

## 5. Implementation Handoff

- **Scope:** Moderate — backlog addition, no replan. Routed to Developer workflow.
- **Next step:** `create story 14.1` → `dev story` per the normal cadence; stories sequenced 14.1 → 14.2 → 14.3 → 14.4 → 14.5 → 14.6 (order is load-bearing per handoff §7: 14.2 is the batched content-hash change, and 14.6's probe supplies the residuals the deferred deep decisions consume).
- **Success criteria:** bit-identity preserved as the default (one contingent documented rebake allowed at 14.5); a `tests/benchmarks/` per-stage benchmark added before each stage is optimized; content-hash regen cycles kept minimal (14.2 batches the pipeline vectorization; 14.4 invalidates nothing); architecture Cat 5a + flag surface synced at 14.4, Cat 3/8 noted at 14.3; 14.6's findings doc records the first measured r50 phase split and an explicit, evidence-based decision on the deferred deep work.
