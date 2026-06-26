# Story 10.1: Junction-start constraint (FR31)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want an opt-in flag that forces a route's start endpoint to a road/trail junction,
So that the surfaced route idea begins where I'd realistically park or step onto the trail.

## Acceptance Criteria

1. **Junction annotation (Stage 9).** Every node in the contracted graph carries an `is_road_trail_junction` boolean, set at contraction in `pipeline/graph.py`: `True` iff the node is incident (in or out) to **both** at least one road connector (an edge whose `highway` classifies as `"connector"`) **and** at least one trail (a super-edge, or a carried-over edge whose `highway` classifies as `"trail"`). Deterministic; computed every run; no RNG.

2. **New opt-in flag.** `--start-at-junction` is a boolean flag on `steeproute`, default **off**, surfaced in `--help`, threaded into `SolverParams` and hence into each report's metadata (FR19). It is absent from `steeproute-setup`.

3. **Flag-off ⇒ byte-identical.** With `--start-at-junction` unset, GRASP/oracle/validator behave exactly as today and the existing default-param regression goldens (both fast and realistic tiers) match **without rebake**.

4. **Flag-on solver + oracle restriction.** With the flag set, GRASP seeds construction **only** at junction nodes and the exhaustive oracle starts walks **only** at junction nodes — driven by the same junction predicate so the two stay on one shared feasible set (the Story 3.7 comparison stays apples-to-apples). FR29 byte-identical reproducibility holds with the flag on (seed-node pool is deterministically ordered).

5. **Flag-on validation.** `start_at_junction` joins the validated per-route constraint set (FR26): when the flag is active, a returned route whose **start endpoint** (`edges[0].node_u`) is not a junction node is flagged with a `ConstraintViolation`, rendered with the prominent banner (FR27), and drives the dedicated non-zero exit code while all results are still written to disk (FR28).

6. **Flag-on golden.** A new flag-on golden fixture pins a real Grenoble-area run with `--start-at-junction` set, and a test asserts **every** returned route starts at a junction node. Existing default-param goldens are left untouched (no rebake).

7. **FR12 messaging.** When the active junction constraint shrinks the result set below `--n`, the existing graceful-degradation path fires and its message names `--start-at-junction` among the levers (alongside `--theta` / `--j-max`) so the cause is honestly surfaced rather than silently loosened.

