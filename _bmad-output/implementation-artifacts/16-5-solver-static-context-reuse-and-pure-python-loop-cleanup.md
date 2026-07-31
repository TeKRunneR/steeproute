# Story 16.5: Solver static-context reuse + pure-Python loop cleanup

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want each worker / migration round to stop rebuilding immutable solver state and the hot loop to
stop doing discardable work,
so that solver startup and per-iteration cost drop with exactly equal solutions.

## Acceptance Criteria

1. A read-only `SolverStaticContext` contains the start-node pool, base-segment map, non-exempt
   segment IDs, and adjacency table. It is built from one exact contracted graph and the parameters
   that affect those values, reused across migration rounds within each worker, and cannot be
   silently paired with an incompatible graph or parameter set. The parent reuses its already-built
   segment map for merge and validation without changing standalone validator behavior.
2. The object-worker architecture remains intact: each spawned worker still owns its graph/context,
   `--workers 1` stays on the current single-process path, and Story 16.6 retains ownership of
   parent-to-worker shared memory and the CSR/array solver redesign.
3. Five behavior-preserving loop changes land and are measured separately: the discarded
   `_construct_one` objective sum is removed; exact `j_max == 0` and `j_max == 1` paths avoid general
   overlap work; general Jaccard union cardinality is derived without allocating a union set; and
   deterministic per-solution sort keys are cached rather than rebuilding sorted edge-ID tuples.
4. Pre/post results compare equal as complete `list[Solution]` values across quality-gate seeds
   `(11, 23, 37, 53, 71)`, the real Grenoble fixture, single-process solving, and migrating parallel
   solving. Existing regression route hashes and goldens remain untouched; no objective-only or
   tolerance-based comparison substitutes for exact equality.
5. The canonical `_route_slope_ok(prefix)` finalization gate remains. RNG draws, traversal order,
   directed-edge tie-breaking, worker-id merge order, progress reporting, convergence accounting,
   fallback behavior, and interrupt salvage retain their current semantics.
6. Measurements separate static-context construction from hot-loop throughput so no work is merely
   moved outside a timed region. They record each loop change, the r20 single-process 100k-iteration
   result (review anchor: 13.58 to 11.43 seconds), and real r20 before/after setup, query, combined
   wall-clock, phase timings, and peak RSS with four workers. Component benchmarks support but do not
   replace the real CLI measurements.
7. After the solver work is complete and measured, the query stages 6–7 flat-data rider is either
   implemented under exact tuple/metric/output equality or explicitly declined with a reason for
   Story 16.7. If taken, deadband hysteresis stays sequential and one final
   `vertices_resampled` tuple-list rebuild remains for rendering and validation.
8. Relevant unit, integration, E2E, benchmark, type, lint, and coverage checks pass. Architecture
   Category 5 records the context/loop decisions and measured results; Category 3 is updated only if
   the stages 6–7 rider lands.

## Tasks / Subtasks

