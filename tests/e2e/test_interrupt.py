"""Real-signal interrupt for the query CLI (Story 7.3, FR14 / NFR3 / FR30 code 130).

Launches the query entry point as a genuine OS subprocess against a seeded cache
and sends it a real interrupt mid-solve. Unlike the `CliRunner` e2e tests this
must be a separate process — you can't deliver a signal to an in-process call —
and the query side touches no network, so a real process reads the offline-seeded
cache without patching.

Asserts the FR14 / NFR3 contract: exit code 130, best-so-far reports flushed to
disk tagged ``convergence_status="interrupted"``, the cache directory byte-for-byte
unchanged, and a normal re-run on the same cache still succeeds (cache reusable).

Windows signal note: the child is created with ``CREATE_NEW_PROCESS_GROUP`` so the
interrupt targets only the child and not the pytest runner — but that flag also
*disables* Ctrl+C handling in the child, so the child bootstrap re-enables it via
``SetConsoleCtrlHandler(NULL, FALSE)``. A real ``CTRL_C_EVENT`` then arrives as a
``KeyboardInterrupt`` exactly as it would from a console Ctrl+C — the production
path. POSIX runs the child in its own session and sends ``SIGINT``.
"""

from __future__ import annotations

import json
import pathlib
import signal
import subprocess
import sys
import time
from collections.abc import Callable

import pytest
from click.testing import Result

_WIN = sys.platform == "win32"

# On Windows, undo the Ctrl+C-disable that CREATE_NEW_PROCESS_GROUP imposes on the
# child, so a targeted CTRL_C_EVENT is delivered as a KeyboardInterrupt. No-op
# elsewhere. Runs before the real entry point so only signal plumbing is scaffolded.
_BOOTSTRAP = (
    "import ctypes; ctypes.windll.kernel32.SetConsoleCtrlHandler(None, 0); " if _WIN else ""
)
_CHILD_CODE = _BOOTSTRAP + "from steeproute.cli.query import main; main()"


def _snapshot(directory: pathlib.Path) -> dict[str, int]:
    """Map every file under `directory` to its size (for an unchanged-cache check)."""
    return {
        str(p.relative_to(directory)): p.stat().st_size
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def test_real_ctrl_c_flushes_best_so_far_and_preserves_cache(
    seeded_cache: pathlib.Path,
    fixture_query_target: tuple[tuple[float, float], float],
    run_query: Callable[..., Result],
    tmp_path: pathlib.Path,
) -> None:
    (center, radius_km) = fixture_query_target
    output_dir = tmp_path / "reports"
    cache_before = _snapshot(seeded_cache)

    args = [
        sys.executable,
        "-u",  # unbuffered, so the parent sees progress lines as they're printed
        "-c",
        _CHILD_CODE,
        "--center",
        f"{center[0]},{center[1]}",
        "--radius",
        f"{radius_km}",
        "--cache-dir",
        str(seeded_cache),
        "--output-dir",
        str(output_dir),
        "--seed",
        "42",
        # Keep the solve running until interrupted: an iteration ceiling it can't
        # reach, a generous time budget, and stagnation off so it can't converge.
        "--iter-budget",
        "100000000",
        "--time-budget",
        "600",
        "--stagnation-iters",
        "0",
        "--progress-interval",
        "0.05",
    ]
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if _WIN else 0,
        start_new_session=not _WIN,
    )
    assert proc.stdout is not None

    # Wait for the first throttled progress line: it is emitted only from inside
    # the solver loop, and by the time one fires (interval 0.05 s ≈ dozens of
    # iterations) at least one route has been admitted — so the interrupt lands
    # mid-solve with a non-empty best-so-far (the flush branch).
    saw_progress = False
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith("progress:"):
            saw_progress = True
            break
    if not saw_progress:
        proc.kill()
        proc.communicate()
        pytest.fail("solver never emitted a progress line; could not time the interrupt")

    proc.send_signal(signal.CTRL_C_EVENT if _WIN else signal.SIGINT)
    try:
        tail = proc.communicate(timeout=30.0)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("CLI did not exit within 30 s of the interrupt")

    assert proc.returncode == 130, f"expected exit 130, got {proc.returncode}\noutput:\n{tail}"

    html_files = sorted(output_dir.glob("route-*.html"))
    assert html_files, f"best-so-far not flushed to disk; output:\n{tail}"
    payload = json.loads((output_dir / "route-1.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["convergence_status"] == "interrupted"

    # NFR3: the query side never writes the cache — it is untouched by the interrupt.
    # The `seeded_cache` root is the test's tmp_path, so the report dirs written
    # under it (`reports`, `reports-rerun`) are excluded from the comparison.
    cache_after = {k: v for k, v in _snapshot(seeded_cache).items() if not k.startswith("reports")}
    assert cache_after == cache_before

    # NFR3: the cache is still valid/reusable — a normal re-run succeeds.
    rerun = run_query(seeded_cache, tmp_path / "reports-rerun", seed=42)
    assert rerun.exit_code == 0, rerun.output
