# pyright: reportUnknownArgumentType=false, reportMissingTypeArgument=false
# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# Reason: networkx MultiDiGraph generics and shapely's overloaded constructors
# surface as Unknown at the seeding/geometry boundary, same per-file relaxation as
# the other cache tests.
"""Unit tests for `cli_adapter.regions` — the `GET /regions` cache-read seam.

Exercises the seam against a crafted cache root (real `write_entry` with an empty
graph, the pattern the cache-coverage tests use) so no build/network runs. The
injectable `cache_root` keeps the real user cache untouched. The seam maps the
cache's public coverage view (`cache.list_prepared_areas`) into the App's
`RegionInfo`, reusing the cache's own geometry helpers for the polygon + envelope.

Rotated rectangles included: a rotated entry reports its true polygon (derived
from `cache.area_polygon`, the ring coverage is tested against),
its full dimensions and bearing, and `radius_km=None` instead of the inert `0.0` a
rotated CLI `Area` carries. Geometry assertions compare against the cache helpers
themselves so the App can never drift from the CLI's km→deg conversion.
"""

from __future__ import annotations

import pathlib

import networkx as nx
import pytest
import shapely

from steeproute.app.cli_adapter import list_regions, resolve_area, to_cli_area
from steeproute.app.models import AreaSpec, RegionInfo
from steeproute.cache import Manifest, area_bbox_wgs84, area_polygon, write_entry
from steeproute.models import Area

# A rotated 16 x 6 km box at a 35° bearing — the "diagonal range" shape rotated
# areas exist for.
_ROTATED_AREA = Area(
    center=(45.19, 5.72),
    radius_km=0.0,
    half_width_km=8.0,
    half_height_km=3.0,
    angle_deg=35.0,
)


def _make_manifest(*, cache_key_hash: str, area: Area) -> Manifest:
    return Manifest(
        area=area,
        untagged_policy="include",
        dem_version="ign_rge_alti_5m_2024-12",
        pipeline_content_hash="a" * 64,
        osm_extract_date="2026-05-20T12:00:00Z",
        cache_key_hash=cache_key_hash,
        steeproute_version="0.1.0",
        steeproute_commit="abc1234",
        created_at="2026-05-20T12:00:00Z",
    )


def _seed_entry(cache_root: pathlib.Path, *, cache_key_hash: str, area: Area) -> None:
    """Real `write_entry` with an empty graph — enough to register a built region."""
    write_entry(
        cache_root, _make_manifest(cache_key_hash=cache_key_hash, area=area), nx.MultiDiGraph()
    )


def _polygon_latlon(area: Area) -> list[tuple[float, float]]:
    """The cache's own ring as `[lat, lon]` vertices, closing vertex dropped."""
    ring = list(area_polygon(area).exterior.coords)
    return [(lat, lon) for lon, lat in ring[:-1]]


def test_list_regions_empty_cache_returns_empty(tmp_path: pathlib.Path) -> None:
    assert list_regions(cache_root=tmp_path) == []


def test_list_regions_absent_cache_has_no_side_effects(tmp_path: pathlib.Path) -> None:
    # A bare `GET /regions` against a fresh machine must not create the cache tree.
    root = tmp_path / "does-not-exist"
    assert list_regions(cache_root=root) == []
    assert not (root / "steeproute").exists()


