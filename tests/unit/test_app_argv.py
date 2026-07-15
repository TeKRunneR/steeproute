"""Unit tests for `cli_adapter.argv` — the setup-argv seam (App Story 1.3).

The only place the App knows `steeproute-setup`'s flag names; these tests pin the
mapping so a CLI flag rename is caught here (the whole point of the adapter
boundary).
"""

from __future__ import annotations

from steeproute.app.cli_adapter import build_setup_argv, resolve_setup_executable
from steeproute.app.models import AreaSpec, SetupParams

_EXE = "fake-steeproute-setup"


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
