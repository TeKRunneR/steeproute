# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false
# Reason: same networkx/osmnx external boundary as `pipeline/osm.py`; this module
# deliberately reaches for osmnx internals (see the module docstring).
"""Offline canary: osmnx's real graph assembly, with and without the ownership adapter.

The unit tests prove each replacement returns what osmnx's own function returns.
They cannot prove the thing production actually depends on: that rebinding
`osmnx.truncate`'s entries is *seen* by `graph_from_polygon`, and that consuming
its intermediates in the middle of osmnx's own six-step chain still lands on an
identical graph.

So this runs the genuine `graph_from_polygon` — every truncation, simplification,
component and street-count step — with only the two network-facing steps
replaced by a synthetic graph. Those two are private osmnx symbols; reaching for
them **in a test** is the trade that keeps them out of production, and if a
future osmnx renames them this fails loudly, which is the job.

The synthetic graph is shaped so all four trimming passes have real work:

    1..6      inside the polygon, the eventual survivors
    20,40     stubs off 2 and 4, making them branch points so simplification
              leaves a non-trivial graph behind (without them the survivors
              collapse to a single node and the equality gate proves little)
    100,101   in the 500 m buffer ring — pass-1 truncation keeps them,
              pass-2 truncation removes them
    110       a stub off 100, so 100 survives simplification too and pass-2
              truncation has a ring node left to remove
    200       inside the polygon but reachable only through the ring, so
              pass-2 truncation orphans it and pass-2 component removes it
    300,301   inside, a smaller separate component — pass-1 component's work
    400,401   outside the buffer entirely — pass-1 truncation's work
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import networkx as nx
import osmnx
import pytest
import shapely

# Imported explicitly rather than reached through `osmnx._overpass`: the package
# `__init__` does not re-export it, so the attribute only exists at runtime because
# `osmnx.graph` imported it, which no type checker models.
from osmnx import _overpass as osmnx_overpass

from steeproute.pipeline import _osmnx_adapter
from steeproute.pipeline._osmnx_adapter import _ADAPTED_OSMNX_VERSIONS, osmnx_owned_intermediates

_CENTER = (45.260, 5.788)
_DIST_M = 1000.0

# Offsets in degrees from the center, chosen against the bbox this `_DIST_M`
# produces (~0.009 deg of latitude) and osmnx's 500 m buffer (~0.0045 deg).
_INSIDE_DEG = 0.004
_RING_DEG = 0.0115
_OUTSIDE_DEG = 0.030

_EDGES: tuple[tuple[int, int], ...] = (
    (1, 2),
    (2, 3),
    (3, 4),
    (4, 5),
    (5, 6),
    (2, 20),
    (4, 40),
    (6, 100),
    (100, 101),
    (100, 110),
    (101, 200),
    (300, 301),
    (400, 401),
)

_LAT_OFFSETS: dict[int, float] = {
    1: -_INSIDE_DEG,
    2: -_INSIDE_DEG / 2,
    3: 0.0,
    4: _INSIDE_DEG / 3,
    5: _INSIDE_DEG / 2,
    6: _INSIDE_DEG,
    20: -_INSIDE_DEG * 0.8,
    40: _INSIDE_DEG * 0.6,
    100: _RING_DEG,
    101: _RING_DEG + 0.0005,
    110: _RING_DEG - 0.0005,
    200: _INSIDE_DEG / 4,
    300: -_INSIDE_DEG / 3,
    301: -_INSIDE_DEG / 4,
    400: _OUTSIDE_DEG,
    401: _OUTSIDE_DEG + 0.001,
}


def _synthetic_graph() -> nx.MultiDiGraph:
    """A fresh raw-ingestion-shaped graph. Fresh every call — the adapter eats it."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.graph["crs"] = "epsg:4326"
    graph.graph["created_with"] = "test"
    for node, lat_offset in _LAT_OFFSETS.items():
        graph.add_node(
            node,
            y=_CENTER[0] + lat_offset,
            # Spread longitudes too, so simplification's geometry work is non-degenerate.
            x=_CENTER[1] + lat_offset / 2,
            street_count=2,
        )
    for u, v in _EDGES:
        for tail, head in ((u, v), (v, u)):
            graph.add_edge(
                tail,
                head,
                key=0,
                osmid=1000 + tail,
                highway="path",
                oneway=False,
                reversed=tail != u,
                length=12.5 + tail,
            )
    return graph


