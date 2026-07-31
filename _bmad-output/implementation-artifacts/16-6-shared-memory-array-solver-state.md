# Story 16.6: Shared-memory array solver state (structural, POC-gated)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want one canonical solver state built in the parent and read directly by workers,
so that O(workers) adjacency / object-graph construction and O(workers × graph) steady memory stop
scaling with worker count.

## Acceptance Criteria

1. **The current object-worker path is pinned as an immutable reference before production changes.**
   At reference commit `6a6fa39`, committed, reviewable oracle vectors capture full ordered
   `Solution`/`Edge` fields, exact serialized float bits, convergence status, and convergence
   iteration for quality-gate seeds `(11, 23, 37, 53, 71)`, the committed Grenoble fixture,
   one-round parallel solving, and migrating parallel solving. Oracle generation is a separate
   explicit developer action; ordinary tests only consume it and cannot regenerate it. Comparisons
   use identical worker count, round plan, seed sequences, and non-wall-clock termination
   (`stagnation_iters=0`, time budget demonstrably non-binding); objective-only, edge-set-only,
   tolerance, or two live implementations drifting together is insufficient.
2. **The spawn-transfer step lands and is measured separately.** The existing lean-graph pickle is
   serialized once into parent-owned `multiprocessing.shared_memory`; worker initializers receive a
   small validated descriptor, attach, and unpickle independently without sending the ~73 MB blob
   through every spawn pipe. This stage intentionally retains one object graph and one
   `SolverStaticContext` per worker. Blob allocation, copy, initializer attach/unpickle, startup
   latency, transient memory, and cleanup are compared against the current bytes-in-`initargs` path
   before CSR work is credited with any gain.
3. **One canonical CSR-style solver state replaces worker object state for N>1.** The parent builds a
   current `SolverStaticContext` once and flattens its already-filtered, already-sorted adjacency; it
   does not independently reimplement graph filtering or candidate ordering. Shared state contains
   an all-node dense domain and adjacency offsets, a **separate ordered start-node index array**
   (junction filtering applies only to the initial draw), exact candidate identity/metric/SAC data,
   blocking-segment CSR, full base-segment CSR, and a deterministic lookup/coding scheme for directed
   edges and base-segment tuples. Workers read immutable array views directly and do not unpickle a
   graph or build a `ContractedGraph`, `SolverStaticContext`, segment map, non-exempt set, or adjacency.
4. **Array construction and distinctness are algorithmically identical to the object reference.** A
   worker preserves the stored candidate order, first-`RCL_SIZE` survivor rule, directed-edge and
   blocking-segment checks, `_next_uniform` draw sequence, and Python-float `Edge` reconstruction for
   emitted walks only. Canonical prefix scoring and `_route_slope_ok` remain the final gate. The
   integer-space distinctness seam preserves the existing strict Jaccard threshold, both-empty
   overlap at `j_max == 0`, duplicate admission at `j_max == 1`, allocation-free general cardinality,
   and the existing **directed edge-triple** sort key. Migrated elites carry or deterministically
   recover their coded canonical identities without restoring a full Python `SegmentMap` per worker.
5. **The array path cannot become the N>1 default until exact equivalence passes.** Object and array
   backends compare as raw `list[Solution]` values over every AC #1 seed/path plus the real fixture
   with `start_at_junction` and `max_descent_slope` exercised independently and together. Junction
   cases run on a graph built with junction annotation enabled (the ordinary integration Grenoble
   fixture is not) or through the real E2E contraction path, and assert both reference and candidate
   outputs are non-empty so equality cannot pass vacuously. Repeated array runs are deterministic per
   `(seed, workers, merge_interval, iter_budget)`, the exhaustive quality gate passes against the
   array path, and all pinned goldens remain untouched. Any mismatch stops promotion and is treated
   as an algorithm change, not normalized or rebaked.
6. **Existing orchestration contracts remain intact.** `--workers 1` stays on the unchanged object
   `GraspSolver` CLI path. For N>1, seed spawning, budget/round partition, worker-id result and merge
   order, elite migration, progress snapshots, convergence status/iteration, completed-worker-only
   interrupt salvage, non-blocking render handoff, and `ParallelGraspFailed` single-process fallback
   keep their current semantics. Backend selection is an internal solver-orchestration seam, not a
   new `SolverParams`/`models.py` field or public CLI flag.
