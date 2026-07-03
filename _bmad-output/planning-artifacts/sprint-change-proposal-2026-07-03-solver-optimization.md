# Sprint Change Proposal — Solver Performance Optimization (Phase 3)

**Date:** 2026-07-03
**Trigger:** Epic 11 complete; Story 11.2's bottleneck analysis delivered the measurement Phase 3 was gated on
**Mode:** Batch
**Scope classification:** Moderate (backlog addition — new epic, no existing-work changes)

---

## 1. Issue Summary

The performance-tuning roadmap (`research/technical-steeproute-performance-tuning-research-2026-07-02.md`) deliberately deferred planning Phase 3 (optimization work) until profiling existed: "Phase 3 scope is unknowable until the flamegraphs exist." Epic 11 shipped Phases 0–2 (instrumentation, profiling, benchmark baselines), and its decision deliverable — `research/steeproute-bottleneck-analysis-2026-07-03.md` — resolved the research's central question:

- The GRASP solver is **~94% of query wall-clock**; its cost is the **bespoke loop skeleton plus per-step object churn** — pure Python, not scoring math (numpy vectorized math: 0.0% of samples) and not networkx algorithms (only adjacency-view construction machinery; **rustworkx is not indicated**).
- Ranked hotspots: RCL construction 57.5% (views re-created ~1M+ times, `Edge` objects re-wrapped per visit, static sort repeated per step), scalar RNG boundary overhead ~13.3%, quadratic θ-prefix re-summing 10.6%, recomputed distinctness sets 7.1%.
- Estimated pure-Python headroom: **≈2.5–4×**. The setup pipeline is ~81% network wait and a low-value target.

The sprint plan has no epic for this work. This proposal promotes the analysis's Phase-3 recommendation into **Epic 12**.

## 2. Impact Analysis

- **Epic impact:** Additive only. Epic 11 complete and untouched; no backlog epics exist to resequence (story 8-5 remains deferred, unaffected). New Epic 12 appended; no renumbering.
- **Story impact:** Four new stories (12.1–12.4), sequenced per the analysis's ranked order; no existing story changes.
- **PRD:** No conflict, no edits. No new FRs — this is performance work on existing behavior. Supports NFR1 (widens margin under the ~10-min design target) and preserves NFR4 (seeded determinism; 12.3 changes the draw *sequence*, not the determinism contract).
- **Architecture:** No edits. All changes stay inside existing solver module boundaries. Story 12.1 incidentally delivers most of the extract-interface-first refactor Phase 4 would require.
- **UX:** N/A (CLI-only).
- **Technical/secondary:** Regression goldens stay green untouched through 12.1–12.2; 12.3 carries the epic's one documented golden rebake (Story 9.3 reconciliation precedent). Every story is judged against Epic 11's pinned benchmark baselines (`--benchmark-compare`). `future-ideas.md` pointer updated.

## 3. Recommended Approach

**Direct Adjustment** — add Epic 12 within the existing plan. Effort: Low (planning) / Medium (implementation, ~4 stories). Risk: Low — items 12.1–12.2 are behavior-identical with the golden suite as guardrail; 12.3's rebake follows a rehearsed workflow; 12.4 makes the Phase-4 decision evidence-based instead of speculative. Rollback and MVP-review paths are not applicable: nothing shipped is wrong, and this is a post-v1 increment.

Decisions recorded during this correct-course:

1. **RNG batching (item 4) is included** as the final optimization story (12.3) rather than deferred — capturing the full ~2.5–4× headroom at the cost of one documented rebake. (Yann, 2026-07-03)
2. Phase 4 (PyO3 kernel) remains **unplanned and conditional** — Epic 12 closes with a go/no-go recommendation (12.4), not Phase-4 stories.
3. Setup-pipeline optimization is explicitly out of scope (network-bound, once-per-area).

## 4. Detailed Change Proposals

### 4.1 `epics.md` — append Epic 12 section (after Epic 11)

