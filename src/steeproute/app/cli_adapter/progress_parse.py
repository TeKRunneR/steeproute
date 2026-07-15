"""Stdout line classifier — setup flavour (App Story 1.4).

Only `cli_adapter` knows CLI stdout line shapes (architecture-app.md §"The
load-bearing rule"). This module classifies the **setup** flavour (Flavour A of
`tests/fixtures/app_stdout/format-inventory.md`) into the unified
`ProgressModel`. The query non-solve stages and GRASP progress lines (Flavours
B/C, where `grasp` is populated) extend this classifier in Story 2.2.

Key finding pinned by the Story 1.1 spike: CLI stage lines carry a **name only,
no `n/total`** — so `stage_index` is derived positionally (incremented per stage
start) and `stage_total` comes from the known ordered stage list below. A setup
**cache-hit** emits zero stage lines (summary block only), which this classifier
tolerates (it never assumes a stage was seen).
"""

from __future__ import annotations

import re
from collections import deque
from typing import final

from steeproute.app.models import JobKind, Phase, ProgressModel

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

# How many trailing raw stdout lines the model carries for the UI log tail.
LOG_TAIL_MAX = 20

# A1 stage start: `stage: <name>[ (<note>)] ...` — the optional ` (<note>)` is a
# human honesty annotation on the start line only; strip it to the clean name.
_STAGE_START = re.compile(r"^stage: (?P<rest>.+?) \.\.\.$")
# A3 stage done: `stage: <name>: <elapsed> s` (name here is always clean).
_STAGE_DONE = re.compile(r"^stage: (?P<name>.+): (?P<elapsed>\d+(?:\.\d+)?) s$")


@final
class SetupProgressParser:
    """Stateful setup-flavour classifier: feed one stdout line, get the updated
    `ProgressModel` snapshot back (or `None` for a blank line).

    Every non-empty line is appended to the bounded `log_tail`. Lines that match
    a setup rule additionally advance the stage state; everything else
    (`  tile i/N` within-stage lines, the `steeproute-setup: …` summary block,
    unknown lines) only feeds `log_tail` and never moves the stage.
    """

    def __init__(self) -> None:
        self._stage_name: str | None = None
        self._stage_index = 0
        self._elapsed: float | None = None
        self._log_tail: deque[str] = deque(maxlen=LOG_TAIL_MAX)

    def feed(self, line: str) -> ProgressModel | None:
        if not line.strip():
            return None
        self._log_tail.append(line)

        start = _STAGE_START.match(line)
        if start is not None:
            self._stage_name = start.group("rest").split(" (", 1)[0]
            self._stage_index += 1
        else:
            done = _STAGE_DONE.match(line)
            if done is not None:
                self._stage_name = done.group("name")
                self._elapsed = float(done.group("elapsed"))

        return self._snapshot()

    def _snapshot(self) -> ProgressModel:
        return ProgressModel(
            phase=Phase.SETUP,
            stage_name=self._stage_name,
            stage_index=self._stage_index,
            stage_total=len(SETUP_STAGES),
            grasp=None,
            elapsed=self._elapsed,
            log_tail=list(self._log_tail),
        )


def progress_parser_for(kind: JobKind) -> SetupProgressParser:
    """Return a fresh stateful classifier for a job kind.

    Only `setup` is classified in this story; the `query` flavour (Flavours B/C)
    lands in Story 2.2. Query jobs are rejected at the API (422), so a query kind
    never reaches the worker that calls this.
    """
    if kind is JobKind.SETUP:
        return SetupProgressParser()
    raise NotImplementedError(
        f"progress classification for kind {kind.value!r} arrives in Story 2.2"
    )
