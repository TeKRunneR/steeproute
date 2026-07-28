---
title: 'Tune query-CLI defaults to match real usage; remove the setup radius cap'
type: 'refactor'
created: '2026-07-28'
status: 'done'
context: []
baseline_commit: '88389cd43e1cbe13788dfca2f50b4c4bb6b55b3d'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The user always overrides the same handful of query-CLI flags away from
their shipped defaults (they're tuned for fast iteration, not the quality/scale the
user actually runs) — typing them every invocation is friction. Separately, the setup
CLI's 50 km radius/dimension ceiling exists only to bother a single trusted user.

**Approach:** Bump the query CLI's own flag defaults to the user's real values, matching
the App's already-validated "quality-demo" numbers where one exists. Delete the setup
CLI's radius/dimension ceiling outright (same treatment the query-side `--area-cap` got
2026-07-27), accepting that the risks it mitigated (Overpass timeout/size limits, an OOM
path, an unbounded-smoothing edge case) become unmitigated again.

## Boundaries & Constraints

**Always:**
- `--start-at-junction` stays a plain presence flag (drop the `--no-start-at-junction`
  companion entirely) and its true default stays `False` when absent — only its click
  definition simplifies, per explicit instruction. `--max-descent-slope` keeps `None`
  (off) as its true absent-default, but gains Click's optional-flag-value form
  (`is_flag=False, flag_value=0.4`) so bare `--max-descent-slope` means 0.4 while typing
  nothing at all still means no cap. Neither flag's *resolved-when-absent* value changes.
- Change only CLI-decorator-level defaults in `cli/_shared.py`/`cli/query.py`/
  `solver/grasp.py` (none of these are pipeline-content-hashed) — do not touch
  `pipeline/smoothing.py`'s `ELEVATION_DEADBAND_DEFAULT_M` or any `models.py` dataclass
  default, so this change causes zero cache re-keying.
- `tests/e2e/conftest.py`'s `run_query` fixture must pin the *pre-change* effective
  values (difficulty-cap T3, l-connector 200, elevation-deadband 0, j-max 0.30, n 5,
  iter-budget 2000, stagnation-iters 100, workers 1, progress-interval 5.0) as its own
  baseline args, ahead of each test's `extra_args`, so every e2e test that currently
  omits these flags keeps today's behavior/runtime — mirroring the pinning discipline
  `tests/integration/conftest.py`, `tests/benchmarks/conftest.py`, and `regression.py`
  already use.
- `app/cli_adapter/params_schema.py`'s `_QUALITY_DEFAULTS` loses the 6 keys that now
  equal the plain CLI default (`iter_budget`, `stagnation_iters`, `difficulty_cap`,
  `elevation_deadband`, `j_max`, `workers`); `start_at_junction`/`max_descent_slope` stay
  (App still wants them on by default; CLI still defaults them off, per above).
- `--merge-interval` (already 250_000, matching real usage), `--theta`, `--min-climb-slope`,
  `--min-climb-ground-length`, `--elevation-smoothing`, `--untagged-trails`,
  `--time-budget`, `--seed`, `--cache-dir` are unchanged.
- Setup radius cap deletion mirrors `spec-remove-area-cap.md`'s pattern: delete the
  constant, the check function, its call site, and its tests outright — don't repurpose
  as a no-op.

**Ask First:** none anticipated — every open design question was already resolved above.

**Never:** don't touch `docs/examples/**` (frozen artifacts); don't add a new CLI flag
to re-gate the setup ceiling; don't change `--n`'s dataclass/model defaults, only the
CLI flag.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bare query invocation | `steeproute --center C --radius R` (no other flags) | Runs with difficulty-cap T4, l-connector 50, elevation-deadband 1, j-max 0, n 10, iter-budget 1000000, stagnation-iters 200000, workers 4, progress-interval 1 | N/A |
| Bare `--max-descent-slope` | flag present, no value | Descent cap = 0.4 | N/A |
| `--max-descent-slope` omitted | flag absent | No descent cap (unchanged) | N/A |
| `--start-at-junction` omitted | flag absent | No junction constraint (unchanged) | N/A |
| Large setup area | `steeproute-setup --center C --radius 200` (previously exceeded the 50 km ceiling) | Setup proceeds; no radius/dimension rejection | N/A |
| `--no-start-at-junction` passed | user types the now-removed spelling | Click reports unknown option, exit 2 | Standard Click error |

</frozen-after-approval>

## Code Map

