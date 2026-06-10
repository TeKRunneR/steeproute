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
are kept here.

Generation needs network access: OpenStreetMap via Overpass and the DEM auto-downloaded
from the **IGN Géoplateforme WMS** (RGE ALTI HIGHRES, 5 m native — layer
`ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES`). There is no `--dem-path`; the cache dir is a
throwaway working location (only `docs/examples/` is committed). Behaviour is pinned by
`--seed 42`. The gallery uses a SAC ceiling of `--difficulty-cap T4` (surfaces more of
the steep alpine terrain than the default T3) and a generous search budget
(`--iter-budget 200000 --stagnation-iters 10000`) so GRASP actually converges on good
loops rather than stopping early — runs still finish in well under a minute and converge
on stagnation long before the iteration cap. `--theta 0.2` (the route-level average-slope
floor) is left at its default **on purpose**: the point of the tool is steep routes, so
the floor is meant to be limiting. It also sets `--elevation-deadband 1` (drops sub-metre
up/down noise from the D+/D− totals) and `--j-max 0` (returned routes share no segments;
this does not affect route 1, only the distinctness of routes 2+). All other parameters
are defaults (`--min-climb-slope 0.2`, `--l-connector 200`, `--elevation-smoothing 50`,
`--n 3`, …). Because these use live OSM data, a future regeneration will not be
byte-identical — that is expected (the zero-tolerance reproducibility guarantee lives in
the pinned-regression goldens, not here).

The query radius is set slightly smaller than the setup radius so the query area is
strictly contained in the prepared area (the FR24 coverage check uses strict
containment).

## Regenerate a region

```sh
# 1. Prepare the local network (OSM + DEM). CACHE is any throwaway dir.
uv run steeproute-setup --center <LAT,LON> --radius <SETUP_KM> --cache-dir <CACHE>

# 2. Query it into the committed gallery location.
uv run steeproute --center <LAT,LON> --radius <QUERY_KM> --cache-dir <CACHE> \
    --output-dir docs/examples/<region> --seed 42 --n 3 \
    --difficulty-cap T4 --iter-budget 200000 --stagnation-iters 10000 \
    --elevation-deadband 1 --j-max 0

# 3. Capture the route-1 thumbnails (headless Chrome/Edge; needs network for tiles).
uv run python devtools/gallery_capture.py \
    docs/examples/<region>/route-1.html docs/examples/<region> --prefix route-1- --wait 9
```

`devtools/gallery_capture.py` drives a headless Chromium-family browser over the
DevTools Protocol: it clip-captures the map `<div>` and exports the profile `<canvas>`
pixels directly. Default `--scale 1.0` keeps the six PNGs well under the 5 MB gallery
budget (~1.6 MB total).

## Regions

| Region | Center (lat,lon) | Setup radius | Query radius | Setup time | Query wall-clock | Peak memory | route 1 |
|---|---|---|---|---|---|---|---|
| `chamrousse` (Chamrousse, Belledonne) | 45.1200, 5.8800 | 6.5 km | 6.0 km | ~42 s | ~7 s | ~261 MB | 10.7 km, +1018 m, 26% |
| `saint-nizier` (Saint-Nizier-du-Moucherotte, Vercors) | 45.1556, 5.6469 | 7.0 km | 6.5 km | ~111 s | ~32 s | ~792 MB | 7.5 km, +1042 m, 24% |
| `col-de-porte` (Col de Porte / Charmant Som, Chartreuse) | 45.2950, 5.7730 | 6.5 km | 6.0 km | ~66 s | ~7 s | ~294 MB | 11.0 km, +1390 m, 22% |

Each region returned the full 3/3 routes, converged (on stagnation), with zero validation failures.

**Note (`saint-nizier`):** its setup uses a 7.0 km radius (not 6.5) because at 6.5 km a
trail vertex landed exactly on the south edge of the padded DEM, raising
`DEMCoverageError`; the larger radius gives enough DEM padding to absorb the osmnx
geometry overshoot.

**Memory envelope (NFR2):** peak working set per query was measured (Windows
`GetProcessMemoryInfo`, in-process). The maximum across regions was **~792 MB**
(`saint-nizier`) — far below the 12 GB threshold, comfortably within the 16 GB envelope.
No region needs an NFR2 caveat in Known Limitations.
