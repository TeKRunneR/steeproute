# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations surface as Unknown; same external-boundary pattern
# as pipeline/osm.py, pipeline/smoothing.py, pipeline/dem.py, pipeline/climbs.py.
"""Pipeline stage 9: contracted climb-graph construction.

`contract_climbs(base_graph, climbs, l_connector) -> ContractedGraph` folds each
`Climb` from stage 8 into a single directed super-edge in a new `MultiDiGraph`,
carries forward connector edges whose `length_m >= l_connector` unchanged, and
returns the contracted graph plus a `(node_u, node_v, key) -> tuple[Edge, ...]`
back-mapping so the validator (Story 3.9) can expand super-edges back to base
edges for per-base-edge constraint checks.

Super-edges carry the same numeric attribute schema as base edges (`length_m`,
`d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`), with `length_m` /
`d_plus_m` / `d_minus_m` summed across the underlying base edges and
`avg_gradient = (d_plus_m + d_minus_m) / length_m` (stage 7's absolute-churn
definition). `sac_scale` aggregates as the maximum SAC rank across the climb's
edges per `pipeline.osm.SAC_SCALE_RANK`, with `None` entries treated as
below-`hiking` (so they never raise the aggregate). `geometry` and
`vertices_resampled` stay on the base edges — consumers reach them via
`super_edge_to_base` when they need geometry, never off the super-edge.

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
) -> ContractedGraph:
    """Stage 9: build the solver-side `ContractedGraph` from stage-8 climbs.

    For each `Climb`, emit one directed super-edge from `climb.edges[0].node_u`
    to `climb.edges[-1].node_v` carrying summed metrics. Non-climb edges with
    `length_m >= l_connector` are carried over from `base_graph` unchanged
    (entire edge-data dict, including `geometry`, `vertices_resampled`,
    `highway`, `osm_way_id`). Shorter connectors are dropped. Nodes whose
    degree falls to 0 after the drop are pruned.

    Args:
        base_graph: post-stage-7 `MultiDiGraph` carrying the
            `length_m`/`d_plus_m`/`d_minus_m`/`avg_gradient`/`sac_scale`
            edge-attribute contract. Never mutated.
        climbs: stage-8 output (`pipeline.climbs.detect_climbs`); each climb's
            `edges` tuple must correspond to a contiguous edge-disjoint
            sequence in `base_graph`.
        l_connector: minimum connector-edge length in meters; connectors
            below this threshold are removed. Inclusive (`>=`).

    Returns:
        `ContractedGraph` whose `graph` is a fresh `MultiDiGraph` (climbs as
        super-edges + surviving connectors, orphan-pruned) and whose
        `super_edge_to_base` maps each super-edge's `(node_u, node_v, key)`
        triple to the underlying `tuple[Edge, ...]` from the corresponding
        climb's `edges` field.

    Super-edge key allocation: when a climb's `(u, v)` already has parallel
    edges in the contracted graph (from a surviving connector or from another
    climb ending on the same endpoints), the new super-edge gets the smallest
    non-conflicting `key` — `max existing key for (u, v) in contracted` + 1,
    or 0 if no existing edge. Order: connectors added first, then climbs in
    input order, so connector keys land first and super-edges layer on top.
    """
    contracted: nx.MultiDiGraph = nx.MultiDiGraph()

    # 1. Build the set of base-edge identities consumed by climbs, then carry
    #    over each surviving connector (non-climb edge with length >= l_connector)
    #    unchanged. Connectors land first so super-edge key allocation in step 2
    #    sees them and picks non-colliding keys.
    climb_edge_ids: set[tuple[int, int, int]] = set()
    for climb in climbs:
        for e in climb.edges:
            climb_edge_ids.add((e.node_u, e.node_v, e.key))

    for u, v, k, data in base_graph.edges(data=True, keys=True):
        if (u, v, k) in climb_edge_ids:
            continue
        if data["length_m"] < l_connector:
            continue
        # `**data` unpacking creates a fresh outer attribute dict for the
        # contracted edge — but mutable values inside (`vertices_resampled`
        # list, `geometry` LineString, list-valued `highway` / `osm_way_id`)
        # remain aliased to `base_graph`'s. `contract_climbs` itself never
        # mutates any of these, so the purity contract holds on the call.
        # Downstream consumers reading the contracted graph must treat edge
        # data as read-only — same convention as `pipeline.climbs`.
        contracted.add_edge(u, v, key=k, **data)

    # 2. Emit one super-edge per climb. Each super-edge carries the aggregated
    #    metrics + the max-rank SAC; geometry / vertices stay on base edges
    #    reachable through `super_edge_to_base`.
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {}
    for climb in climbs:
        u_super: int = climb.edges[0].node_u
        v_super: int = climb.edges[-1].node_v
        k_super: int = _next_key_for(contracted, u_super, v_super)
        length_m: float = sum(e.length_m for e in climb.edges)
        d_plus_m: float = sum(e.d_plus_m for e in climb.edges)
        d_minus_m: float = sum(e.d_minus_m for e in climb.edges)
        avg_gradient: float = (d_plus_m + d_minus_m) / length_m
        sac_scale: str | None = _aggregate_sac_scale(climb.edges)
        contracted.add_edge(
            u_super,
            v_super,
            key=k_super,
            length_m=length_m,
            d_plus_m=d_plus_m,
            d_minus_m=d_minus_m,
            avg_gradient=avg_gradient,
            sac_scale=sac_scale,
        )
        super_edge_to_base[(u_super, v_super, k_super)] = climb.edges

    # 3. Prune any node left with degree 0 after the connector drop (mirrors
    #    `pipeline.__init__._drop_orphan_nodes`; local copy keeps this module
    #    self-contained — the orchestrator's helper is module-private).
    _drop_orphan_nodes(contracted)

    return ContractedGraph(graph=contracted, super_edge_to_base=super_edge_to_base)


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


def _drop_orphan_nodes(graph: nx.MultiDiGraph) -> None:
    """In-place: drop nodes whose degree is 0 from `graph`.

    Called on the freshly-built contracted graph (which `contract_climbs`
    owns), so the in-place mutation does not violate the purity contract on
    the input `base_graph`. Mirrors `pipeline.__init__._drop_orphan_nodes`'s
    behavior; duplicated here so this module is self-contained against the
    setup-side orchestrator.
    """
    orphans: list[int] = [n for n, deg in graph.degree() if deg == 0]
    for n in orphans:
        graph.remove_node(n)
