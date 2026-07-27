---
title: 'Setup observability: honest OSM stage name + osmnx cache-hit visibility'
type: 'chore'
created: '2026-07-27'
baseline_commit: '753670a'
status: 'done'
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Setup prints `stage: osm-download: 169.76 s` even when it downloads nothing — on a warm
run (Overpass response already in osmnx's HTTP cache) nearly all of it is graph-building CPU, so the
name and its note ("one Overpass request; typically takes minutes") mislead. Nor can the user see
whether the response was cached: osmnx logs `Retrieved response from cache file '<path>'` at INFO,
but its `utils.log` never reaches steeproute's logging.

**Approach:** Two independent cheap fixes. (1) Rename the stage `osm-load` with a note naming both
halves, updating the App's `SETUP_STAGES` list and the tests/fixtures asserting the literal. (2) Let
osmnx's log records flow into the stdlib `logging` tree so `--verbose` surfaces them on stderr —
without osmnx printing to stdout or writing a log file.

## Boundaries & Constraints

**Always:** Keep stdout discipline — progress + run summary own stdout via `print`, diagnostics go to
stderr through `logging` (architecture §Cat 8). `SETUP_STAGES` stays 7 entries in pipeline order.
Stage names stay lowercase-kebab with no ` (` inside the name (the App parser splits the note off at
the first ` (`).

**Ask First:** Anything that changes what stdout carries, or needs osmnx private API / monkeypatching.

**Never:** Don't set `osmnx.settings.log_console = True` (prints to `sys.__stdout__`, bypassing
redirection, colliding with the run summary). Don't let osmnx create its `logs/` dir / FileHandler.
No fetch-vs-build *timing* split — that needs osmnx's lower-level calls and belongs to Story 16.4.
Don't rewrite historical traces under `planning-artifacts/research/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior |
|----------|--------------|---------------------------|
| Cache-miss setup, default verbosity | nothing cached | stdout `stage: osm-load (<note>) ...` / `stage: osm-load: <t> s`; no osmnx lines |
| Warm setup, `--verbose` | response cached | stderr carries osmnx's cache-hit line + its other INFO lines; stdout byte-identical to non-verbose |
| Warm setup, no `--verbose` | response cached | no osmnx output on either stream (root logger at WARNING) |
| Any setup run | — | no `logs/` dir created (osmnx FileHandler never instantiated) |
| App parses a pre-rename job log | stored lines say `osm-download` | still parses — position is tracked by incrementing, never by name lookup |

</frozen-after-approval>

## Code Map

- `src/steeproute/pipeline/__init__.py:190` -- the `seam.stage("osm-download", note=...)` call; sole
  producer of the name
- `src/steeproute/app/cli_adapter/progress_parse.py:35` -- `SETUP_STAGES`; supplies `stage_total`
  only (`_enter_stage` increments positionally), so the literal is documentation, not a lookup key
- `src/steeproute/cli/setup.py:258` -- `_configure_osmnx_cache`, called at `:140`; home for a sibling
  osmnx-logging config
- `src/steeproute/cli/_shared.py:36` -- `configure_cli_logging`: root `basicConfig(stream=stderr,
  level=DEBUG if verbose else WARNING, force=True)`, called at `setup.py:128`
- `.venv/.../osmnx/_utils.py` (read-only) -- `log()` writes into a stdlib logger named
  `settings.log_name` ("OSMnx") only when `settings.log_file` is True; `_get_logger` attaches its
  FileHandler and creates `logs/` **only if that logger has no handlers yet**
- Literal-asserting tests: `tests/e2e/test_steeproute_setup.py:180,195`,
  `tests/integration/test_app_sse.py:35,36,123`, `tests/unit/test_app_progress_parse.py:45-69`,
  `tests/unit/test_progress_helpers.py` (uses the name as an arbitrary sample — rename for consistency)
