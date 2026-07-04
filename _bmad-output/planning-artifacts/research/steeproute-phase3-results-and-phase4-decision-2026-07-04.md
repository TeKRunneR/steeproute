# steeproute Phase-3 Results & Phase-4 Decision — Re-profile, Benchmark Reconciliation, What Next

**Date:** 2026-07-04
**Author:** Yann (Story 12.4)
**Answers:** the Epic 12 closing decision — measured against `steeproute-bottleneck-analysis-2026-07-03.md`'s predictions. Decision scope broadened 2026-07-04: the goal is **whole-execution wall-clock**, not solver throughput.

---

## TL;DR

**Phase 3 over-delivered on its own terms:** solver throughput is **5.6× the Epic 11 baseline** (median, committed HEAD; 5.9× on min) — clearly above the predicted 2.5–4× band. Whole-query wall-clock on the 11.2 reference workload dropped **64.1 s → 12.2 s (5.3×)**.

**But the phase split flipped.** With the solver this fast, the per-query pipeline work — which Epic 12 never touched — now dominates on large areas: on a radius-10 graph the solver is only **~31%** of the run; query-side stages 6–9 plus cache deserialization are **~52%**, imports/startup ~3–5 s more. This is designed behavior (the cache stores stages 1–5 keyed independent of query knobs; everything downstream re-runs per query), newly *visible*, not newly *broken*.

**Decision: no-go on the designated Phase-4 branch (PyO3 solver kernel) on performance grounds.** NFR1 has ~15–50× margin, and a perfect solver kernel is Amdahl-capped at ~1.4× end-to-end on the large-area workload that motivates the whole-execution goal. The next levers are all query-side and all pure-Python/numpy-shaped — **Rust is not indicated anywhere new**. The solver kernel remains the one Rust-shaped target if Rust is ever wanted for learning value (a legitimate, separate rationale). Follow-on work, if any, routes through correct-course.

---

## Provenance

| | |
|---|---|
| Commit | `12c693a` (post-12.3; working tree carried only planning/story docs) |
| Machine | Intel Core Ultra 7 155U, 32 GB RAM, Windows 11 Pro, Python 3.13.14 |
| Profiler | py-spy 0.4.2, 100 Hz, `--subprocesses`, run-to-completion |
| Benchmarks | pytest-benchmark autosaves `0001`–`0005`, `.benchmarks/Windows-CPython-3.13-64bit/` |

Three raw captures (each with a same-seed SVG twin — deterministic workload, 11.2 method):