- [x] Pin equivalence and performance baselines before changing solver code (AC: #3, #4, #6)
  - [x] Capture exact solution lists for the quality-gate seeds, real fixture, and migrating
        parallel path; record the current 100k single-process and four-worker r20 measurements.
  - [x] Extend benchmarks to time static-context construction and hot-loop execution separately.
        Keep adjacency construction inside an explicitly reported measurement.
- [x] Introduce and reuse solver static context (AC: #1, #2, #4, #5)
  - [x] Bind context validity to graph identity/content and the relevant filters:
        `start_at_junction`, `difficulty_cap`, and `max_descent_slope`.
  - [x] Replace the worker's adjacency-only process cache with one process-local context reused
        across rounds; preserve the public/default `GraspSolver` construction path.
  - [x] Thread the parent's existing segment map through the narrow validation seam while retaining
        validator self-construction for standalone calls, fallback, and interrupt paths.
- [x] Apply and measure the five loop changes independently (AC: #3, #4, #5)
  - [x] Remove the temporary objective calculation without changing `_construct_one`'s RNG or walk.
  - [x] Add `j_max` endpoint fast paths and allocation-free general Jaccard cardinality while
        preserving strict threshold semantics and the both-empty distance of `0.0`.
  - [x] Cache the existing directed-edge deterministic sort key on held entries and reuse it for
        `current_top()` and worst-entry selection.
- [x] Add regression coverage at the changed seams (AC: #1–#5, #8)
  - [x] Cover context reuse and mismatch rejection, supplied-vs-derived validation state, `j_max`
        endpoints (including both-empty and duplicates), general Jaccard equivalence, and tied
        objective ordering.
  - [x] Extend integration tests for exact static-context equivalence, migration-round reuse,
        seeded reproducibility, progress/fallback/interrupt behavior, and full `Solution` equality.
  - [x] Run pinned E2E regressions without `update-regression`; distinguish any reproduced
        pre-existing realistic-tier `params_hash` issue from route/output drift.
- [x] Decide the stages 6–7 flat-data rider after solver measurement (AC: #7)
  - [x] If cheap enough, remove only the intermediate representation round-trips, preserve public
        pure stage APIs and sequential deadband behavior, and pin final tuples/metrics bit-exact.
  - [x] Otherwise record the measured reason for declining it and leave the residual to Story 16.7.
- [x] Re-measure, document, and close out (AC: #6, #8)
  - [x] Run back-to-back representative r20 setup/query measurements; report touched phases and
        total honestly, including machine-noise bands and any cost shifted between phases.
  - [x] Update Architecture Category 5, and Category 3 only if the rider lands; record individual
        loop gains rather than attributing the combined POC result to every change.

## Dev Notes

- `GraspSolver.__init__` currently rebuilds `base_segment_id_map`, the sorted/optionally-junction
  node pool, and `non_exempt_base_segment_ids` for every migration round. `parallel.py` caches only
  adjacency process-locally; extend that established ownership seam rather than sharing object state
  across processes.
- Context inputs are not all graph-only. The node pool depends on `start_at_junction`; adjacency
  depends on `difficulty_cap` and `max_descent_slope`; segment maps and non-exempt IDs depend on the
  graph. The existing adjacency injection warns that mismatch silently corrupts output, so the
  broader context needs an enforceable compatibility contract.
- Keep derived context under `solver/`, not cache-content-hashed `models.py`. Prefer the existing
  solver modules; add a focused module only if `grasp.py` becomes materially harder to navigate.
- `run_parallel_grasp` already builds one parent segment map for every `_merge`. Story 16.1's
  validator `_GraphContext` independently derives the same map. Reuse the map through a narrow
  optional seam; do not expose or pass the whole solver context into validation.
- The `j_max == 0` fast path must treat two empty canonical sets as overlapping even though
  `empty.isdisjoint(empty)` is true, because the defined Jaccard distance for both-empty is `0.0`.
  The general cardinality rewrite needs the same zero-union branch. At `j_max == 1`, duplicates
  remain admissible because the strict overlap threshold is zero.
- Cache `_sort_key` exactly as defined today: `(-objective, sorted directed (u, v, key) IDs)`.
  Substituting canonical base-segment IDs would change equal-objective ordering and migration merges.
- Do not remove `_route_slope_ok(prefix)`. This project targets Python 3.13; built-in float `sum`
  uses a higher-accuracy algorithm, so the manual cumulative arrays in `_best_theta_prefix` are not
  generally bit-identical to the canonical `route_avg_gradient`.
- Preserve the `_construct_one` test seam used by in-process interrupt tests. Its temporary objective
  is discardable, but empty-walk filtering and the returned value's contract must stay explicit.
- The existing throughput benchmark constructs the solver outside the measured callable while
  adjacency is lazily built inside `run()`. Add explicit startup/context and steady-loop measures
  before refactoring this boundary, or a moved adjacency build will look like a false speedup.
- Use the established r20 query workload with its parameters explicit for A/B comparability:

  `--center 45.260,5.788 --radius 20 --angle 0 --theta 0.20 --min-climb-slope 0.20
  --difficulty-cap T4 --l-connector 50 --min-climb-ground-length 300
  --elevation-smoothing 50 --elevation-deadband 1 --j-max 0 --n 10
  --untagged-trails include --seed 44 --iter-budget 1000000 --time-budget 600
  --stagnation-iters 0 --max-descent-slope 0.4 --start-at-junction --workers 4
  --merge-interval 250000 --progress-interval 1`

- Measure actual setup and query CLI invocations, including total wall-clock and peak RSS. The
  previous story showed solver/machine variance can move CLI total opposite to the touched phase;
  use back-to-back runs and report that rather than extrapolating a component benchmark.
- The stages 6–7 rider may eliminate only two intermediate tuple/array round-trips. One final tuple
  rebuild is required for the renderer/validator, and deadband's sequential hysteresis plus the
  box-equals-curve contract are load-bearing. Declining the rider is an accepted outcome.
- Scope excludes shared graph blobs/CSR worker state (16.6), changes to RNG partitioning or iteration
  budgets, quality/constraint retuning, custom Overpass ingestion, and silent golden rebakes.
- Environment: run unit and integration tests separately; use `uv run basedpyright <files>`. If the
  known stale editable build appears after a commit, use `uv sync --native-tls`, never reinstall.

### Project Structure Notes

- Expected core changes: `src/steeproute/solver/grasp.py`,
  `src/steeproute/solver/distinctness.py`, `src/steeproute/solver/parallel.py`, plus the narrow
  validation/orchestration seam in `validator.py` and `cli/query.py`.
- Expected tests: `tests/unit/test_distinctness.py`, `test_grasp_construction.py`,
  `test_validator.py`; `tests/integration/test_parallel_grasp.py`,
  `test_grasp_reproducible.py`, solver quality tests; existing E2E parallel/golden suites.
- Extend `tests/benchmarks/test_solver_throughput.py` and `test_parallel_speedup.py` with locally
  pinned parameters. Do not import CLI defaults or regression pins into benchmark fixtures.
- If the rider lands, keep its implementation in the existing pipeline orchestration/smoothing/
  metrics modules and its tests beside the current operationalize, smoothing, and climbs tests.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 16.5: Solver static-context reuse +
  pure-Python loop cleanup]
- [Source: _bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md#6.
  Share or precompute solver static state]
- [Source: _bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md#7.
  Pure-Python solver-loop cleanup]
- [Source: _bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md#10.
  Keep query elevation data flat across stages 6–7]
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 5 — Solver architecture]
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 6 — Validation architecture]
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 11 — Testing strategy]
- [Source: _bmad-output/planning-artifacts/prd.md#Reproducibility & Determinism]
- [Source: _bmad-output/planning-artifacts/prd.md#Performance Envelope]
- [Source: _bmad-output/implementation-artifacts/16-3-geometry-optional-query-load-and-schema-v3-cache.md]
- [Source: AGENTS.md#Solver / GRASP; AGENTS.md#Scale target; AGENTS.md#Dev environment]
- [Source: Python 3.13 built-in `sum` documentation](https://docs.python.org/3.13/library/functions.html#sum)

## Dev Agent Record

### Agent Model Used

OpenAI Codex (GPT-5)

### Debug Log References

1. Focused pre-change baselines: 62 unit tests and 33 integration tests passed; committed-fixture
   solver benchmark mean 38.071 ms / 1k iterations. Exact quality-seed outputs and both real r20
   output directories were retained for after-comparison.
2. Real r20 pre-change measurements: 100k single-worker CLI 37.60 s / external 46.98 s / peak RSS
   1,793,200 KiB; four-worker 1M CLI 64.08 s. All parameters were explicit and output objectives
   were 15,892.2 / 20,768.0 respectively.
3. The legacy trial-cache manifest was locally migrated from schema 2 to schema 3, following Story
   16.3's compatibility procedure, solely to load the retained r20 baseline. `.trial-cache` is
   ignored and is not a product change.
4. Component after-benchmarks (committed Grenoble fixture): static-context construction 6.877 ms;
   1k reused-context hot loop 23.006 ms; ordinary 1k lazy-build run 27.696 ms. Individual isolated
   old/new work: discarded objective sum removed 4.129 µs per ten 12-edge walks; `j_max=0`
   33.879→4.935 µs; `j_max=1` 33.481→0.030 µs; general Jaccard 25.517→12.452 µs; cached ordering
   32.983→3.275 µs.
5. Real r20 after: 100k solver-only 9.557 s (review anchor 13.580→11.431 s), objective
   15,892.235169732128; single-worker CLI 33.99 s / external 46.15 s / peak RSS 1,748,724 KiB.
   The before/after 20-file output directories were byte-identical (`diff -rq` empty; matching
   SHA-256 for every HTML/JSON file).
6. Real r20 four-worker after: CLI 55.38 s / external 69.64 s / peak RSS 1,882,640 KiB, objective
   20,768.0. All 20 files were byte-identical to the 64.08 s pre-change run. A second query directly
   after setup was 57.88 s CLI / 72.66 s external / 1,882,956 KiB, establishing local variance.
7. Real r20 setup control (solver code does not enter setup): 289.99 s CLI / 319.03 s external /
   3,778,548 KiB peak RSS. Phases were OSM 125.87, filter 13.43, smoothing 0.55, resampling 19.14,
   DEM 119.48, sampling 8.11, write 3.38 s. IGN returned one HTTP 400 and retried, so network stages
   are not attributed to this story. Recorded setup reference 299.28 s + pre-change query 64.08 s
   gives 363.36 s combined CLI; live setup + immediate query gives 347.87 s.
8. Stages 6–7 rider explicitly declined to Story 16.7: the untouched phase measured 9.54–9.78 s,
   while landing it here would expand the exact-output surface into sequential deadband hysteresis
   and the renderer's required final tuple-list reconstruction. Category 3 therefore remains unchanged.
9. Verification: 1,264 passed / 35 deselected / 96% total coverage; focused benchmarks 8 passed;
   repository-wide BasedPyright 0 errors/warnings; `ruff check` and format check pass on `src tests
   devtools`. A literal `ruff check .` additionally scans the user's unrelated untracked `.agents/`
   plugin tree and reports five pre-existing import/unused issues there; those files were not changed.
10. Code-review follow-up: empty filtered adjacency is now cached as a completed context, an empty
    junction start pool exposes validation state without building adjacency, and the context owner's
    public adjacency alias is read-only. The 67 focused unit/integration tests passed; BasedPyright,
    Ruff, and `git diff --check` were clean on the touched files.

### Completion Notes List

- Added a frozen, read-only `SolverStaticContext` with fail-closed graph/filter compatibility and
  preserved the ordinary one-worker/public solver path.
- Replaced the worker adjacency-only cache with one process-local context, and reused the parent's
  existing base-segment map for merge and validation, including normal, fallback, and interrupt paths.
- Landed all five loop cleanups with exact output semantics: no discarded whole-walk sum, endpoint
  distinctness paths, allocation-free union cardinality, and cached directed sort keys. The canonical
  `_route_slope_ok` gate and every order/RNG/progress/convergence behavior remain intact.
- Exact full-`Solution` equality now runs for quality seeds 11/23/37/53/71 across ordinary and reused
  contexts; real-fixture, migration, fallback, interrupt, and pinned-golden suites all pass without
  `update-regression`.
- Updated Architecture Category 5 with ownership, compatibility, loop decisions, and measurements;
  Category 3 was intentionally not changed because the stages 6–7 rider was declined.
- Closed all three review findings with explicit adjacency build state, cheap segment-map access,
  immutable public mapping views, and focused edge-case regression coverage.

### Change Log

- 2026-07-31: Implemented and measured solver static-context reuse and pure-Python loop cleanup;
  added exact regression coverage and moved the story to review.
- 2026-07-31: Resolved static-context edge-case review findings and marked the story done.

### File List

- `_bmad-output/implementation-artifacts/16-5-solver-static-context-reuse-and-pure-python-loop-cleanup.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/planning-artifacts/architecture.md`
- `src/steeproute/cli/query.py`
- `src/steeproute/solver/distinctness.py`
- `src/steeproute/solver/grasp.py`
- `src/steeproute/solver/parallel.py`
- `src/steeproute/validator.py`
- `tests/benchmarks/test_distinctness_throughput.py`
- `tests/benchmarks/test_solver_throughput.py`
- `tests/integration/test_grasp_junction_start.py`
- `tests/integration/test_parallel_grasp.py`
- `tests/integration/test_grasp_reproducible.py`
- `tests/integration/test_solver_on_toy_graph.py`
- `tests/unit/test_distinctness.py`
- `tests/unit/test_validator.py`
