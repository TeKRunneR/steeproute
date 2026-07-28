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
        "width",
        "height",
        "angle",
        "output_dir",
        "cache_dir",
        "verbose",
        "quiet",
        "version",
    ):
        assert excluded not in fields


def test_no_area_flag_leaks_onto_the_form() -> None:
    """Story 15.3: the map owns area selection, so no area flag may become a field.

    Stronger than the name-by-name list above: it re-derives the area flags from
    the CLI's own option surface, so a *future* area flag added to `cli/query.py`
    and forgotten in `_EXCLUDED_FIELDS` fails here rather than rendering as a
    stray numeric box on the query form.
    """
    area_flags = {"--center", "--radius", "--width", "--height", "--angle"}
    area_field_names = {
        param.name
        for param in query_cli.params
        if param.name is not None and area_flags & set(param.opts)
    }
    assert area_field_names == {"center", "radius", "width", "height", "angle"}, (
        "the CLI's area surface changed - update this test and `_EXCLUDED_FIELDS`"
    )
    assert not area_field_names & _schema_by_name().keys()


def test_quality_demo_values_now_match_plain_cli_defaults() -> None:
    # As of 2026-07-28 (spec-cli-defaults-and-setup-radius-cap.md) the plain
    # query CLI's own defaults were bumped to match these quality-demo
    # numbers, so `_QUALITY_DEFAULTS` no longer needs to override them — these
    # values now come straight from `cli.query`'s click options, same as any
    # other unmentioned field. The values themselves are unchanged.
    fields = _schema_by_name()
    assert fields["iter_budget"].default == 1_000_000
    assert fields["stagnation_iters"].default == 200_000
    assert fields["difficulty_cap"].default == "T4"
    assert fields["elevation_deadband"].default == 1.0
    assert fields["j_max"].default == 0.0
    assert fields["workers"].default == 4


def test_quality_demo_defaults_still_override_cli_defaults() -> None:
    # Steep-route-tool defaults corrected in Story app-4-2: the CLI ships these
    # off/false (an opt-in flag with a real meaning when absent), but this
    # tool's whole point is steep routes, so the App defaults the descent cap
    # on (0.4) and start-at-junction on. The one genuine, intentional
    # divergence left between the App's and the plain CLI's defaults.
    fields = _schema_by_name()
    assert fields["max_descent_slope"].default == 0.4
    assert fields["start_at_junction"].default is True


def test_unmentioned_fields_keep_cli_default() -> None:
    fields = _schema_by_name()
    assert fields["theta"].default == 0.20
    assert fields["n"].default == 10
    assert fields["untagged_trails"].default == "include"


def test_field_types_match_click_option_kinds() -> None:
    fields = _schema_by_name()
    assert fields["theta"].type == "float"
    assert fields["n"].type == "int"
    assert fields["difficulty_cap"].type == "choice"
    assert fields["difficulty_cap"].choices == ("T1", "T2", "T3", "T4", "T5", "T6")
    assert fields["start_at_junction"].type == "bool"
    # `--max-descent-slope` uses Click's optional-flag-value form
    # (`is_flag=False, flag_value=0.4`) so a bare flag means 0.4; `is_flag=False`
    # is what keeps `_field_type` classifying it as "float", not "bool".
    assert fields["max_descent_slope"].type == "float"


def test_schema_field_carries_no_grouping_metadata() -> None:
    # The form is flat (Story app-4-2): SchemaField exposes no basic/advanced
    # grouping, so it stays a pure introspection of the CLI's click options.
    assert not hasattr(_schema_by_name()["theta"], "group")


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
