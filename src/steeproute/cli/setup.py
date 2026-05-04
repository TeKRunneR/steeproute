"""steeproute-setup data-preparation CLI entry point (stages 1-7; wired in Epic 2)."""

import pathlib
from typing import NoReturn

import click

from steeproute.cli._shared import (
    cache_dir_option,
    center_option,
    dem_path_option,
    dem_version_option,
    force_refresh_option,
    osm_age_warn_days_option,
    quiet_option,
    radius_option,
    run_entry_point,
    set_verbose,
    untagged_trails_option,
    verbose_option,
)


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
    if verbose:
        set_verbose(True)

    # Stub: full flag consumption lands in Epic 2. Acknowledge all click-bound kwargs so
    # basedpyright doesn't flag them as unused.
    _ = (
        center,
        radius,
        untagged_trails,
        quiet,
        cache_dir,
        force_refresh,
        dem_version,
        dem_path,
        osm_age_warn_days,
    )

    print("steeproute-setup (data preparation CLI) - stub; full implementation lands in Epic 2")
    return 0


def _invoke_command() -> int:
    """Invoke the click command in standalone mode and convert its SystemExit into an int."""
    try:
        cli.main(standalone_mode=True)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0
    return 0


def main() -> NoReturn:
    run_entry_point(_invoke_command)
