# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# Reason: Starlette's TestClient re-exports httpx, whose response accessors
# (.get/.status_code/.headers/.text) surface as Unknown — a stub boundary, same
# per-file relaxation pattern used for the networkx boundary in conftest.py.
"""Integration smoke tests for the web App skeleton (App Story 1.2).

Covers only the runnable shell: the FastAPI factory, the home page + global
header markup, and the static mounts (frontend dir + reused CLI Leaflet assets).
The job-lifecycle API (POST/GET /jobs, SSE, ...) arrives in later App stories and
is tested then; there are deliberately no such endpoints yet.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from steeproute.app.main import create_app


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
