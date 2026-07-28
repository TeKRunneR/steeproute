# Story 16.3: Geometry-optional query load and schema-v3 cache

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want the query to stop reconstructing per-edge geometry it never reads and, where proven safe, the
cache to stop storing post-stage-5 geometry at all,
so that query load and cache size drop.

## Acceptance Criteria

1. **The "no consumer reads it" claim is proven, not assumed.** A recorded audit covers every
   post-stage-5 reader of a prepared graph — query stages 6-7, `filter_trails` redux, climb detection,
   contraction, solver, validator, `output.render`, the App's `cli_adapter`, the regression harness,
   and setup's own cache-hit branch — and shows none reads the `geometry` edge attribute. The
   information is not lost either way: `vertices_resampled` carries the same vertices, already pinned by
   [test_fixture_pipeline_vertices_resampled_matches_geometry_coords](tests/unit/test_dem.py:564). If
   the audit finds a real consumer, it is either converted to `vertices_resampled` or the story stops
   after AC #2 and records why.
2. **The query never reconstructs cached geometry.** A cache read on the query path builds zero
   `LineString`s, and the graph it returns is content-identical for every consumer that runs: same
   nodes, same ordered `(u, v, key)` edge list, same node/edge attributes, same `vertices_resampled`,
   same metrics/tags. Setup's cache-hit read (which touches only the manifest) gets the same path. No
   dead knob is left behind — if a parameter gates the skip, some in-tree caller must pass each value.
3. **The cache stops storing post-stage-5 geometry (schema v3).** The `graph.pkl` payload advertises
   the new version and omits the geometry arrays entirely; the write path stops building them; the
   post-stage-5 LineString contract check on write is **kept** (a graph reaching `write_entry` without
   geometry is still a `ValueError` — that is the setup pipeline's own invariant, not a serialization
   detail); and the read-side ragged-array reconstruction Story 13.2 added is **deleted**, not left
   dormant. If AC #1's audit blocks this, AC #2 alone is the deliverable and the reason is recorded
   here and in Architecture §Cat 4c.
4. **Pre-existing v2 entries have a decided, tested fate.** Either they still read (geometry arrays
   ignored) or they fail as a clean `CacheCorruptedError` → query exit 2 / setup re-prepare. A unit test
   pins whichever is chosen, and the choice plus its cost is recorded. No path may silently return a
   graph missing attributes a consumer expects.
5. **All five committed fixture entries land as one conversion event, with no golden rebake.**
   `belledonne`, `vercors`, and `chartreuse` cannot be rebuilt offline (a live rebuild would fetch
   different OSM/DEM data and force a rebake), so they are converted in place — the Story 13.2
   v1 → v2 precedent their READMEs describe; `grenoble_small`'s two entries may instead go through
   [regenerate_cache.py](tests/e2e/fixtures/grenoble_small/regenerate_cache.py). Every converted entry
   is proved content-equal (nodes, ordered edges, attrs, `vertices_resampled`) to its pre-conversion
   self **before** the old file is replaced, and all 10 pinned regressions pass with
   `git status tests/e2e/goldens/` empty.
6. **Suite, types, coverage green.** The full offline suite passes, `basedpyright` is clean, and the
   95% bar on `cache.py` is held by direct unit tests of the new read/write paths (including AC #4's
   v2-entry behaviour) — not incidental coverage.
7. **A real r20 query is the acceptance number.** The `load-prepared-area` stage line, CLI-reported
   total, peak RSS, and on-disk entry size are captured before and after on the real r20 command
   (recipe in Dev Notes), back-to-back; a cache-read benchmark is added to `tests/benchmarks/`
   **before** the change as a guardrail, not as the claim (AGENTS.md §Scale target). Review anchors to
   reproduce in shape, not value: ~0.86-0.96 s rebuilding 327,658 `LineString`s, and ~47 MB of
   coordinate arrays inside a 166 MB entry.
8. **Docs match the code.** Architecture §Cat 4c records the v3 on-disk decision (and §Cat 4a/4b the
   format-signal consequence), the reversal of Story 13.2's reconstruction assumption is recorded as an
   **assumption** change rather than a wrong measurement, and the four fixture READMEs' "schema-v2
   payload" notes are corrected.

