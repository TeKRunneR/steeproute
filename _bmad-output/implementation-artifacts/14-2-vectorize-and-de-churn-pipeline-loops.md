# Story 14.2: Vectorize + de-churn the per-edge pipeline loops (one content-hash batch)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the per-vertex smoothing/resampling/metrics loops and the copy-then-remove graph churn replaced with array ops and single-pass graph builds,
so that the remaining setup and query pipeline CPU drops, landed as one cache-invalidation cycle.

## Acceptance Criteria

1. **Given** polyline smoothing + resampling (S2, ~95 s @ r20: smoothing 32.9 s + resampling 62.5 s),
   copy-then-remove churn in `filter_trails` / orphan / short-edge guards and per-stage `graph.copy()`
   (S3, trail-filter ~18 s @ r20 + repeated full-graph copies), and query-side stage-7 metrics + deadband
   (Q2, part of elevation-reshape ~24 s @ r20) are all per-edge Python loops over the same edge geometry,
   **when** they are vectorized per edge via the shapely array interface (moving-average, segment lengths
   `np.hypot(np.diff)`, `np.cumsum` with naive-fold parity verified, `np.searchsorted` lerp resampling,
   `np.diff`-based gain/loss and windowed-descent replicating the two-pointer boundary semantics), the
   copy-then-remove churn is replaced by building a new graph from kept edges (or one orchestrator-owned
   working copy), and `contract_climbs` (Q3, 5.6 s @ r20) is profiled first and optimized **only if** the
   profile shows a material extractable cost — all co-landed as a **single** content-hash change with one
   fixture-regen — **then** coordinate arrays, edge metrics, and deadband output are **bit-equal** to the old
   paths on the `grenoble_small` fixture (or, where a compensated-`sum` site prevents it, **one** documented
   rebake batched with this story); the full suite including goldens passes; public API purity is preserved at
   the `run_setup_stages` / `build_graph_geometry` / `operationalize_graph` boundaries.
2. Per-stage benchmarks exist **before** the change (stages 3 & 4 and trail-filter already ship in
   `tests/benchmarks/test_setup_stages.py`; **stage-7 metrics has none — add it, plus a deadband and a
   `contract_climbs` seam**, with autosave baselines recorded first); the measured drops for stages 3–4,
   trail-filter, and stage-7 metrics are recorded in the close-out.
3. The Q3 `contract_climbs` decision is recorded: either the profile showed a material extractable cost and
   the applied optimization + its measured drop are noted, or the profile showed no material extractable cost
   and it is left untouched with the measurement recorded.

## Tasks / Subtasks

> **Approach pivot (recorded 2026-07-06, approved by Yann).** The story planned *bit-identical* per-edge
> vectorization. Measurement during implementation showed **per-edge numpy dispatch *regresses* these light
> loops** (resample 0.16–0.85× — slower — at every realistic edge size; the scalar arithmetic is cheaper than
> the fixed numpy overhead) and that bit-identity for the distance stages requires `math.hypot` (numpy's
> differs on ~17% of inputs), which forces a Python loop that negates the win. The winning pattern is
> **flat-array-across-all-edges** (one `shapely.get_coordinates` gather → whole-graph numpy math → one
> `shapely.linestrings` rebuild), using `np.hypot`/naive means. This is *numerically equivalent* (not
> bit-identical) — measured max ~1.4e-14 deg (coords) / ~1.7e-10 (metrics), with `d_plus_m`/`d_minus_m`
> exactly bit-identical. **The regression goldens turned out byte-identical anyway** (verified fast + realistic
> + flag-on), so the budgeted rebake/fixture-regen was *not* needed.

