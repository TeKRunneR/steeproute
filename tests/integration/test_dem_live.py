# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: rasterio surfaces DatasetReader / read() as Unknown; same external-boundary
# pattern as pipeline/dem.py.
"""Live DEM-download integration test — calls the IGN WMS; skipped in default CI.

Run locally with:

    uv run pytest -m live

Purpose: prove `resolve_dem` actually fetches usable RGE ALTI elevations from the
public IGN Géoplateforme WMS for a small Grenoble-area request. Bands are wide
enough to absorb the real Chartreuse terrain range without being brittle.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest
import rasterio

from steeproute.models import Area
from steeproute.pipeline.dem_download import resolve_dem

# Le Sappey-en-Chartreuse — same center as the committed fixture; a tiny radius
# keeps the live fetch fast and the payload small.
_AREA = Area(center=(45.260, 5.788), radius_km=0.2)

# Chartreuse Massif elevations sit well within this band; anything outside means
# the response wasn't real altimetry (e.g. an error doc decoded as floats).
_MIN_PLAUSIBLE_M = 150.0
_MAX_PLAUSIBLE_M = 3000.0


@pytest.mark.live
def test_live_resolve_dem_fetches_plausible_alpine_elevations(tmp_path: pathlib.Path) -> None:
    path = resolve_dem(_AREA, tmp_path)
    assert path.is_file()

    with rasterio.open(path) as dataset:
        assert dataset.crs.to_epsg() == 4326
        band = dataset.read(1)
        bounds = dataset.bounds

    # The raster covers the requested area (its bbox is the area + padding ring).
    lat, lon = _AREA.center
    assert bounds.left < lon < bounds.right
    assert bounds.bottom < lat < bounds.top

    assert np.all(np.isfinite(band)), "live DEM has non-finite samples"
    assert _MIN_PLAUSIBLE_M < float(band.min())
    assert float(band.max()) < _MAX_PLAUSIBLE_M
