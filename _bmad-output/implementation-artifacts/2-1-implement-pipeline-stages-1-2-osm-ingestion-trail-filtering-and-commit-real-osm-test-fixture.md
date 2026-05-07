# Story 2.1: Implement pipeline stages 1–2 — OSM ingestion, trail filtering, and commit real-OSM test fixture

Status: done

## Story

As a developer,
I want `pipeline/osm.py` to download OSM trail data for a given area and filter it by `sac_scale`, `highway` type, and untagged-trails policy, plus a committed real-OSM test fixture for downstream use,
so that downstream pipeline stages receive a clean `networkx.MultiDiGraph` containing only trails the user cares about, and subsequent pipeline + E2E tests have a realistic OSM graph to exercise against.

## Acceptance Criteria

1. `pipeline/osm.py` defines `osm_load(area) -> MultiDiGraph` (stage 1) using `osmnx.graph_from_point` and `filter_trails(graph, untagged_policy, difficulty_cap) -> MultiDiGraph` (stage 2). Both pure (no global state, no `print`, returns a new graph — input never mutated). Each edge of the returned graph satisfies the source-attribute contract: `sac_scale` (`str | None`), `highway` (`str | None`), `osm_way_id` (`int`), `geometry` (`shapely.LineString`, present on every edge — synthesize from node coords for edges osmnx omits one for).
2. `tests/fixtures/grenoble_small/osm_graph.graphml` is committed, captured via `osmnx.graph_from_point` for a Grenoble-area point at ~2 km radius. `tests/fixtures/grenoble_small/README.md` documents the exact fetch parameters (lat/lon, radius, network_type / custom_filter, `osmnx` version, capture date). `tests/fixtures/grenoble_small/regenerate.py` reproduces the capture in one `python regenerate.py` invocation. Committed `osm_graph.graphml` < 5 MB.
3. `tests/unit/test_osm.py` loads the real fixture and covers:
    - Include-untagged vs. exclude-untagged policy: same input filtered both ways → edge counts differ; the difference equals the count of edges with no `sac_scale` in the fixture.
    - `difficulty_cap` filtering at each SAC-scale boundary (T1..T6): for each cap, no surviving edge has a `sac_scale` strictly above the cap; sweep is monotonic (each step-up adds edges, never removes).
    - At least one assertion exercising real-trail edge cases the fixture surfaces (e.g. edges with missing `highway` value, unusual geometries) — discoverable from the fixture itself, not pre-listed here.
