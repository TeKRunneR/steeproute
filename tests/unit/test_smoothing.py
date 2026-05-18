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
    ELEVATION_MEDIAN_WINDOW,
    RESAMPLE_SPACING_M,
    SMOOTHING_WINDOW,
    is_valid_polyline,
    median_smooth_elevation,
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


def test_elevation_median_window_is_module_constant() -> None:
    """AC #1: window size lives at module scope as a named constant."""
    assert isinstance(ELEVATION_MEDIAN_WINDOW, int)
    assert ELEVATION_MEDIAN_WINDOW >= 3
    assert ELEVATION_MEDIAN_WINDOW % 2 == 1


def test_median_smooth_elevation_flat_unchanged() -> None:
    """AC #3: constant elevation → output equals input bit-exactly.

    The median of N identical floats is exactly that float; assertion uses raw `==`
    (matches the strictness of the lat/lon-preservation test in the same suite).
    """
    verts = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 1000.0),
        (45.0002, 5.0, 1000.0),
        (45.0003, 5.0, 1000.0),
        (45.0004, 5.0, 1000.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = median_smooth_elevation(g)
    out_verts = out.edges[0, 1, 0]["vertices_resampled"]
    assert len(out_verts) == len(verts)
    for original, smoothed in zip(verts, out_verts, strict=True):
        assert smoothed[0] == original[0]
        assert smoothed[1] == original[1]
        assert smoothed[2] == original[2]


def test_median_smooth_elevation_monotone_output_is_bounded_by_window() -> None:
    """AC #3: for monotone input, each interior output elevation is the median of its
    window — bounded by [window_min, window_max] from the input. Endpoints pinned exactly.

    This is the strong contract median smoothing actually gives. A `>=` non-decreasing
    assertion would be tautological for monotone input: the median of a sorted window
    is automatically order-preserving against its neighbours' medians. The window-bound
    assertion catches a bug like "median returns a value outside the window" (the
    fundamental implementation property of the median).
    """
    verts = [(45.0 + i * 1e-4, 5.0, 1000.0 + i * 10.0) for i in range(7)]
    g = _single_edge_graph_with_elevation(verts)
    out = median_smooth_elevation(g)  # window = ELEVATION_MEDIAN_WINDOW (= 5)
    out_verts = out.edges[0, 1, 0]["vertices_resampled"]
    # Endpoints pinned exactly.
    assert out_verts[0][2] == verts[0][2]
    assert out_verts[-1][2] == verts[-1][2]
    # Interior: median is bounded by the input window's min/max.
    half = ELEVATION_MEDIAN_WINDOW // 2
    for i in range(1, len(verts) - 1):
        lo = max(0, i - half)
        hi = min(len(verts), i + half + 1)
        window_input_elevs = [v[2] for v in verts[lo:hi]]
        assert min(window_input_elevs) <= out_verts[i][2] <= max(window_input_elevs), (
            f"i={i}: out {out_verts[i][2]} outside window-input bounds "
            f"[{min(window_input_elevs)}, {max(window_input_elevs)}]"
        )


def test_median_smooth_elevation_spike_is_replaced_by_window_median() -> None:
    """AC #3: a single spike in an otherwise flat run is replaced by the window median.

    With ELEVATION_MEDIAN_WINDOW = 5, the median of any 5-window covering the
    spike index is the flat baseline (4 flats + 1 spike → median is flat).
    Endpoints (i=0, i=6) are pinned to input; spike at i=3 (deep interior).
    """
    verts = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 1000.0),
        (45.0002, 5.0, 1000.0),
        (45.0003, 5.0, 9999.0),  # spike
        (45.0004, 5.0, 1000.0),
        (45.0005, 5.0, 1000.0),
        (45.0006, 5.0, 1000.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = median_smooth_elevation(g, window=5)
    out_verts = out.edges[0, 1, 0]["vertices_resampled"]
    # Every vertex (endpoints pinned, interior medians) lands at the flat baseline:
    # - i=0, i=6: pinned to input (both 1000).
    # - i=3: deep-interior symmetric window [1:6] = 4 flats + 1 spike → median 1000.
    # - i=1, i=2: clamped windows [0:3]/[0:4] dominated by flats → median 1000.
    # - i=4, i=5: clamped windows [2:7]/[3:7] dominated by flats → median 1000.
    # Asserting every index — not just i=3 — covers the boundary-clamp branches in
    # `_moving_median` (the most error-prone path; an off-by-one in `lo`/`hi`
    # would silently slip past an i=3-only assertion).
    for i in range(len(verts)):
        assert out_verts[i][2] == 1000.0, (
            f"vertex i={i} should be 1000.0 after spike-median smoothing, got {out_verts[i][2]}"
        )


def test_median_smooth_elevation_preserves_lat_lon_exactly() -> None:
    """AC #1, AC #3: the (lat, lon) components of every vertex are bit-exact unchanged."""
    verts = [
        (45.260, 5.788, 1100.0),
        (45.2601, 5.7881, 1120.0),
        (45.2602, 5.7882, 1080.0),
        (45.2603, 5.7883, 1150.0),
        (45.2604, 5.7884, 1130.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = median_smooth_elevation(g)
    out_verts = out.edges[0, 1, 0]["vertices_resampled"]
    for original, smoothed in zip(verts, out_verts, strict=True):
        # lat/lon must be bit-exact equal (not just close): no projection drift,
        # no float-roundtrip drift — stage 6 only touches the elevation component.
        assert smoothed[0] == original[0]
        assert smoothed[1] == original[1]


def test_median_smooth_elevation_does_not_mutate_input() -> None:
    """Pure-function discipline: input graph's vertices_resampled is unchanged."""
    verts = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 9999.0),
        (45.0002, 5.0, 1000.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    before = list(g.edges[0, 1, 0]["vertices_resampled"])
    _ = median_smooth_elevation(g)
    after = list(g.edges[0, 1, 0]["vertices_resampled"])
    assert before == after


def test_median_smooth_elevation_preserves_attribute_contract() -> None:
    """Upstream attributes (geometry + source) carry through stage 6 unchanged."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1010.0), (45.0002, 5.0, 1020.0)]
    g = _single_edge_graph_with_elevation(verts)
    out = median_smooth_elevation(g)
    data = out.edges[0, 1, 0]
    assert isinstance(data["geometry"], shapely.LineString)
    assert data["sac_scale"] == "hiking"
    assert data["highway"] == "path"
    assert data["osm_way_id"] == 12345
    assert len(data["vertices_resampled"]) == len(verts)


def test_median_smooth_elevation_short_polyline_is_passthrough() -> None:
    """A 2-vertex polyline has only endpoints (no interior) → output equals input exactly."""
    verts = [(45.0, 5.0, 1000.0), (45.001, 5.0, 1050.0)]
    g = _single_edge_graph_with_elevation(verts)
    out = median_smooth_elevation(g)
    out_verts = out.edges[0, 1, 0]["vertices_resampled"]
    assert out_verts == verts
