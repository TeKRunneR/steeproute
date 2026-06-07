# Story 6.1: Route-discovery bug fixes — junction split, SAC cap-aware contraction, undirected distinctness

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the solver to find legitimate routes that join a climb mid-way, to keep the easy majority of a climb that contains one over-cap pitch, and to treat opposite-direction reuse of the same trail as overlap,
so that known-good routes (like the Grenoble loop that triggered this correction) are actually returned and the top-N set is genuinely distinct.

## Acceptance Criteria

1. **Junction-aware climb splitting.** `contract_climbs` splits a climb at any interior node that is a real trail junction (incident, in the base graph, to a base segment outside the climb), emitting one super-edge per resulting sub-segment. Default on. The `base_segment_id` / `reusable` / `super_edge_to_base` tagging contract is preserved on the smaller super-edges. A route that boards or leaves a climb at such a junction is constructible.

2. **SAC cap-aware contraction.** At a `--difficulty-cap` below an embedded over-cap pitch, no above-cap super-edge survives and the under-cap terrain of the same climb stays routable. (Above-cap edges are dropped before `detect_climbs`, so a single over-cap pitch can no longer poison an otherwise-usable climb.)

3. **Undirected Jaccard distinctness.** `jaccard_distance` keys on the undirected `base_segment_id` (single-sourced via `solver.reuse`), not the directed `(node_u, node_v, key)`. Two routes traversing one physical trail in opposite directions report `jaccard_distance < 1` and are rejected under `--j-max 0`. The GRASP `TopNTracker`, the validator's set-level distinctness check, and the exhaustive oracle all use this same undirected keying.

4. **Three regression tests, each failing on pre-fix code**, assert AC1, AC2, AC3 respectively, reproducing the exact structural condition the synthetic suite missed.

5. **Quality gate + existing suites stay green.** The GRASP-vs-exhaustive gate (`test_solver_on_toy_graph.py`, ratio ≥ 0.80) and the existing contraction + distinctness unit tests pass — the oracle and solver share one feasible set (split + undirected distinctness).

6. **Purity preserved.** `contract_climbs`, the distinctness functions, and the query-side filter step do not mutate their inputs.

7. **Human-review checkpoint (`bmad-checkpoint-preview`)** on the real trigger area (repro command in Dev Notes) confirms all three fixes before the story is marked done: the target loop appears, and opposite-direction reuse is correctly treated as overlap under `--j-max 0`.

## Tasks / Subtasks

