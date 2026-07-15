# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# Reason: Starlette's TestClient re-exports httpx, whose response accessors
# (.get/.status_code/.headers/.text/.json()) surface as Unknown — a stub boundary,
# same per-file relaxation pattern used for the networkx boundary in conftest.py.
"""Integration tests for the web App API (App Stories 1.2 + 1.3).

Story 1.2 surface: the FastAPI factory, the home page + global header markup, and
the static mounts (frontend dir + reused CLI Leaflet assets).

Story 1.3 surface: the job lifecycle over the real store + single-worker queue,
driven through `TestClient` as a context manager (which runs the `lifespan`, and
with it the worker). The worker spawns a fake CLI script instead of the real
`steeproute-setup` via an injected `build_argv`, and writes to a tmp store root —
so no real build/network runs. SSE, stop, delete, and `/regions` arrive later.
"""

from __future__ import annotations

import pathlib
import sys
import textwrap
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from steeproute.app.main import create_app
from steeproute.app.models import JobRecord

# Fake CLI: emit a stdout line, then exit with the code encoded in argv.
#   argv: <exit_code>
_FAKE_CLI = textwrap.dedent(
    """
    import sys
    print("steeproute-setup: cache-miss (fake)")
    sys.exit(int(sys.argv[1]))
    """
).strip()

# Fake CLI that runs until killed — for the Stop path (Story 1.5). Prints a line
# so the job is observably alive, then blocks so it stays `running` until the
# worker's `stop()` kills it.
_FAKE_CLI_SLEEP = textwrap.dedent(
    """
    import sys, time
    print("steeproute-setup: build starting (fake)", flush=True)
    time.sleep(60)
    """
).strip()


def _make_fake_build_argv(fake_cli: pathlib.Path, exit_code: int):
    def build_argv(_record: JobRecord) -> list[str]:
        return [sys.executable, str(fake_cli), str(exit_code)]

    return build_argv


def _make_sleeper_build_argv(fake_cli: pathlib.Path):
    def build_argv(_record: JobRecord) -> list[str]:
        return [sys.executable, str(fake_cli)]

    return build_argv


def _sleeper_client(tmp_path: pathlib.Path) -> TestClient:
    fake_cli = tmp_path / "fake_sleep_cli.py"
    fake_cli.write_text(_FAKE_CLI_SLEEP, encoding="utf-8")
    app = create_app(
        store_root=tmp_path / "jobs",
        build_argv=_make_sleeper_build_argv(fake_cli),
    )
    return TestClient(app)


def _poll_until_status(
    client: TestClient, job_id: str, target: str, timeout: float = 15.0
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] == target:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach {target!r} within {timeout}s")


def _lifecycle_client(tmp_path: pathlib.Path, exit_code: int) -> TestClient:
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(_FAKE_CLI, encoding="utf-8")
    app = create_app(
        store_root=tmp_path / "jobs",
        build_argv=_make_fake_build_argv(fake_cli, exit_code),
    )
    return TestClient(app)


def _poll_until_terminal(client: TestClient, job_id: str, timeout: float = 15.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    terminal = {"done", "failed", "stopped"}
    while time.monotonic() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] in terminal:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not terminate within {timeout}s")


def _setup_body() -> dict[str, object]:
    return {"kind": "setup", "area": {"center": [45.26, 5.788], "radius_km": 2.0}}


def _client() -> TestClient:
    return TestClient(create_app())


def test_create_app_returns_fastapi_instance() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)


def test_home_page_served_as_html() -> None:
    resp = _client().get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_home_page_renders_global_header() -> None:
    body = _client().get("/").text
    # App name links back to Map home ("/"); Runs link to the run library.
    assert "steeproute" in body
    assert 'href="/"' in body
    assert ">Runs<" in body
    # The live-job-indicator slot is present but empty (wired to SSE in Story 1.5).
    assert 'id="live-indicator"' in body


