"""Unit tests for `cli_adapter.params_schema` — the query form-schema seam
(App Story 2.1).

Pins the introspection contract: excluded fields never leak into the form,
the quality-demo overrides (AGENTS.md) land, and every other field keeps the
CLI's own default — so a `cli/query.py` flag rename/add/remove is caught here
instead of silently drifting the App's config form.
"""

from __future__ import annotations

from steeproute.app.cli_adapter.params_schema import (
    SchemaField,
    query_params_schema,
    resolve_query_defaults,
)
from steeproute.cli.query import cli as query_cli


def _schema_by_name() -> dict[str, SchemaField]:
    return {f.name: f for f in query_params_schema()}


def test_excluded_fields_are_absent() -> None:
    fields = _schema_by_name()
    for excluded in (
        "center",
        "radius",
        "output_dir",
        "cache_dir",
        "verbose",
        "quiet",
        "version",
    ):
        assert excluded not in fields


def test_quality_demo_defaults_override_cli_defaults() -> None:
    fields = _schema_by_name()
    assert fields["iter_budget"].default == 200_000
    assert fields["stagnation_iters"].default == 10_000
    assert fields["difficulty_cap"].default == "T4"
    assert fields["elevation_deadband"].default == 1.0


def test_unmentioned_fields_keep_cli_default() -> None:
    fields = _schema_by_name()
    assert fields["theta"].default == 0.20
    assert fields["n"].default == 5
    assert fields["untagged_trails"].default == "include"
    assert fields["workers"].default == 1


def test_field_types_match_click_option_kinds() -> None:
    fields = _schema_by_name()
    assert fields["theta"].type == "float"
    assert fields["n"].type == "int"
    assert fields["difficulty_cap"].type == "choice"
    assert fields["difficulty_cap"].choices == ("T1", "T2", "T3", "T4", "T5", "T6")
    assert fields["start_at_junction"].type == "bool"


def test_basic_advanced_grouping() -> None:
    fields = _schema_by_name()
    assert fields["theta"].group == "basic"
    assert fields["difficulty_cap"].group == "basic"
    assert fields["n"].group == "basic"
    assert fields["seed"].group == "basic"
    assert fields["workers"].group == "advanced"


def test_schema_field_names_are_real_query_cli_params() -> None:
    # Every schema field name must be a real click param on the query CLI —
    # the introspection can only ever narrow, never invent, field names.
    cli_param_names = {p.name for p in query_cli.params}
    for name in _schema_by_name():
        assert name in cli_param_names


def test_resolve_query_defaults_matches_schema() -> None:
    defaults = resolve_query_defaults()
    fields = _schema_by_name()
    assert defaults.keys() == fields.keys()
    for name, field in fields.items():
        assert defaults[name] == field.default
