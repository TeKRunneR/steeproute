# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
# Reason: same networkx/osmnx external boundary as the module under test.
"""Tests for `pipeline/_osmnx_adapter.py`.

Every equality test here compares against **osmnx's own `largest_component`**
rather than against a restatement of the replacement's logic. That is the point:
the adapter's whole claim is "same answer, less work", and a test that encoded
the replacement's algorithm a second time would agree with a wrong one.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator
from typing import TYPE_CHECKING, cast, override

import networkx as nx
import osmnx
import pytest
import shapely

if TYPE_CHECKING:
    import geopandas as gpd

from steeproute.pipeline._osmnx_adapter import (
    _ADAPTED_OSMNX_VERSIONS,
    _largest_component_consuming,
    _truncate_graph_polygon_owned,
    largest_component_inplace,
    nodes_outside_polygon,
    osmnx_owned_intermediates,
)


def _graph(components: list[list[int]], *, back_edges: bool = False) -> nx.MultiDiGraph:
    """Chain each group of node ids into its own weakly connected component.

    Node insertion order is the order given, so a test can tell an order
    regression apart from a content one. `back_edges` closes every chain in the
    reverse direction too, making each component strongly connected.
    """
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.graph["crs"] = "epsg:4326"
    graph.graph["simplified"] = True
    for group in components:
        for i, node in enumerate(group):
            graph.add_node(node, x=5.0 + node / 1000.0, y=45.0 + node / 1000.0, street_count=2)
            if i:
                previous = group[i - 1]
                graph.add_edge(
                    previous,
                    node,
                    key=0,
                    length=10.0 * node,
                    highway="path",
                    geometry=shapely.LineString([(5.0, 45.0), (5.1, 45.1)]),
                )
                if back_edges:
                    graph.add_edge(node, previous, key=0, length=10.0 * node, highway="path")
    return graph


def _attr(value: object) -> str:
    """Exact string form of one attribute value.

    Geometry goes through `get_coordinates`, not `repr`: a `LineString`'s repr is
    rounded WKT, so `repr` alone silently equates polylines that differ in the
    low-order bits. `repr` on a float is the shortest round-tripping form, hence
    a bijection with the float64 value.
    """
    if isinstance(value, shapely.Geometry):
        coords = shapely.get_coordinates(value)
        return f"{shapely.get_type_id(value)}:" + ",".join(repr(float(c)) for c in coords.ravel())
    return repr(value)


def _shape(graph: nx.MultiDiGraph) -> tuple[object, object, object]:
    """Node sequence, edge sequence and graph attrs — order-sensitive, attrs included."""
    return (
        [
            (n, sorted(((k, _attr(v)) for k, v in d.items()), key=repr))
            for n, d in graph.nodes(data=True)
        ],
        [
            (u, v, k, sorted(((key, _attr(value)) for key, value in d.items()), key=repr))
            for u, v, k, d in graph.edges(data=True, keys=True)
        ],
        sorted(((k, _attr(v)) for k, v in graph.graph.items()), key=repr),
    )


@pytest.mark.parametrize("strongly", [False, True])
def test_largest_component_inplace_matches_osmnx_on_disconnected_graph(strongly: bool) -> None:
    graph = _graph([[1, 2, 3], [10, 11, 12, 13, 14], [20, 21]], back_edges=True)
    expected = osmnx.truncate.largest_component(graph.copy(), strongly=strongly)

    assert _shape(largest_component_inplace(graph.copy(), strongly=strongly)) == _shape(expected)


def test_largest_component_inplace_matches_osmnx_when_already_connected() -> None:
    graph = _graph([[1, 2, 3, 4]])
    expected = osmnx.truncate.largest_component(graph.copy())

    assert _shape(largest_component_inplace(graph.copy())) == _shape(expected)


def test_largest_component_inplace_matches_osmnx_on_weakly_but_not_strongly_connected() -> None:
    """A one-way chain is weakly connected but has N strongly connected components."""
    graph = _graph([[1, 2, 3, 4]])

    assert _shape(largest_component_inplace(graph.copy(), strongly=True)) == _shape(
        osmnx.truncate.largest_component(graph.copy(), strongly=True)
    )


def test_largest_component_inplace_breaks_size_ties_like_osmnx() -> None:
    """Equal-sized components: both implementations must keep the same one."""
    graph = _graph([[1, 2, 3], [10, 11, 12], [20, 21, 22]], back_edges=True)
    expected = osmnx.truncate.largest_component(graph.copy())

    kept = largest_component_inplace(graph.copy())

    assert set(kept.nodes) == {1, 2, 3}, "expected the first of the equal-sized components"
    assert _shape(kept) == _shape(expected)


def test_largest_component_inplace_preserves_node_order_when_the_survivors_are_interleaved() -> (
    None
):
    """Survivors keep their original insertion order, not a re-sorted or grouped one."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    for node in (50, 1, 60, 2, 70, 3):
        graph.add_node(node, x=0.0, y=0.0)
    graph.add_edge(1, 2, key=0)
    graph.add_edge(2, 3, key=0)

    kept = largest_component_inplace(graph)

    assert list(kept.nodes) == [1, 2, 3]


