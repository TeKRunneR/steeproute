# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same networkx boundary noise as the pipeline modules under test.
"""Unit tests for pipeline.graph.contract_climbs (stage 9, Story 3.3).

Synthetic graphs only — every test hand-builds a `MultiDiGraph` carrying the
stage-7 contract (`length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`,
`sac_scale`). Real-fixture coverage lives in
`tests/integration/test_graph_contraction_fixture.py`.

AC #2 scenarios drive the per-shape unit tests; AC #4 adds the `hypothesis`
property test pinning back-mapping injectivity + purity.
"""

from __future__ import annotations

import copy
import math

import networkx as nx
from hypothesis import given, settings
from hypothesis import strategies as st

from steeproute.models import Climb, Edge
from steeproute.pipeline.graph import contract_climbs

_L_CONNECTOR = 200.0


def _make_edge(
    u: int,
    v: int,
    length_m: float,
    d_plus_m: float,
    d_minus_m: float = 0.0,
    sac_scale: str | None = "hiking",
    key: int = 0,
) -> Edge:
    """Project a synthetic `Edge` matching the stage-7 attribute contract."""
    avg_gradient = (d_plus_m + d_minus_m) / length_m if length_m else 0.0
    return Edge(
        node_u=u,
        node_v=v,
        key=key,
        length_m=length_m,
        d_plus_m=d_plus_m,
        d_minus_m=d_minus_m,
        avg_gradient=avg_gradient,
        sac_scale=sac_scale,
    )


def _add_edge_from(g: nx.MultiDiGraph, edge: Edge) -> None:
    """Mirror an `Edge` value into a `MultiDiGraph` carrying the same attributes."""
    g.add_edge(
        edge.node_u,
        edge.node_v,
        key=edge.key,
        length_m=edge.length_m,
        d_plus_m=edge.d_plus_m,
        d_minus_m=edge.d_minus_m,
        avg_gradient=edge.avg_gradient,
        sac_scale=edge.sac_scale,
    )


def _climb_from_edges(edges: list[Edge]) -> Climb:
    """Build a `Climb` aggregate from a contiguous edge sequence."""
    length_m = sum(e.length_m for e in edges)
    d_plus_m = sum(e.d_plus_m for e in edges)
    avg_slope = d_plus_m / length_m if length_m else 0.0
    return Climb(
        edges=tuple(edges),
        length_m=length_m,
        d_plus_m=d_plus_m,
        avg_slope=avg_slope,
    )


