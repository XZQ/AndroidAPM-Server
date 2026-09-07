"""Same-origin Web session endpoints and built console asset delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.config import Settings, get_settings
from androidapm_server.db.models import AuditLog
from androidapm_server.db.session import get_session
from androidapm_server.query import QueryModel, QueryScope, scope_for
from androidapm_server.query_auth import QueryPrincipal, authenticate_query_key
from androidapm_server.web_auth import (
    WEB_CSRF_COOKIE,
    WEB_SESSION_COOKIE,
    authenticate_web_session,
    create_web_session,
    require_web_csrf,
)

router = APIRouter(prefix="/v1/web", tags=["web"])
ui_router = APIRouter(include_in_schema=False)

WEB_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; font-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; img-src 'self' data:; "
        "object-src 'none'; script-src 'self'; style-src 'self'"
    ),
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
WEB_FAVICON_CACHE_CONTROL = "public, max-age=86400"


class WebSessionLoginRequest(QueryModel):
    """A one-time browser exchange for an existing fixed-scope Query key."""

    query_key: SecretStr


class WebSessionResponse(QueryModel):
    """Non-secret browser identity and expiry state."""

    request_id: str
    scope: QueryScope
    role: str
    expires_at_ms: int


@router.post("/session", response_model=WebSessionResponse)
async def create_session(
    login: WebSessionLoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WebSessionResponse:
    """Exchange one Query key for a short-lived, revocable, HttpOnly browser session."""
    query_key = login.query_key.get_secret_value()
    principal = await authenticate_query_key(session, f"Bearer {query_key}")
    issued = create_web_session(principal, settings)
    session.add(
        _session_audit(
            principal.tenant_id,
            principal.key_id,
            request.state.request_id,
            "web.session.create",
            "success",
            principal.app_id,
            principal.environment,
            issued.expires_at,
        )
    )
    await session.commit()
    _set_session_cookies(response, issued.token, issued.csrf_token, issued.expires_at, settings)
    _no_store(response)
    return _session_response(request, principal, issued.expires_at)


@router.get("/session", response_model=WebSessionResponse)
async def get_session_state(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WebSessionResponse:
    """Restore non-secret identity state after a page reload."""
    principal, claims = await authenticate_web_session(session, request, settings)
    _no_store(response)
    return _session_response(request, principal, claims.expires_at)


@router.delete("/session", status_code=204)
async def delete_session(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Audit and clear a Web session after validating its bound CSRF value."""
    principal, claims = await authenticate_web_session(session, request, settings)
    require_web_csrf(request, claims)
    session.add(
        _session_audit(
            principal.tenant_id,
            principal.key_id,
            request.state.request_id,
            "web.session.delete",
            "success",
            principal.app_id,
            principal.environment,
            claims.expires_at,
        )
    )
    await session.commit()
    response.delete_cookie(WEB_SESSION_COOKIE, path="/", samesite="strict")
    response.delete_cookie(WEB_CSRF_COOKIE, path="/", samesite="strict")
    _no_store(response)


@ui_router.get("/assets/{asset_path:path}")
async def get_web_asset(
    asset_path: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Serve only files below the Vite asset root with immutable cache semantics."""
    asset_root = (settings.web_dist_path / "assets").resolve()
    candidate = (asset_root / asset_path).resolve()
    if not _is_file_below(candidate, asset_root):
        return PlainTextResponse("Not found", status_code=404, headers=WEB_SECURITY_HEADERS)
    return FileResponse(
        candidate,
        headers={
            **WEB_SECURITY_HEADERS,
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@ui_router.get("/")
@ui_router.get("/app")
async def get_web_console(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Serve the no-store Web shell without masking unknown API paths."""
    index = (settings.web_dist_path / "index.html").resolve()
    if not index.is_file():
        return PlainTextResponse(
            "AndroidAPM Web console is not built. Run pnpm --dir web build.",
            status_code=503,
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "no-store"},
        )
    return FileResponse(
        index,
        media_type="text/html",
        headers={**WEB_SECURITY_HEADERS, "Cache-Control": "no-store"},
    )


@ui_router.get("/favicon.svg")
async def get_web_favicon(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """Serve the explicit Web console icon without opening a generic root asset route."""
    dist_root = settings.web_dist_path.resolve()
    favicon = (dist_root / "favicon.svg").resolve()
    if not _is_file_below(favicon, dist_root):
        return PlainTextResponse("Not found", status_code=404, headers=WEB_SECURITY_HEADERS)
    return FileResponse(
        favicon,
        media_type="image/svg+xml",
        headers={
            **WEB_SECURITY_HEADERS,
            "Cache-Control": WEB_FAVICON_CACHE_CONTROL,
        },
    )


def _session_response(
    request: Request,
    principal: QueryPrincipal,
    expires_at: int,
) -> WebSessionResponse:
    """Build one typed response while keeping the Query principal implementation private."""
    return WebSessionResponse(
        request_id=request.state.request_id,
        scope=scope_for(principal),
        role=principal.role,
        expires_at_ms=expires_at * 1_000,
    )


def _set_session_cookies(
    response: Response,
    token: str,
    csrf_token: str,
    expires_at: int,
    settings: Settings,
) -> None:
    """Set host-only strict cookies; only the CSRF companion is readable by JavaScript."""
    expires = datetime.fromtimestamp(expires_at, tz=UTC)
    max_age = max(0, expires_at - int(datetime.now(UTC).timestamp()))
    response.set_cookie(
        WEB_SESSION_COOKIE,
        token,
        max_age=max_age,
        expires=expires,
        path="/",
        secure=settings.web_session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        WEB_CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        expires=expires,
        path="/",
        secure=settings.web_session_cookie_secure,
        httponly=False,
        samesite="strict",
    )


def _session_audit(
    tenant_id: str,
    key_id: str,
    request_id: str,
    action: str,
    result: str,
    app_id: str,
    environment: str,
    expires_at: int,
) -> AuditLog:
    """Record session lifecycle metadata without the cookie, CSRF token, or Query secret."""
    return AuditLog(
        tenant_id=tenant_id,
        actor=f"query-key:{key_id}",
        action=action,
        object_type="web_session",
        object_id=None,
        result=result,
        request_id=request_id,
        details_json={
            "app_id": app_id,
            "environment": environment,
            "expires_at": expires_at,
        },
    )


def _is_file_below(candidate: Path, root: Path) -> bool:
    """Reject path traversal and directories without relying on string prefixes."""
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate.is_file()


def _no_store(response: Response) -> None:
    """Prevent browser or intermediary caching of scoped identity state."""
    response.headers["Cache-Control"] = "private, no-store"
