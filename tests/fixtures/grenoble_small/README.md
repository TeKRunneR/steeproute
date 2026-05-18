# grenoble_small — committed real-data test fixture

Captured OSM graph + IGN RGE ALTI DEM raster for a small Grenoble-area cutout,
used by the unit and integration tests for `pipeline/osm.py` (Story 2.1),
`pipeline/smoothing.py` (Story 2.2), and `pipeline/dem.py` (Story 2.3).

## osm_graph.graphml

| Parameter | Value |
|---|---|
| Center | `45.260, 5.788` (Le Sappey-en-Chartreuse) |
| `dist` (bbox half-side) | `2000` m |
| `dist_type` | `bbox` (returns ways inside a `2*dist`-side square, **not** a disk) |
| `custom_filter` | `["highway"~"path\|footway\|track\|steps\|bridleway"]` |
| `useful_tags_way` | osmnx default + `sac_scale` |
| `retain_all` | `False` (largest connected component only) |
| `simplify` | `True` (osmnx default) |
| osmnx version | `2.1.0` |
| Captured | 2026-05-06 |
| File size | 723 KB |
| Counts | 468 nodes, 1208 edges |

### Why this center

Le Sappey-en-Chartreuse is a hiking village in the Chartreuse Massif north of
Grenoble. The 2 km bbox captures genuine `sac_scale` variety — T1 (`hiking`)
through T5 (`demanding_alpine_hiking`) are all represented, plus a handful of
osmnx-merged list-valued `sac_scale` edges that exercise `filter_trails`'s
max-rank handling. That gives the difficulty-cap test five SAC boundaries to
sweep against, and the include-vs-exclude test a balanced ~50/50 split between
tagged and untagged edges to discriminate.

### A footgun worth recording

osmnx's default `useful_tags_way` does **not** include `sac_scale`. Both
`regenerate.py` and the production `osm_load` extend the list before fetching
— without that, every captured edge has `sac_scale=None` regardless of how
well the area is tagged in OSM. Initial fixture-capture attempts on this same
center (and at the Bastille / Chamrousse) appeared to show "no SAC tagging
anywhere"; the data was always there in OSM, the fetch was just dropping it.

### Regenerating

```
python regenerate.py
```

`regenerate.py` uses the OS certificate store via the `truststore` package
(installed as a dev dep), so it Just Works behind corporate TLS-intercepting
proxies whose root CA is in the operating-system trust store but not in
`certifi`'s vendored bundle. No insecure-mode flag is offered: if your
environment can't validate Overpass's certificate via the OS store, the right
fix is to repair the trust chain — not to skip verification.

The fixture content is sanity-checked by `tests/unit/test_osm.py` on every CI
run, so a tampered or empty download would fail there.

## dem.tif

IGN RGE ALTI 5 m extract covering the same bbox as `osm_graph.graphml`.

| Parameter | Value |
|---|---|
| Source | IGN Géoplateforme WMS, layer `ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES` (RGE ALTI HIGHRES) |
| Endpoint | `https://data.geopf.fr/wms-r/wms` (open, no API key) |
| Format requested | `image/x-bil;bits=32` (raw IEEE-754 float32, little-endian — verified empirically) |
| Bbox (WGS84) | Le Sappey ± 2100 m (OSM 2 km bbox + 100 m padding ring) |
| Grid | `840 × 840` pixels ≈ 5 m east-west / 5 m north-south (close to RGE ALTI's 5 m native) |
| CRS on disk | `EPSG:4326` (WGS84 lon/lat) — IGN reprojects on the fly per request |
| dtype | `float32` |
| Elevation range | 356.4 m to 1637.4 m (Chartreuse Massif) |
| nodata | None (the bbox is fully covered) |
| Captured | 2026-05-18 |
| File size | ~754 KB on disk (deflate + float predictor) |

The 100 m padding ring extends the DEM beyond the OSM fixture's 2 km bbox
half-side because `osmnx`'s `dist_type="bbox"` includes ways whose simplified
geometries can extend slightly past the fetch bbox. `sample_elevation` is
strict-bounds-fail-fast by AC contract — padding is the calmer fix than
loosening the check.

### Why WGS84 on disk

We request the grid directly in WGS84 so the committed `dem.tif` is trivial
to inspect against the OSM fixture's bounds without a reprojection pass. The
production `sample_elevation` does *not* care about this choice — it reads
the CRS from the raster header and transforms WGS84 graph coords on the fly.
The CRS-transformation correctness test in `tests/unit/test_dem.py` uses a
synthetic Lambert-93 (EPSG:2154) in-memory GeoTIFF specifically so the non-
WGS84 code path stays exercised on every CI run, even though this fixture
ships in WGS84.

### Regenerating

```
python regenerate_dem.py
```

`regenerate_dem.py` uses the OS certificate store via `truststore` (same
pattern as `regenerate.py`) so it works behind corporate TLS-intercepting
proxies. Endpoint is open data — no API key needed.

The fixture content is sanity-checked by `tests/unit/test_dem.py` (size cap
+ Alpine-plausibility band) on every CI run.
