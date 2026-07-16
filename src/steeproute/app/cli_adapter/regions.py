"""Seam 2 — cache-manifest reading for `GET /regions` (architecture-app.md §Category 6).

The only place in the App that reads the CLI's on-disk cache layout. It lists the
prepared (built) areas so the map can render them as green overlays, going through
`steeproute.cache`'s public coverage API rather than parsing `index.json` itself —
`cache.py` stays the single source of cache-layout truth (it is "the sole
reader/writer of the cache directory").

Read-only: listing regions never writes or builds anything. Uses the CLI's
**default** cache root (the same `platformdirs` location `steeproute-setup` writes
to — `argv.py` deliberately omits `--cache-dir`), so a region built from the App
is visible to the overlay. `cache_root` is injectable purely so tests can point at
a crafted cache without touching the real one.
"""

from __future__ import annotations

import pathlib

from steeproute import cache
from steeproute.app.models import AreaResolution, RegionBounds, RegionInfo
from steeproute.models import Area


def list_regions(cache_root: pathlib.Path | None = None) -> list[RegionInfo]:
    """Return the built regions for the map overlay.

    Resolves the default cache root when `cache_root` is `None`. An empty or
    absent cache yields `[]` (never an error); the geometry each region carries
    is computed by the CLI cache's shared km→deg conversion so it matches
    query-side coverage exactly.
    """
    root = cache_root if cache_root is not None else cache.resolve_cache_root()
    return [_to_region_info(entry) for entry in cache.list_prepared_areas(root)]


def resolve_area(
    center: tuple[float, float],
    radius_km: float,
    *,
    cache_root: pathlib.Path | None = None,
) -> AreaResolution:
    """Resolve a candidate selection to its bbox + green/grey coverage decision.

    Server-side authority for the map picker: computes the WGS84 bbox with the
    CLI cache's own conversion and the coverage decision with its own containment
    (`cache.find_covering_entry`), so the frontend re-derives neither. `covered`
    is true iff some built region strictly contains the selection — the same rule
    the query CLI applies.
    """
    root = cache_root if cache_root is not None else cache.resolve_cache_root()
    area = Area(center=center, radius_km=radius_km)
    south, west, north, east = cache.area_bbox_wgs84(area)
    covering = cache.find_covering_entry(root, area)
    return AreaResolution(
        center=center,
        radius_km=radius_km,
        bounds=RegionBounds(south=south, west=west, north=north, east=east),
        covered=covering is not None,
        cache_key_hash=covering.cache_key_hash if covering is not None else None,
    )


def _to_region_info(entry: cache.CoverageEntry) -> RegionInfo:
    lat, lon = entry.area.center
    south, west, north, east = cache.area_bbox_wgs84(entry.area)
    return RegionInfo(
        cache_key_hash=entry.cache_key_hash,
        center=(lat, lon),
        radius_km=entry.area.radius_km,
        bounds=RegionBounds(south=south, west=west, north=north, east=east),
    )
