# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnusedFunction=false
# Reason: same osmnx/networkx boundary as the underlying pipeline modules.
# `reportUnusedFunction` relaxed for the `_skip_if_fixtures_missing` autouse fixture.
"""End-to-end coverage for `steeproute-setup` (Story 2.8).

Tests exercise the click command in-process via `CliRunner` with both
`pipeline.osm_load` and `cli.setup.resolve_dem` patched to read the committed
Grenoble fixture — the same offline pattern Stories 2.5 and 2.7 use, extended so
the DEM auto-download never touches the network. Subprocess-style smoke tests
live in `test_cli_smoke.py`; this file is about the full hit / miss /
`--force-refresh` flow and the on-disk cache layout.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
from unittest.mock import Mock, patch

import networkx as nx
import osmnx
import pytest
from click.testing import CliRunner, Result

from steeproute.cli.setup import cli as setup_cli
from steeproute.models import Area
from steeproute.pipeline.dem_download import DEFAULT_DEM_VERSION
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

# Fixture baseline carried over from `test_pipeline_end_to_end.py` (updated for
# Story 6.2's road-inclusive fixture). Story 2.8 only smoke-checks the edge count
# is in that band — the orchestrator tests are the authority on exact numbers; we
# just need "did the pipeline run end-to-end and produce a sane cache entry?"
_BASELINE_EDGES = 2086
_DRIFT_TOLERANCE = 0.10


def _load_fixture_constants() -> tuple[float, float, int]:
    """Mirror of the loader in `test_pipeline_end_to_end.py` / `test_cache_roundtrip.py`."""
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen", regen_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CENTER_LAT, module.CENTER_LON, module.DIST_M


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()
_RADIUS_KM = _DIST_M / 1000.0
_CENTER_FLAG = f"{_CENTER_LAT},{_CENTER_LON}"
_RADIUS_FLAG = f"{_RADIUS_KM}"


def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
    """Drop-in for `pipeline.osm_load` that reads the committed graphml fixture."""
    return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))


def _resolve_dem_from_fixture(
    _area: Area,
    _cache_root: pathlib.Path,
    **_kwargs: object,
) -> pathlib.Path:
    """Drop-in for `cli.setup.resolve_dem` that returns the committed DEM fixture.

    Keeps the cache-miss pipeline fully offline — no IGN WMS request — while
    preserving the resolved-local-path contract the orchestrator depends on.
    `**_kwargs` absorbs `dem_version` / `force_refresh`.
    """
    return _DEM_FIXTURE_PATH


@pytest.fixture(autouse=True)
def _skip_if_fixtures_missing() -> None:
    if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; setup e2e tests skipped.")


def _invoke_setup(
    cache_dir: pathlib.Path,
    *extra_args: str,
    patch_pipeline: bool = True,
) -> Result:
    """Run `setup_cli` in-process against the fixture; returns the CliRunner Result.

    `patch_pipeline=True` (default) patches both `osm_load` and `resolve_dem` so
    cache misses run the full pipeline offline. `patch_pipeline=False` skips both
    patches so cache-hit tests can prove the hit branch never re-fetches OSM or
    re-downloads the DEM — a regression where the hit path reached either would
    attempt real network I/O and raise here rather than silently passing.
    """
    runner = CliRunner()
    args = [
        "--center",
        _CENTER_FLAG,
        "--radius",
        _RADIUS_FLAG,
        "--cache-dir",
        str(cache_dir),
        *extra_args,
    ]
    if patch_pipeline:
        with (
            patch("steeproute.pipeline.osm_load", _osm_load_from_fixture),
            patch("steeproute.cli.setup.resolve_dem", _resolve_dem_from_fixture),
        ):
            return runner.invoke(setup_cli, args, catch_exceptions=False)
    return runner.invoke(setup_cli, args, catch_exceptions=False)


def test_setup_first_run_is_cache_miss_writes_entry_and_reports_summary(
    tmp_path: pathlib.Path,
) -> None:
    """AC #1, #2, #4: first invocation runs the pipeline, writes a valid cache entry, and prints the summary."""
    result = _invoke_setup(tmp_path)

    assert result.exit_code == 0, result.output
    assert "cache-miss" in result.output
    assert "cache_key_hash:" in result.output
    assert "entry:" in result.output
    assert "elapsed:" in result.output

    # Exactly one entry directory under `<cache-dir>/steeproute/areas/`, with the
    # three Architecture §Cat 4a files inside.
    areas_dir = tmp_path / "steeproute" / "areas"
    entries = list(areas_dir.iterdir())
    assert len(entries) == 1
    entry = entries[0]
    assert (entry / "manifest.json").is_file()
    assert (entry / "graph.pkl").is_file()
    assert (entry / "bounds.geojson").is_file()

    # The 16-hex hash printed in the summary matches the entry directory name.
    assert entry.name in result.output


