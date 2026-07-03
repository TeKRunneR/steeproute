# Story 12.2: Incremental θ-prefix metrics and cached distinctness sets

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want prefix finalization and distinctness checks to stop recomputing unchanged values,
so that per-iteration overhead drops further with identical results.

## Acceptance Criteria

1. **Incremental θ-prefix scanning.** Given `_best_theta_prefix` currently re-sums the whole prefix per candidate (quadratic in walk length), prefix scanning maintains running `Σlength / ΣD+ / ΣD−` sums, with the canonical `route_avg_gradient` retained as the final acceptance gate — admitted values stay bit-identical to the validator's, per the models.py single-sourcing contract.
2. **Cached distinctness sets.** Given `_canonical_edge_set` is currently recomputed per pairwise comparison (including for already-held solutions), each held solution's canonical edge set is computed once at insertion; the public `jaccard_distance(a, b, segment_map)` function is unchanged (the validator calls it directly).
3. **Behavior-identical.** The regression-golden suite passes untouched, and the full default suite passes with zero test modifications.
4. **Measured gain.** The benchmark suite shows a throughput gain over the post-12.1 baseline (`0002_f2671d1*`) consistent with the ~15% combined attribution, recorded via `--benchmark-compare` in the story close-out.

## Tasks / Subtasks

