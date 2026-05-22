# pyright: reportUnknownVariableType=false
# Reason: `run_setup_stages` and `write_entry` return `MultiDiGraph[Unknown]`; the
# networkx generic parameter is unspecified upstream, same external-boundary pattern
# the `pipeline/` modules use.
"""steeproute-setup data-preparation CLI: parses flags, runs stages 1-7 (or cache-hit), persists.

Wires Epic 2 pieces end-to-end (Story 2.8):

    parse flags
      → resolve cache root + derive `dem_version` from --dem-version or DEM metadata
      → compute_cache_key(area, untagged_policy, dem_version, pipeline_content_hash)
      → read_entry(cache_root, cache_key)
          - hit + not --force-refresh:    skip the pipeline, summary reports "cache-hit"
          - miss or --force-refresh:      run_setup_stages → Manifest → write_entry
      → print summary on stdout (always, even with --quiet, per Architecture §Cat 8)

The summary block emits the 16-hex `cache_key_hash`, the entry path, and the
elapsed wall-clock. `--verbose` switches the stdlib `logging` root to DEBUG on
stderr so the deferred pipeline `logger.debug(...)` and cache `logger.warning(...)`
calls (Stories 2.5/2.7 deferreds) become visible.
"""

from __future__ import annotations

import importlib.metadata
import logging
import pathlib
import time
from typing import NoReturn

import click

from steeproute.cache import (
    Manifest,
    compute_cache_key,
    compute_pipeline_content_hash,
    entry_dir_for,
    read_entry,
    resolve_cache_root,
    write_entry,
)
from steeproute.cli._shared import (
    cache_dir_option,
    center_option,
    configure_cli_logging,
    dem_path_option,
    dem_version_option,
    force_refresh_option,
    osm_age_warn_days_option,
    quiet_option,
    radius_option,
    run_entry_point,
    untagged_trails_option,
    validate_setup_radius,
    verbose_option,
)
from steeproute.errors import (
    BadCLIArgError,
    CacheCorruptedError,
    CacheNotFoundError,
)
from steeproute.models import Area, PipelineConfig
from steeproute.pipeline import run_setup_stages
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
@dem_path_option
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
    dem_path: pathlib.Path | None,
    osm_age_warn_days: int,
) -> int:
    # `--osm-age-warn-days` is parsed here so it appears in --help and gets validated,
    # but the cache-hit age-check itself lands in Story 2.9.
    _ = osm_age_warn_days

    configure_cli_logging(verbose=verbose)

    if dem_path is None:
        raise BadCLIArgError(
            "--dem-path is required for steeproute-setup.",
            detail="Provide a path to a local DEM GeoTIFF, e.g. --dem-path /data/grenoble.tif",
        )
    # Numeric radius check first (pure arithmetic, no I/O) so a typo like
    # `--radius 5000` is rejected with the most relevant error even if other
    # arguments would also fail.
    validate_setup_radius(radius)
    # File-existence check at the CLI boundary (the orchestrator repeats it, but
    # the CLI does it earlier so `_derive_dem_version` can `stat()` safely).
    if not dem_path.is_file():
        raise BadCLIArgError(
            f"--dem-path {dem_path} does not exist or is not a regular file.",
            detail="Provide a path to a local DEM GeoTIFF readable by rasterio.",
        )

    area = Area(center=center, radius_km=radius)
    config = PipelineConfig(untagged_policy=untagged_trails, dem_path=dem_path)
    cache_root = resolve_cache_root(cache_dir)

    resolved_dem_version = dem_version if dem_version is not None else _derive_dem_version(dem_path)
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
            read_entry(cache_root, cache_key)
            cache_hit = True
            entry_dir = entry_dir_for(cache_root, cache_key)
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
        graph = run_setup_stages(area, config)
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
        entry_dir = write_entry(cache_root, manifest, graph)

    elapsed_s = time.perf_counter() - start
    assert entry_dir is not None  # both branches assign it; tells basedpyright
    _print_summary(
        cache_hit=cache_hit,
        cache_key=cache_key,
        entry_dir=entry_dir,
        elapsed_s=elapsed_s,
        quiet=quiet,
    )
    return 0


def _derive_dem_version(dem_path: pathlib.Path) -> str:
    """Derive a stable `dem_version` tag from DEM file metadata.

    Architecture §Cat 4b allows either `--dem-version` (user-supplied tag) or
    a derivation from file metadata. We use `<canonical-filename>-<size>-<mtime_ns>`:
    a real DEM update (different release dropped on disk) changes at least one of
    those three, so the cache key shifts; minor unrelated touches (e.g. a `chmod`)
    leave the string stable.

    `dem_path.resolve().name` canonicalizes the filename case on Windows (NTFS is
    case-insensitive — `Grenoble.TIF` and `grenoble.tif` reference the same file
    and must hash to the same `dem_version`). `stat.st_mtime_ns` preserves
    nanosecond precision so two writes within the same wall-clock second don't
    collide (a corner case mostly visible in tests doing `shutil.copyfile` in
    tight loops, but cheap to defend against).

    Hashing DEM bytes was rejected: production DEMs are multi-GB, and we'd pay
    that I/O on every `steeproute-setup` invocation just to verify the cache
    key — `--dem-version` is the user-supplied opt-in when content-identity
    matters more than the surface metadata.
    """
    stat = dem_path.stat()
    return f"{dem_path.resolve().name}-{stat.st_size}-{stat.st_mtime_ns}"


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


def _print_summary(
    *,
    cache_hit: bool,
    cache_key: str,
    entry_dir: pathlib.Path,
    elapsed_s: float,
    quiet: bool,
) -> None:
    """Emit the run summary to stdout. Always emitted — `--quiet` only suppresses progress."""
    _ = quiet  # Setup has no progress lines to suppress; the summary is the only stdout output.
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
