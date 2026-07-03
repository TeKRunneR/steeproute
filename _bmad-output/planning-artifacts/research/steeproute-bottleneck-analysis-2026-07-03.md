# steeproute Bottleneck Analysis — Ranked List & Phase-3 Recommendation

**Date:** 2026-07-03
**Author:** Yann (Story 11.2)
**Answers:** `technical-steeproute-performance-tuning-research-2026-07-02.md` Phase 1 — the decision question that scopes Phase 3+.

---

## TL;DR

The GRASP solver is **~94% of query wall-clock**, and its time is **pure Python, not native code and not networkx algorithms**. The decision question — scoring math vs. networkx calls vs. loop skeleton — resolves to: **the bespoke loop skeleton plus per-step object churn dominates**. numpy vectorized math is 0.0% of samples; the only native time is ~13% of scalar `Generator.integers` calls (boundary overhead, not compute). networkx costs ~20%, but as *adjacency-view construction machinery* inside the RCL builder, not graph algorithms — so **rustworkx is not indicated**.

**Phase-3 recommendation: pure-Python data-structure fixes first** (precomputed static adjacency, incremental route metrics, batched RNG), with an estimated 2.5–4× headroom. If that plateaus short of the target, the Phase-4 branch is **extract-interface-first → PyO3 kernel** (the learning-value path), *not* rustworkx.

The setup pipeline is **~81% network wait** — already visible per-stage thanks to Story 11.1, and a low-priority optimization target (one-time cost per area).

---

## Provenance

| | |
|---|---|
| Commit | `070debf` + Story 11.1 changes (uncommitted at capture time) |
| Machine | Intel Core Ultra 7 155U, 32 GB RAM, Windows 11 Pro, Python 3.13.14 |
| Area | Chamrousse (quickstart area): setup `--center 45.12,5.88 --radius 6.5`, query `--radius 6.0` |
| Query params | `--seed 42 --n 3 --difficulty-cap T4 --iter-budget 200000 --time-budget 36000 --elevation-deadband 1 --j-max 0` (the gallery quality recipe) |
| Profiler | py-spy 0.4.2, 100 Hz sampling, `--subprocesses`, run-to-completion capture |

Three solver captures (same seed → deterministic workload):

| Capture | Stagnation | Outcome | Samples | Artifact |
|---|---|---|---|---|
| Spec run | `10000` (AC spec) | converged at ~47.5k iters, 17.28 s | 1,994 | [profiling/grasp-flamegraph-spec.svg](profiling/grasp-flamegraph-spec.svg) |
| Steady-state | `0` (disabled) | budget-exhausted, full 200k iters, 64.11 s | 6,506 | [profiling/grasp-flamegraph-200k.svg](profiling/grasp-flamegraph-200k.svg) |
| Raw data | `0` (disabled) | same workload, collapsed-stack format | 7,334 | [profiling/grasp-200k.collapsed](profiling/grasp-200k.collapsed) |

All percentages below are from the **raw 200k capture** (7,334 samples over ~64 s; constant wrapper frames stripped from the committed file). The spec-run flamegraph shows the same shape — the steady-state capture just has better statistics. Throughput reference: **~0.30 s per 1k GRASP iterations** (~3,300 iter/s) on this graph — note the Saint-Nizier gallery sidecar implies ~660 iter/s on its larger graph, so absolute throughput varies ~5× with area; the *distribution* below is what transfers.

---

## Solver — ranked bottleneck list

Phase split: **solver 94.1%**, load/render 5.9% (imports 2.4%, cache read ~1%, query-side elevation smoothing ~1%, output render <1%).

Within the run (% of total samples; cum = function + everything it calls):

