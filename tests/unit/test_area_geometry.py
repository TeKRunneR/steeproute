# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
# Reason: this file pins the boundary semantics of `cache.py`'s module-private
# geometry helpers (`_area_to_polygon`, `_deg_per_km_lon`) and the `Area` model —
# the test tier is the right consumer of these by-convention-private surfaces.
# The `Unknown*` relaxations cover shapely's untyped `.exterior.coords` /
# `.bounds` and pytest's `approx`, same external-boundary pattern as
# `tests/unit/test_check_coverage.py`.
"""Unit tests for the rotated-rectangle `Area` model and geometry helpers (Story 15.1).

Two surfaces:

1. `Area` itself — the square shorthand (`radius_km`) resolves to equal
   half-extents at `angle=0`, indistinguishable from a v1 `Area`.
2. `cache._area_to_polygon` / `cache.area_bbox_wgs84` — the polygon is built in
   a local `cos(lat)` km frame, rotated by `angle_deg`, and converted back to
   WGS84; the `angle=0` path reproduces the pre-Epic-15 square ring byte-for-byte.

`tests/unit/test_check_coverage.py` continues to pin the square-path containment
semantics these helpers feed; this file adds the rotated / non-square coverage.
"""

from __future__ import annotations

import math

import pytest

from steeproute import cache as cache_mod
from steeproute.models import Area

_ROOT2 = math.sqrt(2.0)


# --- Area model: square shorthand backward-compatibility ---------------------


def test_radius_shorthand_resolves_to_equal_half_extents_at_angle_zero() -> None:
    """AC #1: `Area(center, radius_km=r)` is a centered axis-aligned square."""
    area = Area(center=(45.0, 6.0), radius_km=2.0)
    assert area.radius_km == 2.0  # still a readable field for every v1 reader
    assert area.half_extents_km == (2.0, 2.0)
    assert area.angle_deg == 0.0
    assert area.is_square


def test_explicit_extents_override_radius_and_can_be_non_square() -> None:
    """AC #1: an explicit rectangle need not be square, and rotation drops `is_square`."""
    rect = Area(center=(45.0, 6.0), radius_km=0.0, half_width_km=3.0, half_height_km=8.0)
    assert rect.half_extents_km == (3.0, 8.0)
    assert not rect.is_square  # unequal extents
    rotated_square = Area(
        center=(45.0, 6.0), radius_km=0.0, half_width_km=2.0, half_height_km=2.0, angle_deg=30.0
    )
    assert not rotated_square.is_square  # equal extents but rotated


def test_half_extents_recompute_after_dataclasses_replace_of_radius() -> None:
    """`half_extents_km` is derived, so replacing `radius_km` re-derives correctly."""
    import dataclasses

    smaller = dataclasses.replace(Area(center=(45.0, 6.0), radius_km=2.0), radius_km=1.5)
    assert smaller.half_extents_km == (1.5, 1.5)


# --- _area_to_polygon: byte-identical square ring (backward compat) ----------


def test_square_ring_is_byte_identical_to_pre_epic15_formula() -> None:
    """AC #2: `angle=0` equal-extents reproduces today's square ring exactly.

    Rebuilds the pre-Epic-15 ring with the historical formula and asserts the
    generated coordinates match float-for-float — the guard behind the
    no-golden-rebake guarantee for existing `--center/--radius` runs.
    """
    area = Area(center=(45.0, 6.0), radius_km=2.0)
    lat, lon = area.center
    dlat = area.radius_km * cache_mod._DEG_PER_KM_LAT
    dlon = area.radius_km * cache_mod._deg_per_km_lon(lat)
    expected_ring = [
        (lon - dlon, lat - dlat),
        (lon + dlon, lat - dlat),
        (lon + dlon, lat + dlat),
        (lon - dlon, lat + dlat),
        (lon - dlon, lat - dlat),
    ]
    got = list(cache_mod._area_to_polygon(area).exterior.coords)
    assert got == expected_ring


# --- _area_to_polygon: axis-aligned non-square rectangle (fast path) ---------


def test_axis_aligned_rectangle_spans_match_extents() -> None:
    """AC #1/#2: an `angle=0` non-square rectangle uses each extent independently."""
    # At the equator cos(lat)=1 so km→deg is symmetric; lon-span tracks the
    # half-width, lat-span tracks the half-height, with no cross-contamination.
    rect = Area(center=(0.0, 0.0), radius_km=0.0, half_width_km=1.0, half_height_km=3.0)
    minx, miny, maxx, maxy = cache_mod._area_to_polygon(rect).bounds
    lon_span, lat_span = maxx - minx, maxy - miny
    assert lon_span == pytest.approx(2.0 / 111.0)
    assert lat_span == pytest.approx(6.0 / 111.0)


