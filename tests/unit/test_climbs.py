# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same osmnx/networkx/shapely boundary as pipeline/osm.py + pipeline/smoothing.py.
"""Unit tests for pipeline.climbs: compute_edge_metrics (stage 7).

Stage 7 computes per-edge `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`
from the `vertices_resampled` field set by stage 5. Tests are layered:

- Analytical synthetic tests build `vertices_resampled` by hand (no stages 1-5
  needed) to exercise the metric arithmetic in isolation.
- An integration-style fixture test chains stages 1→7 over the real Grenoble
  fixture and asserts aggregate plausibility.
- A hypothesis property test asserts the metric-sign invariants on any
  non-degenerate hand-built input.
"""

from __future__ import annotations

import math
import pathlib

import networkx as nx
import pytest
import shapely
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from steeproute.pipeline.climbs import compute_edge_metrics, is_valid_for_metrics

_FIXTURE_DIR = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "grenoble_small"
_OSM_FIXTURE_PATH = _FIXTURE_DIR / "osm_graph.graphml"
_DEM_FIXTURE_PATH = _FIXTURE_DIR / "dem.tif"

_EARTH_RADIUS_M = 6_378_137.0
_DEG_TO_M_LAT = _EARTH_RADIUS_M * math.radians(1.0)


def _single_edge_graph_with_elevation(
    vertices_resampled: list[tuple[float, float, float]],
) -> nx.MultiDiGraph:
    """Build a one-edge MultiDiGraph carrying the stage-5/6 contract."""
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    first_lat, first_lon, _ = vertices_resampled[0]
    last_lat, last_lon, _ = vertices_resampled[-1]
    g.add_node(0, x=first_lon, y=first_lat)
    g.add_node(1, x=last_lon, y=last_lat)
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


def _expected_length_m(verts: list[tuple[float, float, float]]) -> float:
    """Independent reference implementation of cumulative 2D distance.

    Uses the same local-equirectangular projection as
    `pipeline.smoothing._resample_meters`. Computed here so the test asserts
    the contract, not the implementation.
    """
    mean_lat = sum(lat for lat, _lon, _ in verts) / len(verts)
    deg_to_m_lon = _DEG_TO_M_LAT * math.cos(math.radians(mean_lat))
    total = 0.0
    for i in range(1, len(verts)):
        dlat = (verts[i][0] - verts[i - 1][0]) * _DEG_TO_M_LAT
        dlon = (verts[i][1] - verts[i - 1][1]) * deg_to_m_lon
        total += math.hypot(dlat, dlon)
    return total


# --- AC #4: analytical correctness on synthetic edges -------------------------


