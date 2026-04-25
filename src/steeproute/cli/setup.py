"""steeproute-setup data-preparation CLI entry point (stages 1-7; wired in Epic 2)."""

from typing import NoReturn

from steeproute.cli._shared import run_entry_point


def _main() -> int:
    print("steeproute-setup (data preparation CLI) - stub; full implementation lands in Epic 2")
    return 0


def main() -> NoReturn:
    run_entry_point(_main)
