# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations surface as Unknown; same external-boundary pattern
# as pipeline/osm.py, pipeline/smoothing.py, pipeline/dem.py, pipeline/climbs.py.
"""Pipeline stage 9: contracted climb-graph construction.

`contract_climbs(base_graph, climbs, l_connector) -> ContractedGraph` folds each
`Climb` from stage 8 into a single directed super-edge in a new `MultiDiGraph`,
carries forward **all** non-climb connector edges unchanged (no length-based
drop), and returns the contracted graph plus a
`(node_u, node_v, key) -> tuple[Edge, ...]` back-mapping so the validator
(Story 3.9) can expand super-edges back to base edges for per-base-edge
constraint checks.

Super-edges carry the same numeric attribute schema as base edges (`length_m`,
`d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`), with `length_m` /
`d_plus_m` / `d_minus_m` summed across the underlying base edges and
`avg_gradient = (d_plus_m + d_minus_m) / length_m` (stage 7's absolute-churn
definition). `sac_scale` aggregates as the maximum SAC rank across the climb's
edges per `pipeline.osm.SAC_SCALE_RANK`, with `None` entries treated as
below-`hiking` (so they never raise the aggregate). `geometry` and
`vertices_resampled` stay on the base edges — consumers reach them via
`super_edge_to_base` when they need geometry, never off the super-edge.

**Undirected base-segment reuse tagging (Story 5.1, FR5).** Every contracted
edge additionally carries two attributes the solver / oracle / validator
(Story 5.2) use to enforce once-per-route reuse on the underlying *physical*
trail segment, regardless of direction:

- `base_segment_id` — a `frozenset[tuple[int, int, int]]` of undirected
  base-segment identities. The identity of one base edge is its canonical
  sorted node-pair plus key (`_base_segment_id`), so a forward edge `(u, v, k)`
  and its reverse `(v, u, k)` resolve to the *same* id. A **connector** carries
  a one-element set (its own id); a **super-edge** carries the set of ids of
  the base edges it contracts. Stored as a set uniformly (not a scalar on
  connectors) so Story 5.2 can test "any non-exempt id already used?" without
  branching on edge kind. A climb super-edge therefore shares an id with the
  reverse-direction connectors of the same trail — that collision is what kills
  the degenerate out-and-back.
- `reusable` — `True` only for a connector with `length_m < l_connector` (a
  short linking segment, exempt from the once-only rule and traversable both
  ways). `False` for long connectors and for every super-edge. This repurposes
  `--l-connector` from the old graph-pruning threshold into a reuse-exemption
  threshold (the originally-intended FR5 semantics).

A super-edge is identified by dict-membership in `super_edge_to_base`:
`(u, v, k) in contracted.super_edge_to_base` iff that triple denotes a
super-edge; everything else in `contracted.graph` is a connector edge that
carried over verbatim.

Input `base_graph` is never mutated (purity contract — mirrors
`pipeline.climbs.detect_climbs` and `pipeline.climbs.compute_edge_metrics`).
"""

from __future__ import annotations

import networkx as nx

from steeproute.models import Climb, ContractedGraph, Edge
from steeproute.pipeline.osm import SAC_SCALE_RANK, max_sac_rank

# Inverse of `SAC_SCALE_RANK` for aggregating super-edge `sac_scale`: given the
# maximum rank across a climb's edges, look up the canonical SAC name string.
# Computed once at module load; SAC_SCALE_RANK is closed (6 named ranks).
_SAC_RANK_TO_NAME: dict[int, str] = {rank: name for name, rank in SAC_SCALE_RANK.items()}


