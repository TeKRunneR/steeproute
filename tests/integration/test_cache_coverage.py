# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeArgument=false
# Reason: networkx MultiDiGraph generics same as the rest of cache integration tests.
"""Integration: opportunistic `index.json` rebuild from `check_coverage` (Story 2.10 AC #8).

Closes Story 2.7 D1: a `KeyboardInterrupt` between `manifest.json`'s `os.replace`
and the final `rebuild_index` call inside `write_entry` leaves an `index.json`
that doesn't list the newly-committed entry. The next query-side `check_coverage`
call must notice the discrepancy (parsed-but-empty index while `areas/` has
valid entries) and rebuild before evaluating containment — otherwise the user
gets a phantom "no prepared cache" error for an entry that's actually on disk.

The unit-tier tests in `tests/unit/test_cache_coverage.py` cover the pure
containment logic; this file covers the I/O composition.
"""

from __future__ import annotations

import json
import pathlib

import networkx as nx
import pytest

from steeproute.cache import Manifest, check_coverage, write_entry
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
    """Real `write_entry` with an empty graph — enough for `read_entry` to succeed."""
    write_entry(
        cache_root, _make_manifest(cache_key_hash=cache_key_hash, area=area), nx.MultiDiGraph()
    )


def test_check_coverage_rebuilds_index_when_missing(tmp_path: pathlib.Path) -> None:
    """`index.json` was deleted (e.g. user `rm` or interrupted setup) — `check_coverage` regenerates it."""
    _seed_entry(tmp_path, cache_key_hash="11" * 8, area=Area(center=(45.0, 6.0), radius_km=5.0))
    index_path = tmp_path / "steeproute" / "index.json"
    assert index_path.is_file()
    index_path.unlink()

    result = check_coverage(tmp_path, Area(center=(45.0, 6.0), radius_km=1.0))

    assert result.manifest.cache_key_hash == "11" * 8
    assert index_path.is_file()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert [e["cache_key_hash"] for e in payload["entries"]] == ["11" * 8]


def test_check_coverage_rebuilds_index_when_unparseable(tmp_path: pathlib.Path) -> None:
    """Corrupted `index.json` (mid-write crash, manual edit) — `check_coverage` regenerates it."""
    _seed_entry(tmp_path, cache_key_hash="22" * 8, area=Area(center=(45.0, 6.0), radius_km=5.0))
    index_path = tmp_path / "steeproute" / "index.json"
    index_path.write_text("{not valid json", encoding="utf-8")

    result = check_coverage(tmp_path, Area(center=(45.0, 6.0), radius_km=1.0))

    assert result.manifest.cache_key_hash == "22" * 8
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert [e["cache_key_hash"] for e in payload["entries"]] == ["22" * 8]


def test_check_coverage_rebuilds_when_index_lists_zero_but_areas_has_entries(
    tmp_path: pathlib.Path,
) -> None:
    """Story 2.7 D1: Ctrl-C between manifest commit and `rebuild_index` window.

    `index.json` parses cleanly as an empty list, but `areas/` contains a valid
    entry. `check_coverage` must notice and rebuild — otherwise the user gets
    a phantom "no prepared cache" error for an entry that's actually on disk.
    """
    _seed_entry(tmp_path, cache_key_hash="33" * 8, area=Area(center=(45.0, 6.0), radius_km=5.0))
    index_path = tmp_path / "steeproute" / "index.json"
    # Manually overwrite the (correct) index with an empty-entries variant —
    # this is precisely the on-disk state a Ctrl-C after manifest commit but
    # before `rebuild_index` would leave behind.
    index_path.write_text(
        json.dumps({"schema_version": 1, "entries": []}, indent=2),
        encoding="utf-8",
    )

    result = check_coverage(tmp_path, Area(center=(45.0, 6.0), radius_km=1.0))

    assert result.manifest.cache_key_hash == "33" * 8
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert [e["cache_key_hash"] for e in payload["entries"]] == ["33" * 8]


def test_check_coverage_genuinely_empty_cache_does_not_rebuild_into_phantom_entries(
    tmp_path: pathlib.Path,
) -> None:
    """A genuinely empty cache (no `areas/` entries, no `index.json`) raises empty-cache error.

    Pinning the negative path: rebuild on a fully-empty filesystem must not
    fabricate entries; the user should see the AC #3 message.
    """
    with pytest.raises(CacheNotFoundError) as exc_info:
        _ = check_coverage(tmp_path, Area(center=(45.0, 6.0), radius_km=1.0))
    assert exc_info.value.user_message.startswith("No prepared cache exists yet.")
    assert "steeproute-setup --center" in exc_info.value.user_message


def test_check_coverage_picks_smallest_radius_across_two_real_write_entry_seeds(
    tmp_path: pathlib.Path,
) -> None:
    """AC #5 via real `write_entry × 2` — exercises the I/O composition end-to-end."""
    _seed_entry(tmp_path, cache_key_hash="aa" * 8, area=Area(center=(45.0, 6.0), radius_km=10.0))
    _seed_entry(tmp_path, cache_key_hash="bb" * 8, area=Area(center=(45.0, 6.0), radius_km=3.0))

    result = check_coverage(tmp_path, Area(center=(45.0, 6.0), radius_km=1.0))

    assert result.manifest.cache_key_hash == "bb" * 8
    assert result.manifest.area.radius_km == 3.0
