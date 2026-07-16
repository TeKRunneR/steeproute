"""Stdout line classifier — setup + query flavours (App Stories 1.4 / 2.2).

Only `cli_adapter` knows CLI stdout line shapes (architecture-app.md §"The
load-bearing rule"). This module classifies the three progress flavours of
`tests/fixtures/app_stdout/format-inventory.md` into the unified
`ProgressModel`:

- **Flavour A** (setup stages) → `SetupProgressParser`, phase `setup`.
- **Flavour B** (query non-solve stages) → `QueryProgressParser`, phase `query`.
- **Flavour C** (GRASP solver events) → `QueryProgressParser`, phase `solve`,
  `grasp={iter, best_cost}` populated.

Key finding pinned by the Story 1.1 spike: CLI stage lines carry a **name only,
no `n/total`** — so `stage_index` is derived positionally (incremented per stage
start) and `stage_total` comes from the known ordered stage list per job kind.
The A1/A3 (setup) and B1/B3 (query) stage-line shapes are byte-identical (both
come from the same `StageProgress` seam), so the shared positional tracking
lives in `_StageParser` below; the two flavours differ only in their stage list,
their phase, and whether they classify GRASP `progress:` lines.
"""

from __future__ import annotations

import re
from collections import deque
from typing import final, override

from steeproute.app.models import GraspProgress, JobKind, Phase, ProgressModel

# Setup stages in pipeline order on a cache-miss (format-inventory.md §Key
# finding). The wire carries a name only, so this list supplies `stage_total`;
# position is tracked by incrementing, not by name lookup (`trail-filter` occurs
# in both setup and query kinds, so a name→index map would be ambiguous).
SETUP_STAGES: tuple[str, ...] = (
    "osm-download",
    "trail-filter",
    "polyline-smoothing",
    "resampling",
    "dem-resolve",
    "elevation-sampling",
    "cache-write",
)

# Query non-solve stages in order (format-inventory.md §Key finding). The solve
# phase's GRASP `progress:` lines fall between the `climb-contraction` done line
# and the `validate-render` start line — they do NOT advance the stage index.
QUERY_STAGES: tuple[str, ...] = (
    "load-prepared-area",
    "elevation-reshape",
    "trail-filter",
    "climb-detection",
    "climb-contraction",
    "validate-render",
)

# How many trailing raw stdout lines the model carries for the UI log tail.
LOG_TAIL_MAX = 20

# A1/B1 stage start: `stage: <name>[ (<note>)] ...` — the optional ` (<note>)` is
# a human honesty annotation on the start line only; strip it to the clean name.
_STAGE_START = re.compile(r"^stage: (?P<rest>.+?) \.\.\.$")
# A3/B3 stage done: `stage: <name>: <elapsed> s` (name here is always clean).
_STAGE_DONE = re.compile(r"^stage: (?P<name>.+): (?P<elapsed>\d+(?:\.\d+)?) s$")

# C1 single-process GRASP: `progress: iter=<int> best_objective=<%.1f>
# elapsed=<%.1f>s eta=<…> stagnation=<int>`. Disambiguated from C2 by the first
# token after `progress: ` being `iter=`.
_GRASP_SINGLE = re.compile(
    r"^progress: iter=(?P<iter>\d+) best_objective=(?P<cost>-?\d+(?:\.\d+)?) "
    r"elapsed=(?P<elapsed>\d+(?:\.\d+)?)s"
)
# C2 parallel GRASP: `progress: workers=<r>/<t> iters=<int>
# best_worker_objective=<%.1f> elapsed=<%.1f>s`. `best_worker_objective` is the
# leading worker's running sum and understates the merged result (the honest
# final figure is the summary's `total_objective`, which has no ProgressModel
# field and lands in `log_tail` only).
_GRASP_PARALLEL = re.compile(
    r"^progress: workers=\d+/\d+ iters=(?P<iter>\d+) "
    r"best_worker_objective=(?P<cost>-?\d+(?:\.\d+)?) elapsed=(?P<elapsed>\d+(?:\.\d+)?)s"
)


