# belledonne — queryable cache fixture (regression harness)

A pinned Story 8.2 regression fixture: a **full, queryable** `steeproute` cache root
(`steeproute/index.json` + `steeproute/areas/<hash>/{graph.pkl,bounds.geojson,manifest.json}`)
representing a Belledonne-massif cutout (crystalline, steep alpine terrain). The
`steeproute` query CLI runs against it with a plain
`--cache-dir tests/e2e/fixtures/belledonne/cache` — offline, no patching. Registered
as `FIXTURES["belledonne"]` in [`src/steeproute/regression.py`](../../../../src/steeproute/regression.py)
and asserted at zero tolerance by `tests/e2e/test_pinned_regressions.py`.

| Parameter | Value |
|---|---|
| Center | `45.186753, 5.961482` |
| Seed radius (`steeproute-setup --radius`) | `2.0` km |
| Query radius (regression run) | `1.5` km (strictly contained — FR24 coverage) |
| DEM source | IGN Géoplateforme WMS, layer `ign-rgealti-highres` (RGE ALTI) |
| Pinned params + seed | `seed=42`; see `FIXTURES["belledonne"]` / `_PINNED_PARAMS` |
| Committed cache size | ~360 KB |
| Prepared | 2026-06-10, commit `46332cb` |

## Regenerating

```
uv run steeproute-setup --center 45.186753,5.961482 --radius 2.0 \
  --cache-dir tests/e2e/fixtures/belledonne/cache   # rebuild the cache (needs network)
uv run update-regression --fixture belledonne        # refresh the golden
```

Unlike `grenoble_small`, this cache is prepared from **real** OSM (Overpass) + DEM (IGN
WMS) downloads — there is no committed offline source, so regeneration needs network.
The `dem/` cache dir setup writes under the root is intentionally **not** committed (the
query reads elevation from `graph.pkl`). `graph.pkl` holds the schema-v3 pickled payload
(the graph, with per-edge `geometry` not stored at all — Story 16.3), so it is still sensitive to
networkx/Python upgrades — the regression test surfaces any incompatibility. Any golden
change must be committed with an explicit rationale (see the README "Development notes"
section).

**Manifest migrated to schema v3 in place (Story 15.2, 2026-07-25.)** The rotated-rectangle
`area` block bumped `manifest.json`'s `schema_version` 2 → 3, and `Manifest.from_dict`
rejects any non-current version — so without this the pinned golden would fail at exit 2.
Because this cache cannot be rebuilt offline (and a live rebuild would fetch different
OSM/DEM data and force a golden rebake), only `schema_version` was edited: a square's
`area` block is byte-identical across v2 and v3, and `graph.pkl` was not touched. Same
in-place-conversion approach Story 13.2 used for the v1 → v2 payload change. Verified: the
golden passes unchanged, **no rebake**.

**`graph.pkl` converted in place to payload schema v3 (Story 16.3, 2026-07-28.)** v3 stops
storing per-edge `geometry`: it is the resampled polyline in `(lon, lat)`, and
`vertices_resampled` already carries the same vertices as `(lat, lon, elevation)`. Because this
cache cannot be rebuilt offline, the payload was converted in place — same approach as the v1 → v2
conversion and the manifest migration above: every stored geometry was first verified equal to its
edge's `vertices_resampled`, then the file was rewritten with the identical graph object and no
coordinate arrays, and the converted entry was re-read and compared attribute-by-attribute against
the pre-conversion graph. The payload version is independent of `manifest.json`'s, which stays at 3.
Verified: the golden passes unchanged, **no rebake**. (A legacy v2 entry would still read — the read
path ignores the arrays rather than rebuilding them — so the conversion is about entry size, not
readability.)
