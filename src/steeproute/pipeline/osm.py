# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx + osmnx ship partial / no type stubs; MultiDiGraph operations
# surface as Unknown. Architecture §Type hints lists OSM as an external boundary.
"""Pipeline stages 1-2: OSM ingestion (osmnx) and trail filtering."""

from __future__ import annotations

import math

import networkx as nx
import osmnx
import requests
import shapely

from steeproute.errors import BadCLIArgError, DataSourceUnavailableError
from steeproute.models import Area

# OSM sac_scale tag values mapped to numeric T-rank for difficulty-cap filtering.
# https://wiki.openstreetmap.org/wiki/Key:sac_scale
SAC_SCALE_RANK: dict[str, int] = {
    "hiking": 1,
    "mountain_hiking": 2,
    "demanding_mountain_hiking": 3,
    "alpine_hiking": 4,
    "demanding_alpine_hiking": 5,
    "difficult_alpine_hiking": 6,
}

# OSM highway tags treated as trails. cycleway is bike-only; service/residential
# /etc are roads. After this set, untagged-trails-policy + sac_scale cap further
# narrow the surviving edges.
TRAIL_HIGHWAY_TAGS: frozenset[str] = frozenset({"path", "footway", "track", "steps", "bridleway"})

# Overpass `way[highway~"..."]` filter that osmnx applies during the fetch.
# Keep aligned with TRAIL_HIGHWAY_TAGS so we don't pay for ways we'll drop anyway.
_OSM_CUSTOM_FILTER = '["highway"~"path|footway|track|steps|bridleway"]'


def osm_load(area: Area) -> nx.MultiDiGraph:
    """Stage 1: fetch the OSM trail network for `area` from Overpass via osmnx.

    `area.radius_km` is the bbox half-side, not a disk radius — see `Area` docstring.

    Returns a MultiDiGraph where every edge satisfies the source-attribute
    contract (Architecture §Cat 3): `sac_scale` (str | None), `highway`
    (str | list[str] | None), `osm_way_id` (int | list[int]), `geometry`
    (shapely.LineString — synthesized from endpoint coords for edges osmnx
    omits one for after simplification).

    Raises:
        BadCLIArgError: if `area.radius_km` is non-positive or non-finite, or
            `area.center` falls outside lat ∈ [-90, 90] / lon ∈ [-180, 180].
        DataSourceUnavailableError: Overpass unreachable, request timeout, HTTP
            error, or low-level I/O failure during the `osmnx.graph_from_point`
            call. The original exception is chained via `raise ... from exc` so
            `--verbose` can surface its `repr` on the detail line.
    """
    _validate_area(area)
    _ensure_sac_scale_in_useful_tags()
    try:
        raw = osmnx.graph_from_point(
            center_point=area.center,
            dist=area.radius_km * 1000.0,
            dist_type="bbox",
            custom_filter=_OSM_CUSTOM_FILTER,
            retain_all=False,
            simplify=True,
        )
    except (requests.exceptions.RequestException, OSError, ValueError) as exc:
        # `RequestException` is the base of every `requests` failure (ConnectionError,
        # Timeout, HTTPError, ...) — the documented network-failure modes osmnx propagates
        # from its HTTP backend. `OSError` covers the truly low-level cases (network
        # filesystem hiccups, exhausted file descriptors, etc.).
        #
        # `ValueError` is the contract-correct catch for osmnx's own error classes:
        # `osmnx._errors.ResponseStatusCodeError(ValueError)` fires on non-`ok` HTTP
        # responses (e.g., 5xx Overpass outages — the canonical "source unavailable"
        # scenario this wrap is meant to map to exit 2). `InsufficientResponseError`
        # and `GraphSimplificationError` likewise inherit from `ValueError`. Catching
        # `ValueError` at THIS specific call site is safe because `_validate_area`
        # above already rejected malformed arguments — by elimination, any `ValueError`
        # surfacing from `osmnx.graph_from_point` is a server-response interpretation
        # failure, not a programming error. (Catching `ValueError` at the module level
        # would mask real bugs; constraining it to this call site preserves type-error
        # diagnostics elsewhere.)
        raise DataSourceUnavailableError(
            "OSM source unreachable.",
            detail=f"osmnx.graph_from_point failed: {exc!r}",
        ) from exc
    return normalize_edges(raw)


def _validate_area(area: Area) -> None:
    """Reject Areas that would produce nonsense fetches (out-of-range coords, NaN, Inf, ≤0 radius)."""
    if not math.isfinite(area.radius_km):
        raise BadCLIArgError(
            f"--radius must be finite (got {area.radius_km})",
            detail="NaN and Inf radii produce undefined osmnx behavior.",
        )
    if area.radius_km <= 0:
        raise BadCLIArgError(
            f"--radius must be > 0 (got {area.radius_km})",
            detail="Area construction needs a positive bbox half-side to define a search box.",
        )
    lat, lon = area.center
    if not math.isfinite(lat) or not math.isfinite(lon):
        raise BadCLIArgError(
            f"--center coordinates must be finite (got {area.center})",
        )
    if not -90.0 <= lat <= 90.0:
        raise BadCLIArgError(
            f"--center latitude {lat} is outside [-90, 90]",
        )
    if not -180.0 <= lon <= 180.0:
        raise BadCLIArgError(
            f"--center longitude {lon} is outside [-180, 180]",
        )