def contract_climbs(
    base_graph: nx.MultiDiGraph,
    climbs: list[Climb],
    l_connector: float,
    split_at_junctions: bool = True,
) -> ContractedGraph:
    """Stage 9: build the solver-side `ContractedGraph` from stage-8 climbs.

    For each `Climb`, emit one directed super-edge per contiguous sub-segment
    (see `split_at_junctions`) carrying summed metrics. **All** non-climb edges
    are carried over from `base_graph` unchanged (entire edge-data dict,
    including `geometry`, `vertices_resampled`, `highway`, `osm_way_id`) — no
    length-based drop. Every contracted edge is additionally tagged with a
    `base_segment_id` (undirected, see module docstring) and a `reusable` flag.

    **Junction-aware splitting (Story 6.1, FR10).** A climb is collapsed into
    one atomic super-edge from `climb.edges[0].node_u` to `climb.edges[-1].
    node_v`, deleting its interior nodes. That makes a trail joining the climb
    *partway up* (at a real interior junction) unable to board it — the solver
    can only enter/leave at the two endpoints. With `split_at_junctions=True`
    (default), the climb is split at every interior node that is a real trail
    junction — incident, in `base_graph`, to a base segment whose undirected
    identity is *outside* the climb's own segment set (the climb's own
    reverse-direction edges share ids, so they never trigger a split). Each
    resulting sub-segment becomes its own super-edge and the junction node
    survives as a real node a connector can board at. Single-edge climbs and
    junction-free climbs are unaffected. `split_at_junctions=False` reproduces
    the pre-fix atomic-climb behaviour (diagnostics only).

    Args:
        base_graph: post-stage-7 `MultiDiGraph` carrying the
            `length_m`/`d_plus_m`/`d_minus_m`/`avg_gradient`/`sac_scale`
            edge-attribute contract. Never mutated.
        climbs: stage-8 output (`pipeline.climbs.detect_climbs`); each climb's
            `edges` tuple must correspond to a contiguous edge-disjoint
            sequence in `base_graph`.
        l_connector: short-connector reuse-exemption threshold in meters. A
            carried-over connector is tagged `reusable=True` iff
            `length_m < l_connector` (strict). No edge is dropped on this
            threshold any longer.
        split_at_junctions: when `True` (default), split each climb at its
            interior trail junctions (one super-edge per sub-segment); when
            `False`, emit one atomic super-edge per climb.

    Returns:
        `ContractedGraph` whose `graph` is a fresh `MultiDiGraph` (climb
        sub-segments as super-edges + all connectors, each tagged with
        `base_segment_id` + `reusable`) and whose `super_edge_to_base` maps each
        super-edge's `(node_u, node_v, key)` triple to the underlying
        `tuple[Edge, ...]` it contracts.

    Super-edge key allocation: when a sub-segment's `(u, v)` already has parallel
    edges in the contracted graph (from a surviving connector or from another
    sub-segment ending on the same endpoints), the new super-edge gets the
    smallest non-conflicting `key` — `max existing key for (u, v) in contracted`
    + 1, or 0 if no existing edge. Order: connectors added first, then climbs in
    input order, so connector keys land first and super-edges layer on top.
    """
    contracted: nx.MultiDiGraph = nx.MultiDiGraph()

    # 1. Build the set of base-edge identities consumed by climbs, then carry
    #    over EVERY non-climb connector unchanged (no length-based drop — short
    #    connectors are revived as reuse-exempt linking segments, Story 5.1).
    #    Connectors land first so super-edge key allocation in step 2 sees them
    #    and picks non-colliding keys.
    climb_edge_ids: set[tuple[int, int, int]] = set()
    for climb in climbs:
        for e in climb.edges:
            climb_edge_ids.add((e.node_u, e.node_v, e.key))

    for u, v, k, data in base_graph.edges(data=True, keys=True):
        if (u, v, k) in climb_edge_ids:
            continue
        # `**data` unpacking creates a fresh outer attribute dict for the
        # contracted edge — but mutable values inside (`vertices_resampled`
        # list, `geometry` LineString, list-valued `highway` / `osm_way_id`)
        # remain aliased to `base_graph`'s. `contract_climbs` itself never
        # mutates any of these, so the purity contract holds on the call.
        # `base_segment_id` / `reusable` are NEW keys written onto the fresh
        # contracted dict only — never back onto `base_graph`'s.
        # Downstream consumers reading the contracted graph must treat edge
        # data as read-only — same convention as `pipeline.climbs`.
        contracted.add_edge(
            u,
            v,
            key=k,
            **data,
            base_segment_id=frozenset({_base_segment_id(u, v, k)}),
            reusable=data["length_m"] < l_connector,
        )

    # 2. Emit one super-edge per climb sub-segment (junction-split, Story 6.1).
    #    Each super-edge carries the aggregated metrics + the max-rank SAC;
    #    geometry / vertices stay on base edges reachable through
    #    `super_edge_to_base`. Its `base_segment_id` is the set of undirected ids
    #    of the base edges it contracts (so it collides with the reverse-direction
    #    connectors of the same trail); never reusable.
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {}
    for climb in climbs:
        for seg_edges in _split_climb_at_junctions(base_graph, climb.edges, split_at_junctions):
            u_super: int = seg_edges[0].node_u
            v_super: int = seg_edges[-1].node_v
            k_super: int = _next_key_for(contracted, u_super, v_super)
            length_m: float = sum(e.length_m for e in seg_edges)
            d_plus_m: float = sum(e.d_plus_m for e in seg_edges)
            d_minus_m: float = sum(e.d_minus_m for e in seg_edges)
            # Guard against a zero-length sub-segment (a junction-isolated run of
            # degenerate zero-length base edges) — mirrors `models.route_avg_gradient`'s
            # `length_m > 0 else 0.0` convention. Splitting newly exposes this: the
            # atomic-climb path summed over the whole (min-length-guaranteed) climb.
            avg_gradient: float = (d_plus_m + d_minus_m) / length_m if length_m > 0 else 0.0
            sac_scale: str | None = _aggregate_sac_scale(seg_edges)
            base_segment_id: frozenset[tuple[int, int, int]] = frozenset(
                _base_segment_id(e.node_u, e.node_v, e.key) for e in seg_edges
            )
            contracted.add_edge(
                u_super,
                v_super,
                key=k_super,
                length_m=length_m,
                d_plus_m=d_plus_m,
                d_minus_m=d_minus_m,
                avg_gradient=avg_gradient,
                sac_scale=sac_scale,
                base_segment_id=base_segment_id,
                reusable=False,
            )
            super_edge_to_base[(u_super, v_super, k_super)] = seg_edges

    # No orphan-prune step: with every connector retained, the only nodes in
    # `contracted` are endpoints of added edges (super-edge or connector), so
    # none can have degree 0. Climb-internal nodes are absorbed into the
    # super-edge and simply never added unless a surviving connector touches
    # them.
    return ContractedGraph(graph=contracted, super_edge_to_base=super_edge_to_base)


