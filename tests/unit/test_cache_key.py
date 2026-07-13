"""Unit tests for cache.compute_cache_key, compute_pipeline_content_hash, and Manifest."""

from __future__ import annotations

import dataclasses
import pathlib
import re

import pytest

from steeproute import cache as cache_mod
from steeproute.cache import (
    Manifest,
    compute_cache_key,
    compute_pipeline_content_hash,
)
from steeproute.models import Area

_CACHE_KEY_HEX_LEN = 16
_PIPELINE_CONTENT_HASH_HEX_LEN = 64
_LOWERCASE_HEX_RE = re.compile(r"^[0-9a-f]+$")

_BASE_AREA = Area(center=(45.0716, 6.1079), radius_km=2.0)
_BASE_UNTAGGED = "include"
_BASE_DEM_VERSION = "ign_rge_alti_5m_2024-12"
_BASE_PIPELINE_HASH = "a" * 64


def _key(
    area: Area = _BASE_AREA,
    untagged: str = _BASE_UNTAGGED,
    dem: str = _BASE_DEM_VERSION,
    pipeline_hash: str = _BASE_PIPELINE_HASH,
) -> str:
    return compute_cache_key(area, untagged, dem, pipeline_hash)


def test_compute_cache_key_returns_16_char_lowercase_hex() -> None:
    key = _key()
    assert len(key) == _CACHE_KEY_HEX_LEN
    assert _LOWERCASE_HEX_RE.match(key) is not None


def test_compute_cache_key_is_deterministic_on_unchanged_inputs() -> None:
    assert _key() == _key()


def test_compute_cache_key_canonicalizes_area_drift_below_canonical_precision() -> None:
    # 7th-decimal lat/lon drift + 4th-decimal radius drift, each well below the
    # round-half threshold for their target precision (round-to-6, round-to-3).
    drifted = Area(center=(45.0716001, 6.1079001), radius_km=2.0001)
    assert _key() == _key(area=drifted)


def test_compute_cache_key_changes_when_area_moves_beyond_canonical_precision() -> None:
    # 5th-decimal lat shift sits above the 6-decimal floor — must change the key.
    moved = Area(center=(45.07165, 6.1079), radius_km=2.0)
    assert _key() != _key(area=moved)


def test_compute_cache_key_changes_with_untagged_policy() -> None:
    assert _key(untagged="include") != _key(untagged="exclude")


def test_compute_cache_key_changes_with_dem_version() -> None:
    assert _key(dem="ign_rge_alti_5m_2024-12") != _key(dem="ign_rge_alti_5m_2025-06")


def test_compute_cache_key_changes_with_pipeline_content_hash() -> None:
    assert _key(pipeline_hash="a" * 64) != _key(pipeline_hash="b" * 64)


def test_compute_pipeline_content_hash_returns_64_char_lowercase_hex() -> None:
    h = compute_pipeline_content_hash()
    assert len(h) == _PIPELINE_CONTENT_HASH_HEX_LEN
    assert _LOWERCASE_HEX_RE.match(h) is not None


def test_compute_pipeline_content_hash_is_deterministic_on_unchanged_tree() -> None:
    assert compute_pipeline_content_hash() == compute_pipeline_content_hash()


