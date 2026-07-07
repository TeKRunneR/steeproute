# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: rasterio surfaces DatasetReader / profile dicts as Unknown; same
# external-boundary pattern as pipeline/dem.py. Architecture §Type hints lists
# DEM ingestion as the external boundary that warrants relaxation.
"""Auto-download the DEM raster for an area from the IGN Géoplateforme WMS.

Productionizes the mechanism the committed fixture's `regenerate_dem.py` proved:
fetch IGN RGE ALTI HIGHRES (5 m native) over the public, key-less Géoplateforme
WMS as raw IEEE-754 float32 (`image/x-bil;bits=32`), and write a single-band
float32 GeoTIFF in WGS84 (EPSG:4326).

`resolve_dem(bounds, cache_root)` is the entry point. The setup path derives
`bounds` from the prepared graph's geometry envelope (`graph_dem_bounds`) so the
raster always covers the vertices `sample_elevation` probes — osmnx
`simplify=True` can push simplified edge geometry past the nominal OSM fetch bbox
by an unbounded amount near switchbacks, which a fixed radius+padding ring cannot
safely cover. `resolve_dem` tiles the request so any area up to the setup radius
ceiling stays at native 5 m, mosaics the tiles, and caches the result keyed on the
bbox under `<cache-root>/steeproute/dem/`. A cached raster is reused unless
`force_refresh` is set. Every network / payload failure maps to
`DataSourceUnavailableError("DEM source unreachable.", …)` so `run_entry_point`
surfaces it as exit 2 with the same wording as the existing DEM-open failure
path (NFR6, Architecture §Cat 10).

TLS routes through the OS trust store via `truststore` (same as the fixture
script) so it works behind corporate TLS-intercepting proxies whose root CA is
in the OS store but not in `certifi`'s vendored bundle.
"""

from __future__ import annotations

import hashlib
import http.client
import logging
import math
import os
import pathlib
import random
import time
import urllib.error
import urllib.parse
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen

import networkx as nx
import numpy as np
import rasterio
import shapely
import truststore
from rasterio.transform import from_bounds

from steeproute.cache import dem_cache_path_for
from steeproute.errors import DataSourceUnavailableError
from steeproute.models import Area
from steeproute.progress import StageProgress

_logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read an int tuning knob from the environment, falling back to `default`.

    A missing var uses `default`; a malformed one logs a warning and uses
    `default` rather than crashing at import — this runs before the CLI's
    `BadCLIArgError` tier exists, so a raw `ValueError` here would surface as an
    ugly exit-1 traceback instead of a clean error.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        _logger.warning("ignoring invalid %s=%r; using %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    """Float sibling of `_env_int` — same missing/malformed fallback semantics."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        _logger.warning("ignoring invalid %s=%r; using %s", name, raw, default)
        return default


# IGN Géoplateforme WMS — public RGE ALTI HIGHRES (5 m native). Endpoint, layer,
# and BIL format are kept identical to the committed fixture's `regenerate_dem.py`.
_WMS_URL: str = "https://data.geopf.fr/wms-r/wms"
_LAYER: str = "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES"
_WMS_VERSION: str = "1.3.0"
# IGN serves float altimetry as raw little-endian IEEE-754 over `image/x-bil`
# (byte order verified empirically by the fixture script against known elevations).
_BIL_FORMAT: str = "image/x-bil;bits=32"
_BIL_DTYPE: str = "<f4"  # little-endian float32
_USER_AGENT: str = "steeproute/0.1 (DEM auto-download)"
# Per-request WMS socket timeout. Kept modest because transient failures are now
# retried (`_TILE_MAX_ATTEMPTS`), so a single heroic wait is no longer needed;
# 3 attempts × 30 s + backoff bounds a dead tile at ~100 s instead of minutes.
# Override with `STEEPROUTE_DEM_HTTP_TIMEOUT_S` for unusually slow links.
_HTTP_TIMEOUT_S: int = _env_int("STEEPROUTE_DEM_HTTP_TIMEOUT_S", 30)

# Stable cache-key tag for the IGN RGE ALTI HIGHRES dataset, recorded as the
# manifest `dem_version` when the user doesn't pass `--dem-version`. The source
# is a fixed dataset+extent for a given area, so a constant tag is correct;
# a real IGN release bump is handled by `--force-refresh` or an explicit
# `--dem-version` (Architecture §Cat 4b).
DEFAULT_DEM_VERSION: str = "ign-rgealti-highres"

# Target ground resolution in meters/pixel. RGE ALTI HIGHRES is 5 m native.
_TARGET_RES_M: float = 5.0

# Padding ring (m) for the area→bbox convenience (`_padded_bbox`), retained for
# the offline/live DEM tests that fetch a raster for a nominal area. The
# production setup path no longer sizes the DEM this way — see `graph_dem_bounds`.
_PADDING_M: float = 100.0

# Safety margin (m) added around the *actual graph geometry* envelope when sizing
# the DEM in the setup path. osmnx `simplify=True` produces edge-geometry that can
# bulge past the nominal OSM fetch bbox by an unbounded amount near switchbacks
# (mountain hairpins in the Alps), so a fixed padding over the radius is not safe.
# Sizing from the geometry envelope guarantees coverage; the margin only needs to
# keep the extreme vertices strictly interior so `sample_elevation`'s half-open
# bounds check (east/north exclusive) passes — 100 m is comfortable headroom.
_GRAPH_BOUNDS_MARGIN_M: float = 100.0

# Max pixels per WMS GetMap tile dimension. A single IGN request is capped; 2048
# stays well under it and keeps each tile's BIL payload ~16 MB. Larger areas are
# split into a grid of tiles and mosaicked, preserving native 5 m.
_MAX_TILE_PX: int = 2048

# Default concurrent DEM tile fetches. Tile download is network-wait-bound (the
# GIL is released during `urlopen`), so plain threads give the full speedup with
# no process/pickle cost — this is I/O, not the CPU-bound GRASP parallelism.
# Story 14.3 validated IGN Géoplateforme's behavior under this concurrency at r20
# with no 429s/errors, so 4 is the default; `--dem-fetch-workers` (Story 14.3
# scope revision) lets a user raise or lower it without a code change if IGN's
# tolerance differs from what was observed (Architecture §Cat 3).
DEFAULT_DEM_FETCH_WORKERS: int = 4

# Per-tile transient-failure retry policy. A tile fetch that fails with a transient
# error (timeout, connection reset, HTTP 429/5xx, truncated read) is retried up to
# `_TILE_MAX_ATTEMPTS` times *total* with exponential backoff and full jitter — the
# jitter keeps N concurrent workers that hit the same server hiccup from retrying in
# lockstep and re-thundering it (the same etiquette concern as `--dem-fetch-workers`).
# Deterministic failures (wrong content-type / byte count) are NOT retried; they raise
# `DataSourceUnavailableError` immediately. Override with `STEEPROUTE_DEM_FETCH_RETRIES`
# / `STEEPROUTE_DEM_FETCH_BACKOFF_S`.
_TILE_MAX_ATTEMPTS: int = _env_int("STEEPROUTE_DEM_FETCH_RETRIES", 3)
_TILE_BACKOFF_BASE_S: float = _env_float("STEEPROUTE_DEM_FETCH_BACKOFF_S", 0.5)

# Mean-earth meters-per-degree-latitude. Deliberately matches osmnx's spherical
# earth model (`EARTH_RADIUS_M = 6_371_009` → 2π·R/360 ≈ 111_194.93 m/deg) rather
# than the WGS84-equatorial 111_320, so the DEM bbox is computed on the SAME model
# `osm_load` uses for its `dist_type="bbox"` fetch. With a matched constant the
# `_PADDING_M` ring is a true ~100 m margin over the OSM bbox at every radius
# (using 111_320 eroded it to ~44 m at the 50 km ceiling — review finding).
# Longitude degrees scale by cos(lat); computed from the area center so the bbox
# is correct away from the fixture's specific 45.26° N.
_M_PER_DEG_LAT: float = 111_194.93

# Hash truncation for the per-area DEM cache filename — same 16-hex / 64-bit
# rationale as `cache._CACHE_KEY_HEX_LEN`.
_DEM_KEY_HEX_LEN: int = 16
# Coordinate rounding before hashing the cache key, so float-print noise on the
# computed bbox doesn't produce phantom cache misses. ~11 cm at 6 decimals.
_BBOX_DECIMALS: int = 6


class _TransientDEMError(Exception):
    """Internal: a retryable WMS tile failure (timeout, reset, HTTP 429/5xx, truncated read).

    Raised by `_wms_get_bil` for the failure modes worth retrying and caught by
    `_fetch_tile`'s retry loop. Deterministic failures (wrong content-type, wrong
    byte count) raise `DataSourceUnavailableError` directly and are not retried.
    Never escapes the module: `_fetch_tile` converts a retry-exhausted transient
    into the user-facing `DataSourceUnavailableError` tier.
    """


def resolve_dem(
    bounds: tuple[float, float, float, float],
    cache_root: pathlib.Path,
    *,
    dem_version: str = DEFAULT_DEM_VERSION,
    force_refresh: bool = False,
    progress: StageProgress | None = None,
    fetch_workers: int | None = None,
) -> pathlib.Path:
    """Return a local DEM GeoTIFF covering `bounds`, downloading + caching if absent.

    Args:
        bounds: `(west, south, east, north)` in WGS84 degrees that the raster must
            cover. The setup path derives this from the prepared graph's geometry
            envelope via `graph_dem_bounds`, so the DEM is guaranteed to cover
            every vertex `sample_elevation` will probe.
        cache_root: cache root from `cache.resolve_cache_root` (honors `--cache-dir`).
        dem_version: DEM release tag. Folded into the raster cache key so a changed
            `--dem-version` (e.g. after an IGN dataset bump) re-downloads rather than
            relabelling stale bytes — keeping the manifest's `dem_version` honest.
        force_refresh: re-download even when a cached raster exists.
        progress: optional stage seam (Story 11.1, FR33) — the tile-fetch loop
            reports `tile i/N` through it. `None` (the default) is silent.
        fetch_workers: max concurrent tile fetches (`--dem-fetch-workers`).
            `None` (the default) uses `DEFAULT_DEM_FETCH_WORKERS`.

    Returns:
        Path to a single-band float32 WGS84 GeoTIFF readable by `sample_elevation`.

    Raises:
        DataSourceUnavailableError: the IGN WMS is unreachable, times out, returns
            an HTTP error, or returns an unexpected / non-BIL payload.
    """
    west, south, east, north = bounds
    center_lat = (south + north) / 2.0
    width, height = _grid_dims(west, south, east, north, center_lat)
    dem_key = _dem_cache_key(west, south, east, north, width, height, dem_version)
    path = dem_cache_path_for(cache_root, dem_key)

    if path.is_file() and not force_refresh:
        _logger.debug("DEM cache hit for bounds %s: %s", bounds, path)
        return path

    _logger.debug(
        "DEM cache miss for bounds %s; fetching %dx%d px from IGN WMS.",
        bounds,
        width,
        height,
    )
    arr = _fetch_mosaic(
        west, south, east, north, width, height, progress=progress, max_workers=fetch_workers
    )
    _write_geotiff_atomic(path, arr, west, south, east, north)
    return path


def graph_dem_bounds(
    graph: nx.MultiDiGraph,
    *,
    margin_m: float = _GRAPH_BOUNDS_MARGIN_M,
) -> tuple[float, float, float, float]:
    """Return the WGS84 `(west, south, east, north)` covering every edge vertex + margin.

    Sizing the DEM from the graph's actual edge geometry — rather than the nominal
    OSM fetch bbox — guarantees the raster covers the vertices `sample_elevation`
    probes, regardless of how far osmnx's simplified geometry bulges past the fetch
    bbox (see `_GRAPH_BOUNDS_MARGIN_M`). The margin keeps the extreme vertices
    strictly interior to the raster so the half-open east/north bounds check passes.

    Must be called on the post-stage-4 graph (every edge carries a `LineString`
    `geometry`); the orchestrator's non-empty guard ensures at least one edge.
    """
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for _u, _v, _k, data in graph.edges(data=True, keys=True):
        geom = data.get("geometry")
        if not isinstance(geom, shapely.LineString):
            raise TypeError(
                "pipeline.dem_download: edge geometry must be a shapely.LineString, "
                f"got {type(geom).__name__}"
            )
        gx0, gy0, gx1, gy1 = geom.bounds
        minx, miny = min(minx, gx0), min(miny, gy0)
        maxx, maxy = max(maxx, gx1), max(maxy, gy1)
    if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
        raise ValueError(
            "pipeline.dem_download: graph has no edge geometry to bound; "
            "graph_dem_bounds requires a non-empty post-stage-4 graph."
        )
    lat = (miny + maxy) / 2.0
    margin_lat = margin_m / _M_PER_DEG_LAT
    margin_lon = margin_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return (minx - margin_lon, miny - margin_lat, maxx + margin_lon, maxy + margin_lat)


def _padded_bbox(area: Area) -> tuple[float, float, float, float]:  # pyright: ignore[reportUnusedFunction]
    """Return `(west, south, east, north)` in WGS84 degrees covering area + padding.

    Retained as the canonical area→bbox derivation for the offline/live DEM tests
    (`test_dem_download` / `test_dem_live`) that fetch a raster for a nominal area.
    The production setup path sizes the DEM from `graph_dem_bounds` instead.
    """
    lat, lon = area.center
    half_side_m = area.radius_km * 1000.0 + _PADDING_M
    half_lat_deg = half_side_m / _M_PER_DEG_LAT
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(lat))
    half_lon_deg = half_side_m / m_per_deg_lon
    return (lon - half_lon_deg, lat - half_lat_deg, lon + half_lon_deg, lat + half_lat_deg)


