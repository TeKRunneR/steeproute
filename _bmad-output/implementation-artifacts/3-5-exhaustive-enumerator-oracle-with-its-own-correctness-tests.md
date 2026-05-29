# Story 3.5: Exhaustive enumerator oracle with its own correctness tests

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want a brute-force path enumerator (`tests/integration/exhaustive_oracle.py::enumerate_best`) plus correctness tests on 2–3 handcrafted 5–8 node `ContractedGraph` instances with known-by-inspection optima,
So that Story 3.7's GRASP-vs-exhaustive CI gate (Architecture §Cat 11c) is comparing GRASP against a trusted oracle — addressing the PRD Appendix A "validating against an unvalidated oracle" concern — and the toy-graph factory used by 3.7/3.8 has a reference implementation to bootstrap from.

## Acceptance Criteria

1. `tests/integration/exhaustive_oracle.py` exposes `enumerate_best(graph: ContractedGraph, params: SolverParams, n: int) -> list[Solution]` that returns the top-N feasible paths in `graph.graph` (objective-descending), ranked by the Story 3.6 objective (sum of `d_plus_m + d_minus_m` over the route's edges, with super-edges' aggregated metrics consumed directly — no expansion to base edges required for scoring) and filtered for pairwise distinctness via `solver.distinctness.TopNTracker(n, params.j_max)` applied post-hoc to the complete enumeration. "Feasible" means: a simple walk in `graph.graph` (no `(node_u, node_v, key)` edge repeated), satisfying the slope floor on non-connector edges (super-edges identified via `graph.super_edge_to_base` membership) and the per-edge SAC difficulty cap; graph membership and edge-reuse are guaranteed by construction (the enumerator only emits walks built from `graph.graph` edges, each used at most once). The function is pure and lives under `tests/` — never imported by `src/steeproute/`.

2. `tests/integration/test_oracle_correctness.py` covers 2–3 handcrafted `ContractedGraph` instances, each constructed inline in the test (or via a local `_make_graph(...)` helper) with 5–8 nodes, and **each preceded by an ASCII-art / comment block documenting the graph topology, the per-edge metrics, and the expected optimum route + objective the author derived by inspection**. The fixtures collectively exercise: (a) one graph where a single dominant climb-rich path is the obvious winner; (b) one graph where two structurally-different high-objective paths exist and the oracle must return both as the top-2 under a permissive `j_max`; (c) at least one fixture sized 7–8 nodes to verify behavior past the trivial 5-node case. The test asserts the returned `Solution.edges` canonical edge-set and `Solution.objective` (`math.isclose(..., abs_tol=1e-9)` per Architecture §"Numerical and data discipline") match the documented expectations.

3. Pathological inputs are tested explicitly: an empty graph (no edges) returns `[]`; a graph where every candidate path violates θ or the SAC cap returns `[]`; a graph whose feasible-path count is strictly less than `n` returns `< n` solutions without raising. Each is its own single-purpose test.

4. A wall-clock bound is asserted: enumeration on a 5-node hand-graph completes in under 1 second (Architecture §Cat 11c — the oracle is for *toy* graphs; if a small instance takes longer than this, the algorithm has regressed into the exponential blowup the size cap is meant to avoid). Use `time.perf_counter` with a 1.0 s threshold; mark the test `@pytest.mark.timeout(...)` only if `pytest-timeout` is already in the dev set — otherwise an explicit `assert elapsed < 1.0` is sufficient.

5. All four CI gates green on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. Oracle correctness tests count toward the "oracle correctness pass-required" CI gate in Architecture §Cat 11c — no `pytest.skip` / `xfail`. No new runtime deps; if any dev dep is required (none expected — networkx + hypothesis are already in), justify it in the Completion Notes.

## Tasks / Subtasks

