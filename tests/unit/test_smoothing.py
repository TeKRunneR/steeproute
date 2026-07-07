# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx/shapely boundary as pipeline/osm.py + pipeline/smoothing.py.
"""Unit tests for pipeline.smoothing: smooth_polylines (stage 3) + resample_edges (stage 4)."""

from __future__ import annotations

import math
import pathlib

import networkx as nx
import osmnx
import pytest
import shapely
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from steeproute.pipeline.osm import normalize_edges
from steeproute.pipeline.smoothing import (
    ELEVATION_DEADBAND_DEFAULT_M,
    ELEVATION_SMOOTHING_DEFAULT_M,
    RESAMPLE_SPACING_M,
    SMOOTHING_WINDOW,
    graph_deadband_elevation,
    graph_smooth_elevation,
    is_valid_polyline,
    resample_edges,
    smooth_polylines,
)

_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "grenoble_small"
    / "osm_graph.graphml"
)
_EARTH_RADIUS_M = 6_378_137.0
_DEG_TO_M_LAT = _EARTH_RADIUS_M * math.radians(1.0)


def _single_edge_graph(coords: list[tuple[float, float]]) -> nx.MultiDiGraph:
    """Build a one-edge MultiDiGraph carrying the source-attribute contract from Story 2.1."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=coords[0][0], y=coords[0][1])
    g.add_node(1, x=coords[-1][0], y=coords[-1][1])
    g.add_edge(
        0,
        1,
        key=0,
        geometry=shapely.LineString(coords),
        sac_scale="hiking",
        highway="path",
        osm_way_id=12345,
    )
    return g


def _equirectangular_distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Approximate ground-meter distance between two (lon, lat) points at low latitudes."""
    mean_lat = (a[1] + b[1]) / 2
    deg_to_m_lon = _DEG_TO_M_LAT * math.cos(math.radians(mean_lat))
    dx = (b[0] - a[0]) * deg_to_m_lon
    dy = (b[1] - a[1]) * _DEG_TO_M_LAT
    return math.hypot(dx, dy)


# --- module-scope constants ---


def test_smoothing_window_is_module_constant() -> None:
    """AC #2: window size lives at module scope as a named constant, not inline."""
    assert isinstance(SMOOTHING_WINDOW, int) and SMOOTHING_WINDOW >= 3


def test_resample_spacing_default_is_module_constant() -> None:
    """AC #2: default spacing lives at module scope as a named constant."""
    assert isinstance(RESAMPLE_SPACING_M, float) and RESAMPLE_SPACING_M > 0
    assert RESAMPLE_SPACING_M == 10.0


# --- smooth_polylines: analytical correctness ---


def test_smooth_polylines_straight_line_unchanged() -> None:
    """AC #5: equally-spaced collinear input is preserved by symmetric moving average."""
    coords = [(0.0, 0.0), (0.0001, 0.0), (0.0002, 0.0), (0.0003, 0.0), (0.0004, 0.0)]
    g = _single_edge_graph(coords)
    out = smooth_polylines(g)
    out_coords = list(out.edges[0, 1, 0]["geometry"].coords)
    assert len(out_coords) == len(coords)
    for original, smoothed in zip(coords, out_coords, strict=True):
        assert math.isclose(smoothed[0], original[0], abs_tol=1e-12)
        assert math.isclose(smoothed[1], original[1], abs_tol=1e-12)


def test_smooth_polylines_zigzag_reduces_perpendicular_drift() -> None:
    """AC #5: noisy zigzag input has reduced max-perp-distance from u->v baseline."""
    coords = [
        (0.0, 0.0),
        (0.0001, 1e-5),
        (0.0002, -1e-5),
        (0.0003, 1e-5),
        (0.0004, -1e-5),
        (0.0005, 0.0),
    ]
    g = _single_edge_graph(coords)
    out = smooth_polylines(g)
    out_coords = list(out.edges[0, 1, 0]["geometry"].coords)

    # Baseline is the x-axis (y == 0 between (0,0) and (0.0005, 0)) — perp distance is |y|.
    in_max_perp = max(abs(y) for _x, y in coords)
    out_max_perp = max(abs(y) for _x, y in out_coords)
    assert out_max_perp < in_max_perp


def test_smooth_polylines_preserves_endpoints_exactly() -> None:
    """AC #3: first and last coords of the smoothed polyline equal the input's exactly."""
    coords = [(0.0, 0.0), (0.0001, 1e-5), (0.0002, -1e-5), (0.0003, 0.0)]
    g = _single_edge_graph(coords)
    out = smooth_polylines(g)
    out_coords = list(out.edges[0, 1, 0]["geometry"].coords)
    assert out_coords[0] == coords[0]
    assert out_coords[-1] == coords[-1]


def test_smooth_polylines_does_not_mutate_input() -> None:
    """Pure-function discipline: input graph is unchanged after smooth_polylines."""
    coords = [(0.0, 0.0), (0.0001, 1e-5), (0.0002, -1e-5), (0.0003, 0.0)]
    g = _single_edge_graph(coords)
    before = list(g.edges[0, 1, 0]["geometry"].coords)
    _ = smooth_polylines(g)
    after = list(g.edges[0, 1, 0]["geometry"].coords)
    assert before == after


