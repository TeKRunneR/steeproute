# Story 8.4: README Known Limitations + Quickstart sections

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a reader of the GitHub repo,
I want the README to document the tool's known failure modes (DEM / polyline cliff-bias, GRASP heuristic non-optimality) and include a Quickstart for both CLIs,
so that the PRD's error-model documentation commitment is fulfilled and a visiting reviewer can run the tool in under two minutes.

## Acceptance Criteria

1. **`## Known Limitations` section** added to `README.md` covering all four points:
   - **Data-level error** — DEM / polyline-drift interaction and resulting cliff-bias risk: phantom steepness near cliffs is possible; users should cross-check cliff-proximate routes against topo maps before treating them as ideas.
   - **Solver-level error** — GRASP is a heuristic, not an optimal solver; the GRASP-vs-exhaustive CI ratio is one empirical anchor on small instances but does not generalize to optimality on real-scale queries. The tool finds "a good route," not "*the* route."
   - **Memory envelope (NFR2)** — reflects the measured gallery-generation behavior: max peak working set was ~792 MB (Story 8.3), so the honest statement is "runs comfortably on a commodity 16 GB laptop" — **no >12 GB caveat is warranted**.
   - **Portability (NFR7/NFR8)** — "Developed and tested on Windows. Linux is expected to work but is not actively tested; macOS is not a v1 commitment."

2. **Known Limitations appears in the top third of the README** — portfolio-visible, not buried in an appendix.

3. **`## Quickstart` section** with concrete install + invocation commands for both `steeproute-setup` and `steeproute`, using one of the gallery regions as the worked example. (The existing `## Usage` section — pulled forward in Story 8.3 — already does exactly this; consolidate/rename rather than duplicate.)

4. **`tests/e2e/test_readme_references_gallery.py`** guards against README/gallery drift: it asserts every **surfaced** gallery report (`docs/examples/*/route-1.html`) is referenced from `README.md`, and fails (rather than passing vacuously) if no reports are found. Not `live`-marked, so default CI enforces it.

## Tasks / Subtasks

