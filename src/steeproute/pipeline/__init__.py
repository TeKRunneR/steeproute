# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx MultiDiGraph operations surface as Unknown; same external-boundary
# pattern as pipeline/osm.py, pipeline/smoothing.py, pipeline/dem.py, pipeline/climbs.py.
"""Pipeline stages 1-9, the setup-side orchestrator, and the query-side reshaping.

`run_setup_stages(area, config)` wires the parameter-independent setup pipeline
(stages 1-5) into a single entry point:

    osm_load                 (stage 1)
      → filter_trails        (stage 2)         + non-empty + orphan-prune guards
      → smooth_polylines     (stage 3)
      → resample_edges       (stage 4)         + short-edge prune guard
      → sample_elevation     (stage 5)         + finite-elevation guard

Output is the cached `networkx.MultiDiGraph` carrying the **raw** post-stage-5
edge-attribute contract (`geometry`, `vertices_resampled` with raw elevation,
`sac_scale`, `highway`, `osm_way_id`). The per-edge metrics (`length_m`,
`d_plus_m`, `d_minus_m`, `avg_gradient`) are **not** computed here.

**Stages 6-7 are query-side (Story 6.3).** Elevation smoothing + deadband
reshaping (stage 6) and naive-sum metrics (stage 7) move out of setup into
`operationalize_graph`, called by `cli/query.py`. This keeps the cache
smoothing-independent: `--elevation-smoothing` / `--elevation-deadband` become
free query knobs and the cache key need not include them. Moving the code out of
setup changes `compute_pipeline_content_hash`, so prepared areas re-prepare once
when this ships (the same one-time cost the roads change incurred).

**Difficulty-cap policy (Architecture §Cat 3b + §Cat 4b).** Stages 1-5 are
cached parameter-independent over `difficulty_cap` — the cache key omits it.
The orchestrator therefore pins it to `"T6"` (most permissive recognized SAC
rank) so the cached graph contains every trail edge within SAC bounds; query
side re-filters at the user's chosen cap.

**Inter-stage contract guards.** Four orchestrator-level guards enforce
invariants that the individual stages trust upstream callers to maintain (each
stage stays a pure transform under a stated input contract):

- `_assert_non_empty`: zero edges after `filter_trails` → `PipelineContractError`
  with an actionable message (vs. cryptic ZeroDivisionError downstream).
- `_drop_orphan_nodes`: prune nodes whose degree fell to 0 after edge removal.
- `_drop_short_edges`: drop edges whose post-stage-4 2D length is below
  `_PIPELINE_LENGTH_FLOOR_M` — catches out-and-back / coincident-2D / self-loop
  cases stage 4's bit-identical-coord check misses.
- `_assert_finite_elevations`: post-stage-5 elevation must be finite on every
  vertex; non-finite → `PipelineContractError` naming the offending edge.

Stage 8 (climb detection) and stage 9 (climb-graph contraction) wire on the
query side in Epic 3, downstream of `operationalize_graph`.
"""

from __future__ import annotations

import logging
import math
import pathlib

import networkx as nx

from steeproute.errors import BadCLIArgError, PipelineContractError
from steeproute.models import Area, PipelineConfig
from steeproute.pipeline._common import empty_like
from steeproute.pipeline.climbs import compute_edge_metrics
from steeproute.pipeline.dem import sample_elevation
from steeproute.pipeline.osm import filter_trails, osm_load
from steeproute.pipeline.smoothing import (
    ELEVATION_DEADBAND_DEFAULT_M,
    ELEVATION_SMOOTHING_DEFAULT_M,
    graph_deadband_elevation,
    graph_smooth_elevation,
    resample_edges,
    smooth_polylines,
)
from steeproute.progress import StageProgress

_logger = logging.getLogger(__name__)

# Most permissive recognized SAC rank in `pipeline.osm.SAC_SCALE_RANK`. The
# orchestrator pins `difficulty_cap` to this value so stages 1-7 are
# parameter-independent over it (the cache key omits `difficulty_cap` per
# Architecture §Cat 4b). Query-side filtering applies the user's chosen cap.
_SETUP_DIFFICULTY_CAP: str = "T6"

# WGS84 equatorial radius for the local-equirectangular projection used by the
# short-edge guard. Same value and rationale as `pipeline.smoothing._EARTH_RADIUS_M`
# and `pipeline.climbs._EARTH_RADIUS_M` — duplicated rather than imported so the
# orchestrator stays self-contained against a physical constant.
_EARTH_RADIUS_M: float = 6_378_137.0

