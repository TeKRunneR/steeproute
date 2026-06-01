# Story 3.7: GRASP-vs-exhaustive CI quality gate

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want a CI test that runs `GraspSolver` against the Story 3.5 exhaustive oracle on seeded programmatic toy `ContractedGraph` fixtures and fails when the quality ratio drops below a named threshold,
so that silent solver-quality regressions are caught automatically (Architecture §Cat 11c), the programmatic toy-graph factory that Stories 3.8 (metamorphic) and later solver-unit work also need exists, and the NaN / missing-key / ordering items deferred from Stories 3.5–3.6 to "the consumer that owns the attribute contract" get a decision.

## Acceptance Criteria

1. `tests/integration/conftest.py` gains a **programmatic toy-`ContractedGraph` factory** (parameterized by a generator seed; ~20–30 nodes, configurable edge density and terrain variance, cliff-free) producing graphs whose edge-attribute contract is **complete and finite** — all five stage-7 numerics (`length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`) plus `sac_scale` present on every edge, no NaN/inf — and whose `super_edge_to_base` is populated so the θ-on-super-edge filter is exercised on **both** GRASP and the oracle. Edge density is tuned so the exhaustive oracle terminates within budget: the oracle is exponential in **edge** count (intended for ≤ ~15 edges, see [exhaustive_oracle.py](../../tests/integration/exhaustive_oracle.py) docstring), so a ~20–30-node graph must stay sparse — node count is not the constraint, traversable edge count is.

2. `tests/integration/test_solver_on_toy_graph.py::test_grasp_meets_quality_threshold` runs `GraspSolver(graph, params, numpy.random.default_rng(seed)).run()` and `enumerate_best(graph, params, params.n)` on the **same** factory-produced graph and `SolverParams`, then asserts `grasp_best.objective / exhaustive_best.objective >= QUALITY_THRESHOLD` where `QUALITY_THRESHOLD = 0.80` is a module-scope constant whose comment reads "initial target — tighten to 0.85–0.90 once baseline established". `grasp_best` / `exhaustive_best` are the top-objective solutions (index 0 of each `list[Solution]`); the test asserts both lists are non-empty first (the factory guarantees ≥ 1 feasible route), so a vacuous empty-vs-empty pass is impossible.

3. The comparison is **apples-to-apples**: both sides admit candidates through `TopNTracker(params.n, params.j_max)` (already true by construction — GRASP via `grasp.py`, oracle via `exhaustive_oracle.py`). The test does not re-rank or post-filter either result; it reads `.objective` off the tracker-admitted top route on each side.

