# Story 10.2: Direction-aware descent-slope cap (FR32)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want an opt-in cap that refuses to descend a segment steeper than a threshold while still letting routes climb that segment,
so that returned routes don't bomb down dangerous grades.

## Acceptance Criteria

1. **Windowed descent metric (Stage 7).** Every edge gains a `max_windowed_descent_grad` attribute — the steepest uphill-measured running-average gradient over a fixed distance window along the edge's resampled profile — computed in `compute_edge_metrics` (`pipeline/climbs.py`) from `vertices_resampled`. Parameter-independent of all `SolverParams`; deterministic; no RNG. Carried through stage-9 contraction on connectors, and set on super-edges (aggregated over their base edges).

2. **New opt-in flag.** `--max-descent-slope` is a float option on `steeproute`, default **off** (None), surfaced in `--help`, range-validated at the CLI boundary (finite, `> 0`), threaded into `SolverParams` and hence each report's metadata (FR19). Absent from `steeproute-setup`.

3. **Flag-off ⇒ byte-identical.** With `--max-descent-slope` unset, GRASP/oracle/validator behave exactly as today and the existing default-param regression goldens (fast and realistic tiers) match **without rebake**.

4. **Flag-on solver + oracle feasibility.** With the cap set, GRASP construction and the exhaustive oracle reject any **descending** traversal of an edge whose `max_windowed_descent_grad` exceeds the cap; **uphill** traversal is unconstrained, so the same segment stays eligible as a climb. The descent predicate is single-sourced so GRASP and the oracle stay on one shared feasible set (Story 3.7 stays apples-to-apples). FR29 byte-identical reproducibility holds with the cap on.

5. **Flag-on validation.** A `max_descent_slope` check joins the validated per-route constraint set (FR26): when the cap is active, a returned route that descends an over-cap segment is flagged with a `ConstraintViolation`, rendered with the prominent banner (FR27), and drives the dedicated non-zero exit code while all results are still written to disk (FR28).

6. **Flag-on golden.** A new flag-on golden fixture pins a real Grenoble-area run with `--max-descent-slope` set, with a test asserting no returned route descends an over-cap segment. Existing default-param goldens are left untouched (no rebake).

