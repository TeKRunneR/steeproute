"""Unit tests for cache.write_json_atomic, _bounds_geojson, rebuild_index recovery, and resolve_cache_root.

Atomic write + read + entry-overwrite paths are exercised in
`tests/integration/test_cache_roundtrip.py` and `test_cache_atomic.py`; this
file covers the smaller primitives + recovery branches.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re

import pytest

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


# --- write_json_atomic --------------------------------------------------------


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
    """AC #7 / #8: deterministic output enables diff-stable cache state across runs."""
    target = tmp_path / "manifest.json"
    write_json_atomic(target, {"z": 1, "a": 2, "m": 3})

    raw = target.read_text(encoding="utf-8")
    assert raw.index('"a"') < raw.index('"m"') < raw.index('"z"')


def test_write_json_atomic_chokepoint_no_direct_writes_in_cache_module() -> None:
    """AC #2: `cache.py` contains no direct `open(..., "w")` on JSON files.

    Verified by AST-walking the module — every Story 2.7 JSON write must route
    through `write_json_atomic` per Architecture §Key anti-patterns.
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
            "Story 2.7 routes all JSON writes through `write_json_atomic`."
        )


# --- rebuild_index recovery ---------------------------------------------------


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
    """AC #6: directories without `manifest.json` (`.tmp/`, `.old/`, half-written) are ignored."""
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
    """AC #7: deterministic entry order → diff-stable index.json."""
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


# --- Manifest.from_dict + PreparedData ---------------------------------------


def _manifest_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
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
    assert exc_info.value.detail is not None and "schema_version=1" in exc_info.value.detail


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


# --- read_entry error paths ---------------------------------------------------


