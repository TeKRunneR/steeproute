# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations on the MultiDiGraph surface as Unknown — same
# boundary pattern as the pipeline modules and the other contraction tests.
"""Story 6.1 regression tests — each reproduces a real-area structural condition
the synthetic suite missed and **fails on the pre-fix code**.

Two of the three Story 6.1 fixes are covered here (the third — junction-aware
climb splitting — is pinned in `tests/unit/test_graph_contraction.py`):

1. **SAC cap-aware contraction.** A long mostly-easy climb with one above-cap
   pitch must keep its under-cap terrain routable, with the over-cap pitch
   dropped — achieved by running `filter_trails(..., difficulty_cap)` *before*
   `detect_climbs` (the production `cli/query.py` sequence). On the pre-fix code
   (detect on the unfiltered graph) the climb welds the over-cap pitch and its
   max-rank SAC aggregation poisons the whole super-edge.

2. **Undirected Jaccard distinctness.** Two routes traversing one physical trail
   in opposite directions must register as overlapping (`jaccard_distance < 1`)
   and be rejected under `--j-max 0`. On the pre-fix code (directed
   `(node_u, node_v, key)` keying) they look fully distinct (distance 1.000).

Each test asserts the fixed behaviour AND pins the pre-fix defect via a contrast
assertion, so the "fails on pre-fix" requirement is explicit in the test body.
"""

from __future__ import annotations

import networkx as nx

from steeproute.models import Climb, ContractedGraph, Edge, Solution, SolverParams
from steeproute.pipeline.climbs import detect_climbs
from steeproute.pipeline.graph import contract_climbs
from steeproute.pipeline.osm import filter_trails, max_sac_rank, parse_difficulty_cap
from steeproute.solver.distinctness import TopNTracker, jaccard_distance
from steeproute.solver.reuse import base_segment_id_map
from steeproute.validator import validate, validate_set

_L_CONNECTOR = 200.0
_MIN_CLIMB_SLOPE = 0.20
_MIN_CLIMB_GROUND_LENGTH_M = 300.0


def _add_edge(
    g: nx.MultiDiGraph,
    u: int,
    v: int,
    *,
    length_m: float,
    d_plus_m: float,
    d_minus_m: float = 0.0,
    sac_scale: str | None,
    key: int = 0,
) -> None:
    """Add a synthetic edge carrying the stage-7 contract + `highway` (for filter_trails)."""
    avg_gradient = (d_plus_m + d_minus_m) / length_m if length_m else 0.0
    g.add_edge(
        u,
        v,
        key=key,
        length_m=length_m,
        d_plus_m=d_plus_m,
        d_minus_m=d_minus_m,
        avg_gradient=avg_gradient,
        sac_scale=sac_scale,
        highway="path",
    )


# ----------------------------------------------------------------------------
# Fix 2: SAC cap-aware contraction
# ----------------------------------------------------------------------------


def _build_mixed_difficulty_chain() -> nx.MultiDiGraph:
    """A steep chain 0→1→2→3→4→5 with one above-cap (T4) pitch at edge 2→3.

    Everything else is T2 (`mountain_hiking`). Each edge is 150 m at ~30 % slope,
    so 0→2 and 3→5 each form a ≥300 m climb once the T4 pitch is removed.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    spec: list[tuple[int, int, str]] = [
        (0, 1, "mountain_hiking"),
        (1, 2, "mountain_hiking"),
        (2, 3, "alpine_hiking"),  # T4 — the embedded over-cap pitch
        (3, 4, "mountain_hiking"),
        (4, 5, "mountain_hiking"),
    ]
    for u, v, sac in spec:
        _add_edge(g, u, v, length_m=150.0, d_plus_m=45.0, sac_scale=sac)
    return g


def _contract_from(graph: nx.MultiDiGraph) -> ContractedGraph:
    climbs = detect_climbs(
        graph,
        min_climb_slope=_MIN_CLIMB_SLOPE,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
    )
    return contract_climbs(graph, climbs, l_connector=_L_CONNECTOR)


def test_sac_cap_prefilter_keeps_under_cap_terrain_routable() -> None:
    """At cap T3, pre-filtering drops the T4 pitch so no above-cap super-edge survives.

    Production sequence (`cli/query.py`): `filter_trails(..., difficulty_cap)`
    THEN `detect_climbs` THEN `contract_climbs`. The under-cap climbs (0→2,
    3→5) stay routable; the T4 pitch is gone.
    """
    cap = "T3"
    cap_rank = parse_difficulty_cap(cap)
    base = _build_mixed_difficulty_chain()

    # Fixed sequence: filter first.
    routable = filter_trails(base, "include", cap)
    fixed = _contract_from(routable)

    # No super-edge exceeds the cap.
    for _u, _v, _k, data in fixed.graph.edges(keys=True, data=True):
        rank = max_sac_rank(data["sac_scale"])
        assert rank is None or rank <= cap_rank, (
            f"super-edge {(_u, _v, _k)} has SAC rank {rank} above cap {cap_rank}"
        )
    # The under-cap terrain on both sides of the dropped pitch is still a climb.
    assert fixed.graph.has_edge(0, 2), "under-cap climb 0→2 must survive"
    assert fixed.graph.has_edge(3, 5), "under-cap climb 3→5 must survive"

    # Contrast — pin the pre-fix defect: WITHOUT pre-filtering, the whole chain
    # welds into one climb whose max-rank SAC aggregation produces an above-cap
    # super-edge. This is the exact condition the fix removes.
    prefix_broken = _contract_from(base)
    welded_above_cap = [
        (u, v, k)
        for u, v, k, data in prefix_broken.graph.edges(keys=True, data=True)
        if (rank := max_sac_rank(data["sac_scale"])) is not None and rank > cap_rank
    ]
    assert welded_above_cap, (
        "expected the pre-fix (unfiltered) path to weld an above-cap super-edge "
        "— if this is empty the regression no longer reproduces the bug"
    )


# ----------------------------------------------------------------------------
# Fix 3: undirected Jaccard distinctness
# ----------------------------------------------------------------------------


def _climb(edges: list[Edge]) -> Climb:
    length_m = sum(e.length_m for e in edges)
    d_plus_m = sum(e.d_plus_m for e in edges)
    return Climb(
        edges=tuple(edges),
        length_m=length_m,
        d_plus_m=d_plus_m,
        avg_slope=d_plus_m / length_m if length_m else 0.0,
    )


def _edge(u: int, v: int, key: int = 0) -> Edge:
    return Edge(
        node_u=u,
        node_v=v,
        key=key,
        length_m=250.0,
        d_plus_m=60.0,
        d_minus_m=0.0,
        avg_gradient=0.24,
        sac_scale="hiking",
    )


def _out_and_back_contracted() -> ContractedGraph:
    """Contracted graph: climb 0→1→2 (super-edge) + its long reverse descent 2→1→0.

    The reverse connectors share their undirected `base_segment_id` with the
    super-edge — the collision that makes opposite-direction reuse detectable.
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    uphill = [_edge(0, 1), _edge(1, 2)]
    downhill = [_edge(2, 1), _edge(1, 0)]  # long → non-reusable connectors
    for e in [*uphill, *downhill]:
        g.add_edge(
            e.node_u,
            e.node_v,
            key=e.key,
            length_m=e.length_m,
            d_plus_m=e.d_plus_m,
            d_minus_m=e.d_minus_m,
            avg_gradient=e.avg_gradient,
            sac_scale=e.sac_scale,
        )
    return contract_climbs(g, [_climb(uphill)], l_connector=_L_CONNECTOR)


