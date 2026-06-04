"""Shared CLI plumbing: verbose flag state, exit-code wrapper, reusable click option decorators, OSM-age warning helper."""

import datetime
import logging
import math
import pathlib
import sys
from collections.abc import Callable
from typing import NoReturn, override

import click

from steeproute.cache import Manifest
from steeproute.errors import BadCLIArgError, PreExecutionError

_verbose: bool = False


def set_verbose(value: bool) -> None:
    """Set the verbose flag consulted by run_entry_point. Story 1.5 wires --verbose to this."""
    global _verbose
    _verbose = value


def is_verbose() -> bool:
    """Return the current verbose state. Used by tests; production reads `_verbose` directly."""
    return _verbose


def configure_cli_logging(*, verbose: bool) -> None:
    """Route stdlib `logging` output to stderr at DEBUG (--verbose) or WARNING otherwise.

    Architecture §Cat 8 splits the two streams: progress + run summary go to stdout
    via plain `print`; `logging` is reserved for diagnostics and warnings on stderr.
    `force=True` makes the call idempotent across repeated CliRunner invocations
    inside a single test process.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s: %(name)s: %(message)s",
        force=True,
    )


def run_entry_point(main_fn: Callable[[], int]) -> NoReturn:
    """Run main_fn with shared exit-code policy (0/1/2/130) and stderr error formatting."""
    try:
        code = main_fn()
    except PreExecutionError as e:
        sys.stderr.write(f"error: {e.user_message}\n")
        if _verbose and e.detail is not None:
            sys.stderr.write(f"        {e.detail}\n")
        code = 2
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


# --- Custom param types ---


class LatLonParamType(click.ParamType):
    """Parses 'LAT,LON' strings into (lat, lon) float tuples; rejects out-of-range values.

    On any failure (syntactic or range), raises BadCLIArgError so run_entry_point
    formats the error as `error: {user_message}` and exits 2 (vs click's multi-line
    Usage/Error formatting). Range envelope is inclusive at the boundary:
    lat in [-90, 90], lon in [-180, 180].
    """

    name: str = "lat,lon"

    @override
    def convert(
        self,
        value: str | tuple[float, float],
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> tuple[float, float]:
        if isinstance(value, tuple):
            lat, lon = value
        else:
            try:
                lat_str, lon_str = value.split(",")
                lat, lon = float(lat_str), float(lon_str)
            except ValueError as e:
                raise BadCLIArgError(
                    f"--center {value!r} is not in LAT,LON format",
                    detail="Expected '<latitude>,<longitude>' as decimal degrees, "
                    "e.g. '45.0716,6.1079'.",
                ) from e
        if not -90.0 <= lat <= 90.0:
            raise BadCLIArgError(
                f"--center latitude {lat} is outside [-90, 90]",
            )
        if not -180.0 <= lon <= 180.0:
            raise BadCLIArgError(
                f"--center longitude {lon} is outside [-180, 180]",
            )
        return (lat, lon)


LAT_LON = LatLonParamType()


def validate_area_size(radius_km: float, area_cap_km2: float) -> None:
    """Enforce FR2: reject radii whose disk area exceeds --area-cap.

    Raises BadCLIArgError with a user-facing message in the format:
        --radius {r} produces ~{area} km², exceeds --area-cap of {cap} km²

    Used by cli/query.py only; cli/setup.py has no --area-cap flag (per Architecture
    §FR mapping; setup is "prepare what you'll later query", cap enforcement is
    sufficient at query time).
    """
    area_km2 = math.pi * radius_km * radius_km
    if area_km2 > area_cap_km2:
        raise BadCLIArgError(
            f"--radius {radius_km:g} produces ~{area_km2:.0f} km², "
            f"exceeds --area-cap of {area_cap_km2:g} km²",
        )


# Setup-side hard ceiling on --radius (km), routed in via deferred-work D8 from
# Story 2.1. A 2*r bbox at r=50 km still spans 10_000 km^2 — far above the
# Grenoble Alps personal-tool use case but small enough to catch obvious typos
# (e.g. `--radius 5000`) that would otherwise hand osmnx an Overpass query that
# either times out or exceeds the 1 GB response cap. The query CLI has its own
# `--area-cap`-driven ceiling (`validate_area_size`); setup deliberately has no
# `--area-cap` flag so this constant carries the safety net here.
_SETUP_MAX_RADIUS_KM: float = 50.0


def validate_setup_radius(radius_km: float) -> None:
    """Setup-side --radius sanity ceiling. Rejects non-finite and non-positive values.

    Click parses `"nan"` and `"inf"` as legitimate floats; both slip past naive
    `r <= 0` / `r > max` comparisons (`nan` compares False against everything,
    `inf` only passes the upper bound). The explicit `math.isfinite` check at
    the top closes both. Per Architecture §Cat 10, CLI-tier validation surfaces
    as `BadCLIArgError → exit 2`, not a raw IEEE-754-induced traceback.
    """
    if not math.isfinite(radius_km):
        raise BadCLIArgError(
            f"--radius {radius_km!r} must be a finite number.",
        )
    if radius_km <= 0.0:
        raise BadCLIArgError(
            f"--radius {radius_km:g} must be positive.",
        )
    if radius_km > _SETUP_MAX_RADIUS_KM:
        raise BadCLIArgError(
            f"--radius {radius_km:g} km exceeds the steeproute-setup ceiling of "
            f"{_SETUP_MAX_RADIUS_KM:g} km.",
            detail=(
                "Setup fetches the full bounding box from Overpass; very large "
                "radii hit the Overpass timeout / 1 GB response cap. Split the "
                "area into smaller prepared regions instead."
            ),
        )


def validate_solver_options(
    *,
    theta: float,
    min_climb_slope: float,
    l_connector: float,
    min_climb_ground_length: float,
    j_max: float,
    n: int,
    iter_budget: int | None,
) -> None:
    """Query-side solver-parameter sanity checks at the CLI boundary (§Cat 10 → exit 2).

    The query CLI feeds these flags into `SolverParams`, `GraspSolver`, and
    `TopNTracker`, all of which raise a bare `ValueError` on out-of-range input
    (`iter_budget < 1`, `n < 1`, `j_max ∉ [0, 1]`). A `ValueError` is not a
    `PreExecutionError`, so without this guard it escapes `run_entry_point` as a
    raw traceback (exit 1) instead of the documented `BadCLIArgError → exit 2`.
    Non-finite floats are caught first for the same reason `validate_setup_radius`
    does: `click.FLOAT` parses `"nan"`/`"inf"`, and `nan` then slips past every
    downstream comparison (IEEE-754), silently yielding zero/garbage climbs.

    Checks are fail-fast (first violation wins) and ordered finiteness-then-range
    so a `nan` is reported as non-finite rather than as a confusing range message.
    """
    for name, value in (
        ("--theta", theta),
        ("--min-climb-slope", min_climb_slope),
        ("--l-connector", l_connector),
        ("--min-climb-ground-length", min_climb_ground_length),
        ("--j-max", j_max),
    ):
        if not math.isfinite(value):
            raise BadCLIArgError(f"{name} {value!r} must be a finite number.")
    if theta < 0.0:
        raise BadCLIArgError(f"--theta {theta:g} must be >= 0.")
    if min_climb_slope < 0.0:
        raise BadCLIArgError(f"--min-climb-slope {min_climb_slope:g} must be >= 0.")
    if l_connector < 0.0:
        raise BadCLIArgError(f"--l-connector {l_connector:g} must be >= 0.")
    if min_climb_ground_length <= 0.0:
        raise BadCLIArgError(
            f"--min-climb-ground-length {min_climb_ground_length:g} must be positive."
        )
    if not 0.0 <= j_max <= 1.0:
        raise BadCLIArgError(f"--j-max {j_max:g} must be in [0, 1].")
    if n < 1:
        raise BadCLIArgError(f"--n {n} must be >= 1.")
    if iter_budget is not None and iter_budget < 1:
        raise BadCLIArgError(f"--iter-budget {iter_budget} must be >= 1.")


def ensure_output_dir(output_dir: pathlib.Path) -> None:
    """Create `--output-dir` now so an unusable path fails as exit 2, not a traceback.

    `click.Path(file_okay=False)` already rejects an `--output-dir` that *is* an
    existing file. This catches the residual the renderer would otherwise hit at
    its own `mkdir`: a parent component that is a file (`NotADirectoryError`) or a
    location that can't be created (`PermissionError`, etc.) — both `OSError`
    subclasses — mapping them to `BadCLIArgError → exit 2` per §Cat 10. Creating
    the directory eagerly also fails fast, before the (potentially long) solve.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BadCLIArgError(
            f"--output-dir {output_dir} could not be created: {exc.strerror or exc}.",
            detail="Provide a path to a writable directory.",
        ) from exc