def test_setup_first_run_writes_manifest_with_complete_provenance(
    tmp_path: pathlib.Path,
) -> None:
    """AC #1: the manifest carries every field `Manifest` requires, populated correctly."""
    result = _invoke_setup(tmp_path)
    assert result.exit_code == 0, result.output

    entry = next((tmp_path / "steeproute" / "areas").iterdir())
    payload = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))

    # Schema + the four key-inducing fields the CLI populated.
    assert payload["schema_version"] == 1
    assert payload["untagged_policy"] == "include"
    # With no `--dem-version` flag, `dem_version` is the stable IGN-layer default
    # tag (the DEM is auto-downloaded, not user-supplied).
    assert payload["dem_version"] == DEFAULT_DEM_VERSION
    assert payload["pipeline_content_hash"]
    assert payload["cache_key_hash"] == entry.name
    # Provenance: `steeproute_version` is the installed package version (or
    # `"unknown"` if running from an uninstalled checkout); `steeproute_commit`
    # may be `"unknown"` outside git. Both must be present and string-typed.
    assert isinstance(payload["steeproute_version"], str) and payload["steeproute_version"]
    assert isinstance(payload["steeproute_commit"], str) and payload["steeproute_commit"]
    # `osm_extract_date` and `created_at` are both ISO-8601 second-precision Z-suffix
    # strings populated independently from `provenance.iso8601_utc_now()` at write
    # time. We assert format compliance rather than equality — the CLI happens to
    # capture one `now` and reuse it today, but that's an implementation detail of
    # the diff, not a spec contract; testing equality would freeze in that detail.
    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    assert iso_re.match(payload["osm_extract_date"]) is not None
    assert iso_re.match(payload["created_at"]) is not None
    # `pipeline_content_hash` is a full SHA-256 hex digest (64 lowercase hex chars).
    assert re.fullmatch(r"[0-9a-f]{64}", payload["pipeline_content_hash"]) is not None


def test_setup_graph_edge_count_within_story_2_5_baseline(tmp_path: pathlib.Path) -> None:
    """AC #4: the pipeline actually ran (edge count matches Story 2.5's `_BASELINE_EDGES ± 10%`)."""
    import pickle

    _invoke_setup(tmp_path)
    entry = next((tmp_path / "steeproute" / "areas").iterdir())
    with (entry / "graph.pkl").open("rb") as fp:
        graph: nx.MultiDiGraph = pickle.load(fp)

    edge_count = graph.number_of_edges()
    drift = abs(edge_count - _BASELINE_EDGES) / _BASELINE_EDGES
    assert drift < _DRIFT_TOLERANCE, (
        f"Edge count {edge_count} drifts {drift:.2%} from baseline {_BASELINE_EDGES} "
        f"(tolerance {_DRIFT_TOLERANCE:.0%}); fixture may have regenerated."
    )


def test_setup_second_run_same_flags_is_cache_hit(tmp_path: pathlib.Path) -> None:
    """AC #4: re-invocation hits the cache and never re-enters `osm_load`.

    Pre-seed once with the pipeline patches active so the miss path runs offline;
    the second invocation runs **without** the patches. If the hit-path regressed
    and called `osm_load` or `resolve_dem`, the second invocation would attempt
    real network I/O and (in CI without network) raise — proving the hit-path is
    both OSM- and DEM-independent.
    """
    first = _invoke_setup(tmp_path, patch_pipeline=True)
    assert first.exit_code == 0
    assert "cache-miss" in first.output

    second = _invoke_setup(tmp_path, patch_pipeline=False)
    assert second.exit_code == 0, second.output
    assert "cache-hit" in second.output
    assert "cache-miss" not in second.output


def test_setup_force_refresh_rebuilds_entry_on_existing_key(tmp_path: pathlib.Path) -> None:
    """AC #4: `--force-refresh` re-runs the pipeline even when the cache key matches."""
    first = _invoke_setup(tmp_path)
    assert first.exit_code == 0
    first_entry = next((tmp_path / "steeproute" / "areas").iterdir())
    first_mtime = (first_entry / "manifest.json").stat().st_mtime_ns

    forced = _invoke_setup(tmp_path, "--force-refresh")
    assert forced.exit_code == 0, forced.output
    # Even though the cache entry already exists, the summary reports a miss
    # because `--force-refresh` skips the read entirely.
    assert "cache-miss" in forced.output

    # Same key hash (inputs unchanged) but the manifest was rewritten.
    second_entry = next((tmp_path / "steeproute" / "areas").iterdir())
    assert second_entry.name == first_entry.name
    assert (second_entry / "manifest.json").stat().st_mtime_ns >= first_mtime


