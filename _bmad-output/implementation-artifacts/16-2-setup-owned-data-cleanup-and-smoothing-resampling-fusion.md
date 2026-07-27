# Story 16.2: Setup owned-data cleanup + smoothing/resampling fusion (one content-hash batch)

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want setup to stop copying graphs it is about to discard and stop rebuilding an intermediate graph
between smoothing and resampling,
so that setup CPU and peak memory drop, landed as one cache-invalidation cycle.

## Acceptance Criteria

1. **Consuming elevation attachment.** `sample_elevation` gains a keyword-only ownership opt-in
   (§Cat 3 3a-bis naming: `inplace=`/`consume=` by what is forfeited) that skips the full
   `graph.copy()`; `attach_elevation` opts in, since `cli/setup.py` owns the graph and never reads a
   pre-stage-5 version. The default copying path and its purity test are unchanged. Per-edge geometry
   gathering becomes one bulk `shapely.get_coordinates(..., return_index=True)` call; the rasterio
   `rowcol` + full-band-read lookup, the half-open bounds convention, the nodata/non-finite masks, and
   the first-offending-edge error ordering (bounds before nodata, edge-iteration order) are unchanged.
2. **`vertices_resampled` is bit-identical** on the `grenoble_small` fixture — the existing
   `_scalar_reference_sample_elevation` oracle still passes on the new code, on both paths.
