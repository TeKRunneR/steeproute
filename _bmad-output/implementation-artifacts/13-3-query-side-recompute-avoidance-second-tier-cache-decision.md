# Story 13.3: Query-side recompute avoidance (second-tier cache decision)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want repeat queries to stop re-running unchanged pipeline work,
so that the `filter_trails` redux and stages 8–9 (~13% of large-area wall-clock pre-13.1/13.2) stop being
paid on every invocation — if the post-13.1/13.2 numbers still justify the added cache complexity.

## Acceptance Criteria

1. **Given** the stage 1–5 cache is keyed independent of query knobs by design (Stories 6.1/6.3), so
   `filter_trails` redux and stages 6–9 re-run per query, **when** the cache-boundary options are weighed
   with the post-13.1/13.2 phase split as input (e.g. a light second cache tier keyed on the query knobs, or
   moving the stage-2 redux setup-side) and the chosen option is implemented — or the story records a
   reasoned decision *not* to, if the remaining share no longer justifies the added cache complexity,
   **then** repeat-query wall-clock on the reference workloads reflects the decision, results identical,
   goldens untouched.
2. If a second cache tier ships: writes are atomic (Category 4d pattern), its key includes every input
   affecting the cached stages, and architecture Category 3b is updated.
3. Whichever way the decision goes, it is recorded with the fresh measurements that back it (a documented
   no-go in the architecture doc satisfies the story as fully as a shipped cache).

## Tasks / Subtasks

