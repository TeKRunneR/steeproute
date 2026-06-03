# Story 4.1: Split θ — introduce --min-climb-slope and route-level semantics

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want `--theta` to mean the route-level average-slope floor and a new `--min-climb-slope` flag to carry the climb-detection threshold,
so that the two distinct concepts are independently configurable and `--theta` matches its documented (FR3) route-level intent.

## Acceptance Criteria

1. `SolverParams` (`models.py`) gains a `min_climb_slope: float` field, placed immediately after `theta`. Its class docstring distinguishes the two: `theta` = route-level average-slope floor `(D+ + D−)/length`; `min_climb_slope` = per-climb detection threshold (running-average `d_plus/length`). The documented parameter count is updated 12 → 13. **Every** `SolverParams(...)` construction site — production and tests — is updated to supply the new field.

2. A `--min-climb-slope` click option (`type=FLOAT`, `default=0.20`, `show_default=True`) is added in `cli/_shared.py`, stacked on the `steeproute` query command, and threaded through `cli/query.py` into both the `SolverParams(...)` constructor and the `detect_climbs(...)` call. `--theta`'s help is reworded to "Route-level average-slope floor, (D+ + D−)/length."

3. `validate_solver_options` validates `--min-climb-slope`: non-finite and negative values raise `BadCLIArgError` → exit 2, mirroring the existing `--theta` finiteness/`>= 0` checks (finiteness-then-range ordering preserved).

4. `detect_climbs`'s slope argument is renamed `theta → min_climb_slope` (public parameter, docstrings, and internal helper parameters renamed consistently). Detection logic is unchanged: at the default `0.20` the function returns byte-identical climbs to before.

5. **Scope boundary — no behavioral change at defaults.** Route-level enforcement is *not* introduced in this story. The per-super-edge `avg_gradient < theta` filter in `solver/grasp.py` and `validator.py`, and `validator._route_metrics`, stay exactly as-is (Story 4.2 owns those). `theta` continues to flow into `SolverParams` unchanged. With both flags at `0.20`, every output is numerically identical to pre-story behavior.

6. Tests are brought in line with the split:
   - The Story 3.2 climb-detection tests are re-pointed to `min_climb_slope` (the `detect_climbs` keyword and any `_THETA` references for that call); behavior assertions unchanged.
   - The query `--help` test asserts `--min-climb-slope` appears for `steeproute`.
   - A CLI/unit rejection test covers non-finite and negative `--min-climb-slope` → exit 2.
   - Any output/metadata test asserting the `SolverParams` field set is updated for the new field (it surfaces automatically via `asdict` — see Dev Notes).

