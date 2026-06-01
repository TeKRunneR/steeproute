"""Unit tests for FR1/FR2 area-spec validation at the CLI boundary (Story 1.6)."""

import math
import pathlib
import sys
from collections.abc import Callable
from unittest import mock

import pytest
from click.testing import CliRunner

from steeproute.cli._shared import (
    LAT_LON,
    ensure_output_dir,
    is_verbose,
    validate_area_size,
    validate_solver_options,
)
from steeproute.cli.query import cli as query_cli
from steeproute.cli.query import main as query_main
from steeproute.cli.setup import cli as setup_cli
from steeproute.errors import BadCLIArgError, CacheNotFoundError

# --- LatLonParamType: range validation + BadCLIArgError surfacing (AC #1) ---


@pytest.mark.parametrize(
    ("value", "violation_token"),
    [
        ("abc,def", "LAT,LON"),  # syntactic: non-numeric
        ("45.07", "LAT,LON"),  # syntactic: missing comma
        ("45.07,6.11,extra", "LAT,LON"),  # syntactic: too many fields
        ("95.0,0.0", "latitude"),  # range: lat > 90
        ("-95.0,0.0", "latitude"),  # range: lat < -90
        ("45.0,181.0", "longitude"),  # range: lon > 180
        ("45.0,-181.0", "longitude"),  # range: lon < -180
    ],
)
def test_lat_lon_convert_raises_bad_cli_arg_error(value: str, violation_token: str) -> None:
    """convert() raises BadCLIArgError naming --center and the violation."""
    with pytest.raises(BadCLIArgError) as exc_info:
        LAT_LON.convert(value, None, None)
    msg = exc_info.value.user_message
    assert "--center" in msg
    assert violation_token in msg


def test_lat_lon_convert_accepts_boundary_values() -> None:
    """The [-90, 90] x [-180, 180] envelope is inclusive at the boundary."""
    assert LAT_LON.convert("90.0,180.0", None, None) == (90.0, 180.0)
    assert LAT_LON.convert("-90.0,-180.0", None, None) == (-90.0, -180.0)


# --- validate_area_size: AC #2 message format ---


def test_validate_area_size_passes_below_cap() -> None:
    """Area strictly below the cap is silently accepted."""
    validate_area_size(radius_km=10.0, area_cap_km2=500.0)


def test_validate_area_size_passes_just_below_cap() -> None:
    """Values strictly below the cap are accepted; the comparison is exact (no FP slack)."""
    radius = math.sqrt(500.0 / math.pi) * 0.999
    validate_area_size(radius_km=radius, area_cap_km2=500.0)


def test_validate_area_size_rejects_above_cap() -> None:
    """Area exceeding the cap raises BadCLIArgError naming --radius and --area-cap."""
    with pytest.raises(BadCLIArgError) as exc_info:
        validate_area_size(radius_km=30.0, area_cap_km2=500.0)
    msg = exc_info.value.user_message
    assert "--radius" in msg
    assert "30" in msg
    assert "--area-cap" in msg
    assert "500" in msg
    assert "km" in msg


# --- validate_solver_options: §Cat 10 CLI-boundary guards (Story 3.11 review) ---


def _check_solver_options(
    *,
    theta: float = 0.20,
    l_connector: float = 200.0,
    min_climb_ground_length: float = 300.0,
    j_max: float = 0.30,
    n: int = 5,
    iter_budget: int | None = None,
) -> None:
    """Call `validate_solver_options` with in-range defaults; tests override one field."""
    validate_solver_options(
        theta=theta,
        l_connector=l_connector,
        min_climb_ground_length=min_climb_ground_length,
        j_max=j_max,
        n=n,
        iter_budget=iter_budget,
    )


def test_validate_solver_options_accepts_defaults() -> None:
    """In-range values (including iter_budget=None, the unset default) pass silently."""
    _check_solver_options()


def test_validate_solver_options_accepts_boundary_values() -> None:
    """j_max ∈ {0, 1} inclusive; n=1, iter_budget=1, theta=0, l_connector=0 are the minimums."""
    _check_solver_options(j_max=0.0, n=1, iter_budget=1, theta=0.0, l_connector=0.0)
    _check_solver_options(j_max=1.0)


