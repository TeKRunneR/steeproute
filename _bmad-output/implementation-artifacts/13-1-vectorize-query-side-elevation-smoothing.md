# Story 13.1: Vectorize query-side elevation smoothing (stage 6)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the global Laplacian elevation smoothing to stop iterating whole-graph passes in Python,
so that the dominant query-side cost on large areas drops without changing route results.

## Acceptance Criteria

1. **Given** stage 6 currently runs ≈round(window²/6) whole-graph Laplacian passes (~417 at the 50 m
   default) as per-node Python iteration on every query, **when** the diffusion is reformulated as
   sparse-matrix/array operations — same math, same iteration count, same smoothed profile — **then**
   results are numerically equivalent and the regression-golden suite passes untouched; if reordered float
   arithmetic flips any golden edge-set, the story instead carries one documented rebake via
   `update-regression` with the equivalence argument recorded (Story 9.3/12.3 precedent).
2. Measured stage 6–7 wall-clock on the Chartreuse r10 reference workload drops materially (analysis
   attributes ~27% of the 40 s run), recorded in the story close-out.
3. Solver, validator, and output interfaces are unchanged.

## Tasks / Subtasks

- [x] Task 1: Vectorize `graph_smooth_elevation`'s Jacobi diffusion (AC: #1, #3)
  - [x] Replace the per-node/per-edge Python dict-and-loop relaxation (`smoothing.py:187-227`) with
    array/vectorized operations — same node/interior-vertex state model, same `iters` count, same
    `_DIFFUSION_LAMBDA` relaxation, same neighbor-averaging rule (unweighted mean over incident-edge
    adjacent values)
  - [x] Preserve the function signature, docstring contract, and the `window <= 1.0` no-op short-circuit
    (`smoothing.py:176-178`) exactly
  - [x] Preserve exact endpoint/`(lat, lon)` pass-through and the "new graph, input never mutated" contract
  - [x] Keep the public interface (`graph_smooth_elevation(graph, strength_m)`) and its call site in
    `operationalize_graph` (`pipeline/__init__.py:247`) unchanged — this is a same-module internal rewrite,
    not an interface or call-site change
