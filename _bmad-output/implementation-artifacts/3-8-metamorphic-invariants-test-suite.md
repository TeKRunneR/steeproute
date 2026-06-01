# Story 3.8: Metamorphic invariants test suite

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want `tests/integration/test_metamorphic.py` covering all 8 metamorphic invariants from PRD Appendix A(b),
so that logical bugs unit tests miss — inverted Jaccard, broken seed threading, wrong objective direction, ID-order leakage — are caught automatically by asserting expected monotonicity/equality across transformed inputs (Architecture §Cat 11a/11c).

## Acceptance Criteria

1. `tests/integration/test_metamorphic.py` implements all 8 named invariant tests, each driving the production `GraspSolver` on a **small** programmatic fixture built via `make_toy_contracted_graph` (the Story 3.7 factory in `conftest.py`) — sized **shallower/easier than the 3.7 quality gate** (e.g. fewer layers / lower density) so GRASP reliably reaches the true optimum, which is what makes the monotonicity/equality invariants hold deterministically rather than flakily:
   - `test_relax_theta_objective_non_decreasing`
   - `test_relax_j_max_objective_non_decreasing`
   - `test_relax_difficulty_cap_objective_non_decreasing`
   - `test_increase_iter_budget_objective_non_decreasing`
   - `test_scale_elevation_objective_scales_proportionally`
   - `test_adding_edge_objective_non_decreasing`
   - `test_graph_isomorphism_objective_identical`
   - `test_duplicate_seed_identical_result`

2. **Monotonicity invariants** (`relax_theta`, `relax_j_max`, `relax_difficulty_cap`, `adding_edge`) run GRASP twice — once at the base config, once with the single named relaxation/addition — and assert `new_obj >= old_obj` with an informative message naming both objectives and the changed quantity (e.g. `f"Relaxing theta {old}->{new}: objective dropped {old_obj}->{new_obj}"`). Each is **non-vacuous**: the relaxation must actually widen the admissible search (e.g. base `theta`/`difficulty_cap` filters out edges the relaxed run admits; the added edge is feasible and reachable) — otherwise the test passes trivially and tests nothing. Make the base restrictive enough that the relaxation moves real edges, and keep `iter_budget` high enough on the small fixture that GRASP finds the optimum on both runs.

3. `test_increase_iter_budget_objective_non_decreasing` asserts `best(2N) >= best(N)` for the same generator seed and solver seed. This holds independent of GRASP optimality (the first `N` iterations are identical and the tracker only accumulates), so it needs no near-optimal fixture — but use the same fixture for consistency.

4. `test_scale_elevation_objective_scales_proportionally` scales every edge's elevation attributes (`d_plus_m`, `d_minus_m`, `avg_gradient`) by a constant `k` (and scales `params.theta` by the same `k`) so feasibility and RCL ordering are exactly preserved, then asserts the best objective scales by exactly `k` via `math.isclose(new_obj, k * old_obj, rel_tol=1e-9)`. Document in a comment why `theta` is scaled with `k` (uniform scaling otherwise pulls super-edges across the θ filter and changes the route set).

