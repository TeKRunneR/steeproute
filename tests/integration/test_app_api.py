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

from steeproute.app.geocode import GeocodeFn
from steeproute.app.main import create_app
from steeproute.app.models import AreaSpec, JobKind, JobRecord, JobStatus, new_job_id, utcnow_iso
from steeproute.app.store import JobStore
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


# --- Story 4.3: area_label reverse-geocode -----------------------------------


def _labelled_client(tmp_path: pathlib.Path, geocode: GeocodeFn) -> TestClient:
    """A lifecycle client (exit 0) with an injected reverse-geocoder stub, so the
    `area_label` stamping path runs fully offline (no Nominatim call)."""
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(_FAKE_CLI, encoding="utf-8")
    app = create_app(
        store_root=tmp_path / "jobs",
        build_argv=_make_fake_build_argv(fake_cli, 0),
        geocode=geocode,
    )
    return TestClient(app)


def test_area_label_stamped_from_geocoder(tmp_path: pathlib.Path) -> None:
    seen: list[tuple[float, float]] = []

    def _geocode(lat: float, lon: float) -> str | None:
        seen.append((lat, lon))
        return "Chamrousse"

    with _labelled_client(tmp_path, _geocode) as client:
        body = client.post("/jobs", json=_setup_body()).json()
        assert body["area_label"] == "Chamrousse"
        # Center passed through as (lat, lon) — not transposed.
        assert seen == [(45.26, 5.788)]
        # Persisted, so the run library reads the label straight off GET /jobs.
        assert client.get(f"/jobs/{body['id']}").json()["area_label"] == "Chamrousse"


def test_area_label_none_when_geocoder_raises(tmp_path: pathlib.Path) -> None:
    def _boom(_lat: float, _lon: float) -> str | None:
        raise RuntimeError("geocoder exploded")

    with _labelled_client(tmp_path, _boom) as client:
        resp = client.post("/jobs", json=_setup_body())
        # A raising geocoder never blocks or errors the job: still 201, label unset.
        assert resp.status_code == 201
        assert resp.json()["area_label"] is None


def test_area_label_none_when_labelling_disabled(tmp_path: pathlib.Path) -> None:
    # No geocoder injected (the offline-safe default) → no label, job still created.
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        body = client.post("/jobs", json=_setup_body())
        assert body.status_code == 201
        assert body.json()["area_label"] is None


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


# --- Story 3.2: cancel a queued job (DELETE) ---------------------------------


def test_delete_queued_job_returns_204_and_removes_it(tmp_path: pathlib.Path) -> None:
    # Concurrency = 1: while the first job sleeps (running), a second stays queued.
    with _sleeper_client(tmp_path) as client:
        running = client.post("/jobs", json=_setup_body()).json()["id"]
        queued = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_status(client, running, "running")
        assert client.get(f"/jobs/{queued}").json()["status"] == "queued"

        resp = client.delete(f"/jobs/{queued}")
        assert resp.status_code == 204
        assert resp.content == b""  # 204 No Content, no body

        # Gone from the registry and unreachable — it was cancelled, not run.
        assert client.get(f"/jobs/{queued}").status_code == 404
        assert [j["id"] for j in client.get("/jobs").json()] == [running]
        # Clean up the running job so teardown is prompt.
        client.post(f"/jobs/{running}/stop")


def test_delete_running_job_returns_409(tmp_path: pathlib.Path) -> None:
    # A running job is cancelled with Stop, not DELETE (architecture Category 7).
    with _sleeper_client(tmp_path) as client:
        running = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_status(client, running, "running")
        resp = client.delete(f"/jobs/{running}")
        assert resp.status_code == 409
        assert "detail" in resp.json()
        assert client.get(f"/jobs/{running}").json()["status"] == "running"  # untouched
        client.post(f"/jobs/{running}/stop")


def test_delete_finished_job_returns_409(tmp_path: pathlib.Path) -> None:
    # A terminal job has nothing to cancel → 409 (not 204), and stays in history.
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        job_id = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_terminal(client, job_id)
        assert client.delete(f"/jobs/{job_id}").status_code == 409
        assert client.get(f"/jobs/{job_id}").status_code == 200


def test_delete_unknown_job_returns_404(tmp_path: pathlib.Path) -> None:
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        resp = client.delete("/jobs/does-not-exist")
        assert resp.status_code == 404
        assert "detail" in resp.json()