def test_list_regions_returns_built_regions(tmp_path: pathlib.Path) -> None:
    _seed_entry(tmp_path, cache_key_hash="ab" * 8, area=Area(center=(45.19, 5.72), radius_km=10.0))
    _seed_entry(tmp_path, cache_key_hash="cd" * 8, area=Area(center=(46.0, 6.1), radius_km=3.0))

    regions = list_regions(cache_root=tmp_path)

    assert all(isinstance(r, RegionInfo) for r in regions)
    assert {r.cache_key_hash for r in regions} == {"ab" * 8, "cd" * 8}
    by_hash = {r.cache_key_hash: r for r in regions}
    grenoble = by_hash["ab" * 8]
    assert grenoble.center == (45.19, 5.72)
    assert grenoble.radius_km == 10.0
    assert (grenoble.width_km, grenoble.height_km, grenoble.angle_deg) == (20.0, 20.0, 0.0)
    # Bounds are exactly the cache's shared conversion — the frontend renders and
    # tests containment against these, so they must not diverge.
    south, west, north, east = area_bbox_wgs84(Area(center=(45.19, 5.72), radius_km=10.0))
    assert (
        grenoble.bounds.south,
        grenoble.bounds.west,
        grenoble.bounds.north,
        grenoble.bounds.east,
    ) == (
        south,
        west,
        north,
        east,
    )
    # Sanity: the bbox brackets the center (south < lat < north, west < lon < east).
    assert grenoble.bounds.south < 45.19 < grenoble.bounds.north
    assert grenoble.bounds.west < 5.72 < grenoble.bounds.east


def test_list_regions_rebuilds_index_when_missing(tmp_path: pathlib.Path) -> None:
    # A deleted index.json (entries still on disk) is recovered — same behavior as
    # the query-side `check_coverage`.
    _seed_entry(tmp_path, cache_key_hash="ef" * 8, area=Area(center=(45.0, 6.0), radius_km=5.0))
    (tmp_path / "steeproute" / "index.json").unlink()

    regions = list_regions(cache_root=tmp_path)

    assert [r.cache_key_hash for r in regions] == ["ef" * 8]


def test_resolve_area_bounds_match_shared_conversion(tmp_path: pathlib.Path) -> None:
    # No cache entries → not covered, but the bbox is still the exact server geometry.
    res = resolve_area(AreaSpec(center=(45.19, 5.72), radius_km=10.0), cache_root=tmp_path)
    assert res.covered is False
    assert res.cache_key_hash is None
    south, west, north, east = area_bbox_wgs84(Area(center=(45.19, 5.72), radius_km=10.0))
    assert (res.bounds.south, res.bounds.west, res.bounds.north, res.bounds.east) == (
        south,
        west,
        north,
        east,
    )


def test_resolve_area_covered_when_inside_built_region(tmp_path: pathlib.Path) -> None:
    _seed_entry(tmp_path, cache_key_hash="ab" * 8, area=Area(center=(45.19, 5.72), radius_km=12.0))
    # A smaller selection at the same center sits strictly inside the r12 region.
    res = resolve_area(AreaSpec(center=(45.19, 5.72), radius_km=10.0), cache_root=tmp_path)
    assert res.covered is True
    assert res.cache_key_hash == "ab" * 8


def test_resolve_area_not_covered_when_outside_built_region(tmp_path: pathlib.Path) -> None:
    _seed_entry(tmp_path, cache_key_hash="ab" * 8, area=Area(center=(45.19, 5.72), radius_km=12.0))
    # Far away from the only built region.
    res = resolve_area(AreaSpec(center=(46.5, 7.0), radius_km=10.0), cache_root=tmp_path)
    assert res.covered is False
    assert res.cache_key_hash is None


# Rotated rectangles.
def test_to_cli_area_halves_full_dimensions_once() -> None:
    # The wire carries FULL box dimensions; `Area` stores half-extents. Getting
    # this backwards silently doubles (or halves) every rotated area.
    area = to_cli_area(AreaSpec(center=(45.19, 5.72), width_km=16.0, height_km=6.0, angle_deg=35.0))
    assert area.center == (45.19, 5.72)
    assert area.half_extents_km == (8.0, 3.0)
    assert area.angle_deg == 35.0
    # A rectangle carries the inert radius the CLI resolver uses.
    assert area.radius_km == 0.0


def test_to_cli_area_keeps_the_square_construction_literal() -> None:
    # `radius_km=r` → the literal square `Area` (extents left None), which is what
    # keeps the fetch call, cache key, and manifest block byte-identical.
    area = to_cli_area(AreaSpec(center=(45.19, 5.72), radius_km=10.0))
    assert (area.radius_km, area.half_width_km, area.half_height_km) == (10.0, None, None)
    assert area.is_square


