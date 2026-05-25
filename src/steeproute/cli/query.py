# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# Reason: `check_coverage` returns `PreparedData` whose `graph` is a
# `MultiDiGraph[Unknown]` upstream (networkx generic parameter unspecified).
# Same external-boundary pattern as `cli/setup.py` and `pipeline/`.
"""steeproute query CLI entry point: FR24 coverage check + cache-hit cue (stages 8-9 + solver in later epics).

Story 2.10 wires the query CLI through `cache.check_coverage`, which resolves
the user's `--center` / `--radius` against `index.json` and either returns the
smallest-radius `PreparedData` strictly containing the query area or raises
`CacheNotFoundError` (mapped to exit 2 by `run_entry_point`). The fully-wired
solver lands in Epic 3; this story emits a one-line `cache-hit` summary on
stdout and an OSM-age warning when the chosen entry's `osm_extract_date`
exceeds `--osm-age-warn-days`.
"""

from __future__ import annotations

import datetime
import pathlib
from typing import NoReturn

import click

from steeproute.cache import check_coverage, resolve_cache_root
from steeproute.cli._shared import (
    area_cap_option,
    cache_dir_option,
    center_option,
    configure_cli_logging,
    difficulty_cap_option,
    emit_osm_age_warning,
    iter_budget_option,
    j_max_option,
    l_connector_option,
    min_climb_ground_length_option,
    n_option,
    osm_age_warn_days_option,
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
from steeproute.models import Area


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
@osm_age_warn_days_option
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
    osm_age_warn_days: int,
) -> int:
    configure_cli_logging(verbose=verbose)

    # FR2 sanity: reject queries whose disk-area exceeds --area-cap before we
    # walk the cache. A typo like `--radius 5000` should fail-fast at the CLI
    # boundary, not after a successful cache walk.
    validate_area_size(radius_km=radius, area_cap_km2=area_cap)

    area = Area(center=center, radius_km=radius)
    cache_root = resolve_cache_root(cache_dir)

    # FR24 coverage check. Raises `CacheNotFoundError` (→ exit 2 via
    # `run_entry_point`) when no prepared cache strictly contains the query
    # area; opportunistically rebuilds `index.json` if a prior `write_entry`
    # was interrupted before its final rebuild call.
    prepared = check_coverage(cache_root, area)

    # OSM-age warning on cache-hit (Architecture §Cat 4f). The query CLI has no
    # `--force-refresh` flag of its own — the helper's shared message tells the
    # user to re-run `steeproute-setup --force-refresh` for this area.
    emit_osm_age_warning(
        manifest=prepared.manifest,
        threshold_days=osm_age_warn_days,
        now=datetime.datetime.now(datetime.UTC),
    )

    # Solver wiring lands in Epic 3 — Story 2.10 establishes the cache-hit
    # path and its observable contract. The print already touches `prepared`
    # via `manifest.cache_key_hash`, so no separate basedpyright-silencer is
    # needed for `prepared.graph` (Epic 3 will consume it). Single space
    # between tokens for clean downstream tooling that splits on whitespace.
    print(f"steeproute: cache-hit cache_key_hash: {prepared.manifest.cache_key_hash}")

    # Acknowledge the remaining click-bound kwargs so basedpyright doesn't flag
    # them — the solver, output, and progress wiring consumes them in Epics 3-4.
    _ = (
        theta,
        difficulty_cap,
        l_connector,
        min_climb_ground_length,
        j_max,
        n,
        untagged_trails,
        seed,
        iter_budget,
        time_budget,
        stagnation_iters,
        progress_interval,
        output_dir,
        quiet,
    )
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
