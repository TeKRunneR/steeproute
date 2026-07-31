# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false
"""Explicitly regenerate the immutable Story 16.6 object-solver oracle."""

from __future__ import annotations

import dataclasses
import json
import pathlib
import struct
import sys
from typing import Any

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "integration"))

from conftest import (  # noqa: E402
    GRENOBLE_DIFFICULTY_CAP,
    GRENOBLE_J_MAX,
    GRENOBLE_L_CONNECTOR,
    GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
    GRENOBLE_SEED,
    GRENOBLE_THETA,
    grenoble_fixture,
    make_toy_contracted_graph,
    make_toy_solver_params,
)

from steeproute.models import Edge, Solution, SolverParams  # noqa: E402
from steeproute.solver.grasp import GraspSolver  # noqa: E402
from steeproute.solver.parallel import run_parallel_grasp  # noqa: E402


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


def _params(params: SolverParams) -> dict[str, Any]:
    return dataclasses.asdict(params)


def _single(seed: int) -> dict[str, Any]:
    graph = make_toy_contracted_graph(seed)
    params = make_toy_solver_params(seed=seed)
    solver = GraspSolver(graph, params, np.random.default_rng(seed))
    solutions = solver.run()
    return {
        "graph_seed": seed,
        "rng_seed": seed,
        "params": _params(params),
        "convergence_status": solver.convergence_status,
        "convergence_iteration": solver.convergence_iteration,
        "solutions": _solutions(solutions),
    }


def _parallel(merge_interval: int) -> dict[str, Any]:
    graph_seed, seed, workers = 23, 42, 2
    params = make_toy_solver_params(iter_budget=400, seed=seed)
    result = run_parallel_grasp(
        make_toy_contracted_graph(graph_seed),
        params,
        seed,
        workers,
        merge_interval=merge_interval,
        _backend="object",
    )
    assert result.cleanup is not None and result.cleanup.wait(30.0)
    return {
        "graph_seed": graph_seed,
        "rng_seed": seed,
        "workers": workers,
        "merge_interval": merge_interval,
        "params": _params(params),
        "convergence_status": result.convergence_status,
        "convergence_iteration": result.convergence_iteration,
        "solutions": _solutions(result.solutions),
    }


def _grenoble() -> dict[str, Any]:
    fixture = grenoble_fixture.__wrapped__()
    params = SolverParams(
        theta=GRENOBLE_THETA,
        min_climb_slope=GRENOBLE_THETA,
        difficulty_cap=GRENOBLE_DIFFICULTY_CAP,
        l_connector=GRENOBLE_L_CONNECTOR,
        min_climb_ground_length=GRENOBLE_MIN_CLIMB_GROUND_LENGTH_M,
        j_max=GRENOBLE_J_MAX,
        n=3,
        untagged_policy="include",
        seed=GRENOBLE_SEED,
        iter_budget=200,
        time_budget=3600.0,
        stagnation_iters=0,
    )
    solver = GraspSolver(fixture.contracted, params, np.random.default_rng(GRENOBLE_SEED))
    solutions = solver.run()
    assert solutions
    return {
        "fixture": "tests/fixtures/grenoble_small",
        "rng_seed": GRENOBLE_SEED,
        "params": _params(params),
        "convergence_status": solver.convergence_status,
        "convergence_iteration": solver.convergence_iteration,
        "solutions": _solutions(solutions),
    }


def main() -> None:
    document = {
        "schema": 1,
        "reference_commit": "6a6fa39",
        "generator": "devtools/generate_solver_oracle_16_6.py",
        "quality_seeds": [_single(seed) for seed in (11, 23, 37, 53, 71)],
        "parallel_one_round": _parallel(0),
        "parallel_migration": _parallel(100),
        "grenoble": _grenoble(),
    }
    print(json.dumps(document, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
