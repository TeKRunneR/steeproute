# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations surface as Unknown; same external-boundary pattern
# as pipeline/osm.py, pipeline/smoothing.py, pipeline/dem.py.
"""Pipeline stages 7 + 8: per-edge metrics and climb detection.

Stage 7 (`compute_edge_metrics`) reads each edge's
`vertices_resampled: list[tuple[lat, lon, elevation_m]]` (set by stage 5,
smoothed by stage 6) and attaches four numeric metrics:

- `length_m` — cumulative 2D ground-distance along the polyline. Computed from
  the `(lat, lon)` components using the same local-equirectangular projection
  as `pipeline.smoothing._resample_meters` (cos-of-mean-latitude correction),
  so distances are in real meters with sub-‰ drift over edge-scale lengths.
- `d_plus_m` — sum of strictly positive elevation deltas between consecutive
  vertices. Always ≥ 0.
- `d_minus_m` — sum of the absolute values of strictly negative elevation
  deltas. Always ≥ 0 (positive magnitude, matching the manifest sample in
  Architecture §Cat 4 where `"d_plus_m"` and `"d_minus_m"` are both positive).
- `avg_gradient` — `(d_plus_m + d_minus_m) / length_m`. Total absolute altitude
  change per horizontal meter; dimensionless and ≥ 0. Not a signed slope.
- `max_windowed_descent_grad` — steepest sustained *descending* grade over a fixed
  distance window (`_DESCENT_WINDOW_M`), measured as the net elevation *drop* / run
  between window endpoints, in this directed edge's own traversal direction (a
  window that nets a climb contributes nothing). Because the reciprocal edge carries
  reversed `vertices_resampled`, it records the descent grade for the *opposite*
  direction — so the value is direction-aware, mirroring `d_plus_m`/`d_minus_m`.
  Governs the opt-in FR32 descent cap (Story 10.2), compared against
  `--max-descent-slope` only when that flag is set. Parameter-independent of all
  `SolverParams`.

Stage 8 (`detect_climbs`) walks the post-stage-7 MultiDiGraph and emits the
maximal edge-disjoint contiguous edge-sequences whose cumulative directional
uphill slope (`d_plus_sum / length_sum`) stays ≥ `min_climb_slope` and whose
total ground length is ≥ `min_climb_ground_length`. Each candidate is grown
from its seed in *both* directions — backward to the true steep bottom, then
forward to the top — so the emitted climb is genuinely maximal regardless of
node-id labeling or seed order (Story 9.1, review finding #7). Output is a
`list[Climb]`; the input graph is never mutated. Stage 9 (graph contraction,
Story 3.3) consumes the output to build the solver-side `ContractedGraph`.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

import networkx as nx

from steeproute.models import Climb, Edge

# WGS84 equatorial radius for the local equirectangular projection. Same value
# and rationale as `pipeline.smoothing._EARTH_RADIUS_M` — duplicated rather than
# imported so each pipeline module is self-contained against a physical constant.
_EARTH_RADIUS_M: float = 6_378_137.0

# Minimum 2D ground length (m) for a polyline to count as a valid metrics input.
# A real stage-4 edge is metres long; this 1 µm floor is purely a numeric guard
# that excludes sub-physical polylines whose coordinate spacing is so small the
# 2D length underflows toward zero and `avg_gradient` would overflow to inf. Far
# below any real edge, so it never rejects production data.
_MIN_METRIC_LENGTH_M: float = 1e-6

# Distance window (m) for the FR32 windowed descent metric (Story 10.2). The
# steepest *sustained* grade is measured over a sliding window of this ground
# length, so a short cliff section is caught even when the whole-edge
# `avg_gradient` averages it away. A module-scope named constant (not a CLI flag)
# keeps `max_windowed_descent_grad` parameter-independent — the Architecture §3c
# contract and FR29 determinism both rely on the metric being a pure function of
# the cached geometry. PROVISIONAL: ~30 m spans ~3 resampled segments (≈10 m
# spacing); a future story may surface it as `--descent-window` if needed (PRD
# FR32 leaves the door open).
_DESCENT_WINDOW_M: float = 30.0


def compute_edge_metrics(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Stage 7: attach `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `max_windowed_descent_grad` per edge.

    Args:
        graph: input MultiDiGraph; every edge must carry `vertices_resampled`
            from stages 5-6 with ≥ 2 entries of finite `(lat, lon, elevation_m)`.

    Returns:
        A new MultiDiGraph; the input is never mutated. Upstream attributes
        (`geometry`, `vertices_resampled`, `sac_scale`, `highway`, `osm_way_id`)
        are carried through unchanged.
    """
    out: nx.MultiDiGraph = graph.copy()
    for _u, _v, _k, data in out.edges(data=True, keys=True):
        verts: list[tuple[float, float, float]] = data["vertices_resampled"]
        # One projected-distance pass per edge: `length_m` is the last cumulative
        # value, and the windowed-descent scan reuses the same array (the two used
        # to recompute the identical equirectangular projection independently).
        cum_dist = _cumulative_2d_distances(verts)
        length_m = cum_dist[-1] if cum_dist else 0.0
        d_plus_m, d_minus_m = _elevation_gain_loss(verts)
        # length_m > 0 is guaranteed by stage 4's degenerate-edge drop; we
        # express the contract as a postcondition rather than as defensive
        # guards inside the loop.
        avg_gradient = (d_plus_m + d_minus_m) / length_m
        data["length_m"] = length_m
        data["d_plus_m"] = d_plus_m
        data["d_minus_m"] = d_minus_m
        data["avg_gradient"] = avg_gradient
        data["max_windowed_descent_grad"] = _max_windowed_descent_grad(verts, cum_dist)
    return out


