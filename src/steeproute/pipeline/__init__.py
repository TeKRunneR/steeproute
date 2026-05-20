# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx MultiDiGraph operations surface as Unknown; same external-boundary
# pattern as pipeline/osm.py, pipeline/smoothing.py, pipeline/dem.py, pipeline/climbs.py.
"""Pipeline stages 1-9 and setup-side orchestrator.

`run_setup_stages(area, config)` wires the parameter-independent setup pipeline
(stages 1-7) into a single entry point:

    osm_load                 (stage 1)
      → filter_trails        (stage 2)         + non-empty + orphan-prune guards
      → smooth_polylines     (stage 3)
      → resample_edges       (stage 4)         + short-edge prune guard
      → sample_elevation     (stage 5)
      → median_smooth_elevation (stage 6)      + finite-elevation guard
      → compute_edge_metrics (stage 7)

Output is a `networkx.MultiDiGraph` carrying the full edge-attribute contract
(`geometry`, `vertices_resampled`, `length_m`, `d_plus_m`, `d_minus_m`,
`avg_gradient`, `sac_scale`, `highway`, `osm_way_id`).

**Difficulty-cap policy (Architecture §Cat 3b + §Cat 4b).** Stages 1-7 are
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
- `_assert_finite_elevations`: post-stage-6 elevation must be finite on every
  vertex; non-finite → `PipelineContractError` naming the offending edge.

Stage 8 (climb detection) and stage 9 (climb-graph contraction) wire on the
query side in Epic 3; their orchestrator entry point is not in this story.
"""

from __future__ import annotations

import math

import networkx as nx

from steeproute.errors import BadCLIArgError, PipelineContractError
from steeproute.models import Area, PipelineConfig
from steeproute.pipeline.climbs import compute_edge_metrics
from steeproute.pipeline.dem import sample_elevation
from steeproute.pipeline.osm import filter_trails, osm_load
from steeproute.pipeline.smoothing import (
    median_smooth_elevation,
    resample_edges,
    smooth_polylines,
)

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


def run_setup_stages(area: Area, config: PipelineConfig) -> nx.MultiDiGraph:
    """Run the setup-side pipeline (stages 1-7) end-to-end for `area`.

    Wires the seven stage functions with four orchestrator-level inter-stage
    contract guards (see module docstring for the full sequence and rationale).

    Args:
        area: search area to ingest (drives OSM fetch + DEM coverage check).
        config: per-run knobs — `untagged_policy` (passed to `filter_trails`)
            and `dem_path` (passed to `sample_elevation`).

    Returns:
        A `networkx.MultiDiGraph` with the full setup-side edge-attribute
        contract on every edge (`geometry`, `vertices_resampled`, `length_m`,
        `d_plus_m`, `d_minus_m`, `avg_gradient`, `sac_scale`, `highway`,
        `osm_way_id`) and every node connected to at least one edge.

    Raises:
        PipelineContractError: stage 2 returned an empty graph for `area`, the
            post-stage-4 prunes left zero edges, or stage 6 produced a
            non-finite elevation on some edge.
        DEMCoverageError: a vertex fell outside the DEM bounds or sampled
            nodata (raised by stage 5).
        BadCLIArgError: `area` or `config.untagged_policy` is malformed
            (raised by stages 1-2), or `config.dem_path` does not exist or is
            not a regular file (caught at the orchestrator boundary so the
            expensive stages 1-4 do not run on bad input).
    """
    # Fail-fast on a bad DEM path so we don't waste minutes on OSM + smoothing
    # stages before discovering at stage 5 that the file isn't there. Click's
    # `exists=True` only covers the CLI; the orchestrator is also called from
    # tests and future scripts, so the guard belongs here too.
    if not config.dem_path.is_file():
        raise BadCLIArgError(
            f"--dem-path {config.dem_path} does not exist or is not a regular file.",
            detail="Provide a path to a local DEM GeoTIFF readable by rasterio.",
        )
    graph = osm_load(area)
    graph = filter_trails(graph, config.untagged_policy, _SETUP_DIFFICULTY_CAP)
    _assert_non_empty(graph, area, config.untagged_policy)
    graph = _drop_orphan_nodes(graph)
    graph = smooth_polylines(graph)
    graph = resample_edges(graph)
    graph = _drop_short_edges(graph)
    # Re-assert non-empty after the post-stage-4 prunes: stage-3 (smooth) and
    # stage-4 (resample) drop degenerate edges, and `_drop_short_edges` adds
    # the < 1 mm prune on top. A pathological fixture could leave a zero-edge
    # graph that then hits `sample_elevation` with no actionable error.
    _assert_non_empty(graph, area, config.untagged_policy)
    graph = sample_elevation(graph, config.dem_path)
    graph = median_smooth_elevation(graph)
    _assert_finite_elevations(graph)
    return compute_edge_metrics(graph)


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
    out: nx.MultiDiGraph = graph.copy()
    orphans = [n for n, deg in out.degree() if deg == 0]
    for n in orphans:
        out.remove_node(n)
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
    out: nx.MultiDiGraph = graph.copy()
    edges_to_drop: list[tuple[int, int, int]] = []
    for u, v, k, data in out.edges(data=True, keys=True):
        coords = [(float(c[0]), float(c[1])) for c in data["geometry"].coords]
        if _polyline_length_m(coords) < _PIPELINE_LENGTH_FLOOR_M:
            edges_to_drop.append((u, v, k))
    for u, v, k in edges_to_drop:
        out.remove_edge(u, v, k)
    # The drop may have created new orphans; reuse the same prune helper so
    # the post-guard graph keeps its "every node has degree >= 1" invariant.
    return _drop_orphan_nodes(out)


def _assert_finite_elevations(graph: nx.MultiDiGraph) -> None:
    """Raise `PipelineContractError` if any post-stage-6 elevation is non-finite.

    Stage 5 already fail-fasts on non-finite DEM samples, so this guard only
    catches contract-breaking inputs from a future caller wiring a different
    elevation source. It also defends against NaN-arithmetic absorption in
    stage 7's strict `>`/`<` branches that would otherwise yield silently
    zero `d_plus_m`/`d_minus_m`.
    """
    for u, v, k, data in graph.edges(data=True, keys=True):
        verts: list[tuple[float, float, float]] = data["vertices_resampled"]
        for lat, lon, elev in verts:
            if not math.isfinite(elev):
                raise PipelineContractError(
                    f"Edge ({u}, {v}, {k}) has a vertex at (lat={lat:.6f}, "
                    f"lon={lon:.6f}) with non-finite elevation ({elev!r}) after "
                    f"stage 6.",
                    detail=(
                        "Post-stage-6 elevations must be finite floats. "
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
