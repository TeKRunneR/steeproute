# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportUnusedFunction=false
# Reason: same osmnx/networkx boundary as the underlying pipeline modules, plus
# `reportUnusedFunction` relaxed for the `_skip_if_fixtures_missing` autouse fixture.
"""End-to-end coverage for Story 2.9 — source-unavailable errors + OSM-age warning.

Three behaviors verified at the CLI boundary:

1. DEM unreadable (zero-byte `.tif`) → exit 2 + `error: DEM source unreachable`
   (rasterio fails on `rasterio.open`; the new wrap in `pipeline/dem.py` maps to
   `DataSourceUnavailableError`).

2. `osmnx.graph_from_point` raising `requests.ConnectionError` → exit 2 + `error: OSM
   source unreachable`. `--verbose` rerun surfaces the wrapped exception on the detail line.

3. Cache-hit on a stale entry (`osm_extract_date` > 90 days old) emits a
   `logging.warning(...)` on stderr suggesting `--force-refresh`; exit 0; the summary
   still reports `cache-hit`.

Pattern note: exit-code paths exercise `cli/setup.py::main` via the same `run_entry_point`
wrapper the installed `[project.scripts]` binary uses, so the test sees the same exit-2
mapping the end user does. `cli.main(args=[...], standalone_mode=False)` is the in-process
moral equivalent of "press enter at a shell prompt": click parses args, calls our command
body, and lets `PreExecutionError` propagate (vs. standalone_mode=True which would call
`sys.exit` after handling). We catch the propagating error in our own `_invoke_with_wrapper`,
formatting via `run_entry_point`'s logic so the assertions match what the binary writes.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib
import sys
import urllib.error
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

import networkx as nx
import osmnx
import pytest
import requests
from click.testing import CliRunner

from steeproute.cli._shared import set_verbose
from steeproute.cli.setup import cli as setup_cli
from steeproute.errors import PreExecutionError
from steeproute.models import Area
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"


def _load_fixture_constants() -> tuple[float, float, int, float, str, str]:
    """Mirror of the loader in `test_steeproute_setup.py`, returning derived flag strings too.

    Returns a 6-tuple `(lat, lon, dist_m, radius_km, center_flag, radius_flag)` when
    the loader succeeds; returns a sentinel tuple of zeros + empty-ish strings when
    `regenerate.py` is missing or unimportable. The autouse `_skip_if_fixtures_missing`
    fixture short-circuits any test invocation in the sentinel case, so the zeros are
    never used in practice — they exist purely to keep pytest's collection pass green
    when the data files are absent (partial fixture deletion, future refactor that
    drops the loader script while keeping the GeoTIFF and graphml, etc.).

    Returning the derived flag strings from the same function (rather than computing
    them at module-load time after the loader returns) avoids the basedpyright
    `reportConstantRedefinition` trip-up that fires when uppercase module-level names
    are assigned in different `if/else` branches.
    """
    regen_path = _FIXTURE_DIR / "regenerate.py"
    try:
        spec = importlib.util.spec_from_file_location("_grenoble_small_regen_2_9", regen_path)
        if spec is None or spec.loader is None:
            raise FileNotFoundError(regen_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        lat = float(module.CENTER_LAT)
        lon = float(module.CENTER_LON)
        dist_m = int(module.DIST_M)
        radius_km = dist_m / 1000.0
        return lat, lon, dist_m, radius_km, f"{lat},{lon}", f"{radius_km}"
    except (FileNotFoundError, ImportError, AttributeError):
        # Sentinel — never reached at test time thanks to `_skip_if_fixtures_missing`.
        return 0.0, 0.0, 0, 0.0, "0.0,0.0", "0.0"


_FIXTURES_LOADED = (_FIXTURE_DIR / "regenerate.py").exists()
_CENTER_LAT, _CENTER_LON, _DIST_M, _RADIUS_KM, _CENTER_FLAG, _RADIUS_FLAG = (
    _load_fixture_constants()
)


def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
    """Drop-in for `pipeline.osm_load` that reads the committed graphml fixture."""
    return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))


def _resolve_dem_from_fixture(
    _area: Area,
    _cache_root: pathlib.Path,
    **_kwargs: object,
) -> pathlib.Path:
    """Drop-in for `cli.setup.resolve_dem` returning the committed DEM fixture (offline)."""
    return _DEM_FIXTURE_PATH


@pytest.fixture(autouse=True)
def _skip_if_fixtures_missing() -> None:
    if not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists() or not _FIXTURES_LOADED:
        pytest.skip("OSM or DEM fixture not committed; source-unavailable e2e tests skipped.")


def _base_args(*, cache_dir: pathlib.Path) -> list[str]:
    return [
        "--center",
        _CENTER_FLAG,
        "--radius",
        _RADIUS_FLAG,
        "--cache-dir",
        str(cache_dir),
    ]


@contextmanager
def _capture_stderr() -> Generator[io.StringIO]:
    """Capture writes to `sys.stderr` for the duration of the block.

    `configure_cli_logging` calls `logging.basicConfig(stream=sys.stderr, force=True)`,
    so it picks up whatever `sys.stderr` points at when the CLI runs. Swapping in our
    own buffer here lets us see both `run_entry_point`'s `sys.stderr.write(...)` AND
    the `logger.warning(...)` lines the OSM-age helper emits.
    """
    buf = io.StringIO()
    saved = sys.stderr
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = saved


def _invoke_with_wrapper(args: list[str]) -> tuple[int, str]:
    """Invoke `cli/setup.py` through the same `run_entry_point` mapping the binary uses.

    Returns (exit_code, captured_stderr). `cli.main(standalone_mode=False)` runs the
    click command body in-process and re-raises `PreExecutionError` rather than
    catching it via click's standalone exit-handling — matching what
    `_invoke_command → run_entry_point` does in production.
    """
    with _capture_stderr() as stderr_buf:
        try:
            setup_cli.main(args=args, standalone_mode=False)
            return 0, stderr_buf.getvalue()
        except PreExecutionError as e:
            # Mirror `cli/_shared.py::run_entry_point` exactly: write the user_message,
            # then the detail line only if `_verbose` is set (the eager click callback
            # in `cli/_shared.py::_verbose_callback` already flipped it during parsing).
            from steeproute.cli._shared import is_verbose

            stderr_buf.write(f"error: {e.user_message}\n")
            if is_verbose() and e.detail is not None:
                stderr_buf.write(f"        {e.detail}\n")
            return 2, stderr_buf.getvalue()
        except KeyboardInterrupt:
            # Mirror `run_entry_point`'s exit-130 mapping. The helper docstring
            # advertises "mirror exactly"; missing this branch would let a Ctrl-C
            # during a slow in-process invocation produce a confusing uncaught
            # exception instead of the documented exit-130 contract.
            return 130, stderr_buf.getvalue()
        finally:
            # Defensive reset in case the call left `_verbose=True` and the autouse
            # fixture in `conftest.py` hasn't yet run for the next test.
            set_verbose(False)


# --- AC #4: DEM source unreachable -----------------------------------------------


def test_dem_source_unreachable(tmp_path: pathlib.Path) -> None:
    """AC #4: the IGN WMS download failing → exit 2 + `error: DEM source unreachable`.

    Patches the WMS `urlopen` to raise a `URLError` so the real `resolve_dem`
    error-mapping fires (`DataSourceUnavailableError → exit 2` via `run_entry_point`).
    """
    args = _base_args(cache_dir=tmp_path / "cache")
    with patch(
        "steeproute.pipeline.dem_download.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        exit_code, stderr = _invoke_with_wrapper(args)

    assert exit_code == 2
    # Story 14.3's tile-fetch retries emit `WARNING: ... retrying` lines to stderr
    # before the final error, so the error line is no longer the *first* line —
    # assert on the line itself (same style as the `--verbose` sibling test).
    assert "error: DEM source unreachable" in stderr, stderr


def test_dem_source_unreachable_verbose_surfaces_detail(tmp_path: pathlib.Path) -> None:
    """AC #2: `--verbose` surfaces the wrapped WMS failure on the detail line."""
    args = [*_base_args(cache_dir=tmp_path / "cache"), "--verbose"]
    with patch(
        "steeproute.pipeline.dem_download.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        exit_code, stderr = _invoke_with_wrapper(args)

    assert exit_code == 2
    assert "error: DEM source unreachable" in stderr
    # Detail line carries the wrapped WMS-fetch failure repr.
    assert "IGN WMS GetMap failed" in stderr


# --- AC #5: OSM source unreachable -----------------------------------------------


def test_osm_network_failure(tmp_path: pathlib.Path) -> None:
    """AC #5: `requests.ConnectionError` from `osmnx.graph_from_point` → exit 2 + OSM unreachable.

    Patches `osmnx.graph_from_point` (the call site the wrap in `pipeline/osm.py`
    sits around) so the production try/except actually fires. Patching
    `pipeline.osm_load` directly would bypass the wrap entirely — wrong target.
    """
    args = _base_args(cache_dir=tmp_path / "cache")
    with (
        patch("steeproute.cli.setup.resolve_dem", _resolve_dem_from_fixture),
        patch(
            "osmnx.graph_from_point",
            side_effect=requests.ConnectionError("Failed to establish a new connection"),
        ),
    ):
        exit_code, stderr = _invoke_with_wrapper(args)

    assert exit_code == 2
    assert stderr.startswith("error: OSM source unreachable"), stderr


def test_osm_network_failure_verbose_surfaces_detail(tmp_path: pathlib.Path) -> None:
    """AC #2 + #5: `--verbose` rerun surfaces the wrapped `ConnectionError` on the detail line."""
    args = [*_base_args(cache_dir=tmp_path / "cache"), "--verbose"]
    with (
        patch("steeproute.cli.setup.resolve_dem", _resolve_dem_from_fixture),
        patch(
            "osmnx.graph_from_point",
            side_effect=requests.ConnectionError("Failed to establish a new connection"),
        ),
    ):
        exit_code, stderr = _invoke_with_wrapper(args)

    assert exit_code == 2
    assert "error: OSM source unreachable" in stderr
    # The `from exc` chain puts the wrapped exception's repr on the detail line.
    assert "ConnectionError" in stderr


def test_osm_pipeline_wrapping_catches_requests_timeout(tmp_path: pathlib.Path) -> None:
    """AC #1: the wrap covers the whole `requests.exceptions.RequestException` family, not just ConnectionError."""
    args = _base_args(cache_dir=tmp_path / "cache")
    with (
        patch("steeproute.cli.setup.resolve_dem", _resolve_dem_from_fixture),
        patch(
            "osmnx.graph_from_point",
            side_effect=requests.exceptions.Timeout("Read timeout"),
        ),
    ):
        exit_code, stderr = _invoke_with_wrapper(args)

    assert exit_code == 2
    assert "error: OSM source unreachable" in stderr


# --- AC #7: OSM-age warning on cache-hit -----------------------------------------


def _invoke_cli_runner(
    *,
    cache_dir: pathlib.Path,
    osm_mode: str = "fixture",
    extra_args: tuple[str, ...] = (),
) -> tuple[int, str]:
    """In-process CliRunner invocation for happy-path (exit 0) cache-hit checks.

    Returns (exit_code, combined_stdout_plus_stderr). Reusing the same
    `CliRunner` pattern as `test_steeproute_setup.py` keeps the offline guarantee.

    `osm_mode` controls how `osm_load` is patched:
    - `"fixture"` (default): patch `pipeline.osm_load` with the offline fixture loader
      — used for seed (cache-miss) invocations.
    - `"fail_loud"`: patch `pipeline.osm_load` with a `RuntimeError` side-effect —
      used for cache-hit verification invocations so a regression where the hit
      branch silently reaches `osm_load` fails with a clear message instead of
      hanging on a real Overpass call or appearing to succeed under the fixture.
    """
    runner = CliRunner()
    args = [*_base_args(cache_dir=cache_dir), *extra_args]
    if osm_mode == "fixture":
        patcher = patch("steeproute.pipeline.osm_load", _osm_load_from_fixture)
    elif osm_mode == "fail_loud":
        patcher = patch(
            "steeproute.pipeline.osm_load",
            side_effect=RuntimeError("cache-hit branch must not call osm_load"),
        )
    else:
        raise ValueError(f"Unknown osm_mode: {osm_mode!r}")
    # `resolve_dem` is always patched to the fixture so the seed (cache-miss) run
    # never hits the IGN WMS; the cache-hit re-invocations don't call it at all.
    with patch("steeproute.cli.setup.resolve_dem", _resolve_dem_from_fixture), patcher:
        result = runner.invoke(setup_cli, args, catch_exceptions=False)
    return result.exit_code, result.output


def test_osm_age_warning_emitted_on_stale_cache_hit(tmp_path: pathlib.Path) -> None:
    """AC #7: a cache-hit on a stale entry (osm_extract_date >90d old) emits the warning.

    Seed pattern: patch `cli.setup.iso8601_utc_now` during the first invocation so
    the manifest gets a stale-dated `osm_extract_date`. Re-invoke without the patch;
    the cache-hit path now reads the stale manifest and `_emit_osm_age_warning`
    fires against the real `datetime.datetime.now(UTC)`.

    `configure_cli_logging(force=True)` rebinds the root logger handler to the
    current `sys.stderr` — which `CliRunner` swaps with its own buffer — so the
    warning text lands in `result.output` rather than `caplog.records`.
    """
    cache_dir = tmp_path / "cache"

    # Seed a cache entry whose manifest is dated > 90 days ago (~2 years here for safety).
    stale_iso = "2024-01-01T00:00:00Z"
    with patch("steeproute.cli.setup.iso8601_utc_now", return_value=stale_iso):
        seed_code, seed_output = _invoke_cli_runner(cache_dir=cache_dir)
    assert seed_code == 0, seed_output
    assert "cache-miss" in seed_output

    # Re-invoke with osm_load patched to a fail-loud RuntimeError: cache-hit must
    # not reach `osm_load`. A regression where the hit branch silently re-fetches
    # would surface here as a clear "cache-hit branch must not call osm_load"
    # message rather than (a) hanging on real Overpass or (b) succeeding under
    # the fixture loader and masking the bug.
    hit_code, hit_output = _invoke_cli_runner(
        cache_dir=cache_dir,
        osm_mode="fail_loud",
    )

    assert hit_code == 0, hit_output
    assert "cache-hit" in hit_output
    # The OSM-age warning landed on stderr (configure_cli_logging's StreamHandler).
    # `CliRunner.output` mixes stdout + stderr by default.
    assert "OSM extract for this cache entry" in hit_output
    assert "--force-refresh" in hit_output


def test_osm_age_warning_silent_on_fresh_cache_hit(tmp_path: pathlib.Path) -> None:
    """A fresh cache entry (just-written `osm_extract_date`) does NOT trigger the warning."""
    cache_dir = tmp_path / "cache"

    # Seed with the real `iso8601_utc_now` — the entry's `osm_extract_date` is "now".
    seed_code, seed_output = _invoke_cli_runner(cache_dir=cache_dir)
    assert seed_code == 0, seed_output

    # Re-invoke: cache-hit on a fresh entry, no warning. Fail-loud osm_load patch
    # so a hit-path regression surfaces clearly.
    hit_code, hit_output = _invoke_cli_runner(
        cache_dir=cache_dir,
        osm_mode="fail_loud",
    )

    assert hit_code == 0, hit_output
    assert "cache-hit" in hit_output
    # The fresh-entry summary should not contain the age-warning text.
    assert "OSM extract for this cache entry" not in hit_output


def test_osm_age_warning_threshold_override(tmp_path: pathlib.Path) -> None:
    """`--osm-age-warn-days 30` lowers the threshold so a 60-day-old entry warns.

    Seeds a manifest dated 60 days ago, then re-invokes with `--osm-age-warn-days 30`.
    The 60-day age > 30-day threshold → warning fires. Without the override (default 90),
    the same entry would be silent.
    """
    cache_dir = tmp_path / "cache"

    # Seed with an `osm_extract_date` 60 days in the past.
    import datetime

    now = datetime.datetime.now(datetime.UTC)
    sixty_days_ago = (now - datetime.timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with patch("steeproute.cli.setup.iso8601_utc_now", return_value=sixty_days_ago):
        _invoke_cli_runner(cache_dir=cache_dir)

    # Cache-hit with custom threshold of 30 days — 60 > 30 → warn. Fail-loud
    # osm_load patch so a hit-path regression surfaces clearly.
    exit_code, output = _invoke_cli_runner(
        cache_dir=cache_dir,
        osm_mode="fail_loud",
        extra_args=("--osm-age-warn-days", "30"),
    )
    assert exit_code == 0, output
    assert "cache-hit" in output
    assert "OSM extract for this cache entry" in output
