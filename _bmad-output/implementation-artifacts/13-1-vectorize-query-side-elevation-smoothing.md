# Story 13.1: Vectorize query-side elevation smoothing (stage 6)

Status: ready-for-dev

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

- [ ] Task 1: Vectorize `graph_smooth_elevation`'s Jacobi diffusion (AC: #1, #3)
  - [ ] Replace the per-node/per-edge Python dict-and-loop relaxation (`smoothing.py:187-227`) with
    array/vectorized operations — same node/interior-vertex state model, same `iters` count, same
    `_DIFFUSION_LAMBDA` relaxation, same neighbor-averaging rule (unweighted mean over incident-edge
    adjacent values)
  - [ ] Preserve the function signature, docstring contract, and the `window <= 1.0` no-op short-circuit
    (`smoothing.py:176-178`) exactly
  - [ ] Preserve exact endpoint/`(lat, lon)` pass-through and the "new graph, input never mutated" contract
  - [ ] Keep the public interface (`graph_smooth_elevation(graph, strength_m)`) and its call site in
    `operationalize_graph` (`pipeline/__init__.py:247`) unchanged — this is a same-module internal rewrite,
    not an interface or call-site change
- [ ] Task 2: Verify numerical equivalence and update tests (AC: #1)
  - [ ] Run the existing `test_graph_smooth_elevation_*` suite in `tests/unit/test_smoothing.py` (lines
    478-580ish: no-op, flat-unchanged, never-increases-adjacent-delta, shares-node-value,
    preserves-lat-lon, does-not-mutate, preserves-attribute-contract) unmodified where possible
  - [ ] If float-reordering causes exact-value tests to diverge at the ULP level, tighten to
    tolerance-based comparison rather than deleting the assertion's intent
  - [ ] Run `uv run pytest tests/e2e/test_pinned_regressions.py` first with no code changes assumption — if
    any golden edge-set flips, that is the trigger for the one documented rebake in Task 3; if all pass
    byte-identical, no rebake is needed and Task 3 is skipped entirely
- [ ] Task 3 (conditional — only if goldens flip): Documented golden rebake (AC: #1)
  - [ ] `uv run update-regression --all`, then `--all --tier realistic`, then `--fixture
    grenoble_small_junction` and `--fixture grenoble_small_descent` (10 goldens total, Story 12.3 precedent)
  - [ ] Review every printed diff: value churn only; any `params_hash`/`seed` drift or `min_routes`
    collapse means something other than float-reordering broke — investigate, don't bake
  - [ ] Commit message states the equivalence argument (same math, same iteration count, reordered
    floating-point summation) per the Story 9.3/12.3 precedent
- [ ] Task 4: Measure and record the gain (AC: #2)
  - [ ] Reproduce the Chartreuse r10 reference workload from
    `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` (seed 44, n 10, l-connector 50,
    smoothing 50, descent-cap 0.4, start-at-junction) and measure wall-clock before/after
  - [ ] Record the before/after numbers and the stage 6–7 share in the Dev Agent Record and commit message
- [ ] Task 5: Gates + status
  - [ ] `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest
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

### Debug Log References

### Completion Notes List

### File List