7. **Shared-memory ownership is safe under Windows spawn and every exit path.** Descriptors validate
   schema, shapes, dtypes, offsets, sizes, and integer range before views are exposed; worker ndarray
   views are read-only and their `SharedMemory` handles live at least as long as the views. Lifecycle
   follows one enforceable state machine: every initializer sends one process-unique ACK only after
   successful attach/validation; partial initialization cannot satisfy readiness; exactly one owner
   retains the resource while any process can still attach/read; and every pre-pool, executor-
   construction, initializer, broken-pool, normal, and interrupt transition assigns cleanup to
   exactly one owner. On non-blocking shutdown, ownership transfers to a deferred reaper that waits
   for worker termination before close/unlink without delaying interrupt salvage or render. Cleanup
   exposes an awaitable test/benchmark seam, is idempotent, and proves names cannot be reattached only
   after safe teardown. Blob workers close their handles immediately after successful unpickle;
   array workers retain handles for ndarray-view lifetime. Resource setup/attach failures surface
   through the established parallel fallback contract, not raw errors.
8. **Measurements demonstrate ownership scaling, not merely a faster micro-benchmark.** Object,
   shared-blob, and CSR paths report parent build/serialization, spawn+attach/state-transfer, solve,
   and teardown timing at workers 2/4/8 where the machine permits. Memory evidence includes
   process-tree peak RSS as requested by the epic, shared-block bytes, and a private/unique-memory or
   Windows private-working-set measure so multiply-counted shared pages are not mislabeled as
   O(workers) ownership. Pre-return solve latency and eventual cleanup/process-exit latency are
   reported separately by awaiting the cleanup seam in benchmarks; production retains non-blocking
   validation/render overlap. The story records real r20 setup, query, combined wall-clock, phase
   timings, and peak memory before/after with the full explicit workload; component measurements do
   not replace the real CLI result.
9. **The promoted structural scope is completed and documented.** Current planning still targets
   r50/whole-range use, so the expected outcome is both measured shared-blob and CSR stages. Only an
   explicit product/scope decision that supersedes that target may activate the epic's stop-after-blob
   clause; if that happens before CSR work, record the decision and measurements rather than claiming
   the O(workers × graph) problem was solved. Architecture Category 5a records the landed ownership,
   lifecycle, determinism, fallback, and measured scaling. Unit, integration, E2E, benchmark, type,
   lint, coverage, and untouched-golden checks pass.

## Tasks / Subtasks