**Out of scope:** any change to the `vertices_resampled` list-of-tuples representation — this story
changes what the cache *stores*, not the in-memory edge contract (the same boundary Story 16.2 drew).
Also out: review §8 option 2 (geometry in a sidecar file — moot once v3 omits it), the query-side stage
6-7 flat fusion (Story 16.5's rider), the `manifest.json` schema version, and osmnx ingestion
ownership (16.4).

## Tasks / Subtasks

- [x] Audit the consumers and pin the "before" measurement (AC: #1, #7)
  - [x] Trace every reader of `data["geometry"]` in `src/` and confirm each is setup-side
        (`pipeline/osm.py`, `pipeline/smoothing.py`, `pipeline/dem.py`, `pipeline/dem_download.py`,
        `pipeline/__init__.py::_drop_short_edges`, `cache.py`) — record the list in the Dev Agent
        Record, including the App and regression paths that reach a prepared graph
        → 6 setup-side readers, zero query-side; full list in the Completion Notes. Also verified
        per-edge on real cached data (all 322,939 r20 edges + all five fixture entries) that stored
        geometry equals `vertices_resampled`, so v3 loses no information
  - [x] Add a cache-read benchmark next to the existing query-path ones
        ([tests/benchmarks/test_query_orchestration.py](tests/benchmarks/test_query_orchestration.py))
        or in `test_setup_stages.py` beside the cache-write pair, whichever keeps the fixture chain
        honest; both shapes measured in one run so the comparison is drift-immune
        → `test_cache_load_prepared_entry` (the real `check_coverage` load) +
        `test_cache_geometry_reconstruction` (the removed reattach, isolated so it stays measurable
        after removal). The pair replaces an old-shape/new-shape pair that could not exist here — the
        old shape's inputs are gone once the arrays stop being written
  - [x] Capture the baseline r20 query run and back up the r20 entry directory before touching it
        → baseline captured in an isolated scratch cache root (the `.trial-cache` r20 entry was
        copied, never queried in place), with SHA-256 of all 20 output files
- [x] Stop reconstructing geometry on read (AC: #2)
  - [x] Change [_graph_from_payload](src/steeproute/cache.py:601) / [read_entry](src/steeproute/cache.py:796)
        so the query path builds no `LineString`s; keep every corruption diagnostic
        (`CacheCorruptedError` shapes and messages) that has a test
  - [x] Unit tests: the read-back graph is content-equal to the written one minus `geometry`; the
        corruption paths still fire
        → plus a v2-vs-v3 test asserting the loaded graph is identical across both payload versions
- [x] Schema v3 write path + the v2-entry decision (AC: #3, #4)
  - [x] Bump the graph-payload version and drop the geometry arrays in
        [_graph_to_payload](src/steeproute/cache.py:544); keep the LineString contract check and its
        `ValueError` message shape (tests assert it); delete the now-unreachable ragged-array read code
        → `shapely.to_ragged_array` / `from_ragged_array` are both gone from `cache.py`, along with
        the offsets-length consistency check they existed to protect
  - [x] Decide v2 tolerance vs clean rejection (Dev Notes has a recommendation), implement it, and pin
        it with a test that builds a v2-shaped payload
        → tolerance, as recommended; rationale in the Completion Notes. Pinned by three tests, one of
        them through a real on-disk v2 entry
  - [x] Update the payload tests in [tests/unit/test_cache.py:75](tests/unit/test_cache.py:75) that
        assert on `geometry_coords` / `geometry_offsets`
- [x] Convert the committed fixture caches as one event (AC: #5)
  - [x] Write the throwaway converter (load payload → drop geometry → new version → atomic replace),
        run the content-equality diff on each of the five entries, then replace
        → each entry gated three ways before its file was replaced: every stored geometry verified
        equal to its edge's `vertices_resampled`, then the converted entry re-read and compared
        attribute-by-attribute, then asserted geometry-free. 4.28 → 3.03 MB committed
  - [x] `uv run pytest tests/e2e/test_pinned_regressions.py` (fast + realistic tiers) with an empty
        `git status tests/e2e/goldens/`
        → fast tier: 5/5 pass, `git status tests/e2e/goldens/` empty. Realistic tier: 5/5 fail on a
        **pre-existing** stale `params_hash`, reproduced on unmodified HEAD — route hashes still match
        their goldens exactly. Debug Log 1; a separate task is filed for the rebake
- [x] Verify, measure, document (AC: #6, #7, #8)
  - [x] Full offline suite per-directory (AGENTS.md: never mix `tests/unit` and `tests/integration` in
        one invocation) + `uv run basedpyright`
        → 903 unit / 223 integration / 111 e2e pass (plus the 5 pre-existing realistic-tier
        failures); `basedpyright src tests` 0 errors; `ruff check` clean
  - [x] Re-run the r20 query measurement; record stage line, CLI total, peak RSS, and entry size
        before/after, with the noise band of the untouched stages visible
        → three r20 runs (old/v2, new/v2, new/v3) plus a dedicated load-only probe, because the load
        is ~3% of a query and the solve's own variance swamps it in the CLI total
  - [x] Update Architecture §Cat 4c (+ 4a/4b as needed) and the four fixture READMEs

## Dev Notes

**Two different "schema v3"s live in `cache.py` — do not conflate them.**
[_MANIFEST_SCHEMA_VERSION](src/steeproute/cache.py:113) is **already 3** (Story 15.2's rotated `area`
block), while [_GRAPH_PAYLOAD_VERSION](src/steeproute/cache.py:125) is **2** and its comment explicitly
says it stays at 2 because the manifest v3 bump did not change the payload. The epic's "schema v3" is
the *graph payload* going 2 → 3. The manifest version must not move: `Manifest.from_dict` rejects any
non-current version outright, so bumping it would invalidate entries for a reason that has nothing to
do with this change.

**Recommended shape: v3 omits geometry, and the reader tolerates a v2 payload by ignoring its geometry
arrays.** That single decision delivers both of the epic's options at once — review option 1 (skip
reconstruction) falls out of the tolerant read, so there is no `with_geometry=` flag for nobody to
pass, and option 3 (stop storing it) is the write-side half. It also keeps every already-prepared
entry queryable, which matters concretely: the r20 reference entry is a ~5-minute setup to rebuild, and
the three non-regenerable fixtures would otherwise depend on the conversion in AC #5 being flawless.
Architecture §Versioned-contract-surfaces' "no compat shim" stance is about *manifests* forcing a
re-prepare; here tolerance is a few lines in one function and is directly testable. If you reject it,
say so in the Completion Notes and make the failure a clean `CacheCorruptedError` with the
`--force-refresh` hint, not an `AttributeError` deeper in stage 6.

**What geometry actually is, and why dropping it loses nothing.** Post-stage-4, edge `geometry` is the
resampled polyline in `(lon, lat)`; stage 5's `vertices_resampled` is the same vertices as
`(lat, lon, elev)` — equality already pinned at
[test_dem.py:564](tests/unit/test_dem.py:564). So a v3 entry is not lossy: geometry is reconstructible
from `vertices_resampled` if a future consumer ever needs it. Say that in §Cat 4c; it is the argument
that makes the format change safe rather than merely measured.

**The 13.2 reversal is a change of assumption, not of measurement.** Story 13.2 correctly measured that
bulk `from_ragged_array` rebuilds geometry ~20× faster than per-object WKB unpickling. What changed is
that the query needs *no* rebuild at all. Record it that way in §Cat 4c and in the docstrings you
touch — the ragged-array *write* decomposition is what made this story cheap, since geometry is
already separable from the pickled skeleton.

**Cache keys do not move, so the r20 A/B needs a manual backup.** `cache.py` is deliberately excluded
from `_PIPELINE_CONTENT_GLOBS` ([cache.py:68](src/steeproute/cache.py:68)) — a format change must not
shift keys. Consequence: a new v3 write lands on the *same* entry directory and the `.old/` shuffle
deletes the v2 one. Copy the entry aside before measuring, or you lose the "before" side. Related trap
from Story 16.1: `.trial-cache/` entries can also be schema-stale in the *manifest* sense; migrating
`schema_version` by hand is the documented recipe
([chartreuse README](tests/e2e/fixtures/chartreuse/README.md:39)).

**Measurement recipe (AGENTS.md §Scale target — a real query replay).** The r20 entry lives in
`.trial-cache/`; resolve its key by matching `area` in `.trial-cache/steeproute/areas/*/manifest.json`
rather than trusting any key recorded in a story doc — it moves on every `pipeline/**` edit and the old
entry is GC'd. It is also the largest entry (~166 MB `graph.pkl`). Query command:

```
--center 45.260,5.788 --radius 20 --seed 44 --l-connector 50 --j-max 0 --difficulty-cap T4 --n 10
--iter-budget 1000000 --elevation-deadband 1 --elevation-smoothing 50 --progress-interval 1
--stagnation-iters 0 --max-descent-slope 0.4 --start-at-junction --workers 4 --merge-interval 250000
```

Several of those are now CLI defaults (AGENTS.md); keep them explicit so the numbers stay comparable
with Stories 16.1/16.2. CLI total comes from the summary's `wall_clock_total`; the stage line from
`load-prepared-area`. Peak RSS needs an **in-process** `GetProcessMemoryInfo` probe (throwaway, not
committed) — external polling measures the launcher stub. Wall-clock drifts ~30% across a session on
this machine and peak RSS is reproducible to ~0.01%, so take the A/B strictly back-to-back across a
`git stash` boundary and report the noise band. Expect peak RSS to be the cleanest evidence here: the
win is 327k shapely objects plus ~47 MB of coordinates never entering the heap.

**Environment.** `uv run basedpyright <files>`; `uv run pytest` per test directory. If `uv run` starts
failing en masse in `tests/e2e/test_cli_smoke.py` after a commit, that is the known stale-editable-build
flake — `uv sync --native-tls` once (never `--reinstall-package steeproute`).

### Project Structure Notes

- Files touched: `cache.py` only, on the source side (plus `cli/setup.py` or `cli/query.py` if the
  chosen shape needs a call-site edit). No new modules; the FR→module mapping already assigns the
  on-disk format to `cache.py`.
- Tests land in [tests/unit/test_cache.py](tests/unit/test_cache.py) and
  [tests/integration/test_cache_roundtrip.py](tests/integration/test_cache_roundtrip.py) per
  Architecture §Test organization; benchmarks follow the existing `tests/benchmarks/` conventions
  (`pytestmark = pytest.mark.benchmark`, session fixtures, locally pinned params).
- The fixture converter is throwaway tooling — do not commit it as a package module. `grenoble_small`
  is the only fixture with a committed regenerator.
- Comparing entries: compare **content**, never `graph.pkl` bytes. networkx pickles its lazily built
  `adj`/`succ`/`edges` views, so byte equality is not a valid identity (Story 16.2, §Cat 4c).

### References

- [epics.md#Story 16.3](_bmad-output/planning-artifacts/epics.md:450) — Given/When/Then, the
  "coordinated with 16.2" note (16.2 has already landed, so this story pays its own regen event), and
  the epic's tiered-confidence framing
- [performance review §8](_bmad-output/planning-artifacts/research/steeproute-performance-review-gpt-5-6-2026-07-24.md:464)
  — payload sizes, warm read timings, the three options, and the 13.2 assumption reversal
- [sprint-change-proposal-2026-07-24-ownership-oriented-performance.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-07-24-ownership-oriented-performance.md:80)
  — confidence table: "Decision + measurement", guardrail "content-identical for every live consumer"
- [architecture.md §Cat 4c](_bmad-output/planning-artifacts/architecture.md:355) — schema-v2 on-disk
  format rationale, tradeoffs, and the Story 16.2 consuming-write note; §Cat 4a/4b layout + cache-key
  inputs at [line 309](_bmad-output/planning-artifacts/architecture.md:309)
- [architecture.md §Cat 11](_bmad-output/planning-artifacts/architecture.md:1026) — coverage bars
  (95% on `cache.py`), zero-tolerance golden gate, `skip`/`xfail` prohibition
- [Story 16.2](_bmad-output/implementation-artifacts/16-2-setup-owned-data-cleanup-and-smoothing-resampling-fusion.md:200)
  — cache-invalidation reasoning, the fixture-regen gate that worked, and the graph-content comparison
  method (incl. osmnx's non-deterministic list-tag order)
- [Story 16.1](_bmad-output/implementation-artifacts/16-1-query-orchestration-owned-filter-lean-contracted-graph-validation-context.md:212)
  — r20 query command, peak-RSS probe, and the manifest-schema trap in `.trial-cache/`
- Fixture READMEs: [chartreuse](tests/e2e/fixtures/chartreuse/README.md),
  [vercors](tests/e2e/fixtures/vercors/README.md), [belledonne](tests/e2e/fixtures/belledonne/README.md),
  [grenoble_small](tests/e2e/fixtures/grenoble_small/README.md) — which caches can be rebuilt offline
  and the in-place-conversion precedent
- [AGENTS.md](AGENTS.md) — golden policy (never a silent rebake), §Scale target measurement rule,
  dev-environment commands

## Dev Agent Record

### Agent Model Used

claude-opus-5 (Claude Code, `dev-story` workflow)

### Debug Log References

1. **The five realistic-tier regression goldens were already stale — before this story.** Running the
   `slow`-gated realistic tier fails all five fixtures on `params_hash`
   (`fa5c3a7f…` computed vs `f0b572b9…` stored) while every per-route hash tuple and the seed match
   exactly. Reproduced on unmodified HEAD via `git stash`, so it is not this story's. Cause: the
   2026-07-28 defaults spec added `"--workers": "1"` to `_PINNED_PARAMS`
   ([regression.py](src/steeproute/regression.py:145)), which moved `params_hash` for **both** tiers;
   the fast-tier goldens were rebaked then, the realistic ones were missed because that tier is
   deselected in every normal suite run. Not rebaked here — that is a different change needing its own
   rationale commit, and folding it in would have hidden it inside a cache-format diff. Filed as a
   separate task. What this story did verify: after the v3 conversion the realistic tier's *routes*
   still match their goldens exactly, so neither the code change nor the fixture conversion moved
   output at either tier.
2. **v2 tolerance, not rejection — and it makes the epic's two options one change.** The epic framed
   this as option 1 (`read_entry(with_geometry=False)`) *then* option 3 (schema v3). Implemented as one
   change instead: v3 stops writing geometry, and the reader ignores a v2 entry's coordinate arrays
   rather than rebuilding them. A v2 entry and a v3 entry then hand a query the *same* graph, so option
   1's win arrives for old entries too and there is no `with_geometry=` parameter with only one live
   value. Cost of tolerance: one `frozenset` of accepted versions and one extra test path. What it
   buys: no already-prepared cache is invalidated (the `.trial-cache` r20 entry alone is a ~5-minute
   re-prepare) and the fixture conversion in AC #5 became an optional size optimization rather than a
   prerequisite for green tests. It is not a compat *shim* in the §Versioned-contract-surfaces sense —
   nothing is translated; the difference is bytes we skip.
3. **The `prepared_grenoble_graph` benchmark fixture no longer carries geometry, which broke the two
   cache-*write* benchmarks.** They fed a cache-loaded graph into `_graph_to_payload`, which requires
   post-stage-5 geometry — the exact contract v3 makes a cache-loaded graph unable to satisfy. Fixed by
   adding a `post_stage5_graph` session fixture built through the setup chain
   (`resampled_graph` → `sample_elevation`), which is the shape `steeproute-setup` actually hands to
   `write_entry`. Consequence worth knowing: those two benchmarks' numbers are **not** comparable to
   Story 16.2's, because their input changed, not just the code.
4. **v3 barely beats a tolerated v2 read on time — the load win is the reconstruction, not the bytes.**
   Load-only, r20: old code 3.6-4.1 s → new code on the *same* v2 entry 2.52-2.56 s → new code on the
   v3 entry 2.44-2.56 s. Skipping 45 MB of coordinate array costs almost nothing to read: it is one
   contiguous numpy buffer in an already-warm page cache. The review's "option 2/3 also reduce disk
   I/O" is true but implies more than it delivers; v3's real returns are entry size (−26.9%) and heap
   (−39 MB at load, on top of the −68 MB from not building `LineString`s). Recorded so a future reader
   doesn't attribute the −1.2 s to the schema change.
5. **The CLI total moved the wrong way and that is machine noise, not a regression.** 81.7 s (old) →
   93.1 s (new/v2) → 89.1 s (new/v3), while the stage this story touches dropped 0.94 s and every
   other stage stayed within ±0.5 s. The solve is iteration-bounded here (1M iters, stagnation
   disabled, 4 workers), so its wall-clock varies purely with machine load — the same ±40% band
   Stories 16.1 and 16.2 both documented on this machine. The trustworthy numbers are the
   `load-prepared-area` stage line, the load-only probe, and peak RSS (reproducible to 0.001 GB).

### Completion Notes List

**Landed as one change: schema v3 stops storing post-stage-5 geometry, and nothing reconstructs it.**
Output is byte-identical — all 20 r20 output files SHA-256 equal across old code on a v2 entry, new
code on the same v2 entry, and new code on the converted v3 entry.

**The audit behind that (AC #1).** Every reader of the `geometry` edge attribute in `src/` is
setup-side: `pipeline/osm.py:317` (stage 1-2 synthesizes it for edges osmnx leaves without one),
`pipeline/smoothing.py:567`/`:662` (stages 3-4 read and rebuild it), `pipeline/dem.py:175` (stage 5's
coordinate gather), `pipeline/dem_download.py:260` (DEM extent sizing), `pipeline/__init__.py:375`
(`_drop_short_edges`, a private setup guard), and `cache.py` itself. Query-side readers: **none** —
stages 6-7 and `output.render` read `vertices_resampled`; contraction, the solver and the validator
read metrics and tags; the App's `cli_adapter` and `regression.py` never touch edge geometry
(`output.py`'s `has_geometry` / GeoJSON `"geometry"` keys are template payload, not the attribute).
Setup's own cache-hit read uses only the manifest. Beyond the static audit, the conversion verified
per-edge on **real cached data** that stored geometry equals `vertices_resampled` — 322,939 r20 edges
and all 5,434 fixture edges, lon/lat to 1e-9 deg — so v3 drops a derived copy, not information.

**r20 query, in-process probe, isolated scratch cache root:**

| Measure | old code / v2 | new code / v2 | new code / v3 |
|---|---:|---:|---:|
| `load-prepared-area` stage | 3.23 s | **2.29 s** | **2.49 s** |
| peak working set | 1.831 GB | **1.711 GB** | **1.711 GB** |
| CLI total (noise-dominated, Debug Log 5) | 81.67 s | 93.13 s | 89.11 s |
| 20 output files, SHA-256 | reference | identical | identical |
| `graph.pkl` on disk | 165.8 MB | 165.8 MB | **121.2 MB (−26.9%)** |

**Load-only probe** (one `check_coverage` per fresh process, 3× each, alternating — the load is ~3% of
a query, so isolating it is the only way to see it):

| | load time | peak RSS |
|---|---|---:|
| old code, v2 entry | 3.622 / 4.037 / 4.110 s | 1.065 GB |
| new code, v2 entry (legacy read) | 2.521 / 2.549 / 2.561 s | 0.997 GB |
| new code, v3 entry | 2.439 / 2.455 / 2.559 s | 0.958 GB |

Old → new v3: **−33% load time, −0.107 GB peak RSS**, and −44.6 MB on disk. The review's ~0.9 s
reconstruction anchor reproduced almost exactly (3.23 → 2.29 s on the stage line).

**Component benchmarks** (`grenoble_small`, Min, µs→ms): `test_cache_load_prepared_entry`
10.64 → 10.03 ms — flat inside a wide noise band, which is the guardrail AC #7 asked for rather than a
claim; `test_cache_geometry_reconstruction` 1.98 ms is the work the load no longer does. A 1.5 km
fixture entry is ~1 MB against r20's 166 MB, so per AGENTS.md §Scale target the r20 tables above carry
the claim.

**Fixture entries converted in place, no golden moved.** All five committed entries (belledonne,
vercors, chartreuse, both grenoble_small) converted rather than regenerated — including
`grenoble_small`, which *could* have been rebuilt offline: rewriting the identical graph object with
the arrays dropped is stronger evidence of content equality than a fresh pipeline run, whose float
reordering drifts at ~1e-14 (as the 2026-07-27 regeneration showed). Committed payloads
4.28 → 3.03 MB. All 10 fast-tier + realistic-tier route hash sets unchanged; the 5 realistic failures
are the pre-existing `params_hash` staleness in Debug Log 1.

**Design decisions worth review attention:**

- **The LineString contract check on write is kept**, even though the value is now discarded rather
  than serialized. It is the post-stage-5 pipeline contract — a graph arriving at `write_entry` without
  geometry did not come from the pipeline — and both its `ValueError` message and both call paths stay
  pinned by tests.
- **The payload version moved 2 → 3 while `manifest.json` stays at 3.** Confusing but correct: the two
  version numbers are independent, and bumping the manifest would invalidate entries for a change the
  query cannot observe. The constant's comment says so explicitly, since the old comment argued the
  opposite (that the payload version must *not* advance).
- **Story 13.2's read-side machinery is deleted, not disabled** — `to_ragged_array`,
  `from_ragged_array`, the coordinate/offset payload keys, and the offsets-length consistency check are
  all gone from `cache.py`, along with its `numpy` import. What survives from 13.2 is the decision that
  made this cheap: geometry was already outside the pickled skeleton.
- **A v2 entry read under v3 code returns a graph with no `geometry`, silently.** That is the point,
  but it means a hypothetical future consumer that *did* need geometry would see a missing attribute
  rather than a version error. Mitigated by the fact that geometry is reconstructible from
  `vertices_resampled` (verified above) and by §Cat 4c recording it.

### File List

Source:
- `src/steeproute/cache.py` — schema-v3 geometry-free payload (`_graph_to_payload`), geometry-free
  read with v2 tolerance (`_graph_from_payload`), version constants, `numpy` import removed

Tests:
- `tests/unit/test_cache.py` — 6 new tests (v3 payload shape, legacy-v2 read, v2/v3 equivalence,
  unknown version, bad `graph` part, on-disk v2 entry) + payload-comparison helper updated
- `tests/integration/test_cache_roundtrip.py` — round-trip compares all attrs minus `geometry` and
  asserts geometry is absent
- `tests/benchmarks/test_query_orchestration.py` — 2 new benchmarks (entry load, isolated geometry
  reconstruction)
- `tests/benchmarks/conftest.py` — `post_stage5_graph` fixture (geometry-bearing, setup-side)
- `tests/benchmarks/test_setup_stages.py` — cache-write benchmarks moved onto `post_stage5_graph`

Fixture data (payload converted in place, graph content unchanged):
- `tests/e2e/fixtures/belledonne/cache/steeproute/areas/0fdac3e4201d1b2f/graph.pkl`
- `tests/e2e/fixtures/chartreuse/cache/steeproute/areas/82c54e5c5d39f462/graph.pkl`
- `tests/e2e/fixtures/vercors/cache/steeproute/areas/88bd11bc7d33b4ad/graph.pkl`
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/areas/98f4af770f7dae31/graph.pkl`
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/areas/cb392681518224d8/graph.pkl`

Docs:
- `_bmad-output/planning-artifacts/architecture.md` — §Cat 4c rewritten for schema v3 (decision,
  measurements, v2 read tolerance, the 13.2 assumption reversal); stale `schema_version: 2` in the
  manifest example corrected to 3
- `tests/e2e/fixtures/{belledonne,vercors,chartreuse,grenoble_small}/README.md` — payload-format note
  and conversion record
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/16-3-geometry-optional-query-load-and-schema-v3-cache.md`