def test_cancelled_job_is_skipped_and_queue_keeps_serving(tmp_path: pathlib.Path) -> None:
    # The cancelled (tombstoned) id lingers in the in-memory queue; the worker
    # must pop it, skip it (no store record), and keep serving the next job — one
    # cancelled job never stalls the queue (AC #1).
    with _sleeper_client(tmp_path) as client:
        running = client.post("/jobs", json=_setup_body()).json()["id"]
        cancelled = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_status(client, running, "running")
        assert client.delete(f"/jobs/{cancelled}").status_code == 204

        # Free the worker: it now pops the cancelled tombstone (skip) then the
        # next real job. A third job reaching `running` proves the queue survived.
        client.post(f"/jobs/{running}/stop")
        nxt = client.post("/jobs", json=_setup_body()).json()["id"]
        _poll_until_status(client, nxt, "running")
        assert client.get(f"/jobs/{cancelled}").status_code == 404  # never resurrected
        client.post(f"/jobs/{nxt}/stop")


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
        # Quality-demo overrides are what the form actually prefills, including
        # the steep-route-tool defaults corrected in Story app-4-2.
        assert fields["iter_budget"]["default"] == 1_000_000
        assert fields["difficulty_cap"]["default"] == "T4"
        assert fields["max_descent_slope"]["default"] == 0.4
        assert fields["start_at_junction"]["default"] is True
        # The form is flat (Story app-4-2): no basic/advanced grouping on the wire.
        assert "group" not in fields["theta"]


def test_home_page_renders_map_and_actions() -> None:
    body = _client().get("/").text
    # Full-bleed map container + the two context-sensitive actions.
    assert 'id="map"' in body
    assert 'id="build-btn"' in body
    assert 'id="configure-btn"' in body
    assert "map-home.js" in body
    # Selection-mode control (Story 4.1): the three modes are always present.
    assert 'id="mode-control"' in body
    for value in ("area-pick", "move-selection", "select-region"):
        assert f'value="{value}"' in body
    # Global chrome still present on the reworked home page.
    assert 'id="live-indicator"' in body


def test_frontend_assets_served_no_cache() -> None:
    # Buildless assets carry no content hash, so the app must forbid stale
    # browser copies (a cached old map-home.js silently breaks a shipped change).
    client = _client()
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/static/js/map-home.js").headers["cache-control"] == "no-cache"
    # The immutable vendored Leaflet bundle keeps ordinary caching (no override).
    assert "cache-control" not in client.get("/vendor/leaflet-1.9.4.min.js").headers


# --- Story 2.3: result view (view the resulting routes) ----------------------


def _seed_job(
    tmp_path: pathlib.Path,
    *,
    kind: JobKind = JobKind.QUERY,
    status: JobStatus = JobStatus.DONE,
    route_indices: tuple[int, ...] = (1, 2),
    make_result_dir: bool = True,
) -> tuple[JobStore, str]:
    """Seed a job (and, for a done query, its `result/route-<i>.html` files)
    directly on the store the TestClient app will read. No worker/subprocess —
    the result-view endpoints only read the store, so a crafted record suffices."""
    store = JobStore(tmp_path / "jobs")
    job_id = new_job_id()
    record = JobRecord(
        id=job_id,
        kind=kind,
        area=AreaSpec(center=(45.26, 5.788), radius_km=2.0),
        params={},
        status=status,
        created_at=utcnow_iso(),
    )
    if kind is JobKind.QUERY and status is JobStatus.DONE:
        result_dir = store.job_dir(job_id) / "result"
        if make_result_dir:
            result_dir.mkdir(parents=True, exist_ok=True)
            for i in route_indices:
                (result_dir / f"route-{i}.html").write_text(
                    f"<!doctype html><h1>route {i}</h1>", encoding="utf-8"
                )
        record.result_dir = str(result_dir)
    store.create(record)  # writes job.json into the per-job dir
    # A sibling progress log the result endpoint must never serve.
    (store.job_dir(job_id) / "progress.ndjson").write_text("{}\n", encoding="utf-8")
    return store, job_id


def _seeded_client(tmp_path: pathlib.Path) -> TestClient:
    return TestClient(create_app(store_root=tmp_path / "jobs"))


def test_result_routes_lists_all_and_serves_each(tmp_path: pathlib.Path) -> None:
    _, job_id = _seed_job(tmp_path, route_indices=(1, 2))
    with _seeded_client(tmp_path) as client:
        listing = client.get(f"/jobs/{job_id}/routes")
        assert listing.status_code == 200
        data = listing.json()
        assert [r["filename"] for r in data] == ["route-1.html", "route-2.html"]
        assert [r["index"] for r in data] == [1, 2]
        for name in ("route-1.html", "route-2.html"):
            resp = client.get(f"/jobs/{job_id}/result/{name}")
            assert resp.status_code == 200
            assert "text/html" in resp.headers["content-type"]
            assert "<h1>route" in resp.text


