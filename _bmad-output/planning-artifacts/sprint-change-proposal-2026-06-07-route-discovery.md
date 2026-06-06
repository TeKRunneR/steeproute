# Sprint Change Proposal — Route-Discovery & Elevation-Consistency Fixes

**Author:** Yann (navigated with Dev agent)
**Date:** 2026-06-07
**Trigger:** Manual real-area testing — a known-good loop near Grenoble was never returned, plus elevation metric/display disagreement
**Source brief:** `correct-course-brief-2026-06-05-route-discovery.md` (prototyped & verified on `spike/junction-aware-climbs` and `spike/smoothing-consistency`)
**Mode:** Batch
**Scope classification:** Moderate (backlog reorganization + coordinated dev across pipeline / solver / distinctness / output / tests / docs)
**Recommended path:** Direct Adjustment (no rollback, no MVP reduction)

---

## Section 1 — Issue Summary

Real testing surfaced a route the user **knew should exist** (a loop combining specific steep trails near Grenoble) that the tool never returned, despite every constituent trail being present in the raw OSM data. Interactive diagnosis found **several independent defects stacked on top of each other** plus several quality gaps. Each was reproduced and a throwaway fix prototyped behind an opt-in flag; together they make the route constructible (verified). Separately, the elevation metric, the value the solver optimizes, and the plotted profile **disagreed by ~58–78 m**.

The fixes split into three classes:

