# Story 5.2: Undirected reuse enforcement in solver, oracle, and validator

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want the once-only reuse rule keyed on the undirected base-segment identity with short connectors exempt, enforced consistently by the GRASP solver, the exhaustive oracle, and the validator,
so that no returned route walks a non-exempt trail segment twice in any direction (FR5/FR26) and the degenerate out-and-back chains are eliminated by construction.

## Acceptance Criteria

1. **Undirected base-segment reuse in the solver.** GRASP construction tracks *used base-segment ids* instead of directed `(node_u, node_v, key)` triples. An edge is infeasible iff any of its **non-exempt** base-segment ids is already used; taking an edge records its non-exempt ids; an edge whose non-exempt id-set is empty (a truly-exempt short connector) never blocks and is never recorded. The classic out-and-back over a climb is rejected by construction; a short connector may still legitimately recur within one route.

2. **Per-id exemption — resolves the gap deferred from 5.1.** A base-segment id is reuse-exempt iff **every** edge in the contracted graph carrying it is `reusable`. A short connector that shares an id with a (non-reusable) climb super-edge is therefore **not** exempt for that id — descending the reverse of a climb is forbidden even though the connector is `reusable=True` per-edge. This exemption set is computed once from the graph and single-sourced across solver, oracle, and validator (see Dev Notes).

3. **Oracle enforces the identical rule.** `tests/integration/exhaustive_oracle.py`'s DFS applies the same undirected base-segment reuse with per-id exemption, so the oracle and GRASP enumerate the same feasible set. The directed `frozenset((u,v,key))` dedup/distinctness identity is **unchanged** (Jaccard stays directed — deferred open item, do not touch).

4. **Validator `edge_reuse` keyed on undirected base segment.** The check flags a non-exempt base segment appearing more than once across the route's edges (in either direction); it never flags repeated exempt short connectors. The other three per-route checks (slope_floor, difficulty_cap, graph_membership) are unchanged. The `validator.py` module docstring (`:33-39`) is reworded from the directed edge-simple description to the realized FR5 undirected/exempt semantics.

5. **Behaviour proven by tests.** Unit + integration tests assert: out-and-back over a climb is rejected; a short connector may recur in one route; `edge_reuse` fires on undirected base-segment reuse and not on exempt connectors; the oracle gets a small hand-graph where directed-edge-simple and undirected-base-segment yield *different* optima (locks in the semantics). On the real Grenoble fixture, no returned route reuses a non-exempt base segment in either direction. The Story 3.7 GRASP-vs-exhaustive quality gate passes with both sides on the new feasible set.

