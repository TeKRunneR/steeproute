"""Pin the set of graph-ownership-transfer call sites in `src/`.

`consume=` and `inplace=` are opt-in aliasing escapes: the caller hands a
`MultiDiGraph` to a stage that mutates or strips it in place, skipping a
full-graph copy worth seconds at r20. Each is safe only because the specific
caller owns its graph exclusively and never reads the pre-call version. That is a
precondition no signature or type can express, and a second caller added without
noticing would corrupt results silently rather than fail.

So the call sites are enumerated here instead of being described in prose at each
definition. Adding one means adding it to this test, which is the point: it forces
the "does this caller actually own the graph?" question to be answered once,
deliberately, per site.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "steeproute"

# Call sites that pass `consume=True` / `inplace=True`, as
# `<posix path relative to src/steeproute>::<callee>`. Every entry is a place a
# caller forfeits its input graph.
_EXPECTED_OWNERSHIP_CALLS: frozenset[str] = frozenset(
    {
        # The query CLI owns the cache-loaded graph and discards it after
        # reshaping; it then owns the reshaped graph and re-filters it in place.
        "cli/query.py::operationalize_graph",
        "cli/query.py::filter_trails",
        # The setup CLI built this graph itself and only reads cache metadata after.
        "cli/setup.py::write_entry",
        # The setup orchestrator owns the graph across stages 3-5 and 6-7.
        "pipeline/__init__.py::sample_elevation",
        "pipeline/__init__.py::graph_smooth_elevation",
        "pipeline/__init__.py::graph_deadband_elevation",
        "pipeline/__init__.py::compute_edge_metrics",
    }
)

# `<callee>(` … `consume=True` / `inplace=True` before the closing paren. Callees
# are single identifiers here, so a plain non-greedy scan is enough.
_CALL_RE = re.compile(
    r"(?P<callee>\w+)\(\s*(?P<args>[^()]*?(?:\([^()]*\)[^()]*?)*?)\)",
    re.DOTALL,
)
_OWNERSHIP_KWARG_RE = re.compile(r"\b(?:consume|inplace)\s*=\s*True\b")


def _ownership_call_sites() -> set[str]:
    found: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        rel = path.relative_to(_SRC).as_posix()
        for match in _CALL_RE.finditer(source):
            if not _OWNERSHIP_KWARG_RE.search(match.group("args")):
                continue
            # Skip the definitions' own `inplace: bool = False` params and any
            # match inside a docstring or comment: only real call expressions
            # have the kwarg on the argument side of a callee name.
            found.add(f"{rel}::{match.group('callee')}")
    return found


def test_ownership_transfer_call_sites_are_the_expected_set() -> None:
    actual = _ownership_call_sites()
    unexpected = actual - _EXPECTED_OWNERSHIP_CALLS
    missing = _EXPECTED_OWNERSHIP_CALLS - actual
    assert not unexpected, (
        "New `consume=True` / `inplace=True` call site(s): "
        f"{sorted(unexpected)}. Each one forfeits its input graph to the callee. "
        "Confirm the caller owns that graph exclusively and never reads the "
        "pre-call version, then add it to _EXPECTED_OWNERSHIP_CALLS."
    )
    assert not missing, (
        f"Expected ownership-transfer call site(s) gone: {sorted(missing)}. "
        "If the copy-elision was deliberately reverted, drop the entry."
    )


@pytest.mark.parametrize("callee", sorted({e.split("::")[1] for e in _EXPECTED_OWNERSHIP_CALLS}))
def test_ownership_callee_defaults_to_the_copying_path(callee: str) -> None:
    """Every ownership-transfer callee must default to NOT taking ownership.

    The aliasing escape has to be opt-in: a caller that says nothing gets the pure
    "input never mutated" contract. A flipped default would silently transfer
    ownership at every other call site in the codebase.
    """
    import steeproute.cache
    import steeproute.pipeline
    import steeproute.pipeline.climbs
    import steeproute.pipeline.dem
    import steeproute.pipeline.osm
    import steeproute.pipeline.smoothing

    modules = (
        steeproute.pipeline,
        steeproute.pipeline.climbs,
        steeproute.pipeline.dem,
        steeproute.pipeline.osm,
        steeproute.pipeline.smoothing,
        steeproute.cache,
    )
    func = next((getattr(m, callee) for m in modules if hasattr(m, callee)), None)
    assert func is not None, f"{callee} not found on any pipeline/cache module"

    import inspect

    params = inspect.signature(func).parameters
    flag = params.get("consume") or params.get("inplace")
    assert flag is not None, f"{callee} takes neither `consume` nor `inplace`"
    assert flag.default is False, f"{callee}'s ownership flag must default to False"
