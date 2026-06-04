"""Unit tests for --help and --version output of both CLIs (in-process via CliRunner)."""

import pytest
from click.testing import CliRunner

from steeproute.cli.query import cli as query_cli
from steeproute.cli.setup import cli as setup_cli

QUERY_FLAGS = [
    "--center",
    "--radius",
    "--theta",
    "--min-climb-slope",
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
    "--min-climb-slope",
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


def test_query_help_l_connector_describes_reuse_exemption() -> None:
    """`--l-connector` help reflects the realized FR5 reuse-exemption semantics.

    Guards against regressing to the old "edge-reuse length threshold (short
    connectors vs primary edges)" wording, which described the pre-Epic-5
    directed/drop behaviour. The distinctive phrase below must track the help
    string in `cli/_shared.py::l_connector_option`.
    """
    runner = CliRunner()
    result = runner.invoke(query_cli, ["--help"])
    assert result.exit_code == 0
    # Click reflows help text across lines, so collapse whitespace before matching.
    normalized = " ".join(result.output.split())
    assert "reuse-exemption threshold" in normalized
    assert "in both directions" in normalized


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