def test_setup_with_different_untagged_trails_writes_new_entry(tmp_path: pathlib.Path) -> None:
    """AC #5: changing a key-inducing flag produces a fresh entry rather than overwriting."""
    first = _invoke_setup(tmp_path, "--untagged-trails", "include")
    assert first.exit_code == 0
    second = _invoke_setup(tmp_path, "--untagged-trails", "exclude")
    assert second.exit_code == 0

    areas_dir = tmp_path / "steeproute" / "areas"
    entry_names = sorted(p.name for p in areas_dir.iterdir())
    assert len(entry_names) == 2, (
        f"Expected two distinct cache entries after the policy change; got {entry_names}."
    )
    # Both summary outputs name a different cache_key_hash.
    assert any(name in first.output for name in entry_names)
    assert any(name in second.output for name in entry_names)


def test_setup_with_different_dem_version_writes_new_entry(tmp_path: pathlib.Path) -> None:
    """AC #5: changing the user-supplied `--dem-version` produces a fresh entry.

    Parallel to the `--untagged-trails` sensitivity test — `--dem-version` is also
    a cache-key-composing input (Architecture §Cat 4b), so two runs differing only
    on that flag must produce two distinct entries. Story 2.6's `test_cache_key.py`
    covers the underlying `compute_cache_key` contract; this is the CLI-tier proof.
    """
    first = _invoke_setup(tmp_path, "--dem-version", "v1-test")
    assert first.exit_code == 0
    second = _invoke_setup(tmp_path, "--dem-version", "v2-test")
    assert second.exit_code == 0

    areas_dir = tmp_path / "steeproute" / "areas"
    entry_names = sorted(p.name for p in areas_dir.iterdir())
    assert len(entry_names) == 2, (
        f"Expected two distinct cache entries after the --dem-version change; got {entry_names}."
    )


def test_setup_index_lists_all_written_entries(tmp_path: pathlib.Path) -> None:
    """AC #1 (write_entry rebuilds the index): after two distinct entries, `index.json` lists both."""
    _invoke_setup(tmp_path, "--untagged-trails", "include")
    _invoke_setup(tmp_path, "--untagged-trails", "exclude")

    index_payload = json.loads((tmp_path / "steeproute" / "index.json").read_text(encoding="utf-8"))
    assert index_payload["schema_version"] == 1
    assert len(index_payload["entries"]) == 2


def test_setup_cache_miss_auto_downloads_dem_for_area(tmp_path: pathlib.Path) -> None:
    """The cache-miss branch resolves the DEM via auto-download (no `--dem-path` flag).

    `resolve_dem` is called exactly once, with the requested area and
    `force_refresh=False`, and its returned path feeds the pipeline.
    """
    dem_mock = Mock(return_value=_DEM_FIXTURE_PATH)
    runner = CliRunner()
    args = ["--center", _CENTER_FLAG, "--radius", _RADIUS_FLAG, "--cache-dir", str(tmp_path)]
    with (
        patch("steeproute.pipeline.osm_load", _osm_load_from_fixture),
        patch("steeproute.cli.setup.resolve_dem", dem_mock),
    ):
        result = runner.invoke(setup_cli, args, catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "cache-miss" in result.output
    dem_mock.assert_called_once()
    call = dem_mock.call_args
    assert call.args[0] == Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_RADIUS_KM)
    assert call.kwargs["force_refresh"] is False


def test_setup_force_refresh_redownloads_dem(tmp_path: pathlib.Path) -> None:
    """`--force-refresh` re-resolves the DEM with `force_refresh=True` so it re-downloads."""
    dem_mock = Mock(return_value=_DEM_FIXTURE_PATH)
    runner = CliRunner()
    args = [
        "--center",
        _CENTER_FLAG,
        "--radius",
        _RADIUS_FLAG,
        "--cache-dir",
        str(tmp_path),
        "--force-refresh",
    ]
    with (
        patch("steeproute.pipeline.osm_load", _osm_load_from_fixture),
        patch("steeproute.cli.setup.resolve_dem", dem_mock),
    ):
        result = runner.invoke(setup_cli, args, catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert dem_mock.call_args.kwargs["force_refresh"] is True
