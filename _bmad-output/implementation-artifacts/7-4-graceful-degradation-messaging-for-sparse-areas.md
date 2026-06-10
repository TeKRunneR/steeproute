# Story 7.4: Graceful degradation messaging for sparse areas

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want `steeproute` to return fewer than N routes with a clear explanation when distinctness under the current `--j-max` cannot be satisfied, instead of silently loosening the constraint,
so that Journey 2's sparse-area experience is explicit and preserves my J_max intent.

## Acceptance Criteria

1. **Degradation detected from the returned set.** When a completed solve returns fewer routes than requested (`len(validated.routes) < params.n`), `cli/query.py::main` constructs a degradation explanation naming the observed count and the J_max value. The detection is on the count of routes in the validated set (passed + failed alike, as `_exit_code_for` already treats them), not on the passing subset.

2. **Exact message format.** The explanation matches this literal template (labels stable so tests regex-match): `Only {X} distinct routes satisfy J_max <= {j_max:.2f}. Returning {X} routes; additional candidates would exceed the overlap threshold.` where `{X}` is the returned-route count and `{j_max:.2f}` is the run's `--j-max` formatted to two decimals (e.g. `0.30`). Plain ASCII (`<=`), so it survives a redirected stdout on any platform with no stream reconfiguring; in HTML the `<` is autoescaped to `&lt;` (renders as `<=`).

