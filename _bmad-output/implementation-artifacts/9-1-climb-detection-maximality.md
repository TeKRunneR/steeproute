# Story 9.1: Climb-detection maximality (review finding #7)

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want every detected climb to be genuinely maximal — rooted at its true steep bottom regardless of OSM node-id labeling,
So that no steep chain-start is silently demoted to a connector and routes can board climbs from the bottom.

## Acceptance Criteria

1. `detect_climbs` returns the full maximal contiguous steep chain **independent of node-id labeling and seed order**. Concretely: the same steep chain topology + metrics under two different node labelings yields the **same** climbs (same edge multiset per climb), and a steep chain-bottom edge is always captured into its climb — never orphaned and dropped by the `min_climb_ground_length` gate because a downstream mid-chain edge seeded first and consumed its continuation. This satisfies the "maximal contiguous edge-sequences" guarantee Story 3.2 (epics.md §Story 3.2) stated but did not meet.

2. A **fail-first** regression test (fails on pre-fix `detect_climbs`, passes after) asserts AC #1 on a hand-built graph. Basis: `tmp/repro_findings.py::repro_finding_7` — one identical 3-edge steep chain (each 200 m / +100 m, slope 0.50; `min_climb_ground_length` above one edge, below two) under a "good" labeling (bottom = smallest id) and a "bad" labeling (a mid edge sorts first). Both must now produce the same single 3-edge maximal climb, and the steep bottom edge appears in both outputs.

3. The fix preserves the existing stage-8 contract: FR29 byte-identical determinism (no RNG; all ordering from a pinned sort / explicit tie-break), **edge-disjointness** (each base edge in ≤ 1 climb — Story 3.3 back-mapping injectivity depends on it), the node-monotonicity / no-zigzag guard, climb-aggregate identities (`length_m`/`d_plus_m`/`avg_slope` = sum/sum/ratio of underlying edges within `abs_tol=1e-9`), and input-graph purity (no mutation).

4. The existing stage-8 unit + property tests (`tests/unit/test_climb_detection.py`, `tests/unit/test_climbs.py`), the contraction tests (`tests/unit/test_graph_contraction.py`), and the Story 3.3 back-mapping injectivity property all still pass. Any existing test that pinned the **old non-maximal / forward-only / seed-order behavior** is updated to the corrected expectation (per correct-course §4C), with a one-line note on what changed.

