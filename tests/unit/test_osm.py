# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: test loads networkx graphs from osmnx (untyped); same boundary as pipeline/osm.py.
"""Unit tests for pipeline.osm: osm_load (offline parts) + filter_trails."""

from __future__ import annotations

import pathlib

import networkx as nx
import osmnx
import pytest
import shapely

from steeproute.errors import BadCLIArgError
from steeproute.models import Area
from steeproute.pipeline.osm import (
    TRAIL_HIGHWAY_TAGS,
    filter_trails,
    max_sac_rank,
    normalize_edges,
    osm_load,
    parse_difficulty_cap,
)

_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "fixtures"
    / "grenoble_small"
    / "osm_graph.graphml"
)
_FIXTURE_SIZE_LIMIT_BYTES = 5_000_000  # AC #2: committed fixture must stay under 5 MB.


def test_committed_fixture_under_size_cap() -> None:
    """AC #2 mechanically enforced: a future regeneration that bloats the fixture fails CI."""
    size = _FIXTURE_PATH.stat().st_size
    assert size < _FIXTURE_SIZE_LIMIT_BYTES, (
        f"Fixture {_FIXTURE_PATH.name} is {size} bytes, exceeds {_FIXTURE_SIZE_LIMIT_BYTES}."
    )


@pytest.fixture(scope="module")
def fixture_graph() -> nx.MultiDiGraph:
    """Load and normalize the committed real-OSM fixture once per module."""
    graph: nx.MultiDiGraph = osmnx.load_graphml(_FIXTURE_PATH)
    return normalize_edges(graph)


# --- attribute-contract tests against the real fixture ---


