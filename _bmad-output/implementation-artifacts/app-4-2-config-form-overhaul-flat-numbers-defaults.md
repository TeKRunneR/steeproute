# Story 4.2: Config form overhaul — flat layout, readable numbers, corrected defaults

Status: done

<!-- App track (epics-app.md). Story key `app-4-2-*` is `app-`-prefixed to avoid
     collision with the CLI track's `4-2-*`; both share sprint-status.yaml. -->

## Story

As a user,
I want every query parameter visible at once, long numbers grouped for readability, and defaults that fit a steep-route tool,
so that the config pane is fast to scan and edit without hunting or misreading.

## Acceptance Criteria

1. **Flat form — no collapse.** The config form renders every schema field in one always-visible list. The `<details class="config-advanced">` "Advanced" wrapper is gone; there is no click-to-expand. All ~24 exposed flags are shown together on open.

2. **Grouping retired from the schema, single-source-of-truth intact.** `SchemaField.group` and `_BASIC_FIELDS` are removed from `params_schema.py`; `query_params_schema()` still introspects `steeproute.cli.query`'s click command for every field's name/type/default/choices/help. `build_query_argv` / `resolve_query_defaults` / validation are unchanged in behavior — the schema is still the only place flags are defined.

3. **Readable numbers — display-only.** Long numeric fields (e.g. `iter_budget`, `stagnation_iters`, `area_cap`) display with **space** thousands separators (`1 000 000`), never commas. Grouping is applied on blur and stripped on submit; the value sent on the wire (`POST /jobs` `params`) and thus to argv stays a plain number. A clear/empty field still submits as unset (`null`), exactly as today.

4. **Shared format helper.** The space-grouping + parse-back logic lives in a small reusable frontend module (not inlined in `config-form.js`), so Story 4.3's run-library params view can group numbers identically.

5. **Corrected steep-route defaults.** The App's quality-demo defaults gain `max_descent_slope = 0.4` and `start_at_junction = True`. On a fresh "Configure query", the descent-cap field prefills `0.4` and the start-at-junction checkbox is checked; a default (untweaked) query therefore emits `--max-descent-slope 0.4` and `--start-at-junction` in argv. Every other default is unchanged.

6. **No behavioral regressions elsewhere (scope guard).** Re-run-with-tweaks prefill (Story 3.2), choice/bool/text field rendering, the empty-form submit guard, and the "quality-demo default ≠ CLI default" contract all still hold. Changes stay within `config-form.js`, a new format module, `params_schema.py`, `app.css`/`index.html` cleanup, and the affected tests — no `argv.py`/`models.py`/`api.py` logic change (only the endpoint docstring, now that "basic/advanced" is retired).

## Tasks / Subtasks

