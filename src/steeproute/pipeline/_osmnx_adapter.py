# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx + osmnx ship partial / no type stubs; MultiDiGraph operations
# surface as Unknown. Architecture §Type hints lists OSM as an external boundary.
"""Ownership adapter for the osmnx calls inside pipeline stage 1.

`osmnx.graph_from_point` / `graph_from_polygon` build a chain of intermediate
graphs and hand each step its input by value, so every step opens with a full
copy of a graph the previous one will never read again. Measured across a warm
r20 ingestion (2026-07-29), the two steps this module replaces cost 42.2 s
(largest component, twice) and 36.9 s (truncation, twice) of a ~193 s assembly.

What lands in front of osmnx's own implementations is a **rebind of two public
`osmnx.truncate` functions**, held for exactly the duration of one fetch — not a
local re-implementation of `graph_from_polygon`. That choice is the whole safety
argument: osmnx keeps driving its own pipeline, so the 500 m buffer, both
truncation passes, both component passes, the deliberate no-re-simplify between
them, and the buffered-graph street count all stay in their original order by
construction instead of by our own careful copying. No private osmnx symbol is
touched — `truncate` and `utils` are both exported modules.

The price is a version pin. That `graph_from_polygon` resolves these through the
`truncate` module attribute at call time is an implementation detail, and which
of its intermediates are dead is read off its body. `_ADAPTED_OSMNX_VERSIONS`
records where that reading was done; anywhere else the adapter declines to engage
and stock osmnx runs.

One observable does change: two of osmnx's `--verbose` INFO lines, the ones
reporting on the r-tree and quadrat machinery the truncation replacement no
longer builds, stop appearing. Graph content, iteration order, and every log line
that describes an outcome rather than a mechanism are unaffected.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np
import shapely

if TYPE_CHECKING:
    from shapely import MultiPolygon, Polygon

logger = logging.getLogger(__name__)

_ADAPTED_OSMNX_VERSIONS: frozenset[str] = frozenset({"2.1.0"})
"""osmnx releases whose `graph_from_polygon` body this adapter was read against.

