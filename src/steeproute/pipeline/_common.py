# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx MultiDiGraph operations surface as Unknown; same external-boundary
# pattern as the other pipeline modules.
"""Primitives shared across the vectorized / de-churned pipeline stages (Story 14.2).

These live in one module rather than being reimplemented per stage so a fix to a
non-trivial primitive (the graph-rebuild idiom, the per-edge monotone-offset
searchsorted trick) lands in one place instead of drifting across copies.
"""

from __future__ import annotations

from collections.abc import Container, Sequence
from typing import Literal

import networkx as nx
import numpy as np
import shapely


def flat_coordinates(
    geoms: Sequence[shapely.LineString],
) -> tuple[np.ndarray, np.ndarray]:
    """Gather `geoms`' coordinates into ONE flat `(V, 2)` array + per-geometry offsets.

    Returns `(coords, offs)` where geometry `i`'s vertices are `coords[offs[i]:offs[i+1]]`
    and `offs` has length `len(geoms) + 1`. One `shapely.get_coordinates` call for the
    whole batch replaces per-geometry `np.asarray(geom.coords)` + `np.concatenate`
    (measured 3.77 s → 1.51 s over an r20 graph's 2.86 M coordinates, bit-identical
    values — Story 16.2). Callers keep their own geometry-type validation so their
    error messages stay module-specific.

    z is dropped (shapely's 2D default), matching the `coords[:, 0]` / `coords[:, 1]`
    slicing every caller applies.
    """
    if not geoms:
        return np.empty((0, 2), dtype=np.float64), np.zeros(1, dtype=np.intp)
    coords, idx = shapely.get_coordinates(geoms, return_index=True)
    counts = np.bincount(idx, minlength=len(geoms))
    offs = np.zeros(len(geoms) + 1, dtype=np.intp)
    np.cumsum(counts, out=offs[1:])
    return np.asarray(coords, dtype=np.float64), offs


def empty_like(
    graph: nx.MultiDiGraph, exclude_nodes: Container[int] | None = None
) -> nx.MultiDiGraph:
    """A new empty `MultiDiGraph` carrying `graph`'s graph-level attrs + its nodes.

    The build-from-kept-edges primitive for the de-churned stages: callers add
    only the surviving edges. Node order and node attributes are preserved (minus
    any in `exclude_nodes`); edges are added by the caller in the source graph's
    iteration order, so the rebuilt graph is content- and order-identical to the
    old copy-then-remove result.
    """
    out: nx.MultiDiGraph = nx.MultiDiGraph()
    out.graph.update(graph.graph)
    if exclude_nodes:
        out.add_nodes_from((n, d) for n, d in graph.nodes(data=True) if n not in exclude_nodes)
    else:
        out.add_nodes_from(graph.nodes(data=True))
    return out


def per_edge_searchsorted(
    cum: np.ndarray,
    idx: np.ndarray,
    local_targets: np.ndarray,
    target_edge: np.ndarray,
    side: Literal["left", "right"] = "left",
) -> np.ndarray:
    """One global `np.searchsorted` that stays within each edge's block.

    `cum` is a per-edge-local nondecreasing key (reset to 0 at each edge start);
    `idx[i]` is the edge owning vertex `i`. `local_targets[s]` is a search value
    expressed in edge `target_edge[s]`'s local coordinate. Each edge's block is
    offset by a constant strictly larger than any local key *or* target, so the
    concatenated `cum` is globally sorted and every target searches only within
    its own edge. Returns the raw `np.searchsorted` indices into `cum`; callers
    apply their own boundary clamp (the two current callers want different ones).

    Deriving the offset from the max of *both* `cum` and `local_targets` is the
    single safety invariant — it must dominate every value that can appear inside
    one edge's block — so callers no longer hand-derive a per-site margin.
    """
    cmax = float(cum.max(initial=0.0))
    tmax = float(local_targets.max(initial=0.0))
    offset = max(cmax, tmax) + 1.0
    gmono = cum + idx * offset
    targets = local_targets + target_edge * offset
    return np.searchsorted(gmono, targets, side=side)