- `src/steeproute/cli/_shared.py` -- bump `difficulty_cap_option`/`l_connector_option`/`elevation_deadband_option`/`j_max_option`/`n_option`/`workers_option`/`progress_interval_option` defaults; collapse `start_at_junction_option` to a single `is_flag=True` (drop `--no-...`); add `flag_value=0.4` to `max_descent_slope_option`; delete `_SETUP_MAX_RADIUS_KM`, `_SETUP_CEILING_DETAIL`, `validate_setup_area`.
- `src/steeproute/cli/setup.py` -- drop the `validate_setup_area` import and call site.
- `src/steeproute/cli/query.py` -- `DEFAULT_ITER_BUDGET` 2000 → 1_000_000; refresh its docstring reasoning.
- `src/steeproute/solver/grasp.py` -- `STAGNATION_ITERS_DEFAULT_PLACEHOLDER` 100 → 200_000; refresh its docstring.
- `src/steeproute/app/cli_adapter/params_schema.py` -- prune the 6 now-redundant `_QUALITY_DEFAULTS` keys; update the module/dict comments.
- `tests/e2e/conftest.py` -- pin `run_query`'s baseline args to pre-change defaults.
- `tests/unit/test_app_argv.py`, `tests/unit/test_app_params_schema.py` -- fix `n` default assertions (5 → 10); refresh comments that called the 6 pruned keys "quality-demo overrides."
- `tests/unit/test_area_parsing.py` -- delete the `validate_setup_area` import and its whole test block.
- `tests/e2e/test_cli_smoke.py` -- delete `test_setup_radius_above_ceiling_exits_2`.
- `tests/unit/test_cli_options.py` -- `test_verbose_flag_sets_verbose_state_on_setup_cli` must trip its offline `BadCLIArgError` via a malformed `--center` instead of the deleted ceiling.
- `_bmad-output/planning-artifacts/prd.md` -- fill in the `--iter-budget`/`--progress-interval` "TBD" cells; update the changed-default rows; rewrite the "Compute budget blown" risk row to drop the now-false setup-ceiling mitigation.
- `README.md` -- Key Parameters rows that framed a value as a manual override (difficulty-cap, elevation-deadband, iter-budget/stagnation-iters, n/j-max, workers) now describe the default.
- `AGENTS.md` -- correct the "CLI defaults are too low for quality output" framing now that most of those values are the defaults.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- reopen/annotate the setup-ceiling-mitigated rows (D8 and the OOM/n_intervals rows) as unmitigated.
- `_bmad-output/planning-artifacts/future-ideas.md` -- mark both backlog items done, dated, pointing at this spec.

## Tasks & Acceptance

**Execution:**
- [x] `cli/_shared.py` -- apply all default/flag-shape changes above -- delivers the new query-CLI defaults and the flag simplifications
- [x] `cli/_shared.py`, `cli/setup.py` -- delete the setup radius/dimension ceiling and its call site -- removes the cap
- [x] `cli/query.py`, `solver/grasp.py` -- bump `DEFAULT_ITER_BUDGET`/`STAGNATION_ITERS_DEFAULT_PLACEHOLDER` -- resolves the unset-flag fallbacks to the new values
- [x] `app/cli_adapter/params_schema.py` -- prune redundant `_QUALITY_DEFAULTS` keys -- keeps the App schema truthful
- [x] `tests/e2e/conftest.py` -- pin `run_query`'s baseline args -- keeps every e2e test that omits these flags at today's behavior/runtime
- [x] `tests/unit/test_app_argv.py`, `test_app_params_schema.py`, `test_area_parsing.py`, `tests/e2e/test_cli_smoke.py`, `tests/unit/test_cli_options.py` -- apply the fixes listed in the Code Map -- keeps the suite green and honest
- [x] `prd.md`, `README.md`, `AGENTS.md`, `deferred-work.md`, `future-ideas.md` -- apply the doc updates listed in the Code Map -- keeps docs truthful post-change

**Acceptance Criteria:**
- Given a bare `steeproute --center ... --radius ...` invocation, when it runs, then every solver-affecting flag not explicitly passed resolves to its new default (Boundaries table).
- Given `--max-descent-slope` passed with no value, when parsed, then the cap is 0.4; given it is omitted entirely, then no cap applies.
- Given `--no-start-at-junction`, when passed, then Click rejects it as an unrecognized option.
- Given `steeproute-setup --radius 200` (or any large `--width`/`--height`), when run, then it is not rejected on size grounds.
- Given the full test suite, when run after this change, then it passes, and no e2e test's wall-clock time regresses from the iter-budget/stagnation-iters bump.

## Spec Change Log

