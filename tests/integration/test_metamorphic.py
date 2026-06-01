# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: networkx operations on `ContractedGraph.graph` surface as Unknown — same
# boundary pattern as `pipeline/` modules, `exhaustive_oracle.py`, and
# `test_solver_on_toy_graph.py`. `conftest` is imported as a top-level module
# because pytest's `prepend` mode puts the test dir on sys.path — same shape as
# `from exhaustive_oracle import ...` in `test_solver_on_toy_graph.py`.
"""Metamorphic invariants for the GRASP solver — the 8 logical relations from
PRD Appendix A(b) (Architecture §Cat 11a/11c).

Metamorphic testing catches logical bugs that unit tests miss — inverted Jaccard,
broken seed threading, wrong objective direction, node-ID order leaking into the
result — by transforming a solved instance in a controlled way and asserting the
expected monotonicity / equality on the re-solved instance, *without* needing to
know the absolute optimum.

Why the fixture is small and shallow
=====================================

GRASP is a heuristic, so most of these invariants are properties of the *optimum*,
not of an arbitrary heuristic run: relaxing a filter (θ, difficulty cap) or adding
an edge changes the restricted-candidate-list contents and therefore the
seed-driven walk, so GRASP's best is only guaranteed monotone if GRASP actually
reaches the optimum on both sides. We therefore use a deliberately small, sparse
graph (`num_layers=5`, `layer_width=2`, `density=0.4` → 10 nodes) — the *opposite*
of the Story 3.7 quality gate, which deepened the graph to make GRASP suboptimal.
On this shape GRASP reaches the exhaustive optimum on every seed (verified against
`enumerate_best` during design), so the monotonicity/equality relations hold
deterministically rather than flakily.

Three invariants hold regardless of optimality and would pass on any fixture:
`increase_iter_budget` (same seed → the first N iterations are identical and the
tracker only accumulates), `duplicate_seed` (FR29 byte-identical reproducibility),
and `relax_j_max` on the top-1 objective (the highest-objective constructed route
is always admitted first with nothing higher to overlap-reject it, so the top-1
objective is `j_max`-independent — see `distinctness.TopNTracker`).

`pytest.skip`/`xfail` are forbidden here (Architecture §Cat 11c — pass-required).
"""

from __future__ import annotations

import math

import networkx as nx
import numpy as np
import pytest
from conftest import make_toy_contracted_graph, make_toy_solver_params

from steeproute.models import ContractedGraph, Edge, Solution, SolverParams
from steeproute.solver.grasp import GraspSolver

# --- Fixture shape: small + sparse so GRASP == the exhaustive optimum on every seed.
_NUM_LAYERS = 5
_LAYER_WIDTH = 2
_DENSITY = 0.4
# Generator seeds tuned (vs `enumerate_best`) so GRASP is optimal AND every
# relaxation below is non-vacuous (the base config genuinely filters edges the
# relaxed config admits).
_SEEDS: tuple[int, ...] = (20, 21, 24, 25, 26)
# High enough that GRASP reliably reaches the optimum on this 10-node graph;
# each run is sub-second, keeping the whole suite far under the ≤2 min budget.
_ITER_BUDGET = 5000


def _base_graph(seed: int) -> ContractedGraph:
    """The shared small/sparse toy graph (GRASP reaches the optimum here)."""
    return make_toy_contracted_graph(
        seed, num_layers=_NUM_LAYERS, layer_width=_LAYER_WIDTH, density=_DENSITY
    )


def _params(**overrides: object) -> SolverParams:
    """Default toy `SolverParams` with `_ITER_BUDGET`, plus any per-test override.

    `iter_budget` defaults to `_ITER_BUDGET` but an explicit override wins (the
    iter-budget invariant supplies its own small/large budgets).
    """
    overrides.setdefault("iter_budget", _ITER_BUDGET)
    return make_toy_solver_params(**overrides)  # pyright: ignore[reportArgumentType]


