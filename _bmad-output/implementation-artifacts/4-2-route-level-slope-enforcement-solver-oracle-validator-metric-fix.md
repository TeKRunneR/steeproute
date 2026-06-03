# Story 4.2: Route-level slope enforcement in solver, oracle, and validator (+ metric fix)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want the binding slope constraint to be the whole-route average `(D+ + D−)/length ≥ θ`, enforced consistently by the solver, the exhaustive oracle, and the validator,
so that every returned route is genuinely steep on average (FR3/FR26) rather than steep only on its individual climbs.

## Acceptance Criteria

1. The per-super-edge filter `if eid in super_edges and data["avg_gradient"] < theta: continue` is **removed** from `solver/grasp.py::_build_rcl`. RCL feasibility is now exactly: not-yet-used (edge-simple) + SAC cap. The now-unused `theta` / `super_edge_to_base` locals in `_build_rcl` are removed, and the method's docstring no longer lists a θ filter.

2. `GraspSolver.run()` admits a finalized `Solution` to the tracker only when it passes a **route-level** feasibility gate: `(Σ d_plus_m + Σ d_minus_m) / Σ length_m ≥ params.theta`. Implemented as a `_route_slope_ok(solution)` helper; empty-edge solutions are still discarded first (existing guard). A route below θ is silently dropped (not admitted), mirroring how the RCL filter used to suppress candidates.

3. In `validator.py`, the per-super-edge `slope_floor` check (the `edge_id in super_edge_ids and edge.avg_gradient < params.theta` block inside the per-edge loop) is **replaced** by a single route-level `slope_floor` `ConstraintViolation`, emitted once when the route's `avg_gradient` (computed `(D+ + D−)/length`) is `< params.theta`. The check no longer lives in the per-edge loop and no longer reads `super_edge_to_base` for slope. The module docstring's "Slope floor" bullet is reworded to describe route-level semantics.

