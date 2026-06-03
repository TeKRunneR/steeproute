# Story 4.3: Re-validate metamorphic + CLI tests and sync planning docs

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want the metamorphic suite, CLI smoke tests, and planning docs brought into line with the route-level semantics,
so that the correction is fully covered and the PRD/architecture/epics no longer contradict the code.

## Acceptance Criteria

1. **`scale_elevation` invariant co-scales `min_climb_slope`.** `test_scale_elevation_objective_scales_proportionally` explicitly co-scales both `θ` and `min_climb_slope` by `k` (or sets both to 0) so feasibility is preserved by construction, and documents that `min_climb_slope` is inert in this GRASP-only fixture (it drives `detect_climbs`, upstream of the directly-built `ContractedGraph`) — so the co-scaling preserves intent rather than changing behaviour. The invariant still passes on all `_SEEDS`.

2. **`relax_theta` binds meaningfully and the 4.2 breadcrumbs are cleared.** The `relax_theta → objective non-decreasing` invariant is confirmed to bind under route-level semantics. Either the Story 4.2 suite-level reconcile (`test_relax_theta_binds_on_at_least_one_seed`) is retained and finalized, or it is replaced by a retuned per-seed strict guard — dev's call, with the rationale documented in the test. No "deferred to Story 4.3 / 4.3 scope" notes remain anywhere in `test_metamorphic.py`.

3. **`relax_min_climb_slope` resolved.** A `relax_min_climb_slope → objective non-decreasing` invariant is added **only if it binds meaningfully**; otherwise the existing invariant set is explicitly documented as sufficient, with the rationale that `min_climb_slope` does not enter the GRASP solver (a solver-level invariant on it would be vacuous on the metamorphic fixture). Do not add a vacuous test to hit a number.

4. **All 8 metamorphic invariants pass** under route-level `θ` semantics on the primary Windows platform, with no `pytest.skip`/`xfail` (Architecture §Cat 11c).

5. **CLI help/smoke coverage verified.** The Stories 1.5/1.7-layer tests assert `--min-climb-slope` appears in `steeproute --help` (`tests/unit/test_cli_help.py`, `tests/e2e/test_cli_smoke.py`). This coverage was added in Story 4.1 — verify it is present and correct; add only what is genuinely missing.

6. **Planning docs verified consistent with the implemented route-level behaviour.** PRD (FR3/FR3b, Config Schema, defaults), architecture (stage 8, constraint table, metadata param list), and this epics file are confirmed to match the code; any residual straggler is fixed. The bulk of this sync was front-loaded during correct-course and Story 4.1 — this AC is a **verification pass**, not a rewrite (see Dev Notes).

7. The full test suite (unit + integration + e2e) and all four CI gates pass on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. No new deps. Coverage floors hold.

## Tasks / Subtasks