7. All four CI gates green on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest`. No new deps. Coverage floors hold.

## Tasks / Subtasks

- [x] Task 1: Add `min_climb_slope: float` to `SolverParams` after `theta`; update the docstring (two-concept distinction) and the "12 parameters" count → 13. (AC: #1)
  - [x] Update every `SolverParams(...)` construction site (see Dev Notes "Field-addition fan-out" — 11 files).
- [x] Task 2: Add `min_climb_slope_option` in `cli/_shared.py`; reword `theta_option` help; add `min_climb_slope` to `validate_solver_options` (finiteness loop + `>= 0` range check). (AC: #2, #3)
- [x] Task 3: Stack `--min-climb-slope` on the query command in `cli/query.py`; thread it into `SolverParams` and into the `detect_climbs(...)` call (which now takes `min_climb_slope=...`, not `theta=...`). (AC: #2)
- [x] Task 4: Rename `detect_climbs`'s slope arg `theta → min_climb_slope` in `pipeline/climbs.py` (public param, docstrings, `_qualifies_as_seed` / `_pick_steepest_extension` params); logic untouched. (AC: #4)
- [x] Task 5: Re-point Story 3.2 climb-detection tests; add `--min-climb-slope` to the `--help` flag assertion; add the non-finite/negative rejection case; update any param-set/metadata assertion. (AC: #6)
- [x] Task 6: Run all four gates on Windows; confirm no behavioral drift at defaults. (AC: #5, #7)

## Dev Notes

- **This is a pure parameter-split + plumbing story, not a semantics change.** The whole point is that `--theta 0.20 --min-climb-slope 0.20` reproduces today's output exactly. The binding route-level constraint, the solver finalization gate, the validator route-level check, and the `_route_metrics` fix all land in **Story 4.2** — do not pull them forward. Reviewers should treat any output diff at defaults as a regression.
- **Field-addition fan-out (the #1 disaster to prevent).** `SolverParams` is `@dataclass(frozen=True, slots=True)` with required (non-default) fields, so adding `min_climb_slope` breaks **every** construction site at import/collection time. All known sites use keyword args, so positional ordering is safe, but each must add the field. Sites: `src/steeproute/cli/query.py`; `tests/integration/conftest.py` (`make_toy_solver_params` factory, ~`:236`); and direct constructions in `tests/unit/test_models.py`, `tests/unit/test_output.py`, `tests/unit/test_validator.py`, `tests/unit/test_grasp_construction.py`, `tests/integration/test_output_on_fixture.py`, `tests/integration/test_validator_on_fixture.py`, `tests/integration/test_grasp_on_fixture.py`, `tests/integration/test_grasp_reproducible.py`, `tests/integration/test_oracle_correctness.py`. Grep `SolverParams(` to confirm the full set before finishing.
- **The field auto-surfaces in report metadata.** `output._build_metadata` does `asdict(params)` (`output.py:180`), so `min_climb_slope` appears in every HTML/JSON metadata block with zero renderer changes — but any test asserting the exact `params` dict (likely in `tests/unit/test_output.py` / `test_output_on_fixture.py`) will break and must be updated. The architecture's "SolverParams = 12 parameters" / metadata-list count sync is **Story 4.3** (A8); here just fix the code docstring count.
- **`detect_climbs` rename is mechanical.** The arg drives `_qualifies_as_seed(data, theta)` and `_choose_next_edge(..., theta, ...)` and the inline `new_avg < theta` comparison (`climbs.py:172,197,232,248,276`). Rename the parameter throughout for clarity; the comparison logic is identical. Default `0.20` ⇒ identical seeds/continuations ⇒ identical climbs.
- **`validate_solver_options` ordering.** Add `("--min-climb-slope", min_climb_slope)` to the finiteness loop (`_shared.py:187-194`) so `nan`/`inf` are reported as non-finite, then a separate `if min_climb_slope < 0.0:` range check — same shape as the existing `--theta` pair. Add `min_climb_slope` to the function's keyword-only signature and pass it from `cli/query.py`.
- **Docs are already synced for this story's surface.** PRD FR3 (reworded), FR3b (new climb-detection flag), and the Config Schema `--min-climb-slope` row were applied during correct-course (`sprint-change-proposal-2026-06-03.md`). Do **not** re-edit the PRD. Architecture stage-8 / constraint-table / metadata / SolverParams-count sync is Story 4.3.
- **Note the legacy `θ` semantics still active after this story.** Because 4.2 hasn't run yet, `params.theta` is still consumed only by the near-vacuous per-super-edge check. That's expected and correct for 4.1 — the story deliberately leaves the (eventually-removed) check in place so the diff stays scoped to the parameter split.

### Project Structure Notes

- **Modify:** `src/steeproute/models.py` (`SolverParams`), `src/steeproute/cli/_shared.py` (new option + `validate_solver_options`), `src/steeproute/cli/query.py` (stack option, thread into params + `detect_climbs`), `src/steeproute/pipeline/climbs.py` (arg rename).
- **Modify tests:** `tests/unit/test_climb_detection.py` (re-point), `tests/unit/test_cli_help.py` (flag list), `tests/unit/test_cli_options.py` (rejection case), plus every `SolverParams(...)` site above.
- **Reuse, do not reinvent:** the `theta_option` / `validate_setup_radius` patterns in `_shared.py` are the exact templates for the new option and its finiteness check — follow them verbatim for help-text, `show_default`, and `BadCLIArgError` wording style. Flag stacking and kwarg threading in `cli/query.py` mirror the existing `--theta` wiring line-for-line.

### Testing standards summary

- Unit tests in `tests/unit/`, integration in `tests/integration/`, e2e in `tests/e2e/`; naming `test_<unit>_<scenario>` (Architecture §"Test organization").
- CLI help/option tests run in-process via `click.testing.CliRunner` (see `tests/unit/test_cli_help.py`, `tests/unit/test_cli_options.py`).
- No `pytest.skip`/`xfail`; no new deps. The pure-logic coverage floors (`climbs.py` etc.) must hold — the rename adds no new branches.
- A focused regression check: run the existing Story 3.2 climb tests *before* and *after* the rename and confirm identical pass set (proves no behavioral drift at defaults).

### References

- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-03.md §4B/4C, §5](../planning-artifacts/sprint-change-proposal-2026-06-03.md) — the canonical handoff: B1–B4 (code) + C/3.2, C/1.5 (tests) are Story 4.1's exact scope; B5–B7 are 4.2
- [Source: _bmad-output/planning-artifacts/epics.md §"Story 4.1"](../planning-artifacts/epics.md) — BDD acceptance criteria, compatibility note (defaults preserve numeric behavior)
- [Source: _bmad-output/planning-artifacts/prd.md §FR3, §FR3b, §Config Schema](../planning-artifacts/prd.md) — reworded route-level FR3 + new climb-detection FR3b + `--min-climb-slope` schema row (already applied)
- [Source: src/steeproute/models.py:124-163](../../src/steeproute/models.py) — `SolverParams` field list + docstring to extend
- [Source: src/steeproute/cli/_shared.py:164-208,248-254](../../src/steeproute/cli/_shared.py) — `validate_solver_options` + `theta_option` (templates for the new option + check)
- [Source: src/steeproute/cli/query.py:88-200](../../src/steeproute/cli/query.py) — option stack, `SolverParams(...)` construction, `detect_climbs(...)` call to update
- [Source: src/steeproute/pipeline/climbs.py:120-278](../../src/steeproute/pipeline/climbs.py) — `detect_climbs` + helpers; `theta` arg to rename
- [Source: src/steeproute/output.py:179-190](../../src/steeproute/output.py) — `asdict(params)` auto-surfaces the new field in report metadata

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps.

**Final gate pass (all green):**

```
uv run ruff format            → 2 files reformatted (wrapped lengthened detect_climbs calls), rest clean
uv run ruff check             → All checks passed!
uv run basedpyright           → 0 errors, 0 warnings, 0 notes
uv run pytest -q              → 653 passed, 1 deselected in ~117 s (was 608 baseline; +45 from
                                parametrized flag/rejection cases across unit + e2e layers)
--cov on changed modules      → models.py 100%, climbs.py 98%, _shared.py 98%, query.py 97%
                                (all newly-added lines covered; the few uncovered lines are
                                pre-existing — climbs.py:90/95, _shared.py:149/153/512, query.py:274-275)
```

### Completion Notes List

**Pure parameter-split, no behavioral change at defaults (AC #5 honored).** Route-level enforcement was deliberately NOT pulled forward — the per-super-edge `avg_gradient < theta` filter in `grasp.py`/`validator.py` and `_route_metrics` are untouched (Story 4.2 owns those). With both flags at 0.20 every output is numerically identical; the full pre-existing fixture/metamorphic/oracle suite passes unchanged, which is the regression proof for "no drift."

**Field-addition fan-out was wider than the story's 11-site estimate.** Two additional sites surfaced once `validate_solver_options` gained a required `min_climb_slope` kwarg and the `detect_climbs` slope arg was renamed:
- `tests/unit/test_area_parsing.py::_check_solver_options` — the wrapper around `validate_solver_options` needed the new kwarg (with an in-range default), plus three new rejection cases (nan/inf/negative → `--min-climb-slope`).
- `tests/integration/test_metamorphic.py` — covered transitively: its `_params(...)` delegates to `conftest.make_toy_solver_params`, so updating the conftest factory fixed it with no direct edit.
- Every `detect_climbs(theta=…)` call site (7 test files, ~26 calls) had to swap the keyword to `min_climb_slope=` because the rename is a hard rename, not an alias.

**`--theta` help uses ASCII `(D+ + D-)/length`, not the Unicode `(D+ + D−)/length`** the PRD wording shows. Click renders `--help` to the terminal's stdout encoding; on a Windows console (cp1252) the U+2212 minus sign would raise `UnicodeEncodeError`. ASCII hyphen-minus is console-safe and reads identically. Same reasoning kept the help string single-line and short.

**`_THETA` renamed to `_MIN_CLIMB_SLOPE` in the pure-detection tests** — `tests/unit/test_climb_detection.py`, `tests/integration/test_climb_detection_fixture.py`, and `tests/integration/test_graph_contraction_fixture.py` (none of which construct `SolverParams`) — to document the re-point clearly. In the genuinely-mixed fixtures (`test_grasp_on_fixture.py`, `test_grasp_reproducible.py`, `test_output_on_fixture.py`, `test_validator_on_fixture.py`) the local `_THETA = 0.20` constant still feeds the route-level `theta` field as well, so it was left as-is and only the `detect_climbs` keyword was swapped — keeps the constant's dual meaning honest at the default.

**The new field auto-surfaces in report metadata** via `output._build_metadata`'s `asdict(params)` — confirmed by giving `min_climb_slope` a distinct value (0.19) in `test_output.py` and asserting that string appears in both HTML and JSON. No renderer change was needed.

**AC walkthrough:**
1. AC #1 — `min_climb_slope` added after `theta`; docstring distinguishes FR3 vs FR3b; count 12→13; all construction sites updated. ✅
2. AC #2 — `--min-climb-slope` (default 0.20) added, stacked, threaded into `SolverParams` + `detect_climbs`; `--theta` help reworded. ✅
3. AC #3 — non-finite/negative `--min-climb-slope` → `BadCLIArgError` exit 2 (finiteness-then-range ordering). ✅
4. AC #4 — `detect_climbs` slope arg renamed; logic identical; climb tests pass byte-for-byte at 0.20. ✅
5. AC #5 — no route-level enforcement added; per-super-edge check + `_route_metrics` unchanged; full suite confirms zero drift. ✅
6. AC #6 — 3.2 tests re-pointed; `--help` lists the flag (unit + e2e); rejection cases added; metadata assertion updated. ✅
7. AC #7 — ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 653 passed ✅; no new deps; coverage holds. ✅

### File List

**Modified (source):**
- `src/steeproute/models.py` — `SolverParams` gains `min_climb_slope: float` (after `theta`); docstring distinguishes FR3/FR3b; count 12→13.
- `src/steeproute/cli/_shared.py` — new `min_climb_slope_option`; `theta_option` help reworded; `validate_solver_options` gains the `min_climb_slope` kwarg, finiteness check, and `>= 0` range check.
- `src/steeproute/cli/query.py` — import + stack `--min-climb-slope`; thread into `validate_solver_options`, `SolverParams`, and `detect_climbs`.
- `src/steeproute/pipeline/climbs.py` — `detect_climbs` slope arg + helpers renamed `theta → min_climb_slope`; docstrings updated. Logic unchanged.

**Modified (tests):**
- `tests/unit/test_models.py` — factory + round-trip assert for `min_climb_slope`; docstring 12→13.
- `tests/unit/test_output.py` — `_PARAMS` field (0.19) + expected-metadata-string assertion.
- `tests/unit/test_area_parsing.py` — `_check_solver_options` wrapper kwarg + 3 rejection cases.
- `tests/unit/test_cli_help.py` — `--min-climb-slope` added to `QUERY_FLAGS` / `QUERY_ONLY_FLAGS`.
- `tests/unit/test_climb_detection.py` — `_THETA → _MIN_CLIMB_SLOPE`; `detect_climbs` keyword swap.
- `tests/unit/test_grasp_construction.py`, `tests/unit/test_validator.py` — `SolverParams` field added.
- `tests/integration/conftest.py` — `make_toy_solver_params` field added (covers `test_metamorphic` transitively).
- `tests/integration/test_grasp_on_fixture.py`, `test_grasp_reproducible.py`, `test_oracle_correctness.py`, `test_output_on_fixture.py`, `test_validator_on_fixture.py` — `SolverParams` field added + `detect_climbs` keyword swap where present.
- `tests/integration/test_climb_detection_fixture.py`, `test_graph_contraction_fixture.py` — `_THETA → _MIN_CLIMB_SLOPE` (pure-detection) + `detect_climbs` keyword swap.
- `tests/e2e/test_cli_smoke.py` — `--min-climb-slope` in `QUERY_FLAGS` + negative-value exit-2 smoke test.

**Modified (tracking):**
- `_bmad-output/implementation-artifacts/4-1-split-theta-introduce-min-climb-slope-and-route-level-semantics.md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked to `review`; `last_updated`.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-03 | Yann (Claude Opus 4.8) | Lightweight diff review (same-session, in lieu of `bmad-code-review`). No correctness issues. 2 consistency nits fixed: stale `θ` comment in `test_climb_detection.py`; `_THETA → _MIN_CLIMB_SLOPE` rename completed in the two pure-detection fixtures (`test_climb_detection_fixture.py`, `test_graph_contraction_fixture.py`) that construct no `SolverParams`. Completion note corrected accordingly. Re-validated: ruff ✅, basedpyright 0/0/0 ✅, renamed detection tests 23 passed. Status review → done. | _pending_ |
| 2026-06-03 | Yann (Claude Opus 4.8) | Story 4.1 implemented: split `θ` into route-level `--theta` (semantics unchanged in code this story) and a new per-climb `--min-climb-slope` detection flag (FR3b). Added `SolverParams.min_climb_slope` (count 12→13), `min_climb_slope_option` + finiteness/`>=0` validation in `cli/_shared.py`, threaded it through `cli/query.py` into `SolverParams` and `detect_climbs`, and renamed `detect_climbs`'s slope arg `theta → min_climb_slope`. No route-level enforcement (deferred to 4.2); behavior numerically identical at default 0.20/0.20. Re-pointed all `detect_climbs` call sites + 12 `SolverParams` construction sites; added `--help` (unit+e2e) and rejection-path coverage. All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 653 passed. No new deps. Status → review. | _pending_ |
