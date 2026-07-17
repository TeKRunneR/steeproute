---
stepsCompleted: [lean-pass]
inputDocuments: ['_bmad-output/brainstorming/brainstorming-session-2026-07-14-1437.md', '_bmad-output/planning-artifacts/future-ideas.md']
scope_note: 'Lean pass for an N=1 thin CLI wrapper — screen inventory + key flows + rough wireframes for the least-obvious views (progress, run library). Deliberately not a full BMAD UX spec.'
---

# UX Design Specification — steeproute web App

**Author:** Yann
**Date:** 2026-07-14

> **Post-v1 update (2026-07-17, App Epic 4).** Hands-on use of the shipped app
> drove four refinements (correct-course
> [sprint-change-proposal-2026-07-17-app-ux-improvements.md](sprint-change-proposal-2026-07-17-app-ux-improvements.md)),
> reflected inline below: **S1** gains three explicit selection modes (area-pick
> default / move-selection / select-region — click a built region to query it
> directly); **S2** drops the basic/advanced split (all flags always visible) and
> uses space-grouped numbers with `max_descent_slope=0.4` + `start_at_junction`
> on by default; **S4** run cards lead with a reverse-geocoded town label and add
> a click-to-reveal query-params view. This decides the Cluster-D "run-card
> fields" question previously left open in §4/§5.

> **Scope of this document.** steeproute's App is a thin web UI over two existing
> CLIs (`setup` + `query`). Its design was settled in the
> [brainstorming session](../brainstorming/brainstorming-session-2026-07-14-1437.md);
> this spec does not re-open those decisions. Per the session's closing note, this
> is a **lean pass**: a screen inventory, the key flows, and rough wireframes for
> the two views that are least obvious (live **progress** and the **run library**).
> Everything else is thin and reuses existing output — no visual-design system,
> no component library, no responsive/accessibility spec is warranted for a
> single-user local tool.

---

## 1. Design principles (inherited, not invented)

These fall out of the brainstorming decisions and constrain every screen below:

1. **The backend job runner is the product.** The UI is a thin skin over a
   single-worker serial queue. Screens exist to *start* jobs, *watch* them, and
   *revisit* them — nothing more.
2. **Reuse over re-render.** Result routes are the existing Leaflet HTML report,
   embedded in an iframe as-is. No native map re-rendering of results.
3. **Deliberate, not magic.** Building a region (`setup`) and querying it are two
   explicit user actions, never auto-chained. The UI must make the cached/uncached
   distinction visible so the two-step feels natural, not like an error.
4. **Every run is addressable and returnable.** Fire-and-forget: you can navigate
   away from a running job and come back to its live progress at any time. There is
   no blocking modal anywhere.
5. **CLI-honest progress.** Progress is scraped CLI stdout surfaced faithfully
   (phase + stage + log tail + best-so-far cost). No fabricated bars/ETAs for v1 —
   the user reads CLI-style progress comfortably.

---

## 2. Screen inventory

Five surfaces. Only two (★) carry non-obvious design weight and get wireframes below.

| # | Screen | Purpose | Design weight | Reuse |
|---|--------|---------|---------------|-------|
| S1 | **Map home** | Pick center+radius; see cached regions; launch "Build region" or "Configure query" | Low–medium | New Leaflet picker |
| S2 | **Config form** | Basic/advanced flag entry before queuing a query (or a build) | Low | New form; defaults = quality demo params |
| S3 | **★ Run watch (progress)** | Live progress of one job; return-to-live; Stop | **High** | New — the crux (unified progress) |
| S4 | **★ Run library** | List all jobs (queue + history); open finished, re-run-with-tweaks | **Medium** | New — the job registry *is* the index |
| S5 | **Result view** | The finished routes | None | **Existing** Leaflet HTML report in an iframe |

**Global chrome:** a persistent header with (a) app name → Map home, (b) a
**Runs** link → Run library, and (c) a compact **live-job indicator** (e.g.
"● query running — r20 · iter 12k") that is present on every screen and links to
S3 for the active job. This indicator is what makes "return to live progress at
any time" real without a modal.

Navigation is flat — three primary destinations (Map home, Run library, the
active Run watch). Config (S2) and Result view (S5) are reached *through* those,
never top-level.

---

## 3. Key flows

Notation: `→` = user action/navigation; `⟳` = server-side job state.

