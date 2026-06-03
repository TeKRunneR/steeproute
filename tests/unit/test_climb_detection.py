# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: same networkx boundary noise as the pipeline modules under test.
"""Unit tests for pipeline.climbs.detect_climbs (stage 8, Story 3.2).

Synthetic graphs only — every test hand-builds a MultiDiGraph carrying the
stage-7 contract (`length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`,
`sac_scale`). Real-fixture coverage lives in
`tests/integration/test_climb_detection_fixture.py`.

The four AC #2 scenarios drive most tests; AC #4 purity + edge-disjointness
add the final two cases.
"""

from __future__ import annotations

import math

import networkx as nx

from steeproute.models import Edge
from steeproute.pipeline.climbs import detect_climbs

# Defaults matching PRD §"Initial parameter defaults". Tests pass explicit
# values to detect_climbs so we don't depend on these as module-scope
# constants in production code — they live here only as the canonical
# scenario knobs.
_MIN_CLIMB_SLOPE = 0.20
_MIN_CLIMB_GROUND_LENGTH = 300.0


def _chain_graph(specs: list[tuple[float, float]]) -> nx.MultiDiGraph:
    """Build a linear chain `0 -> 1 -> ... -> N` from `(length_m, d_plus_m)` specs.

    Every edge gets `d_minus_m=0`, `avg_gradient=d_plus_m/length_m` (matching
    stage 7's contract when `d_minus_m=0`), and `sac_scale="hiking"`.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for i, (length, d_plus) in enumerate(specs):
        g.add_edge(
            i,
            i + 1,
            key=0,
            length_m=float(length),
            d_plus_m=float(d_plus),
            d_minus_m=0.0,
            avg_gradient=float(d_plus) / float(length) if length else 0.0,
            sac_scale="hiking",
        )
    return g


def test_qualifying_uphill_chain_returns_single_climb() -> None:
    # Five 100 m edges at 25 m gain each — per-edge slope 0.25 ≥ min_climb_slope=0.20,
    # total 500 m ≥ min=300 m. Whole chain should collapse into one Climb.
    g = _chain_graph([(100.0, 25.0)] * 5)
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert len(climbs) == 1
    climb = climbs[0]
    assert len(climb.edges) == 5
    assert math.isclose(climb.length_m, 500.0, abs_tol=1e-9)
    assert math.isclose(climb.d_plus_m, 125.0, abs_tol=1e-9)
    assert math.isclose(climb.avg_slope, 0.25, abs_tol=1e-9)
    # AC #1 aggregate identity
    assert math.isclose(climb.length_m, sum(e.length_m for e in climb.edges), abs_tol=1e-9)
    assert math.isclose(climb.d_plus_m, sum(e.d_plus_m for e in climb.edges), abs_tol=1e-9)
    assert math.isclose(climb.avg_slope, climb.d_plus_m / climb.length_m, abs_tol=1e-9)


def test_short_qualifying_chain_below_min_length_not_emitted() -> None:
    # Single 100 m edge at slope 0.25 — per-edge slope qualifies but the
    # total length (100 m) is below the 300 m floor → no climb emitted.
    g = _chain_graph([(100.0, 25.0)])
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert climbs == []


def test_undulating_chain_terminates_when_running_average_would_drop() -> None:
    # Chain: 0→1 (100 m, +25), 1→2 (100 m, +30), 2→3 (100 m, +35), 3→4 (200 m, +5).
    # After 3 edges: cum=90/300=0.30. Adding edge 3 would give 95/500=0.19 < 0.20.
    # → climb terminates at 3 edges; length 300 m hits the min-length floor exactly.
    g = _chain_graph([(100.0, 25.0), (100.0, 30.0), (100.0, 35.0), (200.0, 5.0)])
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert len(climbs) == 1
    climb = climbs[0]
    assert len(climb.edges) == 3
    assert math.isclose(climb.length_m, 300.0, abs_tol=1e-9)
    assert math.isclose(climb.d_plus_m, 90.0, abs_tol=1e-9)
    assert math.isclose(climb.avg_slope, 0.30, abs_tol=1e-9)
    # The 4th edge must stay unconsumed (proves the termination is real, not
    # that we silently absorbed it).
    assert all((e.node_u, e.node_v) != (3, 4) for e in climb.edges)


def test_empty_graph_returns_empty_list() -> None:
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert climbs == []


def test_graph_with_no_qualifying_edges_returns_empty_list() -> None:
    # Per-edge slope 0.05 on every edge — none qualify as seeds.
    g = _chain_graph([(100.0, 5.0)] * 5)
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert climbs == []


def test_detect_climbs_does_not_mutate_input_graph() -> None:
    """AC #4: purity check — node/edge counts and every edge-data dict preserved.

    Iterates every edge (not just one sample) so a bug that mutates the dict
    of any single edge would be caught — e.g. an implementation marking
    edges as consumed by writing a key into their attribute dict.
    """
    g = _chain_graph([(100.0, 25.0)] * 5)
    nodes_before = g.number_of_nodes()
    edges_before = g.number_of_edges()
    # Snapshot every edge: identity-of-the-dict + full contents.
    snapshots: dict[tuple[int, int, int], tuple[int, dict[str, object]]] = {
        (u, v, k): (id(data), dict(data)) for u, v, k, data in g.edges(data=True, keys=True)
    }

    _ = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )

    assert g.number_of_nodes() == nodes_before
    assert g.number_of_edges() == edges_before
    for u, v, k, data in g.edges(data=True, keys=True):
        snapshot_id, snapshot_contents = snapshots[(u, v, k)]
        assert id(data) == snapshot_id, f"edge ({u}, {v}, {k}) data dict was replaced"
        assert dict(data) == snapshot_contents, f"edge ({u}, {v}, {k}) data dict contents mutated"


def test_climbs_are_edge_disjoint_across_parallel_chains() -> None:
    """AC #4: each edge appears in at most one climb."""
    # Two independent uphill chains: 0→1→2 (400 m, 100 D+) and 10→11→12 (400 m, 100 D+).
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for u, v in [(0, 1), (1, 2), (10, 11), (11, 12)]:
        g.add_edge(
            u,
            v,
            key=0,
            length_m=200.0,
            d_plus_m=50.0,
            d_minus_m=0.0,
            avg_gradient=0.25,
            sac_scale="hiking",
        )

    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert len(climbs) == 2
    seen: set[tuple[int, int, int]] = set()
    for climb in climbs:
        for e in climb.edges:
            assert isinstance(e, Edge)
            key = (e.node_u, e.node_v, e.key)
            assert key not in seen, f"edge {key} appears in multiple climbs"
            seen.add(key)


