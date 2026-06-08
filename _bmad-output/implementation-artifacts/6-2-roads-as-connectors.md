# Story 6.2: Roads as connectors

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want routes to use short minor-road segments that link steep trails,
so that loops requiring a brief paved connector (like the Grenoble trigger route) become constructible.

## Acceptance Criteria

1. **Minor roads admitted (fetch + filter).** A curated minor-road set (starting point: `residential, unclassified, service, living_street, tertiary`) is added to the Overpass fetch filter (`_OSM_CUSTOM_FILTER`) and admitted by `filter_trails` as connectors: kept regardless of `--untagged-trails` policy and never dropped by the SAC cap (roads carry no SAC grade). Trail behavior (existing `TRAIL_HIGHWAY_TAGS`, untagged policy, SAC cap) is unchanged.

2. **Major / non-trail roads still excluded, with multi-tag veto.** Major road types (e.g. `motorway`, `primary`) and bike-only `cycleway` are still dropped. A way multi-tagged with both a minor-road and a major-road tag (e.g. `["motorway", "service"]`) does **not** leak in — the major-road tag vetoes admission. (This is the opposite tie-break from the permissive `_is_trail_highway` rule, and is intentional.)

3. **Roads are never climbs and self-limit.** Admitted roads carry no SAC grade and do not become climbs in stage 8 / stage 9. No road-specific cost term is added; the existing vertical-effort (D+/D−) objective self-limits road use to genuine short links.

4. **Short road connectors reuse-exempt by the same rule.** A short road segment (`length_m < --l-connector`) is tagged `reusable=True` by the existing length-based rule in `contract_climbs` — exactly as a short trail connector — and may recur in a route. No road-specific reuse logic is introduced.

5. **Tests.** The two `test_osm.py` tests that assert roads are dropped are **inverted** to the new contract; new tests assert minor roads are admitted as connectors and that major roads — including the multi-tag `["motorway", "service"]` case — are excluded.

6. **Cache invalidation (documented).** Editing `pipeline/osm.py` changes `compute_pipeline_content_hash`, so `pipeline_content_hash` changes and prepared caches re-prepare on the next `steeproute setup` — the expected one-time cost of a setup-side data-surface change. No manual version bump or cache-key schema change is needed.

7. **Purity preserved.** `filter_trails` continues to return a new graph and never mutates its input.

## Tasks / Subtasks

