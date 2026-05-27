# Story 3.3: Pipeline stage 9 — contracted climb-graph construction

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want `pipeline/graph.py::contract_climbs(base_graph, climbs, l_connector) -> ContractedGraph` to fold each Climb into a single super-edge with aggregated metrics, drop sub-`l_connector` connector edges, and maintain a super-edge → base-edges back-mapping,
So that the GRASP solver (Story 3.6) operates on the right abstraction and FR5 (L_connector) has its enforcement home.

## Acceptance Criteria

1. `src/steeproute/pipeline/graph.py::contract_climbs(base_graph: nx.MultiDiGraph, climbs: list[Climb], l_connector: float) -> ContractedGraph` is implemented as a pure function (input `base_graph` and its edge-data dicts never mutated). For each `Climb`, one super-edge from `climb.edges[0].node_u` to `climb.edges[-1].node_v` is added to the contracted graph carrying the stage-7 attribute schema (`length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`) with metrics aggregated from the underlying base edges (`sum` for the three metrics, `(d_plus + d_minus) / length` for `avg_gradient`, the max-difficulty SAC rank across the climb for `sac_scale`). Connector (non-climb) edges with `length_m >= l_connector` are carried through unchanged; shorter ones are dropped. Nodes left with degree 0 after the drop are pruned. The returned `ContractedGraph.super_edge_to_base` maps every super-edge's `(node_u, node_v, key)` triple to the `tuple[Edge, ...]` of base edges it contracts.

2. `tests/unit/test_graph_contraction.py` exercises hand-built `MultiDiGraph`s (no fixture I/O) and asserts each of these cases produces the documented outcome:
   - one climb of N qualifying edges → contracted graph has one super-edge `(climb_start, climb_end, key)` whose `length_m`, `d_plus_m`, `d_minus_m` exactly equal the summed underlying metrics within `math.isclose(abs_tol=1e-9)`, and `super_edge_to_base[(climb_start, climb_end, key)]` round-trips to the climb's `edges` tuple in order;
   - empty `climbs` list with a base graph of two connectors, one `length_m=300` and one `length_m=100`, and `l_connector=200` → contracted graph keeps the first, drops the second; `super_edge_to_base` is empty;
   - a connector at exactly `l_connector` is admitted (the threshold is inclusive, `>=`);
   - a base graph with bidirectional edges `(u→v)` and `(v→u)` where only the `u→v` direction is a climb → contracted graph contains the `u→v` super-edge plus the `v→u` direction as either a connector edge (if its summed length `>= l_connector`) or dropped (if shorter); the reverse direction is never automatically mapped as a super-edge;
   - after sub-`l_connector` connectors are dropped, any node whose remaining degree is 0 is absent from the contracted graph.

3. `tests/integration/test_graph_contraction_fixture.py` loads the committed Grenoble Le Sappey fixture via the same `osm_load` monkeypatch pattern used by `test_climb_detection_fixture.py`, runs `run_setup_stages` → `detect_climbs(graph, theta=0.20, min_climb_ground_length=300)` → `contract_climbs(graph, climbs, l_connector=200)`, and asserts: contracted graph has strictly fewer edges than `base_graph` (climbs collapse + connectors prune); for every super-edge, the back-mapped base-edge sequence's `sum(e.length_m)` equals the super-edge's stored `length_m` and `sum(e.d_plus_m)` equals the stored `d_plus_m` within `math.isclose(abs_tol=1e-9)`.

4. A `hypothesis` property test (in `tests/unit/test_graph_contraction.py`) generates small random `(base_graph, climbs)` pairs (programmatic factory; reuse or duplicate the chain-graph helper from `test_climb_detection.py`) and asserts: (a) `super_edge_to_base` is injective on base-edge identity — no `(node_u, node_v, key)` of a base edge appears in two distinct super-edge mappings; (b) `contract_climbs` is pure — the input `base_graph`'s node count, edge count, and a deep-copy snapshot of every edge-data dict are unchanged after the call.

5. All four CI gates green on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0 errors / 0 warnings / 0 notes), `uv run pytest --cov`. `pipeline/graph.py` reaches the 95 % pure-logic coverage floor (Architecture §Cat 11e). No new runtime or dev deps.

