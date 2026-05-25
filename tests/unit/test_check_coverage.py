"""Unit tests for `cache.check_coverage` (Story 2.10).

Two surfaces are exercised here:

1. Pure helpers (`_select_smallest_containing`, `_area_to_polygon`,
   `_no_prepared_cache_message`, `_partial_coverage_message`) — hand-built input,
   no on-disk cache. These are the AC #9 boundary-pinning tests.
2. `check_coverage` over real on-disk cache state — seeded via `write_entry` with
   minimal in-memory graphs. These cover the empty-cache, single-contains, and
   none-contains compositions end-to-end inside this module's scope. e2e tests in
   `tests/e2e/test_coverage_check.py` drive the same scenarios through the real
   query CLI.

The integration tier (`tests/integration/test_cache_coverage.py`) covers the
opportunistic `rebuild_index` path that closes Story 2.7 D1.
"""

# pyright: reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeArgument=false
# Reason: this file exists to pin the boundary semantics of `cache.py`'s pure
# selector + message helpers — they are module-private by convention so other
# call sites don't reach in, but the test tier is the right consumer. The
# `Unknown*` relaxations cover `write_entry`'s `MultiDiGraph[Unknown]` parameter
# (networkx generic-unspecified upstream), same external-boundary pattern as
# `tests/unit/test_cache.py`.

from __future__ import annotations

import pathlib

import networkx as nx
import pytest

from steeproute import cache as cache_mod
from steeproute.cache import (
    Manifest,
    check_coverage,
    write_entry,
)
from steeproute.errors import CacheNotFoundError
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
    """Write a real cache entry with an empty graph — minimum to pass `read_entry`."""
    write_entry(
        cache_root,
        _make_manifest(cache_key_hash=cache_key_hash, area=area),
        nx.MultiDiGraph(),  # pyright: ignore[reportArgumentType, reportMissingTypeArgument]
    )


# --- _area_to_polygon: strict-containment boundary semantics -----------------


def test_area_to_polygon_shape_matches_bounds_geojson_axis_order() -> None:
    """AC #1: the query polygon and the entry polygon use the same axis convention.

    `_bounds_geojson` emits `[lon, lat]` per RFC 7946; `_area_to_polygon` must
    produce a polygon whose `.bounds` reflects the same ordering so containment
    math doesn't silently swap latitude and longitude.
    """
    area = Area(center=(45.0, 6.0), radius_km=2.0)
    poly = cache_mod._area_to_polygon(area)
    # shapely .bounds == (minx, miny, maxx, maxy) where x is longitude.
    minx, miny, maxx, maxy = poly.bounds
    assert minx < 6.0 < maxx
    assert miny < 45.0 < maxy
    # The polygon is centered on the area's (lat, lon). At 45° N, deg-per-km is
    # roughly 1/111 for lat and 1/(111*cos(45°)) ≈ 1/78.5 for lon — so the
    # lon-span is *wider* than the lat-span for the same radius_km.
    lat_span = maxy - miny
    lon_span = maxx - minx
    assert lon_span > lat_span


def test_area_to_polygon_containment_boundary_semantics() -> None:
    """AC #9: pin `shapely.Polygon.contains` semantics for the coverage check.

    `shapely.contains` (DE-9IM `[T*****FF*]`) returns True when no point of the
    inner polygon lies in the outer's exterior. That means:
      - identical polygons → True (a polygon contains itself);
      - inner strictly smaller, same center → True;
      - inner extends outside on any side → False.

    The architecture §Cat 4e "strict containment" wording is about this
    no-points-outside rule, not about boundary-touching. Pinning the semantics
    here so a future shapely upgrade or accidental switch to `.within` /
    `.intersects` is caught immediately.
    """
    entry = cache_mod._area_to_polygon(Area(center=(45.0, 6.0), radius_km=2.0))
    # 1) Identical polygons: a polygon contains itself.
    same = cache_mod._area_to_polygon(Area(center=(45.0, 6.0), radius_km=2.0))
    assert entry.contains(same)
    # 2) Smaller, same center: strictly inside → contained.
    smaller = cache_mod._area_to_polygon(Area(center=(45.0, 6.0), radius_km=1.5))
    assert entry.contains(smaller)
    # 3) Larger at the same center: pokes outside on every side → NOT contained.
    larger = cache_mod._area_to_polygon(Area(center=(45.0, 6.0), radius_km=3.0))
    assert not entry.contains(larger)
    # 4) Shifted off-center such that the inner bbox extends past the entry's
    #    east edge → NOT contained.
    shifted = cache_mod._area_to_polygon(Area(center=(45.0, 6.05), radius_km=1.0))
    assert not entry.contains(shifted)


