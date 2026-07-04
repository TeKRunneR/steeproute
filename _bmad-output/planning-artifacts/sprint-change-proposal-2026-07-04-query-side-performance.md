# Sprint Change Proposal — Query-Side Performance (Whole-Execution Wall-Clock)

**Date:** 2026-07-04
**Trigger:** Epic 12 complete; Story 12.4's results-and-decision doc delivered the Phase-4 no-go and the ranked query-side lever list
**Mode:** Batch
**Scope classification:** Moderate (backlog addition — new epic, no existing-work changes)

---

## 1. Issue Summary

Epic 12 exceeded its own target — solver throughput landed at **5.6× the Epic 11 baseline** (predicted band: 2.5–4×), and the 11.2 reference query dropped 64.1 s → 12.2 s. In doing so it **flipped the phase split**: `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` shows that on the large-area reference workload (Chartreuse r10, 40.0 s) the solver is now only ~31% of wall-clock, while query-side work Epic 12 never touched dominates:

- Stages 6–7 (Laplacian elevation smoothing + deadband + edge metrics): **~27%** — ≈417 whole-graph smoothing passes at the 50 m default, per query.
- `filter_trails` redux + stages 8–9 (detect + contract): **~13%** — designed per-query recompute (the stage 1–5 cache is keyed independent of query knobs, Stories 6.1/6.3).
- Cache `read_entry` (per-edge WKB parse + incremental graph rebuild): **~11%**.
- Imports/process startup: **~3–5 s constant** (a third of a small-area run).

The decision doc resolves the Epic 12 closing question: **the designated Phase-4 branch (extract-interface-first → PyO3 solver kernel) is a no-go on performance grounds** — NFR1 margin is ~15–50×, and a perfect kernel is Amdahl-capped at ~1.4× end-to-end on the workload that motivates the whole-execution goal. The next levers are all query-side and all pure-Python/numpy-shaped. The sprint plan has no epic for that work; this proposal promotes the doc's ranked lever list into **Epic 13**.

Decisions recorded during this correct-course (Yann, 2026-07-04):

1. **Epic 13 takes all four query-side levers** plus a re-measure close-out (the 12.4 pattern) — not a leaner subset.
2. **The Rust/PyO3 kernel is not planned.** It stays in `future-ideas.md` as the one Rust-shaped option on learning value only; its precondition (time-boxed cargo-behind-corporate-proxy spike) is unchanged and any pickup routes through its own correct-course.

## 2. Impact Analysis

- **Epic impact:** Additive only. Epics 1–12 all `done` and untouched; backlog is empty (story 8-5 stays deferred, unaffected). New Epic 13 appended; no renumbering.
- **Story impact:** Five new stories (13.1–13.5); no existing story changes.
- **PRD:** No conflict, no edits. No new FRs — performance work on existing behavior. Supports NFR1 (this epic serves the broadened *whole-execution wall-clock* reading of the goal) and preserves NFR4 (seeded determinism).
- **Architecture:** No upfront edits, but two stories touch recorded decisions and carry doc-sync in their ACs: Story 13.2 changes the cache entry's on-disk format (Category 4c) and bumps the manifest schema version (Category 4b invalidation semantics — existing entries re-prepare once); Story 13.3 revisits Category 3b's premise that query-side stages are "fast enough to not need caching" and updates it if a second cache tier ships.
- **UX:** N/A (CLI-only).
- **Technical/secondary:** Regression goldens expected green throughout; Story 13.1 carries one *contingent* documented rebake (reordered float arithmetic from vectorization could flip a golden edge-set — Story 9.3/12.3 precedent) — the default expectation is untouched goldens. Measurement anchors are the decision doc's pinned reference workloads (Chamrousse r6 12.19 s / Chartreuse r10 40.05 s, fixed seeds/params) rather than the solver-scoped pytest-benchmark suite. `future-ideas.md` pointer updated.

## 3. Recommended Approach

**Direct Adjustment** — add Epic 13 within the existing plan. Effort: Low (planning) / Medium (implementation, ~5 stories). Risk: Low — 13.1/13.2/13.4 are behavior-preserving with the golden suite as guardrail; 13.3 is gated by its own design decision with an explicit "reasoned no" exit; 13.5 makes the what-next decision evidence-based. Rollback and MVP-review paths are not applicable: nothing shipped is wrong, and this is a post-v1 increment.