5. `test_graph_isomorphism_objective_identical` relabels all node IDs through a bijection (rebuilding both the `MultiDiGraph` and the `super_edge_to_base` keys/`Edge` values) and asserts the best objective is **exactly** equal (`==`) to the original — the objective is a label-independent elevation sum, so any inequality signals node-ID order leaking into the result (requires the near-optimal fixture from AC #1). `test_duplicate_seed_identical_result` runs GRASP twice with fresh `numpy.random.default_rng(params.seed)` on the same fixture and asserts identical objective **and** identical edge-traversal sequence per route (FR29 cross-check on the toy fixture; deterministic regardless of optimality).

6. Total wall-clock for `test_metamorphic.py` ≤ **2 minutes** in CI; no `pytest.skip`/`xfail` anywhere (Architecture §Cat 11c — pass-required). Record the measured runtime in Completion Notes.

7. All four CI gates green on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. No new runtime or dev deps (numpy + networkx already present). Coverage floors hold — this is a pure test-tier story; no `src/` changes expected.

## Tasks / Subtasks

- [x] Task 1: Choose and pin the metamorphic fixture shape — a shallow/sparse `make_toy_contracted_graph(...)` config where GRASP reaches the optimum. Add a module-scope seed list and a small helper that builds the base `ContractedGraph` + `SolverParams`, reusing the conftest factory and `make_toy_solver_params`. (AC: #1)
  - [x] Optionally cross-check GRASP-best == `enumerate_best(...)`-best once in setup to confirm the chosen shape is genuinely in GRASP's optimal regime (belt-and-suspenders for the monotonicity/isomorphism invariants).
- [x] Task 2: Implement the 4 monotonicity tests with non-vacuous relaxations + informative messages. (AC: #2)
- [x] Task 3: Implement `increase_iter_budget` (prefix-accumulation), `scale_elevation` (scale elevation attrs + θ by `k`, `math.isclose`), `graph_isomorphism` (relabel bijection, exact `==`), and `duplicate_seed` (objective + edge-sequence identity). (AC: #3, #4, #5)
- [x] Task 4: Confirm the ≤ 2 min budget on a real run and record it; verify no `pytest.skip`/`xfail`. (AC: #6)
- [x] Task 5: Run all four gates on Windows. (AC: #7)

### Review Findings

_Adversarial code review (3 layers: blind hunter, edge case hunter, acceptance auditor) run 2026-06-01. 13 raw findings → 2 patch + 10 dismissed (deduped). No decision-needed, no defer. The edge-case + auditor layers ran live against the real source and refuted most blind-hunter speculation. Sources tagged per finding._

- [x] [Review][Patch] **(LOW-MED)** The `_best_objective` assertion message over-claims a factory guarantee that does not hold at the theta test's base config. The toy-graph factory only guarantees the always-feasible spine for `theta <= 0.25` (`conftest.py` docstring), but `test_relax_theta_objective_non_decreasing` uses base `theta=0.45`, which severs the spine on all 5 seeds (verified: e.g. seed 25 spine gradients `[0.272, 0.27, 0.498, 0.572]`). Feasibility there is incidental — non-super/connector edges + GRASP partial walks, empirically verified — not contractual. Fix: reword the assertion message so it doesn't cite a universal factory guarantee, and add a one-line comment in the theta test noting base `theta=0.45` relies on empirically-verified (non-spine) feasibility. [tests/integration/test_metamorphic.py:90, :184] [source: edge+blind] — **Resolved:** message reworded to "expected >= 1 feasible route for this fixture/params"; clarifying comment added to the theta test.
- [x] [Review][Patch] **(LOW)** `test_increase_iter_budget_objective_non_decreasing` uses budgets `5` and `50`, but AC #3 states the relation as `best(2N) >= best(N)` (a doubling). The invariant holds and is arguably stronger at 10×, but deviates from the spec's literal contract. Fix: change `large = 50` to `large = 10` (N=5, 2N=10) to match AC #3; still passes (strict on seeds 24/25, equal elsewhere). [tests/integration/test_metamorphic.py:250] [source: auditor] — **Resolved:** budgets changed to `5, 10`.

#### Dismissed (recorded for traceability)

- `_with_scaled_elevation` leaves `super_edge_to_base` unscaled → objective desync (blind) — refuted by live verification: the solver reads objective/metrics off the **nx-graph** edge data; `super_edge_to_base` is used only for super-edge *membership* (`grasp.py:_build_rcl`), whose `(u,v,key)` keys are scale-invariant. Leaving it unscaled is correct; all 5 seeds pass at `rel_tol=1e-9`.
- Objective may not be linear in elevation → exact `k`-scaling fragile (blind) — refuted: objective is exactly `sum(d_plus_m + d_minus_m)` (`grasp.py:168`), homogeneous degree-1 in elevation; scaling θ by `k` holds feasibility + RCL order. Verified exact on all seeds.
- `avg_gradient` double-derived, desyncs under scaling (blind) — refuted: scaling `avg_gradient *= k` is consistent with `(d+ + d-)/length` (length unchanged), and it's membership-only for super-edges anyway.
- `test_increase_iter_budget` has no strict check → can pass vacuously (blind) — by design: AC #3 frames this as a `>=` monotonicity check (prefix-accumulation) that explicitly "needs no near-optimal fixture"; non-vacuity is not required for it. The 5→10 patch (above) retains a strict increase on seeds 24/25.
- `relax_j_max` sum-based non-vacuity "rises for the wrong reason" (blind) — refuted: "the held set grew" is precisely the correct signal that relaxing distinctness had an effect; the top-1 `>=` invariant is asserted separately. Distinctness correctness is pinned by Story 3.4.
- Isomorphism exact `==` could false-positive on order-sensitive float sums (blind) — refuted: GRASP sorts nodes (`tuple(sorted(...))`) and the RCL ends in a total sort, and `offset=1000` preserves relative order, so the float ops are identical. Exact `==` verified on all 5 seeds.
- `_with_added_edge` strict rise may be false (blind) — refuted: node 8 exists (last spine node), the edge's `avg_gradient=4.02` clears θ and `sac="hiking"` clears any cap, and strict rise was verified on all 5 seeds.
- Solver RNG seed (42) is independent of the parametrized graph seed (blind) — by design: `SolverParams.seed` is the FR29 solver-RNG source, independent of the graph-generator seed (same posture as Story 3.7); the 5 seeds vary topology, which is the intended coverage.
- `zip(strict=True)` redundant after the length assert (blind) — cosmetic; no fix needed.
- Tuned seeds/constants will rot on a future fixture change (blind) — inherent to the seed-tuned fixture approach the story accepts; the code comments already flag the retune path, and a stale relaxation fails loud (red build), not silently.

## Dev Notes

- **GRASP is a heuristic — the monotonicity/equality invariants are properties of the *optimum*, not of an arbitrary heuristic run.** Relaxing θ (or the difficulty cap, or adding an edge) changes the RCL contents and therefore the seeded RNG-driven walk, so GRASP's best is **not** guaranteed monotone with a fixed seed unless GRASP actually reaches the optimum. The fix is the fixture: keep it small/shallow so GRASP is reliably optimal. Story 3.7 found a shallow 6-layer DAG at `density=0.18` made all GRASP ratios `1.000` (trivially perfect) — that is the regime you want here, the *opposite* of 3.7's gate, which deliberately deepened the graph to make GRASP suboptimal. Reuse `make_toy_contracted_graph` with smaller `num_layers`/`layer_width` and lower `density`.

- **Three invariants are robust regardless of optimality** — lean on this if a fixture tweak is fiddly: `increase_iter_budget` (same seed → first N iterations identical, tracker only accumulates → `best(2N) >= best(N)` always); `duplicate_seed` (byte-identical by FR29); `relax_j_max` (the top-1 objective is `j_max`-independent — the construction sequence doesn't depend on the tracker, and the highest-objective constructed route is always admitted first with nothing higher to overlap-reject it, so `best[0].objective` is unchanged → `>=` holds as equality). The other five (`relax_theta`, `relax_difficulty_cap`, `adding_edge`, `scale_elevation`, `graph_isomorphism`) need the near-optimal fixture.

- **`scale_elevation` is the one subtle transform.** The objective is `sum(d_plus_m + d_minus_m)` over route edges, so it's linear in elevation — but the θ filter reads `avg_gradient` (`= (d+ + d-)/length`) off the **nx graph edge data** (`grasp.py:_build_rcl`). Scaling elevation up by `k` raises `avg_gradient`, pulling previously-sub-θ super-edges *into* the feasible set and changing the route. Scale `params.theta` by the same `k` to hold the filter outcome invariant (`avg_gradient·k >= theta·k` iff `avg_gradient >= theta`); positive scaling preserves the RCL `(d+ + d-)`-descending order, so the identical route is built and the objective scales by exactly `k`. Only the **nx-graph** attributes drive the objective/feasibility; `super_edge_to_base` is consumed by the validator (Story 3.9), not the solver — scaling it too is optional tidiness, not required for these tests.

- **Transforms must build a *new* `ContractedGraph`, not mutate the shared fixture.** `ContractedGraph`/`Edge`/`Solution` are `frozen=True, slots=True` (Story 3.1). For `adding_edge`/`scale_elevation`/`isomorphism`, `.copy()` the `MultiDiGraph`, apply the change, and construct a fresh `ContractedGraph`. For `isomorphism`, `nx.relabel_nodes(g, mapping, copy=True)` handles the graph; remap `super_edge_to_base` keys `(u,v,key)` and rebuild each `Edge` with relabeled `node_u`/`node_v`.

- **Apples-to-apples / no re-ranking.** Read `.objective` off index 0 of `GraspSolver(...).run()` (the tracker-admitted top route). Do not add a second filter or re-rank — same discipline as Story 3.7's gate.

- **Determinism.** Every GRASP run takes a *fresh* `numpy.random.default_rng(params.seed)` (sharing a Generator across runs bleeds state — see `test_grasp_reproducible.py`). The two sides of each invariant must differ only in the one transformed quantity.

- **What this story does NOT do:** the runtime validator (Story 3.9); pinned real-data regression goldens (Story 5.1/5.2). No `src/steeproute/` changes — pure test-tier.

### Project Structure Notes

- **New:** `tests/integration/test_metamorphic.py` — the 8 invariant tests (Architecture §Cat 11a(b), file already named in the §Cat 11 source-tree at line 841).
- **Reused (not modified):** `tests/integration/conftest.py` — `make_toy_contracted_graph` / `make_toy_solver_params` / `toy_graph_factory` factory from Story 3.7. Call it with smaller knobs; do not change the factory unless a metamorphic-specific helper is genuinely needed (if so, keep it additive).
- **Import shape:** `from steeproute.solver.grasp import GraspSolver`; `from steeproute.models import ContractedGraph, Edge, Solution, SolverParams`. If you cross-check against the oracle, `from exhaustive_oracle import enumerate_best` (pytest `prepend` mode puts the test dir on `sys.path` — see `test_solver_on_toy_graph.py` header).
- Mirror the networkx-boundary pyright pragma header from `test_solver_on_toy_graph.py` / `conftest.py` (`reportUnknownVariableType=false`, etc.).

### Testing standards summary

- Integration tests in `tests/integration/test_metamorphic.py`; naming `test_<unit>_<scenario>` per Architecture §"Test organization".
- Float comparisons via `math.isclose(..., rel_tol=1e-9)` for the `scale_elevation` proportionality; the monotonicity assertions are `>=` inequalities; `isomorphism`/`duplicate_seed` are exact `==` (FR29 promises byte-identical — `math.isclose` would mask exactly the drift the test guards, same rationale as `test_grasp_reproducible.py`).
- No `pytest.skip`/`xfail` (Architecture §Cat 11c pass-required). No new deps; `hypothesis` is not needed — the value here is deterministic seeded comparison.
- Test-tier code; the 80% project-wide / 95% solver-core coverage floors are unaffected (no `src/` changes).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.8"](../planning-artifacts/epics.md) — the 8 named invariants, ≤ 2 min budget, no-skip, informative-message requirement
- [Source: _bmad-output/planning-artifacts/prd.md §"Appendix A (b)"](../planning-artifacts/prd.md) — the scope-disciplined 8-invariant list (source-of-truth; resist expansion)
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 11 (11a/11c)](../planning-artifacts/architecture.md) — metamorphic suite in v1; `test_metamorphic.py` source-tree slot (line 841); invariants pass-required, no-skip
- [Source: tests/integration/conftest.py](../../tests/integration/conftest.py) — `make_toy_contracted_graph(seed, num_layers, layer_width, density, ...)` + `make_toy_solver_params(...)` + measured oracle-tractability ceiling
- [Source: tests/integration/test_solver_on_toy_graph.py](../../tests/integration/test_solver_on_toy_graph.py) — the Story 3.7 pattern to mirror (fixture call, `.objective` off index 0, no re-rank, pragma header, oracle import shape)
- [Source: tests/integration/test_grasp_reproducible.py](../../tests/integration/test_grasp_reproducible.py) — FR29 duplicate-seed pattern (fresh `default_rng` per run, exact `==` on objective + edge sequence)
- [Source: src/steeproute/solver/grasp.py:171-224](../../src/steeproute/solver/grasp.py) — `_build_rcl` filters (θ on super-edges via `avg_gradient`, SAC cap) + `(d+ + d-)` objective; the surfaces each transform must move
- [Source: src/steeproute/solver/distinctness.py:60-161](../../src/steeproute/solver/distinctness.py) — `TopNTracker(n, j_max)` admission (why top-1 objective is `j_max`-independent)
- [Source: src/steeproute/pipeline/osm.py:20-27,191-241](../../src/steeproute/pipeline/osm.py) — `SAC_SCALE_RANK` (hiking=1 … alpine_hiking=4) + `parse_difficulty_cap`; informs a non-vacuous `relax_difficulty_cap`
- [Source: src/steeproute/models.py:124-180](../../src/steeproute/models.py) — `SolverParams`, `ContractedGraph`, `Edge`, `Solution` shapes (all `frozen=True, slots=True`)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps (numpy + networkx already present).

**Fixture-design exploration (Task 1):** Before writing the suite, swept candidate shapes with a throwaway script that ran `GraspSolver` against `enumerate_best` on each. `num_layers=5, layer_width=2, density=0.4` (10 nodes) gave GRASP-best == oracle-best (ratio exactly 1.000) on every candidate seed — the optimal regime the monotonicity/isomorphism invariants require. Also confirmed each relaxation is non-vacuous on the chosen seeds `(20, 21, 24, 25, 26)`: `relax_theta` (0.45→0.25) and `adding_edge` strictly increase on all five; `relax_difficulty_cap` (T1→T4) strictly increases on all five; `relax_j_max` leaves top-1 unchanged (as predicted) while the held-set total strictly increases; `scale_elevation` (k=2.5, θ scaled too) reproduces `k·base` under `math.isclose`; isomorphism reproduces the objective bit-for-bit. The script was deleted after design — the committed test re-derives all of this deterministically from seeds.

**Final pass (all green):**

```
uv run ruff check tests/integration/test_metamorphic.py  → All checks passed!
uv run ruff format --check                               → already formatted
uv run basedpyright tests/integration/test_metamorphic.py → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                                      → 590 passed, 1 deselected in 167 s; 97% overall
                                                          (was 550 before this story; +40 = 8 invariants × 5 seeds)
```

**Runtime (AC #6):** `test_metamorphic.py` alone runs in **~13 s** (40 cases; slowest single case ~0.56 s) — far under the ≤ 2 min ceiling. No `pytest.skip`/`xfail` anywhere.

### Completion Notes List

**Design decisions worth review attention:**

1. **Optimal-regime fixture is the whole game.** GRASP is a heuristic, so the monotonicity/equality invariants are properties of the *optimum*, not of an arbitrary run — relaxing a filter or relabelling nodes changes the seeded walk. I deliberately use a small, sparse 10-node graph (the opposite of Story 3.7's intentionally-suboptimal gate) where GRASP reaches the exhaustive optimum on every seed, so the relations hold deterministically. Verified GRASP == `enumerate_best` during design; the committed suite doesn't call the oracle (keeps it fast and dependency-light) but the seeds are pinned from that verification.

2. **Non-vacuity is asserted, not assumed.** For `relax_theta`, `relax_difficulty_cap`, and `adding_edge` the test asserts both the invariant (`new >= old`, informative message) *and* a strict `new > old` guard so a future fixture change that silently makes the relaxation a no-op fails loudly. `relax_j_max` is the exception: the top-1 objective is `j_max`-independent (the global-best route is always admitted first), so it asserts top-1 `>=` (holds as equality) plus a strict increase on the held-set **total** objective to prove the relaxation actually enriched the top-N set.

3. **`scale_elevation` scales θ with k.** The objective is linear in elevation, but the θ filter reads `avg_gradient` off the nx-graph edge data; scaling elevation alone would pull super-edges across the filter and change the route. Scaling `theta` by the same `k` holds feasibility and RCL ordering invariant, so the identical route is chosen and the objective scales by exactly `k` (asserted with `math.isclose`).

4. **No `src/` changes.** Pure test-tier story — one new file, conftest factory reused unmodified. Coverage floors unaffected (`grasp.py` / `distinctness.py` / `models.py` remain at 100%).

**AC walkthrough — evidence per criterion:**

1. AC #1 — all 8 named tests in `test_metamorphic.py`, each driving `GraspSolver` on `make_toy_contracted_graph(seed, num_layers=5, layer_width=2, density=0.4)`; shallower/sparser than the 3.7 gate, GRASP-optimal on every seed. ✅
2. AC #2 — the 4 monotonicity tests assert `new_obj >= old_obj` with informative messages + a non-vacuity guard (strict increase, or strict held-set-total increase for `relax_j_max`). ✅
3. AC #3 — `increase_iter_budget` asserts `best(50) >= best(5)` (prefix-accumulation). ✅
4. AC #4 — `scale_elevation` scales elevation attrs + θ by `k=2.5`, asserts `math.isclose(new, k*old, rel_tol=1e-9)`. ✅
5. AC #5 — `graph_isomorphism` relabels node ids (graph + `super_edge_to_base`), asserts exact `==`; `duplicate_seed` asserts identical objective + edge sequence per route. ✅
6. AC #6 — full file ~13 s ≤ 2 min; no `pytest.skip`/`xfail`. ✅
7. AC #7 — ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 590 passed at 97% ✅. No new deps. ✅

### File List

**New:**
- `tests/integration/test_metamorphic.py` — the 8 metamorphic invariants × 5 generator seeds (40 cases), with graph-transform helpers (`_with_added_edge`, `_with_scaled_elevation`, `_relabelled`) building fresh `ContractedGraph`s.

**Modified:**
- `_bmad-output/implementation-artifacts/3-8-metamorphic-invariants-test-suite.md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked to `review`; `last_updated: 2026-06-01`.

**Untouched (intentionally):** all `src/steeproute/` and `tests/integration/conftest.py` — pure test-tier story; the Story 3.7 toy-graph factory is reused unmodified.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-01 | Yann (Claude Opus 4.8) | Code review (3 adversarial layers: blind hunter, edge case hunter, acceptance auditor) — 13 raw findings → 2 patch + 10 dismissed. **Both patches applied.** **(LOW-MED)** corrected the `_best_objective` assert message (it cited a factory feasibility guarantee that doesn't hold at the theta test's base θ=0.45, where the spine is severed and feasibility comes from non-spine edges) + added a clarifying comment to `test_relax_theta`. **(LOW)** changed `test_increase_iter_budget` budgets `5,50 → 5,10` to match AC #3's literal `best(2N) >= best(N)`. Dismissed 10 (most were blind-hunter speculation the edge-case + auditor layers refuted via live runs: `super_edge_to_base` scaling desync [membership-only], objective-linearity [exact `D+ + D-` sum], isomorphism float-`==` [order preserved], added-edge feasibility [verified], solver-seed independence [by design]). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 40/40 on `test_metamorphic.py`. Status → done. | _pending_ |
| 2026-06-01 | Yann (Claude Opus 4.8) | Story 3.8 implemented: metamorphic invariants test suite (PRD Appendix A(b), Architecture §Cat 11a/11c). **`tests/integration/test_metamorphic.py`** (new) — all 8 invariants (`relax_theta`, `relax_j_max`, `relax_difficulty_cap`, `increase_iter_budget`, `scale_elevation`, `adding_edge`, `graph_isomorphism`, `duplicate_seed`), each parameterized over 5 generator seeds `(20, 21, 24, 25, 26)`. Reuses the Story 3.7 `make_toy_contracted_graph` factory at a deliberately small/sparse shape (5 layers × 2 nodes, density 0.4 → 10 nodes) where GRASP reaches the exhaustive optimum (verified vs `enumerate_best` during design), so the monotonicity/equality relations hold deterministically. Monotonicity tests carry non-vacuity guards; `scale_elevation` scales θ with `k` to hold feasibility invariant; isomorphism/duplicate-seed use exact `==`. Suite ~13 s (≤ 2 min budget), no `pytest.skip`/`xfail`. No `src/` changes. All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 590 passed (was 550; +40) at 97%. | _pending_ |