def test_smooth_polylines_preserves_attribute_contract() -> None:
    """sac_scale, highway, osm_way_id carry through stage 3 unchanged."""
    coords = [(0.0, 0.0), (0.0001, 0.0), (0.0002, 0.0)]
    g = _single_edge_graph(coords)
    out = smooth_polylines(g)
    data = out.edges[0, 1, 0]
    assert data["sac_scale"] == "hiking"
    assert data["highway"] == "path"
    assert data["osm_way_id"] == 12345
    assert isinstance(data["geometry"], shapely.LineString)


# --- resample_edges: analytical correctness ---


def test_resample_edges_uniform_spacing_within_tolerance() -> None:
    """AC #5: consecutive output vertices are within tolerance of `spacing_m`.

    Polyline at lat=0, length ~111 m (1e-3 deg lon). At spacing_m=10 we expect
    11 intervals of ~10.12 m each. Tolerance choices:
    - `rel_tol=1e-3` between pairs absorbs the equirectangular-projection
      round-trip drift (sub-‰ at edge scale; see module docstring).
    - `abs_tol=0.5 m` on the mean accounts for `round(total / spacing_m)`
      snapping the interval count to the nearest integer.
    """
    coords = [(0.0, 0.0), (1e-3, 0.0)]
    g = _single_edge_graph(coords)
    out = resample_edges(g, spacing_m=10.0)
    out_coords = list(out.edges[0, 1, 0]["geometry"].coords)

    distances = [
        _equirectangular_distance_m(out_coords[i], out_coords[i + 1])
        for i in range(len(out_coords) - 1)
    ]
    expected = sum(distances) / len(distances)
    for d in distances:
        assert math.isclose(d, expected, rel_tol=1e-3)
    assert math.isclose(expected, 10.0, abs_tol=0.5)


def test_resample_edges_endpoints_match_input_exactly() -> None:
    """AC #3: first and last output coords are bit-for-bit equal to input's."""
    coords = [(5.123456, 45.678901), (5.123556, 45.678801), (5.123756, 45.678701)]
    g = _single_edge_graph(coords)
    out = resample_edges(g, spacing_m=10.0)
    out_coords = list(out.edges[0, 1, 0]["geometry"].coords)
    assert out_coords[0] == coords[0]
    assert out_coords[-1] == coords[-1]


def test_resample_edges_short_segment_keeps_two_points() -> None:
    """A segment shorter than `spacing_m` collapses to just [first, last]."""
    coords = [(0.0, 0.0), (1e-5, 0.0)]  # ~1.1 m
    g = _single_edge_graph(coords)
    out = resample_edges(g, spacing_m=10.0)
    out_coords = list(out.edges[0, 1, 0]["geometry"].coords)
    assert len(out_coords) == 2
    assert out_coords[0] == coords[0]
    assert out_coords[-1] == coords[-1]


def test_resample_edges_does_not_mutate_input() -> None:
    """Pure-function discipline: input graph is unchanged after resample_edges."""
    coords = [(0.0, 0.0), (1e-3, 0.0)]
    g = _single_edge_graph(coords)
    before = list(g.edges[0, 1, 0]["geometry"].coords)
    _ = resample_edges(g, spacing_m=10.0)
    after = list(g.edges[0, 1, 0]["geometry"].coords)
    assert before == after


def test_resample_edges_preserves_attribute_contract() -> None:
    """sac_scale, highway, osm_way_id carry through stage 4 unchanged."""
    coords = [(0.0, 0.0), (1e-3, 0.0)]
    g = _single_edge_graph(coords)
    out = resample_edges(g, spacing_m=10.0)
    data = out.edges[0, 1, 0]
    assert data["sac_scale"] == "hiking"
    assert data["highway"] == "path"
    assert data["osm_way_id"] == 12345
    assert isinstance(data["geometry"], shapely.LineString)


def test_resample_edges_rejects_non_positive_spacing() -> None:
    coords = [(0.0, 0.0), (1e-3, 0.0)]
    g = _single_edge_graph(coords)
    with pytest.raises(ValueError, match="positive finite"):
        _ = resample_edges(g, spacing_m=0.0)
    with pytest.raises(ValueError, match="positive finite"):
        _ = resample_edges(g, spacing_m=-5.0)


def test_resample_edges_rejects_non_finite_spacing() -> None:
    coords = [(0.0, 0.0), (1e-3, 0.0)]
    g = _single_edge_graph(coords)
    with pytest.raises(ValueError, match="positive finite"):
        _ = resample_edges(g, spacing_m=float("nan"))
    with pytest.raises(ValueError, match="positive finite"):
        _ = resample_edges(g, spacing_m=float("inf"))


# --- degenerate-edge handling (carry-forward from Story 2.1) ---


def test_smooth_polylines_drops_zero_length_edge() -> None:
    """AC #4: an edge with coincident endpoints is dropped from the output graph."""
    coords = [(0.0, 0.0), (0.0, 0.0)]
    g = _single_edge_graph(coords)
    out = smooth_polylines(g)
    assert (0, 1, 0) not in out.edges
    # Nodes are kept (orphan-pruning is Story 2.5's call).
    assert 0 in out.nodes and 1 in out.nodes


def test_resample_edges_drops_zero_length_edge() -> None:
    coords = [(0.0, 0.0), (0.0, 0.0)]
    g = _single_edge_graph(coords)
    out = resample_edges(g, spacing_m=10.0)
    assert (0, 1, 0) not in out.edges


