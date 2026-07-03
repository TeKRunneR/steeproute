# Story 11.2: Profile solver and setup pipeline into a ranked bottleneck list

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want py-spy flamegraphs of a realistic GRASP run plus the instrumented setup breakdown, analyzed into a ranked bottleneck list,
so that Phase 3 optimization targets measured hotspots instead of guesses.

## Acceptance Criteria

1. **py-spy available as a dev dependency.** Added to `[dependency-groups] dev` in `pyproject.toml` (needs ≥ 0.4.1 for CPython 3.13); `uv run py-spy --version` works natively on Windows. No other dependency changes.

2. **Solver flamegraph captured (T4→T5 input).** `py-spy record` flamegraph(s) of a quality-params GRASP run — `--iter-budget 200000 --stagnation-iters 10000 --difficulty-cap T4`, fixed seed, Grenoble-scale prepared area — with the capture covering enough of the steady-state search loop for stable percentage attribution. Flamegraph artifacts committed (or linked from the findings doc with their location documented).

3. **Cold-cache setup breakdown captured (reuses 11.1, T3).** One real network-on `steeproute-setup` run from an empty `--cache-dir`, with Story 11.1's per-stage timeline (7 stage done-lines + `tile i/N` lines) recorded verbatim.

4. **Findings document delivered** in `_bmad-output/planning-artifacts/` containing: the ranked bottleneck list with percentage attribution; Python-vs-native attribution per hotspot; an explicit answer to the research's decision question — scoring/feasibility math vs. networkx calls vs. loop skeleton; and the setup per-stage table separating network wait from CPU work.

5. **Phase-3 recommendation closes the document,** following the research's decision tree (numpy batch scoring / rustworkx / PyO3 kernel / setup-side levers), scoped to what the measurements actually indict.

6. **No production code changes.** `src/steeproute/` and `tests/` untouched; Scalene-under-WSL2 used only if flamegraphs leave the Python-vs-native split ambiguous (record the judgment either way); default suite and all four gates stay green; regression goldens untouched.

## Tasks / Subtasks

