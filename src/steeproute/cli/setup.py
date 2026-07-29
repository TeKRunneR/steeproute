# pyright: reportUnknownVariableType=false
# Reason: `run_setup_stages` and `write_entry` return `MultiDiGraph[Unknown]`; the
# networkx generic parameter is unspecified upstream, same external-boundary pattern
# the `pipeline/` modules use.
"""steeproute-setup data-preparation CLI: parses flags, runs stages 1-5 (or cache-hit), persists.

The whole flow:

    parse flags
      → resolve cache root + point osmnx's HTTP cache under it
        + resolve `dem_version` (--dem-version or the default IGN-layer tag)
      → compute_cache_key(area, untagged_policy, dem_version, pipeline_content_hash)
      → read_entry(cache_root, cache_key)
          - hit + not --force-refresh:    skip the pipeline, summary reports "cache-hit"
          - miss or --force-refresh:      build_graph_geometry → resolve_dem
                                          (auto-download + cache) → attach_elevation
                                          → Manifest → write_entry
      → print summary on stdout (always, even with --quiet, per Architecture §Cat 8)

Every cache-miss stage runs inside the `StageProgress` seam (FR33): stage-start /
stage-elapsed lines on stdout (suppressed by `--quiet`), `tile i/N` within the DEM
fetch, and a machine-readable per-stage `timings` dict for profiling attribution.

The DEM raster is fetched automatically for the area from the IGN Géoplateforme
WMS (`pipeline.dem_download.resolve_dem`) — there is no `--dem-path` flag. Only
the cache-miss branch downloads; a cache hit touches neither OSM nor the DEM.

The summary block emits the 16-hex `cache_key_hash`, the entry path, and the
elapsed wall-clock. `--verbose` switches the stdlib `logging` root to DEBUG on
stderr so the pipeline's `logger.debug(...)` and the cache's
`logger.warning(...)` calls become visible — as does osmnx's own INFO chatter
(`_configure_osmnx_logging`).

Independently of `--verbose`, a cache-miss run splits the `osm-load` stage into its
fetch and graph-build halves as within-stage lines (`_OsmnxFetchReporter`), because
neither "did this actually download anything?" nor "how much of this was CPU?" is
recoverable from a single stage total dominated by graph-build work.
"""

from __future__ import annotations

