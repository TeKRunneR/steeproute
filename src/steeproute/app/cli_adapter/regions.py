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
from typing import TypedDict

from steeproute import cache
from steeproute.app.models import AreaResolution, AreaSpec, RegionBounds, RegionInfo
from steeproute.cli import _shared as cli_shared
from steeproute.errors import BadCLIArgError
from steeproute.models import Area


class _GeometryFields(TypedDict):
    """The `AreaGeometry` fields both `RegionInfo` and `AreaResolution` carry."""

    center: tuple[float, float]
    radius_km: float | None
    width_km: float
    height_km: float
    angle_deg: float
    polygon: list[tuple[float, float]]
    bounds: RegionBounds


def to_cli_area(area: AreaSpec) -> Area:
    """Convert the App's wire area into the CLI domain `Area`.

    Delegates to the CLI's own `cli/_shared.resolve_area` — the authoritative
    owner of the area surface — so the App cannot drift from it on any of the
    three things that matter: the **halving** of full `width`/`height` into `Area`
    half-extents, the *literal* square construction for the radius shorthand
    (what keeps a square's cache key / fetch / manifest byte-identical), and the
    exactly-one-of rule.

    `AreaSpec` already validated the shape (so a request fails 422 before getting
    here); the CLI resolver's `BadCLIArgError` is re-raised as `ValueError` — it
    means the App's guard and the CLI's rule have genuinely diverged, and keeping
    the CLI error type inside this package is what lets the API layer stay free of
    `steeproute.*` imports.
    """
    try:
        return cli_shared.resolve_area(
            center=area.center,
            radius_km=area.radius_km,
            width_km=area.width_km,
            height_km=area.height_km,
            angle_deg=area.angle_deg,
        )
    except BadCLIArgError as exc:
        raise ValueError(str(exc)) from exc


def _geometry_fields(area: Area) -> _GeometryFields:
    """Project a CLI `Area` into the App's geometry view.

    Everything comes from the cache's own helpers: `area_polygon` for the true
    (possibly rotated) ring — the very geometry coverage is tested against — and
    `area_bbox_wgs84` for its axis-aligned envelope. `radius_km` is reported only
    for a centered square (`Area.is_square`), never as the inert `0.0` a rotated
    `Area` carries; a client that needs a size for any shape reads
    `width_km`/`height_km` instead. Note a *rotated square* therefore reports
    dimensions rather than a radius: the cache does not record which spelling
    prepared an entry (see `cache.format_area_flags`).
    """
    lat, lon = area.center
    half_width_km, half_height_km = area.half_extents_km
    south, west, north, east = cache.area_bbox_wgs84(area)
    return {
        "center": (lat, lon),
        "radius_km": half_width_km if area.is_square else None,
        "width_km": 2.0 * half_width_km,
        "height_km": 2.0 * half_height_km,
        "angle_deg": area.angle_deg,
        "polygon": _polygon_latlon(area),
        "bounds": RegionBounds(south=south, west=west, north=north, east=east),
    }


def _polygon_latlon(area: Area) -> list[tuple[float, float]]:
    """`area`'s true ring as `[lat, lon]` vertices for Leaflet.

    `cache.area_polygon` returns `(lon, lat)` (RFC 7946) with the first vertex
    repeated last to close the ring; the App's wire convention is `[lat, lon]`
    (matching `AreaSpec.center`) and Leaflet closes a polygon itself, so the axis
    flip and the closing-vertex drop both happen here — the single boundary
    between the two conventions.
    """
    ring = [(float(lon), float(lat)) for lon, lat in cache.area_polygon(area).exterior.coords]
    return [(lat, lon) for lon, lat in ring[:-1]]


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
    area: AreaSpec,
    *,
    cache_root: pathlib.Path | None = None,
) -> AreaResolution:
    """Resolve a candidate selection to its geometry + green/grey coverage decision.

    Server-side authority for the map picker: the true polygon and its envelope
    come from the CLI cache's own conversion, and the coverage decision from its
    own containment (`cache.find_covering_entry`), so the frontend re-derives
    neither. `covered` is true iff some built region strictly contains the
    selection — the same orientation-aware rule the query CLI applies, so a
    selection inside a rotated entry's *envelope* but outside the box itself is
    correctly declined.

    Raises:
        ValueError: the area's shape is malformed per the CLI resolver (see
            `to_cli_area`); the API layer maps this to 422.
    """
    root = cache_root if cache_root is not None else cache.resolve_cache_root()
    cli_area = to_cli_area(area)
    covering = cache.find_covering_entry(root, cli_area)
    return AreaResolution(
        **_geometry_fields(cli_area),
        covered=covering is not None,
        cache_key_hash=covering.cache_key_hash if covering is not None else None,
    )


def _to_region_info(entry: cache.CoverageEntry) -> RegionInfo:
    """Project one prepared cache entry into the App's overlay shape.

    Carries the entry's **true** (possibly rotated) polygon alongside its
    axis-aligned envelope, never the envelope alone: an envelope-only overlay draws
    a rotated entry too large, and its `radius_km` reads as the inert `0.0`.
    `_geometry_fields` is what keeps both honest.
    """
    return RegionInfo(cache_key_hash=entry.cache_key_hash, **_geometry_fields(entry.area))