- [x] Junction-aware climb splitting (AC: #1, #6)
  - [x] In `pipeline/graph.py::contract_climbs`, split each climb at interior trail junctions (helpers `_split_climb_at_junctions`, `_is_junction`); emit one super-edge per sub-segment, preserving `base_segment_id` / `reusable` / `super_edge_to_base` tagging
  - [x] Decide split scope (all externally-connected junctions vs. routable-only) — chose split-at-all (see Completion Notes)
  - [x] `split_at_junctions` param on `contract_climbs` (default `True`); no CLI flag added (diagnostics-only toggle deemed unnecessary surface)
- [x] SAC cap-aware contraction (AC: #2, #6)
  - [x] In `cli/query.py`, run `filter_trails(prepared.graph, untagged_trails, difficulty_cap)` before `detect_climbs`
  - [x] Kept the solver's per-edge RCL SAC filter as cheap defense (recorded in Completion Notes)
- [x] Undirected Jaccard distinctness (AC: #3, #6)
  - [x] Re-key `solver/distinctness.py::_canonical_edge_set` on the undirected `base_segment_id`, sourced via `solver.reuse.base_segment_id_map`
  - [x] Thread the identity to GRASP's `TopNTracker`, the validator set-level check, and the oracle so all three share it
- [x] Tests (AC: #4, #5)
  - [x] Junction-split regression test (interior junction → mid-climb-turn route constructible) — `tests/unit/test_graph_contraction.py`
  - [x] SAC-cap regression test (over-cap pitch flanked by under-cap terrain → no above-cap super-edge; under-cap stays routable) — `tests/integration/test_route_discovery_fixes.py`
  - [x] Distinctness regression test (opposite-direction reuse → `jaccard_distance < 1`, rejected under `--j-max 0`) — `tests/integration/test_route_discovery_fixes.py`
  - [x] Confirmed GRASP-vs-exhaustive gate and existing contraction/distinctness unit tests pass (full suite: 678 passed)
- [x] Human-review checkpoint (AC: #7) — user ran the manual trigger-area check and confirmed it looks correct (2026-06-07)

### Review Findings

Adversarial review 2026-06-07 (Blind Hunter + Edge Case Hunter + Acceptance Auditor). Auditor: all 7 ACs satisfied, no scope creep. Findings below.

- [x] [Review][Patch] Guard `avg_gradient` against a zero-length split sub-segment — mirror `models.route_avg_gradient`'s `length_m > 0 else 0.0` [src/steeproute/pipeline/graph.py:176]
- [x] [Review][Patch] Pass `prepared.graph` (not the filtered `routable_graph`) to `output.render` — geometry is read-only and the full graph is strictly safer for FR28 failed-route rendering; render never iterates the graph, so zero downside [src/steeproute/cli/query.py:234]
- [x] [Review][Patch] Add an end-to-end test pinning undirected keying through `validate()` (two opposite-direction routes → set-level pairwise violation under `--j-max 0`), so a future drop of the `graph` arg to `validate_set` is caught [tests/integration/test_route_discovery_fixes.py]
- [x] [Review][Patch] Remove stray diagnostic scratch files before commit — `diagnosis_map.html`, `oracle_probe.py` (not part of the change) [repo root]
- [x] [Review][Defer] No "no routable terrain at this cap" message when `filter_trails` empties the graph (silent exit-0, zero reports) — deferred: Epic 7 (FR12 graceful degradation / FR22 run summary) owns empty-result messaging; not unique to this change

## Dev Notes

These three defects were the **stacked** causes of one known-good Grenoble loop never being returned. Items 1, 2, 4 of the correct-course brief; verified to make the route constructible on throwaway spikes. **Re-implement cleanly against the architecture and test conventions — do not merge the spike commits.** Spikes are reference-only: `spike/junction-aware-climbs` (junction split `3c9ea9b`, SAC cap pre-filter `501ad7a`); the distinctness defect was diagnosed only (no prototype).

**Junction split — `pipeline/graph.py::contract_climbs`.** Today each climb collapses to one atomic super-edge `climb.edges[0].node_u → climb.edges[-1].node_v`; interior nodes are absorbed and deleted, so a trail joining a climb partway up can't board it. Split at any interior node that is incident *in the base graph* to a base segment outside the climb (a real junction), emitting one super-edge per sub-segment. Each super-edge must keep the existing tagging discipline (module docstring at `pipeline/graph.py:24-44`): `base_segment_id` = frozenset of the undirected ids of its contracted base edges, `reusable=False`, and a `super_edge_to_base` entry. Cost on the spike was modest (+5.7% contracted edges, solve time ~flat) because splits happen only at genuine junctions. **Open tuning decision (yours):** split at *all* externally-connected junctions, or only "routable" ones (skip dead-end stubs) to limit fragmentation.

**SAC cap pre-filter — `cli/query.py`.** Root cause: `_aggregate_sac_scale` (`pipeline/graph.py:219`) takes the **max** SAC rank across a climb's edges, so two T5 edges poisoned a 4.3 km mostly-T2 climb at `--difficulty-cap t4`. Fix is query-side: call `filter_trails(prepared.graph, untagged_trails, difficulty_cap)` (`pipeline/osm.py:120`, pure, returns a new graph) **before** `detect_climbs` so above-cap pitches never weld into a climb. Query-side keeps the cache cap-independent (setup pins T6; cache key omits `difficulty_cap`) and keeps `--difficulty-cap` a fast knob. Note: `filter_trails` re-applies the trail-highway + untagged-policy filters too — idempotent on the already-setup-filtered graph, applying only the query-time cap.

**Undirected distinctness — `solver/distinctness.py`.** Root cause: `_canonical_edge_set` (`distinctness.py:31`) keys on the **directed** `(node_u, node_v, key)`, while reuse (Epic 5) keys on the **undirected** `base_segment_id`. Two routes walking the same trail in opposite directions look fully distinct (Jaccard distance 1.000). **Key interface subtlety:** `Edge` / `Solution` do **not** carry `base_segment_id` — it lives on the contracted graph's edge-data dicts. So distinctness must resolve each edge's base id through the graph, via `solver.reuse.base_segment_ids(data, u, v, k)` (the single source already used by GRASP at `grasp.py:145` and the validator at `validator.py:224`). This means `jaccard_distance` / `TopNTracker` need access to the graph or a precomputed edge→base-id map threaded in:
- `TopNTracker` is built at `grasp.py:137` (`TopNTracker(params.n, params.j_max)`) — it has the `ContractedGraph`.
- The validator's set-level check is at `validator.py:100` (`jaccard_distance(_as_solution(a), _as_solution(b))`) — it computes `non_exempt_base_segment_ids(graph)` already, so it has the graph.
- The oracle (`tests/integration/exhaustive_oracle.py`) must match.

All three must change together to stay on **one feasible set** — that's what keeps the Story 3.7 gate meaningful. Closes the distinctness item explicitly deferred in the Epic 5 proposal.

**Architecture conventions (must follow):** functions stay pure (no input-graph mutation — `pipeline/graph.py` and `solver/` modules already document this); named module-scope constants over inline magic numbers; `frozen=True, slots=True` dataclasses; no data shape passed as a loose `dict`. networkx edge data is read-only for downstream consumers.

**Human-review checkpoint repro** (run after regression tests are green): center `45.260,5.788`, radius `4`, `--cache-dir ./.trial-cache`, `--seed 44 --l-connector 50 --j-max 0 --difficulty-cap t4 --n 10 --iter-budget 200000`. Confirm the target loop appears (junction split + SAC cap) and opposite-direction trail reuse is treated as overlap (distinctness). On the spike, the loop appeared as routes 5 & 9.

### Project Structure Notes

- Code touched: `pipeline/graph.py` (junction split), `cli/query.py` (SAC pre-filter call sequence), `solver/distinctness.py` (undirected keying) + the GRASP/validator/oracle wiring needed to thread the base-id identity.
- No cache-boundary change — all three fixes are query-time (stages 8–9 / solve). No `pipeline_content_hash` change, no cache re-prepare. (Roads `6.2` and elevation smoothing `6.3` are the cache-moving changes.)
- Tests: `tests/unit/test_graph_contraction.py` (`_make_edge` helper, super-edge SAC tests at lines 404+), `tests/unit/test_distinctness.py`, `tests/integration/test_solver_on_toy_graph.py` (gate, `QUALITY_THRESHOLD = 0.80`), `tests/integration/exhaustive_oracle.py`. Add junction-split behavior tests; keep existing contraction tests green at the new default.

### References

- [Sprint change proposal — route discovery](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-07-route-discovery.md) §4B (B1–B3), §4C, §4D
- [Correct-course brief](_bmad-output/planning-artifacts/correct-course-brief-2026-06-05-route-discovery.md) Items 1, 2, 4
- [Epic 6 / Story 6.1](_bmad-output/planning-artifacts/epics.md) (lines 790–811)
- Code: [contract_climbs](src/steeproute/pipeline/graph.py:67), [_aggregate_sac_scale](src/steeproute/pipeline/graph.py:219), [_canonical_edge_set](src/steeproute/solver/distinctness.py:31), [reuse.base_segment_ids](src/steeproute/solver/reuse.py:54), [query call sequence](src/steeproute/cli/query.py:201)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Amelia / dev-story)

### Debug Log References

Full suite: `uv run pytest` → 678 passed, 2 deselected (`@pytest.mark.live`), ~113s. Lint (`ruff check`), format (`ruff format --check`, my files), and type-check (`basedpyright src/steeproute`) all clean.

### Completion Notes List

- **Junction split** (`pipeline/graph.py`): added `split_at_junctions: bool = True` to `contract_climbs` + helpers `_split_climb_at_junctions` / `_is_junction`. A climb splits at any interior node incident (in the base graph) to a base segment whose **undirected** id is outside the climb's own set — so the climb's reverse-direction edges (same undirected id) never trigger a split, only a genuinely different trail does. **Split scope decision: split at all externally-connected junctions** (simplest; matches spike evidence of +5.7% contracted edges / flat solve time). Did **not** add a CLI flag — the diagnostics-only toggle is exposed as the `split_at_junctions` param (default on), keeping the CLI surface lean. Each sub-segment becomes its own super-edge with preserved `base_segment_id`/`reusable`/`super_edge_to_base` tagging; back-mapping stays injective (existing property test green).
- **SAC cap pre-filter** (`cli/query.py`): `filter_trails(prepared.graph, untagged_trails, difficulty_cap)` runs before `detect_climbs`; the filtered graph feeds detection, contraction, and `output.render` alike (above-cap edges are in no route, so excluding them from report geometry is consistent). Query-side, so the cache stays difficulty-independent. **Decision: kept** the solver's per-edge RCL SAC filter as cheap defense (it now never triggers on real GRASP output, but costs nothing and documents intent).
- **Undirected distinctness**: added `solver.reuse.base_segment_id_map(graph)` as the single source of the directed→undirected projection. `solver/distinctness.py` (`_canonical_edge_set`, `jaccard_distance`, `TopNTracker`) gained an optional `segment_map`; `None` preserves the pre-6.1 directed behaviour (keeps all existing unit tests green). GRASP, the exhaustive oracle, and the validator's `validate_set` all build the map from their graph and pass it, so the three stay on one feasible/distinct set (the Story 3.7 gate remains apples-to-apples — confirmed passing). `Edge`/`Solution` deliberately stay metric-only; the graph-derived map resolves identity at the distinctness boundary.
- **No cache-boundary change** — all three fixes are query-time. The toy-graph fixtures key `base_segment_id` on the directed id, so the undirected change is a no-op there (gate + metamorphic unperturbed).
- **AC7 (human-review checkpoint) — DONE.** User ran the manual trigger-area check (repro command in Dev Notes) and confirmed the result looks correct (target loop appears; opposite-direction reuse treated as overlap). Confirmed 2026-06-07.
- Out of scope / untouched: `tests/unit/test_dem_download.py` has pre-existing format drift (flagged by `ruff format --check`) — not part of this story, left alone.

### File List

- `src/steeproute/pipeline/graph.py` — junction-aware climb splitting (`split_at_junctions`, `_split_climb_at_junctions`, `_is_junction`)
- `src/steeproute/cli/query.py` — SAC cap pre-filter before climb detection; filtered graph threaded to detect/contract/render
- `src/steeproute/solver/reuse.py` — new `base_segment_id_map`
- `src/steeproute/solver/distinctness.py` — optional `segment_map` on `_canonical_edge_set` / `jaccard_distance` / `TopNTracker`
- `src/steeproute/solver/grasp.py` — build + pass the undirected segment map to `TopNTracker`
- `src/steeproute/validator.py` — `validate_set` keys set-level Jaccard on the undirected map
- `tests/integration/exhaustive_oracle.py` — oracle `TopNTracker` uses the undirected segment map
- `tests/unit/test_graph_contraction.py` — junction-split regression + opt-out + no-over-split tests
- `tests/integration/test_route_discovery_fixes.py` — SAC-cap + undirected-distinctness regression tests (new)
- `tests/integration/test_graph_contraction_fixture.py` — relaxed super-edge count assertion (`>= len(climbs)`) for split

## Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-06-07 | 0.1 | Story drafted (create-story) |
| 2026-06-07 | 1.0 | Implemented junction split + SAC cap pre-filter + undirected distinctness; 3 regression tests; full suite green (678 passed). AC7 human-review checkpoint pending user. Base commit `faf515e`. |
| 2026-06-07 | 1.1 | AC7 manual checkpoint confirmed by user. Adversarial code review (3 layers): all ACs satisfied, no High/Med defects. Applied 4 patches — `avg_gradient` zero-length guard, `output.render` uses full `prepared.graph`, end-to-end undirected-wiring test, removed scratch files. 1 deferred to Epic 7. Full suite 679 passed; lint/format/type-check clean. Status → done. |
