# Story 16.4: osmnx in-place component / consume ingestion adapter

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want osmnx's largest-component and truncate/simplify steps to stop double-traversing and copying
graphs that are owned intermediates,
so that warm setup ingestion CPU drops with a bit-identical graph.

## Acceptance Criteria

1. **The diff harness exists and is calibrated before any production edit.** An offline old-vs-new
   graph comparison runs against the **cached r20 Overpass response** already on this machine
   (`.trial-cache/steeproute/osmnx/afe167647cd512af66d244543028591a5c012885.json`, 91 MB — Story 14.5
   could not find one and had to settle for r2) and compares: node ID set **and iteration order**, edge
   `(u, v, key)` set **and iteration order**, every node attribute (including the semantically-inert
   `street_count`), every edge attribute, graph-level attrs, and `geometry` coordinate sequences
   exactly. List-valued tags compare as **multisets** — osmnx's multi-way tag order is
   process-nondeterministic (Story 16.2 Debug Log 5); a naive diff drowns in ~4.4k false positives.
   The harness is calibrated by a **control pair** (the same code twice) before it is trusted to gate
   anything.
2. **The single-traversal in-place largest component lands and is bit-identical.** One
   `weakly_connected_components` pass replaces osmnx's `is_weakly_connected` + `weakly_connected_components`
   + `nx.MultiDiGraph(G.subgraph(...))` copy, removing rejected nodes from the owned graph instead. The
   retained-component **policy** is unchanged — same component chosen, no islands kept, `retain_all`
   stays `False`. Unit tests cover the helper directly on synthetic weakly/strongly disconnected graphs
   plus the already-connected and empty-graph cases (osmnx's `is_weakly_connected` raises
   `NetworkXPointlessConcept` on an empty graph; `max()` over an empty component generator raises
   `ValueError` — pick one and pin it).
3. **The intervention is scoped and version-guarded — never an unscoped permanent monkeypatch.** The
   osmnx version this adapter was verified against is asserted at runtime (or the adapter refuses to
   engage and falls back to stock osmnx) with a diagnostic naming the pin, and a test fails when the
   installed version drifts outside it. Every private osmnx symbol the adapter touches is enumerated in
   one place with why the public API cannot serve. The dependency constraint in `pyproject.toml`
   (`osmnx>=2.0,<3`) is narrowed only if the story concludes it must be, with the reason recorded.
4. **Stage 1's error contract survives untouched.** `DataSourceUnavailableError` still wraps the same
   exception tuple with the same `detail` shape and still exits 2, `_validate_area` /
   `_ensure_sac_scale_in_useful_tags` / `truststore.inject_into_ssl()` / `normalize_edges` keep their
   current order and semantics, and the fetch-stack imports stay **lazy** inside the fetch path (Story
   14.4: module-level imports there re-inflate every spawned solver worker by ~4 s).
   `tests/unit/test_osm.py` and `tests/e2e/test_source_unavailable.py` pass unmodified.
5. **Consuming truncation / simplification is a measured decision, not an assumption.** Each candidate
   is run through the AC #1 harness and recorded as applied / rejected-with-numbers. The ownership map
   is **not** uniform and must be honoured: inside `graph_from_polygon`, truncate-pass-1 and
   component-pass-1 inputs are dead intermediates, but truncate-**pass-2** must leave the post-simplify
   buffered graph intact — `stats.count_streets_per_node(G_buff, nodes=G.nodes)` reads it *after*
   pass 2, so consuming there silently changes every node's `street_count`. Declining a lever
   (e.g. because consuming `simplify_graph` means vendoring ~120 lines of osmnx for one `G.copy()`) is a
   valid, recorded outcome — Story 14.5's precedent.
6. **The r20 warm-ingestion baseline is re-measured on this machine, not inherited.** This stage has
   measured 132 s (review machine), ~170 s (Story 16.1), and 232-240 s (Story 16.2) — so the review's
   `132 → 99 s` is a *shape*, not a target. Before/after come from a **real `steeproute-setup` replay**
   (AGENTS.md §Scale target), back-to-back across a `git stash` boundary, with the untouched stages'
   noise band reported alongside and peak RSS measured by an in-process probe.
7. **The `osm-load` stage reports its fetch-vs-build split.** The stage still prints one total; the split
   arrives as within-stage lines in the established `_OsmnxFetchReporter` shape. `SETUP_STAGES` stays 7
   entries, stage names stay lowercase-kebab, stdout keeps carrying only progress + summary
   (Architecture §Cat 8), and a warm run's split shows the fetch half as ~nothing. If the split turns
   out to be reachable **without** the adapter's lower-level access, say so — the epic assumed
   otherwise and the finding is worth recording either way.
