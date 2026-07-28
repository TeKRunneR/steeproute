"""Unit tests for `cli_adapter.argv` — the setup-argv seam.

The only place the App knows `steeproute-setup`'s flag names; these tests pin the
mapping so a CLI flag rename is caught here (the whole point of the adapter
boundary).
"""

from __future__ import annotations

import pathlib

from steeproute.app.cli_adapter import (
    build_query_argv,
    build_setup_argv,
    resolve_query_executable,
    resolve_setup_executable,
)
from steeproute.app.models import AreaSpec, QueryParams, SetupParams

_EXE = "fake-steeproute-setup"
_QUERY_EXE = "fake-steeproute"
_OUT_DIR = pathlib.Path("/tmp/fake-job/result")


def _argv(area: AreaSpec, params: SetupParams) -> list[str]:
    return build_setup_argv(area, params, executable=_EXE)


def test_minimal_argv_is_center_and_radius() -> None:
    argv = _argv(AreaSpec(center=(45.26, 5.788), radius_km=2.0), SetupParams())
    assert argv == [_EXE, "--center", "45.26,5.788", "--radius", "2"]


def test_defaults_emit_no_optional_flags() -> None:
    # untagged_trails=include and force_refresh=False are the CLI defaults, so the
    # adapter must not emit them (command stays equivalent to a bare CLI call).
    argv = _argv(AreaSpec(center=(1.0, 2.0), radius_km=1.5), SetupParams())
    assert "--untagged-trails" not in argv
    assert "--force-refresh" not in argv
    assert "--dem-version" not in argv


def test_fractional_radius_is_preserved() -> None:
    argv = _argv(AreaSpec(center=(1.0, 2.0), radius_km=1.5), SetupParams())
    assert argv[argv.index("--radius") + 1] == "1.5"


def test_force_refresh_flag() -> None:
    argv = _argv(AreaSpec(center=(1.0, 2.0), radius_km=2.0), SetupParams(force_refresh=True))
    assert "--force-refresh" in argv


def test_untagged_trails_exclude_flag() -> None:
    argv = _argv(
        AreaSpec(center=(1.0, 2.0), radius_km=2.0),
        SetupParams(untagged_trails="exclude"),
    )
    idx = argv.index("--untagged-trails")
    assert argv[idx + 1] == "exclude"


def test_dem_version_flag() -> None:
    argv = _argv(
        AreaSpec(center=(1.0, 2.0), radius_km=2.0),
        SetupParams(dem_version="RGEALTI-2024"),
    )
    idx = argv.index("--dem-version")
    assert argv[idx + 1] == "RGEALTI-2024"


def test_executable_defaults_to_resolved_console_script() -> None:
    # Without an injected executable, argv[0] is whatever the environment resolves
    # (an absolute path when installed, else the bare script name).
    argv = build_setup_argv(AreaSpec(center=(1.0, 2.0), radius_km=2.0), SetupParams())
    assert argv[0] == resolve_setup_executable()
    # Absolute path when installed on PATH (e.g. .venv/Scripts/steeproute-setup.EXE),
    # else the bare script name — matched case-insensitively (Windows uppercases it).
    stem = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    assert stem in ("steeproute-setup", "steeproute-setup.exe")


# Build_query_argv.
def _query_argv(
    area: AreaSpec, params: QueryParams, output_dir: pathlib.Path = _OUT_DIR
) -> list[str]:
    return build_query_argv(area, params, output_dir, executable=_QUERY_EXE)


def test_query_argv_includes_area_and_output_dir() -> None:
    argv = _query_argv(AreaSpec(center=(45.26, 5.788), radius_km=2.0), QueryParams())
    assert argv[0] == _QUERY_EXE
    assert argv[argv.index("--center") + 1] == "45.26,5.788"
    assert argv[argv.index("--radius") + 1] == "2"
    assert argv[argv.index("--output-dir") + 1] == str(_OUT_DIR)


def test_query_argv_unset_fields_resolve_to_quality_demo_defaults() -> None:
    # All-None QueryParams() must resolve to these quality-demo numbers. As of
    # 2026-07-28 these are simply
    # the plain query CLI's own defaults — no more App-side override — but the
    # values themselves, and this test pinning them, are unchanged.
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert argv[argv.index("--iter-budget") + 1] == "1000000"
    assert argv[argv.index("--stagnation-iters") + 1] == "200000"
    assert argv[argv.index("--difficulty-cap") + 1] == "T4"
    assert argv[argv.index("--elevation-deadband") + 1] == "1"
    assert argv[argv.index("--j-max") + 1] == "0"
    assert argv[argv.index("--workers") + 1] == "4"


