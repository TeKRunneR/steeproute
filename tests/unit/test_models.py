"""Tests for `steeproute.models` — Story 3.1 query-side data contract.

The four invariants (frozen, slots, value-equality, round-trip) are pinned per
Architecture §"Python code conventions" and Story 3.1 AC #3. The parametrized
suite covers all 12 new dataclasses; the per-class round-trip tests document
the field list explicitly so the diff is reviewable.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable

import networkx as nx
import pytest

from steeproute.models import (
    Climb,
    ConstraintViolation,
    ContractedGraph,
    Edge,
    PairwiseViolation,
    ProvenanceInfo,
    Route,
    RouteMetrics,
    RouteValidation,
    Solution,
    SolverParams,
    ValidatedRouteSet,
)

# --- Canonical-instance factories ---------------------------------------------
# A shared MultiDiGraph instance lets ContractedGraph equality fall back on
# identity for the `graph` field (networkx graphs don't define value-equality).
# Story 3.1 only pins the *dataclass* contract; graph-content equality is an
# Epic-3-downstream concern.
_SHARED_GRAPH: nx.MultiDiGraph = nx.MultiDiGraph()  # pyright: ignore[reportMissingTypeArgument, reportUnknownVariableType]


def _make_edge() -> Edge:
    return Edge(
        node_u=1,
        node_v=2,
        key=0,
        length_m=100.0,
        d_plus_m=20.0,
        d_minus_m=0.0,
        avg_gradient=0.20,
        sac_scale="T3",
    )


def _make_climb() -> Climb:
    return Climb(
        edges=(_make_edge(),),
        length_m=100.0,
        d_plus_m=20.0,
        avg_slope=0.20,
    )


def _make_contracted_graph() -> ContractedGraph:
    return ContractedGraph(
        graph=_SHARED_GRAPH,
        super_edge_to_base={(1, 2, 0): (_make_edge(),)},
    )


def _make_solver_params() -> SolverParams:
    return SolverParams(
        theta=0.15,
        min_climb_slope=0.16,
        difficulty_cap="T3",
        l_connector=500.0,
        min_climb_ground_length=300.0,
        j_max=0.5,
        n=5,
        area_cap=100.0,
        untagged_policy="include",
        seed=42,
        iter_budget=1000,
        time_budget=600.0,
        stagnation_iters=50,
    )


def _make_solution() -> Solution:
    return Solution(edges=(_make_edge(),), objective=20.0)


def _make_route_metrics() -> RouteMetrics:
    return RouteMetrics(
        length_m=10000.0,
        d_plus_m=800.0,
        d_minus_m=800.0,
        avg_gradient=0.10,
    )


def _make_constraint_violation() -> ConstraintViolation:
    return ConstraintViolation(
        constraint_id="slope_floor",
        detail="Edge slope 0.18 below floor 0.20",
        numeric={"observed": 0.18, "required": 0.20},
    )


def _make_route_validation_passed() -> RouteValidation:
    return RouteValidation(passed=True, violations=[])


def _make_route() -> Route:
    return Route(
        edges=[_make_edge()],
        metrics=_make_route_metrics(),
        validation=_make_route_validation_passed(),
    )


def _make_pairwise_violation() -> PairwiseViolation:
    return PairwiseViolation(
        route_index_a=0,
        route_index_b=1,
        jaccard_observed=0.6,
        jaccard_max=0.5,
    )


def _make_validated_route_set() -> ValidatedRouteSet:
    return ValidatedRouteSet(
        routes=[_make_route()],
        set_violations=[_make_pairwise_violation()],
    )


def _make_provenance_info() -> ProvenanceInfo:
    return ProvenanceInfo(
        steeproute_version="0.1.0",
        git_commit_short="abc1234",
        git_dirty=False,
        osm_extract_date="2026-05-25T12:00:00Z",
        dem_version="ign_rge_alti_5m_2024-12",
        pipeline_content_hash="0123456789abcdef",
    )


_FACTORIES: dict[type, Callable[[], object]] = {
    Edge: _make_edge,
    Climb: _make_climb,
    ContractedGraph: _make_contracted_graph,
    SolverParams: _make_solver_params,
    Solution: _make_solution,
    RouteMetrics: _make_route_metrics,
    ConstraintViolation: _make_constraint_violation,
    RouteValidation: _make_route_validation_passed,
    Route: _make_route,
    PairwiseViolation: _make_pairwise_violation,
    ValidatedRouteSet: _make_validated_route_set,
    ProvenanceInfo: _make_provenance_info,
}

_ALL_CLASSES = list(_FACTORIES.keys())


# --- Parametrized invariants (AC #3 a/b/c/d) ----------------------------------


@pytest.mark.parametrize("cls", _ALL_CLASSES, ids=lambda c: c.__name__)
def test_dataclass_is_frozen(cls: type) -> None:
    """AC #3b: `frozen=True` raises `FrozenInstanceError` on any field mutation."""
    instance = _FACTORIES[cls]()
    first_field = dataclasses.fields(cls)[0].name  # pyright: ignore[reportUnknownArgumentType]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, first_field, getattr(instance, first_field))


