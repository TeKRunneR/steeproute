# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx boundary as pipeline/osm.py and tests/e2e/conftest.py.
"""Regenerate the committed *queryable* cache root for the grenoble_small regression fixture.

Run from the repo root:

    uv run python tests/e2e/fixtures/grenoble_small/regenerate_cache.py

Unlike the committed `tests/fixtures/grenoble_small/cache/` (a bare manifest used
by the integration tests), this produces a full cache root — `steeproute/index.json`
+ `steeproute/areas/<hash>/{graph.pkl,bounds.geojson,manifest.json}` — that the
`steeproute` query CLI can run against with a plain `--cache-dir` and no patching.
It is what the Story 8.1 regression harness (`tests/e2e/test_pinned_regressions.py`)
and `uv run update-regression` query to (re)build the golden.

Offline: it seeds from the committed `tests/fixtures/grenoble_small/` OSM graphml +
DEM raster (patching `osm_load` / `resolve_dem`), exactly like `conftest.seeded_cache`,
so no Overpass / IGN-WMS access is needed. Re-run it whenever the OSM/DEM fixtures or
the setup-side pipeline change; then refresh the golden with `uv run update-regression`.
"""

from __future__ import annotations

import pathlib
import shutil
from unittest.mock import patch

import networkx as nx
import osmnx
from click.testing import CliRunner

from steeproute.cli.setup import cli as setup_cli
from steeproute.models import Area
from steeproute.pipeline.osm import normalize_edges

_HERE = pathlib.Path(__file__).resolve().parent
_SRC_FIXTURE = _HERE.parents[2] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _SRC_FIXTURE / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _SRC_FIXTURE / "dem.tif"
_CACHE_DIR = _HERE / "cache"

# Mirror of tests/fixtures/grenoble_small/regenerate.py — the seed area.
CENTER_LAT = 45.260
CENTER_LON = 5.788
SEED_RADIUS_KM = 2.0


def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
    return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))


def _resolve_dem_from_fixture(
    _area: Area, _cache_root: pathlib.Path, **_kwargs: object
) -> pathlib.Path:
    return _DEM_FIXTURE_PATH


def main() -> None:
    if _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR)
    _CACHE_DIR.mkdir(parents=True)

    args = [
        "--center",
        f"{CENTER_LAT},{CENTER_LON}",
        "--radius",
        f"{SEED_RADIUS_KM}",
        "--cache-dir",
        str(_CACHE_DIR),
    ]
    with (
        patch("steeproute.pipeline.osm_load", _osm_load_from_fixture),
        patch("steeproute.cli.setup.resolve_dem", _resolve_dem_from_fixture),
    ):
        result = CliRunner().invoke(setup_cli, args, catch_exceptions=False)
    if result.exit_code != 0:
        raise SystemExit(f"steeproute-setup failed:\n{result.output}")
    print(result.output)
    print(f"Queryable cache written to {_CACHE_DIR}")


if __name__ == "__main__":
    main()