def _grid_dims(
    west: float,
    south: float,
    east: float,
    north: float,
    center_lat: float,
) -> tuple[int, int]:
    """Pixel `(width, height)` for the bbox at ~`_TARGET_RES_M` ground resolution."""
    m_per_deg_lon = _M_PER_DEG_LAT * math.cos(math.radians(center_lat))
    width_m = (east - west) * m_per_deg_lon
    height_m = (north - south) * _M_PER_DEG_LAT
    width = max(1, round(width_m / _TARGET_RES_M))
    height = max(1, round(height_m / _TARGET_RES_M))
    return width, height


def _dem_cache_key(
    west: float,
    south: float,
    east: float,
    north: float,
    width: int,
    height: int,
    dem_version: str,
) -> str:
    """Stable 16-hex key over the bbox, grid, layer, format, and DEM version.

    Includes the layer + format so a future source/format change never reuses a
    raster fetched under the old one, and `dem_version` so a changed `--dem-version`
    forces a fresh download instead of reusing the previously cached raster.
    """
    parts = (
        f"{round(west, _BBOX_DECIMALS)}",
        f"{round(south, _BBOX_DECIMALS)}",
        f"{round(east, _BBOX_DECIMALS)}",
        f"{round(north, _BBOX_DECIMALS)}",
        str(width),
        str(height),
        _LAYER,
        _BIL_FORMAT,
        dem_version,
    )
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:_DEM_KEY_HEX_LEN]