def test_result_routes_ordered_numerically_not_lexically(tmp_path: pathlib.Path) -> None:
    _, job_id = _seed_job(tmp_path, route_indices=(1, 2, 10))
    with _seeded_client(tmp_path) as client:
        # Lexical order would put route-10 before route-2; the endpoint sorts by
        # the integer index.
        data = client.get(f"/jobs/{job_id}/routes").json()
        assert [r["filename"] for r in data] == [
            "route-1.html",
            "route-2.html",
            "route-10.html",
        ]
        assert [r["index"] for r in data] == [1, 2, 10]


def test_result_file_traversal_is_refused(tmp_path: pathlib.Path) -> None:
    _, job_id = _seed_job(tmp_path, route_indices=(1,))
    with _seeded_client(tmp_path) as client:
        # `../job.json` (fully percent-encoded so it reaches the handler rather
        # than being normalized away) must not escape `<job>/result/`.
        escaped = client.get(f"/jobs/{job_id}/result/%2e%2e%2fjob.json")
        assert escaped.status_code == 404
        assert '"id"' not in escaped.text  # the record was NOT leaked
        # The sibling progress log is equally unreachable.
        assert client.get(f"/jobs/{job_id}/result/%2e%2e%2fprogress.ndjson").status_code == 404


def test_missing_route_file_404(tmp_path: pathlib.Path) -> None:
    _, job_id = _seed_job(tmp_path, route_indices=(1,))
    with _seeded_client(tmp_path) as client:
        assert client.get(f"/jobs/{job_id}/result/route-9.html").status_code == 404


def test_result_view_gated_to_done_query(tmp_path: pathlib.Path) -> None:
    # Every non-(done-query) job offers no viewable result: routes + file 404.
    cases = [
        _seed_job(tmp_path, kind=JobKind.QUERY, status=JobStatus.STOPPED)[1],
        _seed_job(tmp_path, kind=JobKind.QUERY, status=JobStatus.FAILED)[1],
        _seed_job(tmp_path, kind=JobKind.SETUP, status=JobStatus.DONE)[1],
        _seed_job(tmp_path, kind=JobKind.QUERY, status=JobStatus.RUNNING)[1],
    ]
    with _seeded_client(tmp_path) as client:
        for job_id in cases:
            assert client.get(f"/jobs/{job_id}/routes").status_code == 404
            assert client.get(f"/jobs/{job_id}/result/route-1.html").status_code == 404


def test_result_endpoints_unknown_job_404(tmp_path: pathlib.Path) -> None:
    with _seeded_client(tmp_path) as client:
        assert client.get("/jobs/nope/routes").status_code == 404
        assert client.get("/jobs/nope/result/route-1.html").status_code == 404


def test_result_routes_empty_when_no_files(tmp_path: pathlib.Path) -> None:
    # A done query that produced no routes (graceful degradation) lists [], not 404.
    _, job_id = _seed_job(tmp_path, route_indices=(), make_result_dir=False)
    with _seeded_client(tmp_path) as client:
        assert client.get(f"/jobs/{job_id}/routes").json() == []


def test_result_view_page_and_js_served() -> None:
    client = _client()
    page = client.get("/runs/some-id/result")
    assert page.status_code == 200
    body = page.text
    assert 'id="route-frame"' in body
    assert 'id="route-selector"' in body
    assert "result.js" in body
    assert client.get("/static/js/result.js").status_code == 200


# --- Story 3.1: run library list ---------------------------------------------

# Fake query CLI: emit a minimal run summary (with total_objective), exit 0 — so
# the worker's done-query path captures result_objective from the stdout tail.
# 8421.5 is exactly representable, so downstream assertions compare it directly.
_FAKE_CLI_QUERY_SUMMARY = textwrap.dedent(
    """
    print("stage: validate-render ...")
    print("stage: validate-render: 0.20 s")
    print("--- Run summary ---")
    print("routes_returned: 3/5")
    print("total_objective: 8421.5")
    print("validation_failures: 0")
    print("convergence_status: converged")
    """
).strip()


def _query_summary_client(tmp_path: pathlib.Path) -> TestClient:
    fake_cli = tmp_path / "fake_query_cli.py"
    fake_cli.write_text(_FAKE_CLI_QUERY_SUMMARY, encoding="utf-8")
    app = create_app(
        store_root=tmp_path / "jobs",
        build_argv=_make_sleeper_build_argv(fake_cli),  # argv has no exit arg → exits 0
    )
    return TestClient(app)


def test_query_done_captures_result_objective(tmp_path: pathlib.Path) -> None:
    # A done query records its summary total_objective so the run-library card
    # can show the cost without re-parsing route JSON (App Story 3.1).
    with _query_summary_client(tmp_path) as client:
        resp = client.post("/jobs", json=_query_body())
        assert resp.status_code == 201
        final = _poll_until_terminal(client, resp.json()["id"])
        assert final["status"] == "done"
        assert final["result_objective"] == 8421.5


