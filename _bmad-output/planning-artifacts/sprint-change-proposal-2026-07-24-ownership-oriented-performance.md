# Sprint Change Proposal — Ownership-Oriented Performance Pass

**Date:** 2026-07-24
**Author:** Yann (via correct-course)
**Trigger input:** `research/steeproute-performance-review-gpt-5-6-2026-07-24.md` (reviewer GPT-5.6-sol, ref commit `4380970`)
**Mode:** Batch
**Scope classification:** Moderate (new epic + backlog reorg; no PRD FR changes)

---

## 1. Issue Summary

A fresh, measured end-to-end performance review of steeproute (setup + query, r20 reference workload:
center 45.260,5.788, 20 km half-side) identified that after Epics 11–14 the code is well optimized
*numerically* — the next tier of wins is **ownership of graph state**, not more NumPy math. The
review's own bottom line: don't copy a graph the caller has finished with; don't rebuild a graph
merely to strip two attributes; don't derive immutable graph-wide sets per route or per worker; don't
reconstruct cache data no query consumer reads; don't give every worker a private object graph when
they all read the same state.

This review is also the **resumption the parked Story 14.6 anticipated**. Epic 14's r50 probe was
deferred 2026-07-14 with the explicit note "revisit via correct-course when resumed"; this review
supplies the measured what-next evidence, anchored at r20 rather than the originally-imagined r50 run.

**Evidence (all measured on the review machine, WSL2 / 14-core Ultra 7, from the user's cached r20
data — no live Overpass/IGN calls):**

- **Query orchestration (strongest):** three changes produced **byte-identical** output — every one
  of 20 files (10 HTML + 10 JSON) SHA-256-matched — while cutting CLI-reported wall from **80.02 → 67.33 s
  (−15.9%)**, external process wall 90.82 → 78.62 s, and peak RSS **2.67 → 2.05 GB (−23.4%)**.
- **osmnx ingestion:** a behavior-equivalent single-traversal largest-component + in-place removal cut
  the two largest-component calls from 33.57 → 2.77 s and total warm ingestion **131.94 → 99.10 s
  (−24.9%)**, same 131,793 nodes / 327,911 edges.
- **Setup elevation wrapper:** ~7.6 s of measured components around the already-vectorized core (5.39 s
  graph copy avoidable on a consuming path; per-edge geometry extraction 3.76 → 1.51 s via bulk
  `shapely.get_coordinates`, bit-identical coordinates).
- **Setup smoothing/resampling:** ~7.4 s / ~7.8 s of profiled cost is building/flattening an
  intermediate NetworkX graph between the two stages.
- **Solver:** a combined pure-Python micro-POC cut single-process 100k-iter time **13.58 → 11.43 s
  (−15.8%)** with exactly-equal solutions; the dominant scaling limit is per-worker adjacency/object
  construction, addressed by the existing shared-memory-array design.

---

## 2. Impact Analysis

- **Epic impact:** additive — one new **Epic 16 (Ownership-Oriented Performance Pass)**. No renumber.
  No existing epic changes behavior. Closes the loop opened by the parked Story 14.6.
- **Story impact:** seven new backlog stories (16.1–16.7). No existing story is reopened or modified.
- **Artifact conflicts:** none new in the PRD (no FRs added or changed). Architecture Categories
  3 (pipeline), 4c (on-disk cache format), and 5a (solver) will be updated **by the landing stories**
  (16.2/16.3/16.4/16.6 carry the "architecture Cat X updated" AC), matching how Epics 13/14
  behavior-preserving work handled architecture edits — no pre-emptive architecture edit in this
  proposal.
- **Technical impact:** all work is behavior-preserving with bit-identity as the default guardrail.
  Cache-content-changing stories (16.2, 16.3) batch their regeneration to pay one invalidation event.
  `--workers 1` output stays byte-identical; any golden change is a single documented rebake, never
  silent (AGENTS.md golden policy). NFR4 (seeded determinism) preserved throughout.
- **Deferred-item interaction:** two of the three levers deferred to a post-probe correct-course on
  2026-07-06 are now **promoted** into Epic 16 because this review supplies their gating evidence —
  the schema-v3 geometry-optional cache contract (**"Q4"**, Story 16.3) and the shared-memory-array
  solver state (the structural fix, Story 16.6). Two levers **remain deferred** because this review
  does not cover them: the **S5-deep custom Overpass→graph parser** (the review's Batch C in-place
  adapter is explicitly *not* this) and **per-stage multiprocess pipeline parallelization**; both now
  scope from Epic 16's Story 16.7 residuals.

---