def test_frontend_css_served_from_static_mount() -> None:
    resp = _client().get("/static/css/app.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_vendored_leaflet_assets_served_no_cdn() -> None:
    client = _client()
    js = client.get("/vendor/leaflet-1.9.4.min.js")
    css = client.get("/vendor/leaflet-1.9.4.min.css")
    assert js.status_code == 200
    assert css.status_code == 200
    # Reused from the CLI report's vendored copy — the home page must not point
    # at a CDN.
    assert "unpkg.com" not in _client().get("/").text
    assert "cdn" not in _client().get("/").text.lower()


# --- Story 1.3: job lifecycle ------------------------------------------------


def test_post_job_returns_201_queued(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        resp = client.post("/jobs", json=_setup_body())
        assert resp.status_code == 201
        body = resp.json()
        assert body["kind"] == "setup"
        assert body["status"] == "queued"
        assert body["id"]
        # snake_case, no envelope: the record is returned directly at top level.
        assert "data" not in body
        assert body["created_at"] is not None
        assert body["area"]["radius_km"] == 2.0


def test_setup_job_runs_to_done(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        job_id = client.post("/jobs", json=_setup_body()).json()["id"]
        final = _poll_until_terminal(client, job_id)
        assert final["status"] == "done"
        assert final["exit_code"] == 0
        assert final["started_at"] is not None
        assert final["finished_at"] is not None


def test_setup_job_nonzero_exit_marks_failed(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=2) as client:
        job_id = client.post("/jobs", json=_setup_body()).json()["id"]
        final = _poll_until_terminal(client, job_id)
        assert final["status"] == "failed"
        assert final["exit_code"] == 2
        assert final["stdout_tail"]  # tail captured for diagnostics


def test_get_unknown_job_returns_404(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        resp = client.get("/jobs/does-not-exist")
        assert resp.status_code == 404
        assert "detail" in resp.json()  # FastAPI default error shape, no envelope


def test_list_jobs_reflects_submissions(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        assert client.get("/jobs").json() == []
        first = client.post("/jobs", json=_setup_body()).json()["id"]
        second = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_terminal(client, first)
        _poll_until_terminal(client, second)
        ids = [job["id"] for job in client.get("/jobs").json()]
        assert ids == [first, second]  # id-ordered == submission order


def test_query_kind_rejected_422(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        body = {"kind": "query", "area": {"center": [45.26, 5.788], "radius_km": 2.0}}
        assert client.post("/jobs", json=body).status_code == 422


def test_bad_area_rejected_422(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        # Missing radius_km → pydantic validation error.
        body = {"kind": "setup", "area": {"center": [45.26, 5.788]}}
        assert client.post("/jobs", json=body).status_code == 422


# --- Story 1.5: hard-cancel Stop ---------------------------------------------


def test_stop_running_job_marks_stopped(tmp_path: pathlib.Path) -> None:
    with _sleeper_client(tmp_path) as client:
        job_id = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_status(client, job_id, "running")
        resp = client.post(f"/jobs/{job_id}/stop")
        assert resp.status_code == 200
        final = _poll_until_terminal(client, job_id)
        assert final["status"] == "stopped"
        # Hard cancel → CLI Ctrl-C exit convention, and no result (Category 7).
        assert final["exit_code"] == 130
        assert final["result_dir"] is None


def test_stop_queued_job_returns_409(tmp_path: pathlib.Path) -> None:
    # Concurrency = 1: while the first job sleeps (running), a second stays queued.
    with _sleeper_client(tmp_path) as client:
        running = client.post("/jobs", json=_setup_body()).json()["id"]
        queued = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_status(client, running, "running")
        assert client.get(f"/jobs/{queued}").json()["status"] == "queued"
        resp = client.post(f"/jobs/{queued}/stop")
        assert resp.status_code == 409
        assert "detail" in resp.json()
        # Clean up the running job so teardown is prompt.
        client.post(f"/jobs/{running}/stop")


def test_stop_finished_job_returns_409(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        job_id = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_terminal(client, job_id)
        assert client.post(f"/jobs/{job_id}/stop").status_code == 409


def test_stop_unknown_job_returns_404(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        resp = client.post("/jobs/does-not-exist/stop")
        assert resp.status_code == 404
        assert "detail" in resp.json()


# --- Story 1.5: run-watch page + frontend modules served ---------------------


def test_run_watch_page_served_as_html() -> None:
    # Any id resolves to the same page; the page's JS reads the id from the URL.
    resp = _client().get("/runs/anything")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert 'id="log-tail"' in body  # the progress frame
    assert 'id="stop-btn"' in body  # the Stop control
    assert 'id="live-indicator"' in body  # global chrome present here too


def test_frontend_js_modules_served_from_static_mount() -> None:
    client = _client()
    for module in ("api.js", "run-watch.js", "live-indicator.js"):
        resp = client.get(f"/static/js/{module}")
        assert resp.status_code == 200, module
        assert "javascript" in resp.headers["content-type"]
    # api.js is the single URL holder; the other modules import from it.
    assert "/jobs/" in client.get("/static/js/api.js").text
