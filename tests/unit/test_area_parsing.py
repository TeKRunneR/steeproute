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
    resolve_area,
    validate_area_size,
    validate_setup_area,
    validate_solver_options,
)
from steeproute.cli.query import cli as query_cli
from steeproute.cli.query import main as query_main
from steeproute.cli.setup import cli as setup_cli
from steeproute.errors import BadCLIArgError, CacheNotFoundError
from steeproute.models import Area

_CENTER = (45.0716, 6.1079)


def _resolve(
    *,
    radius: float | None = None,
    width: float | None = None,
    height: float | None = None,
    angle: float = 0.0,
    center: tuple[float, float] = _CENTER,
) -> Area:
    """`resolve_area` with the CLI's keyword names, defaulted for brevity."""
    return resolve_area(
        center=center, radius_km=radius, width_km=width, height_km=height, angle_deg=angle
    )

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


# --- resolve_area: the shared area surface (Story 15.3 AC #1/#2/#3) ---


def test_resolve_area_radius_builds_the_v1_square() -> None:
    """AC #2: `--radius` constructs exactly the pre-Epic-15 `Area` — no extents stored.

    Storing `radius_km` literally (rather than equal explicit extents) is what
    keeps the manifest `area` block, the fetch call, and every square message
    byte-identical.
    """
    assert _resolve(radius=6.0) == Area(center=_CENTER, radius_km=6.0)


def test_resolve_area_width_height_are_full_dimensions() -> None:
    """AC #1: `--width`/`--height` are box dimensions; `Area` stores half-extents."""
    area = _resolve(width=16.0, height=6.0, angle=35.0)
    assert area.half_extents_km == (8.0, 3.0)
    assert area.angle_deg == 35.0
    # A rotated area's `radius_km` is inert — the extents drive the geometry.
    assert area.radius_km == 0.0
    assert not area.is_square


def test_resolve_area_angle_with_radius_is_a_rotated_square() -> None:
    """AC #1: `--angle` rotates either spelling; a rotated square is a legal shape."""
    area = _resolve(radius=4.0, angle=30.0)
    assert area.half_extents_km == (4.0, 4.0)
    assert area.angle_deg == 30.0
    # `is_square` means "v1-equivalent" (axis-aligned), so a rotated square is not one —
    # it correctly takes the polygon fetch path.
    assert not area.is_square


def test_resolve_area_equal_extents_stay_square() -> None:
    """A square spelled as equal extents is still `is_square` (axis-aligned, equal)."""
    assert _resolve(width=12.0, height=12.0).is_square


def test_resolve_area_rejects_no_size_at_all() -> None:
    """AC #1: `--radius` is no longer click-required, so the resolver owns the check."""
    with pytest.raises(BadCLIArgError) as exc_info:
        _resolve()
    msg = exc_info.value.user_message
    assert "--radius" in msg
    assert "--width" in msg


def test_resolve_area_rejects_radius_combined_with_extents() -> None:
    """AC #1: the two spellings are mutually exclusive."""
    with pytest.raises(BadCLIArgError) as exc_info:
        _resolve(radius=5.0, width=10.0, height=4.0)
    assert "--radius" in exc_info.value.user_message


@pytest.mark.parametrize(
    ("width", "height"),
    [(10.0, None), (None, 4.0)],
)
def test_resolve_area_rejects_partial_extents(width: float | None, height: float | None) -> None:
    """AC #1: one dimension alone is not a rectangle."""
    with pytest.raises(BadCLIArgError) as exc_info:
        _resolve(width=width, height=height)
    msg = exc_info.value.user_message
    assert "--width" in msg
    assert "--height" in msg


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_resolve_area_rejects_non_finite_angle(bad: float) -> None:
    """AC #3: a non-finite bearing yields NaN corners and an unusable polygon."""
    with pytest.raises(BadCLIArgError) as exc_info:
        _resolve(radius=5.0, angle=bad)
    assert "--angle" in exc_info.value.user_message


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
def test_resolve_area_rejects_bad_radius_naming_radius(bad: float) -> None:
    """AC #3: diagnostics name `--radius` when the size was spelled as a radius."""
    with pytest.raises(BadCLIArgError) as exc_info:
        _resolve(radius=bad)
    msg = exc_info.value.user_message
    assert "--radius" in msg
    assert "--width" not in msg


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -1.0])
@pytest.mark.parametrize("flag", ["--width", "--height"])
def test_resolve_area_rejects_bad_extent_naming_that_flag(flag: str, bad: float) -> None:
    """AC #3: a bad dimension is reported against the dimension flag the user typed."""
    width, height = (bad, 4.0) if flag == "--width" else (4.0, bad)
    with pytest.raises(BadCLIArgError) as exc_info:
        _resolve(width=width, height=height)
    msg = exc_info.value.user_message
    assert flag in msg
    assert "--radius" not in msg


