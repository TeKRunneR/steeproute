"""steeproute query CLI entry point (stages 8-9 + solver; wired in later epics)."""

from typing import NoReturn

from steeproute.cli._shared import run_entry_point


def _main() -> int:
    print("steeproute (query CLI) - stub; full implementation lands in Epics 2-4")
    return 0


def main() -> NoReturn:
    run_entry_point(_main)