# --- _select_smallest_containing: pure selection logic ----------------------


def _entry(
    cache_key_hash: str, lat: float, lon: float, radius_km: float
) -> cache_mod._IndexedEntry:
    return cache_mod._IndexedEntry(
        cache_key_hash=cache_key_hash,
        area=Area(center=(lat, lon), radius_km=radius_km),
    )


def test_select_smallest_containing_returns_none_for_empty_index() -> None:
    query = Area(center=(45.0, 6.0), radius_km=1.0)
    assert cache_mod._select_smallest_containing(query, []) is None


def test_select_smallest_containing_returns_single_match_when_only_one_contains() -> None:
    query = Area(center=(45.0, 6.0), radius_km=1.0)
    indexed = [
        # Far away → does not contain.
        _entry("00" * 8, lat=46.0, lon=7.0, radius_km=2.0),
        # Same center, radius 5 → strictly contains the 1-km query.
        _entry("11" * 8, lat=45.0, lon=6.0, radius_km=5.0),
    ]
    chosen = cache_mod._select_smallest_containing(query, indexed)
    assert chosen is not None
    assert chosen.cache_key_hash == "11" * 8


def test_select_smallest_containing_picks_smallest_radius_when_multiple_contain() -> None:
    """AC #5: smaller radius → less graph to load, so prefer the tightest fit."""
    query = Area(center=(45.0, 6.0), radius_km=1.0)
    indexed = [
        _entry("aa" * 8, lat=45.0, lon=6.0, radius_km=10.0),
        _entry("bb" * 8, lat=45.0, lon=6.0, radius_km=3.0),
        _entry("cc" * 8, lat=45.0, lon=6.0, radius_km=5.0),
    ]
    chosen = cache_mod._select_smallest_containing(query, indexed)
    assert chosen is not None
    assert chosen.cache_key_hash == "bb" * 8


def test_select_smallest_containing_tiebreaks_by_cache_key_hash() -> None:
    """AC #5: deterministic tiebreak when two entries have identical radius."""
    query = Area(center=(45.0, 6.0), radius_km=1.0)
    indexed = [
        _entry("ff" * 8, lat=45.0, lon=6.0, radius_km=3.0),
        _entry("00" * 8, lat=45.0, lon=6.0, radius_km=3.0),
        _entry("88" * 8, lat=45.0, lon=6.0, radius_km=3.0),
    ]
    chosen = cache_mod._select_smallest_containing(query, indexed)
    assert chosen is not None
    assert chosen.cache_key_hash == "00" * 8


def test_select_smallest_containing_returns_none_when_query_only_partially_inside() -> None:
    """Query bbox pokes outside the entry bbox → not strictly contained."""
    query = Area(center=(45.0, 6.0), radius_km=2.0)
    # Entry has the same center but a smaller radius — the query is the larger.
    indexed = [_entry("11" * 8, lat=45.0, lon=6.0, radius_km=1.0)]
    assert cache_mod._select_smallest_containing(query, indexed) is None


def test_select_smallest_containing_returns_none_when_query_far_from_all_entries() -> None:
    """Query is entirely outside every entry's bbox."""
    query = Area(center=(45.0, 6.0), radius_km=1.0)
    indexed = [
        _entry("11" * 8, lat=48.0, lon=2.0, radius_km=10.0),
        _entry("22" * 8, lat=43.0, lon=10.0, radius_km=10.0),
    ]
    assert cache_mod._select_smallest_containing(query, indexed) is None


# --- Message formatters ------------------------------------------------------


def test_no_prepared_cache_message_echoes_query_center_and_radius() -> None:
    """AC #3: empty-cache error suggests a directly copy-pasteable setup command.

    Lead phrase distinguishes the empty-cache case ("No prepared cache exists yet.")
    from the partial-coverage case ("No prepared cache covers this area.") so users
    can triage at a glance — pinned here so a future tightening of the wording
    doesn't silently revert this UX (Story 2.10 P5).
    """
    query = Area(center=(45.0716, 6.1079), radius_km=10.5)
    msg = cache_mod._no_prepared_cache_message(query)
    assert msg.startswith("No prepared cache exists yet.")
    assert "steeproute-setup --center 45.0716,6.1079 --radius 10.5" in msg
    assert "--dem-path" in msg


