# Story 5.1: Base-segment identity and connector revival at contraction

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want `contract_climbs` to retain all connectors and tag every contracted edge with an undirected base-segment identity and a reuse-exemption flag,
so that the solver, oracle, and validator (Story 5.2) can enforce undirected once-only reuse with a short-connector tolerance off shared, single-sourced edge data.

## Acceptance Criteria

1. **All connectors are carried over — no length-based drop.** `contract_climbs` no longer drops sub-`l_connector` connectors. Every non-climb base edge survives into the contracted graph (orphan-prune-after-drop step is gone). Existing aggregation/super-edge/key-allocation behaviour is unchanged.

2. **Every contracted edge carries a `base_segment_id` and a `reusable` flag.**
   - A **connector** carries its own undirected `base_segment_id` and `reusable = (length_m < l_connector)`.
   - A **super-edge** carries the **set** of `base_segment_id`s of the base edges it contracts (sourced via `super_edge_to_base`) and `reusable = False`.

3. **The base-segment identity is identical for a segment and its reverse.** The id scheme makes a forward edge and its reverse-direction counterpart resolve to the same `base_segment_id`, and makes a climb super-edge share at least one `base_segment_id` with the reverse-direction connectors of the same trail. (Id scheme is the implementer's choice — see Dev Notes; the binding contract is direction-invariance.)

4. **`models.py` docstrings reflect the realized FR5 semantics.** The `ContractedGraph` / edge-attribute docstring documents the new `base_segment_id` + `reusable` attributes and the super-edge → base-segment-id-set mapping; the stale "sub-`l_connector` connectors dropped" line is corrected. `SolverParams.l_connector` docstring changes from "shorter connectors drop out at the contraction step" to reuse-exemption-threshold wording.

5. **`contract_climbs` remains pure.** Input `base_graph` is never mutated (topology or any edge-data dict) — the existing purity property tests still hold.

6. **`tests/unit/test_graph_contraction.py` is updated to the new behaviour:** short connectors are retained and tagged `reusable=True`; long connectors and super-edges are tagged `reusable=False`; a climb super-edge shares at least one `base_segment_id` with the reverse-direction connectors of the same trail; `base_segment_id` round-trips. The old "short connector dropped" / orphan-prune-after-drop assertions are inverted or removed.

7. The four CI gates pass on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest` — with no new deps and coverage floors held.

## Tasks / Subtasks

- [x] Task 1: Stop dropping connectors in `contract_climbs`; carry over **all** non-climb edges and remove the orphan-prune-after-drop step. (AC: #1, #5)
- [x] Task 2: Choose and implement the undirected `base_segment_id` scheme (see Dev Notes); tag every contracted edge — connectors with their own id + `reusable` flag, super-edges with the id-set from `super_edge_to_base` + `reusable=False`. (AC: #2, #3)
- [x] Task 3: Update `models.py` docstrings — `ContractedGraph`/edge-attribute contract + `SolverParams.l_connector`. (AC: #4)
- [x] Task 4: Rework `tests/unit/test_graph_contraction.py` — invert the drop/orphan assertions, add `reusable`-tagging + `base_segment_id` round-trip + forward/reverse-share coverage. Update the `hypothesis` strategy if connector-drop assumptions leak into it. (AC: #6)
- [x] Task 5: Run all four gates + full suite on Windows. (AC: #7)

### Review Findings

_Code review 2026-06-04 (Blind Hunter + Edge Case Hunter + Acceptance Auditor). All 7 ACs verified PASS; gates trusted (658 passed). Findings below are forward-looking — none are defects in 5.1's contraction logic, but one materially affects whether Story 5.2 can meet its success criterion._

- [x] [Review][Decision→Defer to 5.2] Short reverse-of-climb connectors are tagged `reusable=True`, which defeats out-and-back prevention for short-edge climbs — **Deferred to Story 5.2** (Yann's call, 2026-06-04): Story 5.2 owns the reuse-enforcement semantics, so per-id exemption is the natural fix at the point the rule is wired and testable against the fixture. When a climb's individual base edges are each `< l_connector` (common: a 300 m+ climb made of 50–200 m OSM segments), their reverse-direction connectors are tagged `reusable=True` by the length-only rule, yet their `base_segment_id` is also in the climb super-edge's id set. Under Story 5.2's stated rule ("reusable edges never block and are never recorded"), the solver would record the super-edge's ids on ascent but then be *allowed* to descend the reusable reverse connectors — so the degenerate out-and-back survives for this class of climb. Root cause: `reusable` is per-edge but the once-only identity is per-id; a single physical id is simultaneously exempt (via the short connector) and once-only (via the super-edge). The proposal's literal contract (`reusable = length_m < l_connector`) is implemented faithfully, but the contract itself has this gap. **Recommended fix for 5.2:** evaluate exemption per-id — an id is exempt only if every edge carrying it is reusable (so a short connector sharing an id with a non-reusable super-edge is *not* exempt). Alternative: tag `reusable = (length_m < l_connector) AND id ∉ any super-edge id set` back in 5.1's `contract_climbs`. [src/steeproute/pipeline/graph.py contract_climbs]

- [x] [Review][Defer] base-segment identity is key-dependent — anomalous parallel ways can under/over-merge [src/steeproute/pipeline/graph.py:_base_segment_id] — deferred, pre-existing: the undirected id is `(min,max,key)`, so the forward/reverse collision relies on osmnx assigning the same key to both directions of one way (true for normal two-way edges). Mismatched-key parallel ways under-merge (reuse becomes a no-op for that segment); a same-key opposite-direction distinct way would over-merge. Both are vanishingly rare on trail data and acknowledged in the docstring. Validate empirically on the real Grenoble fixture when Story 5.2 wires the rule.

- [x] [Review][Defer] Uncommitted correct-course doc edits (prd/architecture/epics) sit in the same working tree as 5.1 [planning-artifacts/] — deferred, pre-existing: `prd.md`/`architecture.md`/`epics.md` carry the 2026-06-03 undirected-reuse correct-course edits (FR5 reword, stage-9, constraint table, Epic 5 insertion + renumber), uncommitted since before 5.1 dev. They are correctly *absent* from 5.1's File List (5.1 did not author them). Commit them separately from the 5.1 code so the story commit matches its declared File List.

## Dev Notes

- **Scope is the contraction stage only (B1 + B2 + tests C/3.3).** This is the foundation story: it produces the tagged edge data that Story 5.2 consumes to flip the reuse rule. **Do not touch `solver/grasp.py`, `validator.py`, or `exhaustive_oracle.py`** — that behaviour change is Story 5.2. Keep the feasible set unchanged in *behaviour* here; you are only adding attributes and reviving connectors.

- **Choosing the `base_segment_id` scheme (the one genuinely new design decision).** The binding contract (AC #3) is: *identical for a segment and its reverse*. Two candidates, with the tradeoff:
  - **Canonical sorted node-pair + key** — `(min(node_u, node_v), max(node_u, node_v), key)`. **Recommended.** Forward `(u,v,0)` and reverse `(v,u,0)` collapse to the same id, and it is per-edge precise. Caveat to verify: the reverse-direction edge must reuse the same `key` (osmnx assigns key 0 to both directions of a simple two-way edge; confirm on the fixture). This is the natural extension of the existing canonical `(node_u, node_v, key)` edge identity already documented on `Edge` (`models.py:72-74`).
  - **`osm_way_id`** — direction-invariant (osmnx shares `osmid` across both directed edges) but **too coarse**: a single OSM way spans many edges between intersections, so keying on it would over-block (forbid an entire way after one edge of it is used). Rejected unless you add intra-way disambiguation.
  - The proposal explicitly leaves this open ("Not pre-decided here", sprint-change-proposal §2). Pick one, document it in the `contract_climbs` docstring, and make the test assert the direction-invariance property rather than the concrete id value.

- **Attribute shape for uniform downstream handling.** Story 5.2's solver/validator will ask "is any non-exempt base-segment id of this edge already used?" — that reads most cleanly if **every** edge exposes its base-segment ids as the same type. Consider storing `base_segment_id` as a `frozenset` on every contracted edge (a connector → a one-element set; a super-edge → its multi-element set), so 5.2 iterates uniformly. This is a suggestion, not a mandate — if you store a scalar on connectors and a set on super-edges, document it so 5.2 doesn't trip on the type split.

- **`reusable` is connector-only and short-only.** `reusable = True` **iff** the edge is a connector **and** `length_m < l_connector`. Super-edges and long connectors are `reusable = False`. This is the inversion of the old drop rule: what used to be *dropped* (`length_m < l_connector`) is now *kept and flagged reusable*.

- **Purity is already pinned — keep it.** `test_contract_climbs_does_not_mutate_input_graph` and the `hypothesis` purity property (`test_contract_climbs_is_pure_under_random_inputs`) snapshot every edge-data dict. The new tags go on the **contracted** graph's edges (which `contract_climbs` owns), never written back to `base_graph` — the `**data` unpack already creates fresh outer dicts (`graph.py:99-106`). Don't regress this.

- **Architecture/PRD/epics doc text is already synced.** The 2026-06-03 correct-course front-loaded the planning-artifact edits (architecture stage 9 `architecture.md:254` and constraint table `:517` already describe undirected base-segment reuse + short-connector exemption; epics FR-map `epics.md:161`). The PRD/architecture/epics *prose* sync and CLI help-string are **Story 5.3's** verification pass — not this story. This story's only doc work is the `models.py` docstrings (AC #4), which are source.

### Project Structure Notes

- **Modify (source):** `src/steeproute/pipeline/graph.py` (`contract_climbs` + drop the orphan-prune-after-drop path), `src/steeproute/models.py` (docstrings only — no field changes to dataclasses).
- **Modify (tests):** `tests/unit/test_graph_contraction.py` (invert drop assertions, add tagging coverage). Check `tests/integration/test_graph_contraction_fixture.py` for any "connector dropped" assumption and adjust if present.
- **Do NOT touch:** `solver/grasp.py`, `validator.py`, `tests/integration/exhaustive_oracle.py`, PRD/architecture/epics prose (Stories 5.2 / 5.3).
- **Reuse, don't reinvent:** the existing `_make_edge` / `_add_edge_from` / `_climb_from_edges` helpers and the `_chain_climb_strategy` `hypothesis` generator in the test file are the building blocks — extend them, don't replace.

### Testing standards summary

- Synthetic-graph unit tests in `tests/unit/`; naming `test_<unit>_<scenario>` (Architecture §"Test organization"). No `pytest.skip`/`xfail` (Architecture §Cat 11c — pass-required).
- New assertions cover added branches (connector retention + tagging), so coverage floors should hold or rise; no new source branches go untested.
- Regression proof: the rest of the suite stays green. A *value* shift in solver/validator output is **not** expected from this story (no behaviour change to reuse yet); if one appears, it's a bug — investigate before proceeding.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 5.1"](../planning-artifacts/epics.md) — BDD acceptance criteria
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-03-undirected-segment-reuse.md §2, §4B (B1/B2), §4C (tests 3.3), §5](../planning-artifacts/sprint-change-proposal-2026-06-03-undirected-segment-reuse.md) — canonical handoff: 5.1 owns B1/B2 + tests C/3.3; base-segment-id scheme left to implementer; direction-invariance is the binding contract
- [Source: src/steeproute/pipeline/graph.py:45-138](../../src/steeproute/pipeline/graph.py) — `contract_climbs`: connector drop at `:97-98`, orphan prune at `:136`, super-edge mapping at `:111-131`
- [Source: src/steeproute/models.py:119-138](../../src/steeproute/models.py) — `ContractedGraph` docstring ("sub-`l_connector` connectors dropped" — to correct); `Edge` canonical `(node_u, node_v, key)` identity at `:72-74`; `SolverParams.l_connector` at `:158-159`
- [Source: tests/unit/test_graph_contraction.py:161-296](../../tests/unit/test_graph_contraction.py) — drop/orphan tests to invert; `_make_edge`/`_climb_from_edges`/`_chain_climb_strategy` helpers to reuse
- [Source: _bmad-output/planning-artifacts/architecture.md:254,260-266,517](../planning-artifacts/architecture.md) — stage-9 description (already synced), edge-attribute contract (3c), constraint table (already synced)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps (`frozenset` is stdlib).

**Final gate pass (all green):**

```
uv run ruff check        → All checks passed!
uv run ruff format --check → 73 files already formatted
uv run basedpyright      → 0 errors, 0 warnings, 0 notes
uv run pytest -q         → 658 passed, 1 deselected in ~107 s
                           (655 baseline + 3 net new contraction tests)
contraction unit+integ   → 21 passed in ~1.9 s
```

### Completion Notes List

**Scope held to the contraction stage (B1 + B2 + tests C/3.3).** No change to `solver/grasp.py`, `validator.py`, or `exhaustive_oracle.py` — the reuse-rule flip is Story 5.2. This story only revives connectors and adds the `base_segment_id` + `reusable` tags they will consume.

**`base_segment_id` scheme = canonical sorted node-pair + key** (the recommended option from the story Dev Notes), implemented as `_base_segment_id(u, v, k) -> (min, max, k)`. A forward edge and its reverse collapse to one id; a climb super-edge shares ≥1 id with the reverse-direction connectors of the same trail (verified by `test_super_edge_shares_base_segment_id_with_reverse_connector`). Rejected `osm_way_id` (too coarse — a way spans many edges → would over-block).

**`base_segment_id` is a `frozenset` on *every* edge** (connector → one-element set; super-edge → the set of its contracted edges' ids), so Story 5.2 can test "any non-exempt id already used?" uniformly without branching on edge kind. Documented in both the `graph.py` and `models.py` docstrings.

**`reusable` is strict and connector-only:** `True` iff a connector with `length_m < l_connector`; `False` for long connectors and every super-edge. This is the exact inversion of the old drop rule — what used to be dropped is now kept and flagged reusable.

**Orphan-prune removed, not just bypassed.** With every connector retained, no node can end up degree-0 (a node enters the contracted graph only as an endpoint of an added edge), so `_drop_orphan_nodes` became dead code and was deleted. `test_no_orphan_prune_short_connector_node_retained` pins that a node previously pruned now survives.

**Purity preserved.** The new tags are written only onto the fresh contracted edge dicts (`**data` already copies the outer dict); `base_graph` is never touched. The existing snapshot + `hypothesis` purity tests still pass unchanged.

**No solver value-shift despite the larger graph.** Reviving short connectors expands the contracted graph the GRASP fixture tests run on, but those tests assert structural properties (≤N, edge-simple walk, slope floor, SAC cap, Jaccard), not exact output — all still pass. The metamorphic / oracle / toy-graph suites build `ContractedGraph` directly, bypassing `contract_climbs`, so they are untouched.

**AC walkthrough:**
1. AC #1 — length filter + orphan-prune removed; all connectors carried over; aggregation/super-edge/key-allocation unchanged. ✅
2. AC #2 — every contracted edge carries `base_segment_id` (frozenset) + `reusable`; connector = own id, super-edge = set + `reusable=False`. ✅
3. AC #3 — direction-invariant id; forward/reverse connectors share; super-edge shares ≥1 id with reverse connectors. ✅
4. AC #4 — `ContractedGraph` docstring corrected (no drop) + documents the two new attributes; `SolverParams.l_connector` reworded to reuse-exemption semantics. ✅
5. AC #5 — purity snapshot + hypothesis property tests green. ✅
6. AC #6 — drop/orphan unit tests inverted; tagging + id-contract tests added; fixture-integration docstrings de-stale'd. ✅
7. AC #7 — ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 658 passed ✅; no new deps; coverage floor (`fail_under = 0`) holds. ✅

### File List

**Modified (source):**
- `src/steeproute/pipeline/graph.py` — `contract_climbs`: drop the length-based connector cut + orphan-prune; tag every connector with `base_segment_id` (own id) + `reusable=(length_m < l_connector)`; tag every super-edge with the id-set of its contracted edges + `reusable=False`. Added `_base_segment_id` helper; removed dead `_drop_orphan_nodes`. Module + function docstrings updated.
- `src/steeproute/models.py` — `ContractedGraph` docstring: corrected "connectors dropped" → "all retained", documented `base_segment_id` + `reusable`; `SolverParams.l_connector` docstring reworded to reuse-exemption threshold.

**Modified (tests):**
- `tests/unit/test_graph_contraction.py` — inverted the short-connector-drop / orphan-prune tests to retention + `reusable` tagging; added `test_forward_and_reverse_connectors_share_base_segment_id`, `test_super_edge_base_segment_id_is_set_of_contracted_edge_ids`, `test_super_edge_shares_base_segment_id_with_reverse_connector`; de-stale'd strategy + purity-test comments.
- `tests/integration/test_graph_contraction_fixture.py` — docstring updates only (the `< base edges` assertion holds via climb collapse; connectors no longer dropped).

**Modified (tracking):**
- `_bmad-output/implementation-artifacts/5-1-base-segment-identity-and-connector-revival-at-contraction.md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked to `review`; `last_updated`.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-04 | Yann (Claude Opus 4.8) | Story 5.1 implemented: `contract_climbs` now retains all connectors (no length-based drop, orphan-prune removed) and tags every contracted edge with an undirected `base_segment_id` (canonical sorted node-pair + key, stored as a `frozenset` on every edge) and a `reusable` flag (`True` only for connectors `< l_connector`); super-edges carry the id-set of the edges they contract and `reusable=False`. `models.py` docstrings synced. Foundation for Story 5.2's undirected reuse rule — no solver/validator behaviour changed yet. All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 658 passed. No new deps. Status → review. | _pending_ |
