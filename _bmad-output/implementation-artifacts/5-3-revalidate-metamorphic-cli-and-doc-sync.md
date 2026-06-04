# Story 5.3: Re-validate metamorphic + CLI tests and sync planning docs

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want the metamorphic suite, CLI help tests, and planning docs brought into line with the undirected base-segment reuse semantics delivered by Stories 5.1–5.2,
so that the change is fully covered and the PRD/architecture/epics no longer describe directed edge-simple reuse or connector-dropping anywhere.

## Acceptance Criteria

1. **Metamorphic suite re-validated under undirected reuse.** The 8 invariants in `tests/integration/test_metamorphic.py` pass under the new semantics, and the two identity-sensitive ones are demonstrably sound: (a) **node-relabel invariance** — relabelling node ids leaves the best objective byte-identical *and* the base-segment identity carried on edge data is relabelled consistently (or it is documented why the directed-tag factory makes the objective invariance hold regardless); (b) **add-edge monotonicity** — adding an edge never retro-blocks an already-used base segment, so the best objective is still non-decreasing. The suite docstring records where undirected-reuse *behaviour* is actually proven (the dedicated solver/oracle/validator units + the real-Grenoble-fixture test, per Story 5.2) and why this suite deliberately stays on directed per-edge tags.

2. **The `raise l_connector → best objective non-decreasing` invariant is resolved.** Either it is added as a non-vacuous invariant, or it is **omitted with a documented rationale** in the suite docstring mirroring the existing "Why there is no `min_climb_slope` invariant" note — i.e. `l_connector` is a contraction-time reuse-exemption threshold consumed by `contract_climbs` upstream of the `ContractedGraph` this suite builds directly, so the solver never reads it and varying it here is inert. (The epics AC explicitly permits "add or justify omitting".)

3. **CLI `--l-connector` help reflects the realized semantics and is asserted.** The Click `help=` text for `--l-connector` is reworded from the stale "edge-reuse length threshold … (short connectors vs primary edges)" to the reuse-exemption phrasing, and the CLI help/smoke tests gain an assertion on the help-string *text* (today they assert only that the flag *name* appears in `--help`). The reworded text and the assertion agree.

4. **Planning docs no longer describe directed edge-simple or connector-drop reuse.** PRD (FR5, `--l-connector` constraints row, and any straggler parameter line), epics (the FR5 entry in the requirements list, plus any *done*-story AC that still asserts connector-dropping or a directed edge-reuse limit), and architecture (stage 9, constraint table, edge-attribute contract) all describe undirected base-segment reuse with the short-connector exemption. Most of this was applied by the 2026-06-03 correct-course (commit `56f4532`); this AC is a verification pass plus the remaining stragglers (see Dev Notes for the concrete spots).

5. **Gates green.** The four CI gates pass on Windows — `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest` (full unit + integration + e2e suite) — with no new deps and coverage floors held.

## Tasks / Subtasks

