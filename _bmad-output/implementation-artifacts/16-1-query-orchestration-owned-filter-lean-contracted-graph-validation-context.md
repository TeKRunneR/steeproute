# Story 16.1: Query orchestration batch — owned filter, lean contracted graph, one validation context

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the query to stop duplicating graph state it already owns — rebuilding the graph to filter it,
rebuilding it again to strip two attributes, and rescanning it once per route to validate,
so that whole-query wall-clock and peak memory drop with byte-identical output.

## Acceptance Criteria

1. **Owned query filter.** `filter_trails` gains a keyword-only opt-in that removes the rejected
   edges from the caller's graph and returns that same object instead of building a new one via
   `empty_like`. The default remains the copying, input-never-mutated path — every existing caller
   (setup stage 2, all fixtures, `tests/unit/test_osm.py`'s purity and cap-sweep tests) is unchanged
   and unmutated. Filter *semantics* are untouched: same `classify_highway` / `max_sac_rank` /
   untagged-policy decisions, same surviving-edge set, same edge iteration order.
2. **Query CLI opts in and the render contract is corrected.** `cli/query.py`'s difficulty-cap redux
   uses the consuming path, so the render graph *is* the filtered graph. The comment block asserting
   `operational_graph` is "strictly a superset" is replaced by the real invariant — every base edge
   reachable from a solver route (including an FR28 failed route) is by construction in the filtered
   graph, because contraction was built from it. No rendered route loses geometry.
3. **Lean contracted graph.** `geometry` / `vertices_resampled` never land on contracted connector
   edges, and the `ContractedGraph` advertises that lean contract so `run_parallel_grasp` serializes
   it directly instead of calling `solver_graph_view`. `solver_graph_view` stays public and correct
   for a non-lean graph (its existing integration test keeps passing unchanged), and the
   `ParallelGraspFailed` guard still wraps exactly the parallel-only serialization step — shared
   setup above that guard still propagates rather than falling back.
4. **One validation context.** The non-exempt base-segment ID set and the base-segment map are each
   built once per `validate` call and shared across every per-route check and the set-level check
   (today: one full ~327k-edge scan *per route*), and the duplicate per-route `_route_metrics`
   recomputation inside `_validate_edges` is gone. `validate_route(route, graph, params)` and
   `validate_set(routes, params, graph=None)` keep their current call shapes and build a context when
   none is supplied — the 17 standalone `validate_route` test call sites and
   `test_validate_route_matches_orchestrator` pass untouched.
5. **Byte-identical output.** SHA-256 over every HTML + JSON output file matches the pre-change run on
   the exact r20 command below and on at least one committed fixture area. Pinned regression goldens
   pass with **no rebake** (AGENTS.md golden policy — a rebake here would mean the change is not
   behavior-preserving and is a defect, not a documented update).
6. **Suite and types green.** The full offline suite passes (~842 tests), `basedpyright` is clean, and
   the 95%-coverage bar on `pipeline/` and `validator.py` is held by direct unit tests for the new
   consuming path and the new validation context — not incidental coverage.
7. **Benchmarks added before the change.** `tests/benchmarks/` gains a query-path `filter_trails`
   benchmark (the operational graph at the user difficulty cap, not the setup-side T6 one) and a
   `validate` benchmark over a real route set — both recorded before/after.
8. **End-to-end measurement recorded.** CLI-reported total, external process wall, and peak RSS are
   captured before and after on the r20 workload, with the per-stage `trail-filter`,
   `climb-contraction`, and `validate-render` lines. Review anchors to reproduce in *shape*, not
   exact value: ~80.0 → 67.3 s CLI, ~2.67 → 2.05 GB peak RSS.
9. **Docs match the code.** Architecture §Cat 5a's lean-graph paragraph (which currently states the
   parent keeps the *full* contracted graph for validation/render) and §Cat 3 stage 9's attribute
   contract are updated, along with the `ContractedGraph` docstring's "on connectors only:
   geometry/vertices_resampled" clause.

