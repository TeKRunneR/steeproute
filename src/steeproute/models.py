"""Query-side data contract: dataclasses passed between pipeline stages 8-9,
the GRASP solver, the validator, and the output renderer.

All cross-boundary structured data uses `@dataclass(frozen=True, slots=True)`
per Architecture §"Python code conventions". The architecture-pinned shapes
(`Route`, `RouteValidation`, `ConstraintViolation`, `PairwiseViolation`,
`ValidatedRouteSet`) match §Cat 6b verbatim, and `SolverParams` mirrors the
§Cat 9 report-metadata field list 1:1 — changing either shape means changing
the architecture doc with it.

`Area` and `PipelineConfig` live here too, rather than in `pipeline/`, because
the same shapes feed both setup-side ingestion and the query-side cache
coverage check.

This module's raw bytes are part of the prepared-cache key
(`cache._PIPELINE_CONTENT_GLOBS`), so any edit here — comments included —
re-keys subsequent setup runs.
"""

import pathlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

# Solver termination outcome surfaced in every report's metadata (Architecture
# §Cat 5e). Homed here — the lowest layer — so both the solver (which sets it)
# and the output renderer (which emits it) import one definition, rather than the
# renderer owning a type the solver would have to depend on.
ConvergenceStatus = Literal["converged", "budget-exhausted", "interrupted"]


@dataclass(frozen=True, slots=True)
class Area:
    """Geographic search area as an optionally-rotated rectangle.

    `center` is `(lat, lon)` in WGS84 decimal degrees. The rectangle is defined
    by two half-extents (half-width east–west, half-height north–south, both in
    km) and an `angle_deg` bearing that rotates the box in a local `cos(lat)` km
    frame. A **centered square** — the common case — is the `angle_deg == 0`,
    equal-extents special case; an **axis-aligned rectangle** is `angle_deg == 0`
    with unequal extents.

    `radius_km` is the **square shorthand / bbox half-side**:
    `Area(center=..., radius_km=r)` describes a `2r`-side axis-aligned square,
    which stage 1 fetches with `osmnx.graph_from_point(..., dist_type="bbox")`.
    It is named `radius_km` (not `bbox_half_side_km`) to match the cache manifest
    field and the `--radius` CLI flag 1:1 — the geometric meaning is square
    half-side, **not** a disk radius. When `half_width_km` / `half_height_km` are
    `None` they resolve to `radius_km` (see `half_extents_km`), so `radius_km`
    readers work for square areas without branching.

    A rotated / non-square rectangle sets `half_width_km`, `half_height_km`, and
    `angle_deg` explicitly (done at the CLI boundary); for such an area
    `radius_km` is meaningless, so read geometry through `half_extents_km` and
    `angle_deg`, never off `radius_km`.
    """

    center: tuple[float, float]
    # Required (no default) so an area can't be constructed with no size at all.
    # For the square shorthand it's the half-side; a rotated / non-square
    # rectangle passes `radius_km=0.0` (inert — the extents drive the geometry)
    # plus explicit extents. Value-range validation (>0, finite) stays at the
    # CLI/`osm._validate_area` boundary, not here (this is a data carrier).
    radius_km: float
    half_width_km: float | None = None
    half_height_km: float | None = None
    angle_deg: float = 0.0

    @property
    def half_extents_km(self) -> tuple[float, float]:
        """Effective `(half_width_km, half_height_km)` in km.

        Resolves the square shorthand: a `None` extent falls back to
        `radius_km`. Computed on access (not stored) so `dataclasses.replace`
        of `radius_km` on a square area re-derives correctly. Downstream
        geometry derives everything from this — no reader should branch on
        which of `radius_km` / the extents was supplied.
        """
        half_width = self.radius_km if self.half_width_km is None else self.half_width_km
        half_height = self.radius_km if self.half_height_km is None else self.half_height_km
        return (half_width, half_height)

    @property
    def is_square(self) -> bool:
        """`True` iff this is a centered, axis-aligned square."""
        half_width, half_height = self.half_extents_km
        return self.angle_deg == 0.0 and half_width == half_height


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Knobs for the setup-side pipeline orchestrator (`pipeline.run_setup_stages`).

    Only fields that genuinely change the cached graph live here. `difficulty_cap`
    is intentionally absent: stages 1-7 are parameter-independent over it per
    Architecture §Cat 3b (the cache key omits it; see §Cat 4b), so the
    orchestrator pins it to the most permissive value internally and query-side
    re-filters at the user's chosen cap.

    Smoothing / resample / elevation-median windows stay at their module-scope
    constants in the relevant `pipeline/` modules — no per-call overrides today.
    """

    untagged_policy: str
    dem_path: pathlib.Path


@dataclass(frozen=True, slots=True)
class Edge:
    """Query-side projection of the MultiDiGraph edge-attribute contract (Architecture §Cat 3c).

    The solver, validator, and renderer all pass `Edge` values around. Geometry
    and resampled vertices stay graph-side; this type carries the lean metric
    tuple every consumer actually reads. SAC scale is `str | None` because the
    untagged-trails policy admits edges without a SAC tag
    (`PipelineConfig.untagged_policy="include"`).

    `key` disambiguates parallel edges between the same node pair, matching
    networkx's `MultiDiGraph` convention. The `(node_u, node_v, key)` tuple is
    the canonical edge identity used for Jaccard hashing (Architecture
    §"Numerical and data discipline").

    Ordering supplied by the producer; consumers must not reorder.
    """

    node_u: int
    node_v: int
    key: int
    length_m: float
    d_plus_m: float
    d_minus_m: float
    avg_gradient: float
    sac_scale: str | None


def route_avg_gradient(edges: Iterable[Edge]) -> float:
    """Whole-route average gradient `(ΣD+ + ΣD−) / Σlength`, else `0.0` if length ≤ 0.

    The single source of truth for the route-level slope metric (FR3). The GRASP
    finalization gate (`solver/grasp.py` `_route_slope_ok`), the validator's
    `slope_floor` check + `RouteMetrics.avg_gradient` (`validator.py`), and the
    exhaustive oracle (`tests/integration/exhaustive_oracle.py`) all call this so
    they compare bit-identical values — a route admitted by the solver can never
    be flagged by the validator over a float-summation-order discrepancy.
    """
    edge_seq = tuple(edges)  # materialize: the metric makes two passes
    total_length = sum((e.length_m for e in edge_seq), 0.0)
    total_climb = sum((e.d_plus_m + e.d_minus_m for e in edge_seq), 0.0)
    return total_climb / total_length if total_length > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class Climb:
    """A contiguous edge-sequence meeting the slope-floor + min-length criteria.

    Output of pipeline stage 8 (`pipeline.climbs.detect_climbs`). Each climb
    becomes a super-edge in the contracted graph at stage 9. `edges` is a tuple
    (not a list) so the climb is structurally immutable.
    """

    edges: tuple[Edge, ...]
    length_m: float
    d_plus_m: float
    avg_slope: float


HEAVY_EDGE_ATTRS: frozenset[str] = frozenset({"vertices_resampled", "geometry"})
"""Per-edge attributes that never appear on a lean contracted graph.

