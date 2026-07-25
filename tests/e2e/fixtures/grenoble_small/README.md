# grenoble_small — queryable cache fixture (regression harness)

The `cache/` directory here is a **full, queryable** `steeproute` cache root
(`steeproute/index.json` + `steeproute/areas/<hash>/{graph.pkl,bounds.geojson,manifest.json}`).
The `steeproute` query CLI runs against it with a plain `--cache-dir tests/e2e/fixtures/grenoble_small/cache`
— no network, no patching. It is the Story 8.1 proof fixture for the pinned-regression
harness (`tests/e2e/test_pinned_regressions.py` + `src/steeproute/regression.py`).

Distinct from `tests/fixtures/grenoble_small/` (the OSM graphml + DEM raster, plus a bare
manifest, consumed by the unit/integration pipeline tests). This cache is *seeded from* those
committed fixtures.

| Parameter | Value |
|---|---|
| Center | `45.260, 5.788` (Le Sappey-en-Chartreuse) |
| Seed radius (bbox half-side) | `2.0` km |
| Query radius (regression run) | `1.5` km (strictly contained — FR24 coverage) |
| Pinned params + seed | see `FIXTURES["grenoble_small"]` in `src/steeproute/regression.py` |

## Regenerating

```
uv run python tests/e2e/fixtures/grenoble_small/regenerate_cache.py   # rebuild the cache
uv run update-regression --fixture grenoble_small                     # refresh the golden
```

Regenerate after the setup-side pipeline or the OSM/DEM source fixtures change. `graph.pkl`
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
