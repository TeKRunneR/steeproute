"""steeproute query CLI entry point (stages 8-9 + solver; wired in later epics)."""

import pathlib
from typing import NoReturn

import click

from steeproute.cli._shared import (
    area_cap_option,
    cache_dir_option,
    center_option,
    difficulty_cap_option,
    iter_budget_option,
    j_max_option,
    l_connector_option,
    min_climb_ground_length_option,
    n_option,
    output_dir_option,
    progress_interval_option,
    quiet_option,
    radius_option,
    run_entry_point,
    seed_option,
    stagnation_iters_option,
    theta_option,
    time_budget_option,
    untagged_trails_option,
    validate_area_size,
    verbose_option,
)


@click.command(
    name="steeproute",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(package_name="steeproute", prog_name="steeproute")
@center_option
@radius_option
@theta_option
@difficulty_cap_option
@l_connector_option
@min_climb_ground_length_option
@j_max_option
@n_option
@area_cap_option
@untagged_trails_option
@seed_option
@iter_budget_option
@time_budget_option
@stagnation_iters_option
@progress_interval_option
@output_dir_option
@verbose_option
@quiet_option
@cache_dir_option
def cli(
    *,
    center: tuple[float, float],
    radius: float,
    theta: float,
    difficulty_cap: str,
    l_connector: float,
    min_climb_ground_length: float,
    j_max: float,
    n: int,
    area_cap: float,
    untagged_trails: str,
    seed: int | None,
    iter_budget: int | None,
    time_budget: float,
    stagnation_iters: int | None,
    progress_interval: float | None,
    output_dir: pathlib.Path,
    verbose: bool,
    quiet: bool,
    cache_dir: pathlib.Path | None,
) -> int:
    validate_area_size(radius_km=radius, area_cap_km2=area_cap)

    # Stub: full flag consumption lands in Epics 2-4. Acknowledge all click-bound kwargs so
    # basedpyright doesn't flag them as unused.
    _ = (
        center,
        radius,
        theta,
        difficulty_cap,
        l_connector,
        min_climb_ground_length,
        j_max,
        n,
        area_cap,
        untagged_trails,
        seed,
        iter_budget,
        time_budget,
        stagnation_iters,
        progress_interval,
        output_dir,
        verbose,
        quiet,
        cache_dir,
    )

    print("steeproute (query CLI) - stub; full implementation lands in Epics 2-4")
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
