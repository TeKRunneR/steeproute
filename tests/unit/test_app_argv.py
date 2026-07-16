"""Unit tests for `cli_adapter.argv` — the setup-argv seam (App Story 1.3).

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


# --- build_query_argv (App Story 2.1) ----------------------------------------


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
    # All-None QueryParams() must NOT fall back to the CLI's own low defaults —
    # the App's quality-demo overrides must be what actually gets passed.
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert argv[argv.index("--iter-budget") + 1] == "1000000"
    assert argv[argv.index("--stagnation-iters") + 1] == "200000"
    assert argv[argv.index("--difficulty-cap") + 1] == "T4"
    assert argv[argv.index("--elevation-deadband") + 1] == "1"
    assert argv[argv.index("--j-max") + 1] == "0"
    assert argv[argv.index("--area-cap") + 1] == "100000"
    assert argv[argv.index("--workers") + 1] == "4"


def test_query_argv_unset_fields_resolve_to_cli_defaults_when_unmentioned() -> None:
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert argv[argv.index("--theta") + 1] == "0.2"
    assert argv[argv.index("--n") + 1] == "5"
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


def test_query_argv_max_descent_slope_omitted_when_unset() -> None:
    argv = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert "--max-descent-slope" not in argv


def test_query_argv_max_descent_slope_included_when_set() -> None:
    argv = _query_argv(
        AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams(max_descent_slope=0.4)
    )
    assert argv[argv.index("--max-descent-slope") + 1] == "0.4"


def test_query_argv_start_at_junction_flag_only_when_true() -> None:
    off = _query_argv(AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams())
    assert "--start-at-junction" not in off
    on = _query_argv(
        AreaSpec(center=(1.0, 2.0), radius_km=1.0), QueryParams(start_at_junction=True)
    )
    assert "--start-at-junction" in on


def test_query_argv_executable_defaults_to_resolved_console_script() -> None:
    argv = build_query_argv(AreaSpec(center=(1.0, 2.0), radius_km=2.0), QueryParams(), _OUT_DIR)
    assert argv[0] == resolve_query_executable()
    stem = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    assert stem in ("steeproute", "steeproute.exe")
