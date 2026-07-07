# pyright: reportUnknownVariableType=false
# Reason: `run_setup_stages` and `write_entry` return `MultiDiGraph[Unknown]`; the
# networkx generic parameter is unspecified upstream, same external-boundary pattern
# the `pipeline/` modules use.
"""steeproute-setup data-preparation CLI: parses flags, runs stages 1-5 (or cache-hit), persists.

Wires Epic 2 pieces end-to-end (Story 2.8):

    parse flags
      → resolve cache root + point osmnx's HTTP cache under it (Story 11.1, T2)
        + resolve `dem_version` (--dem-version or the default IGN-layer tag)
      → compute_cache_key(area, untagged_policy, dem_version, pipeline_content_hash)
      → read_entry(cache_root, cache_key)
          - hit + not --force-refresh:    skip the pipeline, summary reports "cache-hit"
          - miss or --force-refresh:      build_graph_geometry → resolve_dem
                                          (auto-download + cache) → attach_elevation
                                          → Manifest → write_entry
      → print summary on stdout (always, even with --quiet, per Architecture §Cat 8)

Every cache-miss stage runs inside the `StageProgress` seam (Story 11.1, FR33):
stage-start / stage-elapsed lines on stdout (suppressed by `--quiet`), `tile i/N`
within the DEM fetch, and a machine-readable per-stage `timings` dict for the
profiling story (11.2).

The DEM raster is fetched automatically for the area from the IGN Géoplateforme
WMS (`pipeline.dem_download.resolve_dem`) — there is no `--dem-path` flag. Only
the cache-miss branch downloads; a cache hit touches neither OSM nor the DEM.

The summary block emits the 16-hex `cache_key_hash`, the entry path, and the
elapsed wall-clock. `--verbose` switches the stdlib `logging` root to DEBUG on
stderr so the deferred pipeline `logger.debug(...)` and cache `logger.warning(...)`
calls (Stories 2.5/2.7 deferreds) become visible.
"""

from __future__ import annotations

import datetime
import importlib.metadata
import logging
import pathlib
import time
from typing import NoReturn

import click
import osmnx

from steeproute.cache import (
    Manifest,
    compute_cache_key,
    compute_pipeline_content_hash,
    entry_dir_for,
    osmnx_cache_dir_for,
    read_entry,
    resolve_cache_root,
    write_entry,
)
from steeproute.cli._shared import (
    cache_dir_option,
    center_option,
    configure_cli_logging,
    dem_fetch_workers_option,
    dem_version_option,
    emit_osm_age_warning,
    force_refresh_option,
    osm_age_warn_days_option,
    quiet_option,
    radius_option,
    run_entry_point,
    untagged_trails_option,
    validate_dem_fetch_workers,
    validate_setup_radius,
    verbose_option,
)
from steeproute.errors import (
    CacheCorruptedError,
    CacheNotFoundError,
)
from steeproute.models import Area
from steeproute.pipeline import attach_elevation, build_graph_geometry
from steeproute.pipeline.dem_download import (
    DEFAULT_DEM_VERSION,
    graph_dem_bounds,
    resolve_dem,
)
from steeproute.progress import StageProgress
from steeproute.provenance import get_commit_short, iso8601_utc_now

_logger = logging.getLogger(__name__)


