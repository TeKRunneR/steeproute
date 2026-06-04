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

- **Slope floor** (FR3) is a **route-level** constraint: the whole-route average
  gradient `(D+ + D−)/length` must clear θ. It is checked once per route against
  the aggregate `avg_gradient`, mirroring the solver's finalization gate
  (`solver/grasp.py` `_route_slope_ok`) so GRASP output validates by
  construction. Per-climb steepness is the separate `--min-climb-slope`
  detection threshold applied upstream in stage 8 — not a validator concern.
- **Difficulty cap** rejects an edge iff `max_sac_rank(sac_scale)` is a known
  rank above the parsed cap; `None` (untagged / unrecognized SAC) passes — same
  policy as the solver and the Story 3.5 oracle.
- **Edge-reuse limit** is the **undirected base-segment** once-only rule (FR5,
  Story 5.2): a non-exempt physical trail segment may appear at most once per
  route, regardless of direction. It keys on the `base_segment_id` tags Story
  5.1 wrote at contraction — a climb super-edge and the reverse-direction
  connectors of the same trail share an id — so ascending a climb and descending
  its reverse is a violation (the out-and-back). Exemption is per-id: an id is
  exempt iff every edge carrying it is `reusable` (a short connector
  `length_m < l_connector`), so repeated exempt short connectors are **not**
  flagged. The non-exempt id set and per-edge blocking ids are single-sourced
  with the solver and oracle through `solver.reuse`, so a GRASP-admitted route
  validates by construction. This realizes the originally-intended `--l-connector`
  semantics (a reuse-exemption threshold, per the §Cat 6 table and PRD FR5),
  replacing the earlier directed edge-simple re-check.
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
    route_avg_gradient,
)
from steeproute.pipeline.osm import max_sac_rank, parse_difficulty_cap
from steeproute.solver.distinctness import jaccard_distance
from steeproute.solver.reuse import blocking_ids, non_exempt_base_segment_ids

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
    nx_graph = graph.graph
    violations: list[ConstraintViolation] = []

    # Slope floor (FR3): the *whole-route* average gradient must clear θ. This is
    # a route-level constraint, not a per-edge one — a route may chain steep
    # climbs across flat valley connectors and still fail because its overall
    # (D+ + D−)/length dips below θ. Computed via `_route_metrics` so the
    # validator's slope check and the report's `avg_gradient` are single-sourced.
    avg_gradient = _route_metrics(list(edges)).avg_gradient
    if avg_gradient < params.theta:
        violations.append(
            ConstraintViolation(
                constraint_id="slope_floor",
                detail=(
                    f"route avg_gradient {avg_gradient:.4f} is below the "
                    f"route-level slope floor θ={params.theta}"
                ),
                numeric={"observed": avg_gradient, "required": params.theta},
            )
        )

    # Per-edge checks run over *distinct* edge identities (first occurrence
    # preserved for deterministic order): a reused edge is one bad edge, not
    # two, so it must not emit duplicate difficulty/membership violations.
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

    # Edge-reuse limit: each non-exempt base trail segment may appear at most
    # once, regardless of direction (FR5, Story 5.2). Counts are tallied over the
    # non-exempt base-segment ids each route edge occupies — single-sourced with
    # the solver/oracle via `solver.reuse`, so a GRASP-admitted route never trips
    # this. Exempt short connectors carry no non-exempt id, so repeating one (in
    # either direction) is not a violation. Edges absent from the operational
    # graph contribute nothing here — they are already flagged by
    # `graph_membership` above.
    non_exempt = non_exempt_base_segment_ids(graph)
    segment_counts: dict[tuple[int, int, int], int] = {}
    for edge in edges:
        data = nx_graph.get_edge_data(edge.node_u, edge.node_v, edge.key)
        if data is None:
            continue
        for sid in blocking_ids(data, edge.node_u, edge.node_v, edge.key, non_exempt):
            segment_counts[sid] = segment_counts.get(sid, 0) + 1
    for sid, count in segment_counts.items():
        if count > 1:
            violations.append(
                ConstraintViolation(
                    constraint_id="edge_reuse",
                    detail=(
                        f"base trail segment {sid} is traversed {count} times; "
                        f"non-exempt segments may be used at most once per route, "
                        f"in either direction (reuse limit 1)"
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
    return RouteMetrics(
        length_m=length_m,
        d_plus_m=d_plus_m,
        d_minus_m=d_minus_m,
        avg_gradient=route_avg_gradient(edges),
    )


def _as_solution(route: Route) -> Solution:
    """Wrap a `Route` as a transient `Solution` for `jaccard_distance` reuse.

    `jaccard_distance` keys on the canonical `(node_u, node_v, key)` identity and
    ignores `objective`, so wrapping with `objective=0.0` reuses the single
    source of Jaccard truth (`solver/distinctness.py`) without duplicating the
    edge-identity projection here.
    """
    return Solution(edges=tuple(route.edges), objective=0.0)
