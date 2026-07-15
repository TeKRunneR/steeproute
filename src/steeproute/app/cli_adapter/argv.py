"""Seam 1 — argv construction for the `steeproute-setup` subprocess.

The only place in the App that knows the CLI's flag names. A validated
`SetupParams` + `AreaSpec` become the argv list handed to
`asyncio.create_subprocess_exec` by the worker (architecture-app.md §Category 1,
§"The load-bearing rule").

Deliberately does NOT pass `--cache-dir`: `steeproute-setup` writes to its own
default cache root (`platformdirs.user_cache_dir("steeproute")`), which is the
same on-disk cache the region overlay reads in Story 1.6. A private cache dir
would make built regions invisible to the map (architecture-app.md §Category 6).
"""

from __future__ import annotations

import shutil

from steeproute.app.models import AreaSpec, SetupParams

# The installed console script (pyproject `[project.scripts]`). Resolved against
# the current environment's PATH rather than assumed bare so a non-activated
# venv still finds it.
_SETUP_SCRIPT: str = "steeproute-setup"


def resolve_setup_executable() -> str:
    """Absolute path to the `steeproute-setup` console script.

    Falls back to the bare script name if `which` finds nothing — the worker
    then surfaces the spawn `FileNotFoundError` as a `failed` job rather than
    crashing, which is the honest signal that the App's own package is not
    installed on PATH.
    """
    return shutil.which(_SETUP_SCRIPT) or _SETUP_SCRIPT


def build_setup_argv(
    area: AreaSpec,
    params: SetupParams,
    *,
    executable: str | None = None,
) -> list[str]:
    """Build the `steeproute-setup` argv for a setup job.

    `executable` is injectable so tests can point argv[0] at a fake command while
    still exercising the real flag-mapping logic.
    """
    lat, lon = area.center
    argv: list[str] = [
        executable or resolve_setup_executable(),
        "--center",
        f"{lat},{lon}",
        "--radius",
        _format_number(area.radius_km),
    ]
    # Only emit non-default flags — keeps the command legible and matches the
    # CLI defaults exactly when the App defaults are unchanged.
    if params.untagged_trails != "include":
        argv += ["--untagged-trails", params.untagged_trails]
    if params.force_refresh:
        argv.append("--force-refresh")
    if params.dem_version is not None:
        argv += ["--dem-version", params.dem_version]
    return argv


def _format_number(value: float) -> str:
    """Render a float flag value without a trailing `.0` for whole numbers
    (`2.0 → "2"`, `1.5 → "1.5"`); click's FLOAT parses both."""
    if value == int(value):
        return str(int(value))
    return repr(value)
