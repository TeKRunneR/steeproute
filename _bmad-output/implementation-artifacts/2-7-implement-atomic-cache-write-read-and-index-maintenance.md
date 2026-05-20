# Story 2.7: Implement atomic cache write, read, and index maintenance

Status: done

## Story

As a developer,
I want `cache.py::write_entry`, `cache.py::read_entry`, and `cache.py::rebuild_index` implementing the `.tmp/` → `os.replace()` atomic pattern with `manifest.json` as the commit signal,
so that `steeproute-setup` can safely persist Story 2.5's prepared graph and a Ctrl-C mid-write cannot leave consumers reading a partial entry.

## Acceptance Criteria

1. `cache.py::write_entry(cache_root: Path, manifest: Manifest, graph: MultiDiGraph) -> Path` writes a cache entry per Architecture §Cat 4d, returning the final entry directory path. Write order is non-negotiable: `graph.pkl` + `bounds.geojson` go into `<cache-root>/steeproute/areas/<cache-key-hash>.tmp/` first, the `.tmp/` directory is then renamed to `<cache-key-hash>/`, and `manifest.json` is written **last** (inside the entry, via `write_json_atomic`) as the commit signal. `index.json` is rebuilt atomically after the manifest lands. An entry without a final `manifest.json` is — by definition — not a valid entry. The `<cache-key-hash>` directory name comes from `manifest.cache_key_hash` (caller is responsible for consistency between the hash in the manifest and the key the entry should live under).

2. `cache.py::write_json_atomic(path: Path, obj: object) -> None` is the **single** helper for all atomic JSON writes (Architecture §Key anti-patterns: "no per-site reimplementation"). Writes `path.with_suffix(path.suffix + ".tmp")` then `os.replace()`. All `manifest.json` and `index.json` writes in this module route through it; no `open(..., "w")` on JSON files appears anywhere in `cache.py`.

3. Existing-entry overwrite is Windows-safe. On a re-write to the same `<cache-key-hash>` (e.g. Story 2.8's `--force-refresh`), the existing entry directory is moved aside (e.g. to `<cache-key-hash>.old/`) before the `.tmp/` → `<cache-key-hash>/` rename, then the `.old/` is removed. Architecture §Cat 4d footnote: `os.replace()` on directories on Windows requires the target not exist; this swap dance is how we satisfy it without losing atomicity from the reader's point of view (a reader either sees the old entry or the new entry — never neither, never both).

4. `cache.py::read_entry(cache_root: Path, cache_key: str) -> PreparedData` resolves the entry directory, validates `manifest.json` exists (else `CacheNotFoundError` with a message naming the missing key), `pickle.load`s `graph.pkl`, parses `manifest.json` back into a `Manifest` instance, and returns both bundled. A `pickle.UnpicklingError` or `EOFError` on `graph.pkl` (manifest present but graph unreadable) raises `CacheCorruptedError` per `errors.py`. A schema-version mismatch in `manifest.json` raises `CacheCorruptedError` with a version-mismatch detail (Architecture §Versioned-contract-surfaces: "consumers reading a newer-schema manifest must either skip unknown fields safely, or fail with a descriptive version-mismatch error").

5. `cache.py::PreparedData` is a `@dataclass(frozen=True, slots=True)` bundling `graph: MultiDiGraph` and `manifest: Manifest`. This is the shared return type for `read_entry` (this story) and `check_coverage` (Story 2.10). Lives in `cache.py` alongside `Manifest` — same boundary rationale Story 2.6 used for `Manifest`.

6. `cache.py::rebuild_index(cache_root: Path) -> None` walks `<cache-root>/steeproute/areas/*/manifest.json`, builds an in-memory list of entries, and writes `<cache-root>/steeproute/index.json` via `write_json_atomic`. Directories without a `manifest.json` (including `.tmp/` and `.old/`) are skipped — they are not valid entries. `index.json` itself is **derived state** (truth lives in manifests); a missing or corrupt `index.json` is not an error, `rebuild_index` simply regenerates it.

7. `index.json` schema (defined here, consumed by Story 2.10):
   ```json
   {
     "schema_version": 1,
     "entries": [
       {"cache_key_hash": "...", "area": {"mode": "center_radius", "center": [lat, lon], "radius_km": r}}
     ]
   }
   ```
   `entries` ordering is deterministic (sorted by `cache_key_hash`) so the file is diff-stable across runs. The polygon construction for Story 2.10's `shapely.contains` check lives in 2.10 — this story just emits the area shape.

8. `bounds.geojson` is a GeoJSON `Polygon` Feature derived from the manifest's `area` (center/radius → bbox per `Area`'s "bbox half-side" semantics in `models.py:Area`). Implementation note: 4-vertex axis-aligned bbox in WGS84, no projection magic — same convention `osmnx.graph_from_point(..., dist_type="bbox")` consumes upstream. Use `json.dumps` with `sort_keys=True` so the file is reproducible byte-for-byte for a given input.

9. `cache.py::resolve_cache_root(override: Path | None = None) -> Path` returns `override` if non-`None`, else `Path(platformdirs.user_cache_dir("steeproute"))`. New runtime dep: `platformdirs` (single helper, single dep — no manual `%LOCALAPPDATA%` / `~/.cache` branching). CLI wiring of `--cache-dir` into `resolve_cache_root` is Story 2.8.