5. The Grenoble integration baselines in `tests/integration/test_climb_detection_fixture.py` (`_BASELINE_CLIMB_COUNT`, `_BASELINE_TOTAL_D_PLUS_M`) are re-recorded if the maximality fix shifts them — these are explicitly regression snapshots that "an algorithm change that shifts these is intentional and requires re-recording" (that file's module docstring). Update the values + the derivation note; do **not** widen the ±10 % band. (Cross-tier *golden* rebake — fast + realistic — is deferred to Story 9.3, not done here.)

6. All four CI gates green on Windows: `uv run ruff check`, `uv run ruff format --check`, `uv run basedpyright` (0/0/0), `uv run pytest --cov`. `pipeline/climbs.py` holds its 95 % pure-logic coverage floor on the changed code path. No new runtime deps.

## Tasks / Subtasks

- [x] Task 1: Make `detect_climbs` genuinely maximal regardless of seed order (AC: #1, #3). Dev picks the approach — backward extension from the seed, descending-slope seeding, or equivalent — so long as edge-disjointness, node-monotonicity, FR29 determinism, and the aggregate identities all hold. Keep the existing `_qualifies_as_seed` / running-average-slope / `min_climb_ground_length` semantics; do not touch `compute_edge_metrics` (stage 7).
- [x] Task 2: Add the fail-first relabel-isomorphism regression test (AC: #2). Confirm it FAILS on pre-fix code, then passes. Port the topology from `repro_finding_7`; assert same-output-under-both-labelings and bottom-edge-always-captured.
- [x] Task 3: Re-run and reconcile the existing stage-8 / contraction / injectivity suites; update any test that pinned the old behavior, and re-record the Grenoble fixture baselines if shifted (AC: #4, #5).
- [x] Task 4: Verify all four CI gates + the coverage floor on the changed path (AC: #6).

## Dev Notes

### The defect (what to fix)

`detect_climbs` (`src/steeproute/pipeline/climbs.py:124`) iterates seeds in `sorted(edge_data.keys())` order (sorted `(u, v, key)`) and only ever extends **forward** via `_pick_steepest_extension`. If a mid-chain edge seeds before the chain's bottom edge, the forward walk consumes mid + upstream edges and emits them as a climb; the genuinely-steep **bottom edge**, processed later, can no longer extend (its continuation is already `consumed`) and — if shorter than `min_climb_ground_length` alone — is silently dropped, demoting a real climb-bottom to a connector. The output is therefore "maximal-forward-from-seed only," dependent on arbitrary OSM node-id labeling — contradicting the function's own docstring and Story 3.2's AC.

### Constraints the fix must keep (do not regress)

- **Edge-disjointness** — the `consumed` set guarantees each base edge lands in ≤ 1 climb. Story 3.3's super-edge back-mapping injectivity assumes this with no extra dedup. Whatever traversal you adopt must preserve it.
- **Node-monotonicity** — the existing `visited_nodes` guard keeps a climb a path, not a zigzag walk through bidirectional/parallel edges on saddle terrain (added in Story 3.2 review). Backward extension must respect the same no-revisit rule.
- **FR29 determinism** — no RNG; every ordering decision comes from a pinned sort + explicit tie-break (current code breaks slope ties on `(node_v, key)`). A backward walk needs an equally explicit, deterministic incoming-edge order.
- **Aggregate identities + purity** — `Climb.length_m`/`d_plus_m`/`avg_slope` stay sum/sum/ratio of underlying edge metrics (`abs_tol=1e-9`); the input graph is never mutated (snapshot-dict-read pattern already in place).

### Blast radius (informational)

Maximal climbs change which base edges become super-edges vs. stay connectors → the contracted graph (Story 3.3) shifts → downstream route output shifts. That cascade (metamorphic invariants, Story 3.7 ratio, both golden tiers) is **revalidated and rebaked in Story 9.3**, not here. This story's scope is the algorithm fix + its fail-first regression test + reconciling directly-affected stage-8/contraction unit tests and the climb-detection fixture baselines. No cache invalidation: stage 8 runs query-side, `pipeline_content_hash` is unaffected.

### Project Structure Notes

- **Modified:** `src/steeproute/pipeline/climbs.py` — `detect_climbs` traversal + helpers as needed. Stage 7 (`compute_edge_metrics` and its helpers) untouched.
- **New:** one fail-first regression test. Place with the other stage-8 unit tests (`tests/unit/test_climb_detection.py`) unless the relabel-isomorphism framing reads better as its own small file — dev's call; mirror the existing `_chain_graph` helper style.
- **Possibly updated:** `tests/unit/test_climb_detection.py`, `tests/unit/test_climbs.py`, `tests/unit/test_graph_contraction.py` (only where they pinned old behavior); `tests/integration/test_climb_detection_fixture.py` baselines.
- **Out of scope:** golden rebake, metamorphic/3.7 revalidation, oracle docstring, GRASP (#10 is Story 9.2) — all Story 9.2/9.3.

### Testing standards summary

- Float-equality on aggregates uses `math.isclose(..., abs_tol=1e-9)`, never `==` (Architecture §"Numerical and data discipline").
- The new test must demonstrably fail on pre-fix code — capture that in the Dev Agent Record (project convention for regression-pinned bug fixes, per correct-course §4C and the Epic 4/5/6 precedent).
- Coverage floor 95 % on the changed `climbs.py` path (Architecture §Cat 11e).

### References

- [Source: _bmad-output/planning-artifacts/epics.md §"Story 9.1"](_bmad-output/planning-artifacts/epics.md) — AC source-of-truth; §"Story 3.2" latent-gap note
- [Source: _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-18-route-discovery-quality.md](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-18-route-discovery-quality.md) — §4B (B1), §4C, §2 technical impact / determinism
- [Source: src/steeproute/pipeline/climbs.py:124](src/steeproute/pipeline/climbs.py) — `detect_climbs` + `_pick_steepest_extension` (the forward-only walk to fix)
- [Source: tmp/repro_findings.py:252](tmp/repro_findings.py) — `repro_finding_7`, the fail-first test basis
- [Source: tests/integration/test_climb_detection_fixture.py:50](tests/integration/test_climb_detection_fixture.py) — regression-snapshot baselines + re-record convention
- [Source: _bmad-output/implementation-artifacts/3-2-pipeline-stage-8-climb-detection.md](_bmad-output/implementation-artifacts/3-2-pipeline-stage-8-climb-detection.md) — prior dev notes: directional-slope metric, greedy-steepest branching, node-monotonicity, FR29 tie-break

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (1M context) (`claude-opus-4-8[1m]`), via Claude Code CLI on Windows 11.

### Debug Log References

**Environment:** Python 3.13 / `uv`. No new runtime or dev deps. The editable `steeproute` build was stale relative to git HEAD at session start, so `uv run` tried to rebuild and hit the corporate-TLS `invalid peer certificate: UnknownIssuer` flake (uv's bundled cert bundle lacks the Hardis TLS-inspection CA). Settled it once with `uv sync --native-tls` (uses the Windows cert store), then ran tests with `uv run --no-sync`. After settling, the e2e CLI smoke suite passes (43/43) — see Completion Notes.

**Fail-first proof (AC #2):** with the source fix stashed (`git stash push -- src/steeproute/pipeline/climbs.py`), `test_maximal_climb_is_independent_of_node_id_labeling` FAILS — the "bad" labeling returns a 2-edge climb `[(5,30,0),(30,40,0)]` with the steep bottom `(10,5,0)` orphaned and dropped (200 m < 300 m floor). With the fix restored it passes (one 3-edge climb under both labelings).

**Test runs (all `--no-sync`):**

```
pytest tests/unit/test_climb_detection.py tests/unit/test_climbs.py   → 26 passed (was 25; +1 new regression test)
pytest tests/unit tests/integration                                   → 692 passed, 2 deselected
ruff check / ruff format --check (changed files)                       → clean
basedpyright src/steeproute/pipeline/climbs.py                         → 0 errors, 0 warnings, 0 notes
pytest <climb tests> --cov=src/steeproute                             → climbs.py 99% (135 stmts, 2 missing:
                                                                         lines 101/106, pre-existing stage-7
                                                                         is_valid_for_metrics branches)
```

### Completion Notes List

**Approach — backward extension to the true bottom.** `detect_climbs` now grows each candidate from its seed in two phases: first *backward* (new `_pick_steepest_backward` prepends the steepest qualifying-as-seed incoming edge until none remains), then the existing *forward* greedy. The candidate is a `deque` so backward edges `appendleft` and the emitted tuple still runs bottom → top. Key property that keeps the change minimal and safe: every backward edge is itself `≥ min_climb_slope`, so every prefix measured from the new bottom stays `≥ min_climb_slope` (weighted average of two `≥ θ` quantities) — no running-average recheck is needed, and the forward semantics are byte-for-byte unchanged. The backward picker mirrors the forward one's guards (consumed, candidate-set, node-monotonicity) and tie-breaks deterministically on `sorted(in_edges)` → `(node_u, key)` for FR29.

**Why every existing unit test stayed green.** All pre-existing unit tests build chains whose bottom already sorts first (`_chain_graph` starts at node 0), so the backward phase is a no-op and behavior is identical. The fix only changes output where a chain's bottom does *not* sort first — exactly the defect.

**Fixture baseline re-record (AC #5).** On the Le Sappey fixture the maximality fix re-rooted several chains, shifting the climb count 50 → 45 and total climb D+ 8065.5 → 7731.1 m (count drift landed right at the ±10 % band edge). Re-recorded `_BASELINE_CLIMB_COUNT`/`_BASELINE_TOTAL_D_PLUS_M` with rationale, per that file's "an algorithm change that shifts these requires re-recording" convention. Band left at ±10 %.

**Deferred to Story 9.3 (intended downstream shifts — NOT regressions).** The contracted-graph change ripples into route output, so 5 e2e tests now drift: the 4 `test_pinned_regressions.py` goldens and `test_degradation.py::test_relaxed_jmax_produces_more_routes` (grenoble_small now returns 2 routes at both tight and relaxed J_max). These are the blast radius the correct-course routes through Story 9.3's single golden rebake + end-to-end revalidation — done *after* 9.2, because 9.2 shifts route output again and rebaking now would just be redone. All must-stay-green tests for this story (every unit + integration test, including the 3.8 metamorphic suite and the 3.7 GRASP-vs-exhaustive gate) pass.

**Environmental, not a regression (resolved).** Before settling the build, 43 `tests/e2e/test_cli_smoke.py` tests failed: each shells out to a fresh `uv run <cli>` subprocess that tried to rebuild the stale editable install and hit the corporate-TLS `invalid peer certificate: UnknownIssuer` flake. This is the known Hardis-network build flake — recovery is `uv sync --native-tls` (use the OS cert store). After that, all 43 pass. Unrelated to this change. The full e2e run then shows exactly **5 failed, 81 passed** — the 5 being the deferred goldens (4) + degradation (1) above.

### File List

**Modified:**
- `src/steeproute/pipeline/climbs.py` — `detect_climbs` grows candidates backward-then-forward (bottom-rooted, labeling-independent); new private helper `_pick_steepest_backward`; `deque` import; module + function docstrings updated for the maximality guarantee. Stage-7 code (`compute_edge_metrics` and helpers) untouched.
- `tests/unit/test_climb_detection.py` — new `_steep_chain` helper + `test_maximal_climb_is_independent_of_node_id_labeling` (fail-first relabel-isomorphism regression test).
- `tests/integration/test_climb_detection_fixture.py` — re-recorded regression-snapshot baselines (50→45 climbs, 8065.5→7731.1 m D+) with rationale; module docstring notes the Story 9.1 re-record.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story `9-1-climb-detection-maximality` walked `ready-for-dev → in-progress → review`; epic-9 → `in-progress`.

### Change Log

| Date | Author | Description |
|---|---|---|
| 2026-06-18 | Yann (Claude Opus 4.8) | Story 9.1 implemented (review finding #7): `detect_climbs` now roots every climb at its true steep bottom via backward extension, making output independent of OSM node-id labeling / seed order and finally meeting Story 3.2's "maximal" AC. New fail-first relabel-isomorphism regression test (verified red on pre-fix code). Fixture baselines re-recorded (50→45 climbs / 8065.5→7731.1 m D+). All 692 unit+integration tests green (incl. 3.7 gate + 3.8 metamorphic); ruff/format/basedpyright clean; climbs.py 99% coverage. Golden rebake + e2e revalidation of the downstream route-output shift deferred to Story 9.3 per correct-course sequencing. |