**A. Correctness bugs** (found only by the user's manual testing — the automated suite passed throughout):

1. **Junction-aware climb splitting** (brief Item 1). Stage-9 contraction collapses each climb into a single atomic super-edge and deletes interior nodes. A trail that joins a climb *partway up* (at a real junction interior to the climb) can't board it — the solver can only enter/leave at the two endpoints. The target route needed exactly such a mid-climb turn. Root cause: `pipeline/graph.py::contract_climbs` emits one super-edge `node_u→…→node_v` per climb; interior nodes absorbed.
2. **SAC cap-aware contraction** (brief Item 2). At `--difficulty-cap t4`, a long mostly-easy climb containing even one T5 pitch is rejected *in full*. Root cause: `contract_climbs._aggregate_sac_scale` aggregates a climb's SAC to the **max** across its edges; the solver's RCL then rejects the whole super-edge. Observed: two T5 edges poisoned a 4.3 km, mostly-T2 climb.
3. **Undirected Jaccard distinctness** (brief Item 4 / priority #3). With `--j-max 0` (no overlap allowed), returned routes still share physical trail. Root cause: `solver/distinctness.py::_canonical_edge_set` keys on the **directed** `(node_u, node_v, key)`, while reuse (Epic 5) keys on the **undirected** `base_segment_id`. Two routes walking the same trail in opposite directions look fully distinct (Jaccard distance 1.000). This is the open item explicitly **deferred** in the Epic 5 proposal.
4. **Elevation-smoothing consistency** (brief Item 8, incl. the Item 5 deadband). The metric/box D+/D−, the value the solver selects on, and the plotted curve disagreed (~58–78 m, symmetric). The solver/box used a **per-edge** smoothing pinned to raw DEM at node boundaries; the display used a separate **continuous** whole-route smoothing. The deadband made it worse — it reshaped the metric at sum-time but never touched the displayed vertices.

**B. Feature gap:**

5. **Roads as connectors** (brief Item 3). Routes can't use short paved segments linking steep trails — roads are excluded at both the Overpass fetch filter and `TRAIL_HIGHWAY_TAGS`. The user's loop needed a short road link.

**C. Display readability** (display-only, no routing impact):

6. **Slope-display readability** (brief Item 6). The diverging color scale saturated at 30% grade (all steeper terrain looked identical), and the displayed slope is `rise/run` over a single ~10 m segment — it spikes to implausible values (58% on an 8.6% trail; extreme spikes where a route nearly doubles back over a sub-5 m end-segment).

**Why the automated tests missed the bugs (A1–A4) — and the user's standing requirement.** The existing suite exercises small synthetic/oracle fixtures and aggregate plausibility bands. The four bugs each only manifest on **real-area structural conditions** the synthetic fixtures didn't reproduce: junction-dominated topology (A1), real Alpine over-cap pitches inside easy climbs (A2), opposite-direction reuse of the same physical trail (A3), and the per-edge-vs-continuous smoothing split over real DEM (A4). **The user requires that each of these four fixes ship with a regression test that reproduces the exact structural condition and fails on the pre-fix code, plus an explicit human-review checkpoint after dev** so the user can confirm on the real trigger area that the bug is genuinely fixed. Both requirements are baked into the relevant stories below.

> **Note on brief Item 7 (solver termination flags).** Out of scope here by the user's direction — the misleading `--help` text, the inert `--time-budget`/`--stagnation-iters`, and the `--iter-budget` default all belong to **Operational Robustness** (the renumbered Epic 7, Story 7.2), which already owns time-budget + stagnation wiring. Captured as an impact note on that epic, not a story here.

---

## Section 2 — Impact Analysis

### Epic impact

- **Epic 3 (done)** — primary. Touches stage-9 contraction (3.3), climb detection inputs (3.2), Jaccard distinctness (3.4), GRASP RCL (3.6), validator (3.9), output rendering (3.10), and the metamorphic suite (3.8).
- **Epic 2 (done)** — the roads-as-connectors change is **setup-side** (stages 1–2: `osm.py` fetch filter + `filter_trails`). It changes `pipeline_content_hash`, so the stages-1–7 cache key **self-invalidates** — prepared areas re-fetch on next setup. Touches Stories 2.1 (OSM ingestion/trail filter) and its tests.
- **Epic 5 (done)** — the undirected-distinctness fix **closes the open item** the Epic 5 proposal deferred ("Jaccard distinctness identity … Decision deferred"), and single-sources distinctness off the same `base_segment_id` Epic 5 introduced.
- **Epic 4 (done)** — no direct change; the route-level slope floor (θ) interacts with the new canonical elevation profile (A4 reshapes the geometry θ is computed on) — covered by re-validating the metamorphic `scale_elevation` / `relax_theta` invariants.
- **Operational Robustness (was Epic 6 → now Epic 7, backlog)** — two notes: (a) brief Item 7 (termination + `--iter-budget` default + help text) lands here; (b) more, smaller super-edges from junction-splitting (A1) **enlarge GRASP's search space**, so the iter-budget/time-budget policy this epic decides should account for it.
- **Release Polish (was Epic 7 → now Epic 8, backlog)** — pinned regression fixtures (8.1/8.2) don't exist yet, so there are **no goldens to rebake**. The committed example outputs under `results/` will change on regeneration (they're outputs, not pinned fixtures). The new bug-regression tests are a natural feeder into the Epic 8 pinned-fixture set.

### Sequencing decision (per user instruction)

Implement **now**, ahead of Operational Robustness and Release Polish. Following the precedent of the Epic 4 and Epic 5 corrections (insert ahead, renumber so numeric order = execution order):

- **New Epic 6 — Route-Discovery & Elevation-Consistency Fixes** (this change).
- Former **Epic 6 (Operational Robustness)** → **Epic 7**.
- Former **Epic 7 (Release Polish)** → **Epic 8**.

Both downstream epics are pure backlog with no story files, so the renumber is **text-only** (sprint-status.yaml + epics.md headings, story numbers, and internal cross-references) — no file renames.

### Artifact conflicts

| Artifact | Conflict | Update needed |
|---|---|---|
| PRD | Config schema (new `--elevation-smoothing`, `--elevation-deadband` flags); data-sources note (roads admitted as connectors); FR11 distinctness wording (undirected) | Add two flag rows; add roads-as-connectors data note; align FR11/distinctness phrasing with undirected base-segment identity |
| Architecture | Stage 2 trail-filter (admit minor roads as connectors); stage 6 (per-edge median → global graph-Laplacian, query-side); stage-3b CLI split (smoothing/deadband move query-side); constraint table / distinctness note (undirected); edge-attribute contract (canonical profile) | Reword stages 2 & 6; note smoothing/deadband are query-side reshaping; annotate distinctness as undirected; document one-canonical-profile-per-edge |
| Epics | New Epic 6 section + 7 stories; renumber Epic 6→7 / 7→8 (headings, story numbers, internal refs); FR coverage map rows (FR11, roads, smoothing) | See Section 4A |
| Code | `pipeline/osm.py`, `pipeline/graph.py`, `pipeline/smoothing.py`, `cli/query.py`, `cli/_shared.py`, `solver/distinctness.py`, `solver/grasp.py`, `templates/route.html.j2`, `output.py` | See Section 4B |
| Tests | osm / contraction / distinctness / smoothing / climbs / metamorphic + 4 new bug-regression tests | See Section 4C |
| sprint-status.yaml | New epic + renumber | See Section 5 |

### Technical impact (algorithmic)

- **Search space grows (A1).** Splitting climbs at genuine junctions adds super-edges (prototype: contracted graph +5.7% edges, solve time ~flat). Couples to the Epic 7 budget policy.
- **Feasible terrain grows (A2, B5).** Pre-filtering above-cap edges before climb detection unlocks the easy majority of mixed-difficulty climbs; admitting minor roads adds connector edges. The `D++D−` objective self-limits road use to genuine links (roads are ~flat → no vertical to bank).
- **Distinctness tightens (A3).** Keying Jaccard on undirected base-segment identity makes opposite-direction reuse count as overlap — fewer spuriously-"distinct" routes survive top-N (correct behavior; interacts with FR12 graceful degradation, owned by Epic 7).
- **One canonical elevation profile (A4).** A single global graph-Laplacian smoothing (and the deadband as a profile transform) feeds box, solver objective, and plotted curve alike; box−curve gap → 0.000 m; no manufactured slope spikes. Query-side, so cache stays smoothing-independent.
- **Cache invalidation.** A1/A3 are query-time (stages 8–9 / solve), no invalidation. A4 (smoothing) **moves the cache boundary**: setup now caches raw post-stage-5 elevation and stages 6–7 (smoothing + metrics) move query-side, so `pipeline_content_hash` changes and existing caches re-prepare **once** when this ships; thereafter `--elevation-smoothing` is a free query knob (smoothing-independent cache). B5 (roads) is setup-time and likewise self-invalidates via `pipeline_content_hash`.

### Open tuning items (non-blocking, for dev/architecture)

- Split at *all* externally-connected junctions vs. only "routable" ones (skip dead-end stubs) — fragmentation vs. completeness (A1).
- Exact minor-road type set + multi-tag tie-breaking (a way tagged `["motorway","service"]` must not leak in) (B5).
- Smoothing strength semantics + the vertices→meters unit conversion (decouple from the 10 m resample spacing) (A4).
- Whether short road connectors are reuse-exempt like short trail connectors (recommend: yes, same `--l-connector` rule) (B5).

---

## Section 3 — Recommended Approach

**Direct Adjustment.** Every change is either a defect fix bringing code in line with intended behavior (A1–A4), a contained data-inclusion feature (B5), or display-only polish (C6). No completed work needs rollback; MVP scope is unchanged. All five substantive changes were **prototyped and verified** on spikes — risk is in clean re-implementation against the architecture and test conventions, not in feasibility.

- **Effort:** Medium. Seven stories; the elevation-profile overhaul (6.5) is the largest single piece.
- **Risk:** Low–Medium. Main risks: (a) junction-splitting fragmentation interacting with solver budget (routed to Epic 7); (b) getting the graph-Laplacian wiring right so box == curve (two per-edge approaches already failed — see brief Item 8, do not repeat); (c) keeping GRASP/oracle/validator on one feasible set so the Story 3.7 quality gate stays meaningful.
- **Timeline:** Hobby project; a focused stretch. Within tolerance.

**Alternatives considered & rejected** (from the brief's prototyping):

- *Per-edge "average neighbouring edges' context at each node"* — fails on the junction-dominated graph (a jump at every junction). Do not repeat.
- *Per-edge moving-average with pinned endpoint* — manufactures ~1000% slope spikes and can't smooth across 2-vertex edges. Do not repeat. (The global Laplacian fixes both.)
- *Bake SAC cap at setup* (cache key includes `difficulty_cap`) — viable but costlier; query-side keeps `--difficulty-cap` a fast knob (recommended).
- *Rollback / MVP review* — not applicable.

---

## Section 4 — Detailed Change Proposals

### A. Documentation (planning artifacts) — applied as part of this correction

**A1 — Epics: new Epic 6 section** with the 7 stories in Section 5, inserted ahead of Operational Robustness.

**A2 — Epics + sprint-status: renumber** former Epic 6 (Operational Robustness) → Epic 7, former Epic 7 (Release Polish) → Epic 8 — headings, story numbers (`6.x`→`7.x`, `7.x`→`8.x`), and internal cross-references (incl. fixing the stale "integrates with Story 5.4" ref in the run-summary story, which should point at the degradation story).

**A3 — Epics FR coverage map:** annotate FR11 (distinctness now undirected base-segment); add a roads-as-connectors note under the data-prep rows; note the canonical-elevation-profile change under the pipeline rows.

**A4 — PRD Config Schema:** add two Constraints/Solver rows —
```
| --elevation-smoothing | <default, in meters> | Strength of the global elevation smoothing (graph-Laplacian diffusion), in meters |
| --elevation-deadband  | 0 (off)             | Hysteresis floor (m): flattens sub-floor up/down reversals out of the elevation profile, reshaping which segments clear the slope thresholds |
```

**A5 — PRD data note:** under data sources / trail policy, add that a curated set of **minor road types is admitted as connectors** (not climbs; no SAC grade), letting routes cross short paved gaps between trails; the vertical-effort objective self-limits road use.

**A6 — Architecture:**
- Stage 2 (trail filter): admit a curated minor-road set as connectors with tightened multi-tag handling; roads bypass the SAC-cap / untagged policy (no SAC grade) and are never climbs.
- Stage 6 (elevation smoothing): replace the per-edge moving-median with a **global graph-Laplacian diffusion** over the whole vertex field (each graph node a single shared variable), applied **query-side**; the deadband is a **profile transform** on the same field. Box, solver objective, and plotted curve are all the naive up/down sum of this one canonical profile.
- Stage-3b CLI split: note elevation smoothing + deadband move to **query-side** (was setup-side stage 6), keeping the cache smoothing-independent.
- Constraint table / distinctness: annotate FR11 Jaccard as keyed on **undirected base-segment identity** (single-sourced via `solver.reuse`, same identity as the reuse rule).

### B. Code (handoff to dev — per story)

**Story 6.1 — Route-discovery bug fixes** (Items 1, 2, 4). Three independent defects diagnosed as the stacked causes of the one missing route:

- **B1 — `pipeline/graph.py::contract_climbs` (junction split).** Split a climb at any interior node incident (in the base graph) to a segment outside the climb (a real trail junction). New helpers `_split_climb_edges`, `_is_junction`. **Default on** (atomic climbs are the defect); a `--split-climbs-at-junctions/--no-...` toggle is optional for diagnostics. Preserve `base_segment_id` / `reusable` / `super_edge_to_base` tagging on the resulting (smaller) super-edges.
- **B2 — `cli/query.py` (SAC cap pre-filter).** Run `filter_trails(graph, untagged, difficulty_cap)` to drop above-cap edges **before** `detect_climbs`, so climbs never weld an over-cap pitch into otherwise-usable terrain. Decide deliberately whether to keep the solver's now-redundant per-edge RCL SAC filter as defense or remove it (recommend keep as cheap defense).
- **B3 — `solver/distinctness.py::_canonical_edge_set` (undirected distinctness).** Key on the **undirected** `base_segment_id` (the identity already on contracted edges from Epic 5) instead of the directed `(node_u, node_v, key)`. `jaccard_distance` and `TopNTracker` then see opposite-direction reuse as overlap. Single-source the identity via `solver.reuse` so GRASP/oracle/validator/distinctness all share it.

**Story 6.2 — Roads as connectors** (Item 3). **B4 — `pipeline/osm.py`.** Add a curated minor-road set (starting point: `residential, unclassified, service, living_street, tertiary`) to the Overpass fetch filter (`_OSM_CUSTOM_FILTER`) and admit them in `filter_trails` as connectors (bypass SAC cap / untagged policy). Tighten multi-tag handling so genuinely-major roads (e.g. a way also tagged `motorway`) don't leak in. Setup-side; `pipeline_content_hash` self-invalidates the cache.

**Story 6.3 — Unified elevation profile + display + closeout** (Items 8, 5, 6).

- **B5 — `pipeline/smoothing.py` + `cli/query.py` + `cli/_shared.py` (smoothing + deadband).** Add `graph_smooth_elevation` (global graph-Laplacian diffusion, Jacobi iterations, shared node variable) and `graph_deadband_elevation` (profile transform: flatten sub-floor reversals, keep turning points, interpolate between, endpoints pinned to the shared node value). Wire in `cli/query.py` query-side: smooth → deadband once over the whole graph, then `compute_edge_metrics` does a **naive sum** (both reshapings already in the geometry) → climbs → contraction → solver, and the **same** graph feeds `output.render(...)` with the render-side continuous pass disabled. Add `--elevation-smoothing` (meters) and `--elevation-deadband` (meters) flags. **Remove dead code:** the per-edge `median_smooth_elevation`/`mean_smooth_elevation` path and the render-side continuous-smoothing branch. Keep `compute_edge_metrics` a pure naive sum (do **not** add a `deadband_m` parameter — it would be a trap; the deadband lives in the profile now).
- **B6 — `templates/route.html.j2` + `output.py` (display).** Color: raise the diverging-scale clamp to `tan(30°) ≈ 0.58`. Slope baseline: compute the displayed slope over a **longer baseline** (±2–3 vertices ≈ 30–50 m) instead of a single ~10 m segment, which also tames the sub-5 m end-segment spikes. Add cumulative D+/D− to the profile hover (reaches the box totals at the final vertex — a one-glance consistency check). Display-only; no routing impact.

### C. Tests (handoff to dev)

**The four bug-regression tests (must fail on pre-fix code):**

- **6.1 / junction split** — fixture where a side trail joins a climb at an interior junction; assert the climb is split there and a route making the mid-climb turn is constructible. (Old atomic-climb code absorbs the interior node → fails.)
- **6.1 / SAC cap** — a climb with one above-cap pitch flanked by under-cap terrain; assert that at a cap below the pitch, no above-cap super-edge survives and the under-cap terrain stays routable. (Old max-SAC aggregation poisons the whole climb → fails.)
- **6.1 / distinctness** — two routes traversing the same base segment in opposite directions; assert `jaccard_distance < 1` (overlap detected) and that they're rejected under `--j-max 0`. (Old directed key → distance 1.000 → fails.)
- **6.3 / smoothing** — over a route: assert box D+/D− equals the plotted-curve cumulative at the final vertex (gap ≤ tolerance), and that max per-segment `|ΔElev|` never exceeds the raw-DEM maximum (no manufactured spikes). (Old per-edge-vs-continuous split → ~58–78 m gap → fails.)

These use small, **topology-specific** fixtures that reproduce the exact structural condition the synthetic suite missed; where cheap, add a pinned real-area assertion (feeds the Epic 8 regression-fixture set).

**Other test updates:**
- **6.1** — contraction unit tests stay green at default; add junction-split behavior tests.
- **6.2** — the two `test_osm.py` tests asserting roads are dropped **invert** to the new contract (B4).
- **6.3 (closeout)** — re-validate the 8 metamorphic invariants under the new contraction/distinctness/smoothing (esp. `scale_elevation`, `relax_theta`, node-relabel isomorphism — the canonical profile and base-segment identity must stay relabel-invariant); CLI smoke/help tests assert the two new flags appear; full suite green on Windows.

### D. Human-review checkpoints (user requirement)

**Stories 6.1 and 6.3** each carry an explicit post-dev **human-review checkpoint** (`bmad-checkpoint-preview`) as the final acceptance gate, run on the real trigger area (the loop near Grenoble, seed 44, T4 — repro command in the brief) after the regression tests are green:

- **6.1 checkpoint** confirms all three fixes on the real run: the target loop is now constructible (junction split + SAC cap) and opposite-direction trail reuse is correctly treated as overlap under `--j-max 0` (distinctness).
- **6.3 checkpoint** confirms box D+/D− matches the plotted curve, no manufactured slope spikes, genuine steep terrain preserved, and the color/baseline display reads correctly.

Story **6.2** (roads, a feature) uses standard review. Folding the four bug fixes into two checkpoint sessions keeps the verification you asked for while cutting story/checkpoint overhead.

---

## Section 5 — Implementation Handoff

**Scope:** Moderate → Product-Owner-style backlog reorganization + Developer implementation.

**Decision:** insert as **new Epic 6 — Route-Discovery & Elevation-Consistency Fixes**, ahead of Operational Robustness (→ Epic 7) and Release Polish (→ Epic 8). Text-only renumber.

**Proposed stories (Epic 6) — consolidated to 3 to cut story/token overhead:**

| # | Story | Brief item(s) | Class | Special treatment |
|---|---|---|---|---|
| 6.1 | Route-discovery bug fixes: junction-aware climb splitting + SAC cap-aware contraction + undirected Jaccard distinctness | 1, 2, 4 | Bugs (×3) | 3 regression tests + **human-review checkpoint**; closes Epic 5 deferred distinctness item |
| 6.2 | Roads as connectors | 3 | Feature (setup-side) | Standard review; invert the 2 `test_osm.py` drop-tests |
| 6.3 | Unified elevation profile (graph-Laplacian smoothing + deadband as profile transform) + slope-display readability + closeout (metamorphic re-validation, CLI help, doc consistency) | 8, 5, 6 | Bug + display + closeout | Box==curve regression test + **human-review checkpoint** |

**Why this slicing:** Items 1/2/4 are the three stacked causes of the *one* missing route — one coherent "make the known-good route discoverable" story, verified in a single checkpoint on the trigger area. Items 8/5/6 all reshape or render the *one* canonical elevation profile (the brief explicitly links Item 6b's slope baseline to Item 8's short-end-segment artifact). Roads (Item 3) stays separate — it's the only setup-side, cache-invalidating change and a feature, not a bug. The metamorphic re-validation + CLI-help + doc-consistency closeout folds into 6.3 (the last story, touching the most), rather than a dedicated story as in the Epic 4/5 precedent.

**Recommended order:** 6.1 → 6.2 → 6.3. 6.2 (roads) is independent and can slot anywhere. 6.3 reshapes the elevation baseline and carries the closeout, so it lands last. (Hobby-project guidance, not a commitment.)

**Success criteria:**
- The triggering loop (and routes containing both target ways) is returned on the trial area (verified in prototyping — the combined fix made it constructible).
- All four bugs have a regression test that fails on pre-fix code and passes after (three in 6.1, one in 6.3), each confirmed in a human-review checkpoint on the real area.
- Box D+/D− == plotted-curve cumulative (gap ≤ tolerance) across all routes; no manufactured slope spikes; genuine steep Alpine terrain preserved.
- Routes use short road connectors where useful; the objective self-limits road use.
- GRASP-vs-exhaustive gate (3.7) and all 8 metamorphic invariants pass under the new feasible set / profile.
- PRD, architecture, and epics reflect the new behavior; Operational Robustness (Epic 7) and Release Polish (Epic 8) renumbered consistently.

**Status of edits:** on approval, the planning-artifact edits (A1–A6 + sprint-status renumber + new Epic 6) are **applied** as part of this course-correction (matching the Epic 4 / Epic 5 precedent); the Section 4B/4C **code + test** changes are the dev-story handoff (Epic 6, Stories 6.1 → 6.3).

**Deferred to Epic 7 (Operational Robustness):** brief Item 7 — wire `--time-budget`/`--stagnation-iters`, fix the misleading `--help` text, and set the `--iter-budget` default in light of the junction-split search-space growth (Story 7.2).
