"""Short-lived same-origin Web sessions backed by revocable Query credentials."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.config import Settings
from androidapm_server.errors import ApiError
from androidapm_server.query_auth import (
    QueryPrincipal,
    authenticate_query_key,
    authenticate_query_key_id,
)

WEB_SESSION_COOKIE = "androidapm_session"
WEB_CSRF_COOKIE = "androidapm_csrf"
WEB_CSRF_HEADER = "x-apm-csrf-token"
WEB_SESSION_VERSION = 1
WEB_SESSION_SIGNATURE_BYTES = 32
WEB_SESSION_MAX_PAYLOAD_BYTES = 1_024
WEB_SESSION_CLOCK_SKEW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class WebSessionClaims:
    """Integrity-protected session claims containing no raw credential or tenant data."""

    key_id: str
    issued_at: int
    expires_at: int
    csrf_sha256: str


@dataclass(frozen=True, slots=True)
class NewWebSession:
    """One signed cookie plus its independently transported CSRF value."""

    token: str
    csrf_token: str
    expires_at: int


def create_web_session(principal: QueryPrincipal, settings: Settings) -> NewWebSession:
    """Issue a bounded signed session that references, but never embeds, an ``apmq1`` secret."""
    now = int(datetime.now(UTC).timestamp())
    expires_at = now + settings.web_session_ttl_seconds
    if principal.expires_at is not None:
        expires_at = min(expires_at, int(principal.expires_at.timestamp()))
    if expires_at <= now:
        raise _invalid_web_session()

    csrf_token = secrets.token_urlsafe(32).rstrip("=")
    payload = {
        "v": WEB_SESSION_VERSION,
        "kid": principal.key_id,
        "iat": now,
        "exp": expires_at,
        "csrf": hashlib.sha256(csrf_token.encode("ascii")).hexdigest(),
    }
    encoded_payload = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    key = _session_key(settings)
    signature = hmac.new(key, encoded_payload, hashlib.sha256).digest()
    return NewWebSession(
        token=f"{_b64url(encoded_payload)}.{_b64url(signature)}",
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


async def authenticate_query_request(
    session: AsyncSession,
    request: Request,
    settings: Settings,
    *,
    unsafe: bool = False,
) -> QueryPrincipal:
    """Accept explicit Bearer auth or a same-origin cookie, with CSRF on cookie writes."""
    authorization = request.headers.get("authorization")
    if authorization is not None:
        return await authenticate_query_key(session, authorization)
    principal, claims = await authenticate_web_session(session, request, settings)
    if unsafe:
        _verify_csrf(request, claims)
    return principal


async def authenticate_web_session(
    session: AsyncSession,
    request: Request,
    settings: Settings,
) -> tuple[QueryPrincipal, WebSessionClaims]:
    """Verify a browser cookie and re-check the referenced Query key on every request."""
    token = request.cookies.get(WEB_SESSION_COOKIE)
    claims = decode_web_session(token, settings)
    principal = await authenticate_query_key_id(session, claims.key_id)
    return principal, claims


def require_web_csrf(request: Request, claims: WebSessionClaims) -> None:
    """Require the bound double-submit CSRF token for a Web-session state change."""
    _verify_csrf(request, claims)


def decode_web_session(token: str | None, settings: Settings) -> WebSessionClaims:
    """Validate signature, shape, clock bounds, and deployment TTL for one session cookie."""
    if token is None:
        raise _invalid_web_session()
    try:
        encoded_payload, encoded_signature = token.split(".", maxsplit=1)
        payload = _b64url_decode(encoded_payload)
        signature = _b64url_decode(encoded_signature)
        if len(payload) > WEB_SESSION_MAX_PAYLOAD_BYTES:
            raise ValueError("session payload is oversized")
        if len(signature) != WEB_SESSION_SIGNATURE_BYTES:
            raise ValueError("session signature has an invalid length")
        expected = hmac.new(_session_key(settings), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("session signature mismatch")
        value = json.loads(payload)
        claims = _parse_claims(value)
        now = int(datetime.now(UTC).timestamp())
        if (
            claims.issued_at > now + WEB_SESSION_CLOCK_SKEW_SECONDS
            or claims.expires_at <= now
            or claims.expires_at <= claims.issued_at
            or claims.expires_at - claims.issued_at > settings.web_session_ttl_seconds
        ):
            raise ValueError("session clock bounds are invalid")
    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ) as error:
        raise _invalid_web_session() from error
    return claims


def _parse_claims(value: Any) -> WebSessionClaims:
    """Parse only the fixed session claim schema."""
    if not isinstance(value, dict) or set(value) != {"v", "kid", "iat", "exp", "csrf"}:
        raise ValueError("session claims have an invalid shape")
    version = value["v"]
    key_id = value["kid"]
    issued_at = value["iat"]
    expires_at = value["exp"]
    csrf_sha256 = value["csrf"]
    if (
        version != WEB_SESSION_VERSION
        or not isinstance(key_id, str)
        or len(key_id) != 18
        or any(character not in "0123456789abcdef" for character in key_id)
        or not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(csrf_sha256, str)
        or len(csrf_sha256) != 64
        or any(character not in "0123456789abcdef" for character in csrf_sha256)
    ):
        raise ValueError("session claims are invalid")
    return WebSessionClaims(
        key_id=key_id,
        issued_at=issued_at,
        expires_at=expires_at,
        csrf_sha256=csrf_sha256,
    )


def _verify_csrf(request: Request, claims: WebSessionClaims) -> None:
    """Compare cookie, header, and the digest bound into the signed session."""
    cookie = request.cookies.get(WEB_CSRF_COOKIE)
    header = request.headers.get(WEB_CSRF_HEADER)
    if (
        cookie is None
        or header is None
        or len(cookie) > 256
        or len(header) > 256
        or not hmac.compare_digest(cookie, header)
    ):
        raise _invalid_csrf()
    digest = hashlib.sha256(header.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(digest, claims.csrf_sha256):
        raise _invalid_csrf()


def _session_key(settings: Settings) -> bytes:
    """Decode the dedicated Web-session key and fail closed when absent or weak."""
    try:
        key = base64.b64decode(
            settings.web_session_hmac_key_b64.get_secret_value(),
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise _web_session_unavailable() from error
    if len(key) < 32:
        raise _web_session_unavailable()
    return key


def _invalid_web_session() -> ApiError:
    """Return a non-specific browser-session authentication failure."""
    return ApiError(401, "invalid_web_session", "The Web session is invalid or expired")


def _invalid_csrf() -> ApiError:
    """Reject ambient-cookie writes without revealing session details."""
    return ApiError(403, "csrf_validation_failed", "The Web request could not be verified")


def _web_session_unavailable() -> ApiError:
    """Fail closed when a dedicated session signing key is not configured."""
    return ApiError(
        503,
        "web_session_unavailable",
        "Web session authentication is not configured",
        True,
    )


def _b64url(value: bytes) -> str:
    """Encode an unpadded URL-safe token component."""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    """Decode a bounded unpadded URL-safe token component."""
    if len(value) > 2_048:
        raise ValueError("encoded session component is oversized")
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )
    if _b64url(decoded) != value:
        raise ValueError("session component is not canonical base64url")
    return decoded