# --- _area_to_polygon: rotation ---------------------------------------------


def test_rotating_a_square_by_90_degrees_preserves_its_envelope() -> None:
    """A square is 90°-symmetric: rotating it must not change its bounds."""
    center = (45.0, 6.0)
    unrotated = cache_mod._area_to_polygon(Area(center=center, radius_km=2.0)).bounds
    rotated = cache_mod._area_to_polygon(
        Area(center=center, radius_km=0.0, half_width_km=2.0, half_height_km=2.0, angle_deg=90.0)
    ).bounds
    assert rotated == pytest.approx(unrotated)


def test_rotated_square_becomes_a_diamond_with_known_vertices() -> None:
    """AC #4: rotation is verified against hand-computed corner coordinates.

    A 1 km-half-side square at the equator (cos(lat)=1) rotated 45° clockwise
    becomes a diamond whose four vertices sit on the axes at distance
    `sqrt(2) km` from the center (each original corner swings onto an axis).
    """
    diamond = cache_mod._area_to_polygon(
        Area(center=(0.0, 0.0), radius_km=0.0, half_width_km=1.0, half_height_km=1.0, angle_deg=45.0)
    )
    corners = list(diamond.exterior.coords)[:-1]  # drop the closing repeat
    reach_deg = _ROOT2 / 111.0
    expected = [
        (-reach_deg, 0.0),  # W
        (0.0, -reach_deg),  # S
        (reach_deg, 0.0),  # E
        (0.0, reach_deg),  # N
    ]

    def _matches(corner: tuple[float, ...], vertex: tuple[float, float]) -> bool:
        return corner[0] == pytest.approx(vertex[0], abs=1e-12) and corner[
            1
        ] == pytest.approx(vertex[1], abs=1e-12)

    assert len(corners) == 4
    # Every generated corner matches exactly one expected diamond vertex.
    for corner in corners:
        assert sum(_matches(corner, vertex) for vertex in expected) == 1


def test_rotation_uses_cos_lat_frame_not_raw_degree_space() -> None:
    """AC #4: the degree-space-skew case — rotation must lift into a km frame.

    At 60° N, cos(lat)=0.5, so one east-km is ~twice the longitude-degrees of one
    north-km. A 45°-rotated square there reaches the same km distance east and
    north, so its longitude reach must be ~2× its latitude reach — which only
    holds if rotation happened in the `cos(lat)` km frame, not raw degrees (where
    the two reaches would be equal).
    """
    diamond = cache_mod._area_to_polygon(
        Area(center=(60.0, 0.0), radius_km=0.0, half_width_km=1.0, half_height_km=1.0, angle_deg=45.0)
    )
    _minx, _miny, maxx, maxy = diamond.bounds
    lon_reach, lat_reach = maxx, maxy - 60.0
    assert lon_reach == pytest.approx(_ROOT2 * cache_mod._deg_per_km_lon(60.0))
    assert lat_reach == pytest.approx(_ROOT2 * cache_mod._DEG_PER_KM_LAT)
    # Raw-degree-space rotation would make these equal; the km frame makes them differ.
    assert lon_reach > lat_reach * 1.5


# --- area_bbox_wgs84: true envelope of the (possibly rotated) polygon ---------


def test_envelope_of_square_coincides_with_the_box() -> None:
    """AC #3: for an axis-aligned square the envelope is the box itself."""
    area = Area(center=(45.0, 6.0), radius_km=2.0)
    south, west, north, east = cache_mod.area_bbox_wgs84(area)
    minx, miny, maxx, maxy = cache_mod._area_to_polygon(area).bounds
    assert (south, west, north, east) == (miny, minx, maxy, maxx)


def test_envelope_of_rotated_box_is_strictly_larger_than_the_box() -> None:
    """AC #3: a rotated rectangle's envelope over-approximates the box.

    The axis-aligned envelope of a 45°-rotated square is the bounding diamond's
    box — `sqrt(2)`× wider/taller than the un-rotated square — so treating the
    envelope as "the region" over-reports coverage. Pinned so the envelope-leak
    audit (Stories 15.2/15.3) has a guard.
    """
    center = (45.0, 6.0)
    square = Area(center=center, radius_km=2.0)
    rotated = Area(center=center, radius_km=0.0, half_width_km=2.0, half_height_km=2.0, angle_deg=45.0)
    s_south, s_west, s_north, s_east = cache_mod.area_bbox_wgs84(square)
    r_south, r_west, r_north, r_east = cache_mod.area_bbox_wgs84(rotated)
    assert (r_east - r_west) > (s_east - s_west)
    assert (r_north - r_south) > (s_north - s_south)
    # Envelope grows by ~sqrt(2) for a 45° rotation.
    assert (r_east - r_west) == pytest.approx((s_east - s_west) * _ROOT2, rel=1e-9)
