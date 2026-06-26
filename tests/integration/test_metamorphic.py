# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportImplicitRelativeImport=false
# Reason: networkx operations on `ContractedGraph.graph` surface as Unknown — same
# boundary pattern as `pipeline/` modules, `exhaustive_oracle.py`, and
# `test_solver_on_toy_graph.py`. `conftest` is imported as a top-level module
# because pytest's `prepend` mode puts the test dir on sys.path — same shape as
# `from exhaustive_oracle import ...` in `test_solver_on_toy_graph.py`.
"""Metamorphic invariants for the GRASP solver — the 8 logical relations from
PRD Appendix A(b) (Architecture §Cat 11a/11c), plus the FR32 descent-cap relation
(Story 10.2).

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

Why there is no `min_climb_slope` invariant
============================================

These 8 invariants are the complete set for the GRASP solver. There is
deliberately no `relax_min_climb_slope` relation: `min_climb_slope` is a
climb-*detection* threshold consumed by `detect_climbs` (pipeline stage 8),
which runs *upstream* of the `ContractedGraph` this suite builds directly — the
solver never reads it. Varying it here would leave every objective identical, so
the invariant would be vacuous (a strict-gain guard would simply fail). The
detection-side monotonicity of `min_climb_slope` belongs to a climb-detection
test, not to this solver-level suite. The `scale_elevation` invariant still
co-scales it for intent (see that test), but the result is unaffected.

Why there is no `l_connector` invariant (Story 5.3)
===================================================

The Section-4C sprint-change proposal floated a `raise l_connector → best
objective non-decreasing` invariant. It is deliberately **omitted** here for the
same structural reason as `min_climb_slope`: `l_connector` is a *contraction-time*
reuse-exemption threshold consumed only by `pipeline/graph.py::contract_climbs`,
which tags each contracted edge with a `reusable` flag (`length_m < l_connector`)
and an undirected `base_segment_id`. The solver derives its reuse-exemption set
purely from those per-edge tags (`solver/reuse.py`) and never reads
`SolverParams.l_connector`. This suite builds a `ContractedGraph` **directly**,
bypassing `contract_climbs`, so varying `l_connector` is inert — the invariant
would be vacuous (a strict-gain guard would simply fail). Raising it would
require routing a fixture through `contract_climbs`, which is a contraction-level
concern, not a solver-level one.

The undirected-reuse *behaviour* (out-and-back rejected, exempt connector may
recur, undirected `base_segment_id` enforced in either direction) is proven by
the dedicated solver/oracle/validator unit tests and the real-Grenoble-fixture
test added in Story 5.2 — not here. This suite stays on the toy factory's
*directed* per-edge tags (`conftest.py`) on purpose: that keeps its feasible set
bit-identical to pre-5.2 so the 8 objective-monotonicity invariants below (which
are orthogonal to reuse identity) and the Story 3.7 quality gate are unperturbed.
The two invariants that *do* touch the base-segment identity — node-relabel
isomorphism and add-edge monotonicity — exercise it explicitly (the relabel
transform remaps the identity tuples; the add-edge transform tags the new edge
with a fresh, non-colliding id so it cannot retro-block an existing segment).

Why the descent-cap invariant uses its own fixture (Story 10.2)
==============================================================

The 9th relation — `relax --max-descent-slope (raise the cap) → best objective
non-decreasing` — needs a graph that actually has a *descent* to cap. The toy
factory models every super-edge as a net climb (`d_minus_m` is a small fraction of
`d_plus_m`), so no traversal is a descent and the cap would be inert (vacuous). The
invariant therefore builds a tiny dedicated descent-bearing `ContractedGraph`
(`_descent_graph`) where the highest-objective route descends a steep segment: a
tight cap blocks that descent and forces a lower-objective uphill alternative, a
loose cap admits it. GRASP reaches the optimum on this 4-node graph for every seed,
so the relation holds deterministically. Same direction as `relax_difficulty_cap`.

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
    # Tag the new edge with its own distinct directed base-segment id (matching
    # the toy factory's convention, `conftest.py`). A fresh `(u, v, key)` id can
    # never collide with an existing segment, so adding the edge cannot
    # retro-block any segment already used by a route — the add-edge
    # monotonicity invariant holds under undirected reuse for that reason, not by
    # accident of the untagged-edge directed fallback in `solver/reuse.py`.
    g.edges[u, v, key]["base_segment_id"] = frozenset({(u, v, key)})
    g.edges[u, v, key]["reusable"] = False
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
    # `relabel_nodes` remaps node KEYS but not the `(u, v, key)` tuples stored
    # inside each edge's `base_segment_id` frozenset — remap those too so the
    # base-segment identity is a faithful isomorph of the original. Without this
    # the tags would reference stale node ids; the objective stays identical only
    # because each toy edge is its own directed segment, but the relabelled graph
    # would not actually carry a relabel-invariant identity. Remapping makes the
    # "node-id ordering never leaks into the result" property hold for the reuse
    # identity as well as the objective.
    for _u, _v, _key, data in g.edges(keys=True, data=True):
        data["base_segment_id"] = frozenset(
            (mapping[a], mapping[b], k) for (a, b, k) in data["base_segment_id"]
        )
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


# Theta pair for the route-level relaxation. `θ` floors the WHOLE-route average
# (D+ + D−)/length, not individual super-edges, so lowering it admits a superset
# of routes and the best objective is monotone non-decreasing. old=0.45 keeps
# every seed feasible (verified non-empty); new=0.25 admits the unfiltered
# optimum. Non-vacuity is asserted at the suite level (see
# `test_relax_theta_binds_on_at_least_one_seed`) rather than per-seed: under
# route-level semantics the seeds' feasibility boundaries no longer coincide
# (seed 21 goes infeasible just above 0.45 while seed 26 only bends near 0.46 —
# disjoint), so no single θ pair makes a per-seed strict-drop guard bind on all
# five. The per-seed monotone (`>=`) invariant plus the suite-level strict-gain
# guard together pin the relation completely.
_RELAX_THETA_OLD, _RELAX_THETA_NEW = 0.45, 0.25


@pytest.mark.parametrize("seed", _SEEDS)
def test_relax_theta_objective_non_decreasing(seed: int) -> None:
    """Lowering the route-level slope floor θ admits a superset of routes → best objective must not drop."""
    graph = _base_graph(seed)
    old_obj = _best_objective(graph, _params(theta=_RELAX_THETA_OLD))
    new_obj = _best_objective(graph, _params(theta=_RELAX_THETA_NEW))
    assert new_obj >= old_obj, (
        f"seed {seed}: relaxing theta {_RELAX_THETA_OLD}->{_RELAX_THETA_NEW} dropped "
        f"objective {old_obj}->{new_obj}"
    )


def test_relax_theta_binds_on_at_least_one_seed() -> None:
    """Suite-level non-vacuity: the θ relaxation strictly raises the objective on some seed.

    Guards against the monotonicity test silently degrading into a tautology (e.g. if
    a future change made θ inert). A per-seed strict guard isn't viable under route-level
    semantics — see the `_RELAX_THETA_*` note — so the guarantee is asserted across the
    seed set instead.
    """
    strict_gains = [
        _best_objective(_base_graph(seed), _params(theta=_RELAX_THETA_NEW))
        > _best_objective(_base_graph(seed), _params(theta=_RELAX_THETA_OLD))
        for seed in _SEEDS
    ]
    assert any(strict_gains), (
        f"relaxing theta {_RELAX_THETA_OLD}->{_RELAX_THETA_NEW} was a no-op on every seed "
        f"in {_SEEDS} — the monotonicity test is vacuous, retune the fixture/seeds"
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

    Both slope parameters are co-scaled by the same `k` so feasibility is
    invariant. `theta` (the route-level floor) is scaled so the gate outcome is
    unchanged (`avg_gradient*k >= theta*k` iff `avg_gradient >= theta`); positive
    scaling also preserves the RCL `(d+ + d-)`-descending order, so the identical
    route is chosen and its objective scales by exactly `k`. `min_climb_slope` is
    co-scaled too for intent — though it is inert on this fixture (the solver
    never consumes it; it drives `detect_climbs`, upstream of the directly-built
    graph), so the co-scaling documents the semantics rather than affecting the
    result.
    """
    graph = _base_graph(seed)
    k = 2.5
    base_params = _params()
    old_obj = _best_objective(graph, base_params)
    scaled_obj = _best_objective(
        _with_scaled_elevation(graph, k),
        _params(theta=base_params.theta * k, min_climb_slope=base_params.min_climb_slope * k),
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


# --------------------------------------------------------------------------- #
# FR32 descent-cap monotonicity (Story 10.2) — dedicated descent fixture (the
# toy factory models no descents; see the module docstring).
# --------------------------------------------------------------------------- #


def _descent_graph() -> ContractedGraph:
    """A 4-node graph whose best route descends a steep, cappable segment.

    super 0→1 (uphill, obj 250) then either descent 1→2 (net loss, windowed grad
    0.70, obj 300) or super 1→3 (uphill, obj 100). Best route off the cap is
    [0→1, 1→2] (obj 550); a cap below 0.70 blocks the descent, leaving [0→1, 1→3]
    (obj 350).
    """
    g: nx.MultiDiGraph = nx.MultiDiGraph()

    def _add(
        u: int, v: int, *, length_m: float, d_plus_m: float, d_minus_m: float, grad: float
    ) -> Edge:
        avg = (d_plus_m + d_minus_m) / length_m
        g.add_edge(
            u,
            v,
            key=0,
            length_m=length_m,
            d_plus_m=d_plus_m,
            d_minus_m=d_minus_m,
            avg_gradient=avg,
            sac_scale="hiking",
            max_windowed_descent_grad=grad,
            base_segment_id=frozenset({(min(u, v), max(u, v), 0)}),
            reusable=False,
        )
        return Edge(u, v, 0, length_m, d_plus_m, d_minus_m, avg, "hiking")

    e01 = _add(0, 1, length_m=400.0, d_plus_m=250.0, d_minus_m=0.0, grad=0.0)
    e12 = _add(1, 2, length_m=400.0, d_plus_m=0.0, d_minus_m=300.0, grad=0.70)
    e13 = _add(1, 3, length_m=400.0, d_plus_m=100.0, d_minus_m=0.0, grad=0.0)
    return ContractedGraph(
        graph=g, super_edge_to_base={(0, 1, 0): (e01,), (1, 2, 0): (e12,), (1, 3, 0): (e13,)}
    )


def _descent_params(*, max_descent_slope: float | None, seed: int) -> SolverParams:
    return SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T5",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=5,
        area_cap=500.0,
        untagged_policy="include",
        seed=seed,
        iter_budget=_ITER_BUDGET,
        time_budget=3600.0,
        stagnation_iters=0,
        max_descent_slope=max_descent_slope,
    )


@pytest.mark.parametrize("seed", _SEEDS)
def test_relax_max_descent_slope_objective_non_decreasing(seed: int) -> None:
    """Raising the descent cap admits descents it previously forbade → best objective must not drop."""
    graph = _descent_graph()
    tight, loose = 0.45, 2.0  # 0.45 blocks the 0.70 descent; 2.0 admits everything
    old_obj = _best_objective(graph, _descent_params(max_descent_slope=tight, seed=seed))
    new_obj = _best_objective(graph, _descent_params(max_descent_slope=loose, seed=seed))
    assert new_obj >= old_obj, (
        f"seed {seed}: relaxing max_descent_slope {tight}->{loose} dropped objective "
        f"{old_obj}->{new_obj}"
    )
    assert new_obj > old_obj, (
        f"seed {seed}: relaxing max_descent_slope {tight}->{loose} was a no-op "
        f"({old_obj}=={new_obj}) — test is vacuous, retune the fixture/seed"
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