# Length floor for the post-stage-4 short-edge guard, in meters. 1 mm is six
# orders of magnitude below the 10 m resample spacing — no legitimate trail
# edge sits below it, and the float-underflow regime where `length_m → 0` and
# `avg_gradient → ∞` becomes unreachable downstream in stage 7.
#
# This catches the out-and-back / coincident-2D / self-loop polylines that
# `resample_edges`'s "all coords identical" check misses (e.g. a self-loop
# whose geometry is `[(0,0), (eps, eps), (0,0)]` resamples to ~bit-zero
# length but passes the bit-identical-coords filter). Module-scope per
# Architecture §Numerical and data discipline.
_PIPELINE_LENGTH_FLOOR_M: float = 1e-3


def run_setup_stages(
    area: Area,
    config: PipelineConfig,
    *,
    progress: StageProgress | None = None,
) -> nx.MultiDiGraph:
    """Run the setup-side pipeline (stages 1-5) end-to-end for `area`.

    Wires the five stage functions with four orchestrator-level inter-stage
    contract guards (see module docstring for the full sequence and rationale).
    Elevation smoothing/deadband (stage 6) and per-edge metrics (stage 7) are
    query-side now (Story 6.3) — see `operationalize_graph`.

    Args:
        area: search area to ingest (drives OSM fetch + DEM coverage check).
        config: per-run knobs — `untagged_policy` (passed to `filter_trails`)
            and `dem_path` (passed to `sample_elevation`).
        progress: optional stage-timing seam (Story 11.1, FR33) — each stage
            announces start/elapsed through it and records into its `timings`
            dict. `None` (the default) is silent, preserving existing callers.

    Returns:
        A `networkx.MultiDiGraph` with the raw post-stage-5 edge-attribute
        contract on every edge (`geometry`, `vertices_resampled` with raw
        elevation, `sac_scale`, `highway`, `osm_way_id`) and every node
        connected to at least one edge. The per-edge metrics are added query-side
        by `operationalize_graph`.

    Raises:
        PipelineContractError: stage 2 returned an empty graph for `area`, the
            post-stage-4 prunes left zero edges, or stage 5 produced a
            non-finite elevation on some edge.
        DEMCoverageError: a vertex fell outside the DEM bounds or sampled
            nodata (raised by stage 5).
        BadCLIArgError: `area` or `config.untagged_policy` is malformed
            (raised by stages 1-2), or `config.dem_path` does not exist or is
            not a regular file (caught at the orchestrator boundary so the
            expensive stages 1-4 do not run on bad input).
    """
    # Fail-fast on a missing DEM so we don't waste minutes on OSM + smoothing
    # stages before discovering at stage 5 that the file isn't there. In the CLI
    # flow `cli/setup.py` resolves the DEM via `resolve_dem` (auto-download) *after*
    # building the geometry, so this guard fires only for the test / script callers
    # that pass a pre-existing fixture DEM; a missing file there means a
    # corrupt/evicted cache entry.
    if not config.dem_path.is_file():
        raise BadCLIArgError(
            f"DEM file {config.dem_path} does not exist or is not a regular file.",
            detail=(
                "steeproute-setup auto-downloads the DEM; a missing file here points "
                "to a corrupt cache — re-run steeproute-setup with --force-refresh."
            ),
        )
    graph = build_graph_geometry(area, config.untagged_policy, progress=progress)
    return attach_elevation(graph, config.dem_path, progress=progress)