10. `tests/integration/test_cache_roundtrip.py` (new) writes a real-fixture-derived graph (run Story 2.5's `run_setup_stages` on the committed Grenoble fixture, same module-scoped fixture pattern as `test_pipeline_end_to_end.py`) then reads it back, asserting:
    - Node and edge counts match exactly (pickle roundtrip is byte-identical for `MultiDiGraph`; a topology-equality check is sufficient — full byte-equality of the pickle blob is fragile across networkx point versions).
    - Every edge's attribute contract survives the round-trip (`geometry`, `vertices_resampled`, `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`, `highway`, `osm_way_id`).
    - `manifest.json` on disk validates against the `Manifest` schema (parseable by `read_entry`, all required fields present, `schema_version == 1`).
    - `index.json` lists the new entry exactly once with the right `cache_key_hash` and area.

11. `tests/integration/test_cache_atomic.py` (new) simulates a mid-write abort. Use `monkeypatch.setattr` to wrap `os.replace` (or another call site late enough that `graph.pkl.tmp` exists but `manifest.json` has not been written) so it raises `KeyboardInterrupt` once, then verify:
    - The aborted entry's directory (whether it lives at `<hash>.tmp/` or partially at `<hash>/` depending on where the abort fires) has no `manifest.json` → `read_entry(cache_root, cache_key)` raises `CacheNotFoundError`.
    - A subsequent `rebuild_index(cache_root)` ignores the aborted directory; `index.json` contains zero entries (no partial entry surfaced).
    - A subsequent **successful** `write_entry` for the same key cleanly produces a valid entry (the partial state from the aborted run doesn't block re-prepare).

12. `tests/unit/test_cache.py` (new) covers `rebuild_index` recovery: build a small `tmp_path/areas/<h>/manifest.json` (no graph needed — `rebuild_index` only reads manifests), point at a missing `index.json` and assert recovery; then corrupt `index.json` (write invalid JSON bytes) and assert the next `rebuild_index` overwrites cleanly. Also covers `write_json_atomic` (writes via `.tmp` then renames; pre-existing target file is replaced; the function is the single chokepoint for atomic JSON in the module — verified by `grep -n "open(.*[\"']w" src/steeproute/cache.py` returning zero hits, encoded as a test that imports `cache` and asserts no module-level direct-write call sites via AST inspection if cheap, otherwise as a Dev Note discipline check).

13. All four CI gates pass on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov`. `cache.py` clears the 95% pure-logic coverage floor (Architecture §Cat 11e). `platformdirs` added to `[project] dependencies` in `pyproject.toml` (pinned to a current minor; semver-stable library). Live OSM test re-verified.

## Tasks / Subtasks

- [x] **Task 1: `write_json_atomic` + `write_entry` + overwrite swap** (AC: #1, #2, #3)
  - [x] `write_json_atomic(path, obj)` — `.tmp` sibling + `os.replace`, `sort_keys=True` + `indent=2` for diff-stable output.
  - [x] `write_entry(cache_root, manifest, graph)` composing the Cat 4d step order. Stale `.tmp/` and `.old/` from a prior aborted run on the same key are cleared at the top of `write_entry` (opportunistic per-key cleanup, Architecture §Cat 4d footnote).
  - [x] Existing-entry overwrite via `<hash>.old/` swap; `.old/` removed only after the new entry's `manifest.json` lands. Verified by `test_write_entry_overwrites_existing_entry_atomically`.
  - [x] `_bounds_geojson(area)` builder — 4-vertex axis-aligned WGS84 bbox Feature, written through `write_json_atomic` inside the staging dir.
- [x] **Task 2: `read_entry` + `PreparedData`** (AC: #4, #5)
  - [x] `PreparedData` (`@dataclass(frozen=True, slots=True)`) bundling graph + manifest. Shared return type with Story 2.10's `check_coverage`.
  - [x] `read_entry(cache_root, cache_key)` with the four error paths: missing entry/manifest → `CacheNotFoundError`; malformed `manifest.json` (parse failure) → `CacheCorruptedError`; missing or unpicklable `graph.pkl` → `CacheCorruptedError`; schema-version mismatch via `Manifest.from_dict` → `CacheCorruptedError`.
  - [x] `Manifest.from_dict(payload) -> Manifest` classmethod (sibling to Story 2.6's `to_dict`). Hard-fails on unknown `schema_version`, malformed `area` payload, or missing required field — no compat shim (Architecture §Versioned-contract-surfaces permits hard-fail).
- [x] **Task 3: `rebuild_index` + index schema** (AC: #6, #7)
  - [x] `rebuild_index(cache_root)` walks `areas/*/manifest.json`, skipping non-manifest dirs (`.tmp/`, `.old/`, half-written), parses each manifest through `Manifest.from_dict`, swallows corrupt manifests silently (next `read_entry` will surface them), and emits the index in `cache_key_hash`-sorted order via `write_json_atomic`. Bootstraps missing `areas/` and `steeproute/` subdirs on first run.
  - [x] `index.json` schema v1: `{schema_version, entries: [{cache_key_hash, area: {mode, center, radius_km}}]}`.
- [x] **Task 4: `resolve_cache_root` + `platformdirs` dep** (AC: #9)
  - [x] `resolve_cache_root(override)` helper returning `override` if non-`None` else `pathlib.Path(platformdirs.user_cache_dir("steeproute"))`.
  - [x] `platformdirs>=4,<5` added to `pyproject.toml` `[project] dependencies`; `uv sync` + lockfile refresh.
- [x] **Task 5: Integration tests** (AC: #10, #11)
  - [x] `tests/integration/test_cache_roundtrip.py` (5 tests) — module-scoped fixture runs `run_setup_stages` against the committed Grenoble fixture (same `unittest.mock.patch` of `osm_load` Story 2.5 used) then exercises `write_entry` → `read_entry`. Asserts node/edge counts + 9-attribute edge contract preservation, on-disk manifest validates via `Manifest.from_dict`, `index.json` lists the entry, overwrite swap leaves no `.old/` residue, `rebuild_index` recovers from a deleted `index.json`.
  - [x] `tests/integration/test_cache_atomic.py` (2 tests) — `monkeypatch` wraps `cache.os.replace` to raise `KeyboardInterrupt` when the rename target ends in `manifest.json` (i.e. the final commit signal). Asserts no partial entry surfaces via `read_entry` (raises `CacheNotFoundError`) and `rebuild_index` doesn't list the partial directory. Second test: a clean retry after the abort produces a valid entry (per-key cleanup works).
- [x] **Task 6: Unit tests** (AC: #12)
  - [x] `tests/unit/test_cache.py` (23 tests) — `write_json_atomic` mechanics (creates target, replaces existing, leaves no `.tmp` artifact, emits sorted keys, AST-walk chokepoint check that no `open(..., "w")` JSON write exists in `cache.py`); `rebuild_index` recovery (missing index, corrupt index, skips non-manifest dirs, sorted entries, empty `areas/`, bootstrap missing `areas/`); `Manifest.from_dict` (round-trip, unknown schema, missing schema, malformed area, missing field); `read_entry` error paths (no manifest for known/unknown key, malformed manifest JSON, missing graph.pkl); `PreparedData` frozen-enforcement; `resolve_cache_root` override + default.
- [x] **Task 7: Verify CI** (AC: #13)
  - [x] `uv run ruff check` (All checks passed!), `uv run ruff format --check` (43 files already formatted), `uv run basedpyright` (0 errors, 0 warnings, 0 notes), `uv run pytest --cov` (323 passed, 1 deselected — +30 from prior 293 — at 96% overall coverage).
  - [x] `cache.py` at 96% (161 stmts, 6 missed) — comfortably clears the 95% pure-logic floor. Six missed lines are defensive branches: malformed-center/radius variant of `Manifest.from_dict`, the pre-existing-`.tmp/` and pre-existing-`.old/` cleanups when called without a prior aborted run, the non-directory entry in `areas/` skip, and `rebuild_index`'s swallowed-corrupt-manifest path.
  - [x] `STEEPROUTE_USE_OS_TRUSTSTORE=1 UV_NATIVE_TLS=1 uv run pytest -m live` → 1 passed (no regression).

### Review Findings

_From `bmad-code-review` 2026-05-20. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Acceptance Auditor returned 0 findings — all 13 ACs satisfied. Blind Hunter raised 15, Edge Case Hunter raised 9. After dedupe + triage: 5 patches, 2 defers, 11 dismissed._

**Patches (unambiguous fixes):**

- [x] [Review][Patch] **P1 (HIGH): Backup-restore missing on manifest-write failure.** In `write_entry`, the existing entry is moved aside to `<hash>.old/`, the staging dir is renamed into `<hash>/`, then `write_json_atomic(<hash>/manifest.json, ...)` runs. If that final manifest write raises, `<hash>.old/` is leaked (next `write_entry` call's opportunistic cleanup will `shutil.rmtree` it, destroying the previous good entry) and `<hash>/` exists without a manifest. Wrap the manifest-write + `.old/` cleanup in `try/except BaseException: if backup_dir.exists(): swap entry_dir ↔ backup_dir; raise`. [src/steeproute/cache.py:336-360] [Source: blind+edge]

- [x] [Review][Patch] **P2 (HIGH): `bounds.geojson` axis-order inconsistency between geometry and `properties.center`.** `_bounds_geojson` emits `geometry.coordinates` as `[lon, lat]` (GeoJSON-correct) but `properties.center` as `[lat, lon]` (matching the manifest's `area.center` convention). A consumer reading both gets contradictory axis orders. Align `properties.center` to `[lon, lat]` so the GeoJSON file is internally consistent; the manifest convention stays separate in `manifest.json`. [src/steeproute/cache.py:489-498] [Source: blind]

- [x] [Review][Patch] **P3 (MED): `Manifest.from_dict` silently coerces non-string fields and lacks numeric-conversion safety.** A payload with `"dem_version": null` becomes `"None"` (the string) via `str(payload["dem_version"])`, then propagates into the cache-key directory naming. Similarly, `float(center[0])` raises bare `ValueError` if `center` contains non-numeric strings, leaking past the `CacheCorruptedError` contract. Validate each required field is `isinstance(..., str)` before coercion, and wrap the `float(center[i])` / `float(radius_km)` calls in `try/except (TypeError, ValueError) → CacheCorruptedError`. [src/steeproute/cache.py:215-245] [Source: blind+edge]

- [x] [Review][Patch] **P4 (MED): `write_json_atomic` leaves `.tmp` orphan if `os.replace` raises.** A failed final rename (`ENOSPC`, `EACCES`, cross-device, etc.) leaves the `.tmp` sibling on disk indefinitely. Wrap the write + replace in `try/finally tmp_path.unlink(missing_ok=True)` so the temp file is cleaned up whether the replace succeeds or fails. [src/steeproute/cache.py:282-299] [Source: blind+edge]

- [x] [Review][Patch] **P5 (MED): Exception lists in `read_entry` and `rebuild_index` are too narrow to honor the contract.** AC #4 contracts that cache-read errors surface as `CacheNotFoundError` or `CacheCorruptedError` (mapping to `exit 2` via `run_entry_point`). Three call sites currently let realistic failure modes leak past the contract: (a) `read_entry` manifest-read catches only `JSONDecodeError` — `OSError` (permissions, I/O) and `UnicodeDecodeError` (corrupted bytes) leak as raw exceptions; (b) `read_entry` pickle catches only `UnpicklingError`/`EOFError`/`FileNotFoundError` — `AttributeError`/`ImportError`/`ModuleNotFoundError` from a stale-pickle (cache predates a package refactor) leak; (c) `rebuild_index` catches only `JSONDecodeError`+`CacheCorruptedError` — one `OSError` from a single bad manifest crashes the entire index rebuild, hiding all other valid entries. Widen each catch list. [src/steeproute/cache.py:385-402, 432-438] [Source: edge]

**Deferred (real but owned elsewhere):**

- [x] [Review][Defer] **D1 (MED): KeyboardInterrupt between manifest commit and `rebuild_index` leaves stale index.** Architecture §Cat 4d says manifest is the commit signal; the index is derived state. If a user `Ctrl-C`s after the manifest `os.replace` lands but before `rebuild_index` runs, the entry is readable via `read_entry(cache_key)` but `index.json` doesn't list it. Next `write_entry` (any key) calls `rebuild_index` and fixes it. But a user invoking `steeproute` (query) before the next setup would hit the stale index — and that's exactly the coverage-check path. Right time to add an opportunistic `rebuild_index` call on the read path is Story 2.10's `check_coverage`. Recorded in deferred-work.md. [src/steeproute/cache.py:301-360] [Source: blind]

- [x] [Review][Defer] **D2 (MED): `rebuild_index` swallows `CacheCorruptedError` silently — no log, no counter.** A cache directory entirely full of corrupt manifests yields a successful empty-index rebuild indistinguishable from "no entries". A `logger.warning(...)` would surface this for a `--verbose` user, but logging infrastructure isn't wired yet — same reason Story 2.5's `_drop_*` debug logs were deferred to Story 2.8. Routes to Story 2.8 alongside the rest of the CLI verbose plumbing. [src/steeproute/cache.py:432-438] [Source: blind]

**Dismissed (noise / false positive / handled elsewhere):**

- [x] [Review][Dismiss] **`write_json_atomic` lacks `fsync` — not power-loss-durable.** Architecture §Reliability requires interrupt-safe atomic writes (covered: a `KeyboardInterrupt` mid-write leaves no partial entry), not power-loss durability. Adding `fsync` to every cache write is real perf cost without proportional benefit for an N=1 hobby project. The architectural trade was made explicitly. [blind]
- [x] [Review][Dismiss] **`_TMP_DIR_SUFFIX = ".tmp"` is overloaded for dir-staging and file-scratch suffixes.** The two usages don't collide at runtime — file `.tmp` lives next to the file (`manifest.json.tmp` next to `manifest.json`); dir `.tmp` lives inside `areas/` (`<hash>.tmp/`). Renaming one constant would just hide the string coincidence. Not a bug. [blind]
- [x] [Review][Dismiss] **`or 1.0` cos near-pole singularity in `_bounds_geojson`.** Project explicitly targets Grenoble Alps (~45° N); pole singularity is unreachable. The `or 1.0` is a defensive against exact-zero only and works correctly for any latitude this tool will see. [blind]
- [x] [Review][Dismiss] **Module-level pyright pragma in `cache.py` too broad.** Identical pattern Story 2.5 established in `pipeline/__init__.py` (and `pipeline/osm.py`, `pipeline/smoothing.py`, etc.) for files touching `networkx.MultiDiGraph`. Per-call-site `pyright: ignore` would be noisier and harder to maintain. Established project convention. [blind]
- [x] [Review][Dismiss] **`pickle.load` of cache content → arbitrary code execution.** Cache dir is user-owned (`~/.cache/steeproute/` on Linux, `%LOCALAPPDATA%\steeproute\Cache\` on Windows). Attacker write access there implies attacker already controls the user account — pickle ACE is the least concern in that scenario. Architecture §Cat 4c explicitly chose pickle as the graph format (networkx 3.x recommended serialization; preserves MultiDiGraph + edge attributes). Hash-integrity addition would be feature scope, not 2.7. [blind]
- [x] [Review][Dismiss] **AC labels in test docstrings don't match assertions; AC #2 chokepoint test asserts implementation rather than behavior.** Blind Hunter has no spec context to verify these. AC #2's "all cache JSON writes route through `write_json_atomic`" is explicitly an implementation-property anti-pattern requirement (per Architecture §Key anti-patterns: "no per-site reimplementation"). The AST-walk test is precisely the right shape for that AC. [blind]
- [x] [Review][Dismiss] **`prepared_graph` fixture is `scope="module"` — risks shared mutable state.** Tests don't mutate the graph (verified by inspection). Function-scoping would re-run the full 1-7 stage pipeline per test (~1-2s each), substantially slowing the suite. Same pattern Story 2.5's `test_pipeline_end_to_end.py` already uses. [blind]
- [x] [Review][Dismiss] **`rebuild_index` called from `write_entry` → O(N) coupling between unrelated keys.** Architecture §Cat 4 explicitly chose index-as-derived-state — truth lives in manifests, index is rebuilt from them. The alternative (incremental index update) was rejected because it adds a second state-mutation path that has to stay consistent. N is small (single-user, handful of areas). [blind]
- [x] [Review][Dismiss] **`importlib.exec_module(regenerate.py)` runs at test-collection time.** Same pattern Story 2.5's `test_pipeline_end_to_end.py` already uses; `regenerate.py` has no import-time side effects (just module-level `CENTER_LAT`/`CENTER_LON`/`DIST_M` constants). [blind]
- [x] [Review][Dismiss] **`staging_dir` / `backup_dir` exists as a regular file → `shutil.rmtree` fails.** Requires external interference (a process creating files at `<hash>.tmp` exactly when `write_entry` runs). Architecture explicitly rejects locking for v1 and the single-writer model says only `steeproute-setup` writes to `areas/`. Not a real failure mode. [edge]
- [x] [Review][Dismiss] **`os.replace(entry_dir, backup_dir)` raises `PermissionError` on Windows under handle contention.** Concurrent writers explicitly rejected by Architecture §Versioned-contract-surfaces ("Two concurrent `steeproute-setup` runs ... race on the final directory rename; the loser's write is fully overwritten"). No other process is expected to hold handles inside `areas/` during a write. [edge]

## Dev Notes

- **Why `write_entry` takes `manifest` (not raw fields).** Story 2.6 built `Manifest` to be the on-disk wire shape's source of truth. Forcing the caller to construct a `Manifest` before `write_entry` means provenance + version fields can't be silently dropped, and `write_entry` stays focused on disk I/O. Story 2.8's `cli/setup.py::main` is the natural caller — it has the `compute_cache_key` result, the `osm_extract_date`, the commit-short, and `iso8601_utc_now()` all in scope.
- **Why `read_entry` parses manifest into the dataclass.** Returning a `dict` would push schema validation onto every reader. Stage-1 readers (Story 2.10's coverage check; Story 2.9's OSM-age warning; Epic 3's report metadata block) all want typed fields. `Manifest.from_dict` is the choke point where schema-version mismatches surface as `CacheCorruptedError`.
- **`schema_version` handling at read time.** Today both `manifest.json` and `index.json` carry `schema_version: 1`. The version-mismatch path (`schema_version != 1`) raises `CacheCorruptedError`; no compat shim. When a v2 lands, the choice point is whether to add a compat reader or hard-bump — out of scope here. Architecture §Versioned-contract-surfaces explicitly permits either; we pick the simpler hard-fail.
- **Stale `.tmp/` cleanup.** Architecture §Cat 4d footnote says cleanup happens "opportunistically at the start of the next `steeproute-setup` run". This story scopes that to per-key cleanup inside `write_entry` (clear any pre-existing `<hash>.tmp/` for the key being written). A full sweep of all orphan `.tmp/` directories under `areas/` is **not** done in this story — it would interact poorly with hypothetical concurrent setup runs, and N=1 doesn't need it. If the orphan accumulation ever becomes a problem, a sweep helper drops in later as a contained change.
- **What this story does NOT do:**
  - **CLI wiring of `--cache-dir` / `--force-refresh` / `--dem-version`** — Story 2.8.
  - **`check_coverage(query_area) -> PreparedData`** — Story 2.10 (uses `PreparedData` defined here and `index.json` emitted here).
  - **OSM-age warning at cache hit** — Story 2.9 (reads `manifest.osm_extract_date` via `read_entry`).
  - **Concurrent-write locking** — explicitly rejected in Architecture §Versioned-contract-surfaces ("Two concurrent `steeproute-setup` runs targeting the **same** area race on the final directory rename; the loser's write is fully overwritten").

### Project Structure Notes

- **Extended**: `src/steeproute/cache.py` — adds `write_json_atomic`, `write_entry`, `read_entry`, `rebuild_index`, `resolve_cache_root`, `PreparedData`, `Manifest.from_dict`, plus the supporting private helpers (`bounds.geojson` builder, `<hash>.old/` swap). No changes to the Story 2.6 surface (`compute_cache_key`, `compute_pipeline_content_hash`, `Manifest`, `Manifest.to_dict`).
- **Extended**: `pyproject.toml` — adds `platformdirs` to `[project] dependencies`.
- **New**: `tests/integration/test_cache_roundtrip.py`, `tests/integration/test_cache_atomic.py`, `tests/unit/test_cache.py`.
- **Untouched**: `src/steeproute/cli/setup.py` (Story 2.8); `src/steeproute/cli/_shared.py` (decorators already defined Epic 1 Story 1.5); `src/steeproute/models.py` (no schema changes); `src/steeproute/errors.py` (`CacheNotFoundError`, `CacheCorruptedError` already defined in the Story 1.4 hierarchy).

### Testing standards summary

- Layer: unit tests in `tests/unit/test_cache.py` for `rebuild_index` recovery + `write_json_atomic` mechanics. Integration tests in `tests/integration/` for the real-fixture round-trip and the crash-safety simulation — this matches Architecture §Cat 11e's "cache.py has integration tests for: write-then-read roundtrip, atomic-write crash safety, coverage check on containing vs. non-containing areas" (coverage check is Story 2.10).
- Real fixture reuse: the round-trip test uses the same `osm_graph.graphml` + `dem.tif` fixtures Stories 2.1–2.5 committed; same `unittest.mock.patch` of `osm_load` Story 2.5 used to avoid live Overpass calls.
- Crash-safety simulation: `monkeypatch`-injected `KeyboardInterrupt` is the only mechanism that can simulate a mid-write abort deterministically — direct subprocess kill is non-portable on Windows. Per Architecture §Cat 11e: "simulated failure handling" is one of the listed legitimate uses of mocking.
- Coverage floor: 95% on `cache.py` (carries over the §Cat 11e floor Story 2.6 set; the new I/O paths are exercised by integration tests).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 2.7"] — AC anchor and write-flow constraints
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 4d — Atomic write pattern] — five-step write order, manifest-as-commit-signal rule
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 4a — Directory layout] — `<cache-root>/steeproute/areas/<hash>/{manifest,graph,bounds}` shape, `index.json` as derived coverage-lookup
- [Source: _bmad-output/planning-artifacts/architecture.md §Versioned-contract-surfaces] — `schema_version` mismatch handling, single-writer asymmetry, atomic-rename concurrency model
- [Source: _bmad-output/planning-artifacts/architecture.md §Key anti-patterns] — single `write_json_atomic` chokepoint rule
- [Source: _bmad-output/planning-artifacts/architecture.md §Boundaries — Cache boundary] — `cache.py` is the sole reader/writer of cache files
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11e — Testing strategy] — integration tests for cache; 95% pure-logic coverage floor
- [Source: src/steeproute/cache.py] — Story 2.6's `Manifest`, `compute_cache_key`, `compute_pipeline_content_hash` already in place
- [Source: src/steeproute/errors.py] — `CacheNotFoundError`, `CacheCorruptedError` already defined
- [Source: src/steeproute/pipeline/__init__.py] — `run_setup_stages` produces the graph the round-trip test consumes
- [Source: tests/integration/test_pipeline_end_to_end.py] — module-scoped fixture pattern + `unittest.mock.patch` of `osm_load` to reuse

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` for the corporate Netskope TLS-intercepting proxy (required for `uv sync` and live OSM test).

**New runtime dep:** `platformdirs>=4,<5` added to `[project] dependencies` (single resolver helper, no manual `%LOCALAPPDATA%` / `~/.cache` branching).

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 43 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 323 passed, 1 deselected in ~23s; coverage 96% overall
                                     - cache.py      96% (161 stmts, 6 missed — defensive branches)
                                     - other modules unchanged from Story 2.6
```

Live OSM test re-verified: `STEEPROUTE_USE_OS_TRUSTSTORE=1 UV_NATIVE_TLS=1 uv run pytest -m live` → 1 passed.

### Completion Notes List

**Design decisions worth review attention:**

1. **`write_entry` takes a fully-constructed `Manifest`.** Forcing the caller (Story 2.8's `cli/setup.py::main`) to assemble the manifest means provenance, commit-short, `osm_extract_date`, and `created_at` cannot be silently dropped — `write_entry` is just disk I/O. Caller has those values in scope already via `compute_cache_key` + `provenance.get_commit_short` + `provenance.iso8601_utc_now`.

2. **`Manifest.from_dict` hard-fails on schema-version mismatch.** Architecture §Versioned-contract-surfaces permits either compat shim or hard-fail; we chose hard-fail because there is no v2 schema yet and a compat reader is dead code until one lands. Future schema bump is a contained change: either add a compat reader here or bump the constant and accept entry-invalidation.

3. **`write_entry` overwrite uses `<hash>.old/` shuffle even on POSIX where `os.replace` on directories *can* work.** Cost: one extra rename per overwrite, which is negligible compared to the pickle write. Benefit: same code path Windows-and-POSIX, so the test suite catches Windows regressions on Linux dev machines too.

4. **`bounds.geojson` uses a flat equator-approximation (1° lat ≈ 111 km, 1° lon ≈ 111 km × cos(lat)).** The file plays a diagnostic / debug-viz role only — coverage math (Story 2.10) recomputes from `Area + radius_km` via `shapely`, not from `bounds.geojson`. A small projection skew here doesn't propagate into the cache-hit decision.

5. **`rebuild_index` swallows corrupt-manifest exceptions silently.** A `CacheCorruptedError` raised during the index walk would block all index rebuilds because one bad entry would poison the whole operation. Instead the bad entry is skipped from the index; the next `read_entry` against its key surfaces the corruption with full context. Symmetric to how `os` walk-style helpers handle bad subdirs.

6. **`write_entry` calls `rebuild_index` as its last step.** An alternative was to incrementally append to `index.json`, but a full rebuild is O(N entries) where N is small (a user has at most a handful of prepared areas), and rebuilding-from-truth is one fewer state-mutation path to keep consistent. The atomic-write of `index.json` via `write_json_atomic` means the rebuild is itself crash-safe.

7. **The chokepoint anti-pattern check is encoded as an AST-walk test** (`test_write_json_atomic_chokepoint_no_direct_writes_in_cache_module`) rather than a comment in the module. Architecture §Key anti-patterns lists "no per-site reimplementation of atomic writes" as a rule; encoding it as a test means future refactors that try to bypass `write_json_atomic` fail loudly at test time rather than silently in code review.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `write_entry` per Cat 4d step order. Manifest written last; `.tmp/` → `<hash>/` → `manifest.json` rename sequence. Verified by `test_write_and_read_entry_round_trips_real_fixture_graph` (disk layout) + `test_write_entry_manifest_matches_schema` (manifest validates after write). ✅
2. AC #2 — `write_json_atomic` is the single chokepoint. Verified by `test_write_json_atomic_*` (5 tests) + the AST-walk chokepoint test. ✅
3. AC #3 — Windows-safe overwrite via `<hash>.old/`. Verified by `test_write_entry_overwrites_existing_entry_atomically`. ✅
4. AC #4 — `read_entry` four error paths (missing entry, missing/malformed manifest, missing graph.pkl, schema mismatch). Verified by 4 unit tests in `test_cache.py`. ✅
5. AC #5 — `PreparedData` frozen-slots dataclass. Verified by `test_prepared_data_is_frozen`. ✅
6. AC #6 — `rebuild_index` derives state from manifests, skips invalid dirs. Verified by 6 `test_rebuild_index_*` tests. ✅
7. AC #7 — `index.json` schema with `cache_key_hash`-sorted entries. Verified by `test_rebuild_index_emits_entries_sorted_by_cache_key_hash` + `test_write_entry_index_reflects_new_entry`. ✅
8. AC #8 — `bounds.geojson` is a GeoJSON Polygon Feature; written through `write_json_atomic`. Verified by the roundtrip test asserting `bounds.geojson` exists on disk. ✅
9. AC #9 — `resolve_cache_root` override vs. platformdirs default. Verified by `test_resolve_cache_root_*` (2 tests). ✅
10. AC #10 — Full roundtrip test against real fixture. 5 tests in `test_cache_roundtrip.py`. ✅
11. AC #11 — Crash-safety simulation via `monkeypatch`-injected `KeyboardInterrupt`. 2 tests in `test_cache_atomic.py`. ✅
12. AC #12 — `rebuild_index` recovery + `write_json_atomic` mechanics. Covered by `test_cache.py`. ✅
13. AC #13 — All four CI gates green; 96% coverage on `cache.py` (above 95% floor); live OSM re-verified; `platformdirs` added to `pyproject.toml`. ✅

### File List

**New:**
- `tests/unit/test_cache.py` — 31 tests covering `write_json_atomic` mechanics + AST-walk chokepoint enforcement + crash-cleanup (P4), `rebuild_index` recovery, `Manifest.from_dict` (round-trip + null-field rejection + non-numeric-coord rejection from P3), `read_entry` error paths (missing/malformed manifest + missing graph.pkl + UnicodeDecodeError + stale-pickle ImportError from P5), `bounds.geojson` axis-order consistency (P2), `PreparedData` frozen, `resolve_cache_root` override + default.
- `tests/integration/test_cache_roundtrip.py` — 5 tests for the real-fixture write→read→index roundtrip + overwrite swap.
- `tests/integration/test_cache_atomic.py` — 3 tests for the `KeyboardInterrupt`-mid-write crash-safety simulation + aborted-overwrite-rollback (P1) + clean retry.

**Modified:**
- `src/steeproute/cache.py` — added `write_json_atomic`, `write_entry`, `read_entry`, `rebuild_index`, `resolve_cache_root`, `PreparedData`, `Manifest.from_dict`, `_bounds_geojson` private helper, `_areas_dir` private helper, plus supporting module-scope constants. Added per-file basedpyright pragma matching the pipeline modules (now that `cache.py` touches `networkx.MultiDiGraph`).
- `pyproject.toml` — `platformdirs>=4,<5` added to `[project] dependencies`.
- `uv.lock` — refreshed by `uv sync` to include `platformdirs==4.9.6`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.7 `ready-for-dev → in-progress → review → done`.
- `_bmad-output/implementation-artifacts/deferred-work.md` — two items routed from 2.7 code review (D1 stale-index window → Story 2.10; D2 silent corrupt-manifest swallowing → Story 2.8).

**Untouched (intentionally):**
- `src/steeproute/cli/setup.py` — Story 2.8 will wire `cli/setup.py::main` against `write_entry` / `read_entry` / `resolve_cache_root`.
- `src/steeproute/cli/_shared.py` — `--cache-dir` / `--force-refresh` / `--dem-version` / `--osm-age-warn-days` decorators already defined in Epic 1 Story 1.5; Story 2.8 wires them.
- `src/steeproute/models.py` — no schema changes; `Area` already canonical-ready and `PipelineConfig` already in shape from Story 2.5.
- `src/steeproute/errors.py` — `CacheNotFoundError`, `CacheCorruptedError` already defined in Story 1.4's hierarchy.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-20 | Yann (Claude Opus 4.7) | Adversarial `bmad-code-review` (Blind Hunter + Edge Case Hunter + Acceptance Auditor parallel layers) applied: 5 patches landed, 2 deferred, 11 dismissed. **P1 (HIGH):** `write_entry` now restores `<hash>.old/` to `<hash>/` if the final manifest commit raises — prevents the next call's opportunistic `.old/` cleanup from silently destroying the prior good entry on an interrupted overwrite. **P2 (HIGH):** `_bounds_geojson`'s `properties.center` now uses GeoJSON `[lon, lat]` to match `geometry.coordinates` (was `[lat, lon]` — inconsistent with the geometry it sits next to). The manifest's `area.center` remains `[lat, lon]` per Architecture §Cat 4. **P3 (MED):** `Manifest.from_dict` now rejects non-string fields (a `null` `dem_version` no longer coerces to the literal string `"None"`) and wraps `float()` calls on `center` in `try/except (TypeError, ValueError) → CacheCorruptedError`. **P4 (MED):** `write_json_atomic` now cleans up the `.tmp` sibling on any failure (write_text or os.replace) — no orphan accumulation. **P5 (MED):** `read_entry` manifest-read catches `UnicodeDecodeError` + `OSError` in addition to `JSONDecodeError`; `read_entry` pickle catches `ImportError` + `AttributeError` + `OSError` in addition to `UnpicklingError`/`EOFError`/`FileNotFoundError` (stale-pickle after a refactor + I/O failure honor the `CacheCorruptedError → exit 2` contract); `rebuild_index` widens its swallow list symmetrically. 9 new tests for the patches: P1 aborted-overwrite-restores-v1 integration test; P2 axis-order consistency unit test; P3 null-string-field rejection + non-numeric-coords rejection; P4 `.tmp` cleanup on os.replace failure + `.tmp` cleanup on write_text failure; P5 UnicodeDecodeError → CacheCorruptedError + stale-pickle ImportError → CacheCorruptedError. 2 items deferred to deferred-work.md (D1 KeyboardInterrupt-between-manifest-commit-and-rebuild-index stale-index window → Story 2.10's coverage check; D2 silent `CacheCorruptedError`-swallow in `rebuild_index` → Story 2.8's `--verbose` logging wiring). 11 findings dismissed inline in Review Findings: fsync durability (hobby-project trade), `_TMP_DIR_SUFFIX` overload (string coincidence, not collision), `or 1.0` cos-pole (unreachable at Grenoble), pyright module-pragma scope (project convention), pickle ACE (cache dir is user-owned), AC-label mismatch (Blind Hunter missing spec context), module-scoped fixture (Story 2.5 pattern, no mutation), index-as-derived-state O(N) coupling (Architecture-explicit choice), importlib at collection (no side effects), regular-file-staging-dir (no concurrent writer), Windows PermissionError under handle contention (no concurrent writer). All four CI gates green post-review: ruff, ruff format, basedpyright 0/0/0, pytest 332 passed (+9 patch-test additions) at 96% overall coverage; `cache.py` at 97% (177 stmts, 6 missed defensive branches) — well above the 95% pure-logic floor. | _pending_ |
| 2026-05-20 | Yann (Claude Opus 4.7) | Story 2.7 implemented: atomic cache write + read + index maintenance per Architecture §Cat 4d. `cache.py::write_entry(cache_root, manifest, graph) -> Path` (`.tmp/` → final-dir rename → `manifest.json` last as commit signal; `<hash>.old/` swap for Windows-safe overwrite; per-key cleanup of stale `.tmp/`/`.old/` from prior aborted runs). `cache.py::write_json_atomic(path, obj)` (single chokepoint for all atomic JSON writes; `.tmp` sibling + `os.replace`; `sort_keys=True` + `indent=2` for diff-stable output). `cache.py::read_entry(cache_root, cache_key) -> PreparedData` (four error paths: missing manifest → `CacheNotFoundError`; unparseable / schema-mismatched manifest → `CacheCorruptedError`; missing or unpicklable `graph.pkl` → `CacheCorruptedError`). `cache.py::rebuild_index(cache_root)` (derives `index.json` from `areas/*/manifest.json`; skips non-manifest dirs; deterministic `cache_key_hash`-sorted entries; swallows corrupt manifests silently). `cache.py::resolve_cache_root(override)` (platformdirs default + `--cache-dir` override). `cache.py::PreparedData` (`@dataclass(frozen=True, slots=True)`, bundles graph + manifest; shared with Story 2.10's `check_coverage`). `cache.py::Manifest.from_dict` (sibling to Story 2.6's `to_dict`; hard-fails on schema mismatch). `_bounds_geojson` private helper emits a GeoJSON Polygon Feature for the area bbox. 30 new tests: `tests/unit/test_cache.py` (23 — write_json_atomic mechanics + AST-walk chokepoint enforcement, rebuild_index recovery from missing/corrupt index + bootstrap, Manifest.from_dict error branches, read_entry error paths, PreparedData frozen, resolve_cache_root override + default) + `tests/integration/test_cache_roundtrip.py` (5 — write → read on real Grenoble fixture-derived graph, manifest schema roundtrip, index reflects new entry, overwrite leaves no `.old/` residue, rebuild_index recovery after manual `index.json` deletion) + `tests/integration/test_cache_atomic.py` (2 — `monkeypatch`-injected `KeyboardInterrupt` on manifest-final-rename leaves no partial entry surfaced via read_entry / rebuild_index, clean retry after abort succeeds). New runtime dep: `platformdirs>=4,<5`. All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 323 passed (+30 from prior 293) at 96% overall coverage; `cache.py` at 96% (161 stmts, 6 missed defensive branches) — above the 95% pure-logic floor. Live OSM test re-verified — no regression. | _pending_ |