8. **All four CI gates green on Windows** (`ruff check`, `ruff format --check`, `basedpyright` 0/0/0, `pytest --cov`), pure-logic coverage floors held on the changed paths, no new runtime deps. The 8 metamorphic invariants and the Story 3.7 gate still pass unchanged (their fixtures don't set the flag).

## Tasks / Subtasks

- [x] Task 1: Annotate `is_road_trail_junction` at contraction (AC: #1). In `pipeline/graph.py::contract_climbs`, after the contracted graph is built, set the node attribute using `pipeline.osm.classify_highway` for the road/trail edge distinction; super-edges (keys in `super_edge_to_base`) count as trails. Add junction-annotation unit tests (extend `tests/unit/test_graph_contraction.py` + the fixture test).
- [x] Task 2: Add `--start-at-junction` flag + `SolverParams.start_at_junction` and wire it (AC: #2, #3). Define the option in `cli/_shared.py`, stack it on `cli/query.py`'s command, pass it into `SolverParams`; confirm it appears in `--help` and the JSON sidecar `params` block.
- [x] Task 3: Restrict GRASP seeding and the oracle's walk-starts to junction nodes under the flag (AC: #4). Single-source the junction predicate so both share one feasible set; keep the seed-node pool sorted (FR29). Add a flag-on GRASP-vs-oracle one-feasible-set test on a small graph + an FR29 determinism check with the flag on.
- [x] Task 4: Add the `start_at_junction` validator constraint (AC: #5). Emit a `ConstraintViolation` when the flag is active and the route start isn't a junction; add validator unit tests (pass + reject).
- [x] Task 5: Wire FR12 messaging to name `--start-at-junction` when active (AC: #7); add/extend the degradation test.
- [x] Task 6: Add the flag-on golden fixture + the "every route starts at a junction" assertion; confirm existing goldens match without rebake (AC: #6, #3). See Dev Notes on the harness bare-flag wrinkle.
- [x] Task 7: Verify all four CI gates, coverage floors, the 8 metamorphic invariants, and the Story 3.7 gate (AC: #8).

## Dev Notes

### Recommendation (read first)

This is an **additive, opt-in** feature (default off → no default-output change), the same shape as the Epic 9 additions. The smallest correct cut:

- Compute `is_road_trail_junction` **once, always**, at contraction. It's a self-describing node attribute that costs nothing when the flag is off.
- Gate the *behavior* (seed restriction, oracle restriction, validation) on `params.start_at_junction`.
- Single-source the "is this node a junction?" check so GRASP and the oracle can never drift onto different feasible sets — this is the one property the Story 3.7 quality gate depends on.

### Junction definition + the "connector" terminology trap

The junction predicate (per [architecture.md §"Pipeline stages" stage 9](_bmad-output/planning-artifacts/architecture.md) and the [correct-course proposal §1/§4B1](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-25-junction-start-and-descent-cap.md)): a node is a road/trail junction iff it is incident to **both** an admitted minor **road** and a **trail**.

⚠️ **"Connector" means two different things in this codebase.** In `contract_climbs` (`pipeline/graph.py`), "connector" loosely means *any non-climb carried-over edge* — which includes flat **trails** that aren't part of a climb. The junction definition needs the **road** sense: an edge whose `highway` tag classifies as a road. Use [`pipeline.osm.classify_highway`](src/steeproute/pipeline/osm.py:251) — it returns `"trail"` / `"connector"` (= road) / `None` from the `highway` tag — to make the distinction. Do **not** equate "non-super-edge" with "road."

Concrete predicate per node, over incident edges in the contracted graph:
- **road present** = any incident carried-over edge with `classify_highway(data["highway"]) == "connector"`.
- **trail present** = any incident **super-edge** (`(u,v,k) in super_edge_to_base` — climbs are trails) **or** any carried-over edge with `classify_highway(data["highway"]) == "trail"`.
- `is_road_trail_junction = road present and trail present`.

`contract_climbs` already imports from `pipeline.osm` (`SAC_SCALE_RANK`, `max_sac_rank`) — adding `classify_highway` is consistent. Super-edges are built without a `highway` key, so their road/trail status comes from their `super_edge_to_base` membership, not a tag lookup.

### Threading the flag (minimal blast radius)

- **`models.SolverParams`**: add `start_at_junction: bool = False` as the **last** field. A default keeps every existing `SolverParams(...)` construction in tests/e2e compiling unchanged (all current fields are positionally required) and makes "off" the default. `output.py` serializes params via `asdict(params)` ([output.py:199](src/steeproute/output.py)), so the new field flows into the sidecar/HTML metadata automatically (FR19). Update the dataclass docstring's "13 parameters" → 14 and `tests/unit/test_models.py` if it pins the field set.
- **`cli/_shared.py`**: a `click.option("--start-at-junction", is_flag=True, default=False, ...)` decorator. No entry needed in `validate_solver_options` (a boolean flag has no range to check).
- **`cli/query.py`**: stack the decorator on `cli`, add the `start_at_junction: bool` kwarg, pass `start_at_junction=start_at_junction` into `SolverParams`.

### Solver + oracle (one feasible set, FR29)

- **GRASP** (`solver/grasp.py`): `self._nodes` is the seed pool, the *only* place start nodes are sampled (`_construct_one`). When `params.start_at_junction`, build it from junction nodes only: `tuple(sorted(n for n in graph.graph.nodes if <is junction>))`. Keep it sorted — start-node sampling determinism (FR29) depends on the sorted tuple, exactly as the existing code documents. If the pool is empty, `run()`'s existing `if not self._nodes: return ...` early-return yields `[]`, which is the correct FR12 outcome (no special-casing).
- **Oracle** (`tests/integration/exhaustive_oracle.py`): `enumerate_best` iterates `for start in list(nx_graph.nodes)`. Filter to junction nodes under the flag using the **same** predicate. Prefixes preserve the start node (`_dfs` emits every prefix of a walk starting at `start`), so restricting starts enforces the start-endpoint constraint on both sides identically.
- The route's **start endpoint is `edges[0].node_u`** and equals the seed (construction never prepends), so seed-restriction ⇒ start-endpoint guarantee. The far endpoint is unconstrained (open walk) — per the decided endpoint semantics, the constraint is start-only.

### Validator

Add a `start_at_junction` check to `validator.py::_validate_edges` (the shared per-route path): when `params.start_at_junction` and `edges` is non-empty, read `graph.graph.nodes[edges[0].node_u].get("is_road_trail_junction", False)`; if `False`, append a `ConstraintViolation(constraint_id="start_at_junction", ...)`. It rides the existing FR27 banner / FR28 exit-code path (`cli/query.py::_exit_code_for`) with no other wiring. Default `.get(..., False)` so an untagged node fails closed.

### Golden fixture + the harness bare-flag wrinkle

- The non-regression proof is the **default-param** goldens staying green: `regression.py::params_hash` hashes only the *pinned* CLI flag set ([regression.py:191](src/steeproute/regression.py)), and the existing fixtures don't pin `--start-at-junction`, so they don't move — and a new defaulted `SolverParams` field doesn't change route output. Confirm with `uv run update-regression --all` showing "(no change)" (do **not** commit a rebake).
- The **new** flag-on golden needs `--start-at-junction` in a fixture's `pinned_params`. ⚠️ `run_fixture` builds args as `args += [flag, value]` ([regression.py:280](src/steeproute/regression.py)) — that assumes every pinned param is a `--flag value` pair, which a bare `is_flag` option is not. Resolve cleanly (dev's call): e.g. give the option an explicit `--start-at-junction/--no-start-at-junction` boolean form so `["--start-at-junction", "true"]` round-trips, or teach `run_fixture`/`Fixture` to carry bare flags. Keep determinism (high `--time-budget`, pinned iter/stagnation) like the other fixtures.
- The "every route starts at a junction" assertion is a **separate** test, not something the 5-field golden tuple captures — the golden only pins the deterministic route set. The sidecar's `edges` list is in traversal order ([output.py:109/151](src/steeproute/output.py)), so the start endpoint is `edges[0][0]`; assert each is a junction node. Folding the new fixture into the zero-tolerance CI gate + the realistic tier is **Story 8.5's** job (per the proposal); 10.1 creates the fixture, its committed golden, and the property assertion.

### Cache: no query-side re-prepare needed

`check_coverage` resolves caches by **geographic strict containment** and loads by the entry's stored `cache_key_hash` ([cache.py:1018](src/steeproute/cache.py)) — it never re-matches `pipeline_content_hash`. Stages 8–9 (including this annotation) run **query-side** on the cached raw stage-1–5 graph. So editing `pipeline/graph.py` + `models.py` (both in `_PIPELINE_CONTENT_GLOBS`, [cache.py:59](src/steeproute/cache.py)) bumps `pipeline_content_hash` for any **fresh** `steeproute-setup`, but the committed regression-fixture caches still load by geography and re-run current stages 8–9 — so no fixture rebuild and (flag off) byte-identical output. No cache invalidation for this feature.

### Project Structure Notes

- **Modified:** `pipeline/graph.py` (junction annotation), `models.py` (`SolverParams.start_at_junction`), `cli/_shared.py` (flag), `cli/query.py` (wire flag + FR12 message), `solver/grasp.py` (seed restriction), `validator.py` (new constraint), `tests/integration/exhaustive_oracle.py` (start restriction).
- **New:** junction-annotation tests, validator pass/reject tests, flag-on GRASP-vs-oracle + FR29 tests, the flag-on golden fixture + its committed golden + the junction-start assertion.
- **Possibly updated:** `tests/unit/test_models.py` (new field), the degradation test (`tests/e2e/test_degradation.py`).
- **Out of scope:** the descent cap (Story 10.2), folding the new golden into the CI gate + realistic tier (Story 8.5), any `steeproute-setup` change.

### Testing standards summary

- Float comparisons on aggregates use `math.isclose(..., abs_tol=1e-9)`; FR29 byte-identical tests deliberately use `==` (see `test_grasp_construction.py`).
- Junction annotation is pure-logic (`pipeline/`) — held to the 95% coverage floor (Architecture §Cat 11e).
- Build-flake recovery (Epic 9 precedent): if `uv run` hits the corporate-TLS cert error on a stale editable build, settle once with `uv sync --native-tls`, then run with `uv run --no-sync`. Run `tests/unit` and `tests/integration` in **separate** pytest invocations (a `from conftest import ...` collision otherwise).
- No new fail-first regression-bug test is required (this is a *new feature*, not a defect fix) — but the flag-on golden + junction-start assertion are the equivalent pinning.

### References

- [Source: epics.md §"Story 10.1"](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; Epic 10 framing
- [Source: sprint-change-proposal-2026-06-25-junction-start-and-descent-cap.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-25-junction-start-and-descent-cap.md) — §1 (#1), §2 technical impact, §4B (B1/B2/B5), §4C, §5 story text
- [Source: architecture.md §"Pipeline stages" stage 9 + FR-coverage map FR31](_bmad-output/planning-artifacts/architecture.md) — `is_road_trail_junction` annotation site; flag catalog
- [Source: prd.md FR31](_bmad-output/planning-artifacts/prd.md) — requirement text
- [Source: src/steeproute/pipeline/graph.py:67](src/steeproute/pipeline/graph.py) — `contract_climbs` (annotation site); `super_edge_to_base` = trail super-edges
- [Source: src/steeproute/pipeline/osm.py:251](src/steeproute/pipeline/osm.py) — `classify_highway` (road vs trail distinction)
- [Source: src/steeproute/solver/grasp.py:195](src/steeproute/solver/grasp.py) — `self._nodes` seed pool + start-node determinism contract
- [Source: tests/integration/exhaustive_oracle.py:119](tests/integration/exhaustive_oracle.py) — oracle start-node loop (restrict under flag)
- [Source: src/steeproute/validator.py:164](src/steeproute/validator.py) — `_validate_edges` (add the constraint)
- [Source: src/steeproute/cli/query.py:414](src/steeproute/cli/query.py) — `_degradation_message` (FR12 lever wording)
- [Source: src/steeproute/regression.py:191](src/steeproute/regression.py) — `params_hash` over pinned set (why existing goldens stay green); `run_fixture` arg-building (bare-flag wrinkle)
- [Source: _bmad-output/implementation-artifacts/9-2-grasp-theta-feasible-prefix-recovery.md](_bmad-output/implementation-artifacts/9-2-grasp-theta-feasible-prefix-recovery.md) — prior additive-feature precedent: one-feasible-set discipline, FR29, build-flake recovery

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps. Settled the known stale-editable-build / corporate-TLS flake once with `uv sync --native-tls`, then ran all tests with `uv run --no-sync`. `tests/unit` and `tests/integration` were run in **separate** pytest invocations (the documented `from conftest import ...` collision under pytest prepend import mode).

**Test runs (all `--no-sync`):**

```
pytest tests/unit                          → 586 passed   (incl. new junction-annotation, validator, degradation-message tests)
pytest tests/integration                   → 122 passed, 2 deselected (incl. new junction-start GRASP↔oracle test, 3.7 gate, 3.8 metamorphic)
pytest tests/e2e                           → 88 passed, 4 deselected   (incl. new test_junction_start, fast-tier pinned regressions)
pytest tests/e2e/test_pinned_regressions.py -m slow → 4 passed   (realistic-tier goldens match WITHOUT rebake — non-regression proof)
ruff check src tests                       → All checks passed!
ruff format --check src tests              → 98 files already formatted
basedpyright (all changed src files)       → 0 errors, 0 warnings, 0 notes
coverage (changed pure-logic)              → models.py 100%, validator.py 100%, graph.py 99% (lone miss is the pre-existing
                                             `_is_junction` out-edges branch, Story 6.1 — not new code), grasp.py 97%
```

### Completion Notes List

**Junction annotation (Task 1).** `pipeline/graph.py::_annotate_junctions` tags every contracted-graph node `is_road_trail_junction` after the graph is built — computed unconditionally (cheap, self-describing, ignored when the flag is off). Road vs trail is taken from `pipeline.osm.classify_highway` on each carried-over edge's `highway` tag; super-edges (climbs, keyed in `super_edge_to_base`) count as trails directly. This sidesteps the "connector" terminology trap (contraction's non-climb "connector" ≠ road connector). Incidence is checked in both directions.

**Flag + params (Task 2).** `--start-at-junction/--no-start-at-junction` boolean option in `cli/_shared.py` (shows in `--help`), threaded into `SolverParams.start_at_junction` (added **last** with a `= False` default → every existing positional construction stays valid, default output unchanged). It flows into the JSON sidecar `params` block via `asdict(params)` (FR19) for free.

**Solver + oracle one feasible set (Task 3).** Under the flag, `GraspSolver.__init__` restricts the seed pool `self._nodes` to junction nodes (kept sorted → FR29); `exhaustive_oracle.enumerate_best` restricts walk-starts identically. The route start endpoint is `edges[0].node_u` (never prepended; prefixes preserve it), so seed-restriction ⇒ start-endpoint guarantee on both sides. An empty junction set → `run()`'s existing early return yields `[]` (correct FR12 outcome). `test_grasp_junction_start.py` pins: flag-off best route starts at a non-junction; flag-on every route starts at a junction; GRASP == oracle under the flag; flag-on determinism.

**Validator (Task 4).** `validator._validate_edges` adds a `start_at_junction` `ConstraintViolation` when the flag is active and `edges[0].node_u` isn't a junction (fail-closed via `.get(..., False)`). Rides the existing FR27 banner / FR28 exit-code path with no other wiring.

**FR12 (Task 5).** `_degradation_message` now names `--start-at-junction` as both a cause and a lever when the flag is active; flag-off wording is byte-identical to before (existing `test_degradation.py` unchanged-green).

**Flag-on golden + property (Task 6).** New `regression.FLAG_ON_FIXTURES` entry `grenoble_small_junction` reuses the committed `grenoble_small` cache with `--start-at-junction` pinned on; `run_fixture` now renders boolean pinned params (`"true"`/`"false"`) as a bare flag, keeping it inside `params_hash`. Committed golden `tests/e2e/goldens/grenoble_small_junction.json` (5 routes). `tests/e2e/test_junction_start.py` asserts (a) every returned route starts at a junction — no `start_at_junction` validation violation on any sidecar — and (b) the run matches its golden. Deliberately kept OUT of `regression.FIXTURES` (the zero-tolerance CI gate) — folding it in + the realistic tier is Story 8.5's job. The existing default-param goldens (both tiers) match **without** rebake — verified empirically.

**No cache invalidation.** `check_coverage` resolves by geography and loads by stored `cache_key_hash`, never re-matching `pipeline_content_hash`; stage 9 runs query-side, so the committed caches load unchanged and (flag off) produce byte-identical output.

**Code review (lightweight, recall-biased) — 6 findings, all fixed.** Key behavioral change: junction road/trail detection no longer routes through `classify_highway` (whose "trails win" tie-break hid the road side of osmnx-merged `["service","footway"]`-style ways, silently dropping genuine junctions). It now tests road- and trail-tag presence independently via new `pipeline.osm.has_road_highway` / `has_trail_highway`. On `grenoble_small` this grows the junction pool 84→99 (36 of 1846 edges are merged road+trail; ~28% of junctions lean on one) — an intentional recall gain confirmed against the literal FR31 wording, so `tests/e2e/goldens/grenoble_small_junction.json` was **rebaked** (objectives rose). Other fixes: annotation gated behind `contract_climbs(annotate_junctions=...)` so the flag-off path skips the O(E) pass; the read predicate single-sourced as `pipeline.graph.is_junction_node` (GRASP/oracle/validator all consult it — same discipline as `solver.reuse`); the validator reframed as the *enforcement* of FR31 (seed-restriction is only an efficiency prune); `regression._BOOLEAN_FLAGS` renders boolean pinned params by flag name (not value string) and fails loud; `_degradation_message` builds its constraint/lever clauses from one flag read.

### File List

**Modified (src):**
- `src/steeproute/pipeline/graph.py` — `_annotate_junctions` helper (gated by `contract_climbs(annotate_junctions=...)`) + `is_junction_node` shared read predicate; imports `has_road_highway`/`has_trail_highway`.
- `src/steeproute/pipeline/osm.py` — `has_road_highway` / `has_trail_highway` independent tag-presence predicates (review #1).
- `src/steeproute/models.py` — `SolverParams.start_at_junction: bool = False` (last field) + docstring (13→14 params).
- `src/steeproute/cli/_shared.py` — `start_at_junction_option` decorator.
- `src/steeproute/cli/query.py` — stack the option, thread into `SolverParams`, FR12 lever wording in `_degradation_message`.
- `src/steeproute/solver/grasp.py` — junction-restricted seed pool in `__init__` (sorted, FR29) + docstring.
- `src/steeproute/validator.py` — `start_at_junction` per-route constraint + module docstring.
- `src/steeproute/regression.py` — `FLAG_ON_FIXTURES` (`grenoble_small_junction`); `run_fixture` boolean-flag rendering; `_select` named-lookup over flag-on fixtures.

**Modified (tests):**
- `tests/integration/exhaustive_oracle.py` — junction-restricted walk-starts under the flag.
- `tests/unit/test_graph_contraction.py` — 5 junction-annotation tests + `_add_edge_with_highway` helper.
- `tests/unit/test_validator.py` — 3 `start_at_junction` tests + `_mark_junctions` helper; `_params` gains `start_at_junction`.

**New (tests + golden):**
- `tests/integration/test_grasp_junction_start.py` — GRASP↔oracle one-feasible-set + FR29 under the flag.
- `tests/unit/test_degradation_message.py` — FR12 message wording (flag on/off, full set).
- `tests/e2e/test_junction_start.py` — flag-on junction-start property + golden-match.
- `tests/e2e/goldens/grenoble_small_junction.json` — committed flag-on golden (5 routes).

### Change Log

| Date | Author | Description |
|---|---|---|
| 2026-06-26 | Yann (Claude Opus 4.8) | Story 10.1 implemented (FR31, opt-in `--start-at-junction`): junction-node annotation at stage 9 (`is_road_trail_junction`); GRASP seed + exhaustive-oracle walk-start restriction on one shared feasible set; validator `start_at_junction` constraint (FR26/27/28); FR12 degradation message names the new lever; new flag-on golden fixture + junction-start property assertion. Default off → default output byte-identical; existing goldens (fast + realistic tiers) match without rebake. 586 unit + 122 integration + 88 e2e green; ruff/format/basedpyright clean; FR29 determinism preserved. New `SolverParams.start_at_junction` defaulted last to keep blast radius minimal. Folding the new golden into the zero-tolerance CI gate + realistic tier deferred to Story 8.5. |
