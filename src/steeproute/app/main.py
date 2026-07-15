"""FastAPI application factory + entry point for the steeproute web App.

App Story 1.2 stood up the runnable skeleton (factory, static mounts, home page).
App Story 1.3 hangs the job runner off it: a per-job JSON store and a single
serial worker (concurrency = 1, architecture-app.md §Category 2) started in the
`lifespan`, plus the `POST/GET /jobs` API. Progress classification + SSE (1.4),
the map/run-watch/run-library UI (1.5+), and `/regions` (1.6) come later.

Run it with `uv run steeproute-app` (single-worker uvicorn) or, for hot reload,
`uv run fastapi dev src/steeproute/app/main.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import pathlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from steeproute.app.api import router as jobs_router
from steeproute.app.queue import BuildArgv, JobQueue, Worker, default_build_argv
from steeproute.app.sse import ProgressHub
from steeproute.app.store import JobStore, default_store_root

logger = logging.getLogger(__name__)

# Frontend shell shipped as package data under this subpackage.
_STATIC_DIR: pathlib.Path = pathlib.Path(str(files("steeproute.app"))) / "static"
# Reuse the Leaflet 1.9.4 copy the CLI HTML report already vendors — no CDN, no
# new asset dependency. Same resource handle `output.py::_load_asset` reads from.
_VENDOR_ASSETS_DIR: pathlib.Path = pathlib.Path(str(files("steeproute"))) / "templates" / "assets"


router = APIRouter()


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@router.get("/runs/{job_id}", include_in_schema=False)
def run_watch() -> FileResponse:
    """Serve the S3 Run-watch page (Story 1.5). UI lives under `/runs*`, the JSON
    API under `/jobs*`; the page's JS reads the `{job_id}` back out of the URL
    (the handler needs no param). The `/runs` run-library list (no id) lands in
    Story 3.1 — no route conflict."""
    return FileResponse(_STATIC_DIR / "run-watch.html")


def _make_lifespan(
    *,
    store_root: pathlib.Path | None,
    build_argv: BuildArgv | None,
):
    """Build the lifespan that owns the job runner.

    `store_root` and `build_argv` are injectable so tests use a tmp store and a
    fake subprocess command; production passes neither and gets the real defaults.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        store = JobStore(store_root if store_root is not None else default_store_root())
        queue = JobQueue()
        hub = ProgressHub()
        worker = Worker(store, queue, build_argv=build_argv or default_build_argv, hub=hub)
        app.state.job_store = store
        app.state.job_queue = queue
        app.state.progress_hub = hub
        # Exposed so `POST /jobs/{id}/stop` can reach the running child (Story 1.5).
        app.state.job_worker = worker

        task = asyncio.create_task(worker.run())
        logger.info("steeproute-app started; single-worker job queue running")
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            logger.info("steeproute-app shutting down; worker stopped")

    return lifespan


def create_app(
    *,
    store_root: pathlib.Path | None = None,
    build_argv: BuildArgv | None = None,
) -> FastAPI:
    """Build the FastAPI application (factory, so tests get isolated instances)."""
    app = FastAPI(
        title="steeproute",
        lifespan=_make_lifespan(store_root=store_root, build_argv=build_argv),
    )
    app.include_router(router)
    app.include_router(jobs_router)

    # Frontend assets (CSS/JS) and the reused Leaflet bundle. Kept on distinct
    # prefixes; the home page references `/static/...` and `/vendor/...`.
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    app.mount("/vendor", StaticFiles(directory=_VENDOR_ASSETS_DIR), name="vendor")

    return app


# Module-level app for `fastapi dev src/steeproute/app/main.py`.
app = create_app()


def run() -> None:
    """`steeproute-app` entry point: launch uvicorn with a single worker.

    Deliberately NOT the click `run_entry_point` wrapper the CLIs use — this is a
    server launcher. Concurrency = 1 (NFR1); the solver saturates all cores, so
    there is never more than one worker process.
    """
    import uvicorn

    uvicorn.run("steeproute.app.main:app", host="127.0.0.1", port=8000, workers=1)