4. The test parameterizes across **3–5 distinct generator seeds** (catches generator-bias — a single lucky/unlucky topology won't represent the ratio), each running the full GRASP-vs-oracle comparison.

5. Total wall-clock for `test_solver_on_toy_graph.py` ≤ **60 s** in CI; the toy-graph size/density is the constraining knob. Record the measured runtime in Completion Notes.

6. No `pytest.skip` / `xfail` anywhere in the new test or factory (Architecture §Cat 11c — this gate is pass-required). Disabling would require an issue reference + commit-message rationale; none is used here.

7. **Resolve the 8 deferred items routed to Story 3.7** — 3.5 deferred #1–#5 and 3.6 deferred #1–#3 in [deferred-work.md](deferred-work.md). **Default posture: resolved-by-construction** — the factory owns a finite/all-keys-present contract, so the NaN / missing-key family (3.5 #1, 3.6 #1–#3) cannot trigger on this consumer; close them without adding `src/` guards, per the project's "trust internal code, validate only at boundaries" convention. The factory naturally emits cycles/closed walks → 3.5 #2 (self-loop/cycle walk semantics) is exercised directly. 3.5 #5 (traversal order) is **not observed** by the ratio (it reads `.objective`, a scalar). Only re-defer an item (with a named future owner) if it genuinely isn't closed by this consumer; do **not** add defensive `src/` guards under this story. Update `deferred-work.md` resolution columns.

8. All four CI gates green on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. No new runtime or dev deps (numpy + networkx already present). Coverage floors hold (the new code is test-tier, not `src/` solver-core).

## Tasks / Subtasks

- [x] Task 1: Add the programmatic toy-`ContractedGraph` factory to `tests/integration/conftest.py` — seed-parameterized, ~20–30 nodes, tunable density + terrain variance, finite/complete attribute contract, populated `super_edge_to_base`, guaranteed ≥ 1 feasible route. Reuse the `_add_edge`-style attribute-writing pattern from [test_oracle_correctness.py](../../tests/integration/test_oracle_correctness.py) so `avg_gradient = (d_plus_m + d_minus_m) / length_m` matches the stage-7 definition. (AC: #1)
  - [x] Tune density empirically so the oracle finishes within the per-seed share of the 60 s budget; document the chosen node/edge/density knobs in the factory docstring.
- [x] Task 2: Write `tests/integration/test_solver_on_toy_graph.py::test_grasp_meets_quality_threshold` with the `QUALITY_THRESHOLD = 0.80` module constant + tighten-later comment; parameterize across 3–5 generator seeds; assert non-empty results then the ratio. (AC: #2, #3, #4)
- [x] Task 3: Confirm the ≤ 60 s budget on a real run and capture the measured time; verify no `pytest.skip`/`xfail`. (AC: #5, #6)
- [x] Task 4: Work through the 8 routed deferred items (3.5 #1–#5, 3.6 #1–#3); decide resolution per AC #7 and update `deferred-work.md`. (AC: #7)
- [x] Task 5: Run all four gates on Windows. (AC: #8)

### Review Findings

_Adversarial code review (3 layers: blind hunter, edge case hunter, acceptance auditor) run 2026-05-29. 17 raw findings → 6 patch + 6 dismissed (deduped). No decision-needed, no defer. Sources tagged per finding._

- [x] [Review][Patch] **(HIGH)** The "cycles present on every seed" guarantee is false for seeds 11 & 23 — both produce acyclic graphs (verified: `nx.is_directed_acyclic_graph` is `True` for seeds 11/23; only 37/53/71 have cycles). Back-edges connect `rng.choice`-picked arbitrary columns, so a cycle only forms when the chosen `dst` node happens to have a forward path to the chosen `src` node. This also makes the deferred 3.5 #2 resolution ("closed walks traversed on all 5 seeds") overstated. Fix: route each back-edge between the column-0 spine nodes (`layers[src_layer][0] → layers[dst_layer][0]`) as a feasible super-edge (`sac="hiking"`), so the spine forward-chain + back-edge guarantees a traversable directed cycle on every seed; re-verify ratios afterward. [tests/integration/conftest.py:~218 make_toy_contracted_graph step 3] [source: blind+auditor]
- [x] [Review][Patch] **(MED)** `make_toy_contracted_graph(num_layers=1)` crashes with an opaque `ValueError: high <= 0` from `rng.integers(0, src_layer)` (src_layer forced to 0), and `num_layers=1` would silently produce no spine. The factory advertises `num_layers`/`layer_width` as free knobs for Story 3.8 reuse with no lower bound. Fix: add loud boundary guards at the top — `num_layers >= 2`, `layer_width >= 1` — matching the project's fail-loud-at-boundaries convention (`TopNTracker n>=1`, `GraspSolver iter_budget>=1`). [tests/integration/conftest.py:~113 make_toy_contracted_graph] [source: blind+edge]
- [x] [Review][Patch] **(MED)** Oracle tractability is a Story-3.8 foot-gun: the committed `density=0.45` is fast (~0.04s) but sits near a cliff — `layer_width=4, density=0.6` measured at ~17s (single seed), well past the ≤60s budget. The docstring says "raise with care" but gives no quantitative ceiling. Fix: add concrete measured guidance to the factory docstring (e.g. density 0.45 → ~0.01s; 0.6 → ~1s; width 4 + density 0.6 → ~17s) so a future metamorphic-suite author doesn't blow CI. [tests/integration/conftest.py:~25 module docstring + factory docstring] [source: edge]
- [x] [Review][Patch] **(LOW-MED)** The test asserts only the lower bound (`ratio >= 0.80`); it never checks the oracle is a true upper bound. A regression making GRASP emit an infeasible over-objective route, or making the oracle non-exhaustive, would slip through. Fix: add `assert ratio <= 1.0 + 1e-9` (GRASP can only match or fall short of the brute-force optimum) with an informative message. [tests/integration/test_solver_on_toy_graph.py:~92] [source: blind]
- [x] [Review][Patch] **(LOW)** Spine-feasibility docstring reasoning cites "gradient >= 0.25" clearing "theta <= 0.25", but the real filter tests `avg_gradient` (`= gradient + d_minus_m/length_m >= gradient`). Correctness holds, but the invariant is stated against the wrong field — a future edit lowering the spine gradient floor toward θ would hide breakage from the docstring's reasoning. Fix: pin the guarantee to `avg_gradient` in the docstring. [tests/integration/conftest.py:~96 factory docstring] [source: edge]
- [x] [Review][Patch] **(LOW)** Several `deferred-work.md` resolutions (3.5 #1, 3.6 #1–#3) cite "the toy-graph factory + real `contract_climbs` integration in 3.7's `test_solver_on_toy_graph.py`" as the two closing consumers — but this story's test uses only the synthetic factory; no `contract_climbs` call exists here. The resolved-by-construction argument stands on the factory's contract alone (`contract_climbs`'s finite-contract is tested independently in `test_grasp_on_fixture.py`). Fix: correct the prose so it doesn't imply a `contract_climbs` wiring that isn't in this story. [_bmad-output/implementation-artifacts/deferred-work.md] [source: auditor]

#### Dismissed (recorded for traceability)

- `avg_gradient = (d+ + d−)/length` "looks off" (blind) — this is the established stage-7 absolute-churn definition used codebase-wide (`test_oracle_correctness._add_edge`); correct.
- Super-edge maps to a single-element self-referential base tuple (blind) — valid: `super_edge_to_base` is used only for super-edge **membership** testing in this story, not expansion; the model permits a 1-tuple.
- Test is vacuous / GRASP could just return the spine (blind+edge) — refuted: the committed min ratio is 0.877 (seed 53) and stays 0.877 under higher `iter_budget`, proving genuine GRASP suboptimality, not a trivial match. The HIGH/LOW-MED patches above further harden it; over-specifying GRASP's exploration is undesirable.
- No determinism assertion despite reproducibility claim (blind) — GRASP per-seed reproducibility is already pinned by Story 3.6's `test_grasp_reproducible.py`; re-asserting here is redundant.
- `GraspSolver(..., default_rng(params.seed))` uses the fixed solver seed (42) rather than the graph seed (auditor) — by design: `SolverParams.seed` is the FR29 solver-RNG source, independent of the graph generator seed; deterministic and correct.
- Super-edge gradient (≤0.55) vs θ feasibility unverified (blind) — verified holds: `avg_gradient >= gradient >= 0.25 > theta=0.20` on every spine edge.

## Dev Notes

- **The single biggest risk is the oracle's exponential blowup, not the node count.** Architecture §Cat 11b says "~20–30 nodes" but the oracle (`enumerate_best` → `_dfs`) brute-forces every edge-simple walk and is exponential in **edge** count (its own docstring caps the tractable range at ~15 edges). A 25-node graph with even moderate fan-out has 40–60+ edges and the oracle will never return. Keep the graph **sparse** — near-tree with a few extra cycle-closing edges — so the traversable edge count stays in the oracle's tractable band while still reading as a "~20–30 node" fixture. Tune density down until both sides finish; the 60 s ceiling is the forcing function.

- **Apples-to-apples is the whole point of the gate.** Both GRASP and the oracle already feed `TopNTracker(n, j_max)` — do **not** add a second filter or re-rank on either side, or the ratio stops measuring solver quality. Compare the top-1 objective from each tracker-admitted list. (Story 3.6 Dev Notes §"Same admission policy as the oracle" and Story 3.5's oracle docstring both pin this invariant on purpose.)

- **The factory owns the attribute contract — that is what resolves most deferred items.** 3.5 #1 and 3.6 #1–#3 (KeyError on missing keys; NaN `avg_gradient` bypassing the θ filter; NaN metrics destroying the RCL sort and thus FR29) all hinge on "the consumer that builds the graph owns the contract." This story's factory is that consumer. Producing finite, all-keys-present edge data closes the family *for this consumer* by construction. Whether to also add a defensive `.get(...)` / NaN-guard at the `src/` boundary is a judgment call — the prevailing convention elsewhere in this codebase is "trust internal code, validate only at system boundaries" (see the many "resolved-by-construction" / "re-deferred to Future" notes in `deferred-work.md`), which argues for resolved-by-construction here, not a guard. Make the call explicitly and record it; don't silently leave them pending.

- **Cycles are a feature here.** The Story 3.5 fixtures are all DAGs, so the oracle's closed-walk / node-revisit branch (3.5 #2) is untested. A realistic toy factory will emit cycle-closing edges, exercising that branch naturally — note it when resolving 3.5 #2.

- **FR29 ordering (3.5 #5) is *not* observed by this gate.** The quality ratio reads `.objective` (a scalar sum), not `Solution.edges` traversal order, so the oracle's internal node-insertion-order dependence doesn't affect the assertion. GRASP's own reproducibility is already pinned by Story 3.6's `test_grasp_reproducible.py`. Resolve 3.5 #5 as "not observed by the 3.7 ratio" unless you choose to read edge order off the oracle (you shouldn't need to).

- **What this story does NOT do:** the 8 metamorphic invariants (Story 3.8 — though it reuses this factory); the runtime validator (Story 3.9); the pinned real-data regression goldens (Story 5.1/5.2). No `src/steeproute/` changes are expected unless you elect to add a defensive guard under AC #7 — if so, keep it minimal and pinned by a unit test.

### Project Structure Notes

- **New:** `tests/integration/test_solver_on_toy_graph.py` — the quality-gate test (Architecture §Cat 11c, file already named in the §Cat 11 source-tree at line 840).
- **Modified:** `tests/integration/conftest.py` — add the toy-graph factory fixture alongside the existing `truststore` injection (don't remove it).
- **Modified:** `deferred-work.md` — resolution columns for the 8 routed items.
- **Possibly modified (only under AC #7 option b):** `src/steeproute/solver/grasp.py` and/or `tests/integration/exhaustive_oracle.py` if a defensive boundary guard is chosen; add a pinning unit test if so.
- **Import shape:** test files import the oracle as `from exhaustive_oracle import enumerate_best` (pytest `prepend` mode puts the test dir on `sys.path` — see the header comment in `test_oracle_correctness.py`). `GraspSolver` imports as `from steeproute.solver.grasp import GraspSolver`. The factory belongs in `conftest.py` so both this story and Story 3.8 consume it as a fixture.

### Testing standards summary

- Integration tests in `tests/integration/test_solver_on_toy_graph.py`; naming `test_<unit>_<scenario>` per Architecture §"Test organization".
- Float comparisons via `math.isclose(..., abs_tol=1e-9)` — never `==` (Architecture §"Numerical and data discipline"). The ratio assertion is a `>=` inequality, which is fine; only use `math.isclose` if you assert an exact-equality anywhere.
- No `pytest.skip` / `xfail` — Architecture §Cat 11c lists this gate as pass-required.
- No new runtime or dev deps; numpy + networkx + hypothesis are already in. `hypothesis` is not needed here — the value is the deterministic seeded comparison.
- The new code is test-tier; the 80% project-wide and 95% solver-core floors are unaffected unless you touch `src/`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.7"](../planning-artifacts/epics.md) — AC source-of-truth (QUALITY_THRESHOLD 0.80, 3–5 seeds, ≤ 60 s, no-skip)
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 11 (11a/11b/11c)](../planning-artifacts/architecture.md) — toy-graph generator in conftest; GRASP/exhaustive ratio ≥ 0.80 CI gate; no-skip/no-xfail
- [Source: _bmad-output/planning-artifacts/architecture.md §"Stagnation definition"](../planning-artifacts/architecture.md) — objective is `D+ + D−` summed over the route's edges (same on both sides)
- [Source: tests/integration/exhaustive_oracle.py](../../tests/integration/exhaustive_oracle.py) — `enumerate_best(graph, params, n)`; exponential-in-edges complexity note; TopNTracker admission
- [Source: src/steeproute/solver/grasp.py](../../src/steeproute/solver/grasp.py) — `GraspSolver(graph, params, rng).run()`; same TopNTracker admission + filters as the oracle
- [Source: tests/integration/test_oracle_correctness.py:63-101](../../tests/integration/test_oracle_correctness.py) — `_add_edge` helper + the `avg_gradient = (d_plus_m + d_minus_m) / length_m` stage-7 contract to mirror in the factory
- [Source: tests/integration/conftest.py](../../tests/integration/conftest.py) — existing `truststore` injection to preserve when adding the factory
- [Source: src/steeproute/models.py:60-181](../../src/steeproute/models.py) — `Edge`, `ContractedGraph` (`graph` + `super_edge_to_base`), `SolverParams`, `Solution` shapes
- [Source: src/steeproute/solver/distinctness.py](../../src/steeproute/solver/distinctness.py) — `TopNTracker(n, j_max)` admission policy shared by both sides
- [Source: _bmad-output/implementation-artifacts/deferred-work.md "Deferred from … 3-5 / 3-6"](deferred-work.md) — the 8 items routed to this story (3.5 #1–#5, 3.6 #1–#3)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps (numpy + networkx already present).

**Final pass (all green):**

```
uv run ruff check        → All checks passed!
uv run ruff format --check → 2 files already formatted
uv run basedpyright       → 0 errors, 0 warnings, 0 notes
uv run pytest --cov       → 550 passed, 1 deselected in ~90 s; 97% overall coverage
                            (was 545 before this story; +5 new tests)
```

**Quality-gate timing (AC #5):** `test_solver_on_toy_graph.py` runs in **~4 s** total wall-clock (5 seeds × `iter_budget=20000`) — far under the 60 s ceiling. The exhaustive oracle accounts for ~0.04 s of that across all seeds; GRASP construction dominates the rest. _(Post-review: the initial `iter_budget=3000` gave ~1.1 s but only a 0.81 floor once the cycle-guarantee fix landed — see Change Log; raised to 20000 for margin.)_

**Empirical density tuning (AC #1 / Task 1):** swept `density` and `num_layers`/`layer_width` to balance oracle tractability against gate sensitivity. A shallow 6-layer DAG (density 0.18) made GRASP trivially perfect (all ratios 1.000) — a weak gate. The committed config (8 layers × 3 nodes, density 0.45, 2 spine-to-spine back-edges → 24 nodes / ~34–39 edges) makes the optimal routes long enough — and, with the guaranteed feasible cycle, longer still — that GRASP's best lands at ratio **0.929–1.000** across the committed seeds at `iter_budget=20000`. The 0.929 floor (seed 53) is stable under even higher budgets, confirming it reflects a genuine GRASP heuristic limit rather than under-budgeting — so the gate has real regression teeth while clearing the 0.80 threshold with comfortable margin.

### Completion Notes List

**Design decisions worth review attention:**

1. **The binding constraint is edge count / route length, not node count.** The oracle is exponential in traversable edges; a layered DAG bounds route length to `num_layers - 1`, which keeps the oracle fast *and* sets the difficulty GRASP faces. I deepened the graph (8 layers) rather than widening it: long optimal routes are what genuinely challenge GRASP's greedy-randomized construction, so the ratio becomes a meaningful quality signal instead of a near-constant 1.0.

2. **Committed ratio floor is 0.929, threshold is 0.80.** Comfortable, deterministic margin (GRASP is seeded → zero run-to-run variance, so no flakiness). The `QUALITY_THRESHOLD` comment flags the 0.85–0.90 tightening target; a future tighten to 0.90 would still pass given the 0.929 baseline.

3. **All 8 routed deferred items resolved in `deferred-work.md`** per the agreed resolved-by-construction posture (5 resolved-by-construction / not-observed, 1 resolved-via-cycles, 1 mooted-timing, 1 re-deferred-low-value). The toy factory is the "consumer that owns the attribute contract" the NaN/missing-key family (3.5 #1, 3.6 #1–#3) was waiting on: it emits only finite, all-keys-present edge data, so those crashes cannot trigger via either real consumer (factory or `contract_climbs`). No defensive `src/` guards were added — consistent with the project's "validate only at boundaries" convention and the explicit decision recorded on the story. 3.5 #4 (list-valued `sac_scale`) re-deferred as redundant with existing unit coverage.

4. **No `src/` changes.** This is a pure test-tier story (factory + gate test + deferred-work bookkeeping). Coverage floors are unaffected; `grasp.py` and `distinctness.py` remain at 100%.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `make_toy_contracted_graph(seed, ...)` in `conftest.py`: seed-deterministic, 24 nodes, tunable density/terrain, all five numerics + `sac_scale` finite on every edge, `super_edge_to_base` populated, guaranteed-feasible spine. ✅
2. AC #2 — `test_grasp_meets_quality_threshold` runs `GraspSolver(...).run()` + `enumerate_best(...)` on the same graph+params, asserts both non-empty, then `grasp_best.objective / exhaustive_best.objective >= QUALITY_THRESHOLD` (`= 0.80`, module constant, tighten-later comment). ✅
3. AC #3 — both sides admit through `TopNTracker(n, j_max)` by construction; the test reads `.objective` off index 0 of each, no re-rank/post-filter. ✅
4. AC #4 — parameterized across 5 generator seeds `(11, 23, 37, 53, 71)`. ✅
5. AC #5 — full file ~4 s ≤ 60 s; measured runtime recorded above. ✅
6. AC #6 — no `pytest.skip`/`xfail` in the factory or test. ✅
7. AC #7 — 8 routed deferred items resolved in `deferred-work.md` (see design note 3). ✅
8. AC #8 — ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 550 passed at 97% ✅. No new deps. ✅

### File List

**New:**
- `tests/integration/test_solver_on_toy_graph.py` — `test_grasp_meets_quality_threshold` (5 seeds), `QUALITY_THRESHOLD = 0.80`, `_assert_edge_simple_walk` structural check.

**Modified:**
- `tests/integration/conftest.py` — added `make_toy_contracted_graph` factory + `make_toy_solver_params` helper + `toy_graph_factory` / `toy_solver_params` fixtures (preserving the existing `truststore` injection); added the networkx-boundary pyright pragma header.
- `_bmad-output/implementation-artifacts/deferred-work.md` — resolution columns filled for the 8 items routed from Stories 3.5 (#1–#5) and 3.6 (#1–#3).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story `3-7-grasp-vs-exhaustive-ci-quality-gate` walked `ready-for-dev → in-progress → review`; `last_updated: 2026-05-29`.

**Untouched (intentionally):** all `src/steeproute/` — pure test-tier story; no defensive guards added (deferred items resolved-by-construction).

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-29 | Yann (Claude Opus 4.8) | Story 3.7 implemented: GRASP-vs-exhaustive CI quality gate (Architecture §Cat 11c). **`tests/integration/conftest.py`** — added `make_toy_contracted_graph(seed, ...)`, a seed-deterministic programmatic toy-`ContractedGraph` factory (8 layers × 3 nodes = 24, density 0.45, 2 cycle-introducing back-edges; finite all-keys edge contract; populated `super_edge_to_base`; guaranteed-feasible super-edge spine) plus `make_toy_solver_params` and the `toy_graph_factory` / `toy_solver_params` fixtures. **`tests/integration/test_solver_on_toy_graph.py`** (new) — `test_grasp_meets_quality_threshold` parameterized over 5 generator seeds: runs `GraspSolver.run()` + `enumerate_best()` on the same graph+params (both admit via `TopNTracker`), asserts non-empty results then `grasp/exhaustive objective ratio >= QUALITY_THRESHOLD = 0.80`. Committed ratio floor 0.877 (deterministic); ~1.1 s total wall-clock (oracle ~0.04 s) vs the 60 s ceiling; no `pytest.skip`/`xfail`. **`deferred-work.md`** — resolved the 8 items routed from Stories 3.5/3.6: the NaN/missing-key family (3.5 #1, 3.6 #1–#3) resolved-by-construction (factory owns a finite all-keys contract; no `src/` guards per the validate-at-boundaries convention); 3.5 #2 (cycle/closed-walk semantics) resolved via the factory's back-edges + `_assert_edge_simple_walk`; 3.5 #3 (vacuous-vs-flaky timing) mooted (ratio comparison replaces timing proxy, no new wall-clock assertion); 3.5 #5 (oracle edge order) resolved as not-observed; 3.5 #4 (list `sac_scale`) re-deferred as redundant. No `src/` changes. All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 550 passed (was 545; +5) at 97%. | _pending_ |
| 2026-05-29 | Yann (Claude Opus 4.8) | Code review (3 adversarial layers: blind hunter, edge case hunter, acceptance auditor) — 17 raw findings → 6 patch + 6 dismissed. **All 6 patches applied.** **HIGH:** the "cycles present on every seed" guarantee was false — seeds 11 & 23 produced acyclic graphs (back-edges joined arbitrary columns, so a cycle only formed when a forward path happened to exist). Fixed by routing each back-edge between the column-0 *spine* nodes as a feasible super-edge (`sac="hiking"`), guaranteeing a traversable directed cycle on every seed; verified all 5 seeds now cyclic. This made the optimum longer, dropping the ratio floor to 0.81 at the old `iter_budget=3000`, so the budget was raised to **20000** (min ratio now **0.929**, full gate ~4 s). **MED:** added loud `num_layers >= 2` / `layer_width >= 1` boundary guards (was an opaque `ValueError: high <= 0` for `num_layers=1`); added a measured oracle-tractability ceiling to the factory docstring to protect Story 3.8 reuse (`density 0.6 → ~1 s`, `layer_width 4 + density 0.6 → ~17 s`). **LOW-MED:** added an upper-bound assertion `ratio <= 1.0 + 1e-9` (GRASP must never beat the brute-force optimum — catches a non-exhaustive oracle / infeasible GRASP route the lower bound can't). **LOW:** corrected the spine-feasibility docstring to reason about `avg_gradient` (the filtered field) not the raw construction gradient; corrected `deferred-work.md` prose so it no longer implies a `contract_climbs` wiring inside this story's test (its contract is verified independently in `test_grasp_on_fixture.py`). Dismissed 6 (avg_gradient definition, single-element super-edge tuple, vacuous-test concern [refuted by the 0.929 floor], determinism [covered by 3.6], solver-seed source [by-design], gradient-vs-θ feasibility [verified]). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 550 passed at 97%. | _pending_ |
