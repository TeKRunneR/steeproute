# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
# Reason: same osmnx/networkx/shapely boundary as pipeline/osm.py + pipeline/smoothing.py.
# reportPrivateUsage relaxed: the bit-equality oracle test imports `_DESCENT_WINDOW_M`
# directly so it can never drift from the production constant it mirrors.
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

from steeproute.errors import PipelineContractError
from steeproute.pipeline.climbs import (
    _DESCENT_WINDOW_M,
    compute_edge_metrics,
    is_valid_for_metrics,
)

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


def test_compute_edge_metrics_fails_fast_on_non_finite_without_corrupting_others() -> None:
    """A non-finite coord raises rather than silently corrupting other edges' metrics.

    The vectorized reductions share one `np.cumsum` and one `.max()` across the
    whole graph, so a single NaN would otherwise leak into unrelated edges'
    `length_m` / `max_windowed_descent_grad`. The fail-fast guard converts that
    silent whole-graph corruption into a loud contract error.
    """
    good = [(45.0, 5.0, 1000.0), (45.0001, 5.0, 1010.0), (45.0002, 5.0, 1005.0)]
    # NaN lives only in vertices_resampled (what stage 7 reads); the geometry
    # stays finite so the test exercises the guard, not shapely's own NaN path.
    bad = [(45.0, 5.0, 1000.0), (float("nan"), 5.0, 1010.0), (45.0002, 5.0, 1005.0)]
    g = _single_edge_graph_with_elevation(good)
    g.add_node(2, x=5.0, y=45.0)
    g.add_node(3, x=5.0, y=45.0002)
    g.add_edge(
        2,
        3,
        key=0,
        geometry=shapely.LineString([(5.0, 45.0), (5.0, 45.0001), (5.0, 45.0002)]),
        vertices_resampled=bad,
        sac_scale="hiking",
        highway="path",
        osm_way_id=999,
    )
    with pytest.raises(PipelineContractError, match="non-finite coordinate"):
        compute_edge_metrics(g)


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


# === Story 14.2 bit-equality oracles (Q2: vectorized stage-7 metrics + deadband) ===
#
# Verbatim copies of the pre-14.2 scalar metric/deadband helpers, kept as
# bit-equality oracles: the vectorized production code must produce `==` (exact,
# not approx) metrics/elevations over every edge of the real grenoble_small
# fixture. "Verify before deleting the old code" gate (AC #1).

# Track the production constant directly so the oracle can never drift from it.
_ORACLE_DESCENT_WINDOW_M = _DESCENT_WINDOW_M


def _oracle_cumulative_2d_distances(verts: list[tuple[float, float, float]]) -> list[float]:
    """Verbatim pre-14.2 `_cumulative_2d_distances`."""
    n = len(verts)
    cum: list[float] = [0.0] * n
    if n < 2:
        return cum
    mean_lat = sum(lat for lat, _lon, _elev in verts) / n
    deg_to_m_lat = _EARTH_RADIUS_M * math.radians(1.0)
    deg_to_m_lon = deg_to_m_lat * math.cos(math.radians(mean_lat))
    for i in range(1, n):
        dlat = (verts[i][0] - verts[i - 1][0]) * deg_to_m_lat
        dlon = (verts[i][1] - verts[i - 1][1]) * deg_to_m_lon
        cum[i] = cum[i - 1] + math.hypot(dlat, dlon)
    return cum


def _oracle_elevation_gain_loss(verts: list[tuple[float, float, float]]) -> tuple[float, float]:
    """Verbatim pre-14.2 `_elevation_gain_loss`."""
    d_plus = 0.0
    d_minus = 0.0
    for i in range(1, len(verts)):
        delta = verts[i][2] - verts[i - 1][2]
        if delta > 0:
            d_plus += delta
        elif delta < 0:
            d_minus += -delta
    return d_plus, d_minus


def _oracle_max_windowed_descent_grad(
    verts: list[tuple[float, float, float]], cum_dist: list[float]
) -> float:
    """Verbatim pre-14.2 `_max_windowed_descent_grad`."""
    n = len(verts)
    if n < 2:
        return 0.0
    best = 0.0
    saw_full_window = False
    j = 0
    for i in range(n):
        if j < i:
            j = i
        while j < n - 1 and cum_dist[j] - cum_dist[i] < _ORACLE_DESCENT_WINDOW_M:
            j += 1
        run = cum_dist[j] - cum_dist[i]
        if run >= _ORACLE_DESCENT_WINDOW_M:
            saw_full_window = True
            drop = verts[i][2] - verts[j][2]
            if drop > 0.0:
                grad = drop / run
                if grad > best:
                    best = grad
    if not saw_full_window:
        total_run = cum_dist[n - 1]
        drop = verts[0][2] - verts[n - 1][2]
        if total_run > 0.0 and drop > 0.0:
            best = drop / total_run
    return best


