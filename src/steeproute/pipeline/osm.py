# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx + osmnx ship partial / no type stubs; MultiDiGraph operations
# surface as Unknown. Architecture §Type hints lists OSM as an external boundary.
"""Pipeline stages 1-2: OSM ingestion (osmnx) and trail filtering.

This module's raw bytes are part of the prepared-cache key
(`cache._PIPELINE_CONTENT_GLOBS`), so any edit here — comments included —
re-keys subsequent setup runs.
"""

from __future__ import annotations

import math

import networkx as nx
import shapely

# NOTE: osmnx (which transitively drags in geopandas + pandas, ~2 s), requests and
# truststore are imported lazily inside the fetch path (`osm_load` /
# `_ensure_sac_scale_in_useful_tags`) — they serve only stage 1. `pipeline/__init__`
# imports this module, so every consumer of any pipeline submodule (including
# spawned solver workers that only need `max_sac_rank`/`parse_difficulty_cap`)
# would otherwise pay the full fetch stack at import time — ~4 s of a ~5 s
# per-worker import chain, measured at r20 (2026-07-14).
from steeproute.errors import BadCLIArgError, DataSourceUnavailableError
from steeproute.models import Area
from steeproute.pipeline._common import empty_like
from steeproute.pipeline._osmnx_adapter import osmnx_owned_intermediates

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

# OSM highway tags treated as trails. cycleway is bike-only. After this set,
# untagged-trails-policy + sac_scale cap further narrow the surviving edges.
TRAIL_HIGHWAY_TAGS: frozenset[str] = frozenset({"path", "footway", "track", "steps", "bridleway"})

# OSM highway tags admitted as *connectors* — short paved links between trails.
# Roads carry no sac_scale, so they bypass the SAC cap and the untagged-trails
# policy, and they are never climbs (stage 8 is gradient-driven; roads are ~flat).
# They ride the length-based reuse-exemption (`reusable` iff
# `length_m < l_connector`) and the D+/D- objective, which self-limits road use
# to genuine links — there is deliberately no road-specific cost or reuse term.
# Major roads (motorway, primary, ...) and bike-only cycleway are excluded.
MINOR_ROAD_HIGHWAY_TAGS: frozenset[str] = frozenset(
    {"residential", "unclassified", "service", "living_street", "tertiary"}
)

# Overpass `way[highway~"..."]` filter that osmnx applies during the fetch. Built
# from the trail + minor-road sets so the fetch stays aligned with filter_trails
# — we don't pay for ways we'll drop, nor drop ways we never fetched.
_OSM_CUSTOM_FILTER = '["highway"~"{}"]'.format(
    "|".join(sorted(TRAIL_HIGHWAY_TAGS | MINOR_ROAD_HIGHWAY_TAGS))
)


