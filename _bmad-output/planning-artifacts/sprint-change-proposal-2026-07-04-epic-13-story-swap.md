# Sprint Change Proposal — Epic 13 Story Swap (Area-Scaling Focus)

> **NOT APPROVED / SUPERSEDED (2026-07-05).** Before sign-off, a separate session produced
> `research/steeproute-next-optimization-pass-handoff-2026-07-05.md` — a more thorough, better-measured
> (real r20 data) optimization plan that overlaps this proposal's replacement story (its Q2 is the same
> `compute_edge_metrics` fix) and explicitly wants that work batched with other pipeline-file changes to
> avoid a duplicate cache-content-hash/fixture-regen cycle. None of this proposal's edits were applied to
> `epics.md` or `sprint-status.yaml`. Epic 13 was closed out as-is (13.1–13.3 done, 13.4/13.5 deferred); the
> handoff plan is expected to be brought in via its own correct-course, in a separate session. This document
> is kept only as a record of the interim reasoning.

**Date:** 2026-07-04
**Trigger:** User review of Story 13.3's close-out; clarification of what performance work is actually valued
**Mode:** Batch
**Scope classification:** Minor (story-content swap within an existing, not-yet-started backlog slot; no PRD/architecture edits beyond what 13.3 already recorded)

---

## 1. Issue Summary

Reviewing Story 13.3's no-go decision, Yann corrected two premises the epic was built on:

1. **Repeat-identical-query caching (13.3's subject) has ~no real hit rate.** His actual workflow is explore an area → refocus on a smaller area → adjust a knob — not re-run the same command. This *reinforces* 13.3's no-go rather than reopening it (recorded in the story and in `architecture.md` §3b).
2. **Story 13.4 (lazy imports) targets the wrong thing entirely.** Its ~3–5 s import cost is constant per invocation — it does not grow with area. Yann is explicit: work that shaves seconds off *small* areas is not interesting; the only performance work worth doing is what **scales with area size**, since his interest is running steeproute on areas much larger than the r6/r10 fixtures used for testing/benchmarking.

During Story 13.3's investigation, two genuinely area-scaling costs were already measured and explicitly flagged as "next levers" but left unclaimed by any story:

- `compute_edge_metrics` (stage 7, per-edge Python loop) — ~3.3 s at Chartreuse r10, flagged out-of-scope in Story 13.1's close-out ("next query-side compute levers alongside Stories 13.2–13.4").
- The `filter_trails` difficulty-cap redux (stage 2 logic re-run query-side) — ~2.2 s at r10, measured in Story 13.3's Debug Log.

Both are O(edges) — cost grows with graph size, i.e. roughly with area. Both are the same shape of fix Story 13.1 already proved out (per-edge Python loop → numpy/array operations, same math, golden suite as the correctness net). This is a direct, low-risk swap: retire 13.4's content, replace it with a story that vectorizes these two loops.

## 2. Impact Analysis

- **Epic impact:** Epic 13's shape and remaining stories (13.1–13.3 done/review, 13.5 close-out) are unaffected. Only 13.4's content changes — it was still `backlog`, unstarted, so this is a content swap, not a rollback.
- **Story impact:** One story replaced. New Story 13.4: "Vectorize stage-7 edge-metric computation and the trail-filter redux." Old Story 13.4 (lazy imports) is dropped from the epic — no replacement record needed, it's simply not being done.
- **PRD:** No conflict, no edits — same as the epic's original scope (supports NFR1, no new FRs).
- **Architecture:** No new edits required by this proposal itself; the new story's own dev-story pass will touch whatever internal comments/tests reference `compute_edge_metrics`/`filter_trails`, same as any implementation story.
- **UX:** N/A.
- **Technical/secondary:** Regression goldens expected green; same contingent-rebake allowance as 13.1 (reordered float arithmetic from vectorization, Story 9.3/12.3/13.1 precedent) applies if triggered. No `future-ideas.md` changes.

## 3. Recommended Approach

**Direct Adjustment** — swap the content of the not-yet-started 13.4 slot. Effort: Low (planning) / Low-Medium (implementation — same proven pattern as 13.1). Risk: Low, same guardrails as 13.1 (golden suite, content-identity checks).

`graph.copy()` (~2.6–3.9 s, the purity-contract copy inside `operationalize_graph`) and `contract_climbs` (~2.6 s, graph-building rather than a flat per-edge loop) are also area-scaling but harder to vectorize the same way — left as residue for Story 13.5's what-next rather than folded into this story, keeping it scoped to the two proven-shape wins.

## 4. Detailed Change Proposals

### 4.1 `epics.md` — replace Story 13.4

```
OLD:
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

NEW:
### Story 13.4: Vectorize edge-metric computation and the trail-filter redux

As a user,
I want stage 7's per-edge metrics and the query-side difficulty-cap filter to run as array operations,
So that the part of query cost that actually grows with area gets faster — not the constant per-invocation
overhead.

**Acceptance Criteria:**

**Given** `compute_edge_metrics` (stage 7, `pipeline/climbs.py`) and the `filter_trails` redux
(`pipeline/osm.py:141-190`) are per-edge Python for-loops whose cost scales with edge count (~3.3 s / ~2.2 s
at the Chartreuse r10 reference workload, both flagged as next levers in Stories 13.1/13.3)
**When** both are reformulated as vectorized numpy/array operations — same per-edge formulas, same iteration
semantics, no behavior change
**Then** results are content-identical (same route JSONs) and the regression-golden suite passes untouched;
if reordered float arithmetic flips a golden edge-set, one documented rebake is acceptable (Story
9.3/12.3/13.1 precedent, equivalence argument recorded)
**And** measured stage-7 and trail-filter wall-clock on both reference workloads drops materially, recorded
in the close-out, with the saving framed as area-scaling (not a fixed offset) since that is what the
project's realistic use (larger-than-fixture areas) benefits from
**And** solver, validator, and output interfaces are unchanged
```

### 4.2 `epics.md` — Epic 13 overview paragraph update

```
OLD:
...13.3 is the recompute-avoidance cache-boundary design question,
deliberately sequenced after them because their outcome changes its cost-benefit; 13.4 is bounded startup
work; 13.5 closes with the measurement pattern Story 12.4 established.

NEW:
...13.3 is the recompute-avoidance cache-boundary design question,
deliberately sequenced after them because their outcome changes its cost-benefit (resolved as a no-go,
2026-07-04); 13.4 vectorizes the remaining area-scaling per-edge loops (stage-7 metrics + trail-filter
redux, swapped in for the originally-planned lazy-imports story via correct-course 2026-07-04 — constant
startup overhead was deprioritized in favor of work that scales with area); 13.5 closes with the
measurement pattern Story 12.4 established.
```

### 4.3 `sprint-status.yaml` — rename the 13.4 key

```yaml
OLD:
  13-4-lazy-imports-on-the-query-path: backlog

NEW:
  13-4-vectorize-edge-metrics-and-trail-filter-redux: backlog
```

## 5. Implementation Handoff

- **Scope:** Minor — content swap in an unstarted backlog story, routed directly to `create-story 13.4` → `dev-story` (no PO/architect involvement needed).
- **Next step:** `create-story 13.4` picks up the new content once this proposal is applied.
- **Success criteria:** same as 13.1's pattern — golden suite green (one contingent documented rebake allowed), measured before/after on both reference workloads, framed in terms of how the saving scales with area.
