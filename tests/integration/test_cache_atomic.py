# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx/shapely boundary as the underlying pipeline modules.
"""Integration: simulate a mid-write abort, prove no partial entry surfaces.

Architecture §Cat 4d guarantees Ctrl-C during `write_entry` cannot leave an entry
that readers mistake for valid (manifest = commit signal). We test this by
monkeypatching `os.replace` to raise `KeyboardInterrupt` exactly once, at the
point where `graph.pkl` is on disk in the `.tmp/` directory but `manifest.json`
has not yet been written. Story 2.7 AC #11.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib

import networkx as nx
import osmnx
import pytest

from steeproute.cache import (
    Manifest,
    read_entry,
    rebuild_index,
    write_entry,
)
from steeproute.errors import CacheNotFoundError
from steeproute.models import Area, PipelineConfig
from steeproute.pipeline import run_setup_stages
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"


def _load_fixture_constants() -> tuple[float, float, int]:
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen", regen_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CENTER_LAT, module.CENTER_LON, module.DIST_M


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()


def _build_manifest(area: Area, cache_key_hash: str) -> Manifest:
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


@pytest.fixture(scope="module")
def prepared_graph() -> nx.MultiDiGraph:
    """Run the orchestrator once per module."""
    from unittest.mock import patch

    if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; cache atomic test skipped.")

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        return run_setup_stages(area, config)


def test_keyboard_interrupt_before_manifest_leaves_no_valid_entry(
    prepared_graph: nx.MultiDiGraph,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #11: abort after `graph.pkl.tmp` exists but before `manifest.json` lands.

    We wrap `os.replace` so the directory rename (`<hash>.tmp/` → `<hash>/`)
    succeeds, then the manifest's own `os.replace` (`manifest.json.tmp` →
    `manifest.json`) raises `KeyboardInterrupt`. After the abort:
    - The entry directory exists but has no `manifest.json` → `read_entry`
      raises `CacheNotFoundError`.
    - `rebuild_index` does not list the partial entry.
    """
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    cache_key = "0123456789abcdef"
    manifest = _build_manifest(area, cache_key)

    real_replace = os.replace
    call_log: list[str] = []

    def replace_then_interrupt_on_manifest(
        src: str | os.PathLike[str], dst: str | os.PathLike[str]
    ) -> None:
        dst_str = os.fspath(dst)
        call_log.append(dst_str)
        # The fourth `os.replace` in the Cat 4d sequence is the manifest's
        # final commit: `manifest.json.tmp` → `manifest.json`. Match by name
        # so we don't depend on absolute paths.
        if dst_str.endswith("manifest.json"):
            raise KeyboardInterrupt("simulated Ctrl-C before manifest commit")
        real_replace(os.fspath(src), dst_str)

    monkeypatch.setattr("steeproute.cache.os.replace", replace_then_interrupt_on_manifest)

    with pytest.raises(KeyboardInterrupt):
        _ = write_entry(tmp_path, manifest, prepared_graph)

    # No partial entry surfaces — `read_entry` must report not-found.
    with pytest.raises(CacheNotFoundError):
        _ = read_entry(tmp_path, cache_key)

    # Even if `index.json` was never written (the rebuild step never ran), a
    # subsequent `rebuild_index` does not pick up the partial directory.
    # Restore the real `os.replace` first so `rebuild_index` can write the index.
    monkeypatch.setattr("steeproute.cache.os.replace", real_replace)
    rebuild_index(tmp_path)
    payload = json.loads((tmp_path / "steeproute" / "index.json").read_text(encoding="utf-8"))
    assert payload["entries"] == [], f"Aborted entry surfaced in index.json: {payload['entries']!r}"