**Out of scope:** the setup-side `filter_trails` call site (stage 2 keeps the copying path — it is
immediately followed by another `empty_like` rebuild in `_drop_orphan_nodes`, and Story 16.2 owns
setup ownership work); any cache schema or on-disk format change; any of the review's quality-altering
non-optimizations (`theta`, `j_max`, difficulty/climb/descent/junction constraints, iteration budget,
RNG partitioning).

## Tasks / Subtasks

- [x] Pin the "before" measurement first (AC: #7, #8)
  - [x] Add the two missing benchmarks in [tests/benchmarks/test_setup_stages.py](tests/benchmarks/test_setup_stages.py:87)'s
        query-side section (or a sibling module): query-path `filter_trails` over the operational
        graph at `BENCH_DIFFICULTY_CAP`, and `validate` over a real route set. Params stay pinned
        locally per [tests/benchmarks/conftest.py](tests/benchmarks/conftest.py:8) — never imported
        from CLI defaults or `steeproute.regression`
        → new sibling module `tests/benchmarks/test_query_orchestration.py` + two session fixtures
        (`operational_graph`, `solved_route_set`). Before: filter 33.76 ms, validate 21.65 ms (mean)
  - [x] Capture the baseline r20 run (command in Dev Notes): stage lines, CLI total, external wall,
        peak RSS, and `sha256` of every file in the output dir. Keep the baseline output dir for the
        after-comparison
        → 20 output files pinned; two independent baseline runs are SHA-256 identical (determinism
        confirmed, so byte-identity is a valid gate). See Debug Log for the three blockers hit.
- [x] Consuming query filter (AC: #1, #2)
  - [x] Add the keyword-only opt-in to `filter_trails` in [osm.py](src/steeproute/pipeline/osm.py:219):
        collect the rejected `(u, v, k)` identities in one pass, then remove them from the input graph
        and return it. Google-style docstring documents the ownership forfeiture; the `Returns:`
        block's "input graph is never mutated" sentence gains the explicit exception
        → `consume: bool = False`. Both paths now share one `keep(data)` predicate so the two
        cannot drift on filter semantics (AC #1's "same decisions" guarantee)
  - [x] Opt in at [query.py:314](src/steeproute/cli/query.py:314) and rewrite the comment block at
        [query.py:301-312](src/steeproute/cli/query.py:301) to state the real render invariant
        (AC #2) instead of the superset claim
  - [x] Unit tests in [tests/unit/test_osm.py](tests/unit/test_osm.py:110): the default still leaves
        the input untouched (existing purity test unchanged), the opt-in returns the *same object*
        with the rejected edges gone, and both paths produce the same ordered surviving-edge list and
        the same node set for the same input
        → 4 new tests incl. edge-order/node-order/edge-data equality across 6 policy×cap combos and
        validation-before-mutation
  - [x] Integration check that a rendered route's vertices are unchanged when the render graph is the
        filtered graph — extend the existing wiring in
        [tests/integration/test_elevation_consistency.py](tests/integration/test_elevation_consistency.py:125)
        rather than adding a parallel harness
- [x] Lean contracted graph (AC: #3)
  - [x] Stop splatting the heavy attrs onto connector edges in
        [contract_climbs](src/steeproute/pipeline/graph.py:160) and advertise the lean contract on
        `ContractedGraph` (see Dev Notes for the recommended shape and the `models.py` caveat)
        → `lean: bool = False` field; `contract_climbs` returns `lean=True`. `HEAVY_EDGE_ATTRS`
        moved from `solver/parallel.py` to `models.py` (re-exported) — `pipeline/` must not import
        from `solver/`, and the set is now part of the contracted-graph contract
  - [x] Skip `solver_graph_view` in [run_parallel_grasp](src/steeproute/solver/parallel.py:491) when
        the graph is already lean, keeping `pickle.dumps` inside the existing `ParallelGraspFailed`
        `try` block
  - [x] Keep `solver_graph_view` exported and correct for a non-lean input; its behaviour test at
        [tests/integration/test_parallel_grasp.py:158](tests/integration/test_parallel_grasp.py:158)
        must pass unchanged → passes untouched; it now also marks its own output `lean=True`
  - [x] `test_contracted_graph_pickle_size`
        ([tests/benchmarks/test_parallel_speedup.py:37](tests/benchmarks/test_parallel_speedup.py:37))
        now reports the lean payload rather than the full one — refresh its docstring/comment and
        record the new number (expect ~73 MB @ r20 where it previously reported the full graph)
        → docstring refreshed + asserts `contracted_graph.lean`
  - [x] Unit test in [tests/unit/test_graph_contraction.py](tests/unit/test_graph_contraction.py:190)
        that contracted connectors carry no `geometry` / `vertices_resampled` while every other
        base attribute (`highway`, `osm_way_id`, `sac_scale`, metrics) still passes through, and that
        `super_edge_to_base` back-expansion against the base graph is unaffected
  - [x] Added: integration test that a `lean=True` graph is not rebuilt while a `lean=False` one
        still is, with identical solutions either way
- [x] One validation context (AC: #4)
  - [x] Build the context once in [validate](src/steeproute/validator.py:150) — non-exempt IDs, base
        segment map, and the already-computed `RouteMetrics` — and thread it into `_validate_edges`
        and `validate_set`, replacing the per-route `non_exempt_base_segment_ids(graph)` call at
        [validator.py:310](src/steeproute/validator.py:310) and the `_route_metrics(list(edges))`
        recomputation at [validator.py:201](src/steeproute/validator.py:201)
        → private `_GraphContext` frozen dataclass in `validator.py` (not `models.py`)
  - [x] Keep `validate_route` / `validate_set` standalone: a context is built lazily when not
        supplied, and the new parameter is keyword-only so the documented positional signatures in
        Architecture §Cat 6d still hold
  - [x] Unit tests in [tests/unit/test_validator.py](tests/unit/test_validator.py:504): the context is
        built once for an N-route `validate` (assert via a counting spy on the reuse helpers), and a
        standalone `validate_route` result equals the orchestrator's for the same route
        → spy pins 1 scan for 3 routes (was 3); second test pins `validate` == standalone
        `validate_route` per route on clean / below-θ / reused fixtures
- [x] Verify and record (AC: #5, #6, #8, #9)
  - [x] SHA-256 comparison against the pinned baseline output dir on the r20 command and on a
        committed fixture; `uv run pytest tests/e2e/test_pinned_regressions.py` green with no golden
        change in `git status`
        → r20: **all 20 files (10 HTML + 10 JSON) SHA-256 identical**, verified four ways incl.
        old-vs-new code back-to-back across a `git stash` boundary. Goldens (fast + slow tier) pass
        with **no rebake**; `git status tests/e2e/goldens/` empty
  - [x] Full offline suite per-directory (AGENTS.md: never mix `tests/unit` and `tests/integration` in
        one invocation) + `uv run basedpyright` on the touched files
        → 888 unit / 223 integration / 110 e2e / 5 slow, all pass; `basedpyright src tests` 0 errors
        (1 pre-existing warning in `test_cache.py`, untouched); `ruff check` clean
  - [x] Re-run the r20 measurement and record before/after (three numbers + three stage lines) in the
        Completion Notes
  - [x] Update Architecture §Cat 5a, §Cat 3 stage 9, and the `ContractedGraph` docstring
        → also added §Cat 3 "Ownership opt-ins on the pure boundary (3a-bis)" (the `inplace=` vs
        `consume=` convention was undocumented) and a §Cat 6d paragraph on the validation context

## Dev Notes

**The three changes are independent — land and verify them one at a time.** Each is individually
byte-identity-checkable; only the wall-clock claim needs all three. The combined POC that produced the
epic's anchor numbers is documented in
[the performance review §1](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:153),
including the per-change isolated timings — read §1a/1b/1c before starting each part.

**Editing `pipeline/**` re-keys the cache, and that is fine here.** `filter_trails`, `contract_climbs`,
and `models.py` are all inside `_PIPELINE_CONTENT_GLOBS` ([cache.py:68](src/steeproute/cache.py:68)),
so `pipeline_content_hash` shifts and a *future* `steeproute-setup` writes under a new key
(re-prepare once). **Query reads are unaffected**: `check_coverage`
([cache.py:1397](src/steeproute/cache.py:1397)) resolves by geometric containment of indexed entries
and never compares the content hash, so the warm r20 entry in `.trial-cache/` and the committed
`tests/e2e/fixtures/*/cache/` roots stay queryable. Report byte-identity also survives, because
`_build_provenance` ([query.py:553](src/steeproute/cli/query.py:553)) echoes the *manifest's* stored
hash/version/commit rather than the live tree, and `output.py` embeds no timestamp.

**Why in-place filtering is render-safe (AC #2).** `output.render` reads the base graph only through
`_edge_vertices` ([output.py:259](src/steeproute/output.py:259)) for per-route vertex expansion — there
is no whole-network overlay layer. Route edges are super-edges and connectors of the contracted graph,
which was built from the filtered graph, so every base edge a route expands to is present. Note
`_edge_vertices` returns `[]` for an absent edge rather than raising, so a regression here would be
*silent* — pin it with the vertex-equality assertion in the task list, not by inspection.

**Naming the consuming path.** Two conventions already exist: keyword-only `inplace: bool = False` on
leaf stages that add attributes to the same object
([compute_edge_metrics](src/steeproute/pipeline/climbs.py:82),
[graph_smooth_elevation](src/steeproute/pipeline/smoothing.py:228)), and `consume: bool = False` on the
orchestrator-level group that takes whole-graph ownership
([operationalize_graph](src/steeproute/pipeline/__init__.py:226), whose docstring at lines 249-257 is
the wording template). This change is structural and the caller forfeits the unfiltered view, so
`consume=` is the better fit — matching the `consume=True` call already sitting eight lines above at
[query.py:294](src/steeproute/cli/query.py:294). Either is defensible; pick one and be consistent.

**Advertising the lean contract.** A defaulted `frozen`/`slots` field on `ContractedGraph`
([models.py:167](src/steeproute/models.py:167)) is the typed, pickle-surviving option and needs no
change at existing construction sites; `solver_graph_view` should mark its own output lean too. The
alternative — a marker in the nx graph-level attr dict — is unused in production today and would be
silently dropped by both `contract_climbs` and `solver_graph_view`, which each start from a bare graph
and never propagate `graph.graph`. Note Story 14.4's precedent
(Architecture §Cat 5a, [architecture.md:408](_bmad-output/planning-artifacts/architecture.md:408)) that
solver *orchestration* knobs stay out of `models.py` to avoid re-keying caches — that reasoning does
not block a genuine attribute of the contracted-graph data contract, and this story re-keys anyway.

**Where the validation context lives:** `validator.py`, not `models.py` — it is derived state, not
pipeline data crossing a stage boundary (same §Cat 5a reasoning as above). Use
`@dataclass(frozen=True, slots=True)` per Architecture §Implementation Patterns.

**Determinism.** Both filter paths preserve survivor order (in-place removal keeps insertion order;
the rebuild adds in iteration order), which the review verified as an "identical ordered edge list".
`non_exempt_base_segment_ids` is an order-independent `frozenset` union and `blocking_ids` an
intersection, so hoisting them cannot reorder anything. Canonical edge-set hashing is sorted by
`(u, v, key)` regardless (Architecture §Numerical and data discipline). Still verify empirically —
zero-tolerance goldens are the gate, not the argument.

**Measurement (AGENTS.md §Scale target — end-to-end, not micro-benchmarks).** The r20 entry is
prepared at `.trial-cache/` (key `9b2e739d3113b2f7`, center 45.260,5.788, 20 km). Query command:

```
--seed 44 --l-connector 50 --j-max 0 --difficulty-cap T4 --n 10 --iter-budget 1000000
--elevation-deadband 1 --elevation-smoothing 50 --progress-interval 1 --stagnation-iters 0
--max-descent-slope 0.4 --start-at-junction --area-cap 1500 --workers 4 --merge-interval 250000
```

CLI total comes from the `wall_clock_total` line in the run summary; stage lines from
`ProgressReporter.stage()`. For peak RSS on Windows, use an in-process `GetProcessMemoryInfo` wrapper
(throwaway, not committed) — external `Start-Process` polling measures the `steeproute.exe` launcher
stub, not the worker, as recorded in
[Story 8.3's notes](_bmad-output/implementation-artifacts/8-3-readme-gallery-with-3-5-pre-computed-example-reports.md:101).
Report the shape of the win, not a machine-specific number; the review's anchors were measured on
WSL2/14-core.

**Environment.** `uv run basedpyright <files>`; `uv run pytest` per test directory. If `uv run` starts
failing en masse in `tests/e2e/test_cli_smoke.py` after a commit, that's the known stale-editable-build
flake — `uv sync --native-tls` once (never `--reinstall-package steeproute`).

### Project Structure Notes

- Files touched: `pipeline/osm.py`, `pipeline/graph.py`, `models.py`, `solver/parallel.py`,
  `validator.py`, `cli/query.py` — each the module the FR→module mapping already assigns to this
  behaviour; no new modules.
- Tests land in the mirrored existing files (`tests/unit/test_osm.py`,
  `tests/unit/test_graph_contraction.py`, `tests/unit/test_validator.py`) per Architecture §Test
  organization. Benchmarks go in `tests/benchmarks/` — a fourth layer that Architecture §Cat 11 does
  not document; follow the existing modules' conventions (`pytestmark = pytest.mark.benchmark`,
  session-scoped fixtures, locally pinned params).
- Known doc/code divergence, do not "fix" as part of this story: Architecture §Boundaries says outside
  code calls orchestrator functions in `pipeline/__init__.py` rather than individual stages, but
  `cli/query.py` has imported `filter_trails` directly since the stage-6-7 query-side move.

### References

- [epics.md#Epic 16 / Story 16.1](_bmad-output/planning-artifacts/epics.md:400) — epic thesis, tiered
  confidence, and this story's Given/When/Then
- [sprint-change-proposal-2026-07-24-ownership-oriented-performance.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-ownership-oriented-performance.md:78)
  — confidence table and per-story guardrails
- [performance review §1a/1b/1c/1d](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:155)
  — the measured POC for each of the three changes plus the combined byte-identical proof
- [architecture.md §Cat 3](_bmad-output/planning-artifacts/architecture.md:234) — pure-function stage
  boundary (3a), stage-9 contraction attribute contract, edge-attribute contract (3c)
- [architecture.md §Cat 4b/4c](_bmad-output/planning-artifacts/architecture.md:294) — cache-key inputs
  including the pipeline content hash; on-disk format (not changed here)
- [architecture.md §Cat 5a](_bmad-output/planning-artifacts/architecture.md:390) — lean graph view,
  worker pickling, `ParallelGraspFailed` scope, `--workers 1` byte-identity
- [architecture.md §Cat 6d](_bmad-output/planning-artifacts/architecture.md:492) — validator function
  signatures and independent testability
- [architecture.md §Cat 11](_bmad-output/planning-artifacts/architecture.md:1017) — coverage bars,
  zero-tolerance golden gate, `pytest.skip`/`xfail` prohibition
- [AGENTS.md](AGENTS.md) — golden policy, Scale-target measurement rule, dev-environment commands
- [README.md](README.md) — `update-regression` workflow (should not be needed for this story)

## Dev Agent Record

### Agent Model Used

claude-opus-5 (Claude Code, `dev-story` workflow)

### Debug Log References

Four things in this story's own Dev Notes turned out to be wrong or unusable. Recorded here because
the next Epic 16 story will hit the same three environment items.

1. **The review's r20 command is now rejected by the CLI.** `--area-cap 1500` predates Story 15.3,
   which changed area measurement from the `π·r²` disk proxy to the true box area (`width × height`).
   r20 is a 40 × 40 km box = 1600 km² > 1500 → `BadCLIArgError`. Used `--area-cap 100000` (AGENTS.md's
   practical no-op ceiling) for every run. The cap is a pre-flight validation gate the solver never
   reads, so this does not affect comparability.
2. **The r20 reference cache was schema-incompatible, which the Dev Notes missed.** The note that
   `check_coverage` ignores `pipeline_content_hash` is correct, but Story 15.2 bumped the *manifest*
   schema 2 → 3 and `Manifest.from_dict` rejects any non-current version outright (no compat shim).
   Fixed by the migration Story 15.2 documents for exactly this case — editing `schema_version` alone,
   since "a square's `area` block is byte-identical across v2 and v3"
   (`tests/e2e/fixtures/grenoble_small/README.md`); `graph.pkl` untouched, v2 manifest backed up to
   the scratchpad. This is a gitignored local measurement cache, not repo data.
3. **Wall-clock on this machine drifted ~30% during the session** (a suspend/resume mid-run, then
   progressive slowdown). Two runs of *identical unmodified* code gave 94.93 s and 110.19 s, and
   untouched stages moved as much as the touched ones (`elevation-reshape` 11.78 → 18.95 s). Any
   before/after taken minutes apart is therefore untrustworthy. Final numbers come from a strict
   back-to-back A/B: `git stash push -- src/` → measure → `git stash pop` → measure. Peak RSS, by
   contrast, was stable to 0.01% across runs and is the cleanest evidence in this story.
4. **My first version of the query-filter benchmark measured the wrong path.** It called
   `filter_trails` without `consume=True`, i.e. the unchanged copying path, and duly showed no
   change. Split into `_copying` and `_consuming` benchmarks; the consuming one uses
   `benchmark.pedantic(setup=...)` so the required per-round `graph.copy()` stays outside the
   measured region.

### Completion Notes List

**All three changes landed with byte-identical output.** The r20 result is the headline: 20 output
files (10 HTML + 10 JSON) SHA-256 identical, confirmed four ways — before/after, old-code/new-code
back-to-back across a `git stash` boundary, and both baseline runs against each other (which also
proved the run is deterministic before any refactor, making SHA-256 a valid gate).

**r20 back-to-back A/B** (`--workers 4`, 1M iters, warm `.trial-cache` r20, 14-logical-core machine;
adjacent runs, old code via `git stash`):

| Measure | old | new | Δ |
|---|---:|---:|---|
| `trail-filter` | 6.15 s | **0.46 s** | **−93%** |
| `validate-render` | 7.55 s | **2.67 s** | **−65%** |
| `climb-contraction` | 11.00 s | 7.51 s | −32% (lean build; partly noise) |
| CLI `wall_clock_total` | 110.19 s | **78.84 s** | **−28%** |
| peak working set | 2.434 GB | **1.832 GB** | **−24.7%** |
| routes / objective | 10/10, 21100.0 | same | exact |

Across all runs (2 old, 2 new): `trail-filter` 4.60–6.15 → 0.46–0.64 s; `validate-render`
7.55–9.45 → 2.64–2.67 s; CLI total 94.9–110.2 → 78.2–78.8 s; peak RSS 2.434 → 1.832 GB both times.
**Honest caveat:** untouched stages varied up to ±40% between runs, so the CLI-total range
(−17.7% to −28%) is noisy at its edges. What is not noisy: the two targeted stage lines moved by
65–93% in every pairing, and peak RSS is reproducible to 0.01%. The review's anchors (−15.9% CLI,
−23.4% RSS, filter 4.19 → 0.57 s, validate 6.55 → 2.87 s) are reproduced in shape and slightly
exceeded — as expected, since the new code is also ~15× more consistent run-to-run (78.15 / 78.84 s
vs 94.9 / 110.2 s), plausibly because 0.6 GB less resident memory makes it less sensitive to system
state.

Component benchmarks (`grenoble_small`, same run so drift-immune): query filter **12.05 → 3.07 ms
(~4×)** copying vs consuming. The `validate` benchmark shows no material change and says so in its
docstring — a 1.5 km area's contracted graph is too small for the hoisted rescans to register; per
AGENTS.md §Scale target it is kept as a "does not get slower" guardrail, not as evidence, and the r20
stage line carries the claim.

**Design decisions worth review attention:**

- Both `filter_trails` paths share one `keep(data)` predicate, so they cannot drift on filter
  semantics. The cost is one extra Python call per edge on the **setup-side** copying path, which
  runs over the larger raw graph — so this story could in principle pay its query win back as a
  setup regression. **Measured on a real r20 setup** (warm caches, no network: cached Overpass
  response + cached DEM raster; isolated scratch cache root so the r20 reference entry was never at
  risk; old code via `git stash`, `areas/` cleared between runs so both re-ran the full pipeline):

  | | old | new |
  |---|---:|---:|
  | `trail-filter` stage | 17.41 s | 14.44 s |
  | total external wall | 251.03 s | **251.78 s (+0.3%)** |

  **Setup is unchanged.** The touched stage moved 3 s the *opposite* way from the predicted
  regression, while untouched control stages in the same pair moved by up to 33%
  (`polyline-smoothing` 13.44 → 8.99 s, `osm-download` 159.29 → 169.76 s) — so the per-stage noise
  band is ±3 s and a ~0.1 s effect is not resolvable at this scale. The `grenoble_small` benchmark
  can resolve it (+0.76% on the Min statistic over ~100 rounds, 6.727 → 6.778 ms) but that number
  should **not** be projected onto r20 — per AGENTS.md §Scale target, the r20 total above is the
  claim, and it is that setup wall-clock is unaffected within noise.

  If a reviewer wants the overhead at exactly zero anyway, the fix is a single unified loop with the
  decision inlined (one copy of the semantics, no per-edge call). Deliberately not done: it trades
  readability for an effect that a real r20 setup cannot detect.
- `HEAVY_EDGE_ATTRS` moved to `models.py`. Required: `pipeline/graph.py` now needs it and
  `pipeline/` importing from `solver/` would invert the layering. Re-exported from `solver.parallel`,
  so every existing import still resolves.
- `ContractedGraph.lean` defaults to `False` — the conservative direction. A false `False` costs one
  rebuild; a false `True` would ship the heavy payload to every worker.
- `_GraphContext` is private to `validator.py`, not in `models.py`, because `models.py` is
  content-hashed into every cache key (Story 14.4's precedent for `workers`).
- The render-safety invariant replaced the old "strictly a superset" comment at the call site rather
  than being argued: an integration test pins rendered vertices vertex-for-vertex against the
  superset path, because `output._edge_vertices` returns `[]` for a missing edge instead of raising,
  which would make a regression here silent.

**Not touched:** `_bmad-output/planning-artifacts/future-ideas.md` shows as modified in `git status` —
that is Yann's own edit made during this session, deliberately left alone.

### File List

Source:
- `src/steeproute/pipeline/osm.py` — `filter_trails` `consume=` opt-in + shared `keep` predicate
- `src/steeproute/pipeline/graph.py` — lean contraction, `lean=True`, docstring contract
- `src/steeproute/models.py` — `HEAVY_EDGE_ATTRS` (moved here), `ContractedGraph.lean`
- `src/steeproute/validator.py` — `_GraphContext`, one derivation per `validate` call
- `src/steeproute/solver/parallel.py` — skip `solver_graph_view` for a lean graph; re-export
- `src/steeproute/cli/query.py` — `consume=True` at the redux + corrected render invariant

Tests:
- `tests/unit/test_osm.py` — 4 consuming-path tests
- `tests/unit/test_graph_contraction.py` — 2 lean-contraction tests
- `tests/unit/test_validator.py` — 2 validation-context tests
- `tests/integration/test_elevation_consistency.py` — rendered-vertices equality guard
- `tests/integration/test_parallel_grasp.py` — lean-graph rebuild-skip test
- `tests/benchmarks/test_query_orchestration.py` — **new**, 3 benchmarks
- `tests/benchmarks/conftest.py` — `operational_graph` + `solved_route_set` fixtures
- `tests/benchmarks/test_parallel_speedup.py` — pickle-size benchmark now measures the lean payload

Docs:
- `_bmad-output/planning-artifacts/architecture.md` — §Cat 3 stage 9 + new 3a-bis, §Cat 5a worker
  payload / setup hardening, §Cat 6d validation context
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/16-1-query-orchestration-owned-filter-lean-contracted-graph-validation-context.md`