def test_query_argv_unset_fields_resolve_to_cli_defaults_when_unmentioned() -> None:
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert argv[argv.index("--theta") + 1] == "0.2"
    assert argv[argv.index("--n") + 1] == "10"
    assert argv[argv.index("--untagged-trails") + 1] == "include"


def test_query_argv_explicit_value_overrides_default() -> None:
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams(theta=0.35, n=8))
    assert argv[argv.index("--theta") + 1] == "0.35"
    assert argv[argv.index("--n") + 1] == "8"


def test_query_argv_seed_omitted_when_unset() -> None:
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert "--seed" not in argv


def test_query_argv_seed_included_when_set() -> None:
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams(seed=42))
    assert argv[argv.index("--seed") + 1] == "42"


def test_query_argv_max_descent_slope_defaults_to_quality_value() -> None:
    # The App defaults the descent cap to 0.4 (on) where the CLI ships it `None`
    # (off), so an all-unset QueryParams still emits the flag. Deliberate: this is a
    # steep-route tool.
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert argv[argv.index("--max-descent-slope") + 1] == "0.4"


def test_query_argv_max_descent_slope_included_when_set() -> None:
    argv = _query_argv(
        AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams(max_descent_slope=0.25)
    )
    assert argv[argv.index("--max-descent-slope") + 1] == "0.25"


def test_query_argv_start_at_junction_defaults_on() -> None:
    # The App defaults start-at-junction on where the CLI ships it off, so an unset
    # QueryParams still emits the flag.
    default = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert "--start-at-junction" in default
    explicit = _query_argv(
        AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams(start_at_junction=True)
    )
    assert "--start-at-junction" in explicit


def test_query_argv_executable_defaults_to_resolved_console_script() -> None:
    argv = build_query_argv(AreaSpec(center=(1.0, 2.0), radius_km=2.0), QueryParams(), _OUT_DIR)
    assert argv[0] == resolve_query_executable()
    stem = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    assert stem in ("steeproute", "steeproute.exe")


# Rotated / rectangular areas.
_ROTATED = AreaSpec(center=(45.19, 5.72), width_km=16.0, height_km=6.0, angle_deg=35.0)


def test_setup_argv_emits_full_dimensions_and_bearing() -> None:
    # `--width`/`--height` are FULL box dimensions on the CLI, so they pass through
    # unhalved (halving to Area half-extents happens only at the CLI Area boundary).
    argv = _argv(_ROTATED, SetupParams())
    assert argv == [
        _EXE,
        "--center",
        "45.19,5.72",
        "--width",
        "16",
        "--height",
        "6",
        "--angle",
        "35",
    ]
    assert "--radius" not in argv


def test_query_argv_emits_full_dimensions_and_bearing() -> None:
    argv = _query_argv(_ROTATED, QueryParams())
    assert argv[argv.index("--center") + 1] == "45.19,5.72"
    assert argv[argv.index("--width") + 1] == "16"
    assert argv[argv.index("--height") + 1] == "6"
    assert argv[argv.index("--angle") + 1] == "35"
    assert "--radius" not in argv


def test_axis_aligned_rectangle_omits_the_bearing() -> None:
    # angle 0 is the CLI default; emitting it would be noise (and would make the
    # command differ from what a user would type).
    argv = _argv(AreaSpec(center=(1.0, 2.0), width_km=8.0, height_km=4.5), SetupParams())
    assert "--angle" not in argv
    assert argv[argv.index("--height") + 1] == "4.5"


def test_rotated_square_keeps_the_radius_spelling() -> None:
    # `--radius R --angle A` is a legal CLI shape; a bearing must not force the
    # width/height spelling.
    argv = _argv(AreaSpec(center=(1.0, 2.0), radius_km=3.0, angle_deg=45.0), SetupParams())
    assert argv == [_EXE, "--center", "1.0,2.0", "--radius", "3", "--angle", "45"]


def test_square_argv_is_unchanged_by_the_rotated_surface() -> None:
    # Regression guard: a square area produces exactly the square-shorthand
    # argv — no --angle, no width/height, same flag order.
    setup_argv = _argv(AreaSpec(center=(45.26, 5.788), radius_km=2.0), SetupParams())
    assert setup_argv == [_EXE, "--center", "45.26,5.788", "--radius", "2"]
    query_argv = _query_argv(AreaSpec(center=(45.26, 5.788), radius_km=2.0), QueryParams())
    assert query_argv[:5] == [_QUERY_EXE, "--center", "45.26,5.788", "--radius", "2"]
    assert "--angle" not in query_argv
    assert "--width" not in query_argv
