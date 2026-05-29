# Story 3.4: TopNTracker with Jaccard distinctness

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want `solver/distinctness.py::TopNTracker` admitting solutions, rejecting duplicates per the Jaccard ceiling, and tracking total objective for stagnation — alongside the pure `jaccard_distance` function it uses,
So that FR11 (top-N distinctness) and FR12 (graceful degradation) have a clean, testable home orthogonal to the GRASP loop (Story 3.6) and the stagnation termination (Epic 4).

## Acceptance Criteria

1. `src/steeproute/solver/distinctness.py` defines `TopNTracker(n: int, j_max: float)` with three public methods — `consider(solution: Solution) -> bool` (returns True iff the candidate was admitted, i.e. the tracker's state changed), `current_top() -> list[Solution]` (objective-descending), `total_objective() -> float` (sum of `Solution.objective` across the current top-N; `0.0` when empty) — plus a free-function `jaccard_distance(a: Solution, b: Solution) -> float`. `jaccard_distance` is pure, treats each edge as its canonical identity tuple `(node_u, node_v, key)` per Architecture §"Numerical and data discipline", and returns `1 - |E(a) ∩ E(b)| / |E(a) ∪ E(b)|` (so identical edge-sets → 0.0 and disjoint edge-sets → 1.0). The tracker reads `j_max` as the **similarity ceiling** (FR7) — a candidate overlaps an existing solution iff `jaccard_distance(...) < 1 - j_max`.

2. `tests/unit/test_distinctness.py` covers the four PRD structural cases via separate, single-purpose tests on hand-built `Solution` instances (no fixture I/O):
   - **admission into empty tracker** — first `consider(sol)` returns True; `current_top() == [sol]`; `total_objective() == sol.objective`;
   - **rejection-by-worse** — tracker filled to capacity with high-objective distinct solutions; a new distinct candidate with strictly worse objective than every member returns False and leaves `current_top()` byte-identical;
   - **rejection-by-Jaccard** — tracker holds one solution `A`; `consider(B)` with `jaccard_distance(A, B) < 1 - j_max` and `B.objective < A.objective` returns False; `current_top() == [A]`;
   - **substitution** — tracker holds `[A]` at capacity `n=1`; `consider(B)` with `B.objective > A.objective` and `jaccard_distance(A, B) >= 1 - j_max` (distinct) returns True; `current_top() == [B]`. A second variant at `n=2` with the worst-member-replaced semantic: `[A, B]` (A best) + new `C` distinct from both with `C.objective > B.objective` → `current_top() == [A, C]`.

3. A `hypothesis` property suite in the same file pins four invariants on randomly-generated `Solution` pairs (edge-sets drawn over a bounded `(node_u, node_v, key)` integer alphabet, objectives in `[0, 1e4]`):
   - **symmetry** — `jaccard_distance(a, b) == jaccard_distance(b, a)`;
   - **identity** — `jaccard_distance(a, a) == 0.0`;
   - **range** — `0.0 <= jaccard_distance(a, b) <= 1.0`;
   - **admission order-independence for sufficiently-distinct inputs** — given a list of solutions whose pairwise `jaccard_distance` all exceed `1 - j_max + ε` (so distinctness can never reject), feeding them through two trackers in two different orders yields `set(t1.current_top()) == set(t2.current_top())` (equality on the `Solution` value-set, not list ordering — `current_top` lists are objective-sorted, but the resulting top-N membership must be a function of input only).

4. The tracker holds no reference to mutable external state: a property test (or a single explicit case) constructs the same input sequence in a fresh tracker twice and asserts the two trackers' `current_top()` lists are equal — proving `consider` does not mutate caller-side state (e.g. the `Solution` values) and that the tracker's accept/reject decision is a pure function of `(n, j_max, history)`. Document in the module docstring: the tracker stores `Solution` references directly (the type is `frozen=True, slots=True` so this is safe — see Story 3.1's contract).

5. All four CI gates green on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0 errors / 0 warnings / 0 notes), `uv run pytest --cov`. `solver/distinctness.py` reaches the 95% pure-logic coverage floor (Architecture §Cat 11e). No new runtime or dev deps (hypothesis is already in the dev set — used by `tests/unit/test_graph_contraction.py` and `tests/unit/test_climbs.py`).

## Tasks / Subtasks

- [x] Task 1: Implement `jaccard_distance` and `TopNTracker` in `src/steeproute/solver/distinctness.py`. Keep both in one module — the tracker is the only consumer of the free function and they share the canonical-edge-identity helper. (AC: #1, #4)
- [x] Task 2: Write the four structural-case unit tests on hand-built `Solution` instances. (AC: #2)
- [x] Task 3: Add the four `hypothesis` property tests using a small `Solution` strategy factory. (AC: #3)
- [x] Task 4: Verify CI gates and coverage; address any drift. (AC: #5)

### Review Findings

- [x] [Review][Decision → Patch applied] Non-finite objective (NaN/inf) silently poisons the tracker — VERIFIED: a NaN-objective `Solution` is admitted unconditionally (the under-capacity branch does no comparison), then can never be evicted because no value satisfies `> nan`; `total_objective()` returns `nan` permanently, which silently breaks the Epic 4 stagnation watcher (`nan != nan` so stagnation mis-fires/never fires). `inf` dominates similarly and poisons `total_objective()`. `Solution.objective` is an unconstrained `float`; there is no finiteness guard, although `__init__` already guards `n` and `j_max`. **Options:** (a) add `if not math.isfinite(solution.objective): raise ValueError(...)` at the top of `consider()` — consistent with the existing constructor guards, ~free cost, converts silent poisoning into a loud failure (RECOMMENDED); (b) document that producers (Story 3.6 solver) must supply finite objectives and add no runtime guard. [src/steeproute/solver/distinctness.py:107-122] — **RESOLVED (option a):** `consider()` now raises `ValueError` on non-finite objective; tests `test_consider_rejects_nan_objective` / `test_consider_rejects_infinite_objective` added.
- [x] [Review][Decision → Patch applied] All-overlap eviction diverges from the Dev Notes' single-incumbent substitution rule — the Dev Notes ("TopNTracker substitution policy") state that on multi-overlap "the highest-objective overlapping incumbent governs the comparison (and is the one replaced if the candidate wins)" — i.e. compare against / replace a single incumbent. The implementation instead requires the candidate to beat **every** overlapping incumbent (`all(solution.objective > s.objective ...)`) and evicts all of them. This was a deliberate, documented choice (Completion Note #2): single-incumbent replacement would leave the candidate still overlapping the survivors, breaking the pairwise-distinct invariant. The all-overlap behavior was verified invariant-preserving and is arguably *more* correct than the spec — but code ≠ spec, and the multi-overlap path is not directly tested. **Options:** (a) ratify — amend the Dev Note to describe all-overlap eviction and add a multi-overlap unit test (RECOMMENDED); (b) revert the code to the spec's single-incumbent rule. [src/steeproute/solver/distinctness.py:124-131] — **RESOLVED (option a):** Dev Note "TopNTracker substitution policy" rewritten to describe all-overlap eviction; tests `test_consider_evicts_all_overlapping_incumbents_when_new_beats_each` / `test_consider_rejects_when_candidate_beats_some_but_not_all_overlaps` added.
- [x] [Review][Patch] Class docstring describes the sort key as `(-objective, canonical_edge_set)` (a `frozenset`, which is not order-comparable and would `TypeError` if used as a sort key); the code correctly uses a sorted edge-id tuple via `_sort_key`. Fix the docstring wording to match. [src/steeproute/solver/distinctness.py:82]
- [x] [Review][Patch] Over-claimed order-independence / "byte-for-byte" reproducibility in a test comment — the comment asserts `total_objective()` agrees "byte-for-byte … regardless of input order", but float addition is non-associative and (on overlapping inputs) the held set itself can differ by order. FR29 reproducibility actually holds because the GRASP solver feeds a deterministic, seed-derived sequence — not because admission is order-independent. Reword the comment to state the real guarantee (`math.isclose` over a fixed sequence) and stop claiming general order-independence. [tests/unit/test_distinctness.py:508-510]
- [x] [Review][Patch] Order-independence property test only feeds pairwise-disjoint inputs (`_make_disjoint_solutions` → distance 1.0 for every pair), so the overlap branch is never exercised — the test would pass even if the overlap/eviction logic were broken. Strengthen it with distinct-but-not-disjoint inputs (share some edges, pairwise distance still above the threshold) so the overlap computation actually runs and returns empty. [tests/unit/test_distinctness.py:478-510]
- [x] [Review][Patch] `test_jaccard_distance_of_solution_with_itself_is_zero` can pass vacuously — `_solution_strategy(min_edges=0)` draws empty solutions, which hit the `not union` short-circuit (`0.0`) rather than the non-empty identity path (`1 - |E∩E|/|E∪E|`) the docstring claims to verify. Add `assume(a.edges)` (or a separate explicit non-empty case) so the intended path is exercised. [tests/unit/test_distinctness.py:404-410]
- [x] [Review][Patch] `j_max = 1.0` silently disables the distinctness filter (`overlap_threshold = 0.0`, so `jaccard_distance < 0.0` is never true) and admits byte-identical duplicate routes; it is inside the validated `[0.0, 1.0]` range but undocumented as degenerate. Add a one-line docstring note. [src/steeproute/solver/distinctness.py:98-105]
- [x] [Review][Defer] Duplicate edges within a single `Solution` collapse in the canonical `frozenset`, so Jaccard cannot distinguish an out-and-back route (an edge traversed twice) from a single traversal, while a per-edge objective (D+ + D-) would count the repeat — a latent inconsistency. Whether the solver emits such routes is a Story 3.6 concern. [src/steeproute/solver/distinctness.py:29-36] — deferred, cross-story (depends on Story 3.6 solver output semantics)

## Dev Notes

- **Distance vs. similarity, and the `j_max` convention.** The architecture and PRD speak of `J_max` as the pairwise **overlap (similarity) ceiling** — e.g. `j_max = 0.30` means "two routes may share at most 30% of their edges by Jaccard similarity." The function defined in this story is `jaccard_distance` (per the epic spec) — i.e. `1 - similarity`. So the tracker's rejection predicate translates to: a candidate overlaps an existing solution iff `jaccard_distance(candidate, existing) < 1 - j_max`. Capture this once at module-docstring level so the dev agent in Stories 3.6 / 3.9 (and the renderer reading `PairwiseViolation.jaccard_observed` in 3.10) read the same convention. **Numeric guard:** if both edge-sets are empty, define `jaccard_distance` as `0.0` (identical empty sets) — avoid the `0/0` trap. This is a pure-logic edge case; an empty `Solution` is illegal at the validator stage (Story 3.9), but defining the math at the primitive layer keeps the function total.

- **Edge identity is `(node_u, node_v, key)`.** `Solution.edges` is a `tuple[Edge, ...]` in route-traversal order (Story 3.1's contract). Jaccard is a *set* metric — collapse each edge to its identity triple before building the comparison sets. Don't compare on `Edge` value equality directly: two `Edge` instances for the same `(u, v, k)` could in principle differ on `length_m` due to ULP drift between producers; the canonical identity tuple is the only stable join key (Architecture §"Numerical and data discipline" — the same rule that governs cache-key hashing).

- **`TopNTracker` substitution policy.** Two related cases share the "substitution" AC bucket: (a) a new candidate overlaps one or more existing members (`jaccard_distance < 1 - j_max`) and beats **every** overlapping incumbent on objective — evict all of them and admit the candidate; (b) the tracker is at capacity with all members pairwise-distinct, the new candidate overlaps none of them, and its objective beats the worst member — replace the worst. Both produce a `True` return and a state change. **All-overlap eviction (not single-incumbent):** when a candidate overlaps multiple members, it is admitted only if it strictly beats *all* of them, and *all* are evicted. Replacing only the highest-objective overlapping incumbent (an earlier draft of this note) is wrong — it would leave the candidate still overlapping the survivors, breaking the pairwise-distinct invariant. The all-overlap rule may shrink the held set below `n` (FR12 graceful degradation, not a bug). The check order is: compute the full overlap set first; if empty, take the no-overlap fill/replace-worst branch; otherwise apply the all-overlap rule.

- **Objective ordering and ties.** `current_top()` returns the held solutions sorted by `objective` descending. On exact ties (rare with float objectives, but possible if the solver scores zero-edge routes — pathological), break ties on the canonical edge-set hash so the order is deterministic regardless of insertion order. The PRD's reproducibility commitment (FR29) is byte-identical edge-sets; carrying a deterministic ordering up through the validator and renderer keeps the HTML/JSON sidecar bytes stable too.

- **`total_objective()` is the stagnation hook.** Architecture §Cat 5e defines stagnation as "Top-N total objective unchanged for `--stagnation-iters` consecutive iterations" (sum of objectives across all N held solutions). This story exposes the read; the Epic 4 stagnation watcher polls it between iterations. Empty tracker → `0.0` (not `None`) so the watcher can do `prev == curr` without branching.

- **No CLI / no orchestration.** This story doesn't touch `cli/`, doesn't read `SolverParams`, doesn't wire into `GraspSolver`. The tracker takes `n` and `j_max` as raw constructor args; Story 3.6 (`GraspSolver`) constructs it from `params.n` and `params.j_max`. Keeping the tracker dependency-free makes it trivially unit-testable and keeps the boundary between solver-internal types (`Solution`, `TopNTracker`) and the wider package narrow per Architecture §"Boundaries".

- **`hypothesis` strategies — keep the alphabet bounded.** Property tests generate `Solution` instances by drawing edge tuples over a small integer alphabet (e.g. `node_u, node_v in [0, 10]`, `key in [0, 2]`) so collisions / overlaps actually happen in the generated population. Without a bounded alphabet most random pairs are trivially disjoint and the "Jaccard in [0, 1]" / symmetry properties pass vacuously. Use `hypothesis.strategies.lists` with `unique_by=lambda e: (e.node_u, e.node_v, e.key)` so each `Solution.edges` tuple is a valid set candidate.

- **Test naming follows the `test_<unit>_<scenario>` pattern.** Architecture §"Test organization" cites `test_jaccard_exceeds_threshold_is_rejected` as the canonical sentence-shape; pick names in that style (e.g. `test_consider_admits_into_empty_tracker`, `test_consider_rejects_overlap_when_new_is_worse`, `test_consider_substitutes_worst_when_full_and_better`).

- **What this story does NOT do:**
  - Implement `GraspSolver` or its construction loop — Story 3.6.
  - Implement the stagnation watcher itself — Epic 4 / Story 4.2 wires `total_objective()` into a termination check.
  - Persist or serialize `Solution` — `output.py` (Story 3.10) consumes `Route` after the validator converts.
  - Add CLI flags — `--j-max` / `--n` already exist on the click decorator (Epic 1 Story 1.5) and reach this code only via `SolverParams` in Story 3.6.
  - Touch `models.py` — `Solution` and `Edge` shapes landed in Story 3.1.

### Project Structure Notes

- **New:** `src/steeproute/solver/distinctness.py` — `jaccard_distance` (free function) + `TopNTracker` class.
- **New tests:** `tests/unit/test_distinctness.py` — four structural-case tests + four hypothesis property tests + the mutability/no-shared-state assertion.
- **Modified:** none — `solver/__init__.py` already exists with a one-line module docstring; don't re-export `TopNTracker` from it (Architecture §"Python code conventions" prefers absolute imports — `from steeproute.solver.distinctness import TopNTracker`).
- **Untouched:** every other source module. Story 3.6 (`solver/grasp.py`) is the first downstream consumer.

### Testing standards summary

- Tests in `tests/unit/` per Architecture §"Test organization"; file name mirrors module (`test_distinctness.py` ↔ `solver/distinctness.py`).
- Test names follow `test_<unit>_<scenario>` (Architecture §"Test organization").
- Float comparisons use `math.isclose(..., abs_tol=1e-9)` — never `==` on floats (Architecture §"Numerical and data discipline"). The `jaccard_distance(a, a) == 0.0` identity is the one exception: it's exact because identical sets give exact `|E∩E| / |E∪E| = 1.0`.
- `hypothesis` is the project's property-testing tool (already in dev deps); reuse the `from hypothesis import given, settings; from hypothesis import strategies as st` import style from `tests/unit/test_climbs.py` and `tests/unit/test_graph_contraction.py`. Use `@settings(max_examples=...)` with the project's existing budget (50 examples is the established pattern in Story 3.3) — don't crank it up; CI runtime is precious.
- Coverage floor for `solver/distinctness.py` is 95% (pure-logic module per Architecture §Cat 11e). With one free function + one ~50-line class this is straightforward.
- No new fixtures required; build `Solution` instances inline per test using a small `_make_solution(edge_tuples, objective)` helper local to the test module.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.4"](../planning-artifacts/epics.md) — AC source-of-truth
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 5 (5d) "TopNTracker"](../planning-artifacts/architecture.md) — class signature (`__init__(n, j_max)`, `consider`, `current_top`, `total_objective`)
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 5 (5e) "Stagnation definition"](../planning-artifacts/architecture.md) — `total_objective()` is the stagnation hook
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 6 (6b)](../planning-artifacts/architecture.md) — `PairwiseViolation` shape; `jaccard_observed` is the similarity (1 - distance)
- [Source: _bmad-output/planning-artifacts/architecture.md §"Numerical and data discipline"](../planning-artifacts/architecture.md) — canonical edge ordering `(node_u, node_v, key)` for set / hash operations
- [Source: _bmad-output/planning-artifacts/architecture.md §"Python code conventions"](../planning-artifacts/architecture.md) — absolute imports, frozen+slots discipline, no module-level mutable state
- [Source: _bmad-output/planning-artifacts/architecture.md §"Test organization"](../planning-artifacts/architecture.md) — `test_<unit>_<scenario>` naming, `tests/unit/` placement
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11 (11e)](../planning-artifacts/architecture.md) — 95% coverage floor for `solver/distinctness.py`
- [Source: _bmad-output/planning-artifacts/prd.md §FR7, FR11, FR12](../planning-artifacts/prd.md) — `--j-max` flag, top-N distinctness, graceful degradation
- [Source: src/steeproute/models.py] — `Solution`, `Edge` shapes (Story 3.1); `Solution.edges: tuple[Edge, ...]`, `Solution.objective: float`
- [Source: src/steeproute/solver/__init__.py] — pre-existing one-line docstring; no re-exports added by this story
- [Source: tests/unit/test_graph_contraction.py:1-26] — established `hypothesis` import + module-pragma pattern to mirror

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. No new runtime or dev deps (hypothesis already in the dev set).

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 57 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest                      → 514 passed, 1 deselected in ~92s
uv run pytest ...distinctness --cov-report=term-missing
                                   → solver/distinctness.py 100% (50/50); 20 tests
```

### Completion Notes List

**Design decisions worth review attention:**

1. **`j_max` is the similarity ceiling; `jaccard_distance` returns `1 - similarity`.** The epic spec names the free function `jaccard_distance` (a *distance*), but PRD/Architecture treat `--j-max` as the *overlap/similarity* ceiling (`0.30` = "share at most 30%"). The tracker therefore flags overlap when `jaccard_distance(new, s) < 1 - j_max`. The convention is pinned in the module docstring so Stories 3.6/3.9/3.10 read one source of truth. `jaccard_observed` on `PairwiseViolation` (Story 3.10's renderer) is the *similarity* (`1 - distance`).

2. **Overlap eviction requires beating *every* overlapping incumbent — not just the best one.** A candidate that overlaps two held solutions and beats only one would, if admitted by replacing just the loser, still overlap the survivor — breaking the pairwise-distinct invariant. So the overlap branch admits only when `new.objective` strictly beats all overlapping members, then evicts all of them. This can shrink the held set below `n` (FR12 graceful degradation), which is correct, not a bug.

3. **Deterministic tie-break via `(-objective, sorted_edge_ids)`.** `current_top()` sorts on a key that breaks objective-ties on the canonical edge-id tuple, so equal-objective routes order reproducibly (FR29). `_worst_held()` reuses the same key via `max(...)` — the worst-in-sort-order is exactly `max` by that key — so eviction order mirrors the display order with no second comparator to drift out of sync.

4. **Both-empty Jaccard defined as `0.0`.** An empty `Solution` is illegal at the validator stage, but defining `jaccard_distance` over the `0/0` union keeps the primitive total and the property tests (range, symmetry, identity) clean. Empty-vs-non-empty correctly returns `1.0`.

5. **Constructor guards.** `n < 1` and `j_max ∉ [0, 1]` raise `ValueError` at construction — these are programming errors (the CLI layer validates flag ranges upstream in Story 3.6's wiring), so failing loud is the right default rather than silently clamping.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `solver/distinctness.py` defines `TopNTracker(n, j_max)` with `consider`/`current_top`/`total_objective` + free-function `jaccard_distance`. Distance uses the canonical `(node_u, node_v, key)` identity set; identical → `0.0`, disjoint → `1.0`. Overlap predicate is `jaccard_distance < 1 - j_max`. ✅
2. AC #2 — four structural cases as separate single-purpose tests: `test_consider_admits_into_empty_tracker`, `test_consider_rejects_when_new_is_worse_than_every_member`, `test_consider_rejects_overlap_when_new_is_worse`, `test_consider_substitutes_when_new_is_better_and_distinct_at_capacity` + the n=2 worst-replaced variant `test_consider_substitutes_worst_when_full_and_new_beats_worst_distinct`. ✅
3. AC #3 — four hypothesis property tests (symmetry, self-identity=0, range [0,1], admission order-independence over disjoint inputs), bounded `(node_u, node_v, key)` alphabet so overlaps actually occur. ✅
4. AC #4 — `test_consider_is_pure_under_fresh_tracker_replay` (two fresh trackers, same sequence → identical decisions + state) and `test_consider_does_not_mutate_input_solution`. Module docstring documents the held-reference contract (safe because `Solution` is frozen+slots). ✅
5. AC #5 — all four gates green on Windows; `solver/distinctness.py` at 100% (above the 95% floor); full suite 514 passed, no regressions; no new deps. ✅

**Code review resolutions (2026-05-29):** Two `decision-needed` + five `patch` findings applied (see Review Findings). Key changes: (1) `consider()` now raises `ValueError` on a non-finite objective — closes the NaN-poisoning hole that would silently break the Epic 4 stagnation watcher; (2) the all-overlap eviction policy was ratified (it is invariant-preserving and safer than the Dev Note's original single-incumbent wording, which was corrected) and is now pinned by a multi-overlap test plus a "beats some but not all" rejection test; (3) the order-independence property test now also runs over shared-but-distinct inputs so the overlap branch is genuinely exercised, and its over-claimed "byte-for-byte regardless of order" comment was corrected (FR29 rests on the solver's deterministic sequence, not on order-independence); (4) the jaccard self-identity property test forces non-empty draws; (5) docstrings corrected (sort-key wording; `j_max` boundary behavior). Full suite now 518 passed; `distinctness.py` still 100%.

### File List

**New:**
- `src/steeproute/solver/distinctness.py` — `jaccard_distance` free function + `TopNTracker` class with the documented all-overlap-eviction admission policy, deterministic `(-objective, edge-id)` ordering, constructor guards, and a `consider()` finiteness guard on `objective` (added in review).
- `tests/unit/test_distinctness.py` — 24 tests: 4 AC #2 structural cases + n=2 substitution variant + single/multi-overlap eviction + "beats some but not all" rejection + ordering/empty-total assertions + 2 constructor guards + 2 non-finite-objective guards + 4 AC #3 hypothesis property tests (order-independence now covers shared-but-distinct inputs) + jaccard edge cases (empty/disjoint/canonical-identity) + 2 AC #4 purity tests.

**Modified:**
- _(none — `models.py` / `solver/__init__.py` unchanged; `TopNTracker` is imported via the absolute path, not re-exported)_

**Updated (out-of-source):**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story `3-4-topntracker-with-jaccard-distinctness` walked `backlog → ready-for-dev → in-progress → review`. `last_updated: 2026-05-29`.

**Untouched (intentionally):**
- `src/steeproute/models.py` — `Solution`, `Edge` shapes landed in Story 3.1; consumed as-is.
- `src/steeproute/solver/grasp.py` (Story 3.6) is the first downstream consumer — not created here.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-29 | Yann (Claude Opus 4.8) | Story 3.4 implemented: `TopNTracker` + `jaccard_distance` (FR11/FR12) for Epic 3. **`src/steeproute/solver/distinctness.py`** (new) — pure `jaccard_distance(a, b)` over canonical `(node_u, node_v, key)` edge-identity sets (`0.0` identical, `1.0` disjoint, `0.0` both-empty); `TopNTracker(n, j_max)` with `consider`/`current_top`/`total_objective`. `j_max` is the similarity ceiling (overlap iff `jaccard_distance < 1 - j_max`). Admission policy: no-overlap → fill-or-beat-worst; overlap → admit only if it beats *every* overlapping incumbent (preserves pairwise distinctness; may shrink below `n` per FR12). Deterministic `(-objective, sorted_edge_ids)` ordering for FR29. Constructor guards on `n`/`j_max`. **`tests/unit/test_distinctness.py`** (new) — 20 tests covering all four ACs. All four CI gates green: ruff ✅, ruff format ✅, basedpyright 0/0/0 ✅, pytest 514 passed at 97% overall with `distinctness.py` at 100% (50/50). No new runtime or dev deps. | _pending_ |
| 2026-05-29 | Yann (Claude Opus 4.8) | Code review (3 adversarial layers) findings applied — 2 decision-needed + 5 patch resolved, 1 deferred, 4 dismissed. **`distinctness.py`**: added `consider()` finiteness guard (raise `ValueError` on NaN/inf objective — closes a verified silent-poisoning hole that would break the Epic 4 stagnation watcher); docstring corrected (sort-key is a sorted edge-id tuple not a `frozenset`; documented greedy order-dependence vs FR29; documented `j_max=0.0/1.0` degenerate behavior). **Story Dev Note** "substitution policy" rewritten to describe the ratified all-overlap eviction (the original single-incumbent wording was unsafe). **`test_distinctness.py`**: +4 tests (multi-overlap evict-all, beats-some-not-all reject, NaN guard, inf guard); order-independence property now also exercises shared-but-distinct inputs (was disjoint-only, never hit the overlap branch); jaccard self-identity property forces non-empty draws; over-claimed "byte-for-byte regardless of order" comment corrected. Deferred: intra-solution duplicate-edge / out-and-back Jaccard semantics (owned by Story 3.6). All gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 518 passed; `distinctness.py` 100%. | _pending_ |
