"""Tenant-scoped, domain-separated installation pseudonymization."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

from androidapm_server.constants import INSTALLATION_HMAC_DOMAIN

_MIN_KEY_BYTES = 32
_MAX_KEY_VERSIONS = 16
SESSION_HMAC_DOMAIN = b"androidapm.session.v1"


@dataclass(frozen=True, slots=True)
class InstallationHmacKeyRing:
    """Versioned HMAC key ring supporting safe replay across active-key rotation."""

    keys: dict[str, bytes]
    active_version: str

    @classmethod
    def parse(cls, keys_json: str, active_version: str) -> InstallationHmacKeyRing:
        """Parse a bounded JSON mapping of key version to standard Base64 secret."""
        try:
            document = json.loads(keys_json)
        except json.JSONDecodeError as error:
            raise ValueError("installation HMAC key ring must be valid JSON") from error
        if not isinstance(document, dict) or not document or len(document) > _MAX_KEY_VERSIONS:
            raise ValueError("installation HMAC key ring must contain 1-16 versions")
        keys: dict[str, bytes] = {}
        for version, encoded in document.items():
            if (
                not isinstance(version, str)
                or not version
                or len(version) > 32
                or not isinstance(encoded, str)
            ):
                raise ValueError("installation HMAC key ring entry is invalid")
            try:
                secret = base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise ValueError("installation HMAC key must use standard Base64") from error
            if len(secret) < _MIN_KEY_BYTES:
                raise ValueError("installation HMAC keys must contain at least 32 bytes")
            keys[version] = secret
        if active_version not in keys:
            raise ValueError("active installation HMAC key version is not configured")
        return cls(keys=keys, active_version=active_version)

    def digest(self, tenant_id: str, installation_id: str, version: str | None = None) -> str:
        """Return a tenant-isolated HMAC without retaining the plaintext input."""
        return self._digest(INSTALLATION_HMAC_DOMAIN, tenant_id, installation_id, version)

    def session_digest(self, tenant_id: str, session_id: str, version: str | None = None) -> str:
        """Separate anonymous runtime sessions from installation identities cryptographically."""
        return self._digest(SESSION_HMAC_DOMAIN, tenant_id, session_id, version)

    def _digest(self, domain: bytes, tenant_id: str, identity: str, version: str | None) -> str:
        selected_version = version or self.active_version
        key = self.keys.get(selected_version)
        if key is None:
            raise ValueError("installation HMAC key version is unavailable")
        message = b"\x00".join(
            (
                domain,
                tenant_id.encode("utf-8"),
                identity.encode("utf-8"),
            )
        )
        return hmac.new(key, message, hashlib.sha256).hexdigest()
