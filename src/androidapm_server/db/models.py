"""Persistent control-plane and durable-inbox models."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
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

from androidapm_server.constants import (
    ARTIFACT_STATUS_ACTIVE,
    IDENTITY_QUALITY_ABSENT,
    IDENTITY_QUALITY_AUTHENTICATED,
    INBOX_STATUS_PENDING,
    NORMALIZATION_VERSION,
    SYMBOL_STATUS_PENDING,
    TIMESTAMP_QUALITY_EVENT_DECLARED,
)
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
    ci_keys: Mapped[list[CiKey]] = relationship(back_populates="tenant")
    symbol_artifacts: Mapped[list[SymbolArtifact]] = relationship(back_populates="tenant")
    query_keys: Mapped[list[QueryKey]] = relationship(back_populates="tenant")


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


class CiKey(Base):
    """Hashed non-runtime credential used by trusted build pipelines."""

    __tablename__ = "ci_keys"

    key_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    app_id: Mapped[str | None] = mapped_column(String(256))
    scopes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(String(512))

    tenant: Mapped[Tenant] = relationship(back_populates="ci_keys")


class QueryKey(Base):
    """Hashed read credential with fixed tenant/app/environment and L0-L2 role scope."""

    __tablename__ = "query_keys"

    key_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    app_id: Mapped[str] = mapped_column(String(256), nullable=False)
    environment: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    description: Mapped[str | None] = mapped_column(String(512))

    tenant: Mapped[Tenant] = relationship(back_populates="query_keys")


class InboxEvent(Base):
    """One durably acknowledged Android event awaiting downstream export."""

    __tablename__ = "inbox_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_inbox_tenant_event"),
        Index("ix_inbox_claim", "status", "next_attempt_at", "lease_expires_at"),
        Index("ix_inbox_tenant_received", "tenant_id", "received_at"),
        Index(
            "ix_inbox_query_scope_time",
            "tenant_id",
            "app_id",
            "environment",
            "occurrence_timestamp_ms",
        ),
        Index(
            "ix_inbox_query_release",
            "tenant_id",
            "app_id",
            "environment",
            "app_version",
            "release_identity_quality",
        ),
        Index("ix_inbox_incident_fingerprint", "tenant_id", "incident_fingerprint"),
        Index(
            "ix_inbox_release_time",
            "tenant_id",
            "app_id",
            "environment",
            "app_version",
            "occurrence_timestamp_ms",
            "id",
        ),
        Index(
            "ix_inbox_fingerprint_time",
            "tenant_id",
            "app_id",
            "environment",
            "incident_fingerprint",
            "occurrence_timestamp_ms",
            "id",
        ),
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
    version_code: Mapped[str | None] = mapped_column(String(64))
    variant: Mapped[str | None] = mapped_column(String(128))
    protocol: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_identity_quality: Mapped[str] = mapped_column(
        String(32), nullable=False, default=IDENTITY_QUALITY_AUTHENTICATED
    )
    release_identity_quality: Mapped[str] = mapped_column(
        String(32), nullable=False, default=IDENTITY_QUALITY_ABSENT
    )
    installation_identity_quality: Mapped[str] = mapped_column(
        String(32), nullable=False, default=IDENTITY_QUALITY_ABSENT
    )
    installation_hmac: Mapped[str | None] = mapped_column(String(64))
    installation_hmac_key_version: Mapped[str | None] = mapped_column(String(32))
    event_timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurrence_timestamp_ms: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, nullable=False)
    collection_timestamp_ms: Mapped[int | None] = mapped_column(BIGINT_PRIMARY_KEY)
    timestamp_quality: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TIMESTAMP_QUALITY_EVENT_DECLARED
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    normalization_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=NORMALIZATION_VERSION
    )
    normalized_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    incident_fingerprint: Mapped[str | None] = mapped_column(String(64))
    native_identity_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
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
    symbolization_job: Mapped[SymbolizationJob | None] = relationship(
        back_populates="inbox_event", uselist=False, lazy="selectin"
    )


class SymbolArtifact(Base):
    """Validated immutable Java mapping or Native ELF artifact."""

    __tablename__ = "symbol_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "artifact_type",
            "app_id",
            "version_code",
            "app_build",
            "variant",
            "abi",
            "build_id",
            name="uq_symbol_artifact_identity",
        ),
        Index("ix_symbol_artifact_lookup", "tenant_id", "artifact_type", "app_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(String(32), nullable=False)
    app_id: Mapped[str] = mapped_column(String(256), nullable=False)
    version_code: Mapped[str] = mapped_column(String(64), nullable=False)
    app_build: Mapped[str] = mapped_column(String(128), nullable=False)
    variant: Mapped[str] = mapped_column(String(128), nullable=False)
    abi: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    build_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ARTIFACT_STATUS_ACTIVE)
    uploaded_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    tenant: Mapped[Tenant] = relationship(back_populates="symbol_artifacts")
    jobs: Mapped[list[SymbolizationJob]] = relationship(back_populates="artifact")


class SymbolizationJob(Base):
    """Durable owner-leased symbolization state for one crash inbox event."""

    __tablename__ = "symbolization_jobs"
    __table_args__ = (
        Index("ix_symbol_job_claim", "status", "next_attempt_at", "lease_expires_at"),
        Index("ix_symbol_job_tenant_event", "tenant_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)
    inbox_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("inbox_events.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=SYMBOL_STATUS_PENDING)
    artifact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("symbol_artifacts.id", ondelete="SET NULL")
    )
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fingerprint_sha256: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(128))
    tool_version: Mapped[str | None] = mapped_column(String(128))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    inbox_event: Mapped[InboxEvent] = relationship(back_populates="symbolization_job")
    artifact: Mapped[SymbolArtifact | None] = relationship(back_populates="jobs")


class IngestRateWindow(Base):
    """Distributed fixed-window quota counters for one ingest credential."""

    __tablename__ = "ingest_rate_windows"

    key_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ingest_keys.key_id", ondelete="CASCADE"), primary_key=True
    )
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
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


class ReleaseDecision(Base):
    """Human release decision tied to a bounded evidence window and rationale."""

    __tablename__ = "release_decisions"
    __table_args__ = (
        Index(
            "ix_release_decision_scope_created", "tenant_id", "app_id", "environment", "created_at"
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    app_id: Mapped[str] = mapped_column(String(256), nullable=False)
    environment: Mapped[str] = mapped_column(String(128), nullable=False)
    release_version: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_from_ms: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, nullable=False)
    evidence_to_ms: Mapped[int] = mapped_column(BIGINT_PRIMARY_KEY, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