def _tile_ranges(n: int) -> Iterator[tuple[int, int]]:
    """Yield `(start, end)` pixel blocks of at most `_MAX_TILE_PX` covering `[0, n)`."""
    i = 0
    while i < n:
        j = min(i + _MAX_TILE_PX, n)
        yield i, j
        i = j


def _fetch_tile(
    y0: int,
    y1: int,
    x0: int,
    x1: int,
    west: float,
    south: float,
    east: float,
    north: float,
    width: int,
    height: int,
) -> tuple[int, int, int, int, bytes]:
    """Fetch one tile's BIL bytes; returns `(y0, y1, x0, x1, body)` for the parent.

    Runs on a worker thread. Computes the tile's sub-bbox with the exact same linear
    interpolation the sequential path used (so the assembled mosaic is byte-identical)
    and issues the blocking WMS request, retrying transient failures up to
    `_TILE_MAX_ATTEMPTS` times with jittered exponential backoff. Validation, reshape,
    and array placement stay in the parent thread (`_fetch_mosaic`) — the worker only
    does the network wait. A retry-exhausted transient (or any deterministic failure)
    surfaces as `DataSourceUnavailableError` and is re-raised unchanged when the parent
    calls `future.result()`.
    """
    tile_w = x1 - x0
    tile_h = y1 - y0
    # Linearly interpolate this tile's sub-bbox over the full bbox.
    # x grows west→east; y (row index) grows north→south.
    t_west = west + (east - west) * (x0 / width)
    t_east = west + (east - west) * (x1 / width)
    t_north = north - (north - south) * (y0 / height)
    t_south = north - (north - south) * (y1 / height)
    last_exc: _TransientDEMError | None = None
    for attempt in range(_TILE_MAX_ATTEMPTS):
        try:
            body = _wms_get_bil(t_west, t_south, t_east, t_north, tile_w, tile_h)
            return y0, y1, x0, x1, body
        except _TransientDEMError as exc:
            last_exc = exc
            if attempt + 1 < _TILE_MAX_ATTEMPTS:
                # Exponential backoff with full jitter: sleep in [0, base·2^attempt).
                delay = _TILE_BACKOFF_BASE_S * (2**attempt)
                _logger.warning(
                    "DEM tile fetch attempt %d/%d failed (%s); retrying",
                    attempt + 1,
                    _TILE_MAX_ATTEMPTS,
                    last_exc,
                )
                time.sleep(random.uniform(0, delay))
    raise DataSourceUnavailableError(
        "DEM source unreachable.",
        detail=f"IGN WMS tile failed after {_TILE_MAX_ATTEMPTS} attempts: {last_exc!r}",
    ) from last_exc


