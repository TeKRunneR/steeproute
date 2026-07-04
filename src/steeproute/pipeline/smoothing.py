# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx + shapely operations surface as Unknown; same osmnx-boundary pattern as pipeline/osm.py.
"""Pipeline stages 3-4 (setup) + 6 (query): 2D polyline smoothing, resampling, and elevation reshaping.

Stages 3-4 operate on each edge's `geometry` (a `shapely.LineString` in WGS84
lon/lat from stages 1-2). The polyline is smoothed via a symmetric moving
average then resampled to a uniform ground-meter spacing. Operations use a
per-edge local equirectangular projection (cosine-of-mean-latitude correction)
so spacing_m is honoured in real meters; we keep the graph in WGS84 throughout
(Story 2.3 handles CRS at the DEM boundary, not here).

Stage 6 — the canonical-elevation-profile reshaping (Story 6.3) — moved
**query-side**: setup caches the raw post-stage-5 elevation, and the query CLI
applies `graph_smooth_elevation` then `graph_deadband_elevation` once over the
whole graph before naive-sum metrics (stage 7). The two together produce the
*single* elevation profile that the metric box, the solver objective, and the
plotted curve all read — see each function's docstring. The pre-6.3 per-edge
`median_smooth_elevation` is gone: per-edge smoothing pinned the shared node
elevation per-edge, so incident edges disagreed at the join (box ≠ curve) and
short edges with no interior went unsmoothed. The global graph-Laplacian fixes
both — each graph node is one shared variable, so joins stay consistent.

Endpoints are preserved exactly across stages 3-4: topology — node coordinates
— must not drift. Edges with degenerate geometry (fewer than 2 distinct finite
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
import numpy as np
import shapely

# Symmetric moving-average window for stage 3, in vertices. Window = 3 means each
# interior vertex is the mean of itself and its two neighbours; endpoints pinned.
SMOOTHING_WINDOW: int = 3

# Default vertex spacing for stage 4, in ground meters along the polyline.
RESAMPLE_SPACING_M: float = 10.0

# Default strength of the query-side elevation smoothing, in ground meters. This
# replaces the removed per-edge median (which smoothed over ~50 m at the 10 m
# resample spacing) so the cliff-bias mitigation it provided is preserved by
# default; `--elevation-smoothing` overrides it. Expressed in meters and
# converted to a vertex window internally so it is decoupled from the resample
# spacing (a future spacing change re-derives the iteration count automatically).
ELEVATION_SMOOTHING_DEFAULT_M: float = 50.0

# Default elevation deadband, in meters. 0 disables the profile transform —
# matching pre-6.3 behaviour, where gain/loss summed every raw delta. The
# deadband is opt-in via `--elevation-deadband`.
ELEVATION_DEADBAND_DEFAULT_M: float = 0.0

# Jacobi relaxation factor for the graph-Laplacian diffusion. 0.5 is the standard
# under-relaxed low-pass step: stable (never overshoots) and its per-iteration
# Gaussian-equivalent sigma is ~sqrt(iters/2) vertices, the mapping the
# meters→iterations conversion in `graph_smooth_elevation` relies on.
_DIFFUSION_LAMBDA: float = 0.5

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


def graph_smooth_elevation(
    graph: nx.MultiDiGraph,
    strength_m: float = ELEVATION_SMOOTHING_DEFAULT_M,
) -> nx.MultiDiGraph:
    """Stage 6a: global graph-Laplacian diffusion of the whole elevation field.

    Treats the entire resampled profile as ONE connected field and runs Jacobi
    (Laplacian) diffusion over it: every vertex relaxes toward the mean of its
    chain neighbours, and **each graph node is a single shared variable** whose
    neighbours are the vertex adjacent to it on every incident edge. Two
    consequences matter:

    * Diffusion is a low-pass filter — it strictly shrinks adjacent elevation
      differences, so it can never *create* a slope spike (the failure mode of
      the per-edge moving-average it replaces, which manufactured ~1000 %
      segments by dumping a node offset into one ~10 m segment).
    * Because a node is one shared value, edges incident to it stay consistent
      at the join, so the per-edge metric sum (stage 7) equals the plotted-curve
      sum (`output._profile_series`): **box == curve** by construction. It also
      smooths *across* short / 2-vertex edges, which per-edge methods cannot.

    `strength_m` is the smoothing length in ground meters. It is converted to a
    vertex window (`strength_m / RESAMPLE_SPACING_M`) and then to a Jacobi
    iteration count via the Gaussian-equivalence `sigma ≈ sqrt(iters/2)` of a
    `λ=0.5` step — a moving-average window `W` matches `iters ≈ W² / 6`. A
    `strength_m` at or below the resample spacing (window ≤ 1) is a no-op and the
    input graph is returned unchanged.

    Vectorization (Story 13.1): the whole field lives in ONE flat float64 array —
    node values first, then each edge's interior vertices in edge-iteration
    order — and every Jacobi sweep is a few numpy array passes instead of
    per-vertex Python loops (~417 whole-graph sweeps at the 50 m default made
    the loop form the dominant query-side cost on large areas). Results are
    BIT-IDENTICAL to the scalar formulation, not merely close (pinned by the
    scalar-reference tests in `tests/unit/test_smoothing.py`), so regression
    goldens don't move. Two details make that exact: the interior rule keeps
    the literal `(left + right) / 2` shape, and the per-node neighbour sum
    replicates CPython's builtin `sum()` — which since Python 3.12 is Neumaier
    COMPENSATED summation, not naive sequential adds — via round-by-round
    compensated accumulation (round r adds every node's r-th adjacency entry
    in per-node adjacency order; max-degree rounds total, each a pure array op).

    Only the elevation component of each `(lat, lon, elev)` triple is touched;
    `(lat, lon)` pass through unchanged. Returns a new MultiDiGraph; the input is
    never mutated.
    """
    window = strength_m / RESAMPLE_SPACING_M
    if window <= 1.0:
        return graph
    iters = max(1, round(window * window / 6.0))
    lam = _DIFFUSION_LAMBDA
    out: nx.MultiDiGraph = graph.copy()

    # One shared elevation variable per node; per-edge interior vertices are
    # private. Raw node elevations are already consistent across incident edges
    # (same DEM sample at the shared coordinate), so seeding from any incident
    # endpoint is well-defined. Field layout: x[0:n_nodes] = node values,
    # x[n_nodes:] = interior vertices, edge blocks in edge-iteration order.
    node_index: dict[int, int] = {}
    node_seed: list[float] = []
    edge_keys: list[tuple[int, int, int]] = []
    interiors: list[list[float]] = []
    for u, v, k, data in out.edges(data=True, keys=True):
        elevs = [vert[2] for vert in data["vertices_resampled"]]
        if u not in node_index:
            node_index[u] = len(node_seed)
            node_seed.append(elevs[0])
        if v not in node_index:
            node_index[v] = len(node_seed)
            node_seed.append(elevs[-1])
        edge_keys.append((u, v, k))
        interiors.append(elevs[1:-1])

    n_nodes = len(node_seed)
    starts: list[int] = []
    pos = n_nodes
    for ints in interiors:
        starts.append(pos)
        pos += len(ints)
    x = np.array(node_seed + [e for ints in interiors for e in ints], dtype=np.float64)

    # Node adjacency as flat index arrays: entry i says "node adj_node[i]'s
    # neighbour sum includes field value x[adj_src[i]]". The adjacent value is
    # the edge-end interior vertex, or the opposite node when the edge has no
    # interior. Entries are appended u-then-v per edge, matching the scalar
    # version's per-node adjacency order.
    adj_node: list[int] = []
    adj_src: list[int] = []
    left_src: list[int] = []
    right_src: list[int] = []
    for (u, v, _k), ints, s in zip(edge_keys, interiors, starts, strict=True):
        ui, vi = node_index[u], node_index[v]
        m = len(ints)
        adj_node.append(ui)
        adj_src.append(s if m else vi)
        adj_node.append(vi)
        adj_src.append(s + m - 1 if m else ui)
        for j in range(m):
            left_src.append(ui if j == 0 else s + j - 1)
            right_src.append(vi if j == m - 1 else s + j + 1)

    degree = np.bincount(np.asarray(adj_node, dtype=np.intp), minlength=n_nodes).astype(np.float64)
    left = np.asarray(left_src, dtype=np.intp)
    right = np.asarray(right_src, dtype=np.intp)

    # Regroup adjacency entries into ROUNDS: round r holds every node's r-th
    # entry (in per-node adjacency order), so a node appears at most once per
    # round and rounds can be applied as conflict-free vector ops. There are
    # max-degree rounds. This is what lets the per-node neighbour sum replicate
    # CPython's compensated `sum()` exactly (see docstring): each round performs
    # one Neumaier step for all nodes at once, in each node's original order.
    occurrence: dict[int, int] = {}
    round_nodes_l: list[list[int]] = []
    round_src_l: list[list[int]] = []
    for node_slot, src_slot in zip(adj_node, adj_src, strict=True):
        r = occurrence.get(node_slot, 0)
        occurrence[node_slot] = r + 1
        if r == len(round_nodes_l):
            round_nodes_l.append([])
            round_src_l.append([])
        round_nodes_l[r].append(node_slot)
        round_src_l[r].append(src_slot)
    round_nodes = [np.asarray(a, dtype=np.intp) for a in round_nodes_l]
    round_src = [np.asarray(a, dtype=np.intp) for a in round_src_l]

    for _ in range(iters):
        x_new = np.empty_like(x)
        # Per-node Neumaier compensated sum, one round per adjacency rank.
        # Mirrors CPython's float `sum()` fast path: t = s + val; comp gains
        # the rounding residue of whichever operand dominates; result s + comp.
        sums = np.zeros(n_nodes, dtype=np.float64)
        comp = np.zeros(n_nodes, dtype=np.float64)
        for r_nodes, r_src in zip(round_nodes, round_src, strict=True):
            val = x[r_src]
            s_old = sums[r_nodes]
            t = s_old + val
            comp[r_nodes] += np.where(
                np.abs(s_old) >= np.abs(val), (s_old - t) + val, (val - t) + s_old
            )
            sums[r_nodes] = t
        sums += comp
        x_new[:n_nodes] = (1 - lam) * x[:n_nodes] + lam * (sums / degree)
        x_new[n_nodes:] = (1 - lam) * x[n_nodes:] + lam * ((x[left] + x[right]) / 2)
        x = x_new

    for (u, v, k), ints, s in zip(edge_keys, interiors, starts, strict=True):
        data = out.edges[u, v, k]
        verts: list[tuple[float, float, float]] = data["vertices_resampled"]
        elevs = [
            float(x[node_index[u]]),
            *(x[s : s + len(ints)].tolist()),
            float(x[node_index[v]]),
        ]
        data["vertices_resampled"] = [
            (lat, lon, e) for (lat, lon, _orig), e in zip(verts, elevs, strict=True)
        ]
    return out


def graph_deadband_elevation(
    graph: nx.MultiDiGraph,
    deadband_m: float = ELEVATION_DEADBAND_DEFAULT_M,
) -> nx.MultiDiGraph:
    """Stage 6b: express the elevation deadband as a PROFILE transform.

    The deadband is a hysteresis floor that discards sub-`deadband_m` up/down
    reversals (DEM jitter) while preserving sustained climbs and descents.
    Crucially it reshapes the **vertices themselves**, so the same single
    profile feeds metric, solver, and display — unlike a sum-time-only deadband,
    which would change the box total without ever touching the plotted curve.
    Because it reshapes the geometry, stage 7 (`compute_edge_metrics`) stays a
    naive sum and takes no deadband parameter.

    Per edge: keep the vertices at the genuine turning points (a vertex commits
    once it sits `>= deadband_m` from the last committed reference), pin both
    endpoints (so the shared node elevation set by `graph_smooth_elevation` is
    preserved and joins stay consistent), and linearly interpolate between kept
    points. `deadband_m <= 0` is a no-op and the input graph is returned
    unchanged.

    Only the elevation component is touched; `(lat, lon)` pass through unchanged.
    Returns a new MultiDiGraph; the input is never mutated.
    """
    if deadband_m <= 0.0:
        return graph
    out: nx.MultiDiGraph = graph.copy()
    for _u, _v, _k, data in out.edges(data=True, keys=True):
        verts: list[tuple[float, float, float]] = data["vertices_resampled"]
        elevs = [vert[2] for vert in verts]
        flat = _deadband_profile(elevs, deadband_m)
        data["vertices_resampled"] = [
            (lat, lon, e) for (lat, lon, _orig), e in zip(verts, flat, strict=True)
        ]
    return out


def _deadband_profile(elevs: list[float], deadband_m: float) -> list[float]:
    """Flatten sub-`deadband_m` reversals out of an elevation series; endpoints pinned.

    Keeps the series at the "committed reference" turning points — a vertex is a
    turning point once it sits `>= deadband_m` from the last committed reference —
    and linearly interpolates between consecutive kept points. The first and last
    vertices are always kept (endpoint pinning), so a route's per-edge profiles
    glue continuously at shared nodes.
    """
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
    for a, b in zip(kept, kept[1:], strict=False):  # pairwise: kept[1:] is intentionally shorter
        span = b - a
        ea, eb = elevs[a], elevs[b]
        for j in range(a, b + 1):
            t = (j - a) / span if span else 0.0
            out[j] = ea + t * (eb - ea)
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
