# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: same osmnx / networkx boundary as tests/integration/test_graph_contraction_fixture.py;
# `reportImplicitRelativeImport` — `from conftest import ...` is the shape that resolves
# under pytest's prepend import mode (see test_oracle_correctness.py for the rationale).
"""GRASP FR29 reproducibility: same seed + same graph → byte-identical results.

Story 3.6 AC #6: two `GraspSolver` runs with two fresh
`numpy.random.default_rng(42)` instances on the same `ContractedGraph` and
identical `SolverParams` produce identical `list[Solution]` — same length, same
`Solution.objective` per entry, same `Solution.edges` traversal order.
Downstream golden-hash regressions (Story 5.1) hash the canonical edge-sequence
per route, so FR29 protects edge-set identity AND ordering.

The contracted graph comes from the shared session-scoped `grenoble_fixture`
(tests/integration/conftest.py), so this test isolates the solver's determinism
contract — any drift in the upstream setup chain would be a Story 2.x bug, not a
GRASP bug.
"""

from __future__ import annotations

import numpy as np
from conftest import (
    GRENOBLE_DIFFICULTY_CAP,
    GRENOBLE_J_MAX,
    GRENOBLE_L_CONNECTOR,
    GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
    GRENOBLE_SEED,
    GRENOBLE_THETA,
    GrenobleFixture,
)

from steeproute.models import SolverParams
from steeproute.solver.grasp import GraspSolver

_N = 3
_ITER_BUDGET = 50


def _params() -> SolverParams:
    return SolverParams(
        theta=GRENOBLE_THETA,
        min_climb_slope=GRENOBLE_THETA,
        difficulty_cap=GRENOBLE_DIFFICULTY_CAP,
        l_connector=GRENOBLE_L_CONNECTOR,
        min_climb_ground_length=GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=GRENOBLE_J_MAX,
        n=_N,
        area_cap=500.0,
        untagged_policy="include",
        seed=GRENOBLE_SEED,
        iter_budget=_ITER_BUDGET,
        # Story 7.2 made time/stagnation termination live; this test isolates
        # seed-determinism under iter-budget termination, so disable stagnation
        # and keep the wall-clock budget non-binding.
        time_budget=60.0,
        stagnation_iters=0,
    )


def test_grasp_two_runs_with_same_seed_are_byte_identical(
    grenoble_fixture: GrenobleFixture,
) -> None:
    """FR29 / NFR4: `--seed 42` produces identical edge-sets AND identical traversal orders.

    The downstream golden-hash regression (Story 5.1) hashes the canonical edge
    *sequence*, so this test pins both the multiset and the ordering. Each
    `GraspSolver` instance gets its own fresh `default_rng(42)` — sharing a
    Generator between runs would let state from the first run bleed into the
    second.
    """
    params = _params()
    contracted = grenoble_fixture.contracted
    result_a = GraspSolver(contracted, params, np.random.default_rng(GRENOBLE_SEED)).run()
    result_b = GraspSolver(contracted, params, np.random.default_rng(GRENOBLE_SEED)).run()

    assert len(result_a) == len(result_b), (
        f"different result lengths: {len(result_a)} vs {len(result_b)}"
    )
    for i, (sol_a, sol_b) in enumerate(zip(result_a, result_b, strict=True)):
        # Raw `==` (not `math.isclose`) is deliberate: FR29 promises
        # *byte-identical* reproducibility, so objectives must be bit-for-bit
        # equal. `math.isclose` would mask exactly the drift this test guards.
        assert sol_a.objective == sol_b.objective, (
            f"route {i}: objectives diverge ({sol_a.objective} vs {sol_b.objective})"
        )
        # Canonical edge identity sequence — same triples in the same order.
        ids_a = [(e.node_u, e.node_v, e.key) for e in sol_a.edges]
        ids_b = [(e.node_u, e.node_v, e.key) for e in sol_b.edges]
        assert ids_a == ids_b, f"route {i}: edge sequences diverge ({ids_a} vs {ids_b})"