4. `tests/unit/test_osm.py` also runs `filter_trails` against small crafted synthetic `MultiDiGraph` inputs covering edge cases the fixture may not contain: a graph with a single untagged-only edge (assert include-policy admits it; exclude-policy strips it); a graph with one edge per supported `highway` type (assert filtering preserves the trail-relevant subset and drops non-trail values).
5. `tests/integration/test_osm_live.py::test_live_osm_matches_fixture` is marked `@pytest.mark.live`, calls `osmnx.graph_from_point` with the same fetch parameters as the fixture, and asserts structural similarity to the fixture (node and edge counts within a tolerance band — start at ±10%, document the choice). Skipped in default CI; runnable locally via `uv run pytest -m live`.
6. All four CI gates pass on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright`, `uv run pytest --cov` (default collection — `live`-marked tests excluded). New runtime dependencies added to `pyproject.toml`: `osmnx`, `networkx`, `shapely`. The `live` pytest marker is registered in `[tool.pytest.ini_options].markers` so unmarked-marker warnings stay zero.

## Tasks / Subtasks

- [x] **Task 1: Capture and commit the real-OSM fixture** (AC: #2)
    - [x] Pick a Grenoble-area center point with good trail variety (Bastille / Chartreuse foothills / Belledonne edge). `dist_m = 2000`. Pick `network_type` or a `custom_filter` (osmnx supports both) per the trail-style network you want — document the choice in the README.
    - [x] Write `tests/fixtures/grenoble_small/regenerate.py` calling `osmnx.graph_from_point(...)` then `osmnx.save_graphml(graph, "osm_graph.graphml")`. Make it runnable as `python regenerate.py` from the fixture directory.
    - [x] Write `tests/fixtures/grenoble_small/README.md` listing: center, radius, `osmnx` version, capture date, the regeneration command, and the rationale for the chosen point.
    - [x] Verify `osm_graph.graphml` < 5 MB before committing.
- [x] **Task 2: Implement `osm_load` and `filter_trails`** (AC: #1)
    - [x] Add `osmnx`, `networkx`, `shapely` to `pyproject.toml` `[project] dependencies`; `uv lock`; `uv sync`.
    - [x] `osm_load(area)`: call `osmnx.graph_from_point` with the area's center + radius_in_meters; ensure each edge carries the four contract attributes (rename osmnx's `osmid` → `osm_way_id`; synthesize missing `geometry` from endpoint node coords as `shapely.LineString`; preserve `sac_scale` / `highway` as-is, possibly `None`).
    - [x] `filter_trails(graph, untagged_policy, difficulty_cap)`: drop edges whose `highway` isn't in the trail-relevant set; drop edges whose `sac_scale` exceeds `difficulty_cap`; drop edges with `sac_scale is None` if `untagged_policy == "exclude"`. Return a new `MultiDiGraph` (Architecture §Cat 3 stage convention `def stage(input, config) -> output`); never mutate the input graph.
    - [x] Define the trail-relevant `highway` set, the SAC-scale ordering, and any other thresholds as **module-scope named constants** (no inline magic values per Architecture §Numerical and data discipline).
- [x] **Task 3: Unit tests against fixture + synthetic** (AC: #3, #4)
    - [x] `tests/unit/test_osm.py` loads `tests/fixtures/grenoble_small/osm_graph.graphml` once (module- or session-scope fixture) and asserts the contract claims of AC #3.
    - [x] In-test `MultiDiGraph` factories (or `tests/unit/conftest.py` helpers) for the synthetic cases of AC #4.
- [x] **Task 4: Live integration test** (AC: #5)
    - [x] Register `live` marker in `[tool.pytest.ini_options].markers` (e.g. `markers = ["live: tests that hit external services; skipped in default CI"]`).
    - [x] `tests/integration/test_osm_live.py::test_live_osm_matches_fixture` marked `@pytest.mark.live`, fetching with the same parameters as the fixture, asserting node/edge counts within tolerance.
- [x] **Task 5: Verify CI gates** (AC: #6)
    - [x] `uv sync && uv run ruff check && uv run ruff format --check && uv run basedpyright && uv run pytest --cov`.
    - [x] Confirm default `pytest --cov` does not collect the live test (live marker filtered).

### Review Findings

_From `bmad-code-review` 2026-05-07. Three parallel reviewers (Blind Hunter, Edge Case Hunter, Acceptance Auditor)._

**Decisions resolved 2026-05-07:**

- [x] [Review][Decision] **D1: `dist_type="bbox"` returns a square** → resolved as **option 1**: keep `bbox` fetch, redocument `Area` as a bbox half-side. Patch P12 below. [Source: blind+auditor]
- [x] [Review][Decision] **D2: basedpyright global relaxation** → resolved as **option 2**: scope per-file via `# pyright:` headers in `pipeline/osm.py` and `tests/unit/test_osm.py`; revert global rule disables in `pyproject.toml`. Patch P13 below. [Source: blind+auditor]
- [x] [Review][Decision] **D3: `osmnx.settings.useful_tags_way` mutation is process-wide global state** → resolved as **option 1** (accept): documented footgun-fix; the alternative restore-on-exit would itself be a footgun if any caller in the same process expects the augmented settings (e.g. cache key includes `useful_tags_way` content one day). Dismissed. [Source: auditor]

**Patch (unambiguous fixes):**