def test_resolve_area_rejects_bad_radius_even_under_a_bearing() -> None:
    """AC #3: the bearing must not decide which flag a size error names.

    A rotated square is a legitimate `--radius --angle` pair; reporting its bad
    radius as a `--width` problem would name a flag the user never typed.
    """
    with pytest.raises(BadCLIArgError) as exc_info:
        _resolve(radius=-1.0, angle=30.0)
    assert "--radius" in exc_info.value.user_message


def test_resolve_area_rejects_out_of_range_center() -> None:
    """AC #3: the center guard also applies to direct (non-click) callers."""
    with pytest.raises(BadCLIArgError) as exc_info:
        _resolve(radius=5.0, center=(95.0, 0.0))
    assert "latitude" in exc_info.value.user_message


# --- validate_area_size: FR2 true-rectangle-area cap (Story 15.3 AC #4) ---


def test_validate_area_size_passes_below_cap() -> None:
    """Area strictly below the cap is silently accepted."""
    validate_area_size(_resolve(radius=10.0), area_cap_km2=500.0)


def test_validate_area_size_passes_just_below_cap() -> None:
    """Values strictly below the cap are accepted; the comparison is exact (no FP slack).

    The cap measures the **true** rectangle area (`2r × 2r = 4r²`), so the
    boundary radius is `sqrt(cap/4)` — not the pre-Epic-15 `sqrt(cap/π)` disk
    proxy (which admitted boxes ~27% over the cap).
    """
    radius = math.sqrt(500.0 / 4.0) * 0.999
    validate_area_size(_resolve(radius=radius), area_cap_km2=500.0)


def test_validate_area_size_uses_true_rectangle_area_not_disk_proxy() -> None:
    """AC #4: a radius the `π·r²` proxy admitted is now correctly rejected."""
    radius = math.sqrt(500.0 / math.pi) * 0.999  # ~499.5 km² as a disk, ~635 km² as a box
    with pytest.raises(BadCLIArgError):
        validate_area_size(_resolve(radius=radius), area_cap_km2=500.0)


def test_validate_area_size_rejects_above_cap() -> None:
    """Area exceeding the cap raises BadCLIArgError naming --radius and --area-cap."""
    with pytest.raises(BadCLIArgError) as exc_info:
        validate_area_size(_resolve(radius=30.0), area_cap_km2=500.0)
    msg = exc_info.value.user_message
    assert "--radius" in msg
    assert "30" in msg
    assert "--area-cap" in msg
    assert "500" in msg
    assert "km" in msg
    # True rectangle area (60x60), not the old π·30² ≈ 2827 disk proxy.
    assert "3600" in msg


def test_validate_area_size_rejects_oversize_rectangle_naming_its_own_flags() -> None:
    """AC #4: a rotated box's rejection is copy-pasteable, never a bogus `--radius`."""
    with pytest.raises(BadCLIArgError) as exc_info:
        validate_area_size(_resolve(width=40.0, height=30.0, angle=25.0), area_cap_km2=500.0)
    msg = exc_info.value.user_message
    assert "--width 40" in msg
    assert "--height 30" in msg
    assert "--angle 25" in msg
    assert "1200" in msg  # 40 x 30
    assert "--radius" not in msg


def test_validate_area_size_area_is_bearing_independent() -> None:
    """Rotation preserves area, so the cap verdict cannot depend on `--angle`."""
    validate_area_size(_resolve(width=20.0, height=20.0, angle=37.0), area_cap_km2=401.0)
    with pytest.raises(BadCLIArgError):
        validate_area_size(_resolve(width=20.0, height=20.0, angle=37.0), area_cap_km2=399.0)


# --- validate_setup_area: shape-aware setup ceiling (Story 15.3 AC #5) ---


def test_validate_setup_area_accepts_at_the_ceiling() -> None:
    """The 50 km half-side ceiling is inclusive, unchanged from Story 2.8."""
    validate_setup_area(_resolve(radius=50.0))


