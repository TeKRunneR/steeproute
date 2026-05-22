"""E2E-layer pytest fixtures."""

from collections.abc import Iterator

import pytest

from steeproute.cli._shared import set_verbose


@pytest.fixture(autouse=True)
def reset_verbose_flag() -> Iterator[None]:
    """Mirror of `tests/unit/conftest.py`'s autouse reset.

    The setup-CLI e2e tests invoke `setup_cli` through `CliRunner`, which routes
    `--verbose` through the eager callback in `cli/_shared.py`. Without this
    reset, a verbose-flagged test would pollute `_verbose=True` into the
    following test's process state and silently change `run_entry_point`'s
    `detail`-line rendering.
    """
    set_verbose(False)
    yield
    set_verbose(False)
