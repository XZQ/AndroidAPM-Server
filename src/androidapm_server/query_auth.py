"""Independent credentials for tenant-scoped Query/BFF access."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.auth import PASSWORD_HASHER
from androidapm_server.auth_budget import AUTH_ATTEMPTS, HASH_VERIFIER
from androidapm_server.constants import QUERY_ROLE_INVESTIGATOR, QUERY_ROLES
from androidapm_server.db.models import QueryKey, Tenant
from androidapm_server.errors import ApiError

QUERY_KEY_PREFIX = "apmq1"
QUERY_KEY_SEPARATOR = "_"
QUERY_KEY_ID_BYTES = 9
QUERY_KEY_SECRET_BYTES = 32
QUERY_DUMMY_HASH = PASSWORD_HASHER.hash("dummy-query-credential-secret")


@dataclass(frozen=True, slots=True)
class QueryPrincipal:
    """Authenticated read identity with an immutable tenant/app/environment scope."""

    tenant_id: str
    key_id: str
    app_id: str
    environment: str
    role: str
    expires_at: datetime | None


def generate_query_key() -> tuple[str, str, str]:
    """Return a one-time Query key and the Argon2id hash retained by the server."""
    key_id = secrets.token_hex(QUERY_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(QUERY_KEY_SECRET_BYTES).rstrip("=")
    plaintext = QUERY_KEY_SEPARATOR.join((QUERY_KEY_PREFIX, key_id, secret))
    return key_id, plaintext, PASSWORD_HASHER.hash(secret)


async def authenticate_query_key(
    session: AsyncSession,
    authorization: str | None,
) -> QueryPrincipal:
    """Verify a Query key and derive every data-scope dimension from storage."""
    AUTH_ATTEMPTS.consume()
    key_id, secret = _parse_query_bearer(authorization)
    row = await _load_query_key(session, key_id)
    if row is None:
        await _verify_dummy(secret)
        raise _invalid_query_credential()
    query_key, tenant = row
    try:
        await HASH_VERIFIER.verify(PASSWORD_HASHER.verify, query_key.key_hash, secret)
    except (InvalidHashError, VerificationError, VerifyMismatchError) as error:
        raise _invalid_query_credential() from error

    return _active_principal(query_key, tenant)


async def authenticate_query_key_id(session: AsyncSession, key_id: str) -> QueryPrincipal:
    """Revalidate a signed Web session against the current Query key state."""
    row = await _load_query_key(session, key_id)
    if row is None:
        raise _invalid_query_credential()
    query_key, tenant = row
    return _active_principal(query_key, tenant)


async def _load_query_key(
    session: AsyncSession,
    key_id: str,
) -> tuple[QueryKey, Tenant] | None:
    """Load one Query key and its tenant without accepting a caller-provided scope."""
    result = await session.execute(
        select(QueryKey, Tenant)
        .join(Tenant, Tenant.id == QueryKey.tenant_id)
        .where(QueryKey.key_id == key_id)
    )
    row = result.one_or_none()
    return (row[0], row[1]) if row is not None else None


def _active_principal(query_key: QueryKey, tenant: Tenant) -> QueryPrincipal:
    """Reject revoked, expired, disabled, or structurally invalid Query identities."""

    expires_at = query_key.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        not query_key.active
        or query_key.revoked_at is not None
        or not tenant.active
        or (expires_at is not None and expires_at <= datetime.now(UTC))
        or query_key.role not in QUERY_ROLES
    ):
        raise _invalid_query_credential()
    return QueryPrincipal(
        tenant_id=tenant.id,
        key_id=query_key.key_id,
        app_id=query_key.app_id,
        environment=query_key.environment,
        role=query_key.role,
        expires_at=expires_at,
    )


def require_investigator(principal: QueryPrincipal) -> None:
    """Reject L1/L2 or decision writes from aggregate-only viewers."""
    if principal.role != QUERY_ROLE_INVESTIGATOR:
        raise ApiError(
            403,
            "insufficient_query_role",
            "The query credential does not allow event-level access",
        )


def _parse_query_bearer(authorization: str | None) -> tuple[str, str]:
    """Parse a versioned Query Bearer key without accepting other key audiences."""
    if authorization is None or len(authorization) > 256 or not authorization.startswith("Bearer "):
        raise _invalid_query_credential()
    parts = authorization.removeprefix("Bearer ").split(QUERY_KEY_SEPARATOR, maxsplit=2)
    if len(parts) != 3 or parts[0] != QUERY_KEY_PREFIX or not parts[1] or not parts[2]:
        raise _invalid_query_credential()
    return parts[1], parts[2]


async def _verify_dummy(secret: str) -> None:
    """Narrow timing differences when an unknown Query key id is supplied."""
    try:
        await HASH_VERIFIER.verify(PASSWORD_HASHER.verify, QUERY_DUMMY_HASH, secret)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        pass


def _invalid_query_credential() -> ApiError:
    """Return a deliberately non-specific Query authentication failure."""
    return ApiError(401, "invalid_credential", "The query credential is invalid")
