# Story 16.7: r20/r50 re-measure and what-next close-out

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want a consolidated before/after across setup + query and a fresh phase split,
so that the epic's end-to-end effect is recorded from measurement and any residual deep work is scoped
from evidence.

## Acceptance Criteria

1. **The final measurement has reproducible provenance.** It records the committed code revision,
   dirty-worktree state, machine/OS/CPU/RAM/swap and runtime versions; uses center `45.260,5.788`,
   radius 20 km, angle 0, one identified prepared-data snapshot, and the full explicit query workload
   in Dev Notes. Setup and its immediate query use the same isolated cache. Network/cache-hit state,
   node/edge counts, output objective, route count, and validation outcome are recorded so different
   data or a fast-but-broken run cannot masquerade as an optimization result.
2. **One real r20 setup + immediate query trace supplies the final whole-execution evidence.** The
   trace records every setup/query stage line, each CLI-reported wall, each external process wall,
   their setup+query sums, and setup/query peak RSS separately. It compares against the review anchors
   (setup 299.28 s; query 80.02 s CLI / 90.82 s external / 2,674,232 KiB peak) without treating
   cross-snapshot or cross-session differences as causal. External wall includes worker teardown;
   peaks are never summed.
3. **One real r50 attempt resolves the scale question empirically.** Run the identical explicit
   setup/query workload at radius 50 on the current machine despite the OOM risk. If setup succeeds,
   run the immediate query against that exact entry; if either process OOMs, stop the r50 attempt and
   treat the failure as valid evidence. Record the last completed stage, elapsed wall, peak RSS,
   exit/signal and available OS OOM diagnostics. Do not manually retry after an OOM, weaken
   parameters, substitute a smaller proxy, or present an extrapolation as measurement.
4. **A dated findings update under `_bmad-output/planning-artifacts/research/` consolidates Epic 16.**
   It gives the final r20 phase split and cumulative setup/query effect, reconciles the per-story
   evidence, and keeps the review's demonstrated combined wins (query batch -12.69 s CLI; warm
   ingestion -32.85 s) distinct from isolated components, later controlled A/Bs, noisy totals, and
   fresh-snapshot results. It does not arithmetically add deltas from different graphs/sessions.
5. **The findings close with explicit evidence-based disposition of residual work.** Decide whether
   the custom Overpass-JSON-to-graph parser (S5-deep) and per-stage multiprocess pipeline
   parallelization justify a follow-on correct-course or should stop/defer. Also inventory the two
   handed-off smaller residuals: consuming/vendoring `simplify_graph` (Story 16.4) and carrying flat
   elevation data across query stages 6-7 (Story 16.5). A recommendation is not authorization to
   create or implement the follow-on in this story.
6. **This is a measurement/documentation close-out only.** No files under `src/`, `tests/`, fixture
   caches, regression goldens, `pyproject.toml`, or `uv.lock` change, and `update-regression` is not
   run. `future-ideas.md` reflects the residual decision; Architecture Category 5a's pre-review
   Story 16.6 figures are reconciled with the final active-call measurements rather than silently
   choosing one record; stale setup/query stage ownership in Categories 4/data-flow is corrected to
   match Category 3 and current code. Documentation checks pass and the story moves through review
   before Epic 16 is marked done.

## Tasks / Subtasks

