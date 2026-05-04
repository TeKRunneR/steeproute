"""Shared CLI plumbing: verbose flag state, exit-code wrapper, and reusable click option decorators."""

import pathlib
import sys
from collections.abc import Callable
from typing import NoReturn, override

import click

from steeproute.errors import PreExecutionError

_verbose: bool = False


def set_verbose(value: bool) -> None:
    """Set the verbose flag consulted by run_entry_point. Story 1.5 wires --verbose to this."""
    global _verbose
    _verbose = value


def is_verbose() -> bool:
    """Return the current verbose state. Used by tests; production reads `_verbose` directly."""
    return _verbose


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
    """Parses 'LAT,LON' strings into (lat, lon) float tuples. Range validation: Story 1.6."""

    name: str = "lat,lon"

    @override
    def convert(
        self,
        value: str | tuple[float, float],
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> tuple[float, float]:
        if isinstance(value, tuple):
            return value
        try:
            lat_str, lon_str = value.split(",")
            return (float(lat_str), float(lon_str))
        except ValueError:
            self.fail(
                f"{value!r} is not in LAT,LON format (e.g. '45.0716,6.1079')",
                param,
                ctx,
            )


LAT_LON = LatLonParamType()


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
    help="Average slope floor for eligible routes.",
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
    help="Edge-reuse length threshold in meters (short connectors vs primary edges).",
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

verbose_option = click.option(
    "--verbose",
    is_flag=True,
    default=False,
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
