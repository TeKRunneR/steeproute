# Story 2.1: Configure and queue a query (schema-driven form)

Status: done

<!-- App track (epics-app.md). Story key `app-2-1-*` is `app-`-prefixed to avoid
     collision with the CLI track's `2-1-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want a basic/advanced config form exposing all query flags with quality defaults, and a way to queue the query,
so that I can launch a route generation against a built region with full control.

## Acceptance Criteria

1. **`cli_adapter/params_schema.py` (seam 3).** Introspects `steeproute.cli.query`'s click command into a flag schema (name, type, default, help, choices) — the single source of truth for the form, validation, and argv build (no hand-duplicated flag list). Excludes flags the App owns instead of the user: `--center`/`--radius` (the map selection, not a form field), `--output-dir`/`--cache-dir` (server-controlled paths), `--verbose`/`--quiet` (not a route param). Numeric/choice defaults for the quality-demo knobs (`--iter-budget 200000 --stagnation-iters 10000 --difficulty-cap T4 --elevation-deadband 1`, per AGENTS.md) override the low CLI defaults in the schema; every other field keeps the CLI's own default.

2. **Config form renders and submits.** A basic/advanced form built from the schema (basic = common knobs; advanced = the collapsed full set) opens for a green (built) region, prefilled with the quality-demo defaults. Submitting calls `POST /jobs` with `kind=query`, the validated params, and the selected area; a 201 navigates to the job's Run-watch (mirrors the existing Build flow). Invalid params (wrong type, bad choice) are rejected 422.

3. **Query executes on Epic 1's runner.** `POST /jobs` accepts `kind=query` (the current 422 "setup only" gate is extended, not just relaxed — argv build and progress classification must both handle the query kind; see Dev Notes). The job is queued/serial/stoppable exactly like a setup job, and its stdout log tail is visible on Run-watch (the GRASP best-cost/iteration readout and stage advancement arrive in Story 2.2 — until then the query's stdout only feeds the log tail, not stage progress).

4. **Query gets a real, isolated output directory.** The subprocess argv passes an explicit `--output-dir` under the job's own store directory (not the CLI's relative `./results` default, which would collide across concurrent... sequential-but-reused server working directories). `JobRecord.result_dir` is set accordingly so Story 2.3 can serve it statically later.

5. **Scope guard.** No GRASP readout / stage(n/total) advancement for query (Story 2.2), no result iframe / static result mount (Story 2.3), no run-library changes (Epic 3). The map-home "Configure query" placeholder (`map-home.js`) is replaced by the real form flow; the Build/setup path is untouched.

## Tasks / Subtasks