@pytest.mark.parametrize("cls", _ALL_CLASSES, ids=lambda c: c.__name__)
def test_dataclass_uses_slots(cls: type) -> None:
    """AC #3c: `slots=True` declares `__slots__` matching the field names exactly.

    A direct `instance.new_attr = ...` test would conflict with `frozen=True`
    (which raises `FrozenInstanceError` first); `__slots__` introspection is the
    canonical signal that slots are in effect.
    """
    assert hasattr(cls, "__slots__"), f"{cls.__name__} missing __slots__"
    expected = {f.name for f in dataclasses.fields(cls)}  # pyright: ignore[reportUnknownArgumentType]
    slots: tuple[str, ...] = tuple(cls.__slots__)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    assert set(slots) == expected, (
        f"{cls.__name__}.__slots__ mismatches fields: slots={slots} fields={expected}"
    )


@pytest.mark.parametrize("cls", _ALL_CLASSES, ids=lambda c: c.__name__)
def test_dataclass_value_equality(cls: type) -> None:
    """AC #3d: two instances with identical fields compare equal."""
    a = _FACTORIES[cls]()
    b = _FACTORIES[cls]()
    assert a == b


# --- Per-class round-trip tests (AC #3a; field lists explicit) ---------------


def test_edge_round_trip() -> None:
    edge = _make_edge()
    assert edge.node_u == 1
    assert edge.node_v == 2
    assert edge.key == 0
    assert math.isclose(edge.length_m, 100.0)
    assert math.isclose(edge.d_plus_m, 20.0)
    assert math.isclose(edge.d_minus_m, 0.0)
    assert math.isclose(edge.avg_gradient, 0.20)
    assert edge.sac_scale == "T3"


def test_edge_accepts_none_sac_scale() -> None:
    """`sac_scale: str | None` — untagged trails carry None."""
    edge = dataclasses.replace(_make_edge(), sac_scale=None)
    assert edge.sac_scale is None


def test_climb_round_trip() -> None:
    climb = _make_climb()
    assert len(climb.edges) == 1
    assert climb.edges[0] == _make_edge()
    assert math.isclose(climb.length_m, 100.0)
    assert math.isclose(climb.d_plus_m, 20.0)
    assert math.isclose(climb.avg_slope, 0.20)


def test_contracted_graph_round_trip() -> None:
    cg = _make_contracted_graph()
    assert cg.graph is _SHARED_GRAPH
    assert (1, 2, 0) in cg.super_edge_to_base
    assert cg.super_edge_to_base[(1, 2, 0)] == (_make_edge(),)


def test_solver_params_round_trip() -> None:
    """All 13 Cat 9 metadata-block fields are present and addressable by name."""
    sp = _make_solver_params()
    assert math.isclose(sp.theta, 0.15)
    assert math.isclose(sp.min_climb_slope, 0.16)
    assert sp.difficulty_cap == "T3"
    assert math.isclose(sp.l_connector, 500.0)
    assert math.isclose(sp.min_climb_ground_length, 300.0)
    assert math.isclose(sp.j_max, 0.5)
    assert sp.n == 5
    assert math.isclose(sp.area_cap, 100.0)
    assert sp.untagged_policy == "include"
    assert sp.seed == 42
    assert sp.iter_budget == 1000
    assert math.isclose(sp.time_budget, 600.0)
    assert sp.stagnation_iters == 50


def test_solver_params_accepts_none_seed() -> None:
    """`seed: int | None` — CLI may surface an unset `--seed`; resolver fills in."""
    sp = dataclasses.replace(_make_solver_params(), seed=None)
    assert sp.seed is None


def test_solution_round_trip() -> None:
    sol = _make_solution()
    assert sol.edges == (_make_edge(),)
    assert math.isclose(sol.objective, 20.0)


def test_route_metrics_round_trip() -> None:
    rm = _make_route_metrics()
    assert math.isclose(rm.length_m, 10000.0)
    assert math.isclose(rm.d_plus_m, 800.0)
    assert math.isclose(rm.d_minus_m, 800.0)
    assert math.isclose(rm.avg_gradient, 0.10)


def test_constraint_violation_round_trip() -> None:
    cv = _make_constraint_violation()
    assert cv.constraint_id == "slope_floor"
    assert "below floor" in cv.detail
    assert cv.numeric == {"observed": 0.18, "required": 0.20}


def test_route_validation_round_trip() -> None:
    rv_passed = _make_route_validation_passed()
    assert rv_passed.passed is True
    assert rv_passed.violations == []

    rv_failed = RouteValidation(passed=False, violations=[_make_constraint_violation()])
    assert rv_failed.passed is False
    assert len(rv_failed.violations) == 1
    assert rv_failed.violations[0].constraint_id == "slope_floor"


def test_route_round_trip() -> None:
    route = _make_route()
    assert route.edges == [_make_edge()]
    assert route.metrics == _make_route_metrics()
    assert route.validation == _make_route_validation_passed()


def test_pairwise_violation_round_trip() -> None:
    pv = _make_pairwise_violation()
    assert pv.route_index_a == 0
    assert pv.route_index_b == 1
    assert math.isclose(pv.jaccard_observed, 0.6)
    assert math.isclose(pv.jaccard_max, 0.5)


def test_validated_route_set_round_trip() -> None:
    vs = _make_validated_route_set()
    assert vs.routes == [_make_route()]
    assert vs.set_violations == [_make_pairwise_violation()]


def test_provenance_info_round_trip() -> None:
    p = _make_provenance_info()
    assert p.steeproute_version == "0.1.0"
    assert p.git_commit_short == "abc1234"
    assert p.git_dirty is False
    assert p.osm_extract_date == "2026-05-25T12:00:00Z"
    assert p.dem_version == "ign_rge_alti_5m_2024-12"
    assert p.pipeline_content_hash == "0123456789abcdef"
