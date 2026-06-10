# Story 8.1: Regression golden test harness and update-regression workflow

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a developer,
I want a test harness that compares each pinned regression fixture's GRASP output against a committed 5-field hash tuple (`objective`, `d_plus_m`, `d_minus_m`, `edge_count`, `canonical_edge_sequence_hash`) per route, plus a single `uv run update-regression` command with commit-message rationale discipline,
so that silent behavioral drift between commits is caught automatically and goldens can be intentionally updated when justified (Architecture §Cat 11d).

## Acceptance Criteria

1. **Canonical edge-sequence hash function** — a pure function takes a route's edge list (`(node_u, node_v, key)` triples in traversal order), sorts them by the `(node_u, node_v, key)` tuple, serializes deterministically, and returns a SHA256 hex digest. It captures graph-level edge identity, not just aggregate metrics (Architecture §Cat 11d: `(objective, D+, D−, edge_count)` can collide while the route silently changes).

2. **Golden schema** — each golden is one JSON file per fixture at `tests/e2e/goldens/<fixture_name>.json` with the documented shape: `fixture_name`, `seed`, `params_hash`, and `routes: [{route_index, objective, d_plus_m, d_minus_m, edge_count, canonical_edge_sequence_hash}]` (the top-N tuples). `params_hash` fingerprints the **explicitly-pinned** params the fixture runs with (see AC #3), so a param change makes the golden visibly stale rather than silently comparing apples to oranges.

3. **Fixtures pin their full param set; the harness compares run output to goldens** — each registry entry pins seed + **every behavior-affecting solver knob explicitly** (not via CLI defaults), so a change to a default value or the introduction of a new param can never silently move an existing golden. `tests/e2e/test_pinned_regressions.py` runs `steeproute` on each registered fixture's cache at those pinned params, derives the 5-field tuple per route from the **real** `route-*.json` sidecars (no mocking of the solver or output layers), and asserts an exact match against the committed golden. Parameterized over a fixture registry so 8.2 can extend it without touching the comparison logic.

4. **What "non-regression" means, now and later.** A golden captures current known-good output at one fixture's explicitly-pinned inputs. A regression is *any* change to that output that wasn't deliberately blessed via `update-regression` with a commit rationale. The corollary the harness must honor: a change elsewhere in the system that does **not** alter a fixture's pinned inputs must leave its golden green with **zero edits** — in particular, the harness must not force a no-op golden rebuild merely because `SolverParams` gained a field. New behavior is exercised by *new* regression coverage, never by mutating an existing golden.

5. **`update-regression` command** — a `[project.scripts]` entry invocable as `uv run update-regression [--fixture NAME | --all]` re-runs the named fixture(s), overwrites the golden file(s), and prints a clear before/after diff of what changed. It shares the same canonical-hash + tuple-extraction core the test uses (single source of truth — the test and the writer can never disagree on what a golden *should* contain).

6. **Canonical-hash unit test** — `tests/unit/test_canonical_edge_hash.py` asserts the hash is (a) stable across runs for the same input (determinism, FR29) and (b) changes under a single-edge substitution (mutation detection).

7. **Doc discipline** — `README.md` (dev-notes section) documents that any commit updating goldens must include an explicit rationale in the commit message, and that `pytest.skip`/`xfail` on pinned-regression tests is not a sanctioned workaround (Architecture §Cat 11c — the CI gate itself lands in Story 8.2).

8. **One proof fixture wired end-to-end** — the harness, golden, and `update-regression` round-trip are demonstrated on at least one real fixture so all three are exercised today. The 2–3 representative Grenoble cutouts and the zero-tolerance CI gate are Story 8.2's deliverable.

## Tasks / Subtasks

- [x] Add the shared regression module in `src/` (AC: #1, #2, #5)
  - [x] Canonical edge-sequence hash function + per-route tuple extraction from a sidecar dict
  - [x] Golden read/write (canonical JSON) + `params_hash` over the pinned param set
  - [x] `main()` for the `update-regression` entry point (`--fixture` / `--all`, before/after diff)
  - [x] Register `update-regression` in `pyproject.toml` `[project.scripts]`
- [x] Implement `tests/e2e/test_pinned_regressions.py` over a fixture registry with explicitly-pinned params (AC: #3, #4, #8)
- [x] Implement `tests/unit/test_canonical_edge_hash.py` (AC: #6)
- [x] Commit the proof fixture's golden and add the README dev-note (AC: #7, #8)
- [x] Commit a queryable cache for the proof fixture (per user decision; see Completion Notes) (AC: #3, #8)

## Dev Notes

### Key design decisions (resolve these first)

- **What the harness runs against in 8.1.** Reuse the in-process `seeded_cache` + `run_query` fixtures in [tests/e2e/conftest.py:86](tests/e2e/conftest.py:86) to run `grenoble_small` and commit one golden at `tests/e2e/goldens/grenoble_small.json` as the proof fixture. The committed cache at `tests/fixtures/grenoble_small/cache/` holds only a manifest `.json` with **no `index.json`**, so `check_coverage` can't resolve a query against it directly — `seeded_cache` re-seeds into a tmp cache with a proper index, which is the queryable path. Story 8.2 commits directly-queryable prepared caches at `tests/e2e/fixtures/<region>/cache/` (with index), so the standalone `update-regression` script can run them with a plain `--cache-dir` and no patching. **Scope split:** 8.1 = machinery + canonical-hash + schema + one proof golden; 8.2 = the 2–3 real cutouts + per-fixture READMEs + the zero-tolerance CI gate + CI timing budget.
- **Where `objective` comes from.** The JSON sidecar carries `metrics` (`length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`) and `edges` (`[[u,v,key], …]`) but **not** `objective` ([output.py:146](src/steeproute/output.py:146)). The solver defines `Solution.objective = Σ(d_plus_m + d_minus_m)` over route edges ([grasp.py:336](src/steeproute/solver/grasp.py:336)), so derive `objective = metrics["d_plus_m"] + metrics["d_minus_m"]` from the sidecar. Do **not** thread `objective` through `Route`/`output.py` — Story 3.10 is done and `Route` deliberately drops the solver's objective. The values are self-consistent because the golden is generated and checked by the same code path; the redundancy with the two D fields is intentional (Architecture §Cat 11d keeps `objective` as a distinct field so a future weighted objective that diverges from D+ + D− would be caught). If that day comes, the sidecar must start carrying `objective` — note it, don't pre-build it.
- **Where the shared code lives.** `update-regression` is a `[project.scripts]` entry like `steeproute = "steeproute.cli.query:main"` ([pyproject.toml:84](pyproject.toml:84)), so its code must be importable from the installed package — put the canonical-hash + golden-IO + tuple-extraction in a `src/steeproute/` module (e.g. `regression.py`) and have both tests import it. `basedpyright` already covers `src`, `tests`, `devtools` ([pyproject.toml:170](pyproject.toml:170)).
- **Pin params explicitly; keep goldens robust to future change (AC #3, #4).** Solver output is highly sensitive to params, so a golden is only meaningful against the exact knobs that produced it. The current `run_query` helper passes only `center`/`radius`/`seed` and inherits CLI defaults for `theta`/`j_max`/`n`/etc. ([conftest.py:128-151](tests/e2e/conftest.py:128)) — a regression fixture must instead pin the full behavior-affecting set via `extra_args`, so a future default re-tuning doesn't silently invalidate it. **Key design choice:** compute `params_hash` over the **pinned set the registry actually specifies**, not over the whole `SolverParams` dataclass — otherwise adding a field to `SolverParams` flips the hash and forces a no-op golden rebuild on every fixture. With the pinned-set approach, an unset new field touches nothing the fixture pins, route output is unchanged, and `objective` + `canonical_edge_sequence_hash` stay identical → existing goldens stay green (AC #4). This story only defines what non-regression means and builds the harness; it does **not** design or anticipate any future feature. Future improvements (see [future-ideas.md](_bmad-output/planning-artifacts/future-ideas.md)) may alter solver behavior, but their delivery form is undecided — a new no-op-default param, or an entirely separate layer/CLI on top of the current one — and is out of scope here. Whatever form a future change takes, the obligation is only this: it must not alter existing goldens' output unless deliberately, and any new behavior it introduces gets its own regression coverage rather than a rewrite of an existing golden.

### Implementation guidance

- **Canonical hash — reuse the existing pattern.** `cache.py` already hashes canonical JSON: `json.dumps(canonical, sort_keys=True, separators=(",", ":"))` then `hashlib.sha256(blob.encode()).hexdigest()` ([cache.py:116-123](src/steeproute/cache.py:116)). Mirror it: sort the `[u,v,key]` triples by `(node_u, node_v, key)`, serialize, SHA256. The sort rule matches the canonical edge-ordering rule in Architecture §"Numerical and data discipline" (sort by `(node_u_id, node_v_id, key)`).
- **Reading the run output.** The query writes one `route-<i>.{html,json}` per route, 1-indexed ([output.py:106-155](src/steeproute/output.py:106)); the e2e happy-path test shows the read pattern — glob `route-*.json`, `json.loads`, read `route_index` / `metrics` / `edges` ([test_journey_1_happy_path.py:30-52](tests/e2e/test_journey_1_happy_path.py:30)). Pin `seed=42` (the conftest default) for determinism (FR29).
- **`params_hash`.** Hash the `SolverParams` the fixture runs with (the sidecar exposes them at `metadata.params` — [output.py:199](src/steeproute/output.py:199)) using the same canonical-JSON+SHA256 helper. Its job is to make a fixture's param drift loud, not to be cryptographically meaningful.
- **`update-regression` entry shape.** Keep it a plain `main()`; the `cli/query.py` `main()` + `run_entry_point` wrapper ([query.py tail](src/steeproute/cli/query.py)) is the heavier precedent if you want error mapping, but this is a dev tool — Click or argparse for `--fixture`/`--all` is fine. The before/after diff should show per-route field changes (old → new) so a golden update is reviewable.
- **Anti-patterns (Architecture §"Key anti-patterns").** Use `pathlib.Path` for all paths; atomic writes via the `cache.write_json_atomic` helper ([cache.py:376](src/steeproute/cache.py:376)) rather than re-rolling one; no module-level mutable state.

### Project Structure Notes

- **New:** `src/steeproute/regression.py` (shared core + `update-regression` `main`), `tests/e2e/test_pinned_regressions.py`, `tests/unit/test_canonical_edge_hash.py`, `tests/e2e/goldens/grenoble_small.json`.
- **Modified:** `pyproject.toml` (`[project.scripts]` += `update-regression`), `README.md` (dev-notes: goldens-update rationale + no-skip discipline).
- The Architecture project tree names the goldens dir `tests/e2e/goldens/` and the test `tests/e2e/test_pinned_regressions.py` ([architecture.md:846-850](_bmad-output/planning-artifacts/architecture.md:846)) — match those exactly. Tree example filenames (`grenoble_10km.json`, `pelvoux_8km.json`) are 8.2's real cutouts, not 8.1's.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 8.1] — story statement + ACs (lines 935-948); [#Story 8.2] (lines 950-964) for the 8.1/8.2 scope boundary.
- [Source: _bmad-output/planning-artifacts/future-ideas.md] — post-v1 improvements that may alter solver behavior; delivery form undecided and out of scope here. Context for AC #4 (existing goldens must survive future change untouched), not a design input.
- [Source: _bmad-output/planning-artifacts/architecture.md] — §Cat 11d hash scheme + golden tuple + `update-regression` (lines 989-1016); §Cat 11c CI gate / no-skip discipline (lines 957, 983-987); §"Numerical and data discipline" canonical edge ordering (line 754); project tree (lines 844-851).
- [Source: src/steeproute/output.py:106-155, 199] — sidecar shape: `route_index`, `metrics`, `edges` (`[u,v,key]`), `metadata.params`; no `objective` field.
- [Source: src/steeproute/solver/grasp.py:336] — `Solution.objective = Σ(d_plus_m + d_minus_m)`; [models.py:217-231](src/steeproute/models.py:217) `Solution` / `Route` (Route drops objective).
- [Source: src/steeproute/cache.py:116-123, 376] — canonical-JSON + SHA256 hashing pattern; `write_json_atomic`.
- [Source: tests/e2e/conftest.py:86-153] — `seeded_cache` / `run_query` fixtures (in-process, offline); [test_journey_1_happy_path.py:30-52](tests/e2e/test_journey_1_happy_path.py:30) sidecar-read pattern.
- [Source: pyproject.toml:84-87] — `[project.scripts]` entry-point pattern.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- **The 43 `test_cli_smoke.py` failures in the first full-suite run were a stale-install artifact, not a regression.** Those smoke tests subprocess `uv run steeproute …` to exercise the real entry-point shims. Adding the `update-regression` `[project.scripts]` entry changed `pyproject.toml`, so `uv run` tried to rebuild the package — which failed offline because `uv`'s own resolver couldn't validate pypi's TLS cert through the corporate proxy. Fixed by `uv sync --native-tls` (routes `uv` through the OS trust store, the same reason the app vendors `truststore`); all 43 smoke tests then passed. No code change involved — every non-smoke test (including the 7 new ones) passed in that same run.
- **`graph.pkl` was silently gitignored.** The committed queryable cache lives under a `cache/` directory, which `.gitignore:13` (`cache/`, the osmnx runtime-cache rule) matches. Added a scoped negation (`!tests/e2e/fixtures/**/cache/` + `/**`) so the regression fixture caches are tracked while the runtime `cache/` stays ignored — verified with `git check-ignore` and `git add -n`.

### Completion Notes List

- **Design decision confirmed with user:** exercise `update-regression` against a *committed queryable cache* today (rather than deferring its real run to 8.2). Generated `tests/e2e/fixtures/grenoble_small/cache/` — a full cache root (`index.json` + `areas/<hash>/{graph.pkl,bounds.geojson,manifest.json}`) — by seeding offline from the existing `tests/fixtures/grenoble_small/` OSM graphml + DEM (`regenerate_cache.py`). Both the harness and `update-regression` run it via a plain `--cache-dir`, no patching.
- **`src/steeproute/regression.py`** — shared core + `update-regression` `main()`. `canonical_edge_sequence_hash` sorts edges by `(node_u, node_v, key)` then SHA256s (reuses cache.py's canonical-JSON+SHA256 scheme); `params_hash` covers only the fixture's explicitly-pinned param set (so a new `SolverParams` field can't force a no-op golden rebuild — AC #4); `route_tuple` derives `objective = d_plus_m + d_minus_m` from the sidecar (no `objective` field exists in the sidecar; matches the solver's objective). `run_fixture` invokes the real query CLI in-process (click `CliRunner`) against the committed cache into a temp output dir and reads the real `route-*.json` sidecars. `FIXTURES` registry pins every behavior-affecting knob explicitly; `--time-budget` pinned high so termination is iteration-based (deterministic, FR29) — the run terminates `converged` on stagnation.
- **`update-regression`** registered in `pyproject.toml` `[project.scripts]`; runnable as `uv run update-regression [--fixture NAME | --all]` (or `python -m steeproute.regression`). Prints a per-route before/after diff and rewrites goldens atomically (`cache.write_json_atomic`); idempotent re-run reports `(no change)`.
- **`tests/e2e/test_pinned_regressions.py`** — parametrized over `FIXTURES`; runs each fixture and asserts the full golden matches exactly (zero tolerance). **`tests/unit/test_canonical_edge_hash.py`** — determinism (pinned to a known digest, proving cross-run stability), canonicalization over traversal order, single-edge-substitution mutation detection, parallel-key + direction sensitivity.
- **`tests/e2e/goldens/grenoble_small.json`** — committed golden (5 routes, seed 42).
- **README** — added a "Development notes → Pinned-regression goldens" section: the `update-regression` workflow, the mandatory commit-message rationale for golden updates, and the no-`skip`/`xfail` discipline (Architecture §Cat 11c). The full README replacement is Story 8.3/8.4.
- **Validation:** new files — basedpyright 0/0/0, `ruff format`/`ruff check` clean. Full suite **769 passed / 2 deselected** (was 762 at 7.5 close-out; +7 new tests), 0 failures. The CI zero-tolerance gate + the 2–3 representative cutouts are Story 8.2.

### File List

- `src/steeproute/regression.py` (new — shared regression core + `update-regression` entry point)
- `tests/e2e/test_pinned_regressions.py` (new — parametrized golden comparison)
- `tests/unit/test_canonical_edge_hash.py` (new — canonical-hash unit tests)
- `tests/e2e/goldens/grenoble_small.json` (new — committed golden)
- `tests/e2e/fixtures/grenoble_small/cache/**` (new — committed queryable cache root: `index.json` + `areas/4c348169d4d0bb0c/{graph.pkl,bounds.geojson,manifest.json}`)
- `tests/e2e/fixtures/grenoble_small/regenerate_cache.py` (new — offline cache regeneration script)
- `tests/e2e/fixtures/grenoble_small/README.md` (new — fixture documentation)
- `pyproject.toml` (modified — `[project.scripts]` += `update-regression`)
- `README.md` (modified — Development notes: pinned-regression goldens)
- `.gitignore` (modified — track regression fixture caches; runtime `cache/` still ignored)

## Change Log

- 2026-06-10: Implemented Story 8.1 — pinned-regression golden harness + `update-regression` workflow (Architecture §Cat 11c/11d). New `src/steeproute/regression.py` (canonical edge-sequence SHA256, pinned-set `params_hash`, sidecar-derived 5-field tuples, in-process fixture runner, golden read/write/diff, `update-regression` `main()`); `[project.scripts]` entry; `test_pinned_regressions.py` (zero-tolerance, parametrized) + `test_canonical_edge_hash.py`; committed proof fixture (queryable cache + golden) generated offline from `grenoble_small`; README dev-note; `.gitignore` exception for fixture caches. Per user decision, the `update-regression` round-trip runs against a committed queryable cache today. Status → review.
