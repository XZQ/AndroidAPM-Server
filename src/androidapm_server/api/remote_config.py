"""Authenticated remote configuration read endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.api.ingest import _required_header
from androidapm_server.auth import authenticate_ingest_key
from androidapm_server.constants import (
    HEADER_APP_ID,
    HEADER_ENVIRONMENT,
    HEADER_INSTALLATION_ID,
)
from androidapm_server.db.models import RemoteConfigVersion, Tenant
from androidapm_server.db.session import get_session
from androidapm_server.remote_config import SignedConfig, rollout_includes

router = APIRouter(prefix="/v1", tags=["remote-config"])


@router.get("/config", response_model=None)
async def get_remote_config(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Return the latest active, unexpired signed revision for a stable rollout bucket."""
    app_id = _required_header(request, HEADER_APP_ID, 256)
    environment = _required_header(request, HEADER_ENVIRONMENT, 128)
    installation_id = _required_header(request, HEADER_INSTALLATION_ID, 256)
    principal = await authenticate_ingest_key(
        session,
        request.headers.get("authorization"),
        app_id,
        environment,
    )
    now = datetime.now(UTC)
    result = await session.execute(
        select(RemoteConfigVersion, Tenant.config_rollout_salt)
        .join(Tenant, Tenant.id == RemoteConfigVersion.tenant_id)
        .where(
            RemoteConfigVersion.tenant_id == principal.tenant_id,
            RemoteConfigVersion.app_id == app_id,
            RemoteConfigVersion.environment == environment,
            RemoteConfigVersion.active.is_(True),
            RemoteConfigVersion.expires_at > now,
        )
        .order_by(RemoteConfigVersion.revision.desc())
        .limit(1)
    )
    row = result.one_or_none()
    if row is None:
        return Response(status_code=204)
    config, rollout_salt = row
    if not rollout_includes(rollout_salt, installation_id, config.rollout_basis_points):
        return Response(status_code=204)
    etag = f'"config-{config.revision}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    signed = SignedConfig(
        config.revision,
        _aware(config.issued_at),
        _aware(config.expires_at),
        config.rollout_basis_points,
        config.payload_json,
        config.key_id,
        config.signature_b64,
    )
    content: dict[str, Any] = signed.envelope()
    return JSONResponse(
        content=content,
        headers={"ETag": etag, "Cache-Control": "private, no-cache"},
    )


def _aware(value: datetime) -> datetime:
    """Restore UTC tzinfo for SQLite tests while PostgreSQL returns aware values."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
