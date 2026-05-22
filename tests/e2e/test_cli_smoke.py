"""Subprocess-based smoke tests for the installed steeproute / steeproute-setup CLIs.

Exercises the real `[project.scripts]` entry-point shim (not click's CliRunner — the
unit layer covers that). Verifies help/version output, exit-code-2 paths from Story 1.6
(BadCLIArgError → run_entry_point), and the Story 1.5 stub happy paths.

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
    "--difficulty-cap",
    "--l-connector",
    "--min-climb-ground-length",
    "--j-max",
    "--n",
    "--area-cap",
    "--untagged-trails",
    "--seed",
    "--iter-budget",
    "--time-budget",
    "--stagnation-iters",
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
    "--dem-path",
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


# --- Task 2: --help ---


@pytest.mark.parametrize("flag", QUERY_FLAGS)
def test_query_help_lists_flag(flag: str) -> None:
    result = _run_cli("steeproute", "--help")
    assert result.returncode == 0, result.stderr
    assert flag in result.stdout


@pytest.mark.parametrize("flag", SETUP_FLAGS)
def test_setup_help_lists_flag(flag: str) -> None:
    result = _run_cli("steeproute-setup", "--help")
    assert result.returncode == 0, result.stderr
    assert flag in result.stdout


# --- Task 3: --version ---


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


# --- Task 4: exit-code-2 paths ---


def test_query_malformed_center_exits_2() -> None:
    result = _run_cli("steeproute", "--center", "abc,def", "--radius", "10")
    assert result.returncode == 2
    assert result.stderr.startswith("error:")


def test_query_area_cap_exceeded_exits_2() -> None:
    # π·30² ≈ 2827 km² > default --area-cap 500 km²
    result = _run_cli("steeproute", "--center", "45.07,6.11", "--radius", "30")
    assert result.returncode == 2
    assert result.stderr.startswith("error:")
    assert "--area-cap" in result.stderr


# --- Task 5: happy path ---


def test_query_happy_path_exits_0() -> None:
    result = _run_cli("steeproute", "--center", "45.0716,6.1079", "--radius", "10")
    assert result.returncode == 0, result.stderr
    assert "stub" in result.stdout


def test_setup_missing_dem_path_exits_2() -> None:
    """Story 2.8: `--dem-path` is now required at the setup CLI boundary."""
    result = _run_cli("steeproute-setup", "--center", "45.0716,6.1079", "--radius", "10")
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("error:")
    assert "--dem-path" in result.stderr


def test_setup_radius_above_ceiling_exits_2() -> None:
    """Story 2.8: `validate_setup_radius` rejects half-side > 50 km."""
    result = _run_cli(
        "steeproute-setup",
        "--center",
        "45.0716,6.1079",
        "--radius",
        "5000",
        "--dem-path",
        "doesnotmatter.tif",
    )
    assert result.returncode == 2, result.stderr
    assert result.stderr.startswith("error:")
    assert "ceiling" in result.stderr