7. **New metamorphic invariant.** A 9th invariant joins the suite: relaxing `--max-descent-slope` (raising the cap) never decreases the best objective. Non-vacuous on its fixture (the tighter cap demonstrably filters a descent the looser cap admits). The existing 8 invariants and the Story 3.7 gate still pass unchanged (their fixtures don't set the cap).

8. **FR12 messaging.** When the active cap shrinks the result set below `--n`, the existing graceful-degradation path names `--max-descent-slope` among the levers (alongside `--theta` / `--j-max` / `--start-at-junction`).

9. **Epic 10 closeout + all four CI gates green on Windows** (`ruff check`, `ruff format --check`, `basedpyright` 0/0/0, `pytest --cov`), pure-logic coverage floors held on changed paths, no new runtime deps. Re-validate the metamorphic suite and the Story 3.7 gate; confirm both default-param golden tiers match without rebake; sync PRD/architecture/epics docs. Optional `bmad-checkpoint-preview` on a real Grenoble area confirms sensible avoided steep descents.

## Tasks / Subtasks

- [x] Task 1: Compute `max_windowed_descent_grad` in `compute_edge_metrics` (AC: #1). Add a module-scope named-constant window (e.g. `_DESCENT_WINDOW_M`); slide it along `vertices_resampled` and take the max windowed gradient magnitude. Carry it through `contract_climbs` (connectors via `**data`; set explicitly on super-edges). Add unit/property tests for the metric (extend `tests/unit/test_climbs.py`).
- [x] Task 2: Add `--max-descent-slope` flag + `SolverParams.max_descent_slope` and wire it (AC: #2, #3). Option in `cli/_shared.py`, range check in `validate_solver_options`, stack on `cli/query.py`, pass into `SolverParams` (added **last**, default `None`).
- [x] Task 3: Single-source the descent-feasibility predicate and apply it in GRASP and the oracle (AC: #4). New helper (mirror `solver/reuse.py`) reading `max_windowed_descent_grad` off edge data; off when the cap is None. Add a flag-on GRASP-vs-oracle one-feasible-set test + an FR29 determinism check with the cap on.
- [x] Task 4: Add the `max_descent_slope` validator constraint (AC: #5); validator unit tests (pass + reject).
- [x] Task 5: Wire FR12 messaging to name `--max-descent-slope` when active (AC: #8); extend the degradation test.
- [x] Task 6: Add the flag-on golden fixture + the "no over-cap descent" assertion; confirm existing goldens match without rebake (AC: #6, #3).
- [x] Task 7: Add the 9th metamorphic invariant (relax-cap → objective non-decreasing), non-vacuous (AC: #7).
- [x] Task 8: Epic 10 closeout — re-validate all gates, the 8 existing invariants, the Story 3.7 gate; sync PRD/architecture/epics docs; optional checkpoint-preview (AC: #9).

## Dev Notes

### Recommendation (read first)

Additive, opt-in (default off → no default-output change) — same shape as Story 10.1. The genuine design question the correct-course proposal deferred here is *how directionality enters feasibility*. The smallest correct cut:

- **The metric is direction-agnostic; the *traversal* is directional.** `max_windowed_descent_grad` is a property of the physical segment (steepest grade anywhere on it, measured uphill — same value either way you walk it). Compute it once per base edge in stage 7. A specific *traversal* `u→v` is a **descent** when it nets elevation loss (`data["d_minus_m"] > data["d_plus_m"]`). Block iff *descent AND `max_windowed_descent_grad > cap`*. Uphill traversal is never blocked → the segment stays climbable. **Recommended descent predicate: net-loss.** (Alternatives — any `d_minus_m > 0`, or sign of the windowed value — are the dev's call; net-loss matches the intent "don't descend steep climbs" without blocking rolling connectors.)
- **There is no reverse super-edge to find.** The proposal's "a super-edge taken in reverse is a descent" describes the *physical* act. Mechanically, super-edges exist only in their ascending direction; **descending a climb means walking the reverse-direction base connectors**, which `contract_climbs` already carries over (the reverse `(v,u,k)` is not in `climb_edge_ids`, so it survives as a connector — this is the same fact the FR5 reuse rule leans on). Those reverse connectors carry the high `max_windowed_descent_grad` and net-loss, so the per-edge check above blocks them with no special super-edge handling. A forward super-edge nets gain → never a descent → never blocked. Don't go hunting for a reverse super-edge.
- **Single-source the feasibility check** (a `solver/descent.py` helper, the same discipline as `solver/reuse.py` / `is_junction_node`) so GRASP `_build_rcl`, the oracle `_dfs`, and `validator._validate_edges` can never drift onto different feasible sets — the property the Story 3.7 gate depends on.

### The windowed metric + window placement

`max_windowed_descent_grad` per base edge: slide a window of ~`_DESCENT_WINDOW_M` ground-meters along `vertices_resampled` (~10 m spacing) and take the max of each window's gradient magnitude (window rise / run). It captures a short steep section a whole-edge `avg_gradient` would average away — that's the point of "windowed."

⚠️ **Keep the window a module-scope named constant, not a CLI flag.** Architecture §3c calls the metric "parameter-independent"; a window flag would break that and re-open the FR29/caching story for no v1 benefit. The PRD ("distance-window sub-parameter finalized during implementation") leaves the door open to surface it later. Stage 7 already runs **query-side** ([query.py:233](src/steeproute/cli/query.py)), so the metric is recomputed each query from the cached raw vertices — see the cache note below.

### Threading the flag (minimal blast radius)

- **`models.SolverParams`**: add `max_descent_slope: float | None = None` as the **last** field (after `start_at_junction`). Default keeps every existing `SolverParams(...)` construction valid; `asdict(params)` flows it into the sidecar/HTML metadata (FR19) for free. Update the docstring's "14 parameters" → 15 and `tests/unit/test_models.py` if it pins the field set.
- **`cli/_shared.py`**: a `click.option("--max-descent-slope", type=click.FLOAT, default=None, ...)`. Add it to `validate_solver_options` — but only range-check when **not None** (finite, `> 0`), since None = off (mirror how `--stagnation-iters` handles its optional).
- **`cli/query.py`**: stack the decorator, add the kwarg, pass `max_descent_slope=max_descent_slope` into `SolverParams`; extend `_degradation_message` (one flag read, same pattern Story 10.1 used for `--start-at-junction`).

### Solver + oracle + validator (one feasible set, FR29)

- **GRASP** (`solver/grasp.py` `_build_rcl`): after the reuse/SAC filters, drop any candidate edge the descent helper blocks. Off when `params.max_descent_slope is None`. Pure function of edge data → FR29 holds.
- **Oracle** (`tests/integration/exhaustive_oracle.py` `_dfs`): apply the **same** helper at the same point so the enumerated set matches GRASP's exactly.
- **Validator** (`validator.py` `_validate_edges`): when the cap is active, walk the route's edges and emit a `max_descent_slope` `ConstraintViolation` for any over-cap descending traversal. This *is* the FR32 enforcement (the solver/oracle prune is the efficiency layer); it rides the existing FR27 banner / FR28 exit-code path with no other wiring. Single-source the predicate so a GRASP-admitted route never trips it.

### Validator: super-edge expansion vs. per-edge check

The validator already reads route edges off the contracted graph data dict (`get_edge_data`) for the reuse tally; do the same for the descent check — the carried-over reverse connectors are ordinary contracted edges, so no `super_edge_to_base` expansion is needed for the descent rule (a forward super-edge never descends). Keep the check on the contracted edge as traversed.

### Metamorphic invariant (the 9th)

`relax --max-descent-slope` (raise the cap) → best objective monotone non-decreasing, same shape as `test_relax_difficulty_cap_*` ([test_metamorphic.py:313](tests/integration/test_metamorphic.py)). The toy factory (`conftest.make_toy_contracted_graph`) builds the `ContractedGraph` directly and won't carry `max_windowed_descent_grad` — tag the toy edges (or build a small descent-bearing graph) so a tight cap genuinely filters a descent the loose cap admits, then assert `>=` per seed plus a suite-level strict-gain non-vacuity guard (mirror the `relax_theta` / `relax_difficulty_cap` precedent). Read the suite's "why the fixture is small and shallow" note first — the invariant must hold on a fixture where GRASP reaches the optimum.

### Cache: no re-prepare needed

`check_coverage` resolves caches by geographic containment and loads by stored `cache_key_hash`, never re-matching `pipeline_content_hash` ([cache.py — see Story 10.1 note]). Stage 7 runs **query-side** on the cached raw stage-1–5 graph, so the new metric is computed fresh every query: editing `pipeline/climbs.py` + `models.py` bumps `pipeline_content_hash` for any **fresh** `steeproute-setup`, but the committed regression-fixture caches still load by geography and recompute current stage 7 — so no fixture rebuild and (cap off) byte-identical output. No cache invalidation for this feature.

### Golden fixture (no harness change this time)

`--max-descent-slope` is a **value-taking** float, not a bare flag — `run_fixture` renders it as a `--flag value` pair with no `_BOOLEAN_FLAGS` entry needed (the Story 10.1 bare-flag wrinkle does not recur). Add a `FLAG_ON_FIXTURES` entry reusing the committed `grenoble_small` cache with `--max-descent-slope` pinned at a cap low enough to bite (eyeball the area's grades). The "no over-cap descent" assertion is a separate property test (assert no `max_descent_slope` validation violation on any sidecar) plus a committed golden, mirroring [test_junction_start.py](tests/e2e/test_junction_start.py). Keep it OUT of `regression.FIXTURES` (the zero-tolerance gate) — folding it in + the realistic tier is **Story 8.5's** job.

### Project Structure Notes

- **Modified:** `pipeline/climbs.py` (metric), `pipeline/graph.py` (carry/aggregate on super-edges), `models.py` (`SolverParams.max_descent_slope` + likely `Edge`/data contract), `cli/_shared.py` (flag + range check), `cli/query.py` (wire + FR12 message), `solver/grasp.py` (RCL filter), `validator.py` (constraint), `tests/integration/exhaustive_oracle.py` (DFS filter).
- **New:** `solver/descent.py` (single-sourced predicate), descent-metric tests, validator pass/reject tests, flag-on GRASP-vs-oracle + FR29 tests, the 9th metamorphic invariant, the flag-on golden fixture + its committed golden + the no-over-cap-descent assertion.
- **Out of scope:** folding the new golden into the CI gate + realistic tier (Story 8.5); any `steeproute-setup` change.

### Testing standards summary

- Float comparisons on aggregates use `math.isclose(..., abs_tol=1e-9)`; FR29 byte-identical tests use `==`.
- The metric is pure-logic (`pipeline/`) — held to the 95% coverage floor (Architecture §Cat 11e).
- Build-flake recovery (Epic 9/10.1 precedent): if `uv run` hits the corporate-TLS cert error on a stale editable build, settle once with `uv sync --native-tls`, then `uv run --no-sync`. Run `tests/unit` and `tests/integration` in **separate** pytest invocations (the `from conftest import ...` collision).
- New feature, not a defect fix — the flag-on golden + the no-over-cap-descent assertion + the 9th invariant are the equivalent pinning.

### References

- [Source: epics.md §"Story 10.2"](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; Epic 10 closeout folded here
- [Source: sprint-change-proposal-2026-06-25-junction-start-and-descent-cap.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-25-junction-start-and-descent-cap.md) — §1 #2, §2 technical impact #2, §4B (B3/B4/B5), §4C, §5
- [Source: architecture.md §Cat 3c edge-attribute contract + §Cat 6 constraints table + FR-coverage FR32](_bmad-output/planning-artifacts/architecture.md) — `max_windowed_descent_grad` site; descent constraint scope
- [Source: prd.md FR32 + Config Schema](_bmad-output/planning-artifacts/prd.md) — requirement text; window sub-parameter "finalized during implementation"
- [Source: src/steeproute/pipeline/climbs.py:56](src/steeproute/pipeline/climbs.py) — `compute_edge_metrics` (metric site); running-average machinery to mirror
- [Source: src/steeproute/pipeline/graph.py:148](src/steeproute/pipeline/graph.py) — connector carry-over (`**data`) + super-edge build (set the metric)
- [Source: src/steeproute/solver/grasp.py:405](src/steeproute/solver/grasp.py) — `_build_rcl` (add descent filter)
- [Source: src/steeproute/solver/reuse.py](src/steeproute/solver/reuse.py) — single-sourcing pattern to mirror for `solver/descent.py`
- [Source: tests/integration/exhaustive_oracle.py:190](tests/integration/exhaustive_oracle.py) — `_dfs` feasibility (mirror the filter)
- [Source: src/steeproute/validator.py:173](src/steeproute/validator.py) — `_validate_edges` (add the constraint)
- [Source: src/steeproute/cli/query.py:423](src/steeproute/cli/query.py) — `_degradation_message` (FR12 lever wording)
- [Source: src/steeproute/regression.py:195](src/steeproute/regression.py) — `FLAG_ON_FIXTURES` / `run_fixture` (value-taking flag, no harness change)
- [Source: _bmad-output/implementation-artifacts/10-1-junction-start-constraint.md](_bmad-output/implementation-artifacts/10-1-junction-start-constraint.md) — prior Epic 10 feature: one-feasible-set discipline, FR29, no-rebake proof, build-flake recovery

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps. Settled the known stale-editable-build / corporate-TLS flake once with `uv sync --native-tls`, then ran tests with `uv run --no-sync`.

**Test runs (all `--no-sync`):**

```
pytest (default markers, full)            → 821 passed, 6 deselected
pytest tests/e2e/test_pinned_regressions.py -m slow → 4 passed  (realistic-tier goldens match WITHOUT rebake — non-regression proof)
ruff check src tests                      → All checks passed!
ruff format --check src tests             → 101 files already formatted
basedpyright (whole project)              → 0 errors, 0 warnings, 0 notes
coverage (changed pure-logic)             → descent.py 100%, graph.py 100%, validator.py 100%, grasp.py 100%,
                                            models.py 100%, climbs.py 98% (≥ 95% floor)
```

The 8 existing metamorphic invariants + the new 9th, and the Story 3.7 GRASP-vs-exhaustive gate, all pass (their fixtures don't set the cap → unchanged).

**Pre-existing gate fix:** `uv run basedpyright` (the actual CI gate, whole-project) was already red at HEAD — `tests/unit/test_degradation_message.py` carried a mypy-style `# type: ignore[list-item]` that basedpyright doesn't honor (1 error) plus a `reportPrivateUsage` warning; Story 10.1 verified only changed *src* files and missed it. Fixed cleanly (`cast` + `# pyright: ignore[reportPrivateUsage]`) since the file was edited here, so the whole-project gate is now genuinely 0/0/0.

### Completion Notes List

**Windowed descent metric (Task 1).** `pipeline/climbs.py::_max_windowed_descent_grad` computes, per base edge in stage 7, the steepest sustained grade over a fixed `_DESCENT_WINDOW_M` (30 m) window — `|Δelevation| / run` between window endpoints, max over sliding windows ≥ the window length (whole-edge fallback for shorter polylines). **Direction-agnostic** (absolute net change) so a segment and its reverse share one value. A module constant, not a CLI flag, so the metric stays parameter-independent (Architecture §3c) and FR29-safe. Carried onto connectors verbatim by `contract_climbs` (`**data`) and onto super-edges as the max over their base edges.

**Flag + params (Task 2).** `--max-descent-slope` float option in `cli/_shared.py` (shows in `--help`), range-checked in `validate_solver_options` only when set (finite, `> 0`; `None` = off), threaded into `SolverParams.max_descent_slope` (added **last** with `= None` default → every existing positional construction stays valid). Flows into the JSON sidecar `params` block via `asdict(params)` (FR19) for free.

**One feasible set (Task 3).** `solver/descent.py::descends_over_cap` is the single source: a traversal is blocked iff the cap is set, it nets elevation loss (`d_minus_m > d_plus_m`), and `max_windowed_descent_grad > cap`. Uphill is never blocked → the segment stays climbable. GRASP `_build_rcl`, the oracle `_dfs`, and the validator all consult it — same discipline as `solver.reuse`. **There is no reverse super-edge:** descending a climb means walking the reverse-direction base connectors contraction already carries over; the per-edge check governs them with no special handling. `test_grasp_descent_cap.py` pins: cap-off best route descends the steep segment; cap-on no route descends it (the uphill alternative stays reachable); GRASP == oracle under the cap; FR29 determinism.

**Validator (Task 4).** `validator._validate_edges` adds a `max_descent_slope` `ConstraintViolation` for any over-cap descending traversal when the cap is active; rides the existing FR27 banner / FR28 exit-code path. 4 unit tests (descent-over-cap rejected; uphill-over-cap admitted = stays climbable; at-cap boundary `>` not `>=`; cap-off no-op).

**FR12 (Task 5).** `_degradation_message` names `--max-descent-slope` as both a cause and a lever when active; composes with the Story 10.1 `--start-at-junction` wording; flag-off wording byte-identical.

**Flag-on golden + property (Task 6).** New `FLAG_ON_FIXTURES` entry `grenoble_small_descent` reuses the committed `grenoble_small` cache with `--max-descent-slope 0.40` pinned (a value-taking float → no `_BOOLEAN_FLAGS` / `run_fixture` change needed, unlike 10.1's bare flag). Verified the cap **bites** (route set differs from flag-off) while still returning 5 routes. Committed golden `tests/e2e/goldens/grenoble_small_descent.json`. `tests/e2e/test_descent_cap.py` asserts (a) no route carries a `max_descent_slope` violation and (b) the run matches its golden. Kept OUT of `regression.FIXTURES` (the zero-tolerance CI gate) — folding it in + the realistic tier is Story 8.5's job. Existing default-param goldens (both tiers) match **without** rebake — verified empirically.

**9th metamorphic invariant (Task 7).** `test_relax_max_descent_slope_objective_non_decreasing` (relax cap → best objective non-decreasing) on a dedicated 4-node descent fixture — the toy factory models no descents, so the cap would be vacuous there. Per-seed `>=` plus a strict-gain non-vacuity guard, mirroring `relax_difficulty_cap`.

**No cache invalidation.** Stage 7 runs query-side on the cached raw graph, so the metric is recomputed every query; editing `pipeline/climbs.py` + `models.py` bumps `pipeline_content_hash` only for fresh setups. The committed caches load by geography and recompute current stage 7 → no rebuild, and (cap off) byte-identical output.

**Epic 10 closeout (Task 8).** All four CI gates green; both default-param golden tiers match without rebake; PRD (config schema + FR32) and architecture (§3c metric) synced to note the fixed-window decision. The optional `bmad-checkpoint-preview` visual confirmation was **not** run (left to the user — recommended on a real Grenoble area with `--max-descent-slope` set).

### File List

**Modified (src):**
- `src/steeproute/pipeline/climbs.py` — `_max_windowed_descent_grad` + `_DESCENT_WINDOW_M`; stage-7 sets the attribute; docstring.
- `src/steeproute/pipeline/graph.py` — carry/aggregate `max_windowed_descent_grad` onto super-edges (max over base edges).
- `src/steeproute/models.py` — `SolverParams.max_descent_slope: float | None = None` (last field) + docstring (14→15 params).
- `src/steeproute/solver/descent.py` — **new** single-sourced `descends_over_cap` predicate.
- `src/steeproute/solver/grasp.py` — store the cap; RCL descent filter; docstring.
- `src/steeproute/validator.py` — `max_descent_slope` per-route constraint + module docstring.
- `src/steeproute/cli/_shared.py` — `max_descent_slope_option` decorator + `validate_solver_options` range check.
- `src/steeproute/cli/query.py` — import/stack the option, thread into `SolverParams`, validate call, FR12 lever wording.
- `src/steeproute/regression.py` — `FLAG_ON_FIXTURES` `grenoble_small_descent` entry.

**Modified (tests):**
- `tests/integration/exhaustive_oracle.py` — descent filter in `_dfs` + `enumerate_best`; docstrings.
- `tests/integration/test_metamorphic.py` — 9th invariant + `_descent_graph`/`_descent_params` helpers + docstring.
- `tests/unit/test_climbs.py` — 5 windowed-descent-metric tests + `_SEG_LEN_M`.
- `tests/unit/test_graph_contraction.py` — metric carry-through test.
- `tests/unit/test_validator.py` — 4 `max_descent_slope` tests + `_params`/`_graph` helper params.
- `tests/unit/test_degradation_message.py` — 2 descent-message tests; pre-existing basedpyright error/warning fixed.

**New (tests + golden):**
- `tests/integration/test_grasp_descent_cap.py` — GRASP↔oracle one-feasible-set + FR29 under the cap.
- `tests/e2e/test_descent_cap.py` — flag-on no-over-cap-descent property + golden-match.
- `tests/e2e/goldens/grenoble_small_descent.json` — committed flag-on golden (5 routes).

**Modified (docs):**
- `_bmad-output/planning-artifacts/prd.md` — Config Schema + FR32 fixed-window note.
- `_bmad-output/planning-artifacts/architecture.md` — §Cat 3c `max_windowed_descent_grad` fixed-window note.

### Change Log

| Date | Author | Description |
|---|---|---|
| 2026-06-26 | Yann (Claude Opus 4.8) | Story 10.2 implemented (FR32, opt-in `--max-descent-slope`): direction-agnostic windowed descent metric at stage 7 (`max_windowed_descent_grad`, fixed 30 m window); single-sourced `solver.descent` predicate gating GRASP construction + exhaustive-oracle enumeration + validator on one shared feasible set (descent = net loss & windowed grade > cap; uphill unconstrained); FR12 degradation names the new lever; new flag-on golden fixture + no-over-cap-descent property; new 9th metamorphic invariant. Default off → default output byte-identical; existing goldens (fast + realistic tiers) match without rebake. 821 passed; slow tier 4/4; ruff/format/basedpyright clean (0/0/0, incl. fixing a pre-existing whole-project basedpyright error in `test_degradation_message.py`); FR29 determinism preserved. Epic 10 closeout: PRD/architecture synced; Story 3.7 gate + 8 prior invariants unchanged. Folding the new golden into the zero-tolerance CI gate + realistic tier deferred to Story 8.5. |
| 2026-06-26 | Yann (Claude Opus 4.8) | Code-review fixes (`/code-review`, high effort). **Behavioral:** the windowed metric is now **descending-only**, not direction-agnostic `abs(Δelev)` — a window that nets a *climb* contributes 0. This fixes the case where a net-descending segment whose steepest window was actually an *ascent* was wrongly blocked for that uphill grade (the metric is still computed per directed edge from its own oriented vertices, so it stays direction-aware via the reciprocal edge; the `descends_over_cap` net gate is unchanged). **Cleanup:** stage 7 now makes one projected-distance pass per edge (`_cumulative_2d_distances`) shared by `length_m` and the metric; validator drops the dead `or 0.0` falsy-zero guard for an `assert` + `float()`; added CLI-boundary unit tests for `--max-descent-slope` (NaN/inf/0/negative reject + positive accept). **Documented** in `solver/descent.py` as intentional limitations: net-uphill segments are uncapped, and sub-window transient pitches are averaged out (the cap targets *sustained* descents). `grenoble_small_descent` golden unchanged (no rebake). All gates green. |