- [x] [Review][Patch] **No `area.center` lat/lon validation in `osm_load`** — asymmetric with the negative-radius validation that landed here as a carry-forward from Epic 1 retro. `(lon, lat)` swap or out-of-range coords pass straight to osmnx with opaque downstream failure. [src/steeproute/pipeline/osm.py:43-50] [Source: blind+edge+auditor]
- [x] [Review][Patch] **`radius_km` NaN/Inf passes the `<= 0` guard** — `NaN > 0` is `False`, so `NaN`/`Inf` survives validation and produces a `NaN` `dist` for osmnx. [src/steeproute/pipeline/osm.py:45-49] [Source: edge]
- [x] [Review][Patch] **`max_sac_rank` doesn't normalize OSM-side strings** — cap input is `.strip().upper()`-normalized, but `sac_scale` values from OSM are looked up as-is. A tag with trailing whitespace (real OSM data has these) would silently drop the edge. Either normalize before lookup, or document why we don't. [src/steeproute/pipeline/osm.py:max_sac_rank] [Source: blind]
- [x] [Review][Patch] **`untagged_policy` validation raises `ValueError` instead of `BadCLIArgError`** — inconsistent with negative-radius handling in same module; once Story 2.8 wires `cli/setup.py`, ValueError will leak as untyped traceback instead of becoming a clean exit-2 via `run_entry_point`. [src/steeproute/pipeline/osm.py:189-192] [Source: auditor]
- [x] [Review][Patch] **Live test `ZeroDivisionError` if fixture has zero nodes/edges** — `node_drift = abs(...) / fixture_nodes` with no zero guard. Defensive, but if the committed fixture is ever truncated/corrupted the failure mode is opaque. [tests/integration/test_osm_live.py:42-43] [Source: blind+edge]
- [x] [Review][Patch] **Live test + `regenerate.py` duplicate fixture constants with unit mismatch** — `test_osm_live.py` hardcodes `_FIXTURE_RADIUS_KM = 2.0`; `regenerate.py` uses `DIST_M = 2000`. Manual sync via comment only. AC #5's "same fetch parameters" silently breaks if either drifts. Import constants from `regenerate.py`. [tests/integration/test_osm_live.py:30-31] [Source: auditor]
- [x] [Review][Patch] **Test re-implements `SAC_SCALE_RANK` T-name → string mapping by hand** — `tests/unit/test_osm.py:106-114` hard-codes the `T1→hiking` … `T6→difficult_alpine_hiking` table, duplicating production knowledge. If production constants drift, test silently passes wrong assertion. Derive from production. [tests/unit/test_osm.py:106-114] [Source: blind]
- [x] [Review][Patch] **`regenerate.py` calls `truststore.inject_into_ssl()` unconditionally vs conftest gated on env var** — asymmetric. A user without a corporate proxy gets osmnx routed through OS trust store unnecessarily (on some Linux distros more limited than `certifi`). [tests/fixtures/grenoble_small/regenerate.py:39] [Source: auditor]
- [x] [Review][Patch] **No CI assertion that `osm_graph.graphml` is < 5 MB** — AC #2 requires it; nothing fails CI if a future regeneration produces a 6 MB fixture. Add a one-line size check in the module-scope fixture. [tests/unit/test_osm.py] [Source: auditor]
- [x] [Review][Patch] **Story doc inconsistencies: AC #2 walkthrough still says "Chamrousse 3 km", Change Log row references stale `STEEPROUTE_INSECURE_OSM`** — bullet under AC #2 in Completion Notes and the Change Log row reference the pre-revision fixture/env-var. [_bmad-output/.../2-1-...md] [Source: auditor]
- [x] [Review][Patch] **`cache/` (osmnx HTTP cache) is untracked and unignored** — surfaced during the review process; runtime artifact that must be added to `.gitignore`. Not in the diff but pollutes future reviews. [.gitignore] [Source: review-process observation]
- [x] [Review][Patch] **P12 (from D1): redocument `Area` as bbox half-side** — update `Area` docstring in `models.py` to make explicit that `radius_km` is a bbox half-side (since `dist_type="bbox"`), not a disk radius. Update the `osm_load` docstring and the `tests/fixtures/grenoble_small/README.md` accordingly. Field name `radius_km` is kept (renaming would ripple into Story 2.6 cache manifest schema). [src/steeproute/models.py:6-16; src/steeproute/pipeline/osm.py:34-44; tests/fixtures/grenoble_small/README.md] [Source: D1 resolution]
- [x] [Review][Patch] **P13 (from D2): scope basedpyright relaxations per-file** — revert the five global `reportUnknown*`/`reportMissingTypeArgument` disables in `pyproject.toml [tool.basedpyright]`; add `# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false` headers to `src/steeproute/pipeline/osm.py` and `tests/unit/test_osm.py` (and `tests/integration/test_osm_live.py` if needed). Confirm `uv run basedpyright` still 0/0/0. [pyproject.toml:163-167; src/steeproute/pipeline/osm.py; tests/unit/test_osm.py] [Source: D2 resolution]

