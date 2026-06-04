# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations on the MultiDiGraph surface as Unknown — same
# boundary pattern as `pipeline/` modules, `exhaustive_oracle.py`, and
# `test_oracle_correctness.py`.
"""Integration-layer shared fixtures and session hooks.

Two responsibilities:

1. **TLS trust store** — patches Python's `ssl` module (via the `truststore`
   package) to verify TLS using the operating system certificate store rather
   than `certifi`'s vendored bundle. This Just Works behind corporate
   TLS-intercepting proxies whose root CA is installed in the OS store but not
   in certifi, and is harmless on machines whose OS store mirrors certifi (the
   common case). Symmetric with `regenerate.py`, which does the same
   unconditionally.

2. **Programmatic toy-`ContractedGraph` factory** (`make_toy_contracted_graph`,
   exposed via the `toy_graph_factory` fixture) — the primary solver test
   fixture per Architecture §Cat 11b. Consumed by the GRASP-vs-exhaustive
   quality gate (`test_solver_on_toy_graph.py`, Story 3.7) and reusable by the
   metamorphic suite (Story 3.8). Fully seed-deterministic and decoupled from
   any real OSM/DEM snapshot, so it regenerates identically in CI forever.

   Sizing rationale: the exhaustive oracle (`exhaustive_oracle.enumerate_best`)
   is **exponential in the number of edge-simple forward paths**, so the binding
   constraint on the toy graph is edge density / layer width, NOT the node count.
   The defaults read as a "~20-30 node" fixture (Architecture §Cat 11b) but keep
   the graph sparse (low `density`, few back-edges) so the oracle terminates well
   within the Story 3.7 ≤60s CI budget across all parameterized seeds.

   **Tractability ceiling (measured, single seed, num_layers=8):** `density=0.45`
   (the committed default) → ~0.01s; `density=0.6` → ~1s; `layer_width=4` with
   `density=0.6` → ~17s. The blowup is steep and tracks path count, not edge
   count — a reuser (e.g. the Story 3.8 metamorphic suite) nudging `density`
   above ~0.5 or `layer_width` above 3 can blow the CI budget. Stay at or below
   the defaults unless you measure the oracle time for the new shape.
"""

from __future__ import annotations

from collections.abc import Callable

import networkx as nx
import numpy as np
import pytest
import truststore

from steeproute.models import ContractedGraph, Edge, SolverParams

truststore.inject_into_ssl()


# Topology defaults — num_layers * layer_width = 24 nodes. Empirically tuned
# (Story 3.7): deep enough (8 layers → optimal routes are long, and the
# guaranteed spine-to-spine cycle makes them longer) that GRASP is genuinely
# challenged — its best lands at ~0.93-1.0 of the true optimum across the
# committed seeds, giving the quality gate real teeth — while staying sparse
# enough that the exhaustive oracle finishes in well under the ≤60s CI budget
# (oracle ~0.04s total across 5 seeds; the full gate is ~4s, dominated by
# GRASP's iter_budget). The binding constraint is path count, not node count.
_DEFAULT_NUM_LAYERS = 8
_DEFAULT_LAYER_WIDTH = 3
_DEFAULT_DENSITY = 0.45
_DEFAULT_NUM_BACK_EDGES = 2

# SAC sampling pool, weighted toward in-bounds ranks (<= T3 = rank 3).
# "alpine_hiking" (rank 4) exceeds the default difficulty_cap and exercises the
# SAC filter on both GRASP and the oracle. The guaranteed spine forces "hiking"
# (rank 1), so a feasible route always survives the filters.
_SAC_POOL: tuple[str, ...] = (
    "hiking",
    "hiking",
    "hiking",
    "mountain_hiking",
    "demanding_mountain_hiking",
    "alpine_hiking",
)


