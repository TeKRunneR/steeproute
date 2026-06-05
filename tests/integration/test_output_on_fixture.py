# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx / networkx boundary as tests/integration/test_validator_on_fixture.py.
"""End-to-end render of real GRASP output on the Grenoble fixture (Story 3.10 AC #7).

Reuses the setup → climbs → contract → GRASP → validate chain from
`test_validator_on_fixture.py`, then renders the validated set and asserts the
files exist, parse as HTML, carry the map + elevation-profile sections, embed
real geometry, and stay self-contained.
"""

from __future__ import annotations

import html.parser
import importlib.util
import json
import pathlib
import re
from unittest.mock import patch

import networkx as nx
import numpy as np
import osmnx
import pytest

from steeproute import output
from steeproute.models import (
    Area,
    ContractedGraph,
    PipelineConfig,
    ProvenanceInfo,
    Solution,
    SolverParams,
)
from steeproute.pipeline import run_setup_stages
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import normalize_edges
from steeproute.solver.grasp import GraspSolver
from steeproute.validator import validate

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

_THETA = 0.20
_DIFFICULTY_CAP = "T3"
_L_CONNECTOR = 200.0
_MIN_CLIMB_GROUND_LENGTH_M = 300.0
_J_MAX = 0.30
_N = 3
_ITER_BUDGET = 100
_SEED = 42

_PROVENANCE = ProvenanceInfo(
    steeproute_version="0.0.0-test",
    git_commit_short="abc1234",
    git_dirty=False,
    osm_extract_date="2026-04-17",
    dem_version="RGEALTI-5M",
    pipeline_content_hash="fixturehash",
)


def _load_fixture_constants() -> tuple[float, float, int]:
    regen_path = _FIXTURE_DIR / "regenerate.py"
    spec = importlib.util.spec_from_file_location("_grenoble_small_regen_output", regen_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CENTER_LAT, module.CENTER_LON, module.DIST_M


_CENTER_LAT, _CENTER_LON, _DIST_M = _load_fixture_constants()


def _params() -> SolverParams:
    return SolverParams(
        theta=_THETA,
        min_climb_slope=_THETA,
        difficulty_cap=_DIFFICULTY_CAP,
        l_connector=_L_CONNECTOR,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=_J_MAX,
        n=_N,
        area_cap=500.0,
        untagged_policy="include",
        seed=_SEED,
        iter_budget=_ITER_BUDGET,
        time_budget=60.0,
        stagnation_iters=50,
    )


@pytest.fixture(scope="module")
def fixture_run() -> tuple[nx.MultiDiGraph, ContractedGraph, list[Solution]]:
    """Run setup → climbs → contract → GRASP once; return base graph + contracted + solutions."""

    def _osm_load_from_fixture(_area: Area) -> nx.MultiDiGraph:
        return normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    config = PipelineConfig(untagged_policy="include", dem_path=_DEM_FIXTURE_PATH)
    with patch("steeproute.pipeline.osm_load", _osm_load_from_fixture):
        base_graph = run_setup_stages(area, config)

    climbs = detect_climbs(
        base_graph, min_climb_slope=_THETA, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M
    )
    assert climbs, "expected >= 1 climb on the Grenoble Le Sappey fixture"
    contracted = contract_climbs(base_graph, climbs, l_connector=_L_CONNECTOR)

    solver = GraspSolver(contracted, _params(), np.random.default_rng(_SEED))
    solutions = solver.run()
    assert solutions, "expected >= 1 GRASP route on the Grenoble Le Sappey fixture"
    return base_graph, contracted, solutions


def test_render_real_fixture_writes_parseable_reports(
    fixture_run: tuple[nx.MultiDiGraph, ContractedGraph, list[Solution]],
    tmp_path: pathlib.Path,
) -> None:
    base_graph, contracted, solutions = fixture_run
    validated = validate(solutions, contracted, _params())

    area = Area(center=(_CENTER_LAT, _CENTER_LON), radius_km=_DIST_M / 1000.0)
    output.render(
        validated, base_graph, area, contracted, _params(), _PROVENANCE, "converged", tmp_path
    )

    n = len(validated.routes)
    assert n >= 1
    for i in range(1, n + 1):
        html_path = tmp_path / f"route-{i}.html"
        json_path = tmp_path / f"route-{i}.json"
        assert html_path.exists() and json_path.exists()

        html_text = html_path.read_text(encoding="utf-8")
        # Parses without error and carries the map + profile sections (FR17, FR18).
        html.parser.HTMLParser().feed(html_text)
        assert 'id="map"' in html_text
        assert 'id="elevation-profile"' in html_text
        assert "L.map(" in html_text
        assert "new Chart(" in html_text
        # Self-contained: no external resource tags (assets inlined).
        assert re.search(r"<script[^>]*\bsrc\s*=", html_text) is None
        assert re.search(r"<link\b", html_text) is None

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["route_index"] == i
        assert len(payload["vertices"]) >= 2  # real geometry resolved from the graph
        assert payload["metadata"]["params"]["seed"] == _SEED
