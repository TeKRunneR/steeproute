# pyright: reportImplicitRelativeImport=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""Exact-equivalence and lifecycle gates for shared solver backends."""

from __future__ import annotations

import dataclasses
import gc
import json
import pathlib
import struct
import weakref
from multiprocessing.shared_memory import SharedMemory
from typing import Any

import networkx as nx
import numpy as np
import pytest
from conftest import GrenobleFixture, make_toy_contracted_graph

from steeproute.models import ContractedGraph, Edge, Solution, SolverParams
from steeproute.solver import parallel
from steeproute.solver.array_grasp import ArrayGraspSolver
from steeproute.solver.grasp import GraspSolver
from steeproute.solver.parallel import run_parallel_grasp
from steeproute.solver.shared_state import (
    SharedBlobOwner,
    SharedSolverDescriptor,
    SharedSolverOwner,
    SharedSolverState,
    load_shared_blob,
)

_ORACLE = pathlib.Path(__file__).parent / "fixtures" / "solver_object_oracle_16_6.json"


def _float(value: float) -> dict[str, float | str]:
    return {"value": value, "bits": struct.pack(">d", value).hex()}


def _edge(edge: Edge) -> dict[str, Any]:
    return {
        "node_u": edge.node_u,
        "node_v": edge.node_v,
        "key": edge.key,
        "length_m": _float(edge.length_m),
        "d_plus_m": _float(edge.d_plus_m),
        "d_minus_m": _float(edge.d_minus_m),
        "avg_gradient": _float(edge.avg_gradient),
        "sac_scale": edge.sac_scale,
    }


def _solutions(solutions: list[Solution]) -> list[dict[str, Any]]:
    return [
        {"objective": _float(solution.objective), "edges": [_edge(e) for e in solution.edges]}
        for solution in solutions
    ]


@pytest.fixture(scope="module")
def oracle() -> dict[str, Any]:
    return json.loads(_ORACLE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case_index", range(5))
def test_object_and_array_match_immutable_quality_oracle(
    oracle: dict[str, Any], case_index: int
) -> None:
    case = oracle["quality_seeds"][case_index]
    graph = make_toy_contracted_graph(case["graph_seed"])
    params = SolverParams(**case["params"])
    object_solver = GraspSolver(graph, params, np.random.default_rng(case["rng_seed"]))
    object_result = object_solver.run()
    assert _solutions(object_result) == case["solutions"]
    assert object_solver.convergence_status == case["convergence_status"]
    assert object_solver.convergence_iteration == case["convergence_iteration"]

    context = GraspSolver(graph, params, np.random.default_rng(0)).static_context
    owner = SharedSolverOwner(context)
    state = SharedSolverState(owner.descriptor)
    try:
        array_solver = ArrayGraspSolver(state, params, np.random.default_rng(case["rng_seed"]))
        array_result = array_solver.run()
        assert array_result == object_result
        assert _solutions(array_result) == case["solutions"]
        assert array_solver.convergence_status == case["convergence_status"]
        assert array_solver.convergence_iteration == case["convergence_iteration"]
    finally:
        state.close()
        owner.close_unlink()


@pytest.mark.parametrize(
    ("case_name", "merge_interval"),
    (("parallel_one_round", 0), ("parallel_migration", 100)),
)
@pytest.mark.parametrize("backend", ("object", "blob", "array"))
def test_parallel_backends_match_immutable_oracle(
    oracle: dict[str, Any], case_name: str, merge_interval: int, backend: str
) -> None:
    case = oracle[case_name]
    result = run_parallel_grasp(
        make_toy_contracted_graph(case["graph_seed"]),
        SolverParams(**case["params"]),
        case["rng_seed"],
        case["workers"],
        merge_interval=merge_interval,
        _backend=backend,  # pyright: ignore[reportArgumentType]
    )
    assert _solutions(result.solutions) == case["solutions"]
    assert result.convergence_status == case["convergence_status"]
    assert result.convergence_iteration == case["convergence_iteration"]
    assert result.cleanup is not None and result.cleanup.wait(30.0)


def test_grenoble_object_and_array_results_match_immutable_oracle(
    oracle: dict[str, Any], grenoble_fixture: GrenobleFixture
) -> None:
    case = oracle["grenoble"]
    params = SolverParams(**case["params"])
    solver = GraspSolver(
        grenoble_fixture.contracted, params, np.random.default_rng(case["rng_seed"])
    )
    object_result = solver.run()
    assert _solutions(object_result) == case["solutions"]
    assert solver.convergence_status == case["convergence_status"]
    assert solver.convergence_iteration == case["convergence_iteration"]

    owner = SharedSolverOwner(solver.static_context)
    try:
        state = SharedSolverState(owner.descriptor)
        try:
            array_solver = ArrayGraspSolver(state, params, np.random.default_rng(case["rng_seed"]))
            array_result = array_solver.run()
            assert array_result == object_result
            assert _solutions(array_result) == case["solutions"]
            assert array_solver.convergence_status == solver.convergence_status
            assert array_solver.convergence_iteration == solver.convergence_iteration
        finally:
            state.close()
    finally:
        owner.close_unlink()


@pytest.mark.parametrize(
    ("start_at_junction", "max_descent_slope"),
    ((True, None), (False, 1.0), (True, 1.0)),
)
def test_flagged_object_and_array_parallel_paths_are_nonempty_and_exact(
    start_at_junction: bool, max_descent_slope: float | None
) -> None:
    graph = make_toy_contracted_graph(37)
    for node in graph.graph.nodes:
        graph.graph.nodes[node]["is_road_trail_junction"] = node % 3 == 0
    params = SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=5,
        untagged_policy="include",
        seed=71,
        iter_budget=300,
        time_budget=3600.0,
        stagnation_iters=0,
        start_at_junction=start_at_junction,
        max_descent_slope=max_descent_slope,
    )
    object_result = run_parallel_grasp(graph, params, 71, 2, _backend="object")
    array_result = run_parallel_grasp(graph, params, 71, 2, _backend="array")
    assert object_result.solutions
    assert array_result.solutions == object_result.solutions
    assert object_result.cleanup is not None and object_result.cleanup.wait(30.0)
    assert array_result.cleanup is not None and array_result.cleanup.wait(30.0)