- [x] Task 1: Implement `tests/integration/exhaustive_oracle.py` with `enumerate_best(graph, params, n)`. Use any straightforward DFS / backtracking over `graph.graph.edges(keys=True)`; correctness comes first, performance only matters insofar as AC #4 holds. Apply the slope-floor filter on each super-edge as you walk (early-prune infeasible prefixes); apply the SAC cap per-edge. Collect all feasible walks, score, sort, feed through `TopNTracker(n, params.j_max)` to get the final top-N. (AC: #1)
- [x] Task 2: Build the 2–3 handcrafted `ContractedGraph` fixtures inline in `test_oracle_correctness.py`. Each fixture is a comment-block-documented topology — a reviewer should be able to compute the optimum from the comment alone. Add the `_make_graph(...)` / `_make_edge(...)` helper locally if it shortens fixture construction without obscuring the topology. (AC: #2)
- [x] Task 3: Add the three pathological-case tests (empty graph; all-infeasible; fewer feasible paths than `n`) and the timing assertion on the smallest fixture. (AC: #3, #4)
- [x] Task 4: Verify all CI gates and the oracle-correctness "pass-required" status. Coverage is not measured under `tests/` (only `src/` modules count toward the 80%/95% floors), so no coverage target applies to the oracle module itself; the *behavior* coverage lives in the correctness tests. (AC: #5)

### Review Findings

- [x] [Review][Patch] No fixture exercises parallel multi-edges (same `(u,v)` different `key`) [tests/integration/test_oracle_correctness.py] — every fixture uses `key=0`; the `super_edge_to_base` mapping, the `used_ids` dedup, and the canonical-edge-set are all keyed on `(u, v, key)`, but the multi-key branch is uncovered. Production `contract_climbs` deliberately allocates non-colliding keys when a connector and a super-edge share endpoints ([pipeline/graph.py:141-159](../../src/steeproute/pipeline/graph.py)), so this is the most-likely-buggy path that the suite would miss. Add one fixture with two parallel edges between the same node pair (one super, one connector OR two supers) and assert the oracle enumerates both as distinct candidates. [source: blind+edge]
- [x] [Review][Patch] No assertion that returned `Solution.edges` is a valid edge-simple walk [tests/integration/test_oracle_correctness.py] — every assertion compares `_edge_set(result[0].edges)` (set membership) and `result[0].objective`, but never verifies the returned tuple is a connected walk in the right order. A `_dfs` bug that emits the right edge-set with the wrong ordering or duplicates would pass every test. Add a small helper `_assert_valid_walk(sol)` checking (a) consecutive edges share endpoints (`prev.node_v == next.node_u`) and (b) no `(u,v,k)` triple repeats, then call it on each non-empty result. [source: blind]
- [x] [Review][Patch] Boundary case `avg_gradient == theta` unpinned by any test [tests/integration/exhaustive_oracle.py:151] — the strict `<` means an edge with `avg_gradient` exactly equal to θ passes; a future flip to `<=` (or a production-side flip from `>=` to `>`) would silently change which super-edges qualify. Add one explicit test (e.g. `avg_gradient = θ = 0.20`) asserting the boundary edge is admitted under the current contract. [source: edge]
- [x] [Review][Patch] Empty-graph test passes vacuously w.r.t. the `_dfs` body [tests/integration/test_oracle_correctness.py:457-464] — `list(nx_graph.nodes)` is `[]` so the outer loop never runs; the entire `_dfs` could be `pass` and this test would still go green. Strengthen it (or add a second variant) with a graph that has 2-3 isolated nodes but no edges — the outer loop runs but every DFS terminates immediately, exercising the empty-`path_edges` early-return branch genuinely. [source: edge]
- [x] [Review][Patch] `test_enumerate_best_drops_super_edges_below_theta` could pass for the wrong reason [tests/integration/test_oracle_correctness.py:500-514] — comment claims "all-infeasible case driven by the slope-floor rather than the SAC cap" but `_add_edge`'s default `sac_scale="hiking"` means a regression that accidentally fails the SAC filter on `"hiking"` (rank 1, well below cap T3) would also produce an empty result. Either (a) explicitly set `sac_scale=None` on the test's edge and document in the comment that this isolates the θ branch from the SAC branch, or (b) add a counter-test where the same edge with `avg_gradient ≥ θ` is admitted — proving the only reason the original test returns `[]` is the θ filter. [source: blind]
- [x] [Review][Defer] Missing-key / non-finite-metric crash on real `ContractedGraph` consumers [tests/integration/exhaustive_oracle.py:148-161] — `_dfs` reads `data["sac_scale"]`, `data["length_m"]`, `data["d_plus_m"]`, `data["d_minus_m"]`, `data["avg_gradient"]` directly. Missing keys raise `KeyError`; NaN `avg_gradient` makes `nan < theta` silently `False` (super-edge admitted incorrectly); NaN/inf in metrics makes the objective non-finite and `TopNTracker.consider` raises `ValueError` (per `solver/distinctness.py:137`). The Story 3.5 fixtures all inject the 5 metric keys explicitly; the real consumers are this story's tests plus Story 3.7's toy-graph factory + Story 3.7's real-`contract_climbs` integration. — deferred, cross-story (the producers — Story 3.7's `tests/integration/conftest.py` factory and real `contract_climbs` from Story 3.3 — own the attribute contract; defensive guards in test infrastructure aren't load-bearing today). [source: blind+edge]
- [x] [Review][Defer] Self-loop / cycle edge-simple-walk semantics untested [tests/integration/exhaustive_oracle.py:138-176] — docstring claims "node-revisits via distinct edges are allowed" and closed walks are admissible; every fixture is a DAG with no revisits. A regression that uses node-identity instead of edge-identity in `used_ids` would pass all current tests. — deferred, scope (the cycle-walk semantic isn't an AC for this story; Story 3.7's toy-graph factory will plausibly emit cycles and naturally exercise the branch). [source: edge]
- [x] [Review][Defer] Timing-assertion trade-off [tests/integration/test_oracle_correctness.py:522-536] — the 1.0 s bound on a 5-node fixture is both spec-mandated (AC #4) and somewhat vacuous (5 edges generate ≤ ~120 candidate walks; even a cubic regression in `_dfs` would finish in well under 1 s) yet potentially flaky on cold CI runners (10× variance is real). The story explicitly required this shape. — deferred, spec-mandated (revisit during Story 3.7's GRASP-vs-oracle CI gate where a denser toy fixture naturally exercises the exponential blowup). [source: blind+edge]
- [x] [Review][Defer] List-valued `sac_scale` (osmnx-merged ways) untested at the oracle boundary [tests/integration/exhaustive_oracle.py:148] — `max_sac_rank` handles `list[str]` per `pipeline/osm.py:231-240`; every fixture passes a plain string or `None`. — deferred, cross-cutting (the list-handling branch lives in `max_sac_rank` and is unit-tested at its own boundary; layering an integration test here is redundant). [source: edge]
- [x] [Review][Defer] `Solution.edges` traversal order depends on dict-insertion ordering of `nx_graph.nodes` [tests/integration/exhaustive_oracle.py:99-111, 138-142] — for an edge-set reachable from multiple start nodes, the kept representative's tuple order is whichever DFS arrives first. Tests assert on `_edge_set(...)` so they're robust to this, but FR29 byte-identical reproducibility downstream may rely on this ordering being stable across Python/networkx versions. — deferred, downstream (FR29 is enforced at the GRASP solver layer in Story 3.6 and the regression-golden hash in Story 5.1; the oracle's internal ordering isn't observed by either). [source: blind+edge]



## Dev Notes

- **Path shape.** The enumerator emits *walks* (sequences of edges where the head of edge `i+1` equals the tail of edge `i`) that are *edge-simple* (no `(u, v, key)` triple repeated). It does **not** require node-simplicity — a single node can be revisited if the connecting edges are distinct. Closed loops (start == end) are allowed but not required. The PRD/Architecture say nothing requiring loops; trail routes in practice are often open trailhead-to-trailhead. Enumerating all (start, end) pairs across all nodes is fine for 5–8 node graphs.

- **Where edge-reuse is enforced.** The contracted graph from Story 3.3 already drops sub-`l_connector` edges, so every edge in `graph.graph` is either a super-edge (climb) or a "long" connector. The "edge-reuse limit" in Architecture §Cat 6 simplifies to: each edge in `graph.graph` may appear at most once per route. That's the standard simple-walk constraint — no separate "is this a connector?" branch needed in the enumerator. (Sub-`l_connector` edges are gone from the input by construction; the validator's `--l-connector` check on real GRASP output is a *defense-in-depth* check, not something the oracle needs to model.)

- **Slope floor applies to climbs only.** θ is enforced on *super-edges* (the contracted-graph representation of climbs) — long connectors carry whatever slope the underlying trail has, including downhill/flat, and that's fine by design. The membership test `(u, v, k) in graph.super_edge_to_base` is the canonical way to identify a super-edge (Story 3.3's contract); use it directly, don't introspect edge data dicts.

- **Objective definition.** `d_plus_m + d_minus_m` summed across the route's edges — "vertical effort" per Architecture §"Stagnation definition" and §Cat 5e. Super-edges in the contracted graph carry `d_plus_m` / `d_minus_m` already aggregated from their constituent base edges (Story 3.3's `contract_climbs`); just sum them as-is. **Do not** expand super-edges via `super_edge_to_base` to re-sum — that's a re-derivation of values already on the super-edge attributes, and any drift between the two paths would be a Story 3.3 bug surfaced in the wrong test.

- **Distinctness goes through the production tracker.** Story 3.4's `TopNTracker` *is* the distinctness oracle for both GRASP and the exhaustive enumerator — feed the full sorted enumeration into `tracker.consider(...)` in objective-descending order and read off `tracker.current_top()`. This guarantees the oracle and GRASP use the same admission semantics (including the all-overlap eviction policy ratified in Story 3.4's review), which is what makes 3.7's GRASP-vs-exhaustive ratio actually meaningful. The alternative — a hand-rolled post-hoc filter — would risk asymmetric distinctness semantics that mask real GRASP bugs.