```markdown
## Epic 12: Solver Performance Optimization (Phase 3 — Pure-Python Cheap Wins)

Execute the Phase-3 optimizations the bottleneck analysis indicts, in its ranked order. Profiling (Story 11.2)
resolved the research's decision question: the GRASP solver is ~94% of query wall-clock and its cost is the
bespoke loop skeleton plus per-step object churn — pure-Python data-structure waste, not scoring math (no
batchable dense math exists) and not networkx algorithms (rustworkx explicitly not indicated). Estimated
combined headroom ≈2.5–4×. Stories 12.1–12.2 are behavior-identical (same candidates, same order — regression
goldens stay green untouched); Story 12.3 batches RNG draws, which changes the seeded draw sequence and carries
the epic's one documented golden rebake (Story 9.3 reconciliation precedent). Every story is judged against the
Epic 11 benchmark baselines (`--benchmark-compare`); the epic closes with a fresh profile and an explicit
Phase-4 go/no-go (extract-interface-first → PyO3 kernel is the designated branch if the target is missed).
Setup-pipeline optimization stays out of scope — ~81% network wait, one-time cost per area. Promotes the
Phase-3 recommendation in `research/steeproute-bottleneck-analysis-2026-07-03.md`; inserted via correct-course
2026-07-03; no epic renumber.

**FRs covered:** none new — performance work on existing behavior. Supports NFR1 (widens the margin under the
~10-minute design target) and preserves NFR4 (seeded determinism holds under 12.3's new draw scheme, with
rebaked goldens).

### Story 12.1: Precompute static per-node adjacency for RCL construction

As a user,
I want the solver to stop rebuilding static graph data on every walk step,
So that queries run substantially faster with identical results.

**Acceptance Criteria:**

**Given** the contracted climb graph is immutable for the duration of a solve
**When** `run()` precomputes, once per solve, a per-node adjacency table of pre-built records (`Edge` object,
blocking frozenset, static sort order) and `_build_rcl` consumes it — no networkx view construction, no `Edge`
re-wrapping, no `blocking_ids` recomputation, no re-sorting per step
**Then** solver output is behavior-identical (same candidates in the same order for the same seed) and the full
regression-golden suite passes untouched
**And** the benchmark suite shows a material throughput gain over the pinned Story 11.3 baseline (analysis
attributes ~35–40% of the run to the eliminated work), recorded via `--benchmark-compare` in the story close-out
**And** solver public interfaces, validator, and exhaustive oracle are unchanged

### Story 12.2: Incremental θ-prefix metrics and cached distinctness sets

As a user,
I want prefix finalization and distinctness checks to stop recomputing unchanged values,
So that per-iteration overhead drops further with identical results.

**Acceptance Criteria:**

**Given** `_best_theta_prefix` currently re-sums the whole prefix per candidate (quadratic in walk length) and
`_canonical_edge_set` is recomputed per pairwise comparison
**When** prefix scanning maintains running `Σlength / ΣD+ / ΣD−` sums, with the canonical `route_avg_gradient`
retained as the final acceptance gate (admitted values stay bit-identical to the validator's, per the models.py
contract), and each held solution's canonical edge set is computed once at insertion
**Then** the regression-golden suite passes untouched
**And** the benchmark suite shows a throughput gain over the post-12.1 baseline consistent with the ~15%
combined attribution, recorded via `--benchmark-compare`

### Story 12.3: Batched RNG draws with documented golden rebake

As a user,
I want the per-step scalar RNG boundary overhead removed,
So that the last measured hotspot (~13% of the run) is captured.

**Acceptance Criteria:**

**Given** the hot path currently makes one scalar `Generator.integers` call per walk step — the profile's only
native time, all boundary overhead
**When** RNG draws are batched/chunked so per-step scalar calls disappear from the hot path, preserving the
determinism contract (same `--seed` + code + prepared data → identical output edge-sets, NFR4)
**Then** because the draw sequence changes, regression goldens are rebaked once via the documented
`update-regression` workflow with commit-message rationale (Story 9.3 precedent)
**And** the GRASP-vs-exhaustive quality gate and metamorphic invariants pass on the new outputs
**And** the benchmark gain over the post-12.2 baseline is recorded via `--benchmark-compare`

### Story 12.4: Re-profile, benchmark reconciliation, and Phase-4 decision

As a developer,
I want a post-optimization profile and a consolidated benchmark comparison against the Epic 11 baselines,
So that the Phase-4 decision (PyO3 kernel or stop) is made on measurements, not projections.

**Acceptance Criteria:**

**Given** Stories 12.1–12.3 have landed with per-story benchmark records
**When** I capture a fresh py-spy profile of the same quality-params workload, plus one confirming capture on a
larger area (the analysis's single-area caveat), and consolidate cumulative speedup vs the Story 11.3 baselines
**Then** a findings update in `_bmad-output/planning-artifacts/research/` records the new profile shape, the
cumulative speedup, and whether the measured result lands inside the predicted 2.5–4× band
**And** the document closes with an explicit Phase-4 go/no-go recommendation (extract-interface-first → PyO3
`steeproute-core` kernel is the designated branch; rustworkx and numpy batching remain not indicated) — Phase-4
stories are not planned in this epic
**And** no production code changes in this story
```

### 4.2 `epics.md` — NFR coverage line update

```
OLD:
- NFR1 (compute budget ≤10min design target): Epic 7 — time-budget termination, stagnation, progress reporting
  surfaces elapsed; Epic 11 makes the target measurable (benchmark baselines + per-stage timing)

NEW:
- NFR1 (compute budget ≤10min design target): Epic 7 — time-budget termination, stagnation, progress reporting
  surfaces elapsed; Epic 11 makes the target measurable (benchmark baselines + per-stage timing); Epic 12 raises
  solver throughput against those baselines
```

### 4.3 `sprint-status.yaml` — append Epic 12 entries

```yaml
  epic-12: backlog        # Solver Performance Optimization — Phase 3 cheap wins (correct-course 2026-07-03)
  12-1-precompute-static-per-node-adjacency-for-rcl-construction: backlog
  12-2-incremental-theta-prefix-metrics-and-cached-distinctness-sets: backlog
  12-3-batched-rng-draws-with-documented-golden-rebake: backlog
  12-4-re-profile-benchmark-reconciliation-and-phase-4-decision: backlog
  epic-12-retrospective: optional
```

### 4.4 `future-ideas.md` — update the Performance tuning pointer

```
Append after the "Researched 2026-07-02" line:

**Promoted 2026-07-03:** Phases 0–2 shipped as Epic 11; profiling verdict in
`research/steeproute-bottleneck-analysis-2026-07-03.md` (loop skeleton dominates; rustworkx not indicated).
Phase 3 promoted to Epic 12 via correct-course 2026-07-03. Phase 4 (extract-interface-first → PyO3 kernel)
remains conditional on Epic 12's closing go/no-go.
```

## 5. Implementation Handoff

- **Scope:** Moderate — backlog addition, no replan. Routed to Developer workflow.
- **Next step:** `create story 12.1` → `dev story` per the normal cadence; stories sequenced 12.1 → 12.2 → 12.3 → 12.4 (order is load-bearing: 12.3's rebake must land after the behavior-identical items so their golden-stability guarantee stays checkable, and 12.4 measures the finished state).
- **Success criteria:** goldens green untouched through 12.2; one documented rebake at 12.3; cumulative measured speedup reported against Story 11.3 baselines; Phase-4 go/no-go recorded at 12.4.