### F1 — Query a region that is already cached (the happy path)
```
S1 Map home
  → click map to drop center, drag handle to set radius
  → circle lands inside a GREEN (cached) overlay
  → "Configure query" button enables
S2 Config form (prefilled with quality-demo defaults)
  → adjust basic knobs (advanced collapsed); "Queue query"
  ⟳ job enqueued → worker free → starts immediately
S3 Run watch  (auto-navigated to the new job)
  ⟳ running → done
  → "View routes"
S5 Result view (iframe HTML report)
```

### F2 — First time on an uncached area (the deliberate two-step)
```
S1 Map home
  → drop center + radius over a GREY (uncached) overlay
  → primary action is "Build this region" (NOT "Configure query")
  → "Build this region"
  ⟳ setup job enqueued → runs
S3 Run watch  (setup-flavour progress: its own stages)
  ⟳ done → region overlay for that area flips GREEN
  → now the SAME area offers "Configure query"  →  continues as F1
```
> Open question (Cluster D, deferred): whether an uncached "Configure query"
> attempt should *offer* to build vs. simply be disabled until built. This spec
> assumes **disabled + a "Build this region first" prompt**; revisit when reached.

### F3 — Return to a running job
```
Any screen
  → click the live-job indicator in the header
S3 Run watch  (snapshot of accumulated progress, then live SSE tail)
```

### F4 — Queue several runs
```
S1/S2  → "Queue query"  (repeat N times, different areas/params)
  ⟳ worker runs them one at a time, serially
S4 Run library shows: 1 running, rest queued (in order), history below
```
> Open question (Cluster D, deferred): reorder / cancel-queued UX. v1 assumption:
> queued jobs are cancelable, not reorderable. Revisit when reached.

### F5 — Browse history & re-run with tweaks
```
S4 Run library
  → click a finished run
      • "View routes"      → S5 iframe report
      • "Re-run with tweaks" → S2 Config form PREFILLED from that run's stored params
S2  → tweak → "Queue query"  → continues as F1
```

### F6 — Stop a running job
```
S3 Run watch
  → "Stop"  (mirrors CLI Ctrl-C: best-so-far flush)
  ⟳ status → stopped; found routes still rendered
  → "View routes" available (partial result)
```

### F7 — Server restart mid-run (recovery, not user-initiated)
```
⟳ on boot, any job left `running` → marked `failed (interrupted)`
S4 Run library shows it as failed(interrupted); "Re-run with tweaks" offered
```

---

## 4. Rough wireframes — the least-obvious views

ASCII, deliberately low-fidelity. These fix *information layout and state*, not
visuals. Plain HTML/JS/Leaflet, no design system.

### S3 — Run watch (live progress) ★

The crux screen. It must render **all three stdout flavours** through one model
and read the same whether the job is a setup or a query.

```
┌───────────────────────────────────────────────────────────────────┐
│ steeproute        Map   Runs        ● query running · r20 · iter12k │  ← global header + live indicator
├───────────────────────────────────────────────────────────────────┤
│  Query · r20  (center 45.19,5.72 · radius 20 km)      [ Stop ]      │  ← job identity + Stop (F6)
│  status: RUNNING     started 14:37:02     elapsed 03:41            │
├───────────────────────────────────────────────────────────────────┤
│  PHASE: solve                                                       │  ← progress model: phase
│  STAGE: GRASP restarts            (stage 6 / 9)                     │  ← stage_name + stage_index/total
│  best-so-far cost: 0.284   ·   iteration: 12,041                    │  ← grasp:{iter,best_cost} (query only)
│                                                                     │
│  ┌── log tail ────────────────────────────────────────────────┐    │  ← log_tail[]: raw scraped lines,
│  │ [solve]  restart 8: best 0.291 -> 0.284                     │    │    newest at bottom, monospace,
│  │ [solve]  restart 9 seeded (island migration)               │    │    auto-scroll, this is the
│  │ [solve]  iter 12000  best 0.284  stagnation 1,204          │    │    "CLI-honest" surface
│  │ ...                                                         │    │
│  └────────────────────────────────────────────────────────────┘    │
├───────────────────────────────────────────────────────────────────┤
│  (when status = done)     [ View routes ]                          │  ← → S5 iframe report
│  (when status = stopped)  [ View routes (partial) ]                │
│  (when status = failed)   exit code 2 · [ Re-run with tweaks ]     │
└───────────────────────────────────────────────────────────────────┘
```

