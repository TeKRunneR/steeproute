# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnusedFunction=false
# Reason: same osmnx/networkx boundary as the underlying pipeline modules.
# `reportUnusedFunction` relaxed for the `_skip_if_fixtures_missing` autouse fixture.
"""End-to-end coverage for `steeproute` query CLI's FR24 fail-fast (Story 2.10).

Seeds real cache entries via in-process `setup_cli` invocations (no mocked
coverage data per the epic's AC), then drives the query CLI against those
entries to assert the three documented outcomes:

1. **Empty cache** → exit 2 + actionable `steeproute-setup …` command.
2. **Partial coverage** (query bbox pokes outside every prepared area) →
   exit 2 + nearest-area diagnostic + smaller-radius suggestion.
3. **Multi-containment** (two concentric prepared areas both contain the
   query) → exit 0 + cache-hit cue → entry corresponds to the smaller radius.

The `_osm_load_from_fixture` patch from `test_steeproute_setup.py` keeps the
setup seeds offline; the query verification step runs against the seeded cache
without any patches.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import sys
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

import networkx as nx
import osmnx
import pytest
from click.testing import CliRunner, Result

from steeproute.cli._shared import set_verbose
from steeproute.cli.query import cli as query_cli
from steeproute.cli.setup import cli as setup_cli
from steeproute.errors import PreExecutionError
from steeproute.models import Area
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"


def _load_fixture_constants() -> tuple[float, float, int]:
    """Mirror of the loader in `test_steeproute_setup.py`."""
    regen_path = _FIXTURE_DIR / "regenerate.py"
    try:
        spec = importlib.util.spec_from_file_location("_grenoble_small_regen_coverage", regen_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.CENTER_LAT, module.CENTER_LON, module.DIST_M
    except (FileNotFoundError, ImportError, AttributeError):
        # Sentinel values trigger the autouse skip below — same pattern as
        # `test_source_unavailable.py::_load_fixture_constants`.
        return (0.0, 0.0, 0)


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()
_FIXTURES_LOADED = _DIST_M > 0
_FIXTURE_RADIUS_KM = _DIST_M / 1000.0 if _FIXTURES_LOADED else 0.0


def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
    """Drop-in for `pipeline.osm_load` that reads the committed graphml fixture."""
    return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))


def _resolve_dem_from_fixture(
    _bounds: tuple[float, float, float, float],
    _cache_root: pathlib.Path,
    **_kwargs: object,
) -> pathlib.Path:
    """Drop-in for `cli.setup.resolve_dem` returning the committed DEM fixture (offline)."""
    return _DEM_FIXTURE_PATH


@pytest.fixture(autouse=True)
def _skip_if_fixtures_missing() -> None:
    if not _FIXTURES_LOADED or not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; coverage-check e2e tests skipped.")


def _seed_setup(
    cache_dir: pathlib.Path,
    *,
    center: tuple[float, float] | None = None,
    radius_km: float | None = None,
) -> Result:
    """Run `setup_cli` in-process against the fixture; returns the CliRunner Result."""
    runner = CliRunner()
    lat = center[0] if center is not None else _CENTER_LAT
    lon = center[1] if center is not None else _CENTER_LON
    radius = radius_km if radius_km is not None else _FIXTURE_RADIUS_KM
    args = [
        "--center",
        f"{lat},{lon}",
        "--radius",
        f"{radius}",
        "--cache-dir",
        str(cache_dir),
    ]
    with (
        patch("steeproute.pipeline.osm_load", _osm_load_from_fixture),
        patch("steeproute.cli.setup.resolve_dem", _resolve_dem_from_fixture),
    ):
        return runner.invoke(setup_cli, args, catch_exceptions=False)


def _invoke_query(
    cache_dir: pathlib.Path,
    *,
    center: tuple[float, float],
    radius_km: float,
    area_cap_km2: float = 500.0,
) -> Result:
    """Run `query_cli` in-process against the seeded cache (success path only).

    Use this for exit-0 scenarios. For exit-2 paths, `_invoke_query_with_wrapper`
    mirrors `run_entry_point`'s `PreExecutionError → exit 2` mapping in-process.
    """
    runner = CliRunner()
    args = _query_args(cache_dir, center=center, radius_km=radius_km, area_cap_km2=area_cap_km2)
    return runner.invoke(query_cli, args, catch_exceptions=False)


def _query_args(
    cache_dir: pathlib.Path,
    *,
    center: tuple[float, float],
    radius_km: float,
    area_cap_km2: float = 500.0,
) -> list[str]:
    return [
        "--center",
        f"{center[0]},{center[1]}",
        "--radius",
        f"{radius_km}",
        "--cache-dir",
        str(cache_dir),
        "--area-cap",
        f"{area_cap_km2}",
    ]


@contextmanager
def _capture_stderr() -> Generator[io.StringIO]:
    """Swap `sys.stderr` for a buffer so `run_entry_point`'s writes are captured."""
    buf = io.StringIO()
    saved = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = saved


