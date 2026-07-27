---
title: 'Remove the area-size cap'
type: 'refactor'
created: '2026-07-27'
status: 'done'
context: []
baseline_commit: '653fa816b9143c1c45c834060011b6865c1f6d50'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The query CLI's `--area-cap` ceiling (default 500 km²) never protects the
user in practice — they always override it to a large no-op value (e.g. `100000`) since
they don't want to be bothered by it. It's dead weight: a required `SolverParams` field,
a CLI flag, validation logic, and matching test/doc footprint for a check nobody wants.

**Approach:** Delete the area-cap feature entirely from the query CLI path: the
`--area-cap` flag, `validate_area_size`'s cap check, `SolverParams.area_cap`, its App-layer
plumbing, and FR2/docs mentions. This is deletion, not a default change — `area_cap` stops
existing as a concept in the query pipeline.

## Boundaries & Constraints

**Always:**
- Only the **query CLI's** area cap goes. The **setup CLI's** unrelated 50 km radius
  ceiling (`_SETUP_MAX_RADIUS_KM` / `validate_setup_area` / `validate_setup_radius` in
  `src/steeproute/cli/_shared.py`) is a separate safety net — do not touch it.
- `cache.area_km2()` (`src/steeproute/cache.py`) is dual-purpose (also used for cache
  ranking) — keep the function, only strip cap-specific docstring/comment references to it.
- Every `SolverParams(...)` construction site (tests, benchmarks, fixtures) must drop the
  `area_cap=` kwarg since it's a required dataclass field being removed, not defaulted.

**Ask First:** none anticipated — this is a straightforward, fully-specified deletion.

**Never:**
- Do not touch `docs/examples/**` (historical HTML/JSON run outputs) — those are frozen
  artifacts, not code.
- Do not repurpose `--area-cap` as a hidden/deprecated no-op flag — remove it outright.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Large query area | `steeproute --center ... --width 200 --height 200` (previously would exceed cap) | Query proceeds; no area-size rejection | N/A |
| `--area-cap` passed on CLI | User runs `steeproute --area-cap 500 ...` | Click reports unknown option, exit 2 | Standard Click "no such option" error |
| Setup CLI still capped | `steeproute-setup --center ... --radius 200` | Unchanged: rejected by the existing 50 km setup ceiling | Unchanged existing error message |

</frozen-after-approval>

## Code Map

- `src/steeproute/cli/_shared.py` -- remove `area_cap_option` (~L652-658) and the cap
  branch of `validate_area_size` (~L248-274); keep the function for its other validation
  (size vs. non-cap concerns, if any) or inline remaining checks into callers if nothing
  is left.
- `src/steeproute/cli/query.py` -- remove `--area-cap` flag wiring (L46,133,165), the
  `validate_area_size` call (L188-196), and the `area_cap=` kwarg at L265.
- `src/steeproute/models.py` -- remove `SolverParams.area_cap` field (docstring L260,
  field L290).
- `src/steeproute/app/models.py` -- remove `QueryParams.area_cap` (L173).
- `src/steeproute/app/cli_adapter/argv.py` -- remove the `--area-cap` argv emission
  (L151-152).
- `src/steeproute/app/cli_adapter/params_schema.py` -- remove `_QUALITY_DEFAULTS["area_cap"]`
  (L65-69,82).
