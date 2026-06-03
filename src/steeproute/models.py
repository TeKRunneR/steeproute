"""Query-side data contract: dataclasses passed between pipeline stages 8-9,
the GRASP solver, the validator, and the output renderer.

All cross-boundary structured data uses `@dataclass(frozen=True, slots=True)`
per Architecture §"Python code conventions". The architecture-pinned shapes
(`Route`, `RouteValidation`, `ConstraintViolation`, `PairwiseViolation`,
`ValidatedRouteSet`) match §Cat 6b verbatim; `SolverParams` mirrors the §Cat 9
report-metadata field list 1:1; `Edge` / `Climb` / `ContractedGraph` /
`Solution` / `RouteMetrics` / `ProvenanceInfo` are designed in Story 3.1.

`Area` and `PipelineConfig` live here too (Epic 2) because the same shapes
feed both setup-side ingestion and query-side cache coverage.
"""

import pathlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Area:
    """Geographic search area as a center + bbox half-side.

    `center` is `(lat, lon)` in WGS84 decimal degrees.

    `radius_km` is the **bbox half-side**, not a disk radius. Stage 1 fetches
    OSM with `osmnx.graph_from_point(..., dist_type="bbox")`, which returns
    everything inside a `2 * radius_km`-side square centered on `center`. The
    field is named `radius_km` (rather than `bbox_half_side_km`) to match the
    cache manifest field naming 1:1 (Architecture §Cat 4) and the user-facing
    `--radius` CLI flag — but the geometric meaning is square half-side.

    Lives here (not pipeline/) because the same shape feeds setup-side
    ingestion (Epic 2) and query-side cache coverage check (Epic 3).
    """

    center: tuple[float, float]
    radius_km: float


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

    The solver, validator, and renderer all pass `Edge` values around (consumed
    by Stories 3.2-3.10). Geometry and resampled vertices stay graph-side; this
    type carries the lean metric tuple every consumer actually reads. SAC scale
    is `str | None` because the untagged-trails policy admits edges without a
    SAC tag (`PipelineConfig.untagged_policy="include"`).

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


@dataclass(frozen=True, slots=True)
class Climb:
    """A contiguous edge-sequence meeting the slope-floor + min-length criteria.

    Output of pipeline stage 8 (Story 3.2's `detect_climbs`). Each climb
    becomes a super-edge in the contracted graph (Story 3.3). `edges` is a
    tuple (not a list) so the climb is structurally immutable.
    """

    edges: tuple[Edge, ...]
    length_m: float
    d_plus_m: float
    avg_slope: float


@dataclass(frozen=True, slots=True)
class ContractedGraph:
    """The climb-contracted graph the GRASP solver consumes (Architecture §Cat 3, stage 9).

    `graph` is the contracted `networkx.MultiDiGraph` — climbs as super-edges,
    sub-`l_connector` connectors dropped. Typed as `Any` because networkx 3.x
    ships partial type stubs and we don't want every solver-side import to
    fight the type checker over node/edge access (external-boundary `Any` per
    Architecture §"Type hints and data").

    `super_edge_to_base` is the super-edge → base-`Edge`-sequence back-mapping
    (Story 3.3 AC: "back-mapping round-trips"). The key is the
    `(node_u, node_v, key)` tuple of a super-edge in `graph`; the value is the
    ordered `Edge` sequence the super-edge contracts. Used by the validator
    (Story 3.9) to expand a solver `Solution` back to base edges for
    constraint checks.
    """

    graph: Any  # networkx.MultiDiGraph — partial type stubs (Architecture §"Type hints and data" boundary).
    super_edge_to_base: dict[tuple[int, int, int], tuple[Edge, ...]]


