"""Unit tests for cache.write_json_atomic, _bounds_geojson, rebuild_index recovery, and resolve_cache_root.

Atomic write + read + entry-overwrite paths are exercised in
`tests/integration/test_cache_roundtrip.py` and `test_cache_atomic.py`; this
file covers the smaller primitives + recovery branches.
"""

# pyright: reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: this tier is the intended consumer of `cache.py`'s module-private
# geometry/parse helpers (`_area_to_polygon`, `_read_indexed_entries`) — they are
# private so other call sites don't reach in, not so tests can't pin them. Same
# rationale as `tests/unit/test_check_coverage.py`. The `reportUnknown*` /
# `reportMissingTypeArgument` relaxations cover the networkx boundary in the
# the payload tests, same pattern as `tests/unit/test_smoothing.py`;
# `reportUnknownParameterType` is the signature-level member of that same group,
# needed because the payload helpers take/return a bare `nx.MultiDiGraph`
# (generic parameter unspecified upstream).

from __future__ import annotations

import dataclasses
import json
import pathlib
import pickle
import re

import networkx as nx
import pytest
import shapely

from steeproute import cache as cache_mod
from steeproute.cache import (
    Manifest,
    PreparedData,
    read_entry,
    rebuild_index,
    resolve_cache_root,
    write_json_atomic,
)
from steeproute.errors import CacheCorruptedError, CacheNotFoundError
from steeproute.models import Area

_INDEX_SCHEMA_VERSION = 1
# Mirrored (not imported) on purpose: a production bump must fail loudly here so
# the on-disk-format change is a deliberate, reviewed edit. v3 is the manifest
# schema carrying the generalized (rotated) `area` block.
_MANIFEST_SCHEMA_VERSION = 3


# Consuming graph-payload build.


def _two_edge_graph() -> nx.MultiDiGraph:
    """A minimal post-stage-5 graph: LineString `geometry` + `vertices_resampled`."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=5.78, y=45.26)
    g.add_node(1, x=5.79, y=45.27)
    g.add_node(2, x=5.80, y=45.28)
    g.add_edge(
        0,
        1,
        key=0,
        geometry=shapely.LineString([(5.78, 45.26), (5.785, 45.265), (5.79, 45.27)]),
        vertices_resampled=[(45.26, 5.78, 600.0), (45.265, 5.785, 610.0), (45.27, 5.79, 620.0)],
        highway="path",
    )
    g.add_edge(
        1,
        2,
        key=0,
        geometry=shapely.LineString([(5.79, 45.27), (5.80, 45.28)]),
        vertices_resampled=[(45.27, 5.79, 620.0), (45.28, 5.80, 640.0)],
        highway="track",
    )
    return g


def _assert_payloads_equal(produced: dict[str, object], expected: dict[str, object]) -> None:
    assert produced.keys() == expected.keys()
    pg, eg = produced["graph"], expected["graph"]
    assert isinstance(pg, nx.MultiDiGraph) and isinstance(eg, nx.MultiDiGraph)
    assert list(pg.nodes(data=True)) == list(eg.nodes(data=True))
    assert list(pg.edges(keys=True, data=True)) == list(eg.edges(keys=True, data=True))


def _legacy_v2_payload(graph: nx.MultiDiGraph) -> dict[str, object]:
    """A schema-v2 payload: the stripped graph plus ragged geometry arrays.

    Mirrors the schema-v2 `_graph_to_payload` output so the legacy read path is
    exercised against the shape that is actually on disk in older entries, rather
    than against a hand-waved approximation of it.
    """
    stripped = graph.copy()
    geometries = [d.pop("geometry") for _u, _v, d in stripped.edges(data=True)]
    _, coords, (offsets,) = shapely.to_ragged_array(geometries)
    return {
        "steeproute_graph_payload": 2,
        "graph": stripped,
        "geometry_coords": coords,
        "geometry_offsets": offsets,
    }


def test_graph_to_payload_consume_matches_the_copying_path() -> None:
    """The consuming payload build is content-identical to the copying one."""
    expected = cache_mod._graph_to_payload(_two_edge_graph())
    produced = cache_mod._graph_to_payload(_two_edge_graph(), consume=True)
    _assert_payloads_equal(produced, expected)


def test_graph_to_payload_default_leaves_caller_geometry_intact() -> None:
    """The copying default keeps its purity contract for every other caller."""
    graph = _two_edge_graph()
    _ = cache_mod._graph_to_payload(graph)
    assert all("geometry" in d for _u, _v, d in graph.edges(data=True))


def test_graph_to_payload_consume_strips_geometry_from_the_caller_graph() -> None:
    """Opting in forfeits `geometry` on the caller's own graph."""
    graph = _two_edge_graph()
    _ = cache_mod._graph_to_payload(graph, consume=True)
    assert all("geometry" not in d for _u, _v, d in graph.edges(data=True))
    # Everything else survives — only geometry is dropped.
    assert all("vertices_resampled" in d for _u, _v, d in graph.edges(data=True))