def test_compute_pipeline_content_hash_changes_when_a_pipeline_file_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build a synthetic package layout, point cache_mod.__file__ at it, modify a file, observe hash change.

    Patching `__file__` rather than editing real source keeps the repo tree
    clean and avoids racing with concurrent test runs.
    """
    fake_pkg = tmp_path / "steeproute"
    fake_pkg.mkdir()
    (fake_pkg / "cache.py").write_text("# fake module to anchor __file__\n", encoding="utf-8")
    (fake_pkg / "models.py").write_text("# placeholder models\n", encoding="utf-8")
    pipeline_dir = fake_pkg / "pipeline"
    pipeline_dir.mkdir()
    (pipeline_dir / "__init__.py").write_text("# placeholder orchestrator\n", encoding="utf-8")
    (pipeline_dir / "osm.py").write_text("# stage 1-2\n", encoding="utf-8")

    monkeypatch.setattr(cache_mod, "__file__", str(fake_pkg / "cache.py"))

    before = compute_pipeline_content_hash()
    (pipeline_dir / "osm.py").write_text("# stage 1-2 MODIFIED\n", encoding="utf-8")
    after = compute_pipeline_content_hash()

    assert before != after


def test_compute_pipeline_content_hash_ignores_solver_changes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `solver/` edit must NOT shift the content hash (Story 14.4 cache-safety).

    `--workers` is plumbed entirely through `solver/` + `cli/`, neither of which is
    in `_PIPELINE_CONTENT_GLOBS` (`pipeline/**` + `models.py`). This pins the claim
    that adding `solver/parallel.py` — and any future solver-layer change — leaves
    every prepared cache valid (no invalidation, no golden rebake), the reason the
    story deliberately kept `workers` out of `SolverParams`/`models.py`.
    """
    fake_pkg = tmp_path / "steeproute"
    fake_pkg.mkdir()
    (fake_pkg / "cache.py").write_text("# fake module to anchor __file__\n", encoding="utf-8")
    (fake_pkg / "models.py").write_text("# placeholder models\n", encoding="utf-8")
    pipeline_dir = fake_pkg / "pipeline"
    pipeline_dir.mkdir()
    (pipeline_dir / "__init__.py").write_text("# placeholder orchestrator\n", encoding="utf-8")
    solver_dir = fake_pkg / "solver"
    solver_dir.mkdir()
    (solver_dir / "parallel.py").write_text("# workers=1\n", encoding="utf-8")

    monkeypatch.setattr(cache_mod, "__file__", str(fake_pkg / "cache.py"))

    before = compute_pipeline_content_hash()
    (solver_dir / "parallel.py").write_text("# workers=N MODIFIED\n", encoding="utf-8")
    after = compute_pipeline_content_hash()

    assert before == after


def _build_manifest(**overrides: object) -> Manifest:
    defaults: dict[str, object] = {
        "area": Area(center=(45.0716, 6.1079), radius_km=2.0),
        "untagged_policy": "include",
        "dem_version": "ign_rge_alti_5m_2024-12",
        "pipeline_content_hash": "a" * 64,
        "osm_extract_date": "2026-05-20T12:00:00Z",
        "cache_key_hash": "0123456789abcdef",
        "steeproute_version": "0.1.0",
        "steeproute_commit": "abc1234-dirty",
        "created_at": "2026-05-20T12:00:00Z",
    }
    defaults.update(overrides)
    return Manifest(**defaults)  # pyright: ignore[reportArgumentType]


def test_manifest_to_dict_emits_full_schema_with_nested_area_shape() -> None:
    manifest = _build_manifest()
    d = manifest.to_dict()

    assert d["schema_version"] == 2
    assert d["area"] == {
        "mode": "center_radius",
        "center": [45.0716, 6.1079],
        "radius_km": 2.0,
    }
    assert d["untagged_policy"] == "include"
    assert d["dem_version"] == "ign_rge_alti_5m_2024-12"
    assert d["pipeline_content_hash"] == "a" * 64
    assert d["osm_extract_date"] == "2026-05-20T12:00:00Z"
    assert d["cache_key_hash"] == "0123456789abcdef"
    assert d["steeproute_version"] == "0.1.0"
    assert d["steeproute_commit"] == "abc1234-dirty"
    assert d["created_at"] == "2026-05-20T12:00:00Z"


def test_manifest_is_frozen() -> None:
    manifest = _build_manifest()
    with pytest.raises(dataclasses.FrozenInstanceError):
        manifest.untagged_policy = "exclude"  # pyright: ignore[reportAttributeAccessIssue]
