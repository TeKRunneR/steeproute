# Story 2.6: Implement cache key hashing, manifest schema, and provenance helpers

Status: done

## Story

As a developer,
I want `cache.py` to compute a canonical cache-key hash and define the `manifest.json` schema, and `provenance.py` to resolve git commit hash + dirty flag + a UTC timestamp helper,
so that Story 2.7's atomic write/read flow has the deterministic key, the manifest dataclass, and the provenance fields it needs ready to go.

## Acceptance Criteria

1. `cache.py::compute_cache_key(area: Area, untagged_policy: str, dem_version: str, pipeline_content_hash: str) -> str` returns the 16-hex-character lowercase truncation of `SHA256(canonical_json(...))` over the four inputs. Canonical JSON uses `json.dumps(..., sort_keys=True, separators=(",", ":"))` over a `dict` with stable keys. Area canonicalization rounds `center` lat/lon to 6 decimals and `radius_km` to 3 decimals *before* hashing (Architecture §Cat 4b: "floating-point noise doesn't produce phantom misses"). Pure function — no I/O, no global state.

2. `cache.py::compute_pipeline_content_hash() -> str` returns the full 64-hex SHA256 over the byte content of `src/steeproute/pipeline/**/*.py` + `src/steeproute/models.py`, with files sorted by their POSIX-form path-relative-to-`src/steeproute/` so the hash is platform-independent. Each file's bytes are hashed in order; no separator byte needed (file ordering + content together fully determine the hash). The package root is resolved via `pathlib.Path(__file__).parent` from inside `cache.py`.

3. `cache.py::Manifest` is a `@dataclass(frozen=True, slots=True)` matching the schema in Architecture §Cat 4. Fields, in order: `schema_version: int = 1` (last for default-value ordering), `area: Area`, `untagged_policy: str`, `dem_version: str`, `pipeline_content_hash: str`, `osm_extract_date: str`, `cache_key_hash: str`, `steeproute_version: str`, `steeproute_commit: str`, `created_at: str`. A `to_dict(self) -> dict[str, object]` method returns the wire-shape dict that will be JSON-serialized by Story 2.7's `write_json_atomic` — `area` emits as `{"mode": "center_radius", "center": [lat, lon], "radius_km": r}` (the `mode` literal is hard-coded; center/radius is the only Area mode in v1). All other fields pass through unchanged.

4. `provenance.py::get_commit_short() -> str` shells out to `git rev-parse --short HEAD` and `git -c core.fileMode=false status --porcelain`, returning `"{hash}-dirty"` if the porcelain output is non-empty, else `"{hash}"`. Subprocess calls use `subprocess.run(..., check=True, capture_output=True, text=True, cwd=Path(__file__).parent)`. If `git` is unavailable or the package isn't inside a git repo (`subprocess.CalledProcessError` or `FileNotFoundError`), return the sentinel string `"unknown"` — never raise. No `shell=True`.

5. `provenance.py::iso8601_utc_now() -> str` returns the current UTC time formatted as `"YYYY-MM-DDTHH:MM:SSZ"` (second precision, literal `Z` suffix — matches Architecture §Serialization conventions verbatim and the example timestamp in the §Cat 4 manifest schema). Internally uses `datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")`.

6. `tests/unit/test_cache_key.py` (new) asserts:
   - **Canonicalization** — `Area((45.0716000, 6.1079000), 50.000)` and `Area((45.0716007, 6.1079003), 50.0001)` produce identical cache keys (7th-decimal lat, 7th-decimal lon, and 4th-decimal radius drift, all absorbed by rounding).
   - **Sensitivity to inputs** — changing `untagged_policy`, `dem_version`, *or* `pipeline_content_hash` each individually produces a different key. Cover all three.
   - **Pipeline content hash sensitivity** — write a temporary modification to one of the hashed files (use `tmp_path` + `monkeypatch.setattr` to redirect the package-root path resolution, OR temporarily edit then restore a file — pick the cleanest pattern; document the choice in a comment). Assert the hash changes. Sanity-check that calling `compute_pipeline_content_hash()` twice in a row on an unchanged tree returns identical hashes.
   - **Output shape** — `compute_cache_key` returns a 16-char lowercase hex string; `compute_pipeline_content_hash` returns a 64-char lowercase hex string.