def test_smooth_polylines_drops_edge_with_all_identical_coords() -> None:
    """Three or more identical coords → still no distinct points → dropped."""
    coords = [(1.0, 2.0), (1.0, 2.0), (1.0, 2.0)]
    g = _single_edge_graph(coords)
    out = smooth_polylines(g)
    assert (0, 1, 0) not in out.edges


@pytest.mark.filterwarnings(
    "ignore::RuntimeWarning"
)  # shapely emits when LineString built from NaN coords
def test_resample_edges_drops_edge_with_non_finite_coord() -> None:
    coords = [(0.0, 0.0), (float("nan"), 0.0), (1e-3, 0.0)]
    g = _single_edge_graph(coords)
    out = resample_edges(g, spacing_m=10.0)
    assert (0, 1, 0) not in out.edges


# --- contract violations on edge geometry: fail-fast (P0 from review) ---


def test_smooth_polylines_raises_typeerror_on_non_linestring_geometry() -> None:
    """Non-LineString geometry on a pipeline edge is an upstream contract violation."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=0.0, y=0.0)
    g.add_node(1, x=1e-3, y=0.0)
    g.add_edge(
        0,
        1,
        key=0,
        geometry=shapely.MultiLineString([[(0.0, 0.0), (1e-3, 0.0)], [(1e-3, 0.0), (2e-3, 0.0)]]),
    )
    with pytest.raises(TypeError, match="must be a shapely.LineString"):
        _ = smooth_polylines(g)


def test_resample_edges_raises_typeerror_on_missing_geometry() -> None:
    """A graph edge without a `geometry` attribute is a contract violation, not silent-drop."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=0.0, y=0.0)
    g.add_node(1, x=1e-3, y=0.0)
    g.add_edge(0, 1, key=0)  # no geometry kwarg → data.get("geometry") returns None
    with pytest.raises(TypeError, match="must be a shapely.LineString"):
        _ = resample_edges(g, spacing_m=10.0)


def test_smooth_polylines_strips_3d_linestring_to_2d() -> None:
    """3D LineStrings (z-component present) should be handled, not crash on tuple unpack."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=0.0, y=0.0)
    g.add_node(1, x=1e-3, y=0.0)
    g.add_edge(
        0,
        1,
        key=0,
        geometry=shapely.LineString([(0.0, 0.0, 100.0), (5e-4, 0.0, 110.0), (1e-3, 0.0, 120.0)]),
    )
    out = smooth_polylines(g)
    assert (0, 1, 0) in out.edges
    out_coords = list(out.edges[0, 1, 0]["geometry"].coords)
    # z dropped; output is 2D
    assert all(len(c) == 2 for c in out_coords)


# --- multi-edge graph: dropping does not affect surviving edges ---


def test_smooth_polylines_keeps_valid_edges_when_one_is_degenerate() -> None:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=0.0, y=0.0)
    g.add_node(1, x=1e-3, y=0.0)
    g.add_node(2, x=2e-3, y=0.0)
    g.add_edge(0, 1, key=0, geometry=shapely.LineString([(0.0, 0.0), (1e-3, 0.0)]))
    g.add_edge(1, 2, key=0, geometry=shapely.LineString([(1e-3, 0.0), (1e-3, 0.0)]))  # degenerate
    out = smooth_polylines(g)
    assert (0, 1, 0) in out.edges
    assert (1, 2, 0) not in out.edges


# --- real OSM fixture: attribute-contract preservation ---


@pytest.fixture(scope="module")
def fixture_graph() -> nx.MultiDiGraph:
    """Load and normalize the committed real-OSM fixture once per module."""
    graph: nx.MultiDiGraph = osmnx.load_graphml(_FIXTURE_PATH)
    return normalize_edges(graph)


def test_fixture_smoothed_then_resampled_preserves_contract(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    """AC #6: source-attribute contract carried through unchanged after stages 3 -> 4.

    Snapshot input attrs per edge before running the stages, then assert each
    output edge's `sac_scale` / `highway` / `osm_way_id` equals its input's
    (not just key presence — a buggy stage that silently rewrote `highway`
    from `"path"` to `None` would pass a key-presence check).
    """
    input_attrs: dict[tuple[int, int, int], tuple[object, object, object]] = {
        (u, v, k): (data.get("sac_scale"), data.get("highway"), data.get("osm_way_id"))
        for u, v, k, data in fixture_graph.edges(data=True, keys=True)
    }
    smoothed = smooth_polylines(fixture_graph)
    resampled = resample_edges(smoothed, spacing_m=RESAMPLE_SPACING_M)
    assert resampled.number_of_edges() > 0
    for u, v, k, data in resampled.edges(data=True, keys=True):
        geom = data["geometry"]
        assert isinstance(geom, shapely.LineString)
        assert not geom.is_empty
        coords = list(geom.coords)
        assert len(coords) >= 2
        in_sac, in_hwy, in_way = input_attrs[(u, v, k)]
        assert data.get("sac_scale") == in_sac
        assert data.get("highway") == in_hwy
        assert data.get("osm_way_id") == in_way


def test_fixture_pipeline_endpoints_match_node_coords(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    """AC #3: after stages 3 -> 4, edge endpoints still match their node coords."""
    smoothed = smooth_polylines(fixture_graph)
    resampled = resample_edges(smoothed, spacing_m=RESAMPLE_SPACING_M)
    for u, v, _k, data in resampled.edges(data=True, keys=True):
        coords = list(data["geometry"].coords)
        u_xy = (resampled.nodes[u]["x"], resampled.nodes[u]["y"])
        v_xy = (resampled.nodes[v]["x"], resampled.nodes[v]["y"])
        assert coords[0] == u_xy
        assert coords[-1] == v_xy