- [x] Add `## Known Limitations` to `README.md` covering all four points (AC: #1)
  - [x] Use the actual measured memory figure from Story 8.3 (max ~792 MB) — assert the 16 GB-comfortable statement, no >12 GB caveat (AC: #1)
- [x] Place Known Limitations in the top third of the README; reorder existing sections as needed (AC: #2)
- [x] Consolidate the existing `## Usage` section into a `## Quickstart` (install + setup + query for a gallery region) — no duplicated walkthrough (AC: #3)
- [x] Implement `tests/e2e/test_readme_references_gallery.py` (AC: #4)
- [x] Verify: full suite green, README renders correctly on GitHub (links resolve, section order, top-third placement) (AC: #1–4)

## Dev Notes

### This is the last documentation story — what already exists

Story 8.3 already restructured the README top: an intro, a **Coverage** note (OSM trails ≈ worldwide; IGN RGE ALTI elevation ⇒ France only), a `## Usage` section (install + worked Chamrousse example + key-parameter table), and the `## Gallery`. Read the current [README.md](README.md) before editing — your job is to add Known Limitations + Quickstart and reorder, **not** to rewrite the intro/Coverage/Gallery.

- **Quickstart vs Usage:** 8.3 deliberately pulled Quickstart content forward into `## Usage` (its completion notes flag that 8.4 owns Known Limitations and "can fold/refine Usage into Quickstart"). The clean move is to rename `## Usage` → `## Quickstart` (or merge), keeping the single worked example. Do not leave two near-identical install/run walkthroughs.
- **Top-third placement:** the README is ~110 lines; the top third is roughly the first ~37 lines, which currently lands mid-Usage. Known Limitations must sit high — a natural slot is right after the intro + Coverage and before/around Quickstart. Expect to reorder.

### Memory point is already settled — do not re-measure

Story 8.3 recorded peak working set per gallery query; the max was **~792 MB** (Saint-Nizier), far below the 12 GB threshold. Per 8.3 AC #6 this means **no NFR2 caveat is needed** — state the "runs comfortably on a commodity 16 GB laptop" line. The measurements live in [docs/examples/README.md](docs/examples/README.md); cite that, don't regenerate.

### The drift test — surfaced reports vs. all committed reports (key decision)

`docs/examples/` contains **9 HTML files** (`route-1..3.html` × chamrousse / saint-nizier / col-de-porte), but the Gallery intentionally surfaces only each region's **route-1** report (8.3 design: route-1 is linked per row; route-2/3 are supplementary, reachable via the linked `docs/examples/` folder). A literal "every `*.html` is referenced from README" test therefore **fails today** against the existing, intended README.

**Decision (confirmed with Yann):** scope the test to the reports the Gallery surfaces — assert every `docs/examples/*/route-1.html` is referenced from `README.md`, plus a non-empty guard so a path drift can't pass vacuously. This matches the AC's stated intent ("catches README drift when gallery is regenerated") without forcing route-2/3 into the gallery as clutter. Optionally also assert no README `docs/examples/...html` link is broken (points at a missing file) to catch the reverse drift. (The epics AC literally says "every HTML filename"; the surfaced-only scope is the deliberate, narrower reading.)

- Mirror the structure of the sibling test [tests/e2e/test_gallery_self_contained.py](tests/e2e/test_gallery_self_contained.py): repo-root-relative `docs/examples/` path via `Path(__file__).resolve().parents[2]`, `rglob`, non-empty assertion, not `live`-marked so default `uv run pytest` enforces it (`pyproject.toml` `addopts = ["-m", "not live"]`).

### Source framing for Known Limitations (use these, don't invent)

- Data-level / cliff-bias and solver-level / non-optimality wording is fixed by the PRD — match it, don't soften or overclaim.
- The GRASP-ratio framing caution ("regression signal, not a quality signal"; "does not transfer from a toy instance to real-scale Alpine queries") is the right register for the solver-level point.

### Project Structure Notes

- **New:** `tests/e2e/test_readme_references_gallery.py`.
- **Modified:** `README.md` (add `## Known Limitations`, `## Quickstart`; reorder so Known Limitations is top-third; consolidate `## Usage`).
- No source code or gallery assets change — this is docs + one e2e test. Leave `docs/examples/` contents and the 8.2 fixtures/goldens untouched.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 8.4] (lines 982–998) — story + ACs; Epic 8 intro (931–933).
- [Source: _bmad-output/planning-artifacts/prd.md] — Known Limitations commitment + exact data/solver wording (lines 266–269); cliff-bias risk register entry (450); memory envelope / 16 GB (539); platform support NFR7/NFR8 (554–556); GRASP-ratio "regression-not-quality" framing (587).
- [Source: _bmad-output/planning-artifacts/epics.md] — NFR2/NFR7/NFR8 coverage map (lines 188–197).
- [Source: _bmad-output/implementation-artifacts/8-3-readme-gallery-with-3-5-pre-computed-example-reports.md] — Usage section pulled forward + 8.4 scope boundary (completion notes); max peak memory ~792 MB ⇒ no NFR2 caveat (AC #6).
- [Source: docs/examples/README.md] — per-region recorded peak memory + regen commands.
- [Source: tests/e2e/test_gallery_self_contained.py] — sibling test pattern to mirror (path resolution, non-empty guard, not-`live`-marked).
- [Source: README.md] — current section order and existing route-1 Gallery links.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- None. Documentation + one e2e test; no runtime debugging needed.

### Completion Notes List

- **AC #1 — Known Limitations (4 points).** Added `## Known Limitations` to `README.md` covering: (1) cliff-proximate phantom steepness from DEM/polyline drift, framed as "ideas to verify against a topo map"; (2) GRASP as a heuristic — the CI ratio is a *regression* signal, not an optimality claim ("a good route, not *the* route"); (3) memory — "runs comfortably on a commodity 16 GB laptop", citing the measured ~0.8 GB peak (max 792 MB, Story 8.3), **no >12 GB caveat** as the measurements don't warrant one; (4) platform — Windows tested, Linux expected/untested, macOS not a v1 commitment (NFR7/NFR8).
- **AC #2 — top-third placement.** Known Limitations sits at README lines 14–30 (right after intro + Coverage, before Quickstart), well inside the top third of the ~128-line file — portfolio-visible, not in an appendix.
- **AC #3 — Quickstart.** Consolidated the existing `## Usage` section (added in Story 8.3) into `## Quickstart` by renaming the heading — no duplicated walkthrough. It already carries install (`git clone` + `uv sync`), the two-CLI setup→query workflow with a worked **Chamrousse** example (a gallery region: center 45.12,5.88, setup 6.5 km / query 6.0 km), and the key-parameter table.
- **AC #4 — drift test.** `tests/e2e/test_readme_references_gallery.py` asserts every surfaced report (`docs/examples/*/route-1.html`) is referenced from `README.md`, with a non-empty guard so an empty/relocated gallery fails rather than passing vacuously. Scope is surfaced-reports-only per the confirmed decision (route-2/3 stay supplementary). Repo-root-relative paths via `Path(__file__).resolve().parents[2]`, forward-slash matching via `as_posix()`, not `live`-marked so default CI enforces it. Passes.
- **Quality:** ruff check + ruff format + basedpyright all clean (0/0/0) on the new test. Gallery links left intact; no source/asset changes.

### File List

- `README.md` (modified — added `## Known Limitations` in the top third; renamed `## Usage` → `## Quickstart`)
- `tests/e2e/test_readme_references_gallery.py` (new — README ↔ gallery surfaced-report reference gate)

## Change Log

- 2026-06-11: Implemented Known Limitations + Quickstart README sections and the README↔gallery drift test. Full suite green (774 passed, 2 deselected). Closed out (docs-only change; code review waived). Status → done.