Both are pure rendering payloads — the resampled polyline vertices and the
shapely geometry — that no contracted-graph consumer reads: the solver, climb
detection, contraction and the validator are geometry-blind (Architecture
§Cat 5), and `output.render` resolves geometry off the *operational* graph via
`super_edge_to_base`. Dropping them shrinks the r20 contracted graph from
~204 MB to ~72 MB (2026-07-08).

Homed in this module — the lowest layer — rather than beside its main consumer
`solver.parallel.solver_graph_view`, because it is part of the
`ContractedGraph` lean contract that pipeline stage 9 must satisfy, and
`pipeline/` must not import from `solver/`. `solver.parallel` re-exports it.
"""


@dataclass(frozen=True, slots=True)
class ContractedGraph:
    """The climb-contracted graph the GRASP solver consumes (Architecture §Cat 3, stage 9).

    `graph` is the contracted `networkx.MultiDiGraph` — climbs as super-edges
    and **all** connectors retained (no length-based drop). Typed as `Any`
    because networkx 3.x ships partial type stubs and we don't want every
    solver-side import to fight the type checker over node/edge access
    (external-boundary `Any` per Architecture §"Type hints and data").

    On top of the base edge-attribute contract (`length_m`, `d_plus_m`,
    `d_minus_m`, `avg_gradient`, `sac_scale`, and — on connectors only —
    `highway`/`osm_way_id`), every edge in `graph` carries two reuse-tagging
    attributes set at contraction (FR5):

    - `base_segment_id`: `frozenset[tuple[int, int, int]]` of undirected
      base-segment identities (canonical sorted node-pair + key, so a segment
      and its reverse share the id). A connector carries a one-element set; a
      super-edge carries the set of ids of the base edges it contracts. Stored
      as a set on every edge for uniform downstream handling.
    - `reusable`: `bool`, `True` only for a connector shorter than
      `l_connector` (a short linking segment, exempt from the once-per-route
      reuse rule and bidirectional); `False` for long connectors and every
      super-edge.

    The undirected once-only reuse rule (`solver/reuse.py`, shared by solver,
    oracle, and validator) keys on `base_segment_id` and skips `reusable` edges.

    `super_edge_to_base` is the super-edge → base-`Edge`-sequence back-mapping.
    The key is the `(node_u, node_v, key)` tuple of a super-edge in `graph`; the
    value is the ordered `Edge` sequence the super-edge contracts. The validator
    uses it to expand a solver `Solution` back to base edges for constraint
    checks, so the mapping must round-trip.

    `lean` advertises that no edge carries `HEAVY_EDGE_ATTRS` (see above).
    `contract_climbs` produces a lean graph, so `run_parallel_grasp` serializes it
    straight to its workers instead of rebuilding a stripped copy. It defaults to
    `False` so a graph hand-built by a test or an external caller is treated as
    possibly-heavy and still routed through `solver_graph_view` — the conservative
    direction, since a wrong `False` costs one rebuild while a wrong `True` would
    ship the heavy payload to every worker.
    """

    graph: Any  # networkx.MultiDiGraph — partial type stubs (Architecture §"Type hints and data" boundary).
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]]
    lean: bool = False


@dataclass(frozen=True, slots=True)
class SolverParams:
    """The parameters every query records in its HTML/JSON metadata block (Architecture §Cat 9).

    Field names match the CLI flag names verbatim so they double as the
    JSON-sidecar field names (`snake_case` per Architecture §"Serialization
    conventions"). The metadata block in `output.py` iterates the fields
    directly, so renaming or reordering a field changes the report surface too.

    - `theta`: route-level average-slope floor (dimensionless gradient, e.g.
      0.20 for 20%) — the minimum `(D+ + D−)/length` a returned route as a whole
      must meet (FR3). Distinct from `min_climb_slope` below.
    - `min_climb_slope`: per-climb detection threshold — the minimum
      running-average uphill slope (`d_plus/length`) for a contiguous trail
      segment to qualify as a climb in pipeline stage 8 (FR3b). Drives
      `detect_climbs`; does not by itself constrain the whole route.
    - `difficulty_cap`: SAC scale ceiling (e.g. "T3"); edges above are excluded.
    - `l_connector`: short-connector reuse-exemption threshold (m). Connectors
      shorter than this are reuse-exempt linking segments — kept in the
      contracted graph and reusable in both directions; all other segments may
      be used at most once per route, regardless of direction (FR5).
    - `min_climb_ground_length`: minimum cumulative ground length (m) for a
      candidate climb to qualify (FR3/FR6).
    - `j_max`: pairwise Jaccard distinctness ceiling (FR11).
    - `n`: top-N route count (FR11).
    - `untagged_policy`: matches `PipelineConfig.untagged_policy`; recorded
      here so the report's metadata block carries the full input fingerprint.
    - `seed`: explicit RNG seed (FR29); `None` only at the CLI-flag boundary
      before the seed resolver fills in a value.
    - `iter_budget`: GRASP iteration ceiling (Architecture §Cat 5e termination).
    - `time_budget`: wall-clock ceiling in seconds (§Cat 5e termination).
    - `stagnation_iters`: consecutive-stagnant-iterations threshold; `0`
      disables (Architecture §Cat 5e).
    - `start_at_junction`: opt-in FR31 flag (default off). When `True`, GRASP
      seeds construction and the exhaustive oracle starts walks only at
      road/trail junction nodes (`is_road_trail_junction`, tagged at stage 9),
      and the validator flags any route whose start endpoint isn't a junction.
    - `max_descent_slope`: opt-in FR32 cap (default `None` = off). When set, GRASP
      construction, the exhaustive oracle, and the validator reject any
      *descending* traversal of an edge whose `max_windowed_descent_grad` exceeds
      this; uphill traversal is unconstrained, so the same segment stays eligible
      as a climb.
    """

    theta: float
    min_climb_slope: float
    difficulty_cap: str
    l_connector: float
    min_climb_ground_length: float
    j_max: float
    n: int
    untagged_policy: str
    seed: int | None
    iter_budget: int
    time_budget: float
    stagnation_iters: int
    start_at_junction: bool = False
    max_descent_slope: float | None = None


@dataclass(frozen=True, slots=True)
class Solution:
    """Internal solver output (Architecture §"Boundaries"): an ordered edge-sequence + its objective.

    The GRASP solver emits `list[Solution]`; the validator converts them to
    `Route` instances. Producers must supply `edges` in route-traversal order —
    consumers must not reorder (FR29 byte-identical reproducibility depends on
    it).

    `objective` is the scored value the solver ranked this candidate on
    (typically D+ + D- per Architecture §"Stagnation definition").
    """

    edges: tuple[Edge, ...]
    objective: float


@dataclass(frozen=True, slots=True)
class RouteMetrics:
    """Aggregate metrics computed from a `Route`'s underlying edges.

    Produced by the route builder at the validator boundary; the output renderer
    reads these directly rather than re-summing edge metrics. `avg_gradient` is
    the whole-route
    `(d_plus_m + d_minus_m) / length_m` (FR3 route-level metric) if
    `length_m > 0`, else 0.0 — single-sourced via `route_avg_gradient`.
    """

    length_m: float
    d_plus_m: float
    d_minus_m: float
    avg_gradient: float


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    """One per-route constraint failure surfaced by the validator (Architecture §Cat 6b).

    `numeric` carries observed-vs-required values for the validation banner
    (e.g. `{"observed": 0.18, "required": 0.20}`). The renderer formats
    `constraint_id` + `detail` + `numeric` into the per-route banner.
    """

    constraint_id: str
    detail: str
    numeric: dict[str, float]


@dataclass(frozen=True, slots=True)
class RouteValidation:
    """Per-route validation result (Architecture §Cat 6b).

    `passed=True` iff `violations` is empty. The renderer shows the banner when
    `passed=False` OR a `PairwiseViolation` in the wrapping `ValidatedRouteSet`
    references this route (Architecture §Cat 6b banner logic).
    """

    passed: bool
    violations: list[ConstraintViolation]


@dataclass(frozen=True, slots=True)
class Route:
    """A solver-produced route presented to the user (Architecture §Cat 6b).

    Routes are produced once by the validator from a solver `Solution` + the
    contracted graph + the active `SolverParams`; the renderer writes one HTML +
    one JSON per `Route`.
    """

    edges: list[Edge]
    metrics: RouteMetrics
    validation: RouteValidation


@dataclass(frozen=True, slots=True)
class PairwiseViolation:
    """A set-level Jaccard-distinctness violation between two routes (Architecture §Cat 6b).

    Lives on the wrapping `ValidatedRouteSet` (not on either `Route`) so the
    renderer can surface it in both affected reports without lying about
    ownership. Indices are positional into `ValidatedRouteSet.routes`.
    """

    route_index_a: int
    route_index_b: int
    jaccard_observed: float
    jaccard_max: float


@dataclass(frozen=True, slots=True)
class ValidatedRouteSet:
    """The validator's full output: per-route results + set-level violations (Architecture §Cat 6b).

    Consumed by `output.py::render` and by `cli/query.py`'s exit-code computation
    (Architecture §Cat 6c). `set_violations` ordering matters for FR29
    byte-identical reproducibility, and the validator that produces it owns that
    ordering.
    """

    routes: list[Route]
    set_violations: list[PairwiseViolation]


@dataclass(frozen=True, slots=True)
class ProvenanceInfo:
    """Run-time provenance carried into every HTML + JSON report (Architecture §Cat 9).

    Field names match the report-metadata block, not the cache manifest's
    schema — `git_dirty` is a separate bool here so the renderer can format
    `git_commit_short + "-dirty"` consistently, and `osm_extract_date` /
    `pipeline_content_hash` / `dem_version` / `steeproute_version` echo the
    manifest values from the cache hit that fed this query (Architecture
    §Cat 4b + §Cat 9).

    Built by `provenance.py` at run start; passed through the solver + validator
    unchanged into `output.render(...)`.
    """

    steeproute_version: str
    git_commit_short: str
    git_dirty: bool
    osm_extract_date: str
    dem_version: str
    pipeline_content_hash: str
