# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: builds a tiny networkx MultiDiGraph fixture whose edge-attribute access
# surfaces as Unknown — same external-boundary pattern as the production modules.
"""Unit tests for `output.render` (Story 3.10) on crafted in-memory fixtures.

These exercise every metadata field, both banner branches, HTML self-containment,
the `route-<i>.{html,json}` filename pattern, and interrupt-safety — all without
the real Grenoble fixture (that runs in `tests/integration/test_output_on_fixture.py`).
"""

from __future__ import annotations

import json
import pathlib
import re

import networkx as nx
import pytest

from steeproute import output
from steeproute.models import (
    Area,
    ConstraintViolation,
    ContractedGraph,
    Edge,
    PairwiseViolation,
    ProvenanceInfo,
    Route,
    RouteMetrics,
    RouteValidation,
    SolverParams,
    ValidatedRouteSet,
)

# Distinctive values so a metadata-presence assertion can't match by accident.
_PARAMS = SolverParams(
    theta=0.21,
    min_climb_slope=0.19,
    difficulty_cap="T4",
    l_connector=222.0,
    min_climb_ground_length=333.0,
    j_max=0.27,
    n=29,
    area_cap=555.0,
    untagged_policy="exclude",
    seed=4242,
    iter_budget=1234,
    time_budget=66.0,
    stagnation_iters=77,
)
_PROVENANCE = ProvenanceInfo(
    steeproute_version="9.9.9",
    git_commit_short="deadbee",
    git_dirty=True,
    osm_extract_date="2026-04-20",
    dem_version="RGEALTI-5M-v2",
    pipeline_content_hash="abc123def456",
)
_CONVERGENCE = "budget-exhausted"
# Centered on the `_base_graph` vertices (~45.1, 6.1) so the bbox brackets the route.
_AREA = Area(center=(45.115, 6.115), radius_km=2.0)

# All distinctive metadata values that must appear in BOTH the HTML and the JSON.
_EXPECTED_METADATA_STRINGS = [
    "0.21",  # theta
    "0.19",  # min_climb_slope
    "T4",  # difficulty_cap
    "222.0",  # l_connector
    "333.0",  # min_climb_ground_length
    "0.27",  # j_max
    "29",  # n
    "555.0",  # area_cap
    "exclude",  # untagged_policy
    "4242",  # seed (FR29)
    "1234",  # iter_budget
    "66.0",  # time_budget
    "77",  # stagnation_iters
    "9.9.9",  # steeproute_version
    "deadbee-dirty",  # git_commit_short + dirty flag
    "2026-04-20",  # osm_extract_date
    "RGEALTI-5M-v2",  # dem_version
    "abc123def456",  # pipeline_content_hash
    "budget-exhausted",  # convergence_status
    "1.9.4",  # leaflet asset version
    "4.4.0",  # chart.js asset version
]


def _html_body(path: pathlib.Path) -> str:
    """Rendered HTML with the inlined `<script>`/`<style>` bodies stripped.

    The report inlines ~350 KB of Leaflet + Chart.js, so a raw substring search
    can match library text by accident. Stripping those blocks makes "this value
    is rendered in the document" assertions target the actual metadata/metrics.
    """
    html = path.read_text(encoding="utf-8")
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    return html


def _edge(u: int, v: int, key: int = 0, *, gradient: float = 0.25) -> Edge:
    return Edge(
        node_u=u,
        node_v=v,
        key=key,
        length_m=100.0,
        d_plus_m=25.0,
        d_minus_m=0.0,
        avg_gradient=gradient,
        sac_scale="hiking",
    )


def _base_graph() -> nx.MultiDiGraph:
    """Base graph with three edges, each carrying `(lat, lon, elev)` vertices."""
    g = nx.MultiDiGraph()
    g.add_edge(1, 2, key=0, vertices_resampled=[(45.10, 6.10, 1000.0), (45.11, 6.11, 1050.0)])
    g.add_edge(2, 3, key=0, vertices_resampled=[(45.11, 6.11, 1050.0), (45.12, 6.12, 1120.0)])
    g.add_edge(3, 4, key=0, vertices_resampled=[(45.12, 6.12, 1120.0), (45.13, 6.13, 1090.0)])
    # Reverse of the (3,4) connector, so a route can traverse the linking segment
    # both ways (Story 5.2 reusable connector) — used by the reuse-render test.
    g.add_edge(4, 3, key=0, vertices_resampled=[(45.13, 6.13, 1090.0), (45.12, 6.12, 1120.0)])
    return g


