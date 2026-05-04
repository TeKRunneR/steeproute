"""Unit tests for the click option decorator surface in cli/_shared.py."""

import click
import pytest
from click.testing import CliRunner

from steeproute.cli._shared import (
    LAT_LON,
    LatLonParamType,
    area_cap_option,
    cache_dir_option,
    center_option,
    dem_path_option,
    dem_version_option,
    difficulty_cap_option,
    force_refresh_option,
    is_verbose,
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
    seed_option,
    stagnation_iters_option,
    theta_option,
    time_budget_option,
    untagged_trails_option,
    verbose_option,
)
from steeproute.cli.query import cli as query_cli
from steeproute.cli.setup import cli as setup_cli

# --- LatLonParamType ---


def test_lat_lon_param_type_parses_valid_input() -> None:
    assert LAT_LON.convert("45.0716,6.1079", None, None) == (45.0716, 6.1079)


def test_lat_lon_param_type_rejects_no_comma() -> None:
    with pytest.raises(click.BadParameter):
        LAT_LON.convert("45.07", None, None)


def test_lat_lon_param_type_rejects_non_numeric() -> None:
    with pytest.raises(click.BadParameter):
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
    progress_interval_option,
    output_dir_option,
    verbose_option,
    quiet_option,
    cache_dir_option,
    force_refresh_option,
    dem_version_option,
    dem_path_option,
    osm_age_warn_days_option,
]


@pytest.mark.parametrize("decorator", ALL_DECORATORS)
def test_decorator_is_callable(decorator: object) -> None:
    assert callable(decorator)


# --- --verbose wires set_verbose(True) on both CLIs ---


def test_verbose_flag_sets_verbose_state_on_query_cli() -> None:
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--center", "45.07,6.11", "--radius", "10", "--verbose"])
    assert result.exit_code == 0
    assert is_verbose() is True


def test_query_cli_without_verbose_leaves_state_false() -> None:
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--center", "45.07,6.11", "--radius", "10"])
    assert result.exit_code == 0
    assert is_verbose() is False


def test_verbose_flag_sets_verbose_state_on_setup_cli() -> None:
    runner = CliRunner()
    result = runner.invoke(setup_cli, ["--center", "45.07,6.11", "--radius", "10", "--verbose"])
    assert result.exit_code == 0
    assert is_verbose() is True


def test_setup_cli_without_verbose_leaves_state_false() -> None:
    runner = CliRunner()
    result = runner.invoke(setup_cli, ["--center", "45.07,6.11", "--radius", "10"])
    assert result.exit_code == 0
    assert is_verbose() is False
