# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeArgument=false
# Reason: Starlette's TestClient re-exports httpx, whose response accessors
# (.get/.status_code/.headers/.text/.json()) surface as Unknown — a stub boundary,
# same per-file relaxation pattern used for the networkx boundary in conftest.py.
"""Integration tests for the web App API (App Stories 1.2 + 1.3 + 1.5 + 1.6).

Story 1.2 surface: the FastAPI factory, the home page + global header markup, and
the static mounts (frontend dir + reused CLI Leaflet assets).

Story 1.3 surface: the job lifecycle over the real store + single-worker queue,
driven through `TestClient` as a context manager (which runs the `lifespan`, and
with it the worker). The worker spawns a fake CLI script instead of the real
`steeproute-setup` via an injected `build_argv`, and writes to a tmp store root —
so no real build/network runs.

Story 1.5 surface: the hard-cancel Stop path + run-watch page/JS served.

Story 1.6 surface: `GET /regions` over a crafted cache root (real `write_entry`,
no build) and the map-home markup + `map-home.js` served. `DELETE /jobs/{id}`
arrives with Story 3.2.
"""

from __future__ import annotations

import pathlib
import sys
import textwrap
import time

import networkx as nx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from steeproute.app.main import create_app
from steeproute.app.models import JobRecord
from steeproute.cache import Manifest, write_entry
from steeproute.models import Area

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


def _poll_until_terminal(
    client: TestClient, job_id: str, timeout: float = 15.0
) -> dict[str, object]:
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
    for module in ("api.js", "run-watch.js", "live-indicator.js", "map-home.js"):
        resp = client.get(f"/static/js/{module}")
        assert resp.status_code == 200, module
        assert "javascript" in resp.headers["content-type"]
    # api.js is the single URL holder; the other modules import from it.
    assert "/jobs/" in client.get("/static/js/api.js").text


# --- Story 1.6: GET /regions + map home --------------------------------------


def _seed_cache_entry(cache_root: pathlib.Path, cache_key_hash: str, area: Area) -> None:
    """Register a built region via real `write_entry` (empty graph — no build)."""
    manifest = Manifest(
        area=area,
        untagged_policy="include",
        dem_version="ign_rge_alti_5m_2024-12",
        pipeline_content_hash="a" * 64,
        osm_extract_date="2026-05-20T12:00:00Z",
        cache_key_hash=cache_key_hash,
        steeproute_version="0.1.0",
        steeproute_commit="abc1234",
        created_at="2026-05-20T12:00:00Z",
    )
    write_entry(cache_root, manifest, nx.MultiDiGraph())


def _regions_client(tmp_path: pathlib.Path, seeded: tuple[str, Area] | None = None) -> TestClient:
    cache_root = tmp_path / "cache"
    if seeded is not None:
        _seed_cache_entry(cache_root, seeded[0], seeded[1])
    app = create_app(store_root=tmp_path / "jobs", cache_root=cache_root)
    return TestClient(app)


def test_regions_empty_cache_returns_empty_list(tmp_path: pathlib.Path) -> None:
    with _regions_client(tmp_path) as client:
        resp = client.get("/regions")
        assert resp.status_code == 200
        assert resp.json() == []  # empty cache is [], not an error


def test_regions_lists_built_region_snake_case(tmp_path: pathlib.Path) -> None:
    area = Area(center=(45.19, 5.72), radius_km=10.0)
    with _regions_client(tmp_path, seeded=("ab" * 8, area)) as client:
        body = client.get("/regions").json()
        assert isinstance(body, list) and len(body) == 1  # bare list, no envelope
        region = body[0]
        assert region["cache_key_hash"] == "ab" * 8
        assert region["center"] == [45.19, 5.72]
        assert region["radius_km"] == 10.0
        # snake_case bbox the frontend renders/tests against verbatim.
        assert set(region["bounds"]) == {"south", "west", "north", "east"}
        assert region["bounds"]["south"] < 45.19 < region["bounds"]["north"]
        assert region["bounds"]["west"] < 5.72 < region["bounds"]["east"]