def test_compute_edge_metrics_flat_profile_has_zero_d_plus_d_minus() -> None:
    """Flat elevation → d_plus_m == 0, d_minus_m == 0, avg_gradient == 0."""
    verts = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 1000.0),
        (45.0002, 5.0, 1000.0),
        (45.0003, 5.0, 1000.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = compute_edge_metrics(g)
    data = out.edges[0, 1, 0]
    assert data["d_plus_m"] == 0.0
    assert data["d_minus_m"] == 0.0
    assert data["avg_gradient"] == 0.0
    # length_m matches independent equirectangular reference to sub-‰.
    expected = _expected_length_m(verts)
    assert math.isclose(data["length_m"], expected, rel_tol=1e-6)


def test_compute_edge_metrics_pure_uphill_matches_analytical() -> None:
    """Strictly increasing elevation → d_plus_m == total Δelev, d_minus_m == 0, gradient == Δelev / length."""
    verts = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 1050.0),
        (45.0002, 5.0, 1100.0),
        (45.0003, 5.0, 1150.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = compute_edge_metrics(g)
    data = out.edges[0, 1, 0]
    assert math.isclose(data["d_plus_m"], 150.0, abs_tol=1e-9)
    assert data["d_minus_m"] == 0.0
    expected_len = _expected_length_m(verts)
    assert math.isclose(data["length_m"], expected_len, rel_tol=1e-6)
    expected_grad = 150.0 / expected_len
    assert math.isclose(data["avg_gradient"], expected_grad, rel_tol=1e-6)


def test_compute_edge_metrics_pure_downhill_uses_positive_magnitude_for_d_minus() -> None:
    """Strictly decreasing elevation → d_plus_m == 0, d_minus_m == |Δelev| (positive)."""
    verts = [
        (45.0, 5.0, 1500.0),
        (45.0001, 5.0, 1480.0),
        (45.0002, 5.0, 1450.0),
        (45.0003, 5.0, 1400.0),
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = compute_edge_metrics(g)
    data = out.edges[0, 1, 0]
    assert data["d_plus_m"] == 0.0
    # d_minus_m is positive magnitude (matches Architecture §Cat 4 manifest sample).
    assert math.isclose(data["d_minus_m"], 100.0, abs_tol=1e-9)
    expected_len = _expected_length_m(verts)
    expected_grad = 100.0 / expected_len
    assert math.isclose(data["avg_gradient"], expected_grad, rel_tol=1e-6)


def test_compute_edge_metrics_mixed_up_down_separates_components() -> None:
    """Up-then-down profile → both d_plus_m > 0 and d_minus_m > 0; sum == total |Δelev|."""
    verts = [
        (45.0, 5.0, 1000.0),
        (45.0001, 5.0, 1080.0),  # +80
        (45.0002, 5.0, 1150.0),  # +70
        (45.0003, 5.0, 1100.0),  # -50
        (45.0004, 5.0, 1020.0),  # -80
    ]
    g = _single_edge_graph_with_elevation(verts)
    out = compute_edge_metrics(g)
    data = out.edges[0, 1, 0]
    assert math.isclose(data["d_plus_m"], 150.0, abs_tol=1e-9)
    assert math.isclose(data["d_minus_m"], 130.0, abs_tol=1e-9)
    # Sum == total absolute elevation change.
    assert math.isclose(data["d_plus_m"] + data["d_minus_m"], 280.0, abs_tol=1e-9)
    expected_grad = 280.0 / _expected_length_m(verts)
    assert math.isclose(data["avg_gradient"], expected_grad, rel_tol=1e-6)


# --- AC #1 (Story 10.2): windowed descent metric -----------------------------

# Per-segment horizontal distance for a 0.0001° latitude step (the spacing every
# synthetic profile below uses): same equirectangular projection as production.
_SEG_LEN_M = _DEG_TO_M_LAT * 0.0001


def test_max_windowed_descent_grad_flat_profile_is_zero() -> None:
    """Flat elevation → no grade in any window → metric 0.0."""
    verts = [(45.0, 5.0, 1000.0)] + [(45.0 + 0.0001 * i, 5.0, 1000.0) for i in range(1, 6)]
    out = compute_edge_metrics(_single_edge_graph_with_elevation(verts))
    assert out.edges[0, 1, 0]["max_windowed_descent_grad"] == 0.0


def test_max_windowed_descent_grad_uniform_descent_equals_segment_grade() -> None:
    """A uniform -5 m/segment descent → every window has the same grade = 5 / segment-length."""
    verts = [(45.0 + 0.0001 * i, 5.0, 1000.0 - 5.0 * i) for i in range(6)]
    out = compute_edge_metrics(_single_edge_graph_with_elevation(verts))
    metric = out.edges[0, 1, 0]["max_windowed_descent_grad"]
    assert math.isclose(metric, 5.0 / _SEG_LEN_M, rel_tol=1e-6)


def test_max_windowed_descent_grad_is_descent_only_not_ascent() -> None:
    """The metric measures descents in the stored direction; the reversed (ascending) profile → 0.0.

    Direction-awareness is the whole point of FR32: a segment climbed steeply must
    NOT be capped. Walking `verts` downhill yields the steep grade; the reciprocal
    edge (reversed vertices, the same physical segment climbed) yields 0.0.
    """
    down = [(45.0 + 0.0001 * i, 5.0, 1000.0 - 7.0 * i) for i in range(6)]
    up = [(45.0 + 0.0001 * i, 5.0, 1000.0 + 7.0 * i) for i in range(6)]
    grad_down = compute_edge_metrics(_single_edge_graph_with_elevation(down)).edges[0, 1, 0][
        "max_windowed_descent_grad"
    ]
    grad_up = compute_edge_metrics(_single_edge_graph_with_elevation(up)).edges[0, 1, 0][
        "max_windowed_descent_grad"
    ]
    assert grad_down > 0.0
    assert grad_up == 0.0


def test_max_windowed_descent_grad_short_edge_falls_back_to_whole_edge_grade() -> None:
    """An edge whose whole polyline is shorter than the window → its end-to-end descent grade."""
    # Two vertices ≈ 11 m apart (< the 30 m window), descending 20 m: metric is drop / length.
    verts = [(45.0, 5.0, 1020.0), (45.0001, 5.0, 1000.0)]
    out = compute_edge_metrics(_single_edge_graph_with_elevation(verts))
    metric = out.edges[0, 1, 0]["max_windowed_descent_grad"]
    assert math.isclose(metric, 20.0 / _SEG_LEN_M, rel_tol=1e-6)


def test_max_windowed_descent_grad_short_ascending_edge_is_zero() -> None:
    """A sub-window edge that only *climbs* is not a descent → metric 0.0 (the fallback is descent-only)."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1020.0)]
    out = compute_edge_metrics(_single_edge_graph_with_elevation(verts))
    assert out.edges[0, 1, 0]["max_windowed_descent_grad"] == 0.0


def test_max_windowed_descent_grad_captures_steep_window_over_whole_edge_average() -> None:
    """A short steep descent dominates the metric even though the whole-edge avg_gradient is gentle."""
    # Five flat vertices then a steep -40 m/segment descent of three segments.
    elevations = [1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 960.0, 920.0, 880.0]
    verts = [(45.0 + 0.0001 * i, 5.0, elevations[i]) for i in range(len(elevations))]
    data = compute_edge_metrics(_single_edge_graph_with_elevation(verts)).edges[0, 1, 0]
    metric = data["max_windowed_descent_grad"]
    # The steepest window is the three-segment steep descent (Δ=120 m).
    assert math.isclose(metric, 120.0 / (3.0 * _SEG_LEN_M), rel_tol=1e-6)
    # ...and it is far steeper than the edge's averaged-out gradient.
    assert metric > data["avg_gradient"]


def test_max_windowed_descent_grad_ignores_steep_ascent_within_net_descent() -> None:
    """Finding #3: a net descent whose steepest window *ascends* is not reported as a steep descent.

    A gentle (-5 m/seg) descent interrupted by one steep (+12 m/seg) up-bump nets a
    loss overall, so the edge *is* a descent — but its steepest sustained 30 m window
    is the ascent. That window must contribute 0 to the descent metric (else the cap
    would forbid the traversal for a steep *climb*); the reported grade is the gentle
    descent's, well below the ascent's grade.
    """
    elevations = [
        1000.0,
        995.0,
        990.0,
        985.0,
        980.0,
        992.0,
        1004.0,
        1016.0,
        1011.0,
        1006.0,
        1001.0,
        996.0,
    ]
    verts = [(45.0 + 0.0001 * i, 5.0, elevations[i]) for i in range(len(elevations))]
    data = compute_edge_metrics(_single_edge_graph_with_elevation(verts)).edges[0, 1, 0]
    # Net loss over the edge → it *is* a descent (d_minus 40 > d_plus 36)...
    assert data["d_minus_m"] > data["d_plus_m"]
    # ...but the only steep sustained window is the +12 m/seg ascent, which the
    # descent metric ignores. The reported grade is the uniform -5 m/seg descent,
    # far below where the +12 m/seg ascent would have landed under an abs measure.
    metric = data["max_windowed_descent_grad"]
    assert math.isclose(metric, 5.0 / _SEG_LEN_M, rel_tol=1e-6)
    assert metric < 12.0 / _SEG_LEN_M


# --- pure-function discipline + attribute contract ---------------------------


def test_compute_edge_metrics_does_not_mutate_input() -> None:
    """Pure-function discipline: input graph is unchanged after compute_edge_metrics."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1050.0), (45.0002, 5.0, 1100.0)]
    g = _single_edge_graph_with_elevation(verts)
    # No metric keys present initially.
    assert "length_m" not in g.edges[0, 1, 0]
    _ = compute_edge_metrics(g)
    assert "length_m" not in g.edges[0, 1, 0]


def test_compute_edge_metrics_preserves_attribute_contract() -> None:
    """Upstream attributes carry through stage 7 unchanged."""
    verts = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1050.0), (45.0002, 5.0, 1100.0)]
    g = _single_edge_graph_with_elevation(verts)
    out = compute_edge_metrics(g)
    data = out.edges[0, 1, 0]
    assert isinstance(data["geometry"], shapely.LineString)
    assert data["vertices_resampled"] == verts
    assert data["sac_scale"] == "hiking"
    assert data["highway"] == "path"
    assert data["osm_way_id"] == 12345


