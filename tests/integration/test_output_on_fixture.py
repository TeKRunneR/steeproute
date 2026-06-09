# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: same osmnx / networkx boundary as tests/integration/test_validator_on_fixture.py;
# `reportImplicitRelativeImport` — `from conftest import ...` is the shape that resolves
# under pytest's prepend import mode (see test_oracle_correctness.py for the rationale).
"""End-to-end render of real GRASP output on the Grenoble fixture (Story 3.10 AC #7).

Runs GRASP on the shared `grenoble_fixture` (tests/integration/conftest.py),
renders the validated set, and asserts the files exist, parse as HTML, carry the
map + elevation-profile sections, embed real geometry, and stay self-contained.
"""

from __future__ import annotations

import html.parser
import json
import pathlib
import re

import numpy as np
import pytest
from conftest import (
    GRENOBLE_DIFFICULTY_CAP,
    GRENOBLE_J_MAX,
    GRENOBLE_L_CONNECTOR,
    GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
    GRENOBLE_SEED,
    GRENOBLE_THETA,
    GrenobleFixture,
)

from steeproute import output
from steeproute.models import ProvenanceInfo, Solution, SolverParams
from steeproute.solver.grasp import GraspSolver
from steeproute.validator import validate

_N = 3
_ITER_BUDGET = 100

_PROVENANCE = ProvenanceInfo(
    steeproute_version="0.0.0-test",
    git_commit_short="abc1234",
    git_dirty=False,
    osm_extract_date="2026-04-17",
    dem_version="RGEALTI-5M",
    pipeline_content_hash="fixturehash",
)


def _params() -> SolverParams:
    return SolverParams(
        theta=GRENOBLE_THETA,
        min_climb_slope=GRENOBLE_THETA,
        difficulty_cap=GRENOBLE_DIFFICULTY_CAP,
        l_connector=GRENOBLE_L_CONNECTOR,
        min_climb_ground_length=GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=GRENOBLE_J_MAX,
        n=_N,
        area_cap=500.0,
        untagged_policy="include",
        seed=GRENOBLE_SEED,
        iter_budget=_ITER_BUDGET,
        # Story 7.2 made time/stagnation termination live; disable stagnation so
        # the result stays an iter-budget-only function of the seed (the
        # assertions below pin that exact route set). time_budget can't bind on
        # this small fixture's ~100 fast iterations.
        time_budget=60.0,
        stagnation_iters=0,
    )


@pytest.fixture(scope="module")
def grasp_solutions(grenoble_fixture: GrenobleFixture) -> list[Solution]:
    """Run GRASP once on the shared contracted graph; return its routes."""
    solver = GraspSolver(
        grenoble_fixture.contracted, _params(), np.random.default_rng(GRENOBLE_SEED)
    )
    solutions = solver.run()
    assert solutions, "expected >= 1 GRASP route on the Grenoble Le Sappey fixture"
    return solutions


def test_render_real_fixture_writes_parseable_reports(
    grenoble_fixture: GrenobleFixture,
    grasp_solutions: list[Solution],
    tmp_path: pathlib.Path,
) -> None:
    validated = validate(grasp_solutions, grenoble_fixture.contracted, _params())

    output.render(
        validated,
        grenoble_fixture.base_graph,
        grenoble_fixture.area,
        grenoble_fixture.contracted,
        _params(),
        _PROVENANCE,
        "converged",
        tmp_path,
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
        assert payload["metadata"]["params"]["seed"] == GRENOBLE_SEED
