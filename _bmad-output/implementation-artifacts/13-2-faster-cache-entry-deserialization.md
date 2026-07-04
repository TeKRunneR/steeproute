# Story 13.2: Faster cache-entry deserialization

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want prepared-area cache entries to load without per-edge geometry parsing and incremental graph rebuild,
so that large-area queries stop paying ~11% of wall-clock before any work starts.

## Acceptance Criteria

1. **Given** `read_entry` currently parses per-edge WKB geometry and rebuilds the graph edge-by-edge,
   **when** entry storage moves to an array-based / prebuilt-graph format with a manifest schema-version
   bump (existing entries re-prepare once, per the Category 4b invalidation semantics), **then** the loaded
   graph is content-identical (same nodes, edges, attributes) and the full suite including regression
   goldens passes untouched.
2. Measured `read_entry` time on the Chartreuse r10 entry drops materially, recorded in the close-out.
3. Architecture Category 4c (on-disk format) is updated to record the new decision.

## Tasks / Subtasks

- [x] Task 1: Design and implement the new entry storage format in `cache.py` (AC: #1)
  - [x] Profile/measure where `pickle.load` time actually goes on the Chartreuse r10 entry before picking a
    format (see Dev Notes "Where the cost is"); pick the simplest format that wins, e.g.
    transform-before-serialize (coords as flat arrays + offsets, bulk `shapely.from_ragged_array` on load)
    vs. npz-based storage — measured; see Dev Agent Record (geometry ~60%; every array alternative for the
    non-geometry parts measured *slower* than pickle, so only geometry moved out)
  - [x] Rewrite `write_entry` step 1 and `read_entry`'s graph load; the in-memory `PreparedData` contract
    (nx.MultiDiGraph, exact node/edge attribute types) is unchanged — callers must not need edits (none were)
  - [x] Bump `_MANIFEST_SCHEMA_VERSION` 1 → 2; no compat shim / no dual-format reader (Architecture
    §Versioned-contract-surfaces; `Manifest.from_dict` already rejects mismatches with an actionable message)
  - [x] Preserve the Cat 4d atomic write order exactly: all graph file(s) materialize inside the `.tmp/`
    staging dir, `manifest.json` still written last as the commit signal; `cache.py` stays the sole
    reader/writer of the cache directory (single `graph.pkl` file kept; write order untouched)
- [x] Task 2: Test updates + content-identity proof (AC: #1)
  - [x] Existing unit/integration cache tests (`tests/unit/test_cache.py`, `tests/integration/test_cache_roundtrip.py`,
    `test_cache_atomic.py`, `test_cache_coverage.py`, `tests/unit/test_check_coverage.py`) write and read
    programmatically, so they exercise the new format for free — fixed the pinned literals
    (`schema_version` 1→2 in `_manifest_payload` + the mismatch-detail assertion + one e2e provenance
    assertion; `graph.pkl` filename unchanged, no filename literals to touch)
  - [x] Add/extend a roundtrip test asserting exhaustive content-identity: same node set + node attrs, same
    edge set (u, v, key) + all edge attrs, same graph-level attrs — replaced the one-sample check (its
    "pickle isn't selective" premise no longer holds under positional geometry reattachment)
  - [x] Verify both old-entry recovery paths behave per the schema bump: query exit-2 comes from
    `CacheCorruptedError`'s existing `PreExecutionError` mapping (pinned in `test_errors.py`) + the new v1
    unit test; setup re-prepare-as-recovery had NO pinning test — added
    `test_setup_re_prepares_legacy_schema_v1_entry_once` (e2e) plus two new unit tests (v1 manifest
    rejected; v2 manifest over legacy raw-graph pickle → `CacheCorruptedError`)
- [x] Task 3: Convert the four committed e2e cache fixtures offline (AC: #1)
  - [x] chartreuse / vercors / belledonne: converted **in place** via a one-off scratch script (old
    `graph.pkl` read with plain `pickle.load`, re-serialized through `_graph_to_payload`, manifest
    `schema_version` → 2 with all other fields preserved, same directory name). No network touched
  - [x] grenoble_small: same in-place conversion (uniform + safer than regenerating)
  - [x] Run the full pinned-regression suite (fast + `-m slow` realistic + flag-on tiers) — fast 4/4 and
    realistic 4/4 byte-identical; flag-on tier green inside the full default suite
  - [x] Update the four fixture READMEs (they document `graph.pkl` and pickle sensitivity);
    `regenerate_cache.py` docstring makes no format claims — no change needed
- [x] Task 4: Measure and record the gain (AC: #2)
  - [x] Before/after `read_entry` wall on the Chartreuse r10 entry (`c5bbfb802f3dc22f`, 60,110 nodes /
    152,578 edges): 2.50 s → 1.12 s in-process; `load-prepared-area` stage line 1.09–1.27 s across two
    reference runs (was ~2.6 s implied)
  - [x] Record before/after and the whole-run effect in the Dev Agent Record and commit message
- [x] Task 5: Doc sync + gates (AC: #3)
  - [x] Update `architecture.md` Category 4c with the new format decision and its tradeoffs (replacing the
    pickle rationale; keep the manifest/bounds JSON sidecars rationale); also bumped the manifest schema
    example and the 4c decision-summary bullet
  - [x] `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest --cov`
    green; update sprint-status

## Dev Notes

### Where the cost is (read before designing)

`read_entry` (`src/steeproute/cache.py:542-597`) is a single `pickle.load` of `graph.pkl` — there is no
explicit per-edge loop to vectorize. The research doc's "per-edge WKB parse + graph rebuild" happens *inside*
unpickling: shapely 2.x geometries pickle via their WKB representation, so every edge's `geometry` LineString
is reconstructed through a per-object `from_wkb` call; every edge's `vertices_resampled`
(`list[tuple[float, float, float]]`, dozens of vertices per edge × 152k edges on Chartreuse r10) is rebuilt
as millions of small Python objects one at a time; and networkx's dict-of-dict-of-dict structure rebuilds
through pickle's generic machinery. The fix is serialization mechanics, not call-site changes. Candidate
shape: store coordinate data as flat numpy arrays + per-edge offsets and rebuild LineStrings in bulk via
`shapely.from_ragged_array` (vectorized; shapely `>=2.0` and numpy are already pinned deps — no new
dependency), with the graph skeleton in whatever cheap form measurement supports. Measure the split first
(e.g. time a pickle.load of the graph with geometry/vertices attributes stripped vs. full) so the format
targets the actual dominant term.

**Hard constraint:** the *in-memory* graph handed to callers is unchanged — downstream consumes
`data["geometry"].coords` (`pipeline/__init__.py:306`), iterates `vertices_resampled` as list-of-tuples
(`pipeline/climbs.py:90`), and Story 13.1's vectorized smoothing reads the same contract. Changing the
in-memory representation is out of scope (that would ripple through stages 6–9).

### Cached-graph content contract

Post-stage-5 graph (`run_setup_stages` docstring, `pipeline/__init__.py:101-155`): edges carry `geometry`
(shapely LineString), `vertices_resampled` (`list[tuple[lat, lon, elevation_m]]`), `sac_scale`, `highway`,
`osm_way_id`; nodes carry osmnx `x`/`y` (`pipeline/osm.py:214-215`). Enumerate the actual attribute set
empirically from a real entry rather than trusting this list — the content-identity test (Task 2) should
assert exhaustively over whatever is really there, including graph-level attrs (osmnx `crs` etc.).

### Invalidation mechanics (why a schema bump, not a key change)

`cache.py` is deliberately **excluded** from `_PIPELINE_CONTENT_GLOBS` (`cache.py:55-59`) — it changes *how*
graphs are persisted, not what they contain — so this story does not shift cache keys. The
`_MANIFEST_SCHEMA_VERSION` bump is the invalidation: `Manifest.from_dict` rejects v1 entries with
`CacheCorruptedError` ("Re-prepare with `steeproute-setup --force-refresh`"), the query CLI surfaces that as
exit 2, and `steeproute-setup` already treats `CacheCorruptedError` as re-prepare-as-recovery
(`cli/setup.py:159-168`). That is exactly the AC's "existing entries re-prepare once" — the mechanism
pre-exists; don't build a migration path for user caches. `index.json` and its schema version are untouched.

### The fixture trap (biggest regression risk)

Four **committed queryable cache roots** live under `tests/e2e/fixtures/{grenoble_small,chartreuse,vercors,belledonne}/cache/`
— the pinned-regression harness and `tests/benchmarks/conftest.py` load them via the real cache reader. After
the format change these are unreadable v1 entries, and chartreuse/vercors/belledonne **cannot be regenerated
offline** (prepared from live Overpass + IGN WMS; re-fetching would ingest drifted OSM and flip goldens —
violating AC #1). Offline in-place conversion (old pickle in → new format out, manifest fields preserved,
version bumped) is the only golden-safe path. The conversion script is a one-off — scratch or committed
alongside the fixtures, dev's call. The bare-manifest JSON at `tests/fixtures/grenoble_small/cache/` feeds
integration tests — sweep it and the 6 test files matching `schema_version` for pinned v1 literals.

### Measurement anchors

Phase-3 results doc: `read_entry` ≈ 11% (~4.4 s) of the 40.05 s Chartreuse r10 reference run (seed 44, n 10,
l-connector 50, smoothing 50, descent-cap 0.4, start-at-junction); post-13.1 whole-run baseline is 33.56 s
(13.1 Dev Agent Record). Story 13.1 added query-side `stage:` progress lines — `load-prepared-area` covers
coverage check + deserialization, so per-run numbers are visible without a profiler.

### Testing standards summary

- Gates: `ruff check`, `ruff format --check`, whole-project `basedpyright` 0/0/0, default `uv run pytest
  --cov` (~4:15 typical; much slower usually means a test hit the network).
- `uv` Windows build flake: a stale editable build after a commit or `pyproject.toml` edit makes `uv run`
  hit a corporate-TLS cert error (~43 `test_cli_smoke` failures as the symptom). Fix once with
  `uv sync --native-tls`, then `uv run --no-sync` for the rest of the session.

### Project Structure Notes

- **Modified:** `src/steeproute/cache.py` (`write_entry` step 1, `read_entry`, graph-file constant(s),
  `_MANIFEST_SCHEMA_VERSION`), cache unit/integration tests, the four committed e2e fixture cache roots +
  READMEs, `_bmad-output/planning-artifacts/architecture.md` §Cat 4c, sprint-status.
- **Untouched:** `compute_cache_key` / `_PIPELINE_CONTENT_GLOBS` (keys must not shift), `check_coverage` and
  the `PreparedData` API (callers in `cli/query.py` / `cli/setup.py` unchanged), `index.json` schema,
  `manifest.json` field set (only `schema_version` value changes), pipeline stages, solver, output.
- Out of scope: Story 13.3 (second-tier cache / recompute avoidance), 13.4 (lazy imports), any change to the
  in-memory graph representation consumed by stages 6–9.

### References

- [Source: epics.md §Epic 13 preamble + §Story 13.2](../planning-artifacts/epics.md) — AC source-of-truth
- [Source: research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md §"What next" item 4](../planning-artifacts/research/steeproute-phase3-results-and-phase4-decision-2026-07-04.md)
  — the ~11% attribution and "deserialization engineering" framing
- [Source: architecture.md §Category 4 (lines 270-351)](../planning-artifacts/architecture.md) — 4b key
  composition, 4c on-disk format (the decision this story replaces), 4d atomic write order
- [Source: src/steeproute/cache.py:416-597](src/steeproute/cache.py) — `write_entry` / `read_entry`, the
  functions this story rewrites; `:94` `_MANIFEST_SCHEMA_VERSION`; `:220-306` `Manifest.from_dict` rejection
- [Source: src/steeproute/cli/setup.py:141-168](src/steeproute/cli/setup.py) — cache-hit check +
  re-prepare-as-recovery on `CacheCorruptedError`
- [Source: src/steeproute/pipeline/__init__.py:101-155](src/steeproute/pipeline/__init__.py) — post-stage-5
  edge-attribute contract of the cached graph
- [Source: tests/e2e/fixtures/chartreuse/README.md](tests/e2e/fixtures/chartreuse/README.md) — network-only
  provenance of the three real-data fixture caches (why in-place conversion, not regeneration)
- [Source: tests/e2e/fixtures/grenoble_small/regenerate_cache.py](tests/e2e/fixtures/grenoble_small/regenerate_cache.py)
  — the one offline-regenerable fixture
- [Source: _bmad-output/implementation-artifacts/13-1-vectorize-query-side-elevation-smoothing.md](13-1-vectorize-query-side-elevation-smoothing.md)
  — measurement method, post-13.1 baseline (33.56 s), `load-prepared-area` stage line

## Dev Agent Record

### Agent Model Used

Claude Fable 5 (`claude-fable-5`), via Claude Code CLI on Windows 11.

### Debug Log References

**Pre-design measurement (Chartreuse r10 entry, warm file cache, best of 3):**

```
pickle.load (v1 format, full, 71.2 MB)             2.499 s   ← the before number
pickle.load, geometry stripped        (51 MB)      0.993 s   ← geometry ≈ 60% of the load
pickle.load, vertices_resampled stripped (40 MB)   2.671 s   ← the tuple lists are NOT the problem
pickle.load, skeleton (neither)       (20 MB)      0.538 s
shapely.from_ragged_array (rebuild all 152k geoms) 0.081 s   ← 20× faster than per-object WKB
vertices_resampled rebuild from arrays             2.940 s   ← array rebuild LOSES to pickle (0.45 s)
nx rebuild via add_nodes/edges_from                0.709 s   ← also loses to pickled skeleton (0.54 s)
```

**Gates (all green):**

```
tests/unit/test_cache.py + test_check_coverage.py +
  integration cache tests               → 82 passed
tests/e2e/test_steeproute_setup.py      → 15 passed (incl. new legacy-v1 recovery test)
tests/e2e/test_pinned_regressions.py    → 4 passed fast tier + 4 passed -m slow tier, byte-identical
pytest --cov (default markers)          → 849 passed, 12 deselected in 3:32 (96% cov;
                                          includes the flag-on golden tests)
ruff check / ruff format --check        → clean
basedpyright (whole project)            → 0 errors, 0 warnings, 0 notes
```

**Measurements (Chartreuse r10 entry `c5bbfb802f3dc22f`, 60,110 nodes / 152,578 edges,
warm `.trial-cache`, capture-C params + `--stagnation-iters 0`):**

```
read_entry (in-process, best of 4):  2.50 s → 1.12 s   (~2.2×; graph.pkl 71.2 → 67.8 MB)
load-prepared-area stage line:       1.09 s / 1.27 s across two reference runs
                                     (pre-change ≈ 2.6 s: 2.50 s read_entry + coverage overhead)
whole run (wall_clock_total):        38.46 s / 32.90 s across two runs — the ~1.4 s stage gain
                                     is real but sits inside ±3 s solver run-to-run noise
                                     (13.1's post-story anchor: 33.56 s)
output equivalence:                  both reference runs 10/10 routes, budget-exhausted,
                                     identical sha256 over timestamp-stripped route JSONs
                                     (827870170647b0c4); goldens byte-identical (the strict proof)
```

### Completion Notes List

**Format decision (Task 1, AC #1).** The story's "per-edge WKB parse" framing was confirmed and
localized by measurement: shapely 2.x geometries unpickle through a per-object WKB parse, and that is
~60% of `read_entry` (1.5 of 2.5 s); the networkx skeleton and the `vertices_resampled` lists-of-tuples
unpickle *faster* than any array-based reconstruction (measured, table above). So the v2 format moves
**only geometry** out of the graph: `graph.pkl` now pickles a payload dict — marker/version key, the
graph with per-edge `geometry` stripped, one flat float64 coords array + int64 per-edge offsets
(edge-iteration order) — and `read_entry` rebuilds all LineStrings in one `shapely.from_ragged_array`
call, reattaching them positionally under a `strict=True` zip plus an offsets-length check.
`_graph_to_payload` operates on `graph.copy()` so the caller's graph is untouched (write-side cost,
setup path only). Write order, atomicity, filenames, `PreparedData`, and every call site: unchanged.

**Invalidation semantics (AC #1).** `_MANIFEST_SCHEMA_VERSION` 1 → 2 is the sole invalidation signal
(`cache.py` is excluded from the pipeline content hash by design, so keys did not shift).
`Manifest.from_dict` already rejected mismatches; v1 entries now take the pre-existing recovery paths:
query → `CacheCorruptedError` → exit 2 with the `--force-refresh` hint; setup → re-prepare-as-recovery.
The setup path had no pinning test — added an e2e test that seeds an entry, downgrades its manifest to
v1, re-invokes setup, and asserts cache-miss re-prepare leaving a valid v2 entry.

**Fixture conversion (Task 3, AC #1).** All four committed cache roots (grenoble_small, chartreuse,
vercors, belledonne) plus the local `.trial-cache` (needed for the reference measurement) were converted
in place by a one-off scratch script — old pickle in, `_graph_to_payload` out, manifest version bumped,
every other manifest field preserved. No network, no pipeline re-run, so graph content is bit-identical;
the zero-tolerance golden suite passing untouched on all tiers is the proof. The three real-data
fixtures were **not** regenerated from Overpass/IGN (OSM drift would have flipped goldens).

**Content-identity net (Task 2, AC #1).** The roundtrip integration test is now exhaustive — every
node + attrs, every edge (u, v, key) + full attr-dict equality (shapely structural `==`), graph-level
attrs, and the `list[tuple]` shape of `vertices_resampled` — because positional reattachment makes
"one sample edge" insufficient: a mis-zipped rebuild would swap geometries between edges while all
count-based assertions still passed. A new unit test also pins that a v2 manifest over a legacy
raw-graph pickle surfaces as `CacheCorruptedError` (match: "graph payload"), not a leaked raw graph.

**Measured gain (Task 4, AC #2).** `read_entry` on the Chartreuse r10 entry: **2.50 s → 1.12 s
(~2.2×)**. In the full reference run the `load-prepared-area` stage now reports 1.09–1.27 s (analysis
had attributed ~11% ≈ 4.4 s of the profiled 40 s run to `read_entry`; the unprofiled in-process cost
was 2.5 s). Whole-run wall sits within solver noise (32.9–38.5 s vs 13.1's 33.56 s anchor) — the
~1.4 s absolute stage gain is visible directly in the stage line, which is the stable per-phase
metric Story 13.5 should consolidate on.

### File List

**Modified:**
- `src/steeproute/cache.py` — schema v2: `_MANIFEST_SCHEMA_VERSION` = 2, payload marker constants,
  `_graph_to_payload` / `_graph_from_payload` helpers, `write_entry` step 1 pickles the payload,
  `read_entry` validates + rebuilds geometry in bulk; numpy import.
- `tests/unit/test_cache.py` — `_manifest_payload` → v2; mismatch-detail assertion → `schema_version=2`;
  new `test_manifest_from_dict_raises_on_legacy_v1_schema_version` and
  `test_read_entry_raises_cache_corrupted_on_legacy_graph_payload`.
- `tests/integration/test_cache_roundtrip.py` — roundtrip test made exhaustive (all nodes/edges/attrs +
  graph attrs + `vertices_resampled` shape).
- `tests/unit/test_cache_key.py` — `to_dict` schema assertion → `schema_version == 2`.
- `tests/e2e/test_steeproute_setup.py` — provenance test asserts `schema_version == 2`; edge-count test
  loads via `read_entry` instead of raw `pickle.load`; new
  `test_setup_re_prepares_legacy_schema_v1_entry_once`.
- `tests/e2e/fixtures/grenoble_small/cache/steeproute/areas/4c348169d4d0bb0c/{graph.pkl,manifest.json}`
  — converted in place to v2 (content-identical).
- `tests/e2e/fixtures/chartreuse/cache/steeproute/areas/82c54e5c5d39f462/{graph.pkl,manifest.json}` — same.
- `tests/e2e/fixtures/vercors/cache/steeproute/areas/88bd11bc7d33b4ad/{graph.pkl,manifest.json}` — same.
- `tests/e2e/fixtures/belledonne/cache/steeproute/areas/0fdac3e4201d1b2f/{graph.pkl,manifest.json}` — same.
- `tests/e2e/fixtures/{grenoble_small,chartreuse,vercors,belledonne}/README.md` — graph.pkl format
  wording updated to the v2 payload.
- `_bmad-output/planning-artifacts/architecture.md` — §Cat 4c decision replaced (v2 payload + rationale +
  measurements); 4c summary bullet + manifest schema example updated.
- `_bmad-output/implementation-artifacts/13-2-faster-cache-entry-deserialization.md` — this file.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions.

## Change Log

| Date | Author | Description |
|---|---|---|
| 2026-07-04 | Yann (Claude Fable 5) | Story 13.2 implemented: cache-entry graph storage moved to the schema-v2 ragged-array payload (graph-minus-geometry pickled alongside flat coords + offsets; bulk `shapely.from_ragged_array` rebuild on read). Manifest schema 1→2 is the invalidation signal (keys unchanged); v1 entries re-prepare once via existing recovery paths, setup path now pinned by a new e2e test. All four committed fixture caches converted in place offline; goldens byte-identical on all tiers. `read_entry` on Chartreuse r10: 2.50 → 1.12 s (~2.2×). Architecture §Cat 4c updated. |
| 2026-07-04 | Yann (Claude Fable 5) | Code review (low effort, diff-only pass over `cache.py`): no findings. Story closed out — status set to done. |