| # | Bottleneck | Cum % | Attribution |
|---|---|---|---|
| 1 | **RCL construction** — `_build_rcl` ([grasp.py:453-484](../../../src/steeproute/solver/grasp.py)) | **57.5%** | Pure Python. Breakdown below. |
| 1a | └ networkx `out_edges(...)` view machinery — `reportviews.__call__`/`__init__`/genexpr + `__contains__`/`nbunch_iter` | ~18–19% | Pure Python (networkx has no native core). View objects are **re-created on every `_build_rcl` call**, ~1M+ times per run. |
| 1b | └ per-edge loop bookkeeping (`_build_rcl` self: candidate filtering, set ops, dict lookups) | 20.9% (self) | Pure Python. |
| 1c | └ `Edge` dataclass construction (grasp.py:468) | 9.6% | Pure Python (`dataclass __init__`). **The same static graph edges are re-wrapped into fresh `Edge` objects on every visit.** |
| 1d | └ reuse blocking-set math — `blocking_ids` + `base_segment_ids` | 5.5% | Pure Python frozenset ops, recomputed per visit from edge data. |
| 1e | └ feasibility sort + key lambda (grasp.py:481-482) | ~3.5% | Sorts *all* feasible edges every step, keeps `RCL_SIZE`. The sort key is static per edge. |
| 2 | **Scalar RNG draws** — `Generator.integers` at [grasp.py:400](../../../src/steeproute/solver/grasp.py) (per-step choice) + :391 (start node) | **~13.3%** | **The only native time in the profile** — and it's per-call numpy boundary overhead (one scalar draw per walk step), not vectorized compute. |
| 3 | **θ-prefix finalization** — `_best_theta_prefix` → `_route_slope_ok` → `route_avg_gradient` | **10.6%** | Pure Python. `route_avg_gradient` **re-sums the whole prefix** (two generator passes over all edges) for each prefix checked — quadratic in walk length. |
| 4 | **Distinctness** — `TopNTracker.consider` → `jaccard_distance` → `_canonical_edge_set` | **7.1%** | Pure Python. `_canonical_edge_set` is recomputed per pairwise comparison, including for already-held solutions. |
| 5 | Load/render phase (everything outside `solver.run`) | 5.9% | Imports, cache deserialization (shapely `from_wkb`), query-side smoothing stages 6–7. |

**Python-vs-native attribution:** native ≈ 13–14% (item 2, overhead-shaped); pure Python ≈ 86%; numpy vectorized math **0.0%** (1 sample of 7,334); networkx *algorithms* 0.0% (item 1a is data-structure views only — no shortest-path/traversal algorithm calls exist in the hot path). No Scalene/WSL2 pass was needed: the flamegraphs leave no ambiguity, since every hotspot resolves to named pure-Python frames and the single native contributor is line-pinpointed.

**Decision question answered:** it is the **loop skeleton** case (with heavy per-step object churn), not the scoring-math case (there is no batchable dense math — "scoring" is greedy per-edge comparisons) and not the networkx-algorithms case.

---

## Setup — per-stage breakdown (cold cache, live network)

One real cache-miss run, Chamrousse 6.5 km, captured via Story 11.1's stage timeline ([raw](profiling/setup-timeline.txt) / [timestamped](profiling/setup-timeline-timestamped.txt)). Total **54.01 s**:

| Stage | Elapsed | Share | Network vs CPU |
|---|---|---|---|
| osm-download | 22.39 s | 41.5% | **Network** — single blocking Overpass request (includes osmnx response-parse/graph-build CPU, not separable at this seam). 22 s was a good day; Overpass variance up to minutes is service-side and irreducible. |
| trail-filter | 0.17 s | 0.3% | CPU |
| polyline-smoothing | 0.56 s | 1.0% | CPU |
| resampling | 1.17 s | 2.2% | CPU |
| dem-resolve | 21.73 s | 40.2% | **Network-dominated** — 4 WMS tiles fetched serially (~13 s, ~2 s, ~4 s, ~3 s incl. mosaic + GeoTIFF write; the first tile carries session/first-request overhead). |
| elevation-sampling | 7.46 s | 13.8% | CPU — rasterio point sampling. |
| cache-write | 0.39 s | 0.7% | CPU/disk |
| **Total** | **54.01 s** | | **≈ 81% network / ≈ 19% CPU** |