def filter_trails(
    graph: nx.MultiDiGraph,
    untagged_policy: str,
    difficulty_cap: str,
) -> nx.MultiDiGraph:
    """Stage 2: drop edges that aren't trails, exceed the SAC cap, or fail the policy.

    Args:
        graph: input graph from `osm_load` (or test fixture).
        untagged_policy: "include" to keep edges with `sac_scale=None`,
            "exclude" to drop them.
        difficulty_cap: "T1".."T6" (case-insensitive); edges whose `sac_scale`
            ranks strictly above the cap are dropped.

    Returns:
        New MultiDiGraph; the input graph is never mutated.
    """
    if untagged_policy not in {"include", "exclude"}:
        raise BadCLIArgError(
            f"--untagged-trails must be 'include' or 'exclude' (got {untagged_policy!r})"
        )
    cap_rank = parse_difficulty_cap(difficulty_cap)

    out = graph.copy()
    edges_to_drop: list[tuple[int, int, int]] = []
    for u, v, k, data in out.edges(data=True, keys=True):
        if not _is_trail_highway(data.get("highway")):
            edges_to_drop.append((u, v, k))
            continue
        sac = data.get("sac_scale")
        if sac is None:
            if untagged_policy == "exclude":
                edges_to_drop.append((u, v, k))
            continue
        rank = max_sac_rank(sac)
        if rank is None or rank > cap_rank:
            edges_to_drop.append((u, v, k))
    for u, v, k in edges_to_drop:
        out.remove_edge(u, v, k)
    return out


def _ensure_sac_scale_in_useful_tags() -> None:
    """osmnx's default useful_tags_way drops sac_scale; ensure it's preserved.

    Without this, every fetched edge has sac_scale=None regardless of how the
    area is tagged in OSM — silently breaking filter_trails downstream.
    """
    if "sac_scale" not in osmnx.settings.useful_tags_way:
        osmnx.settings.useful_tags_way = list(osmnx.settings.useful_tags_way) + ["sac_scale"]


def normalize_edges(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Bring osmnx's edge attributes into the source-attribute contract shape.

    Renames `osmid` -> `osm_way_id`; synthesizes missing `geometry` from
    endpoint node coords; defaults `sac_scale` to None when absent. Mutates
    `graph` in place — caller (`osm_load`) owns the freshly-fetched graph.
    """
    for u, v, _k, data in graph.edges(data=True, keys=True):
        if "osmid" in data:
            data["osm_way_id"] = data.pop("osmid")
        if data.get("geometry") is None:
            u_xy = (graph.nodes[u]["x"], graph.nodes[u]["y"])
            v_xy = (graph.nodes[v]["x"], graph.nodes[v]["y"])
            data["geometry"] = shapely.LineString([u_xy, v_xy])
        if "sac_scale" not in data:
            data["sac_scale"] = None
    return graph


def parse_difficulty_cap(cap: str) -> int:
    """Map 'T1'..'T6' (case-insensitive, whitespace-tolerant) to numeric rank 1..6."""
    normalized = cap.strip().upper()
    if len(normalized) != 2 or normalized[0] != "T" or not normalized[1].isdigit():
        raise BadCLIArgError(f"--difficulty-cap must be 'T1'..'T6' (got {cap!r})")
    rank = int(normalized[1])
    if not 1 <= rank <= 6:
        raise BadCLIArgError(f"--difficulty-cap must be 'T1'..'T6' (got {cap!r})")
    return rank


def _is_trail_highway(value: object) -> bool:
    """True if `value` (str or list[str] from osmnx) names a trail-style highway.

    osmnx-merged edges carry list-valued highway when chained ways had different
    tags. We're permissive here: an edge counts as a trail if any constituent
    way is a trail (the question is "should this edge be in the trail graph?",
    and any trail tag is sufficient evidence).
    """
    if isinstance(value, str):
        return value in TRAIL_HIGHWAY_TAGS
    if isinstance(value, list):
        return any(isinstance(v, str) and v in TRAIL_HIGHWAY_TAGS for v in value)
    return False


def max_sac_rank(value: object) -> int | None:
    """Return the most-demanding SAC rank for a sac_scale value, or None if unknown.

    Opposite policy from `_is_trail_highway`: for difficulty bounds we take the
    maximum (most demanding) over a list-valued sac_scale, because if any
    constituent way requires harder hiking, the user shouldn't be routed onto
    the merged edge under a lower difficulty cap. Any unrecognized component
    poisons the whole edge to None (conservative drop).

    Whitespace-tolerant: real-world OSM tags occasionally include trailing
    whitespace (`"hiking "`); we strip before lookup to avoid silent drops.
    """
    if isinstance(value, str):
        return SAC_SCALE_RANK.get(value.strip())
    if isinstance(value, list):
        ranks: list[int] = []
        for v in value:
            if not isinstance(v, str):
                return None
            r = SAC_SCALE_RANK.get(v.strip())
            if r is None:
                return None
            ranks.append(r)
        return max(ranks) if ranks else None
    return None