def test_validate_setup_area_rejects_radius_above_ceiling() -> None:
    """Square wording and threshold are unchanged (byte-identical to pre-Epic-15)."""
    with pytest.raises(BadCLIArgError) as exc_info:
        validate_setup_area(_resolve(radius=60.0))
    msg = exc_info.value.user_message
    assert msg == "--radius 60 km exceeds the steeproute-setup ceiling of 50 km."


@pytest.mark.parametrize(
    ("width", "height", "flag"),
    [(120.0, 10.0, "--width"), (10.0, 120.0, "--height")],
)
def test_validate_setup_area_rejects_oversize_dimension(
    width: float, height: float, flag: str
) -> None:
    """AC #5: the ceiling generalizes per dimension, expressed as a full 100 km span."""
    with pytest.raises(BadCLIArgError) as exc_info:
        validate_setup_area(_resolve(width=width, height=height))
    msg = exc_info.value.user_message
    assert msg.startswith(f"{flag} 120 km exceeds the steeproute-setup ceiling of 100 km.")


def test_validate_setup_area_accepts_a_long_thin_rotated_box() -> None:
    """A 100x20 km box is exactly the square ceiling's span — the point of the epic."""
    validate_setup_area(_resolve(width=100.0, height=20.0, angle=45.0))


# --- validate_solver_options: §Cat 10 CLI-boundary guards (Story 3.11 review) ---


def _check_solver_options(
    *,
    theta: float = 0.20,
    min_climb_slope: float = 0.20,
    l_connector: float = 200.0,
    min_climb_ground_length: float = 300.0,
    elevation_smoothing: float = 50.0,
    elevation_deadband: float = 0.0,
    j_max: float = 0.30,
    n: int = 5,
    iter_budget: int | None = None,
    time_budget: float = 600.0,
    stagnation_iters: int | None = None,
    progress_interval: float = 5.0,
    workers: int = 1,
    merge_interval: int = 250_000,
    max_descent_slope: float | None = None,
) -> None:
    """Call `validate_solver_options` with in-range defaults; tests override one field."""
    validate_solver_options(
        theta=theta,
        min_climb_slope=min_climb_slope,
        l_connector=l_connector,
        min_climb_ground_length=min_climb_ground_length,
        elevation_smoothing=elevation_smoothing,
        elevation_deadband=elevation_deadband,
        j_max=j_max,
        n=n,
        iter_budget=iter_budget,
        time_budget=time_budget,
        stagnation_iters=stagnation_iters,
        progress_interval=progress_interval,
        workers=workers,
        merge_interval=merge_interval,
        max_descent_slope=max_descent_slope,
    )


def test_validate_solver_options_accepts_defaults() -> None:
    """In-range values (including iter_budget=None, the unset default) pass silently."""
    _check_solver_options()


