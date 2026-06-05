# Correct-Course Brief: Route-Discovery & Climb-Contraction Fixes

**Date:** 2026-06-05
**Prototyped on branch:** `spike/junction-aware-climbs` (base `main` @ `4a6e85a`; spike commits `6bf4965`..`643c4d6`)
**Scope note:** The elevation-**smoothing consistency** issue was originally out of scope here, with its own brief on branch `spike/smoothing-consistency` (`correct-course-brief-2026-06-05-elevation-smoothing.md`). It has since been **prototyped and resolved** in a follow-up session — folded in below as **Item 8** (with the verified mechanism and the lessons from two failed approaches). The original smoothing brief on that branch holds the fuller background (GPS-calibration motivation, rejected options); Item 8 is self-contained enough to implement from.

## How to use this brief

This is the **sole hand-off input** for a fresh `bmad-correct-course` session (plus the existing PRD/architecture/epics and the code). It captures problems found and fixes prototyped during an interactive diagnosis session.

The prototypes are **throwaway spikes**: each is behind an opt-in flag, marked `PROTOTYPE` in comments, and some unit tests were intentionally left red. **Re-implement cleanly** against the architecture and test conventions — do not merge the spike commits. Use the spike branch as the reference for *what* the change looks like and *that it works*.

## Origin

A route the user knew should exist (a loop combining specific steep trails near Grenoble) was never returned, despite all its trails being present in the raw OSM data. Diagnosis found **several independent causes** stacked on top of each other. Each is a real defect or design gap; fixing them together made the route constructible (verified — see Item 1).

## Recommended scope & priority

1. **Junction-aware climb splitting** — the core fix; makes legitimate routes reachable.
2. **SAC cap-aware contraction** — stops one over-cap pitch from disabling a whole climb.
3. **Undirected distinctness** — correctness/consistency fix for FR11 vs FR5.
4. **Roads as connectors** — feature; lets routes cross short paved gaps between trails.
5. **Elevation deadband** — a route-*selection* control (not a noise reducer).
6. **Slope-display readability** — color saturation + longer slope baseline (display-only).
8. **Elevation-smoothing consistency** — solver/box/display now derive from one smoothed profile (graph-Laplacian diffusion). Prototyped & verified; supersedes the separate smoothing brief.

Plus a **related finding** (Item 7): solver termination flags are unimplemented and the `--help` text misleads.

---

## Item 1 — Junction-aware climb splitting  ★ core fix

**Problem.** Stage-9 contraction collapses each detected climb into a single atomic super-edge whose interior nodes are deleted. A trail that joins a climb *partway up* (at a bench/junction interior to the climb) cannot board it — the solver can only enter/leave a climb at its two endpoints. The user's route needed exactly such a mid-climb turn.

**Root cause.** `contract_climbs` (`pipeline/graph.py`) emits one super-edge per climb from `climb.edges[0].node_u` to `climb.edges[-1].node_v`; interior nodes are absorbed.

**Prototype (commit `3c9ea9b`).** `contract_climbs(..., split_at_junctions=True)` splits a climb at any interior node incident (in the base graph) to a base segment outside the climb — i.e. a real trail junction. CLI flag `--split-climbs-at-junctions` (default off). New helpers `_split_climb_edges`, `_is_junction`.

**Evidence.** With splitting on (seed 44, T4), a route containing **both** target ways appears (routes 5 & 9). Cost is modest: contracted graph **+5.7% edges** (11185→11827), solve time **~flat** (26.1→26.5 s), because splitting happens only at genuine junctions.

**Decisions for proper implementation.**
- Default on, or opt-in? (Recommend on — atomic climbs are the defect.)
- Split at *all* externally-connected junctions, or only "routable" ones (skip dead-end stubs) to limit fragmentation?
- **Tension to resolve with the solver-budget story:** more, smaller super-edges enlarge the search space GRASP must cover. This couples to Item 7 (iter-budget). Worth deciding the budget policy in the same change.
- Tests: contraction unit tests (currently pass at the default-off), plus new tests for the split behavior.

## Item 2 — SAC cap-aware contraction

