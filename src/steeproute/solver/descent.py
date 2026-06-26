"""Direction-aware descent-slope feasibility — the single source of truth (Story 10.2, FR32).

The GRASP solver (`solver/grasp.py`), the exhaustive oracle
(`tests/integration/exhaustive_oracle.py`), and the runtime validator
(`validator.py`) all gate descending traversals on the *same* predicate. Routing
it through this one module keeps their feasible sets bit-identical — the property
the Story 3.7 GRASP-vs-exhaustive quality gate depends on. (Same single-source
discipline as `solver/reuse.py` for the reuse rule and `pipeline.graph.
is_junction_node` for FR31.)

The rule
========

Stage 7 tags every edge with `max_windowed_descent_grad` — the steepest sustained
grade over a fixed window, a *direction-agnostic* property of the physical segment
(`pipeline.climbs`). A specific *traversal* `u → v` is a **descent** when it nets
elevation loss in that direction (`d_minus_m > d_plus_m`). A descending traversal
is infeasible iff the segment's windowed grade exceeds the cap; **uphill**
traversal is never blocked, so the same segment stays eligible as a climb.

A forward climb super-edge is net uphill, so it is never a descent and is never
blocked; *descending a climb* means walking the reverse-direction base connectors
that contraction carried over (each carries the same windowed grade and nets loss),
so the per-edge check governs them with no special super-edge handling.

Robustness
==========

`max_descent_slope is None` means the cap is off → nothing ever blocks. A `None`
`data` (edge absent from the graph) or a missing `max_windowed_descent_grad`
(a hand-built / not-yet-tagged test graph) reads as grade `0.0` → never blocks, so
non-production graphs degrade to the pre-10.2 unconstrained behaviour rather than
raising `KeyError`.

Known limitations (intentional, documented for future revisit)
==============================================================

The cap is scoped to *sustained whole-segment descents*; two cases are deliberately
out of scope. If field use shows either matters, the metric/gate can be tightened
(the call sites are single-sourced here, so the change is localized):

1. **Net-uphill segments are never capped, even with a steep descent inside them.**
   The gate keys on the segment's *net* direction (`d_minus_m > d_plus_m`). A segment
   that climbs overall but contains a steep descending dip is treated as a climb and
   left uncapped. Rationale: the cap protects against routes whose *character* is a
   steep descent; a net climb with a transient dip is a climb. (`max_windowed_descent_grad`
   itself is descent-directional, so promoting this case to "capped" would mean
   dropping the net-direction gate, not changing the metric.)

2. **Transient (sub-window) pitches are averaged out.** The metric measures the net
   drop between the endpoints of each ≥ `_DESCENT_WINDOW_M` window, so a short steep
   pitch that reverses within the window nets a gentle grade. This is by design — the
   cap targets *sustained* descents, not momentary steps — and is why the window
   exists rather than a per-vertex slope.
"""

from __future__ import annotations

from typing import Any

__all__ = ["descends_over_cap"]


def descends_over_cap(data: dict[str, Any] | None, max_descent_slope: float | None) -> bool:
    """`True` iff traversing this edge (as stored) is a descent steeper than the cap.

    `data` is the contracted-graph edge-data dict for the directed `(u, v, key)`
    being traversed; its `d_plus_m` / `d_minus_m` are oriented for that direction,
    so net loss (`d_minus_m > d_plus_m`) identifies a descending traversal. Off
    (returns `False`) when the cap is unset or the edge is a net climb / flat.
    """
    if max_descent_slope is None or data is None:
        return False
    if data.get("d_minus_m", 0.0) <= data.get("d_plus_m", 0.0):
        return False  # net climb or flat in the traversed direction — never capped
    return data.get("max_windowed_descent_grad", 0.0) > max_descent_slope