# --- hypothesis property: endpoint preservation under any valid input ---


@given(
    coords=st.lists(
        st.tuples(
            st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        ),
        min_size=2,
        max_size=10,
    ),
)
@settings(max_examples=50, deadline=None)
def test_resample_edges_property_endpoints_exact(coords: list[tuple[float, float]]) -> None:
    """AC #7: for any valid polyline, resampled output's first and last == input's first and last.

    Use the production `is_valid_polyline` predicate via `hypothesis.assume` so
    the strategy filter and the stage's degenerate-edge guard agree on what
    counts as valid — eliminates the silent-no-op branch that a rounding-based
    filter would leave behind when float jitter disagrees with raw equality.
    """
    assume(is_valid_polyline(coords))
    g = _single_edge_graph(coords)
    out = resample_edges(g, spacing_m=1000.0)  # large spacing keeps n_intervals tractable
    out_coords = list(out.edges[0, 1, 0]["geometry"].coords)
    assert out_coords[0] == coords[0]
    assert out_coords[-1] == coords[-1]


# === Stage 6: median_smooth_elevation ===========================================


def _single_edge_graph_with_elevation(
    vertices_resampled: list[tuple[float, float, float]],
) -> nx.MultiDiGraph:
    """Build a one-edge MultiDiGraph carrying the stage-5 contract: `vertices_resampled`
    as (lat, lon, elev) triples plus the source attributes from Story 2.1."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    first_lat, first_lon, _ = vertices_resampled[0]
    last_lat, last_lon, _ = vertices_resampled[-1]
    g.add_node(0, x=first_lon, y=first_lat)
    g.add_node(1, x=last_lon, y=last_lat)
    # Mirror stage 5 output: geometry is still (lon, lat) shapely convention.
    geom = shapely.LineString([(lon, lat) for lat, lon, _ in vertices_resampled])
    g.add_edge(
        0,
        1,
        key=0,
        geometry=geom,
        vertices_resampled=vertices_resampled,
        sac_scale="hiking",
        highway="path",
        osm_way_id=12345,
    )
    return g


def _two_edges_sharing_node(
    edge_a: list[tuple[float, float, float]],
    edge_b: list[tuple[float, float, float]],
) -> nx.MultiDiGraph:
    """Build a 2-edge graph (0->1, 1->2) that share node 1 (edge_a[-1] == edge_b[0])."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=edge_a[0][1], y=edge_a[0][0])
    g.add_node(1, x=edge_a[-1][1], y=edge_a[-1][0])
    g.add_node(2, x=edge_b[-1][1], y=edge_b[-1][0])
    for (u, v), verts in (((0, 1), edge_a), ((1, 2), edge_b)):
        g.add_edge(
            u,
            v,
            key=0,
            geometry=shapely.LineString([(lon, lat) for lat, lon, _ in verts]),
            vertices_resampled=verts,
            sac_scale="hiking",
            highway="path",
            osm_way_id=u,
        )
    return g


# === Stage 6a: graph_smooth_elevation (graph-Laplacian diffusion) ================


def test_elevation_smoothing_default_is_module_constant() -> None:
    """AC #1: the default smoothing strength lives at module scope as a named meters constant."""
    assert isinstance(ELEVATION_SMOOTHING_DEFAULT_M, float)
    assert ELEVATION_SMOOTHING_DEFAULT_M > RESAMPLE_SPACING_M  # non-trivial: > one vertex


