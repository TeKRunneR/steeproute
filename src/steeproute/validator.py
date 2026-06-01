"""Runtime route validation (FR26-28) — Architecture §Cat 6.

Validation is a distinct stage between the solver and the output renderer
(§Cat 6a), not a `Route`-construction postcondition: failed routes must be
*produced and flagged*, never rejected (FR27-28). Validation failures are
therefore **data** (`RouteValidation` / `PairwiseViolation`), never raised
exceptions — the CLI computes the exit code off the returned `ValidatedRouteSet`
*after* all outputs are written (§Cat 6c).

The module is pure: no I/O, no module-level state, no input mutation.

Three public functions (§Cat 6d):

- `validate_route` — per-route constraints (slope floor, difficulty cap,
  edge-reuse, graph membership).
- `validate_set` — set-level pairwise Jaccard distinctness.
- `validate` — the orchestrator the CLI calls; converts `Solution`s to
  `Route`s and runs both layers.

Constraint semantics mirror the solver's construction filters so that
GRASP output validates by construction (a failure on real GRASP output signals
a *solver* bug, not a validator one):

- **Slope floor** is checked on **non-connector edges only** — i.e. super-edges
  (climbs), identified by membership in `ContractedGraph.super_edge_to_base`.
  Plain connectors carry their underlying trail gradient and are exempt, exactly
  as the RCL filter does (`solver/grasp.py` `_build_rcl`). Checking every edge
  would wrongly reject legitimate downhill connectors.
- **Difficulty cap** rejects an edge iff `max_sac_rank(sac_scale)` is a known
  rank above the parsed cap; `None` (untagged / unrecognized SAC) passes — same
  policy as the solver and the Story 3.5 oracle.
- **Edge-reuse limit** is the **edge-simple** invariant: each
  `(node_u, node_v, key)` identity may appear at most once in a route. The
  solver already guarantees this (edge-simple walks). The PRD lists "edge-reuse
  limit" as a named constraint and the §Cat 6 table attributes it to
  `--l-connector`, but `l_connector` is the connector *length* threshold
  enforced at graph contraction (Story 3.3 / FR5), not a per-route reuse count;
  the runtime constraint is the edge-simple re-check.
- **Graph membership** is a sanity check that every route edge exists in the
  operational contracted graph.

Jaccard identity is single-sourced from `solver/distinctness.py` (routes are
wrapped as transient `Solution`s and fed to `jaccard_distance`) so set-level
distinctness uses byte-identical semantics to `TopNTracker`'s admission:
two routes overlap iff their Jaccard *similarity* exceeds `j_max`
(equivalently `jaccard_distance < 1 - j_max`).
"""

from __future__ import annotations

from steeproute.models import (
    ConstraintViolation,
    ContractedGraph,
    Edge,
    PairwiseViolation,
    Route,
    RouteMetrics,
    RouteValidation,
    Solution,
    SolverParams,
    ValidatedRouteSet,
)
from steeproute.pipeline.osm import max_sac_rank, parse_difficulty_cap
from steeproute.solver.distinctness import jaccard_distance

__all__ = ["validate", "validate_route", "validate_set"]


def validate_route(route: Route, graph: ContractedGraph, params: SolverParams) -> RouteValidation:
    """Validate one route against every per-route constraint (§Cat 6d).

    Reads `route.edges` only; `route.validation` is the *output* of this check,
    not an input (the orchestrator builds the `Route` with the result). Returns
    a `RouteValidation` whose `passed` is `True` iff no constraint is violated.
    """
    return _validate_edges(route.edges, graph, params)


def validate_set(routes: list[Route], params: SolverParams) -> list[PairwiseViolation]:
    """Return one `PairwiseViolation` per route pair exceeding `j_max` (§Cat 6b).

    Iterates pairs in ascending `(a, b)` index order for deterministic output
    (FR29). A pair violates iff its Jaccard similarity is strictly greater than
    `params.j_max` — the exact complement of `TopNTracker`'s overlap test, so a
    set the tracker admitted yields no violations here.
    """
    overlap_threshold = 1.0 - params.j_max
    violations: list[PairwiseViolation] = []
    for a in range(len(routes)):
        for b in range(a + 1, len(routes)):
            distance = jaccard_distance(_as_solution(routes[a]), _as_solution(routes[b]))
            if distance < overlap_threshold:
                violations.append(
                    PairwiseViolation(
                        route_index_a=a,
                        route_index_b=b,
                        jaccard_observed=1.0 - distance,
                        jaccard_max=params.j_max,
                    )
                )
    return violations


def validate(
    solutions: list[Solution], graph: ContractedGraph, params: SolverParams
) -> ValidatedRouteSet:
    """Orchestrate validation: `Solution`s → validated `ValidatedRouteSet` (§Cat 6d).

    For each solution (in solver order, preserved for FR29) builds a `Route`
    with aggregate `RouteMetrics` and its per-route `RouteValidation`, then runs
    the set-level Jaccard check over the resulting routes.

    Raises `ValueError` on a zero-edge `Solution`: an empty route is illegal at
    the validator stage (see module docstring) — it would build a degenerate
    `passed=True` route and, paired with another empty route, trip the
    both-empty `jaccard_distance == 0.0` branch into a spurious distinctness
    violation. The solver never emits empty walks (`GraspSolver.run` discards
    them), so this only fires on an upstream bug — fail loud at the boundary,
    consistent with `TopNTracker`'s non-finite-objective guard.
    """
    routes: list[Route] = []
    for solution in solutions:
        if not solution.edges:
            raise ValueError("validate() received a zero-edge Solution; empty routes are illegal")
        edges = list(solution.edges)
        routes.append(
            Route(
                edges=edges,
                metrics=_route_metrics(edges),
                validation=_validate_edges(edges, graph, params),
            )
        )
    return ValidatedRouteSet(routes=routes, set_violations=validate_set(routes, params))