# --- Area ---

center_option = click.option(
    "--center",
    type=LAT_LON,
    required=True,
    help="Search-area center as 'LAT,LON' decimal degrees (e.g. '45.0716,6.1079').",
)

radius_option = click.option(
    "--radius",
    type=click.FLOAT,
    required=True,
    help="Search-area radius in kilometers from --center.",
)

# --- Constraints ---

theta_option = click.option(
    "--theta",
    type=click.FLOAT,
    default=0.20,
    show_default=True,
    help="Route-level average-slope floor, (D+ + D-)/length.",
)

min_climb_slope_option = click.option(
    "--min-climb-slope",
    type=click.FLOAT,
    default=0.20,
    show_default=True,
    help="Min running-average uphill slope (d_plus/length) for a segment to count as a climb.",
)

difficulty_cap_option = click.option(
    "--difficulty-cap",
    type=click.Choice(["T1", "T2", "T3", "T4", "T5", "T6"], case_sensitive=False),
    default="T3",
    show_default=True,
    help="SAC difficulty ceiling for eligible route segments.",
)

l_connector_option = click.option(
    "--l-connector",
    type=click.FLOAT,
    default=200.0,
    show_default=True,
    help=(
        "Short-connector reuse-exemption threshold in meters: connectors shorter "
        "than this may be reused and traversed in both directions; every other "
        "segment may be used at most once per route, regardless of direction."
    ),
)