- [x] Pin the object-worker oracle and representative baselines (AC: #1, #5, #8)
  - [x] Generate and commit immutable reference vectors at `6a6fa39` for quality seeds, the real
        fixture, one round, and migration: full ordered dataclass fields, IEEE-754 float bits,
        convergence status/iteration, and the exact run inputs. Keep generation outside normal tests.
  - [x] Record object-path blob size, parent serialization, worker initialization/static-context
        build, solve, process-tree/private memory, and current r20 setup/query/combined measurements.
  - [x] Preserve the object backend behind a private test/benchmark seam until every promotion gate
        passes; do not expose a new end-user backend flag.
- [x] Implement and measure the shared lean-graph blob step (AC: #2, #6, #7, #8)
  - [x] Add a parent-owned shared-blob descriptor/resource with validated length and exception-safe,
        idempotent close/unlink behavior; copy the existing lean pickle exactly once.
  - [x] Attach and unpickle from the descriptor in each spawn initializer while preserving the plain
        initializer progress queue and process-local `SolverStaticContext` reuse from Story 16.5.
  - [x] Implement the ACK/readiness/sole-owner/deferred-reaper state machine so lazy or late worker
        startup cannot race cleanup; expose an awaitable test/benchmark handle and cover allocation,
        executor, initializer, interrupt, and worker-death transitions.
  - [x] A/B exact output, startup, transfer, transient/steady memory, and wall-clock separately from
        the later CSR path.
- [x] Build the canonical shared array contract in the parent (AC: #3, #4, #7)
  - [x] Introduce a focused solver-layer owner/descriptor module; keep it out of cache-content-hashed
        `models.py` and make workers attach through the pool initializer rather than task arguments.
  - [x] Build one `SolverStaticContext` with the current graph/filter compatibility contract, flatten
        its canonical adjacency, reuse its segment map for parent merge/validation, then release
        temporary parent object adjacency when no longer needed.
  - [x] Use sorted all-node IDs for dense adjacency/destination lookup and a separate start-node index
        array. Pin empty start pools and valid zero-candidate adjacency explicitly.
  - [x] Encode directed IDs, exact float64 metrics, SAC values/`None`, blocking IDs, and full base IDs
        without silent integer narrowing; preserve candidate/segment ordering and validate descriptors.
- [x] Implement the workers-only array solve (AC: #3–#6)
  - [x] Mirror `_construct_one`/`_build_rcl` over read-only arrays with identical walk-state filters,
        RCL truncation, RNG consumption, and current-node advancement.
  - [x] Reconstruct ordinary Python `Edge` values only for the emitted walk. Extract one shared pure
        RNG-buffer and prefix-finalization implementation used by object and array paths; do not copy
        `_next_uniform`, `_best_theta_prefix`, or slope logic, and preserve `_construct_one`'s test seam.
  - [x] Add a checked `(Solution, coded_identity)` tracker seam for construction and migrated elites,
        backed by the existing single admission/replacement implementation. Reject mismatched or
        unknown directed identities; retain ordinary `Solution` output and directed-triple ordering.
  - [x] Wire object/shared-blob/array implementations through `run_parallel_grasp` without changing
        the CLI's N=1 branch, seeding, round plan, progress, convergence, merge, interrupt, or fallback.
- [x] Prove correctness at every changed seam (AC: #4–#7, #9)
  - [x] Unit-test descriptor validation, deterministic coding/flattening, candidate order, the
        all-node/start-node split, zero candidates, empty junction pool, self-loops, parallel keys,
        reusable vs blocking IDs, multi-base super-edges, and cleanup idempotence/failures.
  - [x] Compare ordinary and pre-canonicalized tracker admission at `j_max` 0/general/1, including
        both-empty, duplicates, overlapping replacement, capacity eviction, and equal-objective ties.
  - [x] Under forced spawn, compare committed oracle/object/blob/array results for seeds
        `(11, 23, 37, 53, 71)`, one/multiple rounds, flags off/on, real fixture, repeated determinism,
        convergence, progress, fallback, interrupt, and parent merge/validation state. Rebuild with
        junction annotation (or use E2E contraction) and assert non-empty junction/combined outputs.
  - [x] Assert the array worker does not call `pickle.loads`, construct `GraspSolver`/graph static
        state, or build adjacency; run the exhaustive quality gate and existing N=1/N>1 E2E suites.
  - [x] Exercise normal, allocation/setup failure, executor failure, attach failure, worker death, and
        interrupt cleanup; verify no leaked/re-attachable block after safe teardown.
- [x] Measure, document, and close out (AC: #8, #9)
  - [x] Extend solver benchmarks to report blob copy/unpickle, CSR build/attach, hot solve, startup,
        pre-return latency, separately-awaited cleanup/process exit, and process-tree/private memory
        at equal work for workers 2/4/8.
  - [x] Run back-to-back real r20 setup + query measurements with every parameter explicit; record
        setup, query, total, phase timings, external wall, and peak memory without extrapolation.
  - [x] Run r50 only while it remains an active target and the machine can do so safely; record an
        OOM/operational limit as evidence rather than weakening parameters or guessing.
  - [x] Update Architecture Category 5a with the actual backend/lifecycle decision and measurements;
        leave pipeline/cache architecture and goldens untouched.

## Dev Notes

### Developer Context and Guardrails

- **Start from current code, not the July 8 draft literally.** Story 16.5 changed the reference
  architecture: object workers now reuse one frozen `SolverStaticContext` across migration rounds,
  and the parent already retains one segment map for merge/validation. Build/flatten that canonical
  context; do not duplicate `difficulty_cap`, descent filtering, junction logic, base-segment
  derivation, or candidate sorting in a second parent graph traversal.
- The old design's `node_ids` serves two incompatible roles. Under `start_at_junction=True`, only the
  initial node is restricted; a route may immediately move to a non-junction. CSR therefore needs all
  graph/adjacency nodes for dense lookup plus a separate filtered start pool. A junction-only dense
  domain silently deletes valid continuations.
- Migration is also a representation boundary. Rounds 2+ receive ordinary elite `Solution` objects,
  but array tracking needs coded full base-segment identities. Either transmit a small companion
  canonical set with each elite or provide a deterministic shared directed-ID lookup. Rebuilding the
  full tuple-key `SegmentMap` in every worker defeats the memory objective.
- Keep blocking and full base identities separate. Blocking IDs enforce once-per-route reuse and omit
  reusable connectors; full IDs define Jaccard distinctness and must include them. One CSR cannot
  substitute for both.
- Preserve Python numeric semantics at the object boundary. Float64 stores Python floats exactly, but
  turn ndarray scalars back into Python `float`/`int` values when constructing `Edge`; keep edge order
  and the canonical `_route_slope_ok` path so Python 3.13 summation behavior cannot drift.
- A valid empty structure is not an unbuilt structure. Story 16.5 needed a separate
  `_adjacency_built` state to prevent rebuilding an empty table; descriptors/attached state need the
  same explicit distinction for no candidates and no allowed start nodes.
- A descriptor should be tiny, frozen, versioned, and self-validating. Prefer fixed-width NumPy dtypes
  and one/few packed blocks with explicit offsets over Python object arrays. Assert that node/key/base
  IDs fit the chosen signed dtype; never rely on silent NumPy narrowing. Mark attached arrays
  `writeable=False` after the parent finishes filling them.
- The object-to-array build may initially raise parent peak memory because graph + object adjacency +
  arrays coexist. Measure build peak and release temporary adjacency deliberately; do not report only
  lower worker steady-state memory.
- Shared pages may appear in every process's RSS even though they are physically one allocation.
  Report process-tree RSS because the epic asks for it, but pair it with private/unique memory and the
  known block size before claiming ownership scaling.
- Current `parallel.py` deliberately calls `shutdown(wait=False)` so worker heap teardown overlaps
  validation/render. Normal completion has collected every task result, but interrupt can leave
  workers reading shared arrays. Cleanup needs explicit readiness/deferred-release coordination; an
  unconditional `finally: unlink()` is unsafe. On Windows, shared blocks disappear only after every
  handle closes, while POSIX resource tracking differs.
- The reference default is now `--workers 4`, not 1. The invariant is that an explicit
  `--workers 1` keeps its old object behavior; promoting N>1 arrays changes the default execution
  backend and therefore needs the full equivalence gate.
- Use the established explicit r20 workload for A/B comparability:

  `--center 45.260,5.788 --radius 20 --angle 0 --theta 0.20 --min-climb-slope 0.20
  --difficulty-cap T4 --l-connector 50 --min-climb-ground-length 300
  --elevation-smoothing 50 --elevation-deadband 1 --j-max 0 --n 10
  --untagged-trails include --seed 44 --iter-budget 1000000 --time-budget 600
  --stagnation-iters 0 --max-descent-slope 0.4 --start-at-junction --workers 4
  --merge-interval 250000 --progress-interval 1`

- Story 16.5's post-change anchors are context, not promises: static-context construction 6.877 ms,
  reused 1k hot loop 23.006 ms, r20 four-worker query 55.38 s CLI / 69.64 s external / ~1.883 GB peak
  RSS, with a nearby repeat at 57.88 s; setup control 289.99 s CLI / 319.03 s external / ~3.779 GB.
  Reproduce method and shape; do not promise exact cross-session values.
- Python 3.13's `SharedMemory(track=True)` is suitable for related multiprocessing children sharing
  one resource tracker; `track` is ignored on Windows, where deletion occurs only after all handles
  close. `close()` releases one handle and `unlink()` is a once-per-block operation; access after
  unlink may fail depending on platform. NumPy's buffer-backed `ndarray`/`frombuffer` creates a view,
  not an owning copy, so the handle/buffer lifetime must exceed every view. Do not add a dependency or
  upgrade NumPy for this story; the locked Python 3.13 / NumPy 2.4.4 stack already provides the needed
  APIs.

### Architecture Compliance

- `solver/parallel.py` remains the orchestration owner: forced `spawn`, `SeedSequence.spawn`, round
  planning, worker-id merge, progress queue/thread, interrupt salvage, and fallback.
- Parent shared-state ownership belongs under `src/steeproute/solver/`, not `models.py`, pipeline, or
  cache. A focused `shared_state.py` is expected. A focused workers-only `array_grasp.py` is reasonable
  if it can reuse canonical scoring/RNG helpers without circular imports or copied drift.
- `solver/distinctness.py` may gain a narrow pre-canonicalized admission seam; the public
  `TopNTracker` semantics and ordinary callers remain intact. Prefer one admission implementation
  over two trackers whose replacement/tie behavior can diverge.
- The contracted graph remains lean at stage 9. Preserve `solver_graph_view` only as the conservative
  fallback for external/test graphs that do not advertise `lean=True`; do not reintroduce a production
  lean-graph rebuild.
- No on-disk cache schema, pipeline content, report format, CLI flag, constraint, RNG partition,
  iteration budget, or default parameter changes are in scope.

### Testing Requirements

- Keep test layers in separate invocations: unit, integration, and E2E have incompatible
  `conftest.py` roots. Use `uv run basedpyright <files>` and the existing ruff configuration.
- Extend `tests/benchmarks/test_parallel_speedup.py` rather than timing an unrelated proxy. Keep
  benchmark parameters explicit and equal between backends.
- Primary regression homes: `tests/unit/test_distinctness.py`; new focused shared-state/array tests;
  `tests/integration/test_parallel_grasp.py`, `test_grasp_reproducible.py`, and
  `test_solver_on_toy_graph.py`; `tests/e2e/test_parallel_workers.py` plus the pinned regression suite.
- Do not run `update-regression`. Regression goldens pin the explicit parameter sets they already
  declare; array ownership is behavior-preserving and must not move them.

### Project Structure Notes

- Expected production changes: `src/steeproute/solver/parallel.py`, a new focused
  `solver/shared_state.py`, likely `solver/array_grasp.py`, and narrow reusable seams in
  `solver/grasp.py` / `solver/distinctness.py`. `cli/query.py` should need no routing change beyond
  any internal result/fallback plumbing already owned there.
- Expected documentation change: `_bmad-output/planning-artifacts/architecture.md` Category 5a and
  this story's Dev Agent Record. A research update is optional here; Story 16.7 owns the consolidated
  epic-wide findings document.
- Keep comments to non-local invariants and refuted alternatives: junction-start vs dense-node domain,
  blocking-vs-full segment identity, and deferred cleanup under non-blocking teardown are worth
  preserving locally. Restatements of array layouts belong in names/types/tests rather than prose.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 16: Ownership-Oriented Performance Pass]
- [Source: _bmad-output/planning-artifacts/epics.md#Story 16.6: Shared-memory array solver state
  (structural, POC-gated)]
- [Source: _bmad-output/planning-artifacts/research/steeproute-shared-memory-array-solver-design-2026-07-08.md#3. Design]
- [Source: _bmad-output/planning-artifacts/research/steeproute-shared-memory-array-solver-design-2026-07-08.md#4. Bit-identity & correctness strategy (the main risk)]
- [Source: _bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md#6. Share or precompute solver static state]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-ownership-oriented-performance.md#3. Recommended Approach]
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 5 — Solver architecture]
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 11 — Testing strategy]
- [Source: _bmad-output/planning-artifacts/prd.md#Reproducibility & Determinism]
- [Source: _bmad-output/planning-artifacts/prd.md#Performance Envelope]
- [Source: _bmad-output/implementation-artifacts/16-5-solver-static-context-reuse-and-pure-python-loop-cleanup.md#Dev Notes]
- [Source: AGENTS.md#Solver / GRASP; AGENTS.md#Scale target; AGENTS.md#Dev environment]
- [Python 3.13 shared-memory documentation](https://docs.python.org/3.13/library/multiprocessing.shared_memory.html)
- [NumPy buffer-backed ndarray documentation](https://numpy.org/doc/stable/reference/generated/numpy.ndarray.html)

## Dev Agent Record

### Agent Model Used

OpenAI Codex (GPT-5)

### Debug Log References

1. Immutable oracle: generated explicitly from reference commit `6a6fa39`; 221,198-byte JSON pins
   five quality seeds, the committed Grenoble fixture, one round, and migration with full fields and
   IEEE-754 bits. Ordinary tests only consume it.
2. Committed-fixture active-call peak matrix (workers 2/4/8), sampled every 50 ms while each solve
   owned its workers: object RSS 311,900/433,024/705,752 KiB, PSS
   253,215/334,922/513,300 KiB, private 230,724/313,412/490,648 KiB; shared blob RSS
   293,660/418,568/700,252 KiB, PSS 242,436/326,526/508,118 KiB, private
   222,268/306,492/485,496 KiB; array RSS 288,564/413,816/670,968 KiB, PSS
   236,271/314,819/472,809 KiB, private 215,296/292,924/449,432 KiB. Lean pickle
   451,114 bytes; CSR 239,656 bytes. Pre-existing descendants are excluded per matrix case so a
   preceding executor's late process disappearance cannot inflate the next backend.
3. Retained-data r20 query: 58.75 s internal / 71.73 s external / 1,810,692 KiB peak RSS;
   objective 20,768.0, 10/10 routes, zero validation failures. Story 16.5 object reference was
   55.38 s / 69.64 s / 1,882,640 KiB, within recorded wall variance with 71,948 KiB lower peak.
4. Fresh back-to-back r20: setup 187.11 s internal / 211.28 s external / 3,944,404 KiB peak;
   immediate query 62.28 s / 75.54 s / 1,815,284 KiB; combined 249.39 s internal / 286.82 s
   external. The fresh OSM snapshot returned objective 20,362.4 with 10/10 valid routes.
5. r50 was not launched: 15 GiB RAM + 4 GiB swap is below the safe envelope implied by the
   maintained 1.6 GB DEM-only allocation plus the measured near-4 GB r20 setup and the much larger
   r50 graph. This is recorded as an operational safety limit, without weakening the workload.
6. Verification: 1,319 passed / 45 deselected / 95% coverage; focused benchmark matrix 12 passed;
   repository-wide BasedPyright 0 errors/warnings; Ruff and format checks pass on `src tests devtools`.
7. Review remediation: the temporary parent static context and any non-lean solver graph copy are
   released before executor construction; a weak-reference regression proves the copy is collectible
   at that boundary. The Grenoble fixture now compares raw object/array `Solution` lists and
   convergence metadata. Focused verification passed 45 integration tests and all nine active-memory
   matrix cases; BasedPyright and Ruff remained clean on the touched files.

### Completion Notes List

- Added parent-owned shared blob and packed CSR resources with validated descriptors, read-only
  attachment, process-unique readiness ACKs, startup barrier, and deferred idempotent cleanup.
- Promoted the array backend for N>1 after exact object/blob/array equivalence; N=1 remains the
  unchanged object solver path and backend selection remains private.
- Preserved candidate order, RNG stream, prefix slope gate, Python `Edge` output, migration,
  distinctness endpoints, convergence, progress, fallback, interrupt salvage, and validation state.
- Released packing-only object adjacency before workers start, measured active-call peak ownership,
  and added real-fixture object/array promotion coverage.
- Kept all cache-content-hashed models, pipeline behavior, and regression goldens untouched.

### File List

- `_bmad-output/implementation-artifacts/16-6-shared-memory-array-solver-state.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/planning-artifacts/architecture.md`
- `devtools/generate_solver_oracle_16_6.py`
- `src/steeproute/solver/array_grasp.py`
- `src/steeproute/solver/distinctness.py`
- `src/steeproute/solver/grasp.py`
- `src/steeproute/solver/grasp_core.py`
- `src/steeproute/solver/parallel.py`
- `src/steeproute/solver/shared_state.py`
- `tests/benchmarks/test_parallel_speedup.py`
- `tests/integration/fixtures/solver_object_oracle_16_6.json`
- `tests/integration/test_shared_array_solver.py`
- `tests/unit/test_distinctness.py`

### Change Log

- 2026-07-31: Implemented and promoted the shared-memory array solver backend for N>1 after exact
  oracle equivalence, lifecycle hardening, scaling measurements, and full validation.
- 2026-07-31: Resolved review findings for parent temporary-state release, active/peak ownership
  sampling, and raw Grenoble object/array equivalence.
- 2026-08-01: Marked done after review remediation and focused revalidation.