def _oracle_deadband_profile(elevs: list[float], deadband_m: float) -> list[float]:
    """Verbatim pre-14.2 `_deadband_profile`."""
    n = len(elevs)
    if n < 3:
        return list(elevs)
    kept = [0]
    ref = elevs[0]
    for i in range(1, n):
        if abs(elevs[i] - ref) >= deadband_m:
            kept.append(i)
            ref = elevs[i]
    if kept[-1] != n - 1:
        kept.append(n - 1)
    out = list(elevs)
    for a, b in zip(kept, kept[1:], strict=False):
        span = b - a
        ea, eb = elevs[a], elevs[b]
        for jj in range(a, b + 1):
            t = (jj - a) / span if span else 0.0
            out[jj] = ea + t * (eb - ea)
    return out


def _fixture_metrics_input_graph() -> nx.MultiDiGraph:
    """Grenoble fixture through stage 6 (smooth + deadband), input to `compute_edge_metrics`."""
    import osmnx

    from steeproute.pipeline.dem import sample_elevation
    from steeproute.pipeline.osm import normalize_edges
    from steeproute.pipeline.smoothing import (
        graph_deadband_elevation,
        graph_smooth_elevation,
        resample_edges,
        smooth_polylines,
    )

    graph = normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))
    graph = smooth_polylines(graph)
    graph = resample_edges(graph)
    graph = sample_elevation(graph, _DEM_FIXTURE_PATH)
    graph = graph_smooth_elevation(graph)
    return graph_deadband_elevation(graph)


# The flat-array metrics (Story 14.2) use `np.hypot`/`np.add.reduceat` instead of
# `math.hypot`/sequential `+=`, so they match the scalar oracle to floating-point
# reordering (measured max ~1.7e-10 on the fixture; d_plus/d_minus were exactly
# equal), not bit-for-bit. Tolerances below prove numerical equivalence while
# catching real algorithmic divergence; the residual is sub-nm and does not move
# the regression goldens (verified byte-identical, no rebake).
_LENGTH_ATOL = 1e-6  # meters
_GRAD_ATOL = 1e-9  # dimensionless gradient


def test_compute_edge_metrics_numerically_equivalent_to_scalar_reference() -> None:
    """AC #1: vectorized stage-7 metrics equal the scalar oracle to fp-reordering on the fixture."""
    if not _DEM_FIXTURE_PATH.exists():
        pytest.skip("grenoble_small DEM fixture not committed.")
    metrics_input = _fixture_metrics_input_graph()
    produced = compute_edge_metrics(metrics_input)

    total = 0
    for u, v, k, data in produced.edges(data=True, keys=True):
        verts = metrics_input.edges[u, v, k]["vertices_resampled"]
        cum = _oracle_cumulative_2d_distances(verts)
        exp_length = cum[-1] if cum else 0.0
        exp_dplus, exp_dminus = _oracle_elevation_gain_loss(verts)
        exp_avg = (exp_dplus + exp_dminus) / exp_length
        exp_wdg = _oracle_max_windowed_descent_grad(verts, cum)
        ctx = f"edge ({u}, {v}, {k})"
        assert abs(data["length_m"] - exp_length) <= _LENGTH_ATOL, f"{ctx} length_m"
        assert abs(data["d_plus_m"] - exp_dplus) <= _LENGTH_ATOL, f"{ctx} d_plus_m"
        assert abs(data["d_minus_m"] - exp_dminus) <= _LENGTH_ATOL, f"{ctx} d_minus_m"
        assert abs(data["avg_gradient"] - exp_avg) <= _GRAD_ATOL, f"{ctx} avg_gradient"
        assert abs(data["max_windowed_descent_grad"] - exp_wdg) <= _GRAD_ATOL, (
            f"{ctx} max_windowed_descent_grad"
        )
        total += len(verts)
    assert total > 1000, f"expected a substantial fixture, measured only {total} vertices"


def test_graph_deadband_elevation_bit_identical_to_scalar_reference() -> None:
    """AC #1: deadband output is bit-equal to the scalar oracle on the fixture (deadband stays scalar)."""
    if not _DEM_FIXTURE_PATH.exists():
        pytest.skip("grenoble_small DEM fixture not committed.")
    import osmnx

    from steeproute.pipeline.dem import sample_elevation
    from steeproute.pipeline.osm import normalize_edges
    from steeproute.pipeline.smoothing import (
        graph_deadband_elevation,
        graph_smooth_elevation,
        resample_edges,
        smooth_polylines,
    )

    graph = normalize_edges(osmnx.load_graphml(_OSM_FIXTURE_PATH))
    graph = smooth_polylines(graph)
    graph = resample_edges(graph)
    graph = sample_elevation(graph, _DEM_FIXTURE_PATH)
    smoothed = graph_smooth_elevation(graph)

    deadband_m = 2.0  # active threshold — exercises the real hysteresis + interpolation path
    produced = graph_deadband_elevation(smoothed, deadband_m)

    total = 0
    for u, v, k, data in produced.edges(data=True, keys=True):
        src_verts = smoothed.edges[u, v, k]["vertices_resampled"]
        exp_elevs = _oracle_deadband_profile([vert[2] for vert in src_verts], deadband_m)
        got_elevs = [vert[2] for vert in data["vertices_resampled"]]
        assert got_elevs == exp_elevs, f"deadband diverged on edge ({u}, {v}, {k})"
        total += len(src_verts)
    assert total > 1000, f"expected a substantial fixture, deadbanded only {total} vertices"