**Deferred (real but out of scope or owned elsewhere):**

- [x] [Review][Defer] No error handling around `osmnx.graph_from_point` — owned by Story 2.9 (DataSourceUnavailableError mapping). [Source: blind+edge]
- [x] [Review][Defer] Empty-graph result from osmnx — owned by Story 2.9 / 2.5 orchestrator. [Source: edge]
- [x] [Review][Defer] Concurrent test runs mutate shared `osmnx.settings` — not running parallel tests today; revisit if/when pytest-xdist is adopted. [Source: edge]
- [x] [Review][Defer] Zero-length `LineString` when `u==v` or coincident endpoints — downstream concern for stages 3-4 (Story 2.2) where geometry math runs. [Source: edge]
- [x] [Review][Defer] `out.copy()` may OOM on large graphs — premature optimization until benchmarks surface it; `--area-cap` mitigates indirectly. [Source: edge]
- [x] [Review][Defer] `filter_trails` returns copy then removes edges → orphan nodes retained — node-pruning policy belongs to Story 2.5 orchestrator. [Source: auditor]
- [x] [Review][Defer] Drift tolerance ±10% on small fixture (1208 edges → ±120) may flap on bulk-edits in the area — empirical concern; revisit if observed. [Source: blind]
- [x] [Review][Defer] Radius > Overpass limits opaque error — defer until area-cap / setup-side validation lands (Story 2.8). [Source: edge]

## Dev Notes