def test_graph_smooth_elevation_below_spacing_is_noop() -> None:
    """A strength at/below the resample spacing maps to window <= 1 → input returned unchanged."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1080.0), (45.0002, 5.0, 1000.0)]
    g = _single_edge_graph_with_elevation(verts)
    out = graph_smooth_elevation(g, strength_m=RESAMPLE_SPACING_M)  # window == 1
    assert out.edges[0, 1, 0]["vertices_resampled"] == verts


def test_graph_smooth_elevation_flat_unchanged() -> None:
    """Constant elevation is a fixed point of diffusion → output ~equals input."""
    verts = [(45.0 + i * 1e-4, 5.0, 1000.0) for i in range(8)]
    g = _single_edge_graph_with_elevation(verts)
    out = graph_smooth_elevation(g, strength_m=50.0)
    for original, smoothed in zip(verts, out.edges[0, 1, 0]["vertices_resampled"], strict=True):
        assert math.isclose(smoothed[2], original[2], abs_tol=1e-9)


def test_graph_smooth_elevation_never_increases_max_adjacent_delta() -> None:
    """Low-pass property: diffusion cannot manufacture a slope spike.

    The max absolute consecutive-elevation delta after smoothing must not exceed
    the raw maximum — this is the structural guarantee per-edge moving-average
    methods failed (they dumped a node offset into one ~10 m segment).
    """
    verts = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 1002.0),
        (45.0002, 5.0, 1060.0),  # a sharp pitch
        (45.0003, 5.0, 1004.0),
        (45.0004, 5.0, 1006.0),
        (45.0005, 5.0, 1005.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = graph_smooth_elevation(g, strength_m=60.0)
    out_verts = out.edges[0, 1, 0]["vertices_resampled"]
    raw_max = max(abs(verts[i][2] - verts[i - 1][2]) for i in range(1, len(verts)))
    smoothed_max = max(abs(out_verts[i][2] - out_verts[i - 1][2]) for i in range(1, len(out_verts)))
    # Low-pass bound: the smoothed profile never has a larger step than the raw.
    assert smoothed_max <= raw_max + 1e-9
    # ...and the smoothing is NOT a near-no-op: the 1060 m interior spike (index 2,
    # ~58 m above its neighbours) is meaningfully pulled down. A bare `<= raw_max`
    # bound is satisfied by the identity function, so assert real attenuation.
    assert out_verts[2][2] < verts[2][2] - 20.0, (
        f"interior spike barely moved: {verts[2][2]} -> {out_verts[2][2]} (expected < 1040)"
    )


def test_graph_smooth_elevation_shares_node_value_across_incident_edges() -> None:
    """THE box==curve foundation: edges meeting at a node agree on that node's elevation.

    The shared-node-variable Laplacian must leave edge_a's last vertex elevation
    exactly equal to edge_b's first vertex elevation (same graph node 1) so a
    route's concatenated profile has no jump at the join.
    """
    edge_a = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1030.0), (45.0002, 5.0, 1060.0)]
    edge_b = [(45.0002, 5.0, 1060.0), (45.0003, 5.0, 1010.0), (45.0004, 5.0, 1000.0)]
    g = _two_edges_sharing_node(edge_a, edge_b)
    out = graph_smooth_elevation(g, strength_m=50.0)
    a_last = out.edges[0, 1, 0]["vertices_resampled"][-1][2]
    b_first = out.edges[1, 2, 0]["vertices_resampled"][0][2]
    assert a_last == b_first, "node 1 elevation diverged between incident edges (box != curve)"


def test_graph_smooth_elevation_preserves_lat_lon_exactly() -> None:
    """Only the elevation component is touched; (lat, lon) are bit-exact unchanged."""
    verts = [
        (45.260, 5.788, 1100.0),
        (45.2601, 5.7881, 1120.0),
        (45.2602, 5.7882, 1080.0),
        (45.2603, 5.7883, 1150.0),
        (45.2604, 5.7884, 1130.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = graph_smooth_elevation(g, strength_m=50.0)
    for original, smoothed in zip(verts, out.edges[0, 1, 0]["vertices_resampled"], strict=True):
        assert smoothed[0] == original[0]
        assert smoothed[1] == original[1]


def test_graph_smooth_elevation_does_not_mutate_input() -> None:
    """Pure-function discipline: input graph's vertices_resampled is unchanged."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 9999.0), (45.0002, 5.0, 1000.0)]
    g = _single_edge_graph_with_elevation(verts)
    before = list(g.edges[0, 1, 0]["vertices_resampled"])
    _ = graph_smooth_elevation(g, strength_m=50.0)
    assert list(g.edges[0, 1, 0]["vertices_resampled"]) == before


