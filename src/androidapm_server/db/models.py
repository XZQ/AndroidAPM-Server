"""Persistent control-plane and durable-inbox models."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from androidapm_server.constants import INBOX_STATUS_PENDING
from androidapm_server.db.base import BIGINT_PRIMARY_KEY, Base


def utc_now() -> datetime:
    """Return an aware UTC timestamp for application-side defaults."""
    return datetime.now(UTC)


class Tenant(Base):
    """Top-level isolation boundary for credentials and telemetry."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    config_rollout_salt: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: secrets.token_hex(32)
    )

    ingest_keys: Mapped[list[IngestKey]] = relationship(back_populates="tenant")


class IngestKey(Base):
    """Hashed ingest credential scoped to one app and environment."""

    __tablename__ = "ingest_keys"

    key_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    app_id: Mapped[str] = mapped_column(String(256), nullable=False)
    environment: Mapped[str] = mapped_column(String(128), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(String(512))
    requests_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    events_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=30_000)

    tenant: Mapped[Tenant] = relationship(back_populates="ingest_keys")


class InboxEvent(Base):
    """One durably acknowledged Android event awaiting downstream export."""

    __tablename__ = "inbox_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_inbox_tenant_event"),
        Index("ix_inbox_claim", "status", "next_attempt_at", "lease_expires_at"),
        Index("ix_inbox_tenant_received", "tenant_id", "received_at"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    app_id: Mapped[str] = mapped_column(String(256), nullable=False)
    environment: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    sdk_version: Mapped[str] = mapped_column(String(64), nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(128))
    app_build: Mapped[str | None] = mapped_column(String(128))
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    event_timestamp_ms: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=INBOX_STATUS_PENDING)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(Text)


class IngestRateWindow(Base):
    """Distributed fixed-window quota counters for one ingest credential."""

    __tablename__ = "ingest_rate_windows"

    key_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ingest_keys.key_id", ondelete="CASCADE"), primary_key=True
    )
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RemoteConfigVersion(Base):
    """Immutable signed remote configuration revision for one app environment."""

    __tablename__ = "remote_config_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "app_id",
            "environment",
            "revision",
            name="uq_remote_config_scope_revision",
        ),
        Index(
            "ix_remote_config_active",
            "tenant_id",
            "app_id",
            "environment",
            "active",
            "revision",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    app_id: Mapped[str] = mapped_column(String(256), nullable=False)
    environment: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rollout_basis_points: Mapped[int] = mapped_column(Integer, nullable=False, default=10_000)
    key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    signature_b64: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditLog(Base):
    """Security and control-plane audit record without secret material."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(128), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(256))
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(64))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