def is_valid_for_metrics(verts: list[tuple[float, float, float]]) -> bool:
    """True if `verts` is a valid stage-7 input (≥ 1 non-degenerate consecutive pair, all finite).

    Public so the hypothesis property test in `tests/unit/test_climbs.py` can
    use the same validity check as production via `hypothesis.assume`, avoiding
    drift between the strategy filter and the actual stage's input contract.

    Validity here means "the polyline has a real positive 2D length"
    (`>= _MIN_METRIC_LENGTH_M`), which is what stage 4 enforces upstream by
    dropping degenerate edges. We check the *actual* projected length rather than
    a structural "some consecutive `(lat, lon)` pair differs" proxy: a denormal
    coordinate difference (e.g. lon `2.2e-313`) compares unequal yet projects to a
    sub-zero-underflow distance, so the proxy would call it valid while
    `compute_edge_metrics` divided by ~0 and produced an infinite `avg_gradient`.
    Measuring the length directly is the precondition that stage actually needs.
    """
    if len(verts) < 2:
        return False
    if not all(
        math.isfinite(lat) and math.isfinite(lon) and math.isfinite(elev)
        for lat, lon, elev in verts
    ):
        return False
    return _cumulative_2d_distance_m(verts) >= _MIN_METRIC_LENGTH_M


def _cumulative_2d_distances(verts: list[tuple[float, float, float]]) -> list[float]:
    """Prefix-sum of ground-distance in meters: `out[i]` = distance from `verts[0]` to `verts[i]`.

    Local equirectangular projection at the polyline's mean latitude — the same
    pattern as `pipeline.smoothing._resample_meters`. Accurate to ~0.1% over
    edge-scale distances; no external projection dependency. The single source of
    projected distance for stage 7: both whole-edge `length_m` (the last element)
    and `_max_windowed_descent_grad` read this one array, so the projection is
    computed once per edge and the two cannot drift.
    """
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


def _cumulative_2d_distance_m(verts: list[tuple[float, float, float]]) -> float:
    """Total ground-distance in meters along the `(lat, lon)` polyline (the prefix sum's last term)."""
    cum = _cumulative_2d_distances(verts)
    return cum[-1] if cum else 0.0