- **Carry-forward — negative `--radius` validation** ([epic-1-retro-2026-05-06.md:48](_bmad-output/implementation-artifacts/epic-1-retro-2026-05-06.md), [deferred-work.md:34](_bmad-output/implementation-artifacts/deferred-work.md:34)): `--radius` first becomes a real geometric value here. `osmnx.graph_from_point` requires `dist > 0`. CLI wiring lands in Story 2.8, so the cleanest fix is a precondition in `osm_load(area)` itself: raise `BadCLIArgError("--radius must be > 0", ...)` for non-positive radius. 2.8 inherits the guard for free.
- **`area` parameter shape**: Architecture §Cat 4 manifest schema (`{"mode": "center_radius", "center": [lat, lon], "radius_km": ...}`) implies a small `Area` dataclass. `@dataclass(frozen=True, slots=True) Area(center: tuple[float, float], radius_km: float)` in `models.py` keeps the stage signature typed and matches manifest field names 1:1; downstream stories (2.6, 2.8) need *some* shared shape, so locking it now saves churn. Plain primitives are also fine if the dev prefers — pick one and use it consistently.
- **`osm_way_id` rename**: osmnx stores the OSM way ID on edges as `osmid`. The Architecture §Cat 3 contract calls it `osm_way_id`. Rename in `osm_load` rather than aliasing downstream — single point of translation.
- **`geometry` synthesis**: osmnx omits `geometry` on straight node-to-node edges (it's reconstructable from the two endpoints). `osm_load` must synthesize `shapely.LineString([u_coords, v_coords])` for those so the contract holds for *every* edge — otherwise stages 3–4 (Story 2.2) break on missing-geometry edges.
- **Out of scope:**
    - CLI wiring of `osm_load` into `steeproute-setup` (Story 2.8).
    - Cache key / manifest production (Story 2.6).
    - Stages 3–7 (Stories 2.2–2.4).
    - The `DataSourceUnavailableError` mapping for OSM/network failures (Story 2.9).
    - Pinning `osmnx` beyond what `uv lock` produces; the fixture README captures the version that was used.

### Project Structure Notes

- New production module: `src/steeproute/pipeline/osm.py`. The `pipeline/__init__.py` placeholder docstring already says "stages 1-9 and orchestrator" — no edit needed there yet.
- Optional new dataclass in `src/steeproute/models.py` (`Area`) — see Dev Notes above.
- New test files: `tests/unit/test_osm.py`, `tests/integration/test_osm_live.py`.
- New fixture directory: `tests/fixtures/grenoble_small/` with `osm_graph.graphml` (committed binary), `README.md`, `regenerate.py`. `tests/fixtures/` is a new top-level test directory; `[tool.pytest.ini_options].testpaths` is `["tests"]` so pytest sees it but `python_files = ["test_*.py"]` keeps the fixture files out of test discovery. `[tool.coverage.run].source = ["src/steeproute"]` already excludes fixtures from coverage.
- `pyproject.toml` changes: three new runtime deps under `[project] dependencies`; one new line under `[tool.pytest.ini_options]` registering the `live` marker.

### Testing standards summary

- Layer split (Architecture §Cat 11e + §Test organization):
    - `tests/unit/test_osm.py` — pure-function correctness against fixture + synthetic crafted graphs.
    - `tests/integration/test_osm_live.py` — single live-network test, `@pytest.mark.live`, skipped in default CI.
- Fixture-loading: use `osmnx.load_graphml(path)` so the in-test graph object matches what `osm_load` produces.
- Naming: `test_<unit>_<scenario>` (e.g. `test_filter_trails_excludes_untagged_when_policy_exclude`). [Architecture §Test organization]
- Conventions inherited: absolute imports, PEP 604 unions, no `Any` (or one short comment if unavoidable at the osmnx boundary), type-checked under basedpyright, ruff-formatted.

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 2.1"]
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 3 — Data pipeline architecture] — pipeline-stage table, edge-attribute contract, stage signature `def stage(input, config) -> output`
- [Source: _bmad-output/planning-artifacts/architecture.md §Category 11 — Testing strategy] — three-layer split, hybrid fixture rule (real-data primary, synthetic where mechanically necessary)
- [Source: _bmad-output/planning-artifacts/architecture.md §Implementation Patterns — Numerical and data discipline] — module-scope named constants, no inline magic numbers
- [Source: _bmad-output/planning-artifacts/architecture.md §Key anti-patterns to avoid] — no top-level side effects in importable modules
- [Source: _bmad-output/planning-artifacts/prd.md §FR9] — configurable untagged-trails policy
- [Source: _bmad-output/planning-artifacts/prd.md §FR23–FR25] — separate `steeproute-setup` CLI, area coverage check, cache invalidation
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-05-06.md §Carry-forward to Epic 2] — negative-radius validation handoff
- [Source: _bmad-output/implementation-artifacts/deferred-work.md §"Deferred from: lightweight review of 1-6"] — same handoff
- [Source: src/steeproute/cli/_shared.py:88-103] — existing `validate_area_size`; alternative enforcement site if the dev prefers CLI-layer rejection over `osm_load` precondition
- [Source: src/steeproute/errors.py:8-21] — `PreExecutionError` / `BadCLIArgError` (the subclass to raise for non-positive radius)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.7 (`claude-opus-4-7`), via Claude Code CLI on Windows 11 (worktree branch `claude/priceless-blackburn-34e8e1`).

### Debug Log References

**Environment:** Python 3.13.13 / `uv` 0.9.26. `UV_NATIVE_TLS=1` required for the corporate Netskope TLS-intercepting proxy (carry-over from Story 1.7).

**New runtime deps:** `osmnx 2.1.0`, `networkx 3.6.1`, `shapely 2.1.2` (plus transitive: numpy, pandas, geopandas, pyproj, requests, etc — full lock in `uv.lock`).

