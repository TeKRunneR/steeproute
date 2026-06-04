# Sprint Change Proposal — Undirected Segment-Reuse with Short-Connector Tolerance

**Author:** Yann (navigated with Dev agent)
**Date:** 2026-06-03
**Trigger area:** Epic 3 (Query Pipeline / Solver / Validation) — edge-reuse semantics
**Mode:** Batch
**Scope classification:** Moderate (backlog reorganization + coordinated dev across contraction / solver / oracle / validator / tests / docs)
**Recommended path:** Direct Adjustment (no rollback, no MVP reduction)

---

## Section 1 — Issue Summary

Real testing of the tool surfaces **degenerate routes**: the solver chains out-and-back excursions — out-and-back on one trail, then out-and-back on another — because each such excursion roughly doubles the `D+ + D−` objective (climb gains `D+` going up; the reverse descent gains `D−`), and nothing forbids re-walking the same physical trail in the opposite direction.

**Root cause (confirmed in code):** the edge-reuse constraint is **edge-simple on the *directed* triple** `(node_u, node_v, key)`, not on the underlying physical trail. The forward edge `(u,v)` and its reverse `(v,u)` are distinct identities, so an out-and-back is fully legal and — under a `D+ + D−` objective — globally attractive.

- `solver/grasp.py:181-189` — `used_ids` is a set of directed `(node_u, node_v, key)` triples.
- `validator.py:209-225` — `edge_reuse` constraint counts directed triples; "reuse limit 1" per direction.
- `tests/integration/exhaustive_oracle.py:139-167` — DFS dedups on directed triples; oracle and solver share the same directed-edge-simple feasible set.

**Desired behavior (user):** a physical trail segment may be used **only once, regardless of direction**, *except* short linking segments, which remain reusable (and bidirectional) so loops can still be stitched together. Using a climb uphill must forbid returning down the same trail — which kills the out-and-back pattern at the source.

**Corroboration — this is partly doc-vs-code drift, not purely a change of mind:**

- **Architecture constraint table** (`architecture.md:517`): `| Edge-reuse limit | --l-connector | per-route |` — the edge-reuse limit was always meant to be governed by `--l-connector`.
- **Epics FR Coverage** (`epics.md:161`): `FR5 (L_connector) | ... | Enforced by edge-reuse validator` — same intent.
- **PRD FR5**: *"User can configure the length threshold distinguishing short connectors from primary edges."* — i.e. short connectors were always meant to be a distinct class.

**What the code actually does instead:** `--l-connector` is realized purely as a **graph-pruning threshold at contraction** — connectors shorter than `l_connector` are *dropped from the contracted graph entirely* (`pipeline/graph.py:97`), and the runtime reuse constraint is a uniform directed-edge-simple re-check with **no connector exemption at all**. The validator docstring (`validator.py:33-39`) explicitly records this divergence: *"`l_connector` is the connector length threshold enforced at graph contraction … not a per-route reuse count."*

So the "tolerance to reuse short linking segments" the change request assumes **does not exist today** — short connectors aren't reusable, they're absent. Delivering the requested behavior means *realizing the originally-intended FR5 semantics* (l_connector = reuse-exemption threshold) **and** flipping the reuse key from directed to undirected.

**Why a directed→undirected flip alone is insufficient (sharper finding):** the descent half of a degenerate out-and-back is the *reverse* of a climb's trail, which survives contraction as ordinary connectors (≥ `l_connector`). Any rule that keys reuse on the directed edge, or that leaves long connectors freely reusable, fails to forbid that descent. The once-only rule must therefore key on the **underlying base trail segment**, shared between a climb super-edge and its reverse connectors, with the exemption applying specifically to **short** (sub-`l_connector`) connectors.

---

## Section 2 — Impact Analysis

### Resolution chosen (from navigation)

1. **Reuse key becomes undirected and base-segment-scoped.** Uniqueness is enforced on a stable **base-segment identity** that is the same for a trail and its reverse, and the same for a climb super-edge and the base edges it contracts. A route may include any given base segment **at most once**, in either direction.
2. **Short linking segments are exempt and revived.** Connector edges with `length_m < l_connector` are **kept in the contracted graph** (no longer dropped) and **exempt** from the once-only rule — they may be reused and traversed in both directions. This is the "linking tolerance," and it repurposes `--l-connector` from a *drop* threshold to a *reuse-exemption* threshold (the original FR5 intent).
3. **Solver, oracle, and validator share the new semantics** so GRASP output validates by construction and the Story 3.7 GRASP-vs-exhaustive quality gate stays apples-to-apples.

