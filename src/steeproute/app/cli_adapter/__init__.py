"""The single CLI-adapter boundary (architecture-app.md §"The load-bearing rule").

ALL coupling to the CLI subsystem lives here: nothing else in the App hand-builds
argv, imports `steeproute.*` internals, reads the cache layout, or knows a stdout
line format. The package owns four seams; Story 1.3 implements only the first:

1. argv construction from validated params  (Story 1.3, `argv.py`)
2. cache-manifest reading for `GET /regions`  ← Story 1.6 (`regions.py`)
3. params-schema introspection from the CLI arg parser  (Epic 2)
4. stdout line classification into the progress model  ← Story 1.4 (`progress_parse.py`, setup flavour)

Import the adapter only through this public interface.
"""

from __future__ import annotations

from steeproute.app.cli_adapter.argv import build_setup_argv, resolve_setup_executable
from steeproute.app.cli_adapter.progress_parse import (
    SetupProgressParser,
    progress_parser_for,
)
from steeproute.app.cli_adapter.regions import list_regions, resolve_area

__all__ = [
    "SetupProgressParser",
    "build_setup_argv",
    "list_regions",
    "progress_parser_for",
    "resolve_area",
    "resolve_setup_executable",
]
