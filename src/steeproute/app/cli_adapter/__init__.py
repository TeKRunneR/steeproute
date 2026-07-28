"""The single CLI-adapter boundary (architecture-app.md §"The load-bearing rule").

ALL coupling to the CLI subsystem lives here: nothing else in the App hand-builds
argv, imports `steeproute.*` internals, reads the cache layout, or knows a stdout
line format. The package owns four seams:

1. argv construction from validated params (`argv.py`)
2. cache-manifest reading for `GET /regions` (`regions.py`)
3. params-schema introspection from the CLI arg parser (`params_schema.py`)
4. stdout line classification into the progress model (`progress_parse.py`)

Import the adapter only through this public interface.
"""

from __future__ import annotations

from steeproute.app.cli_adapter.argv import (
    build_query_argv,
    build_setup_argv,
    resolve_query_executable,
    resolve_setup_executable,
)
from steeproute.app.cli_adapter.params_schema import (
    SchemaField,
    query_params_schema,
    resolve_query_defaults,
)
from steeproute.app.cli_adapter.progress_parse import (
    QueryProgressParser,
    SetupProgressParser,
    parse_summary_objective,
    progress_parser_for,
)
from steeproute.app.cli_adapter.regions import list_regions, resolve_area, to_cli_area

__all__ = [
    "QueryProgressParser",
    "SchemaField",
    "SetupProgressParser",
    "build_query_argv",
    "build_setup_argv",
    "list_regions",
    "parse_summary_objective",
    "progress_parser_for",
    "query_params_schema",
    "resolve_area",
    "resolve_query_defaults",
    "resolve_query_executable",
    "resolve_setup_executable",
    "to_cli_area",
]
