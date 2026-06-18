# Sprint Change Proposal — Route-Discovery Quality (Climb Maximality & θ-Prefix Recovery)

**Author:** Yann (navigated with Dev agent)
**Date:** 2026-06-18
**Trigger:** v1 general code review (2026-06-11, `tmp/review-findings.md`) findings #7 and #10, both reproduced this session (`tmp/repro_findings.py`)
**Mode:** Batch
**Scope classification:** Moderate (coordinated dev across pipeline / solver / oracle / tests / both golden tiers / docs + a one-line backlog sequencing note)
**Recommended path:** Direct Adjustment (no rollback, no MVP reduction)

> Companion to the already-merged review work on this branch lineage: finding **#6** (GRASP stagnation signal) is fixed (`90bc38f`) and the realistic-budget regression tier was added (`926e597`). This proposal covers the two remaining confirmed findings.

---

## Section 1 — Issue Summary

The v1 general review surfaced two **independent route-discovery quality gaps** in the solver/pipeline. Both are cases of *code not meeting its own intended behavior*, and both were missed by the automated suite (which, at the time, ran the solver only at unconverged low budgets — see the companion realistic-tier work). Each was reproduced on a minimal hand-built graph where the correct outcome is unambiguous by inspection.

**#7 — Climb detection drops steep chain-starts (non-maximal output).** `pipeline/climbs.py::detect_climbs` seeds candidates in sorted `(u, v, key)` order and only ever extends **forward** from the seed. If a mid-chain edge seeds before the chain's bottom edge, the forward walk consumes the mid + upstream edges and emits them as a climb; the genuinely-steep **bottom edge**, processed later, can no longer extend (its continuation is already consumed) and — if short — is dropped by the min-ground-length gate. The real climb's bottom silently becomes a connector.

- This **contradicts Story 3.2's own acceptance criterion** (epics.md:540): `detect_climbs` is specified to return *"maximal contiguous edge-sequences."* It does not — the output is maximal-forward-from-seed only, and depends on arbitrary OSM node-id labeling.
- Repro: one identical 3-edge steep chain (each 200 m / +100 m, slope 0.50) under two node labelings yields **different** output — bottom-edge-smallest-id captures all 3 edges; a labeling where a mid-edge sorts first drops the bottom edge. (`tmp/repro_findings.py`, REPRODUCED.)

**#10 — GRASP emits only maximal walks; θ-feasible prefixes discarded.** `solver/grasp.py::_construct_one` extends a walk until the RCL is empty (a maximal walk); the route-level slope floor θ is checked only on the *finished* maximal walk (`_route_slope_ok`). A steep prefix that clears θ but is then forced to append a flat-but-feasible tail ends below θ and is rejected. The exhaustive oracle, by contrast, emits **every** prefix (`exhaustive_oracle.py:169`), so it returns the steep-only route GRASP threw away.

- Consequence: on some graphs GRASP returns `[]` while a θ-feasible route demonstrably exists, and across the board GRASP's feasible set is a strict subset of the oracle's. This **systematically depresses the Story 3.7 GRASP-vs-exhaustive quality ratio** — the very metric Story 8.5 is slated to tighten the CI threshold against.
- Repro: steep edge `0→1` (avg 0.50, clears θ=0.20 alone) + forced flat connector `1→2` → GRASP returns `[]`; oracle returns `[0→1]`. (`tmp/repro_findings.py`, REPRODUCED.)
- The oracle docstring's "the oracle and GRASP enumerate the identical feasible set" claim (`exhaustive_oracle.py:24`) is currently **false**; #10's fix makes it true.

Both are **quality gaps, not constraint violations** — every route GRASP returns today is still valid; the defects are missing/under-discovered routes and a depressed quality ratio. Neither can crash or corrupt the cache.

---

## Section 2 — Impact Analysis

### Epic impact

- **Epic 3 (done)** — primary. #7 lives in Story 3.2 (climb detection); #10 in Story 3.6 (GRASP main loop). Both interact with Story 3.7 (GRASP-vs-exhaustive gate) and Story 3.8 (metamorphic suite); #10 also touches the Story 3.5 oracle's docstring.
- **Epic 8 (in-progress, Release Polish)** — two interactions:
  - **Goldens rebake.** Both fixes change route output, so **both** regression tiers must be regenerated: the fast goldens from Story 8.2 (currently `review`) *and* the realistic-budget tier added on this branch (`926e597`). This is the intended `update-regression` workflow, not a regression.
  - **Story 8.5 sequencing.** 8.5 "revisit[s] Story 3.7's `QUALITY_THRESHOLD = 0.80` against observed baseline" (epics.md:1009). #10 raises that baseline. **8.5 must run after Epic 9**, or it would tighten the threshold against an artificially-low ratio. (8.5's other two parts — coverage thresholds, Linux job — are independent of Epic 9.)