**Base-segment identity (contract, not mechanism):** every edge in the contracted graph carries a stable undirected base-segment identity and a `reusable` flag, computed once at contraction:
- a **connector** maps to its own undirected base-segment id; `reusable = (length_m < l_connector)`.
- a **super-edge (climb)** maps to the set of undirected base-segment ids of the edges it contracts (via `super_edge_to_base`); never reusable.
The exact id scheme (OSM way id vs. canonical sorted node-pair `+` key) is a dev/architecture choice — the binding requirement is that it is identical for a segment and its reverse. *Not pre-decided here.*

### Epic impact

- **Epic 3 (done)** — primary. Graph contraction (3.3), exhaustive oracle (3.5), GRASP construction (3.6), validator (3.9), GRASP-vs-exhaustive gate (3.7), metamorphic suite (3.8), and output rendering (3.10) all touch edge identity / reuse / connector handling.
- **Epic 1 (done)** — `--l-connector` help text reword only (semantics change, not flag surface); CLI smoke/help tests assert the help string.
- **Epic 5 (Operational Robustness, backlog)** — note only: an undirected once-only rule reduces feasible-route counts more aggressively than the current rule, so graceful-degradation messaging (FR12) will see fewer feasible routes in sparse areas. Same flag already raised for the slope floor; no change required now, absorbed by Operational Robustness when built.
- **Epic 6 (Release Polish, backlog)** — golden-regression harness (6.1/6.2) not yet built, so there are **no goldens to rebake**. The committed example reports under `results/` will change when regenerated, but they are outputs, not pinned fixtures.

### Sequencing decision (per user instruction)

The user wants this implemented **now**, ahead of Operational Robustness and Release Polish. Following the precedent set by the Epic 4 slope-floor correction (insert ahead, renumber to keep numeric order = execution order):

- **New Epic 5 — Undirected segment-reuse semantics** (this change), sequenced immediately after Epic 4.
- Former **Epic 5 (Operational Robustness)** → **Epic 6**.
- Former **Epic 6 (Release Polish)** → **Epic 7**.

Both former epics are pure backlog with no story files, so the renumber is text-only (sprint-status.yaml + epics.md) — no file renames. *(Alternative if churn is unwanted: keep the numbers and append this as Epic 7 sequenced first. Recommendation: renumber, for consistency with the Epic 4 precedent.)*

### Artifact conflicts