3. **Surfaced on stdout.** When the run is degraded, the explanation is printed to stdout as a single line. (Story 7.5's run summary will later absorb this into its `degradation:` field; until then this story prints it directly so the outcome is visible.) A non-degraded run prints no degradation line.

4. **Surfaced in every report's metadata.** The explanation flows through `output.render(...)` → `_build_metadata` into each emitted report's metadata block (HTML metadata table + JSON sidecar, mirrored per §Cat 9), so a user reading a single report sees it was part of a degraded set. A non-degraded run carries no degradation field/row (or an explicit empty value) — it must not render a stray "degradation" row when N routes were returned.

5. **Graceful degradation is not an error.** A degraded run whose returned routes all pass validation exits **0** — degradation is a normal outcome, not a failure (§Cat 6c is unchanged; degradation never flips the exit code on its own).

6. **Interrupted runs are not labelled degraded.** A `KeyboardInterrupt` partial flush (Story 7.3) that happens to carry fewer than N routes is explained by `convergence_status: "interrupted"`, not by sparsity — the interrupt path renders with **no** degradation message. Degradation messaging applies only to a naturally-terminated solve (`converged` / `budget-exhausted`).

7. **Tests.** `tests/e2e/test_degradation.py` runs the query in-process (CliRunner, per the existing Journey-1/2 e2e pattern) on a narrow query within the Grenoble fixture under `--j-max 0.30`, crafted to yield only 2–3 distinct routes, and asserts: fewer than N reports emitted; stdout contains a line matching the AC #2 pattern; exit code 0; the explanation appears in each emitted report's metadata (HTML + JSON). A companion `test_relaxed_jmax_produces_more_routes` runs the same query with `--j-max 0.50` on the same seeded cache and asserts more routes returned and a preprocessing cache-hit (fast re-run) — exercising Journey 2's tuning loop. `tests/unit/test_output.py` is extended to assert the new metadata field renders when present and is absent/empty when not.

## Tasks / Subtasks

- [x] Thread an optional degradation explanation through the output layer (AC: #4)
  - [x] `output.render` + `_build_metadata` accept `degradation: str | None`; add it to the metadata dict next to `convergence_status` / `convergence_iteration`
  - [x] Add a conditional metadata-table row in `templates/route.html.j2` (rendered only when the value is set), mirroring the `convergence` row
- [x] Detect degradation and construct the message in `cli/query.py` (AC: #1, #2, #6)
  - [x] Add a pure `_degradation_message(validated, params) -> str | None` helper returning the AC #2 string when `len(validated.routes) < params.n`, else `None`
  - [x] Compute it inside `_validate_and_render` for non-interrupted statuses only; pass `None` on the interrupt path; thread the value into the `output.render` call
- [x] Surface the message on stdout for the normal path (AC: #3, #5)
  - [x] Return the degradation string from `_validate_and_render` and print it (if any) after the render returns, before the validation-driven `ctx.exit(...)`; confirm exit code stays 0 for an all-passing degraded set
- [x] Tests (AC: #7)
  - [x] `tests/e2e/test_degradation.py` — degraded path (stdout line + metadata in HTML/JSON + exit 0) and `test_relaxed_jmax_produces_more_routes` (more routes + cache-hit re-run)
  - [x] Extend `tests/unit/test_output.py` metadata assertions for the degradation field (present-when-set, absent-when-None)

## Dev Notes

- **Mirror the `convergence_iteration` plumbing from Story 7.3 exactly.** That field is the template for this one: a new optional argument added to `render`/`_build_metadata` ([output.py:56-95](src/steeproute/output.py:56), [output.py:173-204](src/steeproute/output.py:173)), one entry in the metadata dict, one `<tr>` in the template ([route.html.j2:73-74](src/steeproute/templates/route.html.j2:73)). The difference is this field is `str | None` and the row/JSON value should be conditional — don't show a "degradation" row on a healthy N-route run (AC #4). Put the conditional in the template (`{% if metadata.degradation %}`) so the JSON dict can still carry `None` uniformly.
- **Single-source through `_validate_and_render`.** Story 7.3 collapsed the validate→render pair into the `_validate_and_render` closure ([query.py:257-283](src/steeproute/cli/query.py:257)) shared by the normal and interrupt paths. Compute degradation *inside* it (it already has `validated_set` and the run-wide `params`), gating on `status != "interrupted"` (AC #6). This keeps the one-place-to-change `output.render` call shape (FR28) and prevents the interrupt path from ever emitting a degradation message.
- **Detection uses `len(validated.routes)`, the same set `_exit_code_for` reads.** `validate()` wraps the solver's `solutions` 1:1, and `TopNTracker` admits at most `n` ([models.py:308-319](src/steeproute/models.py:308)); a sparse area where distinctness blocks admission naturally returns `< n`. The zero-route degenerate case (`len == 0`) is just the extreme of the same path — message reads "Only 0 distinct routes…", exit 0 — no special-casing.
- **Exit code is untouched.** Degradation must not flip the code: `_exit_code_for` ([query.py:381-385](src/steeproute/cli/query.py:381)) stays validation-driven, so an all-passing degraded set exits 0 (AC #5). Only print the degradation line; don't route it through the exit logic. Architecture confirms degradation is "_not_ an exception … normal outcome" (§"What's not an exception").
- **The stdout line is interim, owned by Story 7.5.** FR12's home is the run summary (Story 7.5's `degradation:` field, §Cat 8 "final summary always on stdout"). 7.5 isn't built yet, so this story prints the line itself to make the outcome visible and let `test_degradation.py` pass now. Keep it a plain `print(...)` to stdout (never `logging`, which §Cat 8 binds to stderr) and expect 7.5 to fold it into the summary block. Don't build the summary block here (out of scope — Story 7.5).
- **Windows console encoding — verify, don't assume.** The message contains `≤` (U+2264). The e2e tests run in-process via `CliRunner`, which captures stdout to an in-memory text buffer, so they won't exercise a real Windows console (cp1252) — they'll pass regardless. But a real `steeproute` run in a Windows terminal could raise `UnicodeEncodeError` or mangle the glyph on stdout. HTML/JSON are written UTF-8 already (unaffected). Before closing, verify the glyph survives a real-console run; if not, reconfigure stdout to UTF-8 at the entry point (`sys.stdout.reconfigure(encoding="utf-8")` in `main`/`run_entry_point`) rather than dropping the `≤` (AC #2 pins it). This is the same "verify the real-OS behavior" posture Story 7.3 took for Ctrl-C delivery.
- **Test it in-process, not via subprocess.** Unlike 7.3 (which needed an OS signal), nothing here requires a real process — use the `seeded_cache` + `run_query` fixtures ([tests/e2e/conftest.py:86-153](tests/e2e/conftest.py:86)) like the other Journey tests. `run_query(..., extra_args=["--j-max", "0.30", "--radius", "<narrow>"])` drives the degraded case; re-invoke with `--j-max 0.50` on the same `seeded_cache` for the relaxed case. The cache-hit assertion checks the `cache-hit cache_key_hash:` stdout cue ([query.py:194](src/steeproute/cli/query.py:194)) is present on the re-run.
- **Crafting the sparse fixture query.** The fixture is small; tune the query `--radius` (and, if needed, `--n`) so the solver genuinely returns 2–3 distinct routes under `--j-max 0.30` — a tight J_max on a narrow cutout. Pin the chosen radius/n in the test with a comment, the way the other e2e tests pin their knobs; if the committed fixture can't be coaxed below N under any reasonable radius, save it as a question rather than loosening the assertion.
- **FR29 unaffected.** The degradation message derives only from the deterministic returned-route count and `params.j_max`; it never feeds the RNG or construction.

### Project Structure Notes

- Files touched: `src/steeproute/output.py` (`render`/`_build_metadata` signature + metadata field), `src/steeproute/templates/route.html.j2` (conditional metadata row), `src/steeproute/cli/query.py` (`_degradation_message` helper + thread through `_validate_and_render` + stdout print). New: `tests/e2e/test_degradation.py`. Modified: `tests/unit/test_output.py` (+ any non-CLI `render` callers — `tests/integration/test_output_on_fixture.py` — if the new arg is positional; prefer a keyword/defaulted arg to avoid the call-site fan-out 7.3 hit).
- No new CLI flags, models, or solver changes — TopNTracker already returns `< n` under distinctness pressure (the BDD's "Given"). This story is messaging + plumbing only.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 7.4] — degradation ACs and BDD scenarios (lines 892-904).
- [Source: _bmad-output/planning-artifacts/prd.md] — FR12 (graceful degradation, fewer than N with explanation), Journey 2 (lines 172-183, sparse-area + iterative re-query), FR11 (distinctness definition).
- [Source: _bmad-output/planning-artifacts/architecture.md] — degradation is a normal outcome, not an exception (§"What's not an exception", ~line 676); run summary carries the explanation (§Cat 8, line 573); FR12 maps to `solver/distinctness.py` + `cli/query.py` summary (line 887); §Cat 9 HTML+JSON parallel metadata.
- [Source: src/steeproute/cli/query.py:257-339] — `_validate_and_render` closure, interrupt path, normal render + exit-code call (degradation wiring sites); [query.py:381-385] `_exit_code_for`.
- [Source: src/steeproute/output.py:56-95, 173-204] — `render`/`_build_metadata` signatures + metadata dict (add `degradation`).
- [Source: src/steeproute/templates/route.html.j2:71-84] — run-metadata table (`convergence` / `convergence_iteration` rows to mirror conditionally).
- [Source: src/steeproute/models.py:208, 308-319] — `SolverParams.n`, `ValidatedRouteSet.routes` (the degradation count source).
- [Source: tests/e2e/conftest.py:86-164] — `seeded_cache`, `run_query`, `fixture_query_target` fixtures for the in-process tests.
- [Source: 7-3-interrupt-handling-with-best-so-far-preservation.md] — the `convergence_iteration` metadata-plumbing pattern this story mirrors; the `_validate_and_render` single-sourcing it extends.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- **Stdout encoding — kept the message ASCII (review decision).** A first cut used `≤` (U+2264) and added `_ensure_utf8_stdout()` to `run_entry_point`, because a *redirected* stdout on Windows uses cp1252 and raises `UnicodeEncodeError` on that glyph (probed and confirmed). Review feedback: for a personal CLI the fancy glyph isn't worth a stream-reconfigure on a shared entry point — use `<=`. Switched the message to plain ASCII and removed `_ensure_utf8_stdout` entirely (this also dissolved a latent bug in it — an unguarded `sys.stdout.encoding` read that could `AttributeError` at startup). HTML autoescapes the `<` to `&lt;` (renders as `<=`); stdout and the JSON sidecar carry raw `<=`.
- **Degradation regime on the committed fixture (probed, not assumed).** The story estimated "2–3 distinct routes under `--j-max 0.30`", but `grenoble_small` is dense: at default params it returns the full N, and distinctness never binds even at `--j-max 0.05` / `--n 20` (and `--radius` doesn't shrink the *solve* graph — it only drives the FR24 coverage check + bbox overlay). Distinctness becomes the binding constraint only when feasibility is first tightened. Swept `--theta` × `--min-climb-slope` × `--j-max`: at `--theta 0.35` (min-climb-slope/j-max/n left at defaults), `--j-max 0.30` → 4 routes and `--j-max 0.50` → 5 routes — i.e. j-max binds and relaxing it admits more, exactly the Journey-2 loop. Chose this regime; the degraded count is 4 (a genuine `<N=5` degradation) rather than the story's estimated 2–3. Tests assert inequalities (`<N`, relaxed `>` tight), not exact counts, so they track the binding behavior rather than a GRASP tipping-point number.

### Completion Notes List

- **`output.py`** — `render` and `_build_metadata` take `degradation: str | None`; `render`'s is a trailing keyword-defaulted arg (`= None`), so the two non-CLI callers (`tests/unit/test_output.py`, `tests/integration/test_output_on_fixture.py`) needed no positional change — only the CLI passes it. Emitted in the metadata dict next to `convergence_status`/`convergence_iteration`, so HTML and JSON mirror it (§Cat 9).
- **`templates/route.html.j2`** — a `degradation` metadata row guarded by `{% if metadata.degradation %}`, so a healthy N-route run renders no stray row while the JSON still carries `null` uniformly (AC #4).
- **`cli/query.py`** — added pure `_degradation_message(validated, params)` returning the AC #2 string when `len(validated.routes) < params.n` else `None` (count is the same set `_exit_code_for` reads; `len == 0` is the degenerate extreme, no special-casing). `_validate_and_render` computes it once (gated on `status != "interrupted"`), threads it into `output.render`, and **returns it** so the normal path's stdout `print` reuses the exact same string — single-sourced, no recompute or drift (review fix). An interrupted partial flush is explained by `convergence_status` and never mislabelled as sparse (AC #6); degradation never touches the validation-driven exit code (AC #5).
- **Validation:** full suite **758 passed / 2 deselected** (was 754 at 7.3 close-out; +4 = 2 e2e degradation tests + 2 unit metadata tests). `ruff check` + `ruff format` clean; basedpyright 0/0/0 on all touched source files. FR29 preserved — the message derives only from the deterministic returned-route count and `params.j_max`; it never feeds the RNG or construction.

### File List

- `src/steeproute/output.py` (modified — `render`/`_build_metadata` take + emit `degradation`)
- `src/steeproute/templates/route.html.j2` (modified — conditional `degradation` metadata row)
- `src/steeproute/cli/query.py` (modified — `_degradation_message` helper; `_validate_and_render` computes + returns it; stdout print on the normal path)
- `tests/e2e/test_degradation.py` (new — degraded path + relaxed-j-max re-query / cache-hit)
- `tests/unit/test_output.py` (modified — `degradation` arg in `_render`; present-when-set / absent-when-None metadata tests)

## Change Log

- 2026-06-09: Implemented Story 7.4 — graceful degradation messaging (FR12). `cli/query.py` detects `len(validated.routes) < n`, builds the `Only X distinct routes satisfy J_max <= …` explanation, prints it to stdout (normal path) and threads it into every report's metadata (HTML row + JSON), gated off the interrupt path; degradation stays exit 0. Full suite 758 passed; lint/format/types clean. Status → review.
- 2026-06-09: Applied lightweight-review fixes — single-sourced the degradation message (now computed once in `_validate_and_render` and returned for the stdout print, removing the duplicate computation) and switched the message from `≤` to plain ASCII `<=`, which let `_ensure_utf8_stdout` and its shared-entry-point reconfigure be deleted entirely (also dissolving a latent unguarded-`encoding` read in it). Tests updated for HTML autoescaping of `<`. Suite green; lint/format/types clean.
- 2026-06-09: Close-out — full suite 758 passed / 2 deselected, lint/format/types clean. Status → done.
