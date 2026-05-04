"""Unit-layer pytest fixtures: shared autouse fixtures for the unit test layer."""

from collections.abc import Iterator

import pytest

from steeproute.cli._shared import set_verbose


@pytest.fixture(autouse=True)
def reset_verbose_flag() -> Iterator[None]:
    """Ensure the module-level _verbose state never leaks between tests."""
    set_verbose(False)
    yield
    set_verbose(False)
