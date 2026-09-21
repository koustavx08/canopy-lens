"""CanopyLens API entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8000   (from the `backend/` directory)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, get_settings
from .errors import register_error_handlers
from .logging_config import configure_logging
from .routers import analysis, boundary, health
from .schemas import HealthResponse
from .services.uploads import discard_orphaned_uploads

log = logging.getLogger("canopylens")

DESCRIPTION = """
CanopyLens detects individual tree crowns in high-resolution imagery and
estimates canopy area.

**Honesty contract:** every number returned by this API is computed from the
imagery you uploaded. When a value cannot be derived — for example a physical
area from a raster with no georeferencing — the API returns an error naming the
problem instead of an estimate.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_dirs()
    # An analysis deletes its own upload when it finishes, so anything still in
    # the directory at startup belongs to a run that was killed mid-flight -
    # and the job that owned it died with the process. Left alone it is disk
    # that nothing will ever free.
    discarded = discard_orphaned_uploads(settings.upload_dir)
    if discarded:
        log.info("Removed %d orphaned upload(s) from a previous run", discarded)
    # Importing the engines package registers the detection engines.
    import canopylens_ml.engines  # noqa: F401

    log.info("CanopyLens API %s starting (environment=%s)", __version__, settings.environment)
    log.info("Data directory: %s", settings.data_dir)
    yield
    # Stop the analysis pool before the process goes: a thread still waiting to
    # start work would otherwise hold the interpreter open at exit.
    analysis.shutdown_analysis_pool()
    log.info("CanopyLens API shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    app.include_router(health.router, prefix="/api")
    app.include_router(boundary.router, prefix="/api")
    app.include_router(analysis.router, prefix="/api")

    # Container orchestrators and uptime checks look for /health at the root,
    # and it costs one route to be found there as well as under /api.
    @app.get("/health", response_model=HealthResponse, tags=["system"], summary="Liveness probe")
    def health_at_root(settings: Settings = Depends(get_settings)) -> HealthResponse:
        return health.health(settings)

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {"name": settings.app_name, "version": __version__, "docs": "/docs"}

    return app


app = create_app()
