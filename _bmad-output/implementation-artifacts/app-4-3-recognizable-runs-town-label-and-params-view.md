# Story 4.3: Recognizable runs — town label + query-params view

Status: done

<!-- App track (epics-app.md). Story key `app-4-3-*` is `app-`-prefixed to avoid
     collision with the CLI track's `4-3-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want to recognize past runs by place and inspect a query's configuration,
so that the run library is usable without decoding GPS coordinates or losing a run's params.

## Acceptance Criteria

1. **`area_label` on the record.** `JobRecord` gains `area_label: str | None = None`, serialized snake_case in `job.json` and on every job endpoint. It is additive — an existing `job.json` written before this story (no `area_label` key) still loads, defaulting to `None`.

2. **New geocode seam — its own module, offline-safe.** A new `steeproute/app/geocode.py` (NOT under `cli_adapter/`, which is the CLI-coupling boundary only) resolves a `(lat, lon)` center to a nearby town/place name via a best-effort reverse geocode (Nominatim). Every failure mode — no network, timeout, non-200, no result, unparseable body — returns `None` and **never raises to the caller**. It sends a descriptive `User-Agent` (Nominatim usage policy) like the CLI's DEM fetch does.

3. **Stamped at creation, never blocks the job.** `create_job` resolves the label and stores it on the record before persisting, so the label is present on the returned `201` record and on the run library from the first render. A failed/absent lookup leaves `area_label = None` and the job is still created (`201`) and enqueued — geocoding can never fail, error, or stall job submission (FR5 fire-and-forget). The lookup runs off the event loop (it must not block the single worker / SSE streams).

4. **Cards lead with the label (setup and query).** Every run card — setup and query — leads with `area_label` as the primary identifier in place of `kind · r{radius}`; the raw center/radius stays as secondary detail. A run with no label falls back to today's coordinate-led display, so a label-less card is never blank or worse than before.

5. **Query card reveals its params on demand.** A query run card exposes a click-to-reveal view of its full stored `params` set (data already in `job.params` — no new endpoint). Long numbers are grouped with `format.js`'s `groupThousands` (Story 4.2), matching the config form. Setup cards show no params view (their `params` are trivial).

6. **No regressions (scope guard).** Epic 1–3 run-library behavior is preserved: running → queued → history ordering, the status-appropriate metric (done-query cost / failed exit code + `interrupted`), status-gated actions (Watch / View routes / Cancel / Re-run with tweaks), and re-run-with-tweaks prefill. Changes stay within `models.py`, `api.py`, the new `geocode.py`, `runs.js`, `runs.html`/`app.css`, and the affected tests — no `cli_adapter/`, `argv.py`, `queue.py`, or CLI change.

7. **Tests.** `geocode.py` is unit-tested fully offline (HTTP stubbed): a success maps to the expected name, and each failure mode returns `None`. An `api.py` integration test asserts `area_label` is stamped when the geocoder yields a name and that a raising/empty geocoder still returns `201` with `area_label = null` (job unblocked). The frontend follows the established buildless convention — no JS test runner; covered by served-markup assertions where practical plus a browser drive-through.

## Tasks / Subtasks