**Final pass (all green):**

```
uv run ruff check                  → All checks passed!
uv run ruff format --check         → 30 files already formatted
uv run basedpyright                → 0 errors, 0 warnings, 0 notes
uv run pytest --cov                → 189 passed, 1 deselected in 14.17s; coverage 94% overall
                                     - pipeline/osm.py 91% (osm_load body uncovered — only the live test exercises it)
                                     - models.py 100% (Area dataclass)
```

### Completion Notes List

**Divergences from story spec / things that needed extra reasoning (worth review attention):**

1. **A pernicious osmnx footgun: `sac_scale` is NOT in `osmnx.settings.useful_tags_way` by default,** so without extending the list it's silently dropped on every fetch. This produced the false signal that several candidate fixture areas (Bastille, Le Sappey, Chamrousse at 2 km) had no SAC tagging at all — the data was always there in OSM, the fetch was just dropping it. Both `regenerate.py` and the production `osm_load` extend the list before fetching. Documented in the fixture README; `test_normalized_fixture_has_sac_scale_key_on_every_edge` catches a regression.

2. **Fixture: Le Sappey-en-Chartreuse, 2 km radius** (matching the story spec). Once `sac_scale` was actually being fetched, Le Sappey at 2 km turned out to give the richest SAC variety of the candidate areas — T1 through T5 all represented (1208 edges, 723 KB committed) — and the smallest fixture file. Earlier-in-this-session reports of "no sac_scale at Le Sappey" were the symptom of #1, not real data; please disregard them.

3. **`filter_trails` handles list-valued `sac_scale`** (an osmnx-merged-way artifact, ~30 fixture edges) by taking the **max** rank — conservative semantics so users aren't routed onto harder terrain than they declared. Opposite policy from `_is_trail_highway` which is permissive (any constituent trail tag = trail). Documented in the docstrings of both helpers; tests cover (`test_filter_trails_list_sac_scale_uses_max_rank`, `test_filter_trails_list_sac_scale_with_unknown_member_drops_edge`). Discovered as a real correctness issue surfaced by the Le Sappey fixture, which has list-valued sac_scale; the earlier Chamrousse fixture had none.

4. **`osm_way_id` and `highway` are typed as `int | list[int]` and `str | list[str]`,** broader than the story's "int" / "str" wording in AC #1. Real osmnx behavior: edges produced by simplification of multiple chained OSM ways carry list-valued `osmid` and `highway` (~12% of edges in the fixture). The contract docstring on `osm_load` states the actual returned types explicitly; tests cover both cases.

5. **`normalize_edges` and `max_sac_rank` are public** (no leading underscore). Both started as private helpers but tests need them to mirror production logic without duplicating the lookup tables (would create drift opportunities), and basedpyright correctly flagged cross-module use of a private name. Renamed; they're legitimate utilities.

6. **basedpyright globally relaxed** for `reportUnknownVariableType`, `reportUnknownArgumentType`, `reportUnknownMemberType`, `reportUnknownParameterType`, `reportMissingTypeArgument`. networkx and osmnx ship partial / no type stubs, so these rules fire on every `MultiDiGraph` operation. The pyproject.toml had them pre-commented for exactly this case. Architecture §Type hints rule "Avoid Any except at explicit external boundaries (OSM response parsing, ...)" anticipates this — OSM IS the external boundary. Documented inline in `pyproject.toml`.

7. **Default pytest collection now excludes `live`-marked tests via `addopts = ["-m", "not live"]`.** AC #6 required this. To run the live test: `uv run pytest -m live`. Behind a corporate TLS-intercepting proxy whose root CA isn't in `certifi`'s bundle, additionally set `STEEPROUTE_USE_OS_TRUSTSTORE=1` — `tests/integration/conftest.py` honors that env var to switch SSL verification to the OS trust store via the `truststore` package (added as a dev dep). No `verify=False` bypass anywhere. Verified locally: `STEEPROUTE_USE_OS_TRUSTSTORE=1 uv run pytest -m live` → 1 passed in 1.97 s (drift within ±10%). `regenerate.py` uses the same `truststore.inject_into_ssl()` mechanism unconditionally.

