"""Liveness, readiness, and Prometheus endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server import __version__
from androidapm_server.db.session import get_session

router = APIRouter(tags=["operations"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Report process liveness without touching dependencies."""
    return {"status": "ok", "version": __version__}


@router.get("/health/ready")
async def ready(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    """Report readiness only when the durable ACK database is reachable."""
    await session.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Expose process metrics in Prometheus text format."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
