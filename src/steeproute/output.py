# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: route geometry is read off the base `MultiDiGraph` (`vertices_resampled`
# edge attribute), whose networkx access surfaces as Unknown — same external-boundary
# pattern as pipeline/ and cli/query.py.
"""HTML report + JSON sidecar rendering (FR15-21, FR29 / Architecture §Cat 9).

`render` writes one self-contained `route-<i>.html` + one `route-<i>.json` per
validated route into the output directory. The HTML inlines the vendored Leaflet
+ Chart.js assets (`templates/assets/`) so each report is a portable file with no
runtime CDN dependency for its libraries; the OSM basemap tiles are still fetched
live when the report is opened in a browser.

Geometry note: `Edge` (models.py) deliberately carries no geometry — the resampled
`(lat, lon, elevation_m)` vertices live on the base operational graph's edges
(`vertices_resampled`, pipeline/dem.py). A route's edges are *contracted-graph*
edges, so a super-edge is expanded to its base edges via `ContractedGraph.
super_edge_to_base` and each base edge (and each plain connector) is then resolved
against `base_graph`. This is why `render` takes the graphs even though the
Architecture §Cat 9 sketch omitted them — `Edge` alone cannot produce a polyline.
"""

from __future__ import annotations

import importlib.resources
import math
import pathlib
from dataclasses import asdict
from typing import Any, Literal

import jinja2

from steeproute.cache import write_json_atomic, write_text_atomic
from steeproute.models import (
    ContractedGraph,
    ProvenanceInfo,
    Route,
    SolverParams,
    ValidatedRouteSet,
)

# Pinned vendored-asset versions, surfaced in every report's metadata block so a
# future "why does this report render differently?" investigation can pin the
# library revision (Architecture §Cat 9). The filenames embed the version.
LEAFLET_VERSION: str = "1.9.4"
CHARTJS_VERSION: str = "4.4.0"

_LEAFLET_JS_ASSET: str = f"leaflet-{LEAFLET_VERSION}.min.js"
_LEAFLET_CSS_ASSET: str = f"leaflet-{LEAFLET_VERSION}.min.css"
_CHARTJS_JS_ASSET: str = f"chart-{CHARTJS_VERSION}.min.js"

_TEMPLATE_NAME: str = "route.html.j2"

ConvergenceStatus = Literal["converged", "budget-exhausted", "interrupted"]