def _validate_edges(
    edges: list[Edge] | tuple[Edge, ...], graph: ContractedGraph, params: SolverParams
) -> RouteValidation:
    """Run the four per-route constraint checks over an edge sequence.

    Shared by `validate_route` (public, takes a `Route`) and `validate` (builds
    the `Route` from a `Solution`) so neither hits the frozen-dataclass
    chicken-and-egg of needing a `RouteValidation` to construct the `Route` it
    validates.
    """
    cap_rank = parse_difficulty_cap(params.difficulty_cap)
    super_edge_ids = graph.super_edge_to_base
    nx_graph = graph.graph
    violations: list[ConstraintViolation] = []

    # Per-edge checks run over *distinct* edge identities (first occurrence
    # preserved for deterministic order): a reused edge is one bad edge, not
    # two, so it must not emit duplicate slope/difficulty/membership violations.
    # Edge reuse itself is reported separately below.
    seen_ids: set[tuple[int, int, int]] = set()
    unique_edges: list[Edge] = []
    for edge in edges:
        edge_id = (edge.node_u, edge.node_v, edge.key)
        if edge_id not in seen_ids:
            seen_ids.add(edge_id)
            unique_edges.append(edge)

    for edge in unique_edges:
        edge_id = (edge.node_u, edge.node_v, edge.key)

        # Slope floor: non-connector (super-edge) climbs must clear θ.
        if edge_id in super_edge_ids and edge.avg_gradient < params.theta:
            violations.append(
                ConstraintViolation(
                    constraint_id="slope_floor",
                    detail=(
                        f"super-edge {edge_id} avg_gradient {edge.avg_gradient:.4f} "
                        f"is below the slope floor θ={params.theta}"
                    ),
                    numeric={"observed": edge.avg_gradient, "required": params.theta},
                )
            )

        # Difficulty cap: a known SAC rank above the cap is a violation.
        rank = max_sac_rank(edge.sac_scale)
        if rank is not None and rank > cap_rank:
            violations.append(
                ConstraintViolation(
                    constraint_id="difficulty_cap",
                    detail=(
                        f"edge {edge_id} sac_scale {edge.sac_scale!r} (rank {rank}) "
                        f"exceeds the difficulty cap {params.difficulty_cap} (rank {cap_rank})"
                    ),
                    numeric={"observed": float(rank), "required": float(cap_rank)},
                )
            )

        # Graph membership: every route edge must exist in the operational graph.
        if not nx_graph.has_edge(edge.node_u, edge.node_v, edge.key):
            violations.append(
                ConstraintViolation(
                    constraint_id="graph_membership",
                    detail=f"edge {edge_id} is not present in the operational contracted graph",
                    numeric={"observed": 0.0, "required": 1.0},
                )
            )

    # Edge-reuse limit: each edge identity may appear at most once (edge-simple).
    counts: dict[tuple[int, int, int], int] = {}
    for edge in edges:
        edge_id = (edge.node_u, edge.node_v, edge.key)
        counts[edge_id] = counts.get(edge_id, 0) + 1
    for edge_id, count in counts.items():
        if count > 1:
            violations.append(
                ConstraintViolation(
                    constraint_id="edge_reuse",
                    detail=(
                        f"edge {edge_id} is traversed {count} times; "
                        f"routes must be edge-simple (reuse limit 1)"
                    ),
                    numeric={"observed": float(count), "required": 1.0},
                )
            )

    return RouteValidation(passed=not violations, violations=violations)


def _route_metrics(edges: list[Edge]) -> RouteMetrics:
    """Aggregate per-edge metrics into a `RouteMetrics` (§Cat 6b / models docstring)."""
    length_m = sum((edge.length_m for edge in edges), 0.0)
    d_plus_m = sum((edge.d_plus_m for edge in edges), 0.0)
    d_minus_m = sum((edge.d_minus_m for edge in edges), 0.0)
    avg_gradient = d_plus_m / length_m if length_m > 0 else 0.0
    return RouteMetrics(
        length_m=length_m,
        d_plus_m=d_plus_m,
        d_minus_m=d_minus_m,
        avg_gradient=avg_gradient,
    )


def _as_solution(route: Route) -> Solution:
    """Wrap a `Route` as a transient `Solution` for `jaccard_distance` reuse.

    `jaccard_distance` keys on the canonical `(node_u, node_v, key)` identity and
    ignores `objective`, so wrapping with `objective=0.0` reuses the single
    source of Jaccard truth (`solver/distinctness.py`) without duplicating the
    edge-identity projection here.
    """
    return Solution(edges=tuple(route.edges), objective=0.0)
