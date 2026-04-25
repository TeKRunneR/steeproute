"""Shared CLI plumbing: verbose flag state and exit-code wrapper."""

import sys
from collections.abc import Callable
from typing import NoReturn

from steeproute.errors import PreExecutionError

_verbose: bool = False


def set_verbose(value: bool) -> None:
    """Set the verbose flag consulted by run_entry_point. Story 1.5 wires --verbose to this."""
    global _verbose
    _verbose = value


def run_entry_point(main_fn: Callable[[], int]) -> NoReturn:
    """Run main_fn with shared exit-code policy (0/1/2/130) and stderr error formatting."""
    try:
        code = main_fn()
    except PreExecutionError as e:
        sys.stderr.write(f"error: {e.user_message}\n")
        if _verbose and e.detail is not None:
            sys.stderr.write(f"        {e.detail}\n")
        code = 2
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