def test_to_cli_area_supports_a_rotated_square() -> None:
    area = to_cli_area(AreaSpec(center=(45.19, 5.72), radius_km=3.0, angle_deg=45.0))
    assert area.half_extents_km == (3.0, 3.0)
    assert area.angle_deg == 45.0
    assert not area.is_square  # a bearing means it takes the rotated code paths


def test_list_regions_reports_a_rotated_entry_true_polygon(tmp_path: pathlib.Path) -> None:
    _seed_entry(tmp_path, cache_key_hash="ab" * 8, area=_ROTATED_AREA)

    (region,) = list_regions(cache_root=tmp_path)

    # The polygon IS the cache's ring (the one coverage is tested against), as
    # [lat, lon] vertices — not a re-derived approximation.
    assert region.polygon == _polygon_latlon(_ROTATED_AREA)
    assert len(region.polygon) == 4
    # Full dimensions + bearing, and no misleading scalar radius.
    assert (region.width_km, region.height_km, region.angle_deg) == (16.0, 6.0, 35.0)
    assert region.radius_km is None


def test_rotated_region_envelope_over_reports_the_box(tmp_path: pathlib.Path) -> None:
    # The documented envelope leak: `bounds` is still exposed (for the box-only overlay
    # path) but is strictly larger than a rotated box — never a containment test.
    _seed_entry(tmp_path, cache_key_hash="ab" * 8, area=_ROTATED_AREA)

    (region,) = list_regions(cache_root=tmp_path)

    box = shapely.Polygon([(lon, lat) for lat, lon in region.polygon])
    envelope = shapely.box(
        region.bounds.west, region.bounds.south, region.bounds.east, region.bounds.north
    )
    assert envelope.area > box.area
    assert envelope.contains(box)


def test_resolve_area_reports_rotated_selection_geometry(tmp_path: pathlib.Path) -> None:
    res = resolve_area(
        AreaSpec(center=(45.19, 5.72), width_km=16.0, height_km=6.0, angle_deg=35.0),
        cache_root=tmp_path,
    )
    assert res.polygon == _polygon_latlon(_ROTATED_AREA)
    assert (res.width_km, res.height_km, res.angle_deg) == (16.0, 6.0, 35.0)
    assert res.radius_km is None
    assert res.covered is False


def test_resolve_area_covered_by_a_rotated_entry(tmp_path: pathlib.Path) -> None:
    _seed_entry(tmp_path, cache_key_hash="ab" * 8, area=_ROTATED_AREA)
    # A smaller box at the same center and bearing sits strictly inside.
    res = resolve_area(
        AreaSpec(center=(45.19, 5.72), width_km=12.0, height_km=4.0, angle_deg=35.0),
        cache_root=tmp_path,
    )
    assert res.covered is True
    assert res.cache_key_hash == "ab" * 8


def test_resolve_area_inside_rotated_envelope_but_outside_the_box_is_not_covered(
    tmp_path: pathlib.Path,
) -> None:
    # The whole point of orientation-aware containment: a selection tucked into a
    # corner of the rotated entry's ENVELOPE, outside the box itself, is declined.
    _seed_entry(tmp_path, cache_key_hash="ab" * 8, area=_ROTATED_AREA)
    _, west, north, _ = area_bbox_wgs84(_ROTATED_AREA)
    corner = (north - 0.002, west + 0.002)  # NW envelope corner, well off the box
    box = area_polygon(_ROTATED_AREA)
    assert not box.contains(shapely.Point(corner[1], corner[0]))  # (lon, lat)

    res = resolve_area(AreaSpec(center=corner, radius_km=0.1), cache_root=tmp_path)

    assert res.covered is False
    assert res.cache_key_hash is None


def test_resolve_area_rejects_a_malformed_shape(tmp_path: pathlib.Path) -> None:
    # A shape that survives model validation would still be caught by the CLI's own
    # resolver; the seam surfaces that as a ValueError (mapped to 422 by the API).
    with pytest.raises(ValueError):
        resolve_area(AreaSpec.model_construct(center=(45.19, 5.72)), cache_root=tmp_path)