3. **Fused smoothing → resampling.** An internal fused path keeps coordinates flat across stages 3-4
   (`_collect_linestrings` → smooth → resample → one `_build_from_flat`) and `build_graph_geometry`
   uses it; the public `smooth_polylines` / `resample_edges` stay pure, exported, and behaviourally
   unchanged for tests and external callers. Fused output is **bit-equal** to composing the two public
   stages: same node set, same ordered edge set, `np.array_equal` on every coordinate — including the
   degenerate-edge cases (both stages' validity masks conjoined at the single build) and with the
   `_drop_short_edges` / `_assert_non_empty` guards still running on the built graph.
4. **The setup stage-line surface is unchanged.** `polyline-smoothing` and `resampling` remain two
   distinct `StageProgress` lines in the same order, so the App's `SETUP_STAGES` 7-stage list, the
   committed `app_stdout` fixture, and the stdout format inventory need no edit (see Dev Notes for the
   seam placement and for what the single-line alternative would cost).
5. **Consuming cache write.** `write_entry` gains a keyword-only consuming opt-in that pops `geometry`
   from the owned graph instead of `graph.copy()`-ing first; `cli/setup.py` opts in (it never touches
   the graph after `write_entry`). The copying default is preserved for every other caller. Payload
   content is unchanged either way: same stripped graph, same ragged coords/offsets in
   `graph.edges()` order, same `ValueError` on a non-LineString edge, same `read_entry` round-trip.
6. **One content-hash batch, verified against the pre-change cache.** All of the above land together
   as a single `pipeline_content_hash` shift. The `grenoble_small` e2e fixture cache is regenerated
   once (`regenerate_cache.py`) and the regenerated entry is proved content-equal to the committed one
   (node/edge sets, edge attrs, coordinates, elevations bit-equal) **before** the old entry is
   replaced. Pinned regression goldens pass with **no rebake**. If some site provably cannot be
   bit-equal, it gets exactly **one** documented rebake batched into this story, with the numeric
   reason recorded here and in the commit (AGENTS.md golden policy — never silent).
7. **Suite, types, coverage green.** The full offline suite passes, `basedpyright` is clean, and the
   95% bar on `pipeline/` and `cache.py` is held by direct unit tests of the new consuming paths and
   the fused path — not incidental coverage.
8. **Benchmarks added before the change.** `tests/benchmarks/` records before/after for each touched
   stage: the existing stage-3/4/5 benchmarks, a new fused smooth+resample benchmark, and a new
   cache-write payload benchmark — with copying vs consuming variants where both paths exist.
9. **A real warm r20 setup replay is the acceptance number.** CLI stage lines
   (`polyline-smoothing`, `resampling`, `elevation-sampling`, `cache-write`), external process wall,
   and peak RSS are captured before and after on a real `steeproute-setup` r20 run, back-to-back.
   Component benchmarks are **not** extrapolated to r20 (AGENTS.md §Scale target). Review anchors to
   reproduce in shape, not value: elevation stage 14.20 s of which ~5.4 s is the copy and 3.77 → 1.51 s
   the gather; ~7.44 s / ~7.84 s of profiled rebuild inside smoothing / resampling; cache-write 7.70 s
   of which ~5.4 s is the copy.
10. **Docs match the code.** Architecture §Cat 3 (the 3a-bis opt-in inventory + a stage-3/4 fusion
    note in the stage table's neighbourhood) and §Cat 4c/4d (the consuming write path) are updated,
    along with every touched docstring.

**Out of scope:** the query-side stage 6-7 flat-representation fusion (review §10 — not in this
story's epic AC); geometry-optional `read_entry` and schema v3 (Story 16.3, which may co-land its
regen with this one if they ship together); osmnx ingestion ownership (16.4); the per-edge Python
loops in `_drop_short_edges` / `_assert_finite_elevations`; any change to the
`vertices_resampled` list-of-tuples representation; the review's quality-altering non-optimizations.

## Tasks / Subtasks

- [x] Pin the "before" measurement first (AC: #8, #9)
  - [x] Add the missing benchmarks next to the existing setup-stage ones
        ([test_setup_stages.py:58](tests/benchmarks/test_setup_stages.py:58)): fused smooth+resample
        vs the two-stage composition, `sample_elevation` copying vs consuming, and cache-write payload
        build copying vs consuming. Params stay pinned locally per
        [tests/benchmarks/conftest.py](tests/benchmarks/conftest.py:6). For the consuming variants use
        `benchmark.pedantic(setup=...)` so the required per-round `graph.copy()` sits outside the
        measured region (Story 16.1's Debug Log item 4 — the first attempt measured the wrong path)
        → 6 benchmarks (3 old-shape / 3 new-shape), each pair measured in ONE run so the comparison
        is drift-immune; `rounds=20, warmup_rounds=1` inline per the existing convention
  - [x] Capture the baseline warm r20 setup run (recipe in Dev Notes): the four stage lines, external
        wall, peak RSS, and a content fingerprint of the produced entry to diff against later
        → captured, but the *authoritative* baseline is the back-to-back `git stash` A/B run later in
        the story (see Debug Log 1); this first run became the determinism control instead
- [x] Consuming + bulk-gather elevation attachment (AC: #1, #2)
  - [x] Add the opt-in to [sample_elevation](src/steeproute/pipeline/dem.py:49) and replace the
        per-edge `np.asarray(geom.coords)` + `np.concatenate` gather
        ([dem.py:158](src/steeproute/pipeline/dem.py:158)) with one bulk call; keep the geometry
        type-check loop and its `pipeline.dem:` message shape
        → `inplace: bool = False` (3a-bis: the stage only *adds* an attribute); bulk gather extracted
        to `_common.flat_coordinates`, now shared with `smoothing._collect_linestrings`
  - [x] Opt in from [attach_elevation](src/steeproute/pipeline/__init__.py:202)
  - [x] Unit tests in [tests/unit/test_dem.py](tests/unit/test_dem.py:390): the scalar-reference
        bit-identity test passes on both paths; the default still leaves the input unmutated; the
        opt-in returns the same object with `vertices_resampled` attached; both error orderings
        (out-of-bounds, nodata) still name the same edge
        → 3 new tests; the pre-existing `test_fixture_pipeline_bit_identical_to_scalar_reference`
        (the pre-14.1 per-point oracle) passes untouched
- [x] Fused smoothing → resampling (AC: #3, #4)
  - [x] Add the fused internal path in
        [pipeline/smoothing.py](src/steeproute/pipeline/smoothing.py:77) reusing the existing
        primitives (`_collect_linestrings`, `_valid_edges_mask`, `_build_from_flat`,
        `per_edge_searchsorted`); wire it into
        [build_graph_geometry](src/steeproute/pipeline/__init__.py:189) with the seam split described
        in Dev Notes
        → `FlatPolylines` + `collect_polylines` / `smooth_polylines_flat` /
        `resample_polylines_flat`; the two public stage functions are now thin wrappers over the same
        internals, so there is exactly one copy of the numerics
  - [x] Unit test: fused vs `resample_edges(smooth_polylines(g))` on the OSM fixture — coordinates
        bit-equal, same ordered edge list, same node set; plus the degenerate-edge cases already
        pinned per-stage ([test_smoothing.py:239](tests/unit/test_smoothing.py:239)) re-asserted
        through the fused path
        → 3 new tests, including one pinning the arc-length poisoning hazard (Debug Log 2)
  - [x] Integration check that the end-to-end setup pipeline output is unchanged
        ([tests/integration/test_pipeline_end_to_end.py](tests/integration/test_pipeline_end_to_end.py))
        rather than adding a parallel harness
        → the existing end-to-end + cache-roundtrip integration tests exercise the fused orchestrator
        unchanged (223 pass); the *content* gate is the r20 and fixture regen diffs below
- [x] Consuming cache write (AC: #5)
  - [x] Add the opt-in to [write_entry](src/steeproute/cache.py:643) /
        [_graph_to_payload](src/steeproute/cache.py:544); opt in at
        [cli/setup.py:224](src/steeproute/cli/setup.py:224) with a comment naming why ownership is
        exclusive
  - [x] Unit tests in [tests/unit/test_cache.py](tests/unit/test_cache.py): payload dicts from both
        paths are equal (graph content + `np.array_equal` on coords/offsets), the default leaves the
        caller's `geometry` attributes intact, the opt-in strips them from the caller's graph, and the
        non-LineString `ValueError` fires on both paths. Round-trip via `read_entry` unchanged
        ([tests/integration/test_cache_roundtrip.py](tests/integration/test_cache_roundtrip.py))
        → 5 new tests. The round-trip test asserts read-back content equality, **not** `graph.pkl`
        byte equality — see Debug Log 3
- [x] Verify, regenerate, record (AC: #6, #7, #9, #10)
  - [x] Regenerate the `grenoble_small` fixture cache per its
        [README](tests/e2e/fixtures/grenoble_small/README.md) and diff the new entry's graph against
        the committed one before committing the replacement; then `uv run pytest
        tests/e2e/test_pinned_regressions.py` with `git status tests/e2e/goldens/` empty
        → regenerated, diffed, and **committed** (both entries re-keyed by the pipeline-hash change).
        All 10 pinned regressions (fast + realistic tiers) pass against the regenerated fixture with
        **no golden change** — `git status tests/e2e/goldens/` empty. See Debug Log 4 for the stale
        pre-existing drift this surfaced and why it does not block the regen
  - [x] Full offline suite per-directory (AGENTS.md: never mix `tests/unit` and `tests/integration`
        in one invocation) + `uv run basedpyright` on the touched files
        → 888 unit / 223 integration / 114 e2e (slow tier included), all pass; `basedpyright src tests`
        0 errors; `ruff check` clean
  - [x] Re-run the warm r20 setup measurement and record before/after in the Completion Notes,
        including which stage lines moved and which control (untouched) stages moved, so the noise
        band is visible
  - [x] Update Architecture §Cat 3 and §Cat 4c/4d
        → §Cat 3 stage table + 3a-bis inventory + new 3a-ter (fused stage pairs); §Cat 4c consuming
        write path incl. the pickle-bytes caveat

## Dev Notes

**Land and verify the three changes one at a time, then co-commit.** Each is independently
equality-checkable; only the "one content-hash batch" AC needs them together. The measured components
are in the performance review — read
[§4 (elevation)](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:329),
[§5 (fusion)](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:353),
and [§9 (cache write)](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:500)
before starting each part. None of the three has a measured *combined stage* result — that is what
AC #9 buys.

**Fusion correctness is about the two validity masks, not the math.** Today stage 3 drops degenerate
edges via `_valid_edges_mask` over the raw coords and stage 4 drops them again over the *smoothed*
coords; stage 4 never sees a stage-3 reject. Fused, every edge stays in the flat arrays through both
computations and the single `_build_from_flat` must apply `valid_raw & valid_smoothed`. That is safe
because every array op is per-edge (`reduceat` over `offs`, the per-edge monotone
`per_edge_searchsorted`), so a doomed row cannot perturb a surviving neighbour — but a doomed row must
not *raise* either: `total == 0` already flows through `np.maximum(1, ...)` and the
`np.where(total > 0.0, ...)` spacing guard. Bit-equality otherwise follows from float64 identity: the
LineString round-trip the fusion removes (`shapely.linestrings` → `get_coordinates`) is exact, and
`mean_lat` / arc-length / searchsorted are computed per edge either way. Verify empirically anyway.

**Keep both stage seams.** Fusion is about not materializing the intermediate graph, not about merging
the timeline. Structure the orchestrator so `polyline-smoothing` times collect+smooth and `resampling`
times resample+build+guards, with the flat arrays handed between them — the two stage names, the App's
[SETUP_STAGES](src/steeproute/app/cli_adapter/progress_parse.py:35), the committed
`tests/fixtures/app_stdout/setup_cache_miss.stdout.txt`, and the e2e stage-order assertion in
`tests/e2e/test_steeproute_setup.py:182` all stay as they are. The single-fused-stage-line alternative
is defensible but costs edits in all four of those plus
`tests/fixtures/app_stdout/format-inventory.md` and the app parse tests — don't spend that for a
cosmetic change. Holding the flat arrays in the orchestrator bends §Cat 3's `stage(graph) -> graph`
shape; that is the same trade 3a-bis already documents, so note it in the docstring rather than
inventing a wrapper type.

**Sharing the coordinate gather.** `pipeline/dem.py` needs the bulk gather that
[_collect_linestrings](src/steeproute/pipeline/smoothing.py:471) already implements, and stage modules
should not import each other — put the graph-agnostic half (`geoms → (coords, offs)`) in
[pipeline/_common.py](src/steeproute/pipeline/_common.py:4) next to `empty_like` /
`per_edge_searchsorted`, and let each module keep its own type-check loop so the two
`TypeError` message prefixes (`pipeline.smoothing:` / `pipeline.dem:`) stay put — tests assert them.

**Optional, only if it benchmarks:** the surviving per-vertex cost in stage 5 is the
`vertices_resampled` tuple rebuild ([dem.py:226](src/steeproute/pipeline/dem.py:226)). Building each
edge's list from three `.tolist()` slices instead of `float(arr[j])` per component is bit-identical
(`.tolist()` yields the same float64 values) and cheap to try. Do not go further — the
representation itself is Story 16.3/Q4 territory.

**Cache invalidation and what actually needs regenerating.** All three files are inside
`_PIPELINE_CONTENT_GLOBS` ([cache.py:68](src/steeproute/cache.py:68) — `cache.py` itself is *not*,
but `pipeline/**` is), so `pipeline_content_hash` shifts and a future `steeproute-setup` writes under
a new key. Query reads are unaffected: `check_coverage` resolves by geometric containment and never
compares the content hash, so `.trial-cache/` and the committed fixture caches stay queryable
(confirmed in Story 16.1). Goldens carry route metrics and canonical hashes only — no provenance
hash — so a fixture regen moves them **only if pipeline output content moved**, which is exactly the
signal AC #6 wants. Note also Story 16.1's discovery that `Manifest.from_dict` rejects any non-current
`schema_version` outright ([16.1 Debug Log](_bmad-output/implementation-artifacts/16-1-query-orchestration-owned-filter-lean-contracted-graph-validation-context.md:274)),
so a hand-migrated local cache stays hand-migrated.

**Measurement recipe (AGENTS.md §Scale target — a real setup replay, not a micro-benchmark).**
`.trial-cache/steeproute/` holds `areas/`, `dem/`, and `osmnx/`; copying `dem/` + `osmnx/` into a
scratch cache root gives a **network-free full-pipeline** setup run (cached Overpass response + cached
DEM raster) that never risks the r20 reference entry. Command shape:
`steeproute-setup --center 45.260,5.788 --radius 20 --cache-dir <scratch>` — note `--area-cap` no
longer exists on either CLI (commit `ac26635`), so Story 16.1's recorded command must be adjusted.
Wall-clock on this machine drifted ~30% across a session and untouched stages moved up to 33% between
adjacent runs, so take the A/B strictly back-to-back (`git stash push -- src/` → measure →
`git stash pop` → measure) and report the noise band with the result. Peak RSS was reproducible to
0.01% and is the cleanest evidence available; measure it with an in-process `GetProcessMemoryInfo`
probe (throwaway, not committed) — external polling measures the launcher stub.

**Environment.** `uv run basedpyright <files>`; `uv run pytest` per test directory. If `uv run` starts
failing en masse in `tests/e2e/test_cli_smoke.py` after a commit, that's the known stale-editable-build
flake — `uv sync --native-tls` once (never `--reinstall-package steeproute`).

### Project Structure Notes

- Files touched: `pipeline/dem.py`, `pipeline/smoothing.py`, `pipeline/_common.py`,
  `pipeline/__init__.py`, `cache.py`, `cli/setup.py` — each the module the FR→module mapping already
  assigns to this behaviour; no new modules.
- Tests land in the mirrored existing files (`tests/unit/test_dem.py`, `tests/unit/test_smoothing.py`,
  `tests/unit/test_cache.py`) per Architecture §Test organization; benchmarks extend
  `tests/benchmarks/test_setup_stages.py` + `conftest.py` (a fourth layer §Cat 11 does not document —
  follow the existing modules' conventions: `pytestmark = pytest.mark.benchmark`, session-scoped
  fixtures, locally pinned params).
- The benchmark fixture chain in `tests/benchmarks/conftest.py:140` deliberately calls the
  orchestrator's private guard prunes so each stage's input matches production; a fused-path benchmark
  should keep that property rather than re-deriving inputs.

### References

- [epics.md#Story 16.2](_bmad-output/planning-artifacts/epics.md:421) — this story's Given/When/Then
  and the epic's tiered-confidence framing (Batch B: measured components, real-replay acceptance)
- [sprint-change-proposal-2026-07-24-ownership-oriented-performance.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-ownership-oriented-performance.md:76)
  — confidence table and per-story guardrails ("bit-equal on `grenoble_small`; 1 batched regen")
- [performance review §4 / §5 / §9 / Batch B](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:329)
  — measured components, plus the [setup baseline table](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:104)
- [architecture.md §Cat 3](_bmad-output/planning-artifacts/architecture.md:234) — stage table,
  pure-stage boundary (3a), ownership opt-ins (3a-bis, line 258), edge-attribute contract (3c)
- [architecture.md §Cat 4b/4c/4d](_bmad-output/planning-artifacts/architecture.md:321) — cache-key
  inputs incl. the pipeline content hash, schema-v2 on-disk payload, atomic write order
- [architecture.md §Cat 11](_bmad-output/planning-artifacts/architecture.md:1022) — coverage bars
  (95% on `pipeline/`, `cache.py`), zero-tolerance golden gate, `skip`/`xfail` prohibition
- [Story 16.1](_bmad-output/implementation-artifacts/16-1-query-orchestration-owned-filter-lean-contracted-graph-validation-context.md:274)
  — the `consume=` convention as landed, the measurement blockers, and the r20 setup A/B result
- [tests/e2e/fixtures/grenoble_small/README.md](tests/e2e/fixtures/grenoble_small/README.md) —
  offline cache regeneration + `update-regression` commands, and the "regenerate after setup-side
  pipeline changes" rule
- [AGENTS.md](AGENTS.md) — golden policy, Scale-target measurement rule, dev-environment commands

## Dev Agent Record

### Agent Model Used

claude-opus-5 (Claude Code, `dev-story` workflow)

### Debug Log References

1. **The r20 "before" number could not come from a pinned pre-change run.** Story 16.1 recorded ~30%
   session drift on this machine; here the untouched `osm-download` stage moved +8.1 s (+3.5%) between
   two adjacent runs. Both A/B runs are therefore strictly back-to-back across a
   `git stash push -- src/` boundary, with the warm caches (`dem/` + `osmnx/` copied out of
   `.trial-cache/`) in an isolated scratch root so the r20 reference entry was never at risk. `areas/`
   did not need clearing: the pipeline-hash change gives old and new code different cache keys, so
   neither run could hit the other's entry.
2. **Fusing the stages exposed a latent arc-length poisoning hazard.** Resampling accumulates segment
   lengths in one *global* `np.cumsum` and then subtracts each edge's base, so a single non-finite
   coordinate makes `cum` NaN for **every subsequent edge**. Today that is unreachable, because stage 3
   drops non-finite edges before stage 4 ever sees them — but a fused pass that carried rejects along
   "to be masked at the rebuild" would have silently corrupted good edges. Degenerate edges are
   therefore dropped at the collect step (`_compact`, a no-op when everything is valid). This also
   fixes the same latent bug in the public `resample_edges` when called directly on a graph containing
   a non-finite edge alongside good ones; a new test pins it.
3. **`graph.pkl` bytes are not a stable identity for a graph.** My first cache-write test asserted
   byte-equality between the copying and consuming paths and failed by 19 bytes. Cause: networkx
   caches its lazily built `adj` / `succ` / `edges` view objects in the graph's `__dict__`, and
   `MultiDiGraph.copy()` materializes a different subset of them than the graph the setup CLI hands
   over — so the pickle carries incidental view artifacts. The test now compares what `read_entry`
   returns, and §Cat 4c records the caveat.
4. **The committed `grenoble_small` square entry was stale relative to today's pipeline — predating
   this story.** Comparing it against a fresh regeneration showed 9,855 coordinate rows differing by
   at most **2.8e-14 deg** and 1,178 edges' elevations differing at that scale: the float-reordering
   drift Story 14.2 documented and accepted (its own note measured ~1.4e-14 on this fixture). The old
   manifest confirmed the provenance — `pipeline_content_hash` `df917f35…` versus the rotated entry's
   `ad1f159e…`, i.e. the square entry had not been rebuilt since Story 15.3 deliberately left it
   byte-for-byte. Attributing that drift to Story 16.2 would be wrong, so this story's own gate was
   run the honest way instead: **regenerate with old code, regenerate with new code, diff those two.**
   Exactly identical, both entries — 16.2 contributes nothing.

   My first call was to leave the committed fixture alone, to keep pre-existing drift out of this
   story's diff. That was wrong, and for a bad reason: I was avoiding a hypothetical golden rebake
   without testing whether one would actually occur. It does not — all 10 pinned regressions (fast +
   realistic tiers) pass against the regenerated fixture with zero golden movement, because the drift
   is ~1e-14 and the goldens pin route metrics and canonical edge hashes, not raw coordinates. With
   that measured, regenerating is pure upside and the fixture is committed: its `pipeline_content_hash`
   is honest again, and — the substantive point — the goldens only cover setup at all while the
   fixture reflects current setup code. Leaving it frozen would have perpetuated exactly the coverage
   gap this story's own r20 diff had to work around.
5. **osmnx ingestion is non-deterministic in list-valued tag ORDER, run to run.** The first r20 diff
   reported 10,960 edges differing on non-geometry attributes (`highway`, `name`, `sac_scale`, …), all
   of the shape `['unclassified', 'footway']` vs `['footway', 'unclassified']`. Two runs of
   **identical** code reproduce it (4,426 such edges), so it is pre-existing, not this story's: osmnx
   collects multi-way tag values through a set and Python randomizes string hashing per process.
   It is **cosmetic** — both consumers are order-insensitive by construction (`classify_highway` is
   any-of over trail tags, `max_sac_rank` takes the max) — so the r20 content gate compares list-valued
   tags as multisets and everything else exactly. Worth knowing before Story 16.4 builds its
   "exact old/new graph diff harness" over ingestion: a naive attribute diff there will drown in this.

### Completion Notes List

**All three changes landed and the setup pipeline's output is unchanged at r20 scale.** The headline
gate: a warm r20 setup run under old code and under new code produce prepared graphs that are
**identical** — 130,267 nodes, 322,939 edges, 2,843,777 geometry coordinates, and all 2,843,777
`vertices_resampled` triples equal, plus every edge attribute (list-valued tags compared as multisets
per Debug Log 5). A control pair (two runs of the *same* new code) shows the same result, so the
comparison method is calibrated.

**r20 back-to-back A/B** (warm `dem/`+`osmnx/` caches, isolated scratch root, offline; old code via
`git stash`):

| Stage | old | new | Δ |
|---|---:|---:|---|
| `osm-download` (untouched control) | 232.45 s | 240.53 s | +3.5% ← noise band |
| `trail-filter` (untouched control) | 16.12 s | 16.76 s | +4.0% ← noise band |
| `dem-resolve` (untouched control) | 2.89 s | 2.65 s | −8.3% ← noise band |
| `polyline-smoothing` | 9.66 s | **3.66 s** | **−62%** |
| `resampling` | 34.87 s | **29.04 s** | **−17%** |
| → stages 3+4 combined | 44.53 s | **32.70 s** | **−26.6% (−11.8 s)** |
| `elevation-sampling` | 22.99 s | **10.98 s** | **−52% (−12.0 s)** |
| `cache-write` | 11.77 s | **6.34 s** | **−46% (−5.4 s)** |
| CLI-reported total | 330.94 s | **310.21 s** | **−6.3% (−20.7 s)** |
| peak working set | 3.501 GB | 3.501 GB | **0.0%** |

The three targeted stages went 79.29 s → 50.02 s (**−37%, −29.3 s**) while the three untouched control
stages moved +8.5 s in aggregate — which is why the CLI total only shows −20.7 s. The per-stage
attribution is the trustworthy part; the total is real but noisy at its edges, exactly as in Story 16.1.

**Peak RSS did not move, and that is a finding, not a measurement failure.** 3.501 GB both runs (this
probe was reproducible to 0.001 GB in 16.1 too). Setup's high-water mark is set during
`osm-download`/ingestion — osmnx building a 322k-edge graph from a 91 MB Overpass response — which this
story does not touch. Removing three whole-graph copies *after* that peak cannot lower it. So the
epic's NFR2 framing holds for the query side (16.1: −24.7%) but **setup peak memory is Story 16.4's
territory**, not 16.2's. The story's AC #9 asked for the number; the number is "unchanged", recorded
honestly rather than quietly dropped.

**Component benchmarks** (`grenoble_small`, both shapes measured in one run so drift-immune; `Min` is
the meaningful statistic for these):

| Benchmark | old shape | new shape | Δ |
|---|---:|---:|---|
| cache-write payload build | 8.87 ms | **1.58 ms** | **−82%** |
| stages 3+4 | 23.89 ms | **16.61 ms** | **−30%** |
| stage 5 elevation | 33.67 ms | **27.03 ms** | **−20%** |

A second full benchmark run (after the fixture regen) reproduced the direction with the usual
small-fixture spread: 5.48 → 1.33 ms, 21.07 → 10.54 ms, 29.63 → 23.20 ms. These agree in *direction*
with r20 but not in magnitude (a 1.5 km fixture has ~2k edges vs r20's 322k), and per AGENTS.md
§Scale target they are kept as guardrails only — the r20 table carries the claim.

**Design decisions worth review attention:**

- **The public stage functions are now thin wrappers over the fused internals**, not parallel
  implementations: `smooth_polylines` = `collect` → `smooth_flat` → build, `resample_edges` =
  `collect` → `resample_flat`. There is one copy of the numerics, so the fused path cannot drift from
  the path the unit tests pin — the same anti-drift argument Story 16.1 used for `filter_trails`'
  shared `keep()` predicate.
- **Both stage seams were kept** (`polyline-smoothing` + `resampling`), so the App's 7-entry
  `SETUP_STAGES`, the committed `app_stdout` fixture, `format-inventory.md`, and the e2e stage-order
  assertion needed no edit. The timings stay honest: the smoothing line no longer includes a graph
  build because there no longer is one, which is why it dropped 62% while resampling — which absorbed
  the single build — dropped only 17%.
- **`inplace=` for `sample_elevation`, `consume=` for `write_entry`**, per §Cat 3 3a-bis's rule about
  what the caller forfeits: stage 5 only *adds* `vertices_resampled`, while the payload build *removes*
  `geometry`. The review's text suggested `consume` for elevation; the architecture's own convention
  says otherwise, and I followed the architecture.
- **`sample_elevation(inplace=True)` still validates before it writes.** Every bounds/nodata check runs
  over the whole flat array before the first `vertices_resampled` assignment, so a `DEMCoverageError`
  leaves the caller's graph un-annotated — pinned by a test.
- **`_graph_to_payload(consume=True)` can leave a graph half-stripped** if the contract check trips
  mid-iteration. Documented rather than defended: that error means the caller violated the
  post-stage-5 contract, and the setup CLI's only response is to exit.
- **The `grenoble_small` fixture cache is regenerated and committed** (AC #6), which re-keys both
  entry directories because the pipeline content hash moved. No golden changed. This also refreshes a
  square entry that had been stale since before Story 14.2 — that pre-existing ~1e-14 drift rides
  along here rather than being attributed to this story; Debug Log 4 records the evidence separating
  the two, including the old-code-regen vs new-code-regen diff proving 16.2 contributes nothing.

### File List

Source:
- `src/steeproute/pipeline/dem.py` — `sample_elevation` `inplace=` opt-in + bulk coordinate gather
- `src/steeproute/pipeline/_common.py` — `flat_coordinates` (shared bulk gather)
- `src/steeproute/pipeline/smoothing.py` — `FlatPolylines`, `collect_polylines`,
  `smooth_polylines_flat`, `resample_polylines_flat`, `_compact`; public stages become wrappers
- `src/steeproute/pipeline/__init__.py` — fused stage-3→4 orchestration (two seams kept),
  `attach_elevation` consumes
- `src/steeproute/cache.py` — `_graph_to_payload` / `write_entry` `consume=` opt-in
- `src/steeproute/cli/setup.py` — `write_entry(..., consume=True)`

Tests:
- `tests/unit/test_dem.py` — 3 consuming/bulk-gather tests
- `tests/unit/test_smoothing.py` — 3 tests (fused vs two-stage bit-equality, degenerate masks,
  arc-length poisoning guard)
- `tests/unit/test_cache.py` — 5 consuming-payload tests
- `tests/benchmarks/test_setup_stages.py` — 6 new benchmarks (3 old-shape / 3 new-shape pairs)

Fixture data (regenerated, one batched cache-invalidation event):
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/index.json`
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/areas/98f4af770f7dae31/` — square entry,
  **new** (replaces `4c348169d4d0bb0c/`, deleted)
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/areas/cb392681518224d8/` — rotated entry,
  **new** (replaces `d0bb61a840431553/`, deleted)

Docs:
- `_bmad-output/planning-artifacts/architecture.md` — §Cat 3 stage table + 3a-bis inventory + new
  3a-ter (fused stage pairs); §Cat 4c consuming write path + pickle-bytes caveat
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/16-2-setup-owned-data-cleanup-and-smoothing-resampling-fusion.md`