def test_validate_solver_options_accepts_boundary_values() -> None:
    """j_max ∈ {0, 1} inclusive; n=1, iter_budget=1, theta=0, l_connector=0 are the minimums."""
    _check_solver_options(j_max=0.0, n=1, iter_budget=1, theta=0.0, l_connector=0.0)
    _check_solver_options(j_max=1.0)
    # Story 7.2: stagnation_iters=0 legitimately disables the check; a tiny
    # positive time_budget is in-range (the §Cat 5e check just trips sooner).
    _check_solver_options(stagnation_iters=0, time_budget=0.001)
    # Story 10.2: `--max-descent-slope` is opt-in — `None` (unset) is the default
    # and any finite, strictly positive gradient is accepted.
    _check_solver_options(max_descent_slope=None)
    _check_solver_options(max_descent_slope=0.01)
    _check_solver_options(max_descent_slope=2.0)
    # Story 14.4: --workers >= 1 (1 = single-process default; any positive count ok).
    _check_solver_options(workers=1)
    _check_solver_options(workers=8)
    # --merge-interval >= 0 (0 = migration disabled; any positive cadence ok).
    _check_solver_options(merge_interval=0)
    _check_solver_options(merge_interval=100_000)


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
        (lambda: _check_solver_options(min_climb_slope=float("nan")), "--min-climb-slope"),
        (lambda: _check_solver_options(min_climb_slope=float("inf")), "--min-climb-slope"),
        (lambda: _check_solver_options(min_climb_slope=-0.1), "--min-climb-slope"),
        (lambda: _check_solver_options(min_climb_ground_length=0.0), "--min-climb-ground-length"),
        (lambda: _check_solver_options(min_climb_ground_length=-5.0), "--min-climb-ground-length"),
        (
            lambda: _check_solver_options(min_climb_ground_length=float("nan")),
            "--min-climb-ground-length",
        ),
        (lambda: _check_solver_options(l_connector=-1.0), "--l-connector"),
        (lambda: _check_solver_options(l_connector=float("inf")), "--l-connector"),
        # Story 6.3 flags: NaN/inf would otherwise crash `graph_smooth_elevation`'s
        # `round()` (exit 1) or silently flatten the profile; negative is nonsensical.
        (lambda: _check_solver_options(elevation_smoothing=float("nan")), "--elevation-smoothing"),
        (lambda: _check_solver_options(elevation_smoothing=float("inf")), "--elevation-smoothing"),
        (lambda: _check_solver_options(elevation_smoothing=-1.0), "--elevation-smoothing"),
        (lambda: _check_solver_options(elevation_deadband=float("nan")), "--elevation-deadband"),
        (lambda: _check_solver_options(elevation_deadband=float("inf")), "--elevation-deadband"),
        (lambda: _check_solver_options(elevation_deadband=-1.0), "--elevation-deadband"),
        # Story 7.1 flag: NaN/inf would make the throttle never fire (silent
        # no-progress); 0/negative would forward every iteration (stdout flood).
        (lambda: _check_solver_options(progress_interval=float("nan")), "--progress-interval"),
        (lambda: _check_solver_options(progress_interval=float("inf")), "--progress-interval"),
        (lambda: _check_solver_options(progress_interval=0.0), "--progress-interval"),
        (lambda: _check_solver_options(progress_interval=-1.0), "--progress-interval"),
        # Story 7.2 flags: NaN/inf/non-positive time-budget would stop the solve
        # on iteration 1 (empty top-N); a negative stagnation window is nonsense.
        (lambda: _check_solver_options(time_budget=float("nan")), "--time-budget"),
        (lambda: _check_solver_options(time_budget=float("inf")), "--time-budget"),
        (lambda: _check_solver_options(time_budget=0.0), "--time-budget"),
        (lambda: _check_solver_options(time_budget=-1.0), "--time-budget"),
        (lambda: _check_solver_options(stagnation_iters=-1), "--stagnation-iters"),
        # Story 14.4: --workers < 1 would break ProcessPoolExecutor / the budget split.
        (lambda: _check_solver_options(workers=0), "--workers"),
        (lambda: _check_solver_options(workers=-2), "--workers"),
        # --merge-interval < 0 is nonsensical (0 legitimately disables migration).
        (lambda: _check_solver_options(merge_interval=-1), "--merge-interval"),
        # Story 10.2 flag: NaN/inf would slip past the IEEE-754 descent comparison;
        # 0/negative would forbid every descent (drop the flag to disable instead).
        (lambda: _check_solver_options(max_descent_slope=float("nan")), "--max-descent-slope"),
        (lambda: _check_solver_options(max_descent_slope=float("inf")), "--max-descent-slope"),
        (lambda: _check_solver_options(max_descent_slope=0.0), "--max-descent-slope"),
        (lambda: _check_solver_options(max_descent_slope=-0.1), "--max-descent-slope"),
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


def test_query_cli_rejects_nonfinite_progress_interval() -> None:
    """`--progress-interval nan` surfaces BadCLIArgError (Story 7.1) — proves it's threaded into validate_solver_options."""
    runner = CliRunner()
    result = runner.invoke(
        query_cli,
        ["--center", "45.0716,6.1079", "--radius", "10", "--progress-interval", "nan"],
    )
    assert isinstance(result.exception, BadCLIArgError)
    assert "--progress-interval" in result.exception.user_message


def test_query_cli_rejects_non_positive_workers() -> None:
    """`--workers 0` / negative surfaces BadCLIArgError (Story 14.4) → exit-2 contract."""
    runner = CliRunner()
    for bad in ("0", "-1"):
        result = runner.invoke(
            query_cli, ["--center", "45.0716,6.1079", "--radius", "10", "--workers", bad]
        )
        assert isinstance(result.exception, BadCLIArgError), (
            f"--workers {bad} should raise BadCLIArgError; got {result.exception!r}"
        )
        assert "--workers" in result.exception.user_message


