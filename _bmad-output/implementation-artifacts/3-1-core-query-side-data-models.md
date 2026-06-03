# Story 3.1: Core query-side data models

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want all query-side dataclasses defined in `src/steeproute/models.py` (Edge, Climb, ContractedGraph, SolverParams, Solution, RouteMetrics, ConstraintViolation, RouteValidation, Route, PairwiseViolation, ValidatedRouteSet, ProvenanceInfo),
So that Stories 3.2–3.11 consume a stable data contract and the `@dataclass(frozen=True, slots=True)` discipline from Architecture §"Python code conventions" applies uniformly.

## Acceptance Criteria

1. `src/steeproute/models.py` adds the 12 dataclasses listed in the story statement. Each is `@dataclass(frozen=True, slots=True)`, fully type-hinted (PEP 604 unions, built-in generics, no `Any` outside an inline-commented external boundary), with a one-line module docstring per class explaining its role. Existing `Area` and `PipelineConfig` stay byte-identical (no field changes, no decorator changes).

2. Shapes pinned by the architecture are honoured verbatim: `Route`, `RouteValidation`, `ConstraintViolation`, `PairwiseViolation`, `ValidatedRouteSet` match Architecture §Cat 6b's published field names and types. `SolverParams` exposes the flags listed in Architecture §Cat 9's metadata block (`theta`, `difficulty_cap`, `l_connector`, `min_climb_ground_length`, `j_max`, `n`, `area_cap`, `untagged_policy`, `seed`, `iter_budget`, `time_budget`, `stagnation_iters`). Shapes not pre-specified by the architecture (`Edge`, `Climb`, `ContractedGraph`, `Solution`, `RouteMetrics`, `ProvenanceInfo`) are designed in this story; field choices are documented in the class docstring with a one-line reference to where the type gets consumed downstream so the dev agent in Stories 3.2+ can adopt them without surprises.

3. `tests/unit/test_models.py` exercises every new dataclass and asserts the four invariants the architecture requires: (a) instantiation round-trips every field; (b) `frozen=True` raises `dataclasses.FrozenInstanceError` on any attempt to mutate a field; (c) `slots=True` raises `AttributeError` on any attempt to assign a new attribute; (d) value-based equality holds (two instances with identical fields compare equal). Use a parametrize fixture covering all 12 classes for (b), (c), (d) rather than 12 hand-rolled tests; (a) round-trip is one explicit test per class so the field list is reviewable.

4. No data shape introduced by this story leaks as a loose `dict` or `TypedDict` anywhere in `src/steeproute/`. A targeted ruff/grep check (in dev notes; no new CI rule for one story) confirms the convention — Architecture §"Key anti-patterns to avoid" forbids it.

5. All four CI gates green on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0 errors / 0 warnings / 0 notes), `uv run pytest --cov`. `models.py` lands at ≥95% coverage (pure-logic floor per Architecture §Cat 11e). No new runtime deps.

## Tasks / Subtasks