- [x] Retire the basic/advanced grouping in the schema seam (AC: #2)
  - [x] Remove `group` from `SchemaField`, delete `_BASIC_FIELDS`, and drop the `group=…` argument in `query_params_schema()`.
  - [x] Update the `get_query_params_schema` docstring in `api.py` (drop "basic-or-advanced group" wording).
- [x] Add the corrected quality-demo defaults (AC: #5)
  - [x] Add `"max_descent_slope": 0.4` and `"start_at_junction": True` to `_QUALITY_DEFAULTS` (params_schema.py). Confirmed argv emits both by default via the existing `resolved(...)` path (no argv.py edit).
- [x] Factor the number-format helper (AC: #4)
  - [x] New `static/js/format.js`: `groupThousands(value)` (space-group integer part, leave any decimal/sign untouched) + `stripGrouping(text)` (strip whitespace incl. NBSP; callers parse with parseInt/parseFloat, `""` → null). Vanilla ES module, no deps.
- [x] Flatten and wire numbers in the form (AC: #1, #3)
  - [x] In `config-form.js` `renderForm`, append all fields directly (removed the `<details>`/`buildGroup(advanced)` split; one flat `config-group`).
  - [x] Long-number fields (default magnitude ≥ 1000) render as `type="text"` `inputmode="numeric"`, format on `blur` via `groupThousands`, and `readParams` runs int/float values through `stripGrouping` before `parseInt`/`parseFloat` so spaces never reach the wire.
- [x] CSS/markup cleanup (AC: #1, #6)
  - [x] Dropped the now-unused `.config-advanced` rules in `app.css`; `.config-group`/`.config-field` intact. Updated the S2 comment in `index.html`.
- [x] Update tests (AC: #2, #3, #5)
  - [x] `tests/unit/test_app_params_schema.py`: replaced `test_basic_advanced_grouping` with `test_schema_field_carries_no_grouping_metadata`; extended the quality-defaults test with `max_descent_slope == 0.4` and `start_at_junction is True`.
  - [x] `tests/unit/test_app_argv.py`: rewrote the two default-behavior tests — an all-unset `QueryParams()` now emits `--max-descent-slope 0.4` and `--start-at-junction`.
  - [x] `tests/integration/test_app_api.py`: removed the `fields["theta"]["group"]` assertion; added `max_descent_slope`/`start_at_junction` default + `"group" not in` assertions.
- [x] Verification (AC: all)
  - [x] `uv run pytest` unit + integration (separate invocations); full offline suite 1035 passed; `basedpyright` + `ruff` clean; browser drive-through against the real schema (flat 21-field render, `1 000 000` grouping, junction pre-checked, `max_descent_slope` 0.4, end-to-end submit stored `iter_budget=999000` plain — not 9).

## Dev Notes

**Post-v1 App Epic 4, Story 4.2 — additive on the shipped config form (Story 2.1); no rollback.** The introspected schema stays the single source of truth for form render, validation, and argv; this story only removes the UI grouping metadata and adjusts two defaults [Source: _bmad-output/planning-artifacts/epics-app.md#Story 4.2; sprint-change-proposal-2026-07-17-app-ux-improvements.md#4.2 (config-form overhaul — merged)].

**Removing `group` is a wire-shape change — hunt every reader.** `SchemaField.group` is serialized by `GET /params/query-schema` and read in `config-form.js` (`f.group === "basic"/"advanced"`). Both the JS filter and the two tests asserting `group` must go together, or FastAPI/pydantic will still serialize the field and the flat render won't take [Source: src/steeproute/app/cli_adapter/params_schema.py:66-77,115; src/steeproute/app/static/js/config-form.js:89-100; tests/unit/test_app_params_schema.py:67-73; tests/integration/test_app_api.py:513].

**The spaces-break-`parseInt` trap is the one real correctness risk.** Today `readParams` does `Number.parseInt(input.value, 10)` — on `"1 000 000"` that yields `1` (parse stops at the first space). Any long-number field shown grouped MUST be parsed through the space-stripping helper on submit, or a grouped iter-budget silently queues a 1-iteration solve. The wire value must remain a plain number; grouping is display-only [Source: src/steeproute/app/static/js/config-form.js:103-119; epics-app.md#Story 4.2 AC (grouping is display-only)].

**`max_descent_slope`/`start_at_junction` default via the existing seam, no argv.py edit.** `resolve_query_defaults()` reads `_QUALITY_DEFAULTS`, and `build_query_argv`'s `resolved(name)` already emits `--start-at-junction` when truthy and `--max-descent-slope <v>` when non-None. Setting the two `_QUALITY_DEFAULTS` entries is the whole change; argv.py, `QueryParams` (both fields already `… | None`), and the CLI options are untouched [Source: src/steeproute/app/cli_adapter/params_schema.py:53-61; src/steeproute/app/cli_adapter/argv.py:105-164; src/steeproute/cli/_shared.py:421-442]. Consequence to note (not a task): with the descent default at `0.4`, an unset field resolves to `0.4`, so the form no longer has a natural "off" for descent cap — acceptable for a steep-route tool, and out of scope to add an explicit disable.

**Which fields get grouping.** Target the genuinely long ones — `iter_budget` (1 000 000), `stagnation_iters` (200 000), `area_cap` (100 000). A general "group the integer part, leave decimals" helper applied to all numeric inputs is also fine and simpler (small values like `theta=0.2`, `n=5` are unaffected since they have no thousands). Dev's call; either satisfies AC #3.

**Frontend conventions (unchanged since Story 1.5/1.6/4.1).** Vanilla ES module, no inline handlers, no build step, no new dependency; `api.js` remains the only URL holder. Buildless assets are served `Cache-Control: no-cache` since Story 4.1, so a plain reload picks up JS changes (no server restart needed for static edits) [Source: _bmad-output/planning-artifacts/architecture-app.md#Frontend conventions; app-4-1-map-selection-modes.md#Completion Notes List (caching fix)].

### Project Structure Notes

Target tree — edits the starred files; **one new file** (`format.js`), no backend logic change [Source: _bmad-output/planning-artifacts/architecture-app.md#Complete project tree]:

```
src/steeproute/app/
├── cli_adapter/params_schema.py  ★ (edit) drop group/_BASIC_FIELDS; +2 quality defaults
├── api.py                        ★ (edit) endpoint docstring only
└── static/
    ├── js/format.js              ★ (NEW) groupThousands / parsePlainNumber — shared, reused by 4.3
    ├── js/config-form.js         ★ (edit) flat render + grouped-number in/out
    ├── css/app.css               ★ (edit) remove .config-advanced rules
    └── index.html                ☆ (comment only) "basic/advanced" wording in the S2 comment
```

### Testing

Per AGENTS.md: run `tests/unit` and `tests/integration` in **separate** invocations; keep the full offline suite green. There is **no JS unit harness** (buildless) — the frontend is covered by the served-markup / schema-endpoint assertions in `test_app_api.py` plus a `run`-skill / browser drive-through; do **not** add a JS test runner [Source: app-4-1-map-selection-modes.md#Testing]. Existing tests that will break and must be updated (not merely re-run): `test_app_params_schema.py::test_basic_advanced_grouping`, `test_app_argv.py::test_query_argv_max_descent_slope_omitted_when_unset` + `test_query_argv_start_at_junction_flag_only_when_true`, and the `fields["theta"]["group"]` line in `test_app_api.py`. Drive-through: open Configure query on a built region → all fields visible with no Advanced toggle; `iter_budget` shows `1 000 000`; start-at-junction pre-checked; submit and confirm the queued job's stored `params` hold plain numbers (no spaces).

### References

- [Source: _bmad-output/planning-artifacts/epics-app.md#Story 4.2: Config form overhaul — flat layout, readable numbers, corrected defaults] — the epic AC this story realizes
- [Source: _bmad-output/planning-artifacts/epics-app.md#FR12] — flat config form, all flags always visible
- [Source: _bmad-output/planning-artifacts/epics-app.md#FR14] — space thousands separators, never commas (French decimal collision)
- [Source: _bmad-output/planning-artifacts/epics-app.md#UX-DR2 (revised)] — drop the advanced collapse; quality defaults include max_descent_slope=0.4 and start_at_junction on
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-07-17-app-ux-improvements.md#4.2 (config-form overhaul — merged)] — exact technical plan (remove `<details>`, neutralize `_BASIC_FIELDS`, grouped text inputs, +2 defaults, shared helper for 4.3)
- [Source: src/steeproute/app/cli_adapter/params_schema.py] — `SchemaField` / `_BASIC_FIELDS` / `_QUALITY_DEFAULTS` / `query_params_schema` (edit target)
- [Source: src/steeproute/app/cli_adapter/argv.py:105-164] — `build_query_argv` `resolved(...)` path that emits the two corrected defaults with no edit
- [Source: src/steeproute/app/static/js/config-form.js] — `renderForm`/`buildGroup`/`readParams` (flat-render + grouped-number edit target)
- [Source: src/steeproute/cli/_shared.py:421-442] — CLI `--start-at-junction/--no-start-at-junction` (default False) and `--max-descent-slope` (default None) being overridden
- [Source: _bmad-output/planning-artifacts/architecture-app.md#Category 9] — params-schema = CLI arg parser, single source of truth for form/validation/argv

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- **Stale running server masked the schema change during verification.** A pre-existing `steeproute-app` (PID 28420) held port 8000 with the *old* Python in memory (`max_descent_slope: None`, `start_at_junction: False`, `group` present) — the params-schema seam is Python, so a running process must be restarted to pick it up (unlike static assets, which are `no-cache` since Story 4.1). Left the user's :8000 process alone; started a throwaway `uvicorn … --port 8001` for verification and stopped it after. The fresh server returned the corrected 21-field flat schema.
- **End-to-end submit proved the spaces-strip path.** Drove the real shipped modules in the browser: `import('/static/js/config-form.js')` → `openConfigForm({center:[45.26,5.79],radius_km:1})`, typed a NBSP-grouped `iter_budget`, dispatched the form `submit`, then read `GET /jobs` — the stored job held `iter_budget=999000` (plain int, **not** 9), `start_at_junction=True`, `max_descent_slope=0.4`. Confirmed the separator is NBSP (U+00A0, char code 160) and `stripGrouping` round-trips it to `"1000000"`. (The verification query job ran on the tiny r1 region and finished on its own; test server then stopped.)

### Completion Notes List

- **Flat form (AC #1, #2).** `SchemaField.group` and `_BASIC_FIELDS` removed from `params_schema.py`; `query_params_schema()` is now pure introspection. `config-form.js` `renderForm` appends all fields in one `config-group` (no `<details>`). The wire schema no longer carries `group`; the endpoint docstring and the S2 `index.html` comment updated to match. Schema remains the single source of truth for form/validation/argv — `build_query_argv`/`resolve_query_defaults` untouched.
- **Readable numbers (AC #3, #4).** New shared `static/js/format.js` (`groupThousands` + `stripGrouping`, NBSP separator, decimal/sign preserved). Long-number fields — selected by a self-contained display heuristic (numeric with default magnitude ≥ 1000: `iter_budget`, `stagnation_iters`, `area_cap`), not a hand-listed name set — render as `type="text" inputmode="numeric"`, re-group on blur, and are stripped back to a plain number in `readParams` before `parseInt`/`parseFloat`. Grouping is display-only; the wire value stays plain. `format.js` is ready for Story 4.3's run-library params view to reuse.
- **Corrected defaults (AC #5).** `_QUALITY_DEFAULTS` gains `max_descent_slope=0.4` and `start_at_junction=True`. These flow to argv through the existing `resolved(...)` path — no `argv.py`/`models.py`/CLI change. Consequence (noted, out of scope): with the descent default at 0.4, an unset field resolves to 0.4, so there is no longer a form-native "off" for the descent cap — acceptable for a steep-route tool.
- **No regressions (AC #6).** Re-run-with-tweaks prefill, choice/bool/text rendering, and the empty-form submit guard all unchanged. Only `config-form.js` + new `format.js`, `params_schema.py`, `api.py` (docstring), `app.css`, `index.html` (comment), and the affected tests changed.
- **Validation.** Full offline suite **1035 passed**, 17 deselected (`uv run --no-sync pytest`); app unit 73 + integration 47 passed in their own invocations; `basedpyright` 0/0 on the changed Python; `ruff` clean. Frontend verified end-to-end in the browser per the Debug Log (no JS test harness — established buildless App convention).

### File List

- `src/steeproute/app/cli_adapter/params_schema.py` (modified) — dropped `SchemaField.group` + `_BASIC_FIELDS`; added `max_descent_slope=0.4` / `start_at_junction=True` to `_QUALITY_DEFAULTS`
- `src/steeproute/app/api.py` (modified) — `get_query_params_schema` docstring (removed "basic-or-advanced group" wording)
- `src/steeproute/app/static/js/format.js` (new) — shared `groupThousands` / `stripGrouping` number-format helpers (reused by Story 4.3)
- `src/steeproute/app/static/js/config-form.js` (modified) — flat render (removed `<details>`/advanced split); grouped-number text inputs + blur regroup + strip-on-submit
- `src/steeproute/app/static/css/app.css` (modified) — removed the unused `.config-advanced` rules
- `src/steeproute/app/static/index.html` (modified) — S2 config-form comment updated (flat, all flags at once)
- `tests/unit/test_app_params_schema.py` (modified) — replaced the grouping test; extended quality-defaults assertions
- `tests/unit/test_app_argv.py` (modified) — rewrote the two default-behavior tests for the corrected defaults
- `tests/integration/test_app_api.py` (modified) — dropped the `group` wire assertion; added corrected-default + `"group" not in` assertions
- `.claude/launch.json` (new) — `steeproute-app` launch config for the browser preview
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status tracking

## Change Log

| Date | Change |
|---|---|
| 2026-07-17 | Story drafted from epics-app.md (Story 4.2 / FR12 / FR14 / UX-DR2 revised) + the 2026-07-17 sprint-change-proposal, on top of the shipped Story 2.1 config form. Flat form + shared space-grouping helper + corrected steep-route defaults; frontend + schema-seam only, no argv/model logic change. Status → ready-for-dev. |
| 2026-07-17 | Implemented all three AC bullets: retired basic/advanced grouping in `params_schema.py`/`config-form.js`, added `max_descent_slope=0.4`/`start_at_junction=on` quality defaults, and factored `format.js` (NBSP space-grouping, display-only, stripped on submit). Updated 3 test files. Full suite 1035 passed; basedpyright/ruff clean. Browser-verified end-to-end (flat 21-field form, `1 000 000` grouping, junction pre-checked, submit stored `iter_budget=999000` plain). Status → review. |
| 2026-07-17 | Code-review fix (low-effort pass finding): the grouped fields are now free `type="text"`, so a mistyped separator survived to the wire — `parseInt("1,000,000")` truncates to `1` (French comma habit), and pure garbage became `NaN → null → silent schema default`. `readParams` now parses numeric fields with strict `Number()` (rejects commas/junk as `NaN`, no truncation), returns `{params, invalid}`, and the submit handler blocks with "Enter a valid number for: …" instead of silently queuing a wrong value. Browser-verified: `1,000,000` and `abc` both block (form stays open, field named); a valid `750 000` still submits as plain int `750000`. |
| 2026-07-17 | Code review addressed and re-verified; no further findings. Status → done. |