def test_read_entry_raises_cache_not_found_when_manifest_missing(tmp_path: pathlib.Path) -> None:
    """AC #4: an entry directory without a `manifest.json` is treated as absent."""
    (tmp_path / "steeproute" / "areas" / "0123456789abcdef").mkdir(parents=True)

    with pytest.raises(CacheNotFoundError, match="0123456789abcdef"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_read_entry_raises_cache_not_found_for_unknown_key(tmp_path: pathlib.Path) -> None:
    with pytest.raises(CacheNotFoundError, match="ffffffffffffffff"):
        _ = read_entry(tmp_path, "ffffffffffffffff")


def test_read_entry_raises_cache_corrupted_on_malformed_manifest_json(
    tmp_path: pathlib.Path,
) -> None:
    """AC #4: unparseable `manifest.json` surfaces as `CacheCorruptedError`."""
    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    (entry_dir / "manifest.json").write_text("{not valid", encoding="utf-8")

    with pytest.raises(CacheCorruptedError, match="unreadable manifest"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_read_entry_raises_cache_corrupted_on_missing_graph_pkl(tmp_path: pathlib.Path) -> None:
    """AC #4: manifest present + graph.pkl missing → `CacheCorruptedError`."""
    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    write_json_atomic(entry_dir / "manifest.json", _manifest_payload())

    with pytest.raises(CacheCorruptedError, match="unreadable graph"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_prepared_data_is_frozen() -> None:
    import networkx as nx

    manifest = Manifest.from_dict(_manifest_payload())
    graph = nx.MultiDiGraph()  # pyright: ignore[reportMissingTypeArgument, reportUnknownVariableType]
    prepared = PreparedData(graph=graph, manifest=manifest)

    with pytest.raises(dataclasses.FrozenInstanceError):
        prepared.manifest = manifest  # pyright: ignore[reportAttributeAccessIssue]


# --- resolve_cache_root -------------------------------------------------------


def test_resolve_cache_root_returns_override_when_provided(tmp_path: pathlib.Path) -> None:
    """AC #9: explicit `--cache-dir` (Story 2.8) bypasses the platformdirs default."""
    resolved = resolve_cache_root(tmp_path)
    assert resolved == tmp_path


def test_resolve_cache_root_returns_platformdirs_default_when_no_override() -> None:
    """AC #9: `None` → `platformdirs.user_cache_dir("steeproute")`."""
    default_root = resolve_cache_root(None)
    # No strict-string assertion — platformdirs picks platform-specific paths
    # (`%LOCALAPPDATA%\\steeproute\\Cache\\` on Windows, `~/.cache/steeproute`
    # on Linux). We sanity-check the result is a `Path` ending with the app name.
    assert isinstance(default_root, pathlib.Path)
    assert re.search(r"[\\/](?i:steeproute)([\\/]Cache)?$", str(default_root)) is not None


# --- Review patch P2: bounds.geojson axis-order consistency ------------------


def test_bounds_geojson_geometry_and_properties_center_use_lon_lat_consistently(
    tmp_path: pathlib.Path,
) -> None:
    """P2: `properties.center` and `geometry.coordinates` must agree on axis order.

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


# --- Review patch P3: Manifest.from_dict input validation --------------------


def test_manifest_from_dict_raises_on_null_string_field() -> None:
    """P3: `null` for a required string field surfaces as CacheCorruptedError, not coerced to 'None'."""
    payload = _manifest_payload(dem_version=None)

    with pytest.raises(CacheCorruptedError) as exc_info:
        Manifest.from_dict(payload)
    assert "dem_version" in exc_info.value.user_message
    assert "not a string" in exc_info.value.user_message


def test_manifest_from_dict_raises_on_non_string_dict_field() -> None:
    """P3: a dict where a string is expected → CacheCorruptedError."""
    payload = _manifest_payload(steeproute_commit={"unexpected": "shape"})

    with pytest.raises(CacheCorruptedError) as exc_info:
        Manifest.from_dict(payload)
    assert "steeproute_commit" in exc_info.value.user_message


def test_manifest_from_dict_raises_on_non_numeric_area_coordinates() -> None:
    """P3: `float()` rejection on non-numeric center surfaces as `CacheCorruptedError`.

    The `isinstance(center, list) and len == 2` guard accepts a list of any
    element types — strings reach the `float()` conversion and the
    `(TypeError, ValueError)` catch maps them cleanly to the contract.
    """
    payload = _manifest_payload(
        area={"mode": "center_radius", "center": ["forty-five", "six"], "radius_km": 2.0}
    )

    with pytest.raises(CacheCorruptedError, match="not numeric"):
        Manifest.from_dict(payload)


# --- Review patch P4: write_json_atomic cleans up .tmp on failure -----------


def test_write_json_atomic_cleans_up_tmp_when_os_replace_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P4: a failed `os.replace` must not leave the `.tmp` sibling behind."""
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
    """P4: a failure during `write_text` (ENOSPC sim) also cleans up the `.tmp` orphan."""
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


# --- Review patch P5: read_entry exception widening -------------------------


def test_read_entry_raises_cache_corrupted_on_unicode_decode_error(
    tmp_path: pathlib.Path,
) -> None:
    """P5: binary garbage in `manifest.json` → CacheCorruptedError (not raw UnicodeDecodeError)."""
    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    # Non-UTF-8 bytes — `read_text(encoding="utf-8")` raises UnicodeDecodeError.
    (entry_dir / "manifest.json").write_bytes(b"\xff\xfe\x00\x00not-utf8")

    with pytest.raises(CacheCorruptedError, match="unreadable manifest"):
        _ = read_entry(tmp_path, "0123456789abcdef")


def test_read_entry_raises_cache_corrupted_on_unpicklable_stale_graph(
    tmp_path: pathlib.Path,
) -> None:
    """P5: a pickle referencing a missing module → CacheCorruptedError (not raw ImportError)."""
    entry_dir = tmp_path / "steeproute" / "areas" / "0123456789abcdef"
    entry_dir.mkdir(parents=True)
    write_json_atomic(entry_dir / "manifest.json", _manifest_payload())
    # A pickle that references a non-existent module — `pickle.load` raises
    # `ModuleNotFoundError` (a subclass of `ImportError`) which P5 maps to
    # `CacheCorruptedError`.
    #   `\x80\x04` = protocol 4 header
    #   `\x95...` = frame
    #   `\x8c<len><name>` = SHORT_BINUNICODE for module name
    #   ...this is fiddly to hand-build. Use `pickle.dumps` against a class
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
