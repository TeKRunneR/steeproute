"""Top-N tracker enforcing pairwise Jaccard distinctness (FR11) + the pure
`jaccard_distance` primitive it consumes.

Convention pinned in one place (Architecture §Cat 6b + §"Numerical and data
discipline"):

- `jaccard_distance(a, b, segment_map)` returns `1 - |E(a) ∩ E(b)| / |E(a) ∪
  E(b)|` over the canonical edge-identity sets. With `segment_map=None` each
  edge collapses to its directed `(node_u, node_v, key)` triple; with a
  `segment_map` (directed id → undirected `base_segment_id`, from
  `solver.reuse.base_segment_id_map`) each edge collapses to the **undirected**
  base segments it occupies, so a route and the reverse-direction traversal of
  the same physical trail count as overlapping (Story 6.1 — aligns FR11
  distinctness with FR5 undirected reuse). Identical sets give `0.0`; disjoint
  give `1.0`. Both-empty is defined as `0.0` (identical empty sets — keeps the
  function total for the otherwise-illegal zero-edge `Solution`).
- `TopNTracker` reads `j_max` as the **similarity ceiling** (FR7's
  `--j-max`): two solutions overlap iff
  `jaccard_distance(...) < 1 - j_max`. So `j_max = 0.30` means "two routes
  may share at most 30% of their edges by Jaccard similarity."

The tracker is solver-internal — `GraspSolver` (Story 3.6) is the sole
producer; the validator (Story 3.9) consumes `current_top()`'s output. No
CLI / config dependency: `n` and `j_max` arrive as raw constructor args.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import NamedTuple

from steeproute.models import Solution

__all__ = ["TopNTracker", "jaccard_distance"]

# Directed edge identity `(node_u, node_v, key)` → the undirected base-segment
# ids it occupies. Built once per graph by `solver.reuse.base_segment_id_map`
# and threaded in so distinctness keys on the same identity as the reuse rule.
SegmentMap = Mapping[tuple[int, int, int], frozenset[tuple[int, int, int]]]


def _canonical_edge_set(
    solution: Solution, segment_map: SegmentMap | None = None
) -> frozenset[tuple[int, int, int]]:
    """Project a `Solution` onto its canonical identity set.

    With `segment_map=None`, each edge collapses to its directed `(node_u,
    node_v, key)` triple per Architecture §"Numerical and data discipline" — the
    same tuple used for cache-key hashing. With a `segment_map`, each edge
    collapses to the **undirected** base-segment ids it occupies (Story 6.1), so
    opposite-direction traversals of the same trail share identity. An edge
    absent from the map degrades to its own directed identity.
    """
    if segment_map is None:
        return frozenset((e.node_u, e.node_v, e.key) for e in solution.edges)
    ids: set[tuple[int, int, int]] = set()
    for e in solution.edges:
        edge_id = (e.node_u, e.node_v, e.key)
        ids |= segment_map.get(edge_id, frozenset({edge_id}))
    return frozenset(ids)


def _jaccard_from_sets(
    edges_a: frozenset[tuple[int, int, int]], edges_b: frozenset[tuple[int, int, int]]
) -> float:
    """Jaccard distance over two already-canonical identity sets.

    The set math of `jaccard_distance`, factored out (Story 12.2) so
    `TopNTracker` can compare cached canonical sets without re-projecting the
    solutions on every pairwise comparison. Same definition: both-empty →
    `0.0`.
    """
    union = edges_a | edges_b
    if not union:
        return 0.0
    intersection = edges_a & edges_b
    return 1.0 - len(intersection) / len(union)


def jaccard_distance(a: Solution, b: Solution, segment_map: SegmentMap | None = None) -> float:
    """Return `1 - |E(a) ∩ E(b)| / |E(a) ∪ E(b)|` over canonical identity sets.

    Range `[0.0, 1.0]`. Identical sets → `0.0`; disjoint → `1.0`. Both
    solutions empty → `0.0` by definition (the `0/0` trap; an empty
    `Solution` is illegal at the validator stage but defining the math here
    keeps the primitive total). `segment_map` selects directed (`None`) vs
    undirected base-segment identity — see `_canonical_edge_set`.

    Pure: takes no shared state, mutates nothing.
    """
    return _jaccard_from_sets(
        _canonical_edge_set(a, segment_map), _canonical_edge_set(b, segment_map)
    )


class _HeldEntry(NamedTuple):
    """A held solution paired with its cached canonical edge-identity set.

    The set is computed once at insertion (Story 12.2) — `Solution` is
    immutable, so it can never go stale.
    """

    solution: Solution
    canonical: frozenset[tuple[int, int, int]]


class TopNTracker:
    """Top-N solution holder with pairwise Jaccard distinctness (FR11, FR12).

    Admission policy (a candidate `new` arrives via `consider(new)`):

    1. Compute the overlap set: every held solution `s` for which
       `jaccard_distance(new, s) < 1 - j_max`.
    2. **No overlap** with any incumbent:
       - if `len(held) < n`           → admit `new`, return `True`;
       - else if `new.objective > worst.objective` → evict the worst,
         admit `new`, return `True`;
       - else                         → reject, return `False`.
    3. **Overlaps** with one or more incumbents:
       - if `new.objective` strictly beats every overlapping incumbent's
         objective → evict all overlap members, admit `new`, return `True`
         (note: tracker may shrink below `n` — this is FR12 graceful
         degradation, not a bug);
       - else → reject, return `False`.

    The "beat every overlap member" rule preserves the invariant that all
    held solutions are pairwise distinct. Replacing only the highest-objective
    overlap member would leave the candidate still overlapping with the
    others.

    Tie-breaking: `current_top()` sorts by `(-objective, sorted_edge_ids)`
    (a sorted tuple of the canonical `(node_u, node_v, key)` triples) so
    equal-objective routes order deterministically for a fixed input
    sequence (FR29 reproducibility — the GRASP solver feeds a deterministic,
    seed-derived sequence). The "worst" used for eviction is the last entry
    of that sorted order.

    Admission is a single-pass greedy filter: which solutions end up held can
    depend on the order candidates arrive in when overlaps are involved
    (Jaccard distinctness is not transitive). This does not threaten FR29 —
    the producer's sequence is itself reproducible — but it does mean the
    tracker is not order-independent for arbitrary permutations of the same
    multiset. Order-independence holds only in the "sufficiently distinct"
    regime where no candidate ever triggers an overlap rejection.

    Degenerate `j_max`: `j_max = 1.0` makes `overlap_threshold = 0.0`, so the
    overlap test (`jaccard_distance < 0.0`) is never true — the distinctness
    filter is effectively disabled and byte-identical duplicate routes can be
    held. `j_max = 0.0` requires fully-disjoint routes (any shared edge is an
    overlap). Both are inside the validated `[0.0, 1.0]` range.

    Stagnation hook: `total_objective()` returns the sum of held objectives
    (`0.0` when empty). The Epic 4 termination watcher polls this between
    GRASP iterations; an unchanged value across `--stagnation-iters`
    iterations triggers `convergence_status = "converged"`.

    Mutability: holds `Solution` references directly. `Solution` is
    `frozen=True, slots=True` (Story 3.1) so this is safe — the tracker
    cannot mutate the values, and the values cannot be mutated through
    other references either.

    Caching (Story 12.2): each held entry pairs the `Solution` with its
    canonical edge-identity set, computed **once at insertion** — the 11.2
    profile attributed ~7% of query wall-clock to re-projecting immutable held
    solutions on every pairwise comparison. Immutability makes the cache safe;
    a cached frozenset is *equal* to a recomputed one, so every distance,
    overlap verdict, and admission decision is unchanged. The cache lives here
    rather than on `Solution` because `slots=True` forbids attaching attributes.
    """

    def __init__(self, n: int, j_max: float, segment_map: SegmentMap | None = None) -> None:
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        if not (0.0 <= j_max <= 1.0):
            raise ValueError(f"j_max must be in [0.0, 1.0], got {j_max}")
        self._n: int = n
        self._j_max: float = j_max
        # `None` → directed `(u, v, key)` distinctness; a map → undirected
        # base-segment distinctness (Story 6.1), shared with the reuse rule so
        # admission and the validator's set-level check see one identity.
        self._segment_map: SegmentMap | None = segment_map
        self._held: list[_HeldEntry] = []

    def consider(self, solution: Solution) -> bool:
        """Try to admit `solution`. Returns `True` iff the held set changed.

        Raises `ValueError` on a non-finite objective: a `NaN` objective would
        be admitted unconditionally (the under-capacity branch does no
        comparison) yet never evictable (no value satisfies `> NaN`), and would
        poison `total_objective()` — silently breaking the Epic 4 stagnation
        watcher. Fail loud at the boundary instead, consistent with the
        constructor's `n` / `j_max` guards. Producers (the GRASP solver) score
        objectives as finite `D+ + D-` sums, so this only fires on an upstream
        bug.
        """
        if not math.isfinite(solution.objective):
            raise ValueError(f"solution.objective must be finite, got {solution.objective}")
        # Candidate's canonical set: once per consider() call, not once per
        # held comparison (Story 12.2). Held sets were cached at insertion.
        candidate_set = _canonical_edge_set(solution, self._segment_map)
        overlap_threshold = 1.0 - self._j_max
        overlapping = [
            entry
            for entry in self._held
            if _jaccard_from_sets(candidate_set, entry.canonical) < overlap_threshold
        ]

        if not overlapping:
            if len(self._held) < self._n:
                self._held.append(_HeldEntry(solution, candidate_set))
                return True
            # Capacity reached; evict the worst if the newcomer beats it.
            worst = self._worst_held()
            if solution.objective > worst.solution.objective:
                self._held.remove(worst)
                self._held.append(_HeldEntry(solution, candidate_set))
                return True
            return False

        # Overlap branch: candidate must strictly beat every overlapping
        # incumbent to maintain the pairwise-distinct invariant.
        if all(solution.objective > entry.solution.objective for entry in overlapping):
            for entry in overlapping:
                self._held.remove(entry)
            self._held.append(_HeldEntry(solution, candidate_set))
            return True
        return False

    def current_top(self) -> list[Solution]:
        """Held solutions, objective-descending with deterministic tie-break."""
        return sorted((entry.solution for entry in self._held), key=_sort_key)

    def total_objective(self) -> float:
        """Sum of held objectives. `0.0` on an empty tracker (stagnation hook)."""
        return sum(entry.solution.objective for entry in self._held)

    def _worst_held(self) -> _HeldEntry:
        """Lowest-objective held entry (last in the deterministic sort order).

        `max(items, key=...)` is equivalent to `sorted(items, key=...)[-1]`
        — the worst-in-sort-order is the same as the max-by-sort-key.
        """
        return max(self._held, key=lambda entry: _sort_key(entry.solution))


def _sort_key(solution: Solution) -> tuple[float, tuple[tuple[int, int, int], ...]]:
    """`(-objective, sorted_edge_ids)` — objective-descending + deterministic tie-break."""
    edge_ids = sorted((e.node_u, e.node_v, e.key) for e in solution.edges)
    return (-solution.objective, tuple(edge_ids))