- [x] `cli_adapter/params_schema.py` — introspection seam (AC: #1)
  - [x] Introspect `steeproute.cli.query.cli.params` (click `Command.params`, a list of `click.Option`) into a typed schema; map click types (`FLOAT`, `INT`, `STRING`, `Choice`, boolean flags) to a JSON-schema-ish shape the form and pydantic validation both consume.
  - [x] Apply the quality-demo default overrides on top of the introspected CLI defaults.
  - [x] Exclude `--center`/`--radius`/`--output-dir`/`--cache-dir`/`--verbose`/`--quiet` (plus `--version`, click's own eager flag — found live in the browser drive-through; see Debug Log).
  - [x] Export from `cli_adapter/__init__.py`.
- [x] `QueryParams` model (AC: #1, #2)
  - [x] Add to `models.py`, mirroring `SetupParams`'s pattern: one field per exposed query flag, typed to match the click option. Deviation from the literal task wording (see Completion Notes): every field defaults to `None` ("unset") instead of hand-copied concrete defaults — `params_schema.resolve_query_defaults()` is the single place a `None` resolves to the quality-demo or CLI default, so the default value is never duplicated between `models.py` and `params_schema.py`.
  - [x] Extend `JobCreate.params` to accept `SetupParams | QueryParams` depending on `kind` (discriminated on the `kind` field already present in the body).
- [x] `cli_adapter/argv.py` — `build_query_argv` (AC: #3, #4)
  - [x] Map `QueryParams` + `AreaSpec` to `steeproute` CLI flags (every field emitted explicitly, not only-non-default — see Completion Notes for why that pattern doesn't fit here).
  - [x] Add an explicit `--output-dir` argument pointed at a per-job directory; added `JobStore.job_dir()` as the public accessor (the private `_job_dir` stays internal).
- [x] `queue.py` — dispatch on job kind (AC: #3, #4)
  - [x] `default_build_argv` now dispatches on `record.kind`; `Worker._run_one` sets `record.result_dir` (via `store.job_dir(id) / "result"`) for query jobs before argv is built, persisted alongside the RUNNING transition.
- [x] `cli_adapter/progress_parse.py` — minimal query classification (AC: #3)
  - [x] Added `QueryProgressParser` (log_tail + `phase=Phase.QUERY` only, no stage/grasp); `progress_parser_for` returns it for `JobKind.QUERY` instead of raising.
- [x] `api.py` — accept `kind=query` (AC: #2, #3)
  - [x] Removed the `create_job` 422 "setup only" gate; `JobCreate`'s own `_coerce_params` validator now rejects a body whose `params` don't match `kind` (422).
- [x] Frontend — config form (AC: #2, #5)
  - [x] `static/js/config-form.js` — new module: added `GET /params/query-schema` (served through `api.js`'s `getQuerySchema()`), renders basic row + collapsed `<details>` advanced section, submits via `createJob`.
  - [x] No dedicated HTML page added — the form renders as a panel (`#config-form`) on the existing map-home page, confirmed via browser drive-through.
  - [x] Replaced `map-home.js`'s `configureBtn` placeholder handler with `openConfigForm(area)`; on submit the form navigates to `runWatchUrl(job.id)`.
  - [x] `static/css/app.css` — `.config-form`/`.config-group`/`.config-field`/`.config-advanced` panel styles.
- [x] Tests (AC: #1-#4)
  - [x] `tests/unit/test_app_params_schema.py`: new file — excluded fields absent, quality-demo overrides, CLI-default passthrough, type/choice mapping, basic/advanced grouping, schema↔click-params correspondence, `resolve_query_defaults` parity.
  - [x] `tests/unit/test_app_argv.py`: added `build_query_argv` cases (area/output-dir, quality-demo default resolution, CLI-default resolution, explicit override, seed/`max-descent-slope` omit-when-unset, `start-at-junction` flag-only-when-true, executable resolution).
  - [x] `tests/unit/test_app_progress_parse.py`: added `QueryProgressParser` cases (blank line, log-tail-only with no stage/grasp, log-tail ordering) and updated the factory test (no longer expects `NotImplementedError`).
  - [x] `tests/integration/test_app_api.py`: replaced `test_query_kind_rejected_422` (its premise is now false) with a `kind=query` lifecycle test (201 → runs → done, `result_dir` set), an invalid-param-type 422 test, a mismatched-params-for-kind 422 test, an explicit-params round-trip test, and a `GET /params/query-schema` test.

## Dev Notes

**This is step 4 of the architecture's implementation sequence** — layering the query flavour's config form onto Epic 1's proven job runner and Run-watch screen [Source: architecture-app.md#Decision Impact Analysis]. It closes FR3 (config form) and starts the query path FR8 hands off to (Story 2.3 finishes it).

**`params_schema.py` is the last of the adapter's four seams** (argv 1.3, progress_parse 1.4, regions 1.6, params_schema here) [Source: architecture-app.md#The load-bearing rule]. Introspect `steeproute.cli.query`'s click `Command` rather than hand-listing flags — `cli/_shared.py` defines every option as a reusable `click.option(...)` decorator (`theta_option`, `n_option`, `iter_budget_option`, …) [Source: src/steeproute/cli/_shared.py:329-637], and `cli/query.py`'s `@click.command` stack (lines 109-140) is the full flag set actually wired to the query CLI [Source: src/steeproute/cli/query.py:109-140]. A click `Command` object's `.params` list carries name/type/default/help for each — that's the introspection target, not the source files.

**Quality-demo defaults, not CLI defaults (FR3).** The CLI's own defaults are tuned low for fast iteration; the App must present `--iter-budget 200000 --stagnation-iters 10000 --difficulty-cap T4 --elevation-deadband 1` as the form's starting values [Source: AGENTS.md#Solver / GRASP]. Every other flag keeps its CLI default — don't invent new defaults for flags AGENTS.md doesn't mention.

**Three things the current codebase does NOT yet support for a query job, all of which must land in this story or the first query job breaks:**
- `api.py::create_job` unconditionally 422s any `kind` other than `setup` [Source: src/steeproute/app/api.py:90-94].
- `queue.py::default_build_argv` unconditionally calls `build_setup_argv` regardless of `record.kind` [Source: src/steeproute/app/queue.py:46-48].
- `progress_parse.py::progress_parser_for` raises `NotImplementedError` for `JobKind.QUERY` [Source: src/steeproute/app/cli_adapter/progress_parse.py:94-105] — its own docstring says "Query jobs are rejected at the API (422), so a query kind never reaches the worker that calls this," which stops being true the moment AC #3 lands. A minimal (log-tail-only) query parser must exist now so `_consume_stdout` doesn't crash on the first stdout line of a real query job; full stage/grasp classification is explicitly Story 2.2's scope, not this one's — don't over-build it here.

**`--output-dir` is not optional.** `cli/query.py` defaults it to `pathlib.Path("./results")` — a relative path resolved against the App server's cwd, not a per-job location [Source: src/steeproute/cli/query.py: `output_dir_option` via `cli/_shared.py:551-557`]. Every query job must pass an explicit `--output-dir` under its own job directory (mirrors architecture's `<job_id>/result/` layout, feeding `JobRecord.result_dir`) [Source: architecture-app.md#Category 5 — Job persistence & store; architecture-app.md#Runtime-resolved paths]. `JobStore` has no public job-directory accessor today (only the private `_job_dir`) [Source: src/steeproute/app/store.py:44-45] — add one rather than let the adapter or queue recompute the path formula, which would create a second source of layout truth (the same anti-pattern Story 1.6 avoided for the cache; see that story's Dev Notes).

**Basic/advanced split is a UI grouping, not a schema concept the CLI has any opinion on.** No architecture doc pins which flags are "common knobs" — use judgment (e.g. `--theta`, `--difficulty-cap`, `--n`, `--seed` as basic; the rest advanced) rather than treating this as a settled requirement to reverse-engineer.

**No new page may be needed.** The architecture's complete project tree lists `config-form.js` as a standalone module but does NOT list a `config-form.html` alongside `index.html`/`run-watch.html`/`runs.html` [Source: architecture-app.md#Complete project tree]. Prefer rendering the form as a panel on the existing map-home page (toggled when "Configure query" is enabled and clicked) over adding a new route — consistent with the thinness NFR and the placeholder this story replaces [Source: src/steeproute/app/static/js/map-home.js:149-152].

**Frontend conventions (established since Story 1.5/1.6).** Vanilla ES modules; `api.js` is the only URL holder — extend it, don't hardcode a fetch elsewhere; kebab-case JS files; no inline handlers; server is the source of truth [Source: architecture-app.md#Frontend conventions; src/steeproute/app/static/js/api.js].

**Params validation tier.** Per the existing `SetupParams`/`JobCreate` pattern, pydantic field types (matching the click option types) are what the 422 gate checks — deep range validation (`--theta >= 0`, `--j-max in [0,1]`, etc.) already lives in `cli/_shared.py::validate_solver_options` and runs inside the spawned subprocess, surfacing as a `failed` job (exit 2), not a duplicated client/API-side range check [Source: src/steeproute/app/models.py:72-89; src/steeproute/cli/_shared.py:181-305]. Don't re-implement those range checks in the App.

### Project Structure Notes

Target tree — this story creates/edits the **starred** files (rest are prior/later) [Source: architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── models.py                     ★ (edit) QueryParams; JobCreate.params dispatch
├── api.py                        ★ (edit) accept kind=query
├── queue.py                      ★ (edit) dispatch build_argv + progress_parser_for on kind
├── store.py                      ★ (edit) public per-job directory accessor
├── cli_adapter/
│   ├── __init__.py               ★ (edit) export params_schema + build_query_argv
│   ├── argv.py                   ★ (edit) build_query_argv
│   ├── params_schema.py          ★ click introspection → form/validation schema (seam 3)
│   └── progress_parse.py         ★ (edit) minimal query classification (log tail only)
└── static/
    ├── index.html                ★ (edit) config-form panel
    ├── css/app.css                ★ (edit) form styles
    └── js/
        ├── api.js                ★ (edit) schema fetch (if server-provided) + query createJob
        ├── map-home.js            ★ (edit) real Configure-query flow, replaces placeholder
        └── config-form.js         ★ schema-driven basic/advanced form
```

### Testing

Per AGENTS.md: `uv run basedpyright <files>`; run `tests/unit` and `tests/integration` in **separate** invocations. App tests use FastAPI's `TestClient` with a fake/echo subprocess (existing pattern in `test_app_api.py`/`test_app_queue.py`) — no real `steeproute` solve needed for unit/integration coverage. There is no JS unit harness (buildless); cover the frontend via served-asset/markup assertions plus a manual `run`-skill / browser drive-through of pick → configure → queue → watch. Existing tests (`test_app_api.py`, `test_app_sse.py`, `test_app_argv.py`, `test_app_progress_parse.py`, `test_app_regions.py`, the CLI's own `query.py` tests) must stay green.

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 2.1: Configure and queue a query (schema-driven form)] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 9 — Config / params model] — schema-from-click-introspection decision, quality-demo defaults
- [Source: _bmad-output/planning-artifacts/architecture-app.md#The load-bearing rule: one CLI-adapter boundary] — seam 3 (params_schema) is the only click-introspecting code
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree] — no dedicated config-form HTML page listed
- [Source: _bmad-output/planning-artifacts/ux-design-specification.md#S2] — basic/advanced conventional form, no wireframe
- [Source: AGENTS.md#Solver / GRASP] — the exact quality-demo param values to default to
- [Source: src/steeproute/cli/query.py:109-140] — the full query click option stack to introspect
- [Source: src/steeproute/cli/_shared.py:329-637] — the reusable option decorators (types/defaults/help)
- [Source: src/steeproute/cli/_shared.py:181-305] — `validate_solver_options`, the range-check tier the App must NOT duplicate
- [Source: src/steeproute/app/models.py:72-111] — `SetupParams`/`JobCreate`/`JobRecord` pattern to mirror for `QueryParams`
- [Source: src/steeproute/app/cli_adapter/argv.py] — `build_setup_argv`'s only-emit-non-default-flags style to mirror
- [Source: src/steeproute/app/cli_adapter/progress_parse.py:94-105] — the `NotImplementedError` that must be resolved before query jobs are accepted
- [Source: src/steeproute/app/queue.py:46-48] — `default_build_argv`'s setup-only dispatch to extend
- [Source: src/steeproute/app/api.py:90-94] — the 422 "setup only" gate to extend
- [Source: src/steeproute/app/store.py:44-45] — `_job_dir`, needing a public per-job-directory accessor
- [Source: src/steeproute/app/static/js/map-home.js:149-152] — the Configure-query placeholder this story replaces
- [Source: src/steeproute/app/static/js/api.js] — the single URL holder to extend
- [Source: _bmad-output/implementation-artifacts/app-1-6-map-home-pick-an-area-and-build-a-region.md] — the adjacent map-home story; frontend conventions and the "don't duplicate a source of truth" precedent (km/deg geometry) this story's argv/output-dir path applies the same way

## Dev Agent Record

### Agent Model Used

claude-sonnet-5 (Claude Sonnet 5)

### Debug Log References

- **`click.version_option`'s eager `--version` flag leaked into the introspected schema and rendered as a bogus "version" checkbox** — found live during the browser drive-through, not by the unit tests (which only assert against a curated set of expected field names, so a genuinely *extra* field wasn't caught). `steeproute.cli.query`'s `cli.params` includes every `click.Option` attached to the command, and `@click.version_option(...)` attaches one named `version` just like any real flag. Fixed by adding `"version"` to `params_schema._EXCLUDED_FIELDS`; added `test_excluded_fields_are_absent` coverage for it and updated the integration schema test. (`click`'s auto-added `--help` is NOT in `.params` — it's injected dynamically at parse/format time — so no equivalent exclusion was needed for it.)
- **Browser pane rendering quirk (unrelated to this story): `#map`'s computed height resolves to 0px in this sandboxed Browser pane** (`getBoundingClientRect`/`offsetHeight`/`clientHeight` all 0, despite `.app-main.map-main` correctly reporting a definite height and the sibling `.picker-panel` stretching correctly via default flex `align-items: stretch`). Confirmed NOT caused by this story's changes: removing the newly-added `#config-form` element via JS didn't fix it, and the CSS chain (`body` flex-column → `.app-main.map-main` flex-row → `.map { flex:1; height:100% }`) is unchanged from Story 1.6, which was itself verified working in a real browser. This made real mouse-driven map clicks unreliable to test in this environment (Leaflet's pixel→latlng transform depends on a nonzero container size), so the config-form itself was verified by dynamically `import()`-ing `config-form.js` and calling `openConfigForm(...)` directly (see Completion Notes) rather than via a simulated map click. Not filed as a story finding since it doesn't reproduce the affected code path (`map-home.js`'s click handler is unchanged) — flagging here in case it resurfaces for a future story that does need real map-click verification in this same environment.
- **Full pick→configure→queue→watch flow verified against a real `steeproute` subprocess**, not just the fake-subprocess test harness: seeded a real (structurally valid, empty-graph) cache entry via `steeproute.cache.write_entry`, opened the config form, set `seed=7`/`n=2`, submitted, and confirmed on Run-watch: `kind=query`, quality-demo defaults visible in the real CLI's own "Run summary" (`iter_budget=200000`, `stagnation_iters=10000`), explicit overrides honored (`n=2 seed=7`), status `done`/exit 0, and `JobRecord.result_dir` set to the expected per-job path. `routes_returned: 0/2` is the expected honest outcome for an empty-graph fixture, not a failure. The seeded cache entry and its `index.json` reference were removed afterward to leave the machine's real dev cache as found.

### Completion Notes List

- **`params_schema.py` (seam 3, new).** Introspects `steeproute.cli.query`'s click `Command.params` — never hand-lists flag names — into `SchemaField`s (name/type/default/help/group/choices). Excludes `center`/`radius`/`output_dir`/`cache_dir`/`verbose`/`quiet`/`version` (the last found via browser testing, see Debug Log). Quality-demo overrides (`iter_budget`, `stagnation_iters`, `difficulty_cap`, `elevation_deadband`) are a small named dict layered on top of the introspected CLI defaults; every other field keeps its CLI default untouched. `resolve_query_defaults()` exposes `{name: default}` as the one place a field's actual default value is computed.
- **`QueryParams` (models.py) — deliberate deviation from the story's literal task wording.** The task said "defaulted to the quality-demo values where the schema overrides them," but implementing that literally would hand-copy each quality-demo numeric value from `params_schema.py` into `models.py` a second time — exactly the dual-source-of-truth risk the architecture's "single source of truth" principle for Category 9 warns against. Instead every `QueryParams` field defaults to `None` ("unset"); `build_query_argv` is the only place `None` gets resolved, via `resolve_query_defaults()`. `extra="forbid"` was added to both `QueryParams` and `SetupParams` so a body posting the wrong kind's fields under a given `kind` fails 422 instead of silently ignoring them — `JobCreate._coerce_params` (a `model_validator(mode="before")`) dispatches the raw `params` dict to the right model ahead of pydantic's own union resolution, so a `SetupParams`-shaped body under `kind=query` is rejected rather than coerced.
- **`build_query_argv` always emits every field explicitly**, unlike `build_setup_argv`'s "only emit non-default flags" style. With ~20 flags and several App defaults deliberately differing from the CLI's own defaults, an only-non-default rule would have to know which default applies per-flag — reintroducing the exact bug `resolve_query_defaults` exists to prevent, for a marginal argv-legibility gain. Two fields keep "omit when unset" semantics because that's their actual CLI meaning: `--seed` (omitted → unseeded) and `--max-descent-slope` (omitted → cap disabled).
- **`JobStore.job_dir()`** is the new public per-job-directory accessor (the store stays the single owner of the job directory layout); the worker sets `record.result_dir = str(store.job_dir(id) / "result")` for query jobs at the RUNNING transition, before argv is built, and persists it in the same `store.update()` call.
- **`QueryProgressParser`** feeds `log_tail` and sets `phase=Phase.QUERY` only — `stage_name`/`stage_index`/`stage_total` stay at zero-value defaults and `grasp` stays `null`. This is intentionally the minimum needed to stop `_consume_stdout` from crashing on a query job's stdout; stage advancement and the GRASP readout are Story 2.2's scope.
- **`api.py`** dropped the setup-only 422 gate entirely (kind-vs-params validation now happens in `JobCreate` itself) and added `GET /params/query-schema` returning the introspected schema directly (list of dataclasses — FastAPI serializes them natively).
- **Frontend**: `config-form.js` (new) renders a basic/advanced panel (`#config-form` in `index.html`) entirely from the introspected schema — no flag names hand-listed in JS. `map-home.js`'s `configureBtn` now calls `openConfigForm({center, radius_km})` instead of the placeholder status message. `api.js` gained `getQuerySchema()`. No new HTML page/route was added (see Dev Notes' reasoning); the form is a panel on the existing map-home page.
- **Existing test updated, not just left green:** `test_query_kind_rejected_422` in `test_app_api.py` asserted the exact behavior this story deliberately reverses, so it was replaced (not merely supplemented) with query-acceptance tests.
- **Validation:** `basedpyright` 0 errors/0 warnings on all changed files. `ruff check` + `ruff format --check` clean. Full `tests/unit` (710 passed) and `tests/integration` (181 passed, 2 deselected) green, run as separate invocations per AGENTS.md — no regressions. Browser-verified end-to-end against a real `steeproute` subprocess (see Debug Log) — the query-flag-schema `version` leak was caught and fixed during this pass.

### File List

- `src/steeproute/app/cli_adapter/params_schema.py` (new) — seam 3: click introspection → form schema
- `src/steeproute/app/cli_adapter/argv.py` (modified) — `build_query_argv`, `resolve_query_executable`
- `src/steeproute/app/cli_adapter/progress_parse.py` (modified) — `QueryProgressParser`; `progress_parser_for` no longer raises for `JobKind.QUERY`
- `src/steeproute/app/cli_adapter/__init__.py` (modified) — export the new params_schema/argv/progress_parse public names
- `src/steeproute/app/models.py` (modified) — `QueryParams`; `SetupParams`/`QueryParams` `extra="forbid"`; `JobCreate._coerce_params` kind-dispatch
- `src/steeproute/app/queue.py` (modified) — `default_build_argv` dispatches on `record.kind`; `_run_one` sets `result_dir` for query jobs
- `src/steeproute/app/api.py` (modified) — removed the setup-only 422 gate; added `GET /params/query-schema`
- `src/steeproute/app/store.py` (modified) — public `JobStore.job_dir()`
- `src/steeproute/app/static/index.html` (modified) — `#config-form` panel markup
- `src/steeproute/app/static/js/config-form.js` (new) — schema-driven basic/advanced form
- `src/steeproute/app/static/js/map-home.js` (modified) — real Configure-query flow, replaces placeholder
- `src/steeproute/app/static/js/api.js` (modified) — `getQuerySchema()`
- `src/steeproute/app/static/css/app.css` (modified) — `.config-form` panel styles
- `tests/unit/test_app_params_schema.py` (new)
- `tests/unit/test_app_argv.py` (modified) — `build_query_argv` cases
- `tests/unit/test_app_progress_parse.py` (modified) — `QueryProgressParser` cases; updated factory test
- `tests/integration/test_app_api.py` (modified) — query lifecycle/validation/schema-endpoint tests; replaced `test_query_kind_rejected_422`
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-16 | Story drafted from epics-app.md + architecture-app.md + the CLI's click option surface, on top of Story 1.6's map-home + Epic 1's job runner. Status → ready-for-dev. |
| 2026-07-16 | Implemented the params_schema seam, `QueryParams`/`JobCreate` kind-dispatch, `build_query_argv` + per-job `--output-dir`, kind-dispatched worker argv/progress classification, the `GET /params/query-schema` endpoint, and the schema-driven config-form panel. 4 new/updated test files; full suite green (710 unit + 181 integration). Browser-verified end-to-end against a real `steeproute` subprocess, fixing one live-caught issue (`--version` leaking into the form schema). Status → review. |
| 2026-07-16 | Code review (low effort): fixed 2 frontend findings. (1) `.config-form` had `position: absolute` with no positioned ancestor, so it anchored to the viewport and overlapped the header — added `position: relative` to `.app-main.map-main`. (2) `openConfigForm` revealed the panel (with a clickable Queue button) before the async schema fetch populated the fields, so a submit in that window queued empty default-only `params` — made `openConfigForm` `await renderForm()` before revealing, plus a defense-in-depth submit guard that refuses to queue when the form has no rendered fields. Both re-verified in the browser (panel sits below header, `offsetParent` = map-main; empty-form submit posts nothing; normal submit still carries `n`/`seed` through to the subprocess). Integration suite green (181). |
| 2026-07-16 | Code review passed, no further findings. Status → done. |