def test_shared_descriptors_are_read_only_validated_and_unlinked() -> None:
    graph = make_toy_contracted_graph(11)
    params = SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=2,
        untagged_policy="include",
        seed=11,
        iter_budget=1,
        time_budget=3600.0,
        stagnation_iters=0,
    )
    context = GraspSolver(graph, params, np.random.default_rng(0)).static_context
    owner = SharedSolverOwner(context)
    name = owner.descriptor.name
    state = SharedSolverState(owner.descriptor)
    assert state.node_ids.flags.writeable is False
    with pytest.raises(ValueError):
        state.node_ids[0] = -1
    with pytest.raises(ValueError, match="schema"):
        SharedSolverState(dataclasses.replace(owner.descriptor, arrays=()))
    state.close()
    owner.close_unlink()
    owner.close_unlink()
    with pytest.raises(FileNotFoundError):
        SharedMemory(name=name, create=False, track=False)


def test_shared_blob_loads_exact_payload_and_cleanup_is_idempotent() -> None:
    payload = {"solver": [1, 2, 3], "value": 4.5}
    owner = SharedBlobOwner(__import__("pickle").dumps(payload))
    name = owner.descriptor.name
    assert load_shared_blob(owner.descriptor) == payload
    owner.close_unlink()
    owner.close_unlink()
    with pytest.raises(FileNotFoundError):
        SharedMemory(name=name, create=False, track=False)


def test_flattening_pins_domains_order_and_both_segment_identities() -> None:
    graph = nx.MultiDiGraph()
    graph.add_nodes_from((0, 1, 2, 3))
    graph.nodes[0]["is_road_trail_junction"] = True

    def add(
        node_u: int,
        node_v: int,
        key: int,
        objective: float,
        base_ids: frozenset[tuple[int, int, int]],
        reusable: bool,
    ) -> None:
        graph.add_edge(
            node_u,
            node_v,
            key=key,
            length_m=100.0,
            d_plus_m=objective,
            d_minus_m=0.0,
            avg_gradient=objective / 100.0,
            sac_scale="hiking",
            base_segment_id=base_ids,
            reusable=reusable,
        )

    add(0, 0, 0, 30.0, frozenset({(0, 0, 0)}), False)
    add(0, 1, 0, 40.0, frozenset({(0, 1, 0)}), True)
    add(0, 1, 1, 50.0, frozenset({(0, 1, 1), (1, 2, 9)}), False)
    graph.edges[0, 1, 1]["sac_scale"] = ["hiking", "mountain_hiking"]
    add(1, 2, 0, 25.0, frozenset({(1, 2, 0)}), False)
    contracted = ContractedGraph(graph=graph, super_edge_to_base={}, lean=True)
    params = SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=2,
        untagged_policy="include",
        seed=1,
        iter_budget=1,
        time_budget=3600.0,
        stagnation_iters=0,
        start_at_junction=True,
    )
    context = GraspSolver(contracted, params, np.random.default_rng(0)).static_context
    owner = SharedSolverOwner(context)
    state = SharedSolverState(owner.descriptor)
    try:
        assert state.node_ids.tolist() == [0, 1, 2, 3]
        assert state.start_nodes.tolist() == [0]
        assert state.candidate_offsets.tolist() == [0, 3, 4, 4, 4]
        assert [state.directed_id(index) for index in range(3)] == [
            (0, 1, 1),
            (0, 1, 0),
            (0, 0, 0),
        ]
        assert state.edge(0).sac_scale == ["hiking", "mountain_hiking"]
        assert state.blocking_offsets[2] == state.blocking_offsets[1]
        assert int(state.base_offsets[1] - state.base_offsets[0]) == 2
    finally:
        state.close()
        owner.close_unlink()


