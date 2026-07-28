# Spec — comment remediation pass

**Status:** done (2026-07-29), `fix/comment-audit-s1`. Prerequisite work (16
wrong/dead comments) landed earlier on the same branch (`aa88d26`).

Every file in `src/` and `tests/` was read. Story/epic references are at **0** in
both trees, as are the `AC #N` / `Task N` / `Review P<n>` / `T2`/`D1`/`D2`/`S3`/
`Q4`/`DEF2` pointers and every banner comment. See §Outcome for what the prose
share did and did not do.

## The problem

Every line of this repo was written by AI agents. Somewhere early they began
writing comments in the voice of the change rather than the voice of the file,
and citing BMAD story IDs inline. Later agents read that as house style and
reproduced it. The result compounds: **40% of `src/` is comment or docstring**
(5,227 of 13,100 lines), heavy with change-narration and 356 prose lines naming
a story or epic.

This is not a defect hunt. The volume and the voice *are* the defect. The goal
is to rewrite the comment layer so the pattern stops reproducing — including in
future agent sessions, which will imitate whatever they find.

## Method — read every line

A previous session tried to shortcut this with pattern greps and it does not
work. Recorded so it is not retried:

- **Diff-voice is semantic, not lexical.** A keyword regex (`no longer`,
  `previously`, `the old`, …) has unmeasured precision — `"Decimal places used
  to round Area fields"` and `"the entry is now valid"` both match and neither
  is diff-voice — and bad recall on what matters.
