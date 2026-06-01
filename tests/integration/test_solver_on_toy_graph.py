# pyright: reportImplicitRelativeImport=false
# Reason: pytest's default `prepend` import mode (no `__init__.py` under
# `tests/`) puts this file's parent dir on sys.path, so `from exhaustive_oracle
# import ...` is the import shape that resolves at runtime — same as
# `test_oracle_correctness.py`.
"""GRASP-vs-exhaustive CI quality gate (Story 3.7, Architecture §Cat 11c).

Runs the production `GraspSolver` and the brute-force `enumerate_best` oracle on
the *same* seeded programmatic toy `ContractedGraph` (from the
`toy_graph_factory` fixture in `conftest.py`) under identical `SolverParams`,
and fails if the GRASP/exhaustive objective ratio drops below
`QUALITY_THRESHOLD`. Both sides admit candidates through the same
`TopNTracker(n, j_max)` — GRASP inside `grasp.py`, the oracle inside
`exhaustive_oracle.py` — so the comparison is apples-to-apples: the test reads
`.objective` off the top-ranked tracker-admitted route on each side without any
re-ranking or post-filtering.

Parameterized across several generator seeds to catch generator-bias (a single
lucky/unlucky topology would not represent the ratio). `pytest.skip`/`xfail` are
forbidden here (Architecture §Cat 11c — pass-required gate).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from exhaustive_oracle import enumerate_best

from steeproute.models import ContractedGraph, Edge, SolverParams
from steeproute.solver.grasp import GraspSolver

QUALITY_THRESHOLD = 0.80
"""GRASP/exhaustive objective-ratio floor.

Initial target — tighten to 0.85-0.90 once a baseline is established
(Architecture §Cat 11c CI-gate table).
"""

# Distinct generator seeds — catches generator-bias from any single topology.
_GENERATOR_SEEDS: tuple[int, ...] = (11, 23, 37, 53, 71)


def _assert_edge_simple_walk(edges: tuple[Edge, ...]) -> None:
    """Assert `edges` is a non-empty edge-simple directed walk.

    Confirms both solver and oracle emit structurally valid routes even when the
    toy graph contains cycles (closes the closed-walk-semantics gap deferred
    from Story 3.5): consecutive edges share an endpoint and no
    `(node_u, node_v, key)` triple repeats.
    """
    assert edges, "route must be non-empty"
    seen: set[tuple[int, int, int]] = set()
    for i, edge in enumerate(edges):
        eid = (edge.node_u, edge.node_v, edge.key)
        assert eid not in seen, f"edge {eid} repeated at position {i}"
        seen.add(eid)
        if i > 0:
            prev = edges[i - 1]
            assert prev.node_v == edge.node_u, (
                f"walk discontinuity at position {i}: {prev.node_v} != {edge.node_u}"
            )


@pytest.mark.parametrize("seed", _GENERATOR_SEEDS)
def test_grasp_meets_quality_threshold(
    seed: int,
    toy_graph_factory: Callable[[int], ContractedGraph],
    toy_solver_params: SolverParams,
) -> None:
    """GRASP's best objective is within `QUALITY_THRESHOLD` of the true optimum."""
    graph = toy_graph_factory(seed)
    params = toy_solver_params

    grasp_result = GraspSolver(graph, params, np.random.default_rng(params.seed)).run()
    exhaustive_result = enumerate_best(graph, params, params.n)

    # Non-empty on both sides — the factory guarantees >= 1 feasible route, so
    # an empty-vs-empty vacuous pass is impossible.
    assert grasp_result, f"seed {seed}: GRASP returned no routes"
    assert exhaustive_result, f"seed {seed}: oracle returned no routes"

    grasp_best = grasp_result[0]
    exhaustive_best = exhaustive_result[0]
    _assert_edge_simple_walk(grasp_best.edges)
    _assert_edge_simple_walk(exhaustive_best.edges)

    # The oracle enumerates everything, so its best is the true optimum; GRASP
    # can only match or fall short of it.
    assert exhaustive_best.objective > 0.0, f"seed {seed}: degenerate optimum"
    ratio = grasp_best.objective / exhaustive_best.objective
    # Upper bound: GRASP must never beat the brute-force optimum. A ratio > 1
    # means GRASP emitted an infeasible over-objective route or the oracle is
    # not actually exhaustive — either is a real bug the lower bound can't catch.
    assert ratio <= 1.0 + 1e-9, (
        f"seed {seed}: GRASP objective {grasp_best.objective:.1f} exceeds the "
        f"exhaustive optimum {exhaustive_best.objective:.1f} (ratio {ratio:.3f}) — "
        f"oracle not exhaustive or GRASP route infeasible"
    )
    assert ratio >= QUALITY_THRESHOLD, (
        f"seed {seed}: GRASP/exhaustive quality ratio {ratio:.3f} below "
        f"threshold {QUALITY_THRESHOLD} "
        f"(grasp={grasp_best.objective:.1f}, exhaustive={exhaustive_best.objective:.1f})"
    )