min_climb_ground_length_option = click.option(
    "--min-climb-ground-length",
    type=click.FLOAT,
    default=300.0,
    show_default=True,
    help="Minimum 2D arc length in meters for a segment to count as a climb.",
)

j_max_option = click.option(
    "--j-max",
    type=click.FLOAT,
    default=0.30,
    show_default=True,
    help="Top-N pairwise Jaccard ceiling (segment-overlap distinctness).",
)

n_option = click.option(
    "--n",
    type=click.INT,
    default=5,
    show_default=True,
    help="Target result count (max number of distinct routes returned).",
)

area_cap_option = click.option(
    "--area-cap",
    type=click.FLOAT,
    default=500.0,
    show_default=True,
    help="Hard area-size cap in km^2 (rejection threshold).",
)

untagged_trails_option = click.option(
    "--untagged-trails",
    type=click.Choice(["include", "exclude"], case_sensitive=False),
    default="include",
    show_default=True,
    help="Policy for OSM trails without sac_scale.",
)

# --- Solver ---

seed_option = click.option(
    "--seed",
    type=click.INT,
    default=None,
    help="Random seed for GRASP (default: unseeded).",
)

iter_budget_option = click.option(
    "--iter-budget",
    type=click.INT,
    default=None,
    help="Maximum GRASP iterations (default: unlimited until time/stagnation budget hits).",
)

time_budget_option = click.option(
    "--time-budget",
    type=click.FLOAT,
    default=600.0,
    show_default=True,
    help="Wall-clock budget in seconds (soft).",
)

stagnation_iters_option = click.option(
    "--stagnation-iters",
    type=click.INT,
    default=None,
    help="Early-termination window: iterations without top-N improvement (default: TBD).",
)

progress_interval_option = click.option(
    "--progress-interval",
    type=click.FLOAT,
    default=None,
    help="Seconds between progress prints (default: TBD).",
)

# --- Output ---

output_dir_option = click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=pathlib.Path),
    default=pathlib.Path("./results"),
    show_default=True,
    help="Output directory for HTML + JSON reports.",
)

# --- Shared meta ---


def _verbose_callback(
    ctx: click.Context,
    param: click.Parameter,
    value: bool,
) -> bool:
    """Eager callback: flip _verbose state during click's first parse pass.

    Eager processing runs before any non-eager option's ParamType.convert, so a
    BadCLIArgError raised from LatLonParamType.convert still reaches run_entry_point
    with verbose state already set — meaning the optional `detail` line is rendered.
    """
    _ = (ctx, param)
    if value:
        set_verbose(True)
    return value


verbose_option = click.option(
    "--verbose",
    is_flag=True,
    default=False,
    is_eager=True,
    callback=_verbose_callback,
    help="Increase log verbosity (also prints PreExecutionError detail lines).",
)

quiet_option = click.option(
    "--quiet",
    is_flag=True,
    default=False,
    help="Suppress progress output (final summary and errors still emitted).",
)

cache_dir_option = click.option(
    "--cache-dir",
    type=click.Path(file_okay=False, path_type=pathlib.Path),
    default=None,
    help="Override the cache root directory (default: platform user-cache directory).",
)

# --- Setup-specific ---

force_refresh_option = click.option(
    "--force-refresh",
    is_flag=True,
    default=False,
    help="Rebuild the cache entry for this area regardless of key match.",
)