def make_toy_contracted_graph(
    seed: int,
    *,
    num_layers: int = _DEFAULT_NUM_LAYERS,
    layer_width: int = _DEFAULT_LAYER_WIDTH,
    density: float = _DEFAULT_DENSITY,
    num_back_edges: int = _DEFAULT_NUM_BACK_EDGES,
    terrain_variance: float = 1.0,
) -> ContractedGraph:
    """Build a deterministic, seed-reproducible toy `ContractedGraph`.

    The graph is a sparse layered structure (`num_layers` layers of
    `layer_width` nodes) with forward edges between consecutive layers plus a
    few back-edges that introduce cycles. Every edge carries the complete,
    finite stage-7 attribute contract (`length_m`, `d_plus_m`, `d_minus_m`,
    `avg_gradient`, `sac_scale`) so no consumer hits a missing-key `KeyError`
    or a NaN-poisoned filter/sort (this is the consumer that owns the contract
    for the items deferred from Stories 3.5/3.6).

    Guarantees:

    - **>= 1 feasible route**: a "spine" along the column-0 node of each layer
      is built entirely from super-edges whose `avg_gradient` (= the construction
      gradient + d_minus_m/length_m, hence >= 0.25) clears any `theta <= 0.25`,
      with `sac_scale="hiking"` (rank 1) clearing any `difficulty_cap >= "T1"`.
      (The θ filter tests `avg_gradient`, not the raw construction gradient.)
    - **Cycles present**: each back-edge connects the column-0 spine node of a
      later layer to that of an earlier layer; combined with the spine's forward
      chain this guarantees a *traversable* directed cycle on every seed (the
      back-edge is a feasible super-edge), so both GRASP construction and the
      oracle DFS exercise the closed edge-simple-walk branch. Requires
      `num_back_edges >= 1` (the default is 2).
    - **Finite metrics**: every numeric attribute is a finite `float`; no
      NaN/inf is ever generated.

    Args:
        seed: RNG seed — same seed yields a byte-identical graph.
        num_layers / layer_width: topology size. Total nodes = product.
            `num_layers >= 2` and `layer_width >= 1` are required (a single layer
            has no spine edge).
        density: probability of each non-spine forward edge between adjacent
            layers. The primary knob for oracle tractability — see the module
            docstring's measured ceiling before raising it above the default.
        num_back_edges: count of cycle-introducing spine-to-spine back-edges.
        terrain_variance: scales the super-edge gradient spread (1.0 → gradients
            in [0.25, 0.55]); higher widens the objective range.

    Returns:
        A `ContractedGraph` whose `super_edge_to_base` maps every super-edge's
        `(node_u, node_v, key)` to a single-element base-`Edge` tuple.
    """
    if num_layers < 2:
        raise ValueError(
            f"num_layers must be >= 2 (got {num_layers}); a single layer has no spine edge"
        )
    if layer_width < 1:
        raise ValueError(f"layer_width must be >= 1 (got {layer_width})")
    rng = np.random.default_rng(seed)
    g: nx.MultiDiGraph = nx.MultiDiGraph()
    layers = [[layer * layer_width + i for i in range(layer_width)] for layer in range(num_layers)]
    for layer_nodes in layers:
        g.add_nodes_from(layer_nodes)
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]] = {}

    def add_edge(u: int, v: int, *, is_super: bool, sac: str | None) -> None:
        length_m = float(rng.uniform(150.0, 600.0))
        if is_super:
            gradient = float(rng.uniform(0.25, 0.25 + 0.30 * terrain_variance))
            d_plus_m = gradient * length_m
            d_minus_m = float(rng.uniform(0.0, 0.10 * length_m))
        else:
            gradient = float(rng.uniform(0.0, 0.15))
            d_plus_m = gradient * length_m
            d_minus_m = float(rng.uniform(0.0, 0.05 * length_m))
        avg_gradient = (d_plus_m + d_minus_m) / length_m
        key = int(
            g.add_edge(
                u,
                v,
                length_m=length_m,
                d_plus_m=d_plus_m,
                d_minus_m=d_minus_m,
                avg_gradient=avg_gradient,
                sac_scale=sac,
            )
        )
        # Story 5.1 reuse tags (read by the solver/oracle via `solver.reuse`).
        # Each synthetic edge is its own distinct base segment, keyed on its
        # DIRECTED `(u, v, key)`: the toy graph models no two edges as the same
        # physical trail, so opposite-direction edges between the same node pair
        # (e.g. a spine forward edge and a back-edge over the same layers) are
        # independent climbs that must NOT merge. Using the directed id keeps the
        # undirected once-only rule equal to the pre-5.2 directed edge-simple
        # feasible set, so the Story 3.7 quality gate and the Story 3.8
        # metamorphic invariants are unperturbed. Genuine forward/reverse
        # collisions (a climb and the reverse of its own trail) are covered by
        # the real-fixture test and the dedicated solver/oracle/validator units.
        g.edges[u, v, key]["base_segment_id"] = frozenset({(u, v, key)})
        g.edges[u, v, key]["reusable"] = False
        if is_super:
            super_edge_to_base[(u, v, key)] = (
                Edge(
                    node_u=u,
                    node_v=v,
                    key=key,
                    length_m=length_m,
                    d_plus_m=d_plus_m,
                    d_minus_m=d_minus_m,
                    avg_gradient=avg_gradient,
                    sac_scale=sac,
                ),
            )

    # 1) Guaranteed feasible spine (column-0 node of each layer).
    for layer in range(num_layers - 1):
        add_edge(layers[layer][0], layers[layer + 1][0], is_super=True, sac="hiking")

    # 2) Random forward edges between consecutive layers.
    for layer in range(num_layers - 1):
        for u in layers[layer]:
            for v in layers[layer + 1]:
                if u == layers[layer][0] and v == layers[layer + 1][0]:
                    continue  # spine edge already added above
                if rng.random() < density:
                    add_edge(
                        u,
                        v,
                        is_super=bool(rng.random() < 0.6),
                        sac=str(rng.choice(_SAC_POOL)),
                    )

    # 3) Cycle-introducing back-edges between spine (column-0) nodes. Routing
    #    both endpoints through the spine guarantees a *traversable* directed
    #    cycle on every seed: the spine forward-chains col-0 of dst_layer up to
    #    col-0 of src_layer, and this edge closes the loop. Built as a feasible
    #    super-edge ("hiking", gradient >= 0.25) so the closed walk clears the θ
    #    and SAC filters — otherwise the cycle would exist in the graph but be
    #    unreachable to GRASP / the oracle. `src_layer > dst_layer` always
    #    (src_layer >= num_layers//2 >= 1; dst_layer < src_layer), so no
    #    self-loop is created.
    for _ in range(num_back_edges):
        src_layer = int(rng.integers(num_layers // 2, num_layers))
        dst_layer = int(rng.integers(0, src_layer))
        add_edge(layers[src_layer][0], layers[dst_layer][0], is_super=True, sac="hiking")

    return ContractedGraph(graph=g, super_edge_to_base=super_edge_to_base)


def make_toy_solver_params(
    *,
    theta: float = 0.20,
    min_climb_slope: float | None = None,
    difficulty_cap: str = "T3",
    j_max: float = 0.30,
    n: int = 3,
    iter_budget: int = 20000,
    seed: int = 42,
) -> SolverParams:
    """Default `SolverParams` for the toy-graph solver tests.

    `theta=0.20` / `difficulty_cap="T3"` match the spine's always-feasible
    super-edges (avg_gradient >= 0.25, rank 1). `iter_budget=20000` is sized so
    GRASP reliably clears the 0.80 quality gate with margin (min ratio ~0.93
    across the committed seeds): the guaranteed feasible cycles make the optimum
    long enough that a smaller budget left seed-53/71 ratios near 0.81, too close
    to the floor. Each iteration is cheap on a 24-node graph (full gate ~4s
    across 5 seeds; the oracle is ~0.04s of that). The Epic 4 termination fields
    (`time_budget`, `stagnation_iters`) are inert here (the solver only honours
    `iter_budget` until Epic 4).

    `min_climb_slope` defaults to `theta` (both ship at 0.20). The GRASP solver
    never reads it — it drives `detect_climbs` (pipeline stage 8), which the
    directly-built `ContractedGraph` fixtures bypass — so the default coupling is
    harmless. Pass it explicitly to decouple the two (e.g. the `scale_elevation`
    invariant co-scales both to document intent).
    """
    return SolverParams(
        theta=theta,
        min_climb_slope=theta if min_climb_slope is None else min_climb_slope,
        difficulty_cap=difficulty_cap,
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=j_max,
        n=n,
        area_cap=500.0,
        untagged_policy="include",
        seed=seed,
        iter_budget=iter_budget,
        time_budget=60.0,
        stagnation_iters=0,
    )


@pytest.fixture
def toy_graph_factory() -> Callable[[int], ContractedGraph]:
    """Factory fixture → `make_toy_contracted_graph` (call with a generator seed)."""
    return make_toy_contracted_graph


@pytest.fixture
def toy_solver_params() -> SolverParams:
    """Default `SolverParams` shared by the toy-graph solver tests."""
    return make_toy_solver_params()