- [x] Establish provenance and a safe measurement workspace (AC: #1, #3, #6)
  - [x] Record revision/worktree and machine/runtime capacity; confirm adequate disk and isolate
        cache, output, stdout/stderr, and `/usr/bin/time -v` records from user/fixture caches.
  - [x] Prepare separate r20/r50 cache and output roots so a failed r50 attempt cannot damage the r20
        evidence or user/fixture caches.
- [x] Capture the final real r20 setup and immediate query (AC: #1, #2)
  - [x] Run setup with the explicit area/preparation inputs below, then query that exact new entry
        with every solver/query parameter explicit.
  - [x] Extract stage lines, internal/external wall, peaks, snapshot/graph identity, routes,
        objective, and validation result; report failures or service retries as evidence.
- [x] Attempt the full r50 setup and, if setup succeeds, its immediate query once (AC: #3)
  - [x] Use the same explicit workload with only radius and isolated paths changed; capture partial
        phase/timing/memory evidence if it fails.
  - [x] After an OOM, record the failure and stop—do not rerun with the same or weakened workload.
- [x] Reconcile the Epic 16 evidence (AC: #4)
  - [x] Build a source-attributed table for the review anchors, Stories 16.1-16.6, and final trace;
        label retained-data, warm/offline A/B, and fresh-snapshot results distinctly.
  - [x] Explain variance and changed data before drawing cumulative conclusions; never sum isolated
        savings or substitute benchmark results for whole execution.
- [x] Write the dated findings/decision document (AC: #4, #5)
  - [x] Record provenance, phase splits, cumulative effect, r50 disposition, and ranked residuals.
  - [x] Give an explicit correct-course-vs-stop recommendation for S5-deep and per-stage
        multiprocessing; record the existing `simplify_graph` and stages 6-7 rider dispositions.
- [x] Synchronize planning truth and close out (AC: #6)
  - [x] Update `future-ideas.md`; reconcile Architecture Category 5a's Story 16.6 matrix with its
        later review-remediated record and correct stale stage-boundary/default wording; keep
        production/test/fixture files untouched.
  - [x] Run documentation/diff checks, update sprint status to review during implementation, and
        mark Story 16.7/Epic 16 done only after review.

## Dev Notes

### Developer Context and Guardrails

- **The deliverable is evidence and a decision, not another optimization.** Resist fixing whichever
  phase is largest. New implementation work must enter through correct-course after this story.
- Use current stage timers; the epic asks for a fresh phase split from real CLI stage lines, not a new
  profiler campaign. A profiler is optional only if a decision cannot be made from existing phase
  evidence.
- The fixed review baseline used retained r20 data at reference commit `4380970`. The final Story
  16.6 fresh trace used a new OSM snapshot and therefore changed objective 20,768.0 to 20,362.4.
  Compare phase shape and whole-run experience, but do not attribute that output difference to code.
- Historical wall drift on this machine reached 30-40%. Record nearby repeats/controls where
  practical and prefer stable targeted stage/peak evidence over a lone total. Network wait and
  service retries are real wall-clock but not CPU optimization credit.
- Peak RSS is per process tree/run. Shared solver pages can be multiply counted in RSS; ownership
  claims must retain Story 16.6's private/PSS/block-size evidence rather than infer private memory
  from the final CLI peak.

### Measurement Workload

Setup shape, with the same cache used by the immediate query:

```text
steeproute-setup --center 45.260,5.788 --radius 20 --angle 0
  --untagged-trails include --dem-version ign-rgealti-highres --dem-fetch-workers 4
  --osm-age-warn-days 90 --cache-dir <scratch-cache>
```

Query workload (keep explicit even where values match current defaults):

```text
steeproute --center 45.260,5.788 --radius 20 --angle 0
  --theta 0.20 --min-climb-slope 0.20 --difficulty-cap T4 --l-connector 50
  --min-climb-ground-length 300 --elevation-smoothing 50 --elevation-deadband 1
  --j-max 0 --n 10 --untagged-trails include --seed 44 --iter-budget 1000000
  --time-budget 600 --stagnation-iters 0 --max-descent-slope 0.4 --start-at-junction
  --workers 4 --merge-interval 250000 --progress-interval 1
  --osm-age-warn-days 90 --cache-dir <scratch-cache> --output-dir <scratch-results>
```

Wrap each CLI separately with `/usr/bin/time -v` on WSL/Linux and retain stdout/stderr. Capture setup
stages `osm-load` (including fetch/build split), `trail-filter`, `polyline-smoothing`, `resampling`,
`dem-resolve`, `elevation-sampling`, `cache-write`; query stages `load-prepared-area`,
`elevation-reshape`, `trail-filter`, `climb-detection`, `climb-contraction`, solve/progress, and
`validate-render`. Record internal and external sums; do not sum peak memory.

For a controlled network-free replay, seed only `osmnx/` and `dem/` into the scratch cache; copying
`areas/` or `index.json` would turn setup into a cache hit and invalidate the trace. An empty scratch
root is a valid fresh-network run, but must be labeled separately; `--force-refresh` is not needed.

### Previous Story Intelligence

| Evidence | Result to reconcile | Interpretation constraint |
|---|---|---|
| Review (`4380970`) | setup 299.28 s; query 80.02/90.82 s, 2.67 GB | fixed anchor, not a cross-machine promise |
| 16.1 | query 110.19 -> 78.84 s; peak 2.434 -> 1.832 GB | exact 20-file identity; untouched-stage noise |
| 16.2 | targeted setup 79.29 -> 50.02 s; total 330.94 -> 310.21 s | graph-identical; setup peak unchanged |
| 16.3 | load 3.23 -> 2.49 s; payload 165.8 -> 121.2 MB | total query was noise-dominated |
| 16.4 | `osm-load` 191.39 -> 129.93 s; total 250.94 -> 190.47 s | graph-identical; whole-setup peak unchanged |
| 16.5 | four-worker query 64.08 -> 55.38 s; external 69.64 s | exact output; stages 6-7 residual 9.54-9.78 s |
| 16.6 | fresh setup 187.11/211.28 s; query 62.28/75.54 s | combined 249.39/286.82 s; r50 not attempted |

Story 16.6's final review-remediated active-call matrix records lean pickle 451,114 bytes and object
private memory 230,724/313,412/490,648 KiB at workers 2/4/8. Architecture Category 5a currently
contains earlier values (451,158 bytes and 232,472/321,028/497,836 KiB). Reconcile provenance and
retain the final record; do not silently overwrite unexplained numbers.

### Architecture Compliance

- Preserve the PRD's <=10-minute typical r10-query target as a design budget, not an SLO; r50 is a
  later scale ambition, not a shipping gate. Do not retune quality, constraints, budgets, seeded
  determinism, or RNG partitioning to improve a chart.
- S5-deep is a new parser, not Story 16.4's scoped osmnx ownership adapter. Judge it against the
  landed residual: adapted `simplify_graph` was 83.37 s / 69% of assembly, while the parser must
  reproduce directionality, tags, way IDs, keys, ordering, geometry, and component policy.
- Per-stage multiprocessing is not one generic lever. Rank only measured residual stages after
  ownership/vectorization, including duplication, transfer, determinism, and memory costs. DEM fetch
  is already threaded; do not count network concurrency as CPU parallelization.
- The stages 6-7 rider has only a roughly 2-3 s ceiling at demo parameters and crosses sequential
  deadband hysteresis plus the renderer's mandatory tuple rebuild. The `simplify_graph` fork would
  vendor complex third-party code without moving the measured end-to-end peak. New evidence must
  overturn those existing stop/defer rationales before either is promoted.

### Testing Requirements

- No new automated tests are expected because behavior does not change. The real setup/query run,
  valid route set, source-attributed tables, link review, `git diff --check`, and an empty
  `git diff -- src tests pyproject.toml uv.lock` are the gates.
- Do not run benchmarks under coverage, do not regenerate fixtures, and do not run
  `update-regression`. If unrelated verification is needed, keep unit/integration/e2e invocations
  separate per repository guidance.

### Project Structure Notes

- New during implementation: one dated findings document under
  `_bmad-output/planning-artifacts/research/`.
- Modified: this story, `sprint-status.yaml`, `future-ideas.md`, and only if needed to reconcile the
  stale matrix, `architecture.md` Category 5a.
- Untouched: production source, tests/benchmarks, fixture caches, goldens, dependency files, and the
  authoritative Epic 16 wording in `epics.md`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 16: Ownership-Oriented Performance Pass; Story 16.7]
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-ownership-oriented-performance.md#Implementation Handoff]
- [Source: _bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md#Measurement provenance and rules; Expected combined headroom]
- [Source: _bmad-output/planning-artifacts/research/steeproute-next-optimization-pass-handoff-2026-07-05.md#5. Work items - setup side; 7. Suggested sequencing]
- [Source: _bmad-output/planning-artifacts/architecture.md#Category 5 - Solver architecture]
- [Source: _bmad-output/planning-artifacts/prd.md#Performance Envelope; Reproducibility & Determinism]
- [Source: _bmad-output/implementation-artifacts/16-1-query-orchestration-owned-filter-lean-contracted-graph-validation-context.md; 16-2-setup-owned-data-cleanup-and-smoothing-resampling-fusion.md#Completion Notes List]
- [Source: _bmad-output/implementation-artifacts/16-3-geometry-optional-query-load-and-schema-v3-cache.md; 16-4-osmnx-in-place-component-consume-ingestion-adapter.md#Completion Notes List]
- [Source: _bmad-output/implementation-artifacts/16-5-solver-static-context-reuse-and-pure-python-loop-cleanup.md; 16-6-shared-memory-array-solver-state.md#Debug Log References]
- [Source: _bmad-output/implementation-artifacts/deferred-work.md#Deferred from: code review of spec-dem-auto-download.md]
- [Source: AGENTS.md#Scale target; AGENTS.md#Solver / GRASP; AGENTS.md#Dev environment]

## Dev Agent Record

### Agent Model Used

OpenAI Codex (GPT-5)

### Implementation Plan

1. Pin provenance and isolate r20/r50 caches, outputs, and raw timing records.
2. Run a controlled full-pipeline r20 setup and its exact explicit query.
3. Attempt the identical r50 workload once; stop after any OOM and retain partial evidence.
4. Reconcile Epic 16 measurements, publish the dated decision, and synchronize current-truth docs.
5. Run the required regression and documentation gates, then move the story to review.

### Debug Log References

1. Measurement workspace: `/tmp/steeproute-16-7-rebqx4`; source revision `2abf253`; WSL2,
   14-logical-core Intel Core Ultra 7 155U, 15 GiB RAM + 4 GiB swap. The r20 cache was seeded with
   source caches only (`osmnx/`, `dem/`), never `areas/`/`index.json`.
2. r20 setup: cache key `094e2012fa5e4a69`, 131,793 nodes / 327,911 edges, 128,306,835-byte
   `graph.pkl`; 127.64 s CLI / 144.23 s external / 3,346,052 KiB peak RSS. OSM fetch was a 1.69 s
   cache hit and graph build 78.62 s. r20 query: 48.84 s CLI / 60.93 s external / 1,818,248 KiB peak,
   objective 20,768.0, 10/10 routes, zero validation failures. Combined wall: 176.48 s CLI /
   205.16 s external.
3. One-shot r50 setup: cache key `1372a4b1765b1656`, 348,974 nodes / 856,673 edges,
   442,629,004-byte `graph.pkl`; 1,597.37 s CLI / 1,750.66 s external / 13,393,268 KiB peak RSS.
   Nine Overpass responses were downloaded; the 100-tile DEM fetch had one built-in timeout retry.
   Immediate r50 query: 110.29 s CLI / 138.82 s external / 5,332,724 KiB peak, objective 26,130.7,
   10/10 routes, zero validation failures. Combined wall: 1,707.66 s CLI / 1,889.48 s external. Both
   processes exited 0 without swap or OOM; no manual retry or parameter weakening occurred.
4. Verification: 1,319 passed / 45 deselected / 95% coverage; Ruff lint and format checks pass on
   `src tests devtools`; repository-wide BasedPyright reports 0 errors/warnings; YAML parse,
   `git diff --check`, and the empty production/test/dependency diff all pass.

### Completion Notes List

- Captured real, isolated r20 and r50 setup-plus-immediate-query traces with full explicit inputs,
  stage lines, internal/external wall, per-process peaks, cache identity, and valid route outcomes.
- Consolidated the review and Stories 16.1–16.6 without adding deltas across snapshots or sessions.
- Recommended a POC-gated correct-course proposal for S5-deep; deferred generic per-stage
  multiprocessing and the stages 6–7 rider, and stopped the separate vendored `simplify_graph` fork.
- Reconciled Architecture's stage ownership, worker-default wording, and final Story 16.6 active-call
  memory matrix; no production, test, fixture, golden, dependency, or lockfile changed.

### File List

- `_bmad-output/implementation-artifacts/16-7-r20-r50-re-measure-and-what-next-close-out.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/planning-artifacts/architecture.md`
- `_bmad-output/planning-artifacts/future-ideas.md`
- `_bmad-output/planning-artifacts/research/steeproute-ownership-performance-closeout-2026-08-03.md`

### Change Log

- 2026-08-03: Captured the final r20 and first full r50 setup/query traces, published the Epic 16
  evidence ledger and residual decisions, and synchronized current-truth planning documentation.
- 2026-08-03: Marked done without a separate code-review pass at the user's direction because the
  story changed documentation only and all validation gates had already passed.