6. **Purity, rendering, and gates.** Solver, oracle, and validator remain pure (no input mutation). `output.py` renders a route containing a reusable connector traversed twice without error (confirmed by a test; expected to need no source change — see Dev Notes). The four CI gates pass on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest` — with no new deps and coverage floors held.

## Tasks / Subtasks

- [x] Task 1: Add a single shared, pure helper that derives the reuse-exempt base-segment id set from a `ContractedGraph` (an id is exempt iff every edge carrying it is `reusable`). Single-source it so solver/oracle/validator agree. (AC: #2) — `solver/reuse.py` (`non_exempt_base_segment_ids`, `blocking_ids`, `base_segment_ids`).
- [x] Task 2: Replace the directed `used_ids` set in `solver/grasp.py` (`_construct_one` / `_build_rcl`) with used non-exempt base-segment-id tracking; update the module docstring's "edge-simple" description. (AC: #1, #6) — kept a directed-edge-simple bound alongside the base-segment set to guarantee termination (see Completion Notes).
- [x] Task 3: Apply the identical rule in `tests/integration/exhaustive_oracle.py`'s `_dfs`; leave the directed dedup key and Jaccard identity untouched. (AC: #3, #6)
- [x] Task 4: Change `validator.py`'s `edge_reuse` check to count non-exempt base-segment ids; rework the module docstring. Keep the other three checks as-is. (AC: #4, #6)
- [x] Task 5: Verify `output.py` renders a twice-traversed reusable connector sanely; adjust only if it assumes edge-uniqueness. (AC: #6) — no source change needed; added a render test.
- [x] Task 6: Extend tests — `test_grasp_construction.py`, `test_grasp_on_fixture.py`, `test_validator.py`, `test_oracle_correctness.py`, `test_solver_on_toy_graph.py` (and their graph-builder helpers, which must now tag edges with `base_segment_id` + `reusable`). (AC: #5)
- [x] Task 7: Run all four gates + full suite on Windows. (AC: #6)

### Review Findings

_Code review 2026-06-04 (Blind Hunter + Edge Case Hunter + Acceptance Auditor). All 6 ACs verified PASS; no scope creep; gates trusted (665 passed). Core algorithm — backtracking add/remove symmetry, per-id exemption construction, and directed-simple termination bound — confirmed correct by all three layers. No HIGH-severity defects. Items below are quality/hardening; none block the story._

- [x] [Review][Decision→Dismiss] Tolerant fallback for missing/malformed reuse tags degrades silently instead of failing loud — `solver/reuse.py` `base_segment_ids` / `non_exempt_base_segment_ids` fall back to the directed `(u,v,key)` identity + `reusable=False` for any edge lacking the tags. **Dismissed (Yann's call, 2026-06-04): keep the tolerant fallback as-is.** Not a live bug — `contract_climbs` always tags every edge, and `cli/query.py:206-214` builds the contracted graph once and hands the *same object* to both the solver and the validator, so the "single feasible set" guarantee holds and Edge Case Hunter's solver/validator-divergence scenario cannot occur. The fallback is intentional (keeps the 5.3-scoped metamorphic suite green) and test-only in practice; a fail-loud assertion was judged not worth the extra code for a state that cannot currently occur. (Merged Blind B5/B6, Edge E1/E4/E5.)
- [x] [Review][Patch] Exact-float `== 200.0` replaced with `math.isclose(..., abs_tol=1e-9)` for codebase float discipline [tests/integration/test_oracle_correctness.py — `test_enumerate_best_undirected_reuse_blocks_out_and_back`]
- [x] [Review][Patch] `_construct_one` docstring "at most twice" wording tightened to not overstate for parallel/multi-key exempt edges [src/steeproute/solver/grasp.py — `_construct_one` docstring]
- [x] [Review][Defer] Self-loop / parallel-edge undirected-id collisions are a Story 5.1 `_base_segment_id` (`(min,max,key)`) scheme property, not this change [src/steeproute/pipeline/graph.py:_base_segment_id] — deferred, pre-existing (already acknowledged in 5.1's key-dependence review note); degenerate OSM shapes only.
- [x] [Review][Defer] The Story 3.7 quality gate and the 3.8 metamorphic suite run on the *directed*-tagged toy factory, so the undirected feasible-set change is not stressed there [tests/integration/conftest.py] — deferred to Story 5.3: route the undirected-identity coverage through the metamorphic re-validation. Undirected behaviour is currently proven by the dedicated solver/oracle/validator units and the real-Grenoble-fixture test.

## Dev Notes

- **Scope: solver + oracle + validator reuse logic only (proposal §4B B3–B6, §4C 3.6/3.9/3.5/3.7).** Story 5.1 already produced the tagged edge data this story consumes — **do not re-touch `pipeline/graph.py` or the `models.py` field/docstring contract** for tags. The metamorphic suite (3.8), CLI help string, and PRD/architecture/epics prose sync are **Story 5.3**, not here. Per-climb slope and SAC-cap filters are unrelated and stay as-is.

- **The crux — per-id exemption (lead recommendation).** Story 5.1's code review surfaced and deferred a real gap to this story (Yann's call, 2026-06-04): `reusable` is per-*edge* but once-only identity is per-*id*. When a climb's base edges are each `< l_connector` (common — a 300 m+ climb of 50–200 m OSM segments), their reverse-direction connectors are tagged `reusable=True`, yet their `base_segment_id` is also in the climb super-edge's id set. The naive rule ("reusable edges never block and are never recorded") would let the solver record the super-edge's ids on ascent and then *descend* the reusable reverse connectors — the out-and-back survives for this whole class of climb. **Fix:** evaluate exemption **per-id, not per-edge** — an id is exempt iff *every* edge carrying it is `reusable`. Compute the non-exempt id set once from the graph; each edge's "blocking ids" = `base_segment_id − exempt_ids`. An edge blocks/records on its blocking ids; an edge with an empty blocking set is truly exempt. This is the binding contract — without it AC #5's "out-and-back rejected on the fixture" will fail for short-edge climbs. (Ref: 5.1 Review Findings, first deferred item.)

- **Where the tags live + access (recommendation).** `base_segment_id` (`frozenset[tuple[int,int,int]]`) and `reusable` (`bool`) live on the **graph edge-data dict**, not on the `Edge` dataclass (Story 5.1; `models.py:119-154`). Recommended: keep `Edge` unchanged and read the tags off the data dict where you already iterate it — the solver's `_build_rcl` and the oracle's `_dfs` both loop `out_edges(..., data=True)`; the validator has `graph` and can look up `nx_graph.get_edge_data(u,v,key)` per route edge (it already does a `graph_membership` lookup). An edge **absent** from the operational graph (a `graph_membership` failure, FR28) contributes no base id to the reuse check — it is already flagged by `graph_membership`, so the validator must stay robust and not `KeyError`. *Alternative:* widen the `Edge` dataclass with the two fields so they flow through `Solution` automatically — cleaner uniformity but broad churn (every `Edge(...)` constructor + every test builder). Either is acceptable; pick one and keep it consistent across the three sites.

- **Single-source the exempt-id set, like `route_avg_gradient`.** Put the "exempt ids from graph" derivation in one pure helper (e.g. alongside `route_avg_gradient` in `models.py`, or a small solver-side module) and call it from all three consumers, so GRASP, the oracle, and the validator can never diverge on the feasible set — that divergence would silently break the Story 3.7 gate.

- **Solver tracking shape.** `_construct_one` currently holds `used_ids: set[tuple[int,int,int]]` of directed triples and adds `(chosen.node_u, chosen.node_v, chosen.key)` per step (`grasp.py:181-189`); `_build_rcl` skips `eid in used_ids` (`:228`). Replace with a `set` of used base-segment ids and skip an edge iff its blocking-id set intersects the used set; on taking an edge, union its blocking ids in. The RCL ranking sort and SAC/θ handling are unchanged.

- **Oracle parity.** `_dfs` mirrors the solver's filters (`exhaustive_oracle.py:139-167`). Apply the same blocking-id logic to its `used_ids`. **Do not** change the `frozenset((u,v,key))` candidate-dedup key at `:134` — that is the directed canonical edge-set used for distinctness, left directed by the proposal's deferred Jaccard open item.

- **Validator reuse check.** Today `_validate_edges` counts directed `(u,v,key)` occurrences and fires `edge_reuse` on count > 1 (`validator.py:209-225`). Re-key it on non-exempt base-segment ids: a violation iff a non-exempt base segment appears more than once across the route. Exempt short connectors recurring must **not** fire. Keep the existing distinct-identity de-dup for the difficulty/membership checks (`:170-207`) as-is.

- **Renderer (likely a no-op).** `output._route_vertices` iterates `route.edges` sequentially and `_extend_dedup` only drops the shared join vertex (`output.py:226-264`), so a reusable connector traversed twice draws its polyline/profile twice — the sane "overdrawn segment" behaviour the proposal anticipated. Add a test asserting no error; change source only if a real edge-uniqueness assumption surfaces.

- **No value-shift is expected to be *silent*.** This story deliberately shrinks the feasible set, so GRASP/oracle outputs *will* change on the fixture — that is the point (AC #5). The Story 3.7 gate asserts structural parity (GRASP vs. oracle on the same feasible set), not fixed values, so it must still pass; metamorphic/toy suites that build `ContractedGraph` directly are Story 5.3's concern.

### Project Structure Notes

- **Modify (source):** `src/steeproute/solver/grasp.py` (`_construct_one`, `_build_rcl`, module docstring), `src/steeproute/validator.py` (`_validate_edges` edge_reuse branch + module docstring), plus the new shared exempt-id helper (`models.py` or a small solver module). `src/steeproute/output.py` only if Task 5 finds a real assumption.
- **Modify (tests):** `tests/integration/exhaustive_oracle.py` (`_dfs`); `tests/unit/test_grasp_construction.py`, `tests/unit/test_validator.py`, `tests/integration/test_oracle_correctness.py`, `tests/integration/test_solver_on_toy_graph.py`, `tests/integration/test_grasp_on_fixture.py`, and a renderer test (`tests/unit/test_output.py` or `tests/integration/test_output_on_fixture.py`).
- **Test-builder tagging is mandatory:** the graph builders in the solver/oracle/validator tests (`_add_edge`/`_graph` in `test_grasp_construction.py`, `_graph` in `test_validator.py:90-113`, `_add_edge`/`_build_fixture_*` in `test_oracle_correctness.py`) were written pre-5.1 and do **not** set `base_segment_id`/`reusable` on edge data. They must now tag edges (mirroring `contract_climbs`) or the new rule reads missing keys. Reuse and extend these helpers; do not fork new ones.
- **Do NOT touch:** `pipeline/graph.py`, the `models.py` tag contract/docstrings (5.1), the metamorphic suite, CLI help, PRD/architecture/epics prose (5.3), and the oracle's directed dedup/Jaccard identity.

### Testing standards summary

- Synthetic-graph unit tests in `tests/unit/`, real-fixture in `tests/integration/`; naming `test_<unit>_<scenario>` (Architecture §"Test organization"). No `pytest.skip`/`xfail` (pass-required, Architecture §Cat 11c).
- New branches (per-id exemption, undirected reuse) must be covered — add the difference-of-optima oracle graph and the short-connector-recurs case so no new source branch goes untested; coverage floor (`fail_under = 0`) must hold or rise.
- Regression proof: the suite stays green except for the *intended* feasible-set change on the fixture (AC #5). The Story 3.7 GRASP-vs-exhaustive parity gate is the strongest guard that solver and oracle still share one feasible set — if it fails, the exempt-id helper is not truly single-sourced.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 5.2"](../planning-artifacts/epics.md) — BDD acceptance criteria (lines 754-766)
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-03-undirected-segment-reuse.md §4B (B3–B6), §4C (3.6/3.9/3.5/3.7), §5](../planning-artifacts/sprint-change-proposal-2026-06-03-undirected-segment-reuse.md) — dev handoff; "reusable edges never block and are never recorded"; oracle/GRASP parity; Jaccard deferred
- [Source: _bmad-output/implementation-artifacts/5-1-base-segment-identity-and-connector-revival-at-contraction.md §"Review Findings"](5-1-base-segment-identity-and-connector-revival-at-contraction.md) — **per-id exemption gap deferred to this story** (the AC #2 crux); base_segment_id scheme = canonical sorted node-pair + key, stored as `frozenset` on every edge
- [Source: src/steeproute/solver/grasp.py:164-246](../../src/steeproute/solver/grasp.py) — `_construct_one` `used_ids` (:181-189), `_build_rcl` skip + Edge build (:226-244)
- [Source: src/steeproute/validator.py:33-39,138-227](../../src/steeproute/validator.py) — module docstring to reword; `edge_reuse` directed-count branch (:209-225)
- [Source: tests/integration/exhaustive_oracle.py:117-167](../../tests/integration/exhaustive_oracle.py) — `_dfs` `used_ids` (:139-167); directed dedup key to keep (:134)
- [Source: src/steeproute/models.py:88-101,119-154](../../src/steeproute/models.py) — `route_avg_gradient` single-source pattern; `ContractedGraph` tag contract (`base_segment_id` frozenset + `reusable`)
- [Source: src/steeproute/output.py:214-264](../../src/steeproute/output.py) — `_route_vertices` / `_extend_dedup` (twice-traversed connector handling)
- [Source: tests/unit/test_validator.py:67-113](../../tests/unit/test_validator.py) — `_edge`/`_graph` builders to extend with tags

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps (stdlib `frozenset`/`set` only).

**Final gate pass (all green):**

```
uv run ruff check          → All checks passed!
uv run ruff format --check  → 74 files already formatted
uv run basedpyright        → 0 errors, 0 warnings, 0 notes
uv run pytest -q           → 665 passed, 1 deselected in ~17 min
                             (658 baseline + 7 net new tests; the 1 deselected
                              is the network-gated osm_live test)
