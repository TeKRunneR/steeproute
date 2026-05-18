# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: rasterio surfaces DatasetReader/profile dicts as Unknown.
"""Regenerate the committed dem.tif fixture for tests/fixtures/grenoble_small/.

Run from this directory:

    python regenerate_dem.py

Fetches IGN RGE ALTI (HIGHRES, 5 m native) via the public IGN Géoplateforme
WMS endpoint as raw IEEE-754 float32 (`image/x-bil;bits=32`), and writes the
result as a single-band float32 GeoTIFF in WGS84 (EPSG:4326).

We request the data already gridded to WGS84 so the committed `dem.tif` is
trivial to inspect against the OSM fixture's bounds; the production
`sample_elevation` doesn't care — it reads the raster's CRS from its header
and reprojects WGS84 graph coords on the fly. The CRS-transformation
correctness test in `tests/unit/test_dem.py` uses a synthetic Lambert-93
GeoTIFF specifically so the non-WGS84 code path stays exercised even though
this fixture happens to ship in WGS84.

TLS is routed through the OS trust store via the `truststore` package, same
as `regenerate.py` — works behind corporate TLS-intercepting proxies whose
root CA is in the OS store but not in `certifi`'s vendored bundle.
"""

from __future__ import annotations

import pathlib
import urllib.parse
import urllib.request

import numpy as np
import rasterio
import truststore
from rasterio.transform import from_bounds

# Bbox half-side around Le Sappey, in geographic degrees. Pads the OSM
# fixture's 2 km bbox half-side by 100 m on each side so that trail edges
# whose `osmnx` simplification produces vertices right on (or just past) the
# fetch bbox are still inside the DEM — `sample_elevation` is strict-bounds-
# fail-fast by design (AC #3), so we need real coverage, not a tight clip.
CENTER_LAT: float = 45.260
CENTER_LON: float = 5.788
HALF_SIDE_M: float = 2000.0 + 100.0  # 2 km OSM bbox + 100 m padding ring
HALF_LAT_DEG: float = HALF_SIDE_M / 111000.0
HALF_LON_DEG: float = HALF_SIDE_M / 78600.0

# 840x840 pixels over the padded bbox ≈ 5 m east-west and 5 m north-south
# at 45° N — close to RGE ALTI HIGHRES's 5 m native resolution.
WIDTH: int = 840
HEIGHT: int = 840

# IGN Géoplateforme WMS, public RGE ALTI HIGHRES layer.
WMS_URL: str = "https://data.geopf.fr/wms-r/wms"
LAYER: str = "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES"
# IGN serves float-valued altimetry as raw IEEE-754 floats over `image/x-bil`.
# Empirically little-endian (verified against known Le Sappey-area elevations).
BIL_FORMAT: str = "image/x-bil;bits=32"

OUTPUT_PATH = pathlib.Path(__file__).parent / "dem.tif"


def main() -> None:
    truststore.inject_into_ssl()

    west = CENTER_LON - HALF_LON_DEG
    east = CENTER_LON + HALF_LON_DEG
    south = CENTER_LAT - HALF_LAT_DEG
    north = CENTER_LAT + HALF_LAT_DEG

    params = {
        "service": "WMS",
        "version": "1.3.0",
        "request": "GetMap",
        "layers": LAYER,
        "styles": "",
        "crs": "CRS:84",  # WGS84 lon/lat
        "bbox": f"{west},{south},{east},{north}",
        "width": WIDTH,
        "height": HEIGHT,
        "format": BIL_FORMAT,
    }
    url = f"{WMS_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "steeproute-fixture/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = resp.read()
    expected = WIDTH * HEIGHT * 4
    if len(body) != expected:
        raise RuntimeError(
            f"Unexpected WMS BIL payload size: got {len(body)} bytes, expected {expected} "
            f"(WIDTH×HEIGHT×4 for float32). Content-Type: {resp.headers.get('Content-Type')!r}."
        )

    # Little-endian float32 (verified empirically — IGN docs are silent on byte order).
    arr = np.frombuffer(body, dtype="<f4").reshape((HEIGHT, WIDTH))

    transform = from_bounds(
        west=west, south=south, east=east, north=north, width=WIDTH, height=HEIGHT
    )
    profile = {
        "driver": "GTiff",
        "height": HEIGHT,
        "width": WIDTH,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": None,
        "compress": "deflate",
        "predictor": 3,  # float predictor; halves the deflate output for float-valued rasters.
        "tiled": True,
    }
    with rasterio.open(OUTPUT_PATH, "w", **profile) as dst:
        dst.write(arr, 1)

    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(
        f"Saved {OUTPUT_PATH.name}: {size_kb:.1f} KB, {HEIGHT}x{WIDTH} float32, "
        f"elevations {float(arr.min()):.1f}–{float(arr.max()):.1f} m."
    )


if __name__ == "__main__":
    main()