- [x] Admit minor roads at the fetch + filter boundary (AC: #1, #2, #7)
  - [x] Define a curated minor-road tag set (`MINOR_ROAD_HIGHWAY_TAGS`, sibling to `TRAIL_HIGHWAY_TAGS`) and build `_OSM_CUSTOM_FILTER` from the union of both sets so the fetch filter stays aligned automatically
  - [x] In `filter_trails`, admit a minor-road edge as a connector regardless of untagged policy and without SAC-cap evaluation; trail handling unchanged
  - [x] Add `_is_minor_road_connector` with a major-road veto for multi-tag edges (admit iff a minor-road tag is present AND every tag is a trail or minor road)
- [x] Confirm downstream connector treatment is automatic (AC: #3, #4)
  - [x] Verified: `contract_climbs` is highway-agnostic (short road → `reusable=True` by length); `detect_climbs` is gradient-driven (roads never seed a climb). No new code — covered by focused tests (see Completion Notes for the flat-tail nuance)
- [x] Tests (AC: #5)
  - [x] Inverted the two drop-tests: `test_filter_trails_one_edge_per_highway_type_keeps_trails_and_minor_roads` and `test_filter_trails_multi_tag_admission_rules` (pins the major-road veto)
  - [x] New tests: minor road admitted under `include`/`exclude` and a low cap; short road tagged `reusable` at contraction; flat road never seeds a climb
- [x] Standard review (no human-review checkpoint required for this story — see Dev Notes)

## Dev Notes

**Scope.** This is the one **setup-side, cache-invalidating** change in Epic 6 and the only *feature* (not a bug fix). It realizes brief Item 3 / proposal §4B-B4. Roads were excluded at two places — the Overpass fetch filter (`_OSM_CUSTOM_FILTER`, [osm.py:36](src/steeproute/pipeline/osm.py:36)) and the trail-tag gate (`TRAIL_HIGHWAY_TAGS` / `_is_trail_highway`, [osm.py:32](src/steeproute/pipeline/osm.py:32)). Both must admit the curated minor-road set. Keep the two aligned (the [osm.py:35](src/steeproute/pipeline/osm.py:35) comment already states this contract — don't pay for ways you'll drop, don't drop ways you fetched).

**Why roads need to bypass the untagged/SAC path.** `filter_trails` ([osm.py:120](src/steeproute/pipeline/osm.py:120)) drops a `sac_scale=None` edge under `--untagged-trails exclude` and SAC-ranks the rest. Roads legitimately have no SAC grade, so the trail untagged/cap logic would wrongly drop them. Treat a minor-road edge as a distinct admit-as-connector branch *before* the untagged/SAC checks — don't try to thread roads through the trail policy.

**Multi-tag tie-break — opposite of trails.** `_is_trail_highway` ([osm.py:202](src/steeproute/pipeline/osm.py:202)) is permissive: any trail tag in a list-valued `highway` admits the edge. For roads the rule is the inverse — a minor-road tag admits, but **any** major/excluded tag in the list vetoes. The brief's canonical trap is a way tagged `["motorway", "service"]`: it must stay out. Decide the major-set membership deliberately (anything not in the trail set and not in the minor-road set is "excluded"); `cycleway` stays excluded (bike-only).

**No new reuse or cost code (AC #3, #4).** The `reusable` flag is purely length-based and set at contraction ([graph.py:155](src/steeproute/pipeline/graph.py:155): `reusable=data["length_m"] < l_connector`), independent of highway type — so a short road is exempt automatically. Climbs are detected by gradient (stage 8), so flat roads simply won't form climbs. The `D++D−` objective banks vertical effort, and roads are ~flat → no vertical to gain → the solver self-limits road use to genuine links. **Resist adding** a road-specific reuse path or cost term; AC #3/#4 are satisfied by existing mechanisms and should be *verified with a test*, not re-implemented.

**Cache (AC #6).** `compute_pipeline_content_hash` ([cache.py:140](src/steeproute/cache.py:140)) hashes the bytes of the pipeline + models source. Editing `osm.py` changes the hash automatically, so prepared areas re-fetch on next setup. This is the intended one-time invalidation — no cache-key schema change.

**Architecture conventions (must follow):** named module-scope constants over inline magic literals (mirror `TRAIL_HIGHWAY_TAGS`); `filter_trails` stays pure (returns a new graph, no input mutation — covered by `test_filter_trails_does_not_mutate_input`); networkx edge data treated read-only by downstream consumers.

**No human-review checkpoint for this story.** Per the proposal (§4D), the four bug fixes carry checkpoints (folded into 6.1 and 6.3); roads is a contained feature reviewed by standard means. The real-area benefit (the trigger loop needing a short road link) is confirmed in the 6.3 checkpoint once all of Epic 6 is in.

### Project Structure Notes

- Code touched: `pipeline/osm.py` only (fetch filter + `filter_trails` admit branch + multi-tag veto helper). No solver, contraction, validator, or output changes — roads ride existing connector/objective machinery.
- Tests: `tests/unit/test_osm.py` (invert the two drop-tests at [test_osm.py:201](tests/unit/test_osm.py:201) and [test_osm.py:218](tests/unit/test_osm.py:218); add admit-as-connector tests). A short-road `reusable` test belongs in `tests/unit/test_graph_contraction.py` (uses the existing `_make_edge` helper).
- The architecture stage-2 row and PRD data note are already updated for this behavior (applied during the correct-course) — no planning-doc edits in this story.

### References

- [Sprint change proposal — route discovery](_bmad-output/planning-artifacts/sprint-change-proposal-2026-06-07-route-discovery.md) §4B-B4, §4C (6.2 test note), §4D (standard review), §2 (cache invalidation)
- [Correct-course brief](_bmad-output/planning-artifacts/correct-course-brief-2026-06-05-route-discovery.md) Item 3
- [Epic 6 / Story 6.2](_bmad-output/planning-artifacts/epics.md:813)
- [Architecture §Pipeline stage 2](_bmad-output/planning-artifacts/architecture.md:247)
- Code: [_OSM_CUSTOM_FILTER](src/steeproute/pipeline/osm.py:36), [TRAIL_HIGHWAY_TAGS](src/steeproute/pipeline/osm.py:32), [filter_trails](src/steeproute/pipeline/osm.py:120), [_is_trail_highway](src/steeproute/pipeline/osm.py:202), [contract_climbs reusable tag](src/steeproute/pipeline/graph.py:155), [compute_pipeline_content_hash](src/steeproute/cache.py:140)
- Previous story: [6.1 — route-discovery bug fixes](_bmad-output/implementation-artifacts/6-1-route-discovery-bug-fixes-junction-split-sac-cap-undirected-distinctness.md) (purity + named-constant conventions; SAC-cap pre-filter now calls `filter_trails` query-side, so the road-admit branch is exercised at both setup and query time)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Amelia / dev-story)

### Debug Log References

Full suite: `uv run pytest` → 699 passed, 2 deselected (`@pytest.mark.live`), ~138s. Lint (`ruff check`) and type-check (`basedpyright src/steeproute`) clean. Format clean except the pre-existing `tests/unit/test_dem_download.py` drift (unrelated; flagged in 6.1, untouched here).

### Completion Notes List

- **Minor-road admit (`pipeline/osm.py`).** Added `MINOR_ROAD_HIGHWAY_TAGS = {residential, unclassified, service, living_street, tertiary}` and rebuilt `_OSM_CUSTOM_FILTER` from `sorted(TRAIL_HIGHWAY_TAGS | MINOR_ROAD_HIGHWAY_TAGS)` so the Overpass fetch regex and the filter logic can never drift.
- **Single classifier (`classify_highway`, review #3).** The two parallel predicates (`_is_trail_highway` permissive / `_is_minor_road_connector` restrictive) were replaced by one `classify_highway(value) -> "trail" | "connector" | None` plus a `_highway_tags` normalizer. Trail wins permissively (any trail tag); a road is admitted only if it carries a minor-road tag AND every tag is a minor road (so `["motorway","service"]` is vetoed, `["service","residential"]` admitted, `["service","footway"]` is a trail). `filter_trails` switches on the category: trail → existing untagged/SAC path; connector → admit, bypassing the untagged policy.
- **Over-cap road respects the cap (review #4).** A road that carries an over-cap `sac_scale` is now dropped by `--difficulty-cap` like a trail; an untagged/unrecognized-sac road is still admitted. Closes the unguarded cap-bypass the review flagged.
- **Whitespace tolerance (review #4).** `_highway_tags` strips tags before lookup, matching `max_sac_rank`'s existing tolerance, so `"service "` / `" path"` aren't silently dropped.
- **AC #3 — "roads never become climbs."** Verified the genuine guarantee: a road never **seeds** a climb (`_qualifies_as_seed` requires per-edge slope ≥ `min_climb_slope`), so a road-only network yields zero climbs (`test_flat_road_never_seeds_a_climb`). Review raised that a flat road could be *absorbed* into a steep climb via the running-average extension; on analysis this is **not a defect** — absorption only happens when the road is the sole steep continuation of a continuous ascent (correctly one climb), and the distinct-trail-junction case is split out by 6.1's junction-split. No road-specific carve-out added (deliberately — roads are low-slope trails).
- **AC #4 — connector reuse is automatic.** `contract_climbs` tags `reusable` purely by `length_m < l_connector`, ignoring `highway`, so a short road is reuse-exempt with no new code (`test_minor_road_connector_follows_length_based_reuse_rule`). No road cost term.
- **AC #6 — cache.** Editing `pipeline/osm.py` changes `compute_pipeline_content_hash` automatically, so prepared caches re-prepare once on next `steeproute setup`. No cache-key schema change.
- **Real road coverage (review #2).** The committed `grenoble_small` fixture was fetched with the old trail-only filter (zero roads). Regenerated via live Overpass with the road-inclusive filter (now single-sourced from `osm._OSM_CUSTOM_FILTER`): 844 nodes / 2086 edges (1498 trail + 588 minor-road connector), 1.18 MB (< 5 MB cap). DEM fixture unchanged (same bbox + 100 m pad already covers road nodes). Re-pinned the topology/length/climb baselines across the affected fixture tests; fixed the include-vs-exclude invariant to count untagged *trails* only (roads are admitted under both policies); added a fixture sanity test asserting road connectors are present and survive `filter_trails`.

### File List

- `src/steeproute/pipeline/osm.py` — `MINOR_ROAD_HIGHWAY_TAGS`; `_OSM_CUSTOM_FILTER` rebuilt from trail+road sets; unified `classify_highway` + `_highway_tags` (replacing `_is_trail_highway`/`_is_minor_road_connector`); `filter_trails` category switch with over-cap-road cap check
- `tests/unit/test_osm.py` — inverted `..._keeps_trails_and_minor_roads`; new `..._multi_tag_admission_rules`, `..._admits_minor_road_connector_regardless_of_policy_and_cap`, `..._sac_tagged_road_respects_difficulty_cap`, `..._tolerates_trailing_whitespace_in_highway_tag`, `test_classify_highway` (parametrized), `test_fixture_contains_admitted_road_connectors`; fixed `..._include_vs_exclude_diff_equals_untagged_trail_count`
- `tests/unit/test_graph_contraction.py` — new `test_minor_road_connector_follows_length_based_reuse_rule`
- `tests/unit/test_climb_detection.py` — new `test_flat_road_never_seeds_a_climb`
- `tests/fixtures/grenoble_small/osm_graph.graphml` — regenerated, road-inclusive (binary)
- `tests/fixtures/grenoble_small/regenerate.py` — fetch filter single-sourced from `osm._OSM_CUSTOM_FILTER`
- `tests/fixtures/grenoble_small/README.md` — updated counts / filter / capture date
- `tests/integration/test_pipeline_end_to_end.py`, `tests/integration/test_climb_detection_fixture.py`, `tests/e2e/test_steeproute_setup.py` — re-pinned fixture baselines

### Review Findings

Lightweight `/code-review` (high effort: 7 finder angles + verification), 2026-06-08. Core change verified correct (multi-tag veto, control-flow restructure, cache, downstream None/SAC handling). Disposition:

- **[#1 climb-absorption] Not a defect** — discussed with user; roads = low-slope trails, absorption only on a continuous ascent; no carve-out (would violate the story's intent). Dropped.
- **[#2 fixture coverage] Fixed** — regenerated the fixture with real road data (user chose real-data coverage over a deterministic synthetic test).
- **[#3 two parallel predicates] Fixed** — unified into `classify_highway`.
- **[#4 over-cap road + whitespace] Fixed** — cap now applies to SAC-tagged roads; tags whitespace-stripped.
- **[curation: `pedestrian` excluded] Noted, not actioned** — `pedestrian` (town-centre walkable links) is outside the curated minor-road set; product call, out of scope.
- **[incidental] Pre-existing `compute_edge_metrics` inf-gradient on a denormal-length edge** — surfaced by Hypothesis during this story (fails on clean `HEAD`); fixed separately by tightening `is_valid_for_metrics` to require a real positive 2D length. Committed apart from the roads change.

## Change Log

| Date | Version | Description |
|------|---------|-------------|
| 2026-06-08 | 0.1 | Story drafted (create-story) |
| 2026-06-08 | 1.0 | Implemented roads-as-connectors: `MINOR_ROAD_HIGHWAY_TAGS`, fetch-filter + `filter_trails` admit branch, multi-tag major-road veto. 2 tests inverted + 3 new. Full suite 682 passed; lint/format/type-check clean. Status → review. |
| 2026-06-08 | 1.1 | Code-review applied: unified `classify_highway` classifier, over-cap-road cap + whitespace tolerance, regenerated fixture with real road data + re-pinned baselines, fixed untagged-count invariant, added road-coverage tests. Incidental pre-existing `compute_edge_metrics` inf-gradient bug fixed separately. Full suite 699 passed; lint/type-check clean. |