@pytest.mark.parametrize(
    ("call", "violation_token"),
    [
        (lambda: _check_solver_options(iter_budget=0), "--iter-budget"),
        (lambda: _check_solver_options(iter_budget=-1), "--iter-budget"),
        (lambda: _check_solver_options(n=0), "--n"),
        (lambda: _check_solver_options(n=-3), "--n"),
        (lambda: _check_solver_options(j_max=1.5), "--j-max"),
        (lambda: _check_solver_options(j_max=-0.1), "--j-max"),
        (lambda: _check_solver_options(j_max=float("nan")), "--j-max"),
        (lambda: _check_solver_options(theta=float("nan")), "--theta"),
        (lambda: _check_solver_options(theta=float("inf")), "--theta"),
        (lambda: _check_solver_options(theta=-0.1), "--theta"),
        (lambda: _check_solver_options(min_climb_ground_length=0.0), "--min-climb-ground-length"),
        (lambda: _check_solver_options(min_climb_ground_length=-5.0), "--min-climb-ground-length"),
        (
            lambda: _check_solver_options(min_climb_ground_length=float("nan")),
            "--min-climb-ground-length",
        ),
        (lambda: _check_solver_options(l_connector=-1.0), "--l-connector"),
        (lambda: _check_solver_options(l_connector=float("inf")), "--l-connector"),
    ],
)
def test_validate_solver_options_rejects_out_of_range(
    call: Callable[[], None], violation_token: str
) -> None:
    """Each out-of-range / non-finite flag raises BadCLIArgError naming the flag."""
    with pytest.raises(BadCLIArgError) as exc_info:
        call()
    assert violation_token in exc_info.value.user_message


# --- ensure_output_dir: residual --output-dir validation (Story 3.11 review) ---


def test_ensure_output_dir_creates_missing_directory(tmp_path: pathlib.Path) -> None:
    """A not-yet-existing (nested) output dir is created."""
    target = tmp_path / "reports" / "nested"
    ensure_output_dir(target)
    assert target.is_dir()


def test_ensure_output_dir_idempotent_on_existing(tmp_path: pathlib.Path) -> None:
    """Re-creating an existing directory is a silent no-op."""
    ensure_output_dir(tmp_path)
    ensure_output_dir(tmp_path)  # no raise


def test_ensure_output_dir_rejects_parent_that_is_a_file(tmp_path: pathlib.Path) -> None:
    """A parent component that is a regular file → BadCLIArgError (not a raw OSError)."""
    a_file = tmp_path / "iam_a_file.txt"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(BadCLIArgError) as exc_info:
        ensure_output_dir(a_file / "sub")
    assert "--output-dir" in exc_info.value.user_message


# --- Query CLI end-to-end (CliRunner): area-cap + happy path (AC #2, #5) ---


def test_query_cli_rejects_out_of_range_n() -> None:
    """`--n 0` surfaces BadCLIArgError out past click.standalone_mode (exit-2 contract)."""
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--center", "45.0716,6.1079", "--radius", "10", "--n", "0"])
    assert isinstance(result.exception, BadCLIArgError)
    assert "--n" in result.exception.user_message