def test_partial_coverage_message_names_nearest_area_and_suggests_smaller_radius() -> None:
    """AC #4: when entries exist but none contain, name the nearest one with actionable hint."""
    query = Area(center=(45.0, 6.0), radius_km=5.0)
    nearest = _entry("11" * 8, lat=45.05, lon=6.05, radius_km=2.0)
    msg = cache_mod._partial_coverage_message(query, nearest)
    assert msg.startswith("No prepared cache covers this area.")
    # Nearest area's center is mentioned for orientation.
    assert "45.05" in msg
    assert "6.05" in msg
    # Either a smaller-radius suggestion or a center-relocation hint must appear.
    assert "--radius" in msg or "--center" in msg


def test_partial_coverage_message_falls_back_to_center_hint_when_query_center_outside() -> None:
    """AC #4: query center outside the nearest entry → suggest narrowing `--center`, not a non-positive radius."""
    query = Area(center=(45.0, 6.0), radius_km=1.0)
    # Nearest is far enough that even radius 0 doesn't fit at the query center.
    nearest = _entry("11" * 8, lat=46.5, lon=6.0, radius_km=0.5)
    msg = cache_mod._partial_coverage_message(query, nearest)
    assert "--center" in msg
    # No bogus "--radius 0" or "--radius -1.2" suggestion.
    assert "--radius 0" not in msg
    assert "--radius -" not in msg


# --- check_coverage end-to-end (in-process, real on-disk seeds) -------------


def test_check_coverage_empty_cache_raises_with_setup_command_suggestion(
    tmp_path: pathlib.Path,
) -> None:
    """AC #3: empty cache dir → CacheNotFoundError with copy-pasteable steeproute-setup hint."""
    query = Area(center=(45.0, 6.0), radius_km=1.0)
    with pytest.raises(CacheNotFoundError) as exc_info:
        _ = check_coverage(tmp_path, query)
    assert exc_info.value.user_message.startswith("No prepared cache exists yet.")
    assert "steeproute-setup --center 45,6" in exc_info.value.user_message
    assert "--radius 1" in exc_info.value.user_message
    assert "--dem-path" in exc_info.value.user_message


def test_check_coverage_single_containing_entry_returns_prepared_data(
    tmp_path: pathlib.Path,
) -> None:
    """AC #1: single strictly-containing entry → its PreparedData is returned."""
    _seed_entry(tmp_path, cache_key_hash="11" * 8, area=Area(center=(45.0, 6.0), radius_km=5.0))
    query = Area(center=(45.0, 6.0), radius_km=1.0)

    result = check_coverage(tmp_path, query)

    assert result.manifest.cache_key_hash == "11" * 8


def test_check_coverage_picks_smallest_radius_when_multiple_contain(
    tmp_path: pathlib.Path,
) -> None:
    """AC #5: with two concentric entries containing the query, the smaller radius is loaded."""
    _seed_entry(tmp_path, cache_key_hash="aa" * 8, area=Area(center=(45.0, 6.0), radius_km=10.0))
    _seed_entry(tmp_path, cache_key_hash="bb" * 8, area=Area(center=(45.0, 6.0), radius_km=3.0))
    query = Area(center=(45.0, 6.0), radius_km=1.0)

    result = check_coverage(tmp_path, query)

    assert result.manifest.cache_key_hash == "bb" * 8
    assert result.manifest.area.radius_km == 3.0


def test_check_coverage_partial_coverage_raises_with_nearest_area_message(
    tmp_path: pathlib.Path,
) -> None:
    """AC #4: query that pokes outside every entry → CacheNotFoundError with nearest-area hint."""
    _seed_entry(tmp_path, cache_key_hash="11" * 8, area=Area(center=(45.0, 6.0), radius_km=2.0))
    # Query is bigger than the entry → bbox pokes out → not strictly contained.
    query = Area(center=(45.0, 6.0), radius_km=5.0)

    with pytest.raises(CacheNotFoundError) as exc_info:
        _ = check_coverage(tmp_path, query)
    msg = exc_info.value.user_message
    assert msg.startswith("No prepared cache covers this area.")
    # Nearest-entry diagnostic names the entry's center + radius verbatim (not
    # just a substring "45" that would match half the float literals in any
    # latitude-bearing message).
    assert "center 45,6" in msg
    assert "radius 2 km" in msg


