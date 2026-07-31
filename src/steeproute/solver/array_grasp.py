"""GRASP construction over attached shared-memory arrays."""

from __future__ import annotations

import time

import numpy as np

from steeproute.models import ConvergenceStatus, Edge, Solution, SolverParams
from steeproute.progress import ProgressCallback, ProgressEvent, estimate_remaining
from steeproute.solver.distinctness import TopNTracker
from steeproute.solver.grasp import RCL_SIZE
from steeproute.solver.grasp_core import UniformDrawBuffer, best_theta_prefix_items
from steeproute.solver.shared_state import SharedSolverState

__all__ = ["ArrayGraspSolver"]


class ArrayGraspSolver:
    """Workers-only solver that reads the parent's canonical static state."""

    def __init__(
        self,
        state: SharedSolverState,
        params: SolverParams,
        rng: np.random.Generator,
        progress_callback: ProgressCallback | None = None,
        initial_solutions: list[Solution] | None = None,
    ) -> None:
        if params.iter_budget < 1:
            raise ValueError(f"iter_budget must be >= 1, got {params.iter_budget}")
        self._state: SharedSolverState = state
        self._params: SolverParams = params
        self._draws: UniformDrawBuffer = UniformDrawBuffer(rng)
        self._progress_callback: ProgressCallback | None = progress_callback
        self._tracker: TopNTracker = TopNTracker(
            params.n, params.j_max, canonicalizer=state.canonical_ids
        )
        for solution in initial_solutions or ():
            identity = state.canonical_ids(solution)
            self._tracker.consider_precanonicalized(solution, identity)
        self.convergence_status: ConvergenceStatus = "budget-exhausted"
        self.convergence_iteration: int = 0

    def run(self) -> list[Solution]:
        if len(self._state.start_nodes) == 0:
            return self._tracker.current_top()
        state = self._state
        length_m = state.length_m_view
        d_plus_m = state.d_plus_m_view
        d_minus_m = state.d_minus_m_view

        def _length_of(row: int) -> float:
            return length_m[row]

        def _climb_of(row: int) -> float:
            return d_plus_m[row] + d_minus_m[row]

        def _materialize(rows: tuple[int, ...]) -> tuple[Edge, ...]:
            return tuple(state.edge(row) for row in rows)

        start = time.monotonic()
        stagnation_counter = 0
        for index in range(self._params.iter_budget):
            rows = self._construct_rows()
            candidate = best_theta_prefix_items(
                rows,
                self._params.theta,
                length_of=_length_of,
                climb_of=_climb_of,
                materialize=_materialize,
            )
            admitted = False
            if candidate is not None:
                identity = self._state.canonical_ids(candidate)
                admitted = self._tracker.consider_precanonicalized(candidate, identity)
            if admitted:
                stagnation_counter = 0
                self.convergence_iteration = index + 1
            else:
                stagnation_counter += 1
            elapsed_s = time.monotonic() - start
            if self._progress_callback is not None:
                iteration = index + 1
                self._progress_callback(
                    ProgressEvent(
                        iteration=iteration,
                        elapsed_s=elapsed_s,
                        best_objective=self._tracker.total_objective(),
                        estimated_remaining_s=estimate_remaining(
                            iteration, self._params.iter_budget, elapsed_s
                        ),
                        stagnation_counter=stagnation_counter,
                    )
                )
            if (
                self._params.stagnation_iters > 0
                and stagnation_counter >= self._params.stagnation_iters
            ):
                self.convergence_status = "converged"
                return self._tracker.current_top()
            if elapsed_s >= self._params.time_budget:
                return self._tracker.current_top()
        return self._tracker.current_top()

    def _construct_rows(self) -> tuple[int, ...]:
        state = self._state
        start_index = int(self._draws.next() * len(state.start_nodes))
        current = int(state.start_nodes[start_index])
        candidate_offsets = state.candidate_offsets_view
        destination = state.candidate_v_dense_view
        blocking_offsets = state.blocking_offsets_view
        blocking_ids = state.blocking_ids_view
        path_rows: list[int] = []
        used_directed: set[int] = set()
        used_segments: set[int] = set()
        while True:
            begin = candidate_offsets[current]
            end = candidate_offsets[current + 1]
            rcl: list[int] = []
            for row in range(begin, end):
                if row in used_directed:
                    continue
                block_begin = blocking_offsets[row]
                block_end = blocking_offsets[row + 1]
                if any(value in used_segments for value in blocking_ids[block_begin:block_end]):
                    continue
                rcl.append(row)
                if len(rcl) == RCL_SIZE:
                    break
            if not rcl:
                break
            row = rcl[int(self._draws.next() * len(rcl))]
            path_rows.append(row)
            used_directed.add(row)
            block_begin = blocking_offsets[row]
            block_end = blocking_offsets[row + 1]
            used_segments.update(blocking_ids[block_begin:block_end])
            current = destination[row]
        return tuple(path_rows)