- **Strict containment is out of scope here.** FR10's strict containment is enforced by `pipeline.graph.contract_climbs` cutting the contracted graph to the area at construction time, before the solver (or oracle) ever sees it. The oracle operates on `graph.graph` as given. Don't add an `Area` check — the handcrafted fixtures aren't built from a real area.

- **Constructing handcrafted fixtures.** Building a `ContractedGraph` for tests requires (a) a `networkx.MultiDiGraph` with the post-stage-7 attribute contract on each edge (`length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`) and (b) a `super_edge_to_base: dict[(u,v,k), tuple[Edge, ...]]` for whichever edges are super-edges. For oracle correctness tests, the base edges inside `super_edge_to_base` can be a *single synthetic `Edge`* per super-edge with the same aggregated metrics — the oracle never expands super-edges, so the base-edge content is unobservable. Pick metric values that make the expected optimum trivial to derive by hand (e.g. `d_plus_m=100, d_minus_m=0` for an uphill super-edge; `0, 0` for a flat connector). Look at `tests/unit/test_graph_contraction.py` for the established pattern of inline `MultiDiGraph` construction.

- **Why this story precedes Story 3.6 (GRASP).** Architecture §Cat 11c lists oracle correctness as a CI gate independent of GRASP. The toy-graph factory used by 3.7/3.8 will land in `tests/integration/conftest.py` and *use* the oracle as the reference; getting the oracle in first means 3.7 can be a thin wiring story rather than co-developing oracle + GRASP + toy factory simultaneously.