- [x] Task 1: Add the 5 architecture-pinned classes — `Route`, `RouteValidation`, `ConstraintViolation`, `PairwiseViolation`, `ValidatedRouteSet` — using the published Cat 6b field schemas (AC: #1, #2)
- [x] Task 2: Add `SolverParams` with all 12 flag fields per Architecture §Cat 9 metadata block (AC: #1, #2)
- [x] Task 3: Design + add the 6 architecture-unspecified classes — `Edge`, `Climb`, `ContractedGraph`, `Solution`, `RouteMetrics`, `ProvenanceInfo` — and document the field rationale in each class docstring with a one-line "consumed-by" hint pointing at the relevant Story 3.x (AC: #1, #2)
- [x] Task 4: Write `tests/unit/test_models.py` with the parametrized invariants suite + one round-trip per class (AC: #3)
- [x] Task 5: Verify the loose-dict guard (AC: #4) and run all four CI gates (AC: #5)

## Dev Notes

- **Why this is `models.py` (flat module), not a sub-package.** Architecture §Cat 1 sets the flat-vs-sub-package threshold at "a single file becomes painful." Twelve small dataclasses sit comfortably in one file. Don't pre-split into `models/route.py` / `models/solver.py` / etc. — promote later if the file genuinely grows hot.

- **Architecture-pinned shapes (don't redesign).** Cat 6b at `_bmad-output/planning-artifacts/architecture.md:444-473` shows the `Route` / `RouteValidation` / `ConstraintViolation` / `ValidatedRouteSet` / `PairwiseViolation` shapes verbatim. Copy them. The Cat 6b sample uses bare `@dataclass`; this story upgrades each to `@dataclass(frozen=True, slots=True)` per the cross-cutting conventions rule — the field lists are unchanged.

- **`SolverParams` flag list.** Cat 9 at `_bmad-output/planning-artifacts/architecture.md:614` enumerates the parameters reports must echo. Use those exact 12 names as field names — they double as the JSON-sidecar field names (Architecture §"Serialization conventions" mandates `snake_case`, which matches). `seed` is `int | None` (CLI surface allows the flag to be unset, in which case `cli/_shared.py` resolves a value before constructing `SolverParams`). `time_budget` carries seconds as `float` (matches the click `--time-budget` flag's unit).

- **Shapes this story designs (not pre-pinned).** `Edge`, `Climb`, `ContractedGraph`, `Solution`, `RouteMetrics`, `ProvenanceInfo`. Suggested minimum field sets (refine if a downstream story needs more — promote reactively):
  - `Edge` — the query-side projection of the MultiDiGraph's edge-attribute contract (Architecture §Cat 3c at `architecture.md:260-265`). Carrying `node_u`, `node_v`, `key`, `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale` is enough for the validator + solver + output renderer. `geometry` (shapely) and `vertices_resampled` stay graph-side; `Edge` is the lean value-type the solver and validator pass around.
  - `Climb` — output of pipeline stage 8 (Story 3.2): the underlying `Edge` sequence that forms the climb plus its aggregate `length_m` / `d_plus_m` / `avg_slope`.
  - `ContractedGraph` — output of pipeline stage 9 (Story 3.3): the climb-contracted graph the solver consumes. Holds the contracted `networkx.MultiDiGraph` (typed via `Any` with an inline boundary comment per Architecture §"Type hints and data"), plus the super-edge → `list[Edge]` back-mapping `dict[tuple[int, int, int], list[Edge]]` so the validator can expand super-edges back to the base graph.
  - `Solution` — internal solver type (`solver/` boundary per Architecture §"Boundaries"): the ordered `list[Edge]` (mix of connectors and super-edges via the contracted graph) plus the objective value the solver scored it on. Convert to `Route` at the validator boundary.
  - `RouteMetrics` — `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`. Sum of underlying `Edge` metrics; computed by the route builder, not stored on `Edge`.
  - `ProvenanceInfo` — what `output.py::render(...)` and `cli/query.py` need from `provenance.py`: `steeproute_version: str`, `git_commit_short: str`, `git_dirty: bool`, `osm_extract_date: str` (ISO 8601 per Architecture §"Serialization conventions"), `dem_version: str`, `pipeline_content_hash: str`. The setup-side cache manifest carries the same provenance fields under different naming; pick the names that match the HTML/JSON metadata-block keys, not the manifest's keys.

- **Loose-`dict` discipline (AC #4).** Existing query-side code that currently passes a raw `dict` (none today on the query path; setup-side `Manifest` already uses a dataclass) doesn't need migration. New code in Stories 3.2+ consumes the new types directly. The dev-time check is one `rg "list\[dict\b|dict\[str, Any\]" src/steeproute/` sweep at story end — no new CI rule for one story.

- **Field-order in repro-sensitive types.** `Solution`'s `edges` ordering and `ValidatedRouteSet.set_violations`'s `PairwiseViolation` ordering matter for FR29 (byte-identical reproducibility) — `dataclass(frozen=True)` doesn't dictate iteration order, but the producing code (solver / validator) must produce them deterministically. This story's contract is just shape; ordering discipline is the producers' concern in 3.4 / 3.6 / 3.9. Worth a one-liner in each class docstring noting "ordering supplied by the producer; consumers must not reorder."

- **What this story does NOT do:**
  - Implement any pipeline stage, solver, validator, or renderer logic — every consumer is a downstream story.
  - Touch `Area` or `PipelineConfig` — both stay as-is.
  - Add JSON serialization for any new type — `output.py` (Story 3.10) handles per-field projection; the dataclasses stay plain.
  - Add a `__post_init__` validator on any class — invariant enforcement lives in `validator.py` (Story 3.9), not in `__init__`.

### Project Structure Notes

- **Modified:** `src/steeproute/models.py` — add 12 new dataclasses; leave `Area` / `PipelineConfig` untouched.
- **New:** `tests/unit/test_models.py` — parametrized invariants suite + per-class round-trip.
- **Untouched:** every other source module. Stories 3.2+ import the new types; this story does not.

### Testing standards summary

- Tests in `tests/unit/` per Architecture §"Test organization"; file name mirrors module (`test_models.py` ↔ `models.py`).
- Use `pytest.mark.parametrize` to apply the frozen / slots / equality invariants across all 12 classes — the round-trip test is per-class so the field list shows up explicitly in the diff.
- `dataclasses.FrozenInstanceError` for the frozen assertion (matches the existing convention in `tests/unit/test_cache.py:317` and `tests/unit/test_cache_key.py:148`).
- No new fixtures required; build instances inline per test.
- Coverage floor for `models.py` is 95% (pure-logic, per Architecture §Cat 11e). With 12 plain dataclasses this is straightforward.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 3.1"] — AC source-of-truth
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 6 (6b)] — `Route`, `RouteValidation`, `ConstraintViolation`, `ValidatedRouteSet`, `PairwiseViolation` field schemas
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 9] — `SolverParams` flag list (metadata block contents)
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 3 (3c)] — edge-attribute contract on the MultiDiGraph (`Edge` projects from this)
- [Source: _bmad-output/planning-artifacts/architecture.md §"Python code conventions"] — `frozen=True, slots=True` discipline; no loose `dict`
- [Source: _bmad-output/planning-artifacts/architecture.md §"Type hints and data"] — PEP 604, built-in generics, no `Any` outside boundary comments
- [Source: _bmad-output/planning-artifacts/architecture.md §"Key anti-patterns to avoid"] — loose-dict prohibition
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11 (11e)] — 95% coverage floor for pure-logic modules
- [Source: _bmad-output/planning-artifacts/prd.md §FR10, FR11, FR26-FR29] — downstream functional requirements the data contract serves
- [Source: src/steeproute/models.py] — current module (Area + PipelineConfig); extend in place
- [Source: tests/unit/test_cache.py:310-317] — established `FrozenInstanceError` test pattern to follow

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. No new runtime or dev deps.

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 50 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 455 passed, 1 deselected in ~85s; coverage 96% overall
                                     - models.py 100% (87/87 — every line and branch of the
                                       new 12 dataclasses + 2 pre-existing exercised by the
                                       parametrized invariants + per-class round-trip tests)
```

**Loose-dict sweep (AC #4):** `rg "dict\[str,\s*Any\]|list\[dict\b" src/steeproute/` returns exactly two pre-existing hits — `cache.py:203` (`Manifest.from_dict(cls, payload: dict[str, Any])`, the deliberate JSON-payload boundary) and `cache.py:508` (`entries: list[dict[str, object]]`, internal index-building scratch). No Story 3.1 type leaks as a loose `dict`.

### Completion Notes List

**Design decisions worth review attention:**

1. **`__slots__` introspection instead of "set a new attr".** Architecture §"Python code conventions" requires `slots=True`. The natural test (`instance.new_attr = "x"` raises `AttributeError`) doesn't work cleanly on a `frozen=True, slots=True` dataclass because frozen's `__setattr__` raises `FrozenInstanceError` first. The parametrized `test_dataclass_uses_slots` instead asserts `hasattr(cls, "__slots__")` AND `set(cls.__slots__) == {field names}` — that's the canonical signal that slots are in effect, and it catches the realistic regression (somebody drops `slots=True` and `__slots__` disappears or becomes empty `()`).

2. **`ContractedGraph` equality via shared `_SHARED_GRAPH`.** The value-equality parametrize test would naturally fail on `ContractedGraph` because two `nx.MultiDiGraph()` instances aren't equal (networkx doesn't define `__eq__`; falls back to identity). The factory shares a module-level `_SHARED_GRAPH` instance so the `graph` field compares by identity (True). Story 3.1's contract is just the dataclass shape; graph-content equality is an Epic-3-downstream concern (the solver/validator tests will exercise graph equality where it matters).

3. **`Edge` uses `tuple[Edge, ...]` for `Climb.edges` and `Solution.edges` but `list[Edge]` for `Route.edges`.** Route's shape matches Architecture §Cat 6b verbatim (`edges: list[Edge]`); `Climb` and `Solution` are this-story-designed types where tuple is the better default for structural-immutability. Producer in Story 3.9 will convert `Solution`'s tuple to a list when minting the `Route` — trivial bridge, but the typing is honest about which side is mutable-shaped.

4. **`SolverParams.seed: int | None` matches the CLI-flag boundary.** The seed resolver in `cli/_shared.py` will fill in a system-generated value when the user omits `--seed`. The dataclass admits `None` so the parsed-but-unresolved state is representable (transient — the resolver runs before `SolverParams` lands in the solver). This matches FR29's intent: explicit seed → reproducible; implicit seed → resolver picks one and records it in the report's metadata block.

5. **`ContractedGraph.graph: Any` boundary comment.** networkx 3.x ships partial type stubs; tightening this to `nx.MultiDiGraph` would force every downstream consumer to fight `reportUnknownMemberType` / `reportMissingTypeArgument` on every `.nodes` / `.edges` access. Architecture §"Type hints and data" explicitly admits `Any` at external boundaries with an inline comment — applied here with a comment pointing at the network-x-stubs rationale. Same defensive pattern as `pipeline/osm.py`'s top-of-file pragma.

6. **`ProvenanceInfo` field naming follows the *report* metadata block, not the manifest.** Architecture §Cat 9 lists `steeproute_version` / `git_commit_short` + `-dirty` flag / OSM extract date / DEM version / pipeline content hash for HTML+JSON metadata. The cache `Manifest` (Story 2.6) uses `steeproute_commit` (combined "abc1234-dirty" form) and lives in `cache.py`. `ProvenanceInfo` splits commit + dirty into separate fields (`git_commit_short` + `git_dirty: bool`) so the renderer can format them consistently across reports; the values themselves are echoed from the cache hit's manifest by `output.py` (Story 3.10) when it constructs the `ProvenanceInfo`.

**AC walkthrough — evidence per criterion:**

1. AC #1 — All 12 new dataclasses (`Edge`, `Climb`, `ContractedGraph`, `SolverParams`, `Solution`, `RouteMetrics`, `ConstraintViolation`, `RouteValidation`, `Route`, `PairwiseViolation`, `ValidatedRouteSet`, `ProvenanceInfo`) added to `src/steeproute/models.py` with `@dataclass(frozen=True, slots=True)`. Type hints use PEP 604 unions (`str | None`, `int | None`) and built-in generics (`list[X]`, `dict[str, float]`, `tuple[Edge, ...]`); the only `Any` is on `ContractedGraph.graph` with an inline boundary comment per Architecture §"Type hints and data". `Area` and `PipelineConfig` are untouched (`git diff` shows them unchanged). Each class has a one-line-or-more module-docstring-style explanation of its role and downstream-consumer hints. ✅

2. AC #2 — Cat 6b shapes (`Route`, `RouteValidation`, `ConstraintViolation`, `PairwiseViolation`, `ValidatedRouteSet`) match the architecture's published field schemas verbatim. `SolverParams` exposes all 12 Cat 9 metadata-block fields (`theta`, `difficulty_cap`, `l_connector`, `min_climb_ground_length`, `j_max`, `n`, `area_cap`, `untagged_policy`, `seed`, `iter_budget`, `time_budget`, `stagnation_iters`). Story-designed shapes (`Edge`, `Climb`, `ContractedGraph`, `Solution`, `RouteMetrics`, `ProvenanceInfo`) are documented in their class docstrings with rationale + consumed-by Story 3.x pointers. ✅

3. AC #3 — `tests/unit/test_models.py` has the four invariants: (a) per-class round-trip tests (12 explicit tests, one per class, plus `test_edge_accepts_none_sac_scale` and `test_solver_params_accepts_none_seed` pinning the union types); (b) `test_dataclass_is_frozen` parametrized across all 12 classes — asserts `FrozenInstanceError` on field reassignment via `setattr`; (c) `test_dataclass_uses_slots` parametrized across all 12 classes — asserts `__slots__` exists and equals the field-name set (canonical slots signal; see Completion Note #1 for why this is the right check, not `attr=`); (d) `test_dataclass_value_equality` parametrized across all 12 classes — two factory invocations produce equal instances. 50 tests in total. ✅

4. AC #4 — Loose-dict sweep confirms no Story 3.1 type leaks: `rg "dict\[str,\s*Any\]|list\[dict\b" src/steeproute/` returns only pre-existing `cache.py` hits (deliberate JSON boundary + internal index scratch). New types (Edge, Climb, etc.) are values, not dicts. ✅

5. AC #5 — `uv run ruff check` ✅, `uv run ruff format --check` ✅, `uv run basedpyright` 0/0/0 ✅, `uv run pytest --cov` 455 passed at 96% overall, `models.py` at 100% coverage (87/87 statements — every new dataclass is fully exercised by the parametrized invariants + per-class round-trips). No new runtime deps. ✅

### File List

**New:**
- `tests/unit/test_models.py` — 50 tests covering all 12 new dataclasses across the four AC #3 invariants (frozen / slots / value-equality / round-trip), plus union-type pin tests for `Edge.sac_scale` and `SolverParams.seed`. Module-level `_SHARED_GRAPH: nx.MultiDiGraph` lets `ContractedGraph` equality fall back on identity for the graph field (networkx doesn't define value-equality on graphs). Factory functions per class keep the field lists in one reviewable place; the parametrized suite drives them through the invariants.

**Modified:**
- `src/steeproute/models.py` — extended from the 2-class Epic-2 baseline (`Area`, `PipelineConfig` unchanged) with the 12 Story 3.1 dataclasses: `Edge`, `Climb`, `ContractedGraph`, `SolverParams`, `Solution`, `RouteMetrics`, `ConstraintViolation`, `RouteValidation`, `Route`, `PairwiseViolation`, `ValidatedRouteSet`, `ProvenanceInfo`. Each is `@dataclass(frozen=True, slots=True)` with PEP 604 unions and built-in generics; the only `Any` is `ContractedGraph.graph` (networkx-stubs boundary, inline-commented). Module docstring expanded to describe the data-contract role. `from typing import Any` added.

**Updated (out-of-source):**
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story `3-1-core-query-side-data-models` walked `backlog → ready-for-dev → in-progress → review`; `epic-3` flipped to `in-progress` on first-story creation. `last_updated: 2026-05-25`.

**Untouched (intentionally):**
- `src/steeproute/{cache.py, validator.py, output.py, provenance.py, ...}` and every consumer of these new types — Stories 3.2+ pick them up from the contract. This story is the foundation; downstream stories are the consumers.
- `src/steeproute/models.py` pre-existing `Area` + `PipelineConfig` — `git diff` confirms zero changes to those two classes.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-25 | Yann (Claude Opus 4.7) | Story 3.1 implemented: query-side data contract for Epic 3. **`src/steeproute/models.py`** extended with 12 `@dataclass(frozen=True, slots=True)` types — Cat-6b-pinned shapes (`Route`, `RouteValidation`, `ConstraintViolation`, `PairwiseViolation`, `ValidatedRouteSet`) match architecture verbatim; `SolverParams` exposes the 12 Cat-9 metadata-block fields 1:1 with the CLI flag surface; six story-designed types (`Edge` as the lean MultiDiGraph projection, `Climb` as stage-8 output, `ContractedGraph` with super-edge back-mapping for the validator, `Solution` as the solver-internal type, `RouteMetrics` as the route-builder aggregate, `ProvenanceInfo` for the renderer's metadata block) all carry docstring rationale + downstream-consumer hints. Type hints honour Architecture §"Type hints and data": PEP 604 unions, built-in generics, single `Any` boundary on `ContractedGraph.graph` (networkx-stubs gap, inline-commented). **`tests/unit/test_models.py`** new file with 50 tests pinning the four AC #3 invariants — `test_dataclass_is_frozen` / `test_dataclass_uses_slots` / `test_dataclass_value_equality` parametrized across all 12 classes (36 cases) plus per-class round-trip tests (12 cases) plus the two union-type pins (`Edge.sac_scale = None`, `SolverParams.seed = None`). `ContractedGraph` value-equality uses a module-level `_SHARED_GRAPH` to sidestep networkx's identity-based `__eq__`; that's a test-tier concession only, not a contract weakening. All four CI gates green: ruff ✅, ruff format ✅, basedpyright 0/0/0 ✅, pytest --cov 455 passed at 96% overall coverage with `models.py` at 100% (87/87). No new runtime or dev deps. AC #4 loose-dict sweep clean — only pre-existing `cache.py` JSON-boundary hits. | _pending_ |