def test_graph_smooth_elevation_preserves_attribute_contract() -> None:
    """Upstream attributes (geometry + source) carry through unchanged."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1010.0), (45.0002, 5.0, 1020.0)]
    g = _single_edge_graph_with_elevation(verts)
    out = graph_smooth_elevation(g, strength_m=50.0)
    data = out.edges[0, 1, 0]
    assert isinstance(data["geometry"], shapely.LineString)
    assert data["sac_scale"] == "hiking"
    assert data["highway"] == "path"
    assert data["osm_way_id"] == 12345
    assert len(data["vertices_resampled"]) == len(verts)


def _scalar_reference_smooth(
    graph: nx.MultiDiGraph, strength_m: float
) -> dict[tuple[int, int, int], list[float]]:
    """Scalar (dict-and-loop) Jacobi reference for `graph_smooth_elevation`.

    Reimplements the pre-13.1 per-node Python formulation verbatim — one shared
    variable per node, private interior vertices, `(1-λ)·old + λ·mean(neighbours)`
    with λ=0.5 and iters = round(window²/6) — so the vectorized production code
    can be asserted BIT-IDENTICAL to it (same operation order, same IEEE-754
    results). Returns {edge_key: full elevation list} for comparison.
    """
    window = strength_m / RESAMPLE_SPACING_M
    assert window > 1.0, "reference expects a non-no-op strength"
    iters = max(1, round(window * window / 6.0))
    lam = 0.5
    node_val: dict[int, float] = {}
    interior: dict[tuple[int, int, int], list[float]] = {}
    edge_keys: list[tuple[int, int, int]] = []
    for u, v, k, data in graph.edges(data=True, keys=True):
        elevs = [vert[2] for vert in data["vertices_resampled"]]
        node_val.setdefault(u, elevs[0])
        node_val.setdefault(v, elevs[-1])
        interior[(u, v, k)] = elevs[1:-1]
        edge_keys.append((u, v, k))
    node_adj: dict[int, list[tuple[tuple[int, int, int], bool]]] = {}
    for ek in edge_keys:
        node_adj.setdefault(ek[0], []).append((ek, True))
        node_adj.setdefault(ek[1], []).append((ek, False))

    def adjacent_to_node(ek: tuple[int, int, int], is_u: bool) -> float:
        ints = interior[ek]
        if is_u:
            return ints[0] if ints else node_val[ek[1]]
        return ints[-1] if ints else node_val[ek[0]]

    for _ in range(iters):
        new_node = {
            n: (1 - lam) * node_val[n]
            + lam * (sum(adjacent_to_node(ek, is_u) for ek, is_u in adj) / len(adj))
            for n, adj in node_adj.items()
        }
        new_interior: dict[tuple[int, int, int], list[float]] = {}
        for ek in edge_keys:
            u, v, _k = ek
            ints = interior[ek]
            m = len(ints)
            new_interior[ek] = [
                (1 - lam) * ints[j]
                + lam
                * (
                    (
                        (node_val[u] if j == 0 else ints[j - 1])
                        + (node_val[v] if j == m - 1 else ints[j + 1])
                    )
                    / 2
                )
                for j in range(m)
            ]
        node_val = new_node
        interior = new_interior
    return {(u, v, k): [node_val[u], *interior[(u, v, k)], node_val[v]] for u, v, k in edge_keys}


def test_graph_smooth_elevation_bit_identical_to_scalar_reference() -> None:
    """Story 13.1: the vectorized diffusion is BIT-IDENTICAL to the scalar formulation.

    Exercises every structural case at once: a degree-3 junction node (variable-
    degree neighbour averaging), interior chains of different lengths, and a
    2-vertex edge with no interior (node-adjacent-to-node fallback). Exact `==`
    on every elevation — the operation-order-preservation argument that keeps
    the regression goldens byte-identical rests on this test.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    # Y-junction at node 1: three edges (0->1 long, 1->2 short, 1->3 two-vertex).
    edge_a = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 1041.5),
        (45.0002, 5.0, 1033.25),
        (45.0003, 5.0, 1090.0),
    ]
    edge_b = [(45.0003, 5.0, 1090.0), (45.0004, 5.0, 1055.125), (45.0005, 5.0, 1010.0)]
    edge_c = [(45.0003, 5.0, 1090.0), (45.0004, 5.001, 1200.0)]  # no interior
    for n, (lat, lon) in enumerate([(45.0, 5.0), (45.0003, 5.0), (45.0005, 5.0), (45.0004, 5.001)]):
        g.add_node(n, x=lon, y=lat)
    for (u, v), verts in (((0, 1), edge_a), ((1, 2), edge_b), ((1, 3), edge_c)):
        g.add_edge(
            u,
            v,
            key=0,
            geometry=shapely.LineString([(lon, lat) for lat, lon, _ in verts]),
            vertices_resampled=verts,
            sac_scale="hiking",
            highway="path",
            osm_way_id=u,
        )
    strength = 50.0
    expected = _scalar_reference_smooth(g, strength)
    out = graph_smooth_elevation(g, strength_m=strength)
    for (u, v, k), exp_elevs in expected.items():
        got = [vert[2] for vert in out.edges[u, v, k]["vertices_resampled"]]
        assert got == exp_elevs, f"edge ({u},{v},{k}) diverged from scalar reference"