8. **Green with no golden rebake.** Full offline suite per-directory, `basedpyright` clean, `ruff`
   clean, all 10 pinned regressions unchanged with `git status tests/e2e/goldens/` empty. A
   `pipeline/**` content-hash shift needs **no** fixture regen (Story 14.1/14.5 finding: the harness
   resolves fixture caches by geometric containment, and goldens patch `osm_load` outright, so they are
   blind to ingestion). If ingestion output does move, stop and hand the four-root rebake back as a
   separate change rather than folding it in.
9. **Docs match the code.** Architecture §Cat 3's stage-1 row / call-path note records the adapter, the
   private-API + version-pin risk, and the ownership map from AC #5. §Cat 4c is **not** touched — an
   ingestion-only change alters no on-disk format.

**Out of scope:** the S5-deep custom Overpass→graph parser (still deferred, scoped from Story 16.7's
residuals); dropping the 500 m buffer or collapsing the truncate/component double pass (Story 14.5
measured both as behavior-changing — pass 2 removes 44% of post-simplify nodes); `retain_all=True`;
per-stage multiprocess pipeline parallelization; anything query-side.

## Tasks / Subtasks

- [x] **Build and calibrate the diff harness first (AC: #1, #6)**
  - [x] Copy `.trial-cache/steeproute/{osmnx,dem}` into a scratch cache root so the r20 reference entry
        is never written through; confirm the warm response is being served (the
        `osm: Overpass response served from cache (no download)` within-stage line) before trusting any
        timing
        → the 91 MB r20 response was present as predicted; every probe run asserts the cache-hit marker
        and shouts if it sees a download, so no run silently went to Overpass
  - [x] Write the comparison over two `osm_load` results — orders, attrs, geometry coords, list tags as
        multisets — and prove it clean on a control pair of identical runs
        → per-node and per-edge hashes written **in iteration order** so a mismatch localizes to
        specific ids without either 3.5 GB graph being alive. Control pairs clean at r1 (63 of 503 edges
        carry list-valued tags, so the multiset rule is load-bearing) and at r20
  - [x] Capture the pre-change `osm-load` stage line and peak RSS from a real r20 setup replay
        → 191.39 s / 3.504 GB; see the table in the Completion Notes
- [x] **Land the single-traversal in-place largest component (AC: #2, #3, #4)**
  - [x] Add the helper plus the scoped, version-guarded seam that makes osmnx use it inside `osm_load`'s
        fetch only (see Dev Notes for the two candidate shapes and the recommendation)
        → scoped rebind, per the user's decision. No private osmnx symbol in production
  - [x] Unit-test the helper against `osmnx.truncate.largest_component` on synthetic disconnected /
        connected / empty graphs, and pin the version guard's behaviour on drift
        → 24 unit tests, all comparing against osmnx's own result rather than restating the
        replacement's logic; mutation-checked against three deliberately broken variants
  - [x] Run the AC #1 harness on the real r20 response; node/edge counts should land at 131,793 /
        327,911 (the POC's figures — verify, don't assume)
        → **130,315 / 323,192** on this machine's response, not the review's figures. Identical between
        stock and adapted, which is what the gate actually asserts
- [x] **Evaluate the consuming truncate/simplify levers (AC: #5)**
  - [x] Take truncate-pass-1 (dead intermediate, review: 19.4 → 9.3 s) and measure it end-to-end
        → taken, and the win is far larger than the review's, for a reason the review missed: osmnx's
        truncation cost is dominated by building a GeoDataFrame of every node, not by the copy.
        27.65 s → 0.63 s
  - [x] Leave truncate-pass-2 copying; add a test or assertion that pins `street_count` equality so the
        coupling can't be "optimized" away later
        → pinned twice: a unit test on the `simplified` discriminator, and an integration test on
        `street_count` equality through osmnx's real chain. The trap was *demonstrated* first — a
        deliberately over-consuming truncation shifts node 4's count 3 → 2 with byte-identical structure
  - [x] Decide on consuming `simplify_graph` from the harness + peak-RSS numbers (review: 3.82 → 2.70 GB
        for the all-consuming POC, but total wall-clock got *worse* and it is noisy) — record either way
        → **declined**, with numbers: setup's peak RSS is set by `elevation-sampling`, not by ingestion,
        so forking ~120 lines of osmnx's most complex function would not move the end-to-end ceiling
- [x] **Fetch-vs-build split on `osm-load` (AC: #7)**
  - [x] Emit the split as within-stage lines; verify a warm run and a `--verbose` run both behave, and
        that `SETUP_STAGES`, the App parse tests, and the committed `app_stdout` fixtures stay valid
        → `SETUP_STAGES` and `progress_parse.py` untouched; the split needed **no** lower-level osmnx
        access, contradicting the epic's assumption (Debug Log 4)
- [x] **Verify, measure, document (AC: #6, #8, #9)**
  - [x] Full suite per-directory (never mix `tests/unit` and `tests/integration` in one invocation),
        `basedpyright`, `ruff`; regressions with an empty goldens diff
        → 946 unit / 226 integration / 111 e2e + all 10 pinned regressions (fast **and** realistic);
        `basedpyright src tests` 0/0/0; `ruff check` + `format --check` clean
  - [x] Back-to-back r20 A/B replay; record `osm-load`, the split, CLI total, peak RSS, and the control
        stages' drift
  - [x] Architecture §Cat 3 note; leave §Cat 4c and `planning-artifacts/research/**` alone

## Dev Notes

**What 14.5 rejected is not what this story does.** Story 14.5 investigated *collapsing* the double
pass — dropping the 500 m buffer, one truncate instead of two — and correctly rejected it as
behavior-changing (pass 2 clips the buffer ring back to the exact bbox: 44% of post-simplify nodes at
r2). This story keeps the buffer, both truncate passes, both component passes, and `count_streets_per_node`
exactly where they are, and only replaces *how* the component step computes its answer. That is why
bit-identity is expected here and was not there.

**The osmnx 2.1.0 call chain, and who owns what** (`.venv/Lib/site-packages/osmnx/graph.py:485-530`):

```
A = _create_graph(response_jsons)          # A dead after B
B = truncate_graph_polygon(A, poly_buff)   # pass 1 — B dead after C
C = largest_component(B)                   # pass 1 — C dead after D
D = simplify_graph(C)                      # D IS READ AT THE END — must survive
E = truncate_graph_polygon(D, polygon)     # pass 2 — must NOT consume D
F = largest_component(E)                   # pass 2 — E dead
spn = count_streets_per_node(D, nodes=F.nodes)   # ← this is why D survives
```

`street_count` is written from `spn` onto every node and is **never read by steeproute** — but it is a
node attribute that lands in the cached pickle, so consuming pass 2 would be an invisible-to-us,
visible-to-the-harness content change. The review's §3 says "consumes the truncation/simplification
inputs" without splitting the passes; don't take it literally.

**Recommended shape: a scoped, version-guarded swap of `osmnx.truncate.largest_component` for the
duration of the fetch — not a re-implementation of `graph_from_polygon`.** `graph_from_polygon` calls
its helpers through module attributes (`truncate.largest_component`, `simplification.simplify_graph`),
so a context manager that rebinds them for exactly one call gets our implementation used while osmnx's
own orchestration — buffer, both passes, street counting, the bbox derivation `osm_load`'s docstring
warns about — runs untouched. That makes sequencing identity structural rather than something the diff
harness has to discover, and it needs **zero private osmnx symbols** for the headline win. The
alternative — vendoring `graph_from_polygon`'s body and driving
`_overpass._download_overpass_network` + `graph._create_graph` ourselves — is what the epic's wording
assumes and is the only route to a *true* fetch-vs-build boundary (see below), but it buys two private
dependencies and an orchestration copy to keep in sync. Neither is a permanent unscoped monkeypatch;
pick one, and record the reasoning where the version guard lives. The consuming-truncation lever needs
`utils_geo._intersect_index_quadrats` (private) either way, so it is a separate risk decision from the
component lever, not a free rider on it.

**The fetch-vs-build split may not actually need the adapter.** `_download_overpass_network` is a
*generator* consumed inside `_create_graph`, so "fetch time" and "parse time" interleave by
construction — materializing the responses to time them would hold every subdivided response in RAM at
once (harmless at r20's single request, not at r50). The cheaper seam already exists:
`cli/setup.py:_OsmnxFetchReporter` reads osmnx's own INFO records, and osmnx logs the per-response
outcome (`Retrieved response from cache file` / `Downloaded …`) then `Retrieved all data from API in N
request(s)` immediately after the download loop. Timestamping those records gives the split with no
private API and no `pipeline/**` edit at all. It is string-coupled to osmnx log wording — but that
coupling is already load-bearing here and already has a drift canary (`tests/unit/test_cli_setup.py`).
Prefer it; if you instead take the vendored-adapter shape, the split falls out of the call sites and
this note is moot.

**Measurement recipe (AGENTS.md §Scale target — a real setup replay).** `.trial-cache/steeproute/`
holds `areas/`, `dem/`, and `osmnx/`; copying `dem/` + `osmnx/` into a scratch root gives a
network-free full-pipeline setup run. Command:
`steeproute-setup --center 45.260,5.788 --radius 20 --cache-dir <scratch>` (~310 s at r20 as of Story
16.2). **The trap:** osmnx keys its HTTP cache on the request payload, so if the adapter perturbs the
buffered polygon or the filter string at all, the 91 MB cached response misses and the run goes to
Overpass for real — minutes plus live-data drift that would invalidate the diff. Watch for the
cache-hit within-stage line on every run. Wall-clock drifts ~30% across a session on this machine and
untouched stages have moved up to 33% between adjacent runs, so A/B strictly back-to-back
(`git stash push -- src/` → measure → `git stash pop` → measure) and report the band. Peak RSS is
reproducible to ~0.01% and needs an **in-process** `GetProcessMemoryInfo` probe (throwaway, not
committed) — external polling measures the launcher stub.

**Cache invalidation.** `pipeline/osm.py` ∈ `_PIPELINE_CONTENT_GLOBS` ([cache.py:68](src/steeproute/cache.py:68)),
so any edit — comments included — shifts `pipeline_content_hash` and the next `steeproute-setup` for an
area re-prepares once. That shift touches **no** committed fixture, golden, or test:
`check_coverage` resolves entries by geometric containment, and every golden path patches `osm_load`.
Do not reflexively regenerate anything.

**Environment.** `uv run basedpyright <files>`; `uv run pytest` per test directory. If `uv run` starts
failing en masse in `tests/e2e/test_cli_smoke.py` after a commit, that is the known stale-editable-build
flake — `uv sync --native-tls` once (never `--reinstall-package steeproute`).

### Project Structure Notes

- Source: `src/steeproute/pipeline/osm.py` (the seam inside `osm_load`) plus, if the adapter warrants
  its own module, a private sibling in `pipeline/` next to `_common.py` — the FR→module mapping already
  assigns stage 1 there, and `pipeline/**` is the hash-keyed set either way. `src/steeproute/cli/setup.py`
  if the split rides the log-record route.
- Tests: `tests/unit/test_osm.py` (helper + version guard), `tests/unit/test_cli_setup.py` (split
  lines / drift canary). A `live`- or `slow`-marked ingestion-parity test belongs in
  `tests/integration/test_osm_live.py` beside the existing drift check — the harness itself is
  throwaway scratchpad tooling, not a committed module.
- Untouched: `cache.py` on-disk format, goldens, fixture caches, `tests/fixtures/**/regenerate.py`.

### References

- [epics.md#Story 16.4](_bmad-output/planning-artifacts/epics.md:474) — the four `And` clauses: diff-harness
  gate, version pin + private-API documentation, the fetch-vs-build split riding along, and the
  re-measure-the-baseline instruction
- [performance review §2](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:262)
  — the per-phase warm r20 table (component pass 1: 25.40 → 2.24 s; pass 2: 8.17 → 0.54 s), the
  integration-caution options, and [§3](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:310)
  for the consuming-truncation numbers and their noise caveat
- [Story 14.5](_bmad-output/implementation-artifacts/14-5-reduce-osmnx-ingestion-cpu-cheap-levers.md) —
  the verified osmnx call chain, why the buffer and pass 2 are load-bearing, why goldens are blind to
  ingestion, and the reasoned-negative precedent
- [Story 16.2 Debug Log 5](_bmad-output/implementation-artifacts/16-2-setup-owned-data-cleanup-and-smoothing-resampling-fusion.md:310)
  — list-valued tag order is process-nondeterministic; plus its r20 setup replay recipe and stage table
- [spec-setup-observability-osm-load.md](_bmad-output/implementation-artifacts/spec-setup-observability-osm-load.md)
  — the `osm-load` rename, the osmnx-logging plumbing the split would build on, and its explicit
  "no fetch-vs-build timing split — belongs to Story 16.4" boundary
- [architecture.md §Cat 3](_bmad-output/planning-artifacts/architecture.md:234) — stage table, the
  ownership opt-in conventions (3a-bis `inplace=` / `consume=`), and the rotated-area stage-1 dispatch;
  [§Cat 8](_bmad-output/planning-artifacts/architecture.md:617) for stream discipline
- [pipeline/osm.py:63](src/steeproute/pipeline/osm.py:63) — `osm_load`: the square/polygon dispatch and
  the EARTH_RADIUS warning, the lazy fetch-stack imports, and the `DataSourceUnavailableError` wrap
- [AGENTS.md](AGENTS.md) — golden policy (never a silent rebake), §Scale target measurement rule,
  dev-environment commands

## Dev Agent Record

### Agent Model Used

claude-opus-5 (Claude Code, `dev-story` workflow)

### Debug Log References

1. **osmnx's truncation cost is the GeoDataFrame, not the copy — which changed the whole shape of the
   change.** The review framed §3 as "consume the truncation input" and measured 19.4 → 9.3 s. Taking
   that literally needs `utils_geo._intersect_index_quadrats`, a private symbol, and the ownership map
   makes pass 2 unconsumable. Prototyping both variants side by side at r20 showed where the time
   actually goes: osmnx materializes a GeoDataFrame of all 806k nodes, builds an r-tree over it, and cuts
   the polygon into quadrats to accelerate the index. Against the 5-vertex rectangle this pipeline
   fetches, all of that is overhead. One `shapely.intersects_xy` over the raw coordinate arrays answers
   the same question:

   | truncate pass 1 @ r20 | wall | peak RSS | private osmnx symbols |
   |---|---:|---:|---:|
   | stock osmnx | 27.65 s | 3.485 GB | – |
   | consuming, osmnx's own selection | 15.07 s | 3.147 GB | 1 |
   | consuming, `shapely.intersects_xy` | **0.63 s** | **3.133 GB** | **0** |

   The public variant is better on every axis, so the production change carries no private dependency at
   all. It does mean the *selection* is reimplemented, not just the ownership — the r20 identity gate is
   what makes that safe rather than hopeful, and the two implementations agree because the quadrats tile
   the polygon exactly and `intersects` is the predicate on both sides.

2. **The pass-2 trap is real, and invisible in graph structure.** Before writing the guard I built the
   bug on purpose: a truncation that consumes *both* passes. Result — identical node list, identical edge
   list, identical attributes, and node 4's `street_count` silently 3 instead of 2, because
   `count_streets_per_node` reads the post-simplify buffered graph *after* pass 2 returns. Anyone
   "simplifying" the `simplified`-flag discriminator away would produce a change that looks clean in
   every structural comparison. That is why the guard is a semantic flag rather than a call counter, and
   why the integration test asserts `street_count` specifically.

3. **Peak RSS: the ingestion win is real and end-to-end invisible.** Ingestion's own peak fell
   3.50 → 3.13 GB, yet whole-setup peak went 3.504 → 3.55 GB. Instrumenting per stage located it: the
   high-water mark is now set by `elevation-sampling` (3.551 GB), with ingestion at 3.126 GB. Peak
   working set is a high-water mark of resident pages — once ingestion had grown the heap to 3.50 GB the
   later stages reused those pages instead of growing; remove the ingestion spike and
   `elevation-sampling` grows the heap itself to essentially the same mark. Evidence the old peak really
   was ingestion's: the isolated stock ingestion probe and the old whole-setup run agree to three
   decimals (3.504 GB both). The reason this matters beyond bookkeeping: it is the measured argument for
   declining the `simplify_graph` fork, and it tells any future memory work at larger radii to target
   `elevation-sampling`, not stage 1.

4. **The fetch-vs-build split did not need the adapter's lower-level access.** The epic asserted
   "splitting the timing needs exactly the lower-level access this adapter introduces". It does not.
   `_download_overpass_network` is a *generator* consumed inside `_create_graph`, so materializing the
   responses to time them would hold every subdivided response in RAM at once — harmless at r20's single
   request, not above it. Instead the boundary comes from osmnx's own log records, which
   `_OsmnxFetchReporter` was already reading: the per-response outcome marks the end of the fetch and
   `graph_from_polygon`'s closing line marks the end of assembly. Zero private access, zero `pipeline/**`
   involvement, so the feature would have been available without this story. Caveat recorded in the
   handler: with several subdivided requests the fetch half absorbs the parse of every response but the
   last — exact at r20, an over-estimate above it. The build half is deliberately suppressed when no
   fetch boundary was seen, because the elapsed time to assembly would then be the *whole* stage and
   would read as a split.

5. **Two encoding traps, one self-inflicted.** `sys.stdout` is **cp1252** on this machine, so a
   character the codepage cannot encode raises `UnicodeEncodeError` mid-progress-line. The em dash I
   first used in the split line happens to be encodable in cp1252; an arrow or a theta is not. The new
   line is ASCII regardless, since the risk is free to avoid. Two pre-existing non-ASCII literals turned
   up while checking: `validator.py:267`'s `θ` is **safe** and should be left alone (violation details
   only reach disk, through explicit-UTF-8 `write_text_atomic` and `ensure_ascii=True` JSON), while
   `app/main.py:155`'s `→` in a log call genuinely fails on a cp1252 stream — reproduced, filed as a
   separate task, not fixed here. Separately, a `pathlib.write_text` without `encoding=` in my own
   tooling mangled a test file to the locale codepage; edits to repo files went through the editor after
   that.

6. **A log-parity test that passed alone and failed in the suite.** `caplog` attaches to the **root**
   logger, and `cli/setup.py:_configure_osmnx_logging` leaves `OSMnx.propagate` False for every
   non-verbose run — so once `test_cli_setup.py` had run, the osmnx records never reached caplog's
   handler. Fixed by collecting on the `OSMnx` logger directly. Worth knowing for any future test that
   asserts on osmnx's output: root-logger capture is not reliable in this suite.

### Completion Notes List

**Landed: osmnx's graph assembly runs through a scoped ownership adapter, and warm r20 ingestion drops
by a third with a bit-identical graph.** `pipeline/_osmnx_adapter.py` rebinds two **public**
`osmnx.truncate` entries for exactly the duration of one fetch; osmnx keeps driving its own pipeline, so
the 500 m buffer, both truncation passes, both component passes, the no-re-simplify between them and the
buffered-graph street count all keep their original order by construction rather than by our copying.

**Real warm r20 `steeproute-setup` replay, back-to-back across a `git stash` boundary, network-free
(cached Overpass response + cached DEM), isolated scratch cache root:**

| Stage | before | after | Δ |
|---|---:|---:|---|
| `osm-load` | 191.39 s | **129.93 s** | **−61.46 s (−32.1%)** |
| `trail-filter` (control) | 14.62 s | 14.87 s | +0.25 s |
| `polyline-smoothing` + `resampling` (control) | 28.31 s | 28.06 s | −0.25 s |
| `dem-resolve` (control) | 2.11 s | 2.34 s | +0.23 s |
| `elevation-sampling` (control) | 9.59 s | 10.20 s | +0.61 s |
| `cache-write` (control) | 4.76 s | 4.90 s | +0.14 s |
| **CLI-reported total** | **250.94 s** | **190.47 s** | **−60.47 s (−24.1%)** |
| peak working set | 3.504 GB | 3.547 GB | +0.043 GB (Debug Log 3) |

Reproducibility: two further new-code runs gave `osm-load` 129.24 s / total 189.72 s and total 190.48 s.
The `polyline-smoothing` / `resampling` split moved 3.69 → 0.88 s and 24.62 → 27.18 s between runs — I
changed neither, and their **sum** is flat, so they are reported combined rather than as two suspicious
deltas.

**Per-phase attribution inside the assembly** (instrumented warm r20; instrumentation inflates the total,
so these are for attribution, not for the headline):

| phase | stock | adapted |
|---|---:|---:|
| `_create_graph` | 25.46 s | 25.58 s |
| truncate pass 1 | 27.07 s | **0.63 s** |
| component pass 1 | 34.10 s | **2.05 s** |
| simplify | 87.23 s | 83.37 s |
| truncate pass 2 | 9.30 s | **7.05 s** |
| component pass 2 | 8.05 s | **0.53 s** |
| street count | 1.72 s | 1.78 s |
| **accounted** | **192.92 s** | **120.97 s** |

`simplify_graph` is now 69% of the assembly and is deliberately untouched.

**Identity gate (AC #1/#2).** Old-vs-new over the real cached r20 Overpass response:
**130,315 nodes and 323,192 edges identical** — node id set *and* iteration order, edge `(u,v,key)` set
*and* iteration order, every node attribute (including the semantically-inert `street_count`), every
edge attribute, graph-level attrs, and geometry coordinate sequences compared exactly. Calibrated by a
control pair of identical runs first, which is what forces the two normalizations that make the
comparison meaningful: osmnx stamps a `created_date` into the graph attrs, and multi-way tag values
arrive through a `set` whose order follows per-process string hashing, so list-valued tags compare as
multisets. Deliberate consequence: the gate cannot see a change in list-tag *order*, which is already
nondeterministic run-to-run and which both consumers read order-blind. Also gated at r1 and, through
osmnx's real `graph_from_polygon`, at fixture scale offline.

**Node/edge counts are not the review's.** The review's POC reported 131,793 / 327,911; this machine's
cached response yields 130,315 / 323,192. The gate asserts stock-vs-adapted equality, not agreement with
the review, so this is a provenance note rather than a discrepancy — and it is why the story said
"verify, don't assume".

**The baseline was re-measured, and the review's `132 → 99 s` really was only a shape.** This stage has
read 132 s (review machine), ~170 s (Story 16.1), 232-240 s (Story 16.2) and 191 s here. The −32% is
the trustworthy figure; the absolute numbers are not portable.

**Levers: two taken, one declined.**

- *Single-traversal in-place largest component* — taken. 42.15 s → 2.58 s across both passes,
  reproducing the review's shape (33.57 → 2.77 s).
- *Consuming truncation* — taken, and worth far more than the review predicted, for a reason the review
  missed (Debug Log 1). 36.37 s → 7.68 s across both passes, with **no** private osmnx symbol.
- *Consuming `simplify_graph`* — **declined.** It would mean vendoring ~120 lines of osmnx's most
  complex function (it carries its own `noqa: C901, PLR0912`) to remove one `G.copy()`. The review's
  case for it was peak RSS; Debug Log 3 measured that setup's peak is set by `elevation-sampling`, not
  by ingestion, so the fork would not move the end-to-end ceiling it was proposed to move. Recorded for
  Story 16.7 as a residual.

**Design decisions worth review attention:**

- **No private osmnx symbol in production.** The story's AC #3 budgeted for enumerating and justifying
  them; the answer is zero. Both rebound names (`truncate.largest_component`,
  `truncate.truncate_graph_polygon`) and both helpers used (`utils.log`, `shapely`) are public. The two
  private symbols that do appear are in *tests*: `_overpass._download_overpass_network` and
  `graph._create_graph`, stubbed so the offline canary can run osmnx's genuine assembly. A rename there
  fails a test loudly instead of degrading production silently.
- **The version guard declines rather than raises.** An osmnx bump outside
  `_ADAPTED_OSMNX_VERSIONS` logs a WARNING and runs stock osmnx, so an upgrade costs the optimization,
  not the ability to prepare an area. A unit test fails when the installed version drifts outside the
  set, because that silent fallback is otherwise invisible. `pyproject.toml`'s `osmnx>=2.0,<3` is
  therefore unchanged.
- **The component replacement reproduces osmnx's own log line verbatim.** Copying a third-party log
  string is normally a smell; here it keeps `--verbose` stderr identical through the swap, and
  `_ADAPTED_OSMNX_VERSIONS` pins the release whose wording it is.
- **Two `--verbose` lines do disappear** — osmnx's INFO records about the r-tree and quadrat machinery
  the truncation replacement no longer builds. Reproducing those would be a lie about work not done, so
  they are dropped and the change is recorded in the adapter docstring and §Cat 3a-quater.
- **The fetch/build split was not added to `progress.timings`.** The machine-readable dict is keyed by
  stage name and adding a dotted sub-key would widen a contract for a stdout-only feature; the split
  stays two within-stage lines.
- **No fixture regen, no golden rebake.** `pipeline/**` bytes changed, so `pipeline_content_hash` shifts
  and the next `steeproute-setup` for an area re-prepares once; committed fixture caches stay queryable
  (resolved by geometric containment) and goldens patch `osm_load` outright, so they are blind to
  ingestion. All 10 pinned regressions pass with `git status tests/e2e/goldens/` empty.

**User decisions (AskUserQuestion, 2026-07-29).** (1) Adapter shape: **scoped rebind** of
`osmnx.truncate.largest_component`, over the vendored-`graph_from_polygon` alternative. (2) Memory
lever depth: **decide from measurement** — resolved as above, taking the consuming truncation and
declining the `simplify_graph` fork.

### File List

Source:
- `src/steeproute/pipeline/_osmnx_adapter.py` — **new.** Ownership adapter: consuming
  `largest_component` and `truncate_graph_polygon` replacements, the public-shapely node selection, and
  the scoped version-guarded context manager
- `src/steeproute/pipeline/osm.py` — `osm_load` wraps its osmnx fetch in `osmnx_owned_intermediates()`
- `src/steeproute/pipeline/__init__.py` — stage-1 comment: the fetch/build split is now reported, and
  no longer claims it would need an adapter around osmnx's internals
- `src/steeproute/cli/setup.py` — `_OsmnxFetchReporter` now reports the fetch/build split (injectable
  clock, assembly-complete marker, graceful omission without a fetch boundary); module docstring

Tests:
- `tests/unit/test_osmnx_adapter.py` — **new.** 24 tests, each comparing against osmnx's own result:
  component equality (disconnected / connected / weak-vs-strong / size ties / interleaved order /
  null graph), log-line parity, truncation node-set equality against osmnx's private quadrat index,
  boundary and non-finite coordinates, the `simplified` pass discriminator, the rebind/restore contract,
  the unverified-version fallback, and the installed-version drift canary
- `tests/integration/test_osmnx_ingestion_adapter.py` — **new.** 3 tests running osmnx's genuine
  `graph_from_polygon` offline with only the two network-facing steps stubbed: graph identity through
  the real chain, a fixture guard proving all four trimming passes do work, and `street_count` equality
- `tests/unit/test_cli_setup.py` — reporter tests updated for the split text; 3 new tests (the split
  itself, build-half reported once, build half omitted without a fetch boundary)

Docs:
- `_bmad-output/planning-artifacts/architecture.md` — §Cat 3 stage-1 row + new §Cat 3a-quater
  (ownership inside a third party's pipeline: the rebind rationale, the non-uniform ownership map, the
  reference-cycle fact, the node-selection change, the measurements, the peak-RSS finding, and the
  `--verbose` delta). §Cat 4c deliberately untouched — on-disk content is unchanged
- `tests/fixtures/app_stdout/format-inventory.md` — A2 within-stage line shape for the split
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/16-4-osmnx-in-place-component-consume-ingestion-adapter.md`

Not committed (investigation scratch, under the session scratchpad):
- `ingest_probe.py` — the AC #1 identity harness (canonical digest + positional differ)
- `phase_probe.py` — per-phase timing/memory attribution inside one `osm_load`, incl. the rejected
  lever prototypes
- `setup_replay.py` — in-process `steeproute-setup` runner with the peak-working-set probe
- `probe_pass2_trap.py` — the demonstration that over-consuming truncation shifts `street_count`

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-29 | Yann (claude-opus-5) | Story 16.4 drafted (`create-story`). Found the warm 91 MB r20 Overpass response already cached locally (Story 14.5 could not); encoded the non-uniform ownership map that makes truncate-pass-2 unconsumable (`count_streets_per_node` reads the post-simplify buffered graph); recommended the scoped rebind over the vendored adapter; flagged that the fetch/build split may not need lower-level access. Two decisions parked for the user. |
| 2026-07-29 | Yann (claude-opus-5) | Story 16.4 dev — landed. User chose the scoped rebind and delegated lever depth to measurement. Component step 42.15 → 2.58 s; truncation 36.37 → 7.68 s via public-shapely node selection (the review's private-API route measured 24× slower, Debug Log 1); `simplify_graph` fork declined on measured peak-RSS grounds. Warm r20 replay: `osm-load` 191.39 → 129.93 s (−32.1%), CLI total 250.94 → 190.47 s (−24.1%), graph identical over 130,315 nodes / 323,192 edges. `osm-load` now reports its fetch/build split, which turned out not to need the adapter at all. Full suite + all 10 pinned regressions green, `basedpyright` 0/0/0, no golden rebake. Status → review. |

## Open Questions (for the user, before dev)

1. **Which shape for the intervention?** Dev Notes recommends the scoped context-managed swap of
   `osmnx.truncate.largest_component` (no private APIs, osmnx's own orchestration still runs, so
   sequencing identity is structural). The epic's wording leans toward vendoring
   `graph_from_polygon`'s body around `_overpass._download_overpass_network` + `graph._create_graph`
   — more private surface and an orchestration copy to maintain, but it is the only route to a true
   fetch-vs-build timing boundary. Confirm the preference, or leave it to the dev's measured judgment.
2. **How far to push the memory lever?** Consuming truncate-pass-1 is cheap and safe. Consuming
   `simplify_graph` means vendoring ~120 lines of osmnx to remove one `G.copy()`, for a peak-RSS win
   the review measured at ~1.1 GB but with *worse* total wall-clock. Take it, or record the decline?