def build_graph_geometry(
    area: Area,
    untagged_policy: str,
    *,
    progress: StageProgress | None = None,
) -> nx.MultiDiGraph:
    """Run the DEM-independent setup stages 1-4 and return the geometry-only graph.

    Stages 1-4 (`osm_load` → `filter_trails` → `smooth_polylines` →
    `resample_edges`, plus the non-empty / orphan / short-edge guards) need no
    elevation data. Splitting them out lets `cli/setup.py` size the DEM from the
    *actual* edge geometry (`pipeline.dem_download.graph_dem_bounds`) before
    `attach_elevation` runs, guaranteeing DEM coverage of every probed vertex.

    Each stage runs inside `progress.stage(...)` (Story 11.1, FR33): the guards
    are folded into their preceding stage (they cost microseconds — a separate
    timeline line would be noise). The stage functions themselves stay pure and
    seam-free; only the orchestrator observes them.

    Returns a `MultiDiGraph` whose edges carry the post-stage-4 contract
    (`geometry`, `sac_scale`, `highway`, `osm_way_id`) — no `vertices_resampled`
    elevation yet — and at least one edge (the non-empty guard).
    """
    seam = progress if progress is not None else StageProgress()
    with seam.stage("osm-download", note="one Overpass request; typically takes minutes"):
        graph = osm_load(area)
    with seam.stage("trail-filter"):
        graph = filter_trails(graph, untagged_policy, _SETUP_DIFFICULTY_CAP)
        _assert_non_empty(graph, area, untagged_policy)
        graph = _drop_orphan_nodes(graph)
    with seam.stage("polyline-smoothing"):
        graph = smooth_polylines(graph)
    with seam.stage("resampling"):
        graph = resample_edges(graph)
        graph = _drop_short_edges(graph)
        # Re-assert non-empty after the post-stage-4 prunes: stage-3 (smooth) and
        # stage-4 (resample) drop degenerate edges, and `_drop_short_edges` adds
        # the < 1 mm prune on top. A pathological fixture could leave a zero-edge
        # graph that then hits `sample_elevation` with no actionable error.
        _assert_non_empty(graph, area, untagged_policy)
    return graph


def attach_elevation(
    graph: nx.MultiDiGraph,
    dem_path: pathlib.Path,
    *,
    progress: StageProgress | None = None,
) -> nx.MultiDiGraph:
    """Run stage 5 (`sample_elevation`) + the finite-elevation guard on `graph`.

    `dem_path` must cover every edge-geometry vertex — `cli/setup.py` ensures this
    by sizing the raster from `graph_dem_bounds(graph)`. A vertex outside the DEM
    raises `DEMCoverageError` from `sample_elevation`.
    """
    seam = progress if progress is not None else StageProgress()
    with seam.stage("elevation-sampling"):
        graph = sample_elevation(graph, dem_path)
        _assert_finite_elevations(graph)
    return graph


def operationalize_graph(
    graph: nx.MultiDiGraph,
    *,
    elevation_smoothing_m: float = ELEVATION_SMOOTHING_DEFAULT_M,
    elevation_deadband_m: float = ELEVATION_DEADBAND_DEFAULT_M,
) -> nx.MultiDiGraph:
    """Query-side stages 6-7: reshape the cached raw-elevation graph into the operational graph.

    The cache stores the raw post-stage-5 elevation (see `run_setup_stages`).
    This applies the canonical-profile reshaping once over the whole graph —
    `graph_smooth_elevation` → `graph_deadband_elevation` — then `compute_edge_metrics`
    as a **naive sum** over that single reshaped profile. The output graph carries
    the full operational edge-attribute contract (`geometry`, `vertices_resampled`
    reshaped, `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`,
    `highway`, `osm_way_id`).

    This is the single home of the box==curve guarantee: the same reshaped
    `vertices_resampled` that the metrics sum over is also what `output.render`
    plots, so the metric box, the solver objective, and the displayed curve all
    read one profile. `cli/query.py` calls this before climb detection and feeds
    the returned graph to `output.render`. Pure — the input graph is never mutated.

    Args:
        graph: the cached post-stage-5 graph (raw `vertices_resampled`).
        elevation_smoothing_m: graph-Laplacian smoothing strength in meters.
        elevation_deadband_m: deadband hysteresis floor in meters (0 = off).
    """
    reshaped = graph_smooth_elevation(graph, elevation_smoothing_m)
    reshaped = graph_deadband_elevation(reshaped, elevation_deadband_m)
    return compute_edge_metrics(reshaped)


def _assert_non_empty(
    graph: nx.MultiDiGraph,
    area: Area,
    untagged_policy: str,
) -> None:
    """Raise `PipelineContractError` if `graph` has zero edges after stage 2.

    Reached when OSM responded successfully but the area contained no trail
    edges under the current `untagged_policy`. Without this guard, downstream
    stages divide by zero (stage 7's `avg_gradient`) with no edge context.
    """
    if graph.number_of_edges() == 0:
        raise PipelineContractError(
            f"Pipeline produced zero edges for area "
            f"(center={area.center}, radius_km={area.radius_km:g}) under "
            f"untagged_policy={untagged_policy!r}.",
            detail=(
                "Widen --radius, switch --untagged-trails (include/exclude), "
                "or pick an area with more recorded trails."
            ),
        )