def test_opposite_direction_reuse_is_overlap_under_undirected_distinctness() -> None:
    """Ascending a climb and descending its reverse register as overlapping (Story 6.1).

    With the undirected `base_segment_id` segment map, the ascent (the super-edge
    0→2) and the descent (the reverse connectors 2→1→0) project to the SAME base
    segments → `jaccard_distance == 0` → rejected under `--j-max 0`. On the
    pre-fix directed keying they are disjoint (distance 1.0) → spuriously
    distinct.
    """
    contracted = _out_and_back_contracted()
    segment_map = base_segment_id_map(contracted)

    super_id = next(iter(contracted.super_edge_to_base))  # (0, 2, k)
    ascent = Solution(edges=(_edge(super_id[0], super_id[1], super_id[2]),), objective=120.0)
    descent = Solution(edges=(_edge(2, 1), _edge(1, 0)), objective=60.0)

    # Fixed: undirected keying sees full overlap.
    assert jaccard_distance(ascent, descent, segment_map) < 1.0
    assert jaccard_distance(ascent, descent, segment_map) == 0.0

    # Rejected under --j-max 0 (any shared segment is overlap).
    tracker = TopNTracker(n=2, j_max=0.0, segment_map=segment_map)
    assert tracker.consider(ascent) is True
    assert tracker.consider(descent) is False
    assert len(tracker.current_top()) == 1

    # Contrast — pin the pre-fix defect: directed keying (no segment map) sees
    # the two as fully distinct, so the degenerate out-and-back pair survives.
    assert jaccard_distance(ascent, descent) == 1.0
    directed_tracker = TopNTracker(n=2, j_max=0.0)
    assert directed_tracker.consider(ascent) is True
    assert directed_tracker.consider(descent) is True
    assert len(directed_tracker.current_top()) == 2


def _params(*, j_max: float) -> SolverParams:
    """Minimal `SolverParams` for the validator wiring test (theta low so both routes pass per-route)."""
    return SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T3",
        l_connector=_L_CONNECTOR,
        min_climb_ground_length=_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=j_max,
        n=2,
        area_cap=500.0,
        untagged_policy="include",
        seed=42,
        iter_budget=1000,
        time_budget=60.0,
        stagnation_iters=0,
    )


def test_validate_threads_undirected_keying_end_to_end() -> None:
    """`validate()` flags the opposite-direction pair as a set-level violation under `--j-max 0`.

    Pins the wiring `validate → validate_set(routes, params, graph) → undirected
    segment map`. Three reviewers flagged that the new `graph`/`segment_map`
    parameters default to `None` (directed keying) for back-compat, so a future
    drop of the `graph` argument would silently revert distinctness to the
    pre-6.1 directed semantics. This test fails if that wiring regresses.
    """
    contracted = _out_and_back_contracted()
    super_id = next(iter(contracted.super_edge_to_base))  # (0, 2, k)
    ascent = Solution(edges=(_edge(super_id[0], super_id[1], super_id[2]),), objective=120.0)
    descent = Solution(edges=(_edge(2, 1), _edge(1, 0)), objective=120.0)
    params = _params(j_max=0.0)

    validated = validate([ascent, descent], contracted, params)

    # Both routes pass per-route (the only thing wrong is they overlap as a set).
    assert all(r.validation.passed for r in validated.routes)
    # End-to-end: the opposite-direction pair is flagged via undirected keying.
    assert validated.set_violations, "validate() must flag the out-and-back pair under --j-max 0"
    assert (
        validated.set_violations[0].route_index_a,
        validated.set_violations[0].route_index_b,
    ) == (
        0,
        1,
    )

    # Contrast — pin the wiring: it is the `graph` argument that flips the result.
    # Directed keying (no graph) sees the two routes as distinct → no violation.
    assert validate_set(validated.routes, params) == []
    assert validate_set(validated.routes, params, contracted)
