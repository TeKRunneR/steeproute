# grenoble_small — queryable cache fixture (regression harness)

The `cache/` directory here is a **full, queryable** `steeproute` cache root
(`steeproute/index.json` + `steeproute/areas/<hash>/{graph.pkl,bounds.geojson,manifest.json}`).
The `steeproute` query CLI runs against it with a plain `--cache-dir tests/e2e/fixtures/grenoble_small/cache`
— no network, no patching. It is the Story 8.1 proof fixture for the pinned-regression
harness (`tests/e2e/test_pinned_regressions.py` + `src/steeproute/regression.py`).

Distinct from `tests/fixtures/grenoble_small/` (the OSM graphml + DEM raster, plus a bare
manifest, consumed by the unit/integration pipeline tests). This cache is *seeded from* those
committed fixtures.

This cache root holds **two prepared entries** — an axis-aligned square and a rotated
rectangle (Story 15.3) — both seeded from the same committed OSM/DEM fixtures and both
listed in `index.json`. They back two independent goldens.

| Parameter | Square entry | Rotated entry |
|---|---|---|
| Center | `45.260, 5.788` (Le Sappey-en-Chartreuse) | same |
| Seed area | radius `2.0` km (bbox half-side) | `3.4 x 2.0` km box at bearing `45°` |
| Query area (regression run) | radius `1.5` km | `3.0 x 1.6` km box at bearing `45°` |
| Fixture | `FIXTURES["grenoble_small"]` | `FIXTURES["grenoble_small_rotated"]` |

Both query areas are strictly contained in their own seed area (FR24 coverage), and the
square query is *not* contained in the rotated entry — so each golden deterministically
resolves to its own entry even though they share a cache root.

**Why the rotated entry has to be prepared, not just queried.** The query area selects a
cache entry; it never clips the search. A rotated *query* against the square entry would
therefore reproduce the square golden's routes exactly and pin nothing. What Epic 15
actually changes is **setup**: the graph is truncated to the rotated ring. The rotated
entry carries that truncated graph (645 KB vs the square's 1.5 MB — it retains ~43% of the
square's area), so its golden is a real regression detector for the rotated geometry.

## Regenerating

```
uv run python tests/e2e/fixtures/grenoble_small/regenerate_cache.py   # rebuild both entries
uv run update-regression --fixture grenoble_small                     # refresh the square golden
uv run update-regression --fixture grenoble_small_rotated             # refresh the rotated golden
uv run update-regression --fixture grenoble_small_rotated --tier realistic
```

The regenerator's offline stage-1 stand-in mirrors the real fetch's shape handling: a
square area gets the committed graphml untouched (it *is* the square bbox fetch), while a
non-square area is passed through osmnx's own `truncate_graph_polygon` +
`largest_component`, the same two steps `graph_from_polygon` finishes with.

**The rotated entry was added to the committed tree without rewriting the square one**
(Story 15.3): the square entry's `manifest.json` and `graph.pkl` are byte-for-byte as
committed, only `index.json` gained a row. Re-running the script above regenerates both
from scratch, which is equivalent but rewrites the square manifest's timestamps.

Regenerate after the setup-side pipeline or the OSM/DEM source fixtures change. Each `graph.pkl`
holds the schema-v2 pickled payload (graph minus geometry + ragged coordinate arrays,
Story 13.2), so it is still sensitive to networkx/Python upgrades — the regression test
(and `update-regression`) will surface any incompatibility. Any golden change must be
committed with an explicit rationale (see the README "Development notes" section).

**Manifest migrated to schema v3 in place (Story 15.2, 2026-07-25.)** The rotated-rectangle
`area` block bumped `manifest.json`'s `schema_version` 2 → 3, and `Manifest.from_dict`
rejects any non-current version. Unlike the other three fixtures this cache *can* be
rebuilt offline via `regenerate_cache.py`, but the manifest was migrated by editing
`schema_version` alone for consistency with them and to keep the diff auditable: a square's
`area` block is byte-identical across v2 and v3, and `graph.pkl` was not touched. Verified:
the golden passes unchanged, **no rebake**.
