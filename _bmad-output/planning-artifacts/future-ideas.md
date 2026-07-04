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

## App
Make an actual web app with a UI to:
- Pick a center point on a map + radius
- Run the setup + solver
- View result routes
- View results of old runs