def render(
    validated_set: ValidatedRouteSet,
    base_graph: Any,
    contracted: ContractedGraph,
    params: SolverParams,
    provenance: ProvenanceInfo,
    convergence: ConvergenceStatus,
    output_dir: pathlib.Path,
) -> None:
    """Write `route-<i>.{html,json}` for every route in `validated_set`.

    Files are 1-indexed (`route-1`, `route-2`, ...; FR21) and written atomically
    (`.tmp` sibling + `os.replace()` via `cache.write_*_atomic`), so a Ctrl-C
    mid-render never leaves a half-written report. Existing files with matching
    names are overwritten in place (idempotent re-runs).

    Args:
        validated_set: validator output (Story 3.9) — routes + set-level Jaccard
            violations. Drives both the per-route reports and the banner logic.
        base_graph: the post-stage-7 operational `MultiDiGraph` carrying the
            `vertices_resampled` edge attribute used for map + profile geometry.
        contracted: the `ContractedGraph` the solver/validator ran on; its
            `super_edge_to_base` expands super-edges to their base edges.
        params: solver parameters, recorded verbatim in the metadata block.
        provenance: run provenance (version, commit, OSM/DEM/pipeline fingerprint).
        convergence: solver convergence status, recorded in the metadata block.
        output_dir: destination directory (created if missing).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    env = _jinja_env()
    template = env.get_template(_TEMPLATE_NAME)
    metadata = _build_metadata(params, provenance, convergence)
    leaflet_css = _load_asset(_LEAFLET_CSS_ASSET)
    leaflet_js = _load_asset(_LEAFLET_JS_ASSET)
    chart_js = _load_asset(_CHARTJS_JS_ASSET)

    for idx0, route in enumerate(validated_set.routes):
        display_index = idx0 + 1
        vertices = _route_vertices(route, base_graph, contracted.super_edge_to_base)
        edge_ids = [[e.node_u, e.node_v, e.key] for e in route.edges]
        pairwise = _pairwise_for(validated_set, idx0)
        metrics = {
            "length_m": route.metrics.length_m,
            "d_plus_m": route.metrics.d_plus_m,
            "d_minus_m": route.metrics.d_minus_m,
            "avg_gradient": route.metrics.avg_gradient,
        }
        validation = {
            "passed": route.validation.passed,
            "violations": [asdict(v) for v in route.validation.violations],
        }
        show_banner = (not route.validation.passed) or bool(pairwise)

        distances, elevations = _profile_series(vertices)
        html = template.render(
            route_index=display_index,
            metadata=metadata,
            metrics=metrics,
            validation=validation,
            pairwise=pairwise,
            show_banner=show_banner,
            leaflet_css=leaflet_css,
            leaflet_js=leaflet_js,
            chart_js=chart_js,
            # A degenerate route (< 2 resolved vertices — e.g. a validation-failed
            # route whose edges aren't in the graph) can't drive a map or profile;
            # the template omits both and the inline init script when this is False,
            # so `L.geoJSON([]).getBounds()` / `fitBounds` never throw.
            has_geometry=len(vertices) >= 2,
            route_geojson=_geojson(vertices),
            profile_distances=distances,
            profile_elevations=elevations,
        )
        sidecar = {
            "route_index": display_index,
            "metadata": metadata,
            "metrics": metrics,
            "validation": {**validation, "pairwise_violations": pairwise},
            "edges": edge_ids,
            "vertices": [list(v) for v in vertices],
        }
        write_text_atomic(output_dir / f"route-{display_index}.html", html)
        write_json_atomic(output_dir / f"route-{display_index}.json", sidecar)


def _jinja_env() -> jinja2.Environment:
    """Jinja2 environment loading from the `steeproute.templates` package.

    `PackageLoader` resolves the template through `importlib` so it works from
    an installed wheel as well as the source tree. Autoescape is on; the inlined
    asset blobs and embedded JSON are passed through `| safe` / `| tojson` in
    the template, never auto-escaped.
    """
    return jinja2.Environment(
        loader=jinja2.PackageLoader("steeproute", "templates"),
        autoescape=jinja2.select_autoescape(["html", "j2"]),
    )


def _load_asset(name: str) -> str:
    """Read a vendored asset's text from `steeproute/templates/assets/`."""
    assets = importlib.resources.files("steeproute") / "templates" / "assets"
    return (assets / name).read_text(encoding="utf-8")


def _build_metadata(
    params: SolverParams,
    provenance: ProvenanceInfo,
    convergence: ConvergenceStatus,
) -> dict[str, Any]:
    """Assemble the metadata block shared verbatim by the HTML and JSON surfaces.

    Building it once and feeding the same dict to both the Jinja2 context and
    `json.dumps` is what guarantees the HTML and JSON mirror each other
    (Architecture §Cat 9 — "HTML + JSON carry the same metadata in parallel").
    `git_commit` collapses `git_commit_short` + the `-dirty` flag into the single
    string the report shows.
    """
    git_commit = (
        f"{provenance.git_commit_short}-dirty"
        if provenance.git_dirty
        else provenance.git_commit_short
    )
    return {
        "params": asdict(params),
        "provenance": {
            "steeproute_version": provenance.steeproute_version,
            "git_commit": git_commit,
            "osm_extract_date": provenance.osm_extract_date,
            "dem_version": provenance.dem_version,
            "pipeline_content_hash": provenance.pipeline_content_hash,
        },
        "convergence_status": convergence,
        "assets": {"leaflet": LEAFLET_VERSION, "chart_js": CHARTJS_VERSION},
    }