@click.command(
    name="steeproute-setup",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(package_name="steeproute", prog_name="steeproute-setup")
@center_option
@radius_option
@untagged_trails_option
@verbose_option
@quiet_option
@cache_dir_option
@force_refresh_option
@dem_version_option
@dem_fetch_workers_option
@osm_age_warn_days_option
def cli(
    *,
    center: tuple[float, float],
    radius: float,
    untagged_trails: str,
    verbose: bool,
    quiet: bool,
    cache_dir: pathlib.Path | None,
    force_refresh: bool,
    dem_version: str | None,
    dem_fetch_workers: int,
    osm_age_warn_days: int,
) -> int:
    configure_cli_logging(verbose=verbose)

    # Numeric radius check first (pure arithmetic, no I/O) so a typo like
    # `--radius 5000` is rejected before any cache or network work.
    validate_setup_radius(radius)
    validate_dem_fetch_workers(dem_fetch_workers)

    area = Area(center=center, radius_km=radius)
    cache_root = resolve_cache_root(cache_dir)
    _configure_osmnx_cache(cache_root)

    # The DEM is auto-downloaded for the area on a cache miss; `dem_version` is a
    # stable IGN-layer tag (or the user's `--dem-version` override), so it's
    # available for the cache key without touching the file.
    resolved_dem_version = dem_version if dem_version is not None else DEFAULT_DEM_VERSION
    pipeline_content_hash = compute_pipeline_content_hash()
    cache_key = compute_cache_key(
        area=area,
        untagged_policy=untagged_trails,
        dem_version=resolved_dem_version,
        pipeline_content_hash=pipeline_content_hash,
    )

    start = time.perf_counter()
    cache_hit = False
    entry_dir: pathlib.Path | None = None

    if not force_refresh:
        try:
            prepared = read_entry(cache_root, cache_key)
            cache_hit = True
            entry_dir = entry_dir_for(cache_root, cache_key)
            # OSM-age warning on cache-hit (Architecture §Cat 4f). Fires before
            # the summary so a stale-cache user sees the suggestion to re-prepare
            # right next to the "cache-hit" line, not buried beneath it. Helper
            # lives in `cli/_shared.py` (Story 2.10) so `cli/query.py` shares
            # the same boundary semantics.
            emit_osm_age_warning(
                manifest=prepared.manifest,
                threshold_days=osm_age_warn_days,
                now=datetime.datetime.now(datetime.UTC),
            )
        except CacheNotFoundError:
            # Genuine miss; fall through to re-prepare.
            cache_hit = False
        except CacheCorruptedError as exc:
            # A corrupt entry under our key blocks the user from a fresh run unless
            # they manually delete the directory. Re-prepare-as-recovery matches the
            # user mental model: "run setup again to fix it". The query CLI handles
            # corruption differently (exits 2) because it has nothing to recover from.
            _logger.warning(
                "Cache entry %s is corrupted (%s); re-preparing.",
                cache_key,
                exc.user_message,
            )

    if not cache_hit:
        # Stage-timing seam (Story 11.1, FR33): every stage announces itself and
        # reports elapsed time on stdout; `--quiet` installs no sink so the seam
        # only times. `progress.timings` keeps the machine-readable per-stage
        # breakdown for profiling attribution (Story 11.2).
        progress = StageProgress(on_line=None if quiet else print)
        # Build the graph geometry first (stages 1-4, DEM-independent), then size
        # the DEM from its *actual* extent so the raster covers every vertex
        # `sample_elevation` probes. osmnx `simplify=True` can push simplified edge
        # geometry past the nominal OSM bbox by an unbounded amount near switchbacks,
        # so a fixed radius+padding ring is not safe (it failed at radius 10 km in
        # the Alps). `--force-refresh` re-fetches the raster so a forced rebuild gets
        # fresh elevation data, not a stale cached one.
        graph = build_graph_geometry(area, untagged_trails, progress=progress)
        with progress.stage("dem-resolve"):
            dem_path = resolve_dem(
                graph_dem_bounds(graph),
                cache_root,
                dem_version=resolved_dem_version,
                force_refresh=force_refresh,
                progress=progress,
                fetch_workers=dem_fetch_workers,
            )
        graph = attach_elevation(graph, dem_path, progress=progress)
        now = iso8601_utc_now()
        manifest = Manifest(
            area=area,
            untagged_policy=untagged_trails,
            dem_version=resolved_dem_version,
            pipeline_content_hash=pipeline_content_hash,
            osm_extract_date=now,
            cache_key_hash=cache_key,
            steeproute_version=_resolve_package_version(),
            steeproute_commit=get_commit_short(),
            created_at=now,
        )
        with progress.stage("cache-write"):
            entry_dir = write_entry(cache_root, manifest, graph)

    elapsed_s = time.perf_counter() - start
    assert entry_dir is not None  # both branches assign it; tells basedpyright
    _print_summary(
        cache_hit=cache_hit,
        cache_key=cache_key,
        entry_dir=entry_dir,
        elapsed_s=elapsed_s,
    )
    return 0


def _resolve_package_version() -> str:
    """Return the installed `steeproute` package version, or a sentinel if unavailable.

    `importlib.metadata.version` typically raises `PackageNotFoundError` when the
    package isn't installed, but a corrupted `.dist-info` directory (truncated
    METADATA, malformed RECORD) can surface as `OSError`, `MetadataError`, or
    other `Exception` subclasses depending on Python version. We catch broadly
    so a half-installed environment can still write a manifest with the `"unknown"`
    sentinel rather than crashing setup with an unhelpful traceback.
    """
    try:
        return importlib.metadata.version("steeproute")
    except Exception:
        return "unknown"


def _configure_osmnx_cache(cache_root: pathlib.Path) -> None:
    """Point osmnx's Overpass HTTP cache at a persistent dir under the cache root.

    Story 11.1 (T2): osmnx 2.x ships `settings.use_cache = True` but
    `settings.cache_folder = "./cache"` — CWD-relative, so responses were cached
    into stray `cache/` folders wherever setup happened to run. Rooting it under
    `resolve_cache_root(...)` makes the cache genuinely persistent and
    `--cache-dir`-aware. `use_cache` is (re)asserted on rather than trusted, so
    a future osmnx default flip can't silently disable it.
    """
    osmnx.settings.use_cache = True
    osmnx.settings.cache_folder = str(osmnx_cache_dir_for(cache_root))


def _print_summary(
    *,
    cache_hit: bool,
    cache_key: str,
    entry_dir: pathlib.Path,
    elapsed_s: float,
) -> None:
    """Emit the run summary to stdout. Always emitted — `--quiet` only suppresses the stage lines."""
    status = "cache-hit" if cache_hit else "cache-miss"
    print(f"steeproute-setup: {status}")
    print(f"  cache_key_hash: {cache_key}")
    print(f"  entry: {entry_dir}")
    print(f"  elapsed: {elapsed_s:.2f} s")


def _invoke_command() -> int:
    """Invoke the click command in standalone mode and convert its SystemExit into an int."""
    try:
        cli.main(standalone_mode=True)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0
    return 0


def main() -> NoReturn:
    run_entry_point(_invoke_command)