def test_query_cli_threads_workers_to_run_parallel_grasp(tmp_path: pathlib.Path) -> None:
    """`--workers` reaches `run_parallel_grasp`'s `workers` arg (Story 14.4 CLI-layer plumbing).

    Patches the pieces so no cache/solve/network work happens: `check_coverage` and the
    stage functions are stubbed, and `run_parallel_grasp` is replaced with a spy that
    records the `workers` value it was called with, then raises to stop the run.
    """
    captured: dict[str, object] = {}

    def fake_run_parallel(*args: object, **kwargs: object) -> object:
        # positional: (contracted, params, seed, workers)
        captured["workers"] = args[3] if len(args) > 3 else kwargs.get("workers")
        raise RuntimeError("stop after run_parallel_grasp call")

    runner = CliRunner()
    with (
        mock.patch("steeproute.cli.query.check_coverage", return_value=mock.MagicMock()),
        mock.patch("steeproute.cli.query.operationalize_graph", return_value=mock.MagicMock()),
        mock.patch("steeproute.cli.query.filter_trails", return_value=mock.MagicMock()),
        mock.patch("steeproute.cli.query.detect_climbs", return_value=[mock.MagicMock()]),
        mock.patch("steeproute.cli.query.contract_climbs", return_value=mock.MagicMock()),
        mock.patch("steeproute.cli.query.emit_osm_age_warning"),
        mock.patch("steeproute.cli.query.run_parallel_grasp", side_effect=fake_run_parallel),
    ):
        result = runner.invoke(
            query_cli,
            [
                "--center",
                "45.0716,6.1079",
                "--radius",
                "10",
                "--cache-dir",
                str(tmp_path),
                "--workers",
                "3",
            ],
            catch_exceptions=True,
        )
    assert isinstance(result.exception, RuntimeError), result.output
    assert captured["workers"] == 3


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
    """True rectangle area > --area-cap surfaces BadCLIArgError."""
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
    radius = math.sqrt(100.0 / 4.0) * 0.999
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


# --- Both CLIs share one area surface (Story 15.3 AC #1) ---


@pytest.mark.parametrize("cli_name", ["query", "setup"])
def test_both_clis_reject_missing_area(cli_name: str) -> None:
    """`--radius` is no longer click-required; the shared resolver rejects "no area"."""
    runner = CliRunner()
    cli = query_cli if cli_name == "query" else setup_cli
    result = runner.invoke(cli, ["--center", "45.0716,6.1079"])
    assert isinstance(result.exception, BadCLIArgError)
    assert "--width" in result.exception.user_message


@pytest.mark.parametrize("cli_name", ["query", "setup"])
def test_both_clis_reject_radius_combined_with_extents(cli_name: str) -> None:
    """FR23: the mutual-exclusion rule is identical on both CLIs."""
    runner = CliRunner()
    cli = query_cli if cli_name == "query" else setup_cli
    result = runner.invoke(
        cli, ["--center", "45.0716,6.1079", "--radius", "5", "--width", "8", "--height", "4"]
    )
    assert isinstance(result.exception, BadCLIArgError)
    assert "--radius" in result.exception.user_message