def _split_climb_at_junctions(
    base_graph: nx.MultiDiGraph,
    climb_edges: tuple[Edge, ...],
    split_at_junctions: bool,
) -> list[tuple[Edge, ...]]:
    """Partition a climb's ordered edges into sub-segments at interior junctions.

    Returns the climb whole (one element) when `split_at_junctions` is `False`
    or the climb has fewer than two edges (no interior node to split at).
    Otherwise cuts the edge sequence at every interior boundary node that
    `_is_junction` flags — a node incident to a base segment outside the climb —
    so a trail joining mid-climb can board at the junction. Each returned tuple
    is a contiguous, non-empty edge run; concatenated in order they reproduce
    `climb_edges`, so the split is a pure partition (back-mapping stays
    injective). Reads `base_graph` only — never mutates it.
    """
    if not split_at_junctions or len(climb_edges) < 2:
        return [tuple(climb_edges)]
    climb_segment_ids: frozenset[tuple[int, int, int]] = frozenset(
        _base_segment_id(e.node_u, e.node_v, e.key) for e in climb_edges
    )
    segments: list[tuple[Edge, ...]] = []
    current: list[Edge] = [climb_edges[0]]
    for prev_edge, next_edge in zip(climb_edges, climb_edges[1:], strict=False):
        # The boundary node between two consecutive climb edges is an interior
        # node; cut there iff it is a real trail junction.
        if _is_junction(base_graph, prev_edge.node_v, climb_segment_ids):
            segments.append(tuple(current))
            current = []
        current.append(next_edge)
    segments.append(tuple(current))
    return segments