def test_edge_projection_carries_full_stage7_contract() -> None:
    """Returned `Edge` carries every stage-7 attribute, with sac_scale propagated."""
    g = _chain_graph([(100.0, 25.0)] * 3)
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert len(climbs) == 1
    first_edge = climbs[0].edges[0]
    assert first_edge.node_u == 0
    assert first_edge.node_v == 1
    assert first_edge.key == 0
    assert math.isclose(first_edge.length_m, 100.0, abs_tol=1e-9)
    assert math.isclose(first_edge.d_plus_m, 25.0, abs_tol=1e-9)
    assert math.isclose(first_edge.d_minus_m, 0.0, abs_tol=1e-9)
    assert math.isclose(first_edge.avg_gradient, 0.25, abs_tol=1e-9)
    assert first_edge.sac_scale == "hiking"


def test_sac_scale_none_propagates_through_edge_projection() -> None:
    # `Edge.sac_scale: str | None` — untagged-policy=include admits unset SAC.
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for u, v in [(0, 1), (1, 2)]:
        g.add_edge(
            u,
            v,
            key=0,
            length_m=200.0,
            d_plus_m=50.0,
            d_minus_m=0.0,
            avg_gradient=0.25,
            sac_scale=None,
        )
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert len(climbs) == 1
    assert all(e.sac_scale is None for e in climbs[0].edges)


def test_branching_picks_steepest_qualifying_continuation() -> None:
    # From node 1, two outgoing edges:
    #   (1, 2, 0): 100 m / +20 → slope 0.20 (just qualifying)
    #   (1, 3, 0): 100 m / +40 → slope 0.40 (steeper)
    # Seed (0,1,0): 100 m / +30 → slope 0.30.
    # Greedy-steepest should pick (1,3,0). Then no outgoing from node 3 → close.
    # 200 m total, but min=180 m here (lowered just for this test).
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_edge(
        0,
        1,
        key=0,
        length_m=100.0,
        d_plus_m=30.0,
        d_minus_m=0.0,
        avg_gradient=0.30,
        sac_scale="hiking",
    )
    g.add_edge(
        1,
        2,
        key=0,
        length_m=100.0,
        d_plus_m=20.0,
        d_minus_m=0.0,
        avg_gradient=0.20,
        sac_scale="hiking",
    )
    g.add_edge(
        1,
        3,
        key=0,
        length_m=100.0,
        d_plus_m=40.0,
        d_minus_m=0.0,
        avg_gradient=0.40,
        sac_scale="hiking",
    )

    climbs = detect_climbs(g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=180.0)
    # Two climbs possible: the steepest extension (0→1→3) and a leftover
    # candidate from (1,2,0) as its own seed. (1,2,0) alone is 100 m < 180 m,
    # so only the (0→1→3) climb survives.
    assert len(climbs) == 1
    climb = climbs[0]
    end_nodes = [(e.node_u, e.node_v) for e in climb.edges]
    assert (1, 3) in end_nodes, f"steepest continuation not taken: {end_nodes}"
    assert (1, 2) not in end_nodes


