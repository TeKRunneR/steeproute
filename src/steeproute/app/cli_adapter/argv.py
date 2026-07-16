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

import pathlib
import shutil
from typing import Any

from steeproute.app.cli_adapter.params_schema import resolve_query_defaults
from steeproute.app.models import AreaSpec, QueryParams, SetupParams

# The installed console script (pyproject `[project.scripts]`). Resolved against
# the current environment's PATH rather than assumed bare so a non-activated
# venv still finds it.
_SETUP_SCRIPT: str = "steeproute-setup"
_QUERY_SCRIPT: str = "steeproute"


def resolve_setup_executable() -> str:
    """Absolute path to the `steeproute-setup` console script.

    Falls back to the bare script name if `which` finds nothing — the worker
    then surfaces the spawn `FileNotFoundError` as a `failed` job rather than
    crashing, which is the honest signal that the App's own package is not
    installed on PATH.
    """
    return shutil.which(_SETUP_SCRIPT) or _SETUP_SCRIPT


def resolve_query_executable() -> str:
    """Absolute path to the `steeproute` (query) console script. See
    `resolve_setup_executable` for the PATH-resolution rationale."""
    return shutil.which(_QUERY_SCRIPT) or _QUERY_SCRIPT


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


def build_query_argv(
    area: AreaSpec,
    params: QueryParams,
    output_dir: pathlib.Path,
    *,
    executable: str | None = None,
) -> list[str]:
    """Build the `steeproute` (query) argv for a query job.

    Every exposed field is always emitted explicitly (unlike
    `build_setup_argv`'s only-non-default style) — with ~20 flags whose App
    default deliberately differs from the CLI default (the quality-demo
    overrides, AGENTS.md), a "skip if default" rule would have to know which
    default applies per-flag and is a needless way to reintroduce the bug this
    story's `resolve_query_defaults` seam exists to prevent. A `None` field
    (unset) resolves to the App's actual default via
    `params_schema.resolve_query_defaults` — the single place that mapping
    lives — except `--seed` and `--max-descent-slope`, whose CLI meaning is
    itself "omit the flag" (unseeded / descent cap disabled), so those two are
    only emitted when actually set.

    `output_dir` MUST be a per-job path — the CLI's own `--output-dir` default
    (`./results`) is relative to the App server's cwd and would collide across
    jobs; callers pass `JobStore.job_dir(job_id) / "result"`.

    `executable` is injectable so tests can point argv[0] at a fake command
    while still exercising the real flag-mapping logic.
    """
    defaults = resolve_query_defaults()

    def resolved(name: str) -> Any:
        value = getattr(params, name)
        return value if value is not None else defaults[name]

    lat, lon = area.center
    argv: list[str] = [
        executable or resolve_query_executable(),
        "--center",
        f"{lat},{lon}",
        "--radius",
        _format_number(area.radius_km),
        "--theta",
        _format_number(resolved("theta")),
        "--min-climb-slope",
        _format_number(resolved("min_climb_slope")),
        "--difficulty-cap",
        str(resolved("difficulty_cap")),
        "--l-connector",
        _format_number(resolved("l_connector")),
        "--min-climb-ground-length",
        _format_number(resolved("min_climb_ground_length")),
        "--elevation-smoothing",
        _format_number(resolved("elevation_smoothing")),
        "--elevation-deadband",
        _format_number(resolved("elevation_deadband")),
        "--j-max",
        _format_number(resolved("j_max")),
        "--n",
        str(resolved("n")),
        "--area-cap",
        _format_number(resolved("area_cap")),
        "--untagged-trails",
        str(resolved("untagged_trails")),
        "--iter-budget",
        str(resolved("iter_budget")),
        "--time-budget",
        _format_number(resolved("time_budget")),
        "--stagnation-iters",
        str(resolved("stagnation_iters")),
        "--workers",
        str(resolved("workers")),
        "--merge-interval",
        str(resolved("merge_interval")),
        "--progress-interval",
        _format_number(resolved("progress_interval")),
        "--osm-age-warn-days",
        str(resolved("osm_age_warn_days")),
        "--output-dir",
        str(output_dir),
    ]
    if resolved("start_at_junction"):
        argv.append("--start-at-junction")
    seed = resolved("seed")
    if seed is not None:
        argv += ["--seed", str(seed)]
    max_descent_slope = resolved("max_descent_slope")
    if max_descent_slope is not None:
        argv += ["--max-descent-slope", _format_number(max_descent_slope)]
    return argv


def _format_number(value: float) -> str:
    """Render a float flag value without a trailing `.0` for whole numbers
    (`2.0 → "2"`, `1.5 → "1.5"`); click's FLOAT parses both."""
    if value == int(value):
        return str(int(value))
    return repr(value)