def _max_windowed_descent_grad(
    verts: list[tuple[float, float, float]], cum_dist: list[float]
) -> float:
    """Steepest sustained *descending* grade over a `_DESCENT_WINDOW_M` window — the FR32 metric.

    Directional property of this directed edge (Story 10.2): the maximum, over every
    sliding window of ≥ `_DESCENT_WINDOW_M` ground-meters, of the net elevation
    *drop* / horizontal_run between the window endpoints, walking `verts` in stored
    order. A window that nets a *climb* contributes nothing (`drop <= 0` → grade
    `0.0`): we never want to forbid a traversal because a sub-section *ascends*
    steeply — only because it *descends* steeply. The reciprocal edge carries
    reversed `vertices_resampled`, so it records the descent grade for the opposite
    direction (`d_plus_m`/`d_minus_m` are oriented the same way). The window catches
    a short steep descent a whole-edge `avg_gradient` would average away; a window
    that drops then climbs back nets a gentle grade *by design* — the cap targets
    *sustained* descents, not transient pitches.

    Windows shorter than `_DESCENT_WINDOW_M` are ignored to suppress single-vertex
    noise; an edge whose *entire* polyline is shorter than the window falls back to
    its end-to-end descent grade so a short steep descending connector is still
    measured. Returns `0.0` for a degenerate (< 2 vertex) or zero-length polyline,
    or one that never descends. `cum_dist` is the prefix-sum from
    `_cumulative_2d_distances` (same projection, computed once per edge); no RNG.
    """
    n = len(verts)
    if n < 2:
        return 0.0

    best = 0.0
    saw_full_window = False
    j = 0
    for i in range(n):
        if j < i:
            j = i
        while j < n - 1 and cum_dist[j] - cum_dist[i] < _DESCENT_WINDOW_M:
            j += 1
        run = cum_dist[j] - cum_dist[i]
        if run >= _DESCENT_WINDOW_M:
            saw_full_window = True
            drop = verts[i][2] - verts[j][2]  # > 0 iff this window descends i → j
            if drop > 0.0:
                grad = drop / run
                if grad > best:
                    best = grad
    if not saw_full_window:
        # Whole polyline is shorter than one window: measure its end-to-end descent.
        total_run = cum_dist[n - 1]
        drop = verts[0][2] - verts[n - 1][2]
        if total_run > 0.0 and drop > 0.0:
            best = drop / total_run
    return best


