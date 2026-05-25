# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx MultiDiGraph operations surface as Unknown; same external-boundary
# pattern as pipeline/__init__.py, pipeline/osm.py, pipeline/smoothing.py, etc.
"""Cache I/O: key hashing + manifest schema (Story 2.6); atomic write + read + index (Story 2.7); coverage check (Story 2.10).

`compute_cache_key` is the single source of truth for which inputs invalidate a cached graph
(Architecture §Cat 4b). `Manifest` is the wire schema written last as the atomic commit signal
(§Cat 4d). `write_entry` / `read_entry` / `rebuild_index` (Story 2.7) implement the `.tmp/`
→ `os.replace()` atomic pattern that guarantees a Ctrl-C mid-write cannot surface a partial
entry. `check_coverage` (Story 2.10) is the FR24 query-side surface — strict `shapely.contains`
against `index.json` entries with smallest-radius tiebreak; it opportunistically rebuilds the
index when a prior `write_entry` was interrupted between manifest commit and index rebuild
(closes Story 2.7 D1). The package is the sole reader/writer of the cache directory
(§Boundaries — Cache boundary), so all serialization concerns live here too. All JSON writes
route through the single `write_json_atomic` helper per Architecture §Key anti-patterns.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import pathlib
import pickle
import shutil
from dataclasses import dataclass
from typing import Any

import networkx as nx
import platformdirs
import shapely

from steeproute.errors import CacheCorruptedError, CacheNotFoundError
from steeproute.models import Area

_logger = logging.getLogger(__name__)

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

# Cache layout — Architecture §Cat 4a. The `steeproute/` subdir under the cache
# root means platformdirs' shared cache-root locations (e.g. `%LOCALAPPDATA%\Cache`)
# stay free of stray files in case a future tool ever drops in alongside.
_CACHE_SUBDIR: str = "steeproute"
_AREAS_SUBDIR: str = "areas"
_APP_NAME: str = "steeproute"

# File names inside an entry directory. Order matters for Cat 4d: graph + bounds
# materialize first inside the `.tmp/` staging dir, then the manifest is the
# last write into the final entry directory — manifest presence is the entry
# validity signal for readers.
_MANIFEST_FILENAME: str = "manifest.json"
_GRAPH_FILENAME: str = "graph.pkl"
_BOUNDS_FILENAME: str = "bounds.geojson"
_INDEX_FILENAME: str = "index.json"

# Atomic-write directory swap markers. `.tmp/` holds in-flight writes;
# `.old/` holds the previous entry during an overwrite swap on Windows
# (where directory `os.replace` requires the target not exist).
_TMP_DIR_SUFFIX: str = ".tmp"
_OLD_DIR_SUFFIX: str = ".old"

# `manifest.json` and `index.json` versions advance independently. v1 is the
# initial released schema; bumping is a coordinated change across both CLIs
# per Architecture §Versioned-contract-surfaces.
_MANIFEST_SCHEMA_VERSION: int = 1
_INDEX_SCHEMA_VERSION: int = 1


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
    write order). `to_dict()` produces the on-disk wire shape; `from_dict()`
    is its inverse, used by `read_entry` to rehydrate manifests from disk.

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
    schema_version: int = _MANIFEST_SCHEMA_VERSION

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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Manifest:
        """Rehydrate a manifest from its on-disk dict shape.

        Raises:
            CacheCorruptedError: if `schema_version` is missing or unknown, or
                if a required top-level field is absent. We deliberately do
                *not* attempt compat shims for newer/older schemas — a
                version-mismatch is surfaced cleanly per Architecture
                §Versioned-contract-surfaces.
        """
        version = payload.get("schema_version")
        if not isinstance(version, int) or version != _MANIFEST_SCHEMA_VERSION:
            raise CacheCorruptedError(
                user_message="Cache entry has an incompatible manifest schema version.",
                detail=(
                    f"Expected schema_version={_MANIFEST_SCHEMA_VERSION}, "
                    f"found {version!r}. Re-prepare with `steeproute-setup --force-refresh`."
                ),
            )
        area_payload = payload.get("area")
        if not isinstance(area_payload, dict):
            raise CacheCorruptedError(
                user_message="Cache manifest is missing or malformed `area` field.",
                detail=f"Got area={area_payload!r}.",
            )
        center = area_payload.get("center")
        radius_km = area_payload.get("radius_km")
        if (
            not isinstance(center, list)
            or len(center) != 2
            or not isinstance(radius_km, (int, float))
        ):
            raise CacheCorruptedError(
                user_message="Cache manifest `area` payload is malformed.",
                detail=f"Got area={area_payload!r}.",
            )
        try:
            area = Area(
                center=(float(center[0]), float(center[1])),
                radius_km=float(radius_km),
            )
        except (TypeError, ValueError) as exc:
            # `float()` rejects non-numeric input — a manifest with
            # `"center": ["forty-five", "six"]` reaches this branch instead of
            # leaking a raw ValueError past the `CacheCorruptedError` contract.
            raise CacheCorruptedError(
                user_message="Cache manifest `area` coordinates are not numeric.",
                detail=f"Got area={area_payload!r}; conversion failed: {exc}.",
            ) from exc

        # All other manifest fields are typed `str` in the dataclass. A JSON
        # `null` would coerce to the literal string `"None"` via `str(...)` and
        # silently propagate into directory names + display strings — reject
        # non-string inputs cleanly instead.
        _STRING_FIELDS: tuple[str, ...] = (
            "untagged_policy",
            "dem_version",
            "pipeline_content_hash",
            "osm_extract_date",
            "cache_key_hash",
            "steeproute_version",
            "steeproute_commit",
            "created_at",
        )
        for field_name in _STRING_FIELDS:
            value = payload.get(field_name)
            if not isinstance(value, str):
                raise CacheCorruptedError(
                    user_message=(
                        f"Cache manifest field {field_name!r} is missing or not a string."
                    ),
                    detail=f"Got {field_name}={value!r}.",
                )

        return cls(
            area=area,
            untagged_policy=payload["untagged_policy"],
            dem_version=payload["dem_version"],
            pipeline_content_hash=payload["pipeline_content_hash"],
            osm_extract_date=payload["osm_extract_date"],
            cache_key_hash=payload["cache_key_hash"],
            steeproute_version=payload["steeproute_version"],
            steeproute_commit=payload["steeproute_commit"],
            created_at=payload["created_at"],
            schema_version=version,
        )


@dataclass(frozen=True, slots=True)
class PreparedData:
    """A cache entry rehydrated from disk: the setup-pipeline graph + its manifest.

    Returned by `read_entry` (Story 2.7) and `check_coverage` (Story 2.10). The
    bundle keeps the typed metadata next to the graph so callers (Story 2.9's
    OSM-age warning, Epic 3's report metadata block) don't have to re-open the
    manifest file.
    """

    graph: nx.MultiDiGraph
    manifest: Manifest


def resolve_cache_root(override: pathlib.Path | None = None) -> pathlib.Path:
    """Return the cache root path, defaulting to platformdirs' user cache dir.

    Story 2.8 wires `--cache-dir` into this helper. Callers must not interpret
    the returned path themselves — pass it to `write_entry` / `read_entry` /
    `rebuild_index` which apply the `steeproute/areas/...` layout consistently.

    Args:
        override: explicit cache-root path (e.g. from `--cache-dir`); when
            `None`, falls back to `platformdirs.user_cache_dir(_APP_NAME)`.
    """
    if override is not None:
        return override
    return pathlib.Path(platformdirs.user_cache_dir(_APP_NAME))


def entry_dir_for(cache_root: pathlib.Path, cache_key: str) -> pathlib.Path:
    """Return the canonical entry directory for `cache_key` under `cache_root`.

    Single source of truth for the `<cache-root>/steeproute/areas/<hash>/` layout
    (Architecture §Cat 4a). External callers (e.g. `cli/setup.py`'s cache-hit
    summary) use this rather than reconstructing the path by string concatenation
    — that would create a second source of layout truth that could silently
    diverge from `write_entry`'s own composition.

    Note: this does not check whether the entry actually exists or is valid;
    use `read_entry` for validation. This is purely a layout-resolution helper.
    """
    return _areas_dir(cache_root) / cache_key


def write_json_atomic(path: pathlib.Path, obj: object) -> None:
    """Write `obj` to `path` atomically as canonical JSON.

    Single chokepoint for all JSON writes in this module per Architecture
    §Key anti-patterns ("no per-site reimplementation"). Writes to a sibling
    `.tmp` file then `os.replace()`s into place — a Ctrl-C mid-write leaves
    the `.tmp` sibling but never a half-written target.

    `sort_keys=True` makes the output diff-stable across runs so cache state
    is reviewable in version-control diffs (e.g. when `--cache-dir` points
    into a repo for test fixtures).
    """
    tmp_path = path.with_name(path.name + _TMP_DIR_SUFFIX)
    try:
        tmp_path.write_text(
            json.dumps(obj, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
    except BaseException:
        # ENOSPC / EACCES / cross-device / Ctrl-C mid-write all leave the
        # `.tmp` sibling behind. Clean it up so orphans don't accumulate.
        tmp_path.unlink(missing_ok=True)
        raise


def write_entry(
    cache_root: pathlib.Path,
    manifest: Manifest,
    graph: nx.MultiDiGraph,
) -> pathlib.Path:
    """Atomically write a cache entry per Architecture §Cat 4d.

    Write order (non-negotiable): `graph.pkl` + `bounds.geojson` materialize
    inside `<cache-root>/steeproute/areas/<hash>.tmp/`, the staging dir is
    swapped into place as `<hash>/` (using a `<hash>.old/` shuffle on Windows
    where directory `os.replace` requires the target not exist), and
    `manifest.json` is written **last** as the commit signal. A Ctrl-C
    anywhere before the manifest write means readers see no entry at this
    key; a Ctrl-C between the manifest write and the index rebuild means
    the entry is readable via `read_entry` but `rebuild_index` will pick it
    up on the next setup run.

    Returns the final entry directory path.
    """
    areas_dir = _areas_dir(cache_root)
    areas_dir.mkdir(parents=True, exist_ok=True)

    entry_dir = areas_dir / manifest.cache_key_hash
    staging_dir = areas_dir / (manifest.cache_key_hash + _TMP_DIR_SUFFIX)
    backup_dir = areas_dir / (manifest.cache_key_hash + _OLD_DIR_SUFFIX)

    # Clear any pre-existing `.tmp/` or `.old/` from a prior aborted run on
    # this key (per-key opportunistic cleanup, Architecture §Cat 4d footnote).
    # Full sweeps across `areas/` are out of scope — N=1 doesn't justify it.
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    staging_dir.mkdir()
    # Step 1: graph.pkl + bounds.geojson into the staging directory.
    with (staging_dir / _GRAPH_FILENAME).open("wb") as graph_fp:
        pickle.dump(graph, graph_fp, protocol=pickle.HIGHEST_PROTOCOL)
    write_json_atomic(staging_dir / _BOUNDS_FILENAME, _bounds_geojson(manifest.area))

    # Step 2-3: swap staging → final via `<hash>.old/` shuffle (Windows-safe).
    # An existing entry at this key is moved aside first so the directory
    # rename's target doesn't exist; the backup is removed only once the
    # new entry's manifest has landed.
    if entry_dir.exists():
        os.replace(entry_dir, backup_dir)
    os.replace(staging_dir, entry_dir)

    # Step 4: manifest.json written last as the atomic commit signal. If the
    # manifest write fails (ENOSPC, EACCES, KeyboardInterrupt), restore the
    # previous valid entry from `backup_dir` so the next `write_entry` call's
    # opportunistic cleanup doesn't sweep the backup away — otherwise an
    # interrupted overwrite would silently destroy the prior good entry.
    try:
        write_json_atomic(entry_dir / _MANIFEST_FILENAME, manifest.to_dict())
    except BaseException:
        if backup_dir.exists():
            shutil.rmtree(entry_dir, ignore_errors=True)
            os.replace(backup_dir, entry_dir)
        raise

    # Manifest landed — the entry is now valid. Discard the backup of the
    # previous entry (if any) and rebuild the index.
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    # Step 5: rebuild index.json atomically.
    rebuild_index(cache_root)

    return entry_dir


def read_entry(cache_root: pathlib.Path, cache_key: str) -> PreparedData:
    """Load the cache entry at `cache_key`, returning graph + manifest.

    Raises:
        CacheNotFoundError: entry directory or `manifest.json` is missing —
            the entry doesn't exist, or a prior write was interrupted before
            the manifest commit signal landed.
        CacheCorruptedError: the manifest exists but `graph.pkl` is unreadable
            (`pickle.UnpicklingError` / `EOFError`), or the manifest itself
            fails schema validation (`Manifest.from_dict` raises).
    """
    entry_dir = _areas_dir(cache_root) / cache_key
    manifest_path = entry_dir / _MANIFEST_FILENAME
    graph_path = entry_dir / _GRAPH_FILENAME

    if not manifest_path.is_file():
        raise CacheNotFoundError(
            user_message=f"No cache entry found for key {cache_key!r}.",
            detail=f"Expected manifest at {manifest_path}.",
        )

    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # `OSError` covers permission denied + I/O failure on the manifest
        # read; `UnicodeDecodeError` covers binary-garbage corruption. The
        # `CacheCorruptedError → exit 2` contract subsumes all three.
        raise CacheCorruptedError(
            user_message=f"Cache entry {cache_key!r} has an unreadable manifest.",
            detail=f"Manifest read/parse failed: {exc}.",
        ) from exc

    manifest = Manifest.from_dict(manifest_payload)

    try:
        with graph_path.open("rb") as graph_fp:
            graph: nx.MultiDiGraph = pickle.load(graph_fp)
    except (
        pickle.UnpicklingError,
        EOFError,
        FileNotFoundError,
        OSError,
        # `ImportError` / `AttributeError` surface when the cached pickle
        # references a class or attribute that's been renamed/removed since
        # the entry was written (e.g. cache predates a package refactor).
        # `ModuleNotFoundError` is a subclass of `ImportError` — no need to
        # list it separately.
        ImportError,
        AttributeError,
    ) as exc:
        raise CacheCorruptedError(
            user_message=f"Cache entry {cache_key!r} has an unreadable graph.",
            detail=f"`pickle.load` failed on {graph_path}: {exc}.",
        ) from exc

    return PreparedData(graph=graph, manifest=manifest)


def rebuild_index(cache_root: pathlib.Path) -> None:
    """Regenerate `<cache-root>/steeproute/index.json` from on-disk manifests.

    Walks `<cache-root>/steeproute/areas/*/manifest.json`, building one entry
    per valid manifest. Directories without a manifest (including in-flight
    `.tmp/` and prior-entry `.old/` shuffles) are skipped — they are not
    valid entries. The index is **derived state**: a missing or corrupt
    `index.json` is not an error; this function regenerates it.

    Entries are emitted sorted by `cache_key_hash` so the file is diff-stable
    across runs (matters when `--cache-dir` points into a fixture directory).
    """
    cache_subdir = cache_root / _CACHE_SUBDIR
    cache_subdir.mkdir(parents=True, exist_ok=True)
    areas_dir = cache_subdir / _AREAS_SUBDIR

    entries: list[dict[str, object]] = []
    if areas_dir.is_dir():
        for entry_dir in sorted(areas_dir.iterdir(), key=lambda p: p.name):
            # `_is_entry_dir` excludes staging (`.tmp/`) and rollback (`.old/`)
            # directories by suffix — a Ctrl-C mid-rollback can leave an `.old/`
            # with a valid-looking but stale manifest; admitting it into the
            # index would surface as a `read_entry` miss at query time
            # (areas/<hash> doesn't exist, only areas/<hash>.old).
            if not _is_entry_dir(entry_dir):
                continue
            manifest_path = entry_dir / _MANIFEST_FILENAME
            if not manifest_path.is_file():
                # Half-written entries (no manifest commit yet) — skip silently.
                continue
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = Manifest.from_dict(payload)
            except (
                json.JSONDecodeError,
                CacheCorruptedError,
                UnicodeDecodeError,
                OSError,
            ) as exc:
                # A corrupt or unreadable manifest is not an index-rebuild
                # concern — one bad entry must not block the rebuild for all
                # the others. The next `read_entry` against the bad key will
                # surface the error with full context. A `--verbose` user gets
                # a stderr warning here so the silent swallow is observable
                # (deferred-work D2 from Story 2.7).
                _logger.warning(
                    "cache.rebuild_index: skipping corrupt manifest at %s: %s",
                    manifest_path,
                    exc,
                )
                continue
            lat, lon = manifest.area.center
            entries.append(
                {
                    "cache_key_hash": manifest.cache_key_hash,
                    "area": {
                        "mode": _AREA_MODE_LITERAL,
                        "center": [float(lat), float(lon)],
                        "radius_km": float(manifest.area.radius_km),
                    },
                }
            )

    entries.sort(key=lambda e: e["cache_key_hash"])  # pyright: ignore[reportArgumentType, reportReturnType]
    write_json_atomic(
        cache_subdir / _INDEX_FILENAME,
        {"schema_version": _INDEX_SCHEMA_VERSION, "entries": entries},
    )


def _areas_dir(cache_root: pathlib.Path) -> pathlib.Path:
    """Resolve `<cache-root>/steeproute/areas/` — single source of layout truth."""
    return cache_root / _CACHE_SUBDIR / _AREAS_SUBDIR


def _bounds_geojson(area: Area) -> dict[str, object]:
    """Build a GeoJSON `Polygon` Feature for `area`'s WGS84 bbox.

    Matches the `bbox`-mode semantics `osmnx.graph_from_point(...,
    dist_type="bbox")` consumes upstream: `radius_km` is the half-side of
    an axis-aligned square in WGS84 degrees (lat/lon), **not** a disk radius.
    The 4-vertex polygon is the simplest faithful representation; Story 2.10's
    `check_coverage` builds an equivalent polygon at query time via the shared
    `_area_to_polygon` helper, so the on-disk sidecar and the in-memory
    coverage geometry can't silently diverge.
    """
    poly = _area_to_polygon(area)
    # `Polygon.exterior.coords` includes the closing duplicate vertex, matching
    # the prior hand-built ring's shape exactly.
    ring = [[float(x), float(y)] for x, y in poly.exterior.coords]
    lat, lon = area.center
    # `properties.center` uses GeoJSON `[lon, lat]` ordering for internal
    # consistency with `geometry.coordinates` (also `[lon, lat]` per RFC 7946).
    # The manifest's `area.center` keeps the `[lat, lon]` ordering Architecture
    # §Cat 4 specifies — that's a separate file and a separate convention.
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {
            "mode": _AREA_MODE_LITERAL,
            "center": [float(lon), float(lat)],
            "radius_km": float(area.radius_km),
        },
    }


# --- Coverage check (Story 2.10) ---------------------------------------------


# WGS84 km→deg approximation used by both `_bounds_geojson` and the coverage
# check. Lives at module scope so both sides share one source of truth — a
# polygon-skew between query and entry would cause phantom misses on edge
# cases. Architecture leaves the conversion implementation-defined for v1.
_DEG_PER_KM_LAT: float = 1.0 / 111.0


def _deg_per_km_lon(lat_deg: float) -> float:
    """Longitude-degrees-per-km factor at a given latitude (cos(lat) compensation).

    At the equator: ~1/111 deg/km. At ±60° N/S: ~1/55.5 deg/km.

    Polar guard: `math.cos(math.radians(90.0))` is `~6.12e-17` (not exactly 0,
    due to float imprecision in `math.radians`), so an `or 1.0` short-circuit
    fallback would never fire — the unfettered formula returns ~1.47e14 deg/km
    at lat=±90, producing polygons that span ~10^14 degrees of longitude.
    `LatLonParamType` accepts lat=±90 inclusive at the CLI boundary, so the
    pole case is reachable. We guard with an explicit epsilon check and treat
    near-pole inputs as equator (graceful degenerate — longitudes converge at
    the pole, so any sensible factor produces a degenerate polygon there).
    """
    cos_lat = math.cos(math.radians(lat_deg))
    # Epsilon chosen so |lat| ≥ 89.99° trips the fallback; below that, the
    # cos compensation is meaningful (at 89° cos ≈ 0.0175, factor ≈ 0.516 deg/km).
    if abs(cos_lat) < 1e-4:
        return 1.0 / 111.0
    return 1.0 / (111.0 * cos_lat)


def _area_to_polygon(area: Area) -> shapely.Polygon:
    """Build the WGS84 lon/lat polygon for `area`'s bbox half-side `radius_km`.

    Shared by `_bounds_geojson` (entry-side, persisted) and `check_coverage`
    (query-side, transient). Coordinates are `(lon, lat)` per RFC 7946 — the
    same axis order shapely uses everywhere else in the codebase (pipeline
    geometries are also lon/lat).

    The 5-point ring closes the polygon (first vertex repeated last). Empty /
    degenerate radii (≤0) would produce a zero-area or self-intersecting polygon
    and downstream `.contains` would return False for everything — acceptable
    for v1 since `validate_setup_radius` rejects non-positive radii at the CLI
    boundary (Story 2.8).
    """
    lat, lon = area.center
    dlat = area.radius_km * _DEG_PER_KM_LAT
    dlon = area.radius_km * _deg_per_km_lon(lat)
    return shapely.Polygon(
        [
            (lon - dlon, lat - dlat),
            (lon + dlon, lat - dlat),
            (lon + dlon, lat + dlat),
            (lon - dlon, lat + dlat),
            (lon - dlon, lat - dlat),
        ]
    )


@dataclass(frozen=True, slots=True)
class _IndexedEntry:
    """One row from `index.json`, internal to `cache.py` containment logic.

    Architecture §Cat 4 frames `index.json` as a coverage-lookup convenience
    file with two fields per entry (`cache_key_hash`, `area`); promoting this
    to a top-level dataclass would be over-engineering for v1 with one reader.
    """

    cache_key_hash: str
    area: Area


def _read_indexed_entries(index_path: pathlib.Path) -> list[_IndexedEntry] | None:
    """Parse `index.json` into `_IndexedEntry`s, or return `None` to signal "rebuild me".

    A `None` return means the file is missing, unparseable, schema-incompatible,
    or structurally malformed in any way — the caller's contract is then to
    invoke `rebuild_index` and retry. A `[]` return means the file parses
    cleanly but lists zero entries; the caller still cross-checks the on-disk
    `areas/` against this in case an interrupted `write_entry` left a stale
    empty index (Story 2.7 D1).
    """
    if not index_path.is_file():
        return None
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != _INDEX_SCHEMA_VERSION:
        return None
    entries_raw = payload.get("entries")
    if not isinstance(entries_raw, list):
        return None
    parsed: list[_IndexedEntry] = []
    for row in entries_raw:
        if not isinstance(row, dict):
            return None
        cache_key_hash = row.get("cache_key_hash")
        area_raw = row.get("area")
        if not isinstance(cache_key_hash, str) or not isinstance(area_raw, dict):
            return None
        center = area_raw.get("center")
        radius_km = area_raw.get("radius_km")
        # `isinstance(True, int)` is True in Python (bool subclasses int) and
        # `float(NaN)` / `float(Infinity)` succeed silently — a malformed payload
        # would otherwise build an `Area` whose polygon raises a raw shapely
        # `GEOSException` from `_area_to_polygon`, breaking the FR24 exit-2
        # contract. Reject these defensively here so the caller rebuilds.
        if (
            not isinstance(center, list)
            or len(center) != 2
            or not isinstance(radius_km, (int, float))
            or isinstance(radius_km, bool)
            or not math.isfinite(radius_km)
            or radius_km <= 0
        ):
            return None
        try:
            lat_raw, lon_raw = float(center[0]), float(center[1])
        except (TypeError, ValueError):
            return None
        # Same finiteness guard on the center coordinates — NaN/Infinity in
        # `center` would also pollute `_area_to_polygon` downstream.
        if not (math.isfinite(lat_raw) and math.isfinite(lon_raw)):
            return None
        try:
            area = Area(center=(lat_raw, lon_raw), radius_km=float(radius_km))
        except (TypeError, ValueError):
            return None
        parsed.append(_IndexedEntry(cache_key_hash=cache_key_hash, area=area))
    return parsed


def _is_entry_dir(path: pathlib.Path) -> bool:
    """True iff `path` looks like a real cache entry directory (not a staging artifact).

    Filters out `<hash>.tmp/` (in-flight writes) and `<hash>.old/` (rollback
    shuffle from Story 2.7's atomic-write pattern). A Ctrl-C in the narrow
    window between `os.replace(entry_dir, backup_dir)` and the manifest
    commit can leave a `<hash>.old/` with the previous entry's full manifest
    on disk — without this suffix filter both `_areas_has_valid_entries` and
    `rebuild_index` would admit it as a live entry. The manifest-presence
    check still applies on top of this; both guards together ensure only
    fully-committed entries enter the rebuilt index.
    """
    if not path.is_dir():
        return False
    return path.suffix not in {_TMP_DIR_SUFFIX, _OLD_DIR_SUFFIX}


def _areas_has_valid_entries(cache_root: pathlib.Path) -> bool:
    """Cheap filesystem probe: does `areas/` contain at least one committed entry?

    Used to detect the "interrupted write" case where a `write_entry` landed
    the entry's `manifest.json` but didn't reach `rebuild_index` before
    Ctrl-C, leaving the index out-of-date (Story 2.7 D1). `.tmp/` and `.old/`
    directories are skipped via `_is_entry_dir` — they're not committed
    entries even when a stale manifest happens to live inside them. We don't
    validate manifest contents here; `rebuild_index` does that next, and an
    invalid one is skipped from the rebuilt index.
    """
    areas_dir = _areas_dir(cache_root)
    if not areas_dir.is_dir():
        return False
    for entry_dir in areas_dir.iterdir():
        if _is_entry_dir(entry_dir) and (entry_dir / _MANIFEST_FILENAME).is_file():
            return True
    return False


def _select_smallest_containing(
    query_area: Area,
    indexed: list[_IndexedEntry],
) -> _IndexedEntry | None:
    """Return the smallest-radius indexed entry whose bbox contains `query_area`, or None.

    "Contains" per `shapely.Polygon.contains` (DE-9IM `[T*****FF*]`): no point
    of the query polygon lies outside the entry polygon. Identical bboxes
    qualify (a polygon contains itself); a query bbox that pokes outside on
    any side does not. This matches the Architecture §Cat 4e "strict
    containment" rule's intent (no out-of-bounds query coverage).

    Ties on `radius_km` are broken by ascending `cache_key_hash` so the result
    is deterministic regardless of `index.json` insertion order.
    """
    query_poly = _area_to_polygon(query_area)
    containing: list[_IndexedEntry] = []
    for entry in indexed:
        entry_poly = _area_to_polygon(entry.area)
        if entry_poly.contains(query_poly):
            containing.append(entry)
    if not containing:
        return None
    return min(containing, key=lambda e: (e.area.radius_km, e.cache_key_hash))


def _planar_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in km using the same flat-earth projection as `_area_to_polygon`.

    This is NOT a true great-circle (haversine) distance — it's a planar
    approximation with cos-latitude correction on the longitude axis. Sharing
    the projection with `_area_to_polygon` makes the "nearest" metric consistent
    with the containment geometry. For Grenoble-area distances (≤ ~100 km) the
    deviation from haversine is well under 1%, which is irrelevant for an
    actionable "narrow your radius" hint.

    Antimeridian crossings (lon1 ~+180, lon2 ~-180) are NOT handled — the
    `lon1 - lon2` delta produces a ~360° span. Out of scope for the Grenoble
    Alps use case; document the limitation if a polar/equatorial use case
    ever materializes.
    """
    avg_lat = (lat1 + lat2) / 2.0
    dlat_km = (lat1 - lat2) / _DEG_PER_KM_LAT
    dlon_km = (lon1 - lon2) / _deg_per_km_lon(avg_lat)
    return math.sqrt(dlat_km * dlat_km + dlon_km * dlon_km)


def _find_nearest(query_area: Area, indexed: list[_IndexedEntry]) -> _IndexedEntry:
    """Pick the entry whose center is closest to `query_area.center`.

    Ties broken by `cache_key_hash` for determinism. `indexed` is non-empty
    by caller contract — `check_coverage` only calls this on the partial-
    coverage branch after the empty-cache case is handled.
    """
    q_lat, q_lon = query_area.center

    def _distance(entry: _IndexedEntry) -> tuple[float, str]:
        e_lat, e_lon = entry.area.center
        return (_planar_distance_km(q_lat, q_lon, e_lat, e_lon), entry.cache_key_hash)

    return min(indexed, key=_distance)


def _format_lat_lon(lat: float, lon: float) -> str:
    """Render `(lat, lon)` for an actionable CLI command. Trims trailing zeros."""
    return f"{_format_number(lat)},{_format_number(lon)}"


def _format_number(value: float) -> str:
    """Strip trailing zeros from a float for clean copy-pasteable CLI output.

    `45.0716` stays as-is; `1.0` becomes `1`; `10.5` stays `10.5`. Click parses
    both `1` and `1.0` as the same `--radius` float so the trimmed form is
    safe to suggest in error messages.

    Signed-zero normalization: `f"{-0.0:g}"` renders as `'-0'` (Python's
    IEEE-754-respecting `:g` format), which would surface in suggested CLI
    commands as `--center -0,-0` — parseable but awkward. Coerce to positive
    zero before formatting so the displayed form is clean.
    """
    if value == 0.0:  # True for both +0.0 and -0.0; `+ 0.0` normalizes the sign.
        value = value + 0.0
    return f"{value:g}"


def _no_prepared_cache_message(query_area: Area) -> str:
    """AC #3: empty-cache error message echoing the query's center and radius.

    Lead phrase distinguishes the empty-cache case from `_partial_coverage_message`'s
    "No prepared cache covers this area." so users can triage at a glance.
    """
    lat, lon = query_area.center
    return (
        f"No prepared cache exists yet. "
        f"Run: steeproute-setup --center {_format_lat_lon(lat, lon)} "
        f"--radius {_format_number(query_area.radius_km)} --dem-path <your DEM>"
    )


def _partial_coverage_message(
    query_area: Area,
    nearest: _IndexedEntry,
) -> str:
    """AC #4: partial-coverage error naming the nearest prepared area.

    Suggests the largest `--radius` value that would fit strictly inside the
    nearest entry while keeping the original query center, OR — if the query
    center is itself outside the nearest entry — suggests narrowing `--center`
    rather than emitting a non-positive radius. Both branches provide a fully
    copy-pasteable `steeproute-setup` command for the "widen the prepared area
    instead" path; the smaller-radius branch additionally suggests a narrowed
    `steeproute` re-invocation.
    """
    q_lat, q_lon = query_area.center
    e_lat, e_lon = nearest.area.center
    dlat_km = abs(q_lat - e_lat) / _DEG_PER_KM_LAT
    dlon_km = abs(q_lon - e_lon) / _deg_per_km_lon((q_lat + e_lat) / 2.0)
    # Largest query radius keeping query bbox strictly inside the entry bbox at
    # the same query center: r_new = min(entry.r - |Δlat_km|, entry.r - |Δlon_km|).
    # If non-positive, the query center sits outside the entry — fall back to
    # the center-relocation hint.
    r_new = min(nearest.area.radius_km - dlat_km, nearest.area.radius_km - dlon_km)
    base = (
        f"No prepared cache covers this area. "
        f"Nearest prepared area: center {_format_lat_lon(e_lat, e_lon)}, "
        f"radius {_format_number(nearest.area.radius_km)} km."
    )
    # Both branches echo a fully copy-pasteable `steeproute-setup` command
    # using the user's original query center + radius so they can widen the
    # prepared area to cover their target without composing the command
    # themselves (UX parity with the empty-cache message).
    widen_setup_cmd = (
        f"steeproute-setup --center {_format_lat_lon(q_lat, q_lon)} "
        f"--radius {_format_number(query_area.radius_km)} --dem-path <your DEM>"
    )
    if r_new > 0:
        return (
            f"{base} Re-run with a smaller --radius (<= {_format_number(r_new)}) "
            f"or prepare your target area: {widen_setup_cmd}"
        )
    return (
        f"{base} Narrow --center toward {_format_lat_lon(e_lat, e_lon)} "
        f"or prepare your target area: {widen_setup_cmd}"
    )


def _diagnostic_detail(indexed: list[_IndexedEntry]) -> str:
    """Verbose `detail` line listing every prepared area for the user."""
    rows = ", ".join(
        f"{e.cache_key_hash}: center {_format_lat_lon(*e.area.center)} "
        f"radius {_format_number(e.area.radius_km)} km"
        for e in indexed
    )
    return f"Prepared areas: [{rows}]"


def check_coverage(cache_root: pathlib.Path, query_area: Area) -> PreparedData:
    """FR24 coverage check: resolve a query area against the prepared cache (Architecture §Cat 4e).

    Strategy:

    1. Read `index.json`. If missing / unparseable / schema-incompatible, OR if
       it parses as empty while `areas/` actually contains valid entries (the
       Story 2.7 D1 interrupted-write window), opportunistically `rebuild_index`
       and re-read.
    2. If the index lists zero entries, raise `CacheNotFoundError` with the
       empty-cache actionable message (AC #3).
    3. For each indexed entry, build its polygon via `_area_to_polygon` and
       test strict `shapely.contains` against the query polygon (also built
       via `_area_to_polygon` so query and entry share one geometry source).
       Among the strictly-containing entries, pick the smallest `radius_km`
       (tiebreak by `cache_key_hash` for determinism).
    4. If no entry strictly contains the query, raise `CacheNotFoundError`
       with the partial-coverage message naming the nearest prepared area
       and an actionable smaller-radius or center-relocation hint (AC #4).
    5. Otherwise, return `read_entry(cache_root, chosen.cache_key_hash)` — any
       `CacheCorruptedError` from the chosen entry's graph propagates unchanged
       (existing exit-2 contract).

    Args:
        cache_root: cache root path as returned by `resolve_cache_root` — the
            same value `write_entry` / `read_entry` accept.
        query_area: the query CLI's parsed `--center` / `--radius`.

    Raises:
        CacheNotFoundError: no entry strictly contains the query, or the cache
            is empty.
        CacheCorruptedError: the selected entry's `graph.pkl` or `manifest.json`
            is unreadable (propagated from `read_entry`).
    """
    index_path = cache_root / _CACHE_SUBDIR / _INDEX_FILENAME
    indexed = _read_indexed_entries(index_path)
    if indexed is None:
        # Missing / unparseable / schema-incompatible — rebuild and retry.
        rebuild_index(cache_root)
        rebuilt = _read_indexed_entries(index_path)
        if rebuilt is None:
            _logger.debug(
                "check_coverage: index at %s remained unreadable after rebuild; "
                "falling back to empty entry list.",
                index_path,
            )
        indexed = rebuilt or []
    elif not indexed and _areas_has_valid_entries(cache_root):
        # Empty index but on-disk entries exist (Story 2.7 D1: interrupted
        # write between manifest commit and final `rebuild_index`).
        rebuild_index(cache_root)
        rebuilt = _read_indexed_entries(index_path)
        if rebuilt is None:
            _logger.debug(
                "check_coverage: rebuild_index ran but index at %s is still unreadable; "
                "falling back to empty entry list.",
                index_path,
            )
        indexed = rebuilt or []

    if not indexed:
        raise CacheNotFoundError(
            user_message=_no_prepared_cache_message(query_area),
            detail=f"Cache root: {cache_root}",
        )

    chosen = _select_smallest_containing(query_area, indexed)
    if chosen is None:
        nearest = _find_nearest(query_area, indexed)
        raise CacheNotFoundError(
            user_message=_partial_coverage_message(query_area, nearest),
            detail=_diagnostic_detail(indexed),
        )

    return read_entry(cache_root, chosen.cache_key_hash)