# --- AC #5: integration-style fixture test through stages 1-7 ----------------


_GRADIENT_PLAUSIBILITY_CAP = 0.8  # 80% — extreme Alpine cap for a sanity-only check.
_GRADIENT_PLAUSIBILITY_MIN_FRACTION = 0.95  # ≥ 95% of edges below the cap.


@pytest.fixture(scope="module")
def fixture_pipeline_through_stage7() -> nx.MultiDiGraph:
    """Run stages 1→5 (setup) then the query-side 6→7 reshaping on the Grenoble fixtures.

    Story 6.3 moved stages 6-7 query-side: setup stops at stage 5 (raw elevation),
    and `operationalize_graph` (smooth → deadband → naive-sum `compute_edge_metrics`)
    produces the operational metrics at query time. Calls `operationalize_graph` at
    its production defaults so the fixture mirrors `cli/query.py` exactly.
    """
    import osmnx

    from steeproute.pipeline import operationalize_graph
    from steeproute.pipeline.dem import sample_elevation
    from steeproute.pipeline.osm import normalize_edges
    from steeproute.pipeline.smoothing import resample_edges, smooth_polylines

    if not _DEM_FIXTURE_PATH.exists():
        pytest.skip("dem.tif fixture not committed; fixture-driven assertions skipped.")
    graph = normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))
    graph = smooth_polylines(graph)
    graph = resample_edges(graph)
    graph = sample_elevation(graph, _DEM_FIXTURE_PATH)
    return operationalize_graph(graph)


