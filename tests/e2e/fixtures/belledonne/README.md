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
query reads elevation from `graph.pkl`). `graph.pkl` is a pickled networkx graph, so it
is also sensitive to networkx/Python upgrades — the regression test surfaces any
incompatibility. Any golden change must be committed with an explicit rationale (see the
README "Development notes" section).