def test_regions_resolve_reports_coverage_over_built_region(tmp_path: pathlib.Path) -> None:
    area = Area(center=(45.19, 5.72), radius_km=12.0)
    with _regions_client(tmp_path, seeded=("ab" * 8, area)) as client:
        # A smaller selection at the same center is strictly contained → covered.
        inside = client.get("/regions/resolve", params={"lat": 45.19, "lon": 5.72, "radius_km": 10})
        assert inside.status_code == 200
        body = inside.json()
        assert body["covered"] is True
        assert body["cache_key_hash"] == "ab" * 8
        assert set(body["bounds"]) == {"south", "west", "north", "east"}
        # A far-away selection is not covered.
        outside = client.get("/regions/resolve", params={"lat": 46.5, "lon": 7.0, "radius_km": 10})
        assert outside.json()["covered"] is False
        assert outside.json()["cache_key_hash"] is None


def test_regions_resolve_rejects_nonpositive_radius(tmp_path: pathlib.Path) -> None:
    with _regions_client(tmp_path) as client:
        resp = client.get("/regions/resolve", params={"lat": 45.19, "lon": 5.72, "radius_km": 0})
        assert resp.status_code == 422  # Query(gt=0)


# --- Story 2.1: query jobs + config-form schema ------------------------------


def _query_body(**params: object) -> dict[str, object]:
    return {
        "kind": "query",
        "area": {"center": [45.26, 5.788], "radius_km": 2.0},
        "params": params,
    }


def test_query_job_accepted_and_runs_to_done(tmp_path: pathlib.Path) -> None:
    # The fake build_argv ignores kind/params entirely (same fixture the setup
    # lifecycle tests use), so this exercises: the API no longer 422s `query`,
    # the worker's per-job result_dir assignment, and the new
    # `QueryProgressParser` (no longer a `NotImplementedError`) on real stdout.
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        resp = client.post("/jobs", json=_query_body())
        assert resp.status_code == 201
        body = resp.json()
        assert body["kind"] == "query"
        assert body["status"] == "queued"

        final = _poll_until_terminal(client, body["id"])
        assert final["status"] == "done"
        assert final["exit_code"] == 0
        # A query job gets a per-job result directory assigned by the worker.
        assert final["result_dir"] is not None
        assert body["id"] in final["result_dir"]


def test_query_job_invalid_param_type_rejected_422(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        body = _query_body(theta="not-a-number")
        assert client.post("/jobs", json=body).status_code == 422


def test_query_job_mismatched_params_rejected_422(tmp_path: pathlib.Path) -> None:
    # A setup-only field posted under kind=query must not be silently ignored.
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        body = {
            "kind": "query",
            "area": {"center": [45.26, 5.788], "radius_km": 2.0},
            "params": {"force_refresh": True},
        }
        assert client.post("/jobs", json=body).status_code == 422


def test_query_job_accepts_explicit_params(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        resp = client.post("/jobs", json=_query_body(theta=0.35, n=8, seed=42))
        assert resp.status_code == 201
        assert resp.json()["params"]["theta"] == 0.35
        assert resp.json()["params"]["n"] == 8
        assert resp.json()["params"]["seed"] == 42


def test_query_params_schema_endpoint(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        resp = client.get("/params/query-schema")
        assert resp.status_code == 200
        fields = {f["name"]: f for f in resp.json()}
        # Excluded (App-owned, plus click's own --version) flags never reach the frontend.
        for excluded in (
            "center",
            "radius",
            "output_dir",
            "cache_dir",
            "verbose",
            "quiet",
            "version",
        ):
            assert excluded not in fields
        # Quality-demo overrides are what the form actually prefills.
        assert fields["iter_budget"]["default"] == 200_000
        assert fields["difficulty_cap"]["default"] == "T4"
        assert fields["theta"]["group"] == "basic"


def test_home_page_renders_map_and_actions() -> None:
    body = _client().get("/").text
    # Full-bleed map container + the two context-sensitive actions.
    assert 'id="map"' in body
    assert 'id="build-btn"' in body
    assert 'id="configure-btn"' in body
    assert "map-home.js" in body
    # Global chrome still present on the reworked home page.
    assert 'id="live-indicator"' in body