def _invoke_query_with_wrapper(args: list[str]) -> tuple[int, str]:
    """Invoke `cli/query.py` through the same `run_entry_point` mapping the binary uses.

    Same pattern as `test_source_unavailable.py::_invoke_with_wrapper` — see that
    file's helper docstring for the rationale (CliRunner.invoke + monkey-patching
    needs an in-process equivalent of the exit-code wrapper).
    """
    with _capture_stderr() as stderr_buf:
        try:
            query_cli.main(args=args, standalone_mode=False)
            return 0, stderr_buf.getvalue()
        except PreExecutionError as exc:
            from steeproute.cli._shared import is_verbose

            stderr_buf.write(f"error: {exc.user_message}\n")
            if is_verbose() and exc.detail is not None:
                stderr_buf.write(f"        {exc.detail}\n")
            return 2, stderr_buf.getvalue()
        except KeyboardInterrupt:
            return 130, stderr_buf.getvalue()
        finally:
            set_verbose(False)


# --- AC #7 (a): empty cache → exit 2 with steeproute-setup command -----------


def test_query_no_prepared_cache_exits_2_with_setup_command(tmp_path: pathlib.Path) -> None:
    """AC #3 / AC #7 (a): query against an empty `--cache-dir` raises FR24 fail-fast."""
    exit_code, stderr = _invoke_query_with_wrapper(
        _query_args(tmp_path, center=(_CENTER_LAT, _CENTER_LON), radius_km=_FIXTURE_RADIUS_KM)
    )

    assert exit_code == 2, stderr
    # AC #3 / P5: empty-cache lead distinguishes from partial-coverage lead.
    assert "No prepared cache exists yet." in stderr
    # The suggested command echoes the query's own --center / --radius so it's
    # directly copy-pasteable.
    assert f"steeproute-setup --center {_CENTER_LAT},{_CENTER_LON}" in stderr
    assert f"--radius {_FIXTURE_RADIUS_KM:g}" in stderr
    # DEM is auto-downloaded — the suggested command no longer carries --dem-path.
    assert "--dem-path" not in stderr


# --- AC #7 (b): partial coverage → exit 2 with nearest-area diagnostic -------


def test_query_partial_coverage_exits_2_with_nearest_area_message(tmp_path: pathlib.Path) -> None:
    """AC #4 / AC #7 (b): seed one entry, then query an area that pokes outside it.

    The seeded entry is the 2-km-radius fixture; the query asks for a 5-km
    radius at the same center, so its bbox extends beyond every side of the
    prepared bbox → strict containment fails → exit 2 with a nearest-area
    diagnostic that names the prepared area and suggests a smaller `--radius`.
    """
    seed = _seed_setup(tmp_path)
    assert seed.exit_code == 0, seed.output

    exit_code, stderr = _invoke_query_with_wrapper(
        _query_args(
            tmp_path,
            center=(_CENTER_LAT, _CENTER_LON),
            radius_km=5.0,  # Larger than the seeded 2 km — pokes outside.
        )
    )

    assert exit_code == 2, stderr
    assert "No prepared cache covers this area." in stderr
    # Nearest-area diagnostic mentions the prepared center + radius.
    assert "Nearest prepared area" in stderr
    assert f"{_CENTER_LAT}" in stderr
    assert f"{_CENTER_LON}" in stderr
    # The actionable suggestion is either a smaller --radius or a tighter --center.
    assert "--radius" in stderr or "--center" in stderr


# --- AC #7 (c): multi-containment → exit 0, picks smallest radius ------------