def _contracted() -> ContractedGraph:
    """A super-edge (1,3,0) expanding to base edges (1,2,0)+(2,3,0)."""
    return ContractedGraph(
        graph=nx.MultiDiGraph(),
        super_edge_to_base={(1, 3, 0): (_edge(1, 2), _edge(2, 3))},
    )


def _route(*, passed: bool = True, violations: list[ConstraintViolation] | None = None) -> Route:
    """A route = super-edge (1,3,0) + connector (3,4,0)."""
    return Route(
        edges=[_edge(1, 3, 0), _edge(3, 4, 0)],
        metrics=RouteMetrics(length_m=300.0, d_plus_m=120.0, d_minus_m=30.0, avg_gradient=0.4),
        validation=RouteValidation(passed=passed, violations=violations or []),
    )


def _render(
    tmp_path: pathlib.Path,
    routes: list[Route],
    set_violations: list[PairwiseViolation] | None = None,
) -> None:
    output.render(
        ValidatedRouteSet(routes=routes, set_violations=set_violations or []),
        _base_graph(),
        _AREA,
        _contracted(),
        _PARAMS,
        _PROVENANCE,
        _CONVERGENCE,
        tmp_path,
    )


def test_render_writes_one_html_and_json_per_route(tmp_path: pathlib.Path) -> None:
    _render(tmp_path, [_route(), _route()])
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["route-1.html", "route-1.json", "route-2.html", "route-2.json"]


def test_metadata_fields_present_in_both_html_and_json(tmp_path: pathlib.Path) -> None:
    _render(tmp_path, [_route()])
    body = _html_body(tmp_path / "route-1.html")  # asset blobs stripped
    json_text = (tmp_path / "route-1.json").read_text(encoding="utf-8")
    for needle in _EXPECTED_METADATA_STRINGS:
        assert needle in body, f"{needle!r} missing from rendered HTML metadata"
        assert needle in json_text, f"{needle!r} missing from JSON"


def test_json_sidecar_structure(tmp_path: pathlib.Path) -> None:
    _render(tmp_path, [_route()])
    payload = json.loads((tmp_path / "route-1.json").read_text(encoding="utf-8"))
    assert payload["route_index"] == 1
    assert payload["edges"] == [[1, 3, 0], [3, 4, 0]]
    # Super-edge (1,3,0) expands to (1,2,0)+(2,3,0); the shared join vertex at
    # (45.11, 6.11, 1050.0) is deduped, then connector (3,4,0) appends one new.
    assert payload["vertices"] == [
        [45.10, 6.10, 1000.0],
        [45.11, 6.11, 1050.0],
        [45.12, 6.12, 1120.0],
        [45.13, 6.13, 1090.0],
    ]
    assert payload["metadata"]["params"]["seed"] == 4242
    assert payload["metrics"] == {
        "length_m": 300.0,
        "d_plus_m": 120.0,
        "d_minus_m": 30.0,
        "avg_gradient": 0.4,
    }


def test_metrics_and_validation_summary_render_in_html(tmp_path: pathlib.Path) -> None:
    """Per-route metrics + the pass/fail summary appear in the HTML, not just JSON (AC #4)."""
    _render(tmp_path, [_route(passed=True)])
    body = _html_body(tmp_path / "route-1.html")  # asset blobs stripped
    assert "300" in body  # length_m 300.0 -> "%.0f"
    assert "120" in body  # d_plus_m
    assert "40.0" in body  # avg_gradient 0.4 -> 40.0 %
    assert "passed" in body  # validation summary


def test_rerender_overwrites_existing_files_in_place(tmp_path: pathlib.Path) -> None:
    """Re-running render into a populated dir overwrites route-<i> in place (AC #1, idempotent)."""
    _render(tmp_path, [_route(passed=True)])
    assert "VALIDATION FAILED" not in (tmp_path / "route-1.html").read_text(encoding="utf-8")

    violation = ConstraintViolation(
        constraint_id="slope_floor",
        detail="edge below slope floor",
        numeric={"observed": 0.05, "required": 0.21},
    )
    _render(tmp_path, [_route(passed=False, violations=[violation])])
    # route-1 reflects the second render — overwritten in place, not appended.
    assert "VALIDATION FAILED" in (tmp_path / "route-1.html").read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["route-1.html", "route-1.json"]


