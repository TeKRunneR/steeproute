"""Top-N tracker enforcing pairwise Jaccard distinctness (FR11) + the pure
`jaccard_distance` primitive it consumes.

Convention pinned in one place (Architecture §Cat 6b + §"Numerical and data
discipline"):

- `jaccard_distance(a, b)` returns `1 - |E(a) ∩ E(b)| / |E(a) ∪ E(b)|` over the
  canonical edge-identity sets (each edge collapsed to its `(node_u, node_v,
  key)` triple). Identical edge-sets give `0.0`; disjoint edge-sets give
  `1.0`. Both-empty is defined as `0.0` (identical empty sets — keeps the
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

from steeproute.models import Solution

__all__ = ["TopNTracker", "jaccard_distance"]


def _canonical_edge_set(solution: Solution) -> frozenset[tuple[int, int, int]]:
    """Project a `Solution` onto its canonical edge-identity set.

    Each edge collapses to `(node_u, node_v, key)` per Architecture §"Numerical
    and data discipline" — the same tuple used for cache-key hashing — so two
    `Edge` values that differ on metrics but share identity collapse together.
    """
    return frozenset((e.node_u, e.node_v, e.key) for e in solution.edges)


def jaccard_distance(a: Solution, b: Solution) -> float:
    """Return `1 - |E(a) ∩ E(b)| / |E(a) ∪ E(b)|` over canonical edge-identity sets.

    Range `[0.0, 1.0]`. Identical edge-sets → `0.0`; disjoint → `1.0`. Both
    solutions empty → `0.0` by definition (the `0/0` trap; an empty
    `Solution` is illegal at the validator stage but defining the math here
    keeps the primitive total).

    Pure: takes no shared state, mutates nothing.
    """
    edges_a = _canonical_edge_set(a)
    edges_b = _canonical_edge_set(b)
    union = edges_a | edges_b
    if not union:
        return 0.0
    intersection = edges_a & edges_b
    return 1.0 - len(intersection) / len(union)


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
    """

    def __init__(self, n: int, j_max: float) -> None:
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        if not (0.0 <= j_max <= 1.0):
            raise ValueError(f"j_max must be in [0.0, 1.0], got {j_max}")
        self._n: int = n
        self._j_max: float = j_max
        self._held: list[Solution] = []

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
        overlap_threshold = 1.0 - self._j_max
        overlapping = [s for s in self._held if jaccard_distance(solution, s) < overlap_threshold]

        if not overlapping:
            if len(self._held) < self._n:
                self._held.append(solution)
                return True
            # Capacity reached; evict the worst if the newcomer beats it.
            worst = self._worst_held()
            if solution.objective > worst.objective:
                self._held.remove(worst)
                self._held.append(solution)
                return True
            return False

        # Overlap branch: candidate must strictly beat every overlapping
        # incumbent to maintain the pairwise-distinct invariant.
        if all(solution.objective > s.objective for s in overlapping):
            for s in overlapping:
                self._held.remove(s)
            self._held.append(solution)
            return True
        return False

    def current_top(self) -> list[Solution]:
        """Held solutions, objective-descending with deterministic tie-break."""
        return sorted(self._held, key=_sort_key)

    def total_objective(self) -> float:
        """Sum of held objectives. `0.0` on an empty tracker (stagnation hook)."""
        return sum(s.objective for s in self._held)

    def _worst_held(self) -> Solution:
        """Lowest-objective held solution (last in the deterministic sort order).

        `max(items, key=_sort_key)` is equivalent to `sorted(items, key=_sort_key)[-1]`
        — the worst-in-sort-order is the same as the max-by-sort-key.
        """
        return max(self._held, key=_sort_key)


def _sort_key(solution: Solution) -> tuple[float, tuple[tuple[int, int, int], ...]]:
    """`(-objective, sorted_edge_ids)` — objective-descending + deterministic tie-break."""
    edge_ids = sorted((e.node_u, e.node_v, e.key) for e in solution.edges)
    return (-solution.objective, tuple(edge_ids))
