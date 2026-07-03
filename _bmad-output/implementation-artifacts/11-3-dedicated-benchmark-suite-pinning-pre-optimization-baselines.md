# Story 11.3: Dedicated benchmark suite pinning pre-optimization baselines

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want a `tests/benchmarks/` pytest-benchmark suite measuring solver throughput and setup-stage wall-clock, excluded from the default test run,
so that every future optimization is judged against pinned baselines instead of anecdotes.

## Acceptance Criteria

1. **pytest-benchmark dev dependency + excluded suite.** pytest-benchmark added to `[dependency-groups] dev`; new `tests/benchmarks/` directory whose tests carry a `benchmark` marker excluded from default collection the same way `live`/`slow` are (marker registered in pyproject `markers`, `addopts` extended to `-m "not live and not slow and not benchmark"`). `uv run pytest tests/benchmarks -m benchmark` runs the suite.

2. **Solver throughput benchmark.** Measures **seconds per 1k GRASP iterations** at fixed seed and params on the `grenoble_small` contracted graph (built once from the committed fixture cache). The workload is deterministic and exactly 1000 iterations per round: `iter_budget=1000`, `stagnation_iters=0` (disables stagnation), `time_budget` pinned high so wall-clock never binds — the same three-terminator reasoning as the regression fixtures (FR29).

3. **Setup-stage benchmarks, offline.** Per-stage timings for the CPU-bound setup stages on the committed `tests/fixtures/grenoble_small/` data (graphml + dem.tif), no live network: one benchmark per stage function (fixture-load-normalize standing in for `osm_load`, `filter_trails`, `smooth_polylines`, `resample_edges`, `attach_elevation`; cache-write optional). The two network stages (Overpass download, DEM WMS fetch) are excluded by construction and this is documented in the suite — their baseline is 11.2's cold-cache capture, not a benchmark.

4. **Benchmark params pinned independently.** The benchmark suite pins its own seed/params (module-level constants in `tests/benchmarks/`), not imported from `regression._PINNED_PARAMS` or CLI defaults — a future re-tune of functional pins or defaults must not silently move throughput baselines.

5. **Baselines captured.** `--benchmark-autosave` baselines from one real run are committed (`.benchmarks/`), or — if the JSON is decided against committing — their location and regeneration command are documented. Either way the baseline's machine-specificity is stated (numbers comparable only on the same machine).

6. **Zero default-run impact + docs.** Default `uv run pytest` collects zero benchmark tests; all functional tests and regression goldens are unmodified; all four gates stay green. README "Development notes" gains a benchmarks subsection documenting the run command and the `--benchmark-autosave` / `--benchmark-compare` workflow expected around every future optimization commit.

## Tasks / Subtasks