def test_single_climb_collapses_into_one_super_edge() -> None:
    """One climb of N qualifying edges → one super-edge with summed metrics."""
    edges = [
        _make_edge(0, 1, length_m=100.0, d_plus_m=25.0),
        _make_edge(1, 2, length_m=100.0, d_plus_m=25.0),
        _make_edge(2, 3, length_m=100.0, d_plus_m=25.0),
        _make_edge(3, 4, length_m=100.0, d_plus_m=25.0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    # Exactly one edge in the contracted graph: the super-edge (0, 4, *).
    assert contracted.graph.number_of_edges() == 1
    assert contracted.graph.has_edge(0, 4)
    keys = list(contracted.graph[0][4].keys())
    assert len(keys) == 1
    super_key = keys[0]
    data = contracted.graph[0][4][super_key]
    assert math.isclose(data["length_m"], 400.0, abs_tol=1e-9)
    assert math.isclose(data["d_plus_m"], 100.0, abs_tol=1e-9)
    assert math.isclose(data["d_minus_m"], 0.0, abs_tol=1e-9)
    assert math.isclose(data["avg_gradient"], 100.0 / 400.0, abs_tol=1e-9)
    assert data["sac_scale"] == "hiking"

    # Back-mapping round-trips: super-edge → exactly the climb's edge tuple.
    assert (0, 4, super_key) in contracted.super_edge_to_base
    assert contracted.super_edge_to_base[(0, 4, super_key)] == climb.edges

    # Climb-internal nodes 1, 2, 3 are absorbed into the super-edge — they
    # must NOT appear in the contracted graph. Only the climb endpoints
    # (0 and 4) survive. A regression that left stray internal nodes (e.g.
    # a mis-typed `add_node` call) would only surface in the solver.
    assert set(contracted.graph.nodes) == {0, 4}


def test_super_edge_aggregate_equals_sum_of_underlying_edges() -> None:
    """AC #2: super-edge `length_m` / `d_plus_m` / `d_minus_m` = sum of base edges."""
    edges = [
        _make_edge(10, 11, length_m=150.0, d_plus_m=30.0, d_minus_m=5.0),
        _make_edge(11, 12, length_m=250.0, d_plus_m=70.0, d_minus_m=10.0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    assert contracted.graph.has_edge(10, 12)
    super_key = next(iter(contracted.graph[10][12].keys()))
    data = contracted.graph[10][12][super_key]
    assert math.isclose(data["length_m"], 400.0, abs_tol=1e-9)
    assert math.isclose(data["d_plus_m"], 100.0, abs_tol=1e-9)
    assert math.isclose(data["d_minus_m"], 15.0, abs_tol=1e-9)
    assert math.isclose(data["avg_gradient"], 115.0 / 400.0, abs_tol=1e-9)


def test_back_mapping_round_trips_to_underlying_edges() -> None:
    """`super_edge_to_base[super_id]` returns the climb's edges in order."""
    edges = [
        _make_edge(0, 1, length_m=100.0, d_plus_m=25.0),
        _make_edge(1, 2, length_m=100.0, d_plus_m=25.0),
        _make_edge(2, 3, length_m=100.0, d_plus_m=25.0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    super_id = next(iter(contracted.super_edge_to_base.keys()))
    mapped = contracted.super_edge_to_base[super_id]
    assert mapped == climb.edges
    # Ordering preserved (not just set equality).
    assert [(e.node_u, e.node_v) for e in mapped] == [(0, 1), (1, 2), (2, 3)]


def test_all_connectors_retained_short_tagged_reusable() -> None:
    """Story 5.1: every connector is kept; short ones are tagged `reusable=True`.

    Inverts the old drop behaviour — `length_m < l_connector` no longer drops
    the edge, it flags it as a reuse-exempt short linking segment.
    """
    long_connector = _make_edge(0, 1, length_m=300.0, d_plus_m=10.0, d_minus_m=10.0)
    short_connector = _make_edge(2, 3, length_m=100.0, d_plus_m=5.0, d_minus_m=5.0)
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    _add_edge_from(g, long_connector)
    _add_edge_from(g, short_connector)

    contracted = contract_climbs(g, [], l_connector=_L_CONNECTOR)

    assert contracted.super_edge_to_base == {}
    # Both connectors survive — no length-based drop.
    assert contracted.graph.has_edge(0, 1)
    assert contracted.graph.has_edge(2, 3)
    # Nodes that the old orphan-prune would have removed now stay.
    assert {0, 1, 2, 3} <= set(contracted.graph.nodes)
    # The short connector is reuse-exempt; the long one is not.
    assert contracted.graph[2][3][0]["reusable"] is True
    assert contracted.graph[0][1][0]["reusable"] is False


def test_connector_exactly_at_l_connector_is_not_reusable() -> None:
    """The exemption threshold is strict (`length_m < l_connector`).

    A connector exactly at `l_connector` is retained but NOT reuse-exempt —
    it is a primary segment subject to the once-only rule.
    """
    boundary_connector = _make_edge(0, 1, length_m=_L_CONNECTOR, d_plus_m=10.0, d_minus_m=10.0)
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    _add_edge_from(g, boundary_connector)

    contracted = contract_climbs(g, [], l_connector=_L_CONNECTOR)

    assert contracted.graph.has_edge(0, 1)
    assert contracted.graph[0][1][0]["reusable"] is False


def test_bidirectional_base_graph_one_direction_climb() -> None:
    """`u→v` climb + reverse-direction edges → super-edge `u→v` + connectors `v→u`.

    The reverse direction is never automatically a super-edge — it's a
    connector subject to the `l_connector` cut.
    """
    uphill_edges = [
        _make_edge(0, 1, length_m=200.0, d_plus_m=50.0),
        _make_edge(1, 2, length_m=200.0, d_plus_m=50.0),
    ]
    # Reverse direction: same trail, descending. Per-edge length 200 m (each
    # ≥ l_connector=200 → retained as connectors).
    reverse_edges = [
        _make_edge(2, 1, length_m=200.0, d_plus_m=0.0, d_minus_m=50.0, sac_scale="hiking"),
        _make_edge(1, 0, length_m=200.0, d_plus_m=0.0, d_minus_m=50.0, sac_scale="hiking"),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in uphill_edges + reverse_edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(uphill_edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    # Super-edge (0→2) exists; reverse-direction (2→1) and (1→0) survive as connectors.
    assert contracted.graph.has_edge(0, 2)
    assert contracted.graph.has_edge(2, 1)
    assert contracted.graph.has_edge(1, 0)
    # The reverse direction is NOT mapped as a super-edge.
    assert (2, 1, 0) not in contracted.super_edge_to_base
    assert (1, 0, 0) not in contracted.super_edge_to_base
    # `super_edge_to_base` holds exactly one entry: the (0, 2, *) super-edge.
    assert len(contracted.super_edge_to_base) == 1
    super_id = next(iter(contracted.super_edge_to_base.keys()))
    assert super_id[0] == 0 and super_id[1] == 2


def test_bidirectional_climb_with_short_reverse_retains_reverse_as_reusable() -> None:
    """`u→v` climb + short reverse-direction edges → super-edge + reusable reverse connectors.

    Inverts the old "short reverse dropped" behaviour: the reverse-direction
    edges are now retained as short reuse-exempt connectors. Critically, each
    reverse connector shares its undirected `base_segment_id` with the climb's
    super-edge — the collision that lets Story 5.2 forbid descending the trail
    a climb just ascended.
    """
    uphill_edges = [
        _make_edge(0, 1, length_m=200.0, d_plus_m=50.0),
        _make_edge(1, 2, length_m=200.0, d_plus_m=50.0),
    ]
    # Reverse direction: each edge 100 m, below `l_connector=200` → now kept as
    # reusable short connectors (previously dropped).
    reverse_edges = [
        _make_edge(2, 1, length_m=100.0, d_plus_m=0.0, d_minus_m=25.0, sac_scale="hiking"),
        _make_edge(1, 0, length_m=100.0, d_plus_m=0.0, d_minus_m=25.0, sac_scale="hiking"),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in uphill_edges + reverse_edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(uphill_edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    # Super-edge (0→2) plus the two reverse connectors all survive.
    assert contracted.graph.has_edge(0, 2)
    assert contracted.graph.has_edge(2, 1)
    assert contracted.graph.has_edge(1, 0)
    # Node 1, reachable via the reverse connectors, is back in the graph.
    assert 1 in contracted.graph.nodes
    # Reverse connectors are short → reuse-exempt.
    assert contracted.graph[2][1][0]["reusable"] is True
    assert contracted.graph[1][0][0]["reusable"] is True
    # Each reverse connector's undirected id is in the super-edge's id set.
    super_id = next(iter(contracted.super_edge_to_base.keys()))
    super_base_ids = contracted.graph[super_id[0]][super_id[1]][super_id[2]]["base_segment_id"]
    assert contracted.graph[2][1][0]["base_segment_id"] <= super_base_ids
    assert contracted.graph[1][0][0]["base_segment_id"] <= super_base_ids


def test_empty_climbs_keeps_all_connectors() -> None:
    """Empty `climbs` → contracted graph holds ALL connectors, short ones reusable."""
    long_a = _make_edge(0, 1, length_m=300.0, d_plus_m=0.0, d_minus_m=0.0)
    long_b = _make_edge(1, 2, length_m=200.0, d_plus_m=0.0, d_minus_m=0.0)
    short = _make_edge(3, 4, length_m=50.0, d_plus_m=0.0, d_minus_m=0.0)
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in [long_a, long_b, short]:
        _add_edge_from(g, e)

    contracted = contract_climbs(g, [], l_connector=_L_CONNECTOR)

    assert contracted.super_edge_to_base == {}
    # All three connectors retained — no drop.
    assert contracted.graph.number_of_edges() == 3
    assert contracted.graph.has_edge(0, 1)
    assert contracted.graph.has_edge(1, 2)
    assert contracted.graph.has_edge(3, 4)
    # Nodes 3 and 4 stay (their short connector is no longer dropped).
    assert {3, 4} <= set(contracted.graph.nodes)
    assert contracted.graph[3][4][0]["reusable"] is True
    assert contracted.graph[0][1][0]["reusable"] is False


def test_no_orphan_prune_short_connector_node_retained() -> None:
    """Story 5.1: a node reachable only via a short connector is now retained.

    Replaces the old orphan-prune-after-drop test — with no drop, there are no
    orphans to prune and the previously-pruned node survives.
    """
    long_edge = _make_edge(0, 1, length_m=300.0, d_plus_m=0.0, d_minus_m=0.0)
    short_edge = _make_edge(0, 5, length_m=50.0, d_plus_m=0.0, d_minus_m=0.0)
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in [long_edge, short_edge]:
        _add_edge_from(g, e)

    contracted = contract_climbs(g, [], l_connector=_L_CONNECTOR)

    # Node 5 — reachable only via the short connector — is no longer pruned.
    assert 5 in contracted.graph.nodes
    assert contracted.graph.has_edge(0, 5)
    assert contracted.graph[0][5][0]["reusable"] is True
    assert {0, 1}.issubset(contracted.graph.nodes)


def test_forward_and_reverse_connectors_share_base_segment_id() -> None:
    """AC #3: a connector and its reverse-direction counterpart get the same id.

    The undirected identity is the canonical sorted node-pair + key, so
    `(0, 1, 0)` and `(1, 0, 0)` collapse to one id — the property Story 5.2
    relies on to forbid re-walking a segment in the opposite direction.
    """
    forward = _make_edge(0, 1, length_m=300.0, d_plus_m=10.0, d_minus_m=10.0)
    reverse = _make_edge(1, 0, length_m=300.0, d_plus_m=10.0, d_minus_m=10.0)
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    _add_edge_from(g, forward)
    _add_edge_from(g, reverse)

    contracted = contract_climbs(g, [], l_connector=_L_CONNECTOR)

    fwd_id = contracted.graph[0][1][0]["base_segment_id"]
    rev_id = contracted.graph[1][0][0]["base_segment_id"]
    assert fwd_id == rev_id
    # Each connector carries exactly its own single base-segment id.
    assert len(fwd_id) == 1


def test_super_edge_base_segment_id_is_set_of_contracted_edge_ids() -> None:
    """AC #2: a super-edge's `base_segment_id` is the set of its base edges' ids.

    `reusable=False`, and the set size equals the number of contracted edges
    (all distinct undirected segments on a simple climb chain).
    """
    edges = [
        _make_edge(0, 1, length_m=150.0, d_plus_m=40.0),
        _make_edge(1, 2, length_m=150.0, d_plus_m=40.0),
        _make_edge(2, 3, length_m=150.0, d_plus_m=40.0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    super_key = next(iter(contracted.graph[0][3].keys()))
    data = contracted.graph[0][3][super_key]
    assert data["reusable"] is False
    expected = {(0, 1, 0), (1, 2, 0), (2, 3, 0)}
    assert data["base_segment_id"] == expected


def test_super_edge_shares_base_segment_id_with_reverse_connector() -> None:
    """AC #3: a climb super-edge shares an id with the reverse connectors of its trail.

    The climb ascends 0→1→2; the descent 2→1→0 survives as connectors (here
    long, so non-reusable). Each descent connector's id must lie inside the
    super-edge's id set — so Story 5.2 sees the out-and-back as a segment reuse.
    """
    uphill = [
        _make_edge(0, 1, length_m=250.0, d_plus_m=60.0),
        _make_edge(1, 2, length_m=250.0, d_plus_m=60.0),
    ]
    downhill = [
        _make_edge(2, 1, length_m=250.0, d_plus_m=0.0, d_minus_m=60.0),
        _make_edge(1, 0, length_m=250.0, d_plus_m=0.0, d_minus_m=60.0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in uphill + downhill:
        _add_edge_from(g, e)
    climb = _climb_from_edges(uphill)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    super_id = next(iter(contracted.super_edge_to_base.keys()))
    super_base_ids = contracted.graph[super_id[0]][super_id[1]][super_id[2]]["base_segment_id"]
    # Both descent connectors are long → retained, non-reusable, and their
    # undirected ids collide with the climb's.
    assert contracted.graph[2][1][0]["reusable"] is False
    assert contracted.graph[2][1][0]["base_segment_id"] <= super_base_ids
    assert contracted.graph[1][0][0]["base_segment_id"] <= super_base_ids
    # At least one shared id (the property the epics AC pins).
    assert super_base_ids & contracted.graph[2][1][0]["base_segment_id"]


def test_super_edge_sac_scale_aggregates_to_max_rank() -> None:
    """`sac_scale` on the super-edge equals the max-rank SAC across the climb."""
    edges = [
        _make_edge(0, 1, length_m=150.0, d_plus_m=30.0, sac_scale="hiking"),
        _make_edge(1, 2, length_m=150.0, d_plus_m=30.0, sac_scale="alpine_hiking"),
        _make_edge(2, 3, length_m=150.0, d_plus_m=30.0, sac_scale="mountain_hiking"),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    super_key = next(iter(contracted.graph[0][3].keys()))
    data = contracted.graph[0][3][super_key]
    assert data["sac_scale"] == "alpine_hiking"


def test_super_edge_sac_scale_is_none_when_all_edges_untagged() -> None:
    """If every climb edge has `sac_scale=None`, the super-edge's `sac_scale` is `None`."""
    edges = [
        _make_edge(0, 1, length_m=200.0, d_plus_m=50.0, sac_scale=None),
        _make_edge(1, 2, length_m=200.0, d_plus_m=50.0, sac_scale=None),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    super_key = next(iter(contracted.graph[0][2].keys()))
    data = contracted.graph[0][2][super_key]
    assert data["sac_scale"] is None


def test_super_edge_key_avoids_collision_with_existing_connector() -> None:
    """When a connector already occupies `(u, v, 0)`, the super-edge picks a different key."""
    # Long connector from 0 to 2 (a "shortcut" trail) survives the cut.
    connector_zero_two = _make_edge(0, 2, length_m=300.0, d_plus_m=0.0, d_minus_m=0.0, key=0)
    # Climb edges 0→1→2 — the super-edge will land at (0, 2) too.
    climb_edges = [
        _make_edge(0, 1, length_m=150.0, d_plus_m=40.0),
        _make_edge(1, 2, length_m=150.0, d_plus_m=40.0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in [connector_zero_two] + climb_edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(climb_edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    # Both edges exist between 0 and 2 — the connector (key=0) and the
    # super-edge (key>=1). They must have distinct keys.
    keys_zero_two = sorted(contracted.graph[0][2].keys())
    assert len(keys_zero_two) == 2
    assert 0 in keys_zero_two
    # The super-edge is identified by its presence in super_edge_to_base.
    super_keys_for_zero_two = [k for (u, v, k) in contracted.super_edge_to_base if (u, v) == (0, 2)]
    assert len(super_keys_for_zero_two) == 1
    # Pin the documented allocation rule (`max existing key + 1`): the connector
    # occupies key 0, so the super-edge must land on key 1. A regression to key
    # 2 (off-by-one) or any other non-zero value would still pass `!= 0`.
    assert super_keys_for_zero_two[0] == 1


def test_two_climbs_share_endpoints_get_distinct_super_edge_keys() -> None:
    """Two climbs both landing on `(0, 3)` get distinct super-edge keys.

    Pins the `_next_key_for` allocation policy: when a previous super-edge
    occupies `(u, v, k)`, the next climb's super-edge lands on `(u, v, k+1)`.
    """
    # Climb A: 0→1→3
    climb_a_edges = [
        _make_edge(0, 1, length_m=150.0, d_plus_m=40.0, key=0),
        _make_edge(1, 3, length_m=150.0, d_plus_m=40.0, key=0),
    ]
    # Climb B: 0→2→3 (independent path same endpoints)
    climb_b_edges = [
        _make_edge(0, 2, length_m=150.0, d_plus_m=40.0, key=0),
        _make_edge(2, 3, length_m=150.0, d_plus_m=40.0, key=0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in climb_a_edges + climb_b_edges:
        _add_edge_from(g, e)
    climb_a = _climb_from_edges(climb_a_edges)
    climb_b = _climb_from_edges(climb_b_edges)

    contracted = contract_climbs(g, [climb_a, climb_b], l_connector=_L_CONNECTOR)

    keys_zero_three = sorted(contracted.graph[0][3].keys())
    assert len(keys_zero_three) == 2  # one super-edge per climb
    # Both super-edges are mapped in super_edge_to_base.
    super_ids_for_zero_three = [
        (u, v, k) for (u, v, k) in contracted.super_edge_to_base if (u, v) == (0, 3)
    ]
    assert len(super_ids_for_zero_three) == 2


# ----------------------------------------------------------------------------
# Story 6.1: junction-aware climb splitting
# ----------------------------------------------------------------------------


def test_climb_split_at_interior_trail_junction_default_on() -> None:
    """Story 6.1: a climb splits at an interior node where a different trail joins.

    Climb 0→1→2→3; a side trail (10→2) joins at interior node 2 — a real
    junction. Splitting is on by default, so the atomic whole-climb super-edge
    (0→3) must NOT appear; instead two super-edges (0→2 and 2→3) emerge and the
    junction node 2 becomes a real node a route can board at. On the pre-fix
    (atomic-climb) code this fails — node 2 is absorbed and only (0, 3) exists.
    """
    climb_edges = [
        _make_edge(0, 1, length_m=150.0, d_plus_m=40.0),
        _make_edge(1, 2, length_m=150.0, d_plus_m=40.0),
        _make_edge(2, 3, length_m=150.0, d_plus_m=40.0),
    ]
    # External segment incident to interior node 2 — a different physical trail.
    side_trail = _make_edge(10, 2, length_m=300.0, d_plus_m=10.0, d_minus_m=10.0)
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in [*climb_edges, side_trail]:
        _add_edge_from(g, e)
    climb = _climb_from_edges(climb_edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    # Split at node 2 → two super-edges; no atomic (0, 3) super-edge.
    assert contracted.graph.has_edge(0, 2)
    assert contracted.graph.has_edge(2, 3)
    assert not contracted.graph.has_edge(0, 3)
    # Junction node 2 survives as a real node; non-junction interior node 1 is
    # still absorbed.
    assert 2 in contracted.graph.nodes
    assert 1 not in contracted.graph.nodes
    # One super-edge per sub-segment.
    super_ids = [sid for sid in contracted.super_edge_to_base if sid[0] in (0, 2)]
    assert len(super_ids) == 2
    # Each sub-super-edge's metrics are the sum of ITS base edges (split is exact).
    seg_0_2 = next(sid for sid in contracted.super_edge_to_base if (sid[0], sid[1]) == (0, 2))
    seg_2_3 = next(sid for sid in contracted.super_edge_to_base if (sid[0], sid[1]) == (2, 3))
    assert math.isclose(contracted.graph[0][2][seg_0_2[2]]["d_plus_m"], 80.0, abs_tol=1e-9)
    assert math.isclose(contracted.graph[2][3][seg_2_3[2]]["d_plus_m"], 40.0, abs_tol=1e-9)
    # base_segment_id / reusable tagging is preserved on the split super-edges.
    assert contracted.graph[0][2][seg_0_2[2]]["reusable"] is False
    assert contracted.graph[0][2][seg_0_2[2]]["base_segment_id"] == {(0, 1, 0), (1, 2, 0)}


def test_no_split_when_split_at_junctions_disabled() -> None:
    """`split_at_junctions=False` reproduces the pre-fix atomic-climb behaviour."""
    climb_edges = [
        _make_edge(0, 1, length_m=150.0, d_plus_m=40.0),
        _make_edge(1, 2, length_m=150.0, d_plus_m=40.0),
        _make_edge(2, 3, length_m=150.0, d_plus_m=40.0),
    ]
    side_trail = _make_edge(10, 2, length_m=300.0, d_plus_m=10.0, d_minus_m=10.0)
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in [*climb_edges, side_trail]:
        _add_edge_from(g, e)
    climb = _climb_from_edges(climb_edges)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR, split_at_junctions=False)

    # Atomic whole-climb super-edge 0→3; no split super-edges at the junction.
    assert contracted.graph.has_edge(0, 3)
    assert not contracted.graph.has_edge(0, 2)
    assert not contracted.graph.has_edge(2, 3)
    assert len([sid for sid in contracted.super_edge_to_base if (sid[0], sid[1]) == (0, 3)]) == 1
    assert len(contracted.super_edge_to_base) == 1


def test_no_split_at_same_trail_reverse_only_interior_node() -> None:
    """An interior node touched only by the climb's own reverse direction is not a junction.

    Climb 0→1→2 with the same trail's descent 2→1→0 present. Node 1 is interior
    but every incident base segment is the climb's own (undirected ids collide),
    so the climb must NOT split there — one super-edge 0→2, as before.
    """
    uphill = [
        _make_edge(0, 1, length_m=250.0, d_plus_m=60.0),
        _make_edge(1, 2, length_m=250.0, d_plus_m=60.0),
    ]
    downhill = [
        _make_edge(2, 1, length_m=250.0, d_plus_m=0.0, d_minus_m=60.0),
        _make_edge(1, 0, length_m=250.0, d_plus_m=0.0, d_minus_m=60.0),
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in [*uphill, *downhill]:
        _add_edge_from(g, e)
    climb = _climb_from_edges(uphill)

    contracted = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    assert contracted.graph.has_edge(0, 2)
    assert len([sid for sid in contracted.super_edge_to_base if (sid[0], sid[1]) == (0, 2)]) == 1


def test_contract_climbs_does_not_mutate_input_graph() -> None:
    """AC #4 purity: input `base_graph` topology and every edge-data dict preserved.

    Mirrors the snapshot pattern from
    `tests/unit/test_climb_detection.py::test_detect_climbs_does_not_mutate_input_graph` —
    snapshots `id(data)` + a deep-copied `dict(data)` per edge so a write-back
    on any single edge would be caught.
    """
    edges = [
        _make_edge(0, 1, length_m=150.0, d_plus_m=40.0),
        _make_edge(1, 2, length_m=150.0, d_plus_m=40.0),
        _make_edge(3, 4, length_m=300.0, d_plus_m=0.0, d_minus_m=0.0),
        _make_edge(5, 6, length_m=50.0, d_plus_m=0.0, d_minus_m=0.0),  # short → reusable connector
    ]
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for e in edges:
        _add_edge_from(g, e)
    climb = _climb_from_edges(edges[:2])

    nodes_before = g.number_of_nodes()
    edges_before = g.number_of_edges()
    snapshots: dict[tuple[int, int, int], tuple[int, dict[str, object]]] = {
        (u, v, k): (id(data), dict(data)) for u, v, k, data in g.edges(data=True, keys=True)
    }

    _ = contract_climbs(g, [climb], l_connector=_L_CONNECTOR)

    assert g.number_of_nodes() == nodes_before
    assert g.number_of_edges() == edges_before
    for u, v, k, data in g.edges(data=True, keys=True):
        snapshot_id, snapshot_contents = snapshots[(u, v, k)]
        assert id(data) == snapshot_id, f"edge ({u}, {v}, {k}) data dict was replaced"
        assert dict(data) == snapshot_contents, f"edge ({u}, {v}, {k}) data dict contents mutated"


# ----------------------------------------------------------------------------
# AC #4: hypothesis property test — back-mapping injectivity + purity
# ----------------------------------------------------------------------------


@st.composite
def _chain_climb_strategy(
    draw: st.DrawFn,
) -> tuple[nx.MultiDiGraph, list[Climb]]:
    """Generate a `(base_graph, climbs)` pair from disjoint linear chains.

    Each chain occupies a non-overlapping node-id range (so the chains are
    edge-disjoint by construction — exactly the post-`detect_climbs`
    invariant). Each chain contributes one climb whose edges are all chain
    edges. A few extra "connector" edges (sub- and supra-`l_connector`) get
    sprinkled across unused node-id pairs.
    """
    n_climbs: int = draw(st.integers(min_value=1, max_value=4))
    # `min_value=1` so single-edge climbs are exercised — that's the smallest
    # valid `Climb` shape and the most likely place an off-by-one in
    # `climb.edges[0].node_u` / `climb.edges[-1].node_v` would manifest.
    edges_per_climb: list[int] = draw(
        st.lists(
            st.integers(min_value=1, max_value=5),
            min_size=n_climbs,
            max_size=n_climbs,
        )
    )

    g: nx.MultiDiGraph = nx.MultiDiGraph()
    climbs: list[Climb] = []
    next_node: int = 0
    for n_edges in edges_per_climb:
        chain_edges: list[Edge] = []
        for _ in range(n_edges):
            length_m: float = draw(st.floats(min_value=100.0, max_value=400.0))
            d_plus_m: float = draw(st.floats(min_value=20.0, max_value=100.0))
            edge = _make_edge(next_node, next_node + 1, length_m=length_m, d_plus_m=d_plus_m)
            _add_edge_from(g, edge)
            chain_edges.append(edge)
            next_node += 1
        # Skip a node id between chains to keep them strictly disjoint.
        next_node += 1
        climbs.append(_climb_from_edges(chain_edges))

    # Sprinkle a few extra connectors — all retained now (no drop), with
    # lengths bracketing `l_connector` so both `reusable` values are exercised.
    n_extra: int = draw(st.integers(min_value=0, max_value=3))
    for _ in range(n_extra):
        u: int = next_node
        v: int = next_node + 1
        # Pick lengths bracketing the l_connector threshold so both reusable
        # (short) and non-reusable (long) connectors appear.
        length_m = draw(st.floats(min_value=50.0, max_value=350.0))
        connector = _make_edge(u, v, length_m=length_m, d_plus_m=0.0, d_minus_m=0.0)
        _add_edge_from(g, connector)
        next_node += 2

    return g, climbs


@given(graph_and_climbs=_chain_climb_strategy())
@settings(max_examples=50, deadline=None)
def test_back_mapping_is_injective_on_base_edge_identity(
    graph_and_climbs: tuple[nx.MultiDiGraph, list[Climb]],
) -> None:
    """No `(node_u, node_v, key)` base-edge identity appears in two super-edge mappings."""
    g, climbs = graph_and_climbs
    contracted = contract_climbs(g, climbs, l_connector=_L_CONNECTOR)
    seen: set[tuple[int, int, int]] = set()
    for super_id, base_edges in contracted.super_edge_to_base.items():
        for e in base_edges:
            key = (e.node_u, e.node_v, e.key)
            assert key not in seen, (
                f"base edge {key} appears in two super-edge mappings "
                f"(second hit on super-edge {super_id})"
            )
            seen.add(key)


@given(graph_and_climbs=_chain_climb_strategy())
@settings(max_examples=30, deadline=None)
def test_contract_climbs_is_pure_under_random_inputs(
    graph_and_climbs: tuple[nx.MultiDiGraph, list[Climb]],
) -> None:
    """Property: `base_graph` is byte-identical after `contract_climbs`.

    Snapshots every edge-data dict via `copy.deepcopy` (the strategy passes
    plain-float / plain-str / None values, so deep-copy is cheap and
    catches both replacement and in-place mutation).
    """
    g, climbs = graph_and_climbs
    nodes_before = g.number_of_nodes()
    edges_before = g.number_of_edges()
    deep_snapshots: dict[tuple[int, int, int], dict[str, object]] = {
        (u, v, k): copy.deepcopy(dict(data)) for u, v, k, data in g.edges(data=True, keys=True)
    }

    _ = contract_climbs(g, climbs, l_connector=_L_CONNECTOR)

    assert g.number_of_nodes() == nodes_before
    assert g.number_of_edges() == edges_before
    for u, v, k, data in g.edges(data=True, keys=True):
        assert dict(data) == deep_snapshots[(u, v, k)], (
            f"edge ({u}, {v}, {k}) data dict mutated by contract_climbs"
        )
