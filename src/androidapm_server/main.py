"""FastAPI application factory and production ASGI entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI

from androidapm_server.api.artifacts import router as artifacts_router
from androidapm_server.api.health import router as health_router
from androidapm_server.api.ingest import router as ingest_router
from androidapm_server.api.middleware import request_context_middleware
from androidapm_server.api.query import router as query_router
from androidapm_server.api.remote_config import router as remote_config_router
from androidapm_server.api.web import router as web_router
from androidapm_server.api.web import ui_router as web_ui_router
from androidapm_server.config import get_settings
from androidapm_server.db.session import close_engine
from androidapm_server.errors import ApiError, api_error_handler, unhandled_error_handler
from androidapm_server.logging import configure_logging

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialize logging and release shared resources on shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info("application_started", environment=settings.environment)
    yield
    await close_engine()
    logger.info("application_stopped")


def create_app() -> FastAPI:
    """Create a dependency-injectable Gateway application."""
    app = FastAPI(
        title="AndroidAPM Gateway",
        version="0.1.0",
        docs_url="/docs" if get_settings().environment != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.middleware("http")(request_context_middleware)
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(remote_config_router)
    app.include_router(artifacts_router)
    app.include_router(query_router)
    app.include_router(web_router)
    app.include_router(web_ui_router)
    return app


app = create_app()


def run() -> None:
    """Run the API with Uvicorn for local and single-process containers."""
    uvicorn.run(
        "androidapm_server.main:app",
        host="0.0.0.0",  # noqa: S104 - the container must accept traffic from its network.
        port=8080,
        proxy_headers=True,
    )


if __name__ == "__main__":
    run()