- **`params_schema.py`'s `_QUALITY_DEFAULTS` pruning, corrected (2026-07-28).** The
  Boundaries section assumed all 6 keys were literal click-option defaults, redundant
  once the CLI default equals the same value. True for `difficulty_cap`,
  `elevation_deadband`, `j_max`, `workers` — pruned as written. `iter_budget` and
  `stagnation_iters` are NOT literal click defaults: both flags keep `default=None`
  at the click level (an explicit "unset" sentinel the CLI body resolves to
  `DEFAULT_ITER_BUDGET`/`STAGNATION_ITERS_DEFAULT_PLACEHOLDER`), so pruning them
  outright made `params_schema.py` report `None` instead of `1000000`/`200000` to the
  App (caught by `test_query_argv_unset_fields_resolve_to_quality_demo_defaults` and
  `test_quality_demo_values_now_match_plain_cli_defaults` both failing). Fix: added
  `_UNSET_FLAG_FALLBACKS`, importing the same two constants `cli/query.py`/
  `solver/grasp.py` resolve the unset flag to, so these two still can't drift from
  the plain CLI's actual behavior — just via a different mechanism than
  `param.default` introspection.
- **`tests/e2e/test_interrupt.py` also needed pinning, not just `conftest.py`.** It
  builds its own subprocess argv directly (bypassing the `run_query` fixture the
  Boundaries section named), and previously omitted `--workers` entirely. With the
  CLI default bumped 1 -> 4, it would have silently started routing through the
  parallel-GRASP path instead of the single-process `except KeyboardInterrupt` path
  this test specifically exercises. Added the same pre-change baseline flags
  (`--workers 1` plus the other 5 not already overridden by this test) directly to
  its own args list.
- **`tests/unit/test_cli_options.py::test_setup_cli_without_verbose_leaves_state_false`**
  (the sibling of the test named in the Code Map) also tripped the now-deleted
  ceiling via `--radius 5000` and would otherwise have started reaching real
  `osm_load`/network code with the ceiling gone. Fixed the same way: malformed
  `--center` instead.
- **`src/steeproute/regression.py`'s `_PINNED_PARAMS` was missing `--workers`
  entirely** (not mentioned in the Code Map, discovered via the e2e run) — every
  regression/flag-on golden had silently been running at whatever the CLI defaulted
  to, never actually pinned, violating the module's own "never inherited from CLI
  defaults" rule. Bumping `--workers` 1->4 flipped these 7 fixtures onto the
  parallel-GRASP path, drifting every golden. Fix: added `"--workers": "1"` to
  `_PINNED_PARAMS` (restoring the single-process path every committed golden was
  actually baked against) and ran `uv run update-regression --all` plus the two
  flag-on fixtures by name. Confirmed via `git diff` that only the `params_hash`
  field changed in all 7 golden JSON files — the `routes` arrays are byte-identical,
  so this is a pinning-coverage fix, not a behavior change requiring a route-level
  rationale.
- Also removed `_is_radius_shorthand` in `cli/_shared.py` (not named in the Code
  Map): it was `validate_setup_area`'s only caller, so deleting `validate_setup_area`
  left it fully dead code with no other reference.
- README.md: also simplified the Quickstart example command, which had passed
  `--difficulty-cap T4 --elevation-deadband 1 --j-max 0` explicitly — now redundant
  with the plain default — alongside the deliberately-smaller `--iter-budget`/
  `--stagnation-iters`/`--n` quick-look overrides. Not explicitly named in the Code
  Map ("Key Parameters rows") but directly affected by the same defaults change.

**Review loop (2026-07-28) — 3 review agents (blind hunter, edge-case hunter, acceptance
auditor) found zero `bad_spec`/`intent_gap` findings (core defaults, flag-shape changes,
and the cache-hash boundary all verified correctly implemented); the following `patch`
findings were auto-fixed without reopening the frozen Intent/Boundaries:**

- **No regression test that the removed `--no-start-at-junction` spelling now errors**
  (found independently by both the blind hunter and the edge-case hunter, and as an
  untested I/O-matrix row by the acceptance auditor). Fix: added
  `test_query_no_start_at_junction_flag_removed` to `tests/e2e/test_cli_smoke.py`,
  mirroring the existing `test_query_area_cap_flag_removed` pattern.
- **No test that bare `--max-descent-slope` actually resolves to 0.4 at the CLI level**
  (acceptance auditor: the AC was implemented and manually verified but had no
  regression test; only App-layer/schema tests existed). Fix: added
  `test_bare_max_descent_slope_flag_resolves_to_0_4` to
  `tests/e2e/test_journey_1_happy_path.py`, asserting on the JSON sidecar's
  `metadata.params.max_descent_slope` field via the existing `run_query`/`seeded_cache`
  fixtures.