4. `validator._route_metrics` computes `avg_gradient = (d_plus_m + d_minus_m) / length_m` (was `d_plus_m / length_m`; zero-length guard unchanged). The route-level slope check (AC #3) reuses this same value — the metric is single-sourced, not recomputed independently. The corrected `avg_gradient` flows into `RouteMetrics` → the HTML/JSON report.

5. `tests/integration/exhaustive_oracle.py` applies the **identical** route-level feasibility test that GRASP uses: the per-super-edge slope filter in `_dfs` is removed, and each enumerated candidate is admitted only if `(Σ d_plus_m + Σ d_minus_m) / Σ length_m ≥ params.theta`. The oracle's and GRASP's feasible sets coincide by definition, keeping the Story 3.7 comparison apples-to-apples.

6. Tests reflect the route-level semantics:
   - `test_validator.py`: a route whose route-average is below θ yields a `slope_floor` violation; a route at/above θ passes; the corrected `(D+ + D−)/length` `avg_gradient` is asserted (including a route where the old uphill-only metric would have differed).
   - An integration test asserts **no** GRASP-admitted route on the real Grenoble fixture falls below route-level θ (feasible-by-construction), and that a sub-θ candidate is discarded.
   - The existing `test_grasp_on_fixture.py::test_super_edges_in_routes_clear_theta` is re-pointed to the route-level invariant (the old per-super-edge assertion no longer expresses the binding constraint).
   - The Story 3.7 GRASP-vs-exhaustive quality gate (`test_solver_on_toy_graph.py`) still passes with both sides sharing the route-level feasibility definition.

7. The solver, validator, and oracle stay **pure** (no I/O, no input mutation). All four CI gates green on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. No new deps. Coverage floors hold.

## Tasks / Subtasks

- [x] Task 1: Remove the per-super-edge slope filter from `_build_rcl`; drop the unused `theta` / `super_edges` slope locals and update the method docstring. (AC: #1)
- [x] Task 2: Add `_route_slope_ok(self, solution)` and gate `tracker.consider(...)` on it in `run()`. (AC: #2)
- [x] Task 3: Replace the validator's per-edge `slope_floor` check with a route-level one, reusing `_route_metrics(...).avg_gradient`; reword the module docstring's slope bullet. (AC: #3)
- [x] Task 4: Fix `_route_metrics.avg_gradient` to `(d_plus_m + d_minus_m) / length_m`. (AC: #4)
- [x] Task 5: Align `exhaustive_oracle.py` — remove the `_dfs` per-super-edge filter; apply the identical route-level admission test before feeding the tracker. (AC: #5)
- [x] Task 6: Update/add tests (validator route-level + metric, GRASP fixture route-level invariant, re-point the old super-edge test); confirm 3.7 gate green. (AC: #6)
- [x] Task 7: Run all four gates on Windows; reconcile any fixture-based value assertions that shifted (see Dev Notes "Expected output drift"). (AC: #7)

### Review Findings

_Code review 2026-06-03 (adversarial: Blind Hunter + Edge Case Hunter + Acceptance Auditor). All 7 ACs verified satisfied. 1 decision-needed (resolved → fixed), 2 deferred, 13 dismissed as noise/already-handled._

- [x] [Review][Decision→Fixed] Route-average slope was computed with two different summation strategies — solver/oracle vs validator. `GraspSolver._route_slope_ok` and the oracle used one interleaved sum `sum(e.d_plus_m + e.d_minus_m for e in edges)`, while `validator._route_metrics` summed `d_plus_m` and `d_minus_m` in two separate passes then added. These could differ by ~1 ULP, so a route whose average sits exactly on θ could be admitted by the solver yet flagged by the validator — contradicting the docstring's "the validator never flags a GRASP-admitted route" invariant and the Dev Notes' "single-source the avg_gradient metric so they can't drift." **Resolved:** extracted one shared pure helper `models.route_avg_gradient(edges)`; the solver gate, `validator._route_metrics.avg_gradient`, the oracle's admission filter, and the fixture invariant test all call it, so the metric is bit-identical across every site (4 hand-copied formulas → 1). [src/steeproute/models.py:route_avg_gradient + solver/grasp.py, validator.py, tests/integration/exhaustive_oracle.py, tests/integration/test_grasp_on_fixture.py]
- [x] [Review][Defer] NaN-gradient produces opposite verdicts across gates — if an edge carried a NaN metric, the solver/oracle reject (`NaN >= θ` is False) but the validator passes (`NaN < θ` is False), so a NaN route would validate clean. Unreachable in practice (finite DEM data + TopNTracker's non-finite-objective guard), and a pre-existing input-hardening gap rather than something this change introduced. [src/steeproute/validator.py:_route_metrics] — deferred, pre-existing/upstream-guarded
- [x] [Review][Defer] Metamorphic `relax_theta` reconcile crosses the 4.2/4.3 scope line — the diff edits `test_metamorphic.py` (work the spec reserved for Story 4.3) to keep the suite green after route-level semantics broke the strict-`>` non-vacuity guard. User-approved, documented in Completion Notes, preserves the `>=` invariant; full metamorphic re-validation remains Story 4.3. Awareness only. [tests/integration/test_metamorphic.py] — deferred to Story 4.3 (tracked in sprint-change-proposal §4C/3.8)

## Dev Notes

- **This story IS a behavioral change — the opposite of 4.1.** Story 4.1 was a pure parameter split (output byte-identical at defaults). This story removes a near-vacuous per-super-edge check and replaces it with a binding route-level floor. Routes that chained steep climbs across flat valley connectors (healthy ~20% climbs, ~6% route average) are now **rejected**. Expect real output changes on the fixture — that is the point (`sprint-change-proposal-2026-06-03.md` §1).

- **Route-level feasibility is a GLOBAL property — enforce at finalization, never greedily in the RCL.** A partial walk may legitimately dip below θ mid-construction and recover by appending a steep climb. The gate belongs in `run()` (after `_construct_one()` returns a complete `Solution`), not in `_build_rcl`. Do **not** try to prune mid-walk — it would wrongly kill recoverable routes (`sprint-change-proposal-2026-06-03.md` §2 "Technical impact").

- **Single-source the avg_gradient metric.** The validator's route-level check (AC #3) and `_route_metrics` (AC #4) must use the *same* `(D+ + D−)/length` computation — have the check read `_route_metrics(list(edges)).avg_gradient` (or compute once and share) rather than duplicating the formula. The solver's `_route_slope_ok` and the oracle's admission test compute the same ratio directly from `solution.edges`; keep all three textually trivial and identical in formula so they can't drift. Use the same `length_m > 0` zero-guard `_route_metrics` already has (a zero-length route → gradient `0.0` → fails θ at the default 0.20, which is correct).

- **The slope check moves OUT of the per-edge loop in `_validate_edges`.** Today it sits inside `for edge in unique_edges:` and the per-edge dedup logic (lines ~163-177). Route-level means one violation per route, computed once from the aggregate — so it no longer participates in the per-edge dedup and no longer reads `super_edge_ids`. The difficulty-cap, graph-membership, and edge-reuse checks are untouched.

- **Oracle alignment keeps Story 3.7 honest.** `_dfs` emits a candidate at every recursion depth; the route-level test can be applied either when recording in `_dfs` or as a filter over `candidates.values()` in `enumerate_best` *before* the `TopNTracker` — either is fine, but it must happen **before** admission so the oracle's feasible set matches GRASP's exactly. The oracle is test infrastructure (never imported from `src/`), so define the ratio test inline rather than importing the solver's private helper.

- **Expected output drift — reconcile, don't fight.** Determinism (FR29) still holds: same seed → same output. But the *values* shift because the feasible set shrank. Audit these for now-stale value assertions and update them to the new correct values (re-running the test reveals them): `tests/integration/test_grasp_on_fixture.py`, `test_grasp_reproducible.py`, `tests/integration/test_output_on_fixture.py`, `tests/integration/test_validator_on_fixture.py`, and `tests/unit/test_output.py` (the `avg_gradient` metric in report metadata changes from uphill-only to total). Treat a *structural* break (missing field, exception) as a bug; treat a *value* change as expected drift to be re-pinned.

- **Scope boundary — do NOT pull 4.3 forward.** The metamorphic suite re-validation (`test_metamorphic.py`, esp. the `scale_elevation` and `relax_theta` invariants), the CLI smoke/help tests, and all PRD/architecture/epics doc sync are **Story 4.3**. If the metamorphic suite breaks under the new semantics, note it for 4.3 — do not edit it here unless a failure is a true regression unrelated to slope semantics.

- **Defaults unchanged.** `--theta 0.20` / `--min-climb-slope 0.20` remain the defaults (set in 4.1). The route-floor default may prove strict in sparse areas — that's a tracked tuning item (`sprint-change-proposal-2026-06-03.md` §"Open tuning item"), not this story's concern.

### Project Structure Notes

- **Modify (source):** `src/steeproute/solver/grasp.py` (`_build_rcl` filter removal + `run()` gate + `_route_slope_ok`), `src/steeproute/validator.py` (`_validate_edges` slope check → route-level, `_route_metrics` fix, docstring), `tests/integration/exhaustive_oracle.py` (`_dfs` filter removal + route-level admission).
- **Modify (tests):** `tests/unit/test_validator.py` (route-level slope cases + metric assertion — note `_params` already supplies `min_climb_slope` from 4.1), `tests/integration/test_grasp_on_fixture.py` (re-point `test_super_edges_in_routes_clear_theta` + add route-level invariant), plus the value-assertion reconciliation listed under "Expected output drift".
- **Reuse, do not reinvent:** `_route_metrics` is the canonical route-aggregate; the route-level slope ratio is just `avg_gradient ≥ θ` against it. `TopNTracker.consider` admission semantics are unchanged — only *what reaches it* changes. The `super_edge_to_base` membership concept stays in use elsewhere (graph structure); only its role in the *slope* path is removed.

### Testing standards summary

- Unit tests in `tests/unit/`, integration in `tests/integration/`; naming `test_<unit>_<scenario>` (Architecture §"Test organization"). Validator slope cases are unit; the fixture feasible-by-construction check is integration.
- No `pytest.skip`/`xfail`; no new deps. Pure-logic coverage floors on `grasp.py` / `validator.py` must hold — the route-level helper adds one small branch each; cover the below-θ and at/above-θ paths.
- The 3.7 quality gate (`test_solver_on_toy_graph.py`) is the cross-check that GRASP and oracle still agree under the shared feasibility definition — it must stay green without loosening `QUALITY_THRESHOLD`.

### References

- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-03.md §4B/B5-B7, §4C/3.6,3.9, §5](../planning-artifacts/sprint-change-proposal-2026-06-03.md) — canonical handoff: B5 (solver), B6 (validator + metric), B7 (oracle) are Story 4.2's exact scope
- [Source: _bmad-output/planning-artifacts/epics.md §"Story 4.2"](../planning-artifacts/epics.md) — BDD acceptance criteria
- [Source: _bmad-output/implementation-artifacts/4-1-split-theta-introduce-min-climb-slope-and-route-level-semantics.md §Dev Notes](4-1-split-theta-introduce-min-climb-slope-and-route-level-semantics.md) — 4.1 deliberately left the per-super-edge check in place for 4.2 to remove; `min_climb_slope` plumbing already done
- [Source: src/steeproute/solver/grasp.py:130-138,196-224](../../src/steeproute/solver/grasp.py) — `run()` admission point + `_build_rcl` θ filter (`:209`) to remove
- [Source: src/steeproute/validator.py:24-31,163-177,224-235](../../src/steeproute/validator.py) — slope-floor docstring bullet, per-edge slope block to replace, `_route_metrics` to fix
- [Source: tests/integration/exhaustive_oracle.py:84-109,136-144](../../tests/integration/exhaustive_oracle.py) — `enumerate_best` admission + `_dfs` per-super-edge filter (`:143`) to align
- [Source: tests/unit/test_validator.py:43-52,134-155](../../tests/unit/test_validator.py) — `_params` factory (already has `min_climb_slope`) + slope-floor tests to update
- [Source: tests/integration/test_grasp_on_fixture.py:179-200](../../tests/integration/test_grasp_on_fixture.py) — `test_super_edges_in_routes_clear_theta` to re-point to route-level

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps.

**Final gate pass (all green):**

```
uv run ruff format --check    → 73 files already formatted
uv run ruff check             → All checks passed!
uv run basedpyright           → 0 errors, 0 warnings, 0 notes
uv run pytest -q              → 655 passed, 1 deselected in ~157 s
                                (was 653; +2 net: validator slope tests 2→3,
                                 + new metamorphic suite-level non-vacuity test)
--cov grasp.py + validator.py → both 100% (all new branches covered)
```

### Completion Notes List

**Behavioral change landed as designed — this was NOT a no-op story.** The near-vacuous per-super-edge `avg_gradient < theta` filter is gone from the RCL (`_build_rcl`), the validator per-edge loop, and the oracle DFS. The binding constraint is now the whole-route average `(ΣD+ + ΣD−)/Σlength ≥ θ`, enforced at GRASP finalization (`_route_slope_ok`), re-checked by the validator (route-level `slope_floor`), and mirrored in the oracle's post-enumeration admission. The route-level formula is single-sourced in spirit across all three sites (solver, validator-via-`_route_metrics`, oracle) and uses the same `length > 0` zero-guard.

**Fixture output survived enforcement.** A real risk flagged in the story was that route-level θ=0.20 might empty the Grenoble fixture's GRASP output. It did not — `test_grasp_on_fixture` still returns ≥1 route, all clearing route-level θ, and the Story 3.7 GRASP-vs-exhaustive gate stays green with both sides sharing the route-level feasible set.

**No drift in the output/reproducibility fixture tests.** The Dev Notes anticipated possible value-assertion churn in `test_output.py`, `test_output_on_fixture.py`, `test_validator_on_fixture.py`, `test_grasp_reproducible.py`. None broke — those tests assert structure/determinism, not the specific `avg_gradient` or route objectives that shifted, so no edits were needed.

**Metamorphic `relax_theta` reconciled (one seed), full re-validation left to 4.3.** Route-level semantics broke only the *non-vacuity guard* of `test_relax_theta_objective_non_decreasing` on seed 26 — the monotonicity invariant (`new_obj >= old_obj`) still holds on all seeds. An empirical probe confirmed **no single θ pair can make the per-seed strict-`>` guard bind for all 5 seeds** under route-level math (seed 21 goes infeasible just above θ=0.45 while seed 26 only bends near 0.46 — disjoint). Per a user decision, applied the **minimal reconcile**: kept the per-seed `>=` invariant, moved the non-vacuity check to a suite-level test (`test_relax_theta_binds_on_at_least_one_seed` — asserts the relaxation strictly raises the objective on ≥1 seed, which holds). The full metamorphic re-validation (fixture/seed re-tuning, the `scale_elevation` invariant co-scaling θ + `min_climb_slope` by k, and a possible `min_climb_slope` relaxation invariant) remains **Story 4.3** scope, as flagged in the sprint-change-proposal §4C/3.8.

**Two existing tests re-pointed (not just renamed) to reflect the new "why".** `test_grasp_construction.py::test_grasp_rcl_excludes_super_edges_below_theta` still passed after the change but for a new reason (the sub-θ *route* is discarded by the finalization gate, not by an RCL membership filter); re-pointed it to `test_grasp_discards_routes_below_route_level_theta` — it now doubles as AC #6's "sub-θ candidate is discarded" assertion. `test_grasp_on_fixture.py::test_super_edges_in_routes_clear_theta` → `test_every_grasp_route_clears_route_level_theta` (asserts each admitted route's whole-route average clears θ); the now-unused `super_edge_ids` fixture plumbing was simplified away.

**Validator slope tests rewritten for route-level (AC #6).** Added: a below-θ route flagged, an exactly-at-θ route admitted (the floor is `>=`), and a descent-counts case (a route the *old* uphill-only metric would have flagged but the corrected `(D+ + D−)/length` metric passes). The `test_validate_route_dedups_per_edge_violations_on_reuse` test was repurposed onto the difficulty cap (still genuinely per-edge), since slope is no longer a per-edge constraint to dedup. `test_validate_builds_routes_with_aggregate_metrics` updated for the corrected `avg_gradient` (0.20 → 0.23).

**AC walkthrough:**
1. AC #1 — per-super-edge filter removed from `_build_rcl`; `theta`/`super_edges` locals dropped; docstring + module construction-shape reworded. ✅
2. AC #2 — `_route_slope_ok` gates `run()`'s `tracker.consider`; empty walks discarded first; sub-θ routes dropped (pinned by `test_grasp_discards_routes_below_route_level_theta`). ✅
3. AC #3 — validator `slope_floor` now route-level (single violation, outside the per-edge loop), no `super_edge_to_base` on the slope path; module docstring reworded. ✅
4. AC #4 — `_route_metrics.avg_gradient = (d_plus_m + d_minus_m)/length_m`; the validator check reuses it (single-sourced); flows to report metadata. ✅
5. AC #5 — oracle `_dfs` slope filter removed; identical route-level admission applied pre-tracker; `_route_slope_ok` helper mirrors the solver's formula. ✅
6. AC #6 — validator route-level + metric tests; fixture route-level invariant; old super-edge test re-pointed; sub-θ-discard test; 3.7 gate green. ✅
7. AC #7 — solver/validator/oracle stay pure (no I/O, no mutation; `list(edges)` copy in validator); all four gates green; no new deps; coverage 100% on changed modules. ✅

### File List

**Modified (source):**
- `src/steeproute/models.py` — added `route_avg_gradient(edges)`: the single source of truth for the route-level slope metric `(ΣD+ + ΣD−)/Σlength` (review fix); updated `RouteMetrics` docstring to match.
- `src/steeproute/solver/grasp.py` — removed the per-super-edge θ filter from `_build_rcl` (+ its `theta`/`super_edges` locals); added `_route_slope_ok` (delegates to `route_avg_gradient`) and gated `run()`'s tracker admission on it; reworded module + `_build_rcl` docstrings to route-level.
- `src/steeproute/validator.py` — `_route_metrics.avg_gradient` now sourced from `route_avg_gradient` (route-level `(D+ + D−)/length`); per-edge `slope_floor` check replaced by a single route-level check, moved out of the per-edge loop; dropped the unused `super_edge_ids` local on the slope path; module-docstring slope bullet reworded.

**Modified (tests):**
- `tests/integration/exhaustive_oracle.py` — removed the `_dfs` per-super-edge slope filter (+ its `super_edges`/`theta` params); added a route-level admission filter in `enumerate_best` and a module-level `_route_slope_ok` helper; feasibility docstring reworded.
- `tests/unit/test_validator.py` — route-level slope tests (below-θ flag, at-θ admit, descent-counts); repurposed the per-edge dedup test onto the difficulty cap; corrected `avg_gradient` assertion (0.23).
- `tests/unit/test_grasp_construction.py` — re-pointed the slope test to `test_grasp_discards_routes_below_route_level_theta` (fixture B comment + module docstring reworded to route-level finalization).
- `tests/integration/test_grasp_on_fixture.py` — `test_super_edges_in_routes_clear_theta` → `test_every_grasp_route_clears_route_level_theta` (asserts via the shared `route_avg_gradient`); simplified the fixtures (dropped the unused `super_edge_ids` plumbing); module-docstring bullet reworded.
- `tests/integration/test_metamorphic.py` — `relax_theta`: kept the per-seed `>=` invariant, extracted `_RELAX_THETA_*` constants, added suite-level `test_relax_theta_binds_on_at_least_one_seed` (non-vacuity); reworded for route-level semantics. (Full 8-invariant re-validation deferred to Story 4.3.)

**Modified (tracking):**
- `_bmad-output/implementation-artifacts/4-2-route-level-slope-enforcement-solver-oracle-validator-metric-fix.md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked to `review`; `last_updated`.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-03 | Yann (Claude Opus 4.8) | Code review (adversarial: Blind Hunter + Edge Case Hunter + Acceptance Auditor). All 7 ACs verified satisfied. 1 decision-needed resolved: extracted shared `models.route_avg_gradient` and routed the solver gate, validator metric, oracle admission, and fixture test through it (4 copies → 1), eliminating a float-summation-order edge case that could let the validator flag a GRASP-admitted route at the θ boundary. 2 items deferred (NaN-gradient hardening → unassigned; full metamorphic re-validation → Story 4.3); 13 dismissed (mostly intended behavior or already CLI-guarded). Re-validated: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 655 passed ✅, 100% coverage on models/grasp/validator. Status review → done. | _pending_ |
| 2026-06-03 | Yann (Claude Opus 4.8) | Story 4.2 implemented: route-level slope-floor enforcement. Removed the near-vacuous per-super-edge `avg_gradient < θ` filter from the GRASP RCL, validator per-edge loop, and exhaustive oracle DFS. Added a whole-route feasibility gate `(ΣD+ + ΣD−)/Σlength ≥ θ` at GRASP finalization (`_route_slope_ok`), a route-level validator `slope_floor` check, and the identical post-enumeration filter in the oracle. Fixed `validator._route_metrics.avg_gradient` to `(D+ + D−)/length` (was uphill-only) — flows to the HTML/JSON report. Re-pointed the solver/validator/fixture slope tests to route-level semantics; reconciled the metamorphic `relax_theta` non-vacuity guard to suite level (full re-validation deferred to 4.3 per sprint-change-proposal §4C/3.8). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 655 passed; 100% coverage on changed modules; no new deps. Story 3.7 GRASP-vs-exhaustive gate holds under the shared route-level feasible set. Status → review. | _pending_ |
