"""Unit tests for --help and --version output of both CLIs (in-process via CliRunner)."""

import pytest
from click.testing import CliRunner

from steeproute.cli.query import cli as query_cli
from steeproute.cli.setup import cli as setup_cli

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

QUERY_ONLY_FLAGS = [
    "--theta",
    "--difficulty-cap",
    "--l-connector",
    "--min-climb-ground-length",
    "--j-max",
    "--n",
    "--area-cap",
    "--seed",
    "--iter-budget",
    "--time-budget",
    "--stagnation-iters",
    "--progress-interval",
    "--output-dir",
]


@pytest.mark.parametrize("flag", QUERY_FLAGS)
def test_query_help_lists_flag(flag: str) -> None:
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--help"])
    assert result.exit_code == 0
    assert flag in result.output


@pytest.mark.parametrize("flag", SETUP_FLAGS)
def test_setup_help_lists_flag(flag: str) -> None:
    runner = CliRunner()
    result = runner.invoke(setup_cli, ["--help"])
    assert result.exit_code == 0
    assert flag in result.output


@pytest.mark.parametrize("flag", QUERY_ONLY_FLAGS)
def test_setup_help_excludes_query_only_flag(flag: str) -> None:
    runner = CliRunner()
    result = runner.invoke(setup_cli, ["--help"])
    assert result.exit_code == 0
    assert flag not in result.output


def test_query_version_exits_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--version"])
    assert result.exit_code == 0
    assert "steeproute" in result.output


def test_setup_version_exits_zero() -> None:
    runner = CliRunner()
    result = runner.invoke(setup_cli, ["--version"])
    assert result.exit_code == 0
    assert "steeproute-setup" in result.output