7. `tests/unit/test_provenance.py` (new) asserts:
   - **Dirty flag flips on a real working-tree modification** — using `tmp_path` as a scratch directory: shell out to `git init`, configure a throwaway `user.name`/`user.email`, `git add` + `git commit` a tracked file, then assert `get_commit_short()` (invoked with that tmp tree as cwd via temporary `monkeypatch` of the cwd argument resolution — or split production code so the cwd is injectable) returns a clean hash; modify the tracked file, assert the return value now ends with `-dirty`. *No `subprocess` mocking* — real git, per Story 2.6 epic AC.
   - **`unknown` sentinel** — running against a directory that is not a git repo (`tmp_path` with no `.git/`) returns `"unknown"`.
   - **`iso8601_utc_now` format** — return value matches `r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"` and round-trips through `datetime.datetime.fromisoformat`.

   To make `get_commit_short` testable against a chosen working directory without leaking that arg into the public API, introduce a private helper `_get_commit_short_at(cwd: pathlib.Path) -> str` that the public `get_commit_short()` delegates to with the package-root path. Tests call the private helper directly. Same shape as Story 2.3's private-helper precedent.

8. The new `Manifest` dataclass has at least one round-trip test in `tests/unit/test_cache_key.py` (or a sibling test file — caller's choice): build a `Manifest` with concrete field values, call `to_dict()`, assert every Architecture §Cat 4 schema key appears with the right type and that `area` emits as the nested dict shape. JSON-write/read happens in Story 2.7; this test only covers shape.

9. `tests/unit/test_provenance.py::test_get_commit_short_real_repo_no_modifications_returns_clean_hash` runs the *real production helper* against the actual `bmad-test` repo (no tmp tree) and asserts the return value matches `r"^[0-9a-f]{7,40}(-dirty)?$"`. This catches integration regressions that the tmp-repo tests can miss (e.g. the production cwd resolution is broken).

10. All four CI gates pass on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov`. No new runtime or dev deps (stdlib `hashlib`, `subprocess`, `json`, `datetime`, `pathlib` cover everything). `cache.py` and `provenance.py` clear the 95% pure-logic coverage floor (Architecture §Cat 11e). Live OSM test re-verified.

## Tasks / Subtasks

- [x] **Task 1: Implement `cache.py` cache-key + content-hash + manifest** (AC: #1, #2, #3)
   - [x] Module docstring expanded from the one-line placeholder per Architecture §Documentation discipline (cache is explicitly called out as a module where longer docstrings are accepted).
   - [x] `compute_cache_key(area, untagged_policy, dem_version, pipeline_content_hash) -> str` per AC #1.
   - [x] `compute_pipeline_content_hash() -> str` per AC #2. Module-scope `_CACHE_KEY_HEX_LEN = 16`, `_AREA_LAT_LON_DECIMALS = 6`, `_AREA_RADIUS_KM_DECIMALS = 3`, `_AREA_MODE_LITERAL = "center_radius"`, `_PIPELINE_CONTENT_GLOBS = ("pipeline/**/*.py", "models.py")`. Sort key is `path.relative_to(package_root).as_posix()`.
   - [x] `Manifest` dataclass (`@dataclass(frozen=True, slots=True)`) + `to_dict()` per AC #3. `schema_version` defaulted, declared last so the dataclass argument ordering rule is satisfied.
- [x] **Task 2: Implement `provenance.py` git + datetime helpers** (AC: #4, #5)
   - [x] Module docstring expanded — provenance is the single source of truth for the commit-hash + ISO 8601 helpers; the longer docstring documents the "never raises" contract.
   - [x] Private `_get_commit_short_at(cwd)` and public `get_commit_short()` per AC #4. `_PACKAGE_ROOT = pathlib.Path(__file__).parent` module-scope. `_UNKNOWN_COMMIT_SENTINEL = "unknown"`. `core.fileMode=false` passed inline to `git status` so a stale-execute-bit on Windows checkouts doesn't spuriously flip the dirty flag.
   - [x] `iso8601_utc_now()` per AC #5.
- [x] **Task 3: Unit tests for cache key + content hash + manifest** (AC: #6, #8)
   - [x] `tests/unit/test_cache_key.py` (new) — 12 tests covering AC #6 bullets + AC #8 round-trip-shape test + frozen-dataclass enforcement. Pipeline-content-hash change test patches `cache_mod.__file__` to a fake-package tmp_path so no real source file is touched (avoids leaving a dirty tree across the suite).
- [x] **Task 4: Unit tests for provenance helpers** (AC: #7, #9)
   - [x] `tests/unit/test_provenance.py` (new) — 5 tests covering AC #7 bullets + AC #9 real-repo smoke. `_init_throwaway_repo` helper builds a tmp git repo via `subprocess.run(["git", ...])` — no mocking. Local `user.name`/`user.email` set so commit succeeds on hosts without global git identity. Non-git test sets `GIT_CEILING_DIRECTORIES` via `monkeypatch.setenv` so a tmp directory inside a parent git checkout still returns the `unknown` sentinel.
- [x] **Task 5: Verify CI** (AC: #10)
   - [x] `uv run ruff check` (All checks passed!), `uv run ruff format --check` (40 files already formatted), `uv run basedpyright` (0 errors, 0 warnings, 0 notes), `uv run pytest --cov` (293 passed, 1 deselected — +17 from prior 276 — at 97% overall coverage).
   - [x] `cache.py` 100% (44 stmts, 0 missed) and `provenance.py` 100% (17 stmts, 0 missed) — both clear the 95% floor.
   - [x] `STEEPROUTE_USE_OS_TRUSTSTORE=1 UV_NATIVE_TLS=1 uv run pytest -m live` → 1 passed (no regression).

### Review Findings

_From lightweight inline review 2026-05-20. Small surface (~110 lines production, ~210 lines tests, all stdlib). Adversarial pass against the two new modules + two new test files. 1 patch landed, 1 deferred, 6 dismissed inline._

**Patches (unambiguous fixes):**

- [x] [Review][Patch] **M1 (MED): `_init_throwaway_repo` doesn't neutralize `commit.gpgsign`.** If a developer machine has `commit.gpgsign=true` globally (common on security-conscious setups), the test's throwaway commit aborts because the synthetic `user.email=story-26-test@example.com` has no matching signing key. Cheap defense: `git -c commit.gpgsign=false commit ...` inline. [tests/unit/test_provenance.py:55-59]

**Deferred (real but owned elsewhere):**

- [x] [Review][Defer] **L1 (LOW): `get_commit_short` treats untracked files as `-dirty`.** `git status --porcelain` includes untracked files by default. After a typical `bmad-dev-story` run that leaves story/planning artifacts in the working tree (or any local-only dev-tooling files outside `.gitignore`), the commit string flips to `-dirty` even though no tracked file was modified. Architecture's "dirty flag if working tree modified" is ambiguous on untracked. Right time to decide is Story 2.8, when the CLI surface starts emitting the commit string in user-visible places — either filter via `--untracked-files=no` or accept current behavior with a docs note. Recorded in deferred-work.md. [src/steeproute/provenance.py:48-54]

**Dismissed (noise / false positive / handled elsewhere):**

- [x] [Review][Dismiss] **`compute_pipeline_content_hash` uses concat-then-hash with no per-file boundary marker** — theoretical hash-collision via file-splitting. Not realistic given the fixed two-pattern glob (`pipeline/**/*.py` + `models.py`) where filename + path-position fully determine identity. Adding a separator byte (e.g. `\0`) is defensive but unnecessary for a hobby project's threat model.
- [x] [Review][Dismiss] **`_canonicalize_area` returns `dict[str, object]` but `center` is a `list[float]`** — `object` is the permissive supertype, basedpyright accepts it. Tightening to `dict[str, list[float] | str | float]` is cosmetic and reduces readability.
- [x] [Review][Dismiss] **`round()` uses banker's rounding (round-half-to-even)** — deterministic within a Python version; only matters at the exact half-mark on the 7th decimal, which a real CLI input won't hit. No behavior change worth a test.
- [x] [Review][Dismiss] **Two `subprocess.run` calls per `get_commit_short` (~100-200ms on Windows)** — called once per cache write or report render. Not a hot path. Caching the result across a single CLI invocation is the right optimization if it ever matters; not today.
- [x] [Review][Dismiss] **`_get_commit_short_at` catches only `CalledProcessError` + `FileNotFoundError`, not `PermissionError` / generic `OSError`** — exotic cases (git binary present but not executable; process-table exhaustion). Catch-only-common-cases lets truly unexpected errors propagate to `run_entry_point` for visibility instead of silently returning `"unknown"`.
- [x] [Review][Dismiss] **`Manifest` slots-enforcement not separately tested** — `frozen=True` already blocks attribute reassignment via the dataclass machinery; the existing `FrozenInstanceError` test covers the user-facing contract. A `slots=True`-specific test (assert `AttributeError` on adding a new attribute) would be testing CPython's dataclass implementation, not our code.

## Dev Notes

- **Where the manifest dataclass lives.** Architecture says "in `cache.py` (or `models.py`)". Story 2.6 places it in `cache.py` to co-locate with the JSON serialization it will drive in Story 2.7 — Architecture §Boundaries scopes `cache.py` as the sole reader/writer of cache files, and the wire schema is a cache-internal concern. `models.py` stays focused on cross-cutting data shapes (`Area`, `PipelineConfig`, the Epic 3 query-side dataclasses).
- **Canonical JSON for cache keys.** `json.dumps(..., sort_keys=True, separators=(",", ":"))` is the Python-stdlib canonical-form recipe. Tuples must be normalized to lists before encoding (`Area.center` is a `tuple[float, float]`; `json.dumps` handles this implicitly but the explicit cast in code makes the canonicalization step visible).
- **Why we sort hashed-pipeline files by `as_posix()` relative path.** Windows produces backslashes from `Path.glob`; sorting raw strings would yield a different order than POSIX, and the hash would silently differ between platforms. Relative-to-package-root + `.as_posix()` is the determinism contract.
- **Why `dem_version` is a required `str` parameter, not derived in this story.** Architecture §Cat 4b lists "`--dem-version` or derived from DEM file metadata". The derivation logic (read GeoTIFF tags, fall back to user override) is a CLI-side concern wired in Story 2.8. Story 2.6's `compute_cache_key` accepts whatever the caller supplies — type-checked as `str`, no defaulting.
- **Why `get_commit_short` returns a sentinel rather than raising.** This helper is called at every `write_entry` for the provenance record (Story 2.7) and at every report render (Epic 3 §FR19). A hard raise on "not a git repo" would make the codebase unusable when installed as a wheel without source — a degraded `"unknown"` provenance line is the right trade. Mirrors `errors.py`'s "user-actionable signal vs. crash" philosophy.
- **What we are not doing this story:**
   - **`write_json_atomic` + `write_entry` + `read_entry` + `rebuild_index`** — Story 2.7.
   - **CLI wiring of `--cache-dir` / `--dem-version` / `--force-refresh`** — Story 2.8 (the click decorators are already defined in Epic 1's `cli/_shared.py`).
   - **`check_coverage` (FR24 coverage lookup)** — Story 2.10.
   - **OSM-extract-date population + age warning** — Story 2.9 owns the warning; the manifest field is just a string here.
   - **`steeproute_version` resolution.** The manifest carries it as a field but Story 2.6 has no caller — Story 2.7 will pass `importlib.metadata.version("steeproute")` (or the dynamic-versioning equivalent) into the constructor. No helper needed in `provenance.py` for it.

### Project Structure Notes

- **Extended**: `src/steeproute/cache.py` — replaces the one-line placeholder with `compute_cache_key`, `compute_pipeline_content_hash`, `Manifest` dataclass + `to_dict`, and the supporting module-scope constants.
- **Extended**: `src/steeproute/provenance.py` — replaces the one-line placeholder with `get_commit_short`, `_get_commit_short_at`, `iso8601_utc_now`, supporting constants.
- **New**: `tests/unit/test_cache_key.py`.
- **New**: `tests/unit/test_provenance.py`.
- **Untouched**: `src/steeproute/cli/setup.py` (Story 2.8); `src/steeproute/cli/_shared.py` (decorators already exist); `src/steeproute/models.py` (no schema changes — `Area` already canonical-ready).

### Testing standards summary

- Layer: pure-logic unit tests under `tests/unit/`. Real-git fixture for the dirty-flag test (Architecture §Cat 11e: "real git behavior preferred over mocking `subprocess`").
- Coverage floor: 95% on `cache.py` and `provenance.py` (pure-logic modules per Architecture §Cat 11e).
- Naming: `test_<unit>_<scenario>` — e.g. `test_compute_cache_key_canonicalizes_area_to_six_decimals`, `test_get_commit_short_appends_dirty_when_working_tree_modified`.
- No new pytest markers needed; the real-git fixture tests stay in the default offline test set (git is a hard dependency of the dev environment per Architecture §Technical Constraints).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 2.6"] — AC anchor and field list
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 4 — Cache architecture] — cache-key composition (§4b), manifest schema (§4c–§4d), atomic-write rule
- [Source: _bmad-output/planning-artifacts/architecture.md §Implementation Patterns — Serialization conventions] — snake_case JSON, ISO 8601 UTC with `Z`, single `write_json_atomic` helper rule
- [Source: _bmad-output/planning-artifacts/architecture.md §Implementation Patterns — Numerical and data discipline] — explicit-tolerance float comparisons, named constants at module scope
- [Source: _bmad-output/planning-artifacts/architecture.md §Implementation Patterns — Type hints and data] — frozen-slots dataclass discipline
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11e — Testing strategy] — 95% pure-logic coverage floor for `cache.py`
- [Source: src/steeproute/models.py] — `Area` dataclass shape (canonicalize against this)
- [Source: src/steeproute/cli/_shared.py] — `--cache-dir`, `--dem-version`, `--osm-age-warn-days`, `--force-refresh` decorators are already defined (Story 2.8 will wire them; no shape changes here)
- [Source: src/steeproute/pipeline/__init__.py] — files that contribute to `compute_pipeline_content_hash` are stable now that the orchestrator has landed

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` for the corporate Netskope TLS-intercepting proxy.

**No new deps.** All four helpers use stdlib only (`hashlib`, `json`, `pathlib`, `subprocess`, `datetime`, `dataclasses`).

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 40 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 293 passed, 1 deselected in ~22s; coverage 97% overall
                                     - cache.py      100% (44 stmts, 0 missed)
                                     - provenance.py 100% (17 stmts, 0 missed)
                                     (other modules unchanged from Story 2.5)
```

Live OSM test re-verified: `STEEPROUTE_USE_OS_TRUSTSTORE=1 UV_NATIVE_TLS=1 uv run pytest -m live` → 1 passed.

**One mid-implementation fix:** the first canonicalization test used drift values `(45.0716007, 6.1079003)` — but 7th-decimal-digit `7` rounds *up* at 6 decimals, so the drifted area canonicalized to a different value than the base. Changed drift to `(45.0716001, 6.1079001)` so position-7 digit `1` rounds down and the absorption holds. The bug was in the test data, not the production helper.

### Completion Notes List

**Design decisions worth review attention:**

1. **`Manifest` lives in `cache.py`, not `models.py`.** Architecture §Cat 4 allows either; I picked `cache.py` so the dataclass sits next to the JSON wire shape it drives via `to_dict()`. Per §Boundaries — Cache boundary, `cache.py` is the sole reader/writer of cache files, so the wire schema is a cache-internal concern. `models.py` stays focused on cross-cutting data shapes (`Area`, `PipelineConfig`, the Epic 3 query-side dataclasses).

2. **`Manifest.schema_version` is declared last in the dataclass.** Python dataclasses require fields with defaults to come after fields without; `schema_version: int = 1` is the only field with a default, so it goes at the bottom of the declaration. The JSON wire shape from `to_dict()` still emits `schema_version` *first* (matching Architecture §Cat 4's example), driven by the explicit dict literal order.

3. **`_PIPELINE_CONTENT_GLOBS = ("pipeline/**/*.py", "models.py")` is module-scope.** Architecture §Cat 4b lists exactly these files. `cache.py` and `provenance.py` are deliberately excluded — they touch *how* graphs are persisted, not what graphs contain. If pipeline source changes effectively orphan all entries, but a cache-helper refactor must not (the on-disk graph bytes are unchanged).

4. **Pipeline-content-hash test patches `cache_mod.__file__` rather than editing real source.** `monkeypatch.setattr(cache_mod, "__file__", str(fake_pkg / "cache.py"))` redirects the package-root resolution at test time. The alternative — editing `pipeline/osm.py`, observing the hash, then reverting — would leave a dirty tree across the rest of the suite and race with parallel test runs. The patched test stays hermetic.

5. **`GIT_CEILING_DIRECTORIES` in the "non-git directory" test.** If pytest's `tmp_path` happened to be created inside a parent git repo (atypical on Windows/CI but conceivable), `git rev-parse` would walk up and succeed — falsifying the test. Setting `GIT_CEILING_DIRECTORIES=tmp_path.parent` via `monkeypatch.setenv` stops git's repo-discovery walk at that boundary. Production code is unaffected; the env var only changes the subprocess's view, not the helper's logic.

6. **`get_commit_short` returns `"unknown"` rather than raising.** Called at every cache write (Story 2.7) and every report render (Epic 3 §FR19). A hard raise on "not a git repo" would make the codebase unusable when installed as a wheel without source — a degraded `"unknown"` provenance line is the right trade. Mirrors the "user-actionable signal vs. crash" philosophy in `errors.py`.

7. **`core.fileMode=false` passed inline to `git status`.** On Windows checkouts of code that originated on POSIX, the execute bit on tracked files can flip after a `git checkout` and silently mark the tree dirty. Inline `-c core.fileMode=false` neutralizes this without requiring a per-machine global config tweak.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `cache.py::compute_cache_key` returns 16-hex SHA256 truncation over canonical JSON; area rounded to 6/3 decimals before hashing. Verified by `test_compute_cache_key_returns_16_char_lowercase_hex`, `_is_deterministic_on_unchanged_inputs`, `_canonicalizes_area_drift_below_canonical_precision`, `_changes_when_area_moves_beyond_canonical_precision`. ✅
2. AC #2 — `cache.py::compute_pipeline_content_hash` returns full 64-hex SHA256 over `pipeline/**/*.py` + `models.py`, files sorted by POSIX relpath. Verified by `_returns_64_char_lowercase_hex`, `_is_deterministic_on_unchanged_tree`, `_changes_when_a_pipeline_file_changes`. ✅
3. AC #3 — `Manifest` is a frozen-slots dataclass with `schema_version: int = 1` + all required fields + `to_dict()` emitting the `{"mode": "center_radius", "center": [...], "radius_km": ...}` nested area shape. Verified by `test_manifest_to_dict_emits_full_schema_with_nested_area_shape`, `test_manifest_is_frozen`. ✅
4. AC #4 — `provenance.get_commit_short` returns `<hash>` or `<hash>-dirty`, falls back to `"unknown"` on `CalledProcessError`/`FileNotFoundError`, never raises. Subprocess uses `check=True, capture_output=True, text=True, cwd=...`, no `shell=True`. Verified by the four `_get_commit_short_at` tests + the real-repo smoke. ✅
5. AC #5 — `provenance.iso8601_utc_now()` returns `YYYY-MM-DDTHH:MM:SSZ`. Verified by `_matches_z_suffixed_second_precision_and_round_trips`. ✅
6. AC #6 — 4 bullets satisfied: canonicalization, three-input-sensitivity sweep, content-hash determinism + change-detection, output shape. ✅
7. AC #7 — 3 bullets satisfied: dirty-flag flips on real tracked-file modification (no subprocess mocking), `unknown` sentinel on non-git directory, iso8601 format + round-trip. Private `_get_commit_short_at(cwd)` introduced as planned. ✅
8. AC #8 — `Manifest.to_dict()` round-trip-shape test lives in `test_cache_key.py` per the spec's "or a sibling test file — caller's choice". ✅
9. AC #9 — `test_get_commit_short_real_repo_matches_clean_or_dirty_hash_pattern` smoke-tests the production helper against the actual repo. ✅
10. AC #10 — All four CI gates green; coverage floors held (100% on both new modules); live OSM re-verified. ✅

### File List

**New:**
- `tests/unit/test_cache_key.py` — 12 tests (compute_cache_key shape + determinism + canonicalization + sensitivity sweep, compute_pipeline_content_hash shape + determinism + change-detection via `__file__` patch, Manifest to_dict + frozen-dataclass).
- `tests/unit/test_provenance.py` — 5 tests (`_get_commit_short_at` clean/dirty/unknown, real-repo smoke, iso8601 round-trip). Uses real `subprocess.run(["git", ...])` against tmp git trees — no mocking.

**Modified:**
- `src/steeproute/cache.py` — replaced one-line placeholder with `compute_cache_key`, `_canonicalize_area`, `compute_pipeline_content_hash`, `Manifest` + `to_dict`, and the supporting module-scope constants.
- `src/steeproute/provenance.py` — replaced one-line placeholder with `get_commit_short`, `_get_commit_short_at`, `iso8601_utc_now`, supporting `_PACKAGE_ROOT` + `_UNKNOWN_COMMIT_SENTINEL` constants.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.6 `backlog → ready-for-dev → in-progress → review`; dated comments added.

**Untouched (intentionally):**
- `src/steeproute/models.py` — `Area` already has the canonical shape the cache key consumes; no changes needed.
- `src/steeproute/cli/_shared.py` — `--cache-dir`, `--dem-version`, `--osm-age-warn-days`, `--force-refresh` decorators were already defined in Epic 1; CLI wiring lands in Story 2.8.
- `src/steeproute/cli/setup.py` — `--center → compute_cache_key → write_entry` wiring lands in Story 2.8.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-20 | Yann (Claude Opus 4.7) | Lightweight review applied: 1 patch landed (M1 MED — `_init_throwaway_repo` in `tests/unit/test_provenance.py` now passes `git -c commit.gpgsign=false commit ...` inline so developer machines with `commit.gpgsign=true` globally don't crash the test's throwaway commit when the synthetic email has no signing key). 1 item deferred to Story 2.8 (L1 — `get_commit_short` treats untracked files as `-dirty`; right time to decide filter vs. accept is when the CLI surface starts emitting the commit string in user-visible places; recorded in deferred-work.md). 6 dismissed inline in Review Findings (concat-then-hash without boundary marker; cosmetic dict-type-tightening; banker's rounding edge case; subprocess perf; narrow exception catch list; slots-enforcement testing). All four CI gates green post-review: ruff, ruff format, basedpyright 0/0/0, pytest 293 passed (unchanged — patch is a test-side fixture tweak, no count change) at 97% overall coverage; `cache.py` 100%, `provenance.py` 100%. | _pending_ |
| 2026-05-20 | Yann (Claude Opus 4.7) | Story 2.6 implemented: cache key hashing + manifest schema + provenance helpers. `cache.py::compute_cache_key(area, untagged_policy, dem_version, pipeline_content_hash) -> str` (16-hex SHA256 over canonical JSON; area canonicalized to 6-decimal lat/lon + 3-decimal radius_km before hashing). `cache.py::compute_pipeline_content_hash() -> str` (64-hex SHA256 over `pipeline/**/*.py` + `models.py`, sorted by POSIX relpath so the hash is platform-stable across Windows/POSIX). `cache.py::Manifest` (`@dataclass(frozen=True, slots=True)`, `schema_version: int = 1`, full Architecture §Cat 4 field set) + `to_dict()` emitting the nested-area wire shape. `provenance.py::get_commit_short()` (real-git subprocess; `-dirty` suffix on non-empty `git status --porcelain`; `"unknown"` sentinel on `CalledProcessError`/`FileNotFoundError`, never raises; `core.fileMode=false` inline guard against Windows execute-bit drift) + private `_get_commit_short_at(cwd)` for testability. `provenance.py::iso8601_utc_now()` (UTC second-precision Z-suffix). 17 new tests: `tests/unit/test_cache_key.py` (12 — shape, determinism, canonicalization absorption at 7th-decimal lat/lon + 4th-decimal radius, three-input sensitivity sweep, content-hash change-detection via `cache_mod.__file__` monkeypatch onto a fake-package tmp_path, Manifest to_dict + frozen enforcement) + `tests/unit/test_provenance.py` (5 — real tmp-git fixture for clean/dirty branches with `subprocess.run(["git", ...])` no mocking, `GIT_CEILING_DIRECTORIES` for the unknown-sentinel test, real-repo smoke, iso8601 round-trip). No new runtime/dev deps (stdlib `hashlib`/`subprocess`/`json`/`datetime`/`pathlib`). All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 293 passed (+17 from prior 276) at 97% overall coverage; `cache.py` 100% (44 stmts), `provenance.py` 100% (17 stmts) — both well above the 95% pure-logic floor. Live OSM test re-verified — no regression. | _pending_ |
