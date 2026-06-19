# Story 9.2: GRASP θ-feasible prefix recovery (review finding #10)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want GRASP to keep a θ-clearing route even when its greedy walk is forced to append a flat tail that drags the whole-walk average below θ,
So that the solver stops returning nothing (or fewer routes) where feasible routes demonstrably exist.

## Acceptance Criteria

1. GRASP recovers the best θ-clearing **prefix** of each constructed walk and offers it to the tracker, instead of discarding the whole walk when a forced flat tail drags the maximal-walk average below θ. Concretely: on the steep-prefix-plus-forced-flat-tail class, GRASP returns a θ-clearing route wherever its constructed walk contains one — it no longer returns `[]` (or fewer routes) where a θ-feasible prefix exists. When the maximal walk itself clears θ, that walk *is* the best θ-clearing prefix, so current behavior is unchanged in that case.

2. A **fail-first** regression test (fails on pre-fix `run()`, passes after) asserts AC #1 against the exhaustive oracle on a steep-edge-plus-forced-flat-tail graph. Basis: `tmp/repro_findings.py::repro_finding_10` — steep edge `0→1` (400 m / +200 m, avg 0.50, clears θ=0.20 alone) plus a forced flat connector `1→2` (2000 m / +0 m). Pre-fix GRASP returns `[]`; after the fix GRASP returns the θ-clearing prefix `[(0,1,0)]` — the same route `enumerate_best` returns. Assert no false empty result.