def test_fixture_pipeline_full_contract_populated(
    fixture_pipeline_through_stage7: nx.MultiDiGraph,
) -> None:
    """AC #5: every output edge carries the full stages-1-7 attribute contract."""
    assert fixture_pipeline_through_stage7.number_of_edges() > 0
    for _u, _v, _k, data in fixture_pipeline_through_stage7.edges(data=True, keys=True):
        assert isinstance(data["geometry"], shapely.LineString)
        assert isinstance(data["vertices_resampled"], list)
        assert isinstance(data["length_m"], float)
        assert isinstance(data["d_plus_m"], float)
        assert isinstance(data["d_minus_m"], float)
        assert isinstance(data["avg_gradient"], float)
        assert "sac_scale" in data
        assert "highway" in data
        assert "osm_way_id" in data


def test_fixture_pipeline_metrics_are_finite_and_signed_correctly(
    fixture_pipeline_through_stage7: nx.MultiDiGraph,
) -> None:
    """AC #5: every metric is a finite float; sign invariants hold per edge."""
    for u, v, k, data in fixture_pipeline_through_stage7.edges(data=True, keys=True):
        ctx = f"edge ({u}, {v}, {k})"
        assert math.isfinite(data["length_m"]), ctx
        assert math.isfinite(data["d_plus_m"]), ctx
        assert math.isfinite(data["d_minus_m"]), ctx
        assert math.isfinite(data["avg_gradient"]), ctx
        assert data["length_m"] > 0.0, ctx
        assert data["d_plus_m"] >= 0.0, ctx
        assert data["d_minus_m"] >= 0.0, ctx
        assert data["avg_gradient"] >= 0.0, ctx


