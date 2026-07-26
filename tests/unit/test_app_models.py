"""Unit tests for `app.models.AreaSpec`'s shape rules (App Story 5.1).

`AreaSpec` is the App's wire/persisted search area. Since CLI Epic 15 it mirrors
that CLI's flag surface: `radius_km` (centered-square shorthand) XOR
`width_km` + `height_km` (full box dimensions), with `angle_deg` rotating either.
These tests pin the exactly-one-of rule at the model level — the API boundary
turns a violation into a 422 (`tests/integration/test_app_api.py`), and
`cli_adapter` relies on the invariant when it builds argv / a CLI `Area`.

`steeproute.cli._shared.resolve_area` remains the authoritative rule; the model
guard exists so a malformed body fails fast instead of becoming a failed job.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from steeproute.app.models import AreaSpec


def test_radius_spelling_is_accepted() -> None:
    area = AreaSpec(center=(45.19, 5.72), radius_km=10.0)
    assert area.radius_km == 10.0
    assert area.width_km is None
    assert area.height_km is None
    assert area.angle_deg == 0.0


def test_width_height_spelling_is_accepted() -> None:
    area = AreaSpec(center=(45.19, 5.72), width_km=16.0, height_km=6.0, angle_deg=35.0)
    assert (area.width_km, area.height_km, area.angle_deg) == (16.0, 6.0, 35.0)
    assert area.radius_km is None


def test_angle_with_radius_is_a_legal_rotated_square() -> None:
    # `--radius R --angle A` is a real shape on the CLI; the App must not treat a
    # bearing as implying explicit extents.
    area = AreaSpec(center=(45.19, 5.72), radius_km=3.0, angle_deg=45.0)
    assert (area.radius_km, area.angle_deg) == (3.0, 45.0)


def test_no_size_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AreaSpec(center=(45.19, 5.72))


def test_angle_alone_does_not_define_an_area() -> None:
    with pytest.raises(ValidationError):
        AreaSpec(center=(45.19, 5.72), angle_deg=35.0)


def test_both_spellings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AreaSpec(center=(45.19, 5.72), radius_km=10.0, width_km=16.0, height_km=6.0)


def test_radius_with_one_dimension_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AreaSpec(center=(45.19, 5.72), radius_km=10.0, width_km=16.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"width_km": 16.0},
        {"height_km": 6.0},
    ],
)
def test_one_lone_dimension_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        AreaSpec(center=(45.19, 5.72), **kwargs)


@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf])
def test_non_positive_or_non_finite_radius_is_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        AreaSpec(center=(45.19, 5.72), radius_km=value)


@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf])
def test_non_positive_or_non_finite_dimension_is_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        AreaSpec(center=(45.19, 5.72), width_km=value, height_km=6.0)


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_non_finite_angle_is_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        AreaSpec(center=(45.19, 5.72), radius_km=10.0, angle_deg=value)


@pytest.mark.parametrize("center", [(91.0, 5.72), (45.19, 181.0), (math.nan, 5.72)])
def test_out_of_range_center_is_rejected(center: tuple[float, float]) -> None:
    # Fail fast at the boundary rather than handing the CLI a degenerate polygon
    # that silently matches nothing (the CLI's own `_validate_center` rule).
    with pytest.raises(ValidationError):
        AreaSpec(center=center, radius_km=10.0)


def test_dimensions_km_reports_effective_full_box_size() -> None:
    square = AreaSpec(center=(45.19, 5.72), radius_km=10.0)
    assert square.dimensions_km == (20.0, 20.0)
    rect = AreaSpec(center=(45.19, 5.72), width_km=16.0, height_km=6.0)
    assert rect.dimensions_km == (16.0, 6.0)


def test_legacy_record_area_still_loads() -> None:
    # A `job.json` written before Story 5.1 carries center + radius_km only.
    area = AreaSpec.model_validate({"center": [45.26, 5.788], "radius_km": 2.0})
    assert area.radius_km == 2.0
    assert area.angle_deg == 0.0
    assert area.dimensions_km == (4.0, 4.0)