- **No test that a large setup area is actually accepted post-ceiling-removal**
  (acceptance auditor: `test_setup_radius_above_ceiling_exits_2` was deleted with no
  replacement asserting the new accepted state). Fix: added
  `test_setup_accepts_a_radius_far_above_the_former_ceiling` to
  `tests/e2e/test_coverage_check.py`, reusing its existing offline `_seed_setup`
  helper (which already accepts a `radius_km` override) with `radius_km=200.0`.
- **`deferred-work.md`'s reopening was incomplete — missed the DEM-mosaic-memory row**
  (acceptance auditor: two more rows in that file still treated the deleted ceiling as
  live; one of them — `_fetch_mosaic`'s unbounded ~1.6 GB float32 array at the old
  50 km ceiling — is the concrete, quantified instance of the "OOM path" this spec's
  own frozen Intent named as becoming unmitigated, and it's now materially worse
  (~25 GB+ at a `--radius 200`), not just unmitigated). Fix: reopened both rows
  (`2-8` section row 1; `spec-dem-auto-download.md` section's mosaic-memory row) with
  dated annotations, same style as the two rows already reopened during implementation.
- **PRD risk row kept "High" severity with no re-justification after losing part of
  its mitigation** (blind hunter). Fix: added one sentence tying the kept severity to
  the human's earlier explicit decision (accept the reopened risk) rather than a claim
  the risk shrank.
- **Minor precision/completeness nits**, all cheap one-line fixes: `argv.py`'s
  `build_query_argv` docstring didn't list `start_at_junction` among the "omit when
  off" exceptions (it already behaved correctly — this was a documentation gap, not a
  bug); `params_schema.py`'s `_UNSET_FLAG_FALLBACKS` comment didn't explain why its two
  constants come from two different modules; `AGENTS.md` described
  `max_descent_slope`'s `None` and `start_at_junction`'s `False` off-defaults as one
  thing ("off/`None`"); `cli/setup.py`'s new comment cited "Story:" with no identifier
  unlike every other comment in this diff; `architecture.md` had 3 stale "`--workers`
  default: 1" mentions (out of this spec's declared doc list, but cheap and directly
  caused by this change); README's quickstart comment underplayed how much heavier the
  new full defaults are (seconds -> up to `--time-budget`'s 600s).
- **Not acted on:** the edge-case hunter's note that Click's optional-flag-value
  parsing may consume a bare `--max-descent-slope` before an unrelated negative token
  starting with `-` — the value is invalid either way (must be positive), only the
  error class differs (a raw Click usage error instead of `BadCLIArgError`); fixing it
  cleanly isn't trivial and the practical impact is negligible for a personal tool.

## Design Notes

`--max-descent-slope`'s optional-value form uses Click's documented flag-value pattern:
`click.option("--max-descent-slope", is_flag=False, flag_value=0.4, default=None, type=click.FLOAT, ...)`.
`--name` alone yields `0.4`; `--name 0.6` yields `0.6`; omitted yields `None`. `is_flag=False`
keeps `params_schema.py`'s `_field_type` classifying it as `"float"`, not `"bool"`.

## Verification

**Commands:**
- `uv run basedpyright src/steeproute` -- expected: no new type errors
- `uv run pytest tests/unit` -- expected: all pass
- `uv run pytest tests/integration` -- expected: all pass (run separately per AGENTS.md)
- `uv run pytest tests/e2e` -- expected: all pass, no test taking materially longer than before
- `git grep -n "no-start-at-junction\|validate_setup_area\|_SETUP_MAX_RADIUS_KM"` -- expected: no matches outside historical story/doc files (and explanatory source comments naming the deleted symbols, e.g. `cli/setup.py`)

**Actually run (2026-07-28):** `basedpyright` 0 errors/warnings; `tests/unit` 897 passed; `tests/integration` 223 passed, 2 deselected; `tests/e2e` 111 passed, 5 deselected. The review loop's e2e-timing check caught a real gap the AC above was guarding against: `tests/e2e/test_coverage_check.py` builds its query argv independently of `tests/e2e/conftest.py::run_query` and had no baseline pin, so 3 of its tests silently jumped from a few seconds to ~90s each on the `--iter-budget`/`--stagnation-iters` bump before being fixed (see Spec Change Log) — full e2e suite went 570s → 331s after the fix, with the remaining slow tests independently confirmed unrelated to this change (DEM-unreachable doesn't invoke `query_cli` at all; parallel-workers pins its own small iter-budget; smoke tests are subprocess-spawn-bound).

## Suggested Review Order

**Query-CLI defaults & flag-shape changes**