def test_fixture_pipeline_gradients_are_plausibly_alpine(
    fixture_pipeline_through_stage7: nx.MultiDiGraph,
) -> None:
    """AC #5: ≥ 95% of edges have avg_gradient < 80% — Alpine sanity cap.

    Rationale: 80% (4 in 5) is well above any sustained-trail gradient and
    catches sign-flip / axis-swap / unit bugs in stages 6-7 without being a
    strict geometric bound (a 10-m edge with 8 m of relief is conceivable).
    """
    gradients = [
        data["avg_gradient"]
        for _u, _v, _k, data in fixture_pipeline_through_stage7.edges(data=True, keys=True)
    ]
    n = len(gradients)
    below_cap = sum(1 for g in gradients if g < _GRADIENT_PLAUSIBILITY_CAP)
    fraction = below_cap / n
    assert fraction >= _GRADIENT_PLAUSIBILITY_MIN_FRACTION, (
        f"Only {below_cap}/{n} ({fraction:.1%}) edges below {_GRADIENT_PLAUSIBILITY_CAP:.0%} gradient; "
        f"expected ≥ {_GRADIENT_PLAUSIBILITY_MIN_FRACTION:.0%}."
    )


# --- AC #6: hypothesis property test on metric invariants --------------------


@given(
    verts=st.lists(
        st.tuples(
            # lat ∈ [40, 50] — temperate-zone band where cos(radians(mean_lat)) is
            # meaningfully ≠ 1 (~0.65-0.77), so the cos-of-mean-latitude correction
            # in `_cumulative_2d_distance_m` is actually exercised. An equatorial
            # range like [-1, 1] would make cos ≈ 1 and silently pass a buggy
            # implementation that omitted the correction.
            st.floats(min_value=40.0, max_value=50.0, allow_nan=False, allow_infinity=False),
            # lon ∈ [-10, 10] — wide enough to keep typical pair-distances >>
            # float-underflow regime even at the upper bound of `max_size`.
            st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0.0, max_value=5000.0, allow_nan=False, allow_infinity=False),
        ),
        min_size=2,
        max_size=10,
    ),
)
@settings(max_examples=50, deadline=None)
def test_compute_edge_metrics_property_metric_invariants(
    verts: list[tuple[float, float, float]],
) -> None:
    """AC #6: for any non-degenerate input, d_plus_m ≥ 0, d_minus_m ≥ 0, length_m > 0, gradient finite.

    Uses the same `is_valid_for_metrics` predicate as the stage's input guard,
    via `hypothesis.assume`, so the strategy filter and production check agree
    on what counts as valid (mirrors Story 2.2's `is_valid_polyline` pattern).
    """
    assume(is_valid_for_metrics(verts))
    g = _single_edge_graph_with_elevation(verts)
    out = compute_edge_metrics(g)
    data = out.edges[0, 1, 0]
    assert data["d_plus_m"] >= 0.0
    assert data["d_minus_m"] >= 0.0
    assert data["length_m"] > 0.0
    assert math.isfinite(data["avg_gradient"])
    assert data["avg_gradient"] >= 0.0