def test_query_multi_containment_picks_smallest_radius(tmp_path: pathlib.Path) -> None:
    """AC #5 / AC #7 (c): two concentric seeds, query contained by both → smallest wins."""
    # Seed the 2-km fixture as the first prepared area. Use the same fixture's
    # OSM graph (loaded via `_osm_load_from_fixture`) for both seeds — only the
    # area metadata differs, which is what coverage selection keys on.
    first = _seed_setup(tmp_path, radius_km=_FIXTURE_RADIUS_KM)
    assert first.exit_code == 0, first.output

    # Second seed at the same center with a different radius. A larger radius
    # produces a different `cache_key_hash` (radius is part of the key) so
    # the second invocation is a cache-miss and writes a new entry.
    second = _seed_setup(tmp_path, radius_km=_FIXTURE_RADIUS_KM + 1.0)
    assert second.exit_code == 0, second.output
    assert "cache-miss" in second.output

    # Sanity: the cache now has two entries.
    areas = sorted((tmp_path / "steeproute" / "areas").iterdir(), key=lambda p: p.name)
    assert len(areas) == 2
    smaller_radius_entry_hashes = {
        json.loads((a / "manifest.json").read_text(encoding="utf-8"))["cache_key_hash"]
        for a in areas
        if json.loads((a / "manifest.json").read_text(encoding="utf-8"))["area"]["radius_km"]
        == _FIXTURE_RADIUS_KM
    }
    assert len(smaller_radius_entry_hashes) == 1
    expected_hash = next(iter(smaller_radius_entry_hashes))

    # Query at the same center with a radius strictly smaller than both seeds.
    result = _invoke_query(
        tmp_path,
        center=(_CENTER_LAT, _CENTER_LON),
        radius_km=_FIXTURE_RADIUS_KM - 0.5,
    )

    assert result.exit_code == 0, result.output
    assert "cache-hit" in result.output
    # The cache-hit cue carries the chosen entry's hash — must be the smaller one.
    assert expected_hash in result.output


# --- AC #6: OSM-age warning on the query-side cache-hit path ------------------


def test_query_emits_osm_age_warning_on_stale_cache_hit(tmp_path: pathlib.Path) -> None:
    """AC #6: a stale cache-hit on the query CLI fires the OSM-age warning on stderr.

    Mirrors `test_source_unavailable.py::test_osm_age_warning_emitted_on_stale_cache_hit`
    but exercises the query CLI's `emit_osm_age_warning` integration. The seed uses
    `patch("steeproute.cli.setup.iso8601_utc_now", ...)` so the written manifest's
    `osm_extract_date` lands ~870 days in the past; the verification step runs the
    query CLI without any patches so the warning fires against real `datetime.now(UTC)`.

    Story 2.9's setup-side parallel test asserts the warning on `steeproute-setup`;
    this test pins the same contract on `steeproute` — closing the test gap the
    code review flagged (Story 2.10 P8).
    """
    # Seed via setup with a stale `iso8601_utc_now` so the manifest's
    # `osm_extract_date` is way past the default 90-day threshold.
    stale_iso = "2024-01-01T00:00:00Z"
    with patch("steeproute.cli.setup.iso8601_utc_now", return_value=stale_iso):
        seed = _seed_setup(tmp_path)
    assert seed.exit_code == 0, seed.output

    # Run the query against the seeded entry. The cache-hit path reads the
    # stale manifest and `emit_osm_age_warning` fires via `_logger.warning`,
    # which `configure_cli_logging` routes to stderr at WARNING level.
    args = _query_args(tmp_path, center=(_CENTER_LAT, _CENTER_LON), radius_km=_FIXTURE_RADIUS_KM)
    exit_code, stderr = _invoke_query_with_wrapper(args)

    assert exit_code == 0, stderr
    # The warning text comes from `_OSM_AGE_WARNING_TEMPLATE` in `cli/_shared.py`.
    assert "OSM extract for this cache entry" in stderr
    assert "days old" in stderr
    assert "steeproute-setup --force-refresh" in stderr


def test_query_no_osm_age_warning_on_fresh_cache_hit(tmp_path: pathlib.Path) -> None:
    """Negative case: a fresh cache-hit on the query CLI does NOT emit the warning.

    Pairs with the stale-warning test above to pin the boundary semantics
    (warning fires iff `age_days > threshold_days`). The seed runs without
    patching `iso8601_utc_now` so the manifest's `osm_extract_date` is "now",
    safely under the 90-day threshold.
    """
    seed = _seed_setup(tmp_path)
    assert seed.exit_code == 0, seed.output

    args = _query_args(tmp_path, center=(_CENTER_LAT, _CENTER_LON), radius_km=_FIXTURE_RADIUS_KM)
    exit_code, stderr = _invoke_query_with_wrapper(args)

    assert exit_code == 0, stderr
    # No warning fires for a fresh entry — neither phrase appears on stderr.
    assert "OSM extract for this cache entry" not in stderr
    assert "steeproute-setup --force-refresh" not in stderr