| Capture | Area / params | Purpose | Wall (profiled) | Samples | Artifact |
|---|---|---|---|---|---|
| A | Chamrousse r6.0, quality recipe (seed 42, n 3, T4, deadband 1, j-max 0), 200k iters, stagnation 0 | **Phase split** at realistic params | 14.84 s | 2,067 | [grasp-post12-chamrousse-200k](profiling/grasp-post12-chamrousse-200k.collapsed) |
| B | same, `--iter-budget 1000000` | **Solver-internal attribution** (statistics ≈ 11.2's 7,334 samples; deliberately overweights the solver — not valid for phase split) | 67.08 s | 7,233 | [grasp-post12-chamrousse-1m](profiling/grasp-post12-chamrousse-1m.collapsed) |
| C | Chartreuse r10 (seed 44, n 10, l-connector 50, smoothing 50, descent-cap 0.4, start-at-junction), 200k iters | **Larger-area confirm** (single-area caveat; the workload where the flip was first observed) | 51.62 s | 6,334 | [grasp-post12-chartreuse-200k](profiling/grasp-post12-chartreuse-200k.collapsed) |

Unprofiled wall-clock references: Chamrousse 200k **12.19 s** (11.2 measured 64.11 s on the identical workload); Chartreuse **40.05 s** (2026-07-04 manual run). Absolute per-phase seconds below are shares scaled onto these unprofiled walls. Both areas use the same 50 m smoothing window (CLI default = explicit 50 on C), so stage 6–7 cost differences are pure graph size.

Caveats: py-spy reported sampler lag ("behind in sampling") during the import/pipeline phases of the 200k captures — early-phase percentages carry a few points of noise; the solver/pipeline ordering is unambiguous. Committed `.collapsed` files have import-machinery stacks aggregated to one token line and process frames normalized (machine paths stripped); totals unchanged. Percentages computed by exact aggregation over the raw files, not eyeballed SVGs.

---

## Benchmark reconciliation (Story 11.3 baselines → committed HEAD)

`test_grasp_1k_iterations`, grenoble_small fixture, ms per 1k iterations:

| Autosave | State | Mean | Median | Min | Cumulative (median) |
|---|---|---|---|---|---|
| `0001_070debf` | Epic 11 baseline (11.3) | 302.0 | 300.9 | 287.1 | 1× |
| `0002_f2671d1` | post-12.1 (static adjacency) | 145.2 | 123.2 | 116.5 | 2.44× |
| `0003_b0e85dd` | post-12.2 (θ-prefix + distinctness) | 102.2 | 81.3 | 74.5 | 3.70× |
| `0004_cdd284a` | post-12.3 (batched RNG) | 51.2 | 52.8 | 44.3 | 5.70× |
| `0005_12c693a` | committed HEAD (this story) | 53.7 | 53.9 | 49.0 | **5.58×** (min: 5.86×) |

**Verdict: above the predicted 2.5–4× band.** 0005 confirms 0004 within noise (the bench asserts `budget-exhausted` per round, so no silent early-exit inflates this). Setup-stage benchmarks unchanged throughout — setup untouched by Epic 12.

---

## Post-optimization solver profile (capture B, 88% solver samples)

Within `solver.run` (cum % of solver samples):

| Item | Post-12.3 | Pre-12.1 (11.2) | Note |
|---|---|---|---|
| `_build_rcl` | 42.3% | 57.5% | still #1; now a plain loop over precomputed adjacency records (top line grasp.py:617, 18% self) — no views, no re-wrapping left to remove |
| tracker / distinctness | 18.2% | 7.1% | grew in *share* as everything else shrank; sets are cached (12.2), residue is sort/compare |
| `_best_theta_prefix` | 9.3% | 10.6% | incremental sums (12.2); residue is the scan itself |
| RNG (`_next_uniform`) | 5.0% | ~13.3% | batching (12.3) removed the native boundary cost |
| walk-loop self (`_construct_one`) | ~13% | — | the loop skeleton proper |

Native time is now ≈0% of the profile (the old `Generator.integers` slice is gone; numpy math remains 0%). **The residue is diffuse pure-Python loop work** — no single extractable sub-hotspot; the only way to take another big bite out of the solver is compiling the whole construction loop (the Phase-4 kernel shape).

Larger-area confirm (capture C): shape transfers — `_build_rcl` 46.8% cum, θ-prefix 7.1%, RNG 3.9%. One size-dependent item: `_build_adjacency`, 12.1's **once-per-solve** precompute, costs ~11% of solver time on the radius-10 graph (~3% of the run) — scales with graph size, not iterations; irrelevant at gallery scale.

---

## The phase split flipped (captures A and C)

| Phase | Chamrousse r6 (12.2 s) | Chartreuse r10 (40.0 s) |
|---|---|---|
| `solver.run` | 57.5% (~7.0 s) | 30.5% (~12.2 s) |
| imports + process startup | ~23% (~2.8 s) | ~14% (~3–5 s) |
| stages 6–7 query-side (Laplacian smoothing + deadband + edge metrics) | 11.7% (~1.4 s) | 27.4% (~11.0 s) |
| cache `read_entry` (shapely `from_wkb` + graph rebuild) | 5.0% (~0.6 s) | 11.0% (~4.4 s) |
| `filter_trails` re-run (stage-2 redux) | 1.4% | 7.3% (~2.9 s) |
| stages 8–9 (detect + contract) | 0.5% | 5.9% (~2.4 s) |
| validate + render | 0.5% | 3.3% (n=10) |

At 11.2 the solver was 94% and everything else 6%; that premise **no longer holds**. Cause: the cache boundary stores stages 1–5 keyed independent of query knobs, so smoothing/metrics/filter/detection/contraction re-run per query ([query.py:243-332](../../../src/steeproute/cli/query.py)) — designed (Stories 6.1/6.3) to keep `--difficulty-cap`, `--elevation-smoothing`, `--elevation-deadband`, `--l-connector` free query knobs. Those stages scale with base-graph size (smoothing runs `round(window²/6)` ≈ 417 whole-graph Laplacian passes at the 50 m default); the solver scales with iterations. Epic 12 shrank only the latter. The import cost (~3 s) is constant per process and was always there — it's just a third of a small-area run now.

**Single-area caveat: resolved, with a twist.** The solver-internal shape transfers across areas (same ranking, same conclusions). The *phase split* does not — it flips with area size, and that flip is the finding that matters for what comes next.

---

## What next — per-lever assessment

Ranked by end-to-end impact on the large-area workload (the whole-execution goal's worst case):

| # | Lever | Share (r10) | Fix shape | Rust? |
|---|---|---|---|---|
| 1 | Stages 6–7: Laplacian smoothing dominates (~417 whole-graph passes) | ~27% | **numpy/scipy-shaped**: the node-elevation diffusion is repeated array math — vectorize via sparse-matrix iteration, or cut the iteration count algorithmically. The exact case the solver *didn't* have. | Not indicated — numpy is the natural tool |
| 2 | Solver residue | ~31% | diffuse pure-Python loop; only big lever left is a compiled kernel of the construction loop. End-to-end ceiling: **~1.4×** here, ~2.3× on small areas. 12.1's flat adjacency records are already most of the extract-interface-first refactor. | The one Rust-*shaped* target; not performance-justified |
| 3 | `filter_trails` redux + stages 8–9 | ~13% | recompute-avoidance / cache-boundary design question (e.g. a light second-tier cache keyed on the query knobs), not a compute problem | No |
| 4 | Cache `read_entry` | ~11% | deserialization engineering (per-edge `from_wkb` + `add_edge` rebuild → array-based storage or a prebuilt-graph format) | No |
| 5 | Imports/startup | ~3–5 s constant | lazy imports on the query path; bounded, cheap | No |

**Recommendations:**

1. **Phase 4 as designated (extract-interface-first → PyO3 `steeproute-core` kernel): no-go on performance need.** The Phase-3 target was exceeded, NFR1 margin is ~15–50× (worst observed quality query 40 s vs the ~10-min design target), and on the workload that motivates the whole-execution goal the kernel's end-to-end ceiling is ~1.4×. rustworkx and in-solver numpy batching **remain not indicated** (still zero algorithm time; still no dense scoring math).
2. **For the whole-execution goal, the evidence indicts the query side, in the order of the table** — headlined by smoothing vectorization (lever 1), with recompute-avoidance, cache-read format, and lazy imports behind it. All pure-Python/numpy-shaped. Plausible combined effect: a 40 s large-area query into the ~20 s range without touching the solver. If pursued, plan it as a new epic via correct-course (the 11.2 → Epic 12 pattern).
3. **Learning value (separate rationale, per the original research framing):** if writing Rust is wanted for its own sake, the solver kernel is still the only Rust-shaped candidate — a legitimate *choice* at a stated ~1.4× (large-area) / ~2.3× (small-area) end-to-end ceiling, not a performance recommendation. Precondition unchanged: the time-boxed cargo-behind-corporate-proxy spike before any commitment.

No Phase-4/Phase-5 stories are planned by this document — it is the decision input for whatever correct-course follows.

---

## Artifacts

- [profiling/grasp-post12-chamrousse-200k.collapsed](profiling/grasp-post12-chamrousse-200k.collapsed) / [.svg](profiling/grasp-post12-chamrousse-200k.svg) — capture A (phase split, realistic params)
- [profiling/grasp-post12-chamrousse-1m.collapsed](profiling/grasp-post12-chamrousse-1m.collapsed) / [.svg](profiling/grasp-post12-chamrousse-1m.svg) — capture B (solver-internal attribution)
- [profiling/grasp-post12-chartreuse-200k.collapsed](profiling/grasp-post12-chartreuse-200k.collapsed) / [.svg](profiling/grasp-post12-chartreuse-200k.svg) — capture C (larger-area confirm)
- `.benchmarks/Windows-CPython-3.13-64bit/0001–0005` — benchmark chain (source of the reconciliation table)
- Committed `.collapsed` files: import stacks aggregated to a token line, process frames normalized; re-aggregate with any flamegraph tool