# --- _read_indexed_entries defensive branches --------------------------------
# Hand-malformed payloads exercise the "return None → caller rebuilds" defenses.
# Real `index.json` payloads from `rebuild_index` never reach these branches,
# but a hand-edited / future-schema-drift / mid-write-corruption payload could.


import json as _json


def _write_index(cache_root: pathlib.Path, payload: object) -> None:
    """Write a literal payload at `index.json` — bypasses `write_json_atomic` on purpose."""
    index_dir = cache_root / "steeproute"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index.json").write_text(_json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    "bad_payload",
    [
        # Top-level is not a dict.
        [{"cache_key_hash": "00" * 8, "area": {}}],
        # Schema version mismatch.
        {"schema_version": 99, "entries": []},
        # `entries` is not a list.
        {"schema_version": 1, "entries": "not-a-list"},
        # `entries` row is not a dict.
        {"schema_version": 1, "entries": ["not-a-dict"]},
        # Row missing `cache_key_hash`.
        {"schema_version": 1, "entries": [{"area": {"center": [45.0, 6.0], "radius_km": 1.0}}]},
        # `area` is not a dict.
        {"schema_version": 1, "entries": [{"cache_key_hash": "00" * 8, "area": "x"}]},
        # `center` is the wrong length.
        {
            "schema_version": 1,
            "entries": [{"cache_key_hash": "00" * 8, "area": {"center": [45.0], "radius_km": 1.0}}],
        },
        # `radius_km` is non-numeric.
        {
            "schema_version": 1,
            "entries": [
                {"cache_key_hash": "00" * 8, "area": {"center": [45.0, 6.0], "radius_km": "huge"}}
            ],
        },
        # `center` elements are non-numeric (TypeError on float()).
        {
            "schema_version": 1,
            "entries": [
                {
                    "cache_key_hash": "00" * 8,
                    "area": {"center": [None, None], "radius_km": 1.0},
                }
            ],
        },
        # Story 2.10 P2: `radius_km` is NaN (would build a polygon with NaN
        # coordinates → shapely raises raw `GEOSException`, violating FR24's
        # exit-2 contract). Defensive parser must reject.
        {
            "schema_version": 1,
            "entries": [
                {
                    "cache_key_hash": "00" * 8,
                    "area": {"center": [45.0, 6.0], "radius_km": float("nan")},
                }
            ],
        },
        # Story 2.10 P2: `radius_km` is +Infinity (would produce a polygon
        # spanning infinite lon — corrupts containment math).
        {
            "schema_version": 1,
            "entries": [
                {
                    "cache_key_hash": "00" * 8,
                    "area": {"center": [45.0, 6.0], "radius_km": float("inf")},
                }
            ],
        },
        # Story 2.10 P2: `radius_km` is negative (inverted-winding polygon
        # with undefined `.contains` semantics).
        {
            "schema_version": 1,
            "entries": [
                {
                    "cache_key_hash": "00" * 8,
                    "area": {"center": [45.0, 6.0], "radius_km": -1.0},
                }
            ],
        },
        # Story 2.10 P2: `radius_km` is `True` — `isinstance(True, int)` is
        # True in Python (bool subclasses int), so the prior `isinstance(...,
        # (int, float))` check would silently coerce to `1.0`.
        {
            "schema_version": 1,
            "entries": [
                {
                    "cache_key_hash": "00" * 8,
                    "area": {"center": [45.0, 6.0], "radius_km": True},
                }
            ],
        },
        # Story 2.10 P2: `center` lat is NaN — would propagate into the polygon.
        {
            "schema_version": 1,
            "entries": [
                {
                    "cache_key_hash": "00" * 8,
                    "area": {"center": [float("nan"), 6.0], "radius_km": 1.0},
                }
            ],
        },
    ],
)
def test_check_coverage_malformed_index_triggers_rebuild(
    tmp_path: pathlib.Path, bad_payload: object
) -> None:
    """Every defensive branch in `_read_indexed_entries` makes the caller rebuild + retry.

    With a real entry under `areas/`, the rebuild rescues the situation and
    `check_coverage` succeeds. Pins each malformed-payload branch as
    rebuild-safe rather than crash-the-CLI.
    """
    _seed_entry(tmp_path, cache_key_hash="cc" * 8, area=Area(center=(45.0, 6.0), radius_km=5.0))
    _write_index(tmp_path, bad_payload)

    result = check_coverage(tmp_path, Area(center=(45.0, 6.0), radius_km=1.0))

    assert result.manifest.cache_key_hash == "cc" * 8


