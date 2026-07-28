# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# Reason: `click.Choice.choices` is typed as `Sequence[Unknown]` upstream (click's
# generic parameter unspecified) — same external-boundary pattern as cli/query.py.
"""Seam 3 — params-schema introspection for the query config form.

The only place the App reads `steeproute.cli.query`'s click `Command` object.
Introspecting it (rather than hand-listing flags) makes the form/validation
schema the single source of truth (architecture-app.md §Category 9): field
names, types, and choices all come straight from the click `Option` objects
that `cli/_shared.py` already defines, so a CLI flag rename/add/remove is
caught here instead of silently drifting the App's form. Defaults come from
`param.default` for every field except `iter_budget`/`stagnation_iters`,
whose real "unset" resolution isn't expressible as a click default (see
`_UNSET_FLAG_FALLBACKS`).

`QueryParams` (models.py) mirrors the exposed field *names and types* by hand
(FastAPI needs a concrete pydantic model); every field there defaults to
`None` ("unset"), and this module is the single place that resolves an unset
field to its actual default value — the App's quality-demo override where one
applies (AGENTS.md), otherwise the CLI's own default. `build_query_argv`
(argv.py) uses `resolve_query_defaults` so a value is only ever defaulted in
one place.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

import click

from steeproute.cli.query import DEFAULT_ITER_BUDGET
from steeproute.cli.query import cli as _query_cli
from steeproute.solver.grasp import STAGNATION_ITERS_DEFAULT_PLACEHOLDER

FieldType = Literal["float", "int", "string", "bool", "choice"]

# Flags the App owns instead of exposing on the form: the map selection
# (center/radius, and the rotated-rectangle spelling width/height/angle),
# server-controlled paths (output-dir, cache-dir), CLI-operational flags that
# don't belong on a route-param form (verbose, quiet), and click's own
# `--version` eager flag — `@click.version_option` puts it in `cli.params` like
# any other Option, so without excluding it the form renders a bogus "version"
# checkbox.
#
# The schema is a live introspection of `cli.query`, so **every** area flag must
# be listed here or it renders as a stray numeric field on the query form. The
# map owns area selection end to end.
_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "center",
        "radius",
        "width",
        "height",
        "angle",
        "output_dir",
        "cache_dir",
        "verbose",
        "quiet",
        "version",
    }
)

# App-only overrides (AGENTS.md §Solver / GRASP). Every field not listed here
# keeps its CLI default, read straight off `param.default` — keep this dict as
# small as possible, since each entry is a place the App and CLI can disagree.
#
# `max_descent_slope` (0.4) and `start_at_junction` (on) are the only genuine
# divergences. The CLI ships them off/None because they are opt-in flags with a
# real meaning when absent, but the whole point of this tool is steep routes, so
# the App defaults them on. They override the CLI's None/False through the same
# `resolve_query_defaults` seam `build_query_argv` reads, so `argv.py` needs no
# knowledge of them.
_QUALITY_DEFAULTS: dict[str, Any] = {
    "max_descent_slope": 0.4,
    "start_at_junction": True,
}

# `iter_budget`/`stagnation_iters` are NOT literal click-option defaults —
# both flags keep `default=None` at the click level (an explicit "unset"
# sentinel `cli/query.py`/`solver/grasp.py` resolve to a concrete ceiling only
# inside the CLI body, not via click's own default machinery), so introspecting
# `param.default` for either would report `None` instead of the number a bare
# invocation actually runs with. These two read straight from the same
# constants `cli/query.py`/`solver/grasp.py` resolve the unset flag to, so this
# module can't drift from what the plain CLI actually does — the analogue of
# `param.default` for a flag whose real default isn't expressible as one. The
# two constants deliberately come from two different layers — `iter_budget`'s
# fallback (`DEFAULT_ITER_BUDGET`) lives in `cli.query` itself (the module this
# schema already treats as its source of truth); `stagnation_iters`'s
# (`STAGNATION_ITERS_DEFAULT_PLACEHOLDER`) lives in `solver.grasp`, since that's
# where the query CLI's own `None`-resolution logic (`cli/query.py`) reads it
# from — this module simply follows the same indirection, not a new one.
_UNSET_FLAG_FALLBACKS: dict[str, Any] = {
    "iter_budget": DEFAULT_ITER_BUDGET,
    "stagnation_iters": STAGNATION_ITERS_DEFAULT_PLACEHOLDER,
}


@dataclasses.dataclass(frozen=True)
class SchemaField:
    """One form field, derived from a click.Option — never hand-duplicated.

    The form is flat by design: every field renders in one always-visible list, so
    there is deliberately no basic/advanced grouping metadata — that would be
    App-side taxonomy the schema would have to carry, and the schema stays a pure
    introspection of the CLI's click options.
    """

    name: str
    type: FieldType
    default: Any
    help: str | None
    choices: tuple[str, ...] | None = None


def _field_type(param: click.Option) -> FieldType:
    if param.is_flag:
        return "bool"
    if isinstance(param.type, click.Choice):
        return "choice"
    if isinstance(param.type, click.types.FloatParamType):
        return "float"
    if isinstance(param.type, click.types.IntParamType):
        return "int"
    return "string"


def query_params_schema() -> list[SchemaField]:
    """Introspect `steeproute.cli.query`'s click command into form fields.

    Iterates `cli.params` (the click `Option` objects the `@...option`
    decorators attached) rather than importing anything from `cli/_shared.py`
    by name, so a flag rename can't silently desync this from the real CLI
    surface. Excluded fields (area + server-owned + verbosity) are skipped.
    """
    fields: list[SchemaField] = []
    for param in _query_cli.params:
        if not isinstance(param, click.Option):
            continue
        name = param.name
        if name is None or name in _EXCLUDED_FIELDS:
            continue
        choices = tuple(param.type.choices) if isinstance(param.type, click.Choice) else None
        default = _QUALITY_DEFAULTS.get(name, _UNSET_FLAG_FALLBACKS.get(name, param.default))
        fields.append(
            SchemaField(
                name=name,
                type=_field_type(param),
                default=default,
                help=param.help,
                choices=choices,
            )
        )
    return fields


def resolve_query_defaults() -> dict[str, Any]:
    """`{field_name: default_value}` for every exposed query field.

    The single place an unset `QueryParams` field is resolved to its actual
    value (`build_query_argv`'s only source for "what does None mean here").
    """
    return {f.name: f.default for f in query_params_schema()}
