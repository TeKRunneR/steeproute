# Epic 16 ownership-performance close-out

**Date:** 2026-08-03
**Scope:** final r20 setup/query trace, one-shot r50 scale trace, and residual-work decision
**Revision:** `2abf25324d757a9929c5e60aa19a4ca55c5bb3e0`

## Decision summary

Epic 16 materially improved the complete r20 workflow without changing route quality. The final
controlled-cache trace completed setup plus query in 176.48 s CLI / 205.16 s external, returned the
same 20,768.0 objective and 10 valid routes as the review query anchor, and reduced query peak RSS
from 2,674,232 to 1,818,248 KiB. The observed setup and combined deltas are not causal A/Bs because
the review setup included live DEM time and used a different prepared snapshot; the per-story
controlled comparisons below remain the evidence for attribution.

The first full r50 attempt fit on this machine. Setup completed at a narrow 13,393,268 KiB peak RSS,
then the immediate query completed at 5,332,724 KiB. The end-to-end run took 1,707.66 s CLI /
1,889.48 s external and returned 10/10 valid routes with objective 26,130.7. No process OOMed and no
manual retry or weakened workload was used. One IGN timeout was handled by the setup command's own
built-in retry policy.

The residual decision is asymmetric:

1. Promote the custom Overpass-JSON-to-graph parser (S5-deep) to a focused correct-course proposal.
   r50 graph build remains 258.96 s and setup approached the machine's memory ceiling. The proposal
   must be POC-gated on graph/order/tag identity and peak memory before production authorization.
2. Defer generic per-stage multiprocessing. The r50 query pipeline is material, especially the
   32.54 s elevation-reshape phase, but duplicating graph/array state while setup already peaks near
   13 GiB is not an evidence-backed generic design. Reconsider only a named stage after representation
   and ownership work supplies a measured transfer/memory budget.
3. Keep the stages 6–7 flat-data rider deferred as a possible input to that named query-stage work,
   not a standalone story. Its r20 saving ceiling was only 2–3 s; r50 makes the 32.54 s containing
   phase worth revisiting, but not all of that phase is removable representation churn.
4. Stop the separate consuming/vendored `simplify_graph` idea. Vendoring about 120 lines of complex
   osmnx code to remove one copy did not move the measured end-to-end peak in Story 16.4, and S5-deep
   is the coherent route if osmnx assembly is replaced.

## Reproducible provenance

| Item | Value |
|---|---|
| Git | `2abf25324d757a9929c5e60aa19a4ca55c5bb3e0`, `main` ahead of `origin/main` by 1 |
| Worktree at measurement start | dirty only for the untracked Story 16.7 file and modified sprint status; no source, test, fixture, golden, dependency, or lockfile changes |
| OS | WSL2, Linux `6.6.87.2-microsoft-standard-WSL2`, x86_64 |
| CPU | Intel Core Ultra 7 155U, 7 cores / 14 logical CPUs |
| Memory | 15 GiB RAM, 4 GiB swap |
| Scratch disk | `/tmp`, 946 GiB available before the run |
| Runtime | uv 0.9.12; Python 3.13.9 |
| Libraries | numpy 2.4.4; networkx 3.6.1; osmnx 2.1.0; shapely 2.1.2; rasterio 1.5.0 |
| Distribution metadata | `0.0.1.dev141+4380970`; manifests independently record executing commit `2abf253-dirty` |
| Scratch root | `/tmp/steeproute-16-7-rebqx4` |

Separate `r20-cache`, `r20-results`, `r50-cache`, and `r50-results` roots prevented either scale run
from reading a prepared `areas/` entry or altering user/fixture caches. The r20 root was seeded only
with the existing `osmnx/` and `dem/` source caches. The r50 root began empty. Each setup and query
had its own stdout, stderr, and `/usr/bin/time -v` record.

Setup command shape, with `20` changed to `50` and the matching isolated root for r50:

```text
steeproute-setup --center 45.260,5.788 --radius 20 --angle 0
  --untagged-trails include --dem-version ign-rgealti-highres --dem-fetch-workers 4
  --osm-age-warn-days 90 --cache-dir <isolated-cache>
```

The immediate query used every behavior-affecting input explicitly:

```text
steeproute --center 45.260,5.788 --radius 20 --angle 0
  --theta 0.20 --min-climb-slope 0.20 --difficulty-cap T4 --l-connector 50
  --min-climb-ground-length 300 --elevation-smoothing 50 --elevation-deadband 1
  --j-max 0 --n 10 --untagged-trails include --seed 44 --iter-budget 1000000
  --time-budget 600 --stagnation-iters 0 --max-descent-slope 0.4 --start-at-junction
  --workers 4 --merge-interval 250000 --progress-interval 1 --osm-age-warn-days 90
  --cache-dir <same-isolated-cache> --output-dir <isolated-results>
```