def _fetch_mosaic(
    west: float,
    south: float,
    east: float,
    north: float,
    width: int,
    height: int,
    *,
    progress: StageProgress | None = None,
    max_workers: int | None = None,
) -> np.ndarray:
    """Fetch the DEM as one or more WMS tiles and stitch into a single float32 array.

    Tiles are fetched concurrently on a `ThreadPoolExecutor` (`max_workers`, or
    `DEFAULT_DEM_FETCH_WORKERS` when `None`); each worker returns its raw BIL bytes
    and the parent thread validates the byte count, reshapes, and writes into the
    tile's disjoint `arr[y0:y1, x0:x1]` slice. Because every tile covers a distinct
    slice and each sub-bbox is computed identically to the old sequential path, the
    assembled mosaic is byte-identical regardless of the order workers complete in
    and regardless of `max_workers`. Row 0 of the returned array is the north edge
    (WMS GetMap origin), matching rasterio's top-left raster origin so the later
    `from_bounds` transform is correct.

    When `progress` is set, a `tile 0/N` line is emitted before any request so the
    seam isn't silent during the first (or only) tile's network wait, then each tile
    reports `tile i/N` as it completes (a monotonic completion counter emitted from
    the parent thread — `StageProgress` is not thread-safe — so the sequence stays
    `tile 0/N … tile N/N`; FR33 within-stage progress, "working" not "stuck").

    On a terminal failure (a tile exhausting its retries, or a byte-count mismatch)
    the executor is shut down with `cancel_futures=True` so tiles not yet started are
    dropped rather than fired at an already-failing server; the ≤ `max_workers` tiles
    genuinely in flight still finish as the context exits.
    """
    truststore.inject_into_ssl()
    workers = max_workers if max_workers is not None else DEFAULT_DEM_FETCH_WORKERS
    arr = np.empty((height, width), dtype=_BIL_DTYPE)
    tiles = [(y0, y1, x0, x1) for y0, y1 in _tile_ranges(height) for x0, x1 in _tile_ranges(width)]
    total_tiles = len(tiles)
    completed = 0
    if progress is not None:
        progress.line(f"tile {completed}/{total_tiles}")
    with ThreadPoolExecutor(max_workers=min(workers, total_tiles)) as pool:
        futures = [
            pool.submit(_fetch_tile, y0, y1, x0, x1, west, south, east, north, width, height)
            for y0, y1, x0, x1 in tiles
        ]
        try:
            for future in as_completed(futures):
                # Re-raises a worker's DataSourceUnavailableError unchanged.
                y0, y1, x0, x1, body = future.result()
                tile_w = x1 - x0
                tile_h = y1 - y0
                expected = tile_w * tile_h * 4
                if len(body) != expected:
                    raise DataSourceUnavailableError(
                        "DEM source unreachable.",
                        detail=(
                            f"IGN WMS returned {len(body)} bytes for a {tile_w}x{tile_h} "
                            f"float32 tile (expected {expected}). The endpoint may be "
                            f"returning an error document instead of BIL data."
                        ),
                    )
                arr[y0:y1, x0:x1] = np.frombuffer(body, dtype=_BIL_DTYPE).reshape((tile_h, tile_w))
                completed += 1
                if progress is not None:
                    progress.line(f"tile {completed}/{total_tiles}")
        except BaseException:
            # Drop tiles not yet started so a failing (or interrupted) run doesn't fire
            # the rest of the batch at the server; in-flight tiles finish on context exit.
            # BaseException so Ctrl-C (KeyboardInterrupt) also cancels rather than drains.
            pool.shutdown(cancel_futures=True)
            raise
    return arr


