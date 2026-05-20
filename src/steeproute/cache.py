"""Cache I/O: key hashing + manifest schema (Story 2.6); atomic writes + coverage check land in Stories 2.7 + 2.10.

`compute_cache_key` is the single source of truth for which inputs invalidate a cached graph
(Architecture §Cat 4b). `Manifest` is the wire schema written last as the atomic commit signal
(§Cat 4d). The package is the sole reader/writer of the cache directory (§Boundaries — Cache
boundary), so all serialization concerns live here too.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass

from steeproute.models import Area

# SHA256 truncation length for cache-entry directory names. Short enough to keep
# directory listings readable, long enough that collision risk across a single
# user's cache footprint is negligible (16 hex = 64 bits).
_CACHE_KEY_HEX_LEN: int = 16

# Decimal places used to round Area fields *before* hashing. Lat/lon at 6
# decimals is ~11 cm at the equator — two orders of magnitude finer than OSM
# tagging precision but coarse enough to absorb float-print noise. Radius_km
# at 3 decimals = 1 m, well below the user-facing CLI granularity.
_AREA_LAT_LON_DECIMALS: int = 6
_AREA_RADIUS_KM_DECIMALS: int = 3

# Sole Area mode in v1. A future polygon or named-region mode would dispatch here.
_AREA_MODE_LITERAL: str = "center_radius"

# Files whose byte content flows into the pipeline content hash. Architecture
# §Cat 4b: changes here effectively invalidate all cached entries (the key
# shifts). `cache.py` and `provenance.py` are deliberately excluded — they
# touch *how* graphs are persisted, not what the graph contains.
_PIPELINE_CONTENT_GLOBS: tuple[str, ...] = ("pipeline/**/*.py", "models.py")


def compute_cache_key(
    area: Area,
    untagged_policy: str,
    dem_version: str,
    pipeline_content_hash: str,
) -> str:
    """Compute the 16-hex SHA256 truncation that identifies a cache entry.

    Pure function over the four inputs from Architecture §Cat 4b. Area
    coordinates and radius are rounded to the canonical precisions before
    hashing so floating-point noise doesn't produce phantom misses.

    Args:
        area: search area (`center`, `radius_km`) — canonicalized internally.
        untagged_policy: `"include"` or `"exclude"` — comes from `--untagged-trails`.
        dem_version: DEM release tag — comes from `--dem-version` or derived
            metadata (derivation lives in Story 2.8; this layer trusts the caller).
        pipeline_content_hash: full SHA256 of pipeline source bytes, typically
            from `compute_pipeline_content_hash()`.

    Returns:
        16-character lowercase-hex string.
    """
    canonical: dict[str, object] = {
        "area": _canonicalize_area(area),
        "untagged_policy": untagged_policy,
        "dem_version": dem_version,
        "pipeline_content_hash": pipeline_content_hash,
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return digest[:_CACHE_KEY_HEX_LEN]


def _canonicalize_area(area: Area) -> dict[str, object]:
    """Round Area fields to the precisions encoded in the cache key."""
    lat, lon = area.center
    return {
        "mode": _AREA_MODE_LITERAL,
        "center": [
            round(float(lat), _AREA_LAT_LON_DECIMALS),
            round(float(lon), _AREA_LAT_LON_DECIMALS),
        ],
        "radius_km": round(float(area.radius_km), _AREA_RADIUS_KM_DECIMALS),
    }


def compute_pipeline_content_hash() -> str:
    """Compute the SHA256 over pipeline + models source bytes.

    Files are sorted by their POSIX-form path relative to the package root so
    the hash is identical across Windows and POSIX hosts (`Path.glob` produces
    backslashes on Windows; sorting raw strings would diverge).

    Returns:
        64-character lowercase-hex string.
    """
    package_root = pathlib.Path(__file__).parent
    files: list[pathlib.Path] = []
    for pattern in _PIPELINE_CONTENT_GLOBS:
        files.extend(package_root.glob(pattern))
    files.sort(key=lambda p: p.relative_to(package_root).as_posix())
    hasher = hashlib.sha256()
    for path in files:
        hasher.update(path.read_bytes())
    return hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class Manifest:
    """Cache-entry metadata schema per Architecture §Cat 4.

    `manifest.json` is the atomic commit signal — a cache entry directory
    without a valid `manifest.json` is ignored by readers (Story 2.7 wires the
    write order). `to_dict()` produces the on-disk wire shape; Story 2.7's
    `write_json_atomic` calls it.

    `schema_version` carries a default so future schemas can be detected at
    read time without breaking existing entries.
    """

    area: Area
    untagged_policy: str
    dem_version: str
    pipeline_content_hash: str
    osm_extract_date: str
    cache_key_hash: str
    steeproute_version: str
    steeproute_commit: str
    created_at: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        """Render the manifest as the JSON-ready dict for atomic writing."""
        lat, lon = self.area.center
        return {
            "schema_version": self.schema_version,
            "area": {
                "mode": _AREA_MODE_LITERAL,
                "center": [float(lat), float(lon)],
                "radius_km": float(self.area.radius_km),
            },
            "untagged_policy": self.untagged_policy,
            "dem_version": self.dem_version,
            "pipeline_content_hash": self.pipeline_content_hash,
            "osm_extract_date": self.osm_extract_date,
            "cache_key_hash": self.cache_key_hash,
            "steeproute_version": self.steeproute_version,
            "steeproute_commit": self.steeproute_commit,
            "created_at": self.created_at,
        }