class _StageParser:
    """Shared positional stage tracking for both progress flavours.

    Feed one stdout line at a time; get the updated `ProgressModel` snapshot back
    (or `None` for a blank line). Every non-empty line is appended to the bounded
    `log_tail`. Stage-start lines advance the stage index; stage-done lines record
    the elapsed time; everything else only feeds `log_tail`. Subclasses set the
    stage list + initial phase and may extend `_classify` (query adds GRASP).
    """

    _STAGES: tuple[str, ...] = ()

    def __init__(self, phase: Phase) -> None:
        self._phase: Phase = phase
        self._stage_name: str | None = None
        self._stage_index: int = 0
        self._elapsed: float | None = None
        self._grasp: GraspProgress | None = None
        self._log_tail: deque[str] = deque(maxlen=LOG_TAIL_MAX)

    def feed(self, line: str) -> ProgressModel | None:
        if not line.strip():
            return None
        self._log_tail.append(line)
        self._classify(line)
        return self._snapshot()

    def _classify(self, line: str) -> bool:
        """Update stage state from a stage line; return True if `line` was one
        (so subclasses can skip their own matching)."""
        start = _STAGE_START.match(line)
        if start is not None:
            self._enter_stage(start.group("rest").split(" (", 1)[0])
            return True
        done = _STAGE_DONE.match(line)
        if done is not None:
            self._stage_name = done.group("name")
            self._elapsed = float(done.group("elapsed"))
            return True
        return False

    def _enter_stage(self, name: str) -> None:
        self._stage_name = name
        self._stage_index += 1

    def _snapshot(self) -> ProgressModel:
        return ProgressModel(
            phase=self._phase,
            stage_name=self._stage_name,
            stage_index=self._stage_index,
            stage_total=len(self._STAGES),
            grasp=self._grasp,
            elapsed=self._elapsed,
            log_tail=list(self._log_tail),
        )


@final
class SetupProgressParser(_StageParser):
    """Setup-flavour classifier (App Story 1.4).

    Phase is always `setup`; `grasp` is always `null` (setup emits no `progress:`
    line). A setup **cache-hit** emits the summary block and zero stage lines, so
    the classifier must stay coherent with no stage seen — which it does: the
    summary lines match no stage rule and only feed `log_tail`.
    """

    _STAGES = SETUP_STAGES

    def __init__(self) -> None:
        super().__init__(Phase.SETUP)


@final
class QueryProgressParser(_StageParser):
    """Query-flavour classifier (App Story 2.2).

    Query non-solve stages (Flavour B) advance `stage n/total` positionally over
    `QUERY_STAGES` with phase `query`, exactly like setup. Between the
    `climb-contraction` done line and the `validate-render` start line the solver
    emits throttled GRASP `progress:` lines (Flavour C): these set phase `solve`
    and populate `grasp={iter, best_cost}`, handling both single-process
    (`iter=`/`best_objective=`) and parallel (`workers=`/`iters=`/
    `best_worker_objective=`) shapes, disambiguated by the first token.

    `grasp` is present **only** during the solve, never reserved: every stage
    start resets it to `null` and returns the phase to `query`, so the
    Run-watch readout appears during the solve and disappears at
    `validate-render` (epics-app.md §Story 2.2 AC2; UX spec §S3 / UX-DR3). A
    `progress:` line never advances the stage index.
    """

    _STAGES = QUERY_STAGES

    def __init__(self) -> None:
        super().__init__(Phase.QUERY)

    @override
    def _classify(self, line: str) -> bool:
        if super()._classify(line):
            return True
        grasp = _GRASP_SINGLE.match(line) or _GRASP_PARALLEL.match(line)
        if grasp is not None:
            self._phase = Phase.SOLVE
            self._grasp = GraspProgress(
                iter=int(grasp.group("iter")),
                best_cost=float(grasp.group("cost")),
            )
            self._elapsed = float(grasp.group("elapsed"))
            return True
        return False

    @override
    def _enter_stage(self, name: str) -> None:
        # A stage start after the solve (validate-render) drops the GRASP readout
        # and returns to the query phase — grasp is present only during the solve.
        self._phase = Phase.QUERY
        self._grasp = None
        super()._enter_stage(name)


def progress_parser_for(kind: JobKind) -> SetupProgressParser | QueryProgressParser:
    """Return a fresh stateful classifier for a job kind: the setup stage-aware
    classifier for `setup`, the stage + GRASP-aware classifier for `query`."""
    if kind is JobKind.SETUP:
        return SetupProgressParser()
    return QueryProgressParser()
