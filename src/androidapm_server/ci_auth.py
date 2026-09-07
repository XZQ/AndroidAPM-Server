"""Independent build-pipeline credentials for privileged artifact operations."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.auth import PASSWORD_HASHER
from androidapm_server.db.models import CiKey, Tenant
from androidapm_server.errors import ApiError

CI_KEY_PREFIX = "apmci1"
CI_KEY_SEPARATOR = "_"
CI_KEY_ID_BYTES = 9
CI_KEY_SECRET_BYTES = 32
CI_DUMMY_HASH = PASSWORD_HASHER.hash("dummy-ci-credential-secret")


@dataclass(frozen=True, slots=True)
class CiPrincipal:
    """Authenticated CI identity and its tenant/app authorization boundary."""

    tenant_id: str
    key_id: str
    app_id: str | None
    scopes: frozenset[str]


def generate_ci_key() -> tuple[str, str, str]:
    """Return a one-time CI key and the Argon2id hash retained by the server."""
    key_id = secrets.token_hex(CI_KEY_ID_BYTES)
    secret = secrets.token_urlsafe(CI_KEY_SECRET_BYTES).rstrip("=")
    plaintext = CI_KEY_SEPARATOR.join((CI_KEY_PREFIX, key_id, secret))
    return key_id, plaintext, PASSWORD_HASHER.hash(secret)


async def authenticate_ci_key(
    session: AsyncSession,
    authorization: str | None,
    required_scope: str,
    app_id: str,
) -> CiPrincipal:
    """Authenticate a CI key, then enforce tenant status, scope, and optional app scope."""
    key_id, secret = _parse_ci_bearer(authorization)
    result = await session.execute(
        select(CiKey, Tenant)
        .join(Tenant, Tenant.id == CiKey.tenant_id)
        .where(CiKey.key_id == key_id)
    )
    row = result.one_or_none()
    if row is None:
        _verify_dummy(secret)
        raise _invalid_ci_credential()
    ci_key, tenant = row
    try:
        PASSWORD_HASHER.verify(ci_key.key_hash, secret)
    except (InvalidHashError, VerificationError, VerifyMismatchError) as error:
        raise _invalid_ci_credential() from error

    expires_at = ci_key.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if (
        not ci_key.active
        or ci_key.revoked_at is not None
        or not tenant.active
        or (expires_at is not None and expires_at <= datetime.now(UTC))
    ):
        raise _invalid_ci_credential()
    scopes = frozenset(ci_key.scopes_json)
    if required_scope not in scopes:
        raise ApiError(403, "scope_mismatch", "The CI credential lacks the required scope")
    if ci_key.app_id is not None and ci_key.app_id != app_id:
        raise ApiError(403, "scope_mismatch", "The CI credential does not allow this app")
    return CiPrincipal(tenant.id, ci_key.key_id, ci_key.app_id, scopes)


def _parse_ci_bearer(authorization: str | None) -> tuple[str, str]:
    """Parse the versioned CI Bearer key without accepting ingest-key prefixes."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise _invalid_ci_credential()
    parts = authorization.removeprefix("Bearer ").split(CI_KEY_SEPARATOR, maxsplit=2)
    if len(parts) != 3 or parts[0] != CI_KEY_PREFIX or not parts[1] or not parts[2]:
        raise _invalid_ci_credential()
    return parts[1], parts[2]


def _verify_dummy(secret: str) -> None:
    """Narrow timing differences when an unknown CI key id is supplied."""
    try:
        PASSWORD_HASHER.verify(CI_DUMMY_HASH, secret)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        pass


def _invalid_ci_credential() -> ApiError:
    """Return the deliberately non-specific privileged authentication failure."""
    return ApiError(401, "invalid_credential", "The CI credential is invalid")