- [x] Task 1: Co-scale `min_climb_slope` with `θ` in `test_scale_elevation_objective_scales_proportionally`; document the inert-in-fixture rationale; confirm it passes on all seeds. (AC: #1)
- [x] Task 2: Finalize the `relax_theta` non-vacuity guard (retain-and-document the suite-level test, or retune for a per-seed strict guard); strip the 4.2 "deferred to 4.3" notes from `test_metamorphic.py`. (AC: #2, #4)
- [x] Task 3: Decide the `relax_min_climb_slope` question — add the invariant only if it binds; otherwise document the existing set as sufficient with the upstream/vacuity rationale. (AC: #3)
- [x] Task 4: Run the full metamorphic suite; confirm all 8 invariants green with no skip/xfail. (AC: #4)
- [x] Task 5: Verify CLI help/smoke tests assert `--min-climb-slope` in `steeproute --help`; add any genuine gap. (AC: #5)
- [x] Task 6: Verify PRD/architecture/epics consistency with the code; fix residual stragglers only. (AC: #6)
- [x] Task 7: Run all four gates + full suite on Windows. (AC: #7)

## Dev Notes

- **Most of this story's nominal scope is already done — VERIFY, do not redo.** The doc sync (PRD A1–A4, architecture A5–A8, epics A9) was applied during the 2026-06-03 correct-course; the CLI help/smoke `--min-climb-slope` coverage was added in Story 4.1. Confirmed present at: PRD `prd.md:108,351,483,484`; architecture `architecture.md:253,258,514,515,615`; epics FR-map `epics.md:158,159`; `test_cli_help.py:13,51` and `test_cli_smoke.py`. **Do not re-reword the PRD or architecture** (the 4.1 note explicitly warned against re-editing synced docs). The genuine work left is the metamorphic-suite finalization (AC #1–#4) plus verification of the rest. Expect a small diff, mostly in `test_metamorphic.py`.

- **The linchpin: `min_climb_slope` is inert in the metamorphic GRASP fixture.** It is consumed only by `detect_climbs` (pipeline stage 8), which runs *upstream* of the `ContractedGraph` — and the metamorphic suite builds that graph directly via `make_toy_contracted_graph`, never calling `detect_climbs` (confirmed: no `min_climb_slope` reference in `solver/grasp.py`). This drives two ACs:
  - **AC #1:** co-scaling `min_climb_slope` is *intent-preserving documentation*, not a behaviour change. It is also already happening transitively — `make_toy_solver_params` couples `min_climb_slope=theta` (`conftest.py:238`), so `_params(theta=base*k)` already scales it. Make it explicit and documented so the AC is satisfied legibly rather than by accident.
  - **AC #3:** a solver-level `relax_min_climb_slope` invariant would be **vacuous** on this fixture. The expected resolution is "document the existing 8 as sufficient" with that rationale. Add an invariant only if you find a non-vacuous way to exercise it (you likely won't without routing the fixture through climb detection — out of scope here).

- **`relax_theta` non-vacuity (AC #2).** Story 4.2 reconciled this to a suite-level test (`test_relax_theta_binds_on_at_least_one_seed`) because under route-level semantics the seeds' feasibility boundaries no longer coincide, so a single θ pair can't make a per-seed strict-`>` guard bind for all 5 seeds (seed 21 goes infeasible just above 0.45; seed 26 only bends near 0.46 — disjoint). The minimal, defensible finalization is to **keep the suite-level guard** and remove the "full re-tuning deferred to 4.3" framing — the monotonicity (`>=`) invariant holds per-seed and non-vacuity holds suite-wide, which is a complete and honest re-validation. Retuning the fixture/seeds for a per-seed strict guard is optional and only worth it if it comes out clean; do not over-invest.

- **Clear the 4.2 → 4.3 breadcrumbs.** `test_metamorphic.py` carries explicit "Story 4.3 scope" / "deferred to Story 4.3" notes (in the `_RELAX_THETA_*` comment block and the `scale_elevation` docstring). Once AC #1–#3 land, those notes are stale and must be removed so the suite reads as finished, not mid-migration.

- **Scope discipline (FR / requirements hygiene).** This is a re-validation + sync story. Do not refactor the solver/validator (Story 4.2's domain), do not retune the route-level θ default (tracked tuning item, not this story), and do not touch stages 1–7.

### Project Structure Notes

- **Modify (tests):** `tests/integration/test_metamorphic.py` — the only substantive code change (AC #1–#4).
- **Verify (no edit expected):** `tests/unit/test_cli_help.py`, `tests/e2e/test_cli_smoke.py` (AC #5); `_bmad-output/planning-artifacts/{prd,architecture,epics}.md` (AC #6).
- **Reuse, do not reinvent:** the existing `_params(...)` override factory, `_with_scaled_elevation`, `_base_graph`, and `_best_objective` helpers in `test_metamorphic.py` are the building blocks for AC #1–#3 — no new harness needed.

### Testing standards summary

- Metamorphic invariants are integration tests in `tests/integration/`; naming `test_<unit>_<scenario>` (Architecture §"Test organization"). `pytest.skip`/`xfail` are forbidden in this suite (Architecture §Cat 11c — pass-required).
- No new deps. Coverage floors hold (this story adds test logic, not source branches).
- Regression proof: the full pre-existing fixture/oracle/reproducibility suite must stay green — a *structural* break is a bug; a *value* shift would indicate accidental solver behaviour change (not expected in this story, which touches no source).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 4.3"](../planning-artifacts/epics.md) — BDD acceptance criteria
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-03.md §4C/3.8, §A5–A8, §6](../planning-artifacts/sprint-change-proposal-2026-06-03.md) — canonical handoff: 4.3 owns C/3.8 (metamorphic), 1.7 (CLI help), A5–A8 (arch doc sync, already applied)
- [Source: _bmad-output/implementation-artifacts/4-2-route-level-slope-enforcement-solver-oracle-validator-metric-fix.md §"Completion Notes" + §"Review Findings"](4-2-route-level-slope-enforcement-solver-oracle-validator-metric-fix.md) — why `relax_theta` was reconciled to suite-level; the metamorphic re-validation explicitly deferred here
- [Source: tests/integration/test_metamorphic.py:180-222,290-308](../../tests/integration/test_metamorphic.py) — `_RELAX_THETA_*` block + suite-level guard + `scale_elevation` invariant carrying the 4.3 breadcrumbs
- [Source: tests/integration/conftest.py:215-250](../../tests/integration/conftest.py) — `make_toy_solver_params` couples `min_climb_slope=theta` (no independent override)
- [Source: src/steeproute/solver/grasp.py](../../src/steeproute/solver/grasp.py) — GRASP consumes `theta` (route-level gate), never `min_climb_slope` (the AC #1/#3 inert-in-fixture fact)
- [Source: tests/unit/test_cli_help.py:9-54](../../tests/unit/test_cli_help.py) — `--min-climb-slope` already in `QUERY_FLAGS` / `QUERY_ONLY_FLAGS` (Story 4.1)

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
uv run pytest -q              → 655 passed, 1 deselected in ~123 s
                                (unchanged from the 4.2 baseline — this story
                                 finalizes existing tests, adds/removes none)
metamorphic suite             → 41 passed in ~13 s (8 invariants × 5 seeds +
                                 the suite-level relax_theta non-vacuity guard)
CLI help + smoke              → 92 passed (--min-climb-slope listing + exit-2 path)
```

### Completion Notes List

**Mostly a verification story, as scoped — the only behaviour-bearing edit is one assertion's parameters.** The doc sync (PRD/architecture/epics) and CLI help/smoke `--min-climb-slope` coverage were front-loaded during the 2026-06-03 correct-course and Story 4.1; this story confirmed them in place and untouched, and finalized the metamorphic suite. Net source diff: a backward-compatible factory keyword + test-only changes. No `src/` production code changed.

**The `min_climb_slope` inertness fact resolved both fuzzy ACs cleanly.** Confirmed `min_climb_slope` is never read by `solver/grasp.py` — it drives `detect_climbs` (stage 8), upstream of the `ContractedGraph` the metamorphic suite builds directly. So:
- **AC #1:** `test_scale_elevation` now co-scales both `θ` and `min_climb_slope` by `k` *explicitly* (passing both to the factory), replacing the previous implicit coupling. The test docstring records that the co-scaling is intent-documentation (inert on this fixture), not a behaviour change.
- **AC #3:** No `relax_min_climb_slope` invariant was added — it would be vacuous (the solver ignores the parameter, so a strict-gain guard would fail). Instead added a "Why there is no `min_climb_slope` invariant" section to the module docstring documenting the existing 8 as the complete solver-level set, and noting that detection-side monotonicity belongs to a climb-detection test.

**`relax_theta` finalized at suite level (AC #2).** Kept Story 4.2's `test_relax_theta_binds_on_at_least_one_seed` as the permanent non-vacuity guard and reworded the `_RELAX_THETA_*` comment to present it as the finished design (per-seed `>=` monotonicity + suite-level strict-gain together pin the relation). Removed the lingering "Full fixture re-tuning … are Story 4.3 scope" breadcrumb. A per-seed strict guard is genuinely impossible under route-level semantics (the seeds' feasibility boundaries are disjoint — seed 21 vs seed 26), so retuning was correctly *not* pursued.

**Factory change (`make_toy_solver_params`) is backward-compatible.** Added `min_climb_slope: float | None = None`; when `None` it resolves to `theta` (the prior hardcoded behaviour), so every existing caller is byte-identical. Only `test_scale_elevation` passes it explicitly. Confirmed via the full suite staying at 655 passed with no value drift.

**AC walkthrough:**
1. AC #1 — `scale_elevation` co-scales `θ` + `min_climb_slope`; docstring documents inert-in-fixture intent; passes on all 5 seeds. ✅
2. AC #2 — `relax_theta` suite-level guard retained + reworded as final; all 4.3 breadcrumbs removed from `test_metamorphic.py`. ✅
3. AC #3 — no vacuous invariant added; module docstring documents the 8 as the sufficient solver-level set with the upstream/vacuity rationale. ✅
4. AC #4 — all 8 invariants green (41 passed), no skip/xfail. ✅
5. AC #5 — verified `--min-climb-slope` in `QUERY_FLAGS`/`QUERY_ONLY_FLAGS` (help) + smoke exit-2 path; 92 passed; nothing missing. ✅
6. AC #6 — PRD (FR3/FR3b, schema, defaults), architecture (stage 8, CLI-split note, constraint table, metadata 13-param list), epics FR-map verified consistent; no stragglers; no edits needed. ✅
7. AC #7 — ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 655 passed ✅; no new deps; coverage floors held. ✅

### File List

**Modified (tests):**
- `tests/integration/test_metamorphic.py` — `scale_elevation` co-scales `min_climb_slope` with `θ` (AC #1) + docstring; `_RELAX_THETA_*` comment reworded to final form, 4.3 breadcrumbs removed (AC #2); module docstring gains a "Why there is no `min_climb_slope` invariant" section (AC #3).
- `tests/integration/conftest.py` — `make_toy_solver_params` gains a backward-compatible `min_climb_slope: float | None = None` keyword (resolves to `theta` when `None`), enabling the explicit co-scaling in `test_scale_elevation`.

**Verified consistent — no edit needed:**
- `tests/unit/test_cli_help.py`, `tests/e2e/test_cli_smoke.py` — `--min-climb-slope` help-listing + exit-2 coverage already present (Story 4.1).
- `_bmad-output/planning-artifacts/{prd,architecture,epics}.md` — route-level semantics already synced (correct-course + Story 4.1).

**Modified (tracking):**
- `_bmad-output/implementation-artifacts/4-3-revalidate-metamorphic-and-cli-tests-and-doc-sync.md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked to `review`; `last_updated`.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-03 | Yann (Claude Opus 4.8) | Story 4.3 implemented: metamorphic-suite re-validation + verification. `scale_elevation` now co-scales `θ` and `min_climb_slope` by `k` explicitly (intent-documentation — `min_climb_slope` is inert in this GRASP-only fixture, driving `detect_climbs` upstream); `relax_theta` finalized at suite level with the 4.2 "deferred to 4.3" breadcrumbs removed; a module-docstring section documents why no `relax_min_climb_slope` invariant exists (it would be vacuous). Added a backward-compatible `min_climb_slope` keyword to `make_toy_solver_params`. Verified CLI help/smoke `--min-climb-slope` coverage (Story 4.1) and PRD/architecture/epics route-level consistency (correct-course) — both already in place, no edits. All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 655 passed (unchanged baseline); metamorphic 41 passed. No new deps. Status → review. | _pending_ |