def detect_climbs(
    graph: nx.MultiDiGraph,
    min_climb_slope: float,
    min_climb_ground_length: float,
) -> list[Climb]:
    """Stage 8: emit edge-disjoint contiguous edge-sequences that qualify as climbs.

    Walks `graph` and returns the maximal directed edge-sequences whose
    cumulative directional uphill slope (`d_plus_sum / length_sum`) stays
    `≥ min_climb_slope` along the chain and whose total `length_m` is
    `≥ min_climb_ground_length`. Each underlying graph edge appears in at most
    one returned `Climb` — Story 3.3's back-mapping injectivity depends on it.

    Maximality (Story 9.1, review finding #7): each candidate is grown from its
    seed edge in both directions — first *backward* (prepending the steepest
    qualifying-as-seed incoming edge until none remains) to root the climb at
    its true steep bottom, then *forward* to its top. Because every backward
    edge is itself `≥ min_climb_slope`, every prefix measured from the new
    bottom stays `≥ min_climb_slope`, so the running-average constraint is
    preserved without a recheck. This makes the output independent of node-id
    labeling and seed order: previously a mid-chain edge seeding before the
    chain's bottom would extend forward only and orphan the (steep) bottom edge,
    silently demoting a real climb-start to a connector.

    Args:
        graph: post-stage-7 MultiDiGraph; every edge must carry the stage-7
            attribute contract (`length_m`, `d_plus_m`, `d_minus_m`,
            `avg_gradient`) plus `sac_scale` (may be `None`). Never mutated.
        min_climb_slope: climb-detection slope threshold — the minimum
            running-average uphill slope (`d_plus/length`) a segment must keep
            to qualify as a climb (dimensionless gradient, e.g. 0.20 for 20 %).
            Distinct from the route-level floor `SolverParams.theta` (FR3 vs FR3b).
        min_climb_ground_length: minimum cumulative 2D ground length (m) for a
            candidate climb to be emitted.

    Returns:
        `list[Climb]` in the order each climb's seed edge is encountered when
        iterating `sorted(graph.edges(keys=True))`. Each `Climb`'s aggregate
        `length_m` / `d_plus_m` / `avg_slope` equals the sum / sum / ratio of
        its underlying edges' metrics within floating-point tolerance.

    Branching policy: at a junction with multiple unconsumed outgoing edges,
    extend with the steepest (highest per-edge `d_plus_m / length_m`) edge
    whose addition keeps the cumulative running-average slope `≥ min_climb_slope` AND
    whose target node has not yet been visited by the candidate (node-monotone
    walk — prevents zigzag climbs that traverse the same node pair through
    bidirectional / parallel edges). Ties on slope break on the outgoing
    edge's `(node_v, key)` order so the choice is deterministic (FR29
    byte-identical reproducibility). The function uses no RNG.
    """
    # Snapshot every edge's attribute dict into a `(u, v, k) -> data` lookup
    # table once, up-front. Avoids repeated `graph[u][v][k]` indexed access
    # (which basedpyright reads as `__getitem__(key: str)` against networkx's
    # partial stubs) and gives a clean Pythonic-typed surface for the inner
    # loops. The dict values are aliases of the live edge-data dicts — we
    # never mutate them, so the purity contract holds.
    edge_data: dict[tuple[int, int, int], dict[str, Any]] = {
        (u, v, k): data for u, v, k, data in graph.edges(data=True, keys=True)
    }
    consumed: set[tuple[int, int, int]] = set()
    climbs: list[Climb] = []

    for seed in sorted(edge_data.keys()):
        if seed in consumed:
            continue
        seed_data = edge_data[seed]
        if not _qualifies_as_seed(seed_data, min_climb_slope):
            continue

        u, v, _k = seed
        # The candidate is grown bottom-first: backward extensions are prepended
        # (appendleft) so the final tuple runs bottom → top regardless of where
        # the seed fell in the chain.
        candidate: deque[tuple[int, int, int]] = deque([seed])
        # Parallel `set` shadow of `candidate` for O(1) membership checks in
        # the extension pickers; without it, `edge_id in candidate` is O(n)
        # per scanned edge, giving worst-case O(E² · avg_degree).
        candidate_set: set[tuple[int, int, int]] = {seed}
        # Node-monotonicity guard: a candidate climb is a path, not a walk —
        # consumers (Story 3.3 super-edge back-mapping, Story 3.6 solver
        # route construction) treat each climb as a monotone uphill segment
        # between two distinct endpoints. Visiting the same node twice would
        # admit zigzag tuples through bidirectional / parallel edges on
        # saddle-shaped terrain.
        visited_nodes: set[int] = {u, v}
        cum_d_plus: float = seed_data["d_plus_m"]
        cum_length: float = seed_data["length_m"]

        # Backward phase: walk to the true bottom by prepending the steepest
        # qualifying-as-seed incoming edge. Each prepended edge is itself
        # `≥ min_climb_slope`, so every prefix measured from the new bottom
        # stays `≥ min_climb_slope` (weighted average of two `≥ θ` quantities)
        # — no running-average recheck is needed here.
        bottom: int = u
        while True:
            back = _pick_steepest_backward(
                graph,
                edge_data,
                bottom,
                min_climb_slope,
                consumed,
                candidate_set,
                visited_nodes,
            )
            if back is None:
                break
            a, _b, _kk = back
            bd = edge_data[back]
            cum_d_plus += bd["d_plus_m"]
            cum_length += bd["length_m"]
            bottom = a
            candidate.appendleft(back)
            candidate_set.add(back)
            visited_nodes.add(a)

        # Forward phase: greedy-steepest continuation keeping the cumulative
        # running-average `≥ min_climb_slope`. The cumulative already includes
        # any backward edges, so the forward semantics are unchanged.
        head: int = v
        while True:
            extension = _pick_steepest_extension(
                graph,
                edge_data,
                head,
                min_climb_slope,
                cum_d_plus,
                cum_length,
                consumed,
                candidate_set,
                visited_nodes,
            )
            if extension is None:
                break
            _a, b, _kk = extension
            ed = edge_data[extension]
            cum_d_plus += ed["d_plus_m"]
            cum_length += ed["length_m"]
            head = b
            candidate.append(extension)
            candidate_set.add(extension)
            visited_nodes.add(b)

        if cum_length >= min_climb_ground_length:
            edges = tuple(
                _edge_from_graph_data(a, b, kk, edge_data[(a, b, kk)]) for (a, b, kk) in candidate
            )
            climbs.append(
                Climb(
                    edges=edges,
                    length_m=cum_length,
                    d_plus_m=cum_d_plus,
                    avg_slope=cum_d_plus / cum_length,
                )
            )
            consumed.update(candidate)

    return climbs


def _qualifies_as_seed(data: dict[str, Any], min_climb_slope: float) -> bool:
    """True if a directed edge's per-edge uphill slope is `≥ min_climb_slope`.

    Uses the directional metric `d_plus_m / length_m` — *not* the absolute
    stage-7 `avg_gradient` (which sums uphill + downhill churn). A descending
    edge has `d_plus_m == 0` and never qualifies. `length_m > 0` is a
    postcondition of stage 7 (same contract `compute_edge_metrics` relies on);
    we don't re-guard it here.
    """
    return data["d_plus_m"] / data["length_m"] >= min_climb_slope


