# steeproute — Future Ideas

Running backlog of post-v1 improvements. Low-friction: append new ideas to the bottom as they occur. Not committed scope, not sequenced — these get promoted into epics/stories (or a correct-course) if and when they're picked up.

Format per idea: a short title, what it does, and any notes on rationale or approach.

> **Promoted 2026-06-25:** the former items #1 (start-at-junction flag) and #2 (direction-aware max-descent-slope flag) were pulled into v1 as **Epic 10 — Practical Route Constraints** (see `sprint-change-proposal-2026-06-25-junction-start-and-descent-cap.md`). Remaining items renumbered.

---

## 1. Strategies for feasible search over larger areas

Make searching large areas tractable within a reasonable time budget. Fuzzy/wide item — an umbrella for time-vs-area scaling techniques rather than one feature.

**Candidate approach (coarse-to-fine):**
- Run the solver many times on the full large area with low iteration counts and varied seeds.
- Gather the most frequently recurring candidate routes/regions across those runs.
- Re-run the solver with higher iteration counts on smaller sub-areas centered on those candidates.

**Notes:**
- Explore other strategies too — this is one idea, not a decided design.
- Interacts with the area-size cap (FR2) and the time-budget / progress reporting machinery.

---

## Performance tuning

- Performance profiling to identify bottlenecks.
- Try a Rust rewrite to see if that offers significant gain

**Researched 2026-07-02:** see `research/technical-steeproute-performance-tuning-research-2026-07-02.md` — phased roadmap (instrument → profile → benchmark → cheap wins → conditional native kernel), Rust scoped to PyO3 hot-loop extraction, rustworkx identified as a no-Rust-authorship alternative.

**Promoted 2026-07-03:** Phases 0–2 shipped as Epic 11; profiling verdict in `research/steeproute-bottleneck-analysis-2026-07-03.md` (loop skeleton dominates; rustworkx not indicated). Phase 3 promoted to Epic 12 via correct-course 2026-07-03. Phase 4 (extract-interface-first → PyO3 kernel) remains conditional on Epic 12's closing go/no-go.

**Decided 2026-07-04 (Epic 12 close, Story 12.4):** solver 5.6× vs baseline — Phase-3 band exceeded; PyO3 solver kernel **no-go on performance need** (Amdahl-capped ~1.4× end-to-end on large areas; stays the one Rust-shaped option on learning value). The phase split flipped: query-side stages 6–9 + cache read + imports now dominate large-area runs — next levers (all pure-Python/numpy-shaped, headlined by smoothing vectorization) ranked in `research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md`; any follow-on arrives via correct-course.

**Promoted 2026-07-04:** the query-side levers promoted to Epic 13 via correct-course 2026-07-04
(`sprint-change-proposal-2026-07-04-query-side-performance.md`). The PyO3 solver kernel stays here as the one
Rust-shaped option on learning value only — not planned; precondition unchanged (time-boxed
cargo-behind-corporate-proxy spike before any commitment).

**Promoted 2026-07-06:** the setup-CPU + solver-parallelism levers from
`research/steeproute-next-optimization-pass-handoff-2026-07-05.md` promoted to Epic 14 via
correct-course 2026-07-06 (`sprint-change-proposal-2026-07-06-setup-solver-scaling.md`).

**Deferred to post-probe correct-course (2026-07-06):** three deep levers from that same handoff were
intentionally NOT in Epic 14 — the custom Overpass-JSON→graph parser (S5-deep), the schema-v3
numpy-array edge contract (Q4), and per-stage multiprocess parallelization (§7 step 6). Each was gated
on the Epic 14 r50 probe (Story 14.6) supplying the residual costs that justify it.

**Promoted / re-scoped 2026-07-24:** a fresh measured end-to-end review
(`research/steeproute-performance-review-gpt-5-6-2026-07-24.md`, ref commit `4380970`, r20) resolved
the parked Story 14.6 what-next decision. Its full findings — query orchestration (proven
byte-identical 80→67 s), setup owned-data + smoothing/resampling fusion, an in-place osmnx
largest-component/consume adapter, the geometry-optional schema-v3 cache (**this is deferred item Q4**,
now promoted), solver static-context + pure-Python loop wins, and the shared-memory-array solver state
(the deferred structural fix, `research/steeproute-shared-memory-array-solver-design-2026-07-08.md`,
now promoted) — were pulled into **Epic 16 (Ownership-Oriented Performance Pass)** via correct-course
(`sprint-change-proposal-2026-07-24-ownership-oriented-performance.md`). The review's Batch C
in-place osmnx adapter is explicitly **not** the S5-deep custom parser. Still deferred and NOT in
Epic 16 (this review does not cover them): the **S5-deep custom Overpass→graph parser** and
**per-stage multiprocess pipeline parallelization** — each still routes through a future
correct-course, now scoped from Epic 16's Story 16.7 residuals rather than 14.6.