Story order is load-bearing: 13.1–13.2 are the two compute-shaped fixes and land first; 13.3's cost-benefit depends on what share *remains* after them, so it is deliberately sequenced third; 13.4 is bounded and independent; 13.5 measures the finished state. Plausible combined effect per the research doc: the 40 s large-area query into the ~20 s range without touching the solver.

## 4. Detailed Change Proposals

### 4.1 `epics.md` — append Epic 13 section (under "Active / future epics")

```markdown
## Epic 13: Query-Side Performance (Whole-Execution Wall-Clock)

Epic 12 exceeded its target (solver 5.6× vs the predicted 2.5–4×) and flipped the phase split: on the
large-area reference workload (Chartreuse r10, 40.0 s) the solver is now ~31% of wall-clock while query-side
work Epic 12 never touched dominates — stages 6–7 ~27% (headlined by ≈417 whole-graph Laplacian smoothing
passes per query), `filter_trails` redux + stages 8–9 ~13%, cache `read_entry` ~11%, imports/startup ~3–5 s
constant. The designated Phase-4 branch (PyO3 solver kernel) is declined on performance grounds
(Amdahl-capped ~1.4× end-to-end; it stays in `future-ideas.md` on learning value only). This epic works the
ranked query-side levers from `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md` — all
pure-Python/numpy-shaped; plausible combined effect ~40 s → ~20 s on the large-area workload. Stories
13.1–13.2 are compute-shaped fixes; 13.3 is the recompute-avoidance cache-boundary design question,
deliberately sequenced after them because their outcome changes its cost-benefit; 13.4 is bounded startup
work; 13.5 closes with the measurement pattern Story 12.4 established. Measurement anchors: the reference
workloads pinned in the Phase-3 results doc (Chamrousse r6 12.19 s / Chartreuse r10 40.05 s, fixed
seeds/params). Goldens expected green throughout; 13.1 carries one contingent documented rebake (float
reordering). Inserted via correct-course 2026-07-04; no epic renumber.

**FRs covered:** none new — performance work on existing behavior. Supports NFR1 (whole-execution
wall-clock on large areas) and preserves NFR4 (seeded determinism).

### Story 13.1: Vectorize query-side elevation smoothing (stage 6)

As a user,
I want the global Laplacian elevation smoothing to stop iterating whole-graph passes in Python,
So that the dominant query-side cost on large areas drops without changing route results.

**Acceptance Criteria:**

**Given** stage 6 currently runs ≈ round(window²/6) whole-graph Laplacian passes (~417 at the 50 m default)
as per-node Python iteration on every query
**When** the diffusion is reformulated as sparse-matrix/array operations — same math, same iteration count,
same smoothed profile
**Then** results are numerically equivalent and the regression-golden suite passes untouched; if reordered
float arithmetic flips any golden edge-set, the story instead carries one documented rebake via
`update-regression` with the equivalence argument recorded (Story 9.3/12.3 precedent)
**And** measured stage 6–7 wall-clock on the Chartreuse r10 reference workload drops materially (analysis
attributes ~27% of the 40 s run), recorded in the story close-out
**And** solver, validator, and output interfaces are unchanged

### Story 13.2: Faster cache-entry deserialization

As a user,
I want prepared-area cache entries to load without per-edge geometry parsing and incremental graph rebuild,
So that large-area queries stop paying ~11% of wall-clock before any work starts.

**Acceptance Criteria:**

**Given** `read_entry` currently parses per-edge WKB geometry and rebuilds the graph edge-by-edge
**When** entry storage moves to an array-based / prebuilt-graph format with a manifest schema-version bump
(existing entries re-prepare once, per the Category 4b invalidation semantics)
**Then** the loaded graph is content-identical (same nodes, edges, attributes) and the full suite including
regression goldens passes untouched
**And** measured `read_entry` time on the Chartreuse r10 entry drops materially, recorded in the close-out
**And** architecture Category 4c (on-disk format) is updated to record the new decision

### Story 13.3: Query-side recompute avoidance (second-tier cache decision)

As a user,
I want repeat queries to stop re-running unchanged pipeline work,
So that the `filter_trails` redux and stages 8–9 (~13% on large areas) stop being paid on every invocation.

**Acceptance Criteria:**

**Given** the stage 1–5 cache is keyed independent of query knobs by design (Stories 6.1/6.3), so
`filter_trails` redux and stages 6–9 re-run per query
**When** the cache-boundary options are weighed with the post-13.1/13.2 phase split as input (e.g. a light
second cache tier keyed on the query knobs, or moving the stage-2 redux setup-side) and the chosen option is
implemented — or the story records a reasoned decision *not* to, if the remaining share no longer justifies
the added cache complexity
**Then** repeat-query wall-clock on the reference workloads reflects the decision, results identical,
goldens untouched
**And** if a second cache tier ships: writes are atomic (Category 4d pattern), its key includes every input
affecting the cached stages, and architecture Category 3b is updated

### Story 13.4: Lazy imports on the query path

As a user,
I want the constant ~3–5 s import/startup cost cut down,
So that small-area queries stop spending up to a third of their wall-clock before doing anything.

**Acceptance Criteria:**

**Given** imports/process startup cost ~3–5 s per invocation regardless of query size
**When** heavyweight imports are deferred off the query path's startup sequence (lazy/function-local where
measurement supports it)
**Then** measured cold-start-to-first-output on the Chamrousse reference workload drops materially, behavior
unchanged, full suite green
**And** `--help`/`--version` and error paths stay fast (CLI smoke tests unaffected)

### Story 13.5: Re-measure and epic close-out

As a developer,
I want a post-epic profile and consolidated wall-clock comparison on both reference workloads,
So that the epic's effect on whole-execution wall-clock is recorded from measurements and the what-next
decision is evidence-based.

**Acceptance Criteria:**

**Given** Stories 13.1–13.4 have landed with per-story measurements
**When** I capture fresh py-spy profiles of both reference workloads (same seeds/params as the 2026-07-04
captures) and consolidate before/after wall-clock
**Then** a findings update in `_bmad-output/planning-artifacts/research/` records the new phase split and the
cumulative effect vs the 12.19 s / 40.05 s anchors, assessed against the plausible ~20 s large-area outcome
**And** the document closes with an explicit what-next recommendation (further work via correct-course, or
stop)
**And** no production code changes in this story
```

