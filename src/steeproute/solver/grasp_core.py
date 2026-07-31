"""Representation-independent primitives shared by GRASP solver backends."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from steeproute.models import Edge, Solution, route_avg_gradient

__all__ = ["UniformDrawBuffer", "best_theta_prefix", "best_theta_prefix_items"]


_RNG_CHUNK = 1024


class UniformDrawBuffer:
    """Consume a Generator's float64 stream in fixed-size native batches."""

    def __init__(self, rng: np.random.Generator) -> None:
        self._rng: np.random.Generator = rng
        self._values: list[float] = []
        self._index: int = 0

    def next(self) -> float:
        if self._index == len(self._values):
            self._values = self._rng.random(_RNG_CHUNK).tolist()
            self._index = 0
        value = self._values[self._index]
        self._index += 1
        return value


def best_theta_prefix(
    edges: tuple[Edge, ...],
    theta: float,
    slope_ok: Callable[[tuple[Edge, ...]], bool] | None = None,
) -> Solution | None:
    """Return the longest prefix clearing the route-level slope floor."""
    return best_theta_prefix_items(
        edges,
        theta,
        length_of=lambda edge: edge.length_m,
        climb_of=lambda edge: edge.d_plus_m + edge.d_minus_m,
        materialize=lambda prefix: prefix,
        slope_ok=slope_ok,
    )


def best_theta_prefix_items[Item](
    items: tuple[Item, ...],
    theta: float,
    *,
    length_of: Callable[[Item], float],
    climb_of: Callable[[Item], float],
    materialize: Callable[[tuple[Item, ...]], tuple[Edge, ...]],
    slope_ok: Callable[[tuple[Edge, ...]], bool] | None = None,
) -> Solution | None:
    """Finalize a represented walk while materializing only its winning prefix."""
    n = len(items)
    cumulative_length = [0.0] * (n + 1)
    cumulative_climb = [0.0] * (n + 1)
    length = 0.0
    climb = 0.0
    for index, item in enumerate(items, start=1):
        length += length_of(item)
        climb += climb_of(item)
        cumulative_length[index] = length
        cumulative_climb[index] = climb

    def _default_check(prefix: tuple[Edge, ...]) -> bool:
        return route_avg_gradient(prefix) >= theta

    check: Callable[[tuple[Edge, ...]], bool] = slope_ok or _default_check
    for end in range(n, 0, -1):
        total_length = cumulative_length[end]
        gradient = cumulative_climb[end] / total_length if total_length > 0.0 else 0.0
        if gradient >= theta:
            edges = materialize(items[:end])
            if check(edges):
                return Solution(edges=edges, objective=cumulative_climb[end])
    return None