8. **Carry-forward resolved**: negative `--radius` is rejected at `osm_load` precondition with `BadCLIArgError`. Story 2.8's CLI wiring inherits the guard. Three parametrized tests cover this.

**AC walkthrough — evidence per criterion:**

1. AC #1 — `pipeline/osm.py` defines `osm_load` (radius validation + osmnx fetch + normalize_edges) and `filter_trails` (pure, returns new graph, never mutates input). 33 unit tests, including `test_filter_trails_does_not_mutate_input` and `test_normalized_fixture_*` for the attribute contract. ✅
2. AC #2 — `tests/fixtures/grenoble_small/{osm_graph.graphml,README.md,regenerate.py}` committed. Fixture is 723 KB at Le Sappey-en-Chartreuse 2 km. README documents fetch parameters + version + capture date + area rationale. `regenerate.py` is one-command runnable (`python regenerate.py`); `truststore.inject_into_ssl()` handles corporate TLS-intercepting proxies via the OS trust store. CI-side fixture-size assertion in `tests/unit/test_osm.py::test_committed_fixture_under_size_cap`. ✅
3. AC #3 — `test_filter_trails_include_vs_exclude_diff_equals_untagged_count` (diff = untagged count exactly), 6 parametrized `test_filter_trails_no_surviving_edge_exceeds_cap[T1..T6]` (cap-respecting), `test_filter_trails_difficulty_cap_sweep_is_monotonic` (monotonic sweep), `test_fixture_contains_multi_way_merged_edges` + `test_fixture_geometry_synthesis_runs` (real-data edge cases). ✅
4. AC #4 — `test_filter_trails_single_untagged_edge_include_keeps_exclude_strips`, `test_filter_trails_one_edge_per_highway_type_keeps_only_trail_tags`, `test_filter_trails_keeps_multi_tag_edge_if_any_tag_is_trail`, `test_filter_trails_drops_edge_with_unknown_sac_scale_value`, plus argument-validation tests. ✅
5. AC #5 — `tests/integration/test_osm_live.py::test_live_osm_matches_fixture` marked `@pytest.mark.live`; fetches with the same params as the fixture; asserts node + edge counts within ±10%. Excluded from default `pytest --cov` (1 deselected). ✅
6. AC #6 — All four CI gates green on Windows. Three new runtime deps in `pyproject.toml`. `live` marker registered. `addopts = ["-m", "not live"]` excludes live test from default collection. ✅

### File List

**New:**
- `src/steeproute/pipeline/osm.py` — `osm_load` (stage 1) + `filter_trails` (stage 2) + `normalize_edges` + `max_sac_rank` helper. Module-scope constants: `SAC_SCALE_RANK`, `TRAIL_HIGHWAY_TAGS`, `_OSM_CUSTOM_FILTER`. ~100 logical lines + docstrings.
- `tests/unit/test_osm.py` — 35 tests covering attribute contract, include-vs-exclude diff, input-mutation safety, cap-sweep across T1..T6, monotonicity, real-trail edge cases (multi-way merge, geometry synthesis), synthetic-graph cases (single untagged, per-highway-type, multi-tag edge, unknown sac_scale, list-valued sac_scale max-rank, list-valued sac_scale with unknown member), and argument validation (unknown policy, malformed cap, case-insensitive cap, non-positive radius).
- `tests/integration/test_osm_live.py` — 1 `@pytest.mark.live` test asserting node/edge drift vs fixture within ±10%.
- `tests/integration/conftest.py` — `pytest_configure` hook honoring `STEEPROUTE_USE_OS_TRUSTSTORE=1` env var to route SSL verification through the OS trust store (via the `truststore` package) instead of `certifi`'s vendored bundle. For developers behind corporate TLS-intercepting proxies whose root CA is OS-installed but not in certifi. CI never sets it; no `verify=False` anywhere.
- `tests/fixtures/grenoble_small/osm_graph.graphml` — committed binary, 723 KB, Le Sappey-en-Chartreuse 2 km, 468 nodes / 1208 edges, 5 SAC boundaries (T1-T5) including list-valued sac_scale entries.
- `tests/fixtures/grenoble_small/README.md` — fetch parameters, osmnx version, capture date, area rationale, `sac_scale`-in-`useful_tags_way` footgun documented.
- `tests/fixtures/grenoble_small/regenerate.py` — one-command fixture regeneration with optional `--insecure` for broken corporate cert chains.

