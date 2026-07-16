# pyright: reportUnknownArgumentType=false, reportMissingTypeArgument=false
# Reason: networkx MultiDiGraph generics surface as Unknown at the seeding boundary,
# same per-file relaxation as the other cache tests.
"""Unit tests for `cli_adapter.regions` — the `GET /regions` cache-read seam (App Story 1.6).

Exercises the seam against a crafted cache root (real `write_entry` with an empty
graph, the pattern the cache-coverage tests use) so no build/network runs. The
injectable `cache_root` keeps the real user cache untouched. The seam maps the
cache's public coverage view (`cache.list_prepared_areas`) into the App's
`RegionInfo`, reusing the cache's own km→deg conversion for the bbox.
"""

from __future__ import annotations

import pathlib

import networkx as nx

from steeproute.app.cli_adapter import list_regions, resolve_area
from steeproute.app.models import RegionInfo
from steeproute.cache import Manifest, area_bbox_wgs84, write_entry
from steeproute.models import Area


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
    res = resolve_area((45.19, 5.72), 10.0, cache_root=tmp_path)
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
    res = resolve_area((45.19, 5.72), 10.0, cache_root=tmp_path)
    assert res.covered is True
    assert res.cache_key_hash == "ab" * 8


def test_resolve_area_not_covered_when_outside_built_region(tmp_path: pathlib.Path) -> None:
    _seed_entry(tmp_path, cache_key_hash="ab" * 8, area=Area(center=(45.19, 5.72), radius_km=12.0))
    # Far away from the only built region.
    res = resolve_area((46.5, 7.0), 10.0, cache_root=tmp_path)
    assert res.covered is False
    assert res.cache_key_hash is None