def test_largest_component_inplace_mutates_and_returns_its_input() -> None:
    graph = _graph([[1, 2, 3, 4], [20, 21]])

    result = largest_component_inplace(graph)

    assert result is graph
    assert 20 not in graph


def test_largest_component_inplace_rejects_the_null_graph_like_osmnx() -> None:
    """osmnx inherits this guard from `is_weakly_connected`; the drop-in keeps it."""
    with pytest.raises(nx.NetworkXPointlessConcept):
        osmnx.truncate.largest_component(nx.MultiDiGraph())
    with pytest.raises(nx.NetworkXPointlessConcept):
        largest_component_inplace(nx.MultiDiGraph())


@contextlib.contextmanager
def _captured_osmnx_log() -> Generator[list[str]]:
    """Collect osmnx's own log records, independent of root-logger wiring.

    Deliberately not `caplog`: that attaches to the **root** logger, so it sees
    nothing once anything in the session has left `OSMnx.propagate` False — which
    `cli/setup.py:_configure_osmnx_logging` does for every non-verbose run, making
    a caplog-based assertion here pass alone and fail in a full-suite run.

    Attaching a handler before the first `osmnx.utils.log` call also suppresses the
    `logs/` directory osmnx's `_get_logger` would otherwise create, since it only
    bolts its FileHandler on when the logger has none.
    """
    osmnx.settings.log_file = True
    osmnx_logger = logging.getLogger(osmnx.settings.log_name)
    messages: list[str] = []

    class _Collect(logging.Handler):
        @override
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Collect(level=logging.INFO)
    previous_level = osmnx_logger.level
    osmnx_logger.addHandler(handler)
    osmnx_logger.setLevel(logging.INFO)
    try:
        yield messages
    finally:
        osmnx_logger.removeHandler(handler)
        osmnx_logger.setLevel(previous_level)


def test_consuming_wrapper_emits_the_same_log_line_as_osmnx() -> None:
    """`--verbose` stderr must not change when the swap is in effect."""
    graph = _graph([[1, 2, 3, 4], [20, 21]])

    with _captured_osmnx_log() as messages:
        osmnx.truncate.largest_component(graph.copy())
        stock = list(messages)
        messages.clear()
        _largest_component_consuming(graph.copy())
        swapped = list(messages)

    assert stock == swapped
    assert any("largest weakly connected component" in message for message in stock)


def test_consuming_wrapper_is_silent_when_nothing_is_trimmed() -> None:
    """osmnx logs only inside its disconnected branch; so must the replacement."""
    graph = _graph([[1, 2, 3, 4]])

    with _captured_osmnx_log() as messages:
        _largest_component_consuming(graph)

    assert messages == []


def _spatial_graph(coords: dict[int, tuple[float, float]]) -> nx.MultiDiGraph:
    """Nodes at explicit `(lon, lat)`, chained in the order given. `crs` for osmnx's gdf path."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.graph["crs"] = "epsg:4326"
    previous: int | None = None
    for node, (lon, lat) in coords.items():
        graph.add_node(node, x=lon, y=lat, street_count=2)
        if previous is not None:
            graph.add_edge(previous, node, key=0, highway="path", length=10.0)
            graph.add_edge(node, previous, key=0, highway="path", length=10.0)
        previous = node
    return graph


_SQUARE = shapely.box(5.0, 45.0, 6.0, 46.0)


@pytest.mark.parametrize("truncate_by_edge", [False, True])
def test_owned_truncation_keeps_the_same_nodes_as_osmnx(truncate_by_edge: bool) -> None:
    """Boundary and corner nodes included — `intersects` keeps them, so both must."""
    graph = _spatial_graph(
        {
            1: (5.5, 45.5),  # interior
            2: (5.0, 45.5),  # exactly on the west edge
            3: (5.0, 45.0),  # exactly on a corner
            4: (6.0, 46.0),  # exactly on the opposite corner
            5: (4.999999, 45.5),  # a hair outside
            6: (7.0, 47.0),  # far outside
            7: (5.9, 45.9),  # interior
        }
    )

    expected = osmnx.truncate.truncate_graph_polygon(
        graph.copy(), _SQUARE, truncate_by_edge=truncate_by_edge
    )
    actual = _truncate_graph_polygon_owned(graph.copy(), _SQUARE, truncate_by_edge=truncate_by_edge)

    assert _shape(actual) == _shape(expected)


def test_owned_truncation_reports_the_same_outside_set_as_osmnx_quadrat_index() -> None:
    """The selection primitive alone, against osmnx's r-tree-over-quadrats result."""
    graph = _spatial_graph(
        {i: (5.0 + i * 0.13, 45.0 + i * 0.11) for i in range(1, 25)}
        | {100: (4.5, 44.5), 101: (6.5, 46.5)}
    )
    # `graph_to_gdfs(edges=False)` is typed as returning a GeoDataFrame, so indexing
    # it widens to Series | GeoDataFrame; the runtime value is the GeoSeries that
    # osmnx's own helper expects.
    gs_nodes = cast("gpd.GeoSeries", osmnx.convert.graph_to_gdfs(graph, edges=False)["geometry"])

    inside_per_osmnx = osmnx.utils_geo._intersect_index_quadrats(gs_nodes, _SQUARE)

    assert nodes_outside_polygon(graph, _SQUARE) == set(graph.nodes) - set(inside_per_osmnx)