def test_aborted_overwrite_restores_previous_entry(
    prepared_graph: nx.MultiDiGraph,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1: an interrupted overwrite must restore the prior good entry from `<hash>.old/`.

    Without the rollback, the failed overwrite would leave `<hash>/` without a
    manifest and `<hash>.old/` orphan-staged for deletion on the next
    `write_entry` — silently destroying the previous valid entry.
    """
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    cache_key = "0123456789abcdef"

    # Land a valid v1 entry first (no monkeypatching yet).
    manifest_v1 = Manifest(
        area=area,
        untagged_policy="include",
        dem_version="ign_rge_alti_5m_2024-12",
        pipeline_content_hash="a" * 64,
        osm_extract_date="2026-05-20T12:00:00Z",
        cache_key_hash=cache_key,
        steeproute_version="0.1.0",
        steeproute_commit="aaaaaaaa",
        created_at="2026-05-20T12:00:00Z",
    )
    write_entry(tmp_path, manifest_v1, prepared_graph)

    # Now monkey-patch so the v2 overwrite aborts during the manifest commit.
    real_replace = os.replace

    def replace_then_interrupt_on_manifest(
        src: str | os.PathLike[str], dst: str | os.PathLike[str]
    ) -> None:
        if os.fspath(dst).endswith("manifest.json"):
            raise KeyboardInterrupt("simulated Ctrl-C during overwrite")
        real_replace(os.fspath(src), os.fspath(dst))

    monkeypatch.setattr("steeproute.cache.os.replace", replace_then_interrupt_on_manifest)

    manifest_v2 = Manifest(
        area=area,
        untagged_policy="include",
        dem_version="ign_rge_alti_5m_2025-06",  # would change cache state if it landed
        pipeline_content_hash="a" * 64,
        osm_extract_date="2026-05-20T12:00:00Z",
        cache_key_hash=cache_key,
        steeproute_version="0.1.0",
        steeproute_commit="bbbbbbbb",
        created_at="2026-05-20T12:00:00Z",
    )
    with pytest.raises(KeyboardInterrupt):
        _ = write_entry(tmp_path, manifest_v2, prepared_graph)

    # After the aborted overwrite, the v1 entry must still be readable.
    monkeypatch.setattr("steeproute.cache.os.replace", real_replace)
    loaded = read_entry(tmp_path, cache_key)
    assert loaded.manifest.dem_version == "ign_rge_alti_5m_2024-12", (
        "v1 entry was destroyed by the aborted v2 overwrite — rollback failed."
    )
    assert loaded.manifest.steeproute_commit == "aaaaaaaa"

    # No `.old/` residue should survive a successful rollback.
    areas_dir = tmp_path / "steeproute" / "areas"
    sibling_names = sorted(p.name for p in areas_dir.iterdir())
    assert sibling_names == [cache_key], (
        f"Expected only the entry directory after rollback, got {sibling_names}."
    )


def test_successful_retry_after_aborted_write_produces_clean_entry(
    prepared_graph: nx.MultiDiGraph,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #11: a clean second `write_entry` after an aborted first run produces a valid entry.

    Verifies the per-key cleanup of stale `.tmp/` / `.old/` directories inside
    `write_entry` so a re-prepare after a Ctrl-C isn't blocked by leftover state.
    """
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    cache_key = "0123456789abcdef"
    manifest = _build_manifest(area, cache_key)

    real_replace = os.replace

    def replace_then_interrupt_on_manifest(
        src: str | os.PathLike[str], dst: str | os.PathLike[str]
    ) -> None:
        if os.fspath(dst).endswith("manifest.json"):
            raise KeyboardInterrupt("simulated Ctrl-C before manifest commit")
        real_replace(os.fspath(src), os.fspath(dst))

    monkeypatch.setattr("steeproute.cache.os.replace", replace_then_interrupt_on_manifest)
    with pytest.raises(KeyboardInterrupt):
        _ = write_entry(tmp_path, manifest, prepared_graph)

    # Restore real `os.replace` and re-attempt — second call must succeed.
    monkeypatch.setattr("steeproute.cache.os.replace", real_replace)
    write_entry(tmp_path, manifest, prepared_graph)

    loaded = read_entry(tmp_path, cache_key)
    assert loaded.manifest.cache_key_hash == cache_key
    assert loaded.graph.number_of_edges() == prepared_graph.number_of_edges()