def test_route_with_unresolvable_edge_still_renders(tmp_path: pathlib.Path) -> None:
    """A validation-failed route whose edge isn't in the graph renders (banner), not crashes (FR28)."""
    bad = Route(
        edges=[_edge(99, 100, 0)],  # absent from base_graph, not a super-edge
        metrics=RouteMetrics(length_m=0.0, d_plus_m=0.0, d_minus_m=0.0, avg_gradient=0.0),
        validation=RouteValidation(
            passed=False,
            violations=[
                ConstraintViolation(
                    constraint_id="graph_membership",
                    detail="edge not in operational graph",
                    numeric={"observed": 0.0, "required": 1.0},
                )
            ],
        ),
    )
    _render(tmp_path, [bad])
    html = (tmp_path / "route-1.html").read_text(encoding="utf-8")
    assert "VALIDATION FAILED" in html
    assert "graph_membership" in html
    # Geometry unresolvable -> empty vertices, map/profile omitted, no crash.
    assert "Route geometry is unavailable" in html
    payload = json.loads((tmp_path / "route-1.json").read_text(encoding="utf-8"))
    assert payload["vertices"] == []


def test_render_handles_reusable_connector_traversed_twice(tmp_path: pathlib.Path) -> None:
    """A route reusing a short connector in both directions renders without error (Story 5.2).

    Story 5.2 lets an exempt short connector recur (both directions) in one
    route. The renderer iterates `route.edges` sequentially and only dedups the
    shared join vertex, so the connector's geometry is simply drawn on each
    traversal — confirm it does not assume edge-uniqueness and crash.
    """
    route = Route(
        edges=[_edge(1, 3, 0), _edge(3, 4, 0), _edge(4, 3, 0)],  # super, conn, conn-reverse
        metrics=RouteMetrics(length_m=400.0, d_plus_m=120.0, d_minus_m=60.0, avg_gradient=0.45),
        validation=RouteValidation(passed=True, violations=[]),
    )

    _render(tmp_path, [route])

    payload = json.loads((tmp_path / "route-1.json").read_text(encoding="utf-8"))
    # The connector identity appears twice (each direction) — not collapsed.
    assert payload["edges"] == [[1, 3, 0], [3, 4, 0], [4, 3, 0]]
    # Geometry resolved for every traversal; the round trip back to node 3's
    # vertex is present, so the polyline is non-empty and the render succeeded.
    assert payload["vertices"], "route geometry should be rendered, not empty"
    assert "VALIDATION FAILED" not in (tmp_path / "route-1.html").read_text(encoding="utf-8")


def test_no_banner_when_route_clean(tmp_path: pathlib.Path) -> None:
    _render(tmp_path, [_route(passed=True)])
    html = (tmp_path / "route-1.html").read_text(encoding="utf-8")
    assert "VALIDATION FAILED" not in html


def test_banner_present_when_route_fails(tmp_path: pathlib.Path) -> None:
    violation = ConstraintViolation(
        constraint_id="slope_floor",
        detail="edge below slope floor",
        numeric={"observed": 0.05, "required": 0.21},
    )
    _render(tmp_path, [_route(passed=False, violations=[violation])])
    html = (tmp_path / "route-1.html").read_text(encoding="utf-8")
    assert "VALIDATION FAILED" in html
    assert "slope_floor" in html
    assert "edge below slope floor" in html


def test_banner_present_on_both_routes_when_pairwise_violation(tmp_path: pathlib.Path) -> None:
    pv = PairwiseViolation(route_index_a=0, route_index_b=1, jaccard_observed=0.9, jaccard_max=0.27)
    _render(tmp_path, [_route(), _route()], set_violations=[pv])
    html1 = (tmp_path / "route-1.html").read_text(encoding="utf-8")
    html2 = (tmp_path / "route-2.html").read_text(encoding="utf-8")
    assert "VALIDATION FAILED" in html1
    assert "VALIDATION FAILED" in html2
    # route-1 banner references the other route (display index 2) and vice versa.
    assert "route 2" in html1
    assert "route 1" in html2
    # The pairwise violation is also machine-readable in both sidecars.
    side1 = json.loads((tmp_path / "route-1.json").read_text(encoding="utf-8"))
    assert side1["validation"]["pairwise_violations"][0]["jaccard_observed"] == 0.9


