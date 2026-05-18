# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx + shapely operations surface as Unknown; same osmnx-boundary pattern as pipeline/osm.py.
"""Pipeline stages 3-4: 2D polyline smoothing and uniform meter-spaced resampling.

Each edge's `geometry` (a `shapely.LineString` in WGS84 lon/lat from stages 1-2)
is smoothed via a symmetric moving average and resampled to a uniform ground-meter
spacing. Operations use a per-edge local equirectangular projection (cosine-of-mean-
latitude correction) so spacing_m is honoured in real meters; we keep the graph in
WGS84 throughout (Story 2.3 handles CRS at the DEM boundary, not here).

Endpoints are preserved exactly across both stages: topology — node coordinates —
must not drift. Edges with degenerate geometry (fewer than 2 distinct finite
points) are dropped from the output graph; carry-forward policy from Story 2.1.
Non-LineString geometry on a pipeline edge is treated as an upstream contract
violation and raises `TypeError` (fail-fast).

Resample-spacing contract: vertex spacing is uniform within float roundoff —
`actual_spacing = total / n_intervals`, with `n_intervals = round(total / spacing_m)`
— and the equirectangular round-trip adds sub-‰ drift over edge-scale distances.
"""

from __future__ import annotations

import math

import networkx as nx
import shapely

# Symmetric moving-average window for stage 3, in vertices. Window = 3 means each
# interior vertex is the mean of itself and its two neighbours; endpoints pinned.
SMOOTHING_WINDOW: int = 3

# Default vertex spacing for stage 4, in ground meters along the polyline.
RESAMPLE_SPACING_M: float = 10.0

# WGS84 equatorial radius for the local equirectangular projection.
_EARTH_RADIUS_M: float = 6_378_137.0


def smooth_polylines(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Stage 3: moving-average smooth each edge's 2D polyline, preserving endpoints.

    Each interior vertex is replaced with the mean of itself and its
    `SMOOTHING_WINDOW // 2` neighbours on either side. First and last vertices
    are pinned to their input values (topology preserved).

    Edges with degenerate geometry (fewer than 2 distinct finite points) are
    dropped from the output graph. Stages 5-7 may then assume non-degenerate
    polylines.

    Raises:
        TypeError: if any edge's `geometry` is not a `shapely.LineString`
            (upstream contract violation; fail-fast).

    Returns a new `MultiDiGraph`; the input graph is never mutated.
    """
    out: nx.MultiDiGraph = graph.copy()
    edges_to_drop: list[tuple[int, int, int]] = []
    for u, v, k, data in out.edges(data=True, keys=True):
        coords = _extract_coords(data.get("geometry"))
        if not is_valid_polyline(coords):
            edges_to_drop.append((u, v, k))
            continue
        smoothed = _moving_average(coords, SMOOTHING_WINDOW)
        data["geometry"] = shapely.LineString(smoothed)
    for u, v, k in edges_to_drop:
        out.remove_edge(u, v, k)
    return out


def resample_edges(
    graph: nx.MultiDiGraph,
    spacing_m: float = RESAMPLE_SPACING_M,
) -> nx.MultiDiGraph:
    """Stage 4: replace each edge's geometry with vertices at ~`spacing_m` ground meters.

    Vertex spacing is uniform along the polyline. First and last coordinates
    are returned exactly equal to the input's first and last (topology
    preserved); interior vertices land at evenly-divided fractions of the
    polyline's total length.

    Edges with degenerate geometry (fewer than 2 distinct finite points) are
    dropped from the output graph.

    Args:
        graph: input graph with `shapely.LineString` `geometry` on every edge.
        spacing_m: target ground-meter spacing between consecutive vertices;
            must be a positive finite number.

    Returns:
        New `MultiDiGraph`; the input graph is never mutated.

    Raises:
        ValueError: if `spacing_m` is non-positive or non-finite.
        TypeError: if any edge's `geometry` is not a `shapely.LineString`.
    """
    if not math.isfinite(spacing_m) or spacing_m <= 0:
        raise ValueError(f"spacing_m must be a positive finite number (got {spacing_m})")
    out: nx.MultiDiGraph = graph.copy()
    edges_to_drop: list[tuple[int, int, int]] = []
    for u, v, k, data in out.edges(data=True, keys=True):
        coords = _extract_coords(data.get("geometry"))
        if not is_valid_polyline(coords):
            edges_to_drop.append((u, v, k))
            continue
        resampled = _resample_meters(coords, spacing_m)
        data["geometry"] = shapely.LineString(resampled)
    for u, v, k in edges_to_drop:
        out.remove_edge(u, v, k)
    return out


def _extract_coords(geometry: object) -> list[tuple[float, float]]:
    """Return [(lon, lat), ...] from a 2D or 3D shapely LineString (z dropped).

    Non-LineString geometry on a pipeline edge is an upstream contract violation;
    raises `TypeError` rather than silently dropping the edge.
    """
    if not isinstance(geometry, shapely.LineString):
        raise TypeError(
            "pipeline.smoothing: edge geometry must be a shapely.LineString, "
            f"got {type(geometry).__name__}"
        )
    return [(float(c[0]), float(c[1])) for c in geometry.coords]


def is_valid_polyline(coords: list[tuple[float, float]]) -> bool:
    """True if `coords` defines a non-degenerate polyline (>=2 distinct, finite points).

    Public so the hypothesis property test in `tests/unit/test_smoothing.py` can
    use the same validity check as production via `hypothesis.assume`, avoiding
    drift between the strategy filter and the actual stage's degenerate-edge guard.
    """
    if len(coords) < 2:
        return False
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in coords):
        return False
    first = coords[0]
    return any(c != first for c in coords[1:])


def _moving_average(
    coords: list[tuple[float, float]],
    window: int,
) -> list[tuple[float, float]]:
    """Moving-average smoothing with endpoints pinned to input values.

    The window is centered on each interior vertex; near the polyline boundaries
    the window is clamped (smaller, asymmetric) to avoid stepping out of bounds.
    For `window=3` (the only value used today) the clamp produces no asymmetry.
    `window` must be odd and >= 1.
    """
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


def _resample_meters(
    coords: list[tuple[float, float]],
    spacing_m: float,
) -> list[tuple[float, float]]:
    """Resample `coords` (lon, lat in WGS84) to uniform ~`spacing_m` ground-meter spacing.

    Uses a local equirectangular projection at the polyline's mean latitude:
    accurate to ~0.1% over edge-scale distances (tens of meters to a few km),
    no external projection dependency. First and last vertices are pinned to
    the input's first and last exactly so endpoints match input node coords.
    """
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
        # Clamp t against float drift accumulating past cumulative[-1]: without
        # this, the tail interior vertex can extrapolate past v's projected
        # location and produce a non-monotone "bulge" before the pinned endpoint.
        t = (d - cumulative[seg]) / seg_len if seg_len > 0 else 0.0
        t = max(0.0, min(1.0, t))
        x = xy[seg][0] + t * (xy[seg + 1][0] - xy[seg][0])
        y = xy[seg][1] + t * (xy[seg + 1][1] - xy[seg][1])
        out.append((x / deg_to_m_lon, y / deg_to_m_lat))
    out.append(coords[-1])
    return out