- [x] Task 1: Measure the current (post-13.1/13.2) cost of the avoidable work (AC: #1, #3)
  - [x] Read the `trail-filter`, `climb-detection`, `climb-contraction` (and, for context,
    `elevation-reshape`) stage lines off both reference workloads — the query CLI has printed per-stage
    wall since 13.1 (`StageProgress` seam), so no profiler is needed for this
  - [x] Do NOT reuse the epic's ~13% / ~2.9 s / ~2.4 s figures — those are shares of the pre-13.1/13.2
    40.05 s run; 13.1/13.2 shrank the denominator, so every surviving stage's share moved even where its
    absolute cost didn't
- [x] Task 2: Weigh the options and commit to a decision (AC: #1, #3)
  - [x] Enumerate against the fresh numbers: (a) second-tier cache of a query-side artifact keyed on the
    knobs that produce it, (b) moving/cheapening the stage-2 redux setup-side, (c) reasoned no-go. Dev
    Notes give the two design traps that bound option (a)'s real value — read them before costing it
  - [x] Weigh realistic hit rate too, not just per-hit savings: a hit requires the user to repeat the same
    area AND every keyed knob verbatim; the primary user sweeps knobs between runs (that is why the
    stage 1–5 cache was made knob-independent in the first place)
- [x] Task 3: Implement the chosen option (skip if no-go) (AC: #1, #2) — skipped per task definition:
  Task 2's decision is a reasoned no-go (see Dev Agent Record); the three subtasks below are the go-branch
  design constraints and did not execute
  - [x] Key composition: every input affecting the cached artifact's content — see Dev Notes for the full
    dependency chain (wider than the filter/detect/contract knobs alone) — N/A (no-go)
  - [x] Atomicity: reuse `cache.py`'s existing primitives (`write_text_atomic`, `write_json_atomic`, the
    `write_entry` staged sequence) — do not re-implement the Category 4d pattern — N/A (no-go)
  - [x] A cache hit must be output-identical to the recomputed path (content-identical route JSONs on the
    reference workloads is the proof; goldens stay untouched either way) — N/A (no-go)
- [x] Task 4: Gates + measurement close-out (AC: #1, #3)
  - [x] `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default
    `uv run pytest --cov` green, regression goldens untouched
  - [x] If go: before/after repeat-query wall on both reference workloads in the Dev Agent Record; if
    no-go: the Task 1 measurements and the rationale, in the same place (no-go branch: recorded)
- [x] Task 5: Doc sync (AC: #2, #3)
  - [x] Update `architecture.md` §Category 3b (and §Category 4 if a tier ships) with the decision — go
    with design, or no-go with rationale (no-go recorded in §3b)
  - [x] Update sprint-status

## Dev Notes

### This is a decision story — the no-go branch is a first-class outcome

The epic sequenced 13.3 *after* 13.1/13.2 precisely because their outcome changes its cost-benefit
(epics.md §Epic 13 preamble). 13.1 cut stage 6 ~2.1× and 13.2 cut `read_entry` ~2.2×; the whole-run
denominator moved from 40.05 s to ~33 s. Measure first (Task 1), then decide. A no-go recorded with numbers
is a complete, successful story — do not ship a cache to avoid writing a paragraph.

### What re-runs per query, and on which knobs

Setup pins `difficulty_cap="T6"` (`_SETUP_DIFFICULTY_CAP`, `pipeline/__init__.py:80`) so the stage 1–5
cache omits it (Architecture §3b). Query-side, in order (`cli/query.py:255-354`):

- `elevation-reshape` — `operationalize_graph` (stages 6–7): depends on `--elevation-smoothing`,
  `--elevation-deadband`. Post-13.1 ~7.4 s at r10, of which `graph.copy()` ~2.6–3.9 s and
  `compute_edge_metrics` ~3.3 s (13.1 close-out).
- `trail-filter` — `filter_trails` redux (`pipeline/osm.py:141-190`): O(edges) re-scan; depends on
  `--difficulty-cap`, `--untagged-trails`. Query-time `--untagged-trails` can only narrow what setup kept
  (setup's value is in the Category 4b key), never widen.
- `climb-detection` — `detect_climbs` (`pipeline/climbs.py:218`): `--min-climb-slope`,
  `--min-climb-ground-length`.
- `climb-contraction` — `contract_climbs` (`pipeline/graph.py:72`): `--l-connector`,
  `annotate_junctions=--start-at-junction`.

`--theta` and `--j-max` are solve/validate-time only — they must NOT appear in any pipeline-artifact key.

### Trap 1 — the key is wider than the stages being cached

The natural artifact to cache is the post-stage-9 `ContractedGraph` (what the solver consumes). But its
edge metrics (`d_plus_m`, `length_m`, …) were baked in by stage 7 *before* `filter_trails` ran — so they
depend on `--elevation-smoothing`/`--elevation-deadband` too. A key covering only the
filter/detect/contract knobs silently serves wrong metrics when only a smoothing knob changed. A correct
key for a post-contraction artifact = base entry's `cache_key_hash` + all eight non-solver knobs
(`elevation_smoothing`, `elevation_deadband`, `untagged_trails`, `difficulty_cap`, `min_climb_slope`,
`min_climb_ground_length`, `l_connector`, `start_at_junction`). Eight knobs verbatim is a narrow hit
condition — feed that into Task 2's hit-rate reasoning.

### Trap 2 — render needs the operational graph, so caching the contracted graph does not skip stages 6–7

`output.render` reads geometry from the full `operational_graph`, not the contracted one
(`cli/query.py:276-278, 316-318` — deliberately, so FR28 failed-route rendering never loses an edge's
geometry). So a second tier that caches only the `ContractedGraph` still runs `operationalize_graph` every
query: the avoidable work is trail-filter + stages 8–9 only, NOT the elevation-reshape block. Skipping
stages 6–7 as well would mean caching the operational graph itself — an artifact the size of the prepared
entry, per knob combination, whose read-back would cost roughly what Story 13.2 just spent effort cutting
(and whose write cost is paid on every miss). Cost both variants honestly in Task 2 rather than assuming
"cache the pipeline output" is one option.

### If a tier ships: machinery to reuse

`cache.py` owns the cache directory (sole reader/writer — 13.2 kept that invariant) and already has the
Category 4d primitives: `write_text_atomic` (`cache.py:390`), `write_json_atomic` (`cache.py:415`), and
`write_entry`'s staged tmp-dir → `os.replace()` → commit-signal-last sequence (`cache.py:528-605`).
`sha256_canonical` (`cache.py:111`) is the existing canonical-JSON hasher for key composition. A
content-identity test in the spirit of 13.2's exhaustive roundtrip (every node/edge/attr, not one sample)
is the right proof shape — a stale or mis-keyed hit returns wrong routes with no exception to catch.

### Measurement anchors

Reference workloads (Phase-3 doc provenance): Chamrousse r6.0 — seed 42, n 3, T4, deadband 1, j-max 0,
200k iters; Chartreuse r10 — seed 44, n 10, l-connector 50, smoothing 50, descent-cap 0.4,
start-at-junction, 200k iters. Pre-13.1/13.2 attribution at r10 (against 40.05 s): `filter_trails` redux
~7.3% (~2.9 s), stages 8–9 ~5.9% (~2.4 s). Post-13.1 whole-run anchor: 33.56 s; 13.2's stage gain sits
inside ±3 s solver run-to-run noise, so per-stage lines (not whole-run wall) are the stable metric — the
13.2 close-out says the same.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default
  `uv run pytest --cov` (~4:15 typical; much slower usually means a test hit the network).
- `uv` Windows flake: stale editable build after a commit/pyproject edit → corporate-TLS cert error
  (~43 `test_cli_smoke` failures). Fix once with `uv sync --native-tls`, then `uv run --no-sync`.

### Project Structure Notes

- **If go:** `src/steeproute/cache.py` (second tier lives with the existing cache machinery unless there's
  an articulated reason not to), `src/steeproute/cli/query.py` (hit/miss wiring inside the existing
  `StageProgress` seam), new unit/integration tests, `architecture.md` §3b/§4, sprint-status.
- **If no-go:** `architecture.md` §3b (record the decision + numbers), sprint-status. No production code.
- **Untouched either way:** stage 1–5 cache key composition (§4b), the schema-v2 entry format (13.2),
  `graph_smooth_elevation` internals (13.1), solver, validator, output rendering, `PreparedData` API.
- Out of scope: Story 13.4 (lazy imports), Story 13.5 (consolidated re-measure — it consumes this story's
  numbers, so record them cleanly).

### References

- [Source: epics.md §Epic 13 preamble + §Story 13.3](../planning-artifacts/epics.md) — AC source-of-truth;
  sequencing rationale ("13.3 … deliberately sequenced after them because their outcome changes its
  cost-benefit")
- [Source: research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md §phase-split table +
  §"What next" item 3](../planning-artifacts/research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md)
  — the ~13% attribution and "cache-boundary design question, not a compute problem" framing
- [Source: architecture.md §Category 3b](../planning-artifacts/architecture.md) — why stages 6–9 are
  query-side and the cache is knob-independent (the design this story revisits at the margin)
- [Source: architecture.md §Category 4b/4d](../planning-artifacts/architecture.md) — base key composition;
  the atomic-write pattern any new tier must follow
- [Source: src/steeproute/cli/query.py:255-354](src/steeproute/cli/query.py) — the query-side stage
  sequence, knob→stage mapping, and render's operational-graph dependency
- [Source: src/steeproute/pipeline/__init__.py:80,220](src/steeproute/pipeline/__init__.py) —
  `_SETUP_DIFFICULTY_CAP` pinning; `operationalize_graph`
- [Source: src/steeproute/pipeline/osm.py:141-190](src/steeproute/pipeline/osm.py) — `filter_trails` redux
- [Source: src/steeproute/pipeline/climbs.py:218](src/steeproute/pipeline/climbs.py) /
  [graph.py:72](src/steeproute/pipeline/graph.py) — stage 8/9 parameter dependence
- [Source: src/steeproute/cache.py:111,390-605](src/steeproute/cache.py) — `sha256_canonical`,
  atomic-write primitives, `write_entry` staged sequence
- [Source: _bmad-output/implementation-artifacts/13-1-vectorize-query-side-elevation-smoothing.md](13-1-vectorize-query-side-elevation-smoothing.md)
  — post-13.1 anchors (33.56 s; reshape-block breakdown), the stage-line seam Task 1 reads
- [Source: _bmad-output/implementation-artifacts/13-2-faster-cache-entry-deserialization.md](13-2-faster-cache-entry-deserialization.md)
  — deserialization cost of a large graph artifact (Trap 2's read-back cost), atomicity/identity-test
  precedent

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Task 1 stage-line measurements (2 runs each, means shown; params identical to the Phase-3 doc's
reference workloads, warm `.trial-cache`, `--stagnation-iters 0`):**

```
                        Chartreuse r10          Chamrousse r6
                        (wall 30.95/30.51 s)    (wall 12.00/12.63 s)
load-prepared-area      1.32 s                  0.13 s
elevation-reshape 6-7   6.76 s                  0.90 s
trail-filter redux      2.23 s                  0.08 s
climb-detection  (8)    0.28 s                  0.05 s
climb-contraction(9)    2.56 s                  0.33 s
validate-render         1.63 s                  0.12 s
----------------------------------------------------------------
filter+8-9 block        5.07 s (16.5%)          0.45 s (3.7%)
  vs pre-13.1/13.2:     ~5.3 s (13% of 40.05)   —
+ reshape (full tier)   11.83 s (38.5%)         1.35 s (11%)
```

Chamrousse entry re-prepared into `.trial-cache` first (setup 43.08 s live, cache
`4882db4d7833978e` — the 12.4 scratch cache was gone). Chartreuse r10 entry `c5bbfb802f3dc22f`
(60,110 nodes / 152,578 edges, schema v2 since 13.2).

**Gates (all green, zero production-code changes):**

```
ruff check / ruff format --check   → clean (107 files)
basedpyright (whole project)       → 0 errors, 0 warnings, 0 notes
uv run pytest --cov (default)      → 849 passed, 12 deselected in 3:19 (96% cov;
                                     goldens untouched — no fixture or golden file changed)
git status                         → planning-artifacts + story + sprint-status only
```

### Completion Notes List

**Decision (Tasks 1–2, AC #1/#3): NO-GO on the second cache tier — the recompute-per-query design
stands.** The fresh numbers and two integration facts tipped it:

1. **The "light" tier is capped at ~4 s net (13%) at r10, ~0.3 s at r6.** Trap 2 held under
   measurement: `output.render` reads geometry from the full operational graph, so caching only the
   `ContractedGraph` leaves stages 6–7 (6.76 s) running every query. The avoidable filter+8–9 block is
   5.07 s gross, minus ~1 s read-back for a graph-sized artifact (13.2 measured 1.12 s for the base
   graph at the same scale).
2. **The headline ~9.5 s (31%) requires persisting the operational graph too** — ~70 MB per
   combination of eight knobs, on top of the contracted artifact, plus GC and a base-content-identity
   key component (`--force-refresh` rewrites the same `cache_key_hash` with fresh OSM; a tier keyed on
   the hash alone would serve stale graphs). Not "light" by any reading.
3. **Golden-harness interaction (found during Task 2, decisive):** `regression.run_fixture` invokes the
   query CLI against the **committed** fixture cache roots (`src/steeproute/regression.py:314`) — an
   always-on tier would write into `tests/e2e/fixtures/*/cache/` during every test run (untracked
   files in the repo tree), and repeat golden runs would validate tier-2 reads instead of exercising
   stages 6–9, masking pipeline regressions locally. Avoiding that means a disable flag or a separate
   cache root — more product surface for a hobby tool.
4. **Every miss gets slower** by the two-graph serialization cost (estimated 2–5 s at r10) — and knob
   sweeps, the exact workflow the knob-independent stage 1–5 cache was designed to serve, are all
   misses.
5. **Most of the cacheable time is compute-shaped, contradicting the research doc's framing.** 13.1's
   close-out already split the reshape block into `graph.copy()` (~3 s, purity contract) and
   `compute_edge_metrics` (~3.3 s, per-edge Python loop); `filter_trails` (2.23 s) is likewise a plain
   O(edges) loop. Both are amenable to the 13.1 vectorization treatment — benefiting every run
   unconditionally, with no key/staleness/masking machinery — and shipping that later would halve the
   tier's value retroactively. Only `contract_climbs` (~2.56 s, graph building) is genuinely
   cache-or-nothing, and 2.5 s does not carry a cache subsystem.

Option (b) (moving/cheapening the stage-2 redux setup-side) was dominated: the redux is 2.23 s of the
block, and pre-classifying setup-side is a subset of the vectorization follow-on anyway.

**Hit-rate note (honest pro-go argument, recorded for balance):** the eight keyed knobs are
recipe-stable in practice while the frequently-swept knobs (seed, θ, n, budgets) are all solver-side —
so a tier's hit rate would likely have been high for the quality-recipe workflow. It lost on the size
of the net win at currently-supported scales and on costs 1–4, not on hit rate.

**Scale caveat (user correction at review):** r6/r10 are test-budget areas, not typical usage — the
long-term ambition is whole-range areas (r50–100 km). The recomputed block scales with edge count
(≈ r²): at r50 it would be minutes per query and a second tier clearly wins. The no-go holds within
the current `--area-cap` envelope (500 km² ≈ r12.6); a whole-range epic should revisit it.

**AC #2/#3 disposition:** AC #2 (atomicity/key/Category-3b-update) applies only to a shipped tier —
its architecture-update clause is satisfied by the no-go record; AC #3 satisfied by the §3b decision
paragraph + this record. Repeat-query wall-clock "reflects the decision" trivially: unchanged, as
measured in Task 1 (the Task 1 runs are themselves repeat queries).

**For Story 13.5's what-next:** the measured follow-on lever is vectorizing `compute_edge_metrics`
(~3.3 s) and the `filter_trails` redux (~2.2 s) via correct-course — ~5 s more off every large-area
run without cache complexity; `graph.copy()` (~3 s) and `contract_climbs` (~2.6 s) are the residue
after that.

### File List

**Modified:**
- `_bmad-output/planning-artifacts/architecture.md` — §Category 3b: added the second-tier
  considered-and-declined decision record with measurements and rationale.
- `_bmad-output/implementation-artifacts/13-3-query-side-recompute-avoidance-second-tier-cache-decision.md`
  — this file.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.

**No production code, test, or golden changes** (the no-go branch; verified via `git status` at
close-out).

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-04 | Yann (Claude Fable 5) | Story 13.3 resolved as a reasoned NO-GO on the second-tier query-side cache. Fresh stage-line measurement (Chartreuse r10 / Chamrousse r6, 2 runs each): the cacheable filter+8–9 block is 5.07 s (16.5%) / 0.45 s (3.7%); a contracted-graph tier nets only ~4 s at r10 because render needs the operational graph, and the full-tier variant costs ~70 MB/knob-combo + miss-penalty + a golden-harness masking hazard (tests run against committed fixture cache roots). Most of the block is vectorizable Python loops (stage-7 metrics ~3.3 s, filter redux ~2.2 s) — recorded as the follow-on lever for 13.5. Architecture §3b updated with the decision; zero production-code changes. |
| 2026-07-05 | Yann | Closed out. At review, the user pointed out the recorded hit-rate/scale framing needed correction (his real workflow re-keys the cache on nearly every run; r6/r10 are test-budget areas, not the target scale) — both corrections were folded into this doc and `architecture.md` §3b before close. Stories 13.4/13.5 deferred (sprint-status.yaml) in favor of a broader, better-measured r50-scale optimization plan from a separate session (`research/steeproute-next-optimization-pass-handoff-2026-07-05.md`), to be brought in via its own correct-course. Status → done. |