def _is_junction(
    base_graph: nx.MultiDiGraph,
    node: int,
    climb_segment_ids: frozenset[tuple[int, int, int]],
) -> bool:
    """`True` iff `node` is incident (in `base_graph`) to a segment outside the climb.

    A real trail junction: some base edge touching `node` — in either direction —
    has an undirected `_base_segment_id` not among the climb's own segment ids.
    The climb's own reverse-direction edges canonicalize to ids already in
    `climb_segment_ids`, so a bidirectional trail does not split itself at every
    interior node — only a genuinely different trail (or a parallel way with a
    different key) triggers a split.
    """
    for u, v, k in base_graph.out_edges(node, keys=True):
        if _base_segment_id(u, v, k) not in climb_segment_ids:
            return True
    for u, v, k in base_graph.in_edges(node, keys=True):
        if _base_segment_id(u, v, k) not in climb_segment_ids:
            return True
    return False


def _base_segment_id(u: int, v: int, k: int) -> tuple[int, int, int]:
    """Undirected base-segment identity: canonical sorted node-pair + key.

    A forward edge `(u, v, k)` and its reverse-direction counterpart `(v, u, k)`
    resolve to the same tuple, so once-only reuse keyed on this id forbids
    re-walking a physical trail segment in either direction (Story 5.1, FR5).
    The natural extension of the existing canonical `(node_u, node_v, key)` edge
    identity documented on `models.Edge`.

    The `key` is preserved as-is: osmnx assigns the same key (0 for a simple
    two-way edge) to both directions, so the reverse edge collides as intended.
    Parallel ways with mismatched keys are vanishingly rare on trail data and
    would at worst under-merge (treat two genuinely-distinct parallel ways as
    distinct), never over-merge unrelated segments.
    """
    return (u, v, k) if u <= v else (v, u, k)


def _next_key_for(contracted: nx.MultiDiGraph, u: int, v: int) -> int:
    """Collision-free `key` for a new edge from `u` to `v` in `contracted`.

    Returns `max(existing keys for (u, v) in contracted) + 1`, or `0` if no
    edge between `(u, v)` exists yet. This is the first key strictly above
    the existing max — NOT the first gap below it (e.g. existing keys
    `[0, 2]` → returns `3`, not `1`). The skip is intentional: scanning for
    gaps would complicate the downstream canonical edge-ordering on
    `(node_u, node_v, key)` without buying anything (networkx accepts
    arbitrary int keys, and key continuity isn't a contract).
    """
    if not contracted.has_edge(u, v):
        return 0
    # `contracted[u][v]` would trip basedpyright (partial stubs declare
    # `__getitem__(key: str)` on the AtlasView). `out_edges(u, keys=True)` is
    # typed cleanly enough that the pragma at the module top absorbs the
    # residual Unknowns.
    existing_keys: list[int] = [k for (_a, b, k) in contracted.out_edges(u, keys=True) if b == v]
    return max(existing_keys) + 1


def _aggregate_sac_scale(edges: tuple[Edge, ...]) -> str | None:
    """Maximum SAC rank across a climb's edges as a SAC-scale string.

    Defers to `pipeline.osm.max_sac_rank` per edge so the
    list-valued-`sac_scale` case (osmnx merges parallel-way tags into a list)
    is handled identically to `filter_trails`' difficulty-cap path. `None`-
    rank and unrecognized-value edges contribute rank 0 — they never raise
    the aggregate. Returns `None` only when every edge's `sac_scale` is
    unset / unrecognized; otherwise returns the canonical SAC string for
    the maximum rank found.
    """
    max_rank: int = 0
    for edge in edges:
        rank = max_sac_rank(edge.sac_scale)
        if rank is not None and rank > max_rank:
            max_rank = rank
    if max_rank == 0:
        return None
    return _SAC_RANK_TO_NAME[max_rank]
