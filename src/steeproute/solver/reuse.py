# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations on `ContractedGraph.graph` surface as Unknown —
# same external-boundary pattern as `solver/grasp.py` and the `pipeline/` modules.
"""Undirected base-segment reuse rule — the single source of feasibility truth (Story 5.2, FR5).

The GRASP solver (`solver/grasp.py`), the exhaustive oracle
(`tests/integration/exhaustive_oracle.py`), and the runtime validator
(`validator.py`) all enforce the *same* once-per-route reuse rule on the
underlying physical trail segment, regardless of direction. Routing the rule
through this one module is what keeps their feasible sets bit-identical — the
property the Story 3.7 GRASP-vs-exhaustive quality gate depends on. (Same
single-source discipline as `models.route_avg_gradient` for the slope floor.)

The rule
========

Story 5.1 tags every contracted edge with a `base_segment_id`
(`frozenset[tuple[int, int, int]]` of undirected ids, so a segment and its
reverse share an id) and a `reusable` flag (`True` only for a short connector
`length_m < l_connector`). A route may traverse any **non-exempt** base segment
**at most once**, in either direction; exempt short connectors may recur freely.

**Exemption is evaluated per-id, not per-edge** (resolving the gap deferred from
Story 5.1): an id is reuse-exempt iff *every* edge carrying it is `reusable`.
`reusable` is a per-edge flag but the once-only identity is per-id, so a short
connector that happens to share an id with a (non-reusable) climb super-edge —
the reverse of that climb — is **not** exempt for that id. That is exactly what
forbids descending the reverse of a climb you have already ascended, killing the
degenerate out-and-back for short-edge climbs.

Mechanically: `non_exempt_base_segment_ids(graph)` is the union of the base ids
of every non-reusable edge (computed once per graph). An edge's *blocking ids*
are its base ids intersected with that set; an edge whose blocking set is empty
is a truly-exempt connector that never blocks and is never recorded.

Robustness
==========

A production `ContractedGraph` always carries the tags (Story 5.1). The lookups
fall back to the directed `(u, v, key)` identity with `reusable=False` for any
edge missing them, so a hand-built or not-yet-tagged test graph degrades to the
pre-5.1 directed edge-simple rule rather than raising `KeyError`.
"""

from __future__ import annotations

from typing import Any

from steeproute.models import ContractedGraph

__all__ = [
    "base_segment_id_map",
    "base_segment_ids",
    "blocking_ids",
    "non_exempt_base_segment_ids",
]


def base_segment_ids(
    data: dict[str, Any] | None, u: int, v: int, k: int
) -> frozenset[tuple[int, int, int]]:
    """The undirected base-segment ids an edge occupies (Story 5.1 `base_segment_id`).

    Falls back to the directed identity `{(u, v, k)}` when `data` is `None` (the
    edge is absent from the graph) or carries no `base_segment_id` (a not-yet-
    tagged test graph) — the pre-5.1 directed behaviour.
    """
    if data is None:
        return frozenset({(u, v, k)})
    stored = data.get("base_segment_id")
    if stored is None:
        return frozenset({(u, v, k)})
    return stored


def base_segment_id_map(
    graph: ContractedGraph,
) -> dict[tuple[int, int, int], frozenset[tuple[int, int, int]]]:
    """Map each contracted edge's directed `(u, v, k)` identity → its undirected base ids.

    The single source of the directed-edge → undirected-base-segment projection
    (Story 6.1). `solver/distinctness.py` consumes this so Jaccard distinctness
    keys on the *same* `base_segment_id` the reuse rule uses — a route walking a
    trail and another walking its reverse then count as overlapping, aligning
    FR11 distinctness with FR5 undirected reuse. An edge missing its tag degrades
    to its directed identity via `base_segment_ids` (test-graph robustness).
    """
    return {
        (u, v, k): base_segment_ids(data, u, v, k)
        for u, v, k, data in graph.graph.edges(keys=True, data=True)
    }


def non_exempt_base_segment_ids(graph: ContractedGraph) -> frozenset[tuple[int, int, int]]:
    """Base-segment ids subject to the once-only rule — ids carried by ≥1 non-reusable edge.

    An id is reuse-*exempt* iff every edge carrying it is `reusable`; equivalently,
    an id is non-exempt iff at least one non-reusable edge carries it. This unions
    the base ids of every non-reusable edge in the graph, so a short connector that
    shares an id with a non-reusable super-edge has that id treated as non-exempt.
    """
    non_exempt: set[tuple[int, int, int]] = set()
    for u, v, k, data in graph.graph.edges(keys=True, data=True):
        if not data.get("reusable", False):
            non_exempt |= base_segment_ids(data, u, v, k)
    return frozenset(non_exempt)


def blocking_ids(
    data: dict[str, Any] | None,
    u: int,
    v: int,
    k: int,
    non_exempt: frozenset[tuple[int, int, int]],
) -> frozenset[tuple[int, int, int]]:
    """The non-exempt base-segment ids this edge occupies (empty → reuse-exempt).

    These are the ids the edge blocks on and records when traversed: an edge is
    infeasible iff any of its blocking ids is already used. A truly-exempt short
    connector has an empty blocking set, so it never blocks and is never recorded.
    """
    return base_segment_ids(data, u, v, k) & non_exempt
