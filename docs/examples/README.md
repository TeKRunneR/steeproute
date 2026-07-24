# Gallery examples — how they were generated

The reports linked from the README `## Gallery` are committed here under
`docs/examples/<region>/`. Each region is a full-size run of the tool in the Grenoble
area, chosen for distinct terrain character (the tool itself is not Grenoble-specific —
see the README for coverage). They are *gallery* regions, deliberately distinct from the
small 2 km regression cutouts in `tests/e2e/fixtures/` (belledonne / vercors /
chartreuse), which exist only to pin golden hashes.

Each region directory holds `route-1..3.html` + `route-*.json` (three routes per region)
and two PNG thumbnails captured from `route-1.html`:

- `route-1-map.png` — the Leaflet map pane
- `route-1-profile.png` — the Chart.js elevation profile

The README gallery shows only `route-1` (the top route) of each region; all three routes
are kept here. As the README notes, the routes are point-to-point exploration aids, not
ready-to-run loops — route 1 climbs far more than it descends because `--max-descent-slope`
caps descent while steep climbs are unconstrained.

Generation needs network access: OpenStreetMap via Overpass and the DEM auto-downloaded
from the **IGN Géoplateforme WMS** (RGE ALTI HIGHRES, 5 m native — layer
`ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES`). There is no `--dem-path`; the cache dir is a
throwaway working location (only `docs/examples/` is committed). Behaviour is pinned by
`--seed 42`.

The runs use `--difficulty-cap T4` (surfaces more of the steep alpine terrain than the
default T3), a large search budget (`--iter-budget 1000000 --stagnation-iters 200000`)
run in parallel across four cores (`--workers 4 --merge-interval 250000`, three
intermediate island-migration merges), the practical-route constraints
`--start-at-junction` and `--max-descent-slope 0.4`, and `--area-cap 100000` (the default
500 km² cap would reject these large areas). `--theta 0.2` (the route-level average-slope
floor) is left at its default **on purpose**: the point of the tool is steep routes, so
the floor is meant to be limiting. It also sets `--elevation-deadband 1` (drops sub-metre
up/down noise from the D+/D− totals) and `--j-max 0` (returned routes share no segments;
this does not affect route 1, only the distinctness of routes 2+). All other parameters
are defaults (`--min-climb-slope 0.2`, `--l-connector 200`, `--elevation-smoothing 50`,
`--n 3`, …). Because these use live OSM data, a future regeneration will not be
byte-identical — that is expected (the zero-tolerance reproducibility guarantee lives in
the pinned-regression goldens, not here). `--workers > 1` is also deterministic only per
`(seed, workers, merge-interval)`, and differs by design from a single-process run.

The setup radius is 1 km larger than the query radius, so the query area is strictly
contained in the prepared area (the FR24 coverage check uses strict containment) with
enough DEM padding to absorb osmnx's geometry overshoot at the box edge.

## Regenerate a region

```sh
# 1. Prepare the local network (OSM + DEM). CACHE is any throwaway dir.
uv run steeproute-setup --center <LAT,LON> --radius <SETUP_KM> --cache-dir <CACHE>

# 2. Query it into the committed gallery location.
uv run steeproute --center <LAT,LON> --radius <QUERY_KM> --cache-dir <CACHE> \
    --output-dir docs/examples/<region> --seed 42 --n 3 \
    --difficulty-cap T4 --iter-budget 1000000 --stagnation-iters 200000 \
    --merge-interval 250000 --workers 4 --elevation-deadband 1 --j-max 0 \
    --start-at-junction --max-descent-slope 0.4 --area-cap 100000

# 3. Capture the route-1 thumbnails (headless Chrome/Edge; needs network for tiles).
uv run python devtools/gallery_capture.py \
    docs/examples/<region>/route-1.html docs/examples/<region> --prefix route-1- --wait 9
```

`devtools/gallery_capture.py` drives a headless Chromium-family browser over the
DevTools Protocol: it clip-captures the map `<div>` and exports the profile `<canvas>`
pixels directly. Default `--scale 1.0` keeps the six PNGs well under the 5 MB gallery
budget (~2.3 MB total).

## Regions

| Region | Center (lat,lon) | Setup radius | Query radius | Setup time | Query wall-clock | Peak memory | route 1 |
|---|---|---|---|---|---|---|---|
| `chartreuse` (Chartreuse massif, N of Grenoble) | 45.3434, 5.7912 | 17 km | 16 km | ~192 s | ~84 s | ~3.3 GB | 12.0 km, +1787 m, 20% |
| `vercors` (Vercors massif, SW of Grenoble) | 45.0342, 5.4513 | 21 km | 20 km | ~213 s | ~84 s | ~3.2 GB | 12.2 km, +2058 m, 22% |
| `south-belledonne` (southern Belledonne massif) | 45.1733, 5.9402 | 12 km | 11 km | ~86 s | ~50 s | ~1.4 GB | 18.0 km, +2454 m, 20% |

Each region returned the full 3/3 routes, converged (`budget-exhausted` — the full ~1M
iterations were used), with zero validation failures.

**Memory envelope (NFR2):** peak memory is the maximum summed working set across the whole
query process tree (parent + the four `--workers` GRASP processes), sampled at ~0.4 s
intervals during the run. It scales with both the prepared-area size and the worker count
(each worker holds its own copy of the search graph). The maximum across regions was
**~3.3 GB** (`chartreuse`, radius 16, `--workers 4`) — comfortably within the 32 GB
development laptop; drop `--workers` if memory is tight.