def test_slope_tie_breaks_on_lower_node_v_then_key() -> None:
    """FR29 reproducibility: equal-slope outgoing edges resolve by `(node_v, key)`.

    Two outgoing edges from node 1, identical slope 0.25: (1, 2, 0) and
    (1, 3, 0). Strict `slope > best_slope` with `sorted(out_edges(...))`
    iteration must pick the lower-`(node_v, key)` tuple, i.e. (1, 2, 0).
    A regression to `>=` (or unsorted iteration) would pick (1, 3, 0)
    instead.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_edge(
        0,
        1,
        key=0,
        length_m=100.0,
        d_plus_m=30.0,
        d_minus_m=0.0,
        avg_gradient=0.30,
        sac_scale="hiking",
    )
    g.add_edge(
        1,
        2,
        key=0,
        length_m=200.0,
        d_plus_m=50.0,
        d_minus_m=0.0,
        avg_gradient=0.25,
        sac_scale="hiking",
    )
    g.add_edge(
        1,
        3,
        key=0,
        length_m=200.0,
        d_plus_m=50.0,
        d_minus_m=0.0,
        avg_gradient=0.25,
        sac_scale="hiking",
    )

    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert len(climbs) == 1
    end_nodes = [(e.node_u, e.node_v) for e in climbs[0].edges]
    assert (1, 2) in end_nodes, f"tie-break picked the wrong continuation: {end_nodes}"
    assert (1, 3) not in end_nodes


def test_parallel_edges_keyed_distinctly_resolve_via_key_tie_break() -> None:
    """Parallel edges (same `(u, v)`, different `key`) are admitted independently.

    Two outgoing edges from node 1, both ending at node 2, with identical
    slope and `key=0` / `key=1`. The deterministic tie-break must pick the
    lower-key edge first; if both extensions were node-monotone we'd still
    only take one (node 2 is added to `visited_nodes` after the first).
    Pins both the parallel-edge admission and the `(node_v, key)` tie-break.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_edge(
        0,
        1,
        key=0,
        length_m=200.0,
        d_plus_m=50.0,
        d_minus_m=0.0,
        avg_gradient=0.25,
        sac_scale="hiking",
    )
    g.add_edge(
        1,
        2,
        key=0,
        length_m=200.0,
        d_plus_m=50.0,
        d_minus_m=0.0,
        avg_gradient=0.25,
        sac_scale="hiking",
    )
    g.add_edge(
        1,
        2,
        key=1,
        length_m=200.0,
        d_plus_m=50.0,
        d_minus_m=0.0,
        avg_gradient=0.25,
        sac_scale="hiking",
    )

    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    # Seed (0,1,0) extends through (1,2,0) (lower key wins tie). Node 2 then
    # joins visited_nodes; (1,2,1) is rejected by node-monotonicity. The
    # leftover seed (1,2,1) alone is 200 m, below the 300 m floor → no
    # second climb emitted.
    assert len(climbs) == 1
    extension_keys = [(e.node_u, e.node_v, e.key) for e in climbs[0].edges]
    assert (1, 2, 0) in extension_keys, f"low-key parallel edge not picked: {extension_keys}"
    assert (1, 2, 1) not in extension_keys


def test_descending_only_edge_does_not_qualify_as_seed() -> None:
    """`d_plus_m == 0` edges (pure descent) are skipped at seed selection.

    Distinct from `test_graph_with_no_qualifying_edges_returns_empty_list`
    (which uses small-but-non-zero d_plus). Pure descent is the semantically
    important "not a climb" branch.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    for u, v in [(0, 1), (1, 2), (2, 3)]:
        g.add_edge(
            u,
            v,
            key=0,
            length_m=200.0,
            d_plus_m=0.0,
            d_minus_m=80.0,
            avg_gradient=0.40,
            sac_scale="hiking",
        )
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    assert climbs == []


def test_node_monotonicity_blocks_zigzag_through_bidirectional_edges() -> None:
    """A walk cannot revisit a node via a back-direction edge of the same pair.

    Edges (0,1,0) and (1,0,0) both with non-zero d_plus (saddle profile).
    Without node-monotonicity, the walk seeds at (0,1,0), extends to
    (1,0,0), then is blocked from (0,1,0) by the consumed-edge check but
    could otherwise zigzag. With node-monotonicity, extension to (1,0,0)
    is rejected because node 0 is already in `visited_nodes`. The walk
    closes at the seed; 200 m < 300 m floor → no climb emitted.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    g.add_edge(
        0,
        1,
        key=0,
        length_m=200.0,
        d_plus_m=50.0,
        d_minus_m=20.0,
        avg_gradient=0.35,
        sac_scale="hiking",
    )
    g.add_edge(
        1,
        0,
        key=0,
        length_m=200.0,
        d_plus_m=50.0,
        d_minus_m=20.0,
        avg_gradient=0.35,
        sac_scale="hiking",
    )
    climbs = detect_climbs(
        g, min_climb_slope=_MIN_CLIMB_SLOPE, min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH
    )
    # Neither candidate reaches 300 m alone — without the guard, the walk
    # could chain (0,1,0) → (1,0,0) for 400 m total, which would emit a
    # zigzag "climb." The guard prevents this; both seeds fail independently.
    assert climbs == []