- **What this story does NOT do:**
  - Implement the toy-graph factory used by Story 3.7's GRASP-vs-exhaustive comparison — that's 3.7's job. This story uses *handcrafted* fixtures only.
  - Implement GRASP — Story 3.6.
  - Wire the oracle into the metamorphic invariants — Story 3.8.
  - Touch `src/steeproute/` — the oracle lives under `tests/integration/` and is never imported by production code.

### Project Structure Notes

- **New:** `tests/integration/exhaustive_oracle.py` — `enumerate_best` + any local helpers.
- **New tests:** `tests/integration/test_oracle_correctness.py` — handcrafted-fixture correctness tests + pathological-case tests + timing assertion.
- **Modified:** none. `tests/integration/conftest.py` is not touched in this story (the toy-graph factory lands in 3.7).
- **Untouched in `src/`:** the oracle is testing-only by design. Importing it from `src/steeproute/` is a regression — the Architecture diagram (§"Project Structure") doesn't list it under `src/`.

### Testing standards summary

- Tests live in `tests/integration/` — they exercise multi-module behavior (the oracle traverses a `ContractedGraph` that itself comes from `pipeline.graph`'s contract, and feeds through `solver.distinctness.TopNTracker`). Naming follows `test_<unit>_<scenario>` per Architecture §"Test organization" (e.g. `test_enumerate_best_finds_dominant_climb_path`, `test_enumerate_best_returns_empty_on_all_infeasible`).
- The oracle module file itself (`exhaustive_oracle.py`) is *not* prefixed with `test_` because it is a *helper*, not a test — pytest will not collect it. This mirrors the Architecture §Cat 11b "handcrafted oracle fixtures" intent and the reference in the project tree (line 842 of architecture.md: `test_oracle_correctness.py` is the test file; the oracle helper lives alongside).
- Float comparisons via `math.isclose(..., abs_tol=1e-9)` — never `==` (Architecture §"Numerical and data discipline"). Exception: the all-zero metrics case admits an exact `0.0` check, but `isclose` is fine there too.
- No `hypothesis` required for this story — the value is in *concrete, hand-derivable* fixtures (PRD Appendix A's whole point: known optima by inspection). Property tests don't help when the goal is "this specific graph has this specific best route."
- No `pytest.skip` / `xfail` on any test in this file — Architecture §Cat 11c lists oracle correctness as pass-required.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.5"](../planning-artifacts/epics.md) — AC source-of-truth
- [Source: _bmad-output/planning-artifacts/prd.md §"Appendix A — Oracle correctness"](../planning-artifacts/prd.md) — "validating against an unvalidated oracle" framing; "2–3 tiny hand-verified graphs (5 nodes, known optimum)"
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11 (11b, 11c)](../planning-artifacts/architecture.md) — handcrafted oracle fixtures; oracle-correctness CI gate; no-skip/no-xfail rule
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 6](../planning-artifacts/architecture.md) — constraint set (slope floor, difficulty cap, edge-reuse, graph membership, pairwise Jaccard) and per-route vs. set-level split
- [Source: _bmad-output/planning-artifacts/architecture.md §"Stagnation definition"](../planning-artifacts/architecture.md) — objective is `D+ + D−` summed across the route's edges
- [Source: _bmad-output/planning-artifacts/architecture.md §"Numerical and data discipline"](../planning-artifacts/architecture.md) — canonical `(node_u, node_v, key)` edge identity; `math.isclose` for float comparison
- [Source: src/steeproute/models.py:102-122](../../src/steeproute/models.py) — `ContractedGraph` shape; `super_edge_to_base` membership is the super-edge marker
- [Source: src/steeproute/models.py:166-181](../../src/steeproute/models.py) — `Solution` shape (`edges: tuple[Edge, ...]`, `objective: float`); edges in traversal order
- [Source: src/steeproute/solver/distinctness.py](../../src/steeproute/solver/distinctness.py) — `TopNTracker(n, j_max)` admission policy + `jaccard_distance`
- [Source: src/steeproute/pipeline/graph.py](../../src/steeproute/pipeline/graph.py) — `contract_climbs` produces the `ContractedGraph` the oracle consumes; sub-`l_connector` edges already dropped at this layer
- [Source: tests/unit/test_graph_contraction.py](../../tests/unit/test_graph_contraction.py) — established pattern for inline `MultiDiGraph` + `ContractedGraph` construction in tests

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. No new runtime or dev deps; oracle reuses `steeproute.solver.distinctness.TopNTracker` (Story 3.4) and `steeproute.pipeline.osm.{max_sac_rank, parse_difficulty_cap}` (Story 2.1).