# Schema-v3 geometry-free payload.


def test_graph_to_payload_writes_no_geometry_parts() -> None:
    """V3 stores the graph and nothing else — no coordinate arrays."""
    payload = cache_mod._graph_to_payload(_two_edge_graph())

    assert payload["steeproute_graph_payload"] == 3
    assert set(payload) == {"steeproute_graph_payload", "graph"}
    graph = payload["graph"]
    assert isinstance(graph, nx.MultiDiGraph)
    assert all("geometry" not in d for _u, _v, d in graph.edges(data=True))
    assert all(len(d["vertices_resampled"]) >= 2 for _u, _v, d in graph.edges(data=True))


def test_graph_from_payload_reads_a_legacy_v2_entry_without_reconstructing_geometry() -> None:
    """A v2 payload still loads; its geometry arrays are ignored, not rebuilt."""
    source = _two_edge_graph()
    legacy = _legacy_v2_payload(source)

    graph = cache_mod._graph_from_payload(legacy, "0123456789abcdef")

    assert list(graph.edges(keys=True)) == list(source.edges(keys=True))
    assert all("geometry" not in d for _u, _v, d in graph.edges(data=True))
    for u, v, k, data in source.edges(keys=True, data=True):
        assert graph.edges[u, v, k]["vertices_resampled"] == data["vertices_resampled"]


def test_graph_from_payload_v2_and_v3_agree_for_every_consumer() -> None:
    """The loaded graph is content-identical across the two payload versions."""
    source = _two_edge_graph()

    from_v2 = cache_mod._graph_from_payload(_legacy_v2_payload(source), "0123456789abcdef")
    from_v3 = cache_mod._graph_from_payload(cache_mod._graph_to_payload(source), "0123456789abcdef")

    assert list(from_v2.nodes(data=True)) == list(from_v3.nodes(data=True))
    assert list(from_v2.edges(keys=True, data=True)) == list(from_v3.edges(keys=True, data=True))


def test_graph_from_payload_rejects_an_unknown_payload_version() -> None:
    """An unrecognized version is corrupt, not silently read."""
    payload = cache_mod._graph_to_payload(_two_edge_graph())
    payload["steeproute_graph_payload"] = 99

    with pytest.raises(CacheCorruptedError, match="incompatible graph payload"):
        _ = cache_mod._graph_from_payload(payload, "0123456789abcdef")


def test_graph_from_payload_rejects_a_non_graph_payload_part() -> None:
    """A payload whose `graph` part isn't a MultiDiGraph is corrupt."""
    with pytest.raises(CacheCorruptedError, match="incompatible graph payload"):
        _ = cache_mod._graph_from_payload(
            {"steeproute_graph_payload": 3, "graph": "not-a-graph"}, "0123456789abcdef"
        )


def test_read_entry_loads_a_legacy_v2_entry_on_disk(tmp_path: pathlib.Path) -> None:
    """Already-prepared v2 caches stay queryable across the format change."""
    area = Area(center=(45.26, 5.78), radius_km=2.0)
    manifest = dataclasses.replace(
        Manifest.from_dict(_manifest_payload()), area=area, cache_key_hash="0" * 16
    )
    entry_dir = tmp_path / "steeproute" / "areas" / manifest.cache_key_hash
    entry_dir.mkdir(parents=True)
    write_json_atomic(entry_dir / "manifest.json", manifest.to_dict())
    (entry_dir / "graph.pkl").write_bytes(
        pickle.dumps(_legacy_v2_payload(_two_edge_graph()), protocol=pickle.HIGHEST_PROTOCOL)
    )

    loaded = read_entry(tmp_path, manifest.cache_key_hash)

    assert loaded.graph.number_of_edges() == 2
    assert all("geometry" not in d for _u, _v, d in loaded.graph.edges(data=True))


@pytest.mark.parametrize("consume", [False, True])
def test_graph_to_payload_rejects_non_linestring_geometry_on_both_paths(consume: bool) -> None:
    """The post-stage-5 contract check is identical on both paths."""
    graph = _two_edge_graph()
    graph.edges[0, 1, 0]["geometry"] = "not-a-linestring"
    with pytest.raises(ValueError, match="requires a shapely LineString"):
        _ = cache_mod._graph_to_payload(graph, consume=consume)


