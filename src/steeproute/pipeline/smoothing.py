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

from steeproute.pipeline._common import empty_like, per_edge_searchsorted

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

# Meters per degree of latitude (the equirectangular projection's lat scale).
_DEG_TO_M_LAT: float = _EARTH_RADIUS_M * math.radians(1.0)


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

    Vectorization (Story 14.2): coordinates for the whole graph are gathered in
    ONE `shapely.get_coordinates` call, the window-3 moving average is a handful
    of flat numpy array ops over every vertex at once (per-edge numpy dispatch
    was measured to *regress* on these light loops — the win comes from
    amortizing the overhead across the whole graph), and the smoothed geometries
    are rebuilt in ONE `shapely.linestrings` call. Degenerate edges are dropped;
    all nodes and edge iteration order are preserved. The output is numerically
    equivalent to the pre-14.2 per-vertex formulation to within floating-point
    reordering (the naive `(a+b+c)/3` mean replaces the compensated builtin
    `sum()`; measured max ~1.4e-14 deg on the fixture) — small enough that the
    regression goldens stay byte-identical, so no rebake was needed.
    """
    out = empty_like(graph)
    meta, coords, offs = _collect_linestrings(graph)
    if not meta:
        return out
    valid = _valid_edges_mask(coords, offs)
    # Window-3 moving average: each interior vertex → mean of its two neighbours
    # and itself; endpoints pinned. `a`/`c` are the left/right neighbour shifts;
    # the edge-boundary rows they cross are never read (masked out by `isint`).
    left = np.empty_like(coords)
    left[1:] = coords[:-1]
    right = np.empty_like(coords)
    right[:-1] = coords[1:]
    mean = (left + coords + right) / 3.0
    isint = np.ones(len(coords), dtype=bool)
    isint[offs[:-1]] = False
    isint[offs[1:] - 1] = False
    smoothed = coords.copy()
    smoothed[isint] = mean[isint]
    _build_from_flat(out, meta, smoothed, offs, valid)
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

    Vectorization (Story 14.2): the whole graph is resampled in flat numpy array
    ops — one `shapely.get_coordinates` gather, a per-edge equirectangular
    projection, `np.hypot` segment lengths + a per-edge-reset `np.cumsum` arc
    length, and one `np.searchsorted` locating every uniform sample's segment
    across all edges at once (a per-edge monotone offset keeps each search inside
    its own edge). Geometries are rebuilt in one `shapely.linestrings` call. This
    is numerically equivalent to the pre-14.2 per-vertex loop to within
    floating-point reordering (`np.hypot` ≠ CPython's `math.hypot` by up to a ULP,
    which could in principle nudge a sample across a segment boundary; measured
    max ~1.4e-14 deg on the fixture, zero boundary flips) — small enough that the
    regression goldens stay byte-identical, so no rebake was needed.
    """
    if not math.isfinite(spacing_m) or spacing_m <= 0:
        raise ValueError(f"spacing_m must be a positive finite number (got {spacing_m})")
    out = empty_like(graph)
    meta, coords, offs = _collect_linestrings(graph)
    if not meta:
        return out
    ne = len(meta)
    valid = _valid_edges_mask(coords, offs)
    idx = np.repeat(np.arange(ne, dtype=np.intp), np.diff(offs))  # per-vertex edge id
    counts = np.diff(offs).astype(np.float64)

    # Per-edge equirectangular projection at each edge's mean latitude.
    lons = coords[:, 0]
    lats = coords[:, 1]
    mean_lat = np.add.reduceat(lats, offs[:-1]) / counts  # (ne,)
    deg_to_m_lon = _DEG_TO_M_LAT * np.cos(np.radians(mean_lat))  # (ne,)
    xs = lons * deg_to_m_lon[idx]
    ys = lats * _DEG_TO_M_LAT

    # Per-edge-local cumulative arc length. Segment i (verts i, i+1) counts only
    # when both ends are the same edge; global cumsum minus each edge's start
    # value resets it per edge.
    within = idx[1:] == idx[:-1]
    seg_len = np.where(within, np.hypot(np.diff(xs), np.diff(ys)), 0.0)
    gcum = np.empty(len(coords), dtype=np.float64)
    gcum[0] = 0.0
    np.cumsum(seg_len, out=gcum[1:])
    cum = gcum - gcum[offs[:-1]][idx]  # per-edge-local
    total = cum[offs[1:] - 1]  # (ne,)

    n_intervals = np.maximum(1, np.round(np.divide(total, spacing_m)).astype(np.intp))  # (ne,)
    actual_spacing = np.where(total > 0.0, total / n_intervals, 0.0)  # (ne,)
    out_counts = n_intervals + 1  # first + (n_intervals-1) interior + last
    out_offs = np.zeros(ne + 1, dtype=np.intp)
    np.cumsum(out_counts, out=out_offs[1:])

    out_coords = np.empty((int(out_offs[-1]), 2), dtype=np.float64)
    out_coords[out_offs[:-1]] = coords[offs[:-1]]  # first vertex, exact
    out_coords[out_offs[1:] - 1] = coords[offs[1:] - 1]  # last vertex, exact

    n_interior = n_intervals - 1  # (ne,)
    if int(n_interior.sum()) > 0:
        samp_edge = np.repeat(np.arange(ne, dtype=np.intp), n_interior)  # (S,)
        samp_base = (np.cumsum(n_interior) - n_interior)[samp_edge]
        j = np.arange(len(samp_edge), dtype=np.intp) - samp_base + 1  # 1..n_interior[e]
        d = actual_spacing[samp_edge] * j
        # One global searchsorted that stays inside each edge (shared helper);
        # `- 1` turns "first vertex past d" into "the segment containing d".
        pos = per_edge_searchsorted(cum, idx, d, samp_edge, side="left") - 1
        pos = np.clip(pos, offs[:-1][samp_edge], offs[1:][samp_edge] - 2)
        seg = cum[pos + 1] - cum[pos]
        t = np.clip(np.where(seg > 0.0, (d - cum[pos]) / seg, 0.0), 0.0, 1.0)
        sx = xs[pos] + t * (xs[pos + 1] - xs[pos])
        sy = ys[pos] + t * (ys[pos + 1] - ys[pos])
        interior_pos = out_offs[:-1][samp_edge] + j
        out_coords[interior_pos, 0] = sx / deg_to_m_lon[samp_edge]
        out_coords[interior_pos, 1] = sy / _DEG_TO_M_LAT

    _build_from_flat(out, meta, out_coords, out_offs, valid)
    return out