def test_query_cli_accepts_a_rotated_rectangle(tmp_path: pathlib.Path) -> None:
    """AC #1: a rotated box parses and reaches the coverage check (not a parse error).

    With an isolated empty `--cache-dir`, reaching `CacheNotFoundError` proves the
    area surface was accepted and an `Area` was built.
    """
    runner = CliRunner()
    result = runner.invoke(
        query_cli,
        [
            "--center",
            "45.0716,6.1079",
            "--width",
            "16",
            "--height",
            "6",
            "--angle",
            "35",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    assert isinstance(result.exception, CacheNotFoundError)


def test_query_cli_rotated_coverage_miss_suggests_matching_flags(tmp_path: pathlib.Path) -> None:
    """AC #7: the suggested `steeproute-setup` command is copy-pasteable, not `--radius 0`."""
    runner = CliRunner()
    result = runner.invoke(
        query_cli,
        [
            "--center",
            "45.0716,6.1079",
            "--width",
            "16",
            "--height",
            "6",
            "--angle",
            "35",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    assert isinstance(result.exception, CacheNotFoundError)
    msg = result.exception.user_message
    assert "--width 16 --height 6 --angle 35" in msg
    assert "--radius" not in msg


def test_setup_cli_builds_a_rotated_area(tmp_path: pathlib.Path) -> None:
    """AC #1: setup accepts the same surface and hands the rotated `Area` to stage 1.

    `osm_load` is patched with a sentinel, so this proves the flags reached area
    construction without any network work.
    """
    from unittest.mock import patch

    runner = CliRunner()
    captured: list[Area] = []

    def _capture(area: Area) -> None:
        captured.append(area)
        raise RuntimeError("reached OSM download")

    with patch("steeproute.pipeline.osm_load", side_effect=_capture):
        runner.invoke(
            setup_cli,
            [
                "--center",
                "45.0716,6.1079",
                "--width",
                "16",
                "--height",
                "6",
                "--angle",
                "35",
                "--cache-dir",
                str(tmp_path),
            ],
            catch_exceptions=True,
        )
    assert captured, "stage 1 was never reached"
    assert captured[0].half_extents_km == (8.0, 3.0)
    assert captured[0].angle_deg == 35.0


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


def test_setup_cli_rejects_nan_radius() -> None:
    """`resolve_area` rejects non-finite radii (NaN, ±Inf).

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
            ],
        )
        assert isinstance(result.exception, BadCLIArgError), (
            f"--radius {bad!r} should raise BadCLIArgError; got {result.exception!r}"
        )
        assert "--radius" in result.exception.user_message


def test_setup_cli_rejects_non_positive_dem_fetch_workers() -> None:
    """validate_dem_fetch_workers rejects 0 and negative values (Story 14.3 scope revision).

    `ThreadPoolExecutor(max_workers=...)` requires >= 1; without this guard a
    `0`/negative `--dem-fetch-workers` would surface as a raw ValueError (exit 1)
    deep inside `_fetch_mosaic` instead of the documented BadCLIArgError tier.
    """
    runner = CliRunner()
    for bad in (0, -1):
        result = runner.invoke(
            setup_cli,
            ["--center", "45.0716,6.1079", "--radius", "1", "--dem-fetch-workers", str(bad)],
        )
        assert isinstance(result.exception, BadCLIArgError), (
            f"--dem-fetch-workers {bad} should raise BadCLIArgError; got {result.exception!r}"
        )
        assert "--dem-fetch-workers" in result.exception.user_message


def test_setup_cli_does_not_enforce_area_cap(tmp_path: pathlib.Path) -> None:
    """Setup CLI has no --area-cap flag and does not call validate_area_size.

    A 30 km radius (3600 km² as a true box) would be rejected by the query CLI's default
    cap of 500 km², but setup accepts it because area-cap enforcement is query-only. The
    setup CLI does apply its own `validate_setup_area` ceiling (Story 2.8), set at a 50 km
    half-side — 30 is below that. We patch `osm_load` (the first pipeline step) with a sentinel: any area-cap
    check would raise `BadCLIArgError` at the CLI boundary before the pipeline starts,
    so the sentinel propagating proves the 30 km radius passed validation with no
    area-cap rejection — without ever touching the network. (Patching a later step
    such as `resolve_dem` would first run the real stage-1 Overpass download for this
    30 km area: minutes of live network per suite run.)
    """
    from unittest.mock import patch

    runner = CliRunner()
    sentinel = RuntimeError("reached OSM download")
    with patch("steeproute.pipeline.osm_load", side_effect=sentinel):
        result = runner.invoke(
            setup_cli,
            ["--center", "45.0716,6.1079", "--radius", "30", "--cache-dir", str(tmp_path)],
            catch_exceptions=True,
        )
    # The run got past `validate_setup_radius` and any would-be area-cap check,
    # reaching stage 1 (the sentinel) — confirming no area-cap rejection.
    assert result.exception is sentinel


def test_setup_cli_threads_dem_fetch_workers_to_resolve_dem(tmp_path: pathlib.Path) -> None:
    """`--dem-fetch-workers` reaches `resolve_dem`'s `fetch_workers` kwarg (Story 14.3 scope revision).

    Patches `resolve_dem` at its `cli.setup` import site and inspects the captured
    kwargs rather than performing any real network/pipeline work.
    """
    captured: dict[str, object] = {}

    def fake_resolve_dem(*_args: object, **kwargs: object) -> pathlib.Path:
        captured.update(kwargs)
        raise RuntimeError("stop after resolve_dem call")

    runner = CliRunner()
    with (
        mock.patch("steeproute.cli.setup.build_graph_geometry", return_value=mock.MagicMock()),
        mock.patch(
            "steeproute.cli.setup.graph_dem_bounds",
            return_value=(6.10, 45.06, 6.12, 45.08),
        ),
        mock.patch("steeproute.cli.setup.resolve_dem", side_effect=fake_resolve_dem),
    ):
        result = runner.invoke(
            setup_cli,
            [
                "--center",
                "45.0716,6.1079",
                "--radius",
                "1",
                "--cache-dir",
                str(tmp_path),
                "--dem-fetch-workers",
                "2",
            ],
            catch_exceptions=True,
        )
    assert isinstance(result.exception, RuntimeError)
    assert captured["fetch_workers"] == 2


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
