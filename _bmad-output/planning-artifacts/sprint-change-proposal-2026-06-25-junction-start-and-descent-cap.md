# Sprint Change Proposal — Junction-Start Constraint & Direction-Aware Descent Cap

**Author:** Yann (navigated with Dev agent)
**Date:** 2026-06-25
**Trigger:** Promotion of `future-ideas.md` items #1 and #2 into committed v1 scope (user instruction).
**Mode:** Batch
**Scope classification:** Moderate (two new opt-in features delivered as one epic; coordinated dev across pipeline / solver / oracle / validator / tests / docs). Not Major — no replan, no rollback, MVP hypothesis unchanged.
**Recommended path:** Direct Adjustment (additive new epic) — **not** rollback, **not** MVP reduction.

> Unlike the recent corrections (Epics 4/5/6/9, all *defect fixes against existing ACs*), this is **scope expansion**: two genuinely new features with new functional requirements, promoted from the post-v1 backlog into v1 ahead of final release polish (Story 8.5).

---

## Section 1 — Issue Summary

Two backlog ideas are being pulled forward into v1. Both add **opt-in, direction- or topology-aware constraints** that make the surfaced routes more practically useful, without changing default behavior.

**#1 — Start-at-junction constraint (future-ideas #1).** Today GRASP may seed a route walk at *any* graph node. The user wants an opt-in flag constraining the **start endpoint** of each returned route to a **road/trail junction** — a node where at least one incident edge is an admitted minor road (connector) and at least one is a trail. Rationale: junctions are where you'd realistically park or step off the road onto the trail, so a route *idea* that starts there is more actionable.

- The road-vs-trail distinction already exists in the data: Stage 2 admits a curated minor-road set as connectors (Epic 6, "roads as connectors"), and Stage 9 retains and tags every connector. A road/trail junction is therefore computable as a **node attribute** at contraction time.
- **Endpoint semantics (decided):** the constraint applies to the **start endpoint only**. Routes are open walks (two distinct endpoints); the far end is unconstrained.

**#2 — Direction-aware maximum-descent-slope cap (future-ideas #2).** A downhill trail above ~40% average slope is unpleasant; above ~50–60% it gets dangerous — yet the *same segment is fine going up*. The user wants an opt-in flag that forbids **descending** a segment whose average slope (over a configurable distance window, measured in the uphill direction) exceeds the threshold, while leaving that segment fully eligible as a **climb** (uphill).

- This is the substantive one: it introduces **directionality into feasibility**. The reuse model today (Epic 5) is purely **undirected** on the base segment. A descent cap means base segment X is traversable *downhill* only if its windowed slope ≤ cap; *uphill* is always allowed. Reuse-identity stays undirected (orthogonal); feasibility gains a directional layer.
- A super-edge (climb) traversed **in reverse** is a descent of the whole climb — which is by construction steep (≥ `min_climb_slope`) — so the cap, when set, is exactly what forbids descending steep climbs. The "distance window" mirrors climb detection's running-average machinery.

Both are **new capabilities, not corrections** — no existing route is wrong today; we are adding constraints a user can opt into. Both default **off**, so default-parameter output is byte-identical to current behavior.

---

## Section 2 — Impact Analysis

### Epic impact

