"""Subprocess-based smoke tests for the installed steeproute / steeproute-setup CLIs.

Exercises the real `[project.scripts]` entry-point shim (not click's CliRunner — the
unit layer covers that). Verifies help/version output, the exit-code-2
`BadCLIArgError → run_entry_point` paths, and the happy paths.

Prerequisite: `uv sync` must have run so `steeproute` and `steeproute-setup` are
installed in the active environment. CI's "Sync dependencies" step satisfies this.
"""

import pathlib
import subprocess

import pytest

# Mirrors tests/unit/test_cli_help.py::QUERY_FLAGS / SETUP_FLAGS — duplication is
# intentional. The two layers verify different things (in-process click structure
# vs. installed-binary stdout); drift between them is a real signal worth a CI fail.
QUERY_FLAGS = [
    "--center",
    "--radius",
    "--theta",
    "--min-climb-slope",
    "--difficulty-cap",
    "--l-connector",
    "--min-climb-ground-length",
    "--elevation-smoothing",
    "--elevation-deadband",
    "--j-max",
    "--n",
    "--untagged-trails",
    "--seed",
    "--iter-budget",
    "--time-budget",
    "--stagnation-iters",
    "--workers",
    "--merge-interval",
    "--progress-interval",
    "--output-dir",
    "--verbose",
    "--quiet",
    "--cache-dir",
    "--version",
    "--help",
]

SETUP_FLAGS = [
    "--center",
    "--radius",
    "--untagged-trails",
    "--verbose",
    "--quiet",
    "--cache-dir",
    "--force-refresh",
    "--dem-version",
    "--dem-fetch-workers",
    "--osm-age-warn-days",
    "--version",
    "--help",
]

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke `uv run <args...>` from the repo root and return the completed process.

    `check=False` so callers can assert on non-zero exit codes; `text=True` decodes
    stdout/stderr to str using the locale's default encoding (sufficient — all
    assertions in this module are over ASCII substrings).
    """
    return subprocess.run(
        ["uv", "run", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )


# --help.
# `--help` output is identical across every flag in a group, so the subprocess runs
# once per CLI (module-scoped) instead of once per parametrized case. This turns ~35
# `uv run --help` invocations (~80 s) into 2 (~5 s).


@pytest.fixture(scope="module")
def query_help() -> subprocess.CompletedProcess[str]:
    return _run_cli("steeproute", "--help")


@pytest.fixture(scope="module")
def setup_help() -> subprocess.CompletedProcess[str]:
    return _run_cli("steeproute-setup", "--help")


@pytest.mark.parametrize("flag", QUERY_FLAGS)
def test_query_help_lists_flag(flag: str, query_help: subprocess.CompletedProcess[str]) -> None:
    assert query_help.returncode == 0, query_help.stderr
    assert flag in query_help.stdout


@pytest.mark.parametrize("flag", SETUP_FLAGS)
def test_setup_help_lists_flag(flag: str, setup_help: subprocess.CompletedProcess[str]) -> None:
    assert setup_help.returncode == 0, setup_help.stderr
    assert flag in setup_help.stdout


# --version.
def test_query_version_exits_zero() -> None:
    result = _run_cli("steeproute", "--version")
    assert result.returncode == 0, result.stderr
    tokens = result.stdout.split()
    assert "steeproute" in tokens[0]
    assert len(tokens) >= 2  # program name + at least a version token


def test_setup_version_exits_zero() -> None:
    result = _run_cli("steeproute-setup", "--version")
    assert result.returncode == 0, result.stderr
    tokens = result.stdout.split()
    assert "steeproute-setup" in tokens[0]
    assert len(tokens) >= 2


# exit-code-2 paths.
def test_query_malformed_center_exits_2() -> None:
    result = _run_cli("steeproute", "--center", "abc,def", "--radius", "10")
    assert result.returncode == 2
    assert result.stderr.startswith("error:")


def test_query_area_cap_flag_removed() -> None:
    """`--area-cap` was deleted outright (not deprecated): unknown-option, exit 2."""
    result = _run_cli("steeproute", "--center", "45.07,6.11", "--radius", "30", "--area-cap", "500")
    assert result.returncode == 2
    assert "no such option" in result.stderr.lower()


def test_query_no_start_at_junction_flag_removed() -> None:
    """`--start-at-junction` is a plain presence flag with no negation spelling.

    `--no-start-at-junction` is not deprecated, it is gone outright — unknown-option,
    exit 2, the same treatment as `--area-cap` above.
    """
    result = _run_cli(
        "steeproute", "--center", "45.07,6.11", "--radius", "30", "--no-start-at-junction"
    )
    assert result.returncode == 2
    assert "no such option" in result.stderr.lower()


# query CLI surface.
def test_query_unprepared_area_exits_2_with_setup_command_suggestion(
    tmp_path: pathlib.Path,
) -> None:
    """FR24: a query against an empty cache exits 2 with an actionable error.

    `cache.check_coverage` raises `CacheNotFoundError` when no prepared cache covers
    the query area, and that maps to exit 2 — *not* to exit 0 with an empty result,
    which is the failure this pins. The CLI is exercised with `--cache-dir <tmp>`
    so the test stays isolated from the user's real cache root.
    """
    result = _run_cli(
        "steeproute",
        "--center",
        "45.0716,6.1079",
        "--radius",
        "10",
        "--cache-dir",
        str(tmp_path),
    )
    assert result.returncode == 2, result.stderr
    # Empty-cache lead distinguishes from partial-coverage lead.
    assert result.stderr.startswith("error: No prepared cache exists yet.")
    assert "steeproute-setup --center 45.0716,6.1079" in result.stderr


def test_query_negative_min_climb_slope_exits_2() -> None:
    """`--min-climb-slope` below 0 → BadCLIArgError → exit 2 (§Cat 10)."""
    result = _run_cli(
        "steeproute",
        "--center",
        "45.0716,6.1079",
        "--radius",
        "10",
        "--min-climb-slope",
        "-0.1",
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("error:")
    assert "--min-climb-slope" in result.stderr


def test_query_zero_workers_exits_2() -> None:
    """`--workers 0` → BadCLIArgError → exit 2 (§Cat 10)."""
    result = _run_cli(
        "steeproute",
        "--center",
        "45.0716,6.1079",
        "--radius",
        "10",
        "--workers",
        "0",
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("error:")
    assert "--workers" in result.stderr


def test_setup_dem_path_flag_removed() -> None:
    """The DEM is auto-downloaded: `--dem-path` is no longer a recognized option."""
    result = _run_cli(
        "steeproute-setup",
        "--center",
        "45.0716,6.1079",
        "--radius",
        "10",
        "--dem-path",
        "anything.tif",
    )
    assert result.returncode == 2, result.stderr
    assert "no such option" in result.stderr.lower()


def test_setup_zero_dem_fetch_workers_exits_2() -> None:
    """`--dem-fetch-workers 0` → BadCLIArgError → exit 2 (§Cat 10)."""
    result = _run_cli(
        "steeproute-setup",
        "--center",
        "45.0716,6.1079",
        "--radius",
        "10",
        "--dem-fetch-workers",
        "0",
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("error:")
    assert "--dem-fetch-workers" in result.stderr