**Problem.** At `--difficulty-cap t4`, a long climb that contains even one T5 pitch is rejected *in full*, including all its T4-and-easier terrain.

**Root cause.** `contract_climbs._aggregate_sac_scale` aggregates a climb's SAC to the **max** across its edges; the solver's RCL then rejects any super-edge whose max-rank exceeds the cap. Observed: **two T5 edges poisoned a 4.3 km, mostly-T2 climb**, making the target trail unusable at T4.

**Prototype (commit `501ad7a`).** In `cli/query.py`, run `filter_trails(graph, untagged, difficulty_cap)` to drop above-cap edges **before** `detect_climbs`, so climbs never weld an over-cap pitch into otherwise-usable terrain.

**Evidence.** Worst super-edge SAC rank drops **6 → 4**; no above-cap super-edges remain.

**Decisions for proper implementation.**
- **Query-side (recommended)** keeps the cache cap-independent (architecture pins T6 at setup; cache key omits `difficulty_cap` per §Cat 4b) and keeps `--difficulty-cap` a fast knob. The user also floated baking it at setup (cache key includes `difficulty_cap`, re-prepare per cap) — viable but costlier; only do that if there's a reason to bake.
- The solver's per-edge RCL SAC filter becomes redundant after pre-filtering; keep it as defense or remove deliberately.

## Item 3 — Roads as connectors

**Problem.** Routes can't use short road segments that connect steep trails; roads are excluded entirely at two layers.

**Root cause.** `pipeline/osm.py`: the Overpass fetch filter and `TRAIL_HIGHWAY_TAGS` both exclude all road highway types.

**Prototype (commit `6bf4965`).** `_PROTOTYPE_ROAD_TAGS` (`residential, unclassified, service, living_street, tertiary`) added to the fetch filter; `filter_trails` keeps them as connectors, bypassing the SAC cap / untagged policy (roads carry no SAC grade).

**Evidence.** Routes now use short road connectors. **No explicit road cost term is needed** — the objective maximizes ascent+descent and roads are ~flat, so the solver self-limits road use to genuine connectors (user-confirmed reasoning).

**Decisions for proper implementation.**
- Which road types to admit (the prototype set is a starting point).
- **Tighten multi-tag handling:** a way tagged e.g. `["motorway","service"]` currently leaks in (any-road-tag-wins). Restrict to genuinely minor roads.
- Should road connectors be reuse-exempt (like short connectors)?
- This is a **setup-side** change (re-fetch); `pipeline_content_hash` changes automatically, so the cache key invalidates on its own.
- **Two `test_osm.py` tests intentionally fail** on the spike (they assert roads are dropped) — update them to the new contract.

## Item 4 — Undirected distinctness (j-max)

**Problem.** With `--j-max 0` (no overlap allowed), returned routes still share trail segments.

**Root cause.** Jaccard distinctness (`solver/distinctness.py::_canonical_edge_set`) keys on the **directed** `(node_u, node_v, key)`, while the reuse rule (Story 5.2) keys on the **undirected** `base_segment_id`. Two routes traversing the same physical trail in **opposite directions** look fully distinct to the Jaccard metric.

**Evidence.** Routes 5 & 6 (exact command) share **16 undirected base segments but 0 directed edges** → Jaccard distance 1.000. Systematic across routes; the D+ and D- excess between metric and profile is symmetric, consistent with opposite-direction reuse.

**Prototype.** None — diagnosed only.

**Decisions for proper implementation.** Make `jaccard_distance` key on the undirected base-segment identity (single-sourced via `solver.reuse`, the same identity GRASP/oracle/validator already share for reuse). Update the oracle's deferred-item note. This aligns FR11 distinctness with FR5 undirected reuse. Add tests for the opposite-direction case.

## Item 5 — Elevation deadband

**Feature (commit `6aca335`).** `--elevation-deadband` applies a hysteresis floor to D+/D- accumulation (query-side recompute from cached `vertices_resampled`; default 0 = exact prior behavior).