- [x] Add `area_label` to the record (AC: #1)
  - [x] `JobRecord.area_label: str | None = None` in `models.py`; round-trips through `store` and the job endpoints (pydantic default covers legacy `job.json`).
- [x] Write the geocode seam (AC: #2)
  - [x] New `steeproute/app/geocode.py`: `reverse_geocode(lat, lon) -> str | None`, best-effort Nominatim reverse geocode, short env-overridable timeout, descriptive `User-Agent`; catches every network/parse error → `None`. `_pick_place_name` selects the place from the address hierarchy (`city`/`town`/`village`/… → `name` → first part of `display_name`).
  - [x] Injectable via `create_app(geocode=…)` → `app.state.geocoder` (mirrors `regions_cache_root`); default `None` = labelling off (no network), so existing tests stay offline unchanged; production module-level `app` wires the real `reverse_geocode`.
- [x] Stamp at creation, off the event loop (AC: #3)
  - [x] `create_job` resolves the label via `_resolve_area_label` (which runs the blocking geocoder through `asyncio.to_thread` + a defensive `try/except`) and sets it on `JobRecord` before `store.create(...)`. A raised exception or `None` never prevents the `201`/enqueue.
- [x] Run-library card overhaul (AC: #4, #5, #6)
  - [x] `runs.js`: `cardTitle` leads with `area_label` (fallback to `kind · r{radius}` when unset); center/radius stay in the secondary meta line.
  - [x] Click-to-reveal `<details>` params view for query cards only, rendering `job.params` with `groupThousands` for long numbers (imported from `format.js`), booleans as on/off, null as an em dash. No new fetch — data is already on the record.
  - [x] `app.css`: `.run-card-params*` styles (native `<details>`, collapsed by default, no inline handlers). `runs.html` needed no change — cards are built entirely in JS.
- [x] Tests (AC: #7)
  - [x] New `tests/unit/test_app_geocode.py` (8 tests): stubbed-HTTP success (place + `display_name` fallback); network error / timeout / non-200 / bad-body / no-place / non-dict → `None`.
  - [x] Extended `tests/integration/test_app_api.py` (3 tests): `area_label` stamped (center passed as `(lat, lon)`, persisted); raising geocoder → `201` + `null`; no geocoder (default) → `201` + `null`.
- [x] Verification (AC: all)
  - [x] `uv run pytest tests/unit` + `tests/integration` (separate invocations): app 43 unit + 53 integration passed. Full offline suite **1046 passed**, 17 deselected. `basedpyright` 0/0 and `ruff` clean on all changed Python.
  - [x] Browser drive-through (throwaway seeded server, port 8001, no geocoder → offline): `setup · Chamrousse` and `query · Grenoble` lead with the label; unlabelled query falls back to `query · r10` with coords in secondary meta; query params reveal (collapsed by default) shows `1 000 000` / `200 000` / `100 000` space-grouped, `on`/`off`, `—` for nulls; setup card has no params view.

## Dev Notes

**Post-v1 App Epic 4, Story 4.3 — additive on the shipped run library (Story 3.1/3.2); no rollback.** This closes the last of the four App-UX frictions (recognizable runs) and depends on Story 4.2's shipped `format.js` for number grouping [Source: _bmad-output/planning-artifacts/epics-app.md#Story 4.3; sprint-change-proposal-2026-07-17-app-ux-improvements.md#4.3 (recognizable runs)].

**The geocode module is a NEW outbound seam, not a `cli_adapter` change.** The architecture is explicit: `cli_adapter/` is strictly the CLI-coupling boundary (argv / cache-manifest / params-schema / stdout-classify). Reverse-geocoding is an external HTTP call, so it lives in its own `app/geocode.py`, and `JobRecord` gains `area_label`. Do NOT add it under `cli_adapter/` [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule (post-v1 note); sprint-change-proposal-2026-07-17-app-ux-improvements.md#Section 2 → Technical impact (4.3)].

**Best-effort is a hard requirement, not a nicety.** The App's posture is single-user/local and CLI-honest; a geocode is one more optional outbound call mirroring the existing OpenTopoMap tile fetch — same connectivity assumption. A failed lookup MUST degrade to today's coordinate display and MUST NOT block, delay past a short timeout, error, or 500 the `POST /jobs`. This is the one real correctness risk: the job runner must stay fully functional with no network at all [Source: epics-app.md#Story 4.3 AC (best-effort, offline-safe); epics-app.md#NFR3/NFR5].

**Don't block the event loop.** `create_job` is an async handler and the single asyncio worker + SSE streams share that loop (concurrency=1, NFR1). A synchronous `urllib`/`requests` call inside the handler would stall the worker and every open SSE stream for the duration. Resolve the label via `asyncio.to_thread(...)` (or an async HTTP client) with a short timeout so submission stays responsive and fire-and-forget (FR5) holds [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 2 (single asyncio worker); epics-app.md#FR5, #NFR1].

**Mirror the CLI's outbound-HTTP conventions.** The DEM downloader uses stdlib `urllib.request` with a `User-Agent` header (`"steeproute/0.1 (…)"`) and an env-overridable timeout, catching `URLError`/`OSError`. Nominatim's usage policy *requires* a descriptive `User-Agent`; reuse the same shape. `httpx` is already available (bundled by `fastapi[standard]`) if an async client is preferred over `to_thread(urllib)` — dev's call; either satisfies AC #2/#3 [Source: src/steeproute/pipeline/dem_download.py:100-105,506-518; pyproject.toml (fastapi[standard], requests)].

**Center order is `(lat, lon)`.** `AreaSpec.center` is `tuple[float, float]` = `(lat, lon)`, and `runs.js` already destructures `const [lat, lon] = job.area.center`. Nominatim reverse expects `lat`/`lon` query params — don't transpose [Source: src/steeproute/app/models.py:63-69; src/steeproute/app/static/js/runs.js:35-37].

**Reuse `format.js`, don't re-group.** The params view groups long numbers with `groupThousands` from `static/js/format.js` (shipped by Story 4.2 precisely for this reuse) — do not reimplement grouping or introduce commas (French decimal collision, FR14) [Source: src/steeproute/app/static/js/format.js; app-4-2-config-form-overhaul-flat-numbers-defaults.md#Completion Notes List].

**Frontend conventions (unchanged since Story 1.5/4.1/4.2).** Vanilla ES module, no inline handlers, no build step, no new frontend dependency; `api.js` stays the only URL holder (no new endpoint is needed here — `job.params` already rides on `GET /jobs`). Buildless static assets are served `Cache-Control: no-cache`, so a plain reload picks up JS/CSS edits; the Python (`models.py`/`api.py`/`geocode.py`) changes need a server restart [Source: _bmad-output/planning-artifacts/architecture-app.md#Frontend conventions; app-4-2…md#Debug Log References (stale server)].

**Politeness / caching (optional, not required).** Nominatim asks ≤1 req/sec and no heavy batch use — trivially satisfied for N=1. A tiny cache keyed on rounded coordinates would avoid re-hitting on a re-run of the same center; nice-to-have, out of scope unless cheap.

### Project Structure Notes

Target tree — **one new module** (`geocode.py`) + one new test; the rest are edits. No `cli_adapter/`, `argv.py`, `queue.py`, or CLI change [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── geocode.py                    ★ (NEW) best-effort Nominatim reverse geocode → str | None
├── models.py                     ★ (edit) JobRecord.area_label: str | None = None
├── api.py                        ★ (edit) create_job stamps area_label (off-loop, best-effort)
└── static/
    ├── js/runs.js                ★ (edit) label-led card + query params reveal (reuses format.js)
    ├── runs.html                 ☆ (edit) markup for the params reveal, if any
    └── css/app.css               ★ (edit) card-label emphasis + params-view styles

tests/
├── unit/test_app_geocode.py      ★ (NEW) stubbed-HTTP success + all failure modes → None
└── integration/test_app_api.py   ★ (edit) area_label stamped; failing geocode → 201 + null
```

### Testing

Per AGENTS.md: run `tests/unit` and `tests/integration` in **separate** invocations; keep the full offline suite green. The whole suite is offline — `test_app_geocode.py` MUST stub the HTTP call (no real Nominatim hit), and the `api.py` test MUST inject/patch the geocoder so job creation never touches the network. There is **no JS unit harness** (buildless); `runs.js` is covered by any served-markup assertions plus the browser drive-through — do not add a JS test runner [Source: app-4-2…md#Testing; app-4-1-map-selection-modes.md#Testing].

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 4.3: Recognizable runs (town label + query-params view)] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/epics-app.md#FR13] — human area label on the card (setup + query); query card exposes its param set on demand
- [Source: _bmad-output/planning-artifacts/epics-app.md#UX-DR4 (revised)] — cards lead with the town label; coordinates secondary; query params click-to-reveal
- [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule (post-v1 note, 2026-07-17)] — geocode is `app/geocode.py`, NOT `cli_adapter`; `JobRecord.area_label`, stamped at creation, offline-safe
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-17-app-ux-improvements.md#Section 4b → Story 4.3] — the settled technical plan (new geocode module, record field, label-led cards, params view reusing 4.2's helper)
- [Source: src/steeproute/app/models.py:159-183] — `JobRecord` (add `area_label`); `AreaSpec.center` is `(lat, lon)`
- [Source: src/steeproute/app/api.py:90-109] — `create_job` (stamp `area_label` before `store.create`)
- [Source: src/steeproute/app/static/js/runs.js:29-141] — `areaLabel`/`metaText`/`renderCard` (label-led card + params reveal edit target)
- [Source: src/steeproute/app/static/js/format.js] — `groupThousands` to reuse for the params view
- [Source: src/steeproute/pipeline/dem_download.py:100-105,506-518] — the CLI's outbound-HTTP shape (User-Agent + timeout + catch URLError/OSError) to mirror

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Injection default chosen to keep the whole existing suite offline.** The geocoder is injected via `create_app(geocode=…)` and defaults to `None` = labelling off. This is the load-bearing decision: had the default been the real `reverse_geocode`, every existing `POST /jobs` test (many, across `test_app_api.py`/`test_app_sse.py`) would attempt a real Nominatim call — violating the offline-suite guarantee (AGENTS.md) and risking rate-limits/flakiness on the corporate TLS network. Production opts in explicitly at the module-level `app = create_app(geocode=reverse_geocode)`; only the real production process ever calls out.
- **Browser verification ran against a throwaway seeded server, not the shipped `steeproute-app`.** The production entry point wires the real geocoder (network); to verify the run-library rendering deterministically and offline, a scratchpad script seeded a tmp `JobStore` with a labelled setup, a labelled done query (full params incl. `iter_budget=1000000`), and an unlabelled failed query, then served `create_app(store_root=tmp)` (no geocoder) on port 8001. Confirmed via `read_page` + `javascript_tool` (the in-app pane's screenshot timed out — a known limitation for this pane; DOM inspection substituted): label-led titles, coordinate fallback, collapsed-by-default params `<details>`, space-grouped numbers, `on`/`—` value formatting, and no params view on the setup card.

### Completion Notes List

- **`area_label` on the record (AC #1).** `JobRecord.area_label: str | None = None` — additive; a `job.json` written before this story loads with `area_label=None`. Serialized snake_case on every job endpoint.
- **Offline-safe geocode seam (AC #2).** New `steeproute/app/geocode.py` — its own module, **not** `cli_adapter` (the architecture's post-v1 note: `cli_adapter` is CLI-coupling only). `reverse_geocode` uses stdlib `urllib` with a descriptive `User-Agent` and a short `STEEPROUTE_GEOCODE_TIMEOUT_S` (default 5s) timeout, mirroring the DEM downloader; it catches `URLError`/`OSError`/`ValueError`/`JSONDecodeError` and returns `None` on any failure — it never raises. `GeocodeFn` type alias + injection make it fully stubbable.
- **Stamped at creation, off-loop, best-effort (AC #3).** `create_job` calls `_resolve_area_label`, which runs the blocking geocoder via `asyncio.to_thread` (so the single worker + open SSE streams never stall) inside a defensive `try/except`. A `None`/raise leaves `area_label` unset and the job is still created (`201`) and enqueued — geocoding can never block or fail submission (FR5).
- **Label-led cards + query params reveal (AC #4, #5, #6).** `runs.js`: `cardTitle` leads with `area_label`, falling back to `kind · r{radius}`; center/radius stay as secondary meta. Query cards (only) get a native `<details>` params view rendering `job.params` — long numbers via `format.js`'s `groupThousands` (Story 4.2 reuse, no re-implementation, no commas), booleans on/off, nulls `—`. No new endpoint/fetch. Epic 1–3 ordering, metrics, actions, and re-run prefill are untouched; `cli_adapter`/`argv`/`queue`/CLI unchanged.
- **Validation.** Full offline suite **1046 passed**, 17 deselected (`uv run --no-sync pytest --cov`, 7m18s); app unit 43 + integration 53 in their own invocations; `basedpyright` 0/0 and `ruff` clean on all changed Python. Frontend verified end-to-end in the browser per the Debug Log (buildless — no JS test harness).

### File List

- `src/steeproute/app/models.py` (modified) — `JobRecord.area_label: str | None = None`
- `src/steeproute/app/geocode.py` (new) — best-effort offline-safe Nominatim reverse-geocode seam + `GeocodeFn` type alias
- `src/steeproute/app/main.py` (modified) — `create_app`/lifespan `geocode` injection → `app.state.geocoder`; module-level `app` wires the real `reverse_geocode`
- `src/steeproute/app/api.py` (modified) — `create_job` stamps `area_label` via `_resolve_area_label` (`asyncio.to_thread`, best-effort); `_geocoder` accessor
- `src/steeproute/app/static/js/runs.js` (modified) — label-led `cardTitle`; query-only click-to-reveal params view reusing `format.js`
- `src/steeproute/app/static/css/app.css` (modified) — `.run-card-params*` styles for the params reveal
- `tests/unit/test_app_geocode.py` (new) — 8 tests: stubbed-HTTP success + every failure mode → `None`
- `tests/integration/test_app_api.py` (modified) — 3 area_label tests (stamped; raising geocoder → 201+null; disabled → 201+null)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-17 | Story drafted from epics-app.md (Story 4.3 / FR13 / UX-DR4 revised) + the 2026-07-17 sprint-change-proposal + the architecture-app post-v1 note, on top of the shipped run library (Story 3.1/3.2) and Story 4.2's `format.js`. New offline-safe `app/geocode.py` seam + `JobRecord.area_label` stamped off-loop at creation + label-led cards with a query-params reveal; no `cli_adapter`/CLI change. Status → ready-for-dev. |
| 2026-07-17 | Implemented all seven ACs: `JobRecord.area_label`; new `app/geocode.py` (best-effort Nominatim, `urllib`, catches all → `None`, never raises); injected via `create_app(geocode=…)` (default off → suite stays offline; production wires the real one); `create_job` stamps the label via `asyncio.to_thread` best-effort (201 never blocked); `runs.js` label-led `cardTitle` + query-only `<details>` params view reusing `format.js`. Added 8 geocode unit tests + 3 api integration tests. Full offline suite 1046 passed; basedpyright/ruff clean; browser-verified offline via a seeded throwaway server. Status → review. |
| 2026-07-17 | Code review (low-effort pass, diff-only): no findings. Status → done. |