State-by-flavour notes (why this layout, not decoration):
- **GRASP (query, flavour 1):** the `best-so-far cost` + `iteration` line is the
  only structured readout; it appears **only** during the solve phase and is blank
  for setup jobs. Don't reserve space for it globally.
- **Query non-solve stages (flavour 2):** cache load, elevation reshape, climb
  detect, contraction, validate/render — these advance `STAGE (n/total)` and emit
  timing lines into the log tail; no GRASP line.
- **Setup (flavour 3):** a *different* stage set entirely; same `PHASE / STAGE
  (n/total) / log tail` frame, GRASP line absent. The frame is flavour-agnostic on
  purpose — that's what makes one screen serve all three.
- The whole body is fed by the **SSE stream**; on load, render the persisted
  snapshot first, then attach live tail (F3). No bars/ETA in v1.

### S4 — Run library ★

The job registry rendered directly — "the index is free once the job store exists."
One list, ordered by lifecycle then recency: **running → queued (in order) →
history (newest first)**.

```
┌───────────────────────────────────────────────────────────────────┐
│ steeproute        Map   Runs        ● query running · r20 · iter12k │
├───────────────────────────────────────────────────────────────────┤
│  Runs                                                               │
│                                                                     │
│  ● RUNNING                                                          │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ query · r20   45.19,5.72 · 20km   elapsed 03:41   [ Watch ] ──┼──┼─→ S3
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ◔ QUEUED (2)                                                       │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ 1  query · r15   45.10,5.70 · 15km            [ Cancel ]      │  │  ← cancel queued (F4);
│  │ 2  setup · new   46.00,6.10 · 25km            [ Cancel ]      │  │    reorder deferred (Cluster D)
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ✓ HISTORY                                                          │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │ done    query · r20   20km   14:31  cost 0.281                │  │
│  │           [ View routes ]  [ Re-run with tweaks ] ────────────┼──┼─→ S5 / S2(prefilled)
│  │ stopped query · r30   30km   14:02  cost 0.402 (partial)      │  │
│  │           [ View routes (partial) ]  [ Re-run with tweaks ]   │  │
│  │ failed  setup · new   25km   13:40  exit 1                    │  │
│  │           [ Re-run with tweaks ]                              │  │
│  │ failed  query · r20   20km   12:10  interrupted (restart)     │  │  ← F7 recovery state
│  │           [ Re-run with tweaks ]                              │  │
│  └─────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

Run-card contents (Cluster D was left open; this is a v1 **starting** proposal,
not a settled spec): `kind · area-label`, center/radius, timestamp, and a
status-appropriate metric (`cost` for done/stopped query jobs, `exit code` for
failed). Actions are status-gated exactly as drawn. Trim or extend when
implementing — this is the minimum that makes the flows above work.

---

## 5. Deliberately out of scope for this pass

To keep the lean promise honest, this spec does **not** cover — and for a
single-user local tool, need not cover:

- Visual design system, color/typography, theming.
- Component library / reusable-component strategy.
- Responsive breakpoints and mobile layout (desktop-local tool).
- Accessibility conformance spec.
- Emotional-response / brand / inspiration exploration.
- S1 (map picker) and S2 (config form) wireframes — conventional patterns
  (Leaflet click-to-pin + a collapsible form); no ambiguity worth a diagram.
- Final resolution of the remaining Cluster D open questions (offer-vs-block on
  uncached, queue reorder/cancel) — flagged inline where they touch a flow, to be
  decided when the implementation reaches them. (The "run-card fields" question is
  now decided — App Epic 4: town label + params view; see the post-v1 update note
  at the top.)

---

## 6. Traceability

Every decision here derives from a settled brainstorming outcome:

| This spec | Source |
|-----------|--------|
| Two-step build→query, cached/uncached overlay | Cluster C; Phase-1 ②③, B1 decision |
| Single Run-watch screen for 3 progress flavours | Cluster B / B2; Phase-1 ⑤ |
| Live-job indicator + return-to-progress (no modal) | Fire-and-forget decision; B3 |
| Reuse HTML report in iframe (S5) | Phase-1 ⑥ |
| Run library = job registry | Emergent theme; B3 |
| Queue serial, cancel-yes/reorder-later | Concurrency decision; Cluster D |
| Stop = best-so-far flush | Phase-1 ⑤; F6 |
| Restart → failed(interrupted) | B3 edge case; F7 |
| Config defaults = quality demo params | Phase-1 ③ |
```