### 4.2 `epics.md` — NFR coverage line update

```
OLD:
- NFR1 (compute budget ≤10min design target): Epic 7 — time-budget termination, stagnation, progress reporting
  surfaces elapsed; Epic 11 makes the target measurable (benchmark baselines + per-stage timing); Epic 12 raises
  solver throughput against those baselines

NEW:
- NFR1 (compute budget ≤10min design target): Epic 7 — time-budget termination, stagnation, progress reporting
  surfaces elapsed; Epic 11 makes the target measurable (benchmark baselines + per-stage timing); Epic 12 raises
  solver throughput against those baselines; Epic 13 attacks the query-side share that dominates large-area
  whole-execution wall-clock post-Epic-12
```

### 4.3 `sprint-status.yaml` — append Epic 13 entries

```yaml
  epic-13: backlog        # Query-Side Performance — whole-execution wall-clock (correct-course 2026-07-04)
  13-1-vectorize-query-side-elevation-smoothing: backlog
  13-2-faster-cache-entry-deserialization: backlog
  13-3-query-side-recompute-avoidance-second-tier-cache-decision: backlog
  13-4-lazy-imports-on-the-query-path: backlog
  13-5-re-measure-and-epic-close-out: backlog
  epic-13-retrospective: optional
```

### 4.4 `future-ideas.md` — update the Performance tuning pointer

```
Append after the "Decided 2026-07-04" line:

**Promoted 2026-07-04:** the query-side levers promoted to Epic 13 via correct-course 2026-07-04
(`sprint-change-proposal-2026-07-04-query-side-performance.md`). The PyO3 solver kernel stays here as the one
Rust-shaped option on learning value only — not planned; precondition unchanged (time-boxed
cargo-behind-corporate-proxy spike before any commitment).
```

## 5. Implementation Handoff

- **Scope:** Moderate — backlog addition, no replan. Routed to Developer workflow.
- **Next step:** `create story 13.1` → `dev story` per the normal cadence; stories sequenced 13.1 → 13.2 → 13.3 → 13.4 → 13.5 (order is load-bearing: 13.3's design decision consumes the post-13.1/13.2 phase split, and 13.5 measures the finished state).
- **Success criteria:** goldens green throughout (one contingent, documented rebake allowed at 13.1); per-story measurements against the 2026-07-04 reference-workload anchors; architecture Categories 4c/3b synced where stories change them; 13.5's findings doc records the cumulative whole-execution effect and an explicit what-next.