**Key finding.** Its **aggregate** effect on total churn is small (≤8% even at 3 m). But its effect on **route selection is significant** — it changes which segments clear `min_climb_slope` / θ, flipping which routes win. **Keep it as a selection control, not as a noise reducer.** (Do not justify or dismiss it by aggregate-elevation impact.)

**Decisions for proper implementation.** Finalize unit/semantics; decide setup- vs query-side. **Now coupled to Item 8 (resolved there):** the display-consistency question for the deadband is answered — the deadband is reframed as a *profile transform* (it flattens sub-floor reversals out of the actual vertices), so it folds into the single canonical profile that feeds solver, box, and display alike. Implement the deadband and the smoothing together as Item 8 describes; do not re-introduce the old sum-time-only deadband.

## Item 6 — Slope-display readability (display-only)

**6a — Color saturation (commits `588ef2f`→`643c4d6`).** The profile's diverging color scale saturated at a 30% grade, so all steeper terrain looked identical. Raised to **tan(30°) ≈ 0.58** (`CLAMP` in `templates/route.html.j2`). Trivial; keep. (User settled on 30° after trying 40°.)

**6b — Longer slope baseline (recommended, not yet prototyped).** The displayed slope is `rise/run` over a **single ~10 m segment** — a derivative over the shortest possible baseline. It spikes (e.g. 58% on an 8.6% trail) and, where a route nearly doubles back, `rise/tiny-run` produces extreme spikes. **Compute the displayed slope over a longer baseline** (±2–3 vertices ≈ 30–50 m). Display-only; no routing impact.

## Item 7 — Related finding: solver termination (not prototyped)

`--iter-budget` defaults to a **hard-coded 2000** (`query.py::DEFAULT_ITER_BUDGET`); `--time-budget` and `--stagnation-iters` are accepted but **inert** (Epic-4 stubs; `solver/anytime.py` is empty). The `--help` text claims "unlimited until time/stagnation budget hits" — **misleading**, and it caused a wrong inference during diagnosis. Raising `--iter-budget` to ~200k materially improved results, so solve quality is iter-budget-bound. **Either wire the Epic-4 termination or fix the help text**, and decide the budget in light of Item 1's search-space growth.

## Item 8 — Elevation-smoothing consistency  ★ resolved prototype

**Problem.** The metric/box D+/D-, the value the solver selects on, and the plotted elevation curve disagreed. The solver/box used a **per-edge** smoothing that pinned node-boundary elevations to raw DEM values; the display used a separate **continuous** (whole-route) smoothing. They differed by ~58–78 m (symmetric in D+/D-). The deadband (Item 5) made it worse — it reshaped the metric at sum time but never touched the displayed vertices.

**Unifying principle (the whole fix).** There must be **one canonical elevation profile per edge**; the box, the solver objective, and the plotted curve are all just the naive up/down sum of that single profile. Any operation that changes the metric must change the *profile itself*, not just the summation.

**What works (prototype on `spike/smoothing-consistency`, commit `c39a93a`).**
- **Smoothing = global graph-Laplacian diffusion** (`pipeline/smoothing.py::graph_smooth_elevation`). The entire resampled vertex field is treated as one connected graph; every vertex relaxes toward the mean of its chain neighbours via Jacobi iterations (`lam=0.5`). Each graph **node is a single shared variable** whose neighbours are the first interior vertex of every incident edge. Because the node value is shared, incident edges stay consistent at the join (box == curve); because diffusion is a low-pass filter, it **cannot create a slope spike** and it smooths *across* short/2-vertex edges.
- **Deadband = profile transform** (`graph_deadband_elevation`): flatten sub-floor reversals out of the actual vertices (keep turning points where the run from the last committed reference exceeds the floor; linearly interpolate between them; endpoints pinned to the shared node value). This replaces the old sum-time hysteresis.
- **Wiring** (`cli/query.py`): apply smoothing then deadband once to the whole graph; the SAME graph feeds `compute_edge_metrics(..., deadband_m=0.0)` (naive sum — both reshapings are already in the geometry) → climbs/contraction → solver, **and** is passed to `output.render(...)` with the render-side continuous pass disabled (`elevation_smoothing_window=1`).
- **Display add:** cumulative D+/D- in the profile hover (`templates/route.html.j2`) — reaches the box totals at the final vertex; a one-glance consistency check.