def _run(graph: ContractedGraph, params: SolverParams) -> list[Solution]:
    """Run GRASP with a fresh seeded RNG (sharing a Generator bleeds state)."""
    return GraspSolver(graph, params, np.random.default_rng(params.seed)).run()


def _best_objective(graph: ContractedGraph, params: SolverParams) -> float:
    """Top-1 objective off the tracker-admitted result (no re-rank / post-filter)."""
    result = _run(graph, params)
    assert result, "GRASP returned no routes — expected >= 1 feasible route for this fixture/params"
    return result[0].objective


# --------------------------------------------------------------------------- #
# Graph transforms — each builds a NEW ContractedGraph (the dataclasses are
# frozen=True, slots=True; never mutate the shared fixture).
# --------------------------------------------------------------------------- #


def _with_added_edge(graph: ContractedGraph) -> ContractedGraph:
    """Add one feasible, high-objective super-edge from the first to the last spine node.

    A large `d_plus_m` makes a route through the new edge strictly better than any
    existing route, so the optimum can only rise — exercising the "adding an edge
    never decreases the best objective" invariant non-vacuously.
    """
    g: nx.MultiDiGraph = graph.graph.copy()
    u, v = 0, (_NUM_LAYERS - 1) * _LAYER_WIDTH  # column-0 node of first / last layer
    length_m, d_plus_m, d_minus_m = 500.0, 2000.0, 10.0
    avg_gradient = (d_plus_m + d_minus_m) / length_m
    key = int(
        g.add_edge(
            u,
            v,
            length_m=length_m,
            d_plus_m=d_plus_m,
            d_minus_m=d_minus_m,
            avg_gradient=avg_gradient,
            sac_scale="hiking",
        )
    )
    super_edge_to_base = dict(graph.super_edge_to_base)
    super_edge_to_base[(u, v, key)] = (
        Edge(u, v, key, length_m, d_plus_m, d_minus_m, avg_gradient, "hiking"),
    )
    return ContractedGraph(graph=g, super_edge_to_base=super_edge_to_base)


def _with_scaled_elevation(graph: ContractedGraph, k: float) -> ContractedGraph:
    """Scale every edge's elevation attributes (`d_plus_m`, `d_minus_m`, `avg_gradient`) by `k`.

    The objective is `sum(d_plus_m + d_minus_m)`, so it is linear in elevation —
    but the θ filter reads `avg_gradient` off the nx-graph edge data, so the caller
    must also scale `theta` by `k` to keep feasibility (and hence the chosen route)
    invariant. `super_edge_to_base` is left as-is: the solver only consumes it for
    super-edge *membership*, not its metrics (those come from the nx-graph data).
    """
    g: nx.MultiDiGraph = graph.graph.copy()
    for _u, _v, _key, data in g.edges(keys=True, data=True):
        data["d_plus_m"] *= k
        data["d_minus_m"] *= k
        data["avg_gradient"] *= k
    return ContractedGraph(graph=g, super_edge_to_base=graph.super_edge_to_base)


def _relabelled(graph: ContractedGraph, offset: int) -> ContractedGraph:
    """Relabel every node id `n -> n + offset` (a bijection) on graph + back-mapping.

    The objective is a label-independent elevation sum, so the best objective must
    be byte-identical to the original; any difference means node-id ordering leaked
    into the result.
    """
    mapping = {node: node + offset for node in graph.graph.nodes}
    g = nx.relabel_nodes(graph.graph, mapping, copy=True)
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {}
    for (u, v, key), edges in graph.super_edge_to_base.items():
        super_edge_to_base[(mapping[u], mapping[v], key)] = tuple(
            Edge(
                mapping[e.node_u],
                mapping[e.node_v],
                e.key,
                e.length_m,
                e.d_plus_m,
                e.d_minus_m,
                e.avg_gradient,
                e.sac_scale,
            )
            for e in edges
        )
    return ContractedGraph(graph=g, super_edge_to_base=super_edge_to_base)


