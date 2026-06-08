# steeproute — Future Ideas

Running backlog of post-v1 improvements. Low-friction: append new ideas to the bottom as they occur. Not committed scope, not sequenced — these get promoted into epics/stories (or a correct-course) if and when they're picked up.

Format per idea: a short title, what it does, and any notes on rationale or approach.

---

## 1. Flag to force routes to start at a road/trail junction

Add a flag that constrains route start points to junctions between a road and a trail (rather than any node in the graph).

**Why:** practical starting points — where you'd realistically park or transition from road onto trail.

---

## 2. Maximum descent-slope flag (direction-aware)

Add a flag capping the slope of *descents*. A downhill trail above ~40% average slope is unpleasant; above ~50–60% it gets dangerous — yet the same segment is fine going *up*.

**Behavior:** a route may only include a segment in the descending direction if its average slope (over a configurable distance window) stays at or below the threshold *measured in the uphill direction*. Segments too steep to descend safely remain eligible as climbs.

**Notes:**
- Direction-aware constraint — distinct from the existing route-level average-slope floor (FR3) and climb-detection threshold (FR3b), which are about minimums, not descent maximums.
- Needs a distance window for the running average, similar to climb detection.

---

## 3. Strategies for feasible search over larger areas

Make searching large areas tractable within a reasonable time budget. Fuzzy/wide item — an umbrella for time-vs-area scaling techniques rather than one feature.

**Candidate approach (coarse-to-fine):**
- Run the solver many times on the full large area with low iteration counts and varied seeds.
- Gather the most frequently recurring candidate routes/regions across those runs.
- Re-run the solver with higher iteration counts on smaller sub-areas centered on those candidates.

**Notes:**
- Explore other strategies too — this is one idea, not a decided design.
- Interacts with the area-size cap (FR2) and the time-budget / progress reporting machinery.
