# Design spec: shared-memory array GRASP for parallel workers

**Status:** DRAFT / gated behind the 14.6 r50 probe. Promote to a numbered sprint story via
`correct-course` after 14.6 (the handoff routes all Q4-class deep work through a post-probe
correct-course). Author: Yann (Claude Opus 4.8), 2026-07-08.

**One-line:** Give parallel GRASP workers a single shared-memory, flat-array view of the contracted
graph that the construction hot loop and the distinctness tracker read *directly*, eliminating the
per-worker graph copy (the O(N) pickle/unpickle startup **and** the O(N) memory blow-up) that caps
Story 14.4 at ~2× and OOMs at high worker counts / large areas.

---

## 1. Why (measured this session, r20 Grenoble, `--radius 20`, Intel Ultra 7 155U / 14 logical cores)

Story 14.4 shipped `--workers N` (process-per-restart, each worker gets its own copy of the
contracted graph). Real-workload testing exposed a hard ceiling and a memory cliff:

| Observation | Number |
|---|---|
| Full contracted graph (130 233 nodes, 322 705 edges) pickle | **204 MB**, 11.4 s `dumps` |
| Lean graph (Story 14.4 `solver_graph_view`, strips `vertices_resampled`/`geometry`) | 72 MB, ~2–5 s `dumps`, ~3.5–10 s `loads` |
| Prebuilt-adjacency bundle (tried as an alternative payload) | 42 MB but **slower**: 7.5 s `dumps` / 14.9 s `loads` (a tower of `Edge`/`frozenset` objects) |
| Per-worker startup (unpickle 72 MB + rebuild adjacency) | ~10–16 s, **grows with N** |
| Compute throughput scaling | 4w ≈ 30k iter/s, 8w ≈ 35k iter/s (~1.8× of 4w), **12w ≈ 35k (plateau — 12 physical cores)** |
| End-to-end, 1M iters | `--workers 1` = 189 s (~113 s solve); `--workers 4` = 131 s (~56 s solve) → **~2× solve, ~1.44× total** |
| `--workers 8` on a loaded machine | worker killed → **`BrokenProcessPool`** (OOM: 8 × ~150 MB working set) |

Two structural problems, both rooted in *copying the graph into every worker*:

1. **Startup is (de)serialization-bound and O(N).** Any Python-object payload is dominated by
   pickle/unpickle CPU — proven by the adjacency bundle being *smaller yet slower*. This caps the
   effective speedup at ~2× even though the solve compute itself is ~3.4× parallel.