def test_write_entry_consume_round_trips_to_the_same_graph(tmp_path: pathlib.Path) -> None:
    """An entry written the consuming way reads back identical to the copying way.

    Content equality, not `graph.pkl` byte equality: networkx caches its lazily
    built `adj` / `succ` / `edges` view objects in the graph's `__dict__`, and
    `MultiDiGraph.copy()` materializes a different subset of them than the graph
    the setup CLI hands over — so the pickled bytes carry incidental view
    artifacts that say nothing about the cached data. What must match is what
    `read_entry` gives a query.
    """
    area = Area(center=(45.26, 5.78), radius_km=2.0)
    manifest = dataclasses.replace(
        Manifest.from_dict(_manifest_payload()), area=area, cache_key_hash="0" * 16
    )
    copying_root = tmp_path / "copying"
    consuming_root = tmp_path / "consuming"

    _ = cache_mod.write_entry(copying_root, manifest, _two_edge_graph())
    consumed_graph = _two_edge_graph()
    _ = cache_mod.write_entry(consuming_root, manifest, consumed_graph, consume=True)

    expected = read_entry(copying_root, manifest.cache_key_hash).graph
    produced = read_entry(consuming_root, manifest.cache_key_hash).graph
    assert list(produced.nodes(data=True)) == list(expected.nodes(data=True))
    assert list(produced.edges(keys=True)) == list(expected.edges(keys=True))
    for u, v, k, data in expected.edges(keys=True, data=True):
        got = produced.edges[u, v, k]
        assert got == data
        assert got["vertices_resampled"] == data["vertices_resampled"]
    assert all("geometry" not in d for _u, _v, d in consumed_graph.edges(data=True))


