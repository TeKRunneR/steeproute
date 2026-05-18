# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx operations surface as Unknown; same external-boundary pattern
# as pipeline/osm.py, pipeline/smoothing.py, pipeline/dem.py.
"""Pipeline stage 7: per-edge length / elevation-gain / elevation-loss / avg-gradient.

Reads each edge's `vertices_resampled: list[tuple[lat, lon, elevation_m]]`
(set by stage 5, smoothed by stage 6) and attaches four numeric metrics:

- `length_m` — cumulative 2D ground-distance along the polyline. Computed from
  the `(lat, lon)` components using the same local-equirectangular projection
  as `pipeline.smoothing._resample_meters` (cos-of-mean-latitude correction),
  so distances are in real meters with sub-‰ drift over edge-scale lengths.
- `d_plus_m` — sum of strictly positive elevation deltas between consecutive
  vertices. Always ≥ 0.
- `d_minus_m` — sum of the absolute values of strictly negative elevation
  deltas. Always ≥ 0 (positive magnitude, matching the manifest sample in
  Architecture §Cat 4 where `"d_plus_m"` and `"d_minus_m"` are both positive).
- `avg_gradient` — `(d_plus_m + d_minus_m) / length_m`. Total absolute altitude
  change per horizontal meter; dimensionless and ≥ 0. Not a signed slope.

Stage 8 (climb detection) will also live in this module; it lands in Story 3.2.
"""

from __future__ import annotations

import math

import networkx as nx

# WGS84 equatorial radius for the local equirectangular projection. Same value
# and rationale as `pipeline.smoothing._EARTH_RADIUS_M` — duplicated rather than
# imported so each pipeline module is self-contained against a physical constant.
_EARTH_RADIUS_M: float = 6_378_137.0


def compute_edge_metrics(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Stage 7: attach `length_m`, `d_plus_m`, `d_minus_m`, `avg_gradient` per edge.

    Args:
        graph: input MultiDiGraph; every edge must carry `vertices_resampled`
            from stages 5-6 with ≥ 2 entries of finite `(lat, lon, elevation_m)`.

    Returns:
        A new MultiDiGraph; the input is never mutated. Upstream attributes
        (`geometry`, `vertices_resampled`, `sac_scale`, `highway`, `osm_way_id`)
        are carried through unchanged.
    """
    out: nx.MultiDiGraph = graph.copy()
    for _u, _v, _k, data in out.edges(data=True, keys=True):
        verts: list[tuple[float, float, float]] = data["vertices_resampled"]
        length_m = _cumulative_2d_distance_m(verts)
        d_plus_m, d_minus_m = _elevation_gain_loss(verts)
        # length_m > 0 is guaranteed by stage 4's degenerate-edge drop; we
        # express the contract as a postcondition rather than as defensive
        # guards inside the loop.
        avg_gradient = (d_plus_m + d_minus_m) / length_m
        data["length_m"] = length_m
        data["d_plus_m"] = d_plus_m
        data["d_minus_m"] = d_minus_m
        data["avg_gradient"] = avg_gradient
    return out


def is_valid_for_metrics(verts: list[tuple[float, float, float]]) -> bool:
    """True if `verts` is a valid stage-7 input (≥ 1 non-degenerate consecutive pair, all finite).

    Public so the hypothesis property test in `tests/unit/test_climbs.py` can
    use the same validity check as production via `hypothesis.assume`, avoiding
    drift between the strategy filter and the actual stage's input contract.

    Validity here means "the polyline has non-zero 2D length", which is what
    stage 4 enforces upstream by dropping degenerate edges. We check it
    structurally by requiring that at least one consecutive `(lat, lon)` pair
    differs — that is the precondition `compute_edge_metrics` needs to avoid
    a `ZeroDivisionError` in `avg_gradient`. Checking only against `verts[0]`
    is weaker: `[(0,0), (1,1), (0,0)]` passes by the first-vertex check yet
    `[(0,0), (0,0), (0,0)]` would also pass it if `verts[1]` happened to
    differ; the consecutive-pair check matches the documented contract.
    """
    if len(verts) < 2:
        return False
    if not all(
        math.isfinite(lat) and math.isfinite(lon) and math.isfinite(elev)
        for lat, lon, elev in verts
    ):
        return False
    return any(
        (verts[i][0], verts[i][1]) != (verts[i - 1][0], verts[i - 1][1])
        for i in range(1, len(verts))
    )


def _cumulative_2d_distance_m(verts: list[tuple[float, float, float]]) -> float:
    """Cumulative ground-distance in meters along the `(lat, lon)` polyline.

    Local equirectangular projection at the polyline's mean latitude — the same
    pattern as `pipeline.smoothing._resample_meters`. Accurate to ~0.1% over
    edge-scale distances; no external projection dependency.
    """
    mean_lat = sum(lat for lat, _lon, _elev in verts) / len(verts)
    deg_to_m_lat = _EARTH_RADIUS_M * math.radians(1.0)
    deg_to_m_lon = deg_to_m_lat * math.cos(math.radians(mean_lat))
    total = 0.0
    for i in range(1, len(verts)):
        dlat = (verts[i][0] - verts[i - 1][0]) * deg_to_m_lat
        dlon = (verts[i][1] - verts[i - 1][1]) * deg_to_m_lon
        total += math.hypot(dlat, dlon)
    return total


def _elevation_gain_loss(verts: list[tuple[float, float, float]]) -> tuple[float, float]:
    """Return `(d_plus_m, d_minus_m)` from consecutive elevation deltas.

    `d_plus_m` is the sum of strictly positive deltas. `d_minus_m` is the sum
    of the absolute values of strictly negative deltas — positive magnitude,
    not a signed quantity.
    """
    d_plus = 0.0
    d_minus = 0.0
    for i in range(1, len(verts)):
        delta = verts[i][2] - verts[i - 1][2]
        if delta > 0:
            d_plus += delta
        elif delta < 0:
            d_minus += -delta
    return d_plus, d_minus
