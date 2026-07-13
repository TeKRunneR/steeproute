"""Unit tests for the click option decorator surface in cli/_shared.py."""

import pathlib

import pytest
from click.testing import CliRunner

from steeproute.cli._shared import (
    LAT_LON,
    LatLonParamType,
    area_cap_option,
    cache_dir_option,
    center_option,
    dem_fetch_workers_option,
    dem_version_option,
    difficulty_cap_option,
    force_refresh_option,
    is_verbose,
    iter_budget_option,
    j_max_option,
    l_connector_option,
    merge_interval_option,
    min_climb_ground_length_option,
    n_option,
    osm_age_warn_days_option,
    output_dir_option,
    progress_interval_option,
    quiet_option,
    radius_option,
    seed_option,
    stagnation_iters_option,
    theta_option,
    time_budget_option,
    untagged_trails_option,
    verbose_option,
    workers_option,
)
from steeproute.cli.query import cli as query_cli
from steeproute.cli.setup import cli as setup_cli
from steeproute.errors import BadCLIArgError, CacheNotFoundError

# --- LatLonParamType ---


def test_lat_lon_param_type_parses_valid_input() -> None:
    assert LAT_LON.convert("45.0716,6.1079", None, None) == (45.0716, 6.1079)


def test_lat_lon_param_type_rejects_no_comma() -> None:
    with pytest.raises(BadCLIArgError):
        LAT_LON.convert("45.07", None, None)


def test_lat_lon_param_type_rejects_non_numeric() -> None:
    with pytest.raises(BadCLIArgError):
        LAT_LON.convert("abc,def", None, None)


def test_lat_lon_param_type_idempotent_on_tuple() -> None:
    assert LAT_LON.convert((45.07, 6.11), None, None) == (45.07, 6.11)


def test_lat_lon_param_type_name_is_lat_lon() -> None:
    assert LatLonParamType.name == "lat,lon"


# --- All decorators are callable ---


ALL_DECORATORS = [
    center_option,
    radius_option,
    theta_option,
    difficulty_cap_option,
    l_connector_option,
    min_climb_ground_length_option,
    j_max_option,
    n_option,
    area_cap_option,
    untagged_trails_option,
    seed_option,
    iter_budget_option,
    time_budget_option,
    stagnation_iters_option,
    workers_option,
    merge_interval_option,
    progress_interval_option,
    output_dir_option,
    verbose_option,
    quiet_option,
    cache_dir_option,
    force_refresh_option,
    dem_version_option,
    dem_fetch_workers_option,
    osm_age_warn_days_option,
]


@pytest.mark.parametrize("decorator", ALL_DECORATORS)
def test_decorator_is_callable(decorator: object) -> None:
    assert callable(decorator)


# --- --verbose wires set_verbose(True) on both CLIs ---


def test_verbose_flag_sets_verbose_state_on_query_cli(tmp_path: pathlib.Path) -> None:
    """`--verbose` is an eager click callback — state flips even when the body fails.

    Post-Story-2.10 the query CLI goes through `check_coverage`, which raises
    `CacheNotFoundError` against an empty `--cache-dir`. The eager-callback
    contract is "set state during the first parse pass" — it runs before the
    body executes, so verifying `is_verbose()` after a deliberately-failing
    invocation confirms eager evaluation still works. Same pattern as the
    setup-side test below (Story 2.8).
    """
    runner = CliRunner()
    result = runner.invoke(
        query_cli,
        [
            "--center",
            "45.07,6.11",
            "--radius",
            "10",
            "--verbose",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    assert isinstance(result.exception, CacheNotFoundError)
    assert is_verbose() is True


def test_query_cli_without_verbose_leaves_state_false(tmp_path: pathlib.Path) -> None:
    """Same pattern as the above test: an empty cache-dir surfaces CacheNotFoundError; verbose state stays False."""
    runner = CliRunner()
    result = runner.invoke(
        query_cli,
        [
            "--center",
            "45.07,6.11",
            "--radius",
            "10",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    assert isinstance(result.exception, CacheNotFoundError)
    assert is_verbose() is False


def test_verbose_flag_sets_verbose_state_on_setup_cli() -> None:
    """`--verbose` is an eager click callback — state flips even when the body later fails.

    The eager-callback contract is "set state during the first parse pass", which
    runs before the command body raises. We trip the body with a radius above the
    50 km ceiling (`validate_setup_radius`), which fails before any cache/network
    work — so the assertion stays offline while still proving eager evaluation.
    """
    runner = CliRunner()
    result = runner.invoke(setup_cli, ["--center", "45.07,6.11", "--radius", "5000", "--verbose"])
    # The above-ceiling radius raises BadCLIArgError, but the eager --verbose
    # callback has already flipped the global state during parsing.
    assert isinstance(result.exception, BadCLIArgError)
    assert is_verbose() is True


def test_setup_cli_without_verbose_leaves_state_false() -> None:
    # `tests/unit/conftest.py::reset_verbose_flag` autouse-resets `_verbose` to
    # `False` before this test runs, so the assertion below tests "the eager
    # callback did NOT flip the state without `--verbose`" — not just inertia
    # carried over from a previous test.
    # Above-ceiling radius fails in the body before any cache/network work, so the
    # invocation stays offline while still proving the callback didn't flip state.
    runner = CliRunner()
    result = runner.invoke(setup_cli, ["--center", "45.07,6.11", "--radius", "5000"])
    assert isinstance(result.exception, BadCLIArgError)
    assert is_verbose() is False
