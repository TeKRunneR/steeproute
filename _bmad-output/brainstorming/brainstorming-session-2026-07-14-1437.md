---
stepsCompleted: [1, 2, 3]
inputDocuments: ['_bmad-output/planning-artifacts/future-ideas.md']
session_topic: 'steeproute web App (UI: pick center+radius on map, run setup+solver, view result routes, browse old runs)'
session_goals: 'A thin, pragmatic web UI over the two existing CLIs (setup + solver) — surface the right screens/flows for pick-area → run → view routes → browse old runs. Not a product-scale feature exploration.'
selected_approach: 'progressive-flow'
techniques_used: ['Role Playing (journey walk)', 'Mind Mapping (clustering)', 'SCAMPER (refine)', 'Decision Tree Mapping (action)']
ideas_generated: ['single-worker backend job runner is the real heart', 'scrape 3 stdout progress flavours into one SSE stream', 'fire-and-forget jobs + return-to-live-progress', 'deliberate manual build(setup)→query two-step', 'basic/advanced config with all flags + quality defaults', 'reuse existing Leaflet HTML report via iframe', 'per-job JSON persistence doubles as runs library', 'one-run-at-a-time serial queue', 'FastAPI + plain HTML/JS/Leaflet stack']
context_file: '_bmad-output/planning-artifacts/future-ideas.md'
---

# Brainstorming Session Results

**Facilitator:** Yann
**Date:** 2026-07-14

## Session Overview

**Topic:** steeproute web App — a UI to pick a center point + radius on a map, run
the setup + solver pipeline, view result routes, and browse results of old runs.

**Goals:** _(to be captured)_

### Context Guidance

Sourced from `future-ideas.md` → "App" section. steeproute is currently a CLI tool
that generates steep walking/running routes from OSM + DEM data via a GRASP solver.
The App idea turns that pipeline into an interactive web experience. Personal /
portfolio project (N=1), so learning value and exploration count as valid rationale,
not just use-case fit.

### Session Setup

Approach: **Progressive flow (broad → narrow)**, scoped to a thin UI over the two
CLIs. Journey: entry → pick area → configure → run/wait → view routes → browse old
runs. Techniques: Role Playing (journey walk) → Mind Mapping (cluster) → SCAMPER
(refine) → Decision Tree (next steps).

## Technique Selection

**Approach:** Progressive Technique Flow (scoped, pragmatic)

- **Phase 1 — Exploration:** Journey walk (role-play the user, station by station)
- **Phase 2 — Pattern Recognition:** Mind mapping / clustering of what surfaced
- **Phase 3 — Development:** SCAMPER on the few stations that carry real design weight
- **Phase 4 — Action Planning:** Decision-tree / concrete next steps for a v1 UI

**Journey Rationale:** For a thin wrapper the pipeline itself is the structure, so
walking the user's path surfaces the real decisions without inflating scope.

## Ideas Generated

### Phase 1 — Journey Walk

**Key framing:** Much of "view routes" already exists — query CLI renders a
Leaflet + Chart.js HTML report + JSON. Cache-based: `setup` builds a region cache;
`query` resolves center/radius against it (exit 2 if uncached). App = input capture
+ orchestration + run library around existing output.

**① Entry / ② Pick area**
- Full-bleed Leaflet map as home; click to drop center pin, drag radius handle
  (circle overlay = query area).
- Show cached regions as shaded overlays (green = instant query, grey = needs setup).
- DECISION (settled): uncached area → **deliberate explicit "Build this region"
  step**, not silent auto-setup. Keeps setup (expensive, cacheable) distinct from query.

**③ Configure the run**
- DECISION (settled): **basic/advanced split**, but ALL flags must remain
  accessible — every flag is impactful. Basic row = common knobs; advanced =
  collapsed full set.
- Open question carried forward: app defaults should likely be the *quality* demo
  params (AGENTS.md), not the deliberately-low CLI defaults.

