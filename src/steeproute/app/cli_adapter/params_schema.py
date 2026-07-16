# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# Reason: `click.Choice.choices` is typed as `Sequence[Unknown]` upstream (click's
# generic parameter unspecified) — same external-boundary pattern as cli/query.py.
"""Seam 3 — params-schema introspection for the query config form.

The only place the App reads `steeproute.cli.query`'s click `Command` object.
Introspecting it (rather than hand-listing flags) makes the form/validation
schema the single source of truth (architecture-app.md §Category 9): field
names, types, choices, and CLI defaults all come straight from the click
`Option` objects that `cli/_shared.py` already defines, so a CLI flag
rename/add/remove is caught here instead of silently drifting the App's form.

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

from steeproute.cli.query import cli as _query_cli

FieldType = Literal["float", "int", "string", "bool", "choice"]

# Flags the App owns instead of exposing on the form: the map selection
# (center/radius), server-controlled paths (output-dir, cache-dir),
# CLI-operational flags that don't belong on a route-param form (verbose,
# quiet), and click's own `--version` eager flag (added by
# `@click.version_option`, present in `cli.params` like any other Option —
# caught live in this story's browser drive-through, where it first showed up
# as a bogus "version" checkbox on the rendered form).
_EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {"center", "radius", "output_dir", "cache_dir", "verbose", "quiet", "version"}
)

# Quality-demo overrides (AGENTS.md §Solver / GRASP): the App's defaults are
# the high-quality manual-run params, not the CLI's fast-iteration defaults.
# Every field not listed here keeps its CLI default unchanged.
_QUALITY_DEFAULTS: dict[str, Any] = {
    "iter_budget": 200_000,
    "stagnation_iters": 10_000,
    "difficulty_cap": "T4",
    "elevation_deadband": 1.0,
}

# Basic row = the common knobs a user tunes first; everything else is
# advanced. Click carries no such metadata — this grouping is a UI judgment
# call, not derived (Story app-2-1 Dev Notes).
_BASIC_FIELDS: frozenset[str] = frozenset({"theta", "difficulty_cap", "n", "seed"})


@dataclasses.dataclass(frozen=True)
class SchemaField:
    """One form field, derived from a click.Option — never hand-duplicated."""

    name: str
    type: FieldType
    default: Any
    help: str | None
    group: Literal["basic", "advanced"]
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
        fields.append(
            SchemaField(
                name=name,
                type=_field_type(param),
                default=_QUALITY_DEFAULTS.get(name, param.default),
                help=param.help,
                group="basic" if name in _BASIC_FIELDS else "advanced",
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
