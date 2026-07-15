"""FastAPI application factory + entry point for the steeproute web App.

App Story 1.2 — the runnable skeleton: a factory, a placeholder lifespan (the
single-worker job runner is added in a later story), the two static mounts
(frontend dir + the CLI's already-vendored Leaflet assets), and the home page.
No job API, store, queue, SSE, or cli_adapter exists yet by design.

Run it with `uv run steeproute-app` (single-worker uvicorn) or, for hot reload,
`uv run fastapi dev src/steeproute/app/main.py`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Frontend shell shipped as package data under this subpackage.
_STATIC_DIR: Path = Path(str(files("steeproute.app"))) / "static"
# Reuse the Leaflet 1.9.4 copy the CLI HTML report already vendors — no CDN, no
# new asset dependency. Same resource handle `output.py::_load_asset` reads from.
_VENDOR_ASSETS_DIR: Path = Path(str(files("steeproute"))) / "templates" / "assets"


router = APIRouter()


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan. A placeholder for now — the single-worker job queue
    is started here in a later story (architecture-app.md §Category 2)."""
    logger.info("steeproute-app starting")
    yield
    logger.info("steeproute-app shutting down")


def create_app() -> FastAPI:
    """Build the FastAPI application (factory, so tests get isolated instances)."""
    app = FastAPI(title="steeproute", lifespan=lifespan)
    app.include_router(router)

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