**Final pass (all green):**

```
uv run ruff check               → All checks passed!
uv run ruff format --check      → 59 files already formatted
uv run basedpyright             → 0 errors, 0 warnings, 0 notes
uv run pytest --cov             → 526 passed, 1 deselected; 97% overall coverage
                                  (oracle module under tests/ — not counted)
test_oracle_correctness.py      → 8 passed in 0.90s
```

### Completion Notes List

**Design decisions worth review attention:**

1. **Import shape — pytest's `prepend` mode dictates the form.** Pytest runs with no `__init__.py` anywhere under `tests/` (verified via glob), so each test file's parent directory is what lands on `sys.path` at collection time. That forces `from exhaustive_oracle import enumerate_best` — relative imports (`from .exhaustive_oracle …`) fail because the test module isn't loaded as part of a package, and the fully-qualified `from tests.integration.exhaustive_oracle …` fails because the rootdir is never on `sys.path` either. The file-level pragma adds `reportImplicitRelativeImport=false` and the comment block documents why; ruff's import sorter (autofixed) groups `exhaustive_oracle` with networkx since both are top-level imports from its perspective.

2. **DFS emits at every depth, not only at leaves.** Every non-empty edge-simple walk is a valid route, so the recursion emits a candidate on entry (after appending the new edge) rather than only when no further edges can be taken. This is what makes the oracle correct for partial paths — e.g. the `bypass: 0→4` in Fixture A is a length-1 walk and a valid (low-objective) candidate, even though it doesn't lead anywhere.

