# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx boundary as pipeline/osm.py.
"""Live OSM integration test — calls Overpass; skipped in default CI.

Run locally with:

    uv run pytest -m live

Purpose: detect drift between the committed fixture and current Overpass data
(e.g. osmnx behavior change, OSM contributor activity in the test area). A small
drift is expected; the tolerance bands below are wide enough to absorb routine
edits.
"""

from __future__ import annotations

import importlib.util
import pathlib

import networkx as nx
import osmnx
import pytest

from steeproute.models import Area
from steeproute.pipeline.osm import osm_load

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"


def _load_fixture_constants() -> tuple[float, float, int]:
    """Import CENTER_LAT/CENTER_LON/DIST_M from the fixture's regenerate.py.

    `tests/fixtures/...` isn't on sys.path as a package, so we load by file path.
    Single source of truth for the live test's fetch parameters — drift is
    impossible because both call sites read from the same module.
    """
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen", regen_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CENTER_LAT, module.CENTER_LON, module.DIST_M


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()
_DRIFT_TOLERANCE = 0.10  # +/- 10% on node and edge counts


@pytest.mark.live
def test_live_osm_matches_fixture() -> None:
    """Fetching live OSM at the fixture's parameters yields a structurally
    similar graph (node and edge counts within +/-10%)."""
    fixture: nx.MultiDiGraph = osmnx.load_graphml(_FIXTURE_PATH)
    live = osm_load(Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0))

    fixture_nodes = fixture.number_of_nodes()
    fixture_edges = fixture.number_of_edges()
    live_nodes = live.number_of_nodes()
    live_edges = live.number_of_edges()

    assert fixture_nodes > 0 and fixture_edges > 0, (
        "Committed fixture has zero nodes or edges — fixture is corrupt or empty."
    )

    node_drift = abs(live_nodes - fixture_nodes) / fixture_nodes
    edge_drift = abs(live_edges - fixture_edges) / fixture_edges

    assert node_drift <= _DRIFT_TOLERANCE, (
        f"Node count drift {node_drift:.1%} exceeds {_DRIFT_TOLERANCE:.0%} "
        f"(fixture={fixture_nodes}, live={live_nodes}). "
        f"Either OSM activity in the area is unusually high, or the fixture is stale."
    )
    assert edge_drift <= _DRIFT_TOLERANCE, (
        f"Edge count drift {edge_drift:.1%} exceeds {_DRIFT_TOLERANCE:.0%} "
        f"(fixture={fixture_edges}, live={live_edges}). "
        f"Either OSM activity in the area is unusually high, or the fixture is stale."
    )