- [x] Task 1: Add py-spy dev dependency (AC: #1). `uv add --dev py-spy`; expect the corporate-TLS flake after the pyproject edit (`uv sync --native-tls` once, then `uv run --no-sync`). Verify `uv run py-spy --version`.
- [x] Task 2: Cold-cache setup run with timeline capture (AC: #3). Fresh scratch `--cache-dir` (this also cold-starts the osmnx HTTP cache — it lives under the cache root since 11.1), stdout tee'd to a file. This same prepared cache then feeds Task 3.
- [x] Task 3: Record solver flamegraph(s) (AC: #2). Quality params + `--seed 42` + raised `--time-budget`; capture via `py-spy record -o <name>.svg --subprocesses -- uv run --no-sync steeproute ...` (or attach `--pid` to a running solve). If the run converges on stagnation too quickly for stable attribution, take a second capture with `--stagnation-iters 0` (full 200k iterations); label both.
- [x] Task 4: Analyze into the ranked bottleneck list (AC: #4). Rank hotspots by sample share; classify each Python vs native; answer the decision question explicitly; build the setup table from Task 2's timeline (network-bound vs CPU-bound per stage).
- [x] Task 5: Write the findings document + commit artifacts (AC: #4, #5). Include run provenance (commit, params, area, machine), the Phase-3 recommendation per the decision tree, and links to the committed flamegraphs.
- [x] Task 6: Close out (AC: #6). Confirm `git status` shows no `src/`/`tests/` changes; gates green (pyproject + markdown + SVG only); update sprint-status.

## Dev Notes

### Recommendation (read first)

This is a **measurement-and-writeup story**: the deliverable is a document, not code. Repo changes are exactly: the py-spy dev dep, the findings doc, the flamegraph SVGs (+ optionally the raw setup timeline capture). Resist fixing anything you find — Phase 3 is a separate, later scoping decision that this document feeds.

**Dependency:** Story 11.1 (currently `review`) must be done and merged first. Its `StageProgress` timeline is the setup-capture instrument (AC #3), and its osmnx-cache fix relocated the Overpass HTTP cache under the steeproute cache root — which is what makes "fresh `--cache-dir` = genuinely cold cache" true.

### The profiling run (exact recipe)

The gallery recipe ([docs/examples/README.md](docs/examples/README.md)) is the pinned quality-params precedent — reuse the quickstart/Chamrousse area, which is documented in-repo and known-good:

```powershell
# 1. Cold setup, timeline captured (AC #3). SCRATCH = any throwaway dir.
uv run --no-sync steeproute-setup --center 45.12,5.88 --radius 6.5 --cache-dir $SCRATCH |
    Tee-Object -FilePath setup-timeline.txt

# 2. Profiled quality query (AC #2).
uv run --no-sync py-spy record -o grasp-flamegraph.svg --subprocesses -- `
    uv run --no-sync steeproute --center 45.12,5.88 --radius 6.0 --cache-dir $SCRATCH `
    --output-dir $SCRATCH\out --seed 42 --n 3 --difficulty-cap T4 `
    --iter-budget 200000 --stagnation-iters 10000 --time-budget 36000 `
    --elevation-deadband 1 --j-max 0
```

Known facts about this workload (from the committed gallery sidecars):

- **`--time-budget` defaults to 600 s** — raise it explicitly so the iteration/stagnation budget governs, not the wall clock.
- **Stagnation fires early at gallery scale**: Saint-Nizier converged at iteration 21,352 (~32 s); Chamrousse ~7 s ([docs/examples/README.md](docs/examples/README.md), route-1.json `convergence_iteration`). A ~10–30 s capture may be enough for coarse ranking, but for stable percentages take the second capture with `--stagnation-iters 0` (0 legitimately disables the check, [\_shared.py:260](src/steeproute/cli/_shared.py)) — at the observed ~660 iter/s that's a ~5-minute pure steady-state sample of the full 200k iterations. Label which capture each number in the doc comes from.
- `--seed 42` makes the profiled run reproducible (NFR4 edge-set determinism); record seed + params + commit hash in the doc's provenance block.

### py-spy on Windows — practicalities

- **The uv-spawns-a-child pitfall**: `py-spy record -- uv run ...` profiles uv's process tree, and Windows console-script launchers (`steeproute.exe`) also spawn a python child — `--subprocesses` catches all of it. Alternative: start the solve in one terminal, then `py-spy record -o out.svg --pid <python-PID>` (attach needs no elevation for your own process; if it's denied, fall back to the `--subprocesses` launch form).
- **Python-vs-native is mostly readable without extra tooling**: networkx and the whole GRASP loop (`solver.py`, `TopNTracker`, scoring) are **pure Python** — default py-spy flamegraphs resolve them completely. "Native" time (numpy ufuncs, shapely/GEOS) appears as sustained leaf time attributed to the Python line making the call — a leaf frame sitting inside a numpy wrapper *is* native time. `py-spy record --native` adds true native frames on Windows if the split within a hotspot matters; Scalene/WSL2 is the last resort only if that still leaves ambiguity (AC #6).
- Default sampling rate (100 Hz) is fine; the solver is single-threaded, so no `--gil`/`--idle` fiddling needed.

### Setup table — separating network wait from CPU

Story 11.1's seven stages already partition cleanly: `osm-download` (network — one Overpass request) and `dem-resolve` (network-dominated — WMS tile fetches, plus mosaic CPU) vs. `trail-filter` / `polyline-smoothing` / `resampling` / `elevation-sampling` / `cache-write` (CPU/disk). If the network/CPU split *within* `dem-resolve` matters, the `tile i/N` lines bracket the HTTP waits — timestamp the capture (e.g. pipe through a timestamping filter or just note tile-line spacing). Don't build new instrumentation; the AC only asks for a per-stage table with the network/CPU distinction called out.

### Findings document

- **Location/naming**: follow the research-doc precedent — `_bmad-output/planning-artifacts/research/steeproute-bottleneck-analysis-2026-07-XX.md`, flamegraph SVGs committed next to it (e.g. `research/profiling/`). py-spy SVGs are typically a few hundred KB — fine to commit; if one balloons, commit the smaller capture and document where the raw one lives.
- **Required content** (AC #4/#5): ranked bottleneck list with % attribution → Python-vs-native per hotspot → explicit decision-question answer → setup per-stage table → Phase-3 recommendation per the research's decision tree: scoring math dominates → numpy batching; networkx algorithm calls dominate → rustworkx; bespoke loop skeleton dominates → extract-interface-first, then PyO3 kernel; setup-side findings → caching/concurrency levers (DEM tile fetch, windowed raster reads).
- This is an analysis doc, not a PRD artifact — keep it factual and provenance-stamped; it's the input that scopes Phase 3+.

### Out of scope (don't drift)

- **Any optimization work** — even one-liners the flamegraph makes tempting. Phase order is non-negotiable (research constraint).
- `tests/benchmarks/` / pytest-benchmark — Story 11.3.
- New instrumentation in `src/` — 11.1's seams are the instrument; if they prove insufficient, record the gap in the doc rather than patching.
- Scalene/WSL2 by default — conditional fallback only.

### Project Structure Notes

- **Modified:** `pyproject.toml` (dev group only) + `uv.lock`.
- **New:** findings doc + flamegraph SVGs under `_bmad-output/planning-artifacts/research/`; optionally the raw setup-timeline capture alongside.
- **Untouched:** everything under `src/steeproute/` and `tests/` (AC #6 — verify with `git status` at close-out).

### Testing standards summary

- No new tests — nothing testable changes. "Green" means: default `pytest` suite unaffected, `ruff check` / `ruff format --check` / whole-project `basedpyright` 0/0/0 unaffected (markdown/SVG are outside their scopes; `_bmad-output` is excluded in ruff config).
- Build-flake recovery after the pyproject edit: `uv sync --native-tls` once, then `uv run --no-sync`.
- Run `tests/unit` and `tests/integration` in separate pytest invocations if running them explicitly (`from conftest import` collision).

### References

- [Source: epics.md §Story 11.2 + §T4–T5 + §Epic 11 preamble](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; decision deliverable definition
- [Source: research/technical-steeproute-performance-tuning-research-2026-07-02.md §Profiling Tooling + §Vectorization Ceiling + §Implementation Roadmap Phase 1 + §Research Synthesis](_bmad-output/planning-artifacts/research/technical-steeproute-performance-tuning-research-2026-07-02.md) — the decision question, the decision tree, py-spy/Scalene platform notes, phase-order constraint
- [Source: docs/examples/README.md](docs/examples/README.md) — quality-params recipe (200k/10k/T4, seed 42, deadband 1, j-max 0), area coordinates, observed convergence behavior
- [Source: docs/examples/saint-nizier/route-1.json §metadata](docs/examples/saint-nizier/route-1.json) — convergence_iteration 21352 (throughput estimate ~660 iter/s)
- [Source: src/steeproute/cli/_shared.py:445-463](src/steeproute/cli/_shared.py) — `--iter-budget` (default None = unlimited), `--time-budget` (default 600 s), `--stagnation-iters` (0 disables)
- [Source: _bmad-output/implementation-artifacts/11-1-setup-stage-timing-seams-and-progress-reporting.md](_bmad-output/implementation-artifacts/11-1-setup-stage-timing-seams-and-progress-reporting.md) — previous story: the 7-stage timeline names, `StageProgress.timings`, osmnx cache now under cache root, gate status (basedpyright genuinely 0/0/0)
- py-spy ≥ 0.4.1 supports CPython 3.13 and Windows natively ([PyPI](https://pypi.org/project/py-spy/), [releases](https://github.com/benfred/py-spy/releases))

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.14 / uv; py-spy 0.4.2 installed via `uv add --dev py-spy --native-tls` (no build flake this session). Story 11.1's changes were present uncommitted in the working tree (user decision: skip dedicated 11.1 review, proceed on top).

**Captures (all live-network / real runs, Chamrousse quickstart area):**

```
setup (cold cache, --radius 6.5)                 → 54.01 s, 7-stage timeline captured
query spec run (200k/10k stagnation, seed 42)    → converged ~47.5k iters, 17.28 s, 1994 samples
query steady-state (stagnation 0, full 200k)     → 64.11 s, 6506 samples (SVG) + 7334 samples (raw)
```

**Gates:**

```
ruff check src tests            → All checks passed!
ruff format --check src tests   → 101 files already formatted
basedpyright (whole project)    → 0 errors, 0 warnings, 0 notes
pytest (default markers, full)  → 842 passed, 6 deselected
```

**py-spy Windows notes (for future profiling):** py-spy cannot introspect the uv trampoline `python.exe` directly (`Error: Failed to find python version from target process`) — `--subprocesses` resolves it. Percentages were computed from a `--format raw` collapsed-stack capture (exact aggregation) rather than eyeballed from SVGs; analysis scripts kept in session scratchpad only.

### Completion Notes List

**Decision question answered (T5).** The solver is 94.1% of query wall-clock and effectively all pure Python: numpy vectorized math is 0.0% of samples, networkx *algorithms* 0.0%. The dominant costs are the bespoke RCL loop (`_build_rcl` 57.5% cumulative) with per-step object churn — networkx adjacency-view re-construction (~18–19%), `Edge` dataclass re-wrapping (~10%), blocking-set recompute (~5.5%) — plus scalar `Generator.integers` draws (~13%, the only native time, overhead-shaped), θ-prefix re-summing (~10.6%), and distinctness (~7.1%). Verdict: **loop skeleton + object churn**, not scoring math, not networkx algorithms.

**Phase-3 recommendation.** Pure-Python data-structure fixes, ordered: (1) precomputed static per-node adjacency (~35–40% of run, no golden impact), (2) incremental θ-prefix metrics (~10%, no golden impact if the canonical gate re-checks), (3) cached canonical edge sets (~4–5%), (4) batched RNG last (~13%, forces golden rebake — Story 9.3 precedent). Estimated 2.5–4× headroom. Phase-4 branch if needed: extract-interface-first → PyO3 kernel. **rustworkx and numpy batch scoring explicitly not indicated** by the measurements.

**Setup breakdown (T3 reuse).** 54.01 s cold-cache run: ≈81% network (osm-download 22.4 s + dem-resolve 21.7 s, 4 serial WMS tiles) / ≈19% CPU (elevation-sampling 7.5 s dominant). Low-priority target; concurrent tile fetch and the (already-fixed) persistent osmnx cache are the levers if ever wanted.

**Scalene judgment (AC #6).** Not needed — every hotspot resolves to named pure-Python frames and the single native contributor is line-pinpointed (`grasp.py:400`); no ambiguity for WSL2/Scalene to resolve.

**No production code changes:** `src/` and `tests/` diffs in the working tree are all Story 11.1's; this story touched only `pyproject.toml` (+1 dev dep), `uv.lock`, and planning-artifacts.

### File List

**Modified:**
- `pyproject.toml` — `py-spy>=0.4.2` added to `[dependency-groups] dev`.
- `uv.lock` — lockfile update for py-spy.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.

**New:**
- `_bmad-output/planning-artifacts/research/steeproute-bottleneck-analysis-2026-07-03.md` — the findings document (ranked list, attribution, decision answer, setup table, Phase-3 recommendation).
- `_bmad-output/planning-artifacts/research/profiling/grasp-flamegraph-200k.svg` — steady-state flamegraph (primary evidence).
- `_bmad-output/planning-artifacts/research/profiling/grasp-flamegraph-spec.svg` — AC-spec run flamegraph (converged).
- `_bmad-output/planning-artifacts/research/profiling/grasp-200k.collapsed` — raw collapsed stacks (source of all percentages).
- `_bmad-output/planning-artifacts/research/profiling/setup-timeline.txt` + `setup-timeline-timestamped.txt` — verbatim cold-cache setup capture.

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-03 | Yann (Claude Fable 5) | Story 11.2 implemented (T4–T5): py-spy 0.4.2 dev dep; three profiled captures of the quality-params GRASP run (spec 200k/10k converged 17.3 s; steady-state full 200k 64.1 s; raw collapsed stacks) + one cold-cache 54 s setup run captured via 11.1's stage timeline. Findings doc `research/steeproute-bottleneck-analysis-2026-07-03.md` delivers the ranked bottleneck list: solver 94% of wall-clock, all pure Python (numpy 0.0%, networkx algorithms 0.0%); decision question resolves to loop-skeleton + per-step object churn; Phase-3 recommendation = static-adjacency precompute → incremental θ-prefix → cached distinctness sets → (rebake-gated) batched RNG, est. 2.5–4×; Phase-4 branch PyO3 kernel, rustworkx explicitly not indicated. No production code changes; gates green (842 passed). |
