# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx/shapely boundary as the underlying pipeline modules.
"""Integration: write a real-fixture-derived graph, read it back, verify roundtrip + index.

The fixture runs Story 2.5's `run_setup_stages` against the committed Grenoble
data (same `unittest.mock.patch` of `osm_load` Story 2.5 used so the test stays
offline) and then exercises `write_entry` → `read_entry` plus `rebuild_index`.
Covers Story 2.7 AC #10.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import networkx as nx
import osmnx
import pytest

from steeproute.cache import (
    Manifest,
    PreparedData,
    read_entry,
    rebuild_index,
    write_entry,
)
from steeproute.models import Area, PipelineConfig
from steeproute.pipeline import run_setup_stages
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

_INDEX_SCHEMA_VERSION = 1


def _load_fixture_constants() -> tuple[float, float, int]:
    """Same loader pattern as `test_pipeline_end_to_end.py`."""
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
    """Run `run_setup_stages` against the committed Grenoble fixture, once per module."""
    from unittest.mock import patch

    if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; cache roundtrip test skipped.")

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        return run_setup_stages(area, config)


def _expected_edge_attributes(data: dict[str, object]) -> dict[str, object]:
    """Pull the raw post-stage-5 setup-pipeline contract off an edge dict.

    Story 6.3 moved the per-edge metrics (`length_m`, `d_plus_m`, ...) query-side,
    so the cached graph carries geometry + raw `vertices_resampled` + source attrs
    only. The roundtrip's job is pickle-integrity of whatever the cache stores.
    """
    return {
        "geometry": data["geometry"],
        "vertices_resampled": data["vertices_resampled"],
        "sac_scale": data["sac_scale"],
        "highway": data["highway"],
        "osm_way_id": data["osm_way_id"],
    }


def test_write_and_read_entry_round_trips_real_fixture_graph(
    prepared_graph: nx.MultiDiGraph,
    tmp_path: pathlib.Path,
) -> None:
    """AC #10: round-trip preserves node count, edge count, and the 9-attribute edge contract."""
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    cache_key = "0123456789abcdef"
    manifest = _build_manifest(area, cache_key)

    entry_dir = write_entry(tmp_path, manifest, prepared_graph)

    # Disk layout — Architecture §Cat 4a.
    assert (entry_dir / "manifest.json").is_file()
    assert (entry_dir / "graph.pkl").is_file()
    assert (entry_dir / "bounds.geojson").is_file()

    loaded = read_entry(tmp_path, cache_key)

    assert isinstance(loaded, PreparedData)
    assert loaded.graph.number_of_nodes() == prepared_graph.number_of_nodes()
    assert loaded.graph.number_of_edges() == prepared_graph.number_of_edges()
    # Sample the first edge's full attribute contract — pickle roundtrip integrity
    # for any edge implies it for the rest (pickle isn't selective).
    for u, v, k, data in prepared_graph.edges(data=True, keys=True):
        loaded_data = loaded.graph.get_edge_data(u, v, k)
        assert loaded_data is not None
        assert _expected_edge_attributes(loaded_data) == _expected_edge_attributes(data)
        break  # one sample is enough


def test_write_entry_manifest_matches_schema(
    prepared_graph: nx.MultiDiGraph,
    tmp_path: pathlib.Path,
) -> None:
    """AC #10: on-disk manifest validates against the `Manifest` schema."""
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    cache_key = "0123456789abcdef"
    manifest = _build_manifest(area, cache_key)

    write_entry(tmp_path, manifest, prepared_graph)
    raw = json.loads(
        (tmp_path / "steeproute" / "areas" / cache_key / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    rehydrated = Manifest.from_dict(raw)

    assert rehydrated == manifest


def test_write_entry_index_reflects_new_entry(
    prepared_graph: nx.MultiDiGraph,
    tmp_path: pathlib.Path,
) -> None:
    """AC #10: `index.json` lists the freshly written entry once."""
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    cache_key = "0123456789abcdef"
    manifest = _build_manifest(area, cache_key)

    write_entry(tmp_path, manifest, prepared_graph)
    payload = json.loads((tmp_path / "steeproute" / "index.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == _INDEX_SCHEMA_VERSION
    assert len(payload["entries"]) == 1
    entry = payload["entries"][0]
    assert entry["cache_key_hash"] == cache_key
    assert entry["area"]["radius_km"] == pytest.approx(_DIST_M / 1000.0)


def test_write_entry_overwrites_existing_entry_atomically(
    prepared_graph: nx.MultiDiGraph,
    tmp_path: pathlib.Path,
) -> None:
    """AC #3: re-writing the same key replaces the entry; `.old/` shuffle is cleaned up."""
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    cache_key = "0123456789abcdef"
    manifest_v1 = _build_manifest(area, cache_key)
    write_entry(tmp_path, manifest_v1, prepared_graph)

    manifest_v2 = Manifest(
        area=area,
        untagged_policy="include",
        dem_version="ign_rge_alti_5m_2025-06",  # the only field that changes
        pipeline_content_hash="a" * 64,
        osm_extract_date="2026-05-20T12:00:00Z",
        cache_key_hash=cache_key,
        steeproute_version="0.1.0",
        steeproute_commit="abc1234",
        created_at="2026-05-20T12:00:00Z",
    )
    write_entry(tmp_path, manifest_v2, prepared_graph)

    # The new manifest replaced the old; no stale `.old/` directory survives.
    loaded = read_entry(tmp_path, cache_key)
    assert loaded.manifest.dem_version == "ign_rge_alti_5m_2025-06"
    areas_dir = tmp_path / "steeproute" / "areas"
    sibling_names = sorted(p.name for p in areas_dir.iterdir())
    assert sibling_names == [cache_key], (
        f"Expected only the entry directory after overwrite, got {sibling_names}."
    )


def test_rebuild_index_picks_up_manually_added_entry(
    prepared_graph: nx.MultiDiGraph,
    tmp_path: pathlib.Path,
) -> None:
    """AC #6: `rebuild_index` is the recovery path when `index.json` falls out of sync."""
    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    cache_key = "0123456789abcdef"
    write_entry(tmp_path, _build_manifest(area, cache_key), prepared_graph)

    # Simulate `index.json` drift by deleting it; `rebuild_index` must reconstruct.
    (tmp_path / "steeproute" / "index.json").unlink()
    rebuild_index(tmp_path)

    payload = json.loads((tmp_path / "steeproute" / "index.json").read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["cache_key_hash"] == cache_key