def _wms_get_bil(
    west: float,
    south: float,
    east: float,
    north: float,
    width: int,
    height: int,
) -> bytes:
    """Issue one WMS 1.3.0 GetMap for the bbox and return the raw BIL response body.

    `crs=CRS:84` selects WGS84 lon/lat axis order, so the bbox is `west,south,
    east,north` (EPSG:4326 under WMS 1.3.0 would be lat-first). Mirrors the
    committed fixture script's request shape exactly.
    """
    params = {
        "service": "WMS",
        "version": _WMS_VERSION,
        "request": "GetMap",
        "layers": _LAYER,
        "styles": "",
        "crs": "CRS:84",
        "bbox": f"{west},{south},{east},{north}",
        "width": width,
        "height": height,
        "format": _BIL_FORMAT,
    }
    url = f"{_WMS_URL}?{urllib.parse.urlencode(params)}"
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            content_type = resp.headers.get_content_type()
            body = resp.read()
    except http.client.IncompleteRead as exc:
        # Truncated read is transient (dropped connection mid-transfer) — retryable.
        raise _TransientDEMError(f"IGN WMS GetMap returned a truncated response: {exc!r}") from exc
    except (urllib.error.URLError, OSError) as exc:
        # `URLError` is the base of urllib's network failures (`HTTPError` for
        # 429/5xx, connection refused, DNS, TLS); `OSError` covers low-level socket /
        # timeout cases. All are transient and retryable; a retry-exhausted failure is
        # mapped to the source-unavailable tier (Cat 10) by `_fetch_tile`.
        raise _TransientDEMError(f"IGN WMS GetMap failed: {exc!r}") from exc
    # WMS reports failures as a 200-OK `ServiceExceptionReport` (XML), and proxies
    # can interpose HTML/text error pages — any of which could coincidentally match
    # the expected BIL byte count and be decoded as garbage elevations. Reject any
    # textual content type up front; real BIL is a binary type (image/x-bil or
    # application/octet-stream).
    if any(token in content_type for token in ("xml", "html", "json", "text")):
        raise DataSourceUnavailableError(
            "DEM source unreachable.",
            detail=(
                f"IGN WMS returned a {content_type!r} document instead of BIL data "
                f"(first 200 bytes: {body[:200]!r})."
            ),
        )
    return body


def _write_geotiff_atomic(
    path: pathlib.Path,
    arr: np.ndarray,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Write `arr` as a single-band float32 WGS84 GeoTIFF at `path`, atomically.

    `.tmp` sibling + `os.replace` so a Ctrl-C mid-write never leaves a partial
    raster the next run would treat as a cache hit (same pattern as
    `cache.write_text_atomic`).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = arr.shape
    transform = from_bounds(
        west=west, south=south, east=east, north=north, width=width, height=height
    )
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "float32",
        "crs": "EPSG:4326",
        "transform": transform,
        "nodata": None,
        "compress": "deflate",
        "predictor": 3,  # float predictor; roughly halves deflate output for float rasters.
        "tiled": True,
    }
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with rasterio.open(tmp_path, "w", **profile) as dst:
            dst.write(arr, 1)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