**④ Launch**
- One "Generate routes" button orchestrates query (region already built via the
  deliberate setup step).

**⑤ Wait / progress**
- DECISION (settled): **fire-and-forget jobs**, not a blocking modal. Runs are
  long; must be able to return to the live progress of any ongoing run at any time.
- CRITICAL NUANCE: progress comes in **three flavours**, and ALL must reach the UI:
  1. GRASP solver → structured `ProgressEvent`s (iterations, best-so-far cost).
  2. Rest of the query CLI (~half the solver-CLI wall-clock) → only per-stage
     start/end + timing lines (cache load, elevation reshape, climb detect,
     contraction, validate/render).
  3. Setup CLI → its own different stage/progress shape.
  → Implies a **unified progress/log model** per job: capture structured events +
    stage lines from all three sources into one job-scoped stream the UI subscribes to.
- Mirror CLI Ctrl-C: a **Stop** button = best-so-far flush, still renders found routes.

**⑥ View result routes**
- DECISION (settled): **reuse the existing Leaflet HTML report as-is** (user likes
  how routes look now). Thinnest option; no native re-render. Likely embed via iframe.

**⑦ Browse old runs**
- Each run = an output dir (HTML + JSON). Re-open = show stored HTML/JSON;
  "re-run with tweaks" = prefill config form from that run's params.
- Runs-index question resolved below (see Emergent theme).

### Emergent theme — the backend job model
Fire-and-forget + return-to-progress means the app is NOT purely stateless over
the CLIs: it needs a **server-side job runner** that spawns setup/query as
subprocesses, captures all three progress flavours into a per-job stream, and
persists job state (queued/running/done/failed/stopped). Once that exists, the
"runs index" is essentially free — the job registry IS the index. So: **keep a
lightweight index/job store** (rather than re-scanning disk folders) because
fire-and-forget already forces server-side job state to exist.

**Progress ingestion (settled):** (a) **scrape stdout text as-is** — simpler than
teaching the CLIs to emit structured JSON; acceptable for a personal tool over
his own CLIs. Tradeoff noted: couples UI to stdout formatting (brittle if output
changes); structured-emit remains a later fallback if scraping gets painful.

**Concurrency (settled):** **one run at a time.** Solver is built to saturate all
compute and its thread-safety across parallel runs is unknown/unintended. So the
job runner is a **serial queue with a single worker** — but **queuing runs** is a
desired feature (line up several, they execute one after another). This also
simplifies the backend (no concurrent-job management).

### Phase 2 — Clustering (confirmed by user)

**Cluster A — Thin & basically solved (reuse existing):** map picker (Leaflet
center+radius), config form (basic/advanced, all flags, quality defaults), result
view (iframe existing HTML report), re-open / re-run-with-tweaks (prefill form).

**Cluster B — Load-bearing core (the only real risk):** single-worker job queue
(spawn setup/query subprocesses, serial); unified progress (scrape all three
stdout flavours into one per-job stream); job persistence + return-to-live-progress
(index doubles as runs library).

**Cluster C — Deliberate orchestration (settled, shapes UX):** explicit "Build
region" step; cached-region overlay (green/grey); Stop = best-so-far flush.

**Cluster D — Open smaller questions:** uncached-area → offer-to-build vs block;
run-card contents; queue management UX (reorder / cancel queued).

Phase 3 focuses on **Cluster B** (agreed).

### Phase 3 — Cluster B developed