def test_graph_smooth_elevation_replicates_compensated_neighbour_sum() -> None:
    """Story 13.1: the neighbour sum must replicate CPython's compensated `sum()`.

    Since Python 3.12, builtin `sum()` over floats uses Neumaier compensated
    summation — so the scalar formulation's per-node `sum(neigh)/len(neigh)` is
    NOT reproducible by a naive sequential/scatter add (they differ in the last
    ULP for value patterns like this one, lifted verbatim from a degree-6 node
    of the grenoble_small fixture where a naive `np.bincount` port drifted the
    pinned goldens). A degree-6 star of 2-vertex edges makes the centre node's
    first-iteration neighbour sum exactly `sum()` over these six values.
    """
    spoke_elevs = [
        535.0315399169922,
        536.9515279134114,
        538.3678588867188,
        535.0315399169922,
        538.3678588867188,
        536.9515279134114,
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_node(0, x=5.0, y=45.0)
    for i, elev in enumerate(spoke_elevs, start=1):
        lat, lon = 45.0 + i * 1e-4, 5.0 + i * 1e-4
        g.add_node(i, x=lon, y=lat)
        verts = [(45.0, 5.0, 536.0), (lat, lon, elev)]  # no interior vertices
        g.add_edge(
            0,
            i,
            key=0,
            geometry=shapely.LineString([(lon2, lat2) for lat2, lon2, _ in verts]),
            vertices_resampled=verts,
            sac_scale="hiking",
            highway="path",
            osm_way_id=i,
        )
    strength = 50.0
    expected = _scalar_reference_smooth(g, strength)
    out = graph_smooth_elevation(g, strength_m=strength)
    for (u, v, k), exp_elevs in expected.items():
        got = [vert[2] for vert in out.edges[u, v, k]["vertices_resampled"]]
        assert got == exp_elevs, f"edge ({u},{v},{k}) diverged from scalar reference"


# === Stage 6b: graph_deadband_elevation (profile transform) =====================


def test_elevation_deadband_default_is_module_constant() -> None:
    """The default deadband lives at module scope and is off (0) by default."""
    assert isinstance(ELEVATION_DEADBAND_DEFAULT_M, float)
    assert ELEVATION_DEADBAND_DEFAULT_M == 0.0


def test_graph_deadband_elevation_zero_is_noop() -> None:
    """deadband_m <= 0 returns the input unchanged."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1001.0), (45.0002, 5.0, 1000.0)]
    g = _single_edge_graph_with_elevation(verts)
    out = graph_deadband_elevation(g, deadband_m=0.0)
    assert out.edges[0, 1, 0]["vertices_resampled"] == verts


def test_graph_deadband_elevation_flattens_subfloor_wiggle() -> None:
    """A sub-floor up/down reversal between two equal-elevation endpoints is flattened out.

    Endpoints (1000 → 1000) are pinned; a +1 m bump in the middle is below a 5 m
    deadband, so the committed profile is flat and contributes no D+ when summed.
    """
    verts = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 1001.0),  # +1 m: below the 5 m floor
        (45.0002, 5.0, 1000.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = graph_deadband_elevation(g, deadband_m=5.0)
    out_elevs = [v[2] for v in out.edges[0, 1, 0]["vertices_resampled"]]
    assert out_elevs[0] == 1000.0 and out_elevs[-1] == 1000.0  # endpoints pinned
    d_plus = sum(max(0.0, out_elevs[i] - out_elevs[i - 1]) for i in range(1, len(out_elevs)))
    assert d_plus == 0.0, f"sub-floor wiggle was not flattened (D+={d_plus})"


def test_graph_deadband_elevation_preserves_sustained_climb() -> None:
    """A monotone climb exceeding the floor passes through (endpoints pinned, gain preserved)."""
    verts = [(45.0 + i * 1e-4, 5.0, 1000.0 + i * 20.0) for i in range(5)]  # +80 m monotone
    g = _single_edge_graph_with_elevation(verts)
    out = graph_deadband_elevation(g, deadband_m=5.0)
    out_elevs = [v[2] for v in out.edges[0, 1, 0]["vertices_resampled"]]
    assert out_elevs[0] == 1000.0 and out_elevs[-1] == 1080.0
    d_plus = sum(max(0.0, out_elevs[i] - out_elevs[i - 1]) for i in range(1, len(out_elevs)))
    assert math.isclose(d_plus, 80.0, abs_tol=1e-9)


def test_graph_deadband_elevation_does_not_mutate_input() -> None:
    """Pure-function discipline: input graph's vertices_resampled is unchanged."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1001.0), (45.0002, 5.0, 1000.0)]
    g = _single_edge_graph_with_elevation(verts)
    before = list(g.edges[0, 1, 0]["vertices_resampled"])
    _ = graph_deadband_elevation(g, deadband_m=5.0)
    assert list(g.edges[0, 1, 0]["vertices_resampled"]) == before


def test_graph_deadband_elevation_preserves_lat_lon_exactly() -> None:
    """Only the elevation component is touched; (lat, lon) are bit-exact unchanged."""
    verts = [(45.260, 5.788, 1100.0), (45.2601, 5.7881, 1101.0), (45.2602, 5.7882, 1100.0)]
    g = _single_edge_graph_with_elevation(verts)
    out = graph_deadband_elevation(g, deadband_m=5.0)
    for original, smoothed in zip(verts, out.edges[0, 1, 0]["vertices_resampled"], strict=True):
        assert smoothed[0] == original[0]
        assert smoothed[1] == original[1]


# === Story 14.2 bit-equality oracles (S2: vectorized stage 3-4) =================
#
# Verbatim copies of the pre-14.2 scalar `_moving_average` / `_resample_meters`,
# kept as bit-equality oracles: the vectorized production code must produce
# `==` (exact, not approx) coordinates over every vertex of the real
# grenoble_small fixture. This is the "verify before deleting the old code" gate
# (AC #1), mirroring 14.1's `_scalar_reference_sample_elevation`.


def _scalar_moving_average(
    coords: list[tuple[float, float]], window: int
) -> list[tuple[float, float]]:
    """Verbatim pre-14.2 `_moving_average` — the stage-3 bit-equality oracle."""
    assert window >= 1 and window % 2 == 1, "window must be odd and >= 1"
    half = window // 2
    n = len(coords)
    smoothed: list[tuple[float, float]] = []
    for i in range(n):
        if i == 0 or i == n - 1:
            smoothed.append(coords[i])
            continue
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window_coords = coords[lo:hi]
        avg_x = sum(c[0] for c in window_coords) / len(window_coords)
        avg_y = sum(c[1] for c in window_coords) / len(window_coords)
        smoothed.append((avg_x, avg_y))
    return smoothed


def _scalar_resample_meters(
    coords: list[tuple[float, float]], spacing_m: float
) -> list[tuple[float, float]]:
    """Verbatim pre-14.2 `_resample_meters` — the stage-4 bit-equality oracle."""
    deg_to_m_lat = _EARTH_RADIUS_M * math.radians(1.0)
    mean_lat = sum(lat for _lon, lat in coords) / len(coords)
    deg_to_m_lon = deg_to_m_lat * math.cos(math.radians(mean_lat))

    xy: list[tuple[float, float]] = [
        (lon * deg_to_m_lon, lat * deg_to_m_lat) for lon, lat in coords
    ]
    cumulative: list[float] = [0.0]
    for i in range(1, len(xy)):
        dx = xy[i][0] - xy[i - 1][0]
        dy = xy[i][1] - xy[i - 1][1]
        cumulative.append(cumulative[-1] + math.hypot(dx, dy))
    total = cumulative[-1]

    n_intervals = max(1, round(total / spacing_m))
    actual_spacing = total / n_intervals if total > 0 else 0.0

    out: list[tuple[float, float]] = [coords[0]]
    seg = 0
    for i in range(1, n_intervals):
        d = actual_spacing * i
        while seg < len(cumulative) - 2 and cumulative[seg + 1] < d:
            seg += 1
        seg_len = cumulative[seg + 1] - cumulative[seg]
        t = (d - cumulative[seg]) / seg_len if seg_len > 0 else 0.0
        t = max(0.0, min(1.0, t))
        x = xy[seg][0] + t * (xy[seg + 1][0] - xy[seg][0])
        y = xy[seg][1] + t * (xy[seg + 1][1] - xy[seg][1])
        out.append((x / deg_to_m_lon, y / deg_to_m_lat))
    out.append(coords[-1])
    return out


def _fixture_geometry_graph() -> nx.MultiDiGraph:
    """Stage-1/2 stand-in: the committed grenoble_small graphml, normalized."""
    return normalize_edges(osmnx.load_graphml(_FIXTURE_PATH))


def _edge_coords(data: dict[str, object]) -> list[tuple[float, float]]:
    geom = data["geometry"]
    assert isinstance(geom, shapely.LineString)
    return [(float(c[0]), float(c[1])) for c in geom.coords]


# Numerical-equivalence tolerance: the flat-array vectorization (Story 14.2) uses
# `np.hypot`/naive means instead of `math.hypot`/compensated `sum()`, so results
# match the scalar reference to within floating-point reordering (measured max
# ~1.4e-14 deg on the fixture), not bit-for-bit. 1e-8 deg (~1 mm) proves numerical
# equivalence while still catching any real algorithmic divergence. The residual
# is sub-nm and does not move the regression goldens (verified byte-identical).
_EQUIV_ATOL = 1e-8


def test_smooth_polylines_numerically_equivalent_to_scalar_reference() -> None:
    """AC #1: vectorized `smooth_polylines` equals the scalar oracle to fp-reordering on the fixture."""
    if not _FIXTURE_PATH.exists():
        pytest.skip("grenoble_small OSM fixture not committed.")
    produced = smooth_polylines(_fixture_geometry_graph())

    ref = _fixture_geometry_graph()
    drop: list[tuple[int, int, int]] = []
    for u, v, k, data in list(ref.edges(data=True, keys=True)):
        coords = _edge_coords(data)
        if not is_valid_polyline(coords):
            drop.append((u, v, k))
            continue
        data["geometry"] = shapely.LineString(_scalar_moving_average(coords, SMOOTHING_WINDOW))
    for e in drop:
        ref.remove_edge(*e)

    prod_edges = {(u, v, k): _edge_coords(d) for u, v, k, d in produced.edges(data=True, keys=True)}
    ref_edges = {(u, v, k): _edge_coords(d) for u, v, k, d in ref.edges(data=True, keys=True)}
    assert prod_edges.keys() == ref_edges.keys(), "stage-3 edge set diverged from scalar reference"
    total = 0
    for key, ref_coords in ref_edges.items():
        got = prod_edges[key]
        assert len(got) == len(ref_coords), f"stage-3 vertex count diverged on edge {key}"
        for (px, py), (rx, ry) in zip(got, ref_coords, strict=True):
            assert abs(px - rx) <= _EQUIV_ATOL and abs(py - ry) <= _EQUIV_ATOL, (
                f"stage-3 coords diverged on edge {key}"
            )
        total += len(ref_coords)
    assert total > 1000, f"expected a substantial fixture, smoothed only {total} vertices"


def test_resample_edges_numerically_equivalent_to_scalar_reference() -> None:
    """AC #1: vectorized `resample_edges` equals the scalar oracle to fp-reordering on the fixture."""
    if not _FIXTURE_PATH.exists():
        pytest.skip("grenoble_small OSM fixture not committed.")
    smoothed = smooth_polylines(_fixture_geometry_graph())
    produced = resample_edges(smoothed)

    ref = smoothed.copy()
    drop: list[tuple[int, int, int]] = []
    for u, v, k, data in list(ref.edges(data=True, keys=True)):
        coords = _edge_coords(data)
        if not is_valid_polyline(coords):
            drop.append((u, v, k))
            continue
        data["geometry"] = shapely.LineString(_scalar_resample_meters(coords, RESAMPLE_SPACING_M))
    for e in drop:
        ref.remove_edge(*e)

    prod_edges = {(u, v, k): _edge_coords(d) for u, v, k, d in produced.edges(data=True, keys=True)}
    ref_edges = {(u, v, k): _edge_coords(d) for u, v, k, d in ref.edges(data=True, keys=True)}
    assert prod_edges.keys() == ref_edges.keys(), "stage-4 edge set diverged from scalar reference"
    total = 0
    for key, ref_coords in ref_edges.items():
        got = prod_edges[key]
        assert len(got) == len(ref_coords), f"stage-4 vertex count diverged on edge {key}"
        for (px, py), (rx, ry) in zip(got, ref_coords, strict=True):
            assert abs(px - rx) <= _EQUIV_ATOL and abs(py - ry) <= _EQUIV_ATOL, (
                f"stage-4 coords diverged on edge {key}"
            )
        total += len(ref_coords)
    assert total > 1000, f"expected a substantial fixture, resampled only {total} vertices"