### Sequencing decision (per user instruction)

Insert as a **new Epic 9 — Route-Discovery Quality**, appended after Epic 8, executing **before Story 8.5**. No epic renumber is needed (Epic 8 is in-progress with completed stories — renumbering it would be wrong; the precedent's insert-ahead-and-renumber applied only when downstream epics were pure backlog). The only sequencing artifact is a one-line note that 8.5 follows Epic 9.

### Artifact conflicts

| Artifact | Conflict | Update needed |
|---|---|---|
| Epics | Story 3.2 AC claims climbs are "maximal" — currently false (#7). No epic claims GRASP enumerates the oracle's full feasible set (GRASP is framed as a heuristic — epics.md:993), so #10 falsifies no requirement. | Add new Epic 9 section (3 stories). Annotate Story 3.2 that maximality was a latent gap closed in Epic 9. Note the 3.7 baseline shifts up (feeds Story 8.5). Sequencing note: 8.5 after Epic 9. |
| PRD | None falsified. "GRASP finds a good route, not *the* route" (heuristic framing) stays accurate; #10 narrows the gap to the oracle. | No wording change. (Optional: nothing required.) |
| Architecture | None falsified. The 3.7 ratio gate (§11c) and "tighten to 0.85–0.90" target are unchanged in intent; #10 helps meet them. | No wording change. |
| Code | `pipeline/climbs.py` (#7), `solver/grasp.py` (#10), `tests/integration/exhaustive_oracle.py` (docstring claim becomes true). | See Section 4B. |
| Tests | climbs unit tests; grasp unit/integration tests; 3.7 quality gate; 3.8 metamorphic; 2 new fail-first regression tests; **both** golden tiers rebake. | See Section 4C. |
| sprint-status.yaml | New epic-9 + 3 stories; 8.5 sequencing note. | See Section 5. |

### Technical impact (algorithmic)

- **Contracted graph changes (#7).** Maximal climbs change which edges become super-edges and which stay connectors → the contracted graph the solver/oracle/validator all consume changes → **all** route output shifts. Larger, correctly-rooted climbs; fewer orphaned steep connectors. Blast radius is every downstream gate (goldens, metamorphic, 3.7).
- **GRASP feasible set grows toward the oracle (#10).** Recovering θ-clearing prefixes means GRASP no longer returns `[]` where a feasible route exists, and the 3.7 ratio rises (GRASP closer to the oracle's enumeration). Must stay deterministic (FR29) and keep GRASP/oracle on one feasible set so the 3.7 comparison stays apples-to-apples.
- **No cache invalidation.** Both fixes are query-time (stage 8 climb detection runs query-side; GRASP is the solver). `pipeline_content_hash` is unaffected; prepared caches are reused.
- **Determinism preserved.** Both fixes must hold FR29 byte-identical reproducibility — #7's seeding/extension order and #10's prefix-selection must be deterministic (pinned sort / explicit tie-break), same discipline as the existing two order-sensitive sites.

---

## Section 3 — Recommended Approach

**Direct Adjustment.** Both changes bring code in line with intended behavior — #7 makes `detect_climbs` finally satisfy its own "maximal" AC; #10 closes a systematic heuristic gap and makes the oracle/GRASP feasible-set claim true. No completed work needs rollback; MVP scope is unchanged.

- **Effort:** Medium. Two contained algorithmic fixes + one revalidation/rebake story.
- **Risk:** Low–Medium. Main risks: (a) keeping #7's new climb traversal deterministic and edge-disjoint (each base edge in ≤1 climb — the Story 3.3 back-mapping injectivity depends on it); (b) keeping GRASP/oracle on one feasible set under #10 so the 3.7 gate stays meaningful; (c) a clean, single golden rebake across both tiers with a clear rationale.
- **Timeline:** Hobby project; a focused stretch. Within tolerance.

**Alternatives considered & rejected:**

- *Defer to post-v1 / log-only* — rejected by user decision; #10 directly feeds the Story 8.5 threshold-tighten, so doing it before v1 closeout is the natural sequence.
- *Fold #7 + #10 into one story* — rejected: independent defects in different modules with different blast radii (#7 changes the contracted graph; #10 changes only solver output). Separate fix stories keep each reviewable and individually regression-pinned, matching the Epic 4/5 two-fix-stories shape.
- *Rollback / MVP review* — not applicable.

---

## Section 4 — Detailed Change Proposals

### A. Documentation (planning artifacts) — applied as part of this correction

- **A1 — Epics: new Epic 9 section** with the 3 stories below (full text in Section 5).
- **A2 — Epics: annotate Story 3.2** that the "maximal contiguous edge-sequences" guarantee had a latent forward-only/seed-order gap, closed in Story 9.1.
- **A3 — Epics: sequencing note** that Story 8.5 (GRASP-ratio threshold revisit) runs **after** Epic 9, against the post-#10 baseline.
- **A4 — sprint-status.yaml:** add `epic-9` + the 3 stories; add the 8.5-after-9 sequencing comment.

No PRD or architecture wording changes are required (Section 2 conflict table).

### B. Code (handoff to dev — per story)

- **B1 — `pipeline/climbs.py::detect_climbs` (#7).** Make emitted climbs genuinely maximal regardless of node-id labeling / seed order — e.g. extend **backward** from the seed as well as forward, or seed in descending-slope order so the steepest bottom roots the chain. Dev picks the approach. Must preserve: stage-8 determinism (FR29), edge-disjointness (each base edge in ≤1 climb), and the existing seed-qualification / running-average-slope / min-ground-length semantics.
- **B2 — `solver/grasp.py::_construct_one` / `run` (#10).** Stop discarding a θ-clearing route when a forced flat tail drags the maximal walk below θ — track the best θ-clearing prefix of each constructed walk and offer it to the tracker. Dev decides the exact prefix policy (best-objective θ-clearing prefix vs. all qualifying prefixes); must stay deterministic (FR29) and keep GRASP on the same feasible set the oracle enumerates.
- **B3 — `tests/integration/exhaustive_oracle.py` (#10).** Update the now-true docstring claim about GRASP and the oracle enumerating the identical feasible set (the oracle already emits prefixes; B2 brings GRASP in line). No logic change to the oracle.

### C. Tests (handoff to dev)

**Two fail-first regression tests (must fail on pre-fix code) — per project convention:**

- **9.1 / climb maximality (#7)** — relabel-isomorphism: the same steep chain under two node labelings produces identical maximal climbs, and a steep chain-bottom edge is always captured (never orphaned/dropped). Basis: `tmp/repro_findings.py`.
- **9.2 / θ-prefix recovery (#10)** — the steep-edge-plus-forced-flat-tail graph: GRASP returns the θ-clearing prefix the oracle returns (no false `[]`). Basis: `tmp/repro_findings.py`.

**Other test updates (Story 9.3 closeout):**

- Re-validate the 8 metamorphic invariants under the new climb detection + GRASP.
- Confirm the Story 3.7 GRASP-vs-exhaustive gate passes; the ratio is expected to **rise**. Do **not** tighten `QUALITY_THRESHOLD` here — that is Story 8.5's job, now correctly sequenced after this epic.
- Rebake **both** golden tiers — `uv run update-regression --all` (fast) and `uv run update-regression --all --tier realistic` — and commit with an explicit rationale (harness convention).
- Update existing climbs / grasp unit tests that pinned the old non-maximal / maximal-walk-only behavior, if any.

### D. Human-review checkpoint (optional, recommended)

A `bmad-checkpoint-preview` on a real Grenoble area after 9.1+9.2 land — to eyeball that climbs now root at their true bottoms and the returned route set improved — is recommended but **optional**: unlike the Epic 6 fixes (which chased one specific known-good route), these gaps are pinned by the two fail-first regression tests + the oracle comparison + the realistic-tier goldens, which are objective gates. Recommend folding it into Story 9.3 as a light final check rather than per-story checkpoints.

---

## Section 5 — Implementation Handoff

**Scope:** Moderate → Developer implementation; planning edits applied now.

**Decision:** new **Epic 9 — Route-Discovery Quality (Climb Maximality & θ-Prefix Recovery)**, after Epic 8, executing before Story 8.5. No renumber.

**Proposed stories (Epic 9):**

| # | Story | Finding | Class | Special treatment |
|---|---|---|---|---|
| 9.1 | Climb-detection maximality | #7 | Bug (code vs. its own AC) | Fail-first relabel-isomorphism regression test; changes the contracted graph → downstream rebake |
| 9.2 | GRASP θ-feasible prefix recovery | #10 | Bug (heuristic gap) | Fail-first regression (GRASP matches oracle on the prefix); raises the 3.7 ratio |
| 9.3 | Revalidation, golden rebake (both tiers), doc sync | #7 + #10 | Closeout | Metamorphic + 3.7 gate; rebake fast + realistic goldens with rationale; optional real-area checkpoint |

**Story texts (for epics.md, outcome-altitude):**

> **### Story 9.1: Climb-detection maximality (review finding #7)**
> As a user, I want every detected climb to be genuinely maximal — rooted at its true steep bottom regardless of OSM node-id labeling — so that no steep chain-start is silently demoted to a connector and routes can board climbs from the bottom.
> **Given** `detect_climbs` currently seeds in sorted `(u,v,key)` order and extends forward only, so a mid-chain seed orphans the upstream steep edge (contradicting Story 3.2's "maximal" AC) **When** I make detection capture the full maximal contiguous steep chain independent of seed order (backward extension or descending-slope seeding), preserving FR29 determinism and edge-disjointness (each base edge in ≤1 climb) **Then** a fail-first regression test asserts the same steep chain under two node labelings yields identical maximal climbs and the steep bottom edge is always captured **And** stage-8 unit/property tests, contraction tests, and the Story 3.3 back-mapping injectivity all hold.

> **### Story 9.2: GRASP θ-feasible prefix recovery (review finding #10)**
> As a user, I want GRASP to keep a θ-clearing route even when its greedy walk is forced to append a flat tail that drags the whole-walk average below θ, so the solver stops returning nothing (or fewer routes) where feasible routes demonstrably exist.
> **Given** `_construct_one` emits only the maximal walk and θ is checked only on the finished walk, so a feasible steep prefix is discarded when a flat tail follows **When** I track the best θ-clearing prefix of each constructed walk and offer it to the tracker, keeping FR29 determinism and one shared feasible set with the oracle **Then** a fail-first regression test asserts GRASP returns the θ-clearing prefix the exhaustive oracle returns on a steep-edge-plus-forced-flat-tail graph (no false empty result) **And** the Story 3.7 GRASP-vs-exhaustive ratio is unchanged or higher, with both sides on one feasible set, and the oracle docstring's identical-feasible-set claim is made accurate.

> **### Story 9.3: Revalidation, golden rebake, and doc sync (Epic 9 closeout)**
> As a developer, I want the route-output changes from 9.1+9.2 revalidated end-to-end and the regression baselines regenerated, so the suite reflects the corrected behavior and Story 8.5 can tighten the quality threshold against a trustworthy baseline.
> **Given** Stories 9.1 and 9.2 are complete **When** I re-validate the 8 metamorphic invariants and the Story 3.7 quality gate, rebake both golden tiers (`update-regression --all` and `--all --tier realistic`) with an explicit rationale, and sync docs (Story 3.2 maximality note; oracle docstring; any known-limitations wording) **Then** the full suite passes on Windows (default tier) and the realistic tier passes via `-m slow` **And** an optional `bmad-checkpoint-preview` on a real Grenoble area confirms climbs root at their true bottoms and the returned route set improved.

**Recommended order:** 9.1 → 9.2 → 9.3, then 8.5. 9.1 first because it changes the contracted graph (the substrate 9.2's GRASP runs on), so a single golden rebake in 9.3 covers both fixes. (Hobby-project guidance, not a commitment.)

**Success criteria:**
- `detect_climbs` returns maximal climbs independent of node-id labeling; no steep chain-bottom is dropped (9.1 regression test fails on pre-fix code, passes after).
- GRASP returns a θ-clearing route wherever one exists; no false `[]` on the repro class (9.2 regression test fails on pre-fix code, passes after).
- Story 3.7 ratio unchanged-or-higher with GRASP/oracle on one feasible set; both metamorphic and quality gates pass.
- Both golden tiers rebaked with rationale; full suite green (default + `-m slow`).
- Epics + sprint-status reflect Epic 9 and the 8.5-after-9 sequencing.

**Status of edits:** on approval, the planning-artifact edits (A1–A4) are **applied** as part of this course-correction (matching the Epic 4/5/6 precedent); the Section 4B/4C code + test changes are the dev-story handoff (Epic 9, Stories 9.1 → 9.3).

**Deferred to Story 8.5 (Release Polish):** tightening Story 3.7's `QUALITY_THRESHOLD` against the post-Epic-9 baseline (do not tighten inside Epic 9).