Both commands were wrapped separately in `/usr/bin/time -v`. Peak RSS values below are per command
and are deliberately not summed.

## Final r20 trace

The setup created cache entry `094e2012fa5e4a69`: 131,793 nodes, 327,911 edges, and a
128,306,835-byte `graph.pkl`. OSM was a source-cache hit with no download; the 1.69 s fetch and
78.62 s graph-build split is explicit. DEM resolution was also a source-cache hit. The query loaded
that exact entry.

| Setup stage | Wall (s) |
|---|---:|
| OSM load | 84.43 |
| └ Overpass fetch | 1.69 |
| └ Graph build | 78.62 |
| Trail filter | 10.24 |
| Polyline smoothing | 2.75 |
| Resampling | 17.95 |
| DEM resolve | 1.02 |
| Elevation sampling | 7.52 |
| Cache write | 3.67 |
| **CLI total** | **127.64** |
| **External process wall** | **144.23** |
| **Peak RSS** | **3,346,052 KiB** |

| Query stage | Wall (s) |
|---|---:|
| Load prepared area | 1.97 |
| Elevation reshape, stages 6–7 | 10.16 |
| Difficulty-cap trail filter | 0.37 |
| Climb detection | 0.97 |
| Climb contraction | 7.04 |
| Validate and render | 0.50 |
| **CLI total** | **48.84** |
| **External process wall** | **60.93** |
| **Peak RSS** | **1,818,248 KiB** |

The solver exhausted the explicit 1,000,000-iteration budget. It returned 10/10 routes, objective
20,768.0, zero validation failures, and 20 output files. The complete r20 operation therefore took
176.48 s CLI / 205.16 s external.

### Review-anchor comparison

| Measure | Review anchor | Final trace | Observed delta |
|---|---:|---:|---:|
| Setup CLI | 299.28 s | 127.64 s | -171.64 s (-57.4%) |
| Query CLI | 80.02 s | 48.84 s | -31.18 s (-39.0%) |
| Query external | 90.82 s | 60.93 s | -29.89 s (-32.9%) |
| Query peak RSS | 2,674,232 KiB | 1,818,248 KiB | -855,984 KiB (-32.0%) |
| Setup + query CLI | 379.30 s | 176.48 s | -202.82 s (-53.5%) |

The final graph matches the review's warm-ingestion POC counts, but the review query entry had
131,745 nodes / 327,658 edges. The equal route objective and validation outcome rule out a
fast-but-broken final run; they do not turn this into a same-snapshot A/B. The review setup also spent
82.87 s in DEM resolution versus 1.02 s here. Even excluding that whole stage, observed setup wall is
216.41 to 126.62 s (-89.79 s, -41.5%), but snapshot/session differences still prevent causal credit.
The controlled story-level comparisons supply that credit.

## One-shot r50 trace

The r50 setup created entry `1372a4b1765b1656`: 348,974 nodes, 856,673 edges, a
442,629,004-byte `graph.pkl`, and a 442,054,938-byte DEM. The empty source cache caused nine
Overpass responses to be downloaded. DEM resolution fetched 100 tiles; its first attempt timed out
and the command's configured three-attempt policy retried successfully. There was no manual retry.

| Setup stage | Wall (s) |
|---|---:|
| OSM load | 762.92 |
| └ Overpass fetch, 9 downloads | 500.00 |
| └ Graph build | 258.96 |
| Trail filter | 40.12 |
| Polyline smoothing | 9.00 |
| Resampling | 48.44 |
| DEM resolve, 100 tiles + one internal retry | 696.92 |
| Elevation sampling | 26.10 |
| Cache write | 13.74 |
| **CLI total** | **1,597.37** |
| **External process wall** | **1,750.66** |
| **Peak RSS** | **13,393,268 KiB** |

| Query stage | Wall (s) |
|---|---:|
| Load prepared area | 6.39 |
| Elevation reshape, stages 6–7 | 32.54 |
| Difficulty-cap trail filter | 0.91 |
| Climb detection | 2.79 |
| Climb contraction | 17.88 |
| Validate and render | 1.23 |
| **CLI total** | **110.29** |
| **External process wall** | **138.82** |
| **Peak RSS** | **5,332,724 KiB** |

The query exhausted the same 1,000,000-iteration budget and returned 10/10 routes, objective
26,130.7, zero validation failures, and 20 output files. Setup plus query took 1,707.66 s CLI /
1,889.48 s external. Neither command used swap or exited abnormally. Relative to r20, the r50 graph
had 2.65× the nodes, 2.61× the edges, and a 3.45× graph payload; setup peak RSS was 4.00× and query
peak 2.93×. The 12.51× setup wall ratio is dominated by deliberately different network/cache state,
so it is not a CPU scaling ratio.

