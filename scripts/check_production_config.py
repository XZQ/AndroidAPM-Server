"""Fail a deployment when production configuration still contains unsafe defaults.

This is deliberately a *pre-deployment* check rather than an application-start
validator: the runtime must keep working in local/test environments that have no
secrets, while an externally reachable deployment must prove every key is real.
Values are never printed; only key names and failure reasons are reported.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
from pathlib import Path

MIN_KEY_BYTES = 32
MIN_DATABASE_PASSWORD_BYTES = 16
MAX_KEY_VERSIONS = 16
PLACEHOLDER_MARKERS = (
    "replace-with",
    "change-me",
    "test-only",
    "example",
    "dummy",
    "your-",
    "localhost",
    "<",
    "${",
)


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a simple KEY=VALUE env file without executing shell syntax."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", maxsplit=1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[name.strip()] = value
    return values


def is_placeholder(value: str) -> bool:
    """Return True for empty, templated, or example-only configuration values."""
    lowered = value.strip().lower()
    return not lowered or lowered in {"{}", "[]", "false", "true"} or any(
        marker in lowered for marker in PLACEHOLDER_MARKERS
    )


def decode_key(value: str) -> bytes | None:
    """Decode a standard Base64 secret, returning None for invalid material."""
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None


def validate(values: dict[str, str]) -> list[str]:
    """Return human-readable failures without echoing any configured value."""
    failures: list[str] = []
    if values.get("APM_ENVIRONMENT", "").strip().lower() != "production":
        failures.append("APM_ENVIRONMENT: must be 'production' for an external deployment")
    if values.get("APM_WEB_SESSION_COOKIE_SECURE", "").strip().lower() != "true":
        failures.append("APM_WEB_SESSION_COOKIE_SECURE: must be 'true'")

    for name in ("APM_WEB_SESSION_HMAC_KEY_B64", "APM_QUERY_CURSOR_HMAC_KEY_B64"):
        raw = values.get(name, "")
        if is_placeholder(raw):
            failures.append(f"{name}: missing or example-only value")
        elif (secret := decode_key(raw)) is None or len(secret) < MIN_KEY_BYTES:
            failures.append(f"{name}: must be standard Base64 with >= {MIN_KEY_BYTES} bytes")

    _validate_key_ring(values, failures)
    _validate_infrastructure(values, failures)
    return failures


def _validate_key_ring(values: dict[str, str], failures: list[str]) -> None:
    """Require a non-empty versioned installation HMAC ring with an active version."""
    raw = values.get("APM_INSTALLATION_HMAC_KEYS_JSON", "")
    if is_placeholder(raw):
        failures.append("APM_INSTALLATION_HMAC_KEYS_JSON: missing or example-only value")
        return
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        failures.append("APM_INSTALLATION_HMAC_KEYS_JSON: must be valid JSON")
        return
    if not isinstance(document, dict) or not document or len(document) > MAX_KEY_VERSIONS:
        failures.append("APM_INSTALLATION_HMAC_KEYS_JSON: must hold 1-16 key versions")
        return
    active = values.get("APM_INSTALLATION_HMAC_ACTIVE_KEY_VERSION", "v1")
    if active not in document:
        failures.append("APM_INSTALLATION_HMAC_ACTIVE_KEY_VERSION: not present in the key ring")
    for version, encoded in document.items():
        if not isinstance(version, str) or not version or not isinstance(encoded, str):
            failures.append("APM_INSTALLATION_HMAC_KEYS_JSON: invalid version entry")
            continue
        if (secret := decode_key(encoded)) is None or len(secret) < MIN_KEY_BYTES:
            failures.append(f"APM_INSTALLATION_HMAC_KEYS_JSON: version '{version}' key is weak")


def _validate_infrastructure(values: dict[str, str], failures: list[str]) -> None:
    """Reject default database passwords, local endpoints and example domains."""
    password = values.get("POSTGRES_PASSWORD", "")
    if is_placeholder(password) or len(password) < MIN_DATABASE_PASSWORD_BYTES:
        failures.append("POSTGRES_PASSWORD: missing, example-only, or shorter than 16 bytes")
    # compose.yaml injects APM_DATABASE_URL from POSTGRES_PASSWORD, so a valid
    # deployment env file normally omits it. Validate only an explicit override.
    database_url = values.get("APM_DATABASE_URL", "")
    if database_url and (
        not database_url.startswith("postgresql") or is_placeholder(database_url)
    ):
        failures.append("APM_DATABASE_URL: override must point at PostgreSQL, not SQLite")
    # APM_DOMAIN is Caddy's site address. A public IP literal is accepted: the
    # existing IP-only deployment has no DNS name, and an IP satisfies this check.
    domain = values.get("APM_DOMAIN", "")
    if is_placeholder(domain) or "." not in domain:
        failures.append("APM_DOMAIN: missing or example-only")
    endpoint = values.get("APM_OTLP_LOGS_ENDPOINT", "")
    if not endpoint.startswith("https://") or is_placeholder(endpoint):
        failures.append("APM_OTLP_LOGS_ENDPOINT: must be an external HTTPS OTLP endpoint")


def main() -> int:
    """Return non-zero when any production configuration check fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Env file to check; defaults to os.environ.")
    args = parser.parse_args()
    if args.env_file is None:
        values = dict(os.environ)
    elif args.env_file.is_file():
        values = parse_env_file(args.env_file)
    else:
        print(f"ERROR: env file not found: {args.env_file}", file=sys.stderr)
        return 2

    failures = validate(values)
    if failures:
        for failure in failures:
            print(f"ERROR: {failure}", file=sys.stderr)
        return 1
    print("Production configuration check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