# --------------------------------------------------------------------------- #
# Monotonicity invariants — relaxing a constraint never decreases the best
# objective. Each is non-vacuous: the relaxation demonstrably changes the result
# on these tuned seeds.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", _SEEDS)
def test_relax_theta_objective_non_decreasing(seed: int) -> None:
    """Lowering the slope floor θ admits more super-edges → best objective must not drop."""
    graph = _base_graph(seed)
    # Base theta=0.45 is above the factory's spine-feasibility guarantee (theta<=0.25),
    # so the spine is severed here; base feasibility comes from non-super/connector edges
    # and is verified non-empty on every seed in _SEEDS. theta>0.25 is required for
    # non-vacuity — all super-edges have avg_gradient>=0.25, so a lower base wouldn't filter any.
    old_theta, new_theta = 0.45, 0.25
    old_obj = _best_objective(graph, _params(theta=old_theta))
    new_obj = _best_objective(graph, _params(theta=new_theta))
    assert new_obj >= old_obj, (
        f"seed {seed}: relaxing theta {old_theta}->{new_theta} dropped objective "
        f"{old_obj}->{new_obj}"
    )
    assert new_obj > old_obj, (
        f"seed {seed}: relaxing theta {old_theta}->{new_theta} was a no-op "
        f"({old_obj}=={new_obj}) — test is vacuous, retune the fixture/seed"
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_relax_j_max_objective_non_decreasing(seed: int) -> None:
    """Raising the Jaccard ceiling j_max relaxes distinctness → best objective must not drop.

    The top-1 objective is `j_max`-independent (the global-best route is always
    admitted — nothing higher exists to overlap-reject it), so this holds as
    equality. Non-vacuity is shown via the held-set *total* objective, which the
    relaxation genuinely increases (more / closer routes survive the filter).
    """
    graph = _base_graph(seed)
    old_j_max, new_j_max = 0.0, 1.0  # fully-disjoint required -> distinctness disabled
    old_result = _run(graph, _params(j_max=old_j_max))
    new_result = _run(graph, _params(j_max=new_j_max))
    assert old_result and new_result, f"seed {seed}: GRASP returned no routes"
    old_top, new_top = old_result[0].objective, new_result[0].objective
    assert new_top >= old_top, (
        f"seed {seed}: relaxing j_max {old_j_max}->{new_j_max} dropped the best "
        f"objective {old_top}->{new_top}"
    )
    old_total = sum(s.objective for s in old_result)
    new_total = sum(s.objective for s in new_result)
    assert new_total > old_total, (
        f"seed {seed}: relaxing j_max {old_j_max}->{new_j_max} did not enrich the "
        f"top-N set (total {old_total}=={new_total}) — test is vacuous"
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_relax_difficulty_cap_objective_non_decreasing(seed: int) -> None:
    """Raising the SAC difficulty cap admits harder edges → best objective must not drop."""
    graph = _base_graph(seed)
    old_cap, new_cap = "T1", "T4"  # hiking-only -> up to alpine_hiking
    old_obj = _best_objective(graph, _params(difficulty_cap=old_cap))
    new_obj = _best_objective(graph, _params(difficulty_cap=new_cap))
    assert new_obj >= old_obj, (
        f"seed {seed}: relaxing difficulty_cap {old_cap}->{new_cap} dropped objective "
        f"{old_obj}->{new_obj}"
    )
    assert new_obj > old_obj, (
        f"seed {seed}: relaxing difficulty_cap {old_cap}->{new_cap} was a no-op "
        f"({old_obj}=={new_obj}) — test is vacuous, retune the fixture/seed"
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_increase_iter_budget_objective_non_decreasing(seed: int) -> None:
    """More GRASP iterations never decrease the best objective.

    Robust regardless of optimality: with the same RNG seed the first N iterations
    of the larger budget are identical to the N-budget run, and the tracker only
    accumulates, so `best(M) >= best(N)` for `M > N`.
    """
    graph = _base_graph(seed)
    small, large = 5, 10  # N, 2N — AC #3's best(2N) >= best(N) relation
    old_obj = _best_objective(graph, _params(iter_budget=small))
    new_obj = _best_objective(graph, _params(iter_budget=large))
    assert new_obj >= old_obj, (
        f"seed {seed}: raising iter_budget {small}->{large} dropped objective {old_obj}->{new_obj}"
    )


# --------------------------------------------------------------------------- #
# Scaling / equality invariants.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", _SEEDS)
def test_scale_elevation_objective_scales_proportionally(seed: int) -> None:
    """Scaling all elevation by k scales the best objective by exactly k.

    `theta` is scaled by the same `k` so the θ filter outcome is invariant
    (`avg_gradient*k >= theta*k` iff `avg_gradient >= theta`); positive scaling
    preserves the RCL `(d+ + d-)`-descending order too, so the identical route is
    chosen and its objective scales by exactly `k`.
    """
    graph = _base_graph(seed)
    k = 2.5
    base_params = _params()
    old_obj = _best_objective(graph, base_params)
    scaled_obj = _best_objective(
        _with_scaled_elevation(graph, k), _params(theta=base_params.theta * k)
    )
    assert math.isclose(scaled_obj, k * old_obj, rel_tol=1e-9), (
        f"seed {seed}: scaling elevation by {k} gave objective {scaled_obj}, expected {k * old_obj}"
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_adding_edge_objective_non_decreasing(seed: int) -> None:
    """Adding an edge never decreases the best objective (more options available)."""
    graph = _base_graph(seed)
    params = _params()
    old_obj = _best_objective(graph, params)
    new_obj = _best_objective(_with_added_edge(graph), params)
    assert new_obj >= old_obj, f"seed {seed}: adding an edge dropped objective {old_obj}->{new_obj}"
    assert new_obj > old_obj, (
        f"seed {seed}: adding a strictly-better edge was a no-op "
        f"({old_obj}=={new_obj}) — test is vacuous"
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_graph_isomorphism_objective_identical(seed: int) -> None:
    """Relabelling node ids leaves the best objective byte-identical (catches id-order bugs)."""
    graph = _base_graph(seed)
    params = _params()
    old_obj = _best_objective(graph, params)
    # Exact `==` (not math.isclose): a label-independent sum must reproduce bit-for-bit;
    # isclose would mask exactly the id-order drift this invariant guards.
    new_obj = _best_objective(_relabelled(graph, offset=1000), params)
    assert new_obj == old_obj, (
        f"seed {seed}: relabelling node ids changed the best objective "
        f"{old_obj}->{new_obj} — node-id ordering is leaking into the result"
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_duplicate_seed_identical_result(seed: int) -> None:
    """Two runs with the same seed are byte-identical — objective AND edge sequence (FR29).

    A toy-fixture cross-check of the determinism contract pinned on the real
    graph by `test_grasp_reproducible.py`. Each run gets a fresh
    `numpy.random.default_rng(params.seed)`.
    """
    graph = _base_graph(seed)
    params = _params()
    result_a = _run(graph, params)
    result_b = _run(graph, params)
    assert len(result_a) == len(result_b), (
        f"seed {seed}: different result lengths {len(result_a)} vs {len(result_b)}"
    )
    for i, (sol_a, sol_b) in enumerate(zip(result_a, result_b, strict=True)):
        assert sol_a.objective == sol_b.objective, (
            f"seed {seed}: route {i} objectives diverge ({sol_a.objective} vs {sol_b.objective})"
        )
        ids_a = [(e.node_u, e.node_v, e.key) for e in sol_a.edges]
        ids_b = [(e.node_u, e.node_v, e.key) for e in sol_b.edges]
        assert ids_a == ids_b, f"seed {seed}: route {i} edge sequences diverge"