- **One new epic — Epic 10 — Practical Route Constraints (Junction Start & Descent Cap)**, appended after Epic 9, executing **before Story 8.5** (Release Polish / CI-threshold tightening). No renumber — Epic 8 is in-progress with completed/`review` stories; renumbering it would be wrong (same reasoning the Epic 9 insertion used).
- **Both features in one epic, one story each** (per user instruction). They're independent opt-in flags; each story carries its feature end-to-end (impl + validation + new golden + doc sync). The descent cap (Story 10.2) is the heavier of the two.
- **Epic 8 (in-progress) — Story 8.5 sequencing.** 8.5 currently runs "after epic-9." It now runs **after Epic 10**. 8.5 should additionally pin the two **new** golden fixtures (flag-on) into its regression set. (8.5's coverage-threshold and Linux-job parts remain independent.)
- **Epics 3 & 7 (done) — touched, not invalidated.** Both features extend Epic 3's solver/validator surface and lean on Epic 7's graceful-degradation (FR12) messaging when an active constraint shrinks the feasible set below N.

### Sequencing decision (per user instruction: "before v1 closeout")

Insert **Epic 10 → Story 8.5**. Both features land in v1 ahead of the final polish/threshold story. No epic renumber. The only sprint-status sequencing artifact is updating the existing 8.5 note from "after epic-9" to "after epic-10."

### Artifact conflicts

| Artifact | Conflict | Update needed |
|---|---|---|
| **PRD** | No FR falsified. Both features are currently absent / Phase-2-adjacent. The CLI flag catalog and FR list don't cover them. | Add **FR31** (start-at-junction) and **FR32** (direction-aware descent cap). Add `--start-at-junction` and `--max-descent-slope` (+ descent window) to the Config Schema flag catalog. Remove the two items from the implicit Phase-2 backlog framing (they're now v1). |
| **Architecture** | No decision falsified. The contracted-graph (Cat 3 §9), solver construction (Cat 5), and edge-attribute contract (Cat 3) don't yet model junction nodes or direction-aware descent feasibility. | Add: junction-node annotation at Stage 9; a precomputed per-base-segment windowed-descent-slope metric in the edge-attribute contract; one-line solver notes (seed-node restriction; direction-aware descent feasibility in construction). Add the two flags to the architecture flag catalog and FR-coverage map. Detailed integration (windowed feasibility vs. atomic super-edges) is finalized at story/architecture granularity — not pre-designed here. |
| **Epics** | No epic covers either feature. | Add Epic 10 (2 stories); add FR31/FR32 rows to the FR Coverage Map; sequencing note (8.5 after Epic 10). |
| **Code** | `pipeline/graph.py` (junction annotation), `pipeline/climbs.py` or metrics (windowed-descent metric), `solver/grasp.py` (seed restriction + descent feasibility), `tests/integration/exhaustive_oracle.py` (mirror both constraints), `validator.py` (two new validated constraints). | See Section 4B. |
| **Tests** | New behaviors unpinned. | Per-feature: unit/property tests, a new metamorphic invariant for the descent cap, and **one new golden fixture per feature pinning the flag ON**. Existing default-param goldens stay **untouched**. See Section 4C. |
| **future-ideas.md** | Items #1 and #2 are being promoted out. | Remove #1 and #2; renumber remaining #3 → #1; leave a one-line "promoted to v1 (Epic 10), 2026-06-25" note. |
| **sprint-status.yaml** | No epic-10. | Add `epic-10` + 2 stories; update the 8.5 sequencing comment to "after epic-10." See Section 5. |

### Technical impact (algorithmic)

- **Opt-in, default-off → no default-output change.** Both flags default off (`--start-at-junction` absent; `--max-descent-slope` = None). With neither set, the solver/oracle/validator behave exactly as today. **Existing goldens do not rebake** — the non-regression contract holds untouched, per the regression-pinning philosophy. Each feature instead adds a **new** golden that pins its flag *on*.
- **Story 3.7 GRASP-vs-exhaustive baseline is unaffected.** The toy fixture used by the 3.7 ratio gate does not set the new flags, so the ratio is unchanged — unlike Epic 9's #10, which raised it. This means 8.5's threshold-tightening baseline does **not** move because of this epic; the "before 8.5" sequencing is for tidiness (8.5 pins the new goldens), not a shifted baseline.
- **#1 junction annotation (Story 10.1).** A node attribute computed at Stage 9: `is_road_trail_junction` = (∃ incident connector edge) ∧ (∃ incident trail super-edge/edge). When the flag is set, GRASP seeds construction only at such nodes; the oracle enumerates only walks starting there; the validator rejects (banners) any returned route whose start endpoint isn't a junction. Shrinks the feasible set → FR12 graceful degradation naturally applies.
- **#2 direction-aware descent feasibility (Story 10.2).** Precompute, per base segment, the **steepest windowed uphill-measured gradient** (the value that governs descent safety) as a new edge attribute in the metrics stage — parameter-independent, so it can live in the cached graph (a one-time `pipeline_content_hash` bump). At query time, construction/oracle/validator compare it against `--max-descent-slope` for any **descending** traversal; uphill traversal is unconstrained. Super-edges traversed in reverse are descents of a steep climb → governed by the cap. Reuse identity (FR5) stays undirected and is **orthogonal** to this directional feasibility layer — they compose, neither falsifies the other.
- **Determinism preserved (FR29).** Both features must hold byte-identical reproducibility — #1's seed-node selection and #2's feasibility checks must be deterministic (pinned ordering / explicit tie-breaks), same discipline as the existing order-sensitive sites.
- **No cache invalidation for #1; one-time re-prepare for #2** if the windowed-descent metric is added to the cached (stages 1–7) graph. If instead computed query-side (stages 8–9), no re-prepare. Dev/architecture picks the cheaper correct placement; either way it's parameter-independent so it need not re-prepare per query.