def osm_load(area: Area) -> nx.MultiDiGraph:
    """Stage 1: fetch the OSM trail network for `area` from Overpass via osmnx.

    Dispatches on the area's shape:

    - **Square** (`Area.is_square`) — `osmnx.graph_from_point(dist_type="bbox")`
      off `radius_km` (the bbox half-side, not a disk radius; see `Area`). Do
      **not** unify this with the polygon branch below: osmnx derives its bbox with
      its own `EARTH_RADIUS_M = 6_371_009` (~1/111.195 deg/km) while
      `cache.area_polygon` uses `1/111`, so feeding our ring in here widens the
      fetch ~0.18% and silently shifts every pinned regression golden.
    - **Non-square / rotated** — `osmnx.graph_from_polygon` over the shared
      `cache.area_polygon(area)` ring, so only the rectangle's own footprint is
      pre-processed and off-axis valley never enters setup. This reuses osmnx's
      existing `truncate_graph_polygon` path (the bbox mode already goes
      bbox→polygon→truncate internally) and narrows the Overpass query too — a
      polygon fetch is sent as a `poly:` filter, not a bbox.

    Returns a MultiDiGraph where every edge satisfies the source-attribute
    contract (Architecture §Cat 3): `sac_scale` (str | None), `highway`
    (str | list[str] | None), `osm_way_id` (int | list[int]), `geometry`
    (shapely.LineString — synthesized from endpoint coords for edges osmnx
    omits one for after simplification).

    Raises:
        BadCLIArgError: if either effective half-extent is non-positive or
            non-finite, the bearing angle is non-finite, or `area.center` falls
            outside lat ∈ [-90, 90] / lon ∈ [-180, 180].
        DataSourceUnavailableError: Overpass unreachable, request timeout, HTTP
            error, or low-level I/O failure during the osmnx fetch (either
            branch). The original exception is chained via `raise ... from exc`
            so `--verbose` can surface its `repr` on the detail line.
    """
    # Deferred fetch-stack imports — see the module-level NOTE above the imports.
    import osmnx
    import requests
    import truststore

    from steeproute.cache import area_polygon

    _validate_area(area)
    _ensure_sac_scale_in_useful_tags()
    # Verify Overpass's TLS against the OS trust store rather than certifi's
    # vendored bundle, so a corporate TLS-intercepting proxy whose root CA is
    # installed in the OS store (but not in certifi) Just Works — and harmless
    # where the OS store mirrors certifi. Mirrors `dem_download._fetch_mosaic`
    # and the integration-test conftest; without it the OSM fetch fails with
    # CERTIFICATE_VERIFY_FAILED while the DEM download (which injects) succeeds.
    truststore.inject_into_ssl()
    fetch_call = "graph_from_point" if area.is_square else "graph_from_polygon"
    try:
        # Inside the adapter osmnx assembles the graph without copying the
        # intermediates it has finished with (`pipeline._osmnx_adapter`). Graph
        # content and iteration order are unchanged — proven by an old/new diff over
        # a real r20 response, not by inspection; two of osmnx's `--verbose` lines
        # about spatial-index internals go away. On an osmnx version the adapter
        # hasn't been verified against it steps aside and stock osmnx runs.
        with osmnx_owned_intermediates():
            if area.is_square:
                # `half_extents_km[0]` (not `radius_km`) so a square expressed as
                # explicit equal extents fetches its real size. For the shorthand
                # `Area(center=..., radius_km=r)` the two are the same float, so this
                # never perturbs a square area's fetch.
                raw = osmnx.graph_from_point(
                    center_point=area.center,
                    dist=area.half_extents_km[0] * 1000.0,
                    dist_type="bbox",
                    custom_filter=_OSM_CUSTOM_FILTER,
                    retain_all=False,
                    simplify=True,
                )
            else:
                raw = osmnx.graph_from_polygon(
                    area_polygon(area),
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
        # surfacing from the osmnx fetch is a server-response interpretation failure,
        # not a programming error. (Catching `ValueError` at the module level would
        # mask real bugs; constraining it to this call site preserves type-error
        # diagnostics elsewhere.) `graph_from_polygon` additionally raises
        # `ValueError` for an invalid polygon, which `_validate_area` has already
        # ruled out for every reachable area.
        raise DataSourceUnavailableError(
            "OSM source unreachable.",
            detail=f"osmnx.{fetch_call} failed: {exc!r}",
        ) from exc
    return normalize_edges(raw)


def _validate_area(area: Area) -> None:
    """Reject Areas that would produce nonsense fetches (out-of-range coords, NaN, Inf, ≤0 extents).

    Validates the *effective* geometry, not `radius_km` alone: a rotated area
    carries an inert `radius_km=0.0` and drives the fetch entirely off its
    half-extents + bearing.

    Diagnostics are reported against the flag the user actually typed. Which one
    that is comes from **whether the extents were supplied**, not from
    `Area.is_square` and not from the bearing:

    - `is_square` compares the two extents, and `nan == nan` is False, so a NaN
      `--radius` would be misreported as a width problem.
    - The bearing is orthogonal to how the size was spelled — a rotated *square*
      is a legitimate `--radius --angle` combination, so a bad radius there must
      still say `--radius`. The angle is therefore validated for every shape,
      before the size at all.
    """
    if not math.isfinite(area.angle_deg):
        raise BadCLIArgError(
            f"--angle must be finite (got {area.angle_deg})",
            detail="A non-finite bearing yields NaN box corners and an unusable fetch polygon.",
        )
    if area.half_width_km is None and area.half_height_km is None:
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
    else:
        half_width_km, half_height_km = area.half_extents_km
        for flag, half_extent_km in (("--width", half_width_km), ("--height", half_height_km)):
            if not math.isfinite(half_extent_km):
                raise BadCLIArgError(
                    f"{flag} must be finite (got a half-extent of {half_extent_km})",
                    detail="NaN and Inf extents produce undefined osmnx behavior.",
                )
            if half_extent_km <= 0:
                raise BadCLIArgError(
                    f"{flag} must be > 0 (got a half-extent of {half_extent_km})",
                    detail=(
                        "Area construction needs positive half-extents to define a search box."
                    ),
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
    *,
    consume: bool = False,
) -> nx.MultiDiGraph:
    """Stage 2: drop edges that aren't trails, exceed the SAC cap, or fail the policy.

    Args:
        graph: input graph from `osm_load` (or test fixture).
        untagged_policy: "include" to keep edges with `sac_scale=None`,
            "exclude" to drop them.
        difficulty_cap: "T1".."T6" (case-insensitive); edges whose `sac_scale`
            ranks strictly above the cap are dropped.
        consume: when True, remove the rejected edges from `graph` in place and
            return `graph` itself instead of building a new graph. The caller then
            forfeits the *unfiltered* graph: after the call there is only the
            filtered one. Only safe when the caller owns `graph` exclusively and
            never needs the rejected edges again. Default False keeps the pure
            "input never mutated" contract every other caller (and the purity
            test) relies on.

    Returns:
        The filtered graph — a new MultiDiGraph, leaving the input untouched;
        or, with `consume=True`, `graph` itself with the rejected edges removed.
    """
    if untagged_policy not in {"include", "exclude"}:
        raise BadCLIArgError(
            f"--untagged-trails must be 'include' or 'exclude' (got {untagged_policy!r})"
        )
    cap_rank = parse_difficulty_cap(difficulty_cap)

    def keep(data: dict[str, object]) -> bool:
        kind = classify_highway(data.get("highway"))
        if kind == "trail":
            sac = data.get("sac_scale")
            if sac is None:
                return untagged_policy != "exclude"
            rank = max_sac_rank(sac)
            return rank is not None and rank <= cap_rank
        if kind == "connector":
            # Roads aren't subject to the untagged policy (they carry no SAC
            # grade by nature). A road that *does* carry an over-cap sac_scale
            # respects the difficulty cap, like a trail; an untagged or
            # unrecognized-sac road is admitted as a connector.
            rank = max_sac_rank(data.get("sac_scale"))
            return rank is None or rank <= cap_rank
        return False

    if consume:
        # Remove-the-rejects, not build-the-survivors. The two branches differ only
        # in which side is the minority: a query-side re-filter at the user's
        # difficulty cap rejects a small fraction of an already-filtered graph, so
        # removing those beats rebuilding the whole graph to keep the rest
        # (~0.3 s against 4.6 s on an r20 graph, 2026-07-24). Removal preserves the
        # surviving edges' insertion order, so the result is order-identical to the
        # rebuild below. Nodes are never removed here either — orphan pruning stays
        # the orchestrator's job.
        rejected = [
            (u, v, k) for u, v, k, data in graph.edges(data=True, keys=True) if not keep(data)
        ]
        graph.remove_edges_from(rejected)
        return graph

    # Build the output from the KEPT edges rather than copy-then-remove: a
    # setup-side stage-2 pass drops most edges (all non-trail/non-connector,
    # over-cap, excluded-untagged), so copying them only to remove them is the
    # dominant cost. All nodes carry over (orphan pruning is the orchestrator's
    # job); edge/node iteration order is preserved.
    out = empty_like(graph)
    for u, v, k, data in graph.edges(data=True, keys=True):
        if keep(data):
            out.add_edge(u, v, key=k, **data)
    return out


def _ensure_sac_scale_in_useful_tags() -> None:
    """osmnx's default useful_tags_way drops sac_scale; ensure it's preserved.

    Without this, every fetched edge has sac_scale=None regardless of how the
    area is tagged in OSM — silently breaking filter_trails downstream.
    """
    import osmnx  # deferred — see the module-level NOTE above the imports

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


def _highway_tags(value: object) -> tuple[list[str], bool]:
    """Normalize an osmnx `highway` value to `(stripped_str_tags, had_non_str_member)`.

    osmnx yields a `str` for a simple way or a `list` for chained/merged ways.
    Tags are whitespace-stripped to match `max_sac_rank`'s tolerance for dirty
    OSM values (e.g. `"service "`). The bool flags whether a list carried any
    non-string member — `classify_highway` uses it to veto road admission
    conservatively while leaving the permissive trail check unaffected.
    """
    if isinstance(value, str):
        return [value.strip()], False
    if isinstance(value, list):
        tags = [v.strip() for v in value if isinstance(v, str)]
        had_non_str = any(not isinstance(v, str) for v in value)
        return tags, had_non_str
    return [], False


def classify_highway(value: object) -> str | None:
    """Classify an OSM `highway` value as a routable `"trail"`, road `"connector"`, or None.

    One classifier, two deliberately-asymmetric multi-tag rules:

    - **Trail (permissive):** any constituent tag in `TRAIL_HIGHWAY_TAGS` makes
      the edge a trail — osmnx-merged edges should join the trail graph if any
      chained way is a trail. Trails win over roads, so a mixed
      `["service", "footway"]` is a trail.
    - **Connector (restrictive):** a road is admitted only if it carries a
      minor-road tag AND *every* tag is a minor road, with no non-string member.
      Any excluded tag — a major road like `motorway`, bike-only `cycleway` —
      vetoes it, so `["motorway", "service"]` is dropped.

    Returns None for anything else (the edge is dropped by `filter_trails`).
    """
    tags, had_non_str = _highway_tags(value)
    if not tags:
        return None
    if any(t in TRAIL_HIGHWAY_TAGS for t in tags):
        return "trail"
    if not had_non_str and all(t in MINOR_ROAD_HIGHWAY_TAGS for t in tags):
        return "connector"
    return None


def has_trail_highway(value: object) -> bool:
    """True iff `value` carries any trail tag (`TRAIL_HIGHWAY_TAGS`).

    Unlike `classify_highway`, this is an *independent presence* test, not a
    mutually-exclusive verdict: a mixed `["service", "footway"]` is both a road
    and a trail, so it answers `True` here and `True` to `has_road_highway`. The
    junction test (FR31, `pipeline.graph._annotate_junctions`) needs both senses
    separately, which `classify_highway`'s "trails win" tie-break would hide.
    """
    tags, _ = _highway_tags(value)
    return any(t in TRAIL_HIGHWAY_TAGS for t in tags)


def has_road_highway(value: object) -> bool:
    """True iff `value` carries any minor-road tag (`MINOR_ROAD_HIGHWAY_TAGS`).

    Independent presence test (see `has_trail_highway`): a mixed road+trail way
    answers `True` to both. No `had_non_str` veto — every edge that survived
    `filter_trails` into the contracted graph is already a clean trail or a clean
    minor road, so the conservative veto `classify_highway` applies at admission
    time has nothing left to reject here.
    """
    tags, _ = _highway_tags(value)
    return any(t in MINOR_ROAD_HIGHWAY_TAGS for t in tags)


def max_sac_rank(value: object) -> int | None:
    """Return the most-demanding SAC rank for a sac_scale value, or None if unknown.

    Opposite policy from `classify_highway`'s permissive trail rule: for
    difficulty bounds we take the maximum (most demanding) over a list-valued
    sac_scale, because if any
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