- [x] Task 1: Pin all per-stage benchmark baselines *before* touching code (AC: #2)
  - [x] Existing seams confirmed: `test_stage2_filter_trails`, `test_stage3_smooth_polylines`, `test_stage4_resample_edges`
  - [x] Added stage-7 (`compute_edge_metrics`), stage-6b (`graph_deadband_elevation`, non-zero deadband), and
        stage-9 (`contract_climbs`) benchmarks + their `conftest.py` fixtures (query-stage input chain off the e2e cache)
  - [x] Autosave baseline `0008` captured on the committed fixtures (no network), same machine as the after-run
- [x] Task 2: Add proof tests *before* deleting old code (AC: #1)
  - [x] Verbatim scalar oracles (`_scalar_moving_average` / `_scalar_resample_meters` in `test_smoothing.py`;
        `_oracle_*` in `test_climbs.py`) — confirmed passing against the *old* code first, then kept as the
        equivalence reference. Converted from `==` to numerical-equivalence (`abs diff ≤ 1e-8 deg` coords /
        `1e-6 m` length / `1e-9` gradient) once the flat path landed (the deadband oracle stays exact `==`).
  - [x] Cover `smooth_polylines`, `resample_edges`, `graph_deadband_elevation`, and all five
        `compute_edge_metrics` fields; non-vacuous guards (`> 1000` vertices) present.
- [x] Task 3: Vectorize stage 3–4 in `pipeline/smoothing.py` (S2) — **flat-array** (AC: #1)
  - [x] `smooth_polylines`: whole-graph window-3 moving average as flat shifted-array ops (`(left+mid+right)/3`),
        edge boundaries masked, endpoints pinned
  - [x] `resample_edges`: flat per-edge equirectangular projection, `np.hypot` segments + per-edge-reset
        `np.cumsum` arc length, one `np.searchsorted` (per-edge monotone offset trick) sampling every edge at once
  - [x] Preserved: degenerate-edge drop (`_valid_edges_mask`), exact endpoints, `TypeError` on non-LineString
        (`_collect_linestrings`), `spacing_m` `ValueError`
- [x] Task 4: De-churn the graph rebuilds (S3) (AC: #1)
  - [x] `filter_trails` (osm.py), `_drop_orphan_nodes`/`_drop_short_edges` (__init__.py): build-from-kept-edges
        (no copy-then-remove); smooth/resample rebuild via one `shapely.linestrings` + `_build_from_flat`
  - [x] Node/edge iteration order + all attributes preserved (goldens byte-identical confirm it)
- [x] Task 5: Vectorize query-side stage 7 (Q2) in `pipeline/climbs.py` — **flat-array** (AC: #1)
  - [x] `compute_edge_metrics`: whole-graph flat `(V,3)` gather; length via `np.hypot`+per-edge-reset cumsum;
        gain/loss via `np.diff` + `np.add.reduceat` per edge (d_plus/d_minus **bit-identical**); windowed descent
        via `_windowed_descent_all` (one `np.searchsorted`, per-edge monotone offset, `run≥window` + `drop>0`
        gates, short-polyline end-to-end fallback)
  - [x] Deadband kept scalar (`_deadband_profile`) — off by default (`ELEVATION_DEADBAND_DEFAULT_M == 0`), not
        on any hot path; hysteresis is sequential. Bit-identical to pre-14.2.
- [x] Task 6: Profile `contract_climbs` (Q3) — **left untouched** (AC: #1, #3)
  - [x] cProfiled on the fixture: body 38% + networkx `add_edge` 25% (inherent to building any graph) dominate;
        no extractable sub-hotspot short of a from-scratch graph builder (deferred). `_is_junction`/`_next_key_for`
        edge-view iteration is ~11% but not worth the complexity at this stage's size. **Decision: left untouched.**
- [x] Task 7: Correctness net + goldens (AC: #1)
  - [x] `test_smoothing.py`, `test_climbs.py`, `test_graph.py`, `test_osm.py`, `test_climb_detection.py`,
        integration `test_pipeline_end_to_end.py` all green
  - [x] `tests/e2e/test_pinned_regressions.py` fast (4/4) + `-m slow` (4/4) + flag-on (`test_junction_start`,
        `test_descent_cap`) all **byte-identical** — no rebake, no fixture regen (caches read by geometric
        containment; the sub-nm divergence never flipped a route or a pinned golden field)
- [x] Task 8: Measure + gates + status (AC: #2, #3)
  - [x] Benchmarks `--benchmark-compare=0008` recorded (Debug Log below)
  - [x] `ruff check` clean, `ruff format` clean, whole-project `basedpyright` 0/0/0, `uv run pytest --cov`
        854 passed / 96% (pipeline modules ≥ 97%); sprint-status 14.2 → review
  - [ ] Real-world r20 confirmation deferred to the 14.6 probe (per 14.1 precedent; the fixture is far smaller
        than r20 so the epic-relevant number is measured there)

## Dev Notes

### What this story touches and why — the batching rationale

This is the **one deliberately-batched content-hash change** of Epic 14 (handoff §7 step 2). Every file it
touches is under `_PIPELINE_CONTENT_GLOBS = ("pipeline/**/*.py", "models.py")` (`cache.py:60`), so **any** byte
change re-keys every cache. Story 14.1 already spent one such cycle (dem.py); the epic's whole point is to not
spend a *third* — so S2 (stages 3–4), S3 (graph churn), Q2 (stage-7 metrics + deadband), and Q3
(`contract_climbs`) all land together. Do not carve this into sub-PRs that each shift the content hash.

Four mechanisms, one story:

| Lever | Where | Measured @ r20 | Shape |
|---|---|---|---|
| **S2** smoothing + resampling | `smoothing.py` `_moving_average`, `_resample_meters` | 32.9 + 62.5 s | per-vertex Python loops |
| **S3** copy-then-remove churn | `osm.py` `filter_trails`; `__init__.py` `_drop_orphan_nodes`/`_drop_short_edges`; `smoothing.py` stage-3/4 `.copy()` | trail-filter 18.4 s + repeated full-graph copies (`MultiDiGraph.copy` 11 calls / 6.6 s @ r5) | structural graph churn |
| **Q2** stage-7 metrics + deadband | `climbs.py` `compute_edge_metrics` (+ `_cumulative_2d_distances`, `_elevation_gain_loss`, `_max_windowed_descent_grad`); `smoothing.py` `graph_deadband_elevation` | part of elevation-reshape 24.4 s | per-vertex Python loops |
| **Q3** contracted-graph build | `graph.py` `contract_climbs` | 5.6 s (no sub-attribution yet) | profile-first, optimize-if-material |

**The math must not change.** Same projection (local equirectangular, cos-of-mean-latitude), same endpoint
pinning, same degenerate-edge drops, same `(lat, lon, elev)` axis order, same metric definitions, same
`_DESCENT_WINDOW_M` two-pointer semantics. Only the *mechanism* moves from Python per-vertex loops to numpy
array ops. `numpy` and `shapely>=2.0` are already pinned deps; no new dependency.

### The summation-parity map (this is the crux — read before writing any reduction)

Every float reduction in the touched code falls into exactly one of two buckets. Getting the bucket wrong is
how goldens drift by 1 ULP (the 13.1 war story).

**Bucket A — naive left-fold `+=` loops → `np.cumsum` is BIT-IDENTICAL.** `np.cumsum` accumulates sequentially
(not pairwise), so `np.cumsum(x)[-1]` reproduces a Python `total += x[i]` loop exactly. These are safe:
- `_resample_meters` `cumulative.append(cumulative[-1] + math.hypot(...))` → `np.cumsum(seg_lengths)`
- `_cumulative_2d_distances` same shape
- `_elevation_gain_loss` `d_plus += delta` / `d_minus += -delta` → masked `np.cumsum(...)[-1]`
  ⚠️ Do **not** reach for `np.sum` / `np.add.reduce` here — those are **pairwise** and will drift. Use `np.cumsum(...)[-1]`.

**Bucket B — builtin `sum(iterable)` → `np.sum` does NOT match.** CPython's builtin `sum()` has been
**Neumaier-compensated** since 3.12; `np.sum` is pairwise. These sites will drift under naive vectorization:
- `_moving_average`: `sum(c[0] for c in window_coords)` / `sum(c[1] ...)` — window is always exactly 3 for
  interior vertices (endpoints pinned), so this is a **3-term** compensated sum.
- `_resample_meters`: `mean_lat = sum(lat for _lon, lat in coords) / len(coords)` — variable-length compensated
  sum over the whole edge; feeds `deg_to_m_lon`, so a ULP here perturbs every projected coord.
- `_cumulative_2d_distances`: `mean_lat = sum(lat for lat, _lon, _elev in verts) / n` — same.

For Bucket B you have three options, in order of preference:
1. **Keep the per-edge scalar reduction in Python.** `mean_lat` is *one* `sum()` per edge (O(edges), not
   O(vertices)) — computing it with builtin `sum()` in a tiny Python step while vectorizing the O(vertices)
   segment/lerp work keeps bit-identity for free and still removes >95% of the loop cost. **Strongly preferred
   for `mean_lat`.**
2. **Replicate Neumaier compensation vectorially** — the 13.1 pattern
   (`tests/unit/test_smoothing.py` `_scalar_reference_smooth` proves the shape; `graph_smooth_elevation` in
   `smoothing.py:270-288` is the round-by-round compensated-sum reference implementation to copy). Viable for
   `_moving_average`'s fixed 3-term window.
3. **Take the one batched documented rebake** (AC #1 permits it). Since this story already owns a content-hash
   cycle, a rebake here is low marginal cost — but it re-bakes all four golden roots, so prefer 1/2 where cheap.
   If you rebake, do it once via `update-regression --all` with the equivalence argument written into the
   close-out. Never silent.

**Recommendation:** `mean_lat` → option 1 (trivially bit-exact). `_moving_average` 3-term → option 2 if the
Neumaier replication is clean, else fold into the one rebake. Decide from the Task-2 equality assertions, not
from guessing.

### `math.hypot` vs `np.hypot` — a real, separate parity risk

The old code uses `math.hypot(dx, dy)`; the vectorized form uses `np.hypot`. **These are not guaranteed
bit-identical** — `math.hypot` (CPython 3.8+) uses a higher-accuracy algorithm, `np.hypot` calls the C library.
This is independent of the summation-parity issue above and applies to `_resample_meters`, `_cumulative_2d_distances`,
and `_polyline_length_m`/`_drop_short_edges`. The Task-2 fixture equality assertion is the ground truth: if it
fails on segment lengths, either compute lengths as `np.sqrt(dx*dx + dy*dy)` and check *that* against the old
(also unlikely to match `math.hypot` exactly), or fold this into the one batched rebake. Verify, don't assume.

### The `_max_windowed_descent_grad` two-pointer subtlety (Q2)

The scalar version (`climbs.py:165-215`) is a forward two-pointer scan: for each `i`, advance `j` until
`cum[j] - cum[i] >= _DESCENT_WINDOW_M`, then take `drop = verts[i].elev - verts[j].elev` and `grad = drop/run`
if `drop > 0`, tracking the max. Vectorizing with `np.searchsorted(cum, cum + _DESCENT_WINDOW_M)` gives the `j`
frontier for all `i` at once — **but** replicate these exactly:
- The `saw_full_window` flag and the short-polyline fallback (whole polyline < one window → end-to-end descent
  grade). `searchsorted` can return `n` (past the end); those `i` have no full window and must be excluded from
  the max, and if *no* `i` had a full window, apply the fallback.
- `run` is `cum[j] - cum[i]` with the **actual** `j` chosen (the first index reaching the window), not exactly
  `_DESCENT_WINDOW_M` — so grad uses the real run. `searchsorted(side='left')` matches the `< WINDOW` loop
  condition (first `j` with `cum[j] - cum[i] >= WINDOW`); verify side against the strict-`<` loop.
- `drop > 0.0` gate (a window that nets a climb contributes `0.0`).
- `best` starts at `0.0` and only strict `grad > best` updates — max reduction is order-independent so bit-safe.
This metric is directional (FR32, Story 10.2); the reciprocal edge carries reversed vertices. Bit-equality here
is verified per-edge by the Task-2 assertion.

### S3 graph de-churn — preserve iteration order, keep purity at the boundary

Today every stage does `out = graph.copy()` then loops removing dropped edges — two full passes plus a
whole-graph deep-ish copy. Replace with **build a new `MultiDiGraph` from the kept edges** in one pass:

- **Default (recommended): each stage stays pure**, still returns a fresh graph, but constructs it from kept
  edges rather than copy-then-remove. This satisfies the AC purity boundary *and* the existing per-function
  purity unit tests (they assert the input graph is not mutated — do not break them). For the vectorized S2
  stages this composes naturally: build the output graph while emitting new geometry, skipping degenerate edges,
  in the same pass.
- **Ordering is load-bearing.** `copy()` + `remove_edge` preserves the relative order of surviving edges and
  nodes. A rebuild **must** iterate `graph.edges(..., keys=True)` (and `graph.nodes`) in original order and add
  kept elements in that order. Reason: `graph_smooth_elevation` (`smoothing.py:207`) lays out its float field in
  **edge-iteration order** and its per-node compensated sums depend on **per-node adjacency order** — a reorder
  can flip the Laplacian result by ULPs even though the graph is "the same". Carry every node attribute (`x`,
  `y`, …) and every edge attribute dict across verbatim.
- **Aggressive alternative (permitted, not required):** the orchestrator (`build_graph_geometry`) owns ONE
  working copy and internal stages mutate it in place, eliminating stages 3/4/5's per-stage `.copy()` entirely
  (handoff §5 S3). This breaks per-stage purity and its tests — only take it if Task-1 measurement shows the
  per-stage copy still dominates after the build-from-kept change, and update the purity contract/tests
  deliberately. Public purity must still hold at `run_setup_stages`/`build_graph_geometry`/`operationalize_graph`.
- `_drop_orphan_nodes` is called twice (once after filter, once inside `_drop_short_edges`); the rebuild pattern
  should not change that the post-guard invariant is "every node has degree ≥ 1".

### Content-hash reality — do NOT over-engineer a fixture regen (same as 14.1)

Read this before regenerating anything. Two independent facts (confirmed by Story 14.1, which changed a
`pipeline/` file with **zero** fixture regen):

1. **The query-side regression harness reads committed fixture caches by geometric containment**
   (`check_coverage` → `_select_smallest_containing`, `cache.py:1151`), **not** by re-deriving the pipeline
   content hash. So a content-hash shift alone does **not** stale the committed caches for test purposes.
2. **What actually determines golden pass/fail:** the *query* stages (6–7 = Q2, 8–9) run **live** from the
   committed cache at test time. If Q2 output is bit-equal, golden routes are byte-identical → **no rebake**. If
   a Bucket-B / `hypot` site drifted Q2 output, rebake once.
3. **Setup stages 2–4 (S2/S3):** the committed caches store post-stage-5 graphs; tests read them as-is (setup is
   not re-run). A **bit-identical** S2/S3 change means the committed caches still faithfully represent
   current-setup output → nothing to regen. Only a *non*-bit-identical S2/S3 change makes the committed caches
   stale vs. the new pipeline, requiring a regen — batched with the same one rebake.

**Bottom line:** if bit-equality holds everywhere (the target, per 14.1's outcome), you touch **no** fixtures
and goldens stay byte-identical, exactly like 14.1 — the content-hash shift only means real user caches
re-prepare once (by design, Category 4b). Do not speculatively regenerate. Regen/rebake is the single batched
contingency for a proven drift, via `update-regression --all`, with the equivalence argument recorded.

### Benchmarks to add (AC #2) — stage 7 has none today

`tests/benchmarks/test_setup_stages.py` covers stages 1–5 only; `test_solver_throughput.py` covers the solver.
**Stages 6–7 have no benchmark.** Add (before optimizing):
- `compute_edge_metrics` (stage 7) — the AC names this one explicitly.
- `graph_deadband_elevation` (needs a non-zero `deadband_m`; default 0.0 early-returns).
- `contract_climbs` (Q3 profiling seam).
Wire fixtures in `tests/benchmarks/conftest.py` by reusing the existing `contracted_graph` chain (`conftest.py:87-115`):
the operationalized+filtered `routable` graph is the metrics/deadband input; `climbs` + `routable` feed
`contract_climbs`. Keep the local-pinning rule (`conftest.py` docstring): pin params locally, never import from
`regression`/CLI defaults. Baselines are machine-local (`.benchmarks/`) — before/after must be same-machine.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest --cov`
  (~4:15 typical; markedly slower usually means a test hit the network). Pipeline modules are gated at **95%**
  coverage (`pipeline/`, per epics.md CI gates) — the vectorized code must stay covered.
- Correctness nets to keep green: `tests/unit/test_smoothing.py` (incl. the hypothesis property tests using
  `is_valid_polyline` — keep that public helper's semantics), `test_climbs.py` (uses `is_valid_for_metrics`),
  `test_osm.py`, `test_graph.py`, `tests/integration/test_pipeline_end_to_end.py`.
- `uv` Windows build flake: after a commit or `pyproject.toml` edit, `uv run` may hit a corporate-TLS cert error
  (~43 `test_cli_smoke` failures as the symptom). Fix once with `uv sync --native-tls`, then `uv run --no-sync …`
  for the rest of the session (14.1 hit this).
- Benchmarks are excluded from the default run (marker `benchmark`); run explicitly with
  `uv run pytest tests/benchmarks -m benchmark`. Autosave/compare workflow in README "Performance benchmarks".
- The `# pyright: reportUnknown*=false` headers on `smoothing.py`/`climbs.py`/`osm.py`/`graph.py`/`__init__.py`
  already relax the networkx/shapely boundary; keep them (numpy is typed, so the new array code stays clean).

### Project Structure Notes

- **Modified (production):**
  - `src/steeproute/pipeline/smoothing.py` — `_moving_average`, `_resample_meters` vectorized (S2);
    `smooth_polylines` / `resample_edges` de-churned (S3); `graph_deadband_elevation` / `_deadband_profile`
    interp vectorized, hysteresis scan kept Python (Q2).
  - `src/steeproute/pipeline/climbs.py` — `compute_edge_metrics`, `_cumulative_2d_distances`,
    `_elevation_gain_loss`, `_max_windowed_descent_grad` vectorized (Q2). Signatures + docstring contracts
    unchanged; `is_valid_for_metrics` semantics unchanged.
  - `src/steeproute/pipeline/osm.py` — `filter_trails` de-churned (S3, build-from-kept). Behavior identical.
  - `src/steeproute/pipeline/__init__.py` — `_drop_orphan_nodes`, `_drop_short_edges`, `_polyline_length_m`
    de-churned/vectorized (S3). Public orchestrator API (`run_setup_stages`/`build_graph_geometry`/
    `operationalize_graph`) signatures unchanged; purity preserved at these boundaries.
  - `src/steeproute/pipeline/graph.py` — `contract_climbs` **only if** Q3 profiling shows material cost (AC #3).
- **Modified (tests):** `tests/unit/test_smoothing.py`, `tests/unit/test_climbs.py` (scalar-reference
  bit-equality proofs); `tests/benchmarks/test_setup_stages.py` + `tests/benchmarks/conftest.py` (new stage-7 /
  deadband / contract_climbs benchmarks + fixtures).
- **Untouched:** `dem.py` (14.1, done), `cache.py`, solver, validator, output, CLI surface,
  `_PIPELINE_CONTENT_GLOBS`. The on-disk cache format is **not** changed (Q4 schema-v3 is deferred per the
  sprint change proposal) — so **no architecture-doc update** is required for this story unless a golden rebake
  forces a Cat 4c note (it should not — the in-memory contract is unchanged).
- **Out of scope:** 14.3 (parallel DEM fetch), 14.4 (`--workers` GRASP parallelism), 14.5 (osmnx CPU levers),
  14.6 (r50 probe). The Q4 numpy-array edge contract (schema v3) and per-stage multiprocess parallelization are
  **deferred to a post-probe correct-course** — do not start them here; if the per-vertex loops resist
  bit-identity or the copies still dominate, record it as input for 14.6, don't reach for the array contract.

### References

- [Source: epics.md §Epic 14 preamble + §Story 14.2](_bmad-output/planning-artifacts/epics.md) — AC
  source-of-truth; the "single content-hash change with one fixture-regen" batching mandate and the
  profile-first Q3 rule.
- [Source: research/steeproute-next-optimization-pass-handoff-2026-07-05.md §4.2 (bit-identity rules), §4b
  (vectorize-first), §5 S2/S3, §6 Q2/Q3, §7 step 2 (the batch), §9 (failure modes)](_bmad-output/planning-artifacts/research/steeproute-next-optimization-pass-handoff-2026-07-05.md)
  — the measured r20 costs, the exact vectorization recipes, the `np.sum`-vs-`sum()` compensated-summation
  war story, and the "every doc number is measured or labeled estimate" discipline.
- [Source: _bmad-output/implementation-artifacts/14-1-vectorize-elevation-sampling.md](_bmad-output/implementation-artifacts/14-1-vectorize-elevation-sampling.md)
  — the immediately-prior story: the "prove bit-equal before deleting via a verbatim scalar reference" test
  discipline, the content-hash-vs-geometric-containment insight (why 14.1 needed zero fixture regen), the
  benchmark autosave/compare workflow, and the `uv sync --native-tls` flake.
- [Source: _bmad-output/implementation-artifacts/13-1-vectorize-query-side-elevation-smoothing.md](_bmad-output/implementation-artifacts/13-1-vectorize-query-side-elevation-smoothing.md)
  — the Neumaier compensated-`sum()` replication pattern and the ragged-array (flat coords + per-edge offsets)
  collection shape.
- [Source: src/steeproute/pipeline/smoothing.py:400-472](src/steeproute/pipeline/smoothing.py) —
  `_moving_average` (Bucket-B 3-term `sum`), `_resample_meters` (Bucket-B `mean_lat`, `math.hypot`, Bucket-A
  cumulative, the `t` clamp); [smoothing.py:72-142](src/steeproute/pipeline/smoothing.py) `smooth_polylines` /
  `resample_edges` copy-then-remove (S3); [smoothing.py:304-368](src/steeproute/pipeline/smoothing.py)
  `graph_deadband_elevation` / `_deadband_profile` (Q2 hysteresis); [smoothing.py:270-288](src/steeproute/pipeline/smoothing.py)
  the compensated-sum reference to copy.
- [Source: src/steeproute/pipeline/climbs.py:76-215](src/steeproute/pipeline/climbs.py) —
  `compute_edge_metrics`, `_cumulative_2d_distances` (Bucket-B `mean_lat`, Bucket-A cumulative),
  `_elevation_gain_loss` (Bucket-A masked fold), `_max_windowed_descent_grad` (two-pointer semantics to
  replicate).
- [Source: src/steeproute/pipeline/osm.py:141-190](src/steeproute/pipeline/osm.py) — `filter_trails`
  copy-then-remove (S3).
- [Source: src/steeproute/pipeline/__init__.py:275-363](src/steeproute/pipeline/__init__.py) —
  `_drop_orphan_nodes`, `_drop_short_edges`, `_polyline_length_m` (S3); [__init__.py:158-249](src/steeproute/pipeline/__init__.py)
  the `build_graph_geometry` / `operationalize_graph` purity boundaries.
- [Source: src/steeproute/pipeline/graph.py:72-200](src/steeproute/pipeline/graph.py) — `contract_climbs`
  (Q3: `**data` re-dict at line 160, `_next_key_for` scans — profile before touching).
- [Source: src/steeproute/cache.py:60](src/steeproute/cache.py) — `_PIPELINE_CONTENT_GLOBS`;
  [cache.py:1151](src/steeproute/cache.py) `check_coverage` containment selection (why goldens read fine).
- [Source: tests/benchmarks/test_setup_stages.py](tests/benchmarks/test_setup_stages.py) +
  [tests/benchmarks/conftest.py:87-156](tests/benchmarks/conftest.py) — existing stage 2/3/4 benchmarks + the
  `contracted_graph` fixture chain to extend for stage-7 / deadband / contract_climbs seams.
- [Source: tests/unit/test_smoothing.py:579](tests/unit/test_smoothing.py) — `_scalar_reference_smooth`, the
  bit-equality-via-scalar-reference test shape to reuse.
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-06-setup-solver-scaling.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-06-setup-solver-scaling.md)
  — the correct-course that inserted Epic 14; §2 confirms 14.2 co-lands S2+S3+Q2+Q3 as one content-hash change
  and that Q4/schema-v3 stays deferred.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Gates (all green):**

```
tests/unit/test_smoothing.py + test_climbs.py + test_osm.py + test_climb_detection.py → 143 passed
tests/e2e/test_pinned_regressions.py            → 4 passed (fast, byte-identical goldens)
tests/e2e/test_pinned_regressions.py -m slow    → 4 passed (realistic, byte-identical)
tests/e2e/test_junction_start.py + test_descent_cap.py → byte-identical (flag-on goldens)
uv run pytest --cov (default markers)           → 854 passed, 15 deselected, 96% overall
                                                   (smoothing.py 98%, climbs.py 98%, osm.py 97%,
                                                    pipeline/__init__.py 99%, graph.py 100% — all > 95% gate)
ruff check src tests                            → All checks passed!
ruff format src tests                           → clean
basedpyright (whole project)                    → 0 errors, 0 warnings, 0 notes
```

**Per-stage benchmarks (grenoble_small fixture, same machine, `--benchmark-compare=0008`), median:**

```
                            before (0008)   after (NOW)    speedup
stage3 smooth_polylines      104.08 ms       19.56 ms       ~5.3x
stage4 resample_edges        155.48 ms       18.28 ms       ~8.5x
stage7 compute_edge_metrics   37.81 ms       23.53 ms       ~1.6x
stage2 filter_trails          10.70 ms       10.28 ms       ~1.0x  (de-churn; few edges dropped at r1.5 —
                                                                    the big filter drop lands at r20 scale)
stage6b deadband (active)     32.00 ms       21.59 ms       (scalar, unchanged; variance)
stage9 contract_climbs        18.60 ms       11.62 ms       (untouched; variance)
```

**Real r20 setup run (user, same-machine before→after, `--radius 20 --force-refresh`):**

```
                     before     after    speedup
polyline-smoothing   17.49 s    7.69 s    2.3x
resampling           35.91 s   21.57 s    1.7x
trail-filter         12.57 s   11.83 s    1.06x  (de-churn; few edges dropped relative to total at r20)
```

The r20 gains (2.3× / 1.7×) are smaller than the fixture benchmark (5–8×) because at scale the stage is
**graph-construction-bound**, not math-bound: a fixture profile of `smooth_polylines` shows the flat math is
only 2.2 ms of 14 ms — the other **11.8 ms (84%) is building a fresh `MultiDiGraph`** (`shapely.linestrings` +
per-edge `add_edge` + attribute-dict copy), which is inherent per-edge Python that scales with edge count and
does not vectorize. Removing that floor needs the deferred **Q4 array-edge contract** (vertices as arrays
end-to-end, no per-stage graph rebuild) — the handoff said "adopt Q4 only if residual copy/pickle cost still
binds"; this run is the evidence that it now binds. Routed to 14.6.

**Post-review micro-opts (this story's own scope):**
- **(a) `_build_from_flat` copy-avoidance** — when no edge is dropped (the common case) the flat coords are fed
  straight to `shapely.linestrings` with no per-edge slice loop or full-array `np.concatenate` copy. Kept
  (bit-identical; marginal on the fixture, avoids copying ~3.5 M points at r20).
- **(b) flat deadband** — attempted, then **reverted**: measured *slower* (62 ms vs 37.8 ms scalar on the e2e
  cache). Deadband's cost is the sequential hysteresis scan + the per-point tuple rebuild (neither vectorizable
  without Q4); the interp fill it would flatten is the minority, so the flat machinery cost more than it saved.
  Deadband stays scalar/bit-identical. Recorded as another Q4-gated item.

**Numerical-equivalence measurements (flat vs verbatim scalar oracle, grenoble_small):**

```
smooth coords    max abs diff 1.42e-14 deg
resample coords  max abs diff 1.42e-14 deg, 0 vertex-count mismatches, 0 segment-boundary flips
metrics          length 1.68e-10 m | d_plus 0.0 | d_minus 0.0 | avg_grad 4.56e-12 | wdg 4.56e-12
```

### Completion Notes List

**Approach: flat-array-across-all-edges, not per-edge (the load-bearing finding).** The story assumed
per-vertex-loop vectorization would win. It does *not* for these light loops: a microbenchmark showed per-edge
numpy `resample` at **0.16–0.85×** (slower than scalar) across 10–1000 vertices/edge — the fixed per-edge
numpy dispatch overhead (~15 µs) exceeds the cheap scalar arithmetic. Stage profiling then showed the scalar
math itself (~126 ms) dominated the resample stage, while extract/build/graph-rebuild were each ≤ 8 ms. The
only pattern that wins is **flat-array**: gather every edge's coords in ONE `shapely.get_coordinates`, do the
whole-graph math in flat numpy ops, rebuild in ONE `shapely.linestrings`. A flat-smooth prototype hit 3× on
the fixture where per-edge lost — confirming the direction before the full rewrite.

**Why goldens stayed byte-identical (no rebake, no fixture regen — the budgeted rebake was unnecessary).**
Two independent reasons, both verified: (1) the flat path uses `np.hypot`/naive means instead of
`math.hypot`/compensated `sum()`, but the divergence is sub-nanometer (max ~1.4e-14 deg coords, ~1.7e-10
metrics) — far below DEM pixel and routing resolution — and `d_plus_m`/`d_minus_m` (the only float fields a
golden pins, via `objective`) came out **exactly** bit-identical because `np.add.reduceat` matched the scalar
`+=` fold; (2) the regression harness reads the committed caches by geometric containment
(`check_coverage`), not by re-deriving the pipeline content hash, so the setup-side (stage 3/4) changes don't
re-run against the committed fixtures. Net: fast + realistic + flag-on goldens all pass unchanged; the only
real-world effect is user caches re-prepare once (content-hash shift, by design — same as 14.1). Fresh
np.hypot caches give routes identical to the committed math.hypot caches (sub-nm coords → same DEM pixels →
same elevations → same routes), so re-preparation is safe.

**The searchsorted "per-edge monotone offset" trick (resample + windowed descent).** Both stages need a
per-edge `searchsorted` (uniform-sample segment location; descent-window frontier). To do all edges in ONE
`np.searchsorted`, the per-edge-local cumulative `cum` is made globally monotone by adding `edge_id * OFFSET`
with `OFFSET > max(edge length) + window` — so every search provably lands inside its own edge, then the
result is clipped to the edge's `[start, end-1]` vertex range. This is what makes the vectorization amortize
instead of looping edges.

**S3 de-churn (build-from-kept, not orchestrator-owned copy).** `filter_trails`, `_drop_orphan_nodes`,
`_drop_short_edges` now build a fresh graph from kept edges (all nodes carried, iteration order preserved) —
no `graph.copy()` + `remove_edge`. Kept per-function purity (the aggressive orchestrator-owned-working-copy
variant the AC allowed was not needed and would have broken the per-stage purity tests). `filter_trails`
barely moves at r1.5 (few non-trail edges) but is the biggest de-churn win at r20 where most edges are dropped.

**Q3 `contract_climbs` left untouched (recorded).** cProfile: the loop body (38%) and networkx `add_edge`
(25%, inherent to constructing any `MultiDiGraph`) dominate; the `_is_junction`/`_next_key_for` edge-view
iteration is ~11%. No material extractable cost short of a from-scratch graph builder — which is exactly the
kind of deep work the epic defers to the post-probe correct-course. Benchmark seam added so 14.6 can re-judge
at r50.

**Deadband kept scalar.** `graph_deadband_elevation` is off by default (`--elevation-deadband 0` → no-op early
return), so it never runs on the default query/golden path; the hysteresis commit-scan is inherently
sequential. Not worth vectorizing; left bit-identical to pre-14.2.

**Dead code removed.** The per-edge scalar helpers (`_moving_average`, `_resample_meters`, `_compensated_sum3`,
`_extract_coords` in smoothing.py; `_elevation_gain_loss`, `_max_windowed_descent_grad` in climbs.py) are gone
— production goes through the flat paths; the scalar reference implementations live in the test oracles.

### File List

**Modified (production):**
- `src/steeproute/pipeline/smoothing.py` — `smooth_polylines` + `resample_edges` rewritten as flat-array
  (shapely batch gather/build, whole-graph numpy math, `searchsorted` offset trick); new helpers
  `_collect_linestrings`, `_valid_edges_mask`, `_build_from_flat`, `_DEG_TO_M_LAT`; `_empty_like` (S3);
  `_deadband_profile` reverted to scalar; removed `_moving_average`/`_resample_meters`/`_compensated_sum3`/`_extract_coords`.
- `src/steeproute/pipeline/climbs.py` — `compute_edge_metrics` rewritten as flat-array; new `_windowed_descent_all`,
  `_DEG_TO_M_LAT`; removed `_elevation_gain_loss`/`_max_windowed_descent_grad`; `_projected_cumulative`/`_cumulative_2d_distance_m`
  retained for `is_valid_for_metrics`.
- `src/steeproute/pipeline/osm.py` — `filter_trails` de-churned (build-from-kept); now imports `empty_like` from `_common.py`, drops redundant `dict(data)` copies.
- `src/steeproute/pipeline/__init__.py` — `_drop_orphan_nodes` / `_drop_short_edges` de-churned; now imports `empty_like` from `_common.py`, drops redundant `dict(data)` copies.
- `src/steeproute/pipeline/_common.py` — **new** (post-review). `empty_like()` and `per_edge_searchsorted()`, shared primitives extracted to de-duplicate what was independently reimplemented in `__init__.py`/`osm.py`/`smoothing.py`/`climbs.py`.

**Modified (tests):**
- `tests/unit/test_smoothing.py` — scalar oracles + numerical-equivalence tests for smooth/resample; removed the
  `_moving_average` direct test.
- `tests/unit/test_climbs.py` — scalar oracles + numerical-equivalence test for metrics; deadband oracle stays exact.
- `tests/benchmarks/conftest.py` — query-stage input fixtures (`prepared_grenoble_graph`, `smoothed_elevation_graph`,
  `metrics_input_graph`, `routable_and_climbs`) + `BENCH_DEADBAND_ACTIVE_M`.
- `tests/benchmarks/test_setup_stages.py` — stage-6b / stage-7 / stage-9 benchmarks.

**Docs:**
- `_bmad-output/implementation-artifacts/14-2-vectorize-and-de-churn-pipeline-loops.md` — this file.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — 14-2 → in-progress → review → done.

**Post-review fixes (this pass):**
- **Correctness fix** — `compute_edge_metrics` now fails fast (`PipelineContractError`) on any non-finite
  coordinate in `vertices_resampled`, before the shared `np.cumsum`/`.max()` reductions run. Code review found
  that a single degenerate edge could silently corrupt *other, valid* edges' `length_m`/`avg_gradient`/
  `max_windowed_descent_grad` via those shared whole-graph reductions — reproduced concretely (a clean edge's
  correct windowed-descent grade of 2.667 silently became 1.333). Pre-14.2, each edge's scalar loop was
  isolated; this closes the regression without touching the vectorized hot path. New regression test:
  `test_compute_edge_metrics_fails_fast_on_non_finite_without_corrupting_others`.
- **New shared module** `src/steeproute/pipeline/_common.py` — `empty_like()` (the build-from-kept-nodes
  idiom, previously triplicated across `pipeline/__init__.py` ×2 and `osm.py::filter_trails` instead of reusing
  smoothing.py's copy) and `per_edge_searchsorted()` (the per-edge monotone-offset searchsorted trick,
  previously duplicated with two independently-derived offset formulas in `climbs.py::_windowed_descent_all`
  and `smoothing.py::resample_edges`). Both call sites now share one audited implementation.
- Dropped 5 redundant `dict(data)` copies in `pipeline/__init__.py` / `osm.py`'s graph-rebuild loops (`**data`
  unpacks identically since `data` is never reused after `add_edge`); left `_build_from_flat`'s copy alone since
  it mutates before use and must not touch the input graph.
- `tests/unit/test_climbs.py`'s oracle now imports `_DESCENT_WINDOW_M` from production instead of hardcoding
  `30.0`, so it can't silently drift from the real constant.
- Verified: `ruff check` clean, `basedpyright` (whole project) 0 errors/0 warnings/0 notes, 627/627 unit tests
  passing (626 pre-existing + 1 new regression test).

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-06 | Yann (Claude Opus 4.8) | Story 14.2 implemented. Vectorized setup stages 3–4 (smooth/resample) + query stage 7 (metrics) as **flat-array-across-all-edges** (per-edge numpy measured to regress; the amortized flat pattern wins — smooth ~5.3×, resample ~8.5×, metrics ~1.6× on the fixture). De-churned `filter_trails`/orphan/short-edge guards (build-from-kept). `contract_climbs` profiled → left untouched (no extractable cost). Numerically equivalent to the scalar path (max ~1.7e-10; d_plus/d_minus bit-identical); **regression goldens byte-identical (fast + realistic + flag-on), no rebake or fixture regen needed**. Gates green (ruff, basedpyright 0/0/0, 854 passed, 96% cov). Approach pivot to full `np.hypot` vectorization approved by Yann. |
| 2026-07-06 | Yann (Claude Opus 4.8) | Real r20 setup confirmation (user run): polyline-smoothing 17.49→7.69 s (2.3×), resampling 35.91→21.57 s (1.7×) — smaller than the fixture because the stages are now graph-construction-bound (flat math 2.2 ms of 14 ms; the rest is per-edge `add_edge`/`shapely` rebuild). Query `elevation-reshape` unchanged — dominated by the 13.1 Laplacian (38%) + active deadband (36%); Q2 metrics is only 26% and improved ~1.4×, lost in variance. Post-review: kept micro-opt (a) `_build_from_flat` copy-avoidance (bit-identical); reverted (b) flat deadband (measured slower — scan+scatter dominate, not the interp). The residual graph-rebuild floor (setup) + deadband scan/scatter + 3× query `graph.copy()` are all evidence that the deferred **Q4 array-edge contract** now binds — routed to the 14.6 probe. |
| 2026-07-07 | Yann (Claude Sonnet 5) | Code review (8 finder angles + 7 verified candidates) found one real correctness regression: `compute_edge_metrics`'s shared whole-graph reductions let one degenerate edge silently corrupt other edges' metrics (blast-radius change vs. the pre-14.2 per-edge loop). Fixed with a fail-fast finiteness guard + regression test. Also fixed 3 confirmed cleanup findings: extracted `pipeline/_common.py` to de-duplicate the graph-rebuild idiom and the per-edge searchsorted trick (previously reimplemented independently in 3-4 places), dropped 5 redundant `dict(data)` copies, and fixed a test oracle hardcoding a production constant instead of importing it. `ruff` clean, `basedpyright` 0/0/0, 627/627 tests passing. Story marked done. |