## 3. Recommended Approach

**Direct Adjustment** — insert Epic 16 as a new backlog epic; no rollback, no MVP-scope change.

Per the 2026-07-24 correct-course decision, the **full review is promoted** into one epic (rather than
the alternative "proven now, structural gated" split). Confidence is tiered and the stories say so:

| Story | Batch | Confidence | Guardrail |
|---|---|---|---|
| 16.1 query orchestration | A | **Proven** (real CLI, SHA-256 identical) | byte-identical output on exact r20 cmd |
| 16.2 setup owned-data + fusion | B | Measured components | bit-equal on `grenoble_small`; 1 batched regen |
| 16.3 geometry-optional / schema v3 (Q4) | B | Decision + measurement | content-identical for every live consumer |
| 16.4 osmnx in-place adapter | C | Measured warm ingestion | Story-14.5 exact old/new diff harness gate |
| 16.5 solver static ctx + loop wins | D | Measured micro-POC | exact `list[Solution]` equality |
| 16.6 shared-memory array solver | D | **Design only, unmeasured** | POC + bit-identity gate before default |
| 16.7 re-measure + what-next | — | Measurement only | no production code |

**Effort/risk:** low risk on 16.1 (proven, reversible, no cache impact); moderate on 16.2–16.4
(ownership refactors + one cache regen + a private-osmnx-API version guard); highest uncertainty on
16.6 (structural, still a design). Sequence 16.1 first for the highest-confidence win. Epic 16 is
independent of the same-day Epic 15 / App Epic 5 (rotated rectangles) — pick up in any order.

**Do not** mix in the review's named quality-altering non-optimizations (lowering `theta`; changing
`j_max`/difficulty/climb/descent/junction constraints; retaining disconnected OSM islands; dropping
buffered/second-truncation semantics; reducing iteration budget or changing RNG partitioning).

---

## 4. Detailed Change Proposals

### 4a. `epics.md` — new Epic 16 (full detail)

Added Epic 16 "Ownership-Oriented Performance Pass" with the tiered-confidence intro (thesis, r20
anchor, promoted/deferred items, guardrails) and Stories 16.1–16.7, each with an `As a … / I want … /
So that …` statement and outcome-altitude Given/When/Then/And acceptance criteria matching the
Epic 13/14 style. No new FRs.

### 4b. `epics.md` — NFR coverage map

- NFR1 line: appended Epic 16 as the "next tier — object-graph churn and repeated derivation of
  immutable state … strongest wins byte-identical".
- NFR2 line: appended Epic 16's query peak-RSS reduction (2.67 → 2.05 GB) and worker steady-memory
  reduction.

### 4c. `sprint-status.yaml`

- `last_updated` note updated to record epic-16 (kept the same-day epic-15 / app-epic-5 note).
- Added `epic-16: backlog` and stories `16-1 … 16-7` (all `backlog`) with a scoping comment (proven
  16.1, design-only 16.6, independent of epic-15).

### 4d. `future-ideas.md`

- Under "Performance tuning": added a "Promoted / re-scoped 2026-07-24" note pointing at Epic 16 and
  the review doc; annotated the 2026-07-06 deferred-items note to mark Q4 and the shared-array design
  as promoted, and to record that S5-deep and per-stage parallelization remain deferred (now scoped
  from Story 16.7 residuals).

---

## 5. Implementation Handoff

**Scope: Moderate** — backlog reorganization (new epic + stories), no fundamental replan.

- **Route to:** Developer agent via the normal BMAD flow — `create-story` for Story 16.1 first (the
  proven, byte-identical query orchestration batch), then `dev-story`.
- **Deliverables produced by this proposal:** Epic 16 in `epics.md`, backlog entries in
  `sprint-status.yaml`, promotion/deferral record in `future-ideas.md`, this proposal document.
- **Success criteria for implementation:**
  - 16.1 reproduces the ~80 → 67 s CLI shape with SHA-256-identical output on the exact r20 command;
  - cache-affecting stories (16.2, 16.3) land as one batched fixture regen with bit-equality verified
    on `grenoble_small` before old code is deleted;
  - 16.4 passes a Story-14.5-style exact old/new graph diff on the cached r20 response with a pinned
    osmnx version;
  - 16.5 holds exact `list[Solution]` equality across quality-gate seeds; the `_route_slope_ok(prefix)`
    gate is retained;
  - 16.6 is proven bit-identical before becoming any default; `--workers 1` stays byte-identical;
  - 16.7 records the consolidated before/after and the evidence-based recommendation on the still-
    deferred S5-deep parser and per-stage parallelization.
