# Story 12.4: Re-profile, benchmark reconciliation, and Phase-4 decision

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want a post-optimization profile and a consolidated benchmark comparison against the Epic 11 baselines,
so that the decision on what to optimize next — anywhere in the execution, Rust or not — is made on measurements, not projections.

## Acceptance Criteria

1. **Fresh quality-params profile.** py-spy captures of the same Chamrousse quality-params workload profiled in 11.2 (seed 42, T4, deadband 1, j-max 0) on the post-12.3 code, delivering BOTH the solver-internal attribution (stable percentages on the remaining hot loop) AND the full-run *phase split* (solver vs imports/cache-read vs query-side stages 6–9 vs render) — the phase split has flipped post-Epic-12 (see Dev Notes) and is now load-bearing for the go/no-go. Artifacts committed under `research/profiling/`.
2. **Confirming larger-area capture.** One additional capture on a larger area confirming — or refuting — that both the solver shape and the phase split transfer, resolving the 11.2 analysis's single-area caveat. The 2026-07-04 radius-10 Chartreuse capture (Dev Notes) is the precedent; reproduce it cleanly or capture Saint-Nizier.
3. **Consolidated benchmark reconciliation.** Cumulative speedup vs the Story 11.3 baselines (`0001_070debf*`) is consolidated from the per-story autosaves (0002–0004) plus one fresh confirming run at the committed HEAD, and the findings doc states explicitly whether the measured result lands inside the predicted 2.5–4× band.
4. **Findings doc with explicit what-next recommendation.** *(Broadened 2026-07-04: the goal is whole-execution wall-clock, not solver throughput.)* A findings update in `_bmad-output/planning-artifacts/research/` records the new profile shape (ranked list with % attribution, both areas, full-run phase split), the cumulative speedup, and closes with an explicit recommendation covering the whole execution: ranked levers (solver residue, query-side stages 6–9, cache read, imports/startup), each assessed Rust vs pure-Python/numpy vs leave-it. The solver's extract-interface-first → PyO3 branch remains one candidate, judged under its measured end-to-end ceiling. Follow-on stories are NOT planned in this story or epic — new work routes through correct-course.
5. **No production code changes.** `src/steeproute/`, `tests/`, `pyproject.toml`, and regression goldens untouched (py-spy is already a dev dep); all four gates stay green; `git status` at close-out shows only planning-artifacts, `.benchmarks/`, and sprint-status changes.

## Tasks / Subtasks

