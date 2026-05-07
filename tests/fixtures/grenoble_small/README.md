# grenoble_small — committed real-data test fixture

Captured OSM graph for a small Grenoble-area cutout, used by the unit and integration
tests for `pipeline/osm.py` (Story 2.1) and downstream pipeline stages (Story 2.2+).

DEM raster (`dem.tif`) lands here in Story 2.3.

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