- `_bmad-output/planning-artifacts/prd.md` -- remove FR2 and the `area_cap` default line.
- `_bmad-output/planning-artifacts/architecture.md` -- remove FR2 mapping and area_cap refs.
- `README.md` -- remove `--area-cap` from the Key Parameters table and the example command.
- `AGENTS.md` -- remove the `--area-cap`-specific guidance lines (the "can't be disabled
  with 0" note becomes moot).
- `_bmad-output/planning-artifacts/epics.md` -- remove/adjust FR2 references.
- Test files (drop `area_cap=` kwarg from `SolverParams(...)` calls; delete cap-specific
  assertions/tests): `tests/unit/test_validator.py`, `test_output.py`,
  `test_degradation_message.py`, `test_models.py`, `test_grasp_construction.py`,
  `test_area_parsing.py`, `test_cli_options.py`, `test_cli_help.py`, `test_app_argv.py`,
  `test_app_params_schema.py`; integration: `test_elevation_consistency.py`,
  `test_grasp_descent_cap.py`, `test_metamorphic.py`, `test_grasp_junction_start.py`,
  `test_route_discovery_fixes.py`, `test_grasp_theta_prefix.py`, `test_output_on_fixture.py`,
  `test_validator_on_fixture.py`, `conftest.py`, `test_grasp_on_fixture.py`,
  `test_grasp_reproducible.py`, `test_time_budget.py`, `test_oracle_correctness.py`,
  `test_stagnation.py`; e2e: `test_cli_smoke.py` (drop
  `test_query_area_cap_exceeded_exits_2`), `test_coverage_check.py`;
  `tests/benchmarks/conftest.py` (drop `area_cap=500.0` from the pinned regression param set).

## Tasks & Acceptance

**Execution:**
- [x] `src/steeproute/cli/_shared.py` -- delete `area_cap_option` and the cap-check branch
  of `validate_area_size` -- removes the flag definition and enforcement
- [x] `src/steeproute/cli/query.py` -- delete `--area-cap` wiring and the
  `validate_area_size`/`area_cap=` call sites -- removes query-CLI surface
- [x] `src/steeproute/models.py` -- delete `SolverParams.area_cap` field and docstring
  entry -- removes the data-model concept
- [x] `src/steeproute/app/models.py`, `app/cli_adapter/argv.py`,
  `app/cli_adapter/params_schema.py` -- delete `area_cap` plumbing -- keeps App layer
  in sync with the CLI it wraps
- [x] Update all listed test files -- drop `area_cap=` kwargs, delete cap-specific tests
  (`test_query_area_cap_exceeded_exits_2`, `test_area_parsing.py` cap cases, `test_cli_help.py`
  help-text assertions, `test_app_argv.py`/`test_app_params_schema.py` cap assertions) --
  keeps the suite green post-deletion
- [x] `tests/benchmarks/conftest.py` -- drop `area_cap=500.0` from the pinned regression
  param set; run `--update-regression` if goldens fail to construct -- keeps regression
  fixtures buildable
- [x] `prd.md`, `architecture.md`, `README.md`, `AGENTS.md`, `epics.md` -- remove FR2 and
  all `area_cap`/`--area-cap` mentions -- keeps docs truthful post-removal

**Acceptance Criteria:**
- Given a query area far larger than the old 500 km² default, when running the query
  CLI, then it proceeds without any area-size rejection.
- Given `--area-cap` passed on the CLI, when running either CLI, then Click exits 2 with
  an unrecognized-option error.
- Given the setup CLI's existing 50 km radius ceiling, when exceeded, then it still
  rejects exactly as before (unaffected by this change).
- Given the full test suite, when run after this change, then it passes with zero
  remaining references to `area_cap`/`--area-cap` outside of `docs/examples/**`.

## Spec Change Log

## Verification

**Commands:**
- `uv run basedpyright src/steeproute` -- expected: no new type errors from the removed field
- `uv run pytest tests/unit` -- expected: all pass, no `area_cap` references remain
- `uv run pytest tests/integration` -- expected: all pass (run separately per AGENTS.md)
- `uv run pytest tests/e2e/test_cli_smoke.py tests/e2e/test_coverage_check.py` -- expected: pass; `--area-cap` now an unrecognized option
- `git grep -in "area.cap" -- . ':!docs/examples'` -- expected: no matches

## Suggested Review Order

**Deletion: CLI flag and validation**

- Entry point — `validate_area_size` and `area_cap_option` are gone; the setup-side ceiling comment now correctly claims it's the only area-size safety net.
  [`_shared.py:255`](../../src/steeproute/cli/_shared.py#L255)

- Query CLI no longer wires `--area-cap` or calls the deleted validator.
  [`query.py:1`](../../src/steeproute/cli/query.py#L1)

**Deletion: data model**

- `SolverParams.area_cap` field removed; docstring field-count corrected 15→14 in review.
  [`models.py:237`](../../src/steeproute/models.py#L237)

**App-layer plumbing kept in sync**

- `QueryParams.area_cap` removed.
  [`app/models.py:148`](../../src/steeproute/app/models.py#L148)

- `--area-cap` argv emission removed.
  [`app/cli_adapter/argv.py:1`](../../src/steeproute/app/cli_adapter/argv.py#L1)

- `_QUALITY_DEFAULTS["area_cap"]` removed.
  [`app/cli_adapter/params_schema.py:70`](../../src/steeproute/app/cli_adapter/params_schema.py#L70)

**Docs corrected to match (incl. review-pass fixes)**

- FR2 dropped, numbering gap explained inline.
  [`prd.md:478`](../../_bmad-output/planning-artifacts/prd.md#L478)

- Risk-mitigation row for "compute budget blown" rewritten (review finding: had lost its mitigation with no replacement reasoning).
  [`prd.md:450`](../../_bmad-output/planning-artifacts/prd.md#L450)

- FR2 mapping dropped; unsubstantiated "r20" envelope claim reverted (review finding, unrelated slip-in).
  [`architecture.md:262`](../../_bmad-output/planning-artifacts/architecture.md#L262)

- `--area-cap` dropped from Key Parameters table and example command.
  [`README.md:77`](../../README.md#L77)

- Gallery regeneration command fixed — it referenced the now-deleted flag (review finding; out of the Code Map but not a frozen output artifact).
  [`docs/examples/README.md:31`](../../docs/examples/README.md#L31)

- Two stale open deferred-work rows updated to stop citing the deleted flag/function (review finding).
  [`deferred-work.md:57`](../../_bmad-output/implementation-artifacts/deferred-work.md#L57)

- Backlog item annotated done.
  [`future-ideas.md:162`](../../_bmad-output/planning-artifacts/future-ideas.md#L162)

**Peripherals: tests**

- New regression test replaces the old cap-exceeded test, asserting Click rejects the deleted flag outright.
  [`test_cli_smoke.py:138`](../../tests/e2e/test_cli_smoke.py#L138)

- Cap-specific test blocks removed; all other `SolverParams(...)` construction sites across ~25 unit/integration/benchmark files drop the `area_cap=` kwarg.
  [`test_area_parsing.py:1`](../../tests/unit/test_area_parsing.py#L1)