- Entry point — every bumped default (`difficulty-cap`, `l-connector`, `elevation-deadband`, `j-max`, `n`, `workers`, `progress-interval`) plus the `--start-at-junction` single-flag collapse and `--max-descent-slope` optional-flag-value form.
  [`_shared.py:457`](../../src/steeproute/cli/_shared.py#L457)

- `--elevation-deadband`'s CLI default is a literal, deliberately decoupled from the pipeline-content-hashed constant — the reason this change causes zero cache re-keying.
  [`_shared.py:494`](../../src/steeproute/cli/_shared.py#L494)

- `DEFAULT_ITER_BUDGET` 2000 → 1,000,000 — the query CLI's own unset-flag fallback.
  [`query.py:109`](../../src/steeproute/cli/query.py#L109)

- `STAGNATION_ITERS_DEFAULT_PLACEHOLDER` 100 → 200,000 — the solver-layer twin of the above.
  [`grasp.py:145`](../../src/steeproute/solver/grasp.py#L145)

**Setup radius/dimension ceiling removed**

- The deletion's call site — `validate_setup_area(area)` is gone; a large `--radius`/`--width`/`--height` now proceeds.
  [`setup.py:138`](../../src/steeproute/cli/setup.py#L138)

- `_shared.py` no longer defines `_SETUP_MAX_RADIUS_KM`/`_SETUP_CEILING_DETAIL`/`validate_setup_area` at all (search the file — nothing to link to, it's gone).

**App-layer schema: two genuine divergences, six now-redundant overrides pruned**

- `_QUALITY_DEFAULTS` shrinks to the two fields the CLI still ships off (`start_at_junction`/`max_descent_slope`) — everything else now reads the plain CLI default.
  [`params_schema.py:80`](../../src/steeproute/app/cli_adapter/params_schema.py#L80)

- `_UNSET_FLAG_FALLBACKS` — why `iter_budget`/`stagnation_iters` need a different resolution mechanism than `param.default` (their click-level default stays `None`).
  [`params_schema.py:100`](../../src/steeproute/app/cli_adapter/params_schema.py#L100)

**Test-suite decoupling from CLI defaults (the load-bearing fix for e2e runtime)**

- `run_query`'s pinned pre-change baseline — the single fix insulating ~9 e2e test files.
  [`conftest.py:135`](../../tests/e2e/conftest.py#L135)

- The gap the review loop found: a second, independent query-argv builder that also needed the same pin.
  [`test_coverage_check.py:137`](../../tests/e2e/test_coverage_check.py#L137)

- `regression.py`'s `_PINNED_PARAMS` was missing `--workers` entirely — every golden was silently unpinned on this flag; now restored to the single-process path they were actually baked against.
  [`regression.py:145`](../../src/steeproute/regression.py#L145)

- The 7 regenerated goldens — `git diff` confirms only `params_hash` changed, routes are byte-identical.
  [`grenoble_small.json`](../../tests/e2e/goldens/grenoble_small.json)

**New regression coverage for the review loop's findings**

- Bare `--max-descent-slope` resolves to 0.4 end-to-end (was previously untested at the CLI level).
  [`test_journey_1_happy_path.py:55`](../../tests/e2e/test_journey_1_happy_path.py#L55)

- A large setup area is now accepted, not just "no longer rejected" in the abstract.
  [`test_coverage_check.py:367`](../../tests/e2e/test_coverage_check.py#L367)

- The removed `--no-start-at-junction` spelling now errors, mirroring the existing `--area-cap` removal test.
  [`test_cli_smoke.py:145`](../../tests/e2e/test_cli_smoke.py#L145)

**Docs: the risk this change deliberately reopens**

- PRD risk row: severity kept High on purpose, tied to the earlier human decision to accept the reopened risk.
  [`prd.md:450`](../../_bmad-output/planning-artifacts/prd.md#L450)

- `deferred-work.md`'s two most consequential reopened rows — the CLI-side-bypass framing that's now moot, and the concrete ~25 GB+ DEM-mosaic-memory risk at a large radius.
  [`deferred-work.md:107`](../../_bmad-output/implementation-artifacts/deferred-work.md#L107)
  [`deferred-work.md:247`](../../_bmad-output/implementation-artifacts/deferred-work.md#L247)

**Peripherals**

- `argv.py`, `AGENTS.md`, `README.md`, `architecture.md`, `future-ideas.md`, `test_app_argv.py`, `test_app_params_schema.py`, `test_area_parsing.py`, `test_cli_options.py`, `test_interrupt.py` — smaller precision fixes and doc-sync, all logged in the Spec Change Log above.
