"""Ed25519-signed immutable remote configuration publication and rollout."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROLLOUT_DENOMINATOR = 10_000


@dataclass(frozen=True, slots=True)
class SignedConfig:
    """Canonical fields returned to clients and covered by an Ed25519 signature."""

    revision: int
    issued_at: datetime
    expires_at: datetime
    rollout_basis_points: int
    payload: dict[str, Any]
    key_id: str
    signature_b64: str

    def envelope(self) -> dict[str, Any]:
        """Build the stable JSON response envelope."""
        return {
            **_unsigned_envelope(
                self.revision,
                self.issued_at,
                self.expires_at,
                self.rollout_basis_points,
                self.payload,
                self.key_id,
            ),
            "signature": self.signature_b64,
        }


def generate_signing_keypair() -> tuple[str, str]:
    """Generate raw Ed25519 private/public keys as standard Base64 strings."""
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(private_raw).decode(), base64.b64encode(public_raw).decode()


def sign_config(
    private_key_b64: str,
    revision: int,
    issued_at: datetime,
    expires_at: datetime,
    rollout_basis_points: int,
    payload: dict[str, Any],
    key_id: str,
) -> SignedConfig:
    """Sign canonical JSON after validating revision, expiry, and rollout bounds."""
    if revision <= 0:
        raise ValueError("revision must be positive")
    if expires_at <= issued_at:
        raise ValueError("expires_at must be after issued_at")
    if not 0 <= rollout_basis_points <= ROLLOUT_DENOMINATOR:
        raise ValueError("rollout_basis_points must be between 0 and 10000")
    unsigned = _unsigned_envelope(
        revision,
        issued_at,
        expires_at,
        rollout_basis_points,
        payload,
        key_id,
    )
    private_key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    signature = private_key.sign(_canonical_bytes(unsigned))
    return SignedConfig(
        revision,
        issued_at,
        expires_at,
        rollout_basis_points,
        payload,
        key_id,
        base64.b64encode(signature).decode(),
    )


def verify_envelope(public_key_b64: str, envelope: dict[str, Any]) -> None:
    """Verify a response envelope; intended for tests and client reference behavior."""
    signature_b64 = str(envelope["signature"])
    unsigned = {key: value for key, value in envelope.items() if key != "signature"}
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    public_key.verify(base64.b64decode(signature_b64), _canonical_bytes(unsigned))


def rollout_includes(rollout_salt: str, installation_id: str, basis_points: int) -> bool:
    """Apply a stable non-enumerable tenant-scoped 0-9999 rollout bucket."""
    if basis_points <= 0:
        return False
    if basis_points >= ROLLOUT_DENOMINATOR:
        return True
    digest = hmac.new(
        bytes.fromhex(rollout_salt),
        installation_id.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return int.from_bytes(digest[:8], "big") % ROLLOUT_DENOMINATOR < basis_points


def _unsigned_envelope(
    revision: int,
    issued_at: datetime,
    expires_at: datetime,
    rollout_basis_points: int,
    payload: dict[str, Any],
    key_id: str,
) -> dict[str, Any]:
    """Build the exact fields covered by the signature."""
    return {
        "revision": revision,
        "issuedAt": issued_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "expiresAt": expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "rolloutBasisPoints": rollout_basis_points,
        "payload": payload,
        "keyId": key_id,
    }


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    """Serialize signed JSON deterministically without whitespace or ASCII rewriting."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
