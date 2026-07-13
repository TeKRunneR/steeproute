# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeArgument=false, reportUnknownParameterType=false
# Reason: networkx MultiDiGraph edge-data access surfaces as Unknown — same
# external-boundary pattern as the pipeline modules under test.
"""Query-side reshape de-churn: one working copy instead of one per stage.

`operationalize_graph` used to `graph.copy()` inside each of `graph_smooth_elevation`,
`graph_deadband_elevation`, and `compute_edge_metrics` — three full-graph copies
(~5 s each on an r20 graph). It now makes ONE working copy and threads the stages
through it `inplace=True`. These tests pin the two invariants that keeps safe:

- the `inplace` flag mutates the passed graph and returns it (vs a fresh copy at
  the default `inplace=False`, which leaves the input pure);
- `operationalize_graph` is still pure (its single top-level copy shields the
  caller) AND byte-identical to the old copy-per-stage sequence.
"""

from __future__ import annotations

import networkx as nx
import shapely

from steeproute.pipeline import operationalize_graph
from steeproute.pipeline.climbs import compute_edge_metrics
from steeproute.pipeline.smoothing import graph_deadband_elevation, graph_smooth_elevation

_METRICS = ("length_m", "d_plus_m", "d_minus_m", "avg_gradient", "max_windowed_descent_grad")


def _reshape_graph() -> nx.MultiDiGraph:
    """A two-edge chain carrying `vertices_resampled` (lat, lon, elev) for stages 6-7.

    ~11 m vertex spacing (so the 50 m smoothing window is active) with a sub-1 m
    elevation wiggle on a sustained rise (so a 1 m deadband actually reshapes it).
    """
    e0 = [
        (45.0000, 6.0, 100.0),
        (45.0001, 6.0, 100.3),
        (45.0002, 6.0, 101.0),
        (45.0003, 6.0, 100.7),
        (45.0004, 6.0, 103.0),
    ]
    e1 = [
        (45.0004, 6.0, 103.0),
        (45.0005, 6.0, 103.4),
        (45.0006, 6.0, 103.1),
        (45.0007, 6.0, 106.0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for a, b, verts in ((0, 1, e0), (1, 2, e1)):
        g.add_edge(
            a,
            b,
            key=0,
            geometry=shapely.LineString([(lon, lat) for lat, lon, _e in verts]),
            vertices_resampled=list(verts),
            sac_scale="hiking",
            highway="path",
            osm_way_id=100 + a,
        )
    return g


def test_compute_edge_metrics_inplace_flag() -> None:
    """`inplace=True` mutates + returns the same object; default leaves the input pure."""
    g = _reshape_graph()
    copied = compute_edge_metrics(g)  # default inplace=False
    assert copied is not g
    assert "length_m" not in g.edges[0, 1, 0], "default call must not mutate the input"
    assert "length_m" in copied.edges[0, 1, 0]

    g2 = _reshape_graph()
    in_place = compute_edge_metrics(g2, inplace=True)
    assert in_place is g2, "inplace=True must return the same graph object"
    assert "length_m" in g2.edges[0, 1, 0], "inplace=True must mutate the passed graph"

    # Same metric values either way — inplace is purely a copy-avoidance knob.
    for m in _METRICS:
        assert copied.edges[0, 1, 0][m] == in_place.edges[0, 1, 0][m]


def test_smooth_and_deadband_inplace_return_same_object() -> None:
    """The two elevation-reshaping stages honour `inplace=True` (same object mutated)."""
    g = _reshape_graph()
    assert graph_smooth_elevation(g, 50.0, inplace=True) is g
    assert graph_deadband_elevation(g, 1.0, inplace=True) is g
    # Default stays pure (new object).
    g2 = _reshape_graph()
    assert graph_smooth_elevation(g2, 50.0) is not g2


def test_operationalize_graph_pure_and_bit_identical_to_copy_per_stage() -> None:
    """One-copy `operationalize_graph` == old copy-per-stage sequence, input untouched."""
    original_vr = {
        (u, v, k): list(d["vertices_resampled"])
        for u, v, k, d in _reshape_graph().edges(data=True, keys=True)
    }

    g = _reshape_graph()
    op = operationalize_graph(g, elevation_smoothing_m=50.0, elevation_deadband_m=1.0)

    # Purity: the input graph gained no metrics and kept its raw vertices.
    for u, v, k, d in g.edges(data=True, keys=True):
        assert "length_m" not in d
        assert d["vertices_resampled"] == original_vr[(u, v, k)]

    # Bit-identity: same result as calling the pure stages in sequence.
    ref = compute_edge_metrics(graph_deadband_elevation(graph_smooth_elevation(g, 50.0), 1.0))
    for u, v, k, d in op.edges(data=True, keys=True):
        assert d["vertices_resampled"] == ref.edges[u, v, k]["vertices_resampled"]
        for m in _METRICS:
            assert d[m] == ref.edges[u, v, k][m]