**B1 · Single-worker job queue.** Job record `{id, kind:setup|query, params,
area{center,radius}, status:queued|running|done|failed|stopped, created/started/
finished_at, exit_code}`. One worker loop: pop next queued → spawn subprocess →
stream stdout → set terminal status. Setup and query are separate job kinds.
- DECISION (settled): deliberate build = **manual two-step** (build region job,
  then query job — user's own two clicks), NOT an auto-linked build+query pair.

**B2 · Unified progress (the crux).** Scraping hinges on the exact stdout formats;
first task is a **format inventory** of all three sources. Line-classifier → small
progress model `{phase, stage_name, stage_index/total, grasp:{iter,best_cost}|null,
elapsed, log_tail[]}`.
- DECISION (settled): **minimal progress UI to start** — current phase + stage name
  + live log tail + best-so-far cost during GRASP. No bars/ETA/sparkline for v1
  (user is comfortable reading CLI-style progress).

**B3 · Persistence + return-to-live-progress.** Store job records + accumulated
progress; reconnecting browser gets snapshot then live tail. Transport: **SSE**
(one-way server→client, fits progress).
- DECISION (settled): **per-job JSON files** to start (dead simple,
  human-inspectable); SQLite is a later option if the run library grows.
- Edge: on server restart, mark any `running` job as `failed (interrupted)`.

### Phase 4 — Action planning

**Tech stack (settled):**
- Backend: **FastAPI (Python)** — same language as the CLIs (can spawn as
  subprocess OR import internals directly), first-class async + SSE, minimal ceremony.
- Frontend: **plain HTML + JS + Leaflet** — thinnest, no build step; Leaflet assets
  already shipped. Consistent with the thin-wrapper goal.

**Suggested build sequence (each milestone independently demoable):**
1. **Spike — stdout format inventory:** capture real setup + query runs (good demo
   params), pin line shapes for the 3 progress sources. De-risks B2 before UI.
   NOTE: this is an *implementation-time spec task*, deliberately NOT done during
   brainstorming (premature; scrapeability of own-code plain text isn't in doubt).
2. **Backend skeleton:** single-worker queue + spawn subprocess + per-job JSON
   persistence + `GET /jobs`. Drive via curl, no UI.
3. **Progress plumbing:** stdout line-classifier → progress model → SSE endpoint.
4. **Frontend run+watch:** config form (basic/advanced) → queue query → live
   progress page → return-to-progress. The app's spine.
5. **Map picker + cached-region overlay:** Leaflet center+radius, green/grey
   regions, "Build region" (setup job) button.
6. **Run library:** list jobs, open finished run (iframe its HTML report),
   re-run-with-tweaks (prefill form from stored params).

**Cluster D leftovers (deferred, non-v1-blocking):** run-card contents, queue
reorder/cancel — decide when reached.

---

## Session Summary

**What this app is:** NOT a stateless HTML form over two CLIs. The fire-and-forget
+ return-to-live-progress requirement makes its heart a **single-worker backend job
runner** (FastAPI) that spawns setup/query subprocesses, scrapes all three stdout
progress flavours into one per-job SSE stream, and persists job state (which doubles
as the runs library). Everything else — map picker, config form, result view — is
thin and largely reuses what the CLI already produces (the Leaflet HTML report).

**Settled decisions:**
- Uncached area → **deliberate manual two-step** (build region, then query). No
  silent/auto setup.
- Config: **basic/advanced split, ALL flags accessible**, app defaults = quality
  demo params (not low CLI defaults).
- **Fire-and-forget jobs**; must return to live progress of any run anytime.
- Progress: **scrape stdout** (all 3 flavours: GRASP events, query-stage timing,
  setup stages); **minimal UI** to start (phase + stage + log tail + best-cost).
- Concurrency: **one run at a time; single-worker queue**; queuing runs is desired.
- Result view: **reuse existing Leaflet HTML report** (iframe) — liked as-is.
- Persistence: **per-job JSON**; **SSE** transport; mark `running`→`failed` on restart.
- Stack: **FastAPI + plain HTML/JS/Leaflet**.

**The one real risk / crux:** unified stdout progress scraping (B2). First
implementation task = the stdout format inventory spike.

**Immediate next step:** if/when promoted, start at build-sequence step 1 (format
inventory spike), then step 2 (backend skeleton). This is backlog material, not
committed scope — promote via a correct-course / epic if picked up.