## App
Make a thin web UI over the two existing CLIs (setup + solver): pick a center
point + radius on a map, run the pipeline, view result routes, browse old runs.

**Brainstormed 2026-07-14** (see `../brainstorming/brainstorming-session-2026-07-14-1437.md`).
Key realization: this is *not* a stateless form over two CLIs. The fire-and-forget
+ return-to-live-progress requirement makes its heart a **single-worker backend job
runner**; everything else is thin and reuses existing output (the query CLI already
renders the Leaflet HTML report).

**Settled decisions:**
- **Stack:** FastAPI backend + plain HTML/JS/Leaflet frontend.
- **Orchestration:** deliberate **manual two-step** — build region (setup job),
  then query job. No silent/auto setup on uncached areas.
- **Config form:** basic/advanced split, **all** flags accessible; app defaults =
  quality demo params (not the low CLI defaults).
- **Jobs:** fire-and-forget; **one run at a time** (single-worker serial queue,
  queuing multiple is desired); must return to live progress of any run anytime.
- **Progress:** scrape stdout for all **three** flavours (GRASP `ProgressEvent`s,
  query-stage start/end+timing, setup stages) into one per-job **SSE** stream;
  minimal UI to start (phase + stage + log tail + best-so-far cost). Stop button =
  best-so-far flush.
- **Result view:** reuse the existing Leaflet HTML report as-is (iframe).
- **Persistence:** per-job JSON files (index doubles as the runs library); on
  server restart mark any `running` job `failed (interrupted)`. SQLite later if needed.

**The crux / only real risk:** unified stdout progress scraping. First
implementation task = a **stdout format inventory** spike (capture real setup +
query runs, pin the line shapes) before building the classifier.

**Build sequence:** (1) format-inventory spike → (2) backend skeleton (queue +
subprocess + per-job JSON + `GET /jobs`) → (3) progress plumbing (classifier →
SSE) → (4) frontend run+watch (config form → live progress → return-to-progress)
→ (5) map picker + cached-region overlay + build button → (6) run library
(list / open via iframe / re-run-with-tweaks).

# App UX improvements

**Promoted 2026-07-17:** these four items were pulled into App Epic 4 via
correct-course (`sprint-change-proposal-2026-07-17-app-ux-improvements.md`) —
now three stories (the config-form items were merged): 4.1 map selection modes,
4.2 config-form overhaul (flat layout + space-grouped numbers + corrected
defaults), 4.3 recognizable runs (town label + params view). See `epics-app.md`.

* On the map I need several different selection modes: 1) select existing region (so that I can easily select a region that was already built, and run a query on it); 2) move the area picker square: right now I can only click a new spot to move it. Both should be separate modes, so that in the "area pick" mode (which is the only current mode), I can still click any point to define a new area.
* Almost all query parameters are actually always important, so the "advanced" section of the query parameter pane is useless. This pane should always open with all parameters.
* On the runs screen, right now it's almost impossible to remember which runs were done where, as GPS coords just aren't explicit for a human user. I need something else: maybe a screenshot of the area, or a name of a town close to the center of the area, if that's easily to determine ==> this is for both setups and queries. For queries specifically, I want to be able to view all query parameters, maybe not at all times, but something I can click to show them at least.
* In query parameters, long numbers (like 1M for iter budget) are difficult to parse. Something that separates thousands better would be good (avoiding commas, I'm French so for us commas can be confusing as they're commonly used as decimal separators). Also, not really a UX thing, but while I'm here: set the default max descent slope to 0.4, and have "Start at junction" checked by default.

# Non-square search areas

**Promoted 2026-07-24:** rotated-rectangle search areas pulled into **CLI Epic 15**
(Rotated-Rectangle Search Areas) + **App Epic 5** (Rotated-Rectangle Map Selection)
via correct-course (`sprint-change-proposal-2026-07-24-rotated-rectangle-areas.md`).
Motivation: hug a diagonally-oriented range (Belledonne SW–NE) so off-axis valley
stays out of the expensive setup phase. One unified model — axis-aligned rectangle
and square are the `angle=0`/equal-extents cases. Arbitrary free-form polygons
remain **out of scope** (per-query map-drawing cost not justified).