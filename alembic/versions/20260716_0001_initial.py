"""Create tenants, ingest keys, durable inbox, and audit log.

Revision ID: 20260716_0001
Revises:
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260716_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT_PRIMARY_KEY = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    """Create the initial production schema."""
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_rollout_salt", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_table(
        "ingest_keys",
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=512), nullable=False),
        sa.Column("app_id", sa.String(length=256), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("requests_per_minute", sa.Integer(), nullable=False),
        sa.Column("events_per_minute", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_ingest_keys_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("key_id", name=op.f("pk_ingest_keys")),
    )
    op.create_index(op.f("ix_ingest_keys_tenant_id"), "ingest_keys", ["tenant_id"])
    op.create_table(
        "ingest_rate_windows",
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["key_id"],
            ["ingest_keys.key_id"],
            name=op.f("fk_ingest_rate_windows_key_id_ingest_keys"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "key_id", "window_started_at", name=op.f("pk_ingest_rate_windows")
        ),
    )
    op.create_table(
        "inbox_events",
        sa.Column("id", BIGINT_PRIMARY_KEY, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("app_id", sa.String(length=256), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("sdk_version", sa.String(length=64), nullable=False),
        sa.Column("app_version", sa.String(length=128), nullable=True),
        sa.Column("app_build", sa.String(length=128), nullable=True),
        sa.Column("protocol", sa.String(length=64), nullable=False),
        sa.Column("event_timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inbox_events")),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_inbox_tenant_event"),
    )
    op.create_index(
        "ix_inbox_claim", "inbox_events", ["status", "next_attempt_at", "lease_expires_at"]
    )
    op.create_index(
        "ix_inbox_tenant_received", "inbox_events", ["tenant_id", "received_at"]
    )
    op.create_table(
        "remote_config_versions",
        sa.Column("id", BIGINT_PRIMARY_KEY, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("app_id", sa.String(length=256), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rollout_basis_points", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(length=128), nullable=False),
        sa.Column("signature_b64", sa.String(length=256), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_remote_config_versions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_remote_config_versions")),
        sa.UniqueConstraint(
            "tenant_id",
            "app_id",
            "environment",
            "revision",
            name="uq_remote_config_scope_revision",
        ),
    )
    op.create_index(
        "ix_remote_config_active",
        "remote_config_versions",
        ["tenant_id", "app_id", "environment", "active", "revision"],
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", BIGINT_PRIMARY_KEY, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("actor", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("object_type", sa.String(length=128), nullable=False),
        sa.Column("object_id", sa.String(length=256), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("ix_audit_tenant_created", "audit_logs", ["tenant_id", "created_at"])


def downgrade() -> None:
    """Remove the complete initial schema for local rollback testing."""
    op.drop_index("ix_audit_tenant_created", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_remote_config_active", table_name="remote_config_versions")
    op.drop_table("remote_config_versions")
    op.drop_index("ix_inbox_tenant_received", table_name="inbox_events")
    op.drop_index("ix_inbox_claim", table_name="inbox_events")
    op.drop_table("inbox_events")
    op.drop_table("ingest_rate_windows")
    op.drop_index(op.f("ix_ingest_keys_tenant_id"), table_name="ingest_keys")
    op.drop_table("ingest_keys")
    op.drop_table("tenants")
