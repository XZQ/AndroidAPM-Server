"""Minimal local administration CLI for tenants and ingest credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from androidapm_server.auth import generate_ingest_key
from androidapm_server.config import get_settings
from androidapm_server.db.models import (
    AuditLog,
    IngestKey,
    RemoteConfigVersion,
    Tenant,
)
from androidapm_server.db.session import get_session_factory
from androidapm_server.remote_config import generate_signing_keypair, sign_config


def parser() -> argparse.ArgumentParser:
    """Build the explicit, non-interactive administration argument parser."""
    root = argparse.ArgumentParser(prog="androidapm-admin")
    subcommands = root.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create-ingest-key")
    create.add_argument("--tenant-id", required=True)
    create.add_argument("--tenant-name", required=True)
    create.add_argument("--app-id", required=True)
    create.add_argument("--environment", required=True)
    create.add_argument("--description")
    create.add_argument("--expires-days", type=int)
    create.add_argument("--requests-per-minute", type=int, default=600)
    create.add_argument("--events-per-minute", type=int, default=30_000)
    subcommands.add_parser("generate-config-keypair")
    publish = subcommands.add_parser("publish-config")
    publish.add_argument("--tenant-id", required=True)
    publish.add_argument("--app-id", required=True)
    publish.add_argument("--environment", required=True)
    publish.add_argument("--payload", type=Path, required=True)
    publish.add_argument("--expires-hours", type=int, default=24)
    publish.add_argument("--rollout-basis-points", type=int, default=10_000)
    publish.add_argument("--created-by", required=True)
    return root


async def create_ingest_key(arguments: argparse.Namespace) -> str:
    """Create or reuse a tenant and return a one-time plaintext ingest key."""
    if arguments.expires_days is not None and arguments.expires_days <= 0:
        raise ValueError("--expires-days must be positive")
    if arguments.requests_per_minute <= 0 or arguments.events_per_minute <= 0:
        raise ValueError("quota values must be positive")

    key_id, plaintext, key_hash = generate_ingest_key()
    expires_at = (
        datetime.now(UTC) + timedelta(days=arguments.expires_days)
        if arguments.expires_days is not None
        else None
    )
    factory = get_session_factory()
    async with factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.id == arguments.tenant_id))
        if tenant is None:
            session.add(Tenant(id=arguments.tenant_id, name=arguments.tenant_name))
        elif tenant.name != arguments.tenant_name:
            raise ValueError("tenant id already exists with a different name")
        session.add(
            IngestKey(
                key_id=key_id,
                tenant_id=arguments.tenant_id,
                key_hash=key_hash,
                app_id=arguments.app_id,
                environment=arguments.environment,
                description=arguments.description,
                expires_at=expires_at,
                requests_per_minute=arguments.requests_per_minute,
                events_per_minute=arguments.events_per_minute,
            )
        )
        session.add(
            AuditLog(
                tenant_id=arguments.tenant_id,
                actor="local-admin-cli",
                action="ingest_key.create",
                object_type="ingest_key",
                object_id=key_id,
                result="success",
                details_json={
                    "app_id": arguments.app_id,
                    "environment": arguments.environment,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
            )
        )
        await session.commit()
    return plaintext


async def publish_config(arguments: argparse.Namespace) -> int:
    """Publish the next immutable signed configuration revision."""
    settings = get_settings()
    secret = settings.remote_config_signing_private_key_b64
    if secret is None or not secret.get_secret_value().strip():
        raise ValueError("APM_REMOTE_CONFIG_SIGNING_PRIVATE_KEY_B64 is required")
    if arguments.expires_hours <= 0:
        raise ValueError("--expires-hours must be positive")
    payload = json.loads(arguments.payload.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("configuration payload must be a JSON object")

    factory = get_session_factory()
    async with factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.id == arguments.tenant_id))
        if tenant is None or not tenant.active:
            raise ValueError("tenant does not exist or is inactive")
        current_revision = await session.scalar(
            select(func.max(RemoteConfigVersion.revision)).where(
                RemoteConfigVersion.tenant_id == arguments.tenant_id,
                RemoteConfigVersion.app_id == arguments.app_id,
                RemoteConfigVersion.environment == arguments.environment,
            )
        )
        revision = (current_revision or 0) + 1
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(hours=arguments.expires_hours)
        signed = sign_config(
            secret.get_secret_value(),
            revision,
            issued_at,
            expires_at,
            arguments.rollout_basis_points,
            payload,
            settings.remote_config_public_key_id,
        )
        session.add(
            RemoteConfigVersion(
                tenant_id=arguments.tenant_id,
                app_id=arguments.app_id,
                environment=arguments.environment,
                revision=revision,
                payload_json=payload,
                issued_at=issued_at,
                expires_at=expires_at,
                rollout_basis_points=arguments.rollout_basis_points,
                key_id=settings.remote_config_public_key_id,
                signature_b64=signed.signature_b64,
                created_by=arguments.created_by,
            )
        )
        session.add(
            AuditLog(
                tenant_id=arguments.tenant_id,
                actor=arguments.created_by,
                action="remote_config.publish",
                object_type="remote_config",
                object_id=f"{arguments.app_id}:{arguments.environment}:{revision}",
                result="success",
                details_json={
                    "revision": revision,
                    "rollout_basis_points": arguments.rollout_basis_points,
                    "expires_at": expires_at.isoformat(),
                },
            )
        )
        await session.commit()
    return revision


def run() -> None:
    """Execute a requested administration action and print secrets once."""
    arguments = parser().parse_args()
    if arguments.command == "create-ingest-key":
        plaintext = asyncio.run(create_ingest_key(arguments))
        print("Ingest key (shown once):")
        print(plaintext)
        return
    if arguments.command == "generate-config-keypair":
        private_key, public_key = generate_signing_keypair()
        print("Private key (store in a secret manager; shown once):")
        print(private_key)
        print("Public key (pin in the Android client):")
        print(public_key)
        return
    if arguments.command == "publish-config":
        revision = asyncio.run(publish_config(arguments))
        print(f"Published remote config revision: {revision}")
        return
    raise AssertionError(f"Unhandled command: {arguments.command}")


if __name__ == "__main__":
    run()
