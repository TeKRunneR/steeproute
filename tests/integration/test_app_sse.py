# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# Reason: Starlette's TestClient re-exports httpx, whose response accessors
# (.get/.status_code/.headers/.text/.json()) surface as Unknown — a stub
# boundary, same per-file relaxation used in test_app_api.py.
"""Integration tests for the SSE progress stream (App Story 1.4).

`GET /jobs/{id}/events` classifies a setup job's stdout into the `ProgressModel`,
persists it to `progress.ndjson`, and streams it snapshot-then-tail. Driven
through `TestClient` as a context manager (which runs the `lifespan`, and with it
the worker + SSE hub). The worker spawns a fake CLI emitting the REAL setup
stage-line shapes (so the classifier path is genuinely exercised) instead of the
real `steeproute-setup`, and writes to a tmp store root — no real build/network.
"""

from __future__ import annotations

import pathlib
import sys
import textwrap
import time

from fastapi.testclient import TestClient

from steeproute.app.main import create_app
from steeproute.app.models import JobRecord

# Fake CLI: emit the real setup stdout line shapes, sleeping `SLEEP` between each
# (to widen the window for a live connect), then exit 0.
#   argv: <sleep_s>
_FAKE_CLI = textwrap.dedent(
    """
    import sys, time
    sleep_s = float(sys.argv[1])
    lines = [
        "stage: osm-download (one Overpass request; typically takes minutes) ...",
        "stage: osm-download: 0.01 s",
        "stage: trail-filter ...",
        "stage: trail-filter: 0.00 s",
        "steeproute-setup: cache-miss",
        "  cache_key_hash: deadbeefdeadbeef",
        "  entry: CACHE_ROOT/steeproute/areas/deadbeefdeadbeef",
        "  elapsed: 0.02 s",
    ]
    for ln in lines:
        print(ln, flush=True)
        time.sleep(sleep_s)
    sys.exit(0)
    """
).strip()

# The fake prints exactly this many non-empty lines → this many `progress` events
# (the classifier emits one snapshot per non-empty line; the endpoint dedupes by
# seq, so the count is invariant regardless of the snapshot/live split).
_EXPECTED_PROGRESS_EVENTS = 8


def _client(tmp_path: pathlib.Path, *, sleep_s: float) -> TestClient:
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(_FAKE_CLI, encoding="utf-8")

    def build_argv(_record: JobRecord) -> list[str]:
        return [sys.executable, str(fake_cli), str(sleep_s)]

    app = create_app(store_root=tmp_path / "jobs", build_argv=build_argv)
    return TestClient(app)


def _setup_body() -> dict[str, object]:
    return {"kind": "setup", "area": {"center": [45.26, 5.788], "radius_km": 2.0}}


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 15.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    terminal = {"done", "failed", "stopped"}
    while time.monotonic() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in terminal:
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not terminate within {timeout}s")


def _parse_events(sse_text: str) -> list[tuple[str, str]]:
    """Parse an SSE body into (event_name, data) pairs. Keepalive comment lines
    (`: …`) are ignored."""
    events: list[tuple[str, str]] = []
    event_name = "message"
    for line in sse_text.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((event_name, line[len("data:"):].strip()))
            event_name = "message"
    return events


def test_sse_unknown_job_returns_404(tmp_path: pathlib.Path) -> None:
    with _client(tmp_path, sleep_s=0.0) as client:
        resp = client.get("/jobs/does-not-exist/events")
        assert resp.status_code == 404
        assert "detail" in resp.json()  # FastAPI default error shape, no envelope


def test_sse_snapshot_replay_after_terminal(tmp_path: pathlib.Path) -> None:
    # Connect AFTER the job has finished → the persisted snapshot IS the whole
    # stream: every progress event replayed, then the terminal status.
    with _client(tmp_path, sleep_s=0.0) as client:
        job_id = client.post("/jobs", json=_setup_body()).json()["id"]
        final = _poll_until_terminal(client, job_id)
        assert final["status"] == "done"

        resp = client.get(f"/jobs/{job_id}/events")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_events(resp.text)

        progress = [d for name, d in events if name == "progress"]
        status = [d for name, d in events if name == "status"]
        # No gap, no duplicate: exactly one progress event per emitted line.
        assert len(progress) == _EXPECTED_PROGRESS_EVENTS
        assert len(status) == 1
        # Progress carries the classified model; the setup stages are present.
        assert any('"stage_name":"osm-download"' in d.replace(" ", "") for d in progress)
        assert any('"stage_name":"trail-filter"' in d.replace(" ", "") for d in progress)
        # Terminal status event reports done.
        assert '"status":"done"' in status[0].replace(" ", "")
        # Ordering: the status event is the last event on the wire.
        assert events[-1][0] == "status"


def test_sse_live_tail_streams_progress_then_status(tmp_path: pathlib.Path) -> None:
    # Connect immediately after submit (job queued/running) → snapshot-then-tail
    # over the live stream. Same observable invariant: all progress, then status.
    with _client(tmp_path, sleep_s=0.05) as client:
        job_id = client.post("/jobs", json=_setup_body()).json()["id"]

        # get() blocks until the stream closes (terminal status), reading the
        # full body — the fake finishes quickly.
        resp = client.get(f"/jobs/{job_id}/events")
        assert resp.status_code == 200
        events = _parse_events(resp.text)

        progress = [d for name, d in events if name == "progress"]
        status = [d for name, d in events if name == "status"]
        assert len(progress) == _EXPECTED_PROGRESS_EVENTS
        assert len(status) == 1
        assert '"status":"done"' in status[0].replace(" ", "")
        assert events[-1][0] == "status"

        # The persisted job also reached done with the progress log on disk.
        assert _poll_until_terminal(client, job_id)["status"] == "done"
        assert (tmp_path / "jobs" / job_id / "progress.ndjson").is_file()