2. **Memory is O(N × graph).** Each worker holds its own full copy. At 8 workers this already
   crashes under load; at r50 (graph several× larger) it will OOM outright. This is the handoff's
   explicitly-flagged risk (§6.2: "worker memory duplication … mitigate with
   `multiprocessing.shared_memory` for the flat arrays").

Neither is fixable by reshaping the Python payload. The only fix is to stop shipping Python objects:
put the solver's input in **shared-memory flat arrays** that workers read directly.

**Note on the other big lever (out of scope here):** even at a perfect 4× solve, total wall is
~108 s because the single-threaded **setup** (elevation-reshape ~38 s + trail-filter ~9 s +
contraction ~9 s ≈ 68 s) then dominates. Setup parallelization/vectorization (14.1/14.2/14.5) is the
larger end-to-end prize; this spec is specifically about the solver's parallel scaling and memory.

## 2. Goal & non-goals

**Goal.** For `--workers N > 1`, reduce per-worker startup to ~spawn+import only (no 72 MB
unpickle, no adjacency rebuild) and reduce total worker memory from O(N × graph) to ~one shared
copy, so that (a) the effective solve speedup approaches the compute ceiling (~3–3.4× at 4–8 workers
on the 155U) and (b) high worker counts / r50 no longer OOM.

**Non-goals.**
- **Not** the full handoff-Q4 "array contract" (cache schema v3 touching smoothing/deadband/metrics/
  render/validator). This is a *solver-internal, query-time, worker-only* representation built from
  the existing contracted graph. It changes **no** on-disk format and **no** pipeline stage. It is a
  narrower, lower-risk sibling of Q4.
- **Not** the `--workers 1` path — that stays the unchanged object-based `GraspSolver` (byte-identical,
  goldens/NFR4 intact). The array path is workers-only.
- **Not** a change to determinism semantics: N>1 stays deterministic per `(seed, workers)`.

## 3. Design

### 3.1 Build a flat, shared representation once in the parent

Reuse the **canonical** `GraspSolver._build_adjacency()` output (it already applies the SAC cap +
`--max-descent-slope` filters and the FR29 total-order sort per node). Flatten it — preserving
per-node candidate order — into CSR-style numpy arrays placed in `multiprocessing.shared_memory`
blocks:

- `node_ids: int64[num_nodes]` — the sorted (junction-filtered when `--start-at-junction`) seed pool
  (`GraspSolver._nodes`). Dense node index = position; original id → dense via `np.searchsorted`
  (node_ids is sorted), so no per-worker dict.
- `cand_offsets: int64[num_nodes+1]` — CSR offsets into the candidate table per dense node.
- Candidate table (row = one directed edge, in canonical order), all length = total candidates:
  - `cand_node_v_dense: int64` (advance the walk), `cand_node_v_orig/cand_node_u/cand_key: int64`
    (Edge reconstruction + directed id), `cand_len/cand_dplus/cand_dminus/cand_avg: float64`,
    `cand_sac_code: int16` (→ a small `sac_code → str|None` table).
  - `cand_block_offsets`/`cand_block_segids: int64` — CSR of int-coded **blocking** seg-ids
    (`base ∩ non_exempt`), used for `used_segments` membership.
  - `cand_baseseg_offsets`/`cand_baseseg_ids: int64` — CSR of int-coded **full** base-segment ids
    (the tracker's Jaccard identity, which uses the full set, not the blocking subset).
- A deterministic int-coding of every distinct base-segment-id tuple (sorted → index), so the tracker
  works entirely in integer space.

The parent passes each worker a tiny **descriptor** (block names + shapes + dtypes + the sac table +
`RCL_SIZE`) via `submit` — bytes, not the graph. Workers attach with `SharedMemory(name=...)` and wrap
`np.ndarray(shape, dtype, buffer=shm.buf)` — zero copy, zero reconstruction. The parent owns the
blocks' lifecycle: keep alive until all workers finish, then `close()` + `unlink()`.

### 3.2 Array-based construction (workers only) — must be bit-identical to `GraspSolver`

Reimplement `_construct_one`/`_build_rcl` over the arrays. `used_directed` becomes a `set[int]` of
candidate row indices; `used_segments` a `set[int]` of coded seg-ids. Iterate a node's candidate rows
in stored (canonical) order, apply the same two walk-state filters, collect the first `RCL_SIZE`
survivors, draw with the **same** `_next_uniform()` chunked RNG, advance. Build `Edge` objects **only
for the emitted walk** (a few dozen edges — cheap), then reuse the existing `_best_theta_prefix` /
`_route_slope_ok` on those. Because the candidate order, the filters, the RNG draw sequence, and the
per-edge float values are identical to `_build_adjacency`/`_build_rcl`, the constructed walks are
**bit-identical** to the object path.

### 3.3 Distinctness tracker in integer space

`TopNTracker` needs each solution's canonical edge-identity set for Jaccard. In the array world,
compute a solution's canonical set as the union of its edges' `cand_baseseg_ids` slices (coded ints).
Add an int-set variant of the tracker (or parameterize the existing one to accept a precomputed coded
canonical set per `Solution`). Jaccard math is unchanged (set intersection/union sizes), so admission
decisions match the object path exactly. Merge across workers as today (worker-id order, one fresh
tracker).

### 3.4 Wiring

`run_parallel_grasp` builds the shared representation once, spawns workers with the descriptor, and
merges. The Story 14.4 machinery (spawn context, `SeedSequence(seed).spawn(N)`, budget split, live
`Manager`-queue progress, `ParallelGraspInterrupted` salvage, `ParallelGraspFailed` → single-process
fallback) is reused unchanged. `--workers 1` still takes the untouched object path.

## 4. Bit-identity & correctness strategy (the main risk)

This reimplements two core, FR29-critical components (construction + tracker). Anchor every step to
the existing object path:

- **Unit (toy graph, all quality-gate seeds):** array-path `run()` output == object-path output —
  raw `==` on objectives and edge-id sequences (mirror `test_grasp_reproducible.py`).
- **Integration (grenoble_small fixture):** same byte-identity, incl. `--start-at-junction` and
  `--max-descent-slope` on (they exercise node filtering + descent cap + reuse blocking).
- **Quality gate:** run the Story 3.7 GRASP-vs-exhaustive gate against the array path too.
- **Determinism:** two `(seed, workers)` runs byte-identical (already asserted for 14.4; keep).
- **Shared-memory lifecycle on Windows:** explicit parent-owned `close()`/`unlink()`; test that
  blocks are freed (no leaked `/dev/shm`-equivalent) and that a worker crash still cleans up
  (compose with the `ParallelGraspFailed` fallback).

If bit-identity can't be achieved for some site (e.g. a float-order subtlety), **stop and reassess** —
a non-identical array path is a different algorithm and must not ship silently.

## 5. Acceptance criteria

1. **Given** `--workers N > 1`, **when** the solve runs, **then** workers attach to a single
   shared-memory array view (no per-worker 72 MB unpickle, no per-worker adjacency rebuild), verified
   by measuring per-worker startup dropping to ~spawn+import.
2. **Given** the array construction + tracker, **then** their `list[Solution]` output is
   **byte-identical** to the object-based `GraspSolver` on the toy graph (all gate seeds) and the
   grenoble_small fixture, including `--start-at-junction` / `--max-descent-slope`; and the
   GRASP-vs-exhaustive quality gate passes against the array path.
3. **Given** total worker memory, **then** it is ~one shared copy (not O(N)), verified by measuring
   peak RSS at `--workers 8` staying flat vs `--workers 2` (no OOM); `--workers 1` output unchanged.
4. **Given** the re-measure (same shape as 14.4's close-out), **then** the r20 1M-iter solve speedup
   is recorded (target: approach the ~3–3.4× compute ceiling at 4–8 workers), and the r50 memory +
   speedup is recorded at the probe.
5. **Given** Windows spawn, **then** shared-memory blocks are created/attached/freed cleanly (no
   leaks, correct teardown on normal completion, interrupt, and worker death).

## 6. Task sketch

- [ ] `solver/shared_graph.py` — flatten canonical adjacency → CSR numpy arrays; int-code seg-ids;
      allocate/attach/free `shared_memory`; descriptor dataclass. Parent-owned lifecycle.
- [ ] `solver/array_construct.py` (or fold into the worker) — array `_construct_one`/`_build_rcl`;
      Edge reconstruction for emitted walks only; reuse `_best_theta_prefix`.
- [ ] Int-space `TopNTracker` variant (or parameterize existing).
- [ ] Wire into `run_parallel_grasp` (build-once, descriptor to workers, teardown); keep 14.4 spawn /
      seed / budget / progress / interrupt / fallback machinery.
- [ ] Tests: bit-identity (toy + fixture, flags on), quality gate on array path, determinism, memory
      (RSS flat across N), shared-memory lifecycle incl. crash/interrupt teardown.
- [ ] Re-measure r20 (1M iters) + r50 at the probe; record in `research/` and the story close-out.

## 7. Sequencing & effort

- **Gated behind 14.6.** Run the r50 probe first — it confirms (a) whether r50 actually needs this for
  memory (strong prior: yes) and (b) that r50 solve budgets are large enough that the compute win
  matters. Promote to a numbered story via the post-probe correct-course (per the epic's §8 gate).
- **Effort:** substantial — reimplementing two FR29-critical components + shared-memory plumbing +
  extensive equivalence testing. A focused multi-session story, best done on an **unloaded machine**
  (this session's benchmarking was repeatedly confounded by machine load).
- **Related but separate:** setup-stage parallelization/vectorization is the larger *total-wall*
  lever once the solver is parallel; track it independently (14.1/14.2/14.5 lineage).

## 8. References

- Story 14.4 (`_bmad-output/implementation-artifacts/14-4-parallel-grasp-restarts.md`) — the shipped
  copy-per-worker design, `solver_graph_view` lean payload, and the close-out measurements this spec
  builds on.
- `src/steeproute/solver/parallel.py` — `run_parallel_grasp`, `solver_graph_view`, `HEAVY_EDGE_ATTRS`,
  `ParallelGraspFailed` fallback, spawn/seed/budget/progress/interrupt machinery to reuse.
- `src/steeproute/solver/grasp.py` — `_build_adjacency` (the canonical source to flatten),
  `_construct_one`/`_build_rcl` (to mirror on arrays), `_next_uniform` (RNG sequence to preserve),
  `_best_theta_prefix` (reuse on emitted walks).
- `src/steeproute/solver/distinctness.py` — `TopNTracker` / `jaccard_distance` (int-space variant).
- `research/steeproute-next-optimization-pass-handoff-2026-07-05.md` §6.2 (worker memory duplication →
  shared_memory), §Q1 (parallel GRASP), §Q4 (full array contract — the broader, deferred cousin), §8
  (r50 probe gate).
