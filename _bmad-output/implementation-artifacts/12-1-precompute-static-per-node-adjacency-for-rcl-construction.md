# Story 12.1: Precompute static per-node adjacency for RCL construction

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the solver to stop rebuilding static graph data on every walk step,
so that queries run substantially faster with identical results.

## Acceptance Criteria

1. **Once-per-solve adjacency table.** Given the contracted climb graph is immutable for the duration of a solve, `run()` precomputes, once per solve, a per-node adjacency table of pre-built records (`Edge` object, blocking frozenset, static sort order) and `_build_rcl` consumes it — no networkx view construction, no `Edge` re-wrapping, no `blocking_ids` recomputation, no re-sorting per step.
2. **Behavior-identical.** Solver output is identical (same candidates in the same order for the same seed) and the full regression-golden suite passes untouched.
3. **Measured gain.** The benchmark suite shows a material throughput gain over the pinned Story 11.3 baseline (the bottleneck analysis attributes ~35–40% of the run to the eliminated work), recorded via `--benchmark-compare` in the story close-out.
4. **Interfaces unchanged.** Solver public interfaces (`GraspSolver` constructor, `run()`, `best_so_far`, `convergence_status`, `convergence_iteration`), the validator, and the exhaustive oracle are unchanged.

## Tasks / Subtasks