dem_version_option = click.option(
    "--dem-version",
    type=click.STRING,
    default=None,
    help="Explicit DEM version tag for cache keying (default: derived from DEM file metadata).",
)

dem_path_option = click.option(
    "--dem-path",
    type=click.Path(exists=False, path_type=pathlib.Path),
    default=None,
    help="Location of DEM files for steeproute-setup.",
)

osm_age_warn_days_option = click.option(
    "--osm-age-warn-days",
    type=click.INT,
    default=90,
    show_default=True,
    help="OSM-extract-age warning threshold in days.",
)


# --- OSM-age warning (Architecture §Cat 4f) ---------------------------------

# Shared "what to re-run" suggestion. Both CLIs land on cache-hit paths
# (`steeproute-setup` re-prepares; `steeproute` queries an existing entry);
# in both cases the action that refreshes OSM is `steeproute-setup --force-refresh`.
# Keeping the message identical across CLIs means users see one consistent
# instruction regardless of which entry-point surfaced the stale data.
_OSM_AGE_WARNING_TEMPLATE: str = (
    "OSM extract for this cache entry is %d days old (threshold: %d days). "
    "Re-run `steeproute-setup --force-refresh` for this area to fetch the latest OSM data."
)


_osm_age_logger = logging.getLogger(__name__)


def emit_osm_age_warning(
    *,
    manifest: Manifest,
    threshold_days: int,
    now: datetime.datetime,
) -> None:
    """Emit a `logging.warning(...)` if the manifest's OSM extract is stale (Architecture §Cat 4f).

    Lifted from `cli/setup.py` (Story 2.9) to `cli/_shared.py` (Story 2.10) so
    `cli/query.py`'s cache-hit path can reuse the same boundary semantics
    without re-implementing them. The warning is non-blocking: callers proceed
    normally after this returns.

    **Boundary semantics are strict:** the helper fires iff `age_days > threshold_days`.
    Equality does NOT warn — an entry whose `osm_extract_date` is exactly `threshold_days`
    ago is treated as "fresh, at the boundary." Any age exceeding the threshold by any
    margin (e.g., 90.5 days at the default threshold) triggers the warning. The rendered
    age in the warning message is `math.ceil(age_days)` so the displayed number always
    reflects "this entry has crossed the threshold" — a 90.5-day entry renders as 91,
    not 90 (which would mislead under `%.0f`'s round-half-to-even).

    A malformed `osm_extract_date` or any other unexpected exception is swallowed via
    `_logger.debug` rather than crashing the cache-hit path: we already have the user's
    graph, the age-warning is auxiliary diagnostic information. (`Manifest.from_dict`
    would have already raised `CacheCorruptedError` on schema violations, so reaching the
    swallow branch in production requires hand-edited or schema-drifted manifests.)

    Args:
        manifest: the cache-entry metadata just read by `read_entry` (setup-side) or
            `check_coverage` (query-side).
        threshold_days: the `--osm-age-warn-days` CLI value (default 90).
        now: current UTC datetime; injected so tests can drive deterministic ages
            without monkey-patching `datetime.datetime.now`.
    """
    try:
        extract_dt = datetime.datetime.fromisoformat(manifest.osm_extract_date)
    except Exception as exc:
        # Defense-in-depth: any failure parsing the manifest's date (malformed string,
        # future schema where the field becomes non-string, OverflowError on an absurdly
        # distant date) must not crash the cache-hit path. The user's graph is already
        # loaded successfully; the age warning is auxiliary diagnostic info. Surface the
        # swallow via `_logger.debug` so `--verbose` users can still diagnose schema drift.
        _osm_age_logger.debug("OSM-age warning skipped (could not parse osm_extract_date): %r", exc)
        return
    # `fromisoformat` produces a naive datetime when the input lacks a tz designator;
    # `provenance.iso8601_utc_now` writes a literal-Z suffix (Python 3.11+ parses Z
    # as UTC). If the manifest predates that convention or is hand-edited to naive,
    # treat it as UTC so the comparison doesn't crash on tz-mismatch.
    if extract_dt.tzinfo is None:
        extract_dt = extract_dt.replace(tzinfo=datetime.UTC)
    age = now - extract_dt
    age_days = age.total_seconds() / 86_400.0
    if age_days > threshold_days:
        # `math.ceil` (not `%.0f`) so a 90.5-day entry renders as 91, not 90 —
        # otherwise the displayed number would contradict the strict-`>` rule
        # the comparison above just applied. See docstring.
        _osm_age_logger.warning(
            _OSM_AGE_WARNING_TEMPLATE,
            math.ceil(age_days),
            threshold_days,
        )