@dataclass(frozen=True, slots=True)
class SolverParams:
    """The 13 parameters every query records in its HTML/JSON metadata block (Architecture §Cat 9).

    Field names match the CLI flag names verbatim so they double as the
    JSON-sidecar field names (`snake_case` per Architecture §"Serialization
    conventions"). The metadata block in `output.py` (Story 3.10) iterates the
    fields directly; reordering or renaming requires touching both surfaces.

    - `theta`: route-level average-slope floor (dimensionless gradient, e.g.
      0.20 for 20%) — the minimum `(D+ + D−)/length` a returned route as a whole
      must meet (FR3). Distinct from `min_climb_slope` below.
    - `min_climb_slope`: per-climb detection threshold — the minimum
      running-average uphill slope (`d_plus/length`) for a contiguous trail
      segment to qualify as a climb in pipeline stage 8 (FR3b). Drives
      `detect_climbs`; does not by itself constrain the whole route.
    - `difficulty_cap`: SAC scale ceiling (e.g. "T3"); edges above are excluded.
    - `l_connector`: minimum connector-edge length (m); shorter connectors
      drop out at the contraction step.
    - `min_climb_ground_length`: minimum cumulative ground length (m) for a
      candidate climb to qualify (FR3/FR6).
    - `j_max`: pairwise Jaccard distinctness ceiling (FR11).
    - `n`: top-N route count (FR11).
    - `area_cap`: maximum query-area radius (km); enforced at CLI parse time.
    - `untagged_policy`: matches `PipelineConfig.untagged_policy`; recorded
      here so the report's metadata block carries the full input fingerprint.
    - `seed`: explicit RNG seed (FR29); `None` only at the CLI-flag boundary
      before the seed resolver fills in a value.
    - `iter_budget`: GRASP iteration ceiling (Epic 4 termination).
    - `time_budget`: wall-clock ceiling in seconds (Epic 4 termination).
    - `stagnation_iters`: consecutive-stagnant-iterations threshold; `0`
      disables (Architecture §Cat 5e).
    """

    theta: float
    min_climb_slope: float
    difficulty_cap: str
    l_connector: float
    min_climb_ground_length: float
    j_max: float
    n: int
    area_cap: float
    untagged_policy: str
    seed: int | None
    iter_budget: int
    time_budget: float
    stagnation_iters: int


@dataclass(frozen=True, slots=True)
class Solution:
    """Internal solver output (Architecture §"Boundaries"): an ordered edge-sequence + its objective.

    The GRASP solver (Story 3.6) emits `list[Solution]`; the validator
    (Story 3.9) converts them to `Route` instances. Producers must supply
    `edges` in route-traversal order — consumers must not reorder (FR29
    byte-identical reproducibility depends on it).

    `objective` is the scored value the solver ranked this candidate on
    (typically D+ + D- per Architecture §"Stagnation definition").
    """

    edges: tuple[Edge, ...]
    objective: float


@dataclass(frozen=True, slots=True)
class RouteMetrics:
    """Aggregate metrics computed from a `Route`'s underlying edges.

    Produced by the route builder at the validator boundary (Story 3.9);
    consumers (output renderer Story 3.10) read these directly rather than
    re-summing edge metrics. `avg_gradient` is `d_plus_m / length_m` if
    `length_m > 0`, else 0.0.
    """

    length_m: float
    d_plus_m: float
    d_minus_m: float
    avg_gradient: float


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    """One per-route constraint failure surfaced by the validator (Architecture §Cat 6b).

    `numeric` carries observed-vs-required values for the validation banner
    (e.g. `{"observed": 0.18, "required": 0.20}`). The renderer (Story 3.10)
    formats `constraint_id` + `detail` + `numeric` into the per-route banner.
    """

    constraint_id: str
    detail: str
    numeric: dict[str, float]


@dataclass(frozen=True, slots=True)
class RouteValidation:
    """Per-route validation result (Architecture §Cat 6b).

    `passed=True` iff `violations` is empty. The renderer (Story 3.10) shows
    the banner when `passed=False` OR a `PairwiseViolation` in the wrapping
    `ValidatedRouteSet` references this route (Architecture §Cat 6b banner
    logic).
    """

    passed: bool
    violations: list[ConstraintViolation]


@dataclass(frozen=True, slots=True)
class Route:
    """A solver-produced route presented to the user (Architecture §Cat 6b).

    Routes are produced once by the validator (Story 3.9) from a solver
    `Solution` + the contracted graph + the active `SolverParams`; the
    renderer (Story 3.10) writes one HTML + one JSON per `Route`.
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

    Consumed by `output.py::render` (Story 3.10) and by `cli/query.py`'s
    exit-code computation (Architecture §Cat 6c). Ordering of
    `set_violations` matters for FR29 byte-identical reproducibility;
    producer is responsible (validator Story 3.9).
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

    Built by `provenance.py` (existing module, populated across Stories 2.6
    setup-side and 3.10 query-side) at run start; passed through the solver
    + validator unchanged into `output.render(...)`.
    """

    steeproute_version: str
    git_commit_short: str
    git_dirty: bool
    osm_extract_date: str
    dem_version: str
    pipeline_content_hash: str