def _drop_orphan_nodes(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Drop nodes whose degree fell to 0 after `filter_trails` removed edges.

    `filter_trails` iterates edges only, so nodes whose last incident edge was
    removed remain in the graph as orphans. Setup-side pipeline policy is to
    return a clean subgraph; downstream stages assume every node connects to
    at least one edge.
    """
    # Build from the kept nodes rather than copy-then-remove (Story 14.2, S3).
    # Orphans have degree 0, so every edge carries over; node/edge order is
    # preserved so downstream output is bit-identical.
    orphans = {n for n, deg in graph.degree() if deg == 0}
    out = empty_like(graph, exclude_nodes=orphans)
    for u, v, k, data in graph.edges(data=True, keys=True):
        out.add_edge(u, v, key=k, **data)
    if orphans:
        _logger.debug("pipeline: dropped %d orphan nodes", len(orphans))
    return out


def _drop_short_edges(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Drop edges whose post-stage-4 2D length is below `_PIPELINE_LENGTH_FLOOR_M`.

    See `_PIPELINE_LENGTH_FLOOR_M` for the floor rationale. Catches out-and-back
    / coincident-2D self-loops that stage 4 lets through (the bit-identical
    check passes them) and would otherwise produce `ZeroDivisionError` in stage
    7's `avg_gradient`.

    Length probe uses the same local-equirectangular projection as
    `pipeline.smoothing._resample_meters` (cos-of-mean-latitude correction).
    """
    # Build from kept edges rather than copy-then-remove (Story 14.2, S3).
    out = empty_like(graph)
    dropped = 0
    for u, v, k, data in graph.edges(data=True, keys=True):
        coords = [(float(c[0]), float(c[1])) for c in data["geometry"].coords]
        if _polyline_length_m(coords) < _PIPELINE_LENGTH_FLOOR_M:
            dropped += 1
            continue
        out.add_edge(u, v, key=k, **data)
    if dropped:
        _logger.debug(
            "pipeline: dropped %d short edges (< %.3g m)",
            dropped,
            _PIPELINE_LENGTH_FLOOR_M,
        )
    # The drop may have created new orphans; reuse the same prune helper so
    # the post-guard graph keeps its "every node has degree >= 1" invariant.
    return _drop_orphan_nodes(out)


def _assert_finite_elevations(graph: nx.MultiDiGraph) -> None:
    """Raise `PipelineContractError` if any post-stage-5 elevation is non-finite.

    Stage 5 already fail-fasts on non-finite DEM samples, so this guard only
    catches contract-breaking inputs from a future caller wiring a different
    elevation source. It also defends against NaN-arithmetic absorption in the
    query-side stage-7 strict `>`/`<` branches that would otherwise yield
    silently zero `d_plus_m`/`d_minus_m`.
    """
    for u, v, k, data in graph.edges(data=True, keys=True):
        verts: list[tuple[float, float, float]] = data["vertices_resampled"]
        for lat, lon, elev in verts:
            if not math.isfinite(elev):
                raise PipelineContractError(
                    f"Edge ({u}, {v}, {k}) has a vertex at (lat={lat:.6f}, "
                    f"lon={lon:.6f}) with non-finite elevation ({elev!r}) after "
                    f"stage 5.",
                    detail=(
                        "Post-stage-5 elevations must be finite floats. "
                        "Check DEM source for nodata / NaN samples."
                    ),
                )


def _polyline_length_m(coords: list[tuple[float, float]]) -> float:
    """Cumulative ground-distance in meters along `(lon, lat)` polyline coords.

    Local-equirectangular projection at the polyline's mean latitude. Same
    pattern as `pipeline.smoothing._resample_meters`; duplicated for module
    self-containment.
    """
    if len(coords) < 2:
        return 0.0
    mean_lat = sum(lat for _lon, lat in coords) / len(coords)
    deg_to_m_lat = _EARTH_RADIUS_M * math.radians(1.0)
    deg_to_m_lon = deg_to_m_lat * math.cos(math.radians(mean_lat))
    total = 0.0
    for i in range(1, len(coords)):
        dlon = (coords[i][0] - coords[i - 1][0]) * deg_to_m_lon
        dlat = (coords[i][1] - coords[i - 1][1]) * deg_to_m_lat
        total += math.hypot(dlon, dlat)
    return total
