"""Ingest credential generation, hashing, and scope verification."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.auth_budget import AUTH_ATTEMPTS, HASH_VERIFIER
from androidapm_server.db.models import IngestKey, Tenant
from androidapm_server.errors import ApiError

KEY_PREFIX = "apm1"
KEY_SEPARATOR = "_"
KEY_ID_BYTES = 9
KEY_SECRET_BYTES = 32
PASSWORD_HASHER = PasswordHasher()
DUMMY_HASH = PASSWORD_HASHER.hash("dummy-credential-secret")


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated tenant and ingest scope resolved from a stored key."""

    tenant_id: str
    key_id: str
    app_id: str
    environment: str
    requests_per_minute: int
    events_per_minute: int


def generate_ingest_key() -> tuple[str, str, str]:
    """Return ``(key_id, plaintext_key, hash)``; plaintext must be shown once."""
    # Hex keeps the lookup id free of the token separator; the secret may contain underscores.
    key_id = secrets.token_hex(KEY_ID_BYTES)
    secret = secrets.token_urlsafe(KEY_SECRET_BYTES).rstrip("=")
    plaintext = KEY_SEPARATOR.join((KEY_PREFIX, key_id, secret))
    return key_id, plaintext, PASSWORD_HASHER.hash(secret)


def parse_bearer_token(authorization: str | None) -> tuple[str, str]:
    """Parse a strict Bearer credential and return its lookup id and secret."""
    if authorization is None or len(authorization) > 256 or not authorization.startswith("Bearer "):
        raise _invalid_credential()
    token = authorization.removeprefix("Bearer ")
    parts = token.split(KEY_SEPARATOR, maxsplit=2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX or not parts[1] or not parts[2]:
        raise _invalid_credential()
    return parts[1], parts[2]


async def authenticate_ingest_key(
    session: AsyncSession,
    authorization: str | None,
    app_id: str,
    environment: str,
) -> Principal:
    """Verify a stored ingest key and enforce its app/environment scope."""
    AUTH_ATTEMPTS.consume()
    key_id, secret = parse_bearer_token(authorization)
    result = await session.execute(
        select(IngestKey, Tenant)
        .join(Tenant, Tenant.id == IngestKey.tenant_id)
        .where(IngestKey.key_id == key_id)
    )
    row = result.one_or_none()
    if row is None:
        # A dummy verification narrows observable timing differences for unknown key ids.
        await _verify_dummy_secret(secret)
        raise _invalid_credential()
    ingest_key, tenant = row
    try:
        await HASH_VERIFIER.verify(PASSWORD_HASHER.verify, ingest_key.key_hash, secret)
    except (InvalidHashError, VerificationError, VerifyMismatchError) as error:
        raise _invalid_credential() from error

    now = datetime.now(UTC)
    expires_at = ingest_key.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        not ingest_key.active
        or ingest_key.revoked_at is not None
        or not tenant.active
        or (expires_at is not None and expires_at <= now)
    ):
        raise _invalid_credential()
    if ingest_key.app_id != app_id or ingest_key.environment != environment:
        raise ApiError(
            403,
            "scope_mismatch",
            "The credential does not allow this app or environment",
        )
    return Principal(
        tenant.id,
        ingest_key.key_id,
        ingest_key.app_id,
        ingest_key.environment,
        ingest_key.requests_per_minute,
        ingest_key.events_per_minute,
    )


async def _verify_dummy_secret(secret: str) -> None:
    """Spend approximately one password verification for an unknown key id."""
    try:
        await HASH_VERIFIER.verify(PASSWORD_HASHER.verify, DUMMY_HASH, secret)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        pass


def _invalid_credential() -> ApiError:
    """Return a deliberately non-specific authentication failure."""
    return ApiError(401, "invalid_credential", "The ingest credential is invalid")