def graph_smooth_elevation(
    graph: nx.MultiDiGraph,
    strength_m: float = ELEVATION_SMOOTHING_DEFAULT_M,
    *,
    inplace: bool = False,
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
    never mutated — unless `inplace=True`, an internal optimization for
    `operationalize_graph` (which owns a single working copy and threads the stage
    functions through it, avoiding one full-graph `copy()` per stage). The
    reads-then-writes structure below makes in-place safe: elevations are gathered
    into flat arrays first, then written back, so mutating the source is harmless.
    """
    window = strength_m / RESAMPLE_SPACING_M
    if window <= 1.0:
        return graph
    iters = max(1, round(window * window / 6.0))
    lam = _DIFFUSION_LAMBDA
    out: nx.MultiDiGraph = graph if inplace else graph.copy()

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
    *,
    inplace: bool = False,
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
    Returns a new MultiDiGraph; the input is never mutated — unless `inplace=True`
    (the `operationalize_graph` single-working-copy optimization; safe here because
    each edge's elevations are read into a local list before being written back).

    Not vectorized (Story 14.2): the transform is off by default
    (`ELEVATION_DEADBAND_DEFAULT_M == 0`, early-return), and even active its cost
    is the sequential hysteresis scan + the per-point `(lat, lon, elev)` tuple
    rebuild — neither vectorizable without the deferred array-edge contract (Q4).
    A flat-interp variant was measured *slower* (the flat machinery cost more than
    the minority interp it replaced), so this stays scalar and bit-identical.
    """
    if deadband_m <= 0.0:
        return graph
    out: nx.MultiDiGraph = graph if inplace else graph.copy()
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


def _collect_linestrings(
    graph: nx.MultiDiGraph,
) -> tuple[list[tuple[int, int, int, dict[str, object]]], np.ndarray, np.ndarray]:
    """Gather every edge's LineString coords into ONE flat array (Story 14.2 vectorization primitive).

    Returns `(meta, coords, offs)` where `meta[e] = (u, v, k, data)` in edge
    iteration order, `coords` is the `(V, 2)` float64 array of all vertices from
    a single `shapely.get_coordinates` call, and `offs` is the `(len(meta)+1,)`
    prefix-sum so edge `e`'s vertices are `coords[offs[e]:offs[e+1]]`.

    Raises `TypeError` on any non-LineString geometry (upstream contract
    violation, fail-fast — same message shape as the old per-edge `_extract_coords`).
    """
    meta: list[tuple[int, int, int, dict[str, object]]] = []
    geoms: list[shapely.LineString] = []
    for u, v, k, data in graph.edges(data=True, keys=True):
        geom = data.get("geometry")
        if not isinstance(geom, shapely.LineString):
            raise TypeError(
                "pipeline.smoothing: edge geometry must be a shapely.LineString, "
                f"got {type(geom).__name__}"
            )
        meta.append((u, v, k, data))
        geoms.append(geom)
    if not geoms:
        return meta, np.empty((0, 2), dtype=np.float64), np.zeros(1, dtype=np.intp)
    coords, idx = shapely.get_coordinates(geoms, return_index=True)
    coords = np.asarray(coords, dtype=np.float64)
    counts = np.bincount(idx, minlength=len(geoms))
    offs = np.zeros(len(geoms) + 1, dtype=np.intp)
    np.cumsum(counts, out=offs[1:])
    return meta, coords, offs


def _valid_edges_mask(coords: np.ndarray, offs: np.ndarray) -> np.ndarray:
    """Per-edge `is_valid_polyline` as a vectorized boolean mask.

    Valid iff the edge has >= 2 vertices, all finite, and not all identical to its
    first vertex — the flat-array equivalent of `is_valid_polyline`.
    """
    ne = len(offs) - 1
    counts = np.diff(offs)
    lons = coords[:, 0]
    lats = coords[:, 1]
    finite = np.isfinite(lons) & np.isfinite(lats)
    idx = np.repeat(np.arange(ne, dtype=np.intp), counts)
    first_lon = lons[offs[:-1]][idx]
    first_lat = lats[offs[:-1]][idx]
    distinct = (lons != first_lon) | (lats != first_lat)
    all_finite = np.logical_and.reduceat(finite, offs[:-1])
    any_distinct = np.logical_or.reduceat(distinct, offs[:-1])
    return (counts >= 2) & all_finite & any_distinct


def _build_from_flat(
    out: nx.MultiDiGraph,
    meta: list[tuple[int, int, int, dict[str, object]]],
    coords: np.ndarray,
    offs: np.ndarray,
    valid: np.ndarray,
) -> None:
    """Rebuild geometries from a flat coords array in ONE `shapely.linestrings` call.

    Adds each valid edge to `out` with its `data` shallow-copied and `geometry`
    replaced by the LineString of `coords[offs[e]:offs[e+1]]`. Invalid (degenerate)
    edges are skipped (dropped from the output). Edge order is preserved.
    """
    counts = np.diff(offs)
    if bool(valid.all()):
        # Common case — no degenerate edges dropped: feed the already-contiguous
        # flat array straight to shapely (no per-edge slice loop, no full-array
        # `np.concatenate` copy of every vertex).
        kept = meta
        line_idx = np.repeat(np.arange(len(meta), dtype=np.intp), counts)
        flat = coords
    else:
        kept = [entry for e, entry in enumerate(meta) if bool(valid[e])]
        if not kept:
            return
        slices = [coords[offs[e] : offs[e + 1]] for e in range(len(meta)) if bool(valid[e])]
        flat = np.concatenate(slices)
        line_idx = np.repeat(np.arange(len(kept), dtype=np.intp), [len(s) for s in slices])
    # `indices=` makes shapely return one LineString per group as an ndarray;
    # `np.asarray` gives it a subscriptable static type (the stub types the
    # scalar-input overload as a bare LineString).
    lines = np.asarray(shapely.linestrings(flat, indices=line_idx), dtype=object)
    for i, (u, v, k, data) in enumerate(kept):
        new_data = dict(data)
        new_data["geometry"] = lines[i]
        out.add_edge(u, v, key=k, **new_data)


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