3. **Dedup by canonical edge-set is safe.** The objective is `sum(d_plus_m + d_minus_m)` over the edge multiset, which is identical for any traversal order. So when DFS reaches the same edge-set from different starting points or via different orderings, the second-encountered representative is dropped — saves an exponential factor of work without changing the result. The first ordering encountered is preserved in `Solution.edges` (Story 3.4's contract allows any valid traversal order in `Solution.edges`; the downstream consumer reads the canonical set, not the order).

4. **TopNTracker is the distinctness oracle, on purpose.** The full enumeration is sorted objective-descending then fed through `TopNTracker(n, params.j_max)` — the exact same admission policy GRASP will use in Story 3.6. The alternative (a hand-rolled post-hoc filter) would risk asymmetric distinctness semantics between oracle and solver, which would mask real GRASP bugs in Story 3.7's quality-ratio gate. This is the key correctness-architectural decision in the story.

5. **SAC-cap handling matches `filter_trails`' include-policy posture.** `max_sac_rank(sac_scale)` returns `None` for both raw-`None` values and unrecognized strings. The oracle skips an edge only when `rank is not None and rank > cap_rank` — so `None`-sac edges pass through (they cleared the upstream filter under the prevailing `untagged_policy`). Same posture as the validator in Story 3.9 will need to take.

6. **θ filter applies to super-edges only.** Membership test against `graph.super_edge_to_base` identifies super-edges; long connectors carry whatever gradient their underlying trail has and are not subject to θ. This matches Story 3.2's climb-detection semantic (θ is a *climb* threshold, not a per-edge threshold).

**AC walkthrough — evidence per criterion:**

1. AC #1 — `tests/integration/exhaustive_oracle.py::enumerate_best(graph, params, n)` returns objective-descending top-`n` distinct feasible routes, filtered by SAC cap + θ-on-super-edges, deduplicated by canonical edge-set, distinctness-filtered through `TopNTracker`. Pure, no `src/steeproute/` imports it. ✅
2. AC #2 — three handcrafted fixtures (`_build_fixture_a/_b/_c`) at 5, 5, 7 nodes; (a) dominant chain in A, (b) two structurally-distinct equal-objective paths in B, (c) 7-node disconnected-component graph in C. Each preceded by an ASCII-art comment block documenting topology, per-edge metrics, and the expected optimum + objective derived by inspection. Three corresponding tests assert canonical edge-set equality + `math.isclose(objective, …, abs_tol=1e-9)`. ✅
3. AC #3 — four pathological tests: `test_enumerate_best_returns_empty_on_empty_graph`, `…_when_every_edge_violates_sac_cap`, `…_returns_fewer_than_n_when_few_feasible_paths_exist`, `…_drops_super_edges_below_theta` (covering the all-infeasible-via-θ case explicitly, complementing the SAC-cap-infeasible case). ✅
4. AC #4 — `test_enumerate_best_completes_under_one_second_on_five_node_fixture` runs Fixture A through `enumerate_best` with `n=3` and asserts `time.perf_counter` elapsed < 1.0 s. Actual measured time well under that threshold (full file runs in 0.90 s for all 8 tests). No `pytest-timeout` dep was needed. ✅
5. AC #5 — all four CI gates green on Windows; full suite at 526 passed (was 518 before this story, +8 new tests); 97% overall coverage held (the oracle module under `tests/` is not counted toward coverage thresholds, consistent with `[tool.coverage.run] source = ["src/steeproute"]`); no `pytest.skip` / `xfail` on any test in this file; no new runtime or dev deps. ✅

### File List

**New:**
- `tests/integration/exhaustive_oracle.py` — `enumerate_best(graph, params, n)` brute-force enumerator + private `_dfs` backtracking helper. Reuses `TopNTracker` (Story 3.4) for top-N + distinctness; reuses `max_sac_rank` / `parse_difficulty_cap` (Story 2.1) for the SAC cap. No imports into `src/`.
- `tests/integration/test_oracle_correctness.py` — 11 tests post-review: four handcrafted-fixture correctness tests (Fixtures A/B/C/D, with ASCII-art topology comments; D added in review to exercise parallel multi-edges), five pathological-input tests (empty / isolated-nodes-no-edges / all-SAC-infeasible / fewer-than-n / sub-θ-super-edge with `sac_scale=None` isolation), one θ-boundary test pinning the strict-`<` contract, and one wall-clock-bound test. Local `_assert_valid_walk(sol_edges)` helper called on every non-empty result to pin the edge-simple-walk structural contract.

**Modified:**
- _(none — `src/steeproute/` untouched by design; the oracle is testing-only infrastructure)_

**Updated (out-of-source):**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story `3-5-exhaustive-enumerator-oracle-with-its-own-correctness-tests` walked `backlog → ready-for-dev → in-progress → review`. `last_updated: 2026-05-29`.

**Untouched (intentionally):**
- `src/steeproute/` — story explicitly forbids exporting the oracle from the main package (Dev Notes: "the oracle lives under `tests/integration/` and is never imported by production code").
- `tests/integration/conftest.py` — the toy-graph factory lands in Story 3.7, not this story.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-29 | Yann (Claude Opus 4.7) | Story 3.5 implemented: exhaustive enumerator oracle + correctness tests for Epic 3 (Architecture §Cat 11c "oracle correctness pass-required" CI gate; PRD Appendix A "validating against an unvalidated oracle"). **`tests/integration/exhaustive_oracle.py`** (new) — `enumerate_best(graph, params, n)` brute-forces all edge-simple directed walks in a `ContractedGraph`, applies the SAC cap per-edge + θ filter on super-edges (membership via `super_edge_to_base`), deduplicates by canonical `(node_u, node_v, key)` edge-set, sorts objective-descending, and feeds the result through `TopNTracker(n, params.j_max)` — same admission policy GRASP will use in Story 3.6 (apples-to-apples comparison for Story 3.7's quality-ratio gate). Pure, no `src/` imports. **`tests/integration/test_oracle_correctness.py`** (new) — 8 tests across three ASCII-art-documented fixtures (5-node single-dominant-chain; 5-node two-structurally-distinct-paths under permissive `j_max`; 7-node disconnected-components) + four pathological cases + a <1 s wall-clock bound on the smallest fixture. All four CI gates green: ruff ✅, ruff format ✅, basedpyright 0/0/0 ✅, pytest 526 passed at 97% coverage. No new runtime or dev deps. | _pending_ |
| 2026-05-29 | Yann (Claude Opus 4.7) | Code review (3 adversarial layers) findings applied — 5 patch + 5 defer resolved, ~10 dismissed. **`test_oracle_correctness.py`**: added Fixture D (two parallel `(0,1)` edges with different `key`s) + `test_enumerate_best_distinguishes_parallel_edges_by_key` (closes the multi-key path that production `contract_climbs._next_key_for` exercises); added `_assert_valid_walk(sol_edges)` helper pinning the structural contract on returned tuples (consecutive `prev.node_v == next.node_u`, no `(u,v,k)` repeats), now called on every non-empty result in Fixtures A/B/C/D; added `test_enumerate_best_admits_super_edge_at_theta_boundary` pinning the strict-`<` contract at exactly `avg_gradient == theta`; added `test_enumerate_best_returns_empty_on_isolated_nodes_with_no_edges` exercising the inner `_dfs` empty-path branch (the existing empty-graph test was vacuous w.r.t. DFS); `test_enumerate_best_drops_super_edges_below_theta` now sets `sac_scale=None` on its edge with a comment so the test unambiguously isolates the θ filter from the SAC filter. Deferred 5 cross-story items (real-graph attribute-contract guards; self-loop/cycle semantics; timing-bound trade-off; list-valued `sac_scale` integration; `Solution.edges` ordering determinism) — all routed to Story 3.7 follow-up via `deferred-work.md`. All gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 529 passed (+3 net tests); coverage 97% held. | _pending_ |