---

## Section 3 — Recommended Approach

**Direct Adjustment (additive).** Two new opt-in features, each promoted to a first-class FR + flag, delivered as **one epic with one story per feature**, sequenced before the final v1 polish story. No completed work is rolled back; the MVP hypothesis ("does effort-maximizing search surface useful route ideas?") is unchanged — these sharpen the *practicality* of the ideas surfaced.

- **Effort:** Story 10.1 (junction) Medium-small; Story 10.2 (descent cap) Medium-large (the direction-aware feasibility layer + oracle/validator mirroring + new metamorphic invariant is the bulk).
- **Risk:** Low–Medium. Main risks: (a) keeping both new constraints deterministic (FR29); (b) keeping GRASP and the oracle on **one** feasible set under each new constraint so the Story 3.7 comparison stays apples-to-apples; (c) for #2, resolving the windowed-descent-vs-atomic-super-edge granularity cleanly (the one genuine design question — deferred to the story/architecture, not pre-decided here).
- **Timeline:** Hobby project; one focused stretch across two stories. Within tolerance.

**Alternatives considered & rejected:**

- *Post-v1 increment (v1.1)* — my initial recommendation; **overridden by user** ("before v1 closeout"). The features land in v1 ahead of Story 8.5.
- *Promote to committed backlog only (FRs now, epic later)* — rejected by the same instruction; the user wants them built in this cycle.
- *Two separate epics (one per feature)* — initially proposed; **overridden by user** ("just 1 epic"). Folded into one epic with a story per feature, which keeps each feature independently regression-pinned while reducing epic ceremony.
- *Make either feature default-on* — rejected: default-on would change default output and force a golden rebake for no user benefit (these are opt-in practicality knobs, not corrections).

---

## Section 4 — Detailed Change Proposals

### A. Documentation (planning artifacts) — applied on approval

- **A1 — PRD: add FR31 + FR32** (outcome altitude):
  - **FR31:** User can constrain a returned route's **start endpoint** to a road/trail junction (a node incident to both an admitted road/connector and a trail), via an opt-in flag. Default off.
  - **FR32:** User can configure a **direction-aware maximum descent slope**: a route may traverse a segment in the **descending** direction only if its average slope, measured uphill over a configurable distance window, stays at or below the threshold; the same segment remains eligible as a **climb** (uphill). Default off.
- **A2 — PRD: Config Schema** — add `--start-at-junction` (flag, default off) and `--max-descent-slope` (float, default None/off) to the constraints flag table; note the descent window sub-parameter (exact flag naming / whether it shares the climb window — architecture-phase).
- **A3 — Architecture:** junction-node annotation at Stage 9; new per-base-segment windowed-descent-slope edge attribute (Cat 3 contract); one-line solver notes (Cat 5: seed-node restriction; direction-aware descent feasibility); add both flags to the architecture flag catalog; add FR31/FR32 to the FR-coverage map.
- **A4 — Epics:** new **Epic 10** section (story texts in Section 5); FR31/FR32 rows in the FR Coverage Map; sequencing note (8.5 after Epic 10).
- **A5 — sprint-status.yaml:** add `epic-10` (+2 stories); update the 8.5 comment to "after epic-10."
- **A6 — future-ideas.md:** remove items #1 and #2; renumber #3 → #1; add a one-line "promoted to v1 (Epic 10) on 2026-06-25" note.