def _attr(value: object) -> str:
    """Exact string form of one attribute value; geometry via coordinates, not WKT repr."""
    if isinstance(value, shapely.Geometry):
        coords = shapely.get_coordinates(value)
        return f"{shapely.get_type_id(value)}:" + ",".join(repr(float(c)) for c in coords.ravel())
    if isinstance(value, list):
        return "[" + ",".join(sorted(_attr(v) for v in value)) + "]"
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


@pytest.fixture
def offline_overpass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only the two network-facing steps of `graph_from_polygon`."""

    def _no_download(*_args: object, **_kwargs: object) -> Iterator[dict[str, Any]]:
        return iter([{}])

    def _synthetic(*_args: object, **_kwargs: object) -> nx.MultiDiGraph:
        # Fresh per call: the adapter consumes its input, and the stock and adapted
        # runs each assemble from their own copy.
        return _synthetic_graph()

    monkeypatch.setattr(osmnx_overpass, "_download_overpass_network", _no_download)
    monkeypatch.setattr(osmnx.graph, "_create_graph", _synthetic)


def _fetch() -> nx.MultiDiGraph:
    polygon = osmnx.utils_geo.bbox_to_poly(osmnx.utils_geo.bbox_from_point(_CENTER, _DIST_M))
    return osmnx.graph_from_polygon(polygon, retain_all=False, simplify=True)


@pytest.fixture
def count_consuming_calls(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Count how often the consuming component step actually ran."""
    calls = 0
    original = _osmnx_adapter.largest_component_inplace

    def _counted(graph: nx.MultiDiGraph, **kwargs: object) -> nx.MultiDiGraph:
        nonlocal calls
        calls += 1
        return original(graph, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(_osmnx_adapter, "largest_component_inplace", _counted)
    return lambda: calls


@pytest.mark.usefixtures("offline_overpass")
def test_adapter_yields_the_graph_osmnx_builds_on_its_own(
    count_consuming_calls: Callable[[], int],
) -> None:
    """The headline gate, at fixture scale: identical graph through the real chain."""
    assert osmnx.__version__ in _ADAPTED_OSMNX_VERSIONS, (
        "adapter declines on unverified osmnx, which would make this test vacuous"
    )

    stock = _fetch()
    with osmnx_owned_intermediates():
        adapted = _fetch()

    assert count_consuming_calls() == 2, "both component passes must have gone through the adapter"
    assert _shape(adapted) == _shape(stock)


@pytest.mark.usefixtures("offline_overpass")
def test_the_assembly_actually_exercises_every_trimming_pass() -> None:
    """Guards the fixture, not the adapter.

    If the synthetic graph ever stops giving all four passes work to do, the
    equality test above would keep passing while proving much less.
    """
    result = _fetch()

    # Survivors are the inside-the-polygon main component only.
    assert 400 not in result and 401 not in result, "pass-1 truncation dropped nothing"
    assert 300 not in result and 301 not in result, "pass-1 component dropped nothing"
    assert 100 not in result and 101 not in result, "pass-2 truncation dropped nothing"
    assert 200 not in result, "pass-2 component dropped nothing"
    assert {1, 2, 4, 20, 40} <= set(result.nodes), "the main component did not survive"
    assert result.number_of_edges() > 0, "a node-only survivor makes the equality gate trivial"
    assert any("geometry" in d for _, _, d in result.edges(data=True)), (
        "no simplified edge carries geometry — simplification did no merging"
    )


@pytest.mark.usefixtures("offline_overpass")
def test_adapter_is_transparent_to_a_disconnecting_second_truncation() -> None:
    """`street_count` is the canary for consuming the wrong intermediate.

    osmnx counts streets on the post-simplify **buffered** graph and copies the
    result onto the un-buffered one, so a replacement that consumed pass-2
    truncation's input would rewrite every node's count while leaving the graph
    structure identical.
    """
    stock = _fetch()
    with osmnx_owned_intermediates():
        adapted = _fetch()

    assert [(n, d.get("street_count")) for n, d in adapted.nodes(data=True)] == [
        (n, d.get("street_count")) for n, d in stock.nodes(data=True)
    ]
