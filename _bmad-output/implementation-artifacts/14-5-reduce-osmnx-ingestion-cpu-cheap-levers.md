# Story 14.5: Reduce osmnx ingestion CPU — cheap levers only

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the osmnx CPU inside the `osm-download` stage reduced by the low-risk levers,
so that setup's post-vectorization dominant CPU cost shrinks without a from-scratch parser.

## Acceptance Criteria

1. **Given** ~141 s of the 289 s r20 `osm-download` stage is **osmnx CPU** (measured, warm HTTP
   cache, handoff §5): raw-graph build ~15 s, truncate-pass-1 ~25 s, largest-component-pass-1 ~29 s,
   `simplify_graph` ~54 s, truncate-pass-2 + largest-component-pass-2 ~13 s — extrapolating to ~15 min
   @ r50 (estimate), and this becomes the **dominant setup CPU cost once 14.1/14.2 land** (elevation
   sampling + the per-edge pipeline loops are already vectorized),
   **when** the cheap levers are investigated **in order** — (a) whether the
   bbox→polygon→`truncate_graph_polygon` **double pass** (two truncate + two largest-component passes,
   ~67 s combined) can be reduced to one for plain-bbox input via lower-level osmnx APIs, and (b) whether
   the second truncate/component pass is redundant for our input — and **any safe reduction is applied**,
   **then** each investigated lever is recorded with its measured effect and its verdict (applied /
   rejected-as-behavior-change / not-worth-it); landing a **reasoned negative result** ("no bit-identical
   cheap lever found; deferred to the 14.6 post-probe correct-course") is an acceptable outcome and is
   **not** a failure of the story (mirrors Story 13.3's decision-or-implement shape).
2. **Given** the bit-identity guardrail (epic preamble: "bit-identity is the default"),
   **when** a lever lands,
   **then** the assembled graph is **bit-identical** where the lever is behavior-preserving — **verified by
   an old-path-vs-new-path graph diff on a real Overpass response** (offline against the warm osmnx HTTP
   cache; see Dev Notes "How to actually verify — goldens are blind to ingestion"), asserting identical
   node set, edge set (u, v, key), and the retained edge attributes (`osm_way_id`, `sac_scale`, `highway`,
   `geometry` coords), **not** by relying on the committed regression goldens (which patch `osm_load` and
   therefore never exercise the real ingestion path).
3. **Given** a lever provably **shifts** ingestion output (graph content differs) but is still judged a
   correct improvement,
   **when** it lands,
   **then** **exactly one** documented golden rebake + fixture regen is taken (the epic's single allowed
   rebake for 14.5): regenerate the committed OSM graphml snapshots (`tests/fixtures/**/regenerate.py`,
   real Overpass fetch), re-run the queryable-cache regen, refresh goldens via `uv run update-regression`,
   and record the equivalence/improvement argument in this story doc + the `update-regression` commit
   message — **never silent** (Story 9.3/12.3 precedent). Architecture **Cat 4c is noted only if on-disk
   content changes** (it does not for an ingestion-only change — geometry storage is unaffected).
4. **Given** the explicit scope fence,
   **when** the story is done,
   **then** the **custom Overpass→graph parser (S5-deep) is out of scope** — recorded as a candidate for
   the post-probe correct-course, to be justified from 14.6's residuals — **and** `retain_all=True` is
   **not** adopted (it keeps unreachable islands → wasted solver iterations) absent a golden evaluation,
   which this story does not undertake.
5. **Given** the epic's measurement discipline (every number is measured-with-a-where or
   labeled estimate/unknown; handoff §9),
   **when** the change lands,
   **then** the osmnx-CPU drop is **measured on a real warm-HTTP-cache r20 re-run** (the osm-download stage
   is **not** offline-benchmarkable — the committed fixtures hold only the post-fetch graphml snapshot, not
   an Overpass response), the before/after `osm-download` CPU split is recorded in the close-out, and any
   residual is handed to the 14.6 probe.

## Tasks / Subtasks

- [x] **Task 1: Confirm the baseline + establish the offline verification harness *before* touching
      `osm_load` (AC: #1, #2, #5)**
  - [x] Confirm the working tree matches `main` (no code changes this story) — the reference state. (Full
        suite not re-run: zero `src/`/`tests/` delta means the code gates are identical to `main`'s green
        state; running the ~4:15 suite for a doc-only outcome adds nothing.)
  - [x] Checked the osmnx HTTP cache: production keys it per-cache-root (`osmnx_cache_dir_for`,
        `cli/setup.py:252`); the handoff's warm 91 MB r20 response is **not** present on this machine (only
        a 0.1 MB small-area response in `./cache`). Recorded — a real r20 measurement would need a fresh
        multi-minute fetch; the grounding experiment used a fast r2 fetch instead (sufficient to settle the
        structural question — see below).
  - [x] Built + ran a diff/trace harness (`scratchpad/probe_osmnx_passes.py`) instrumenting osmnx's
        `truncate_graph_polygon` / `largest_component` / `simplify_graph` around a real `osm_load(r2)` call.
        This is the AC #2 verification surface (goldens patch `osm_load`, so they are blind here).

- [x] **Task 2: Investigate lever (a) — collapse the truncate/component double pass for plain-bbox input
      (AC: #1, #2) → VERDICT: behavior-changing + needs private APIs. Not landed.**
  - [x] Verified the osmnx 2.1.0 call chain from installed source (`graph.py:120/40/410`): 500 m buffer →
        download → `_create_graph` → truncate-pass-1(buffered) → component-pass-1 → `simplify_graph` →
        truncate-pass-2(exact bbox) → component-pass-2 → `count_streets_per_node`.
  - [x] Determined a single pass is **impossible via osmnx's public API** — every public entry
        (`graph_from_point/bbox/polygon`) hardcodes the buffer + double pass. A single pass requires calling
        two **private** internals (`_overpass._download_overpass_network`, `graph._create_graph`) and drops
        the 500 m buffer → changes which boundary ways survive (behavior change, AC #3) **and** adds a
        fragile dependency on osmnx internals that can break on any patch release within `>=2.0,<3`.
  - [x] Not landed: the lever is not bit-identical (AC #2 fails) and the AC #3 path was declined by the user
        after the private-API cost was surfaced (see Completion Notes).

- [x] **Task 3: Investigate lever (b) — is the second truncate/component pass redundant? (AC: #1, #2)
      → VERDICT: NOT redundant (measured). Not landed.**
  - [x] Measured on a real r2 fetch: pass-2 `truncate_graph_polygon` removes **1523 → 851 nodes (−44 %)** —
        it clips the 500 m buffer ring back to the requested bbox — and pass-2 `largest_component` removes a
        further 851 → 844. Pass-2 is structurally necessary (it is what enforces strict bbox containment,
        FR10), **not** a no-op.
  - [x] Skipping pass-2 would return a materially larger graph (buffer ring retained → routes could stray
        outside the search area). Rejected — not bit-identical and violates containment.

- [x] **Task 4: Apply the safe reduction (or record the negative) (AC: #1, #2, #3) → recorded the negative.**
  - [x] N/A — no bit-identical lever survived Tasks 2–3, so nothing applied to `osm_load`. `pipeline/osm.py`
        is untouched.
  - [x] N/A — AC #3 rebake path declined by the user (private-API fragility + modest payoff + `simplify_graph`
        stays). No rebake taken.
  - [x] Recorded the reasoned negative (levers, verdicts, measured pass costs) in Completion Notes and routed
        the real win (S5-deep custom parser) to the 14.6 post-probe correct-course. No risky change forced.
        **Sanctioned outcome per AC #1.**

- [x] **Task 5: Tests + gates (AC: #2, #4)**
  - [x] N/A — no lever landed, so no attribute-contract change to pin beyond the existing
        `tests/unit/test_osm.py` coverage (which already covers `normalize_edges` / `classify_highway` /
        `max_sac_rank`). No new test added (would be speculative for a no-change outcome).
  - [x] N/A — no lever landed, so no `live`-marked parity test to commit. The one-time r2 verification is
        recorded here + in the scratchpad probe.
  - [x] Gates: no `src/`/`tests/` delta → code gates identical to `main` (green). `ruff`/`basedpyright`/
        `pytest` not re-run for a doc-only change (nothing to re-verify).
  - [x] Scope fence intact by construction: no custom-parser module added; `retain_all=False` unchanged in
        `osm_load` (no code touched).

- [x] **Task 6: Content-hash / fixture handling — deliberate, not reflexive (AC: #2, #3)**
  - [x] No `pipeline/osm.py` edit → `compute_pipeline_content_hash()` **unchanged** → no cache
        invalidation, no fixture regen, no golden rebake. (The Story 14.1 containment-lookup finding did not
        even need to be invoked, since the hash never moved.)
  - [x] N/A — AC #3 did not fire (no ingestion-output shift), so no graphml regen / no `update-regression`.
  - [x] Architecture doc-sync skipped per the negative-result branch: no ingestion call-path change (Cat 3
        untouched), no on-disk content change (Cat 4c untouched). The finding is recorded in this story's
        Completion Notes for 14.6 to consume.

## Dev Notes

### What this story is — and its honest shape

This is an **investigate-then-apply-if-safe** story, not a guaranteed-win vectorization like 14.1/14.2.
The target is the ~141 s of osmnx **CPU** (not the ~148 s irreducible Overpass wait) inside the
`osm-download` stage — which becomes setup's dominant CPU cost *after* 14.1 (elevation sampling) and 14.2
(per-edge pipeline loops) land. The AC explicitly sanctions a **reasoned negative** ("no bit-identical
cheap lever found → 14.6"): the double-pass structure below is a correctness feature of osmnx, so a
bit-identical cheap win is genuinely uncertain. **Do not manufacture a win by shipping a behavior change
you can't justify.** Land what's safe; hand the rest to the 14.6 probe.

### The measured double pass (osmnx 2.1.0 — verified from the installed source)

`osm_load` calls `osmnx.graph_from_point(center, dist=r*1000, dist_type="bbox",
custom_filter=_OSM_CUSTOM_FILTER, retain_all=False, simplify=True)`. In osmnx 2.1.0
(`.venv/Lib/site-packages/osmnx/graph.py`) that unwinds to:

```
graph_from_point(dist_type="bbox")           # graph.py:120
  → bbox = bbox_from_point(center, dist)
  → graph_from_bbox(bbox, ...)               # graph.py:40
      → polygon = bbox_to_poly(bbox)
      → graph_from_polygon(polygon, ...)     # graph.py:410
          poly_buff = polygon.buffer(500 m)              # +500 m buffer (projected)
          response = _download_overpass_network(poly_buff, ...)   # the ~148 s wait
          G_buff = _create_graph(response)              # ~15 s @ r20  (806k nodes / 1.64M edges raw)
          G_buff = truncate_graph_polygon(G_buff, poly_buff)      # PASS 1 truncate  ~25 s
          G_buff = largest_component(G_buff)             # PASS 1 component ~29 s
          G_buff = simplify_graph(G_buff)                # ~54 s  (761k → 133k nodes)
          G      = truncate_graph_polygon(G_buff, polygon)        # PASS 2 truncate  ┐
          G      = largest_component(G)                  # PASS 2 component ┘ ~13 s combined
          count_streets_per_node(...) → set street_count
```

**Why the double pass exists (osmnx's own comments):**
- The **500 m buffer** + pass-1-to-buffered-poly: Overpass returns *entire ways* that may include nodes
  outside the requested poly if any one node is inside — buffering keeps that peripheral geometry so the
  later truncate cuts cleanly, and it lets `simplify_graph` run on a **connected** graph.
- Pass-2-to-original-poly (deliberately **not** re-simplifying): shrinks from the buffered extent back to
  the caller's exact bbox, retaining boundary intersections that now connect only two segments. The
  second `largest_component` re-runs "in case the last truncate disconnected anything on the periphery."

**Implication:** the buffer and both passes are load-bearing for boundary correctness. Skipping the buffer
or pass-2 will, in general, **change which edges survive near the bbox boundary** → not bit-identical →
AC #3 territory, not AC #2. Lever (a)/(b) are worth *investigating* (measure how much pass-2 actually
removes on our custom-filtered trail graph — it may be far less than on a full drive network), but treat
bit-identity as something to *prove by diff*, never assume.

### How to actually verify — goldens are BLIND to ingestion (the single most important note)

The committed regression fixtures do **not** exercise the real osmnx ingestion:
`tests/e2e/fixtures/grenoble_small/regenerate_cache.py` and the integration `conftest.seeded_cache`
**patch `osm_load`** to `normalize_edges(osmnx.load_graphml(<committed graphml>))` — a frozen post-fetch
snapshot. The pinned-regression harness (`tests/e2e/test_pinned_regressions.py`) then reads those caches by
**geometric containment**, not by content hash. So:

- A golden rebake would **not** reflect an ingestion change unless you *also* regenerate the committed
  `osm_graph.graphml` snapshots (real Overpass fetch) — which is exactly the AC #3 chain, and why AC #3 is
  "one documented rebake," not "rerun update-regression."
- **The real test of a lever is an old-`osm_load` vs new-`osm_load` graph diff on a live/warm-cache Overpass
  response** (Task 1). Assert equality of: node id set; edge `(u, v, key)` set; and per-edge `osm_way_id`,
  `sac_scale`, `highway`, and `geometry` coordinate sequences (`list(geom.coords)`), after
  `normalize_edges`. That is the AC #2 surface. Nothing in the committed suite substitutes for it.

### Content-hash: shifts, but no fixture regen by default (Story 14.1 finding, re-confirmed)

`osm.py` ∈ `_PIPELINE_CONTENT_GLOBS = ("pipeline/**/*.py", "models.py")` (`cache.py:60`), so **any** byte
edit shifts `compute_pipeline_content_hash()`. Per Story 14.1's verified reasoning, that shift by itself
touches **no committed fixture, test, or golden** (harness reads by containment; content-hash unit tests
use synthetic `"a"*64`). The only effect is a one-time live-cache re-prepare (Cat 4b). **So editing
`osm_load` does not oblige a fixture regen** — regen is triggered *only* by AC #3 (you deliberately refresh
the graphml snapshots because ingestion output changed). Do not reflexively regenerate.

### The osmnx CPU is not offline-benchmarkable — measure on a warm real run

Unlike 14.1/14.2 (committed-fixture pytest-benchmarks), the `osm-download` stage CPU **cannot** be pinned
by a committed benchmark: the repo holds only the *post-fetch, post-simplify* `osm_graph.graphml`, not an
Overpass response to re-ingest. `tests/benchmarks/test_setup_stages.py::test_stage1_standin...` benchmarks
`graphml load + normalize_edges` — explicitly **not** the download/ingest CPU this story targets. So the
AC #5 "measured drop" comes from a **real warm-HTTP-cache r20 re-run** (the handoff's method: the Overpass
response served from the osmnx cache, zero network, so you isolate CPU). Record: before/after `osm-download`
stage wall-clock and the osmnx-internal phase split (read the osmnx INFO-level log timestamps, as the
handoff did). Say "measured, warm cache, r20, center X" — never an unlabeled number (handoff §9).

### Scope guardrails

- **May change:** `src/steeproute/pipeline/osm.py` — only the osmnx-call sequence inside `osm_load`. Keep
  `_validate_area`, `truststore.inject_into_ssl()`, `_ensure_sac_scale_in_useful_tags()`, the
  `DataSourceUnavailableError` wrap (identical exception tuple + message shape — `tests/e2e/
  test_source_unavailable.py` and `tests/unit/test_osm.py` pin it), and `normalize_edges` verbatim. Keep
  the lazy `import osmnx/requests/truststore` inside the fetch path (Story 14.4 follow-up — module-level
  imports there re-inflate every spawned solver worker by ~4 s).
- **Explicitly NOT this story:** the custom Overpass→graph parser (S5-deep — out of scope, → 14.6
  residuals); `retain_all=True` (behavior change, rejected absent golden eval — AC #4); stages 2–9,
  solver, CLI flags (this story adds **no** flag); `cache.py` / on-disk format (Cat 4c untouched unless
  on-disk content changes, which an ingestion-only change does not); DEM fetch (14.3, done).
- No new dependency (osmnx `>=2.0,<3`, currently 2.1.0, already pins the APIs; `truncate` /
  `simplification` / `_overpass` are existing osmnx modules — note `_overpass` is private, so prefer public
  `truncate`/`convert`/`simplification` entry points and treat any private call as a stability risk to flag).

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` **0/0/0**, default
  `uv run --no-sync python -m pytest --cov` (~4:15 typical; markedly slower usually means a test hit the
  network — the ingestion diff must be `live`/`slow`-marked, never in the default run).
- `tests/unit/test_osm.py` is the offline correctness net for the attribute contract (`osm_way_id` rename,
  `geometry` synthesis, `sac_scale` defaulting, `classify_highway`/`max_sac_rank`) — keep every case green;
  a lever must not disturb `normalize_edges`.
- Equality assertions on the ingestion diff use exact `==` on coord sequences and attribute values (FR29
  discipline), never `pytest.approx`.
- **`uv` Windows build flake (recurring, per 14.1–14.4):** after a commit or `pyproject.toml` edit,
  `uv run pytest` / `uv run basedpyright` may hit the corporate-TLS "Failed to canonicalize script path"
  error (~43 `test_cli_smoke` failures). Workaround: `uv run python -m pytest` / `-m basedpyright`, or
  `uv sync --native-tls` once then `uv run --no-sync …`. (See the [[steeproute-typecheck-basedpyright]]
  memory.)
- Benchmarks excluded from the default run (marker `benchmark`); but note this story's real measurement is
  a manual warm-cache run, not a committed benchmark (see above).

### Project Structure Notes

- **Modified (production, if a lever lands):** `src/steeproute/pipeline/osm.py` (`osm_load` internals only).
- **Modified (tests):** `tests/unit/test_osm.py` (attribute-contract pin); optionally a `live`/`slow`-marked
  ingestion-parity test.
- **Modified (docs):** `_bmad-output/planning-artifacts/architecture.md` — Cat 3 stage-1 note *iff* the
  ingestion call path changed; this story doc; `sprint-status.yaml` (14-5 transitions).
- **Fixtures/goldens:** untouched by default (content-hash shift needs no regen — see above); regenerated
  **only** under AC #3, as one documented batch.
- **Content hash:** shifts (any `osm.py` edit) → one-time live-cache re-prepare, **no committed-fixture
  action** unless AC #3 fired.

### References

- [Source: epics.md §Story 14.5 + §Epic 14 preamble](_bmad-output/planning-artifacts/epics.md) — AC
  source-of-truth: cheap levers in order (double-pass reduction, redundant second pass), any safe reduction
  applied; bit-identical where behavior-preserving else **one** documented rebake + fixture regen (never
  silent); custom parser out of scope → post-probe correct-course; `retain_all=True` not adopted without
  golden eval; Cat 4c noted only if on-disk content changes; epic's "one contingent documented rebake
  allowed at 14.5" and "bit-identity is the default guardrail."
- [Source: research/steeproute-next-optimization-pass-handoff-2026-07-05.md §5 S5 (lines 217-262), §2
  baseline (osm-download 289 s = ~141 s CPU + ~148 s wait; phase table), §4 constraints (content-hash,
  bit-identity, never-`np.sum`-silently, `uv` Windows), §8 r50 probe, §9 failure
  modes](_bmad-output/planning-artifacts/research/steeproute-next-optimization-pass-handoff-2026-07-05.md)
  — the measured osmnx-CPU split, the exact lever descriptions (bbox double-pass, `retain_all` behavior
  change, the S5-deep parser payoff ceiling + why it's deferred), the warm-cache measurement method, and
  the r50 Overpass-size risk (91 MB / 806k nodes @ r20 → ~×6 @ r50) to hand to 14.6.
- [Source: src/steeproute/pipeline/osm.py:58-121](src/steeproute/pipeline/osm.py) — `osm_load` (the only
  function this story changes): the `graph_from_point(dist_type="bbox", custom_filter=_OSM_CUSTOM_FILTER,
  retain_all=False, simplify=True)` call, the deferred fetch-stack imports (keep lazy — 14.4 follow-up), the
  `DataSourceUnavailableError` wrap + exact exception tuple/message to preserve, `_validate_area`,
  `_ensure_sac_scale_in_useful_tags`, `normalize_edges` (attribute contract — keep verbatim).
- [Source: .venv/Lib/site-packages/osmnx/graph.py:40-217, 410-533] — the **verified** osmnx 2.1.0 call
  chain: `graph_from_point` → `graph_from_bbox` → `graph_from_polygon` (500 m buffer, download,
  `_create_graph`, truncate-pass-1 + component-pass-1, `simplify_graph`, truncate-pass-2 + component-pass-2,
  `count_streets_per_node`) with osmnx's own comments explaining why the buffer + double pass exist
  (boundary-way completeness, simplify-on-connected). Read this to scope levers (a)/(b) against the real
  code, not a summary.
- [Source: src/steeproute/cache.py:60, 168-186](src/steeproute/cache.py) — `_PIPELINE_CONTENT_GLOBS`
  (`osm.py` is in it → content-hash shift on any edit) and `compute_pipeline_content_hash`.
- [Source: _bmad-output/implementation-artifacts/14-1-vectorize-elevation-sampling.md §"Why goldens are
  safe"](_bmad-output/implementation-artifacts/14-1-vectorize-elevation-sampling.md) — the re-usable
  finding that a `pipeline/**` content-hash shift needs **no** fixture regen because the harness reads
  fixture caches by geometric containment (`check_coverage` → `_select_smallest_containing`), not by
  re-deriving the hash. Same reasoning applies verbatim here.
- [Source: tests/e2e/fixtures/grenoble_small/regenerate_cache.py:48-75](tests/e2e/fixtures/grenoble_small/regenerate_cache.py)
  + [tests/fixtures/grenoble_small/regenerate.py](tests/fixtures/grenoble_small/regenerate.py) — proof that
  fixture-cache regen **patches `osm_load`** to load the committed graphml (ingestion is never exercised by
  goldens); the real-Overpass `regenerate.py` (calls `graph_from_point` directly — would itself need
  updating if the ingestion call path changes and you take the AC #3 rebake). Only grenoble_small has a
  committed queryable-cache regen script; belledonne/vercors/chartreuse do not (surface this to the user
  if AC #3 fires).
- [Source: tests/e2e/test_pinned_regressions.py](tests/e2e/test_pinned_regressions.py) +
  [src/steeproute/regression.py:138-169](src/steeproute/regression.py) — the four regression roots
  (grenoble_small, belledonne, vercors, chartreuse) and the `update-regression` rebake workflow (used only
  under AC #3).
- [Source: tests/benchmarks/test_setup_stages.py:5-21, 58-62](tests/benchmarks/test_setup_stages.py) — the
  explicit note that the stage-1 benchmark is a graphml-load stand-in, **not** the Overpass ingest CPU this
  story targets (hence the manual warm-cache measurement, AC #5).
- [Source: tests/unit/test_osm.py](tests/unit/test_osm.py) + [tests/e2e/test_source_unavailable.py](tests/e2e/test_source_unavailable.py)
  — the offline attribute-contract + `DataSourceUnavailableError` pins the lever must keep green (patch
  targets are `osmnx`/`truststore` directly since the 14.4 lazy-import change).
- [Source: _bmad-output/implementation-artifacts/14-4-parallel-grasp-restarts.md](_bmad-output/implementation-artifacts/14-4-parallel-grasp-restarts.md)
  — the immediately-prior story; the lazy fetch-stack imports in `osm.py` originate there (keep them), and
  its close-out records the r20 setup share (~68 s single-threaded setup) this story chips at.
- [Source: architecture.md §Cat 3 (lines 234-276), §Cat 4c (line 322)](_bmad-output/planning-artifacts/architecture.md)
  — the pipeline stage table (stage 1 = OSM load via osmnx) to note if the call path changes, and Cat 4c
  (on-disk format — touch only if on-disk content changes, which it does not here).

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Grounding experiment — `scratchpad/probe_osmnx_passes.py` (real r2 `osm_load`, osmnx 2.1.0, live
Overpass, 2026-07-14).** Instrumented `truncate_graph_polygon` / `largest_component` / `simplify_graph`
to log node/edge counts + timings in call order:

```
[1] truncate_graph_polygon: 14586->13149 nodes, 29712->26776 edges  (0.57s)   # pass 1 (to buffered poly)
[2] largest_component:      13149->12862 nodes, 26776->26274 edges  (0.72s)   # pass 1
[3] simplify_graph:         12862->1523  nodes, 26274->3703  edges  (1.32s)
[4] truncate_graph_polygon: 1523->851    nodes, 3703->2088  edges  (0.05s)    # pass 2 (to EXACT bbox)  −44% nodes
[5] largest_component:      851->844      nodes, 2088->2086  edges  (0.03s)   # pass 2
final: 844 nodes, 2086 edges; osm_load total 7.30s
```

The decisive number is step [4]: **pass-2 truncate removes 44 % of the post-simplify nodes** — the 500 m
buffer ring being clipped back to the requested bbox. At r2 the CPU is tiny; the structure is
scale-invariant (the same buffer-then-clip shape drives the ~67 s of truncate/component + the 54 s
`simplify_graph` measured at r20 in the handoff).

**osmnx 2.1.0 API surface check** (`.venv/.../osmnx/`): the download + graph build are both **private**
(`_overpass._download_overpass_network`, `graph._create_graph`); only `truncate_graph_polygon` /
`largest_component` / `simplify_graph` are public. Every public graph-download entry
(`graph_from_point/bbox/polygon`) hardcodes the 500 m buffer + double pass internally — so a single pass
is unreachable without the two private functions.

**Working tree:** `git status` shows only `sprint-status.yaml` + this story doc modified; **zero
`src/`/`tests/` delta** — code gates are identical to `main`'s green state, so they were not re-run.

### Completion Notes List

**Outcome: measured negative result — no production code change (AC #1 sanctioned).** Story 14.5 asked
for the *cheap* osmnx-ingestion levers only. Both candidate levers were investigated and rejected on
evidence:

- **Lever (b) — "is the second truncate/component pass redundant?" → No, measured.** Pass-2 removes 44 %
  of post-simplify nodes (the buffer ring); it is what enforces strict bbox containment (FR10). Skipping
  it returns a larger, non-contained graph. Not bit-identical.
- **Lever (a) — "collapse the double pass to one for bbox input?" → behavior-changing + private-API.** A
  single pass is impossible through osmnx's public API; it requires two private osmnx internals
  (`_overpass._download_overpass_network`, `graph._create_graph`) and dropping the 500 m buffer, which
  changes which boundary ways survive. The heavy cost — `simplify_graph` (~54 s @ r20) — runs on the
  buffered graph regardless and is **inside** osmnx, so even this lever leaves it untouched (payoff
  ~15–20 s of the 141 s osmnx CPU).

**User decisions (two AskUserQuestion rounds, 2026-07-14).** (1) Presented the finding that no
bit-identical lever exists; user first authorized the behavior-changing single-pass + rebake. (2) On
surfacing the newly-discovered constraint that a single pass requires **two private osmnx APIs** (fragile
across patch releases within the `>=2.0,<3` pin, and drifting into the 14.6 custom-parser scope) for a
modest payoff, the user chose to **record the negative and defer the real win to 14.6**. No production
code was changed; `pipeline/osm.py` is untouched.

**Consequences of "no code change":** pipeline content hash unchanged → no cache invalidation, no fixture
regen, no golden rebake, no architecture-doc edit (Cat 3 / Cat 4c untouched). Scope fences trivially held
(no custom-parser module; `retain_all=False`).

**Hand-off to Story 14.6.** The genuine osmnx-CPU win is the **S5-deep custom Overpass→graph parser**
(handoff §5 S5): parse the cached Overpass JSON directly into chained edges, never materializing the
~806 k raw nodes and never paying `simplify_graph`'s ~54 s (a way is already a chain). It is explicitly
out of scope here and belongs in the post-probe correct-course, justified from 14.6's r50 residuals. The
`simplify_graph`/truncate cost this story could not touch is the primary evidence that motivates it.

### File List

**No production or test code changed** (this story's outcome is a documented investigation).

**Modified (docs/tracking):**
- `_bmad-output/implementation-artifacts/14-5-reduce-osmnx-ingestion-cpu-cheap-levers.md` — this file
  (tasks marked, Dev Agent Record, outcome banner, status → review).
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 14-5 backlog → ready-for-dev → in-progress
  → review.

**Not committed (investigation scratch):**
- `scratchpad/probe_osmnx_passes.py` — the osmnx pass-trace harness (evidence for the negative result;
  under the session scratchpad, not the repo tree).

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-14 | Yann (Claude Opus 4.8) | Story 14.5 drafted (create-story workflow). Investigate-then-apply-if-safe scope for the osmnx-CPU levers; verified the osmnx 2.1.0 double-pass structure from installed source; codified that goldens are blind to ingestion (patched `osm_load`) so verification is a real-fetch old-vs-new diff, and that a content-hash shift needs no fixture regen by default (Story 14.1 finding). Ready for dev. |
| 2026-07-14 | Yann (Claude Opus 4.8) | Story 14.5 dev — **measured negative result, no production code change** (AC #1 sanctioned). Grounded the investigation with a real r2 `osm_load` pass-trace (`scratchpad/probe_osmnx_passes.py`): pass-2 truncate removes 44 % of post-simplify nodes (the 500 m buffer ring → bbox containment), so lever (b) is not redundant; lever (a) single-pass is impossible via osmnx's public API and needs two private internals (`_overpass._download_overpass_network`, `graph._create_graph`) plus dropping the buffer (behavior change) while `simplify_graph`'s ~54 s stays inside osmnx. Two user decisions: authorized then, on the private-API fragility being surfaced, declined the behavior-changing single-pass + rebake and chose to defer the real win (S5-deep custom parser) to the 14.6 post-probe correct-course. `pipeline/osm.py` untouched → content hash unchanged → no fixture regen / no golden rebake / no architecture edit. Status → review. |
| 2026-07-14 | Yann | User accepted the measured-negative outcome without further review; no code to review. Story marked done. Story 14.6 (r50 probe) and epic 14 deferred in `sprint-status.yaml` — user is pausing performance work to pick up `future-ideas.md` items next; 14.6 remains the entry point back into this epic (via correct-course) whenever resumed. |

## Open Questions (for the user, before dev)

1. **Acceptable to land a negative result?** The osmnx buffer + double-pass is a correctness feature (§"The
   measured double pass"), so a *bit-identical* cheap win is genuinely uncertain — the most likely honest
   outcome is "no safe bit-identical lever; the boundary-correctness passes can't be dropped without
   changing output; deferred to the 14.6 probe / S5-deep parser." AC #1 sanctions this. **Confirm you're
   fine with the story potentially concluding in a measured negative rather than a shipped speedup** (the
   alternative — taking the AC #3 single rebake for a behavior-changing lever — is available but changes
   real ingestion output near the bbox boundary and should be your explicit call, not the dev's).
2. **If AC #3's rebake becomes necessary,** only `grenoble_small` has a committed queryable-cache regen
   script; belledonne/vercors/chartreuse have committed `graph.pkl`s but no in-repo regen script and their
   real-Overpass source snapshots may need re-fetching. Do you want the dev to (a) author the missing regen
   scripts, or (b) stop and hand you the regen as a separate task? (Recommend (b) — a four-root golden
   rebake is a distinct, reviewable change.)