def _pairwise_for(validated_set: ValidatedRouteSet, idx0: int) -> list[dict[str, Any]]:
    """Set-level Jaccard violations referencing route `idx0` (0-based positional).

    `other_index` is the 1-based display index of the *other* route in the pair,
    for banner copy ("too similar to route N").
    """
    out: list[dict[str, Any]] = []
    for pv in validated_set.set_violations:
        if idx0 not in (pv.route_index_a, pv.route_index_b):
            continue
        other0 = pv.route_index_b if pv.route_index_a == idx0 else pv.route_index_a
        out.append(
            {
                "other_index": other0 + 1,
                "jaccard_observed": pv.jaccard_observed,
                "jaccard_max": pv.jaccard_max,
            }
        )
    return out


def _route_vertices(
    route: Route,
    base_graph: Any,
    super_edge_to_base: dict[tuple[int, int, int], Any],
) -> list[tuple[float, float, float]]:
    """Ordered `(lat, lon, elevation_m)` vertices along the route.

    Super-edges (identity present in `super_edge_to_base`) are expanded to their
    base edges; every base edge and every plain connector is resolved against
    `base_graph`'s `vertices_resampled`. Consecutive exact-duplicate vertices
    (the shared node at an edge join) are dropped.
    """
    out: list[tuple[float, float, float]] = []
    for edge in route.edges:
        identity = (edge.node_u, edge.node_v, edge.key)
        if identity in super_edge_to_base:
            for base in super_edge_to_base[identity]:
                _extend_dedup(out, _edge_vertices(base_graph, base.node_u, base.node_v, base.key))
        else:
            _extend_dedup(out, _edge_vertices(base_graph, edge.node_u, edge.node_v, edge.key))
    return out


def _edge_vertices(base_graph: Any, u: int, v: int, key: int) -> list[tuple[float, float, float]]:
    """`vertices_resampled` for one base-graph edge as `(lat, lon, elev)` tuples.

    Returns `[]` when the edge identity is absent from `base_graph` or carries no
    `vertices_resampled`. A validation-failed route (e.g. a `graph_membership`
    violation) can reference edges that don't exist in the operational graph, and
    FR28 requires such routes to still render with a banner — so a missing edge
    yields empty geometry here, never a `KeyError` that would abort the render of
    every route.
    """
    try:
        data = base_graph[u][v][key]
    except KeyError:
        return []
    verts = data.get("vertices_resampled")
    if not verts:
        return []
    return [(float(lat), float(lon), float(elev)) for (lat, lon, elev) in verts]


def _extend_dedup(
    acc: list[tuple[float, float, float]], verts: list[tuple[float, float, float]]
) -> None:
    """Append `verts` to `acc`, skipping a leading vertex equal to `acc`'s last."""
    for vert in verts:
        if acc and acc[-1] == vert:
            continue
        acc.append(vert)


def _geojson(vertices: list[tuple[float, float, float]]) -> dict[str, Any]:
    """A GeoJSON `FeatureCollection` with one LineString in `[lon, lat]` order."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for (lat, lon, _elev) in vertices],
                },
            }
        ],
    }


def _profile_series(
    vertices: list[tuple[float, float, float]],
) -> tuple[list[float], list[float]]:
    """Cumulative ground distance (m) and elevation (m) along the route.

    Distance accumulates great-circle hops between consecutive vertices; the
    template colors each profile segment by its rise/run gradient.
    """
    distances: list[float] = []
    elevations: list[float] = []
    cumulative = 0.0
    prev: tuple[float, float, float] | None = None
    for lat, lon, elev in vertices:
        if prev is not None:
            cumulative += _haversine_m(prev[0], prev[1], lat, lon)
        distances.append(round(cumulative, 1))
        elevations.append(round(elev, 1))
        prev = (lat, lon, elev)
    return distances, elevations


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    radius_m = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * radius_m * math.asin(math.sqrt(a))