def test_owned_truncation_treats_non_finite_coordinates_as_outside() -> None:
    graph = _spatial_graph({1: (5.5, 45.5), 2: (float("nan"), 45.5)})

    assert nodes_outside_polygon(graph, _SQUARE) == {2}


def test_owned_truncation_consumes_only_the_pre_simplify_pass() -> None:
    """The `simplified` flag is what protects the graph the street count still reads."""
    pre_simplify = _spatial_graph({1: (5.5, 45.5), 2: (7.0, 47.0)})
    post_simplify = _spatial_graph({1: (5.5, 45.5), 2: (7.0, 47.0)})
    post_simplify.graph["simplified"] = True

    consumed = _truncate_graph_polygon_owned(pre_simplify, _SQUARE)
    copied = _truncate_graph_polygon_owned(post_simplify, _SQUARE)

    assert consumed is pre_simplify, "the dead intermediate should be mutated, not copied"
    assert copied is not post_simplify, "the graph the street count reads must survive intact"
    assert 2 in post_simplify, "the post-simplify input was mutated — street_count would shift"


def test_owned_truncation_raises_like_osmnx_when_nothing_is_inside() -> None:
    graph = _spatial_graph({1: (7.0, 47.0), 2: (8.0, 48.0)})

    with pytest.raises(ValueError, match="Found no graph nodes within the requested polygon"):
        osmnx.truncate.truncate_graph_polygon(graph.copy(), _SQUARE)
    with pytest.raises(ValueError, match="Found no graph nodes within the requested polygon"):
        _truncate_graph_polygon_owned(graph.copy(), _SQUARE)


def test_adapter_rebinds_both_public_truncate_entries_and_restores_them() -> None:
    stock_component = osmnx.truncate.largest_component
    stock_truncate = osmnx.truncate.truncate_graph_polygon

    with osmnx_owned_intermediates():
        assert osmnx.truncate.largest_component is _largest_component_consuming
        assert osmnx.truncate.truncate_graph_polygon is _truncate_graph_polygon_owned

    assert osmnx.truncate.largest_component is stock_component
    assert osmnx.truncate.truncate_graph_polygon is stock_truncate


def test_adapter_restores_the_rebind_when_the_body_raises() -> None:
    stock_component = osmnx.truncate.largest_component
    stock_truncate = osmnx.truncate.truncate_graph_polygon

    with pytest.raises(RuntimeError), osmnx_owned_intermediates():
        raise RuntimeError("fetch blew up")

    assert osmnx.truncate.largest_component is stock_component
    assert osmnx.truncate.truncate_graph_polygon is stock_truncate


def test_adapter_declines_on_an_unverified_osmnx_version(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An osmnx bump must cost the optimization, not the ability to run setup."""
    monkeypatch.setattr(osmnx, "__version__", "2.99.0")
    stock_component = osmnx.truncate.largest_component
    stock_truncate = osmnx.truncate.truncate_graph_polygon

    with caplog.at_level(logging.WARNING), osmnx_owned_intermediates():
        assert osmnx.truncate.largest_component is stock_component
        assert osmnx.truncate.truncate_graph_polygon is stock_truncate

    assert "2.99.0" in caplog.text
    assert "2.1.0" in caplog.text


def test_installed_osmnx_is_one_the_adapter_was_verified_against() -> None:
    """Drift canary.

    The adapter's safety rests on a reading of `graph_from_polygon`'s body — which
    intermediates are dead, and that it resolves `truncate.largest_component`
    through the module attribute. Neither is part of osmnx's API, so an upgrade
    has to re-run the ingestion diff before widening
    `_ADAPTED_OSMNX_VERSIONS`. Until then setup silently falls back to stock
    osmnx, and this test is the only thing that says so out loud.
    """
    assert osmnx.__version__ in _ADAPTED_OSMNX_VERSIONS


def test_osmnx_graph_assembly_sees_the_rebound_component_entry() -> None:
    """`graph_from_polygon` must reach the rebind through its `truncate` module ref.

    A future osmnx that did `from .truncate import largest_component` would still
    pass every test above while silently ignoring the rebind in production.
    """
    assert osmnx.graph.truncate is osmnx.truncate