def test_empty_candidate_and_empty_junction_structures_are_complete() -> None:
    graph = nx.MultiDiGraph()
    graph.add_nodes_from((4, 9))
    contracted = ContractedGraph(graph=graph, super_edge_to_base={}, lean=True)
    params = SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=2,
        untagged_policy="include",
        seed=1,
        iter_budget=1,
        time_budget=3600.0,
        stagnation_iters=0,
        start_at_junction=True,
    )
    context = GraspSolver(contracted, params, np.random.default_rng(0)).static_context
    owner = SharedSolverOwner(context)
    state = SharedSolverState(owner.descriptor)
    try:
        assert state.node_ids.tolist() == [4, 9]
        assert state.start_nodes.size == 0
        assert state.candidate_offsets.tolist() == [0, 0, 0]
        assert state.candidate_u.size == state.blocking_ids.size == state.base_ids.size == 0
        assert ArrayGraspSolver(state, params, np.random.default_rng(1)).run() == []
    finally:
        state.close()
        owner.close_unlink()


def test_executor_construction_failure_unlinks_array_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []
    temporary_graphs: list[weakref.ReferenceType[nx.MultiDiGraph[Any]]] = []

    real_solver_graph_view = parallel.solver_graph_view

    def capture_solver_graph_view(graph: ContractedGraph) -> ContractedGraph:
        view = real_solver_graph_view(graph)
        temporary_graphs.append(weakref.ref(view.graph))
        return view

    class CapturingOwner(SharedSolverOwner):
        def __init__(self, context: Any) -> None:
            super().__init__(context)
            captured.append(self.descriptor.name)

    def fail_executor(**_kwargs: Any) -> None:
        gc.collect()
        assert temporary_graphs[0]() is None
        raise OSError("simulated executor failure")

    monkeypatch.setattr(parallel, "solver_graph_view", capture_solver_graph_view)
    monkeypatch.setattr(parallel, "SharedSolverOwner", CapturingOwner)
    monkeypatch.setattr(parallel, "ProcessPoolExecutor", fail_executor)
    params = SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=2,
        untagged_policy="include",
        seed=1,
        iter_budget=10,
        time_budget=3600.0,
        stagnation_iters=0,
    )
    graph = dataclasses.replace(make_toy_contracted_graph(11), lean=False)
    with pytest.raises(parallel.ParallelGraspFailed, match="create.*executor"):
        run_parallel_grasp(graph, params, 1, 2)
    with pytest.raises(FileNotFoundError):
        SharedMemory(name=captured[0], create=False, track=False)


def test_initializer_attach_failure_reports_fallback_and_awaitable_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    class InvalidDescriptorOwner(SharedSolverOwner):
        def __init__(self, context: Any) -> None:
            super().__init__(context)
            captured.append(self.descriptor.name)
            self.descriptor: SharedSolverDescriptor = dataclasses.replace(
                self.descriptor, name=f"{self.descriptor.name}-missing"
            )

    monkeypatch.setattr(parallel, "SharedSolverOwner", InvalidDescriptorOwner)
    params = SolverParams(
        theta=0.20,
        min_climb_slope=0.20,
        difficulty_cap="T3",
        l_connector=200.0,
        min_climb_ground_length=300.0,
        j_max=0.30,
        n=2,
        untagged_policy="include",
        seed=1,
        iter_budget=10,
        time_budget=3600.0,
        stagnation_iters=0,
    )
    with pytest.raises(parallel.ParallelGraspFailed, match="initialization") as exc_info:
        run_parallel_grasp(make_toy_contracted_graph(11), params, 1, 2)
    assert exc_info.value.cleanup is not None
    assert exc_info.value.cleanup.wait(30.0)
    with pytest.raises(FileNotFoundError):
        SharedMemory(name=captured[0], create=False, track=False)
