"""Interrupt-safety hooks for the GRASP solver (Epic 4 stub, Story 3.6).

Reserved for Story 4.3, where the CLI's `KeyboardInterrupt` handling lands.
Architecture §Cat 5b is explicit that interrupt handling lives **outside** the
solver — the CLI catches `KeyboardInterrupt`, reads `GraspSolver.best_so_far`,
and writes outputs (Story 4.3's `cli/query.py` try/except wrapper). The solver
itself stays oblivious to signals; this module's job in Epic 4 will be hosting
the small helpers (e.g. a context manager around the solver call site) that
make that wrapper readable.

Until then this module exists to keep the `solver/` import surface stable
across stories: `from steeproute.solver.anytime import ...` should not become
a "module not found" error when Epic 4 fleshes it out. No live logic here.
"""

from __future__ import annotations

__all__: list[str] = []