def test_html_is_self_contained_no_external_resource_tags(tmp_path: pathlib.Path) -> None:
    """No `<script src>`, `<link>`, or `<img src=http>` — assets are all inlined.

    Inlined `<script>`/`<style>` *bodies* may legitimately contain URL strings
    (e.g. Leaflet's built-in attribution link, or the OSM tile-layer template),
    so the assertion targets resource-loading tags, not raw substrings.
    """
    _render(tmp_path, [_route()])
    html = (tmp_path / "route-1.html").read_text(encoding="utf-8")
    assert re.search(r"<script[^>]*\bsrc\s*=", html) is None
    assert re.search(r"<link\b", html) is None
    assert re.search(r"<img[^>]*\bsrc\s*=\s*[\"']https?://", html) is None


def test_basemap_uses_referer_tolerant_provider(tmp_path: pathlib.Path) -> None:
    """Basemap is OSM-derived OpenTopoMap, not referer-gated tile.openstreetmap.org.

    The bare OSM tile server now 403s requests with no Referer (which a
    file://-opened report cannot send); the report uses the topographic,
    referer-tolerant OpenTopoMap provider (trails + contours) with OSM attribution.
    """
    _render(tmp_path, [_route()])
    html = (tmp_path / "route-1.html").read_text(encoding="utf-8")  # raw: tile URL is in a <script>
    assert "tile.opentopomap.org" in html
    assert "{s}.tile.openstreetmap.org" not in html  # the old referer-gated tile URL is gone
    assert "OpenStreetMap contributors" in html
    assert "OpenTopoMap" in html


def test_map_and_profile_hover_linking_wired(tmp_path: pathlib.Path) -> None:
    """The map polyline and the elevation profile are hover-linked both directions."""
    _render(tmp_path, [_route()])
    html = (tmp_path / "route-1.html").read_text(encoding="utf-8")  # raw: wiring is in a <script>
    assert "circleMarker" in html  # map highlight marker
    assert 'line.on("mousemove"' in html  # map -> profile
    assert "setActiveElements" in html  # profile point activation
    assert "onHover" in html  # profile -> map


def test_search_area_overlay_wired(tmp_path: pathlib.Path) -> None:
    """The 2*radius_km query bbox is drawn as an L.rectangle from the injected bbox."""
    _render(tmp_path, [_route()])
    html = (tmp_path / "route-1.html").read_text(encoding="utf-8")  # raw: overlay is in a <script>
    assert "L.rectangle" in html
    assert "searchBbox" in html
    # The injected bbox carries the area center's degrees (equirectangular deltas).
    # 45.115 ± 2.0/111.32 -> south ~45.097, north ~45.133 (full float in tojson).
    assert "45.09703377650018" in html  # south
    assert "45.13296622349982" in html  # north
    # Initial view stays fitted to the ROUTE, not the box.
    assert "line.getBounds()" in html


def test_slope_tooltip_and_diverging_coloring_wired(tmp_path: pathlib.Path) -> None:
    """The profile tooltip emits a signed slope line and coloring is signed-diverging."""
    _render(tmp_path, [_route()])
    html = (tmp_path / "route-1.html").read_text(encoding="utf-8")  # raw: wiring is in a <script>
    assert "tooltip" in html
    assert "callbacks" in html
    assert "slope:" in html
    # Diverging mapper keys off the sign of the slope (ascent red vs descent blue).
    assert "gradientColor" in html
    assert "s >= 0" in html


def test_interrupt_mid_render_leaves_no_partial_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A KeyboardInterrupt during the atomic rename leaves neither a target nor a `.tmp`."""

    def _boom(_src: object, _dst: object) -> None:
        raise KeyboardInterrupt

    # `cache.py` does `import os`, so patching `os.replace` as seen by that module
    # intercepts the atomic rename. String target avoids importing the private `os`.
    monkeypatch.setattr("steeproute.cache.os.replace", _boom)
    with pytest.raises(KeyboardInterrupt):
        _render(tmp_path, [_route()])
    # The except-clause in `write_text_atomic` cleans up the `.tmp` sibling; no
    # half-written `route-1.html` (or `.tmp`) survives.
    assert sorted(p.name for p in tmp_path.iterdir()) == []