## Epic 16 evidence ledger

These rows are separate experiments. Their deltas must not be added.

| Evidence | Kind | Result | Attribution limit |
|---|---|---|---|
| 2026-07-24 review query batch | Real CLI combined POC, retained entry | 80.02→67.33 s CLI (-12.69); 90.82→78.62 s external; exact 20-file output | Demonstrates the combined orchestration batch, not three additive individual wins |
| 2026-07-24 review warm ingestion | Real cached-response POC | 131.94→99.10 s (-32.84); identical 131,793/327,911 graph | Ingestion only, not full setup |
| Story 16.1 | Controlled real query | 110.19→78.84 s; peak 2.434→1.832 GB; exact outputs | Query ownership batch; untouched-stage drift excluded |
| Story 16.2 | Controlled setup stage + real setup | target 79.29→50.02 s; total 330.94→310.21 s; graph-identical | Targeted delta is stronger than noisy total |
| Story 16.3 | Controlled cache/load | load 3.23→2.49 s; payload 165.8→121.2 MB | Total query moved inside noise and is not credited |
| Story 16.4 | Controlled warm setup | OSM load 191.39→129.93 s; setup 250.94→190.47 s; graph-identical | No setup peak reduction; `simplify_graph` remained 83.37 s / 69% of assembly |
| Story 16.5 | Controlled real query | four-worker 64.08→55.38 s; external 69.64 s; exact outputs | Stages 6–7 remained 9.54–9.78 s; nearby repeat showed wall variance |
| Story 16.6 | Exact backend gates + fresh trace | active-call ownership matrix; setup/query 187.11/62.28 s CLI, 211.28/75.54 s external | Fresh OSM changed objective to 20,362.4; not comparable by arithmetic to retained runs |
| Story 16.7 r20 | Final controlled-cache whole operation | 127.64 + 48.84 = 176.48 s CLI; 144.23 + 60.93 = 205.16 s external | Final experience and phase shape; cross-session/snapshot attribution prohibited |
| Story 16.7 r50 | First full fresh-network attempt | 1,597.37 + 110.29 = 1,707.66 s CLI; valid 10-route result | Scale feasibility and operational boundary, not an r20 scaling benchmark |

## Residual rationale

### S5-deep: propose correct-course

At r20, current graph build is still 78.62 s and 62% of setup wall. At r50 it is 258.96 s even
after Story 16.4 removed component/truncation ownership waste, while setup reaches 13,393,268 KiB.
This is enough end-to-end and scale evidence to draft a correct-course proposal for a purpose-built
Overpass JSON parser. It is not authorization to implement one. Promotion must require exact node and
edge iteration order, directionality, keys, way IDs, tags, geometry, component policy, street counts,
and route output; a parser that merely produces a plausible graph fails the gate. The proposal should
measure live setup wall and peak RSS, because avoiding the raw osmnx graph is at least as important as
parser micro-benchmark speed.

### Per-stage multiprocessing: defer generic scope

r50 proves that query-side pipeline work is material: stages 6–9 consume 54.12 s before solver
startup, led by elevation reshape at 32.54 s and contraction at 17.88 s. It does not prove that a
generic process pipeline wins after spawn, transfer, merge, deterministic-order, and duplicated-memory
costs. Setup has only about 2 GiB of nominal RAM headroom at its measured peak, making a design that
copies the parent graph especially suspect. First reduce representation churn or identify one stage
with a shared/partitioned input contract; then demand a real r50 CLI A/B before correct-course.

The stages 6–7 flat-data rider belongs in that investigation. Story 16.5 measured only a 2–3 s r20
ceiling and identified sequential deadband hysteresis plus the renderer's mandatory tuple rebuild.
The r50 containing phase is now large enough to revisit, but a 32.54 s stage line is not evidence that
all 32.54 s is removable. The vendored `simplify_graph` fork remains stopped: its maintenance burden
survives while its original end-to-end peak rationale did not, and S5-deep supersedes it structurally.

## Close-out

Epic 16's implemented ownership changes stand on controlled equivalence and per-story A/B evidence;
the final traces show the resulting user-visible shape without manufacturing an additive speedup.
r20 is comfortably inside the PRD's query design budget. r50 is operational on this 15 GiB machine,
but setup is a near-memory-limit, network-sensitive preparation job. The only justified new proposal
is the POC-gated S5-deep parser. All other residuals remain stopped or deferred until a named stage and
memory-safe transfer contract are measured.