**Modified:**
- `src/steeproute/models.py` — added `Area(center, radius_km)` `@dataclass(frozen=True, slots=True)`.
- `pyproject.toml` — added `osmnx`, `networkx`, `shapely` to runtime deps; added `truststore` to dev deps (powers the corporate-proxy-friendly TLS verification in `tests/integration/conftest.py` and `regenerate.py`); added `live` pytest marker; added `addopts = ["-m", "not live"]`; relaxed five `reportUnknown*`/`reportMissingTypeArgument` basedpyright rules with inline rationale.
- `uv.lock` — regenerated for the new deps.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.1 status `backlog → ready-for-dev → in-progress → review`; epic-2 `backlog → in-progress`; dated comments added.

**Untouched (intentionally):**
- `src/steeproute/pipeline/__init__.py` — placeholder docstring already says "stages 1-9 and orchestrator"; orchestrator wiring lands in Story 2.5.
- `src/steeproute/cli/setup.py` — CLI wiring of `osm_load` lands in Story 2.8.
- `src/steeproute/errors.py` — `BadCLIArgError` (used for non-positive radius) and `DataSourceUnavailableError` (Story 2.9) already in place.

### Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-05-06 | Yann (Claude Opus 4.7) | Story 2.1 implemented: pipeline stages 1–2 (`osm_load` + `filter_trails`) in `src/steeproute/pipeline/osm.py` with the source-attribute contract (sac_scale, highway, osm_way_id, geometry). Real-OSM fixture committed at `tests/fixtures/grenoble_small/` (Le Sappey-en-Chartreuse 2 km, 723 KB, 468 nodes / 1208 edges, captured via osmnx 2.1.0; README + regenerate.py alongside; T1-T5 SAC variety including list-valued sac_scale). 35 unit tests + 1 `@pytest.mark.live` integration test (live test executed locally, passed; excluded from default CI via `addopts`; corporate-proxy TLS handled cleanly via OS trust store using the `truststore` dev dep — unconditional in both `tests/integration/conftest.py` and `regenerate.py`, no `verify=False` anywhere). New `Area` dataclass in `models.py`. `filter_trails` handles list-valued sac_scale via max-rank (conservative). Negative-radius validation lands here (carry-forward from Epic 1 retro). All four CI gates green: ruff, ruff format, basedpyright 0/0/0, pytest 191 passed (35 new + 156 prior) at 94% coverage. | _pending_ |
| 2026-05-07 | Yann (Claude Opus 4.7) | bmad-code-review applied: 13 patches landed (P1+P2 Area validation incl. NaN/Inf/center-range; P3 sac_scale whitespace normalization; P4 BadCLIArgError for `untagged_policy` and `parse_difficulty_cap`; P5 live-test zero-guard; P6 live test imports fixture constants from `regenerate.py` via new `tests/fixtures/.../__init__.py` packages; P7 `parse_difficulty_cap` made public so tests use it instead of duplicating SAC ranks; P8 `truststore.inject_into_ssl()` symmetric in conftest and regenerate.py; P9 fixture-size assertion < 5 MB; P10 doc-inconsistency cleanup; P11 `cache/` + `.review-tmp/` gitignored; P12 `Area` redocumented as bbox half-side; P13 basedpyright relaxations moved from global to per-file pragmas). 3 decisions resolved (D1 keep bbox + redocument Area, D2 per-file pragmas, D3 accept osmnx settings mutation). 8 items deferred (recorded in `deferred-work.md`). | _pending_ |