### B. Code (handoff to dev — per story)

- **B1 — Junction annotation (`pipeline/graph.py`, Story 10.1).** At contraction, tag each node `is_road_trail_junction` from its incident connector/trail edges. Deterministic; no behavior change when the flag is off.
- **B2 — Start-constrained construction (`solver/grasp.py` + oracle, Story 10.1).** When `--start-at-junction` is set, restrict GRASP seed nodes and oracle walk-starts to junction nodes. Keep GRASP and oracle on one feasible set; preserve FR29 determinism.
- **B3 — Windowed-descent metric (metrics/pipeline, Story 10.2).** Precompute per base segment the steepest windowed uphill-measured gradient (parameter-independent). New edge attribute; placement (cached stages 1–7 vs. query-side 8–9) is a dev/architecture call.
- **B4 — Direction-aware descent feasibility (`solver/grasp.py` + oracle, Story 10.2).** During construction and oracle enumeration, forbid any **descending** traversal whose windowed metric exceeds `--max-descent-slope`; leave uphill unconstrained; handle super-edge-in-reverse as a descent. Deterministic; one shared feasible set.
- **B5 — Validator (`validator.py`, both stories).** Extend the validated-constraint set (FR26): start-endpoint-is-junction (when flag active) and no-segment-descended-above-cap (when flag active). Violations surface via the existing FR27 banner / FR28 exit-code path.

### C. Tests (handoff to dev)

**New goldens — additive, flag-on (existing default-param goldens untouched):**

- **Story 10.1:** one new golden fixture pinning `--start-at-junction` on, asserting every returned route starts at a junction.
- **Story 10.2:** one new golden fixture pinning `--max-descent-slope` on, asserting no returned route descends a segment above the cap while steep climbs remain eligible uphill.

**Per-feature unit/property/integration tests:**

- Story 10.1: junction-annotation unit tests; start-constrained construction test; validator rejection test; FR12 degradation message when the junction constraint shrinks the set below N.
- Story 10.2: windowed-descent metric unit/property tests; direction-aware feasibility test (steep segment descendable-as-climb-only); **new metamorphic invariant** — *relax `--max-descent-slope` → best objective monotone non-decreasing* (adds a 9th invariant to the Appendix A(b) suite); validator rejection + banner test.

