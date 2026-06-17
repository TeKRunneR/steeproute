# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx boundary as the underlying pipeline modules.
"""E2E-layer pytest fixtures.

Holds the shared Journey-1 (`steeproute` query happy-path) plumbing used by
`test_journey_1_happy_path.py`, `test_seeded_reproducibility.py`, and
`test_validation_failure_path.py`: seeding a real fixture cache in-process and
invoking the query CLI against it. Kept here (rather than copied per-file like
the older Story 2.x e2e tests) so the three Story 3.11 tests share one
seeding path.
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import Callable, Iterator

import networkx as nx
import osmnx
import pytest
from click.testing import CliRunner, Result

from steeproute.cli._shared import set_verbose
from steeproute.cli.query import cli as query_cli
from steeproute.cli.setup import cli as setup_cli
from steeproute.models import Area
from steeproute.pipeline.osm import normalize_edges

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"


def _load_fixture_constants() -> tuple[float, float, int]:
    """Mirror of the loader in `test_coverage_check.py` — center + bbox half-side."""
    regen_path = _FIXTURE_DIR / "regenerate.py"
    try:
        spec = importlib.util.spec_from_file_location("_grenoble_small_regen_e2e", regen_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.CENTER_LAT, module.CENTER_LON, module.DIST_M
    except (FileNotFoundError, ImportError, AttributeError):
        return (0.0, 0.0, 0)


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()
_FIXTURES_LOADED = _DIST_M > 0
FIXTURE_CENTER: tuple[float, float] = (_CENTER_LAT, _CENTER_LON)
# Seed the cache at the fixture's full bbox half-side; queries run at a strictly
# smaller radius so the FR24 coverage check (strict containment) succeeds.
FIXTURE_SEED_RADIUS_KM: float = _DIST_M / 1000.0 if _FIXTURES_LOADED else 0.0
FIXTURE_QUERY_RADIUS_KM: float = max(FIXTURE_SEED_RADIUS_KM - 0.5, 0.0)


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
def reset_verbose_flag() -> Iterator[None]:
    """Mirror of `tests/unit/conftest.py`'s autouse reset.

    The CLI e2e tests invoke commands through `CliRunner`, which routes
    `--verbose` through the eager callback in `cli/_shared.py`. Without this
    reset, a verbose-flagged test would pollute `_verbose=True` into the
    following test's process state and silently change `run_entry_point`'s
    `detail`-line rendering.
    """
    set_verbose(False)
    yield
    set_verbose(False)


@pytest.fixture(scope="session")
def seeded_cache(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Seed a real fixture cache entry in-process once per session; return the cache root.

    Runs the `steeproute-setup` CLI through `CliRunner` with both `pipeline.osm_load`
    and `cli.setup.resolve_dem` patched to the committed fixtures (offline). A real
    `uv run steeproute-setup` subprocess can't be patched and would hit Overpass +
    the IGN WMS, so seeding stays in-process — exactly the pattern
    `test_coverage_check.py` uses. Skips when the OSM/DEM fixtures aren't committed.

    Session-scoped: the setup pipeline is the dominant per-test cost (~2-3 s) and is
    identical every time, so it runs once and is reused. This is sound because the
    query side never writes the cache (NFR3, asserted by `test_interrupt.py`), and
    every consumer routes its `--output-dir` to its own `tmp_path` — so the shared
    cache root is read-only across tests.
    """
    from unittest.mock import patch

    if not _FIXTURES_LOADED or not _DEM_FIXTURE_PATH.exists() or not _OSM_FIXTURE_PATH.exists():
        pytest.skip("OSM or DEM fixture not committed; Journey-1 e2e tests skipped.")

    cache_root = tmp_path_factory.mktemp("seeded_cache")
    runner = CliRunner()
    args = [
        "--center",
        f"{_CENTER_LAT},{_CENTER_LON}",
        "--radius",
        f"{FIXTURE_SEED_RADIUS_KM}",
        "--cache-dir",
        str(cache_root),
    ]
    with (
        patch("steeproute.pipeline.osm_load", _osm_load_from_fixture),
        patch("steeproute.cli.setup.resolve_dem", _resolve_dem_from_fixture),
    ):
        result = runner.invoke(setup_cli, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return cache_root


@pytest.fixture
def run_query() -> Callable[..., Result]:
    """Return a helper that invokes the query CLI in-process against a seeded cache.

    Reads from the seeded cache without any patch (the query side touches no
    network). Returns the `CliRunner` `Result`, whose `exit_code` reflects the
    validation-driven `ctx.exit(...)` the CLI raises.
    """

    def _invoke(
        cache_dir: pathlib.Path,
        output_dir: pathlib.Path,
        *,
        center: tuple[float, float] = FIXTURE_CENTER,
        radius_km: float = FIXTURE_QUERY_RADIUS_KM,
        seed: int | None = 42,
        extra_args: list[str] | None = None,
    ) -> Result:
        args = [
            "--center",
            f"{center[0]},{center[1]}",
            "--radius",
            f"{radius_km}",
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(output_dir),
        ]
        if seed is not None:
            args += ["--seed", str(seed)]
        if extra_args:
            args += extra_args
        return CliRunner().invoke(query_cli, args, catch_exceptions=False)

    return _invoke


@pytest.fixture
def fixture_query_target() -> tuple[tuple[float, float], float]:
    """`(center, query_radius_km)` covering the seeded fixture cache.

    Exposes the raw values (rather than a `CliRunner` helper) so the
    real-subprocess interrupt test can build its own command line — see
    `test_interrupt.py`, which needs an OS process it can signal.
    """
    return FIXTURE_CENTER, FIXTURE_QUERY_RADIUS_KM