Widening this means re-reading that function and re-running the old-vs-new
ingestion diff on a real Overpass response: the ownership map in
`osmnx_owned_intermediates` is a property of the function's body, not of its
signature, so a patch release can invalidate it without changing any API.
"""


def largest_component_inplace(graph: nx.MultiDiGraph, *, strongly: bool = False) -> nx.MultiDiGraph:
    """`osmnx.truncate.largest_component`'s result, in one traversal and no copy.

    osmnx asks `is_weakly_connected` first and then, when the answer is no,
    walks the components a second time and copies the winner out via
    `nx.MultiDiGraph(G.subgraph(cc))`. Both walks answer the same question, so
    this takes the components once and deletes the losers instead.

    **Mutates and returns `graph`** — valid only where the caller owns it
    exclusively. Inside osmnx's fetch pipeline every component pass gets a dead
    intermediate; see `osmnx_owned_intermediates` for the map.

    The retained component is identical, not merely equivalent: same
    `max(..., key=len)` over the same generator, and `max` returns the first of
    several equal-sized components either way. Iteration order survives too —
    `remove_nodes_from` leaves the survivors and their adjacency in the original
    insertion order, which is exactly what building a fresh graph from a
    subgraph view of the parent reproduces.

    Raises:
        networkx.NetworkXPointlessConcept: on an empty graph, matching the guard
            osmnx inherits from `is_weakly_connected`. Without it, `max` over an
            empty component generator would surface as a bare `ValueError`.
    """
    if graph.number_of_nodes() == 0:
        raise nx.NetworkXPointlessConcept("Connectivity is undefined for the null graph.")
    components = nx.strongly_connected_components if strongly else nx.weakly_connected_components
    retained = max(components(graph), key=len)
    if len(retained) < graph.number_of_nodes():
        # Materialize before removing: `remove_nodes_from` would otherwise be
        # iterating the very node dict it is deleting from.
        graph.remove_nodes_from([n for n in graph if n not in retained])
    return graph


def _largest_component_consuming(
    graph: nx.MultiDiGraph, *, strongly: bool = False
) -> nx.MultiDiGraph:
    """The rebind target: `largest_component_inplace` plus osmnx's own log line.

    osmnx passes the graph positionally at every call site, so the parameter
    rename against its published `G` is invisible.
    """
    # Deferred so importing this module never drags in osmnx (and, through it,
    # geopandas + pandas). `pipeline/__init__` imports the stage modules, so a
    # module-level osmnx import here would land on every spawned solver worker —
    # ~4 s each, the cost `pipeline/osm.py` already defers its imports to avoid.
    # `largest_component_inplace` stays osmnx-free for the same reason.
    import osmnx

    before = graph.number_of_nodes()
    result = largest_component_inplace(graph, strongly=strongly)
    after = result.number_of_nodes()
    if after != before:
        # Reproduce osmnx's message verbatim so `--verbose` stderr is unchanged by
        # the swap. Copying a third-party log string is only safe because
        # `_ADAPTED_OSMNX_VERSIONS` pins the release whose wording this is.
        kind = "strongly" if strongly else "weakly"
        osmnx.utils.log(
            f"Got largest {kind} connected component ({after:,} of {before:,} total nodes)",
            level=logging.INFO,
        )
    return result


def nodes_outside_polygon(graph: nx.MultiDiGraph, polygon: Polygon | MultiPolygon) -> set[int]:
    """Node ids whose `(x, y)` does not intersect `polygon`.

    Same answer as the r-tree-over-quadrats machinery osmnx uses, arrived at
    directly. osmnx materializes a GeoDataFrame of every node, builds a spatial
    index over it, and cuts the polygon into quadrats to accelerate the index —
    which earns its keep against a complex boundary, but this pipeline only ever
    fetches a 5-vertex rectangle, so it is all overhead: 27.65 s → 0.63 s on the
    806k-node pass of a warm r20 ingestion (2026-07-29).

    The two agree because the quadrats tile the polygon exactly: a point
    intersects some quadrat iff it intersects the polygon. `intersects` is the
    predicate on both sides, so boundary nodes are kept, and a node with
    non-finite coordinates is outside either way.
    """
    nodes: list[int] = list(graph.nodes)
    if not nodes:
        return set()
    coords = graph.nodes
    xs = np.fromiter((coords[n]["x"] for n in nodes), dtype=np.float64, count=len(nodes))
    ys = np.fromiter((coords[n]["y"] for n in nodes), dtype=np.float64, count=len(nodes))
    inside = shapely.intersects_xy(polygon, xs, ys)
    return {n for n, keep in zip(nodes, inside.tolist(), strict=True) if not keep}


def _truncate_graph_polygon_owned(
    graph: nx.MultiDiGraph,
    polygon: Polygon | MultiPolygon,
    *,
    truncate_by_edge: bool = False,
) -> nx.MultiDiGraph:
    """Drop-in for `osmnx.truncate.truncate_graph_polygon` that respects ownership.

    Copies its input only when the input is still needed afterwards, which is
    exactly the post-simplify pass (see `osmnx_owned_intermediates` for why). The
    discriminator is `graph["simplified"]`, the flag `simplify_graph` stamps —
    semantic rather than a call counter, so it cannot silently inverse if osmnx
    reorders its pipeline.

    Two of osmnx's log lines are deliberately not reproduced: they report on the
    r-tree and quadrat machinery this no longer builds, so emitting them would be
    a lie. The three that describe what happened are kept.
    """
    import osmnx  # deferred — see `_largest_component_consuming`

    osmnx.utils.log("Identifying all nodes that lie outside the polygon...", level=logging.INFO)
    nodes_outside = nodes_outside_polygon(graph, polygon)
    if len(nodes_outside) == graph.number_of_nodes():
        # osmnx's message and type, so `osm_load`'s DataSourceUnavailableError
        # detail line reads the same either way.
        msg = "Found no graph nodes within the requested polygon."
        raise ValueError(msg)

    if truncate_by_edge:
        # Unused by this pipeline (`osm_load` never sets it), implemented anyway so
        # the drop-in cannot silently diverge from osmnx if that ever changes.
        nodes_to_remove = {
            node
            for node in nodes_outside
            if (set(graph.successors(node)) | set(graph.predecessors(node))).issubset(nodes_outside)
        }
    else:
        nodes_to_remove = nodes_outside

    target = graph.copy() if graph.graph.get("simplified") else graph
    target.remove_nodes_from(nodes_to_remove)
    osmnx.utils.log(f"Removed {len(nodes_to_remove):,} nodes outside polygon", level=logging.INFO)
    osmnx.utils.log("Truncated graph by polygon", level=logging.INFO)
    return target


@contextlib.contextmanager
def osmnx_owned_intermediates() -> Generator[None]:
    """Point osmnx's fetch pipeline at the consuming replacements, then restore.

    Wrap exactly the `graph_from_point` / `graph_from_polygon` call. The rebind
    is process-global while held, so it must not straddle anything concurrent —
    stage 1 is single-threaded and calls osmnx once. On an unadapted osmnx
    version this yields without rebinding, so setup keeps working at stock speed.

    The ownership map inside `graph_from_polygon` that makes consuming safe:

        A = _create_graph(responses)           A dead after B
        B = truncate_graph_polygon(A, buff)    B dead after C
        C = largest_component(B)               C dead after D
        D = simplify_graph(C)                  *** read again at the end ***
        E = truncate_graph_polygon(D, poly)    E dead after F
        F = largest_component(E)
        count_streets_per_node(D, nodes=F.nodes)

    Both component passes consume freely. Truncation does **not**: pass 2's input
    is `D`, and the street count reads `D` after pass 2 returns, so mutating it
    there would silently rewrite every node's `street_count` while leaving the
    graph structure identical. `_truncate_graph_polygon_owned` therefore copies on
    that pass and consumes only on pass 1, discriminating them by the
    `simplified` flag rather than by counting calls.

    One further reason consuming matters more than it looks: a `MultiDiGraph` is
    a reference cycle, so an intermediate osmnx drops is not reclaimed by
    refcounting — it lingers until a cyclic-GC pass, still resident while the next
    step allocates. Not copying it in the first place is what keeps peak RSS down;
    measured 3.50 → 3.13 GB at r20 (2026-07-29).
    """
    import osmnx  # deferred — see `_largest_component_consuming`

    if osmnx.__version__ not in _ADAPTED_OSMNX_VERSIONS:
        logger.warning(
            "osmnx %s is outside the versions this ingestion adapter was verified against "
            "(%s), so osmnx's own graph assembly runs instead. Setup still works, just slower.",
            osmnx.__version__,
            ", ".join(sorted(_ADAPTED_OSMNX_VERSIONS)),
        )
        yield
        return

    stock_component = osmnx.truncate.largest_component
    stock_truncate = osmnx.truncate.truncate_graph_polygon
    osmnx.truncate.largest_component = _largest_component_consuming
    osmnx.truncate.truncate_graph_polygon = _truncate_graph_polygon_owned
    try:
        yield
    finally:
        osmnx.truncate.largest_component = stock_component
        osmnx.truncate.truncate_graph_polygon = stock_truncate