3. The fix preserves the existing solver contract: FR29 byte-identical determinism (prefix selection is a pure, deterministic function of the already-deterministic walk — no RNG, and the construction RNG draw sequence is unchanged), the **one shared feasible set** with the oracle (every prefix GRASP offers is a walk the oracle also enumerates — edge-simple, reuse-respecting, θ-clearing), and route-level θ single-sourced through `models.route_avg_gradient` (the function the validator's `slope_floor` check uses).

4. The existing GRASP unit tests (`tests/unit/test_grasp_construction.py`) and the GRASP fixture integration test (`tests/integration/test_grasp_on_fixture.py`) still pass. In particular the sub-θ dead-end route `(0,2,0)` in that file's Fixture B is still never admitted (its only prefix is itself, below θ). Any test that pinned the **old maximal-walk-only behavior** is updated to the corrected expectation, with a one-line note on what changed.

5. The Story 3.7 GRASP-vs-exhaustive gate (`tests/integration/test_solver_on_toy_graph.py`) passes with the ratio **unchanged or higher**, both sides on one feasible set. `QUALITY_THRESHOLD` is left at `0.80` — tightening it against the post-Epic-9 baseline is **Story 8.5's** job, not this story's. The `exhaustive_oracle.py` docstring's "identical feasible set" claim is made accurate, and `grasp.py`'s construction/determinism docstrings are updated to describe prefix recovery.

6. All four CI gates green on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest --cov`. `solver/grasp.py` holds its pure-logic coverage floor on the changed code path. No new runtime deps. (Cross-tier *golden* rebake + full metamorphic / e2e revalidation of the downstream route-output shift are deferred to Story 9.3, not done here.)

## Tasks / Subtasks

- [x] Task 1: Recover the best θ-clearing prefix in the admission path (AC: #1, #3). Keep `_construct_one`'s maximal-walk construction and its RNG draws byte-for-byte unchanged; in `run()` (or a small helper), compute the best θ-clearing prefix of the constructed walk and offer *that* to `tracker.consider(...)`. Dev picks the exact prefix policy (see Dev Notes).
- [x] Task 2: Add the fail-first regression test (AC: #2). Confirm it FAILS on pre-fix code, then passes. Port the topology from `repro_finding_10`; assert GRASP matches the oracle's `[(0,1,0)]` and never returns a false `[]`.
- [x] Task 3: Make the oracle's "identical feasible set" docstring accurate and update `grasp.py`'s construction/determinism docstrings to describe prefix recovery; reconcile any GRASP unit test that pinned the old maximal-walk-only behavior; run the Story 3.7 gate and confirm it stays green with ratio unchanged-or-higher (AC: #4, #5).
- [x] Task 4: Verify all four CI gates + the coverage floor on the changed path (AC: #6).

## Dev Notes

### The defect (what to fix)

`_construct_one` (`src/steeproute/solver/grasp.py:303`) extends a walk until the RCL is empty — a **maximal** walk — and `run()` checks the route-level floor θ only on that finished maximal walk via `_route_slope_ok`. A steep prefix that clears θ but is then forced to append a flat-but-RCL-feasible tail ends below θ on average, so the whole walk is rejected and nothing is offered to the tracker. The exhaustive oracle, by contrast, emits **every** prefix of every feasible walk (`exhaustive_oracle.py:169`), so it returns the steep-only route GRASP threw away. Result: on some graphs GRASP returns `[]` while a θ-feasible route demonstrably exists, and across the board GRASP's feasible set is a strict subset of the oracle's — systematically depressing the Story 3.7 ratio (the metric Story 8.5 will tighten against).

### Recommended approach

The minimal, safe fix lives in the **admission path in `run()`**, not in construction:

- Leave `_construct_one` returning the maximal walk, and leave the start-node and RCL sampling untouched — this keeps the FR29 RNG draw sequence byte-identical, so `test_grasp_reproducible.py` and the determinism unit test stay green.
- Replace the `if solution.edges and self._route_slope_ok(solution): consider(solution)` step with: compute the best θ-clearing prefix of `solution.edges`, and if one exists, build a `Solution(edges=prefix, objective=Σ(d+ + d−) over the prefix)` and offer that.
- **Objective is monotone non-decreasing in prefix length** (every edge's `d_plus_m + d_minus_m ≥ 0`), so among the θ-clearing prefixes the **longest** one has the maximum objective and is unique → "best-objective θ-clearing prefix" = "longest θ-clearing prefix", and the choice is deterministic with no tie-break needed. When the full walk clears θ, the longest θ-clearing prefix is the full walk → unchanged behavior.

Dev decides the exact policy (the proposal explicitly leaves this open): offering only the best/longest θ-clearing prefix is the simplest thing that satisfies every AC here; offering **all** qualifying prefixes more closely mirrors the oracle and can help the top-N fill with distinct shorter routes, at a little extra `tracker.consider` cost. Start with the longest-prefix policy unless the 3.7 ratio argues for more.

### Constraints the fix must keep (do not regress)

- **One shared feasible set** — a prefix of a feasible walk is itself edge-simple and reuse-respecting (its used-segment set is a subset), and if it clears θ the oracle enumerates it too. So offering θ-clearing prefixes keeps GRASP ⊆ the oracle's feasible set and the Story 3.7 comparison apples-to-apples. Do not invent a route the oracle would call infeasible.
- **FR29 determinism** — no RNG in prefix selection; it is a pure function of the already-deterministic walk. Two `default_rng(seed)` runs must still produce byte-identical `list[Solution]`.
- **Route-level θ semantics** — keep using `models.route_avg_gradient` for the prefix average (single-sourced with the validator), so a GRASP-admitted prefix can never trip the validator's `slope_floor` over a float-summation discrepancy. An empty/zero-length prefix yields gradient `0.0` and is rejected at any positive θ.
- **Sub-θ routes still rejected** — Fixture B's `(0,2,0)` (single-edge dead-end, avg 0.10 < θ) has only itself as a prefix → no θ-clearing prefix → still never admitted. The fix only adds routes where a θ-clearing prefix genuinely exists.

### Blast radius (informational)

Recovering θ-clearing prefixes grows GRASP's output on real fixtures → route output shifts → both golden tiers and the degradation test drift. That cascade (golden rebake fast + realistic, full 8-invariant metamorphic re-validation, optional real-area checkpoint) is **revalidated and rebaked in Story 9.3**, not here — same deferral shape Story 9.1 used. This story's scope is the solver fix + its fail-first regression test + the oracle/grasp docstring sync + confirming the directly-affected GRASP unit/integration tests and the Story 3.7 gate stay green. No cache invalidation: GRASP is query-side; `pipeline_content_hash` is unaffected.

### Project Structure Notes

- **Modified:** `src/steeproute/solver/grasp.py` — `run()` admission path recovers the best θ-clearing prefix; helper as needed; construction/determinism docstrings updated. `_build_rcl` / start-node sampling untouched (FR29 RNG sequence preserved).
- **Modified:** `tests/integration/exhaustive_oracle.py` — docstring only; the "identical feasible set" claim becomes accurate (the oracle already emits prefixes — no logic change).
- **New:** one fail-first regression test. The AC compares GRASP to the oracle, so integration placement (alongside the oracle import shape, e.g. near `test_solver_on_toy_graph.py`) fits; a unit test asserting the known-by-inspection `[(0,1,0)]` is acceptable too — dev's call. Mirror the existing `_add_edge` / inline-graph-with-comment-block style.
- **Possibly updated:** `tests/unit/test_grasp_construction.py` (only where it pinned old maximal-walk-only behavior).
- **Out of scope:** golden rebake, full metamorphic / e2e revalidation, `QUALITY_THRESHOLD` tightening, the climb-detection fix (#7 is Story 9.1, done) — all Story 9.3 / 8.5.

### Testing standards summary

- The new test must demonstrably fail on pre-fix code — capture that in the Dev Agent Record (project convention for regression-pinned bug fixes, per correct-course §4C and the Epic 4/5/6/9.1 precedent).
- Float comparisons on aggregates use `math.isclose(..., abs_tol=1e-9)`, never `==` — except where a test pins FR29 byte-identical reproducibility, which deliberately uses `==` (see `test_grasp_construction.py::test_grasp_run_is_deterministic_under_same_seed`).
- Coverage floor on the changed `grasp.py` path (Architecture §Cat 11e).
- If the build is stale at session start, `uv run` may hit the corporate-TLS cert flake; settle once with `uv sync --native-tls`, then run with `uv run --no-sync` (Story 9.1 Debug Log).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 9.2"](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-18-route-discovery-quality.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-18-route-discovery-quality.md) — §1 (#10), §2 technical impact, §4B (B2/B3), §4C
- [Source: src/steeproute/solver/grasp.py:303](src/steeproute/solver/grasp.py) — `_construct_one` (maximal walk) + `run()` admission path / `_route_slope_ok` (the finalization gate to extend)
- [Source: tests/integration/exhaustive_oracle.py:24](tests/integration/exhaustive_oracle.py) — the "identical feasible set" docstring claim to make accurate; `_dfs` emits every prefix (line 169)
- [Source: tmp/repro_findings.py:192](tmp/repro_findings.py) — `repro_finding_10`, the fail-first test basis
- [Source: tests/integration/test_solver_on_toy_graph.py:34](tests/integration/test_solver_on_toy_graph.py) — Story 3.7 quality gate; `QUALITY_THRESHOLD` stays 0.80 here
- [Source: _bmad-output/implementation-artifacts/9-1-climb-detection-maximality.md](_bmad-output/implementation-artifacts/9-1-climb-detection-maximality.md) — prior Epic 9 story: deferral-to-9.3 shape, fail-first convention, build-flake recovery

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (1M context) (`claude-opus-4-8[1m]`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps. Settled the known stale-editable-build / corporate-TLS flake once with `uv sync --native-tls`, then ran all tests with `uv run --no-sync` (per Story 9.1 Debug Log). Note: `tests/unit` and `tests/integration` must be run in **separate** pytest invocations — collecting both together triggers a `from conftest import ...` name collision (pytest prepend import mode resolves the unit `conftest.py` for an integration module).

**Fail-first proof (AC #2):** on pre-fix `run()`, `test_grasp_recovers_theta_clearing_prefix_under_forced_flat_tail` FAILS with `assert []` — GRASP returns no routes because only the maximal walk `[0→1, 1→2]` (avg 200/2400 ≈ 0.083 < θ=0.20) is offered. With the fix it passes: GRASP offers the longest θ-clearing prefix `[(0,1,0)]`, matching `enumerate_best`.

**Test runs (all `--no-sync`):**

```
pytest tests/unit/test_grasp_construction.py                          → 12 passed (unchanged — no test pinned the old maximal-walk-only behavior)
pytest tests/integration/test_grasp_theta_prefix.py + 3.7 gate +       → 13 passed (incl. Story 3.7 quality gate, fixture, reproducibility/FR29)
  test_grasp_on_fixture + test_grasp_reproducible
pytest tests/unit                                                     → 575 passed
pytest tests/integration                                              → 118 passed, 2 deselected (incl. 3.8 metamorphic, 3.7 gate)
ruff check / ruff format --check (changed files)                      → clean
basedpyright src/steeproute/solver/grasp.py                           → 0 errors, 0 warnings, 0 notes
coverage (changed path)                                               → grasp.py 94% combined; the 6 misses are pre-existing
                                                                        progress-callback / stagnation / time-budget branches
                                                                        (covered by test_progress*/test_stagnation/test_time_budget),
                                                                        NOT the changed admission path or `_best_theta_prefix`
```

### Completion Notes List

**Approach — recover the best θ-clearing prefix at admission, leave construction untouched.** The fix lives entirely in `run()`'s admission step, not in `_construct_one`: the maximal walk and its RNG draw sequence are byte-for-byte unchanged, so FR29 reproducibility (`test_grasp_reproducible.py`) and the determinism unit test stay green. The old `if solution.edges and self._route_slope_ok(solution): consider(solution)` is replaced by `candidate = self._best_theta_prefix(solution.edges); if candidate is not None: consider(candidate)`. The new helper scans prefix lengths from full down to 1 and returns the first (longest) one that clears θ as a fresh `Solution` with its own recomputed objective. **Longest = best**: per-edge `d_plus_m + d_minus_m ≥ 0` makes objective non-decreasing in prefix length, so the longest θ-clearing prefix is the highest-objective one — a single deterministic answer, no tie-break needed.

**Why every existing test stayed green.** When the maximal walk itself clears θ, the longest θ-clearing prefix *is* the whole walk → behavior unchanged. Fixture B's sub-θ dead-end `(0,2,0)` has only itself as a prefix (avg 0.10 < θ) → `_best_theta_prefix` returns `None` → still never admitted. The empty walk yields `None`, subsuming the old `if solution.edges` guard. So no unit test pinned the old maximal-walk-only behavior in a way that needed updating — all 12 passed unchanged.

**One feasible set with the oracle (AC #3/#5).** A prefix of a feasible walk is itself edge-simple and reuse-respecting, and the oracle already emits every prefix (`_dfs`), so GRASP offering θ-clearing prefixes keeps both sides on one feasible set. On the regression fixture GRASP's output now equals the oracle's exactly. The Story 3.7 gate passes with the ratio unchanged-or-higher; `QUALITY_THRESHOLD` left at 0.80 (its tightening is Story 8.5). Made the oracle docstring's "identical feasible set" claim accurate and updated `grasp.py`'s construction docstring to describe prefix recovery.

**Prefix policy choice.** Implemented the *longest θ-clearing prefix per walk* (the simplest policy satisfying every AC), not "offer all qualifying prefixes". The 3.7 ratio held without needing the richer policy, so the extra `consider()` cost was unwarranted.

**Deferred to Story 9.3 (intended downstream shifts — NOT regressions).** Recovering prefixes grows GRASP's output on real fixtures, so the e2e golden tiers + degradation test drift further (on top of Story 9.1's shift). Per the correct-course sequencing, the cross-tier golden rebake (`update-regression --all` + `--all --tier realistic`), full 8-invariant metamorphic re-validation, and the optional real-area checkpoint are Story 9.3's job — not run here. All must-stay-green tests for this story (every unit + integration test, including the 3.8 metamorphic suite and the 3.7 gate) pass.

### File List

**Modified:**
- `src/steeproute/solver/grasp.py` — `run()` admission path now offers the best θ-clearing prefix (new `_best_theta_prefix` helper); `_route_slope_ok` signature changed from `Solution` to `tuple[Edge, ...]` so it gates each prefix; module "Construction shape" docstring updated for prefix recovery. `_construct_one` / `_build_rcl` / start-node sampling untouched (FR29 RNG sequence preserved).
- `tests/integration/exhaustive_oracle.py` — docstring only: the θ paragraph now states both the oracle (every prefix) and GRASP (`_best_theta_prefix`, Story 9.2) keep θ-clearing prefixes, making the "identical feasible set" claim accurate. No logic change.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story `9-2-grasp-theta-feasible-prefix-recovery` walked `ready-for-dev → in-progress → review`.

**New:**
- `tests/integration/test_grasp_theta_prefix.py` — fail-first θ-prefix recovery regression test (verified red on pre-fix code); asserts GRASP matches the oracle on the steep-edge-plus-forced-flat-tail graph, never a false `[]`, and never admits the sub-θ maximal walk.

### Change Log

| Date | Author | Description |
|---|---|---|
| 2026-06-19 | Yann (Claude Opus 4.8) | Story 9.2 implemented (review finding #10): GRASP `run()` now recovers the best (longest, highest-objective) θ-clearing prefix of each constructed walk via new `_best_theta_prefix`, instead of discarding the whole maximal walk when a forced flat tail drags its average below θ. Construction + RNG draws untouched → FR29 byte-identical (reproducibility test green). New fail-first regression test (verified red on pre-fix code) pins GRASP to the exhaustive oracle on the steep-plus-flat-tail graph. Oracle + grasp docstrings synced so the "identical feasible set" claim is accurate. All 575 unit + 118 integration tests green (incl. 3.7 quality gate + 3.8 metamorphic); ruff/format/basedpyright clean; changed path fully covered. Golden rebake + e2e/metamorphic revalidation of the downstream route-output shift deferred to Story 9.3 per correct-course sequencing. |