| Artifact | Conflict | Update needed |
|---|---|---|
| PRD | FR5 wording ("length threshold distinguishing connectors"); Config Schema `--l-connector` description | Reword FR5 to reuse-exemption semantics; reword `--l-connector` row |
| Architecture | `:254` stage-9 ("contraction" drops short connectors); `:517` constraint-table reuse-limit scope; `ContractedGraph` shape note | Stage-9 description (keep+tag, don't drop); confirm `:517` (now matches code); document base-segment id + `reusable` on edge-attribute contract |
| Epics | FR5 coverage row (`:161`); Stories 3.3, 3.5, 3.6, 3.9 ACs referencing "sub-`l_connector` connectors removed" / directed "edge-reuse limit" | Reword affected ACs to undirected base-segment + short-connector exemption |
| Code | `models.py`, `pipeline/graph.py`, `solver/grasp.py`, `validator.py`, `tests/integration/exhaustive_oracle.py`, possibly `output.py` | See Section 4B |
| Tests | contraction / grasp / validator / oracle / metamorphic / fixtures | See Section 4C |
| sprint-status.yaml | New epic + renumber | See Section 5 |

### Technical impact (algorithmic)

- **Feasible set shrinks.** Undirected once-only forbids every out-and-back over a non-exempt segment. GRASP is pushed toward genuine loops and point-to-point traverses that bank vertical without backtracking — the intended behavior.
- **Loop feasibility preserved by the exemption.** Short connectors stay reusable/bidirectional, so loops that must pass through a junction trail twice remain constructible.
- **No greedy-pruning hazard.** Like the slope floor, reuse is a path-local constraint that *can* be enforced during construction (an edge whose base-segment is already used is simply infeasible) — unlike the route-level slope floor, it does not need to defer to finalization. RCL filtering on base-segment-used is correct.
- **No setup-cache invalidation.** Stage 9 runs at query time (`architecture.md:258`); the prepared stages-1–7 cache is unaffected.

### Open items (non-blocking, for dev/architecture)

- **Jaccard distinctness identity.** Distinctness still keys on the directed `(u,v,key)` edge identity. Two routes that differ only in the direction they walk a shared segment would currently count as distinct. Default: leave Jaccard as-is; revisit only if direction-only-different routes leak into the top-N. *(Decision deferred.)*
- **Renderer with reused connectors.** A reusable short connector can now appear twice in a route; confirm `output.py` draws the polyline / elevation profile sanely on repeat traversal (likely fine — sequential profile, overdrawn map segment).

---

## Section 3 — Recommended Approach

**Direct Adjustment.** This is *bringing code in line with the originally-intended FR5 semantics* (l_connector as reuse-exemption threshold) **plus** one genuine behavior change (directed → undirected reuse key). No completed work needs rollback; MVP scope is unchanged.

- **Effort:** Medium. Confined to stage-9 contraction + solver/oracle/validator reuse logic + test/doc re-validation. The base-segment-identity plumbing is the one genuinely new piece.
- **Risk:** Low-Medium. Main risks: (a) the undirected rule proving too strict in sparse areas (tuning, routed through Operational Robustness later, not correctness); (b) getting the base-segment identity right so a climb and its reverse connectors actually collide; (c) keeping GRASP/oracle feasible sets identical for the 3.7 gate.
- **Timeline:** Hobby project; a focused weekend. Within tolerance.

**Alternatives considered:**
- *Undirected once-only on everything, no exemption* — rejected: makes many loops infeasible in sparse areas and contradicts the explicit tolerance ask.
- *Keep dropping short connectors; only tighten direction* — rejected: the degenerate descent uses long reverse connectors, so this does not actually kill out-and-back, and leaves no genuinely-short reusable links.
- *Rollback / MVP review* — not applicable.

---

## Section 4 — Detailed Change Proposals

### A. Documentation (planning artifacts)

**A1 — PRD FR5 (reword)**
```
OLD: FR5: User can configure the length threshold distinguishing short connectors
     from primary edges.
NEW: FR5: User can configure the short-connector length threshold below which a
     linking segment is exempt from the once-per-route reuse limit — short
     connectors may be reused and traversed in both directions; all other
     segments may be used at most once regardless of direction.
```

**A2 — PRD Config Schema → Constraints table (`--l-connector` row)**
```
OLD: | --l-connector | 200m | Edge-reuse length threshold |
NEW: | --l-connector | 200m | Short-connector reuse-exemption threshold; connectors
     shorter than this may be reused (bidirectional), all else is once-per-route undirected |
```

**A3 — Architecture `:254` (stage 9)** — reword "Climb-graph contraction" so it states: climbs collapse to super-edges; **all** connectors are retained (short ones no longer dropped) and tagged with a `reusable` flag (`length_m < l_connector`) and an undirected base-segment id; super-edges carry the base-segment id set of the edges they contract.

**A4 — Architecture `:517` constraint table** — keep the `| Edge-reuse limit | --l-connector | per-route |` row (now matches code); annotate scope as **undirected base-segment, short connectors exempt**.

**A5 — Architecture edge-attribute / `ContractedGraph` contract (`:260` / `models.py` docstring)** — document the new per-edge `base_segment_id` + `reusable` attributes and the super-edge → base-segment-id-set mapping; correct the `ContractedGraph` docstring line that says "sub-`l_connector` connectors dropped."

**A6 — Epics FR5 coverage (`:161`)** — reword to "Enforced by undirected base-segment reuse check (solver + validator); short connectors exempt." Reword Stories 3.3 / 3.5 / 3.6 / 3.9 ACs that reference "sub-`l_connector` connectors removed" or directed "edge-reuse limit."

### B. Code (handoff to dev)

**B1 — `pipeline/graph.py` `contract_climbs`** — stop dropping sub-`l_connector` connectors; carry **all** connectors over. Tag every contracted edge (connector and super-edge) with a stable undirected `base_segment_id` (connector: its own; super-edge: the set from `super_edge_to_base`) and a `reusable` boolean (`True` only for connectors with `length_m < l_connector`). Drop the orphan-prune-after-connector-drop step (no drop). *(Exact id scheme is the implementer's call per Section 2.)*

**B2 — `models.py`** — extend the edge-attribute contract / `ContractedGraph` docstring to cover `base_segment_id` + `reusable`; correct the `SolverParams.l_connector` docstring ("shorter connectors drop out at the contraction step" → "shorter connectors are reuse-exempt linking segments").

**B3 — `solver/grasp.py`** — replace the directed `used_ids: set[(node_u,node_v,key)]` with a set of **used base-segment ids**. In `_build_rcl`, an edge is infeasible iff any of its non-exempt base-segment ids is already used; on taking an edge, add its non-exempt base-segment ids to the used set. Reusable (short-connector) edges never block and are never recorded. Update the module docstring's "edge-simple" description.

**B4 — `validator.py`** — change the `edge_reuse` check from counting directed `(u,v,key)` triples to counting **non-exempt base-segment ids**: a violation iff a non-exempt base segment appears more than once. Exempt short connectors appearing multiple times must **not** be flagged. Update the docstring (`:33-39`) to describe the realized FR5 semantics.

**B5 — `tests/integration/exhaustive_oracle.py`** — change the DFS `used_ids` to the same base-segment-id set with short-connector exemption, mirroring B3, so the oracle and GRASP enumerate the identical feasible set (preserves the Story 3.7 gate).

**B6 — `output.py` (verify)** — confirm rendering handles a reusable connector traversed twice in one route (polyline + elevation profile). Adjust only if it currently assumes edge-uniqueness.

### C. Tests (handoff to dev)

- **3.3 contraction** (`test_graph_contraction*.py`) — the "sub-`l_connector` connector dropped" assertions **invert**: short connectors are now retained and tagged `reusable=True`; assert `base_segment_id` round-trips and that a climb's super-edge shares base-segment ids with its reverse connectors.
- **3.6 GRASP** (`test_grasp_construction.py`, `test_grasp_on_fixture.py`) — assert no route reuses a non-exempt base segment in either direction; assert a short connector *can* recur; assert the classic out-and-back-over-a-climb is now rejected.
- **3.9 validator** (`test_validator*.py`) — `edge_reuse` fires on undirected base-segment reuse; does **not** fire on repeated exempt short connectors; both-direction traversal of one climb's trail is a violation.
- **3.5 oracle** (`test_oracle_correctness.py`) — re-point the hand-graphs to the undirected/exempt feasibility; add a tiny graph where directed-edge-simple and undirected-base-segment give different optima (locks in the semantics).
- **3.7 gate** (`test_solver_on_toy_graph.py`) — re-validate GRASP/oracle parity under the new feasible set.
- **3.8 metamorphic — ACTION NEEDED:** re-validate all 8 invariants under undirected reuse. Most are orthogonal, but the *"add an edge → objective non-decreasing"* and *node-relabel isomorphism* invariants interact with base-segment identity (the identity must be relabel-invariant; adding an edge must not retro-block an existing segment). Consider a **new invariant**: *raising `l_connector` (more segments become reuse-exempt) → best objective non-decreasing.*
- **1.5 / 1.7 CLI** (`test_cli_help.py`, `test_cli_smoke.py`) — update the `--l-connector` help-string assertion to the reworded text.

---

## Section 5 — Implementation Handoff

**Scope:** Moderate → Product-Owner-style backlog reorganization + Developer implementation.

**Decision:** insert as **new Epic 5 — Undirected segment-reuse semantics**, sequenced immediately after Epic 4 and ahead of Operational Robustness. Former Epic 5 (Operational Robustness) → **Epic 6**; former Epic 6 (Release Polish) → **Epic 7**. Both are backlog with no story files → text-only renumber.

**Proposed stories (mirroring the Epic 4 three-story shape):**

- **5.1 — Base-segment identity + connector revival at contraction.** B1, B2, A3, A5; tests C/3.3. Foundation: `contract_climbs` keeps + tags all connectors with `base_segment_id` + `reusable`; super-edges expose their base-segment-id set.
- **5.2 — Undirected reuse enforcement (solver + oracle + validator).** B3, B4, B5, B6; tests C/3.6, 3.9, 3.5, 3.7. The behavior change; keeps GRASP/oracle parity.
- **5.3 — Metamorphic re-validation + doc sync + CLI help.** Tests C/3.8, 1.5/1.7; docs A1, A2, A4, A6. Re-validate the 8 invariants, add the `l_connector`-relaxation invariant, sync PRD/architecture/epics, reword `--l-connector` help.

**Success criteria:**
- No returned route uses a non-exempt base trail segment more than once, in any direction (validator confirms by construction).
- Short connectors (`length_m < l_connector`) remain reusable and bidirectional; loops stay constructible.
- The degenerate out-and-back-chain pattern no longer appears in solver output on the trial area that triggered this.
- GRASP-vs-exhaustive gate and all metamorphic invariants pass under the new semantics.
- PRD FR5, architecture (stage 9 + constraint table + edge contract), and epics reflect the realized semantics.

**Status of doc edits:** on approval, the planning-artifact edits (A1–A6 + sprint-status renumber + new epic) will be **applied** as part of this course-correction (matching the Epic 4 precedent); the Section 4B/4C **code + test** changes are the dev-story handoff (Epic 5, Stories 5.1 → 5.3).

**Open tuning item (not a blocker):** the undirected rule may prove strict in sparse areas; revisit empirically and route slope/reuse infeasibility through Operational Robustness (now Epic 6) graceful degradation (FR12).