**Revalidation (folded into each story's tail):**

- Confirm the 8 existing metamorphic invariants and the Story 3.7 GRASP-vs-exhaustive gate still pass (expected unchanged — flags off in those fixtures).
- Confirm existing default-param goldens (both tiers) still match **without** rebake — this is the non-regression proof.
- Update any unit tests that pinned "any node can start" / "no descent constraint" assumptions, if present.

### D. Human-review checkpoint (optional, recommended)

A `bmad-checkpoint-preview` on a real Grenoble area with each flag on — eyeballing that routes start at sensible road/trail junctions (#1) and that no returned route bombs down a cliff-steep descent while still climbing it (#2). Recommended once per feature (folded into each story's tail), since these are user-facing practicality changes whose value is best confirmed visually.

---

## Section 5 — Implementation Handoff

**Scope:** Moderate → Developer implementation; planning edits applied now.

**Decision:** one new epic — **Epic 10 — Practical Route Constraints (Junction Start & Descent Cap)** — appended after Epic 9, executing **before Story 8.5**. No renumber. One story per feature.

**Proposed stories:**

| # | Story | Feature | Class | Special treatment |
|---|---|---|---|---|
| 10.1 | Junction-start constraint | #1 | New feature (end-to-end) | Node annotation at Stage 9; GRASP + oracle seed restriction; validator + FR12 degradation; new flag-on golden; FR29 determinism |
| 10.2 | Direction-aware descent-slope cap | #2 | New feature (end-to-end) | Windowed per-segment metric; direction-aware feasibility in solver + oracle; super-edge-reverse handling; new metamorphic invariant; validator; new flag-on golden; FR29 |

**Story texts (for epics.md, outcome-altitude):**

> **### Story 10.1: Junction-start constraint (FR31)**
> As a user, I want an opt-in flag that forces a route's start endpoint to a road/trail junction, so the surfaced route idea begins where I'd realistically park or step onto the trail.
> **Given** the contracted graph already distinguishes connectors (roads) from trails but marks no node as a road/trail junction, and GRASP may seed a walk anywhere **When** I annotate junction nodes at contraction and, under `--start-at-junction`, restrict GRASP seeding and the exhaustive oracle's walk-starts to junction nodes — adding start-endpoint-is-junction to the validated constraint set (FR26/FR27/FR28), wiring FR12 messaging when the constraint limits results below N, and preserving FR29 determinism and one shared feasible set **Then** with the flag off, default output is byte-identical to today **And** with the flag on, every returned route starts at a junction (a new flag-on golden pins this) and the existing default-param goldens still match without rebake.

> **### Story 10.2: Direction-aware descent-slope cap (FR32)**
> As a user, I want an opt-in cap that refuses to descend a segment steeper than a threshold while still letting routes climb that segment, so returned routes don't bomb down dangerous grades.
> **Given** the edge-attribute contract has per-edge metrics but no descent-governing windowed slope, and the reuse model is undirected **When** I precompute a per-base-segment steepest windowed uphill-measured gradient, make GRASP construction and the exhaustive oracle reject any descending traversal exceeding `--max-descent-slope` (uphill unconstrained; super-edge-in-reverse treated as a descent), add no-segment-descended-above-cap to the validated set (FR26/FR27/FR28), and keep FR29 determinism with one shared feasible set **Then** with the flag off, output is byte-identical to today **And** with the flag on, no returned route descends an over-cap segment though it stays eligible as a climb (a new flag-on golden pins this), a new metamorphic invariant (relax the cap → objective non-decreasing) passes alongside the existing eight, and the existing default-param goldens still match without rebake.

> **Epic 10 closeout (folded into 10.2 tail):** re-validate the metamorphic suite and the Story 3.7 gate (expected unchanged — flags off in those fixtures), confirm both default-param golden tiers match without rebake, sync PRD/architecture/epics docs, and run an optional `bmad-checkpoint-preview` on a real Grenoble area with each flag on.

**Recommended order:** 10.1 → 10.2, then 8.5. Story 10.1 first (smaller, self-contained); Story 10.2 second (the direction-aware feasibility layer). 8.5 last, additionally pinning the two new flag-on goldens. (Hobby-project guidance, not a commitment.)

**Success criteria:**
- `--start-at-junction` off → byte-identical default output; on → every returned route starts at a road/trail junction, validated and degradation-aware.
- `--max-descent-slope` off → byte-identical default output; on → no returned route descends an over-cap segment, while steep climbs stay eligible uphill; new metamorphic invariant passes.
- GRASP and oracle stay on one shared feasible set under each new constraint; Story 3.7 ratio unchanged.
- Existing default-param goldens (both tiers) match **without** rebake; two new flag-on goldens added.
- PRD (FR31/FR32 + flags), architecture, epics, sprint-status, and future-ideas all reflect the two features and the 8.5-after-Epic-10 sequencing.

**Status of edits:** on approval, the planning-artifact edits (A1–A6) are **applied** as part of this course-correction (matching the Epic 4/5/6/9 precedent); the Section 4B/4C code + test changes are the dev-story handoff (Epic 10, Stories 10.1 → 10.2).

**Deferred to Story 8.5 (Release Polish):** pin the two new flag-on goldens into the regression set; tighten CI thresholds against the (unchanged) baseline.