def _pick_steepest_extension(
    graph: nx.MultiDiGraph,
    edge_data: dict[tuple[int, int, int], dict[str, Any]],
    head: int,
    min_climb_slope: float,
    cum_d_plus: float,
    cum_length: float,
    consumed: set[tuple[int, int, int]],
    candidate_set: set[tuple[int, int, int]],
    visited_nodes: set[int],
) -> tuple[int, int, int] | None:
    """Steepest qualifying outgoing edge from `head` keeping cum-slope `≥ min_climb_slope`.

    Returns `None` when no qualifying continuation exists (closes the candidate
    climb at the previous edge). An edge is qualifying iff it is unconsumed,
    not already in the candidate, its target node is not already in the
    candidate's path (node-monotonicity), and adding it keeps the cumulative
    running-average slope `≥ min_climb_slope`. Deterministic tie-break on
    `(node_v, key)` via the sorted iteration order.
    """
    best: tuple[int, int, int] | None = None
    best_slope: float = -math.inf
    for out_edge in sorted(graph.out_edges(head, keys=True)):
        a, b, kk = out_edge
        edge_id: tuple[int, int, int] = (a, b, kk)
        if edge_id in consumed or edge_id in candidate_set:
            continue
        if b in visited_nodes:
            continue
        ed = edge_data[edge_id]
        length: float = ed["length_m"]
        new_avg = (cum_d_plus + ed["d_plus_m"]) / (cum_length + length)
        if new_avg < min_climb_slope:
            continue
        slope: float = ed["d_plus_m"] / length
        if slope > best_slope:
            best_slope = slope
            best = edge_id
    return best


def _pick_steepest_backward(
    graph: nx.MultiDiGraph,
    edge_data: dict[tuple[int, int, int], dict[str, Any]],
    bottom: int,
    min_climb_slope: float,
    consumed: set[tuple[int, int, int]],
    candidate_set: set[tuple[int, int, int]],
    visited_nodes: set[int],
) -> tuple[int, int, int] | None:
    """Steepest qualifying-as-seed incoming edge to `bottom`, or `None`.

    Used to root a climb at its true steep bottom (Story 9.1). An edge
    `(a, bottom, k)` is a valid backward extension iff it is unconsumed, not
    already in the candidate, its source node `a` is not already on the
    candidate's path (node-monotonicity), and it qualifies as a seed on its own
    (`d_plus_m / length_m ≥ min_climb_slope`). Requiring per-edge qualification
    — rather than a running-average check — keeps every prefix from the new
    bottom `≥ min_climb_slope` (a climb only ever *starts* at a steep edge,
    mirroring `_qualifies_as_seed`). Deterministic tie-break on `(node_u, key)`
    via the sorted iteration order (FR29).
    """
    best: tuple[int, int, int] | None = None
    best_slope: float = -math.inf
    for in_edge in sorted(graph.in_edges(bottom, keys=True)):
        a, b, kk = in_edge
        edge_id: tuple[int, int, int] = (a, b, kk)
        if edge_id in consumed or edge_id in candidate_set:
            continue
        if a in visited_nodes:
            continue
        ed = edge_data[edge_id]
        if not _qualifies_as_seed(ed, min_climb_slope):
            continue
        slope: float = ed["d_plus_m"] / ed["length_m"]
        if slope > best_slope:
            best_slope = slope
            best = edge_id
    return best


def _edge_from_graph_data(u: int, v: int, k: int, data: dict[str, Any]) -> Edge:
    """Project a `MultiDiGraph` edge-data dict into the `Edge` value-type.

    Reads the stage-7 attribute contract verbatim. `sac_scale` falls back to
    `None` when absent (test fixtures may legitimately omit it; production
    `pipeline.osm.normalize_edges` always sets it, sometimes to `None`).
    """
    return Edge(
        node_u=u,
        node_v=v,
        key=k,
        length_m=data["length_m"],
        d_plus_m=data["d_plus_m"],
        d_minus_m=data["d_minus_m"],
        avg_gradient=data["avg_gradient"],
        sac_scale=data.get("sac_scale"),
    )


def _elevation_gain_loss(verts: list[tuple[float, float, float]]) -> tuple[float, float]:
    """Return `(d_plus_m, d_minus_m)` from consecutive elevation deltas.

    `d_plus_m` is the sum of strictly positive deltas. `d_minus_m` is the sum
    of the absolute values of strictly negative deltas — positive magnitude,
    not a signed quantity.
    """
    d_plus = 0.0
    d_minus = 0.0
    for i in range(1, len(verts)):
        delta = verts[i][2] - verts[i - 1][2]
        if delta > 0:
            d_plus += delta
        elif delta < 0:
            d_minus += -delta
    return d_plus, d_minus
