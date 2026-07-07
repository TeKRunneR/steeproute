# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# Reason: networkx MultiDiGraph operations surface as Unknown; same external-boundary
# pattern as the other pipeline modules.
"""Primitives shared across the vectorized / de-churned pipeline stages (Story 14.2).

These live in one module rather than being reimplemented per stage so a fix to a
non-trivial primitive (the graph-rebuild idiom, the per-edge monotone-offset
searchsorted trick) lands in one place instead of drifting across copies.
"""

from __future__ import annotations

from collections.abc import Container
from typing import Literal

import networkx as nx
import numpy as np


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