import datetime
import importlib.metadata
import logging
import pathlib
import time
from collections.abc import Callable
from typing import NoReturn, final, override

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
    angle_option,
    cache_dir_option,
    center_option,
    configure_cli_logging,
    dem_fetch_workers_option,
    dem_version_option,
    emit_osm_age_warning,
    force_refresh_option,
    height_option,
    osm_age_warn_days_option,
    quiet_option,
    radius_option,
    resolve_area,
    run_entry_point,
    untagged_trails_option,
    validate_dem_fetch_workers,
    verbose_option,
    width_option,
)
from steeproute.errors import (
    CacheCorruptedError,
    CacheNotFoundError,
)
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
@width_option
@height_option
@angle_option
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
    radius: float | None,
    width: float | None,
    height: float | None,
    angle: float,
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

    # Area resolution + numeric checks first (pure arithmetic, no I/O) so a
    # malformed area — no size, both spellings, a `--width` with no `--height`,
    # a non-finite/non-positive dimension — is rejected before any cache or
    # network work. There is deliberately **no upper size ceiling** on either CLI:
    # a large `--radius`/`--width`/`--height` is slow, not invalid, and the user
    # who asks for it gets it.
    area = resolve_area(
        center=center, radius_km=radius, width_km=width, height_km=height, angle_deg=angle
    )
    validate_dem_fetch_workers(dem_fetch_workers)

    cache_root = resolve_cache_root(cache_dir)
    _configure_osmnx_cache(cache_root)
    _configure_osmnx_logging(verbose=verbose)

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
            # lives in `cli/_shared.py` so `cli/query.py` shares the same
            # boundary semantics.
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
        # Stage-timing seam (FR33): every stage announces itself and reports
        # elapsed time on stdout; `--quiet` installs no sink so the seam only
        # times. `progress.timings` keeps the machine-readable per-stage breakdown
        # for profiling attribution.
        progress = StageProgress(on_line=None if quiet else print)
        # Report the Overpass fetch outcome inside the `osm-load` stage window —
        # installed here rather than in the pipeline so the stage name stays the
        # only `pipeline/**` edit (that package's bytes key the cache).
        _install_osmnx_fetch_reporter(progress)
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
            # `consume=True`: this graph is ours — built by
            # `build_graph_geometry`/`attach_elevation` above — and nothing reads it
            # after this call (the summary below prints only cache metadata), so
            # let the payload build pop `geometry` off it instead of copying the
            # whole graph first (~5.4 s of the r20 cache-write stage, 2026-07-24).
            entry_dir = write_entry(cache_root, manifest, graph, consume=True)

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

    osmnx 2.x ships `settings.use_cache = True` but
    `settings.cache_folder = "./cache"` — CWD-relative, so responses land in stray
    `cache/` folders wherever setup happens to run. Rooting it under
    `resolve_cache_root(...)` makes the cache genuinely persistent and
    `--cache-dir`-aware. `use_cache` is (re)asserted on rather than trusted, so
    a future osmnx default flip can't silently disable it.
    """
    osmnx.settings.use_cache = True
    osmnx.settings.cache_folder = str(osmnx_cache_dir_for(cache_root))


def _configure_osmnx_logging(*, verbose: bool) -> None:
    """Route osmnx's own log records into the stdlib `logging` tree.

    osmnx never logs through `logging` by default: `utils.log` has two sinks, both
    off. `settings.log_console` prints via `print(..., file=sys.__stdout__)` —
    unusable here, it bypasses redirection and collides with the run summary that
    owns stdout (Architecture §Cat 8). `settings.log_file` *does* route into a real
    `logging.Logger` named `settings.log_name`, but osmnx's `_get_logger` bolts a
    `FileHandler` onto it and creates a `logs/` folder — and it does so *only* when
    that logger has no handlers yet. Pre-attaching a `NullHandler` satisfies that
    check, so we get the records with none of the file side-effects.

    The logger is pinned at INFO rather than left to inherit the root level,
    because `_OsmnxFetchReporter` needs osmnx's INFO records even on a default
    (non-`--verbose`) run — a logger drops sub-level records before any handler
    sees them. `propagate` is what gates the raw text instead: only `--verbose`
    lets osmnx's ~30 INFO lines per run reach the root's stderr handler.

    Trade-off of `propagate=False`: osmnx's own WARNING/ERROR records are also
    stderr-invisible without `--verbose`. They were invisible on *every* run until
    this plumbing existed, and the failure modes that matter (Overpass unreachable,
    HTTP error) surface as steeproute exceptions anyway — so this loses nothing and
    keeps a default run's stderr clean.
    """
    osmnx_logger = logging.getLogger(osmnx.settings.log_name)
    if not osmnx_logger.handlers:  # idempotent across repeated CliRunner invocations
        osmnx_logger.addHandler(logging.NullHandler())
    osmnx_logger.setLevel(logging.INFO)
    osmnx_logger.propagate = verbose
    osmnx.settings.log_file = True


# osmnx log messages, matched as substrings of the rendered record.
# `_http._retrieve_from_cache` logs the cache hit and `_http._parse_response` the
# download, one or the other per response inside `_create_graph`'s response loop;
# `_create_graph` logs the "all data" line immediately after that loop, which is
# therefore where the fetch ends; `graph_from_polygon` logs the "returned graph"
# line as the last thing it does, which is where assembly ends. None is a public
# API, so `test_cli_setup.py` drives osmnx's real cache-read and response-loop
# paths to catch the day an upgrade reworks the wording.
_OSMNX_CACHE_HIT_MARKER = "Retrieved response from cache file"
_OSMNX_DOWNLOAD_MARKER = "Downloaded "
_OSMNX_FETCH_DONE_MARKER = "Retrieved all data from API in "
_OSMNX_ASSEMBLED_MARKER = " returned graph with "


@final
class _OsmnxFetchReporter(logging.Handler):
    """Split the `osm-load` stage into its fetch and graph-build halves on stdout.

    The stage covers a (possibly cached) Overpass fetch *plus* osmnx's graph
    build, and on a warm run the fetch is a no-op while the stage still reports
    minutes — so one opaque total actively misleads. Both halves are recovered
    from osmnx's own log records: the per-response outcome marks the end of the
    fetch, and `graph_from_polygon`'s closing line marks the end of assembly.

    Reading the boundary out of the log stream rather than driving osmnx's request
    API is what keeps this out of `pipeline/**` entirely — no lower-level access,
    no cache re-key. The cost is a documented coupling to osmnx's wording, which
    this handler already carried for the outcome line.

    Emitted through `StageProgress.line`, so lines land indented inside the open
    stage (the `  tile i/N` shape the App already tolerates) and disappear under
    `--quiet`. One line per half, and the fetch half is timed to the **last**
    response, not the first: an area large enough for osmnx to subdivide its
    query produces one outcome record per response, and stopping the fetch clock
    at any but the last would charge the remaining responses to the build half.
    Recording every response but printing once is what keeps those two apart.

    The boundary sits at the last response's outcome record, which osmnx logs
    before parsing that response's JSON — so the fetch half is pure
    network-or-cache-read time and each response's parse falls in the build half.
    """

    def __init__(self, progress: StageProgress, *, clock: Callable[[], float] = time.perf_counter):
        super().__init__(level=logging.INFO)
        self._progress = progress
        self._clock = clock
        # Installed immediately before the stage opens, so this is the stage start
        # to within the few ms of argument validation that precede the fetch.
        self._armed_at = clock()
        self._fetch_ended_at: float | None = None
        self._responses = 0
        self._downloads = 0
        self._last_outcome = ""
        self._fetch_reported = False
        self._build_reported = False

    @override
    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if _OSMNX_CACHE_HIT_MARKER in message:
            self._record_response("served from cache, no download")
        elif message.startswith(_OSMNX_DOWNLOAD_MARKER):
            self._downloads += 1
            self._record_response(message)
        elif message.startswith(_OSMNX_FETCH_DONE_MARKER):
            self._report_fetch()
        elif _OSMNX_ASSEMBLED_MARKER in message:
            self._report_build(message)

    def _record_response(self, outcome: str) -> None:
        self._responses += 1
        self._last_outcome = outcome
        self._fetch_ended_at = self._clock()

    def _report_fetch(self) -> None:
        # No response record means no boundary was seen, so there is nothing to
        # time — an unrecognized outcome wording must not become a fetch half of
        # zero, which would read as a cache hit.
        if self._fetch_reported or self._fetch_ended_at is None:
            return
        self._fetch_reported = True
        # A single response can report its own outcome verbatim (osmnx's size and
        # host are the useful detail); across several, that one response's size
        # would misrepresent the whole fetch, so report the shape instead.
        detail = (
            self._last_outcome
            if self._responses == 1
            else f"{self._responses} responses, {self._downloads} downloaded"
        )
        # ASCII only: stdout is cp1252 on Windows, and a character the codepage
        # can't encode raises UnicodeEncodeError mid-progress-line.
        self._progress.line(
            f"osm: Overpass fetch {self._fetch_ended_at - self._armed_at:.2f} s ({detail})"
        )

    def _report_build(self, message: str) -> None:
        # Without a fetch boundary there is no split to report, only a number that
        # would look like one. The stage's own total still covers the whole thing.
        if self._fetch_ended_at is None or self._build_reported:
            return
        # Fallback emission: a reworded fetch-done marker would otherwise drop the
        # fetch half while the build half — which needs only the response records —
        # still printed, leaving a build time with nothing to be half of.
        self._report_fetch()
        self._build_reported = True
        _, _, shape = message.partition(_OSMNX_ASSEMBLED_MARKER)
        self._progress.line(
            f"osm: graph build {self._clock() - self._fetch_ended_at:.2f} s ({shape})"
        )


def _install_osmnx_fetch_reporter(
    progress: StageProgress, *, clock: Callable[[], float] = time.perf_counter
) -> None:
    """Attach a fresh `_OsmnxFetchReporter` for this run's progress seam.

    Any reporter from an earlier run in the same process (repeated `CliRunner`
    invocations in one test) is dropped first, so lines never go to a stale sink.
    Call this immediately before the stage opens — the reporter times the fetch
    half from its own construction. `clock` is injectable for deterministic tests,
    mirroring `StageProgress` and `throttle`.
    """
    osmnx_logger = logging.getLogger(osmnx.settings.log_name)
    for handler in [h for h in osmnx_logger.handlers if isinstance(h, _OsmnxFetchReporter)]:
        osmnx_logger.removeHandler(handler)
    osmnx_logger.addHandler(_OsmnxFetchReporter(progress, clock=clock))


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
