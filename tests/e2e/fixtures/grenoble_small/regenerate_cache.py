# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx boundary as pipeline/osm.py and tests/e2e/conftest.py.
"""Regenerate the committed *queryable* cache root for the grenoble_small regression fixture.

Run from the repo root:

    uv run python tests/e2e/fixtures/grenoble_small/regenerate_cache.py

Unlike the committed `tests/fixtures/grenoble_small/cache/` (a bare manifest used
by the integration tests), this produces a full cache root — `steeproute/index.json`
+ `steeproute/areas/<hash>/{graph.pkl,bounds.geojson,manifest.json}` — that the
`steeproute` query CLI can run against with a plain `--cache-dir` and no patching.
It is what the regression harness (`tests/e2e/test_pinned_regressions.py`) and
`uv run update-regression` query to (re)build the golden.

**Two entries are prepared into this one cache root:** the original
axis-aligned square, and a rotated rectangle. The rotated entry is what gives the
rotated-rectangle golden something to *pin*: the query area only selects a cache
entry — it never clips the search — so a rotated query against the square entry
would reproduce the square golden's routes exactly and prove nothing. The
rotated-area behaviour lives in **setup**, where the graph is truncated to
the rotated ring, so the golden has to run against a genuinely rotated *prepared*
entry.

Offline: it seeds from the committed `tests/fixtures/grenoble_small/` OSM graphml +
DEM raster (patching `osm_load` / `resolve_dem`), exactly like `conftest.seeded_cache`,
so no Overpass / IGN-WMS access is needed. Re-run it whenever the OSM/DEM fixtures or
the setup-side pipeline change; then refresh the goldens with `uv run update-regression`.
"""

from __future__ import annotations

import pathlib
import shutil
from unittest.mock import patch

import networkx as nx
import osmnx
from click.testing import CliRunner

from steeproute.cache import area_polygon
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

# Rotated seed area. Bearing 45 deg — the SW-NE orientation a real diagonal
# mountain range takes. Sized so its axis-aligned envelope (1.91 km half-extent at
# this bearing) still fits inside the 2.0 km square the source graphml was fetched
# at, while dropping ~57% of that square's area — enough that the truncation
# visibly changes the graph, and therefore the routes.
SEED_WIDTH_KM = 3.4
SEED_HEIGHT_KM = 2.0
SEED_ANGLE_DEG = 45.0


def _osm_load_from_fixture(area: Area) -> nx.MultiDiGraph:
    """Stand in for stage 1, reproducing the real fetch's shape handling offline.

    The committed graphml *is* the square `graph_from_point(dist_type="bbox")`
    fetch, so a square area must be returned untouched — anything else moves the
    square golden. A non-square area gets the same treatment
    the real `graph_from_polygon` applies to a downloaded network: truncate to the
    ring, then keep the largest weakly-connected component. Reusing osmnx's own
    `truncate` functions is what makes this a faithful stand-in rather than a
    hand-rolled approximation of the truncation semantics.
    """
    graph = normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))
    if area.is_square:
        return graph
    graph = osmnx.truncate.truncate_graph_polygon(graph, area_polygon(area))
    return osmnx.truncate.largest_component(graph, strongly=False)


def _resolve_dem_from_fixture(
    _bounds: tuple[float, float, float, float], _cache_root: pathlib.Path, **_kwargs: object
) -> pathlib.Path:
    return _DEM_FIXTURE_PATH


def _prepare(area_args: list[str]) -> None:
    """Run one offline `steeproute-setup` into the shared cache root."""
    args = [
        "--center",
        f"{CENTER_LAT},{CENTER_LON}",
        *area_args,
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


def main() -> None:
    if _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR)
    _CACHE_DIR.mkdir(parents=True)

    _prepare(["--radius", f"{SEED_RADIUS_KM}"])
    _prepare(
        [
            "--width",
            f"{SEED_WIDTH_KM}",
            "--height",
            f"{SEED_HEIGHT_KM}",
            "--angle",
            f"{SEED_ANGLE_DEG}",
        ]
    )
    print(f"Queryable cache written to {_CACHE_DIR} (square + rotated entries)")


if __name__ == "__main__":
    main()
