# Story 12.3: Batched RNG draws with documented golden rebake

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the per-step scalar RNG boundary overhead removed,
so that the last measured hotspot (~13% of the run) is captured.

## Acceptance Criteria

1. **Batched draws.** Given the hot path currently makes one scalar `Generator.integers` call per walk step (the profile's only native time — all boundary overhead), RNG draws are batched/chunked so per-step scalar `Generator` calls disappear from the hot path, preserving the determinism contract: same `--seed` + code + prepared data → identical output edge-sets (NFR4/FR29), with all randomness still flowing exclusively through the injected `numpy.random.Generator`.
2. **Documented golden rebake.** Because the draw sequence changes, all committed regression goldens are rebaked once via the `update-regression` workflow (both tiers plus the flag-on fixtures — 10 files), with an explicit rationale in the commit message (Story 9.3 precedent). Diffs are pure value churn: no `params_hash`/`seed` drift, no `min_routes` collapse.
3. **Quality holds on new outputs.** The GRASP-vs-exhaustive quality gate and the metamorphic invariants suite pass on the new outputs, and the full default suite is green — with test changes limited to re-pinning seed-tuned expectations (see Dev Notes), each preserving its test's original semantics.
4. **Measured gain.** The benchmark suite shows a throughput gain over the post-12.2 baseline (`0003_b0e85dd*`), recorded via `--benchmark-compare` in the story close-out.

## Tasks / Subtasks

- [x] Task 1: Batch RNG draws in `grasp.py` (AC: #1)
  - [x] Replace the two scalar call sites — start-node draw (grasp.py:444, fixed bound per solve) and RCL choice (grasp.py:453, variable bound 1..`RCL_SIZE`) — with consumption from a chunked buffer refilled by one native call per chunk (recommended scheme in Dev Notes)
  - [x] Draw consumption stays a pure function of seed + walk state: no coupling to wall-clock, callback presence, or termination checks; no draw-ahead sized by `iter_budget` (walk lengths are unknowable — chunked refill only)
  - [x] `_construct_one(self)` signature unchanged (load-bearing for `test_interrupt_in_process.py`'s monkeypatch); constructor keeps taking `rng: np.random.Generator`
  - [x] Update the module docstring's "Determinism (FR29)" and construction-shape sections to describe the batched scheme
- [x] Task 2: Rebake goldens via the documented workflow (AC: #2)
  - [x] `uv run update-regression --all`, then `--all --tier realistic`, then `--fixture grenoble_small_junction` and `--fixture grenoble_small_descent` (flag-on fixtures are not in `--all` until Story 8.5) — 10 goldens total
  - [x] Review every printed diff: value churn only (objective/D±/edge_count/hash); any `params_hash`/`seed` change or `min_routes` trip means something else broke — investigate, don't bake
  - [x] Commit message states the rationale explicitly (draw-sequence change from RNG batching, planned in Epic 12)
- [x] Task 3: Reconcile draw-sequence-sensitive tests (AC: #3)
  - [x] `test_stagnation.py::test_admission_with_unchanged_total_still_advances_convergence_iteration` pins `ci == 11` for seed 3 — re-derive the new admission iteration and re-pin, preserving the zero-delta assertion (`events[ci-1] == events[ci-2]`) and the sole-survivor check the test actually proves
  - [x] Sweep the other seeded solver tests (unit `test_grasp_construction`, integration toy-graph/fixture/junction/descent/θ-prefix suites) — property/oracle-comparison assertions should hold as-is; re-pin only literal seed-tuned expectations, with a comment updating the pinned value's derivation
  - [x] e2e `test_degradation.py` + `test_run_summary.py::test_degraded_path` shifted in the 9.3 rebake — verify they still hold on the new outputs (they force degradation via params, so they should)
- [x] Task 4: Prove quality and determinism on the new scheme (AC: #1, #3)
  - [x] GRASP-vs-exhaustive quality gate + `test_metamorphic` pass unmodified
  - [x] `test_grasp_reproducible` / e2e `test_seeded_reproducibility` pass unmodified (they assert same-seed run-pair identity, not specific values)
- [x] Task 5: Benchmark close-out (AC: #4)
  - [x] `uv run pytest tests/benchmarks -m benchmark --benchmark-autosave` (no `--cov`), compare against the post-12.2 `0003_b0e85dd*` autosave via `--benchmark-compare`; record the delta in the Dev Agent Record and commit message
- [x] Task 6: Gates + status
  - [x] `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `pytest --cov` green; update sprint-status

## Dev Notes

### What the profile indicts (why this story exists)

Item 2 of the 11.2 ranked list: scalar `Generator.integers` draws at grasp.py:444 (start node, once per iteration) and grasp.py:453 (RCL choice, once per walk step) — **~13.3% of the pre-12.1 run and the only native time in the profile**, all per-call numpy boundary overhead, not compute. The analysis sequenced this last because it changes the seeded draw sequence and forces the rebake; 12.1/12.2 landed first precisely so their golden-stability guarantee stayed checkable.

The percentages were measured pre-12.1. Stories 12.1+12.2 cut the run ~3.7× without touching the RNG call sites, so RNG's *relative* share of the current run is far larger than 13% — plausibly 30–50%. Batching amortizes the native boundary cost but leaves per-draw Python-side buffer indexing, so expect somewhere in the 1.2–1.7× band over the `0003` baseline. Record whatever is measured honestly.

### Recommended batching scheme

Keep a small float buffer on the solver: refill with `self._rng.random(CHUNK)` (one native call per `CHUNK` draws), consume one value `u` per draw as `int(u * n)`. `u ∈ [0, 1)` so the result is always in `0..n-1` — no bounds edge case. This handles both call sites with one mechanism despite the RCL bound varying per step. Floor bias at `n ≤ len(nodes)` is negligible and GRASP needs no exact-uniformity guarantee — but the docstrings say "uniformly at random", so soften to "uniformly up to float64 granularity" where touched. Chunk size is the dev's call (something like 1024; refill cost is amortized either way). A separate pre-drawn `integers` buffer for start nodes is a legitimate alternative — one mechanism for both sites is just simpler to reason about and document.

Determinism argument to preserve (and restate in the docstring): the value sequence is a pure function of the seed, and consumption order is a pure function of the walks — so two same-seed solvers on the same graph/params still produce byte-identical results. The existing FR29 property that wall-clock affects only the iteration *count* (when the time budget binds), never the *sequence*, must survive: refills happen on buffer exhaustion, never on a clock or callback condition.

### Rebake mechanics (the epic's one planned rebake)

- `src/steeproute/regression.py` single-sources the comparison and the writer, so they can't disagree. `update-regression` re-runs each fixture's committed cache at its explicitly-pinned params, prints a before/after diff, overwrites the golden, and reminds you about the commit rationale.
- **10 goldens**: 4 fixtures × {fast, realistic} via the two `--all` invocations, plus `grenoble_small_junction` and `grenoble_small_descent` by name (Epic 10 flag-on fixtures, outside `--all` until Story 8.5 folds them in). 9.3 rebaked 8; the two flag-on files are the delta since.
- Healthy diff signature (9.3 precedent): route-value churn only, identical `params_hash` and `seed` per golden, every fixture still ≥ `min_routes` routes. Realistic-tier runs are ~200k iters each — budget a few minutes per fixture.

### Sequence-sensitivity map (what breaks vs what holds)

- **Breaks by design:** `tests/e2e/test_pinned_regressions.py` until the rebake lands (that's the gate working).
- **Known seed-tuned pin:** `test_stagnation.py:205` (`assert ci == 11`, seed 3, evict-many graph). Re-derive and re-pin; the test's substance is the zero-total-delta admission + sole-survivor assertions, not the literal 11. If the new sequence no longer produces the evict-many scenario inside the 200-iter budget, re-tune the seed rather than weakening the assertions.
- **Holds structurally:** reproducibility suites (same-seed pair identity), metamorphic invariants, quality gate (oracle enumerates exhaustively; ratio threshold judges GRASP's new output), validator/oracle themselves (no RNG). The interrupt tests monkeypatch `_construct_one` — keep its signature.
- **Check, likely fine:** e2e degradation/run-summary tests (param-forced scenarios, but reconciled once before in 9.3). README gallery reports are committed illustrations, not gated — leave them alone.

### Implementation facts

- RNG call sites: [grasp.py:444](src/steeproute/solver/grasp.py) (`start_idx`), [grasp.py:453](src/steeproute/solver/grasp.py) (`choice_idx`). The injected generator is stored at grasp.py:200; nothing else draws from it (the tracker consumes, never draws).
- Docstring obligations: module-level "Determinism (FR29)" section (grasp.py:74-96) and construction-shape step 1/3 wording (grasp.py:18-31) both describe one-scalar-draw-per-step — update both.
- `SolverParams.seed` / CLI `default_rng(seed)` threading is untouched; this story changes only how the solver consumes the generator.
- Benchmark baseline: `.benchmarks/Windows-CPython-3.13-64bit/0003_b0e85dd*.json` (post-12.2: mean 102.2 ms, median 81.3 ms, min 74.5 ms per 1k iterations on grenoble_small). The bench asserts `convergence_status == "budget-exhausted"` each round, so a silent early-exit can't fake a speedup.

### Out of scope (don't drift)

- Phase-4 work (flat-array interface extraction, PyO3) — 12.4 decides go/no-go
- Any further hot-path restructuring beyond the draw mechanism; validator, oracle, setup pipeline, output rendering
- Re-profiling — that's 12.4

### Previous story intelligence (12.2 close-out)

- 12.2 landed incremental θ-prefix + cached distinctness sets: ~1.52× median over post-12.1, cumulative ~3.7× vs the Epic 11 baseline. Compare target for this story is `0003_b0e85dd*`, not `0001`/`0002`.
- The zero-test-modification proof pattern of 12.1/12.2 **does not apply here** — this story's whole point is a planned, documented output change. The discipline transfers differently: every test change must be a re-pin with stated derivation, never a weakened assertion.
- Gate state to not regress: 842 default tests in ~3–7 min, whole-project basedpyright 0/0/0, grasp.py at 100% coverage. Run benchmarks standalone without `--cov` (coverage distorts timings).
- uv build-flake recovery (fires after commits/pyproject edits): `uv sync --native-tls` once, then `uv run --no-sync`. Run `tests/unit` and `tests/integration` as separate invocations if invoked explicitly (conftest import collision).

### Project Structure Notes

- **Modified:** `src/steeproute/solver/grasp.py` (batched draw mechanism + docstrings), `tests/e2e/goldens/*.json` (10 rebaked), `tests/integration/test_stagnation.py` (re-pinned iteration) + any other literal seed-tuned pins found in the Task 3 sweep, sprint-status, new `.benchmarks/` autosave.
- **Untouched:** `models.py`, `validator.py`, `solver/reuse.py`, `solver/distinctness.py`, `solver/descent.py`, `regression.py`, `tests/integration/exhaustive_oracle.py`, CLI seed threading.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest --cov`.
- Benchmarks: `uv run pytest tests/benchmarks -m benchmark --benchmark-autosave --benchmark-compare` — compare against `0003_*`.
- Golden verification this story is the *inverse* of 12.1/12.2: goldens **must** change (all 10, value-churn-only diffs), and after the rebake the full suite must be green with `git status` showing only the intended files.

### References

- [Source: epics.md §Story 12.3 + §Epic 12 preamble](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; rebake mandate + NFR4 preservation
- [Source: research/steeproute-bottleneck-analysis-2026-07-03.md §Solver ranked list item 2 + §Phase-3 recommendation item 4](_bmad-output/planning-artifacts/research/steeproute-bottleneck-analysis-2026-07-03.md) — ~13.3% attribution, "only native time", sequenced-last rationale
- [Source: sprint-change-proposal-2026-07-03-solver-optimization.md §Decision 1](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-03-solver-optimization.md) — RNG batching included (not deferred) at the cost of one documented rebake
- [Source: src/steeproute/solver/grasp.py:444,453](src/steeproute/solver/grasp.py) — the two scalar call sites; :74-96 determinism docstring to update
- [Source: src/steeproute/regression.py](src/steeproute/regression.py) — `update-regression` entry point, `FIXTURES`/`REALISTIC_FIXTURES`/`FLAG_ON_FIXTURES`, `min_routes` floor, commit-rationale convention
- [Source: _bmad-output/implementation-artifacts/9-3-revalidation-golden-rebake-and-doc-sync.md](_bmad-output/implementation-artifacts/9-3-revalidation-golden-rebake-and-doc-sync.md) — the rehearsed rebake workflow this story reuses
- [Source: tests/integration/test_stagnation.py:185-205](tests/integration/test_stagnation.py) — the seed-tuned `ci == 11` pin
- [Source: _bmad-output/implementation-artifacts/12-2-incremental-theta-prefix-metrics-and-cached-distinctness-sets.md](_bmad-output/implementation-artifacts/12-2-incremental-theta-prefix-metrics-and-cached-distinctness-sets.md) — post-12.2 baseline (`0003_b0e85dd*`), gate state

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Gates (all green):**

```
tests/unit                                    → 617 passed
tests/integration                             → 131 passed, 2 deselected
tests/e2e (rebaked goldens, repro, interrupt) → 94 passed, 4 deselected in 1:44
ruff check src tests                          → All checks passed!
ruff format --check src tests                 → 104 files already formatted
basedpyright (whole project)                  → 0 errors, 0 warnings, 0 notes
pytest --cov (default markers)                → 842 passed, 12 deselected in 3:16; grasp.py 100% cov
```

**Golden rebake (AC #2):** `update-regression --all` (4 fast) + `--all --tier realistic` (4 realistic) + `--fixture grenoble_small_junction` + `--fixture grenoble_small_descent` = 10 goldens. All diffs pure value churn (objective/D±/edge_count/hash); **no `params_hash` or `seed` change**; every golden still holds 5 routes (no `min_routes` trip). `git status` under `tests/e2e/goldens/` shows exactly the 10 intended files.

**Benchmark compare (AC #4), `--benchmark-autosave --benchmark-compare=0003` vs post-12.2 baseline:**

```
test_grasp_1k_iterations   0003 baseline: mean 102.2 ms, median 81.3 ms, min 74.5 ms
test_grasp_1k_iterations   NOW:           mean  51.2 ms, median 52.8 ms, min 44.3 ms
→ ~1.54× median / ~1.68× min throughput gain over post-12.2 (inside the story's
  predicted 1.2–1.7× band). Cumulative vs 0001 Epic 11 baseline: median
  300.9 → 52.8 ms ≈ 5.7× — clears the analysis's 2.5–4× Phase-3 headroom estimate.
setup-stage benchmarks: unchanged within noise (expected — setup untouched)
```

Saved as `.benchmarks/Windows-CPython-3.13-64bit/0004_cdd284a*.json`.

### Completion Notes List

**Batched draws (Task 1, AC #1).** New `_next_uniform()` on `GraspSolver` serves uniform `[0, 1)` values from a buffer refilled by one native `self._rng.random(_RNG_CHUNK)` call per 1024 draws (`.tolist()` once per refill so per-draw consumption is a native list index yielding a Python float). Both call sites now derive bounded indices as `int(u * n)` — exact for every `n` this solver sees (`n < 2^53`, so the result is always in `0..n-1`; argument recorded in the module docstring). Refills happen only on exhaustion, never on clock/callback/termination conditions, so the consumed sequence is a pure function of the seed (FR29/NFR4). `_construct_one(self)` signature and the constructor's `rng: np.random.Generator` unchanged. `_RNG_CHUNK` is documented as a non-knob: chunking does not change the consumed `random()` stream, so its value can never move solver output.

**Golden rebake (Task 2, AC #2).** All 10 goldens regenerated via the documented `update-regression` workflow (the epic's one planned rebake, Story 9.3 precedent). Healthy diff signature throughout: route-value churn only, identical `params_hash`/`seed`, 5 routes per fixture. Rationale goes in the close-out commit message per the workflow's convention.

**Test reconciliation (Task 3, AC #3).** Exactly three test edits, all semantics-preserving: (1) `test_stagnation.py` — the seed-3 evict-many admission moved from iteration 11 to 14; re-pinned with derivation comment, zero-delta and sole-survivor assertions untouched and passing. (2) `test_grasp_construction.py::test_grasp_rejects_out_and_back_over_a_climb` — seed 42 → 44: the fixture's two equal-objective overlapping routes mean first-constructed-wins, and the non-vacuity guard needs the first start draw to land on node 0 (`default_rng(44).random(1024)[0] ≈ 0.123 → node 0`); derivation in the docstring. (3) `test_progress.py` — not sequence-sensitivity but speed-sensitivity: 600 iterations now finish inside one throttle interval (per-iter ~0.02 ms vs the ~0.7 ms the constants assumed), so the budget/interval were rescaled (600 → 5000 iters, 0.02 → 0.01 s) to keep the solve spanning several intervals; assertions untouched. e2e degradation/run-summary held as-is, as predicted.

**Quality on new outputs (Task 4, AC #1/#3).** GRASP-vs-exhaustive quality gate, metamorphic invariants, `test_grasp_reproducible`, and e2e `test_seeded_reproducibility` all pass unmodified — determinism and solution quality hold under the batched scheme.

**Measured gain (Task 5, AC #4).** ~1.54× median / ~1.68× min solver throughput over post-12.2 (81.3 → 52.8 ms median per 1k iterations on grenoble_small), inside the story's predicted band. Cumulative Epic-12 speedup vs the Story 11.3 baseline: ~5.7× median — above the 2.5–4× Phase-3 estimate, with 12.4's re-profile to confirm the shape.

### File List

**Modified:**
- `src/steeproute/solver/grasp.py` — `_RNG_CHUNK` constant, `_draw_buffer`/`_draw_index` state, `_next_uniform()` helper, both draw sites converted to `int(u * n)`; module docstring Determinism/construction-shape sections updated.
- `tests/unit/test_grasp_construction.py` — out-and-back test seed 42 → 44 (re-pin, derivation documented).
- `tests/integration/test_stagnation.py` — evict-many admission iteration pin 11 → 14 (re-pin, derivation documented).
- `tests/integration/test_progress.py` — iter budget 600 → 5000, throttle interval 0.02 → 0.01 s (speed-assumption rescale).
- `tests/e2e/goldens/*.json` — 10 goldens rebaked (4 fast, 4 realistic, 2 flag-on).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.
- `_bmad-output/implementation-artifacts/12-3-batched-rng-draws-with-documented-golden-rebake.md` — this story file.

**New:**
- `.benchmarks/Windows-CPython-3.13-64bit/0004_cdd284a0bc1dfda24bde8a7e6eedc925d271f7c5_20260704_092622_uncommited-changes.json` — post-12.3 benchmark autosave.

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-04 | Yann (Claude Fable 5) | Story 12.3 implemented: GRASP RNG draws batched through a chunked `rng.random(_RNG_CHUNK)` buffer consumed as `int(u * n)` — per-step scalar `Generator` calls eliminated from the hot path, determinism contract preserved (FR29/NFR4). All 10 regression goldens rebaked via `update-regression` (documented draw-sequence change, Story 9.3 precedent); three semantics-preserving test re-pins. Solver throughput ~1.54× median over post-12.2 (81.3 → 52.8 ms per 1k iterations; ~5.7× cumulative vs Epic 11 baseline), recorded via `--benchmark-compare`. |
