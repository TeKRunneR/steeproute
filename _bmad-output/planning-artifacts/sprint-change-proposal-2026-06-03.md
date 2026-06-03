# Sprint Change Proposal — Route-Level Average-Slope Floor

**Author:** Yann (navigated with Dev agent)
**Date:** 2026-06-03
**Trigger story / area:** Epic 3 (Query Pipeline, Solver, Validation) — slope-floor semantics
**Mode:** Batch
**Scope classification:** Moderate (backlog reorganization + coordinated dev across solver / validation / tests / docs)
**Recommended path:** Direct Adjustment (no rollback, no MVP reduction)

---

## Section 1 — Issue Summary

The average-slope floor `θ` (`--theta`) is enforced **per-climb** in code, but the PRD, the architecture, and the original intent all specify a **route-level** floor.

**Corroboration (this is doc-vs-code drift, not a change of mind):**

- **PRD FR3**: *"User can configure the average-slope floor for eligible **routes**."*
- **PRD Executive Summary**: a route *"subject to a configurable average-slope floor (default ≥20%)."*
- **Architecture constraint table** (`architecture.md:514`): `| Slope floor ≥ θ | --theta | per-route |` — explicitly **per-route**.

**What the code actually does:**

- `pipeline/climbs.py` — θ is the climb-*detection* threshold (each climb's running-average uphill slope ≥ θ).
- `solver/grasp.py:209` (`_build_rcl`) — rejects a super-edge if its `avg_gradient < θ`.
- `validator.py:167` — slope-floor violation checked on **super-edges only** (docstring `validator.py:24` states this intentionally).

**Why it matters (sharper finding):** A climb is detected by `d_plus/length ≥ θ`, and a super-edge's `avg_gradient = (d_plus + d_minus)/length` is **always ≥** its uphill slope. So the solver/validator test `avg_gradient < θ` can essentially **never fire** — the per-climb "enforcement" is effectively a no-op. **Today there is no binding slope constraint on a route as a whole**: a route can chain steep climbs across kilometres of flat valley connectors, report healthy "20% climbs," and itself average ~6%. That is precisely the failure mode a route-level floor prevents, and it undercuts the "where the vertical lives / effort-maximization" premise.

**Secondary defect found:** `validator._route_metrics` computes `avg_gradient = d_plus/length` (uphill only), inconsistent with the per-edge `avg_gradient = (d_plus + d_minus)/length`. This value is what the HTML report displays (`output.py:101`). Fixed as part of this change to match the chosen route metric.

---

## Section 2 — Impact Analysis

### Resolution chosen (from navigation)

- **Split θ into two parameters:**
  - `--theta` (θ) → **route-level** average-slope floor. Metric: **`(D+ + D−) / total_length ≥ θ`**. Default `0.20` (preserves PRD's documented "≥20%").
  - `--min-climb-slope` (**new**) → per-climb detection threshold (running-average `d_plus/length`). Default `0.20` (preserves current climb-detection behaviour).
- The near-vacuous per-super-edge slope check is **removed** from solver and validator; the binding constraint becomes the route-level floor.

### Epic impact

- **Epic 3 (done)** — primary. Climb detection, GRASP construction/admission, validator, exhaustive oracle, GRASP-vs-exhaustive gate, metamorphic suite, report metric.
- **Epic 1 (done)** — CLI flag surface (`--min-climb-slope` added; `--theta` help reworded) + smoke/help tests.
- **Operational Robustness (now Epic 5, backlog)** — note only: a restrictive route floor can reduce feasible-route count; graceful degradation (FR12) currently addresses only J_max distinctness, not slope feasibility. Flagged for Epic 5 to absorb; no change required now.
- **Epic 2, Release Polish (now Epic 6)** — unaffected (stages 1–7 are parameter-independent; θ/min-climb-slope are query-time stage 8–9 inputs).

### Artifact conflicts

| Artifact | Conflict | Update needed |
|---|---|---|
| PRD | FR3 wording; missing FR for climb-detection threshold; Config Schema + defaults list | Reword FR3; add new FR; add `--min-climb-slope` row + default; clarify θ |
| Architecture | `:253` stage-8 lists θ for detection; `:258` CLI-split note; metadata param list `:614`; SolverParams "12 params" | Swap detection param to min-climb-slope; add it to metadata; confirm `:514` (already per-route — now correct) |
| Epics | FR Coverage Map; Stories 1.5, 3.2, 3.5–3.9 ACs | Reword affected ACs |
| Code | `models.py`, `cli/_shared.py`, `cli/query.py`, `pipeline/climbs.py`, `solver/grasp.py`, `validator.py`, `tests/integration/exhaustive_oracle.py` | See Section 4 |
| sprint-status.yaml | Done epics need a tracking home for the fix | New correction epic (Section 5) |

### Technical impact (algorithmic)

Route-level enforcement changes solver behaviour: flat/downhill connectors dilute the route average, so GRASP is pushed to minimise flat connectors and pack climbs — **more** aligned with effort-maximization, but a real behavioural change to a `done` solver. Route-level feasibility is a **global** property, so it is enforced at **solution finalization** (not greedily mid-walk): a partial walk may dip below θ and recover by adding a steep climb.

---

## Section 3 — Recommended Approach

**Direct Adjustment.** The architecture already intended per-route, so this is *bringing code in line with the spec* plus *adding one configurable parameter* — not a replan. No completed work needs rollback (we fix/extend, not revert). MVP scope is unchanged.

- **Effort:** Medium. Confined to Epic 3 internals + one CLI flag + doc sync + test re-validation.
- **Risk:** Low-Medium. Main risk is the route-floor default proving too strict in sparse areas (tuning, not correctness) and metamorphic-test interactions (Section 4).
- **Timeline:** Hobby-project; a focused weekend's work. Within tolerance.

**Alternatives considered:** *Replace* (single θ, route-level only) was rejected in favour of *split* because climb detection structurally needs its own threshold. *Rollback* and *MVP review* are not applicable.

---

## Section 4 — Detailed Change Proposals

### A. Documentation (planning artifacts)

**A1 — PRD FR3 (reword)**
```
OLD: FR3: User can configure the average-slope floor for eligible routes.
NEW: FR3: User can configure the route-level average-slope floor — the minimum
     ratio of total vertical change to total length, (D+ + D−)/length, that a
     returned route as a whole must meet.
```

**A2 — PRD: new FR for climb-detection threshold** (inserted after FR6; numbering at PM's discretion to avoid global renumber)
```
NEW: FR6b: User can configure the minimum running-average uphill slope for a
     trail segment to qualify as a climb (the climb-detection threshold,
     distinct from the route-level floor in FR3).
```

**A3 — PRD Config Schema → Constraints table**
```
OLD: | --theta | 0.20 | Average slope floor |
NEW: | --theta            | 0.20 | Route-level average-slope floor, (D+ + D−)/length |
     | --min-climb-slope  | 0.20 | Min running-average uphill slope to count as a climb |
```

**A4 — PRD Configurable parameters (defaults) list**
```
OLD: - `θ` (avg slope floor) = 0.20
NEW: - `θ` (route-level avg-slope floor, (D+ + D−)/length) = 0.20
     - `min_climb_slope` (climb-detection slope threshold) = 0.20
```

**A5 — Architecture `:253` (stage 8)**
```
OLD: | 8 | Climb detection (parameter-dependent: θ, min_climb_ground_length) | pipeline/climbs.py |
NEW: | 8 | Climb detection (parameter-dependent: min_climb_slope, min_climb_ground_length) | pipeline/climbs.py |
```

**A6 — Architecture `:258` (CLI-split note)** — replace "changing θ hits cache for stages 1–7, re-does 8–9 only" reference so that climb detection keys on `min_climb_slope`; θ is consumed at solve/validate time (route-level), not in stages 8–9.

**A7 — Architecture `:514` constraint table** — keep θ row (already `per-route`, now matches code); **add**:
```
NEW: | Climb-detection slope ≥ min_climb_slope | --min-climb-slope | per-climb (stage 8) |
```

**A8 — Architecture metadata param list (`:614`) + SolverParams "12 parameters"** — add `min_climb_slope`; count becomes 13.

**A9 — Epics FR Coverage Map** — split the FR3 row (Epic 1 flag / Epic 3 route-level enforcement) and add an FR6b row (Epic 1 flag / Epic 3 climb detection).

### B. Code (handoff to dev)

**B1 — `models.py` `SolverParams`** — add field `min_climb_slope: float` (after `theta`); update docstring (θ = route-level floor; new field = detection threshold); param count 12→13.

**B2 — `cli/_shared.py`** — add `min_climb_slope_option` (`--min-climb-slope`, `default=0.20`, `show_default=True`); reword `theta_option` help → *"Route-level average-slope floor, (D+ + D−)/length."*; add `--min-climb-slope` to `validate_solver_options` (finite + `>= 0`).

**B3 — `cli/query.py`** — stack the new option; thread `min_climb_slope` into `SolverParams` and into the `detect_climbs(...)` call.

**B4 — `pipeline/climbs.py`** — `detect_climbs(graph, theta, …)` → `detect_climbs(graph, min_climb_slope, …)` (rename the slope arg; logic unchanged — behaviour identical at default 0.20). Update docstrings referencing θ.

**B5 — `solver/grasp.py`** — **remove** the per-super-edge filter `if eid in super_edges and data["avg_gradient"] < theta: continue` from `_build_rcl`. **Add** a route-level feasibility gate at finalization in `run()`:
```
OLD: if solution.edges:
         self._tracker.consider(solution)
NEW: if solution.edges and self._route_slope_ok(solution):
         self._tracker.consider(solution)
```
where `_route_slope_ok` admits iff `(Σ d_plus + Σ d_minus) / Σ length ≥ params.theta`. (Edge-simple + SAC filters in RCL unchanged.)

**B6 — `validator.py`** — replace the per-super-edge `slope_floor` check (`:166-177`) with a **route-level** `slope_floor` `ConstraintViolation` when `route.metrics.avg_gradient < params.theta`; update module docstring `:24`. Fix `_route_metrics`: `avg_gradient = (d_plus_m + d_minus_m) / length_m` (was `d_plus_m / length_m`). Drop the `super_edge_to_base` dependency from the slope path.

**B7 — `tests/integration/exhaustive_oracle.py`** — replace the per-super-edge slope filter (`:143`) with the same route-level feasibility test on each enumerated path, so the oracle and GRASP share identical feasibility (keeps the 3.7 comparison valid).

### C. Tests (handoff to dev)

- **3.2 climb detection** — re-point tests to `min_climb_slope`; behaviour unchanged at 0.20.
- **3.9 validator** — new route-level slope cases: route below θ → violation; route at/above θ → pass; assert `avg_gradient` uses (D+ + D−)/length.
- **3.6 GRASP** — assert no admitted solution falls below the route-level θ; assert sub-θ candidates are discarded.
- **3.8 metamorphic — ACTION NEEDED:** the *"scale elevation by k → objective scales by k"* invariant now interacts with feasibility (scaling elevation by k scales route slope, changing which routes pass θ). The test must **scale θ and min_climb_slope by k** (or set both to 0) to hold. Re-validate all 8 invariants; "relax θ → objective non-decreasing" now binds meaningfully. Consider adding a min_climb_slope relaxation invariant.
- **1.5 / 1.7 CLI smoke + help** — `--help` must list `--min-climb-slope`; add a finiteness/`>=0` rejection case.

---

## Section 5 — Implementation Handoff

**Scope:** Moderate → Product-Owner-style backlog reorganization + Developer implementation.

**Decision (confirmed):** insert the correction as a **new Epic 4**, sequenced *ahead of* Operational Robustness so its graceful-degradation logic reasons about correct feasible-route counts. Former Epic 4 (Operational Robustness) → **Epic 5**; former Epic 5 (Release Polish) → **Epic 6**. Epic 4/5 stories were all `backlog` with no story files yet, so the renumber is text-only (sprint-status.yaml + epics.md) — no file renames.

**sprint-status.yaml (applied):**

```yaml
  epic-4: backlog        # Route-level slope-floor correction
  4-1-split-theta-introduce-min-climb-slope-and-route-level-semantics: backlog
  4-2-route-level-slope-enforcement-solver-oracle-validator-metric-fix: backlog
  4-3-revalidate-metamorphic-and-cli-tests-and-doc-sync: backlog
  epic-4-retrospective: optional
  # former epic-4 Operational Robustness -> epic-5; former epic-5 Release Polish -> epic-6
```

- **4-1** — params + CLI + climb-detection rename (B1–B4, A1–A4, A9 + C/1.5).
- **4-2** — solver gate, validator route-level check + metric fix, oracle alignment (B5–B7 + C/3.6, 3.9).
- **4-3** — metamorphic re-validation, CLI smoke/help, architecture/epics doc sync (C/3.8, 1.7, A5–A8).

**Success criteria:**
- `--theta` and `--min-climb-slope` both configurable; `--help` lists both; defaults 0.20/0.20.
- Every returned route satisfies `(D+ + D−)/length ≥ θ` by construction; validator confirms; HTML report shows the (D+ + D−)/length gradient.
- GRASP-vs-exhaustive and all 8 metamorphic invariants pass under the new semantics.
- PRD, architecture, and epics reflect the split.

**Status of doc edits:** PRD, architecture, epics.md, and sprint-status.yaml edits in Section 4A + Section 5 have been **applied** as part of this course-correction. The Section 4B/4C **code + test** changes are the dev-story handoff (Epic 4, Stories 4-1 → 4-3).

**Open tuning item (not a blocker):** route-floor default 0.20 with detection 0.20 may be strict in sparse areas; revisit empirically and consider routing slope-infeasibility through Epic 5 (Operational Robustness) graceful degradation (FR12).