def test_query_cli_happy_path_passes_parsing_then_hits_coverage_check(
    tmp_path: pathlib.Path,
) -> None:
    """Valid args clear parse + area-cap, then surface `CacheNotFoundError` from `check_coverage`.

    Story 2.10 wired the query CLI through `cache.check_coverage` (FR24). With
    no prepared cache under the (test-isolated) `--cache-dir`, the wired CLI
    raises `CacheNotFoundError` instead of reaching the Story 1.5 stub body.
    What this test still proves: parsing succeeded, validation succeeded, and
    the CLI body executed far enough to call `check_coverage`. The exit-2
    contract itself is covered by `tests/e2e/test_coverage_check.py`.
    """
    runner = CliRunner()
    result = runner.invoke(
        query_cli,
        [
            "--center",
            "45.0716,6.1079",
            "--radius",
            "10",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    assert isinstance(result.exception, CacheNotFoundError)


def test_query_cli_rejects_radius_exceeding_area_cap() -> None:
    """π·r² > --area-cap surfaces BadCLIArgError."""
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--center", "45.0716,6.1079", "--radius", "30"])
    assert isinstance(result.exception, BadCLIArgError)
    assert "--area-cap" in result.exception.user_message


def test_query_cli_accepts_radius_just_below_custom_cap(tmp_path: pathlib.Path) -> None:
    """User-overridden --area-cap is honored; radius producing area below cap passes validation.

    Post-Story-2.10 the CLI now goes through `check_coverage` after passing
    `validate_area_size`. With an isolated empty `--cache-dir`, success past
    the area-cap guard is signalled by reaching `CacheNotFoundError` rather
    than a `BadCLIArgError` from the cap.
    """
    runner = CliRunner()
    radius = math.sqrt(100.0 / math.pi) * 0.999
    result = runner.invoke(
        query_cli,
        [
            "--center",
            "45.0716,6.1079",
            "--radius",
            f"{radius:.6f}",
            "--area-cap",
            "100",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    # Area-cap passed (no BadCLIArgError); coverage check then raises.
    assert isinstance(result.exception, CacheNotFoundError)


def test_query_cli_rejects_malformed_center() -> None:
    """Malformed --center bubbles BadCLIArgError out past click.standalone_mode."""
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--center", "abc,def", "--radius", "10"])
    assert isinstance(result.exception, BadCLIArgError)
    assert "--center" in result.exception.user_message


def test_query_cli_rejects_out_of_range_latitude() -> None:
    """Range check fires from inside LatLonParamType during parse."""
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--center", "95.0,0.0", "--radius", "10"])
    assert isinstance(result.exception, BadCLIArgError)
    assert "latitude" in result.exception.user_message


# --- Setup CLI: lat/lon range applies; area-cap does not (AC #6) ---


def test_setup_cli_inherits_lat_lon_range_validation() -> None:
    """Range validation lives in LatLonParamType; setup CLI inherits it."""
    runner = CliRunner()
    result = runner.invoke(setup_cli, ["--center", "95.0,0.0", "--radius", "10"])
    assert isinstance(result.exception, BadCLIArgError)
    assert "latitude" in result.exception.user_message


def test_setup_cli_rejects_nan_radius(tmp_path: pathlib.Path) -> None:
    """validate_setup_radius rejects non-finite radii (NaN, ±Inf).

    `click.FLOAT` accepts the strings "nan", "inf", "-inf" via `float()`. Without
    an explicit finiteness check, `nan` slips past every comparison (IEEE-754),
    and the area silently propagates into osmnx as a crash. Story 2.8 review P1.
    """
    runner = CliRunner()
    for bad in ("nan", "inf", "-inf"):
        result = runner.invoke(
            setup_cli,
            [
                "--center",
                "45.0716,6.1079",
                "--radius",
                bad,
                "--dem-path",
                str(tmp_path / "nonexistent.tif"),
            ],
        )
        assert isinstance(result.exception, BadCLIArgError), (
            f"--radius {bad!r} should raise BadCLIArgError; got {result.exception!r}"
        )
        assert "--radius" in result.exception.user_message


def test_setup_cli_does_not_enforce_area_cap(tmp_path: pathlib.Path) -> None:
    """Setup CLI has no --area-cap flag and does not call validate_area_size.

    A 30 km radius (~2827 km²) would be rejected by the query CLI's default cap of 500 km²,
    but setup accepts it because area-cap enforcement is query-only. The setup CLI does
    apply its own `validate_setup_radius` ceiling (Story 2.8), set at 50 km — 30 is below
    that. We provide a `--dem-path` so the test isolates the area-cap question; the path
    points at `tmp_path / nonexistent.tif` (a guaranteed-non-existent path that does not
    depend on the test-process CWD), so the failure mode is the dem-existence guard, not
    a real OSM fetch.
    """
    runner = CliRunner()
    result = runner.invoke(
        setup_cli,
        [
            "--center",
            "45.0716,6.1079",
            "--radius",
            "30",
            "--dem-path",
            str(tmp_path / "nonexistent.tif"),
        ],
    )
    # The 30 km radius passes both `validate_setup_radius` (≤ 50 km) and any
    # would-be area-cap check; what fails the run is the dem-existence guard
    # at the CLI boundary. That failure mode confirms the absence of area-cap
    # rejection on the setup path.
    assert isinstance(result.exception, BadCLIArgError)
    assert "--area-cap" not in result.exception.user_message


# --- --verbose ordering with BadCLIArgError from convert (AC #4) ---


def test_verbose_state_is_set_before_lat_lon_convert_runs() -> None:
    """--verbose is eager: state flips before LatLonParamType.convert can raise.

    Without is_eager=True, the malformed --center would raise BadCLIArgError during
    click's parse pass before --verbose's body wiring ever ran, so is_verbose() would
    stay False and run_entry_point's `detail` line would be suppressed in the real CLI.
    """
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--verbose", "--center", "abc,def", "--radius", "10"])
    assert isinstance(result.exception, BadCLIArgError)
    assert is_verbose() is True


def test_verbose_with_malformed_center_renders_detail_via_main(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End-to-end: --verbose + malformed --center → run_entry_point prints the detail line.

    Goes through main() (not just CliRunner) so the run_entry_point exit-code wrapper runs.
    """
    argv = ["steeproute", "--verbose", "--center", "abc,def", "--radius", "10"]
    with mock.patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc_info:
        query_main()
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    # run_entry_point indents the detail line with 8 spaces; presence of that prefix
    # on a non-first line proves verbose state was set BEFORE convert raised.
    assert "\n        " in err