**Two approaches that FAILED (do not repeat them).**
1. *Per-edge "average the neighbouring edges' context" at each node.* The trail graph is **junction-dominated** (~3052 degree-3 nodes vs ~90 degree-2). Each incident edge averages a different neighbour set, so every junction got a different node elevation → a jump at every junction; the gap did not collapse.
2. *Per-edge moving average with the endpoint pinned* (to raw, or to one shared value). Pinning forces the endpoint to one value while the adjacent interior is a wide mean offset from it, dumping the whole offset into one ~10 m segment → **manufactured slope spikes** (observed ~1000%; raw-pinning even grew the max per-segment drop 35 m → 57 m). Per-edge methods also **cannot smooth across 2-vertex/short edges** (no interior to average), so big raw jumps survived. The Laplacian fixes both because it is global and never pins.

**Evidence (seed 44, T4, split, repro below).** With the Laplacian: box−curve gap **0.000 m** across all routes (with and without deadband); max per-segment `|ΔElev|` reduced 34.7 m (raw) → ~9.8 m at window 10, monotonically with iterations; manufactured spikes eliminated. Residual >50% segments are **genuine steep Alpine terrain** (raw has ~5247 sustained >50% segments) — smoothing correctly leaves them; do not over-smooth to hide them.

**Decisions for proper implementation.**
- **Query-side** (decided with the user) — keeps the cache smoothing-independent, fast to sweep.
- **`window` → iterations mapping / unit.** The prototype maps `iters ≈ window² / 6` (Gaussian-equivalent of a moving-average window) and keeps the unit in **vertices**. Two follow-ups: pick a defensible strength semantics, and **convert the unit to meters** (decouple from the 10 m resample spacing — the original smoothing brief's standing request).
- **Remove the misleading `deadband_m` parameter** from `compute_edge_metrics` once the deadband is a profile transform — it only ever does a naive sum now, so passing `0.0` is a trap for future readers (the user flagged this).
- **Remove the dead code:** the per-edge `mean_smooth_elevation`, the node-field prototype, and the render-side continuous-smoothing branch in `output.py` (now bypassed via `window=1`) — there is one smoothing, applied once.
- **Short end-segment artifact (links to Item 6b).** The resampler leaves sub-5 m end-segments (min ~0.66 m); a small `ΔElev` over a tiny run reads as an extreme slope regardless of smoothing (~5–7 per route set). Item 6b's longer slope baseline addresses exactly this on the display side — resolve together.
- **Performance.** Diffusion runs over the whole graph each query (the solver needs all candidate edges smoothed). Fine for the prototype; revisit if query latency matters.
- Tests: smoothing/climbs unit suites pass on the prototype; add tests asserting (a) box == curve over a route, (b) max segment `|ΔElev|` never exceeds raw (no spike manufacture).

---

## Reference

- **Branch (Items 1–7):** `spike/junction-aware-climbs` (base `main` @ `4a6e85a`).
- **Commits (Items 1–7):** `6bf4965` roads · `6aca335` deadband · `501ad7a` SAC cap · `3c9ea9b` junction split · `eb28a72` smoothing knob · `e5cf06e` display smoothing · `588ef2f`/`643c4d6` color clamp.
- **Branch (Item 8):** `spike/smoothing-consistency` (branched from `spike/junction-aware-climbs`, so Items 1–7 are present in its tree). Smoothing resolution commit **`c39a93a`**; files `pipeline/smoothing.py` (`graph_smooth_elevation`, `graph_deadband_elevation`), `cli/query.py`, `templates/route.html.j2`.
- **Verification context:** all evidence above is from center `45.260,5.788`, radius 4, `--cache-dir ./.trial-cache`, seed 44, `--l-connector 50 --j-max 0 --difficulty-cap t4 --n 10 --iter-budget 200000`. Item 8 evidence adds `--elevation-smoothing-window 10` (and `--elevation-deadband 1.0` for the deadband-consistency check).
- Spike prototypes are opt-in flags and are **evidence, not merge candidates**.
