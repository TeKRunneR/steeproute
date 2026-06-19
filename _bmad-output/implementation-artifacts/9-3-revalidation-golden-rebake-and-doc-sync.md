# Story 9.3: Revalidation, golden rebake, and doc sync (Epic 9 closeout)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want the route-output changes from Stories 9.1 and 9.2 revalidated end-to-end and the regression baselines regenerated,
So that the suite reflects the corrected behavior and Story 8.5 can tighten the quality threshold against a trustworthy baseline.

## Acceptance Criteria

1. Both golden tiers are rebaked to the post-9.1+9.2 output. `uv run update-regression --all` regenerates the four fast-tier goldens (`grenoble_small`, `belledonne`, `vercors`, `chartreuse`) and `uv run update-regression --all --tier realistic` regenerates the four realistic-tier goldens. `tests/e2e/test_pinned_regressions.py` passes — the fast tier under default `uv run pytest`, the realistic tier under `uv run pytest -m slow`. The goldens are committed with an **explicit rationale** in the commit message (the route-output shift from climb maximality #7 + θ-prefix recovery #10), per the `update-regression` reminder and the Epic 9 sequencing.

2. The 8 metamorphic invariants (`tests/integration/test_metamorphic.py`) and the Story 3.7 GRASP-vs-exhaustive gate (`tests/integration/test_solver_on_toy_graph.py`) pass. `QUALITY_THRESHOLD` is left at `0.80` — tightening it against the post-Epic-9 baseline is **Story 8.5's** job, explicitly not done here.

3. The degradation e2e (`tests/e2e/test_degradation.py`) passes **and stays meaningful**: `test_relaxed_jmax_produces_more_routes` still asserts a genuine tight-`<`-relaxed gain across Journey 2's tighten→relax loop. If the 9.1+9.2 route-count shift made the existing `--theta 0.35` / `--j-max 0.30→0.50` regime non-binding (per Story 9.1 Dev notes, `grenoble_small` began returning the same count at both J_max values), re-tune the degradation knobs so the loop is exercised again — do **not** neuter the assertion to make it pass.

4. Docs are consistent with the shipped 9.1+9.2 behavior: the Story 3.2 maximality note (epics.md), the exhaustive-oracle "identical feasible set" docstring, and any README Known-Limitations wording carry no stale claim (climbs forward-only / seed-dependent, or GRASP discarding θ-feasible prefixes). Most of this was updated inside 9.1/9.2 — this AC is the verification pass plus any residual fixup.

5. The full suite passes on Windows by default (`uv run pytest`) — including the five e2e tests 9.1+9.2 deferred (the four `test_pinned_regressions.py` goldens + `test_degradation.py`) — and the realistic tier passes via `uv run pytest -m slow`. All four CI gates green: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest --cov`. No new runtime deps.

6. *(Optional, not a gate)* A `bmad-checkpoint-preview` on a real Grenoble area confirms climbs root at their true bottoms and the returned route set improved; capture the observation if run.

## Tasks / Subtasks

- [x] Task 1: Rebake both golden tiers (AC: #1). Ran `uv run update-regression --all` then `uv run update-regression --all --tier realistic` against the committed caches; reviewed the before/after diffs — pure value churn (8 goldens, 93/93 fast + 94/94 realistic insert/delete), no `params_hash`/`seed` drift, all fixtures held 5 routes (no `min_routes` collapse).
- [x] Task 2: Revalidate the must-stay-green logic gates (AC: #2). Metamorphic suite (8 invariants) + Story 3.7 gate green (46 passed); `QUALITY_THRESHOLD` untouched at `0.80`.
- [x] Task 3: Reconcile the degradation e2e (AC: #3). The 9.1+9.2 route recovery made `--j-max` non-binding on this fixture (full N=5 even at j-max 0.02); re-anchored the degradation regime to `--theta 0.50` (feasibility-bound) across all three affected tests, keeping a genuine tight-`<`-relaxed gain. Reasons documented in both test modules.
- [x] Task 4: Doc-sync verification pass (AC: #4). Confirmed the Story 3.2 note, oracle "identical feasible set" docstring, and README Known-Limitations are accurate (already synced in 9.1/9.2); no residual stale wording.
- [x] Task 5: Full-suite + four-gate green on Windows, plus the realistic slow tier (AC: #5). 779 default + 4 slow passed; ruff/format/basedpyright(0/0/0)/pytest --cov all green.

## Dev Notes

**This is a data-and-revalidation closeout, not a logic change.** The route-output shift it absorbs was already produced by 9.1 (`detect_climbs` now bottom-rooted) and 9.2 (GRASP recovers θ-clearing prefixes); both fixes are on `main`. The only code judgement here is re-tuning the **degradation test** regime (Task 3) — everything else is rebaking goldens, running gates, and verifying docs.

### Golden rebake (the core action)

- The harness and the writer share `src/steeproute/regression.py`, so the comparison and the regenerated golden can never disagree. `update-regression` re-runs each fixture's committed cache at its **explicitly-pinned** params (`_PINNED_PARAMS` fast / `_REALISTIC_PARAMS` ~200k iters), prints a before/after diff, and overwrites the golden. Eight goldens total: 4 fixtures × {fast, realistic}.
- Read the diffs before committing. Expected: scalar metrics and `canonical_edge_sequence_hash` move, possibly route counts; `params_hash` and `seed` should **not** change (you pinned no new params). A `params_hash` change means an accidental knob edit — investigate, don't bake it.
- `min_routes` (≥1) refuses to bake a run that collapsed to ~zero routes, turning a real regression into a green no-op — so a rebake that trips it is a signal to investigate, not to lower the floor.
- The realistic tier runs 4 fixtures at ~200k iters — expect it to take real wall-clock time. It is deterministic (FR29): `--time-budget` is pinned high so wall-clock never binds; termination is iteration/stagnation-based.

### Degradation reconciliation (the one judgement call)

`test_degradation.py` induces degradation with `--theta 0.35` so distinctness (not feasibility) binds: at `--j-max 0.30` the fixture returns `< N=5`, and relaxing to `0.50` admits more. Recovering more feasible routes (9.1+9.2) can make `grenoble_small` hit the same count at both J_max values, making `test_relaxed_jmax_produces_more_routes` vacuous. Re-tune the regime (a tighter J_max pair, a different θ, or both) so the tight-`<`-relaxed gain is genuine again. The companion `test_degradation_returns_fewer_than_n_with_explanation` and the `_DEGRADATION_PATTERN` message must stay coherent with whatever J_max you settle on. Keep counts asserted as inequalities (not exact GRASP tipping-point numbers), as the file already does.

### What is in / out of scope

- **In:** the 8 goldens, the metamorphic + 3.7 revalidation, the degradation reconciliation, the doc-sync verification, the four gates.
- **Out:** any `QUALITY_THRESHOLD` change (Story 8.5); any new solver/climb logic; cache invalidation (both fixes are query-side — `pipeline_content_hash` is unaffected, caches are **not** regenerated). The `test_climb_detection_fixture.py` integration baselines were already re-recorded in 9.1 and are untouched by 9.2 (solver-side), so they need no change here.

### Determinism, environment, commit hygiene

- Seeded GRASP is byte-deterministic, so a clean rebake is reproducible run-to-run on the same cache. Float aggregates compare with `math.isclose(abs_tol=1e-9)`; the golden hash tuple compares exactly.
- If the editable build is stale at session start, `uv run` can hit the corporate-TLS cert flake (≈43 `test_cli_smoke` fail). Settle once with `uv sync --native-tls`, then run with `uv run --no-sync` (Story 9.1/9.2 Debug Logs). Run `tests/unit` and `tests/integration` in **separate** pytest invocations to avoid the `conftest` name collision noted in 9.2.
- Golden-updating commits **must** state an explicit rationale in the message (enforced by convention + the `update-regression` closing reminder).

### Project Structure Notes

- **Regenerated (data):** `tests/e2e/goldens/{grenoble_small,belledonne,vercors,chartreuse}.json` (fast) and `…{...}.realistic.json` (realistic).
- **Possibly modified:** `tests/e2e/test_degradation.py` (degradation-regime re-tune only); doc residuals in `_bmad-output/planning-artifacts/epics.md`, `tests/integration/exhaustive_oracle.py`, or `README.md` if the verification finds stale wording.
- **Not touched:** `src/steeproute/**` logic, `tests/integration/test_climb_detection_fixture.py` baselines, any cache directory.

### Prerequisite note

Story 9.1 is still at `review` (not `done`) in sprint-status, though its code is on `main` (commit `6b7dd4e`). 9.3's revalidation covers both 9.1 and 9.2; closing 9.1 out is a separate process step. See the question at the end.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 9.3"](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; §"Story 3.2" maximality note
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-18-route-discovery-quality.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-18-route-discovery-quality.md) — §4B closeout sequencing; Story 8.5 runs after Epic 9
- [Source: src/steeproute/regression.py](src/steeproute/regression.py) — `update-regression` entry point, `FIXTURES` / `REALISTIC_FIXTURES`, `_PINNED_PARAMS`, `min_routes` floor, `golden_path` tiering
- [Source: tests/e2e/test_pinned_regressions.py](tests/e2e/test_pinned_regressions.py) — fast + `slow`-gated realistic golden gates
- [Source: tests/e2e/test_degradation.py:73](tests/e2e/test_degradation.py) — `test_relaxed_jmax_produces_more_routes` to keep meaningful
- [Source: tests/integration/test_metamorphic.py](tests/integration/test_metamorphic.py) — the 8 invariants to revalidate
- [Source: tests/integration/test_solver_on_toy_graph.py:34](tests/integration/test_solver_on_toy_graph.py) — Story 3.7 gate; `QUALITY_THRESHOLD` stays `0.80`
- [Source: _bmad-output/implementation-artifacts/9-1-climb-detection-maximality.md](_bmad-output/implementation-artifacts/9-1-climb-detection-maximality.md) — deferred-to-9.3 list (4 goldens + degradation); fixture baseline already re-recorded
- [Source: _bmad-output/implementation-artifacts/9-2-grasp-theta-feasible-prefix-recovery.md](_bmad-output/implementation-artifacts/9-2-grasp-theta-feasible-prefix-recovery.md) — second route-output shift; conftest split-run note; oracle docstring already synced

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (1M context) (`claude-opus-4-8[1m]`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps. Settled the stale-editable-build / corporate-TLS flake once with `uv sync --native-tls`, then ran everything with `uv run --no-sync` (per Story 9.1/9.2). Plain `uv run pytest` (testpaths-driven, one invocation) ran clean — the unit/integration `conftest` collision only bites when both dirs are passed as explicit path args, not in the CI-style whole-tree run.

**Red baseline confirmed (the 9.1+9.2 blast radius this story absorbs):** before rebake, `tests/e2e/test_pinned_regressions.py` (4 fast goldens) + `test_degradation.py` (2) + `test_run_summary.py::test_degraded_path` (1) failed = 7 e2e tests, all from the route-output shift.

**Golden rebake diffs (AC #1):** fast `update-regression --all` → 93 insert / 93 delete; realistic `--all --tier realistic` → 94/94. Pure value churn (objective/D±/edge_count/hash); **no `params_hash` or `seed` change**, all four fixtures held 5 routes (no `min_routes` collapse).

**Degradation regime probe (AC #3):** swept θ∈[0.20,0.50] × j-max∈[0.02,0.90] (throwaway probe, deleted). Post-9.1+9.2 the fixture returns a full N=5 of near-disjoint routes even at j-max 0.02, and ≥20 distinct routes at `--n 20` — so `--j-max` no longer binds at any feasible θ. Degradation is now feasibility-bound: `--theta 0.50` → 2 routes (degraded), `--theta 0.20` → 5. Re-anchored all three degradation tests to that regime.

**Pre-existing gate debt fixed (per user direction):** whole-tree `basedpyright` was red at HEAD (11 errors + 18 warnings) and `ruff format` flagged 2 files — all in files untouched since Epic 6; 9.1/9.2 only ran basedpyright scoped to their one changed source file, so it was never caught. basedpyright here fails on warnings too (exit 1), so cleared both. Fixes: real type annotations (`_contract_from`/`_out_and_back_contracted` `-> object` → `-> ContractedGraph`), removed a vestigial unused `tmp_path` param, an inline `reportArgumentType` ignore for an nx int-edge-key, and per-file pragma headers for external-boundary/test-idiom noise (rasterio/mock signatures, `pytest.approx`, networkx graph access, private-helper unit tests) — the project's documented per-file relaxation pattern.

**Final gates (all `--no-sync`):**

```
pytest (default tier)              → 779 passed, 6 deselected
pytest -m slow (realistic tier)    → 4 passed, 781 deselected
pytest --cov=src/steeproute        → 779 passed; 95% total
ruff check / ruff format --check   → clean (excl. untracked .tmp_extract/ junk — see below)
basedpyright (whole tree)          → 0 errors, 0 warnings, 0 notes (exit 0)
```

**Note — untracked `.tmp_extract/`:** an untracked junk dir (`extract_all.py` + `extracted.txt`, present at session start) trips the *local* `ruff check`; it is not committed so CI on a clean checkout never sees it. Left in place (not mine to delete) — recommend removing it.

### Completion Notes List

**Scope as written — data + revalidation, one judgement call.** The route-output shift was produced by 9.1 (climb maximality) + 9.2 (θ-prefix recovery), both already on `main`; this story rebakes the 8 goldens, re-runs the must-stay-green gates, reconciles the deferred e2e tests, and verifies docs. No production-code logic changed.

**Degradation reconciliation (the judgement call).** The original tests induced degradation at `--theta 0.35` and relaxed `--j-max` to demonstrate Journey 2's distinctness tuning. The Epic 9 fixes made the solver find a far richer, near-disjoint route set, so distinctness stopped binding on this small fixture at any feasible θ — relaxing `--j-max` (even 0.10→0.90) admits nothing more. Rather than neuter the assertion, I re-anchored the regime to `--theta` (the lever that genuinely binds now): `--theta 0.50` degrades below N=5, relaxing to `--theta 0.20` admits the full set. Renamed `test_relaxed_jmax_produces_more_routes` → `test_relaxed_theta_produces_more_routes`; updated both module docstrings to explain why theta, not j-max. Distinctness/Jaccard logic stays covered by the `TopNTracker` unit tests and the `relax_j_max` metamorphic invariant. The degradation message still names `J_max <= 0.30` (j-max unchanged at its default), so the assertion regexes match unchanged.

**A third degradation-dependent test surfaced.** Beyond the two `test_degradation.py` tests the story anticipated, `test_run_summary.py::test_degraded_path` also relied on the `--theta 0.35` regime (its own `_DEGRADE_THETA`); reconciled it identically.

**Pre-existing gate debt (resolved on your call).** The whole-tree `basedpyright`/`ruff format` gates were already red at HEAD on Epic-6-era files, unrelated to the golden rebake. Per your decision, fixed them so the Epic 9 closeout genuinely lands all four gates green: real type fixes where the annotation was wrong, per-file pragmas (the project's sanctioned pattern) for boundary/test-idiom noise.

**`QUALITY_THRESHOLD` left at 0.80** — its tightening against the post-Epic-9 baseline is Story 8.5's job (now unblocked).

**AC #6 optional checkpoint not run** — `bmad-checkpoint-preview` on a real Grenoble area is optional and not a gate; available on request.

**Code-review follow-ups applied (review findings #1/#2/#3).** A `/code-review` pass found no correctness bugs (the 8 goldens verified clean: objective = D⁺+D⁻ on every row, no degenerate routes, no `params_hash`/seed drift). Three quality/accuracy items were fixed:
- **#2 (degradation message accuracy):** `cli/query.py::_degradation_message` previously asserted a single cause — "additional candidates would exceed the overlap threshold" — which is false when degradation is feasibility-bound (the new theta regime). Reworded to state the observable shortfall and name both levers: `Only X of N requested routes satisfy the current constraints (theta=…, J_max <= …); relax --theta or --j-max to admit more.` Updated the two asserting e2e regexes (`test_degradation.py`, `test_run_summary.py`); `test_output.py`'s sample-string render test is message-agnostic and untouched.
- **#1 (doc drift from the rename + message change):** added forward-pointer notes to `epics.md` §Story 7.4 (the AC source-of-truth) and the `7-4` story artifact, since both still named `test_relaxed_jmax_produces_more_routes` and the old `--j-max`/message regime.
- **#3 (over-broad pragma):** replaced the file-wide `reportUnusedParameter=false` in `test_dem_download.py` with four inline `# pyright: ignore[reportUnusedParameter]` on the urlopen-mock callbacks, so a genuinely-unused param elsewhere in that file still gets flagged.

All four gates stayed green after the fixes (779 default + 4 slow; basedpyright 0/0/0).

### File List

**Regenerated (data — golden rebake, AC #1):**
- `tests/e2e/goldens/{grenoble_small,belledonne,vercors,chartreuse}.json` — fast tier
- `tests/e2e/goldens/{grenoble_small,belledonne,vercors,chartreuse}.realistic.json` — realistic tier

**Modified (degradation reconciliation, AC #3 + review #2 message rewording):**
- `tests/e2e/test_degradation.py` — regime re-anchored to `--theta 0.50`; `test_relaxed_jmax_produces_more_routes` → `test_relaxed_theta_produces_more_routes`; module + test docstrings updated; `_DEGRADATION_PATTERN` + the no-degradation sentinel updated for the reworded message.
- `tests/e2e/test_run_summary.py` — `_DEGRADE_THETA` → `--theta 0.50`; module docstring + comment updated; degradation regex updated for the reworded message.

**Modified (code-review follow-ups):**
- `src/steeproute/cli/query.py` — `_degradation_message` reworded to be cause-neutral and name both `--theta`/`--j-max` levers (review #2); docstring updated.
- `_bmad-output/planning-artifacts/epics.md` — §Story 7.4 update note (rename + reworded message) (review #1).
- `_bmad-output/implementation-artifacts/7-4-graceful-degradation-messaging-for-sparse-areas.md` — forward-pointer note (review #1).

**Modified (pre-existing gate debt, AC #5):**
- `tests/integration/test_route_discovery_fixes.py` — `_contract_from` / `_out_and_back_contracted` annotated `-> ContractedGraph` (import added); fixes 9 basedpyright errors.
- `tests/unit/test_graph_contraction.py` — two inline `# pyright: ignore[reportArgumentType]` for nx int edge-key indexing.
- `tests/unit/test_area_parsing.py` — removed vestigial unused `tmp_path` param from `test_setup_cli_rejects_nan_radius`.
- `tests/unit/test_dem_download.py` — pragma header extended (reportPrivateUsage/UnannotatedClassAttribute file-wide; reportUnusedParameter handled inline per-callback after review #3) + ruff reformat.
- `tests/unit/test_check_coverage.py` — existing pragma extended with `reportUnknownMemberType` + ruff reformat.
- `tests/unit/test_progress_helpers.py` — added `reportUnknownMemberType` pragma (pytest.approx).
- `tests/fixtures/grenoble_small/regenerate.py` — pragma extended with `reportPrivateUsage`.

**Modified (status):**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — `9-3-…` walked `ready-for-dev → in-progress → review`.

### Change Log

| Date | Author | Description |
|---|---|---|
| 2026-06-19 | Yann (Claude Opus 4.8) | Code-review follow-ups (#1/#2/#3): reworded `cli/query.py` degradation message to be cause-neutral (names both `--theta`/`--j-max` levers) instead of falsely asserting overlap-rejection — degradation can now be feasibility-bound; updated the two e2e regexes. Added doc-drift forward-pointers to epics.md §7.4 + the 7-4 artifact (rename + reworded message). Replaced the over-broad file-wide `reportUnusedParameter=false` in test_dem_download.py with four inline per-callback ignores. All four gates green (779 default + 4 slow; basedpyright 0/0/0). |
| 2026-06-19 | Yann (Claude Opus 4.8) | Story 9.3 implemented (Epic 9 closeout). Rebaked both golden tiers (8 goldens, fast + realistic) to absorb the 9.1+9.2 route-output shift — pure value churn, no params_hash/seed/route-count drift. Revalidated the 8 metamorphic invariants + Story 3.7 gate green (QUALITY_THRESHOLD left 0.80). Reconciled degradation e2e: the Epic 9 route recovery made `--j-max` non-binding (near-disjoint route set), so re-anchored all three degradation-dependent tests (test_degradation.py ×2 + test_run_summary.py::test_degraded_path) to a feasibility-bound `--theta 0.50` regime, preserving a genuine tighten→relax gain. Verified docs consistent. Fixed pre-existing whole-tree basedpyright (11 errors + 18 warnings) and ruff-format debt from Epic-6 test files (per user direction) so all four gates land green: 779 default + 4 slow passed, ruff/format clean, basedpyright 0/0/0, 95% coverage. No production-code logic changed; no new deps. |
