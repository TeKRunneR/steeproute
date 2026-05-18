# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx + shapely + rasterio surface MultiDiGraph / LineString /
# DatasetReader operations as Unknown; same external-boundary pattern as
# pipeline/osm.py and pipeline/smoothing.py. Architecture §Type hints lists
# OSM/DEM ingestion as the external boundary that warrants relaxation.
"""Pipeline stage 5: DEM elevation sampling via rasterio.

Reads elevations from a local DEM GeoTIFF at every vertex of each edge's
resampled `geometry` (output of stage 4) and attaches them as a new
`vertices_resampled` edge attribute, while preserving the upstream attribute
contract (`geometry`, `sac_scale`, `highway`, `osm_way_id`).

Axis-ordering contract: `vertices_resampled` entries are `(lat, lon,
elevation_m)` — **lat first** — while `geometry` is `(lon, lat)` per shapely's
convention. The swap is performed here once; downstream stages 6-7 trust the
`(lat, lon, elev)` order.

CRS handling is explicit: the DEM's CRS is read from the raster header, not
assumed. Graph WGS84 lon/lat coords are projected to the DEM's native CRS
via a single `pyproj.Transformer` per call before sampling. IGN RGE ALTI 5m
ships in Lambert-93 (EPSG:2154); the production code does not hardcode that.

A vertex that lands outside the DEM's bounds, or on a nodata pixel, raises
`DEMCoverageError` (a `PreExecutionError` subclass) naming one offending edge
and the DEM's bounds. No silent NaN: every elevation in `vertices_resampled`
is a finite float.
"""

from __future__ import annotations

import math
import pathlib

import networkx as nx
import pyproj
import rasterio
import shapely

from steeproute.errors import DEMCoverageError

# Graph-side CRS: shapely geometries from stages 1-4 are WGS84 lon/lat.
WGS84_EPSG: int = 4326


def sample_elevation(
    graph: nx.MultiDiGraph,
    dem_path: pathlib.Path,
) -> nx.MultiDiGraph:
    """Stage 5: sample DEM elevation at every vertex of every edge's geometry.

    Each output edge gains a `vertices_resampled: list[tuple[float, float, float]]`
    attribute whose entries are `(lat, lon, elevation_m)` — note **lat first**,
    opposite shapely's `(lon, lat)` ordering. The list has one entry per
    coordinate of the edge's `geometry`. Upstream attributes (`geometry`,
    `sac_scale`, `highway`, `osm_way_id`) are carried through unchanged.

    Args:
        graph: input MultiDiGraph with `shapely.LineString` `geometry` on every
            edge in WGS84 lon/lat (output of stage 4).
        dem_path: path to a local DEM GeoTIFF. CRS is read from the raster
            header; nodata is honoured if defined.

    Returns:
        A new MultiDiGraph; the input is never mutated.

    Raises:
        DEMCoverageError: if any edge vertex falls outside the DEM's bounds or
            lands on a nodata pixel. The message names one offending edge and
            the DEM bounds (fail-fast on the first violation).
        TypeError: if any edge's `geometry` is not a `shapely.LineString`.
    """
    out: nx.MultiDiGraph = graph.copy()
    with rasterio.open(dem_path) as dataset:
        dem_crs = dataset.crs
        if dem_crs is None:
            raise DEMCoverageError(
                f"DEM at {dem_path} has no CRS metadata; cannot project graph coordinates.",
                detail="Ensure the GeoTIFF declares a CRS (e.g., EPSG:2154 for IGN RGE ALTI 5m).",
            )
        bounds = dataset.bounds  # (left, bottom, right, top) in DEM CRS units
        nodata = dataset.nodata
        # NaN-nodata is handled implicitly by the `not math.isfinite(elev)` branch
        # below; only finite nodata needs the equality check. Real DEM sentinels
        # (-9999, -32768, NaN) are exactly representable in float so a strict `==`
        # is robust for them; pathological non-representable finite sentinels are
        # out of scope.
        nodata_finite: float | None = (
            float(nodata) if nodata is not None and math.isfinite(float(nodata)) else None
        )
        transformer = pyproj.Transformer.from_crs(
            WGS84_EPSG,
            dem_crs,
            always_xy=True,
        )

        for u, v, k, data in out.edges(data=True, keys=True):
            geom = data.get("geometry")
            if not isinstance(geom, shapely.LineString):
                raise TypeError(
                    "pipeline.dem: edge geometry must be a shapely.LineString, "
                    f"got {type(geom).__name__}"
                )
            lons = [float(c[0]) for c in geom.coords]
            lats = [float(c[1]) for c in geom.coords]
            xs, ys = transformer.transform(lons, lats)
            xs_list = [float(x) for x in xs]
            ys_list = [float(y) for y in ys]

            for x, y in zip(xs_list, ys_list, strict=True):
                # Half-open on the east/north edges to match rasterio's pixel
                # convention: a point at exactly `bounds.right` (or `bounds.top`)
                # maps to a pixel index of `width` (or `height`), which is outside
                # the array. Inclusive on the west/south edges (pixel index 0).
                if not (bounds.left <= x < bounds.right and bounds.bottom < y <= bounds.top):
                    raise DEMCoverageError(
                        f"Edge ({u}, {v}, {k}) has a vertex at projected "
                        f"({x:.3f}, {y:.3f}) outside DEM bounds "
                        f"(left={bounds.left:.3f}, bottom={bounds.bottom:.3f}, "
                        f"right={bounds.right:.3f}, top={bounds.top:.3f}).",
                        detail=f"DEM CRS: {dem_crs}; DEM path: {dem_path}",
                    )

            samples = dataset.sample(zip(xs_list, ys_list, strict=True), indexes=1)
            vertices_resampled: list[tuple[float, float, float]] = []
            for lon, lat, sample_arr in zip(lons, lats, samples, strict=True):
                elev = float(sample_arr[0])
                # `not math.isfinite(elev)` catches NaN (including NaN-nodata
                # rasters where the sample reads back as NaN) and ±Inf.
                # `elev == nodata_finite` catches finite sentinels like -9999.
                if not math.isfinite(elev) or (nodata_finite is not None and elev == nodata_finite):
                    raise DEMCoverageError(
                        f"Edge ({u}, {v}, {k}) has a vertex at (lat={lat:.6f}, "
                        f"lon={lon:.6f}) sampling a nodata or non-finite DEM "
                        f"value (raster nodata={nodata}). Treat as outside coverage.",
                        detail=f"DEM CRS: {dem_crs}; DEM path: {dem_path}",
                    )
                vertices_resampled.append((lat, lon, elev))
            data["vertices_resampled"] = vertices_resampled

    return out