- [x] Task 1: Fresh benchmark run at committed HEAD (AC: #3)
  - [x] `uv run pytest tests/benchmarks -m benchmark --benchmark-autosave` (no `--cov`) at HEAD `12c693a` — a clean committed-state number (0004 was captured with 12.3 still uncommitted); sanity-check it lands ≈ 0004 (median ~52.8 ms/1k iters)
  - [x] Tabulate the chain: 0001 (Epic 11 baseline) → 0002 (post-12.1) → 0003 (post-12.2) → 0004/0005 (post-12.3) — median and min per hop, cumulative vs 0001
- [x] Task 2: Re-profile Chamrousse quality-params workload (AC: #1)
  - [x] Fresh scratch `--cache-dir`; setup `--center 45.12,5.88 --radius 6.5` (live network, ~1 min)
  - [x] Capture (a), phase split: profile the query at realistic params (200k/stagnation 0) end-to-end — solver share vs imports, `cache.read_entry`, `operationalize_graph` (stages 6–7), `filter_trails`, `detect_climbs`/`contract_climbs` (8–9), validate/render
  - [x] Capture (b), solver-internal attribution: same but `--iter-budget 1000000` — see Dev Notes for the sample-count problem the 5.7× speedup creates; do NOT use this capture for the phase split (the inflated iter budget deliberately overweights the solver)
  - [x] Aggregate percentages from raw collapsed captures (11.2 method), not eyeballed SVGs
- [x] Task 3: Confirming capture on a larger area (AC: #2)
  - [x] Preferred: the radius-10 Chartreuse area already prepared in `./.trial-cache` (`--center 45.260,5.788 --radius 10`, cache hit `c5bbfb802f3dc22f`) — zero network wait, and it's the area where the flipped phase split was first observed; re-capture with provenance (exact command in Dev Notes). Alternative: Saint-Nizier (`--center 45.1556,5.6469`, setup radius 7.0 — not 6.5, DEM-edge `DEMCoverageError`; query `--radius 6.5`)
  - [x] Compare both the solver ranked shape and the phase split against Chamrousse; call out any hotspot whose share moves materially with graph size (smoothing and filter passes scale with the base graph; the solver scales with iterations)
- [x] Task 4: Write the findings doc (AC: #3, #4)
  - [x] New dated doc in `_bmad-output/planning-artifacts/research/` (11.2 precedent), provenance-stamped (commit, machine, params, sampling rate, per-capture sample counts)
  - [x] Content: post-optimization ranked bottleneck list + phase split (both areas) → benchmark reconciliation table with in/out-of-band verdict → closing whole-execution what-next section addressing the decision inputs in Dev Notes
- [x] Task 5: Doc sync + close-out (AC: #4, #5)
  - [x] Update `future-ideas.md` performance pointer: Phase 4 no longer "conditional on Epic 12's closing go/no-go" — record the decision and link the findings doc
  - [x] Verify `git status`: no `src/`/`tests/` changes; gates green (`ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `pytest --cov`)
  - [x] Update sprint-status: story → review/done per workflow; this is Epic 12's last story — when it reaches done, set `epic-12: done` (epic flip happens at story-done, post-review)

## Dev Notes

### This is a measurement-and-writeup story (11.2's twin)

The deliverable is a document. Repo changes are exactly: the findings doc + profiling artifacts, one `.benchmarks/` autosave, `future-ideas.md`, sprint-status. **Resist fixing anything the new profile reveals** — any residual optimization candidates go in the doc as findings, not commits. AC #5 is verified with `git status` at close-out, same as 11.2.

### Measured 2026-07-04: the phase split has flipped on large areas

A manual radius-10 run (Chartreuse, `--center 45.260,5.788`, warm cache, 200k iters, n=10) finished the solve in ~10 s but took **40 s wall-clock**. A py-spy capture of the identical command (6,177 samples) attributes the run:

| Where | ~Share | What it is |
|---|---|---|
| `solver.run` | ~32% | the Epic-12-optimized GRASP loop |
| stages 6–7 query-side — `graph_smooth_elevation` + `graph_deadband_elevation` + `compute_edge_metrics` | ~24% | Laplacian smoothing does ~`window²/6` ≈ 417 whole-graph passes at `--elevation-smoothing 50` |
| `filter_trails` re-run + `detect_climbs`/`contract_climbs` | ~13% | difficulty-cap/l-connector are query knobs, so stages 2-redux + 8–9 run per query |
| `cache.read_entry` | ~6% | deserialization (shapely `from_wkb`) of the radius-10 graph |
| imports + process startup | ~16–22% | interpreter + module tree + uv/console-script trampoline |

**This is designed behavior, not a regression** — the cache stores stages 1–5 keyed independent of query knobs ([query.py:243-332](src/steeproute/cli/query.py)); stages 6–9 re-run per query. It was invisible in 11.2 (solver 94%) because the solver was 5.7× slower and the profiled area (radius 6) ~3× smaller. The consequence for this story: **the "solver is ~94% of query wall-clock" premise no longer holds**, the findings doc must record the new phase split per area size, and the go/no-go must reason under Amdahl — on large areas a further solver speedup (PyO3) can no longer buy more than ~1.5× end-to-end. Raw capture + aggregation script from this observation are in the session scratchpad (`query44.collapsed`, `agg.py`); re-capture with committed provenance rather than reusing them. Any optimization of the query-side pipeline (smoothing iteration count, cache-boundary placement, import cost) is a *finding for the doc*, not work for this story — it routes through correct-course.

### The sample-count problem (main practical trap)

11.2's steady-state capture got 7,334 samples from 200k iterations over ~64 s at 100 Hz. Post-12.3 the same 200k iterations finish in ~11 s → ~1,100 samples — too few for stable percentages on sub-10% items. Fix by raising the workload and/or the rate, and document it in provenance:

- `--iter-budget 1000000 --stagnation-iters 0` ≈ 55 s of pure steady state (deterministic per seed, same code path — a longer sample of the same workload, not a different one), and/or
- `py-spy record --rate 250` (higher sampling; keep an eye on overhead — record the rate used).

Saint-Nizier runs ~5× slower per iteration, so its 200k-iter capture is likely long enough as-is — check the sample count before deciding.

### Profiling mechanics (rehearsed in 11.2 — reuse verbatim)

- Capture form: `uv run --no-sync py-spy record -o <name>.svg --subprocesses -- uv run --no-sync steeproute ...` — py-spy cannot introspect the uv trampoline directly; `--subprocesses` resolves it. Take a parallel `--format raw` capture; compute all percentages from collapsed stacks (exact), strip constant wrapper frames before committing.
- Query params (the gallery quality recipe): `--seed 42 --n 3 --difficulty-cap T4 --elevation-deadband 1 --j-max 0 --time-budget 36000` + the iter/stagnation settings above.
- `--time-budget` defaults to 600 s — always raise it so the iteration budget governs.
- Artifact naming: distinguish areas and vintage, e.g. `grasp-post12-chamrousse-1m.svg` / `grasp-post12-saintnizier-200k.svg` + matching `.collapsed`, committed next to the 11.2 artifacts in `research/profiling/`.

### Benchmark numbers already on record

| Autosave | State | median ms / 1k iters | vs 0001 |
|---|---|---|---|
| `0001_070debf*` | Epic 11 baseline (Story 11.3) | 300.9 | 1× |
| `0002_f2671d1*` | post-12.1 | ~123 | ~2.4× |
| `0003_b0e85dd*` | post-12.2 | 81.3 | ~3.7× |
| `0004_cdd284a*` | post-12.3 | 52.8 | **~5.7×** |

(Medians from story close-out records; the 0002 value is derived from 12.2's "~1.52× over post-12.1" — pull exact per-hop mean/median/min from the JSON files in `.benchmarks/Windows-CPython-3.13-64bit/` when building the reconciliation table.) The bench asserts `convergence_status == "budget-exhausted"` per round, so early-exit can't fake a speedup. Never run benchmarks under `--cov`.

Prima facie the 2.5–4× band is **exceeded** — but the AC wants that stated from the consolidated table, and the re-profile is what shows whether the remaining time has the shape the analysis predicted (loop-skeleton remainder after churn removal, RNG boundary gone from the native slice).

### What-next decision inputs (what the closing section must address)

*(Broadened 2026-07-04, per Yann: the goal is whole-execution wall-clock, not solver throughput — the decision space is the full run, and Rust is a candidate tool per lever, not a solver-only question.)* The recommendation must be explicit and evidence-based, addressing:

1. **Performance need:** cumulative measured speedup vs the 2.5–4× prediction, and the NFR1 margin — gallery queries converged in ~7–32 s *before* Epic 12, against a ~10-minute design target, so NFR1 leaves no wall-clock *need*; "shorter whole runs" is a stated quality-of-life goal, not a requirement. State plainly, per lever, what a fix would buy end-to-end.
2. **Remaining headroom shape, under Amdahl:** what the post-12.3 profile now indicts — is there still a concentrated extractable core (PyO3-shaped), or is the residue diffuse? And with the solver down to ~1/3 of wall-clock on large areas (see the flipped-phase-split section), state the *end-to-end* ceiling of any further solver-only work explicitly; if the doc identifies remaining worthwhile targets, they are now more likely query-side (stages 6–9, cache read, imports) than solver-side.
3. **Rust-vs-pure-Python fit, per lever:** the "rustworkx / numpy batching not indicated" verdicts were *solver-scoped* — re-assess tooling fit for each new lever from its measured shape. Notably, elevation smoothing is repeated array-shaped math (the opposite of the solver's object-churn profile), where numpy/scipy may be the natural fix; filter/contraction re-runs may be cache-boundary or don't-recompute questions rather than compute problems; imports/startup are not Rust-fixable at all. Don't pre-decide — let the captures rank it.
4. **Learning value as a separate, legitimate rationale:** the original research explicitly evaluates Rust on *both* wall-clock gain and learning value, and for this project learning value alone is a valid basis for choosing Rust on a lever where it's viable (project framing). Keep the two rationales separated: an honest "not needed for performance" can coexist with "chosen for learning" — the doc should not launder a learning-motivated choice as a performance-driven one. If any Rust work is recommended, note the research's precondition: the time-boxed cargo-behind-corporate-proxy spike comes before committing.

The output is a *recommendation* recorded in the doc, not new epic/story planning — follow-on work, if any, arrives via a future correct-course (the 11.2 → Epic 12 pattern).

### Previous story intelligence (12.3 close-out)

- Post-12.3 gate state: 842 default tests green in ~3:16 with `--cov`, whole-project basedpyright 0/0/0, grasp.py 100% coverage, all 10 goldens rebaked and green. Nothing in this story should move any of it.
- 12.3's batched-RNG scheme (`_next_uniform`, chunked `rng.random(1024)` buffer, `int(u * n)`) is what the new profile should show in place of the old ~13% `Generator.integers` native slice — expect near-zero native time overall.
- Setup-stage benchmarks were unchanged through Epic 12 (setup untouched) — no need to re-profile setup; the 11.2 setup table stands.
- uv build-flake recovery (fires after commits/pyproject edits): `uv sync --native-tls` once, then `uv run --no-sync`. Setup runs need live network; Overpass/WMS variance is service-side — budget a few minutes per cold setup.

### Out of scope (don't drift)

- Any production code change, however tempting the new flamegraph makes it (AC #5)
- Planning Phase-4 stories or touching `epics.md` — the decision routes through correct-course if it becomes work
- Setup-pipeline profiling or optimization (network-bound, settled in 11.2)
- Golden/benchmark-suite changes; `tests/benchmarks/` is the instrument, not the subject

### Project Structure Notes

- **New:** findings doc under `_bmad-output/planning-artifacts/research/` (dated, e.g. `steeproute-phase3-results-and-phase4-decision-2026-07-04.md`); flamegraph SVGs + `.collapsed` captures under `research/profiling/`; one `.benchmarks/` autosave (0005).
- **Modified:** `_bmad-output/planning-artifacts/future-ideas.md` (pointer update), `_bmad-output/implementation-artifacts/sprint-status.yaml`.
- **Untouched:** everything under `src/steeproute/` and `tests/`, `pyproject.toml`, `uv.lock`, regression goldens, `epics.md`.

### Testing standards summary

- No new tests — nothing testable changes. Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest --cov` all green and unaffected.
- Benchmarks standalone without `--cov` (coverage distorts timings); `tests/unit` and `tests/integration` in separate invocations if run explicitly (conftest import collision).

### References

- [Source: epics.md §Story 12.4 + §Epic 12 preamble](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; go/no-go mandate and designated Phase-4 branch
- [Source: research/steeproute-bottleneck-analysis-2026-07-03.md §Phase-3 recommendation + §Caveats](_bmad-output/planning-artifacts/research/steeproute-bottleneck-analysis-2026-07-03.md) — 2.5–4× prediction, single-area caveat, "confirming capture on a larger area" mandate, pre-optimization ranked list to diff against
- [Source: research/technical-steeproute-performance-tuning-research-2026-07-02.md §Implementation Roadmap Phase 4 + §Research Synthesis](_bmad-output/planning-artifacts/research/technical-steeproute-performance-tuning-research-2026-07-02.md) — Phase-4 conditionality, extract-interface-first → PyO3 path, learning-value evaluation basis, cargo-proxy spike precondition
- [Source: _bmad-output/implementation-artifacts/11-2-profile-solver-and-setup-pipeline-into-a-ranked-bottleneck-list.md](_bmad-output/implementation-artifacts/11-2-profile-solver-and-setup-pipeline-into-a-ranked-bottleneck-list.md) — the rehearsed py-spy recipe, Windows practicalities, raw-capture aggregation method
- [Source: _bmad-output/implementation-artifacts/12-3-batched-rng-draws-with-documented-golden-rebake.md §Dev Agent Record](_bmad-output/implementation-artifacts/12-3-batched-rng-draws-with-documented-golden-rebake.md) — 0004 numbers, cumulative ~5.7×, gate state
- [Source: docs/examples/README.md §Regions](docs/examples/README.md) — Chamrousse and Saint-Nizier coordinates/radii, the 7.0 km Saint-Nizier setup note, quality recipe
- [Source: src/steeproute/cli/query.py:238-336](src/steeproute/cli/query.py) — the query-side stage 6–9 re-run boundary (`operationalize_graph` → `filter_trails` → `detect_climbs` → `contract_climbs`) behind the flipped phase split; the design rationale is in the inline comments (Stories 6.1/6.3)
- [Source: .benchmarks/Windows-CPython-3.13-64bit/](.benchmarks/Windows-CPython-3.13-64bit) — autosaves 0001–0004 (exact per-hop numbers)
- [Source: sprint-change-proposal-2026-07-03-solver-optimization.md §3 Decisions + §5 Handoff](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-03-solver-optimization.md) — Phase 4 unplanned-and-conditional; 12.4 defined as the evidence-based decision point

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Benchmark run at committed HEAD `12c693a` (AC #3):** saved as `0005_12c693a*`; grasp 1k iterations mean 53.7 / median 53.9 / min 49.0 ms — confirms 0004 within noise. Chain (median ms/1k iters): 300.9 → 123.2 → 81.3 → 52.8 → 53.9 = **5.58× cumulative** (5.86× on min). Setup-stage benchmarks unchanged.

**Captures (py-spy 0.4.2, 100 Hz, `--subprocesses`; each raw capture has a same-seed SVG twin):**

```
Chamrousse setup (cold, r6.5, live network)        → 43.87 s, cache 7f8b193e66d9322c
A: cham r6.0 quality 200k, stagnation 0 (raw)      → 14.84 s, 2,067 samples (SVG twin: 19.92 s / 2,492)
B: same, --iter-budget 1000000 (raw)               → 67.08 s, 7,233 samples (SVG twin: 67.36 s / 7,284)
C: chartreuse r10 (.trial-cache, seed 44, n 10)    → 51.62 s, 6,334 samples (SVG twin: 49.65 s / 6,016)
Unprofiled wall references: cham 200k 12.19 s (11.2 measured 64.11 s); chartreuse 40.05 s (2026-07-04 manual run)
```

**Gates (all green):**

```
git status                      → no src/ or tests/ changes (planning artifacts + .benchmarks only)
ruff check src tests            → All checks passed!
ruff format --check src tests   → 104 files already formatted
basedpyright (whole project)    → 0 errors, 0 warnings, 0 notes
pytest --cov (default markers)  → 842 passed, 12 deselected in 3:28; grasp.py 100% cov
```

### Completion Notes List

**Benchmark reconciliation (Task 1, AC #3).** Cumulative solver throughput at committed HEAD: **5.58× median / 5.86× min** vs the Story 11.3 baseline — clearly **above the predicted 2.5–4× band**. Whole-query wall-clock on the 11.2 reference workload: 64.11 s → 12.19 s (5.3×).

**Re-profile (Tasks 2–3, AC #1–2).** Capture B (7,233 samples, statistics ≈ 11.2's) gives the post-optimization solver-internal shape: `_build_rcl` 42.3% cum (was 57.5%), tracker/distinctness 18.2%, θ-prefix 9.3%, RNG 5.0% (native time ≈ 0 — batching removed the old ~13% native slice). Residue is diffuse pure-Python loop work — no extractable sub-hotspot short of compiling the whole construction loop. Captures A and C nail the phase split: solver 57.5% of the run on Chamrousse r6 but only **30.5% on Chartreuse r10**, where query-side stages 6–7 (27.4%), cache read (11.0%), filter redux (7.3%), stages 8–9 (5.9%) and ~3–5 s of imports/startup dominate. Single-area caveat resolved with a twist: the solver-internal shape transfers across areas; the phase split does not — it flips with area size. One size-dependent solver item found: `_build_adjacency` (12.1's once-per-solve precompute) costs ~11% of solver time on the r10 graph.

**Findings doc + decision (Task 4, AC #3–4).** `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` delivers the reconciliation table, both profiles, the flipped phase split, and the broadened what-next: **PyO3 solver kernel no-go on performance need** (band exceeded, NFR1 margin ~15–50×, Amdahl ceiling ~1.4× end-to-end on large areas; stays the one Rust-shaped option on the separated learning-value rationale, cargo-proxy spike precondition intact). Next levers are all query-side and pure-Python/numpy-shaped, headlined by smoothing vectorization (stage 6–7 Laplacian ≈ 417 whole-graph passes, ~27% of a large-area run); rustworkx and in-solver numpy batching remain not indicated. No follow-on stories planned — correct-course is the route.

**Close-out (Task 5, AC #4–5).** `future-ideas.md` pointer updated with the decision; committed `.collapsed` captures normalized (import-machinery stacks aggregated to a token line, process frames stripped of machine-local paths — sizes 0.5–1 MB each, 11.2 precedent); zero production-code changes verified via `git status`; all four gates green. Epic-12 flips to done when this story passes review.

### File List

**New:**
- `_bmad-output/planning-artifacts/research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` — the findings + decision document.
- `_bmad-output/planning-artifacts/research/profiling/grasp-post12-chamrousse-200k.collapsed` / `.svg` — capture A (phase split, realistic params).
- `_bmad-output/planning-artifacts/research/profiling/grasp-post12-chamrousse-1m.collapsed` / `.svg` — capture B (solver-internal attribution).
- `_bmad-output/planning-artifacts/research/profiling/grasp-post12-chartreuse-200k.collapsed` / `.svg` — capture C (larger-area confirm).
- `.benchmarks/Windows-CPython-3.13-64bit/0005_12c693a764a31d25f7a62e4c91eae83395fe6d28_20260704_101321_uncommited-changes.json` — committed-HEAD benchmark autosave.

**Modified:**
- `_bmad-output/planning-artifacts/future-ideas.md` — Phase-4 pointer resolved with the 2026-07-04 decision.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.
- `_bmad-output/implementation-artifacts/12-4-re-profile-benchmark-reconciliation-and-phase-4-decision.md` — this story file.

**Untouched:** everything under `src/steeproute/` and `tests/`, `pyproject.toml`, `uv.lock`, regression goldens (AC #5).

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-04 | Yann (Claude Fable 5) | Story 12.4 implemented (measurement + decision, no production code): benchmark chain reconciled at committed HEAD (5.58× median / 5.86× min vs Epic 11 baseline — above the 2.5–4× band; whole-query 64.1 s → 12.2 s on the 11.2 workload); three py-spy captures (Chamrousse 200k phase split, Chamrousse 1M solver attribution, Chartreuse r10 larger-area confirm) committed under `research/profiling/`. Findings doc `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` records the flipped phase split (solver ~31% on large areas; query-side stages 6–9 + cache read + imports dominate) and the broadened decision: PyO3 solver kernel no-go on performance need (~1.4× end-to-end ceiling; learning-value option kept separate); next levers query-side, numpy-shaped, headlined by smoothing vectorization; follow-on via correct-course. `future-ideas.md` synced. All gates green (842 passed). |