- Docs: `tests/fixtures/app_stdout/format-inventory.md:67,87,108`,
  `tests/benchmarks/test_setup_stages.py:14`, `epics.md:498` (Story 16.4's reference)

## Tasks & Acceptance

**Execution:**
- [x] `src/steeproute/pipeline/__init__.py` -- rename to `osm-load`, note naming both halves (e.g.
  `"Overpass fetch (cached responses reused) plus graph build"`); fix surrounding docstring/comment
  references
- [x] `src/steeproute/app/cli_adapter/progress_parse.py` -- update the `SETUP_STAGES` entry; length
  (7) unchanged
- [x] `src/steeproute/cli/setup.py` -- add osmnx-logging config beside `_configure_osmnx_cache`,
  called from the same place: attach a `logging.NullHandler()` to
  `logging.getLogger(osmnx.settings.log_name)` **first**, then set `osmnx.settings.log_file = True`;
  leave `log_console` False. Records propagate to the root logger already pointed at stderr, so
  `--verbose` shows them and the default run filters them at WARNING — no explicit verbose gate
- [x] the four literal-asserting test files -- update stage name and note text
- [x] `tests/unit/` (setup CLI) -- test that the config attaches a handler to the `OSMnx` logger,
  leaves `log_console` False, creates no `logs/` dir, and that an INFO record on that logger reaches
  stderr under `--verbose` but not without it
- [x] doc-sync the three doc sites; leave `planning-artifacts/research/**` untouched
- [x] `_bmad-output/planning-artifacts/future-ideas.md` -- mark the section done with the date, in the
  style of the existing "Done 2026-07-27" entry under `# Misc`

**Acceptance Criteria:**
- Given a cache-miss run, when it completes, then the timeline shows `osm-load` with a both-halves
  note and no other stage line changed.
- Given the offline suite, when it runs, then it passes and no `osm-download` literal remains in
  `src/` or `tests/`.

## Design Notes

Why the NullHandler trick and not a verbose gate: osmnx's `log()` has two sinks. `log_console` prints
to `sys.__stdout__` — unusable, it collides with the run summary e2e tests assert on. `log_file`
routes into a genuine `logging.Logger` named `"OSMnx"`, but `_get_logger` bolts a FileHandler on and
creates `./logs` *only* when that logger has no handlers yet. Pre-attaching a `NullHandler` satisfies
that check: we get the records, none of the file side-effects. `_get_logger` also skips its
`setLevel(DEBUG)` in that branch, so the logger stays NOTSET and inherits the root level — that
inheritance is the whole verbosity mechanism, so do **not** set a level on the `OSMnx` logger.

Two things landed differently than planned, both deliberate:

- **The pinned `setup_cache_miss.stdout.txt` capture keeps the old name.** It is a recording of a real
  Story 1.1 run, and rewriting it would fabricate history. Left byte-faithful, it doubles as the
  pre-rename compatibility case (`test_pre_rename_setup_capture_still_parses_positionally`), so the
  matrix's stored-log row is now pinned by a real test rather than an argument.
- **One `osm-download` literal survives on purpose:** the comment at
  `src/steeproute/pipeline/__init__.py:190` explaining *why* the stage isn't called that. The AC's
  "no literal remains" is met in spirit — no code, contract, or assertion carries the old name.

Consequence worth knowing: `compute_pipeline_content_hash` hashes raw `pipeline/**` source bytes, so
renaming the stage re-keys every prepared area. Existing entries stay readable by queries; the next
`steeproute-setup` for an area is a cache-miss and re-prepares once.

## Verification

**Commands:**
- `uv run basedpyright src/steeproute/cli/setup.py src/steeproute/pipeline/__init__.py src/steeproute/app/cli_adapter/progress_parse.py` -- expected: no new diagnostics
- `uv run pytest tests/unit`, then `tests/integration`, then `tests/e2e` -- expected: pass (per-directory, never mixed — AGENTS.md)
- `grep -rn "osm-download" src/ tests/` -- expected: no hits
- `uv run steeproute-setup --center 45.15,5.85 --radius 3 --verbose` on a warm cache -- expected: `stage: osm-load` on stdout, osmnx cache-hit line on stderr, no `logs/` dir; re-run without `--verbose`: no osmnx lines, stdout unchanged

## Suggested Review Order

**The honest stage name**

- Entry point: the renamed seam plus the comment justifying the name.
  [`pipeline/__init__.py:197`](../../src/steeproute/pipeline/__init__.py#L197)

- The App's stage list — documentation only; `stage_total` is all it feeds.
  [`progress_parse.py:36`](../../src/steeproute/app/cli_adapter/progress_parse.py#L36)

**Surfacing osmnx's cache-hit log**

- The whole mechanism: `NullHandler` first, then `log_file` — see docstring for why.
  [`setup.py:274`](../../src/steeproute/cli/setup.py#L274)

- Call site, beside the existing osmnx cache config; after `configure_cli_logging`.
  [`setup.py:142`](../../src/steeproute/cli/setup.py#L142)

**Tests worth reading**

- Version-drift canary: a real `osmnx.utils.log` call must produce no `logs/` dir.
  [`test_cli_setup.py:173`](../../tests/unit/test_cli_setup.py#L173)

- Pre-rename stored job logs still parse — renaming is not a wire break.
  [`test_app_progress_parse.py:126`](../../tests/unit/test_app_progress_parse.py#L126)

- Note text with nested parentheses still strips to the clean name.
  [`test_app_progress_parse.py:48`](../../tests/unit/test_app_progress_parse.py#L48)