Setup conclusions: the pipeline is network-bound and runs **once per area** — a low-value optimization target relative to the solver. If it's ever wanted: concurrent DEM tile fetch would cut `dem-resolve` toward its slowest tile (bounded gain, ~2× on this area, more on large areas with ~25 tiles); the osmnx HTTP cache (fixed persistent in Story 11.1) already removes the Overpass wait for repeat setups of overlapping areas. `elevation-sampling` (7.5 s) is the only CPU stage worth a look (windowed/batched raster reads), and only after solver work.

---

## Phase-3 recommendation

Following the research's decision tree: the "loop skeleton dominates" branch — but with an unusually rich cheap-wins layer first, because the churn is *data-structure* waste, not algorithmic necessity. Recommended order:

1. **Precompute static per-node adjacency** (targets items 1a, 1c, 1d, 1e ≈ **35–40%** of the run). The contracted graph is immutable during a solve. Build once per `run()`: for each node, a pre-sorted tuple of `(Edge, blocking_frozenset, sac_rank, descent_over_cap)` records. `_build_rcl` becomes: iterate a plain tuple, filter on `used_directed`/`used_segments`/cap, truncate to `RCL_SIZE` — no networkx views, no `Edge` re-construction, no re-sorting, no `blocking_ids` recompute. Behavior-identical (same candidates, same order) → **no golden impact**.
2. **Incremental θ-prefix metrics** (targets item 3 ≈ **10%**). Maintain running `Σlength / ΣD+ / ΣD−` while scanning prefixes instead of re-summing each one. Keep the canonical `route_avg_gradient` as the final acceptance gate so admitted values stay bit-identical to the validator's (the models.py docstring contract) → no golden impact if the incremental sums are used only to *select* the prefix and the gate re-checks it.
3. **Cache `_canonical_edge_set` per held solution** (targets item 4 ≈ **4–5%**). Held solutions are immutable; their canonical sets can be computed once at insertion. No golden impact.
4. **Batch RNG draws** (targets item 2 ≈ **13%**) — **last, because it changes the draw sequence**: any deviation from one-scalar-draw-per-step changes seeded outcomes and forces a golden rebake (Story 9.3 reconciliation precedent). Do it only bundled with a planned rebake, or accept the ~13% as the price of golden stability.

Estimated combined headroom **≈ 2.5–4×** in pure Python (items 1–3 alone ≈ 2×–2.5×, no rebake required). Re-profile and re-benchmark (Story 11.3 baselines) after each item.

**Phase-4 branch, if needed:** extract-interface-first (isolate `_construct_one`/`_build_rcl` behind a flat-array interface — item 1 above is 80% of that refactor anyway), then a **PyO3 `steeproute-core` kernel** for the construction loop. **rustworkx is explicitly not indicated**: no measured time sits in graph algorithms, and item 1 removes the graph-*structure* overhead without a library migration. numpy batch scoring is likewise not indicated — there is no dense scoring math to batch.

Caveats: single machine, single area (Chamrousse ~3,300 iter/s; larger graphs run ~5× slower per iteration but the profile shape should transfer — worth one confirming capture on a larger area before committing to Phase-4-scale work); 100 Hz sampling puts ±≈1% noise on the percentages.

---

## Artifacts

- [profiling/grasp-flamegraph-200k.svg](profiling/grasp-flamegraph-200k.svg) — steady-state flamegraph (full 200k iterations, primary evidence)
- [profiling/grasp-flamegraph-spec.svg](profiling/grasp-flamegraph-spec.svg) — AC-spec run (stagnation 10k, converged) for comparison
- [profiling/grasp-200k.collapsed](profiling/grasp-200k.collapsed) — raw collapsed stacks (py-spy `--format raw`, wrapper frames stripped) — source of all percentages; re-aggregate with any flamegraph tool
- [profiling/setup-timeline.txt](profiling/setup-timeline.txt) / [setup-timeline-timestamped.txt](profiling/setup-timeline-timestamped.txt) — verbatim cold-cache setup capture (Story 11.1 stage seam output)