- **The single largest pattern has no marker words at all.** Five module
  docstrings are written as release changelogs ("Story 2.10 wired… Story 3.11
  wires… Story 7.1 wires…"). Present tense, zero regex hits. Found only by
  reading.
- Greps *are* reliable for structural facts: story-ID counts, banner comments,
  commented-out code, doc-anchor resolution, density. Use them for the baseline
  and the progress metric, never for deciding what a comment is.

So: open each file, read each comment against the code it sits on, rewrite.

## Rules

**Voice.** Write for someone opening the file cold, with no knowledge of the
change that introduced the line. If a sentence only makes sense to the reviewer
of a diff, it is wrong. No "now", "no longer", "the old X", "as of Story N",
"before this", "byte-identical to pre-Epic-N".

**No story/epic IDs in code.** They belong in commit messages, which are
permanently correct because they are pinned to a moment. In code they rot:
epics here get renumbered (`sprint-status.yaml:102,110`), and the App and CLI
tracks both have a "Story 5.1". Four spellings are currently in use (`Story N`,
`App Story N`, `Story app-N`, `Story-N`). Same for review-thread references
("review finding #10", "Story 10.1 review #5") and opaque codes (`T2`, `D1`,
`D2`, `S3`, `Q4`, `DEF2`).

**Doc references.** Only to current-truth docs (`architecture.md`,
`architecture-app.md`, `prd.md`), by relative path or stable section label,
never line numbers. Never to point-in-time records (story files,
implementation-artifacts, sprint-change proposals, retros). The comment must
still stand alone if the doc vanishes. The existing `Architecture §Cat 4b`
scheme works — 142 uses, all resolving — keep it.

**Keep** (these earn their length; do not sweep them):
- refuted alternatives with a measurement — "don't go back to X, it cost 7.4 s at r20"
- non-local invariants — "must stay in sync with X", "callers must not reorder"
- units, coordinate frames, axis order, valid ranges, mutation/ownership
- deliberate deviations from local convention, with the reason
- empirical numbers *with provenance* (area + date, not a story ID)
- external-boundary knowledge (osmnx/rasterio/click behaviour that isn't guessable)

**Cut**: restatement of the next line; docstrings paraphrasing the signature;
banner comments (11 in `.py`, 5 in `app.css`); changelog narration; snapshot
framing ("Only setup jobs are exercised in Story 1.3"); duplicated passages.

These rules are written for `src`. Test files add one more, and it is the most
important one in the repo — see §Tests.

**Target.** No percentage quota — cutting to a number would destroy the good
comments. Cut history and duplication and the number falls out; expect low 30s
for `src`. Reference band, same methodology: osmnx 42%, pyproj 39%, networkx
32%, rasterio 30%, numpy 25%, pydantic 23%.

## Baseline (post-`aa88d26`)

Measured with the session's own counter, which is slightly stricter than the
figures the spec was written against (it counts a line as prose if it is inside a
module/class/function docstring or is a comment-only line):

| | lines | prose | share | prose lines naming a story/epic |
|---|---|---|---|---|
| `src/steeproute` before | 13,100 | 5,765 | 44% | 367 |
| `src/steeproute` after | 13,057 | 5,727 | 44% | **0** |
| `tests` before | 24,641 | 5,851 | 24% | 393 |
| `tests` after | 24,338 | 5,720 | 24% | **0** |

## Work list — `src/` by prose share

Order is by concentration, not size. Status: TODO / DONE.

| prose | file | story refs | status |
|---|---|---|---|
| 390/669 (58%) | `solver/grasp.py` | 44 | DONE |
| 232/419 (55%) | `pipeline/graph.py` | 15 | DONE |
| 230/424 (54%) | `models.py` | 27 | DONE |
| 131/252 (52%) | `solver/distinctness.py` | 10 | DONE |
| 220/431 (51%) | `pipeline/__init__.py` | 12 | DONE |
| 60/122 (49%) | `solver/reuse.py` | 7 | DONE |
| 79/170 (46%) | `progress.py` | 6 | DONE |
| 78/169 (46%) | `app/cli_adapter/params_schema.py` | 2 | DONE |
| 113/245 (46%) | `pipeline/dem.py` | 6 | DONE |
| 68/157 (43%) | `app/cli_adapter/regions.py` | 3 | DONE |
| 183/432 (42%) | `pipeline/osm.py` | 10 | DONE |
| 657/1554 (42%) | `cache.py` | 44 | DONE |
| 248/603 (41%) | `solver/parallel.py` | 5 | DONE |
| 172/421 (41%) | `validator.py` | 11 | DONE |
| — | `solver/descent.py` | 2 | DONE |
| — | remaining 25 `src` files | — | DONE |
| — | `app/static/**` js/css/html (334 comment lines) | — | DONE |

`tests/` is a comparable body of work with its own rules — see §Tests below.

## Tests — half the job, not a footnote

By prose, `tests/` is the same size as `src/`: **5,322 prose lines against
5,227**, and **373 story/epic references against 356** — more than `src`. Any
plan that treats it as a trailing item is mis-scoped. The same drift is here for
the same reason: agents wrote test docstrings in the voice of the story that
added the test.

**The failure mode unique to tests.** In `src` a wrong comment misleads. In a
test, a docstring that describes an assertion the test does not actually make is
worse: nothing fails when it drifts, and it convinces a reviewer that coverage
exists. **614 of 921 test functions carry a docstring** — that is the surface to
check, and every one must be read against its own `assert`s, not skimmed. This
cannot be sampled; a claim is only verifiable at the assertion it describes.

**Keep, in a test:** what regression this guards and why the case is
interesting; why a fixture holds the specific values it does; why an assertion
is loose or strict; a documented flake or platform quirk; the reason a test is
skipped or marked. **Cut:** restatement of arrange/act/assert; the story that
introduced the test; "as of Story N this also covers…"; scene-setting that
repeats the module docstring above it.

**Two files in `tests/` are not tests** and should be read under the `src`
rules: `tests/integration/exhaustive_oracle.py` (49% prose, 20 refs — a
reference implementation the solver is validated against) and the `conftest.py`
files (40% / 32% prose, no test functions).

### Work list — `tests/` by prose lines

The top 22 of 95 files hold 3,016 of the 5,322 prose lines. Sorted by absolute
prose (where the work is), not by share as the `src` table is.

| prose | share | refs | docstring'd fns | file | status |
|---|---|---|---|---|---|
| 217/564 | 38% | 5 | 12 | `integration/test_oracle_correctness.py` | DONE |
| 208/978 | 21% | 14 | 33 | `unit/test_graph_contraction.py` | DONE |
| 198/521 | 38% | 7 | 10 | `integration/test_metamorphic.py` | DONE |
| 195/836 | 23% | 11 | 30 | `unit/test_check_coverage.py` | DONE |
| 171/489 | 35% | 12 | 11 | `unit/test_grasp_construction.py` | DONE |
| 151/1026 | 15% | 11 | 41 | `unit/test_smoothing.py` | DONE |
| 151/937 | 16% | 13 | 37 | `unit/test_cache.py` | DONE |
| 149/372 | 40% | 9 | 0 | `integration/conftest.py` | DONE |
| 132/1036 | 13% | 21 | 1 | `integration/test_app_api.py` | DONE |
| 123/635 | 19% | 4 | 21 | `unit/test_dem_download.py` | DONE |
| 123/634 | 19% | 9 | 27 | `unit/test_validator.py` | DONE |
| 121/400 | 30% | 2 | 8 | `e2e/test_source_unavailable.py` | DONE |
| 115/636 | 18% | 5 | 20 | `unit/test_climbs.py` | DONE |
| 115/630 | 18% | 6 | 23 | `unit/test_dem.py` | DONE |
| 115/237 | 49% | 20 | 0 | `integration/exhaustive_oracle.py` | DONE |
| 113/514 | 22% | 1 | 20 | `unit/test_distinctness.py` | DONE |
| 112/699 | 16% | 22 | 38 | `unit/test_area_parsing.py` | DONE |
| 112/527 | 21% | 3 | 9 | `unit/test_climb_detection.py` | DONE |
| 109/697 | 16% | 8 | 32 | `unit/test_osm.py` | DONE |
| 108/450 | 24% | 12 | 15 | `e2e/test_steeproute_setup.py` | DONE |
| 97/378 | 26% | 3 | 6 | `e2e/test_coverage_check.py` | DONE |
| 81/252 | 32% | 4 | 0 | `benchmarks/conftest.py` | DONE |
| 2,306 | — | 171 | 220 | remaining 73 files | DONE |

A caution for this table: prose share is a much weaker signal in tests than in
`src`. A 13%-prose file with 1,036 lines (`test_app_api.py`) carries more drift
than a 49%-prose file with 237. Rank by the prose column, and treat the
docstring'd-function count as the real read-effort estimate.

## Specific things already identified — all resolved

- **Module-docstring-as-changelog** — rewritten as description, not history:
  `cli/query.py`, `cli/setup.py`, `app/main.py`, `app/api.py`, `app/queue.py`,
  `app/models.py`, `app/cli_adapter/__init__.py` (which also listed a per-seam
  implementation status; all four seams exist).
- **Triplication in `solver/grasp.py`** — each argument now lives once, on the
  code it constrains: θ-as-route-level-constraint in `_route_slope_ok`, the
  longest-prefix argument in `_best_theta_prefix`, stagnation-vs-delta in
  `run()`'s docstring. Other sites point at them.
- **`HEAVY_EDGE_ATTRS` tangle** — `models.py` now states why the constant is
  homed at the lowest layer (`pipeline/` must not import `solver/`) and cites
  itself, not the re-export path; `solver/parallel.py` states only why it
  re-exports.
- **Four "sole caller" claims** — encoded as
  `tests/unit/test_graph_ownership_call_sites.py`, which enumerates the seven
  ownership-transfer call sites and fails on any new one, plus a second test that
  every such callee defaults to the copying path. Verified non-vacuous. The prose
  at each definition now states the ownership *precondition* (durable) rather
  than the caller census (not).
- **`cli/setup.py:140`** — dead symbol dropped; the comment states the durable
  rule ("no upper size ceiling on either CLI: a large area is slow, not
  invalid"). The mirror of the same problem in
  `tests/e2e/test_coverage_check.py` (docstring *and* test name) was fixed too.
- **Provenance half-applied** — the `~13%` and `~35-40%` figures now read
  "py-spy, r6 Grenoble area, 2026-07-03". Every other bare number in `src/` got
  the same treatment: the `~8 s` adjacency rebuild and `~8 s` pool-teardown tail
  (r20, 2026-07-08 / 2026-07-14), the `~5.4 s` / `~5 s` / `~4.6 s` / `~7.4 s` /
  `3.77 s → 1.51 s` / `~204 MB → ~72 MB` figures (r20, 2026-07-08 / 2026-07-24).

### One deliberate exception

`regression.py:135-144`, which §Model comments calls the best comment in the
repo, still cites `spec-cli-defaults-and-setup-radius-cap.md` — an
implementation-artifact, so a point-in-time record that §Doc references says not
to point code at. Left as-is: it states its fact inline and stands without the
doc. Flagging the tension rather than silently resolving it either way. The six
*other* citations of that same spec, in `tests/`, were removed — where they
carried a date, the date stayed.

## Model comments — read these first to calibrate

- `regression.py:136-145` (`--workers`) — dated, names the spec, states the
  concrete failure and the durable rule. The best comment in the repo.
- `cache.py:963-970` — refutes the obvious `or 1.0` guard with the number.
- `cache.py:737-753` — a non-obvious correctness hazard (footprint tie →
  lexicographic coin flip → stale provenance).
- `cache.py:662-667` — "the bytes are not equal and must not be asserted equal".
- `cli/setup.py:286-308` — osmnx logging internals and the `propagate` trade-off.
- `pipeline/osm.py:129-147` — why catching `ValueError` is safe *at this site*.
- `solver/parallel.py:515-520` — "deliberately NOT `with ProcessPoolExecutor`".
- `solver/descent.py:35-55` — "Known limitations (intentional)", with where to
  change them.
- `pipeline/climbs.py:242-245` — "numpy's hypot diverges by up to a ULP on ~17%
  of inputs".

## Gotcha

`cache._PIPELINE_CONTENT_GLOBS` is `("pipeline/**/*.py", "models.py")` and hashes
**raw file bytes**. Editing a comment in those files changes the cache key, so
the next `steeproute-setup` for an area re-prepares and evicts the old entry.
Harmless for queries — `check_coverage` matches on geometry, so existing prepared
areas stay readable — but budget for one re-prepare, and batch edits to those
files rather than dribbling them across commits.

## Verification per file

`uv run basedpyright <file>`, then the matching test directory (never mix
`tests/unit` and `tests/integration` in one invocation). Full suite before
merging. Regression goldens must not move; if one does, something other than a
comment changed.

## Outcome

**Story/epic references: 367 → 0 in `src/`, 393 → 0 in `tests/`.** Same for the
`AC #N` acceptance-criteria pointers (~90 in `tests/`), the `Task N` /
`Review P<n>` review-thread ids, the opaque `T2`/`D1`/`D2`/`S3`/`Q4`/`DEF2`
deferred-work codes, and every banner comment (2 in `src/*.py`, 5 in `app.css`,
7 bare section markers in `map-home.js`, 38 three-line banners + the
`test_metamorphic.py` rulers in `tests/`). No TODOs, no commented-out code.

**Prose share did not fall, and that is the honest result.** `src/` is 44% before
and after; `tests/` is 24% both times. The spec predicted "low 30s" for `src/` on
the theory that cutting history and duplication would drop the number on its own.
It didn't, for a reason worth recording:

- The history *was* cut — but most of it was **load-bearing narration**, not
  filler. "Story 14.2 vectorized this" became "don't push numpy dispatch down to
  per-edge arrays, it measurably regresses on loops this light" — a refuted
  alternative with a reason, which is longer than the changelog line it replaced
  and worth far more.
- Where a comment only *annotated* (a bare `(Story 5.2)` after a claim), removal
  shortened the line but not the line count.
- The remaining density is concentrated in the modules that legitimately carry
  algorithm rationale: `cache.py`, `solver/grasp.py`, `pipeline/{graph,smoothing,
  climbs,__init__}.py`, `models.py` — ~3,200 of `src/`'s 5,727 prose lines. Two
  checks say this is not restatement: a scan for one-line docstrings whose words
  are a subset of the function name found 14 candidates and **all 14** carry
  information the name doesn't (an axis order, a 404 branch, a side effect, a
  purity contract); a scan for comments restating the following line found zero.

So the number is a symptom, not the target, exactly as §Target said. Chasing it
to the low 30s from here would mean deleting rationale.

**What the pass did not do.** The docstring-vs-assertion audit (§Tests' "most
important" rule) was run mechanically — pairing each of the 614 docstring'd test
functions with its `assert`s / `raises` / `_assert_*` helpers and flagging
no-assert, unmatched-count and unmatched-negation shapes. Every flag resolved to
a false positive (helper delegation, or a number living in a fixture param), and
one real find: a docstring naming a deleted symbol. That is weaker evidence than
a human reading all 614 against their assertions; treat "no drifted test
docstrings" as unproven rather than established.