- [x] Task 1: Build the per-node adjacency table (AC: #1)
  - [x] One pass over `graph.graph.edges(keys=True, data=True)` grouped by source node: per edge, pre-build the `Edge` dataclass, its blocking frozenset (`blocking_ids(data, u, v, k, self._non_exempt_ids)`), and drop edges failing the two walk-state-independent filters (SAC cap vs `self._cap_rank`, `descends_over_cap(data, self._max_descent_slope)`) — they can never become feasible within this solve
  - [x] Pre-sort each node's records once with the existing key `(-(d_plus_m + d_minus_m), node_v, key)`; store as plain tuples keyed by node (nodes with no surviving out-edges → empty tuple / `.get` default)
  - [x] Build the table in `run()` before the iteration loop (after the empty-graph early return), per AC #1 wording — this also keeps the precompute inside the benchmark's measured region (the 11.3 bench measures only `.run()`)
- [x] Task 2: Make `_build_rcl` consume the table (AC: #1, #2)
  - [x] Replace the `out_edges` loop: iterate the node's pre-sorted records, keep those passing the two walk-state filters (`(u, v, k) not in used_directed`, `not (blocking & used_segments)`), stop as soon as `RCL_SIZE` are collected — pre-sorted input makes first-K-feasible identical to old sort-all-then-truncate
  - [x] Return shape stays `list[tuple[Edge, frozenset]]` so `_construct_one` is untouched beyond, at most, passing the table through
  - [x] Update the module docstring's determinism paragraph (the "RCL ranking ends in a total sort" claim) to describe the precomputed static order
- [x] Task 3: Prove behavior identity (AC: #2, #4)
  - [x] Full default suite passes with zero test modifications (unit `test_grasp_construction`, integration `test_grasp_on_fixture` / `test_grasp_reproducible` / `test_grasp_theta_prefix` / `test_metamorphic` / oracle quality gate, e2e `test_pinned_regressions` / `test_seeded_reproducibility`)
  - [x] Goldens byte-untouched (`git status` clean under `tests/e2e/goldens/`); no changes to `validator.py`, `solver/reuse.py`, or `tests/integration/exhaustive_oracle.py`
- [x] Task 4: Benchmark close-out (AC: #3)
  - [x] `uv run pytest tests/benchmarks -m benchmark --benchmark-autosave` (no `--cov`), compare against the committed `0001_*` baseline via `--benchmark-compare`; record the delta in the Dev Agent Record and commit message
- [x] Task 5: Gates + status
  - [x] `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `pytest --cov` green; update sprint-status

## Dev Notes

### What the profile indicts (why this story exists)

The 11.2 analysis pins ~57.5% of query wall-clock in `_build_rcl`, of which ~35–40% is pure static-data churn eliminated by this story:

- **1a (~18–19%):** networkx `out_edges(...)` view objects re-created every call, ~1M+ times per run
- **1c (9.6%):** the same static graph edges re-wrapped into fresh `Edge` dataclasses on every visit
- **1d (5.5%):** `blocking_ids` frozenset math recomputed per visit
- **1e (~3.5%):** sorting all feasible edges every step on a key that is static per edge

Item 1b (`_build_rcl` self-time, 20.9%) shrinks too but its remainder (walk-state set ops) is inherent — don't chase it here.

### Behavior-identity argument (the thing to preserve)

RNG consumption depends only on the RCL *length* at each step (`integers(0, len(rcl))`), and candidate choice on its *order*. Both are preserved: the static filters (SAC cap, descent cap) don't read walk state, so pre-filtering at table build removes exactly the edges the old loop rejected every visit; the sort key is static per edge and total (tie-break `(node_v, key)` is unique per source node), so pre-sorting once and taking the first `RCL_SIZE` feasible records yields the same list as filter-then-sort-then-truncate. Same RCLs → same draws → byte-identical solutions. This is why AC #2 demands goldens green *untouched* — any golden diff means the refactor is wrong, not that a rebake is due (12.3 owns the epic's one rebake).

### Implementation facts

- All params feeding the static filters are frozen before `run()`: `self._cap_rank`, `self._max_descent_slope`, `self._non_exempt_ids` are set in `__init__` from immutable `SolverParams` ([grasp.py:211-219](src/steeproute/solver/grasp.py)).
- `Edge` and `Solution` are frozen dataclasses — sharing one `Edge` instance across many RCLs/solutions is safe; existing tests compare by equality, not identity.
- Reuse the existing helpers, don't reimplement: `blocking_ids` handles untagged test graphs (falls back to directed identity — `test_grasp_construction` builds hand-made graphs relying on this), `max_sac_rank` handles `None`/unrecognized `sac_scale`.
- `_build_rcl` is private — its signature may change freely (take the table or read a `self` attribute set by `run()`; if a record type wants a name, a module-level NamedTuple in grasp.py is enough — no new module).
- The oracle ([exhaustive_oracle.py:181](tests/integration/exhaustive_oracle.py)) *mirrors* `_build_rcl` feasibility through the shared `solver/reuse.py` helpers but is independent code — leave it alone; the quality gate then independently confirms the feasible set didn't move.
- grasp.py already carries the pyright header pragma for networkx Unknowns; the table build keeps the networkx surface inside it.

### Out of scope (don't drift)

- `_best_theta_prefix` re-summing and `_canonical_edge_set` caching → Story 12.2
- RNG batching (any change to draw sequence) → Story 12.3; this story must not touch `self._rng` call sites
- Flat-array/interface extraction beyond the table itself → Phase 4, only if 12.4 says go
- Setup pipeline, validator, oracle, output rendering

### Previous story intelligence (11.3 + Epic 11 close-out)

- Benchmark suite exists and is the AC #3 instrument: `tests/benchmarks/test_solver_throughput.py` measures seconds per 1k seeded GRASP iterations on grenoble_small; committed baseline `.benchmarks/Windows-CPython-3.13-64bit/0001_*.json` pins **~313 ms/1k iters (~3,200 iter/s)**. Bench asserts `convergence_status == "budget-exhausted"` each round, so a silent early-exit can't fake a speedup.
- README "Performance benchmarks" documents the autosave/compare loop expected around exactly this kind of commit.
- Gate state to not regress: 842 default tests passing, whole-project basedpyright genuinely 0/0/0.
- uv build-flake recovery (fires after commits/pyproject edits): `uv sync --native-tls` once, then `uv run --no-sync`. Run `tests/unit` and `tests/integration` as separate invocations if invoked explicitly (conftest import collision).

### Project Structure Notes

- **Modified:** `src/steeproute/solver/grasp.py` only (table build + `_build_rcl` rewrite + docstring maintenance), plus sprint-status and a new `.benchmarks/` autosave JSON from the close-out run.
- **Untouched:** everything else — all tests unmodified (behavior-identity is proven by the *existing* suite, not new tests; an equivalence unit test is optional and only if cheap), goldens, `models.py`, `solver/reuse.py`, `solver/descent.py`, `validator.py`, oracle.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest --cov` (~4–7 min; much slower means a test is hitting the network).
- Benchmarks run standalone without `--cov` (coverage instrumentation distorts timings).
- Goldens verification is `git status` cleanliness under `tests/e2e/goldens/` after the full suite — not a rebake.

### References

- [Source: epics.md §Story 12.1 + §Epic 12 preamble](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth, behavior-identical mandate, benchmark-judged close-out
- [Source: research/steeproute-bottleneck-analysis-2026-07-03.md §Solver ranked list + §Phase-3 recommendation item 1](_bmad-output/planning-artifacts/research/steeproute-bottleneck-analysis-2026-07-03.md) — items 1a/1c/1d/1e attribution, the precompute design sketch, "no golden impact" claim
- [Source: sprint-change-proposal-2026-07-03-solver-optimization.md §2-3](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-03-solver-optimization.md) — story sequencing rationale (12.1 first; goldens stay checkable through 12.2)
- [Source: src/steeproute/solver/grasp.py:367-484](src/steeproute/solver/grasp.py) — `_construct_one` / `_build_rcl` current shape, sort key, RNG call sites to leave untouched
- [Source: src/steeproute/solver/reuse.py](src/steeproute/solver/reuse.py) — `blocking_ids` / `non_exempt_base_segment_ids` semantics + test-graph fallback
- [Source: _bmad-output/implementation-artifacts/11-3-dedicated-benchmark-suite-pinning-pre-optimization-baselines.md](_bmad-output/implementation-artifacts/11-3-dedicated-benchmark-suite-pinning-pre-optimization-baselines.md) — baseline location, compare workflow, gate state

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Gates (all green, zero test modifications):**

```
tests/unit/test_grasp_construction.py          → 12 passed
solver integration + metamorphic (7 modules)   → 67 passed
goldens + seeded repro + interrupt e2e         → 7 passed; git status clean under tests/e2e/goldens/
ruff check src tests                           → All checks passed!
ruff format --check src tests                  → 104 files already formatted
basedpyright (whole project)                   → 0 errors, 0 warnings, 0 notes
pytest --cov (default markers)                 → 842 passed, 12 deselected in 3:18; grasp.py 100% cov
```

**Benchmark compare (AC #3), `--benchmark-autosave --benchmark-compare` vs committed `0001_070debf` baseline:**

```
test_grasp_1k_iterations   0001 baseline: mean 302.0 ms, median 300.9 ms, min 287.1 ms
test_grasp_1k_iterations   NOW:           mean 145.2 ms, median 123.2 ms, min 116.5 ms
→ ~2.1× mean / ~2.4× median throughput gain (one 240 ms outlier round inflates the NOW mean)
setup-stage benchmarks: unchanged within noise (expected — setup untouched)
```

Saved as `.benchmarks/Windows-CPython-3.13-64bit/0002_f2671d1*.json`.

### Completion Notes List

**Table build (Task 1).** New `_CandidateRecord` NamedTuple (`directed_id`, `edge`, `blocking`) and `_build_adjacency()`: one pass over `graph.edges(keys=True, data=True)` grouped by source node, applying the two walk-state-independent filters once (SAC cap, FR32 descent cap — edges failing them can never become feasible within a solve, so they are dropped from the table), pre-building each `Edge` and its `blocking_ids` frozenset via the unchanged `solver/reuse.py` helpers, then pre-sorting each node's records with the exact old key `(-(d_plus_m + d_minus_m), node_v, key)`. Built in `run()` after the empty-graph early return — once per solve per AC #1, and inside the benchmark's measured region so the ~2.4× claim honestly includes the table cost.

**Hot loop (Task 2).** `_build_rcl` no longer touches networkx: it scans the node's pre-sorted records via `self._adjacency.get(current, ())`, applies only the two walk-state filters (`directed_id in used_directed`, `blocking & used_segments`), and stops at `RCL_SIZE` — first-K-feasible over pre-sorted input is identical to the old filter→sort→truncate. Signature and return shape unchanged; `_construct_one` untouched (its `(self)` signature is load-bearing for `test_interrupt_in_process.py`'s monkeypatch). Module docstring's FR29 "RCL ranking" paragraph updated to describe the precomputed static order.

**Behavior identity (Task 3, AC #2/#4).** Proven by the existing suite exactly as the story demanded: same RCL content and order → same RNG consumption → byte-identical solutions. Goldens byte-untouched, seeded-reproducibility e2e green, metamorphic invariants green, oracle/validator/`solver/reuse.py` untouched, all 842 default tests pass unmodified.

**Measured gain (Task 4, AC #3).** ~2.4× median solver throughput on grenoble_small (301 → 123 ms per 1k iterations) — above the ~1.6× the 35–40% attribution alone predicts, consistent with the story's note that item 1b's self-time (per-step set ops/bookkeeping over fewer, pre-built records) shrinks too.

### File List

**Modified:**
- `src/steeproute/solver/grasp.py` — `_CandidateRecord`, `_build_adjacency()`, `_build_rcl` rewrite, `run()` precompute hook, docstring maintenance.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.
- `_bmad-output/implementation-artifacts/12-1-precompute-static-per-node-adjacency-for-rcl-construction.md` — this story file.

**New:**
- `.benchmarks/Windows-CPython-3.13-64bit/0002_f2671d19ee627ad75bb4ff45d94ad4ce6b0fc374_20260703_210038_uncommited-changes.json` — post-12.1 benchmark autosave.

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-03 | Yann (Claude Fable 5) | Story 12.1 implemented: once-per-solve per-node adjacency table (`_build_adjacency` — static filters, pre-built `Edge`s + blocking sets, per-node static pre-sort) consumed by a graph-free `_build_rcl` with early stop at `RCL_SIZE`. Behavior-identical: 842 default tests pass unmodified, goldens byte-untouched, oracle/validator untouched. Solver throughput ~2.4× (median 301 → 123 ms per 1k iterations on grenoble_small), recorded via `--benchmark-compare` against the Story 11.3 baseline. |