def test_setup_done_has_no_result_objective(tmp_path: pathlib.Path) -> None:
    # A setup job produces no route report → no cost metric (stays None).
    with _lifecycle_client(tmp_path, exit_code=0) as client:
        resp = client.post("/jobs", json=_setup_body())
        final = _poll_until_terminal(client, resp.json()["id"])
        assert final["status"] == "done"
        assert final["result_objective"] is None


def test_failed_query_has_no_result_objective(tmp_path: pathlib.Path) -> None:
    # A non-zero exit is failed, not done — the objective capture is skipped, so
    # the card falls back to the exit code (never a stale/partial cost).
    with _lifecycle_client(tmp_path, exit_code=1) as client:
        resp = client.post("/jobs", json=_query_body())
        final = _poll_until_terminal(client, resp.json()["id"])
        assert final["status"] == "failed"
        assert final["result_objective"] is None


def test_run_library_page_and_js_served() -> None:
    client = _client()
    page = client.get("/runs")  # no id — the library list, not run-watch
    assert page.status_code == 200
    body = page.text
    assert 'id="runs-list"' in body
    assert "runs.js" in body
    assert 'id="live-indicator"' in body  # global chrome present
    assert client.get("/static/js/runs.js").status_code == 200


def test_runs_bare_path_distinct_from_run_watch() -> None:
    # `/runs` serves the library (runs-list); `/runs/{id}` serves run-watch
    # (job-identity) — the bare path must not collide with the id route.
    client = _client()
    assert 'id="runs-list"' in client.get("/runs").text
    assert 'id="job-identity"' in client.get("/runs/some-id").text


# --- Story 3.3: restart recovery (boot reconciliation via the lifespan) -------

# Fake CLI that records its run order — for the boot queue-rebuild test. argv:
# <order_file> <job_id>. Appends its id then exits 0, so the order file shows the
# sequence in which the re-enqueued jobs actually ran (concurrency = 1).
_FAKE_CLI_ORDER = textwrap.dedent(
    """
    import sys
    with open(sys.argv[1], "a", encoding="utf-8") as fh:
        _ = fh.write(sys.argv[2] + "\\n")
    print("done (fake)")
    sys.exit(0)
    """
).strip()


def _seed_status(store: JobStore, job_id: str, status: JobStatus) -> None:
    """Persist a bare setup record in a given status directly on the store —
    simulating what a pre-restart store looks like on disk (no worker)."""
    store.create(
        JobRecord(
            id=job_id,
            kind=JobKind.SETUP,
            area=AreaSpec(center=(45.26, 5.788), radius_km=2.0),
            params={},
            status=status,
            created_at=utcnow_iso(),
        )
    )


def test_boot_recovers_interrupted_and_rebuilds_queue(tmp_path: pathlib.Path) -> None:
    # Seed a store as if a crash left it: one `running` (interrupted), two
    # `queued`, one `done`. Then boot the app on that store (the TestClient
    # context runs the lifespan, hence the boot reconciliation).
    store = JobStore(tmp_path / "jobs")
    _seed_status(store, "01-running", JobStatus.RUNNING)
    _seed_status(store, "02-queued", JobStatus.QUEUED)
    _seed_status(store, "03-queued", JobStatus.QUEUED)
    _seed_status(store, "04-done", JobStatus.DONE)

    order_file = tmp_path / "run-order.txt"
    fake_cli = tmp_path / "fake_order_cli.py"
    fake_cli.write_text(_FAKE_CLI_ORDER, encoding="utf-8")

    def build_argv(record: JobRecord) -> list[str]:
        return [sys.executable, str(fake_cli), str(order_file), record.id]

    app = create_app(store_root=tmp_path / "jobs", build_argv=build_argv)
    with TestClient(app) as client:
        # Recovery: the interrupted running job is now failed(interrupted), and
        # was NOT re-enqueued (it is failed, not queued).
        interrupted = client.get("/jobs/01-running").json()
        assert interrupted["status"] == "failed"
        assert interrupted["failure_reason"] == "interrupted"
        assert interrupted["finished_at"] is not None
        # The terminal `done` record is left untouched.
        assert client.get("/jobs/04-done").json()["status"] == "done"
        # Queue rebuild: the two persisted `queued` jobs resume and run to done
        # (without the rebuild they would sit `queued` forever).
        assert _poll_until_terminal(client, "02-queued")["status"] == "done"
        assert _poll_until_terminal(client, "03-queued")["status"] == "done"

    # ...and they ran in creation order (concurrency = 1, FIFO from list()).
    assert order_file.read_text(encoding="utf-8").split() == ["02-queued", "03-queued"]