def test_normalized_fixture_has_geometry_on_every_edge(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    for _u, _v, _k, data in fixture_graph.edges(data=True, keys=True):
        assert isinstance(data["geometry"], shapely.LineString), (
            "Every edge must carry a shapely.LineString geometry "
            "(synthesized from endpoint coords if osmnx omitted one)."
        )


def test_normalized_fixture_renames_osmid_to_osm_way_id(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    for _u, _v, _k, data in fixture_graph.edges(data=True, keys=True):
        assert "osm_way_id" in data, "Edge missing osm_way_id (rename of osmid)"
        assert "osmid" not in data, "Edge should no longer have raw osmid"


def test_normalized_fixture_has_sac_scale_key_on_every_edge(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    for _u, _v, _k, data in fixture_graph.edges(data=True, keys=True):
        assert "sac_scale" in data
        sac = data["sac_scale"]
        # osmnx-merged edges (chained ways with different sac_scale) carry a list.
        assert sac is None or isinstance(sac, str) or isinstance(sac, list)


# --- include-vs-exclude policy ---


def test_filter_trails_include_vs_exclude_diff_equals_untagged_count(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    """The two policies must differ by exactly the count of untagged edges."""
    untagged_count = sum(
        1
        for _u, _v, _k, data in fixture_graph.edges(data=True, keys=True)
        if data.get("sac_scale") is None
    )
    assert untagged_count > 0, (
        "Fixture sanity check: needs both tagged and untagged edges to discriminate."
    )
    included = filter_trails(fixture_graph, "include", "T6")
    excluded = filter_trails(fixture_graph, "exclude", "T6")
    assert included.number_of_edges() - excluded.number_of_edges() == untagged_count


def test_filter_trails_does_not_mutate_input(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    original_edges = fixture_graph.number_of_edges()
    _ = filter_trails(fixture_graph, "exclude", "T1")
    assert fixture_graph.number_of_edges() == original_edges


# --- difficulty-cap sweep ---


@pytest.mark.parametrize("cap", ["T1", "T2", "T3", "T4", "T5", "T6"])
def test_filter_trails_no_surviving_edge_exceeds_cap(
    fixture_graph: nx.MultiDiGraph, cap: str
) -> None:
    """For each cap, no surviving tagged edge has a sac_scale strictly above it."""
    cap_rank = parse_difficulty_cap(cap)
    out = filter_trails(fixture_graph, "include", cap)
    for _u, _v, _k, data in out.edges(data=True, keys=True):
        sac = data.get("sac_scale")
        if sac is None:
            continue
        edge_rank = max_sac_rank(sac)
        assert edge_rank is not None and edge_rank <= cap_rank, (
            f"Edge with sac_scale={sac!r} (max rank={edge_rank}) survived cap {cap}"
        )


def test_filter_trails_difficulty_cap_sweep_is_monotonic(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    """Stepping the cap up never removes edges (only adds them)."""
    counts = [
        filter_trails(fixture_graph, "include", cap).number_of_edges()
        for cap in ["T1", "T2", "T3", "T4", "T5", "T6"]
    ]
    for prev, nxt in zip(counts, counts[1:], strict=False):
        assert nxt >= prev, f"Non-monotonic sweep: {counts}"


# --- real-trail edge cases the fixture surfaces ---


def test_fixture_contains_multi_way_merged_edges(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    """osmnx simplification merges chained ways; expose list-typed osm_way_id."""
    list_osmids = sum(
        1
        for _u, _v, _k, data in fixture_graph.edges(data=True, keys=True)
        if isinstance(data.get("osm_way_id"), list)
    )
    assert list_osmids > 0, (
        "Fixture sanity check: osmnx simplification should produce some "
        "merged-way edges where osm_way_id is a list[int]."
    )


def test_fixture_geometry_synthesis_runs(
    fixture_graph: nx.MultiDiGraph,
) -> None:
    """A meaningful share of fixture edges are straight node-to-node (no geometry from osmnx);
    after normalize_edges they all have a 2-point synthesized LineString."""
    two_point_lines = sum(
        1
        for _u, _v, _k, data in fixture_graph.edges(data=True, keys=True)
        if len(list(data["geometry"].coords)) == 2
    )
    assert two_point_lines > 0, (
        "Fixture sanity check: some edges should be synthesized 2-point lines."
    )


# --- crafted synthetic graphs (AC #4) ---


def _make_edge_attrs(
    highway: str | list[str],
    sac_scale: str | list[str] | None,
    osm_way_id: int = 1,
) -> dict[str, object]:
    return {
        "highway": highway,
        "sac_scale": sac_scale,
        "osm_way_id": osm_way_id,
        "geometry": shapely.LineString([(0.0, 0.0), (0.001, 0.0)]),
    }


def test_filter_trails_single_untagged_edge_include_keeps_exclude_strips() -> None:
    """A graph with one untagged path edge: include keeps it, exclude drops it."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, **_make_edge_attrs("path", None))

    kept = filter_trails(graph, "include", "T6")
    dropped = filter_trails(graph, "exclude", "T6")

    assert kept.number_of_edges() == 1
    assert dropped.number_of_edges() == 0


def test_filter_trails_one_edge_per_highway_type_keeps_only_trail_tags() -> None:
    """A graph with one edge per known highway type keeps only TRAIL_HIGHWAY_TAGS values."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    trail_tags = sorted(TRAIL_HIGHWAY_TAGS)
    non_trail_tags = ["motorway", "primary", "service", "cycleway", "residential"]

    next_node = 1
    for tag in trail_tags + non_trail_tags:
        graph.add_edge(next_node, next_node + 1, key=0, **_make_edge_attrs(tag, "hiking"))
        next_node += 2

    out = filter_trails(graph, "include", "T6")

    surviving_highways = {data["highway"] for _u, _v, _k, data in out.edges(data=True, keys=True)}
    assert surviving_highways == set(trail_tags)


def test_filter_trails_keeps_multi_tag_edge_if_any_tag_is_trail() -> None:
    """osmnx-style list-valued highway: an edge tagged ['steps','footway'] is a trail."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, **_make_edge_attrs(["steps", "footway"], "hiking"))
    graph.add_edge(3, 4, key=0, **_make_edge_attrs(["motorway", "service"], "hiking"))

    out = filter_trails(graph, "include", "T6")

    assert out.number_of_edges() == 1


def test_filter_trails_drops_edge_with_unknown_sac_scale_value() -> None:
    """An unrecognized sac_scale string is treated as out-of-range and dropped."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, **_make_edge_attrs("path", "totally_invalid"))

    out = filter_trails(graph, "include", "T6")

    assert out.number_of_edges() == 0


def test_filter_trails_list_sac_scale_uses_max_rank() -> None:
    """An osmnx-merged edge with list-valued sac_scale is capped by the most-demanding tag.

    [hiking (T1), demanding_mountain_hiking (T3)] passes T3 cap (max=T3 <= 3)
    but fails T2 cap (max=T3 > 2). Conservative semantics so users aren't
    routed onto harder terrain than they declared.
    """
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.add_edge(
        1,
        2,
        key=0,
        **_make_edge_attrs("path", ["hiking", "demanding_mountain_hiking"]),
    )

    assert filter_trails(graph, "include", "T3").number_of_edges() == 1
    assert filter_trails(graph, "include", "T2").number_of_edges() == 0


def test_filter_trails_list_sac_scale_with_unknown_member_drops_edge() -> None:
    """If any item in a list-valued sac_scale is unrecognized, the whole edge drops."""
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, **_make_edge_attrs("path", ["hiking", "totally_invalid"]))

    assert filter_trails(graph, "include", "T6").number_of_edges() == 0


# --- argument validation ---


def test_filter_trails_rejects_unknown_untagged_policy() -> None:
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    with pytest.raises(BadCLIArgError, match="--untagged-trails"):
        _ = filter_trails(graph, "MAYBE", "T6")


@pytest.mark.parametrize("cap", ["", "X1", "T0", "T7", "T10", "T", "1T"])
def test_filter_trails_rejects_malformed_difficulty_cap(cap: str) -> None:
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    with pytest.raises(BadCLIArgError, match="--difficulty-cap"):
        _ = filter_trails(graph, "include", cap)


@pytest.mark.parametrize("cap", ["T1", "t1", " T3 ", "T6"])
def test_filter_trails_accepts_case_insensitive_difficulty_cap(cap: str) -> None:
    graph: nx.MultiDiGraph = nx.MultiDiGraph()
    graph.add_edge(1, 2, key=0, **_make_edge_attrs("path", "hiking"))
    out = filter_trails(graph, "include", cap)
    assert out.number_of_edges() == 1


# --- osm_load: offline-checkable preconditions only ---


@pytest.mark.parametrize("bad_radius", [0.0, -0.001, -10.0])
def test_osm_load_rejects_non_positive_radius(bad_radius: float) -> None:
    """Carry-forward from Epic 1 retro: --radius first becomes geometric here."""
    area = Area(center=(45.119, 5.873), radius_km=bad_radius)
    with pytest.raises(BadCLIArgError, match="--radius must be > 0"):
        _ = osm_load(area)


@pytest.mark.parametrize("bad_radius", [float("nan"), float("inf"), float("-inf")])
def test_osm_load_rejects_non_finite_radius(bad_radius: float) -> None:
    """NaN passes `> 0` (returns False), so explicit isfinite check is required."""
    area = Area(center=(45.119, 5.873), radius_km=bad_radius)
    with pytest.raises(BadCLIArgError, match="--radius must be finite"):
        _ = osm_load(area)


@pytest.mark.parametrize(
    "bad_center",
    [(91.0, 5.873), (-91.0, 5.873), (45.119, 181.0), (45.119, -181.0)],
)
def test_osm_load_rejects_out_of_range_center(bad_center: tuple[float, float]) -> None:
    """Lat ∈ [-90, 90] / lon ∈ [-180, 180]; out-of-range slipped past osmnx silently before."""
    area = Area(center=bad_center, radius_km=2.0)
    with pytest.raises(BadCLIArgError, match=r"--center (latitude|longitude)"):
        _ = osm_load(area)


@pytest.mark.parametrize("bad_center", [(float("nan"), 5.873), (45.119, float("inf"))])
def test_osm_load_rejects_non_finite_center(bad_center: tuple[float, float]) -> None:
    area = Area(center=bad_center, radius_km=2.0)
    with pytest.raises(BadCLIArgError, match="--center coordinates must be finite"):
        _ = osm_load(area)


def test_max_sac_rank_normalizes_whitespace() -> None:
    """OSM tags occasionally carry trailing whitespace; lookup should still succeed."""
    assert max_sac_rank("hiking ") == 1
    assert max_sac_rank(" mountain_hiking") == 2
    assert max_sac_rank(["hiking ", " demanding_mountain_hiking"]) == 3
