"""Unit tests for the canonical edge-sequence hash (Story 8.1 AC #6 / Architecture §Cat 11d).

The hash is the regression harness's mutation detector: it must be (a) stable across
runs so a committed golden stays comparable, and (b) sensitive to any change in the
route's edge identity so a silently-altered route can't slip past on matching scalars.
"""

from __future__ import annotations

from steeproute.regression import canonical_edge_sequence_hash

# A route's edges as (node_u, node_v, key) triples.
_EDGES = [(1, 2, 0), (2, 3, 0), (3, 1, 1)]


def test_hash_is_stable_across_runs() -> None:
    """SHA256 over the canonical serialization is process-independent (FR29).

    Pinned to a known digest so a change to the serialization scheme (sort rule,
    separators) is caught here rather than silently invalidating every golden.
    """
    assert (
        canonical_edge_sequence_hash(_EDGES)
        == "761bd353af9799d9a0ba31e562f9415b0f94e32fb52756949a0b4c38bdbdd421"
    )


def test_hash_is_repeatable() -> None:
    assert canonical_edge_sequence_hash(_EDGES) == canonical_edge_sequence_hash(list(_EDGES))


def test_hash_is_canonical_over_traversal_order() -> None:
    """Same edge set, different serialization order -> same hash (edges are sorted first)."""
    shuffled = [(3, 1, 1), (1, 2, 0), (2, 3, 0)]
    assert canonical_edge_sequence_hash(_EDGES) == canonical_edge_sequence_hash(shuffled)


def test_hash_changes_on_single_edge_substitution() -> None:
    """Swapping one edge for another changes the digest (mutation detection)."""
    mutated = [(1, 2, 0), (2, 3, 0), (3, 4, 1)]  # last edge's node_v 1 -> 4
    assert canonical_edge_sequence_hash(_EDGES) != canonical_edge_sequence_hash(mutated)


def test_hash_distinguishes_parallel_edges_by_key() -> None:
    """Parallel edges between the same node pair differ only by `key` and must not collide."""
    assert canonical_edge_sequence_hash([(1, 2, 0)]) != canonical_edge_sequence_hash([(1, 2, 1)])


def test_hash_is_direction_sensitive() -> None:
    """A directed edge and its reverse are distinct identities."""
    assert canonical_edge_sequence_hash([(1, 2, 0)]) != canonical_edge_sequence_hash([(2, 1, 0)])