def test_areas_has_valid_entries_returns_false_for_missing_areas_dir(
    tmp_path: pathlib.Path,
) -> None:
    """Cold-edge: cache root exists but has no `areas/` subdirectory yet."""
    assert cache_mod._areas_has_valid_entries(tmp_path) is False


def test_areas_has_valid_entries_returns_false_when_only_tmp_dirs_present(
    tmp_path: pathlib.Path,
) -> None:
    """`.tmp/` and `.old/` directories are not valid entries even if they hold files."""
    areas = tmp_path / "steeproute" / "areas"
    areas.mkdir(parents=True)
    (areas / "abcd1234.tmp").mkdir()
    (areas / "abcd1234.tmp" / "graph.pkl").write_bytes(b"partial")
    # A directory under `areas/` without a manifest doesn't count as a valid entry.
    (areas / "ef567890").mkdir()
    assert cache_mod._areas_has_valid_entries(tmp_path) is False


def test_areas_has_valid_entries_skips_old_dirs_holding_stale_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """Story 2.10 P3: a `.old/` directory with a manifest is a rollback artifact, not a live entry.

    `write_entry`'s rollback path swaps the prior entry into `<hash>.old/` via
    `os.replace(entry_dir, backup_dir)` before the new manifest commit. A
    Ctrl-C in the narrow window before `shutil.rmtree(backup_dir)` leaves a
    `<hash>.old/manifest.json` on disk. Pre-fix, `_areas_has_valid_entries`
    matched only on `manifest.json` presence and would have admitted this
    rollback artifact as a live entry — leading `rebuild_index` to include
    a key for which `areas/<hash>/` no longer exists, surfacing later as an
    inscrutable `CacheNotFoundError` at query time.
    """
    areas = tmp_path / "steeproute" / "areas"
    areas.mkdir(parents=True)
    old_entry = areas / "abcd1234.old"
    old_entry.mkdir()
    # A real-looking manifest inside the `.old/` artifact (matches what the
    # rollback swap would leave on disk).
    (old_entry / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")
    assert cache_mod._areas_has_valid_entries(tmp_path) is False


def test_rebuild_index_skips_old_dirs_holding_stale_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """Story 2.10 P3: `rebuild_index` must also ignore `<hash>.old/` directories.

    Companion to the `_areas_has_valid_entries` test above — the same suffix
    skip applies to the on-disk rebuild walk. Otherwise the rebuilt
    `index.json` would carry a phantom key pointing at a directory that no
    longer exists post-rollback.
    """
    areas = tmp_path / "steeproute" / "areas"
    areas.mkdir(parents=True)
    old_entry = areas / "abcd1234.old"
    old_entry.mkdir()
    # A valid-looking manifest that, pre-fix, would have been admitted into
    # the rebuilt index. Build it via `Manifest.to_dict()` so the parse round
    # trips through real schema validation (the stale-but-not-bogus case).
    stale_manifest = Manifest(
        area=Area(center=(45.0, 6.0), radius_km=1.0),
        untagged_policy="include",
        dem_version="v1",
        pipeline_content_hash="x" * 64,
        osm_extract_date="2024-01-01T00:00:00Z",
        cache_key_hash="abcd1234",
        steeproute_version="0.1.0",
        steeproute_commit="abc",
        created_at="2024-01-01T00:00:00Z",
    )
    (old_entry / "manifest.json").write_text(
        _json.dumps(stale_manifest.to_dict()), encoding="utf-8"
    )

    cache_mod.rebuild_index(tmp_path)
    index_payload = _json.loads(
        (tmp_path / "steeproute" / "index.json").read_text(encoding="utf-8")
    )
    # The rebuilt index must be empty — the `.old/` artifact was skipped.
    assert index_payload["entries"] == []
