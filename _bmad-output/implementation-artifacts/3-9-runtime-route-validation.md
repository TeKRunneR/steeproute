# Story 3.9: Runtime route validation

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want `validator.py` implementing per-route, set-level, and orchestrator validation per Architecture §Cat 6,
so that FR26–28 are fulfilled: every returned route is checked against all declared constraints, failures carry structured violations the renderer can banner, and the CLI exit code is driven off the validated set rather than an exception.

## Acceptance Criteria

1. `validator.py` exposes the three §Cat 6d functions with their exact signatures — `validate_route(route, graph, params) -> RouteValidation`, `validate_set(routes, params) -> list[PairwiseViolation]`, `validate(solutions, graph, params) -> ValidatedRouteSet` — and is **pure**: no I/O, no module state, no input mutation. Validation failures are **data**, never raised exceptions (Architecture §Cat 6c).

2. `validate_route` checks all four per-route constraints and returns a `RouteValidation` whose `passed` is `True` iff `violations` is empty:
   - **slope floor** — every non-connector edge has `avg_gradient ≥ params.theta` (non-connector = a super-edge, i.e. its `(node_u, node_v, key)` is a key in `graph.super_edge_to_base`; plain connectors are exempt — mirrors the solver's θ filter at `grasp.py:209`);
   - **difficulty cap** — every edge has `max_sac_rank(edge.sac_scale) ≤ parse_difficulty_cap(params.difficulty_cap)` (a `None` rank passes, matching the solver);
   - **edge-reuse limit** — see Dev Notes "Open question"; implement the edge-simple invariant (each `(node_u, node_v, key)` appears at most once) and confirm the `l_connector` coupling before finalizing;
   - **graph membership** — every route edge's `(node_u, node_v, key)` exists in `graph.graph` (sanity check that the route references only operational-graph edges).
   Each failure is a `ConstraintViolation` carrying a stable `constraint_id`, a human-readable `detail`, and a `numeric` dict with `observed` vs. `required` (e.g. `{"observed": 0.18, "required": 0.20}`).

3. `validate_set` returns one `PairwiseViolation` per route pair whose Jaccard **similarity** exceeds `params.j_max`, with `route_index_a < route_index_b`, `jaccard_observed` = the observed similarity, and `jaccard_max = params.j_max`. Reuse the canonical `(node_u, node_v, key)` edge-identity from `solver/distinctness.py` so the semantics match `TopNTracker`'s admission exactly (overlap iff similarity `> j_max`).

4. `validate` orchestrates: converts each `Solution` to a `Route` (building `RouteMetrics` with `avg_gradient = d_plus_m / length_m` when `length_m > 0` else `0.0`, per the `RouteMetrics` docstring) and its `RouteValidation`, then runs `validate_set` over the resulting routes, returning a `ValidatedRouteSet`. Route order preserves the solver's `Solution` order and `set_violations` are emitted in deterministic `(a, b)` pair order (FR29 byte-identical reproducibility).

5. `tests/unit/test_validator.py` has one test per per-route constraint plus the set-level Jaccard case, each with a **crafted-violating** and a **crafted-clean** fixture, asserting the correct `constraint_id` (or `PairwiseViolation` fields) and the `numeric` observed-vs-required values (PRD structural requirement).

6. `tests/integration/test_validator_on_fixture.py` runs `validate` on real `GraspSolver` output from the Grenoble fixture and asserts every route passes with no set violations (GRASP output validates by construction — a failure here signals a solver bug); a second integration test crafts a `Solution` that deliberately violates one constraint (e.g. inserts an edge with `avg_gradient` below θ) and asserts the violation is caught with correct metadata.

7. All four CI gates green on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. No new runtime deps. Coverage floors hold, including the **95% pure-logic floor on `validator.py`** (Architecture §Cat 11).

## Tasks / Subtasks

- [x] Task 1: Implement `validate_route` with the four per-route constraint checks, emitting `ConstraintViolation`s with stable `constraint_id`s and `observed`/`required` numerics. (AC: #1, #2)
  - [x] Reuse `parse_difficulty_cap` / `max_sac_rank` from `pipeline/osm.py` for the difficulty cap (parse the cap once).
  - [x] Resolve the edge-reuse / `l_connector` semantics (Dev Notes "Open question") before locking the check.
- [x] Task 2: Implement `validate_set` — pairwise Jaccard similarity over canonical edge-identity, flagging pairs above `j_max`, deterministic `(a, b)` order. (AC: #1, #3)
  - [x] Reuse the canonical `(node_u, node_v, key)` projection from `solver/distinctness.py` rather than reinventing it (lift a shared helper or wrap `jaccard_distance` — keep the identity definition single-sourced).
- [x] Task 3: Implement the `validate` orchestrator — `Solution → Route` (with `RouteMetrics`), per-route + set-level, deterministic ordering. (AC: #1, #4)
- [x] Task 4: Write `tests/unit/test_validator.py` — one violating + one clean fixture per constraint, asserting `constraint_id` + numerics. (AC: #5)
- [x] Task 5: Write `tests/integration/test_validator_on_fixture.py` — real-GRASP-output all-pass + crafted-violation caught. (AC: #6)
- [x] Task 6: Run all four gates on Windows; confirm 95% coverage on `validator.py`. (AC: #7)

### Review Findings

_Adversarial code review (3 layers: blind hunter, edge-case hunter, acceptance auditor) run 2026-06-01. 14 raw findings → 1 decision-needed + 1 patch + 1 defer + 11 dismissed (deduped). The auditor confirmed all 7 ACs satisfied and solver-mirror symmetry intact._

- [x] [Review][Patch] **(was Decision → resolved: fail loud)** `validate` does not handle empty / zero-edge `Solution`s, and the both-empty pair is spuriously flagged as a Jaccard violation. `GraspSolver.run()` discards empty walks (`grasp.py:136`), so this cannot occur through the real pipeline — but `validate` is a public function with no guard. A zero-edge `Solution` becomes a `passed=True` zero-edge `Route`; two such routes feed `jaccard_distance` → `0.0` (both-empty contract), and `0.0 < 1 - j_max` is `True`, producing a `PairwiseViolation(jaccard_observed=1.0)` that would drive exit code 1. **Resolution (user decision):** raise `ValueError` on any zero-edge `Solution` at the `validate` boundary — enforces the docstring's "illegal at the validator stage" contract, consistent with `TopNTracker`'s non-finite-objective guard. [validator.py:104-123] [source: edge]
- [x] [Review][Patch] **(LOW)** Per-edge violations are emitted once per *traversal*, not per distinct edge identity. A reused edge that also fails slope_floor / difficulty_cap / graph_membership yields duplicate `ConstraintViolation`s (one per occurrence) plus the `edge_reuse` violation. Only reachable on a non-edge-simple route (which GRASP never produces), so impact is cosmetic banner noise. Fix: run the per-edge checks over distinct `(node_u, node_v, key)` identities. [validator.py:141-179] [source: blind]
- [x] [Review][Defer] **(LOW-MED)** Finiteness is not guarded: a `NaN` super-edge `avg_gradient` silently passes the slope floor (`NaN < θ` is `False`), and `NaN`/negative edge metrics propagate into `RouteMetrics` (negative `length_m` slips the `> 0` guard, `NaN` length surfaces verbatim to the renderer). [validator.py:145, :202-207] [source: edge] — deferred, pre-existing/cross-cutting: the solver's `_build_rcl` shares the same `< θ` behavior and the pipeline already asserts finite metrics; a finiteness contract spanning pipeline → solver → validator is out of scope for this story.

## Dev Notes

- **Validation is a distinct stage, not a `Route` postcondition** (Architecture §Cat 6a). `Route.__init__` must not throw on invalid input — failed routes are produced, flagged, and rendered with a banner (FR27–28). Keep the validator orthogonal and explicit.
- **Per-route vs. set-level split** (§Cat 6b): per-route violations live on `Route.validation`; Jaccard violations have no single home route, so they live on the wrapping `ValidatedRouteSet.set_violations` and cross-reference the affected pair by positional index.
- **Non-connector = super-edge.** The solver applies the θ floor only to super-edges (membership in `graph.super_edge_to_base`), letting plain connectors carry their underlying trail gradient (`grasp.py:184-210`). The validator must use the **same** definition or it will flag GRASP-legal routes — AC #6's "every route passes" depends on this symmetry.
- **Difficulty cap mirrors the solver** (`grasp.py:206-208`): reject when `rank is not None and rank > cap_rank`; a `None` rank (untagged / unrecognized SAC) passes.
- **Jaccard semantics are pinned in `distinctness.py`**: `j_max` is a *similarity ceiling*; `TopNTracker` treats two solutions as overlapping iff `jaccard_distance < 1 - j_max`, i.e. similarity `> j_max`. The set-level validator must flag exactly that condition. `jaccard_distance` currently takes `Solution`; `validate_set` takes `Route` — single-source the canonical-edge-set identity rather than duplicating the `(node_u, node_v, key)` projection.
- **Determinism (FR29).** The solver feeds a deterministic top-N sequence; preserve `Solution` order into `routes`, and emit `set_violations` by ascending `(route_index_a, route_index_b)`. No set/dict iteration order leaking into output.
- **Open question — edge-reuse limit vs. `l_connector`.** Architecture §Cat 6's constraint table labels this row "Edge-reuse limit | `--l-connector`", and the PRD FR26 lists "edge-reuse limit" as a distinct constraint, but `l_connector` is defined elsewhere (FR5) as the connector **length** threshold enforced at contraction (Story 3.3), not a reuse count. The solver already guarantees edge-simple walks (each `(node_u, node_v, key)` used once, `grasp.py:143-165`). Implement the edge-simple invariant as the reuse check and confirm whether any additional `l_connector`-keyed semantics are intended before finalizing — flagged to {user_name} below.

### Project Structure Notes

- **Implement:** `src/steeproute/validator.py` (currently a one-line placeholder docstring — replace it).
- **New tests:** `tests/unit/test_validator.py`, `tests/integration/test_validator_on_fixture.py` (the §Cat 11 source-tree names `test_validator.py` at line 831).
- **Reuse (do not duplicate):**
  - `steeproute.models` — `Solution`, `Route`, `RouteMetrics`, `RouteValidation`, `ConstraintViolation`, `PairwiseViolation`, `ValidatedRouteSet`, `Edge`, `ContractedGraph`, `SolverParams` are all defined (Story 3.1) — consume them, do not redefine.
  - `steeproute.pipeline.osm` — `parse_difficulty_cap`, `max_sac_rank` (same helpers the solver uses).
  - `steeproute.solver.distinctness` — canonical edge-identity / `jaccard_distance`.
- **Networkx boundary:** reading edges off `graph.graph` (a `MultiDiGraph` typed `Any`) will surface Unknown types — mirror the pyright pragma header used in `grasp.py`/`pipeline/` modules if needed.

### Testing standards summary

- Unit tests in `tests/unit/`, integration in `tests/integration/`; naming `test_<unit>_<scenario>` (Architecture §"Test organization").
- Crafted fixtures build `Route`/`Solution`/`Edge`/`ContractedGraph` directly (all `frozen=True, slots=True`); the integration test reuses the real Grenoble fixture + `GraspSolver` (see `tests/integration/test_grasp_on_fixture.py` for the fixture wiring).
- `validator.py` is a 95%-coverage pure-logic module — exercise every constraint branch.
- No `pytest.skip`/`xfail`; no new deps (numpy + networkx already present).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.9"](../planning-artifacts/epics.md) — the three functions, constraint list, per-constraint + integration test requirements, purity
- [Source: _bmad-output/planning-artifacts/architecture.md §Cat 6 (6a–6d)](../planning-artifacts/architecture.md) — validation-as-stage, data shapes, banner + exit-code coupling, validator interface, constraints table
- [Source: _bmad-output/planning-artifacts/prd.md §FR26–FR28](../planning-artifacts/prd.md) — every-route validation, banner, exit-code-with-disk-write contract
- [Source: src/steeproute/models.py:199-267](../../src/steeproute/models.py) — `ConstraintViolation` / `RouteValidation` / `Route` / `RouteMetrics` / `PairwiseViolation` / `ValidatedRouteSet` shapes (§Cat 6b verbatim)
- [Source: src/steeproute/solver/grasp.py:171-224](../../src/steeproute/solver/grasp.py) — feasibility filters the validator must mirror (θ on super-edges, SAC cap, edge-simple walk)
- [Source: src/steeproute/solver/distinctness.py:31-57](../../src/steeproute/solver/distinctness.py) — canonical `(node_u, node_v, key)` edge-set + `jaccard_distance`; `j_max` as similarity ceiling
- [Source: src/steeproute/pipeline/osm.py:191-241](../../src/steeproute/pipeline/osm.py) — `parse_difficulty_cap`, `max_sac_rank` (difficulty-cap check)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps (numpy + networkx already present).

**Final pass (all green):**

```
uv run ruff check <new files>                 → All checks passed!
uv run ruff format <new files>                → 1 reformatted (test_validator.py), then clean
uv run basedpyright <new files>               → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                           → 606 passed, 1 deselected in 173 s; 97% overall
                                                (was 590 after 3.8; +16 = 12 unit + 4 integration)
src/steeproute/validator.py coverage          → 100% (exceeds the 95% pure-logic floor)
```

### Completion Notes List

**Open question resolved (edge-reuse vs. `l_connector`).** The story flagged an ambiguity: the §Cat 6 constraint table labels the reuse row `--l-connector`, but `l_connector` is the connector *length* threshold enforced at contraction (Story 3.3 / FR5), not a per-route reuse count. Resolved by implementing the **edge-simple invariant** — each `(node_u, node_v, key)` identity may appear at most once (`constraint_id="edge_reuse"`, `observed`=traversal count, `required`=1) — which is what the solver already guarantees (`grasp.py` edge-simple walks) and what FR26's named "edge-reuse limit" maps to. No `l_connector`-keyed runtime check was added. Documented in the `validator.py` module docstring.

**Design decisions worth review attention:**

1. **Constraint semantics mirror the solver's RCL filters** so GRASP output validates by construction (AC #6). Slope floor is checked on **super-edges only** (membership in `super_edge_to_base`), exactly as `grasp.py:209`; difficulty cap rejects only a *known* SAC rank above the cap (`max_sac_rank(...) is not None and > cap_rank`), matching the solver and the Story 3.5 oracle. Checking θ on every edge would wrongly reject legitimate downhill connectors.
2. **Jaccard identity single-sourced.** `validate_set` wraps each `Route` as a transient `Solution(objective=0.0)` and calls `solver.distinctness.jaccard_distance`, so set-level distinctness uses byte-identical edge-identity semantics to `TopNTracker`'s admission — no duplicated `(u,v,key)` projection. A pair violates iff similarity `> j_max` (i.e. `jaccard_distance < 1 - j_max`), the exact complement of the tracker's overlap test, so a tracker-admitted set yields zero violations.
3. **Frozen-dataclass chicken-and-egg.** `Route.validation` is a required field, but `validate_route(route, ...)` produces a `RouteValidation`. Resolved with a private `_validate_edges(edges, ...)` shared by the public `validate_route` (reads `route.edges`, ignores `route.validation`) and the `validate` orchestrator (builds the `Route` once from a `Solution` with the computed validation). No two-phase rebuild of frozen objects.
4. **Validation failures are data, never exceptions** (§Cat 6c) — the module is pure (no I/O, no state, no input mutation; pinned by `test_validate_route_does_not_mutate_inputs`). Exit-code coupling lands in Story 3.11.

**AC walkthrough:**

1. AC #1 — three §Cat 6d functions with exact signatures; pure (purity test green). ✅
2. AC #2 — all four per-route constraints emit `ConstraintViolation`s with stable `constraint_id` + observed/required numerics. ✅
3. AC #3 — `validate_set` flags pairs above `j_max`, `(a,b)` ascending order, observed similarity + `jaccard_max`. ✅
4. AC #4 — `validate` converts `Solution`→`Route` with summed `RouteMetrics` (`avg_gradient = d_plus_m/length_m`), preserves order, runs both layers. ✅
5. AC #5 — `test_validator.py`: violating + clean fixture per constraint, asserting `constraint_id` + numerics. ✅
6. AC #6 — `test_validator_on_fixture.py`: real GRASP output all-pass + crafted below-θ super-edge caught with correct metadata. ✅
7. AC #7 — ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 606 passed ✅; validator.py 100% coverage. No new deps. ✅

### File List

**New:**
- `src/steeproute/validator.py` — runtime route validation (replaces the placeholder docstring): `validate_route`, `validate_set`, `validate`, plus private `_validate_edges` / `_route_metrics` / `_as_solution`.
- `tests/unit/test_validator.py` — 12 unit tests (violating + clean per constraint, set-level Jaccard, orchestrator metrics/ordering, purity).
- `tests/integration/test_validator_on_fixture.py` — 4 integration tests on the real Grenoble fixture (all-pass by construction, crafted-violation caught, standalone-vs-orchestrator parity).

**Modified:**
- `_bmad-output/implementation-artifacts/3-9-runtime-route-validation.md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked `ready-for-dev → in-progress → review → done`; `last_updated: 2026-06-01`.
- `_bmad-output/implementation-artifacts/deferred-work.md` — 1 deferred finding (metric-finiteness contract) logged under the 2026-06-01 code-review heading.

**Post-review (patches applied):**
- `src/steeproute/validator.py` — `validate` raises on zero-edge `Solution`; per-edge checks deduped over distinct edge identities.
- `tests/unit/test_validator.py` — +2 tests (empty-solution rejection, per-edge dedup on reuse); now 14 unit tests.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-01 | Yann (Claude Opus 4.8) | Code review (3 adversarial layers: blind hunter, edge-case hunter, acceptance auditor) — 14 raw findings → 1 decision-needed + 1 patch + 1 defer + 11 dismissed. **Both patches applied.** **(Decision→fail-loud)** `validate` now raises `ValueError` on a zero-edge `Solution` (closes a latent both-empty-routes spurious-Jaccard-violation; enforces the docstring's "illegal at validator stage" contract). **(LOW)** per-edge constraint checks now run over *distinct* edge identities so a reused edge emits one violation per constraint, not one per traversal. 1 defer logged to `deferred-work.md` (metric-finiteness contract, cross-cutting with solver/pipeline). +2 unit tests (`test_validate_rejects_empty_solution`, `test_validate_route_dedups_per_edge_violations_on_reuse`). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 608 passed (was 606; +2) at 97%; validator.py 100%. Status → done. | _pending_ |
| 2026-06-01 | Yann (Claude Opus 4.8) | Story 3.9 implemented: runtime route validation (`validator.py`, FR26–28, Architecture §Cat 6). Three pure functions — `validate_route` (slope floor on super-edges, difficulty cap, edge-simple reuse, graph membership), `validate_set` (pairwise Jaccard via reused `jaccard_distance`), `validate` (orchestrator: `Solution`→`Route` + metrics, both layers). Edge-reuse open question resolved as the edge-simple invariant. Constraint semantics mirror the solver's RCL filters so GRASP output validates by construction. **New:** `src/steeproute/validator.py`, `tests/unit/test_validator.py` (12), `tests/integration/test_validator_on_fixture.py` (4). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 606 passed (was 590; +16) at 97%; validator.py 100%. No new deps. | _pending_ |
