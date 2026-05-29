"""Unit tests for solver.distinctness (Story 3.4).

`jaccard_distance` is a pure free function operating on canonical edge-identity
sets; `TopNTracker` is a stateful container enforcing the FR11 distinctness
ceiling. All tests build `Solution` instances inline — no fixture I/O.

Coverage map (AC → test):
- AC #2 admission / rejection-by-worse / rejection-by-Jaccard / substitution
  → the four `test_consider_*` cases (plus the n=2 substitution variant).
- AC #3 jaccard symmetry / identity / range / admission-order-independence
  → the four `test_jaccard_distance_*` and `test_admission_*` property tests.
- AC #4 no-shared-state / pure consider
  → `test_consider_is_pure_under_fresh_tracker_replay`.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from steeproute.models import Edge, Solution
from steeproute.solver.distinctness import TopNTracker, jaccard_distance

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _make_edge(u: int, v: int, key: int = 0) -> Edge:
    """Build a minimal `Edge` carrying only fields the tracker actually reads.

    Metrics are placeholders — Jaccard distance and the tracker use only the
    canonical `(node_u, node_v, key)` identity tuple.
    """
    return Edge(
        node_u=u,
        node_v=v,
        key=key,
        length_m=100.0,
        d_plus_m=25.0,
        d_minus_m=0.0,
        avg_gradient=0.25,
        sac_scale="hiking",
    )


def _make_solution(edge_ids: list[tuple[int, int, int]], objective: float) -> Solution:
    """Build a `Solution` from a list of `(node_u, node_v, key)` triples."""
    return Solution(
        edges=tuple(_make_edge(u, v, key=k) for u, v, k in edge_ids),
        objective=objective,
    )


# ----------------------------------------------------------------------------
# AC #2 — four structural cases for TopNTracker.consider
# ----------------------------------------------------------------------------


def test_consider_admits_into_empty_tracker() -> None:
    """First `consider(sol)` on an empty tracker admits unconditionally."""
    tracker = TopNTracker(n=3, j_max=0.30)
    sol = _make_solution([(0, 1, 0), (1, 2, 0), (2, 3, 0)], objective=100.0)

    admitted = tracker.consider(sol)

    assert admitted is True
    assert tracker.current_top() == [sol]
    assert math.isclose(tracker.total_objective(), 100.0, abs_tol=1e-9)


def test_consider_rejects_when_new_is_worse_than_every_member() -> None:
    """Tracker full of pairwise-distinct better solutions rejects a worse newcomer."""
    tracker = TopNTracker(n=3, j_max=0.30)
    a = _make_solution([(0, 1, 0), (1, 2, 0), (2, 3, 0)], objective=300.0)
    b = _make_solution([(10, 11, 0), (11, 12, 0), (12, 13, 0)], objective=200.0)
    c = _make_solution([(20, 21, 0), (21, 22, 0), (22, 23, 0)], objective=150.0)
    for s in (a, b, c):
        assert tracker.consider(s) is True

    snapshot = tracker.current_top()
    # New distinct candidate with strictly worse objective than every held member.
    worse = _make_solution([(30, 31, 0), (31, 32, 0), (32, 33, 0)], objective=100.0)

    admitted = tracker.consider(worse)

    assert admitted is False
    assert tracker.current_top() == snapshot
    assert math.isclose(tracker.total_objective(), 300.0 + 200.0 + 150.0, abs_tol=1e-9)


def test_consider_rejects_overlap_when_new_is_worse() -> None:
    """Candidate overlapping an incumbent at lower objective is rejected (FR11)."""
    tracker = TopNTracker(n=3, j_max=0.30)
    # Edges 0..9 — share 8 of 10 with the candidate below ⇒ similarity 0.8 > j_max=0.30.
    a_edges = [(i, i + 1, 0) for i in range(10)]
    a = _make_solution(a_edges, objective=500.0)
    tracker.consider(a)

    # Same 8 first edges + 2 different tail edges; Jaccard similarity = 8 / 12 ≈ 0.67.
    overlap_edges = [(i, i + 1, 0) for i in range(8)] + [(100, 101, 0), (101, 102, 0)]
    overlap_worse = _make_solution(overlap_edges, objective=400.0)

    admitted = tracker.consider(overlap_worse)

    assert admitted is False
    assert tracker.current_top() == [a]


def test_consider_substitutes_when_new_is_better_and_distinct_at_capacity() -> None:
    """n=1 capacity: a distinct, better newcomer replaces the incumbent."""
    tracker = TopNTracker(n=1, j_max=0.30)
    a = _make_solution([(0, 1, 0), (1, 2, 0), (2, 3, 0)], objective=200.0)
    tracker.consider(a)

    b = _make_solution([(50, 51, 0), (51, 52, 0), (52, 53, 0)], objective=400.0)
    # Disjoint edge-sets → jaccard_distance(a, b) == 1.0, comfortably > 1 - j_max.

    admitted = tracker.consider(b)

    assert admitted is True
    assert tracker.current_top() == [b]
    assert math.isclose(tracker.total_objective(), 400.0, abs_tol=1e-9)


def test_consider_substitutes_worst_when_full_and_new_beats_worst_distinct() -> None:
    """n=2 capacity: distinct newcomer better than the worst member replaces the worst."""
    tracker = TopNTracker(n=2, j_max=0.30)
    a = _make_solution([(0, 1, 0), (1, 2, 0)], objective=300.0)
    b = _make_solution([(10, 11, 0), (11, 12, 0)], objective=200.0)
    tracker.consider(a)
    tracker.consider(b)

    c = _make_solution([(20, 21, 0), (21, 22, 0)], objective=250.0)
    # c distinct from both a and b; obj=250 beats b (200) but not a (300).

    admitted = tracker.consider(c)

    assert admitted is True
    top = tracker.current_top()
    assert top == [a, c]  # objective-descending: a (300) then c (250).
    assert math.isclose(tracker.total_objective(), 300.0 + 250.0, abs_tol=1e-9)


# ----------------------------------------------------------------------------
# Constructor validation guards
# ----------------------------------------------------------------------------


def test_tracker_rejects_non_positive_n() -> None:
    """`n < 1` is a programming error — fail loud at construction."""
    with pytest.raises(ValueError, match="n must be >= 1"):
        TopNTracker(n=0, j_max=0.30)


def test_tracker_rejects_out_of_range_j_max() -> None:
    """`j_max` outside `[0.0, 1.0]` is a programming error — fail loud at construction."""
    with pytest.raises(ValueError, match=r"j_max must be in \[0.0, 1.0\]"):
        TopNTracker(n=3, j_max=1.5)


# ----------------------------------------------------------------------------
# Additional structural assertions on TopNTracker semantics
# ----------------------------------------------------------------------------


def test_current_top_is_objective_descending() -> None:
    """`current_top()` always orders by objective descending regardless of insert order."""
    tracker = TopNTracker(n=3, j_max=0.30)
    low = _make_solution([(0, 1, 0)], objective=100.0)
    mid = _make_solution([(10, 11, 0)], objective=200.0)
    high = _make_solution([(20, 21, 0)], objective=300.0)
    # Insert worst-first.
    for s in (low, mid, high):
        tracker.consider(s)

    assert tracker.current_top() == [high, mid, low]


def test_total_objective_on_empty_tracker_is_zero() -> None:
    """Empty tracker → `total_objective() == 0.0` (lets the stagnation watcher poll without branching)."""
    tracker = TopNTracker(n=5, j_max=0.30)

    assert tracker.total_objective() == 0.0
    assert tracker.current_top() == []


def test_consider_replaces_overlapping_incumbent_when_new_is_better() -> None:
    """Overlap + better-objective newcomer replaces the (single) overlapping incumbent."""
    tracker = TopNTracker(n=3, j_max=0.30)
    a_edges = [(i, i + 1, 0) for i in range(10)]
    a = _make_solution(a_edges, objective=300.0)
    distinct = _make_solution([(100, 101, 0), (101, 102, 0)], objective=200.0)
    tracker.consider(a)
    tracker.consider(distinct)

    # Same 9 of 10 edges as a; similarity 9/11 ≈ 0.82 > j_max ⇒ overlap.
    overlap_better = _make_solution(
        [(i, i + 1, 0) for i in range(9)] + [(500, 501, 0)], objective=400.0
    )

    admitted = tracker.consider(overlap_better)

    assert admitted is True
    top = tracker.current_top()
    # `a` is evicted by overlap_better; `distinct` survives.
    assert overlap_better in top
    assert a not in top
    assert distinct in top


# Shared geometry for the two multi-overlap tests below. The candidate's 4 edges
# split into a "left half" shared with `a` and a "right half" shared with `b`, so
# the candidate overlaps BOTH incumbents while `a` and `b` stay mutually disjoint
# (and therefore can coexist in the tracker):
#   cand vs a: 2 shared / 5 union → similarity 0.4, distance 0.6 < 0.70 → overlap
#   cand vs b: 2 shared / 5 union → similarity 0.4, distance 0.6 < 0.70 → overlap
#   a   vs b: 0 shared            → distance 1.0 ≥ 0.70 → distinct
_CAND_EDGES = [(0, 1, 0), (1, 2, 0), (2, 3, 0), (3, 4, 0)]
_A_EDGES = [(0, 1, 0), (1, 2, 0), (10, 11, 0)]  # shares left half of cand
_B_EDGES = [(2, 3, 0), (3, 4, 0), (20, 21, 0)]  # shares right half of cand


def test_consider_evicts_all_overlapping_incumbents_when_new_beats_each() -> None:
    """All-overlap eviction: a candidate overlapping TWO (mutually-distinct) incumbents and beating both evicts both."""
    tracker = TopNTracker(n=4, j_max=0.30)
    a = _make_solution(_A_EDGES, objective=200.0)
    b = _make_solution(_B_EDGES, objective=250.0)
    far = _make_solution([(50, 51, 0), (51, 52, 0)], objective=180.0)
    tracker.consider(a)
    tracker.consider(b)
    tracker.consider(far)
    assert {a, b, far} == set(tracker.current_top())  # a and b coexist (mutually distinct)

    cand = _make_solution(_CAND_EDGES, objective=300.0)  # beats both (300 > 250, 300 > 200)
    assert jaccard_distance(cand, a) < 1 - 0.30
    assert jaccard_distance(cand, b) < 1 - 0.30
    assert jaccard_distance(a, b) >= 1 - 0.30

    admitted = tracker.consider(cand)

    assert admitted is True
    top = tracker.current_top()
    # BOTH a and b evicted (not just the higher-objective one); `far` (distinct) survives.
    assert cand in top
    assert a not in top
    assert b not in top
    assert far in top
    # Held set shrank from 3 to 2 — FR12 graceful degradation, not a bug.
    assert len(top) == 2


def test_consider_rejects_when_candidate_beats_some_but_not_all_overlaps() -> None:
    """Overlap branch: a candidate must beat EVERY overlapping incumbent, not just one."""
    tracker = TopNTracker(n=4, j_max=0.30)
    a = _make_solution(_A_EDGES, objective=200.0)
    b = _make_solution(_B_EDGES, objective=500.0)
    tracker.consider(a)
    tracker.consider(b)
    assert {a, b} == set(tracker.current_top())

    # Overlaps both; beats a (300 > 200) but NOT b (300 < 500) → rejected, nothing evicted.
    cand = _make_solution(_CAND_EDGES, objective=300.0)

    admitted = tracker.consider(cand)

    assert admitted is False
    top = tracker.current_top()
    assert a in top and b in top
    assert cand not in top


# ----------------------------------------------------------------------------
# Non-finite objective guard
# ----------------------------------------------------------------------------


def test_consider_rejects_nan_objective() -> None:
    """A NaN objective is a programming error — fail loud rather than poison the tracker."""
    tracker = TopNTracker(n=3, j_max=0.30)
    nan_sol = _make_solution([(0, 1, 0)], objective=float("nan"))

    with pytest.raises(ValueError, match="must be finite"):
        tracker.consider(nan_sol)


def test_consider_rejects_infinite_objective() -> None:
    """An inf objective is rejected at the boundary (would dominate + poison total_objective)."""
    tracker = TopNTracker(n=3, j_max=0.30)
    inf_sol = _make_solution([(0, 1, 0)], objective=float("inf"))

    with pytest.raises(ValueError, match="must be finite"):
        tracker.consider(inf_sol)


# ----------------------------------------------------------------------------
# AC #3 — hypothesis property tests on jaccard_distance
# ----------------------------------------------------------------------------

# Bounded alphabet so generated edge-sets actually overlap a non-trivial fraction
# of the time. Without bounding, near-every random pair is trivially disjoint
# and the symmetry / range checks pass vacuously (Dev Notes guidance).
_NODE_RANGE = st.integers(min_value=0, max_value=8)
_KEY_RANGE = st.integers(min_value=0, max_value=2)
_EDGE_ID = st.tuples(_NODE_RANGE, _NODE_RANGE, _KEY_RANGE)
_OBJECTIVE = st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False)


@st.composite
def _solution_strategy(draw: st.DrawFn, min_edges: int = 0, max_edges: int = 8) -> Solution:
    """Draw a `Solution` with unique-by-canonical-id edges over the bounded alphabet."""
    edge_ids = draw(st.lists(_EDGE_ID, min_size=min_edges, max_size=max_edges, unique=True))
    objective = draw(_OBJECTIVE)
    return _make_solution(edge_ids, objective)


@given(_solution_strategy(), _solution_strategy())
@settings(max_examples=50)
def test_jaccard_distance_is_symmetric(a: Solution, b: Solution) -> None:
    assert jaccard_distance(a, b) == jaccard_distance(b, a)


@given(_solution_strategy(min_edges=1))
@settings(max_examples=50)
def test_jaccard_distance_of_solution_with_itself_is_zero(a: Solution) -> None:
    # `min_edges=1` forces the non-empty identity path (|E∩E| / |E∪E| = 1.0 exact)
    # rather than the `not union` short-circuit. The both-empty case (also 0.0)
    # is pinned separately by `test_jaccard_distance_of_two_empty_solutions_is_zero`.
    assert a.edges  # guard: the strategy must not hand us an empty solution here
    assert jaccard_distance(a, a) == 0.0


@given(_solution_strategy(), _solution_strategy())
@settings(max_examples=50)
def test_jaccard_distance_value_is_in_unit_interval(a: Solution, b: Solution) -> None:
    d = jaccard_distance(a, b)
    assert 0.0 <= d <= 1.0


def test_jaccard_distance_of_two_empty_solutions_is_zero() -> None:
    """Edge case: 0/0 trap explicitly defined as `0.0` (identical empty sets)."""
    empty_a = Solution(edges=(), objective=0.0)
    empty_b = Solution(edges=(), objective=0.0)

    assert jaccard_distance(empty_a, empty_b) == 0.0


def test_jaccard_distance_of_empty_vs_nonempty_is_one() -> None:
    """Empty vs non-empty: intersection empty, union non-empty → distance 1.0."""
    empty = Solution(edges=(), objective=0.0)
    full = _make_solution([(0, 1, 0)], objective=10.0)

    assert jaccard_distance(empty, full) == 1.0


def test_jaccard_distance_disjoint_edge_sets_is_one() -> None:
    a = _make_solution([(0, 1, 0), (1, 2, 0)], objective=100.0)
    b = _make_solution([(10, 11, 0), (11, 12, 0)], objective=100.0)

    assert jaccard_distance(a, b) == 1.0


def test_jaccard_distance_uses_canonical_edge_identity_not_metrics() -> None:
    """Two `Edge` values sharing (node_u, node_v, key) but differing on metrics collapse."""
    # Same (u, v, k) triple but different length_m — Jaccard ignores metrics.
    same_id_diff_metrics_a = Edge(
        node_u=0,
        node_v=1,
        key=0,
        length_m=100.0,
        d_plus_m=25.0,
        d_minus_m=0.0,
        avg_gradient=0.25,
        sac_scale="hiking",
    )
    same_id_diff_metrics_b = Edge(
        node_u=0,
        node_v=1,
        key=0,
        length_m=200.0,
        d_plus_m=50.0,
        d_minus_m=0.0,
        avg_gradient=0.25,
        sac_scale="demanding_mountain_hiking",
    )
    sol_a = Solution(edges=(same_id_diff_metrics_a,), objective=10.0)
    sol_b = Solution(edges=(same_id_diff_metrics_b,), objective=20.0)

    assert jaccard_distance(sol_a, sol_b) == 0.0


# ----------------------------------------------------------------------------
# AC #3 — admission order-independence on sufficiently-distinct inputs
# ----------------------------------------------------------------------------


def _make_disjoint_solutions(n_solutions: int) -> list[Solution]:
    """Build n solutions whose edge-sets are pairwise disjoint (distance = 1.0)."""
    return [
        _make_solution(
            [(1000 * i, 1000 * i + 1, 0), (1000 * i + 1, 1000 * i + 2, 0)],
            objective=float(100 * (i + 1)),
        )
        for i in range(n_solutions)
    ]


def _make_sharing_but_distinct_solutions(n_solutions: int) -> list[Solution]:
    """Build n solutions that SHARE one common hub edge yet stay above the j_max=0.30 threshold.

    Each solution = 1 shared hub edge + 3 unique edges. Any pair shares exactly
    1 of 7 union edges → similarity 1/7 ≈ 0.143 < 0.30 → distance ≈ 0.857 > the
    `1 - j_max = 0.70` overlap threshold. So they are "sufficiently distinct"
    (no candidate ever triggers an overlap rejection) BUT not disjoint — this
    forces `consider`'s overlap list-comprehension to compute a real, non-1.0
    distance and decide "not overlapping", exercising the branch that
    `_make_disjoint_solutions` (all distances = 1.0) never reaches.
    """
    hub: tuple[int, int, int] = (9999, 9998, 0)
    return [
        _make_solution(
            [
                hub,
                (1000 * i, 1000 * i + 1, 0),
                (1000 * i + 1, 1000 * i + 2, 0),
                (1000 * i + 2, 1000 * i + 3, 0),
            ],
            objective=float(100 * (i + 1)),
        )
        for i in range(n_solutions)
    ]


@given(
    st.integers(min_value=2, max_value=8),
    st.integers(min_value=1, max_value=8),
    st.booleans(),
)
@settings(max_examples=30)
def test_admission_order_independent_for_sufficiently_distinct_solutions(
    capacity: int, count: int, sharing: bool
) -> None:
    """Sufficiently-distinct candidates → top-N membership is a function of input, not order.

    `sharing` toggles between fully-disjoint inputs and inputs that share a hub
    edge (still above the threshold). The sharing case is what actually exercises
    the overlap-distance computation in `consider`.
    """
    solutions = (
        _make_sharing_but_distinct_solutions(count) if sharing else _make_disjoint_solutions(count)
    )
    reversed_solutions = list(reversed(solutions))

    t1 = TopNTracker(n=capacity, j_max=0.30)
    t2 = TopNTracker(n=capacity, j_max=0.30)
    for s in solutions:
        t1.consider(s)
    for s in reversed_solutions:
        t2.consider(s)

    # In the sufficiently-distinct regime no candidate is ever rejected for
    # overlap, so admission reduces to top-N-by-objective — order-independent in
    # MEMBERSHIP (this is exactly AC #3's scoped claim). `current_top()` is sorted
    # so the lists are equal too; we compare as sets to make the membership claim
    # explicit. NB: this is NOT a general order-independence guarantee — on
    # overlapping inputs the greedy filter is order-dependent. FR29 reproducibility
    # rests on the solver feeding a deterministic seed-derived sequence, not on
    # this property.
    assert set(t1.current_top()) == set(t2.current_top())
    # total_objective over the same membership; `isclose` because float addition
    # order differs between the two trackers (not byte-identical, and it needn't be).
    assert math.isclose(t1.total_objective(), t2.total_objective(), abs_tol=1e-9)


# ----------------------------------------------------------------------------
# AC #4 — purity / no shared mutable state
# ----------------------------------------------------------------------------


def test_consider_is_pure_under_fresh_tracker_replay() -> None:
    """Feeding the same sequence to two fresh trackers yields identical state."""
    sequence = [
        _make_solution([(0, 1, 0), (1, 2, 0)], objective=300.0),
        _make_solution([(0, 1, 0), (1, 2, 0), (2, 3, 0)], objective=250.0),  # overlaps[0]
        _make_solution([(10, 11, 0), (11, 12, 0)], objective=200.0),
        _make_solution([(20, 21, 0)], objective=400.0),
    ]

    t1 = TopNTracker(n=2, j_max=0.30)
    t2 = TopNTracker(n=2, j_max=0.30)
    decisions_1 = [t1.consider(s) for s in sequence]
    decisions_2 = [t2.consider(s) for s in sequence]

    assert decisions_1 == decisions_2
    assert t1.current_top() == t2.current_top()
    assert math.isclose(t1.total_objective(), t2.total_objective(), abs_tol=1e-9)


def test_consider_does_not_mutate_input_solution() -> None:
    """`Solution` is frozen — the tracker must not attempt mutation (defensive smoke test)."""
    tracker = TopNTracker(n=2, j_max=0.30)
    sol = _make_solution([(0, 1, 0), (1, 2, 0)], objective=100.0)
    before_edges = sol.edges
    before_objective = sol.objective

    tracker.consider(sol)

    # Frozen + slots already guarantees this, but the assertion makes the
    # purity contract explicit at the test layer.
    assert sol.edges is before_edges
    assert sol.objective == before_objective
