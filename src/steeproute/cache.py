# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx MultiDiGraph operations surface as Unknown; same external-boundary
# pattern as pipeline/__init__.py, pipeline/osm.py, pipeline/smoothing.py, etc.
"""Cache I/O: key hashing + manifest schema (Story 2.6); atomic write + read + index (Story 2.7); coverage check lands in Story 2.10.

`compute_cache_key` is the single source of truth for which inputs invalidate a cached graph
(Architecture §Cat 4b). `Manifest` is the wire schema written last as the atomic commit signal
(§Cat 4d). `write_entry` / `read_entry` / `rebuild_index` (Story 2.7) implement the `.tmp/`
→ `os.replace()` atomic pattern that guarantees a Ctrl-C mid-write cannot surface a partial
entry. The package is the sole reader/writer of the cache directory (§Boundaries — Cache
boundary), so all serialization concerns live here too. All JSON writes route through the
single `write_json_atomic` helper per Architecture §Key anti-patterns.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import pickle
import shutil
from dataclasses import dataclass
from typing import Any

import networkx as nx
import platformdirs

from steeproute.errors import CacheCorruptedError, CacheNotFoundError
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
            if not entry_dir.is_dir():
                continue
            manifest_path = entry_dir / _MANIFEST_FILENAME
            if not manifest_path.is_file():
                # `.tmp/`, `.old/`, half-written entries — skip silently.
                continue
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = Manifest.from_dict(payload)
            except (
                json.JSONDecodeError,
                CacheCorruptedError,
                UnicodeDecodeError,
                OSError,
            ):
                # A corrupt or unreadable manifest is not an index-rebuild
                # concern — one bad entry must not block the rebuild for all
                # the others. The next `read_entry` against the bad key will
                # surface the error with full context.
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
    `shapely.contains` check builds an equivalent polygon at query time.
    """
    lat, lon = area.center
    # `radius_km` is bbox half-side per `models.Area`. Architecture leaves the
    # km→deg conversion implementation-defined for v1; we use a simple WGS84
    # equator-approximation (1° lat ≈ 111 km, 1° lon ≈ 111 km × cos(lat)) which
    # is good enough at Grenoble's ~45° N for the diagnostic / debug-viz role
    # `bounds.geojson` plays. Coverage math (Story 2.10) recomputes from the
    # canonical Area + radius_km, not from bounds.geojson, so a small
    # projection-skew here doesn't propagate.
    deg_per_km_lat = 1.0 / 111.0
    deg_per_km_lon = 1.0 / (111.0 * math.cos(math.radians(lat)) or 1.0)
    dlat = area.radius_km * deg_per_km_lat
    dlon = area.radius_km * deg_per_km_lon
    ring = [
        [lon - dlon, lat - dlat],
        [lon + dlon, lat - dlat],
        [lon + dlon, lat + dlat],
        [lon - dlon, lat + dlat],
        [lon - dlon, lat - dlat],
    ]
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