# Write_json_atomic.
def test_write_json_atomic_creates_target_with_expected_content(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "index.json"
    write_json_atomic(target, {"entries": [], "schema_version": _INDEX_SCHEMA_VERSION})

    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload == {"entries": [], "schema_version": _INDEX_SCHEMA_VERSION}


def test_write_json_atomic_replaces_existing_target(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "index.json"
    target.write_text('{"old": true}', encoding="utf-8")

    write_json_atomic(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


def test_write_json_atomic_leaves_no_tmp_artifact(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "manifest.json"
    write_json_atomic(target, {"k": 1})
    # The `.tmp` sibling must have been os.replaced into place — no lingering artifacts.
    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert siblings == ["manifest.json"]


def test_write_json_atomic_emits_sorted_keys(tmp_path: pathlib.Path) -> None:
    """Deterministic output enables diff-stable cache state across runs."""
    target = tmp_path / "manifest.json"
    write_json_atomic(target, {"z": 1, "a": 2, "m": 3})

    raw = target.read_text(encoding="utf-8")
    assert raw.index('"a"') < raw.index('"m"') < raw.index('"z"')


def test_write_json_atomic_chokepoint_no_direct_writes_in_cache_module() -> None:
    """`cache.py` contains no direct `open(..., "w")` on JSON files.

    Verified by AST-walking the module — every JSON write must route through
    `write_json_atomic` per Architecture §Key anti-patterns.
    """
    import ast

    source = pathlib.Path(cache_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "open"):
            continue
        # `open(path, "w")` or `open(path, mode="w")` → flag as a direct-write call site.
        positional_mode = (
            len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and "w" in node.args[1].value
        )
        kw_mode = any(
            kw.arg == "mode"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
            and "w" in kw.value.value
            for kw in node.keywords
        )
        assert not (positional_mode or kw_mode), (
            f"Direct `open(..., 'w')` write at line {node.lineno} in cache.py — "
            "All cache JSON writes must route through `write_json_atomic`."
        )


# Rebuild_index recovery.
_DEFAULT_AREA = Area(center=(45.0, 6.0), radius_km=2.0)


def _write_entry_dir(
    cache_root: pathlib.Path,
    cache_key_hash: str,
    *,
    area: Area = _DEFAULT_AREA,
) -> pathlib.Path:
    """Build a minimal entry directory with just a manifest — enough for rebuild_index."""
    areas_dir = cache_root / "steeproute" / "areas"
    areas_dir.mkdir(parents=True, exist_ok=True)
    entry_dir = areas_dir / cache_key_hash
    entry_dir.mkdir()
    manifest = Manifest(
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
    write_json_atomic(entry_dir / "manifest.json", manifest.to_dict())
    return entry_dir


def test_rebuild_index_creates_index_when_missing(tmp_path: pathlib.Path) -> None:
    _write_entry_dir(tmp_path, "0123456789abcdef")

    rebuild_index(tmp_path)

    index_path = tmp_path / "steeproute" / "index.json"
    assert index_path.exists()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == _INDEX_SCHEMA_VERSION
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["cache_key_hash"] == "0123456789abcdef"


def test_rebuild_index_overwrites_corrupt_index(tmp_path: pathlib.Path) -> None:
    _write_entry_dir(tmp_path, "0123456789abcdef")
    index_path = tmp_path / "steeproute" / "index.json"
    index_path.write_text("{not valid json", encoding="utf-8")

    rebuild_index(tmp_path)

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == _INDEX_SCHEMA_VERSION
    assert len(payload["entries"]) == 1


def test_rebuild_index_skips_dirs_without_manifest(tmp_path: pathlib.Path) -> None:
    """Directories without `manifest.json` (`.tmp/`, `.old/`, half-written) are ignored."""
    areas_dir = tmp_path / "steeproute" / "areas"
    areas_dir.mkdir(parents=True)
    (areas_dir / "fedcba9876543210.tmp").mkdir()
    (areas_dir / "fedcba9876543210.tmp" / "graph.pkl").write_bytes(b"partial")
    (areas_dir / "0000111122223333.old").mkdir()
    # And one valid entry alongside the two non-entries.
    _write_entry_dir(tmp_path, "0123456789abcdef")

    rebuild_index(tmp_path)

    payload = json.loads((tmp_path / "steeproute" / "index.json").read_text(encoding="utf-8"))
    assert [e["cache_key_hash"] for e in payload["entries"]] == ["0123456789abcdef"]


def test_rebuild_index_emits_entries_sorted_by_cache_key_hash(tmp_path: pathlib.Path) -> None:
    """Deterministic entry order → diff-stable index.json."""
    _write_entry_dir(tmp_path, "ffffffffffffffff")
    _write_entry_dir(tmp_path, "0000000000000000")
    _write_entry_dir(tmp_path, "8888888888888888")

    rebuild_index(tmp_path)

    payload = json.loads((tmp_path / "steeproute" / "index.json").read_text(encoding="utf-8"))
    hashes = [e["cache_key_hash"] for e in payload["entries"]]
    assert hashes == sorted(hashes)


def test_rebuild_index_creates_empty_index_when_no_entries(tmp_path: pathlib.Path) -> None:
    """Bootstrap case: `areas/` exists but is empty."""
    (tmp_path / "steeproute" / "areas").mkdir(parents=True)

    rebuild_index(tmp_path)

    payload = json.loads((tmp_path / "steeproute" / "index.json").read_text(encoding="utf-8"))
    assert payload == {"schema_version": _INDEX_SCHEMA_VERSION, "entries": []}


def test_rebuild_index_bootstraps_missing_areas_directory(tmp_path: pathlib.Path) -> None:
    """First-run case (Architecture §Operational details): cache root has no `areas/` yet."""
    rebuild_index(tmp_path)

    index_path = tmp_path / "steeproute" / "index.json"
    assert index_path.exists()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert payload == {"schema_version": _INDEX_SCHEMA_VERSION, "entries": []}


# Manifest.from_dict + PreparedData.
def _manifest_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "area": {"mode": "center_radius", "center": [45.0716, 6.1079], "radius_km": 2.0},
        "untagged_policy": "include",
        "dem_version": "ign_rge_alti_5m_2024-12",
        "pipeline_content_hash": "a" * 64,
        "osm_extract_date": "2026-05-20T12:00:00Z",
        "cache_key_hash": "0123456789abcdef",
        "steeproute_version": "0.1.0",
        "steeproute_commit": "abc1234",
        "created_at": "2026-05-20T12:00:00Z",
    }
    base.update(overrides)
    return base


def test_manifest_from_dict_round_trips_to_dict_output() -> None:
    payload = _manifest_payload()
    manifest = Manifest.from_dict(payload)

    assert manifest.to_dict() == payload


def test_manifest_from_dict_raises_on_unknown_schema_version() -> None:
    payload = _manifest_payload(schema_version=99)

    with pytest.raises(CacheCorruptedError) as exc_info:
        Manifest.from_dict(payload)
    assert "schema version" in exc_info.value.user_message
    assert (
        exc_info.value.detail is not None
        and f"schema_version={_MANIFEST_SCHEMA_VERSION}" in exc_info.value.detail
    )


@pytest.mark.parametrize("legacy_version", [1, 2])
def test_manifest_from_dict_raises_on_legacy_schema_version(legacy_version: int) -> None:
    """Every superseded schema is rejected with the re-prepare hint, no compat shim.

    v1 → v2 changed the graph payload format; v2 → v3 generalized the
    `area` block to the rotated rectangle. Architecture
    §Versioned-contract-surfaces takes the same line for both: a stale entry
    re-prepares once via the existing recovery paths (query: exit 2 with the
    actionable message; setup: re-prepare-as-recovery).
    """
    payload = _manifest_payload(schema_version=legacy_version)

    with pytest.raises(CacheCorruptedError) as exc_info:
        Manifest.from_dict(payload)
    assert "schema version" in exc_info.value.user_message
    assert exc_info.value.detail is not None and "--force-refresh" in exc_info.value.detail


def test_manifest_from_dict_raises_on_missing_schema_version() -> None:
    payload = _manifest_payload()
    del payload["schema_version"]

    with pytest.raises(CacheCorruptedError):
        Manifest.from_dict(payload)


def test_manifest_from_dict_raises_on_malformed_area() -> None:
    payload = _manifest_payload(area="not-a-dict")

    with pytest.raises(CacheCorruptedError) as exc_info:
        Manifest.from_dict(payload)
    assert "area" in exc_info.value.user_message


def test_manifest_from_dict_raises_on_missing_required_field() -> None:
    payload = _manifest_payload()
    del payload["dem_version"]

    with pytest.raises(CacheCorruptedError) as exc_info:
        Manifest.from_dict(payload)
    assert exc_info.value.detail is not None and "dem_version" in exc_info.value.detail


# Read_entry error paths.
def test_read_entry_raises_cache_not_found_when_manifest_missing(tmp_path: pathlib.Path) -> None:
    """An entry directory without a `manifest.json` is treated as absent."""
    (tmp_path / "steeproute" / "areas" / "0123456789abcdef").mkdir(parents=True)

    with pytest.raises(CacheNotFoundError, match="0123456789abcdef"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_read_entry_raises_cache_not_found_for_unknown_key(tmp_path: pathlib.Path) -> None:
    with pytest.raises(CacheNotFoundError, match="ffffffffffffffff"):
        _ = read_entry(tmp_path, "ffffffffffffffff")


def test_read_entry_raises_cache_corrupted_on_malformed_manifest_json(
    tmp_path: pathlib.Path,
) -> None:
    """Unparseable `manifest.json` surfaces as `CacheCorruptedError`."""
    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    (entry_dir / "manifest.json").write_text("{not valid", encoding="utf-8")

    with pytest.raises(CacheCorruptedError, match="unreadable manifest"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_read_entry_raises_cache_corrupted_on_missing_graph_pkl(tmp_path: pathlib.Path) -> None:
    """Manifest present + graph.pkl missing → `CacheCorruptedError`."""
    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    write_json_atomic(entry_dir / "manifest.json", _manifest_payload())

    with pytest.raises(CacheCorruptedError, match="unreadable graph"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_read_entry_raises_cache_corrupted_on_legacy_graph_payload(
    tmp_path: pathlib.Path,
) -> None:
    """A v2+ manifest over a raw pickled graph (no payload dict) is corrupt.

    Unreachable through normal writes (the manifest version and the payload
    format move together), but a hand-assembled or half-converted entry must
    surface as `CacheCorruptedError`, not leak a raw graph or crash on a dict
    lookup.
    """
    import pickle

    import networkx as nx

    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    write_json_atomic(entry_dir / "manifest.json", _manifest_payload())
    legacy_graph = nx.MultiDiGraph()  # pyright: ignore[reportMissingTypeArgument, reportUnknownVariableType]
    (entry_dir / "graph.pkl").write_bytes(
        pickle.dumps(legacy_graph, protocol=pickle.HIGHEST_PROTOCOL)
    )

    with pytest.raises(CacheCorruptedError, match="graph payload"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_prepared_data_is_frozen() -> None:
    import networkx as nx

    manifest = Manifest.from_dict(_manifest_payload())
    graph = nx.MultiDiGraph()  # pyright: ignore[reportMissingTypeArgument, reportUnknownVariableType]
    prepared = PreparedData(graph=graph, manifest=manifest)

    with pytest.raises(dataclasses.FrozenInstanceError):
        prepared.manifest = manifest  # pyright: ignore[reportAttributeAccessIssue]


# Resolve_cache_root.
def test_resolve_cache_root_returns_override_when_provided(tmp_path: pathlib.Path) -> None:
    """Explicit `--cache-dir` bypasses the platformdirs default."""
    resolved = resolve_cache_root(tmp_path)
    assert resolved == tmp_path


def test_resolve_cache_root_returns_platformdirs_default_when_no_override() -> None:
    """`None` → `platformdirs.user_cache_dir("steeproute")`."""
    default_root = resolve_cache_root(None)
    # No strict-string assertion — platformdirs picks platform-specific paths
    # (`%LOCALAPPDATA%\\steeproute\\Cache\\` on Windows, `~/.cache/steeproute`
    # on Linux). We sanity-check the result is a `Path` ending with the app name.
    assert isinstance(default_root, pathlib.Path)
    assert re.search(r"[\\/](?i:steeproute)([\\/]Cache)?$", str(default_root)) is not None


# `entry_dir_for`.


def test_entry_dir_for_matches_write_entry_layout(tmp_path: pathlib.Path) -> None:
    """`entry_dir_for` returns the same path that `write_entry` would produce.

    Single source of truth for the `<cache-root>/steeproute/areas/<hash>/` layout
    (Architecture §Cat 4a). External callers (`cli/setup.py`'s cache-hit summary)
    use this rather than reconstructing the path by string concatenation. If the
    layout ever changes, both `write_entry` and `entry_dir_for` move in lockstep.
    """
    from steeproute.cache import entry_dir_for

    cache_key = "fedcba9876543210"
    result = entry_dir_for(tmp_path, cache_key)
    # Expected layout: `<cache-root>/steeproute/areas/<cache-key>/`.
    assert result == tmp_path / "steeproute" / "areas" / cache_key


# Bounds.geojson axis-order consistency.
def test_bounds_geojson_geometry_and_properties_center_use_lon_lat_consistently(
    tmp_path: pathlib.Path,
) -> None:
    """`properties.center` and `geometry.coordinates` must agree on axis order.

    GeoJSON RFC 7946 mandates `[lon, lat]` in `geometry.coordinates`; the
    properties block follows the same convention so a consumer reading both
    fields doesn't get contradictory axis orders. We exercise the helper
    indirectly via `write_entry` + re-parse rather than importing the private
    builder.
    """
    import json as _json

    import networkx as nx

    area = Area(center=(45.0716, 6.1079), radius_km=2.0)
    manifest = Manifest(
        area=area,
        untagged_policy="include",
        dem_version="ign_rge_alti_5m_2024-12",
        pipeline_content_hash="a" * 64,
        osm_extract_date="2026-05-20T12:00:00Z",
        cache_key_hash="0123456789abcdef",
        steeproute_version="0.1.0",
        steeproute_commit="abc1234",
        created_at="2026-05-20T12:00:00Z",
    )
    from steeproute.cache import write_entry  # pyright: ignore[reportUnknownVariableType]

    write_entry(tmp_path, manifest, nx.MultiDiGraph())  # pyright: ignore[reportMissingTypeArgument, reportUnknownArgumentType]
    feature = _json.loads(
        (tmp_path / "steeproute" / "areas" / "0123456789abcdef" / "bounds.geojson").read_text(
            encoding="utf-8"
        )
    )

    properties_center = feature["properties"]["center"]
    geometry_first_vertex = feature["geometry"]["coordinates"][0][0]

    # First vertex of the ring is `[lon - dlon, lat - dlat]`. Both elements
    # are less than their `properties.center` counterparts when the axis order
    # matches — that's the consistency check.
    assert geometry_first_vertex[0] < properties_center[0]
    assert geometry_first_vertex[1] < properties_center[1]
    # Strong assertion: `properties.center` first element matches longitude (6.1079).
    assert properties_center == pytest.approx([6.1079, 45.0716])  # pyright: ignore[reportUnknownMemberType]


# Rotated geometry through the on-disk surfaces.


def _rotated_area() -> Area:
    return Area(
        center=(45.0716, 6.1079),
        radius_km=0.0,
        half_width_km=8.0,
        half_height_km=3.0,
        angle_deg=35.0,
    )


def _write_rotated_entry(cache_root: pathlib.Path) -> pathlib.Path:
    import networkx as nx

    from steeproute.cache import write_entry  # pyright: ignore[reportUnknownVariableType]

    manifest = Manifest(
        area=_rotated_area(),
        untagged_policy="include",
        dem_version="ign_rge_alti_5m_2024-12",
        pipeline_content_hash="a" * 64,
        osm_extract_date="2026-05-20T12:00:00Z",
        cache_key_hash="0123456789abcdef",
        steeproute_version="0.1.0",
        steeproute_commit="abc1234",
        created_at="2026-05-20T12:00:00Z",
    )
    write_entry(cache_root, manifest, nx.MultiDiGraph())  # pyright: ignore[reportMissingTypeArgument, reportUnknownArgumentType]
    return cache_root / "steeproute" / "areas" / "0123456789abcdef"


def test_bounds_geojson_records_the_true_rotated_ring(tmp_path: pathlib.Path) -> None:
    """The sidecar carries the real footprint, not the axis-aligned envelope.

    A rotated box's envelope is strictly larger than the box, so a sidecar built
    from `.bounds` would misrepresent what was actually prepared. The ring must
    match `_area_to_polygon` — the same geometry coverage tests against — and its
    corners must therefore sit strictly inside the envelope's corners.
    """
    import json as _json

    entry_dir = _write_rotated_entry(tmp_path)
    feature = _json.loads((entry_dir / "bounds.geojson").read_text(encoding="utf-8"))

    ring = feature["geometry"]["coordinates"][0]
    expected = list(cache_mod._area_to_polygon(_rotated_area()).exterior.coords)
    assert len(ring) == 5  # 4 corners + the closing vertex
    assert ring[0] == ring[-1]
    for (got_lon, got_lat), (want_lon, want_lat) in zip(ring, expected, strict=True):
        assert got_lon == pytest.approx(want_lon)  # pyright: ignore[reportUnknownMemberType]
        assert got_lat == pytest.approx(want_lat)  # pyright: ignore[reportUnknownMemberType]

    # Not the envelope: every corner is strictly inside it on at least one axis.
    south, west, north, east = cache_mod.area_bbox_wgs84(_rotated_area())
    assert all(west < lon < east or south < lat < north for lon, lat in ring)


def test_bounds_geojson_properties_describe_the_rotated_shape(tmp_path: pathlib.Path) -> None:
    """`properties` uses the manifest's vocabulary, with GeoJSON `[lon, lat]` center."""
    import json as _json

    entry_dir = _write_rotated_entry(tmp_path)
    properties = _json.loads((entry_dir / "bounds.geojson").read_text(encoding="utf-8"))[
        "properties"
    ]

    assert properties["mode"] == "center_extents_angle"
    assert properties["half_width_km"] == 8.0
    assert properties["half_height_km"] == 3.0
    assert properties["angle_deg"] == 35.0
    assert "radius_km" not in properties
    # `[lon, lat]` per RFC 7946 — the inverse of the manifest's `[lat, lon]`.
    assert properties["center"] == pytest.approx([6.1079, 45.0716])  # pyright: ignore[reportUnknownMemberType]


def test_rebuild_index_writes_rotated_geometry_readable_by_coverage(
    tmp_path: pathlib.Path,
) -> None:
    """The index write and read sides agree on a rotated row.

    `rebuild_index` and `Manifest.to_dict` share `_area_wire_dict`, so a rotated
    entry round-trips through `index.json` without the geometry being flattened to
    a square — the failure that would make coverage silently resolve the wrong box.
    """
    import json as _json

    _ = _write_rotated_entry(tmp_path)
    rebuild_index(tmp_path)

    index_path = tmp_path / "steeproute" / "index.json"
    row = _json.loads(index_path.read_text(encoding="utf-8"))["entries"][0]
    assert row["area"] == {
        "mode": "center_extents_angle",
        "center": [45.0716, 6.1079],
        "half_width_km": 8.0,
        "half_height_km": 3.0,
        "angle_deg": 35.0,
    }

    parsed = cache_mod._read_indexed_entries(index_path)
    assert parsed is not None
    assert parsed[0].area.half_extents_km == (8.0, 3.0)
    assert parsed[0].area.angle_deg == 35.0


def test_gc_scopes_superseded_entries_by_rotated_geometry(tmp_path: pathlib.Path) -> None:
    """Two boxes differing only in bearing are distinct prepared areas, not supersessions.

    `_gc_superseded_entries` matches on `_canonicalize_area`, so it inherits the
    rotated mode for free — pinned here because a canonicalizer that ignored the
    angle would make writing one box silently delete the other.
    """
    import networkx as nx

    from steeproute.cache import (  # pyright: ignore[reportUnknownVariableType]
        entry_dir_for,
        write_entry,
    )

    base = Manifest(
        area=_rotated_area(),
        untagged_policy="include",
        dem_version="ign_rge_alti_5m_2024-12",
        pipeline_content_hash="a" * 64,
        osm_extract_date="2026-05-20T12:00:00Z",
        cache_key_hash="aaaaaaaaaaaaaaaa",
        steeproute_version="0.1.0",
        steeproute_commit="abc1234",
        created_at="2026-05-20T12:00:00Z",
    )
    turned = dataclasses.replace(
        base,
        area=dataclasses.replace(_rotated_area(), angle_deg=95.0),
        cache_key_hash="bbbbbbbbbbbbbbbb",
    )
    write_entry(tmp_path, base, nx.MultiDiGraph())  # pyright: ignore[reportMissingTypeArgument, reportUnknownArgumentType]
    write_entry(tmp_path, turned, nx.MultiDiGraph())  # pyright: ignore[reportMissingTypeArgument, reportUnknownArgumentType]

    assert entry_dir_for(tmp_path, "aaaaaaaaaaaaaaaa").is_dir()
    assert entry_dir_for(tmp_path, "bbbbbbbbbbbbbbbb").is_dir()


# Manifest.from_dict input validation.
def test_manifest_from_dict_raises_on_null_string_field() -> None:
    """`null` for a required string field surfaces as CacheCorruptedError, not coerced to 'None'."""
    payload = _manifest_payload(dem_version=None)

    with pytest.raises(CacheCorruptedError) as exc_info:
        Manifest.from_dict(payload)
    assert "dem_version" in exc_info.value.user_message
    assert "not a string" in exc_info.value.user_message


def test_manifest_from_dict_raises_on_non_string_dict_field() -> None:
    """A dict where a string is expected → CacheCorruptedError."""
    payload = _manifest_payload(steeproute_commit={"unexpected": "shape"})

    with pytest.raises(CacheCorruptedError) as exc_info:
        Manifest.from_dict(payload)
    assert "steeproute_commit" in exc_info.value.user_message


def test_manifest_from_dict_raises_on_non_numeric_area_coordinates() -> None:
    """`float()` rejection on non-numeric center surfaces as `CacheCorruptedError`.

    The `isinstance(center, list) and len == 2` guard accepts a list of any
    element types — strings reach the `float()` conversion and the
    `(TypeError, ValueError)` catch maps them cleanly to the contract.
    """
    payload = _manifest_payload(
        area={"mode": "center_radius", "center": ["forty-five", "six"], "radius_km": 2.0}
    )

    with pytest.raises(CacheCorruptedError, match="not numeric"):
        Manifest.from_dict(payload)


# Write_json_atomic cleans up .tmp on failure.
def test_write_json_atomic_cleans_up_tmp_when_os_replace_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed `os.replace` must not leave the `.tmp` sibling behind."""
    target = tmp_path / "manifest.json"

    def failing_replace(_src: object, _dst: object) -> None:
        raise OSError("simulated cross-device link failure")

    monkeypatch.setattr("steeproute.cache.os.replace", failing_replace)
    with pytest.raises(OSError, match="simulated"):
        write_json_atomic(target, {"k": 1})

    # `.tmp` orphan must not survive a failed write.
    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert siblings == []


def test_write_json_atomic_cleans_up_tmp_when_write_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure during `write_text` (ENOSPC sim) also cleans up the `.tmp` orphan."""
    import pathlib as _pl

    target = tmp_path / "manifest.json"
    real_write_text = _pl.Path.write_text
    call_count = {"n": 0}

    def failing_write_text(self: _pl.Path, *_args: object, **_kwargs: object) -> None:
        call_count["n"] += 1
        # First call creates the tmp file partially, then raises.
        real_write_text(self, "partial-content-before-failure", encoding="utf-8")
        raise OSError("simulated ENOSPC")

    monkeypatch.setattr(_pl.Path, "write_text", failing_write_text)
    with pytest.raises(OSError, match="ENOSPC"):
        write_json_atomic(target, {"k": 1})

    siblings = sorted(p.name for p in tmp_path.iterdir())
    assert siblings == [], f"Expected no .tmp orphan; got {siblings}"


# Read_entry exception widening.
def test_read_entry_raises_cache_corrupted_on_unicode_decode_error(
    tmp_path: pathlib.Path,
) -> None:
    """Binary garbage in `manifest.json` → CacheCorruptedError (not raw UnicodeDecodeError)."""
    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    # Non-UTF-8 bytes — `read_text(encoding="utf-8")` raises UnicodeDecodeError.
    (entry_dir / "manifest.json").write_bytes(b"\xff\xfe\x00\x00not-utf8")

    with pytest.raises(CacheCorruptedError, match="unreadable manifest"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_read_entry_raises_cache_corrupted_on_unpicklable_stale_graph(
    tmp_path: pathlib.Path,
) -> None:
    """A pickle referencing a missing module → CacheCorruptedError (not raw ImportError)."""
    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    write_json_atomic(entry_dir / "manifest.json", _manifest_payload())
    # A pickle that references a non-existent module — `pickle.load` raises
    # `ModuleNotFoundError` (a subclass of `ImportError`) which P5 maps to
    # `CacheCorruptedError`.
    # `\x80\x04` = protocol 4 header
    # `\x95...` = frame
    # `\x8c<len><name>` = SHORT_BINUNICODE for module name
    # ...this is fiddly to hand-build. Use `pickle.dumps` against a class
    # whose module we'll then make unimportable via the qualified-name string.
    import pickle

    payload_bytes = pickle.dumps({"some": "obj"})
    # Surgical-replace `__builtin__`/`builtins` reference if present, else
    # construct a known-bad pickle: GLOBAL referring to a missing module.
    bad_pickle = b"\x80\x04c__nonexistent_module_for_test__\nClassName\n."
    (entry_dir / "graph.pkl").write_bytes(bad_pickle)
    _ = payload_bytes  # keep import-side-effect

    with pytest.raises(CacheCorruptedError, match="unreadable graph"):
        _ = read_entry(tmp_path, "0123456789abcdef")