- [x] Task 1: Incremental θ-prefix metrics in `_best_theta_prefix` (AC: #1, #3)
  - [x] One forward pass over `edges` building cumulative left-fold sums (start `0.0`, add per edge in order) for `length_m` and `d_plus_m + d_minus_m` — these partial sums are bit-identical to what `route_avg_gradient(edges[:k])` computes (see Dev Notes)
  - [x] Keep the downward scan (`end` from `len(edges)` to 1, first hit wins = longest θ-clearing prefix) but test each `end` in O(1) from the cumulative sums, mirroring the exact `route_avg_gradient` semantics: ratio = `cum_climb/cum_length` if `cum_length > 0.0` else `0.0`, compared with `>= theta`
  - [x] On the hit, slice `edges[:end]` **once**, gate it through the canonical `route_avg_gradient(prefix) >= theta` check (`_route_slope_ok`) as the final acceptance, and build the `Solution` — its `objective` is the cumulative climb sum at `end` (bit-identical to the old re-sum, same left fold)
  - [x] No prefix slicing or re-summing inside the scan loop; docstring updated to describe the incremental scan + canonical gate
- [x] Task 2: Cache canonical edge sets in `TopNTracker` (AC: #2, #3)
  - [x] Factor the set math of `jaccard_distance` into an internal sets-in helper; the public `jaccard_distance(a, b, segment_map)` keeps its exact signature and behavior (validator.py and test_distinctness.py call it directly)
  - [x] Tracker holds each solution's canonical set alongside it, computed once at insertion (`Solution` is `frozen=True, slots=True` — the cache must live in the tracker, not on the object); candidate's set computed once per `consider()` call, not once per held comparison
  - [x] Preserve `consider()` semantics exactly: insertion order of held entries, overlap detection, evict-all-overlapping / evict-worst branches, the non-finite-objective guard firing before any set work
  - [x] Public surface unchanged: `TopNTracker(n, j_max, segment_map)`, `consider`, `current_top`, `total_objective` (tests touch only these — internal shape is free)
- [x] Task 3: Prove behavior identity (AC: #3)
  - [x] Full default suite passes with zero test modifications (unit `test_distinctness` / `test_grasp_construction`, integration `test_grasp_theta_prefix` / `test_grasp_on_fixture` / `test_grasp_reproducible` / `test_metamorphic` / oracle quality gate, e2e `test_pinned_regressions` / `test_seeded_reproducibility`)
  - [x] Goldens byte-untouched (`git status` clean under `tests/e2e/goldens/`); no changes to `models.py`, `validator.py`, `solver/reuse.py`, or `tests/integration/exhaustive_oracle.py`
- [x] Task 4: Benchmark close-out (AC: #4)
  - [x] `uv run pytest tests/benchmarks -m benchmark --benchmark-autosave` (no `--cov`), compare against the post-12.1 `0002_f2671d1*` autosave via `--benchmark-compare`; record the delta in the Dev Agent Record and commit message
- [x] Task 5: Gates + status
  - [x] `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `pytest --cov` green; update sprint-status

## Dev Notes

### What the profile indicts (why this story exists)

Two independent hotspots from the 11.2 analysis, both pure-Python recomputation of unchanged values:

- **Item 3 (10.6% of the pre-12.1 run):** `_best_theta_prefix` → `_route_slope_ok` → `route_avg_gradient`. `route_avg_gradient` materializes the prefix tuple and makes two full generator passes **per prefix checked** — quadratic in walk length; the winning prefix's objective is then summed a third time.
- **Item 4 (7.1%):** `TopNTracker.consider` → `jaccard_distance` → `_canonical_edge_set`. Both sets are rebuilt on every pairwise comparison — the candidate's up to `n` times per `consider`, and every held solution's on every candidate arrival, though held solutions are immutable.

The analysis attributes ~15% combined recoverable time (items 2 and 3 of the Phase-3 recommendation). Note the percentages were measured against the **pre-12.1** profile: 12.1 cut total run time ~2.4× without touching these paths, so their *relative* share of the post-12.1 run is larger — expect the `--benchmark-compare` delta vs the `0002` baseline to land well above a naive 15%, plausibly 1.2–1.6×. Judge AC #4 against the `0002` baseline, and record whatever is measured honestly.

### Bit-identity argument — θ-prefix (the thing to preserve)

`route_avg_gradient` computes `sum((x for ...), 0.0)` — a left fold starting at `0.0`, accumulating in edge order. A forward cumulative array built the same way (`c[k] = c[k-1] + x_k`, `c[0] = 0.0`) produces *the exact same float* at every k, because float addition over the same operands in the same order is deterministic. So the incremental ratio at each `end` is bit-identical to what the old code's `route_avg_gradient(edges[:end])` returned — same comparisons, same first hit, same prefix, same objective. The canonical gate on the winning prefix (AC #1) is therefore guaranteed to agree; it stays because the models.py contract single-sources the metric across solver, validator, and oracle, and the gate makes the invariant structural rather than incidental. Do **not** compute the sums backward (subtracting from the total) — right-to-left accumulation is a different fold and can differ in the last ulp, which is a candidate-selection change and a golden diff.

Mirror the zero-length branch exactly: `route_avg_gradient` returns `0.0` when `Σlength ≤ 0`, and the comparison is `>=` theta.

### Behavior-identity argument — distinctness

`_canonical_edge_set` is a pure function of an immutable `Solution` (+ the fixed `segment_map`): a cached frozenset is *equal* to a recomputed one, so every `jaccard_distance` value, every overlap verdict, every admission/eviction decision, and `current_top()`'s output are unchanged. RNG is untouched (the tracker consumes, never draws). Same admissions → byte-identical goldens; any golden diff means the refactor is wrong (12.3 owns the epic's one rebake).

### Implementation facts

- `_best_theta_prefix` lives at [grasp.py:368-392](src/steeproute/solver/grasp.py); `run()` calls it once per iteration at grasp.py:320. `_route_slope_ok` (grasp.py:353) wraps the canonical gate. Both are private — signatures may change freely, but don't touch `_construct_one` (its `(self)` signature is load-bearing for `test_interrupt_in_process.py`'s monkeypatch) or any `self._rng` call site (12.3's territory).
- `route_avg_gradient` contract: [models.py:94-107](src/steeproute/models.py) — read the docstring; the solver/validator/oracle bit-identity promise is explicit there.
- Distinctness module: [distinctness.py](src/steeproute/solver/distinctness.py) — `_canonical_edge_set` (:42), `jaccard_distance` (:63), `consider` (:152). `__all__` exports `TopNTracker` and `jaccard_distance` only.
- `jaccard_distance` external caller: [validator.py:135](src/steeproute/validator.py) feeds it transient `Solution`-wrapped routes — the public function must keep computing sets itself. `test_distinctness.py` also calls it directly ~15 times.
- `test_distinctness.py` never reads `_held` — only `consider`/`current_top`/`total_objective` — so the internal held-entry shape (e.g. a list of `(Solution, frozenset)` pairs or a small record) is unconstrained. `current_top()`/`_worst_held` must still return/rank bare `Solution`s via the existing `_sort_key`.
- `_sort_key`'s per-call edge-id sort (distinctness.py:211) is **not** in the attribution — `current_top()` runs once per solve at termination, `_worst_held` only on the capacity-eviction branch. Leave it alone.
- The exhaustive oracle enumerates every prefix independently ([exhaustive_oracle.py](tests/integration/exhaustive_oracle.py)) — leave it alone; the quality gate then independently confirms the feasible set didn't move.

### Out of scope (don't drift)

- RNG batching (any change to draw count/sequence) → Story 12.3
- Flat-array/interface extraction, PyO3 → Phase 4, only if 12.4 says go
- `_construct_one`'s full-walk objective sum, `_sort_key`, validator, oracle, setup pipeline, output rendering

### Previous story intelligence (12.1 close-out)

- 12.1 landed `_build_adjacency` + graph-free `_build_rcl` in grasp.py, measured ~2.4× median (301 → 123 ms per 1k iterations on grenoble_small). Benchmark compare target for this story is the `0002_f2671d1*` autosave under `.benchmarks/Windows-CPython-3.13-64bit/`, not the `0001` Epic-11 baseline.
- The bench asserts `convergence_status == "budget-exhausted"` each round, so a silent early-exit can't fake a speedup. Run benchmarks standalone without `--cov` (coverage distorts timings).
- Gate state to not regress: 842 default tests passing in ~3–7 min, whole-project basedpyright genuinely 0/0/0, grasp.py at 100% coverage.
- The behavior-identity proof pattern worked exactly as designed in 12.1: zero test modifications, goldens verified by `git status` cleanliness — repeat it, don't invent new equivalence tests unless one is genuinely cheap.
- uv build-flake recovery (fires after commits/pyproject edits): `uv sync --native-tls` once, then `uv run --no-sync`. Run `tests/unit` and `tests/integration` as separate invocations if invoked explicitly (conftest import collision).

### Project Structure Notes

- **Modified:** `src/steeproute/solver/grasp.py` (`_best_theta_prefix` rewrite + docstring) and `src/steeproute/solver/distinctness.py` (tracker-internal caching + set-math helper), plus sprint-status and a new `.benchmarks/` autosave JSON from the close-out run.
- **Untouched:** everything else — all tests unmodified, goldens, `models.py`, `validator.py`, `solver/reuse.py`, `tests/integration/exhaustive_oracle.py`.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest --cov` (~4–7 min; much slower means a test is hitting the network).
- Benchmarks: `uv run pytest tests/benchmarks -m benchmark --benchmark-autosave --benchmark-compare` — compare against `0002_*`.
- Goldens verification is `git status` cleanliness under `tests/e2e/goldens/` after the full suite — not a rebake.

### References

- [Source: epics.md §Story 12.2 + §Epic 12 preamble](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; behavior-identical mandate; benchmark-judged close-out
- [Source: research/steeproute-bottleneck-analysis-2026-07-03.md §Solver ranked list items 3–4 + §Phase-3 recommendation items 2–3](_bmad-output/planning-artifacts/research/steeproute-bottleneck-analysis-2026-07-03.md) — 10.6% + 7.1% attribution, incremental-sums design sketch, "canonical gate" and "compute once at insertion" prescriptions, "no golden impact" claims
- [Source: src/steeproute/models.py:94-107](src/steeproute/models.py) — `route_avg_gradient` single-sourcing contract (the bit-identity promise AC #1 protects)
- [Source: src/steeproute/solver/grasp.py:353-392](src/steeproute/solver/grasp.py) — `_route_slope_ok` + `_best_theta_prefix` current shape; grasp.py:320 call site
- [Source: src/steeproute/solver/distinctness.py](src/steeproute/solver/distinctness.py) — `_canonical_edge_set` / `jaccard_distance` / `TopNTracker.consider` current shape; admission-policy docstring to preserve
- [Source: src/steeproute/validator.py:94,135](src/steeproute/validator.py) — external `jaccard_distance` caller pinning the public signature
- [Source: _bmad-output/implementation-artifacts/12-1-precompute-static-per-node-adjacency-for-rcl-construction.md](_bmad-output/implementation-artifacts/12-1-precompute-static-per-node-adjacency-for-rcl-construction.md) — post-12.1 baseline (`0002_f2671d1*`), gate state, behavior-identity proof pattern

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Gates (all green, zero test modifications):**

```
tests/unit (test_distinctness + test_grasp_construction)   → 36 passed
solver integration + metamorphic (4 modules)                → 54 passed
tests/e2e (goldens, seeded repro, interrupt)                → 94 passed; git status clean under tests/e2e/goldens/
ruff check src tests                                        → All checks passed!
ruff format --check src tests                               → 104 files already formatted
basedpyright (whole project)                                → 0 errors, 0 warnings, 0 notes
pytest --cov (default markers)                              → 842 passed, 12 deselected in 3:02; grasp.py + distinctness.py 100% cov
```

**Benchmark compare (AC #4), `--benchmark-autosave --benchmark-compare=0002` vs post-12.1 baseline:**

```
test_grasp_1k_iterations   0002 baseline: mean 145.2 ms, median 123.2 ms, min 116.5 ms
test_grasp_1k_iterations   NOW:           mean 102.2 ms, median  81.3 ms, min  74.5 ms
→ ~1.52× median / ~1.56× min throughput gain over post-12.1 (cumulative vs 0001 Epic 11
  baseline: median 300.9 → 81.3 ms ≈ 3.7×)
setup-stage benchmarks: unchanged within noise (expected — setup untouched)
```

Saved as `.benchmarks/Windows-CPython-3.13-64bit/0003_b0e85dd*.json`.

### Completion Notes List

**Incremental θ-prefix (Task 1).** `_best_theta_prefix` now builds forward cumulative left-fold sums of `length_m` and `d_plus_m + d_minus_m` in one O(n) pass, then runs the same downward scan testing each `end` in O(1). The fold is exactly `route_avg_gradient`'s (`0.0` start, per-edge accumulation in walk order), so every incremental ratio is bit-identical to the old per-prefix recomputation, including the mirrored zero-length → `0.0` branch. The winning prefix is sliced once and still passes the canonical `_route_slope_ok` gate before admission (AC #1's models.py single-sourcing contract); its `objective` is the cumulative climb at `end` — the identical fold the old re-sum produced. Backward subtraction was deliberately avoided (different fold order → last-ulp drift → candidate-selection change).

**Cached distinctness (Task 2).** New `_jaccard_from_sets` helper carries the set math; public `jaccard_distance(a, b, segment_map)` delegates to it, signature and behavior unchanged (validator.py:135 and test_distinctness.py call it directly). `TopNTracker` now holds `_HeldEntry` NamedTuples pairing each `Solution` with its canonical set, computed once at insertion; `consider()` computes the candidate's set once per call and compares against cached held sets. Admission semantics preserved exactly — insertion order, overlap detection, both eviction branches, non-finite guard first. Cache lives in the tracker because `Solution` is `slots=True`.

**Behavior identity (Task 3, AC #3).** Proven by the existing suite exactly as the story demanded: cached frozensets are equal to recomputed ones and the incremental fold is bit-identical, so admissions and prefix selection are unchanged → byte-identical solutions. Goldens byte-untouched, 842 default tests pass unmodified, `models.py` / `validator.py` / `solver/reuse.py` / oracle untouched.

**Measured gain (Task 4, AC #4).** ~1.52× median solver throughput over the post-12.1 baseline (123.2 → 81.3 ms per 1k iterations on grenoble_small) — inside the story's predicted 1.2–1.6× band for the ~15%-of-original attribution. Cumulative Epic-12 speedup vs the Story 11.3 baseline: ~3.7×, already inside the analysis's 2.5–4× Phase-3 headroom estimate with 12.3 still to come.

### File List

**Modified:**
- `src/steeproute/solver/grasp.py` — `_best_theta_prefix` rewritten to cumulative-sum scan + canonical gate; docstring updated.
- `src/steeproute/solver/distinctness.py` — `_jaccard_from_sets` helper, `_HeldEntry` NamedTuple, tracker-internal canonical-set caching; class docstring updated.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.
- `_bmad-output/implementation-artifacts/12-2-incremental-theta-prefix-metrics-and-cached-distinctness-sets.md` — this story file.

**New:**
- `.benchmarks/Windows-CPython-3.13-64bit/0003_b0e85dd59959499d42e323aca55ead31a3abf8d1_20260703_212551_uncommited-changes.json` — post-12.2 benchmark autosave.

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-03 | Yann (Claude Fable 5) | Story 12.2 implemented: `_best_theta_prefix` scans prefixes in O(1) from forward cumulative left-fold sums (bit-identical to `route_avg_gradient`, canonical gate retained); `TopNTracker` caches each held solution's canonical edge set at insertion and the candidate's once per `consider()` (public `jaccard_distance` unchanged). Behavior-identical: 842 default tests pass unmodified, goldens byte-untouched. Solver throughput ~1.52× over post-12.1 (median 123.2 → 81.3 ms per 1k iterations; ~3.7× cumulative vs Epic 11 baseline), recorded via `--benchmark-compare`. |