- [x] Task 1: Re-validate the 8 metamorphic invariants under undirected reuse; make the relabel and add-edge invariants sound and document where undirected-reuse behaviour is actually covered. (AC: #1)
  - [x] Confirm `_relabelled` either relabels the `base_segment_id` edge-data tags or that the directed-tag factory makes the objective invariance moot; pick one and pin it in code + docstring. — chose to remap the tag tuples (faithful isomorph).
  - [x] Confirm `_with_added_edge` (an untagged super-edge → directed fallback in `solver/reuse.py`) does not retro-block an existing segment. — now tags the new edge with a fresh non-colliding directed id, so soundness no longer relies on the fallback.
- [x] Task 2: Add the `raise l_connector → objective non-decreasing` invariant **or** add a justify-omit docstring block parallel to the `min_climb_slope` note. (AC: #2) — justify-omit (l_connector is contraction-time, inert on the directly-built graph).
- [x] Task 3: Reword the `--l-connector` Click `help=` string (`cli/_shared.py:281`) to reuse-exemption semantics; add a help-string-text assertion to the CLI help/smoke layer. (AC: #3)
- [x] Task 4: Doc-sync verification + stragglers — PRD `:110`, epics FR5 list entry `:28` and Story 3.3 ACs `:548-549`; verify epics 3.5/3.6/3.9 ACs and architecture `:254/:517/:260-266`, rewording only where the old drop/directed rule survives. (AC: #4)
- [x] Task 5: Run all four gates + full suite on Windows. (AC: #5)

## Dev Notes

- **Lead recommendation on the `l_connector` invariant (AC #2): justify-omit, don't force it.** The realized architecture computes reuse exemption from per-edge `reusable` tags set at *contraction* (`pipeline/graph.py:137`), not from `SolverParams.l_connector` at solve time. The metamorphic suite builds a `ContractedGraph` **directly** (bypassing `contract_climbs`), so `l_connector` is never consumed and varying it is inert — exactly the situation the suite already documents for `min_climb_slope` (`test_metamorphic.py:38-48`). The clean, defensible outcome is a short docstring block stating this, not a synthetic invariant that would be vacuous or require re-plumbing the fixture through `contract_climbs`. Add the real invariant only if you choose to route a fixture through contraction — not required by the AC.

- **This story is mostly verification + stragglers, because the correct-course front-loaded the doc sync.** Commit `56f4532` ("docs(planning): apply 2026-06-03 undirected segment-reuse correct-course") already applied the new wording to: PRD FR5 (`prd.md:486`) and the `--l-connector` constraints row (`prd.md:354`); architecture stage 9 (`architecture.md:254`) and the constraint table (`architecture.md:517`); epics FR5 coverage row (`epics.md:161`) and the whole Epic 5 section (`epics.md:225,733`). Do **not** re-edit those — verify and move on. The `models.py` docstrings were synced by Story 5.1.

- **Concrete doc stragglers still carrying the old framing (the real AC #4 work):**
  - `prd.md:110` — "`L_connector` (edge-reuse length threshold) = 200m" still uses the old "edge-reuse length threshold" phrasing.
  - `epics.md:28` — the FR5 entry in the requirements list still reads "length threshold distinguishing short connectors from primary edges" (the pre-correct-course wording; the *coverage row* at `:161` is already fixed).
  - `epics.md:548-549` — Story 3.3's ACs still say "dropping shorter ones" / "sub-`l_connector` connectors removed". These are a *done* story's historical ACs; reword to the keep-and-tag semantics so the epics file is internally consistent (the 5.3 story goal), but keep the edit minimal.
  - `epics.md:576` (Story 3.5 oracle) and `:643` (Story 3.9 validator) reference an "edge-reuse limit" generically. **Verify only** — reword solely if they assert the *directed* or *drop* rule; a neutral "edge-reuse limit" mention does not need touching (avoid mechanical over-editing).
  - `architecture.md:260-266` (edge-attribute contract 3c) lists the *pipeline* (stage 1–7) graph attributes; `base_segment_id`/`reusable` are *contracted-graph* (stage 9) attributes already documented at `:254` and in `models.py`. Confirm coherence; do not bolt the contracted-graph tags onto the stage-7 contract.

- **CLI help (AC #3).** The option lives in `cli/_shared.py:276-281`; current `help=` is `"Edge-reuse length threshold in meters (short connectors vs primary edges)."`. Reword to reuse-exemption semantics consistent with `prd.md:354` (e.g. short connectors `< --l-connector` are reuse-exempt and bidirectional; all else once-per-route, undirected). Today **no test asserts the help text** — `test_cli_help.py` (`QUERY_FLAGS`) and `test_cli_smoke.py` only assert the flag *name* appears in `--help`. Add a focused assertion that a distinctive phrase of the reworded text is present in `steeproute --help`. Keep it in the in-process unit layer (`test_cli_help.py`, `CliRunner`) to stay fast; the e2e smoke layer asserting flag names is sufficient there.

- **Metamorphic tag subtlety (AC #1).** `conftest.make_toy_contracted_graph` tags every edge with a **directed** `base_segment_id = frozenset({(u, v, key)})` and `reusable = False` (`conftest.py:177-178`) — Story 5.2's deliberate choice so the factory's feasible set stays bit-identical to pre-5.2 and the 8 invariants + the 3.7 gate are unperturbed (genuine forward/reverse collisions are covered elsewhere). The `_relabelled` transform (`test_metamorphic.py:159-183`) uses `nx.relabel_nodes`, which relabels node *keys* but **not** the `(u,v,key)` tuples *inside* the `base_segment_id` edge-data frozensets — so after relabel the tags reference stale node ids. Because each edge is its own directed segment with `reusable=False` on this sparse graph, the *objective* is unaffected, which is why the invariant currently passes. AC #1 asks you to make this explicit: either extend `_relabelled` to remap the tag tuples too (cheap, and the more honest "identity is relabel-invariant" assertion), or document the moot-ness. Prefer remapping the tags.

- **Scope guardrails — do NOT touch:** `solver/grasp.py`, `solver/reuse.py`, `validator.py`, `exhaustive_oracle.py`, `pipeline/graph.py`, the `models.py` tag contract — the *behaviour* is finished (Stories 5.1/5.2, gates green at 665 passed). This story changes only tests, the CLI help string (one line + its assertion), and planning-doc prose. No feasible-set change is expected; if a metamorphic invariant *fails* (rather than needing a doc/assertion), that is a regression to investigate, not to paper over.

### Project Structure Notes

- **Modify (source):** `src/steeproute/cli/_shared.py` (the `--l-connector` `help=` string only — no flag-surface or validation change).
- **Modify (tests):** `tests/integration/test_metamorphic.py` (relabel-tag remap and/or docstring; `l_connector` invariant or justify-omit block); `tests/unit/test_cli_help.py` (add help-string-text assertion). Touch `tests/integration/conftest.py` only if relabel handling needs a helper.
- **Modify (docs):** `_bmad-output/planning-artifacts/prd.md` (`:110`), `_bmad-output/planning-artifacts/epics.md` (`:28`, `:548-549`, verify `:576`/`:643`), `_bmad-output/planning-artifacts/architecture.md` (verify `:254`/`:517`/`:260-266`).
- **Reuse, don't reinvent:** extend the existing `_relabelled`/`_with_added_edge` transforms and the `make_toy_contracted_graph` factory; do not fork new fixtures. Mirror the existing docstring style for any justify-omit rationale.

### Testing standards summary

- Synthetic-graph tests in `tests/unit/`, real-fixture in `tests/integration/`, e2e in `tests/e2e/`; naming `test_<unit>_<scenario>` (Architecture §"Test organization"). No `pytest.skip`/`xfail` (Architecture §Cat 11c — pass-required); the metamorphic suite explicitly forbids them.
- Any new assertion (help-text, relabel-tag, optional `l_connector` invariant) must be non-vacuous — follow the suite's existing pattern of pairing a monotonicity assertion with a strict-gain / binds-on-some-seed guard so the test can't silently degrade to a tautology.
- Coverage floor (`fail_under = 0`) must hold or rise; no new source branch goes untested (this story adds essentially no source branches — the CLI help string is data).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 5.3"](../planning-artifacts/epics.md) — BDD acceptance criteria (lines 768-780)
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-03-undirected-segment-reuse.md §4C (3.8, 1.5/1.7), §4A (A1/A2/A4/A6), §5 (Story 5.3)](../planning-artifacts/sprint-change-proposal-2026-06-03-undirected-segment-reuse.md) — metamorphic re-validation, new `l_connector` invariant, CLI help reword, doc-sync targets
- [Source: _bmad-output/implementation-artifacts/5-2-undirected-reuse-enforcement-solver-oracle-validator.md §"Review Findings"](5-2-undirected-reuse-enforcement-solver-oracle-validator.md) — the deferred item routing undirected-identity coverage through the metamorphic re-validation (this story); directed-tag factory rationale
- [Source: tests/integration/test_metamorphic.py:7-51,159-183,331-357](../../tests/integration/test_metamorphic.py) — the 8 invariants; the `min_climb_slope` omission precedent (`:38-48`); `_relabelled` / `_with_added_edge` transforms
- [Source: tests/integration/conftest.py:80-191](../../tests/integration/conftest.py) — `make_toy_contracted_graph`; directed tag at `:177-178`
- [Source: tests/unit/test_cli_help.py:9-32,67-72](../../tests/unit/test_cli_help.py) — `QUERY_FLAGS`; flag-name-only `--help` assertion (no text assertion yet)
- [Source: src/steeproute/cli/_shared.py:276-281](../../src/steeproute/cli/_shared.py) — `l_connector_option` Click definition + stale `help=` string
- [Source: _bmad-output/planning-artifacts/prd.md:110,354,486](../planning-artifacts/prd.md) — straggler param line (`:110`); already-synced constraints row (`:354`) and FR5 (`:486`)
- [Source: _bmad-output/planning-artifacts/architecture.md:254,260-266,517](../planning-artifacts/architecture.md) — already-synced stage 9 / constraint table; edge-attribute contract 3c to verify

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (`claude-opus-4-8`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps (stdlib only).

**Final gate pass (all green):**

```
uv run ruff check          → All checks passed!
uv run ruff format --check  → 74 files already formatted
uv run basedpyright        → 0 errors, 0 warnings, 0 notes
uv run pytest -q           → 666 passed, 1 deselected in ~295 s
                             (665 baseline + 1 net new CLI help-text test;
                              the 1 deselected is the network-gated osm_live test)
tests/integration/test_metamorphic.py → 41 passed in ~12 s (post-edit sanity)
tests/unit/test_cli_help.py            → 51 passed in ~1 s
```

### Completion Notes List

**No behaviour change — tests, one CLI help string, and planning-doc prose only.** Solver/oracle/validator/contraction were untouched (Stories 5.1/5.2 own them); the feasible set is unchanged and the full suite passed without any value-shift to investigate.

**AC #1 — metamorphic invariants re-validated under undirected reuse.** The 8 invariants pass on the directed-tagged toy factory. The two identity-sensitive transforms in `test_metamorphic.py` were made faithful rather than incidentally-passing:
- `_relabelled` now remaps the `(u, v, key)` tuples *inside* each edge's `base_segment_id` frozenset (`nx.relabel_nodes` only remaps node keys, not tag contents), so the relabelled graph carries a genuinely relabel-invariant base-segment identity — the "node-id ordering never leaks" property now holds for the reuse identity, not just the objective.
- `_with_added_edge` now tags the new super-edge with its own fresh directed `base_segment_id` + `reusable=False`. A fresh id can't collide with an existing segment, so add-edge monotonicity holds *by construction* rather than by accident of the untagged-edge directed fallback in `solver/reuse.py`.

**AC #2 — `l_connector` invariant justify-omitted.** Added a module-docstring section (parallel to the existing "Why there is no `min_climb_slope` invariant" note) explaining that `l_connector` is a contraction-time reuse-exemption threshold consumed only by `contract_climbs`; the solver derives exemption from per-edge `reusable` tags and never reads `SolverParams.l_connector`. This suite builds a `ContractedGraph` directly, so varying `l_connector` is inert and an invariant would be vacuous. The docstring also records that the undirected-reuse *behaviour* is proven by the dedicated solver/oracle/validator units + the real-fixture test (Story 5.2), and why the suite intentionally stays on directed per-edge tags (keeps the feasible set bit-identical to pre-5.2 so the objective-monotonicity invariants and the Story 3.7 gate are unperturbed).

**AC #3 — CLI help reworded + asserted.** `cli/_shared.py::l_connector_option` `help=` changed from "Edge-reuse length threshold in meters (short connectors vs primary edges)." to reuse-exemption phrasing consistent with `prd.md:354`. Added `test_query_help_l_connector_describes_reuse_exemption` to `test_cli_help.py` — it normalizes Click's reflowed `--help` output and asserts the distinctive phrases "reuse-exemption threshold" and "in both directions" appear. (Previously no test asserted the help *text* — only flag names.)

**AC #4 — planning-doc sync.** Most of the doc sync was already applied by the 2026-06-03 correct-course (commit `56f4532`): PRD FR5 (`:486`) + `--l-connector` constraints row (`:354`), architecture stage 9 (`:254`) + constraint table (`:517`), epics FR5 coverage row (`:161`) + Epic 5 section. Remaining stragglers fixed:
- `prd.md:110` — "edge-reuse length threshold" → "short-connector reuse-exemption length threshold".
- `epics.md:28` — FR5 entry in the requirements list reworded to match the realized FR5 (mirrors `prd.md:486`).
- `epics.md` Story 3.3 — **decision (flagging for review):** rather than rewrite a *done* story's historical ACs (which described the as-built drop behaviour), I appended a concise "**Superseded by Epic 5 (Story 5.1)**" annotation stating all connectors are now retained + tagged and reuse is enforced undirected at solve/validate time. This removes the contradiction while preserving the record, matching the in-file precedent (the correct-course updated the FR5 coverage row + added Epic 5 but left the Epic-3 story ACs intact). If you'd prefer a full in-place reword of the 3.3 ACs instead, that's a one-line change — say the word.
- Verified neutral / no edit: epics 3.5 (`:576`) and 3.9 (`:645`) say "edge-reuse limit" generically without asserting the directed/drop rule; 3.6 has no reuse-drop language; architecture `:254`/`:517` already synced; architecture `:260-266` is the *pipeline* (stage 1–7) edge-attribute contract — `base_segment_id`/`reusable` are *contracted*-graph (stage 9) attributes documented at `:254` + in `models.py`, so 3c is correct as-is. **architecture.md was not modified.**

**AC #5 — gates.** All four green on Windows (see Debug Log). No new deps; coverage floor (`fail_under = 0`) held.

### File List

**Modified (source):**
- `src/steeproute/cli/_shared.py` — `l_connector_option` `help=` reworded to reuse-exemption semantics (no flag-surface or validation change).

**Modified (tests):**
- `tests/integration/test_metamorphic.py` — `_relabelled` remaps `base_segment_id` tag tuples; `_with_added_edge` tags the new edge with a fresh directed id; module docstring gains the "Why there is no `l_connector` invariant" section + where-undirected-reuse-is-covered note.
- `tests/unit/test_cli_help.py` — added `test_query_help_l_connector_describes_reuse_exemption` (help-string-text assertion).

**Modified (docs):**
- `_bmad-output/planning-artifacts/prd.md` — `:110` default-list `L_connector` description.
- `_bmad-output/planning-artifacts/epics.md` — `:28` FR5 requirements-list entry reworded; Story 3.3 AC block gains an Epic-5 supersession annotation.

**Modified (tracking):**
- `_bmad-output/implementation-artifacts/5-3-revalidate-metamorphic-cli-and-doc-sync.md` — tasks checked, Dev Agent Record filled, status `ready-for-dev → in-progress → review`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status walked to `review`; `last_updated`.

## Change Log

| Date | Author | Description | Commit |
|---|---|---|---|
| 2026-06-04 | Yann (Claude Opus 4.8) | Story 5.3 implemented: Epic 5 closeout. Metamorphic suite re-validated under undirected reuse — `_relabelled` now produces a faithful isomorph (remaps `base_segment_id` tag tuples) and `_with_added_edge` tags its new edge with a fresh non-colliding id, so the relabel + add-edge invariants are sound by construction; the `l_connector` invariant is justify-omitted with a documented rationale (contraction-time threshold, inert on the directly-built graph; undirected behaviour proven by 5.2's units + real-fixture). `--l-connector` CLI help reworded to reuse-exemption semantics + a help-text assertion added. Planning-doc stragglers synced (prd.md:110, epics.md:28); Story 3.3's historical ACs annotated as superseded by Epic 5 rather than rewritten. Architecture verified already-synced (no change). All four gates green: ruff ✅, format ✅, basedpyright 0/0/0 ✅, pytest 666 passed. No new deps. Status → review. | _pending_ |
| 2026-06-04 | Yann (Claude Opus 4.8) | Lightweight review (inline diff, story diff is tests + one CLI help string + doc prose): **Approve**, no blockers. Verified `_relabelled` purity (relabel_nodes shallow-copies edge dicts; original fixture untouched), `_with_added_edge` no-behaviour-change tag clarification (matches the prior untagged directed fallback), and the non-vacuous help-text assertion. Two non-blocking observations noted (test-helper tag assumption; intentional help-string coupling). All 5 ACs confirmed. Story → done; Epic 5 → done (5.1/5.2/5.3 all done). | _pending_ |