- [x] Task 1: Dependency + exclusion wiring (AC: #1, #6). `uv add --dev pytest-benchmark` (expect the corporate-TLS flake after the pyproject edit); register the `benchmark` marker; extend `addopts`. Prove default collection is unchanged (`uv run pytest --collect-only -q` count identical before/after adding the suite).
- [x] Task 2: `tests/benchmarks/` scaffolding (AC: #1, #4). `conftest.py` with locally-pinned bench params/seed constants and a session-scoped fixture building the `grenoble_small` contracted graph from the committed e2e cache (`check_coverage` → `operationalize_graph` → `filter_trails` → `detect_climbs` → `contract_climbs` — the `cli/query.py` sequence). Module-level `pytestmark = pytest.mark.benchmark` in every test module.
- [x] Task 3: Solver throughput benchmark (AC: #2). Fresh `GraspSolver` + fresh `np.random.default_rng(seed)` per round via `benchmark.pedantic(..., setup=...)` (a solver instance is single-run; tracker state accumulates). Assert the run actually did 1000 iterations (`convergence_status == "budget-exhausted"`), so a silent early-exit can't fake a speedup.
- [x] Task 4: Setup-stage benchmarks (AC: #3). One benchmark per CPU stage chained off session-scoped stage-output fixtures (each stage's input built once; stage functions are pure per the architecture's pipeline-boundary rule, so re-running them per round is sound). Fixture load path mirrors `tests/e2e/conftest.py::_osm_load_from_fixture` (`normalize_edges(osmnx.load_graphml(...))`). Skip cleanly when fixtures aren't committed (same guard as e2e conftest).
- [x] Task 5: Capture + commit baselines (AC: #5). One `--benchmark-autosave` run on the reference machine; commit `.benchmarks/` JSON (or document location); record machine context.
- [x] Task 6: README dev-notes subsection (AC: #6). Run command, autosave/compare workflow around optimization commits, machine-specificity caveat.
- [x] Task 7: Validation (AC: #6). Default suite green and benchmark-free; `ruff check` / `ruff format --check` / whole-project `basedpyright` 0/0/0 / `pytest --cov` all green; goldens untouched; update sprint-status.

## Dev Notes

### Recommendation (read first)

Everything this suite needs already exists — the story is wiring, not invention. Three design points matter; the rest is mechanical:

1. **Benchmark time, never output.** Benchmarks assert nothing about routes (that's the goldens' job — the research is explicit that mixing throughput and quality in one metric makes both noisy). This is also what lets the suite survive Phase 3 untouched: even the rebake-gated batched-RNG optimization only moves timings here, never breaks the suite.
2. **Pin params locally** (AC #4). Copy the values you want into `tests/benchmarks/conftest.py` constants; do not import `regression._PINNED_PARAMS`. Sensible starting point: the fast-tier pinned values with the three terminators overridden per AC #2 (`iter_budget=1000`, `stagnation_iters=0`, `time_budget=100000`).
3. **Exact iteration counts or the metric lies.** "Seconds per 1k iterations" requires all 1000 iterations to run — hence stagnation disabled and the `convergence_status == "budget-exhausted"` assertion in Task 3. (`SolverParams.stagnation_iters=0` disables the check — the `--stagnation-iters 0` semantics from §Cat 5e.)

### Exclusion mechanics (the live/slow pattern)

- pyproject `markers` gains `benchmark: ...` and `addopts` becomes `["-m", "not live and not slow and not benchmark"]`. A CLI `-m benchmark` overrides the addopts `-m` (last one wins) — exactly how `-m live` / `-m slow` work today.
- Note pytest-benchmark itself registers a `benchmark` marker (it's the per-test config marker, `@pytest.mark.benchmark(group=...)`). Using it bare as a selection marker is compatible; the pyproject registration is then documentation. If the dual use feels muddy, a distinct name (`bench`) is fine — dev's call, but mirror it everywhere.
- Run benchmarks **without `--cov`**: coverage instrumentation distorts timings. The default CI gate (`pytest --cov`) never sees these tests, so nothing else changes.

### Solver benchmark — building the workload

- The contracted graph comes from the committed regression cache (`tests/e2e/fixtures/grenoble_small/cache/` — a full cache root the CLI reads with plain `--cache-dir`, no patching). Programmatic path, all in a session-scoped fixture: `check_coverage(cache_root, area)` → `prepared.graph` → `operationalize_graph(...)` → `filter_trails(...)` → `detect_climbs(...)` → `contract_climbs(...)` ([query.py:193-332](src/steeproute/cli/query.py)). Center/radius: `(45.260, 5.788)` / 1.5 km ([regression.py:138-146](src/steeproute/regression.py)) — hardcode them in the bench conftest per AC #4.
- `GraspSolver(contracted, params, rng)` with `progress_callback=None` ([grasp.py:166-172](src/steeproute/solver/grasp.py)); benchmark only `.run()`. Constructor work (`base_segment_id_map`, tracker, node sort) stays in the pedantic `setup` callable so the measured region is the iteration loop.
- Expected magnitude: 11.2 measured ~660 iter/s at quality params on Chamrousse — ~1.5 s per 1k-iteration round; pytest-benchmark's default ~5 rounds keeps the whole suite well under a minute.
- Optionally benchmark `detect_climbs` + `contract_climbs` as their own group — they're query-side one-time costs Phase 3 might also touch; cheap to add since the fixtures exist. Not required by the AC.

### Setup-stage benchmarks — offline by construction

- Inputs are the *unit/integration* fixtures `tests/fixtures/grenoble_small/{osm_graph.graphml,dem.tif}` (not the e2e cache — you need the pre-pipeline raw graph). Stage 1 stand-in is the fixture load: `normalize_edges(osmnx.load_graphml(path))` — label it honestly (it benchmarks graphml parse + normalize, not the Overpass download).
- Stage chain: `filter_trails` → `smooth_polylines` → `resample_edges` → `attach_elevation(graph, dem_path)` (all exported from `steeproute.pipeline`; `_SETUP_DIFFICULTY_CAP = "T6"` is what setup pins, [pipeline/__init__.py:80](src/steeproute/pipeline/__init__.py)). Build each stage's input once in session-scoped fixtures; benchmark the stage call.
- **Do not wire `StageProgress` into benchmarks** — 11.1's seam is the *runtime* instrument; here pytest-benchmark is the instrument and each stage is its own test. Pass no `progress` kwarg.
- Document in the module docstring that `osm-download`/`dem-resolve` network time is out of benchmark scope (baseline: 11.2's cold-cache capture, ≈81% of the 54 s setup was network).

### Baselines and workflow docs

- `--benchmark-autosave` writes `.benchmarks/<machine-slug>/0001_<commit>.json`. Recommend committing it — personal single-machine project, a few KB of JSON, and it makes "compare against pre-optimization" a one-flag operation. Check `.gitignore` doesn't swallow `.benchmarks/`.
- README workflow (the AC #6 subsection, next to the goldens section at [README.md:101](README.md)): baseline once now; around every future optimization commit run `uv run pytest tests/benchmarks -m benchmark --benchmark-autosave` before and after, compare with `--benchmark-compare` (or `pytest-benchmark compare <id> <id>`); numbers are machine-local.

### Context from 11.1/11.2 (both currently in review, uncommitted in the working tree)

This story doesn't build on their code — benchmarks don't use `StageProgress`, and the only shared file is `pyproject.toml` (11.2 added `py-spy` to the same dev group this story appends to). The bottleneck analysis (`research/steeproute-bottleneck-analysis-2026-07-03.md`) says what these baselines will referee: Phase-3 candidates are static-adjacency precompute, incremental θ-prefix, cached distinctness sets, batched RNG — all solver-loop work the throughput benchmark measures directly. Gate state from 11.1/11.2 close-outs: 842 passed default suite, basedpyright genuinely 0/0/0 (don't regress), `ruff format` drift was fixed in 11.1.

### Out of scope (don't drift)

- **Any optimization work** — this story pins the "before"; Phase 3 is scoped separately from 11.2's list.
- CI benchmark job / `--benchmark-compare-fail` thresholds — timing assertions on shared CI runners are noise; benchmarks stay a local instrument (CI tightening is deferred Story 8.5 territory anyway).
- asv / historical trend tracking — research verdict: pytest-benchmark first.
- Route-quality metrics in benchmarks — goldens own quality; benchmarks own time.

### Project Structure Notes

- **New:** `tests/benchmarks/` (`conftest.py`, `test_solver_throughput.py`, `test_setup_stages.py` — names indicative), committed `.benchmarks/` baseline JSON.
- **Modified:** `pyproject.toml` (dev dep, `markers`, `addopts`), `uv.lock`, `README.md` (dev-notes subsection).
- **Untouched:** `src/steeproute/` entirely; all existing tests; goldens.
- basedpyright covers `tests/` and pytest-benchmark ships no type stubs — the `benchmark` fixture will surface `reportUnknown*`. Use the established per-file header-pragma precedent (`tests/unit/test_osm.py` style) scoped to the benchmark modules; keep the whole-project 0/0/0.

### Testing standards summary

- The suite must skip cleanly (not error) when fixtures aren't committed — mirror the `seeded_cache` guard ([tests/e2e/conftest.py:104-105](tests/e2e/conftest.py)).
- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `pytest --cov` — plus this story's extra proof: default collection count unchanged.
- Run `tests/unit` and `tests/integration` in separate pytest invocations if invoked explicitly (`from conftest import` collision).
- Build-flake recovery after the pyproject edit: `uv sync --native-tls` once, then `uv run --no-sync`.

### References

- [Source: epics.md §Story 11.3 + §Epic 11 preamble](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth
- [Source: research/technical-steeproute-performance-tuning-research-2026-07-02.md §Performance-Regression Harness + §Development Workflow + §Success Metrics](_bmad-output/planning-artifacts/research/technical-steeproute-performance-tuning-research-2026-07-02.md) — pytest-benchmark-first verdict, throughput-vs-quality separation, seconds-per-1k metric definition
- [Source: research/steeproute-bottleneck-analysis-2026-07-03.md](_bmad-output/planning-artifacts/research/steeproute-bottleneck-analysis-2026-07-03.md) — what Phase 3 will change; ~660 iter/s magnitude; setup ≈81% network
- [Source: src/steeproute/regression.py:110-146](src/steeproute/regression.py) — pinned-param values/rationale, grenoble_small center/radius/seed, time-budget-pinned-high determinism pattern
- [Source: src/steeproute/cli/query.py:193-336](src/steeproute/cli/query.py) — cache → contracted-graph → solver sequence the bench conftest replays
- [Source: src/steeproute/solver/grasp.py:148-209](src/steeproute/solver/grasp.py) — solver constructor, single-run semantics, `convergence_status`
- [Source: tests/e2e/conftest.py](tests/e2e/conftest.py) — offline fixture-load pattern + skip guard
- [Source: pyproject.toml:206-233](pyproject.toml) — markers/addopts exclusion pattern to extend
- [Source: _bmad-output/implementation-artifacts/11-2-profile-solver-and-setup-pipeline-into-a-ranked-bottleneck-list.md](_bmad-output/implementation-artifacts/11-2-profile-solver-and-setup-pipeline-into-a-ranked-bottleneck-list.md) — previous story: gate state, TLS-flake recipe, measured throughput

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.14 / uv; pytest-benchmark 5.2.3 (+ py-cpuinfo 9.0.0) installed via `uv add --dev pytest-benchmark --native-tls` (no build flake this session). Built on top of the uncommitted 11.1/11.2 working tree, per plan (global 3-story review to follow).

**Collection proof (AC #6):**

```
before: 842/848 tests collected (6 deselected)
after:  842/854 tests collected (12 deselected)   ← same 842 selected; 6 new benchmarks deselected
```

**Benchmark run (all 6, autosaved):**

```
test_grasp_1k_iterations                    mean 313 ms/1k iters (~3,200 iter/s)
test_stage1_standin_graphml_load_normalize  mean 102 ms
test_stage2_filter_trails                   mean  12 ms
test_stage3_smooth_polylines                mean  82 ms
test_stage4_resample_edges                  mean 104 ms
test_stage5_sample_elevation                mean 995 ms
```

**Gates:**

```
ruff check src tests            → All checks passed!
ruff format --check src tests   → 104 files already formatted
basedpyright (whole project)    → 0 errors, 0 warnings, 0 notes
pytest --cov (default markers)  → 842 passed, 12 deselected (exit 0)
goldens                         → untouched (git status clean under tests/e2e/goldens)
```

Note: the passing `pytest --cov` run took 23:52 wall-clock vs the usual ~6-7 min. A `--durations=25` re-run attributed it to ONE pre-existing test: `tests/unit/test_area_parsing.py::test_setup_cli_does_not_enforce_area_cap` (495 s of a 12:31 run; all other tests ≤ 9 s). That test patches `resolve_dem` but not `osm_load`, so it live-downloads a 30 km-radius Overpass extract every run — previously masked by osmnx's CWD-stray `./cache` (239 MB found in the repo root), unmasked by Story 11.1's (correct) cache relocation because the test's cache root is a fresh `tmp_path` per run. **Fixed in this session at Yann's request** (not a 11.3 AC): the sentinel patch moved from `resolve_dem` to `steeproute.pipeline.osm_load` (reaching stage 1 equally proves no area-cap rejection — same target the e2e conftest patches); test now 0.01 s, fully offline. Stray repo-root `cache/` (239 MB) deleted. `tests/unit/test_area_parsing.py` is therefore dirty in the working tree alongside the three stories — reviewers should attribute it to this finding.

### Completion Notes List

**Exclusion wiring (Task 1).** `benchmark` marker registered in pyproject `markers`; `addopts` now `-m "not live and not slow and not benchmark"`. Collection proof above: the default run selects exactly the same 842 tests as before the suite existed. Note pytest-benchmark itself also registers a `benchmark` marker (its per-test config decorator) — bare use as a selection marker is compatible; kept the name for the `live`/`slow` symmetry.

**Bench pins (Task 2, AC #4).** All params/seed/geometry pinned as module constants in `tests/benchmarks/conftest.py` — copied from the fast-tier regression pins as of 2026-07-03, never imported from `regression.py` or CLI defaults, with the three terminators overridden for exact-count throughput: `iter_budget=1000`, `stagnation_iters=0` (disabled), `time_budget=100000`. Session-scoped `contracted_graph` replays the `cli/query.py` sequence against the committed grenoble_small e2e cache (skip-guard on `<cache>/steeproute/index.json` — the initial guard checked the wrong level and silently skipped; caught because the first full benchmark run reported 5 passed + 1 skipped).

**Solver throughput (Task 3).** `benchmark.pedantic(rounds=5, warmup_rounds=1)` with a per-round `setup` building a fresh solver + fresh seeded RNG (solver instances are single-run); only `.run()` is measured. Post-run assertion: every round's `convergence_status == "budget-exhausted"`. Measured ~313 ms per 1k iterations on grenoble_small (~3,200 iter/s — faster than 11.2's ~660 iter/s Chamrousse figure, as expected on a 1.5 km graph).

**Setup stages (Task 4).** Five benchmarks chained off session-scoped stage-input fixtures that mirror `build_graph_geometry` exactly, including the private guard prunes (`_drop_orphan_nodes`/`_drop_short_edges` — fixture-side, outside the measured region; precedent `tests/integration/test_pipeline_end_to_end.py`). Stage 1 is the honest offline stand-in (graphml parse + `normalize_edges`); network stages documented as out of scope with the 11.2 capture as their baseline. `sample_elevation` dominates CPU-side (~1 s), consistent with 11.2's setup table.

**Baseline (Task 5).** `--benchmark-autosave` JSON committed under `.benchmarks/Windows-CPython-3.13-64bit/` (`.gitignore` verified not to swallow it). `--benchmark-compare` verified working against it. Machine-local caveat documented in README and both module docstrings.

**README (Task 6).** "Performance benchmarks" subsection under Development notes: run command (`uv run pytest tests/benchmarks -m benchmark` — standalone invocation, no `--cov`), the autosave/compare loop around every optimization commit, machine-locality, and the time-vs-quality division of labor against the goldens section above it.

### File List

**Modified:**
- `pyproject.toml` — `pytest-benchmark>=5.2.3` dev dep; `benchmark` marker; `addopts` exclusion.
- `uv.lock` — lockfile update (pytest-benchmark, py-cpuinfo).
- `README.md` — "Performance benchmarks" dev-notes subsection.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.
- `tests/unit/test_area_parsing.py` — out-of-AC, user-requested during this session: live-Overpass sentinel fix (see Debug Log).

**New:**
- `tests/benchmarks/conftest.py` — pinned bench params + session-scoped graph/stage-input fixtures.
- `tests/benchmarks/test_solver_throughput.py` — seconds-per-1k-GRASP-iterations benchmark.
- `tests/benchmarks/test_setup_stages.py` — five per-stage setup benchmarks (offline).
- `.benchmarks/Windows-CPython-3.13-64bit/0001_070debf5242836d727174a094e54d26277c0d50a_20260703_141619_uncommited-changes.json` — pre-optimization baseline.

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-03 | Yann (Claude Fable 5) | Story 11.3 implemented (Phase 2): `tests/benchmarks/` pytest-benchmark suite, `benchmark`-marker-excluded from the default run (842 selected before and after — zero default-run impact). Solver throughput pinned at ~313 ms per 1k seeded GRASP iterations on grenoble_small (exact-count: stagnation disabled, budget-exhausted asserted per round); five offline setup-stage benchmarks (graphml-load stand-in → filter → smooth → resample → sample_elevation, network stages documented out of scope). All pins local to the suite (AC #4). Pre-optimization `--benchmark-autosave` baseline committed under `.benchmarks/`; README documents the compare workflow around future optimization commits. Gates green; goldens untouched. |
