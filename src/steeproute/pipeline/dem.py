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
import numpy as np
import pyproj
import rasterio
import rasterio.errors
import rasterio.transform
import shapely

from steeproute.errors import DataSourceUnavailableError, DEMCoverageError

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
        DataSourceUnavailableError: `rasterio.open` cannot read the DEM —
            permission denied, corrupt header, truncated file, missing file
            (when `is_file()` raced an unlink), network-filesystem hiccup, etc.
            Coverage-tier failures keep their `DEMCoverageError` class so users
            can distinguish "can't open the file" from "file opened but the
            area isn't covered" (Architecture §Cat 10).
        TypeError: if any edge's `geometry` is not a `shapely.LineString`.
    """
    out: nx.MultiDiGraph = graph.copy()
    try:
        dataset_ctx = rasterio.open(dem_path)
    except (rasterio.errors.RasterioIOError, OSError) as exc:
        # `RasterioIOError` is rasterio's primary "couldn't read the raster" class
        # (subclass of `OSError`). Catching `OSError` too covers truly low-level
        # I/O failures (network filesystem disconnect, EACCES on systems where
        # `is_file()` returns True without read permission). We deliberately keep
        # the existing `DEMCoverageError` paths inside the `with` block — those
        # represent "DEM opened fine but the data is wrong shape for this area",
        # categorically distinct from "DEM source unreachable" per Cat 10. The
        # `is_file()` check in `cli/setup.py` catches the common typo case earlier;
        # this wrap covers what slips past it (deferred-work DEF2 from Story 2.8).
        raise DataSourceUnavailableError(
            "DEM source unreachable.",
            detail=f"rasterio.open({dem_path}) failed: {exc!r}",
        ) from exc
    with dataset_ctx as dataset:
        dem_crs = dataset.crs
        if dem_crs is None:
            raise DEMCoverageError(
                f"DEM at {dem_path} has no CRS metadata; cannot project graph coordinates.",
                detail="Ensure the GeoTIFF declares a CRS (e.g., EPSG:2154 for IGN RGE ALTI 5m).",
            )
        bounds = dataset.bounds  # (left, bottom, right, top) in DEM CRS units
        # Sanity-check the raster transform before iterating vertices: a malformed
        # DEM with negative pixel width or a flipped N/S origin would otherwise
        # surface as a wall of per-vertex out-of-bounds errors, hiding the
        # underlying "this DEM is upside-down" diagnosis. Routed in via
        # deferred-work D2 from Story 2.3.
        if bounds.right <= bounds.left or bounds.top <= bounds.bottom:
            raise DEMCoverageError(
                f"DEM at {dem_path} has inverted or zero-width bounds "
                f"(left={bounds.left}, right={bounds.right}, "
                f"bottom={bounds.bottom}, top={bounds.top}).",
                detail=(
                    "Expected right > left and top > bottom. The raster transform "
                    "may have a negative pixel width or a flipped origin."
                ),
            )
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

        # --- Vectorized sampling (Story 14.1) --------------------------------
        # Formerly a per-edge `transformer.transform` + per-point `dataset.sample`
        # loop (~65 µs/point over millions of points — the biggest setup CPU
        # stage). Reformulated as flat-array numpy work: gather every edge's
        # coords once, project the whole graph in one `pyproj` call, resolve
        # pixel indices with rasterio's own `rowcol` (guaranteeing the same
        # nearest-pixel selection `dataset.sample` used), and read them by fancy
        # index off a single band read. The per-vertex bounds/nodata guards
        # become vectorized masks that still fail fast on the first offending
        # edge with the identical message shape. Output is bit-identical to the
        # old path (proven over the grenoble_small fixture in tests/unit/test_dem.py).
        edge_refs: list[tuple[object, object, object, dict[str, object]]] = []
        lon_chunks: list[np.ndarray] = []
        lat_chunks: list[np.ndarray] = []
        offsets: list[int] = [0]
        for u, v, k, data in out.edges(data=True, keys=True):
            geom = data.get("geometry")
            if not isinstance(geom, shapely.LineString):
                raise TypeError(
                    "pipeline.dem: edge geometry must be a shapely.LineString, "
                    f"got {type(geom).__name__}"
                )
            coords = np.asarray(geom.coords, dtype=np.float64)  # (n, 2) as (lon, lat)
            lon_chunks.append(coords[:, 0])
            lat_chunks.append(coords[:, 1])
            offsets.append(offsets[-1] + coords.shape[0])
            edge_refs.append((u, v, k, data))

        if not edge_refs:
            # Empty graph: nothing to sample (defensive — matches the old no-op).
            return out

        lons = np.concatenate(lon_chunks)
        lats = np.concatenate(lat_chunks)
        offset_arr = np.asarray(offsets, dtype=np.int64)

        xs, ys = transformer.transform(lons, lats)
        xs = np.asarray(xs, dtype=np.float64)
        ys = np.asarray(ys, dtype=np.float64)

        # Half-open on the east/north edges to match rasterio's pixel convention:
        # a point at exactly `bounds.right` (or `bounds.top`) maps to a pixel index
        # of `width` (or `height`), which is outside the array. Inclusive on the
        # west/south edges (pixel index 0). Fail fast on the first offending vertex
        # (in edge-iteration order), matching the old per-edge loop's first raise.
        in_bounds = (
            (bounds.left <= xs) & (xs < bounds.right) & (bounds.bottom < ys) & (ys <= bounds.top)
        )
        if not bool(in_bounds.all()):
            idx = int(np.argmax(~in_bounds))
            u, v, k, _ = edge_refs[int(np.searchsorted(offset_arr, idx, side="right")) - 1]
            raise DEMCoverageError(
                f"Edge ({u}, {v}, {k}) has a vertex at projected "
                f"({float(xs[idx]):.3f}, {float(ys[idx]):.3f}) outside DEM bounds "
                f"(left={bounds.left:.3f}, bottom={bounds.bottom:.3f}, "
                f"right={bounds.right:.3f}, top={bounds.top:.3f}).",
                detail=f"DEM CRS: {dem_crs}; DEM path: {dem_path}",
            )

        # `rasterio.transform.rowcol` is the exact call `dataset.sample` uses to map
        # coordinates to pixels (default op = `numpy.floor` → int), so the resolved
        # rows/cols — and therefore the sampled values off a full-band read — are
        # bit-identical to the old per-point `dataset.sample`. Every vertex is
        # in-bounds here, so all indices are valid array positions.
        rows, cols = rasterio.transform.rowcol(dataset.transform, xs, ys)
        band = dataset.read(1)
        elevs = np.asarray(band[rows, cols], dtype=np.float64)

        # `~np.isfinite` catches NaN (including NaN-nodata rasters that read back as
        # NaN) and ±Inf; `elevs == nodata_finite` catches finite sentinels like -9999.
        bad = ~np.isfinite(elevs)
        if nodata_finite is not None:
            bad |= elevs == nodata_finite
        if bool(bad.any()):
            idx = int(np.argmax(bad))
            u, v, k, _ = edge_refs[int(np.searchsorted(offset_arr, idx, side="right")) - 1]
            raise DEMCoverageError(
                f"Edge ({u}, {v}, {k}) has a vertex at (lat={float(lats[idx]):.6f}, "
                f"lon={float(lons[idx]):.6f}) sampling a nodata or non-finite DEM "
                f"value (raster nodata={nodata}). Treat as outside coverage.",
                detail=f"DEM CRS: {dem_crs}; DEM path: {dem_path}",
            )

        for i, (_u, _v, _k, data) in enumerate(edge_refs):
            start, end = offsets[i], offsets[i + 1]
            data["vertices_resampled"] = [
                (float(lats[j]), float(lons[j]), float(elevs[j])) for j in range(start, end)
            ]

    return out