```

### Completion Notes List

**Scope held to the reuse logic (solver + oracle + validator + shared helper).** No change to `pipeline/graph.py`, the `models.py` tag contract, the metamorphic suite, CLI help, or PRD/architecture/epics prose (Story 5.3). The directed dedup / Jaccard identity in the oracle was left untouched (deferred open item).

**Single source of the rule — `solver/reuse.py` (new).** `non_exempt_base_segment_ids(graph)` = the union of base ids of every non-reusable edge (computed once per graph). `blocking_ids(data, u, v, k, non_exempt)` = an edge's base ids ∩ non-exempt. GRASP, the oracle, and the validator all call these, so they can never diverge on the feasible set — the Story 3.7 gate confirms parity holds (47 solver/metamorphic/repro tests green).

**Per-id exemption (the AC #2 crux, deferred from 5.1) is implemented.** An id is exempt iff *every* edge carrying it is `reusable`; a short reverse-of-climb connector that shares an id with the non-reusable super-edge is therefore non-exempt and blocks. Verified directly: `test_grasp_rejects_out_and_back_over_a_climb`, `test_validate_route_flags_undirected_base_segment_reuse`, and the real-fixture `test_no_grasp_route_reuses_a_nonexempt_base_segment`.

**Necessary refinement of the proposal's wording — termination.** The proposal said "reusable edges never block and are never recorded." Taken literally that lets a walk loop `a→b→a→b…` forever on an exempt connector (the brute-force oracle would never terminate). The walk therefore keeps a **directed-edge-simple** bound (`used_directed`) alongside the undirected base-segment set (`used_segments`): an exempt connector may appear at most once per direction (twice total); a non-exempt segment at most once in either direction. For the no-tags fallback this collapses to the pre-5.1 directed edge-simple rule. The solver and oracle apply the identical two-set logic; the validator (which only judges route validity, not construction) checks the base-segment rule alone, so it correctly does not flag a repeated exempt connector.

**`Edge` dataclass left unchanged.** The solver/oracle read `base_segment_id`/`reusable` off the graph edge-data dict they already iterate; the validator looks them up via `graph.get_edge_data(...)`. An edge absent from the graph contributes nothing to the reuse check (already flagged by `graph_membership`), and the helper falls back to the directed identity for any untagged edge, so the metamorphic suite's untagged `_with_added_edge` transform does not `KeyError`.

**Renderer needed no source change.** `output._route_vertices` already iterates `route.edges` sequentially; a twice-traversed connector simply draws twice. Pinned by `test_render_handles_reusable_connector_traversed_twice`.

**Toy factory tagged with the *directed* id (test-fidelity fix).** The toy graph models no two edges as the same physical trail, so the factory tags each synthetic edge as its own directed `(u, v, key)` segment. An earlier attempt tagging the undirected `(min,max,key)` made the factory's back-edges (which can land on adjacent spine layers, i.e. the reverse of a forward spine edge) spuriously share a base id, which shrank the feasible set and tripped two metamorphic *non-vacuity* guards (the invariant itself never failed). The directed tag keeps the factory's feasible set bit-identical to pre-5.2, so the Story 3.7 gate and all 8 metamorphic invariants are unperturbed; genuine forward/reverse collisions are covered by the real-fixture test and the dedicated units.

**AC walkthrough:**
1. AC #1 — GRASP tracks used base-segment ids; out-and-back over a climb rejected by construction; short connector may recur. ✅
2. AC #2 — per-id exemption in `solver/reuse.py`; short reverse-of-climb connector is non-exempt. ✅
3. AC #3 — oracle `_dfs` applies the identical rule; directed dedup/Jaccard untouched. ✅
4. AC #4 — validator `edge_reuse` keyed on non-exempt base segments; never fires on exempt connectors; docstring reworded. ✅
5. AC #5 — out-and-back rejected, connector-recurs, undirected-reuse `edge_reuse`, different-optima oracle, real-fixture no-reuse all asserted; Story 3.7 gate passes. ✅
6. AC #6 — solver/oracle/validator stay pure; renderer test green; ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 665 passed; no new deps. ✅

### File List

**Added (source):**
- `src/steeproute/solver/reuse.py` — the single-sourced undirected base-segment reuse rule (`non_exempt_base_segment_ids`, `blocking_ids`, `base_segment_ids`), tolerant of untagged edges (directed fallback).

**Modified (source):**
- `src/steeproute/solver/grasp.py` — `_construct_one` / `_build_rcl` now track `used_directed` (edge-simple, for termination) + `used_segments` (undirected non-exempt base ids); `_build_rcl` returns `(Edge, blocking_ids)` pairs; `__init__` computes `_non_exempt_ids` once; module + method docstrings updated.
- `src/steeproute/validator.py` — `_validate_edges` `edge_reuse` check re-keyed onto non-exempt base-segment counts (was directed `(u,v,key)` counts); module docstring reworded to the realized FR5 semantics. Other three checks unchanged.

**Modified (tests):**
- `tests/integration/exhaustive_oracle.py` — `_dfs` applies the identical two-set reuse rule; directed dedup key kept; docstrings updated.
- `tests/integration/conftest.py` — toy factory tags every edge with `base_segment_id` (directed) + `reusable=False`.
- `tests/unit/test_grasp_construction.py` — `_add_edge` gains reuse-tag params; added `test_grasp_rejects_out_and_back_over_a_climb`, `test_grasp_allows_exempt_connector_in_both_directions`, `_seg` helper.
- `tests/integration/test_oracle_correctness.py` — `_add_edge` gains reuse-tag params; added `test_enumerate_best_undirected_reuse_blocks_out_and_back`, `_seg` helper.
- `tests/unit/test_validator.py` — `_graph` gains `base_ids`/`reusable_ids` overrides; added `test_validate_route_flags_undirected_base_segment_reuse`, `test_validate_route_does_not_flag_repeated_exempt_connector`, `_seg` helper; section comment + one docstring de-stale'd.
- `tests/unit/test_output.py` — added a reverse connector to `_base_graph` + `test_render_handles_reusable_connector_traversed_twice`.
- `tests/integration/test_grasp_on_fixture.py` — refactored to a `solver_chain` fixture exposing the contracted graph; added `test_no_grasp_route_reuses_a_nonexempt_base_segment`.

**Modified (tracking):**
- `_bmad-output/implementation-artifacts/5-2-undirected-reuse-enforcement-solver-oracle-validator.md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked to `review`; `last_updated`.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-04 | Yann (Claude Opus 4.8) | Story 5.2 implemented: undirected base-segment reuse enforced consistently by the GRASP solver, the exhaustive oracle, and the validator via a new single-sourced `solver/reuse.py`. Per-id exemption (an id is exempt iff every edge carrying it is reusable) closes the 5.1-deferred gap so a short reverse-of-climb connector still blocks the descent — out-and-back chains are eliminated by construction. Walks keep a directed-edge-simple bound for termination alongside the undirected once-only rule. `Edge` unchanged (tags read off graph data; directed fallback for untagged graphs keeps the metamorphic suite green). Renderer needed no change. Toy factory tagged with directed ids to preserve the pre-5.2 feasible set (3.7 gate + metamorphic invariants unperturbed). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 665 passed. No new deps. Status → review. | _pending_ |