## Tasks / Subtasks

- [x] Task 1: Create `src/steeproute/pipeline/graph.py` with `contract_climbs` + private helpers. Match `pipeline/climbs.py`'s pragma comment + import + purity discipline. (AC: #1)
- [x] Task 2: Write `tests/unit/test_graph_contraction.py` covering the AC #2 synthetic scenarios on hand-built `MultiDiGraph`s. (AC: #2)
- [x] Task 3: Add the AC #4 `hypothesis` property test for back-mapping injectivity + purity to the same unit-test file. (AC: #4)
- [x] Task 4: Write `tests/integration/test_graph_contraction_fixture.py` — reuse the `osm_load` monkeypatch + `run_setup_stages` + `detect_climbs` pattern from `test_climb_detection_fixture.py`, then chain `contract_climbs`. (AC: #3)
- [x] Task 5: Verify CI gates and coverage; address any drift. (AC: #5)

### Review Findings

- [x] [Review][Patch] Tautological integration assertion: super-edge metrics compared against same source that fed them — `base_edges = contracted.super_edge_to_base[super_id]` IS `climb.edges`; the super-edge's `data["length_m"]` is `sum(e.length_m for e in climb.edges)`, and the test recomputes the same sum over the same iterable. The substantive cross-check (going back to `base_graph[u][v][k]["length_m"]` per base edge and summing those) is the only thing that catches a bug where `Edge.length_m` carries stale metrics or where stage 8 emits an edge with metrics that diverge from the graph. [tests/integration/test_graph_contraction_fixture.py:88-128]
- [x] [Review][Patch] Misleading comment about edge-data aliasing in `contract_climbs` — the block comment at lines 99-104 claims "the contracted graph holds its own attribute dict — but mutations on `base_graph[u][v][k]` after this call would not propagate here either way". Verified that nested mutable values (`vertices_resampled: list`, `geometry: shapely.LineString`, list-valued `highway` / `osm_way_id`) ARE shared by reference even though the outer dict is freshly built by `**data` unpacking. Practical risk is low (downstream consumers shouldn't mutate edge data and architecture forbids it) but the reassuring tone of the comment misleads future readers about what the aliasing actually guarantees. Fix is comment-only: honestly describe the shallow-copy semantics. [src/steeproute/pipeline/graph.py:99-104]
- [x] [Review][Patch] `_next_key_for` docstring claims "smallest non-conflicting key" but returns `max + 1` — if existing keys are `[0, 2]`, "smallest non-conflicting" is `1`, but the function returns `3`. Behavior is correct for collision-freeness; the docstring is the wrong description and a future reader fixing the perceived discrepancy would break determinism. Rename / rephrase to "first key above `max(existing)`". [src/steeproute/pipeline/graph.py:139-152]
- [x] [Review][Patch] Hypothesis `_chain_climb_strategy` never exercises single-edge climbs (`min_value=2`) — the smallest valid `Climb` has one edge, and that is the boundary case most likely to surface an off-by-one in `climb.edges[0].node_u` / `climb.edges[-1].node_v`. Lowering the strategy's `min_value` to 1 broadens injectivity + purity coverage at zero cost. [tests/unit/test_graph_contraction.py:_chain_climb_strategy]
- [x] [Review][Patch] `test_super_edge_key_avoids_collision_with_existing_connector` only asserts the super-edge's key `!= 0` — but the documented policy is `max(existing) + 1 = 1`. A regression that returned `2` (off-by-one) or any other non-zero value would still pass. Strengthen to `super_keys_for_zero_two[0] == 1` to pin the actual allocation rule. [tests/unit/test_graph_contraction.py:631-655]
- [x] [Review][Patch] No explicit assertion that climb-internal nodes are absent from the contracted graph — `test_single_climb_collapses_into_one_super_edge` verifies the contracted graph has exactly one edge `(0, 4, *)` and the super-edge attribute schema, but doesn't pin that intermediate nodes 1, 2, 3 are not in `contracted.graph.nodes`. A regression that added stray internal nodes (e.g., via a mis-typed `add_node` call) would surface only as a downstream solver oddity. Add `assert 1 not in contracted.graph.nodes` (etc.) to the existing test. [tests/unit/test_graph_contraction.py:79-109]
- [x] [Review][Patch] AC #2 bidirectional case only exercises "reverse direction retained as connector" branch, never "reverse direction dropped if shorter" — `test_bidirectional_base_graph_one_direction_climb` uses 200 m reverse-direction edges (at-threshold → retained). The AC text reads "either a connector edge (if its summed length `>= l_connector`) or dropped (if shorter)". Add a small parametrize variant (or a sibling test) with reverse-direction edges below `l_connector` to pin the dropped branch. [tests/unit/test_graph_contraction.py:185-222]
- [x] [Review][Defer] `_aggregate_sac_scale` / `_SAC_RANK_TO_NAME` brittle on `SAC_SCALE_RANK` shape changes — the rank-0 sentinel is implicit (relies on no SAC name ever being assigned rank 0) and the inverse map silently collapses if two SAC names ever share a rank. Architecture-level invariant of `pipeline.osm.SAC_SCALE_RANK`; document the dependency and add a startup assertion if `SAC_SCALE_RANK` ever becomes mutable. [src/steeproute/pipeline/graph.py:42, 164-182]
- [x] [Review][Defer] `_drop_orphan_nodes` is structurally unreachable in the build-from-scratch flow — `nx.MultiDiGraph().add_edge(u, v, ...)` is the only node-insertion path; nodes with no incident edges never get added. The helper is defensive parity with `pipeline.__init__._drop_orphan_nodes` per Dev Notes, but contributes the 1 missed coverage line on `graph.py`. Spec-vs-architecture tension (the spec mandated mirroring; the architecture makes it dead code) — leave for a future refactor that revisits the orphan-prune convention across `pipeline/`. [src/steeproute/pipeline/graph.py:185-196]
- [x] [Review][Defer] `_aggregate_sac_scale` semantically conflates `None` (untagged) and unrecognized SAC value — `max_sac_rank(None)` and `max_sac_rank(<list with unknown element>)` both return `None`, and the helper treats them identically (rank 0, ignored). Story 3.1's `Edge.sac_scale: str | None` annotation says `None` means untagged, but the real OSM data can carry list-valued or unrecognized strings that hit the same branch. Defensive choice acknowledged in Completion Notes #1 but worth flagging if a future story tightens the SAC contract. [src/steeproute/pipeline/graph.py:175-182]
- [x] [Review][Defer] Connector-prune branch not separately asserted in integration test — AC #3 asserts the contracted graph has strictly fewer edges than the base graph, which is satisfied by the union of climb-collapse + connector-prune. The unit tests cover each branch individually but the integration test treats them as a black box. Adding `assert any(data["length_m"] < _L_CONNECTOR for ... in base_graph.edges(data=True))` would pin the fixture genuinely exercises the prune path. [tests/integration/test_graph_contraction_fixture.py:79-100]
- [x] [Review][Defer] Synthetic unit tests don't pin `geometry` / `vertices_resampled` / `highway` / `osm_way_id` carry-over on connectors — `_add_edge_from` writes only the five stage-7 numeric attributes + `sac_scale`. The `pipeline/graph.py:62` docstring promises "entire edge-data dict, including `geometry`, `vertices_resampled`, `highway`, `osm_way_id`" is preserved. A regression that dropped one of those attrs would only surface in Story 3.10's renderer. Requires synthetic fixture data with shapely / list-valued attrs to fix properly. [tests/unit/test_graph_contraction.py:_add_edge_from + _make_edge]
- [x] [Review][Defer] Hypothesis strategy never generates "two climbs share endpoints" — `_chain_climb_strategy` builds chains over strictly disjoint node-id ranges, so `_next_key_for` is never exercised with a non-zero return from the property tests. `test_two_climbs_share_endpoints_get_distinct_super_edge_keys` covers it once with a hand-built case; a stronger strategy would broaden randomized coverage. [tests/unit/test_graph_contraction.py:_chain_climb_strategy]
- [x] [Review][Defer] Integration test executes fixture's `regenerate.py` via `spec.loader.exec_module` at module-load time — pre-existing pattern from Story 3.2's `test_climb_detection_fixture.py`. A future `regenerate.py` with side effects (network fetch, file writes) would trip on test import. Address with a fixture-architecture pass that hoists constants into a shared `tests/fixtures/grenoble_small/_constants.py` (or JSON sidecar) instead of executing `regenerate.py`. [tests/integration/test_graph_contraction_fixture.py:48-58]

## Dev Notes

- **Super-edge attribute schema (AC #1).** The contracted MultiDiGraph's super-edge data dict carries the same numeric keys as the stage-7 contract — `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale` — so downstream code (`solver/grasp.py`, `validator.py`) reads a super-edge identically to a connector. Aggregates: `length_m = sum(e.length_m)`, `d_plus_m = sum(e.d_plus_m)`, `d_minus_m = sum(e.d_minus_m)`, `avg_gradient = (d_plus_m + d_minus_m) / length_m` (stage 7's absolute-churn definition), `sac_scale = ` highest-rank SAC value across the climb's edges per `pipeline.osm.SAC_SCALE_RANK` (used for difficulty-cap validation; `None` entries treated as the lowest rank, i.e. ignored). Don't carry `geometry` or `vertices_resampled` on super-edges — those stay on the base edges reachable through `super_edge_to_base`; the solver / validator never reads them off a super-edge directly.

- **Super-edge identity is dict-membership, not a flag.** A `(node_u, node_v, key)` is a super-edge iff it is a key of `ContractedGraph.super_edge_to_base`; everything else in `ContractedGraph.graph` is a connector. Don't introduce a `is_super_edge` edge attribute — it would duplicate the back-mapping's source of truth.

- **Super-edge key allocation.** When the contracted graph already contains parallel edges between `(climb_start, climb_end)` (from other climbs landing on the same endpoints, or from surviving connectors), assign the new super-edge the smallest non-conflicting `key`. The straightforward `(max existing key for (u, v)) + 1` strategy is fine — networkx's `add_edge(u, v, key=...)` accepts arbitrary ints, and the canonical edge ordering downstream (`sorted((u, v, key))`) is deterministic regardless.

- **Connectors pass through unchanged.** A connector edge `(u, v, k)` is any base edge whose `(u, v, k)` triple does not appear in any climb's `edges`. Connectors with `length_m >= l_connector` (inclusive) are carried into the contracted graph with their entire base data dict — `geometry`, `vertices_resampled`, `highway`, `osm_way_id`, `sac_scale`, plus the four stage-7 metrics. The `>=` cut (not `>`) matches the `_drop_short_edges` pattern's documented inclusiveness.

- **Bidirectionality (AC #2 fourth case).** Trails appear as both `(u→v)` and `(v→u)` directed edges in the OSM-derived `MultiDiGraph`. `detect_climbs` walks directionally — it only emits climbs whose cumulative `d_plus_m / length_m` is ≥ θ in the traversed direction. The reverse direction (mostly downhill on the same trail) is never a climb from Story 3.2's algorithm. `contract_climbs` therefore preserves the reverse-direction edges as ordinary connectors (subject to the `l_connector` cut). The contracted graph is still a `MultiDiGraph`; treat the `u→v` super-edge and any surviving `v→u` connectors independently.

- **Orphan node prune at the end.** After contracting climbs and dropping sub-`l_connector` connectors, sweep nodes whose degree is 0 (`out.degree(n) == 0`) and remove them. Mirror the `pipeline/__init__.py::_drop_orphan_nodes` pattern (private helper, module-local — don't import the orchestrator's). Orphans are uncommon in practice but tracking them through the solver would surface as silent dead-ends.

- **Edge-data dict access pattern.** Same trap as Story 3.2: indexed access `graph[u][v][k]` against `nx.MultiDiGraph` trips basedpyright with `int → str` argument-type errors against networkx's partial stubs. If multiple lookups are needed, snapshot a `dict[tuple[int, int, int], dict[str, Any]]` from `graph.edges(data=True, keys=True)` once up-front (same shape as `pipeline.climbs.detect_climbs` line 162-164). The snapshot's values alias live edge-data dicts — never mutate them, the purity contract holds by read-only use.

- **Purity discipline.** Build the contracted `MultiDiGraph` from scratch (`nx.MultiDiGraph()`) — don't `base_graph.copy()` and edit. Add super-edges + filtered connectors explicitly. The `Edge` projections inside `super_edge_to_base` come from a small private `_edge_from_graph_data(u, v, k, data) -> Edge` helper mirroring the one in `pipeline.climbs`; duplicate the helper locally (the stage-8 module's helper is private — don't import a `_`-prefixed name across modules, Architecture §"Python code conventions"). The post-call invariants the hypothesis test asserts (node count, edge count, edge-data deep-copy) catch any accidental write-back.

- **networkx boundary pragma.** Top of `pipeline/graph.py` carries the same module-level pragma as `pipeline/climbs.py`:

  ```python
  # pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
  ```

  with the same one-line `Reason: ...` comment underneath. One pragma covers the whole module; don't pepper inline `# type: ignore`s.

- **Defaults.** `l_connector = 200 m` per PRD §"Initial parameter defaults" / Cat 9 / `cli/_shared.py`. Tests pass it explicitly; `contract_climbs` takes it as a positional argument (no module-scope default — defaults live at the CLI flag layer per Architecture §Cat 1).

- **Integration-test fixture wiring.** Use the same `importlib.util` + `monkeypatch("steeproute.pipeline.osm_load", ...)` pattern that `tests/integration/test_climb_detection_fixture.py:74-91` already uses (which itself mirrors `test_pipeline_end_to_end.py`). Same `_THETA = 0.20` and `_MIN_CLIMB_GROUND_LENGTH_M = 300.0`. Add `_L_CONNECTOR = 200.0`. Keep the integration suite to two or three assertions max — `len(contracted.edges) < len(base.edges)` plus the per-super-edge back-expansion identity. No regression-baseline counts (the climb-detection fixture already pins counts upstream; this one's job is the contract-relation check).

- **What this story does NOT do:**
  - Wire `contract_climbs` into an orchestrator (`pipeline/__init__.py::run_query_stages` is Story 3.11's deliverable alongside `cli/query.py`).
  - Touch `models.py` — `Edge`, `Climb`, `ContractedGraph` shapes landed in Story 3.1.
  - Cache the contracted graph — stages 8-9 are parameter-dependent and run on every query (Architecture §Cat 3b).
  - Enforce strict containment / area-cap on the contracted graph — that lives in the validator (Story 3.9) and solver (Story 3.6).
  - Implement any climb-detection logic — `detect_climbs` is upstream (Story 3.2), consumed as-is here.

### Project Structure Notes

- **New:** `src/steeproute/pipeline/graph.py` — `contract_climbs` public entry point + private helpers (super-edge aggregator, connector filter, orphan-prune, `_edge_from_graph_data` projection).
- **New tests:** `tests/unit/test_graph_contraction.py`, `tests/integration/test_graph_contraction_fixture.py`.
- **Untouched:** every other source module. Story 3.6 (GRASP) will be the first downstream consumer of `contract_climbs`'s output; `pipeline/__init__.py` stays setup-side-only until Story 3.11.

### Testing standards summary

- Tests in `tests/unit/` and `tests/integration/` per Architecture §"Test organization"; file name mirrors the function under test (`test_graph_contraction.py` ↔ `pipeline/graph.py::contract_climbs`).
- Float-equality assertions on aggregates use `math.isclose(..., abs_tol=1e-9)`, never `==` on floats (Architecture §"Numerical and data discipline").
- One `hypothesis` property test required by AC #4. Hypothesis is already a project dev dep (used in `tests/unit/test_climbs.py`); no new dep needed.
- Coverage floor for `pipeline/graph.py` is 95% (Architecture §Cat 11e — pure-logic module).
- No new fixtures.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.3"](../_bmad-output/planning-artifacts/epics.md) — AC source-of-truth
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 3 (3a–3c)] — pipeline stage boundary, MultiDiGraph contract, query-side CLI split
- [Source: _bmad-output/planning-artifacts/architecture.md §"Numerical and data discipline"] — float-tolerance, deterministic edge ordering on `(node_u, node_v, key)`
- [Source: _bmad-output/planning-artifacts/architecture.md §"Test organization"] — three-tier test layout
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11 (11e)] — 95 % coverage floor for pure-logic modules
- [Source: _bmad-output/planning-artifacts/prd.md §FR5] — L_connector flag this stage enforces
- [Source: src/steeproute/models.py] — `Edge`, `Climb`, `ContractedGraph` shapes (Story 3.1); `ContractedGraph.super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]]` is the back-mapping shape this story produces
- [Source: src/steeproute/pipeline/climbs.py] — stage 8 producer (Story 3.2); mirror its purity, pragma, and `_edge_from_graph_data` helper patterns
- [Source: src/steeproute/pipeline/__init__.py:167-181] — `_drop_orphan_nodes` pattern to duplicate locally
- [Source: src/steeproute/pipeline/osm.py — `SAC_SCALE_RANK`] — SAC rank table for super-edge `sac_scale` aggregation
- [Source: tests/integration/test_climb_detection_fixture.py:1-92] — fixture-loading pattern to reuse for the integration test
- [Source: tests/unit/test_climb_detection.py:31-50] — `_chain_graph` synthetic-MultiDiGraph factory pattern to reuse

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. No new runtime or dev deps.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 55 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 493 passed, 1 deselected in ~120s; coverage 97% overall
                                     - graph.py 98% (49 statements, 1 missed —
                                       the orphan-prune `remove_node` call;
                                       structurally unreachable in the build-
                                       from-scratch flow but retained as
                                       defensive parity with
                                       `pipeline.__init__._drop_orphan_nodes`)
```

### Completion Notes List

**Design decisions worth review attention:**

1. **`sac_scale` aggregation defers to `pipeline.osm.max_sac_rank`.** The real Grenoble fixture has list-valued `sac_scale` on edges where osmnx merges parallel ways' tags (e.g. `["mountain_hiking", "demanding_mountain_hiking"]`). Story 3.1's `Edge.sac_scale: str | None` annotation is narrower than the runtime contract — `_edge_from_graph_data` in `pipeline.climbs` propagates the list through without coercion. Rather than re-implementing list-handling inside `_aggregate_sac_scale`, the helper delegates to `max_sac_rank` (already in `pipeline.osm`, handles both `str` and `list[str]`), then looks up the canonical SAC name via a module-scope reverse map `_SAC_RANK_TO_NAME`. The integration test caught this on the first run.

2. **Super-edge identity via dict-membership, not a `is_super_edge` flag.** `(u, v, k) in contracted.super_edge_to_base` iff that triple denotes a super-edge — no boolean attribute on edge-data. The contract is documented at module-docstring level; downstream consumers (validator, solver) hold the dict reference, not a per-edge flag, so the truth is in one place.

3. **Build-from-scratch instead of `base_graph.copy()`.** Adding all surviving connectors first, then super-edges with `_next_key_for`-allocated keys, gives a deterministic edge ordering. A copy-and-edit approach would have to delete every climb-consumed edge, recompute orphans, and would carry forward Connector edge-data with extra cleanup. The from-scratch path also makes the purity contract trivial: `base_graph` is never written to.

4. **Super-edge key allocation is `max existing key + 1` looked up via `out_edges(u, keys=True)`.** Indexed access `contracted[u][v].keys()` trips basedpyright (networkx's partial stubs declare `__getitem__(key: str)` on the AtlasView, so `max(keys()) + 1` reads as `str + int`). `out_edges(u, keys=True)` is typed cleanly enough that the module-top pragma absorbs the residual `Unknown`. Same trap Story 3.2 documented; the workaround pattern is now established for `pipeline/` modules.

5. **Orphan-prune helper is structurally defensive.** Because the contracted graph is built from scratch and `nx.MultiDiGraph.add_edge(u, v, ...)` is what introduces nodes, a node that never appears in any surviving edge is never added in the first place — orphans cannot exist in current flow. `_drop_orphan_nodes` is retained as parity with `pipeline.__init__._drop_orphan_nodes` and as a safety net if a future change adds a node-only insertion path. The single missed-coverage line is `graph.remove_node(n)` inside the helper.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `pipeline/graph.py::contract_climbs(base_graph, climbs, l_connector) -> ContractedGraph` implemented; pure (input never mutated — verified by `test_contract_climbs_does_not_mutate_input_graph` checking node/edge counts + `id(data)` + `dict(data)` per edge, plus the `hypothesis` property `test_contract_climbs_is_pure_under_random_inputs`). Super-edges carry the full stage-7 numeric contract (`length_m` / `d_plus_m` / `d_minus_m` summed; `avg_gradient = (d_plus_m + d_minus_m) / length_m`; `sac_scale` = max-rank SAC). Connectors with `length_m >= l_connector` carry through verbatim; shorter dropped. ✅

2. AC #2 — `tests/unit/test_graph_contraction.py` covers the five prescribed scenarios + 7 additional structural tests: one-climb collapse + aggregate identity + back-mapping round-trip; empty-climbs short/long mix; connector-at-threshold inclusivity; bidirectional asymmetry; orphan-node behavior; max-rank SAC; all-`None` SAC propagation; super-edge key disjointness from existing connectors; two climbs sharing endpoints get distinct keys; purity. 13 explicit unit tests + 2 property tests. ✅

3. AC #3 — `tests/integration/test_graph_contraction_fixture.py` reuses `test_climb_detection_fixture.py`'s `osm_load` monkeypatch pattern; runs `run_setup_stages → detect_climbs → contract_climbs` against the committed Le Sappey fixture at PRD-default `(θ=0.20, min=300, l_connector=200)`. Two assertions: contracted edge count strictly less than base edge count; per-super-edge back-expansion's `sum(length_m)` and `sum(d_plus_m)` (and `sum(d_minus_m)`) match the stored aggregate within `math.isclose(abs_tol=1e-9)`. ✅

4. AC #4 — `tests/unit/test_graph_contraction.py` carries two `hypothesis` property tests over the `_chain_climb_strategy` generator (disjoint linear chains + sprinkled connectors bracketing the `l_connector` threshold): `test_back_mapping_is_injective_on_base_edge_identity` (50 examples) and `test_contract_climbs_is_pure_under_random_inputs` (30 examples, deep-copy snapshot per edge). ✅

5. AC #5 — `uv run ruff check` ✅, `uv run ruff format --check` ✅, `uv run basedpyright` 0/0/0 ✅, `uv run pytest --cov` 493 passed at 97 % overall coverage with `pipeline/graph.py` at 98 % (every executable path of `contract_climbs` + `_next_key_for` + `_aggregate_sac_scale` exercised; the single missed line is the unreachable orphan-prune `remove_node` call — see design decision #5). No new runtime or dev deps. ✅

### File List

**New:**
- `src/steeproute/pipeline/graph.py` — `contract_climbs` public entry point + three private helpers (`_next_key_for` for super-edge key allocation, `_aggregate_sac_scale` for max-rank SAC aggregation via `pipeline.osm.max_sac_rank`, `_drop_orphan_nodes` mirroring the orchestrator's pattern). Module-top pragma comment matches `pipeline/climbs.py`'s networkx-boundary disposition.
- `tests/unit/test_graph_contraction.py` — 13 explicit unit tests covering AC #2 scenarios + structural edge cases (max-rank SAC, all-`None` SAC, super-edge key disjointness, two climbs sharing endpoints) + 2 `hypothesis` property tests (AC #4: injectivity + purity, ~80 examples total at 50 + 30). Synthetic graphs only; no fixture I/O.
- `tests/integration/test_graph_contraction_fixture.py` — 2 tests chaining `run_setup_stages → detect_climbs → contract_climbs` against the committed Le Sappey fixture at PRD defaults. Pins the contracted-graph-smaller-than-base relation and the back-expansion aggregate identity.

**Modified:**
- _(none — `models.py` / `climbs.py` / `pipeline/__init__.py` unchanged)_

**Updated (out-of-source):**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story `3-3-pipeline-stage-9-contracted-climb-graph-construction` walked `backlog → ready-for-dev → in-progress → review → done`. `last_updated: 2026-05-27`.
- `_bmad-output/implementation-artifacts/deferred-work.md` — appended 7 low-severity items from the code review (SAC-rank brittleness, dead `_drop_orphan_nodes`, list/None SAC conflation, integration connector-prune branch, connector geometry carry-over, hypothesis "shared endpoints" coverage, fixture `exec_module` pattern).

**Untouched (intentionally):**
- `src/steeproute/models.py` — `Edge`, `Climb`, `ContractedGraph` shapes landed in Story 3.1; this story consumes them as-is.
- `src/steeproute/pipeline/{climbs.py, __init__.py}` — stage 8 producer and setup-side orchestrator stay unchanged; the query-side wire-up of stages 8-9 lands in Story 3.11 alongside `cli/query.py`.
- Every other source module — Story 3.6 (GRASP) will be the first downstream consumer of `contract_climbs`'s output.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-27 | Yann (Claude Opus 4.7) | Story 3.3 implemented: pipeline stage 9 (contracted climb-graph construction) for Epic 3. **`src/steeproute/pipeline/graph.py`** (new) hosts `contract_climbs(base_graph, climbs, l_connector) -> ContractedGraph` — pure function building a fresh `MultiDiGraph` with one super-edge per `Climb` (summed `length_m` / `d_plus_m` / `d_minus_m`, derived `avg_gradient`, max-rank `sac_scale` via `pipeline.osm.max_sac_rank`) plus connectors `>= l_connector` carried verbatim from the base graph. Super-edge identity is dict-membership in `ContractedGraph.super_edge_to_base`; key allocation via `_next_key_for(u, v) = max existing key + 1` keeps super-edges disjoint from surviving connectors and from other climbs sharing endpoints. Three private helpers (`_next_key_for`, `_aggregate_sac_scale`, `_drop_orphan_nodes`) keep `contract_climbs`'s main body lean. **`tests/unit/test_graph_contraction.py`** (new) ships 13 explicit unit tests covering AC #2 scenarios + structural edges (max-rank SAC, all-None SAC, key-disjoint super-edges) plus 2 `hypothesis` property tests (AC #4: injectivity + purity, 80 examples). **`tests/integration/test_graph_contraction_fixture.py`** (new) chains setup → stage 8 → stage 9 against the committed Le Sappey fixture at PRD defaults; asserts edge-count strictly decreases and per-super-edge back-expansion equals the stored aggregate within `math.isclose(abs_tol=1e-9)`. All four CI gates green: ruff ✅, ruff format ✅, basedpyright 0/0/0 ✅, pytest --cov 493 passed at 97 % overall coverage with `graph.py` at 98 %. No new runtime or dev deps. | _pending_ |
| 2026-05-27 | Yann (Claude Opus 4.7) | Code review patches applied: 7 items resolved. **`pipeline/graph.py`**: aliasing comment on `**data` spread rewritten to honestly describe shallow-copy semantics (outer dict fresh, nested mutables aliased); `_next_key_for` docstring corrected from "smallest non-conflicting" to "first key above `max(existing)`". **`tests/integration/test_graph_contraction_fixture.py`**: `test_super_edge_aggregates_match_back_expanded_base_metrics` rewritten to look up base edge metrics via `base_graph.get_edge_data(...)` instead of `super_edge_to_base`'s `Edge` projections (the original was tautological — it compared a sum against the same sum that produced the stored value). **`tests/unit/test_graph_contraction.py`**: hypothesis `_chain_climb_strategy` `edges_per_climb` min lowered to 1 (single-edge climbs now exercised); `test_super_edge_key_avoids_collision_with_existing_connector` strengthened from `!= 0` to `== 1` (pins the documented `max + 1` allocation rule); `test_single_climb_collapses_into_one_super_edge` adds `set(contracted.graph.nodes) == {0, 4}` to pin climb-internal-node absence; new `test_bidirectional_climb_with_short_reverse_drops_reverse_direction` covers the AC #2 fourth bullet's "dropped if shorter" branch. 7 low-severity findings deferred to `deferred-work.md`. All four CI gates green; pytest 494 passed at 97 % overall, `graph.py` 98 %. No new runtime or dev deps. | _pending_ |