- [x] Task 2: Verify numerical equivalence and update tests (AC: #1)
  - [x] Run the existing `test_graph_smooth_elevation_*` suite in `tests/unit/test_smoothing.py` (lines
    478-580ish: no-op, flat-unchanged, never-increases-adjacent-delta, shares-node-value,
    preserves-lat-lon, does-not-mutate, preserves-attribute-contract) unmodified where possible
    — all pass UNMODIFIED; two new scalar-reference equivalence tests added (see Dev Agent Record)
  - [x] If float-reordering causes exact-value tests to diverge at the ULP level, tighten to
    tolerance-based comparison rather than deleting the assertion's intent — NOT NEEDED: results are
    bit-identical, no assertion was touched
  - [x] Run `uv run pytest tests/e2e/test_pinned_regressions.py` first with no code changes assumption — if
    any golden edge-set flips, that is the trigger for the one documented rebake in Task 3; if all pass
    byte-identical, no rebake is needed and Task 3 is skipped entirely — ALL PASS byte-identical (fast,
    realistic `-m slow`, and flag-on tiers)
- [x] Task 3 (conditional — only if goldens flip): Documented golden rebake (AC: #1) — SKIPPED per the
  Task 2 gate: all goldens pass untouched (the naive first cut DID drift them by 1 ULP; root cause found
  and fixed via compensated summation instead of rebaking — see Dev Agent Record)
- [x] Task 4: Measure and record the gain (AC: #2)
  - [x] Reproduce the Chartreuse r10 reference workload from
    `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` (seed 44, n 10, l-connector 50,
    smoothing 50, descent-cap 0.4, start-at-junction) and measure wall-clock before/after
  - [x] Record the before/after numbers and the stage 6–7 share in the Dev Agent Record and commit message
- [x] Task 5: Gates + status
  - [x] `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest
    --cov` green; update sprint-status

## Dev Notes

### What this story touches and why

`graph_smooth_elevation` (`src/steeproute/pipeline/smoothing.py:144-235`) is a Jacobi relaxation over the
whole query graph: one shared elevation value per node, private interior-vertex values per edge, `iters =
round(window²/6)` sweeps (≈417 at the 50 m default / 10 m resample spacing). The current implementation is
pure-Python `dict`-of-floats with nested loops over `node_adj` and `edge_keys` per iteration — the analysis
in `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` (§"What next") attributes ~27% of
the 40 s Chartreuse r10 run to stages 6–7, headlined by this loop. This is the #1-ranked lever in that
document, sequenced first in Epic 13 because it is a pure compute-shape fix with no cache-boundary or
architecture-decision entanglement.

**The math must not change** — same relaxation rule (`(1-λ)·old + λ·mean(neighbors)`), same `λ=0.5`, same
`iters` derivation, same node/interior state split. Only the *mechanism* moves from Python loops to
array-oriented operations (numpy is already a project dependency, `pyproject.toml`; no new dependency is
implied by "vectorize" — evaluate whether numpy alone suffices before considering scipy, which is **not**
currently a dependency and would need a `pyproject.toml` addition and rationale if introduced).

### Numerical-equivalence risk (AC #1's conditional branch)

Float reordering is the known risk: vectorized summation (e.g. reducing over a numpy array) sums in a
different order than the current Python `sum(neigh)` per node, so bit-exact equality is not guaranteed even
though the analytical result is unchanged. This story's AC already carries the escape hatch used by Stories
9.3 and 12.3: if any golden edge-set flips, that's a documented rebake, not a bug. Run the pinned-regression
suite early (Task 2) to know immediately which branch applies — do not assume a rebake is needed.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest
  --cov` (~4:15 typical runtime; much slower usually means a test hit the network).
- `tests/unit/test_smoothing.py` already has direct unit coverage of `graph_smooth_elevation`'s properties
  (no-op below spacing, flat-input unchanged, never increases max adjacent delta, shared node value across
  incident edges, lat/lon pass-through, no input mutation, attribute-contract preservation) — these are the
  correctness net for the rewrite; keep them passing, tightening only float-exact assertions to
  tolerance-based if the reorder trips them.
- `uv` Windows build flake: a stale editable build after a commit or `pyproject.toml` edit makes `uv run`
  hit a corporate-TLS cert error (~43 `test_cli_smoke` failures as the symptom). Fix once with `uv sync
  --native-tls`, then use `uv run --no-sync` for the rest of the session.

### Project Structure Notes

- **Modified:** `src/steeproute/pipeline/smoothing.py` (`graph_smooth_elevation` internals only), possibly
  `tests/unit/test_smoothing.py` (only if float-exact assertions need tolerance), possibly
  `tests/e2e/goldens/*.json` (only if the rebake branch triggers), sprint-status.
- **Untouched:** `graph_deadband_elevation`, `smooth_polylines`, `resample_edges` (stages 3-4, setup-side —
  out of scope), `operationalize_graph`'s call shape (`pipeline/__init__.py:220-249`), `compute_edge_metrics`
  (stage 7, `pipeline/climbs.py`), solver, validator, output rendering, CLI surface.
- Out of scope: Story 13.2 (cache deserialization), 13.3 (recompute-avoidance/cache-boundary), 13.4 (lazy
  imports) — don't drift into those levers even if they look adjacent while touching this file.

### References

- [Source: epics.md §Epic 13 preamble + §Story 13.1](_bmad-output/planning-artifacts/epics.md) — AC
  source-of-truth, epic-level phase-split context and rebake contingency
- [Source: research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md §"What next — per-lever
  assessment" item 1](_bmad-output/planning-artifacts/research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md)
  — the ~27% attribution, "numpy/scipy-shaped" framing, Chartreuse r10 reference-workload params
- [Source: src/steeproute/pipeline/smoothing.py:144-235](src/steeproute/pipeline/smoothing.py) —
  `graph_smooth_elevation`, the function this story rewrites internally
- [Source: src/steeproute/pipeline/__init__.py:220-249](src/steeproute/pipeline/__init__.py) —
  `operationalize_graph`, the unchanged call site and box==curve contract
- [Source: tests/unit/test_smoothing.py:478-580](tests/unit/test_smoothing.py) — existing
  `graph_smooth_elevation` unit-test suite (the correctness net)
- [Source: src/steeproute/regression.py](src/steeproute/regression.py) — `update-regression` entry point,
  fixture tiers, commit-rationale convention
- [Source: _bmad-output/implementation-artifacts/12-3-batched-rng-draws-with-documented-golden-rebake.md](_bmad-output/implementation-artifacts/12-3-batched-rng-draws-with-documented-golden-rebake.md)
  and [9-3-revalidation-golden-rebake-and-doc-sync.md](_bmad-output/implementation-artifacts/9-3-revalidation-golden-rebake-and-doc-sync.md)
  — the rehearsed conditional-rebake workflow this story reuses if triggered
- [Source: pyproject.toml](pyproject.toml) — `numpy>=1.26,<3` already a dependency; scipy is not

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Gates (all green):**

```
tests/unit/test_smoothing.py                  → 41 passed (39 existing unmodified + 2 new)
tests/e2e/test_pinned_regressions.py          → 4 passed (fast tier, byte-identical goldens)
tests/e2e/test_pinned_regressions.py -m slow  → 4 passed (realistic tier, byte-identical goldens)
pytest --cov (default markers)                → 844 passed, 12 deselected in 3:24 (96% cov;
                                                 includes the 2 flag-on golden tests)
ruff check src tests                          → All checks passed!
ruff format src tests                         → applied; --check clean
basedpyright (whole project)                  → 0 errors, 0 warnings, 0 notes
```

**Measurements (Chartreuse r10 entry `c5bbfb802f3dc22f`, 60,110 nodes / 152,578 edges,
warm `.trial-cache`, capture-C params + `--stagnation-iters 0`):**

```
graph_smooth_elevation (stage 6):  9.00 s → 4.20 s   (~2.1×; the 417-pass Jacobi loop itself
                                                       ~6 s → ~0.3 s; the residual is graph.copy()
                                                       ~2.6-3.9 s, paid identically by both versions)
operationalize_graph (stages 6-7): ~12.2 s → ~7.4 s  (-39%; compute_edge_metrics ~3.3 s unchanged —
                                                       out of scope, stage-7 per-edge loop)
whole run (in-process wall):       37.47 s → 33.56 s (-3.9 s, ~10% of the run; 10/10 routes,
                                                       budget-exhausted, both runs)
output equivalence:                all 10 route JSONs content-identical before/after
                                   (sha256 over timestamp-stripped JSON: 0d9428ee765574c6 both)
```

### Completion Notes List

**Vectorized diffusion (Task 1, AC #1/#3).** `graph_smooth_elevation` keeps its exact state model —
one shared float per node, private interior vertices per edge — but the field now lives in one flat
float64 numpy array (nodes first, then edge interior blocks in edge-iteration order), and each of the
~417 Jacobi sweeps is a handful of array ops: per-node neighbour sums via round-by-round accumulation,
interior relaxation via precomputed left/right gather indices with the literal `(left + right) / 2`
shape. Signature, docstring contract, `window <= 1.0` no-op, endpoint/lat-lon pass-through, purity
(input never mutated), and the `operationalize_graph` call site are all unchanged.

**The Neumaier discovery (why Task 3 was skipped instead of triggered).** The first vectorized cut used
`np.bincount` scatter-adds for the neighbour sums and drifted all 4 fast goldens — by 1 ULP in float
metrics (`d_plus_m` etc.) with **identical edge-set hashes**. Root cause isolated with a scratch
harness: since Python 3.12, builtin `sum()` over floats uses **Neumaier compensated summation**, so the
scalar code's per-node `sum(neigh)` is not reproducible by naive sequential adds (first divergence at
degree-6 nodes of the real fixture graph, iteration 2). Fix: the vectorized version replicates
CPython's compensated sum exactly — round r adds every node's r-th adjacency entry (per-node adjacency
order preserved), maintaining vectorized `sums`/`comp` arrays mirroring CPython's `f_result`/`c`;
max-degree rounds total (~6 on real graphs). Result: bit-identical output on the full fixture graph
across all 417 iterations (0 differing elevations), all 10 goldens byte-identical, no rebake needed —
the AC's preferred branch.

**Tests (Task 2).** All 39 existing smoothing tests pass unmodified. Two new tests pin the equivalence:
(1) `test_graph_smooth_elevation_bit_identical_to_scalar_reference` — a Y-junction graph (degree-3
node, varied interior lengths, a no-interior 2-vertex edge) asserted `==` against an in-test scalar
reference implementation (verbatim pre-13.1 formulation); (2)
`test_graph_smooth_elevation_replicates_compensated_neighbour_sum` — a degree-6 star whose spoke
elevations are lifted verbatim from the fixture node where the naive port diverged; proven to FAIL
against the naive `bincount` version and pass against the compensated one, so the compensation
behaviour cannot silently regress.

**Measured gain (Task 4, AC #2).** Stage-6 smoothing 9.00 → 4.20 s on the Chartreuse r10 reference
workload; whole-run 37.47 → 33.56 s in-process (seeded, 10/10 routes, outputs content-identical).
Honest attribution for 13.5's consolidation: the analysis attributed ~27% (~11 s) to "stages 6–7"; the
Jacobi loop this story vectorized was ~6 s of that (now ~0.3 s, ~20×). The remainder of the block is
`graph.copy()` (~2.6–3.9 s, purity contract, paid by both versions) and `compute_edge_metrics`
(~3.3 s, stage 7 per-edge Python loop, out of this story's scope) — both now visible as the next
query-side compute levers alongside Stories 13.2–13.4.

**User-directed addition (not in the story ACs, requested during review): query-side stage progress.**
The query CLI previously ran silent outside the solver's iteration progress, even though the
non-solver phases dominate large-area wall-clock (this epic's whole premise). The non-solver phases
now run inside the same `StageProgress` seam the setup CLI has used since Story 11.1 — zero new
machinery: `load-prepared-area` (coverage check + cache deserialization), `elevation-reshape`
(stages 6-7), `trail-filter` (difficulty-cap redux), `climb-detection`, `climb-contraction`, and
`validate-render` each print the established `stage: <name> ...` / `stage: <name>: X.XX s` pair on
stdout. `--quiet` suppresses them exactly like setup's stage lines and the solver's progress lines
(§Cat 8: the run summary always prints). Interrupt path: a stage interrupted mid-flight emits no done
line and the exception propagates unchanged (existing seam contract); the interrupt-path
validate-render reports like the normal one. Two new e2e tests
(`tests/e2e/test_query_stage_progress.py`) pin presence + pipeline order of the stage lines, and the
existing `--quiet` e2e test now also asserts `stage:` suppression. Incidental benefit for Stories
13.2/13.5: per-phase wall-clock is now visible in every run without a profiler.

### File List

**Modified:**
- `src/steeproute/pipeline/smoothing.py` — `graph_smooth_elevation` internals vectorized (flat field
  array, adjacency/round index arrays built once, Neumaier-compensated per-node sums, gather-based
  interior relaxation); function docstring extended with the vectorization + compensated-sum notes;
  `numpy` import added.
- `tests/unit/test_smoothing.py` — two new equivalence tests + `_scalar_reference_smooth` helper
  (scalar reference implementation used by both).
- `src/steeproute/cli/query.py` — non-solver query phases wrapped in the Story 11.1 `StageProgress`
  seam (user-directed addition; see Completion Notes); module docstring updated.
- `tests/e2e/test_quiet_suppresses_progress.py` — `--quiet` now also asserted to suppress `stage:` lines.
- `_bmad-output/implementation-artifacts/13-1-vectorize-query-side-elevation-smoothing.md` — this file.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.

**New:**
- `tests/e2e/test_query_stage_progress.py` — stage-line presence + pipeline-order e2e tests for the
  query CLI.

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-04 | Yann (Claude Fable 5) | Story 13.1 implemented: stage-6 graph-Laplacian elevation smoothing vectorized (flat float64 field, ~417 Jacobi sweeps as array ops). Bit-identical to the scalar formulation — including replicating CPython 3.12+'s Neumaier-compensated `sum()` via round-by-round compensated accumulation — so all 10 regression goldens pass untouched (no rebake). Stage-6 wall 9.0 → 4.2 s and whole-run 37.5 → 33.6 s on the Chartreuse r10 reference workload; outputs content-identical. 39 existing tests unmodified; 2 new equivalence tests pin the bit-exactness. |
| 2026-07-04 | Yann (Claude Fable 5) | User-directed addition: query CLI non-solver phases (cache load, stages 6-7 reshape, trail-filter redux, detection, contraction, validate+render) now report `stage:` start/elapsed lines via the existing Story 11.1 `StageProgress` seam, suppressed by `--quiet`. Two new e2e tests pin presence + order; quiet e2e extended. |
