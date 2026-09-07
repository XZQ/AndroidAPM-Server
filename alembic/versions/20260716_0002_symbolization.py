"""Add CI credentials, symbol artifacts, and durable symbolization jobs.

Revision ID: 20260716_0002
Revises: 20260716_0001
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260716_0002"
down_revision: str | Sequence[str] | None = "20260716_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT_PRIMARY_KEY = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    """Create isolated CI credentials and the symbolization control plane."""
    op.add_column("inbox_events", sa.Column("version_code", sa.String(length=64), nullable=True))
    op.add_column("inbox_events", sa.Column("variant", sa.String(length=128), nullable=True))
    op.create_table(
        "ci_keys",
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=512), nullable=False),
        sa.Column("app_id", sa.String(length=256), nullable=True),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_ci_keys_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("key_id", name=op.f("pk_ci_keys")),
    )
    op.create_index(op.f("ix_ci_keys_tenant_id"), "ci_keys", ["tenant_id"])
    op.create_table(
        "symbol_artifacts",
        sa.Column("id", BIGINT_PRIMARY_KEY, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("app_id", sa.String(length=256), nullable=False),
        sa.Column("version_code", sa.String(length=64), nullable=False),
        sa.Column("app_build", sa.String(length=128), nullable=False),
        sa.Column("variant", sa.String(length=128), nullable=False),
        sa.Column("abi", sa.String(length=32), nullable=False),
        sa.Column("build_id", sa.String(length=128), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("uploaded_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_symbol_artifacts_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbol_artifacts")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_symbol_artifacts_storage_key")),
        sa.UniqueConstraint(
            "tenant_id", "artifact_type", "app_id", "version_code", "app_build", "variant", "abi", "build_id",
            name="uq_symbol_artifact_identity",
        ),
    )
    op.create_index(
        "ix_symbol_artifact_lookup", "symbol_artifacts", ["tenant_id", "artifact_type", "app_id"]
    )
    op.create_table(
        "symbolization_jobs",
        sa.Column("id", BIGINT_PRIMARY_KEY, autoincrement=True, nullable=False),
        sa.Column("inbox_event_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("artifact_id", sa.BigInteger(), nullable=True),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("fingerprint_sha256", sa.String(length=64), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("tool_version", sa.String(length=128), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["symbol_artifacts.id"], name=op.f("fk_symbolization_jobs_artifact_id_symbol_artifacts"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["inbox_event_id"], ["inbox_events.id"], name=op.f("fk_symbolization_jobs_inbox_event_id_inbox_events"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_symbolization_jobs")),
        sa.UniqueConstraint("inbox_event_id", name=op.f("uq_symbolization_jobs_inbox_event_id")),
    )
    op.create_index(
        "ix_symbol_job_claim", "symbolization_jobs", ["status", "next_attempt_at", "lease_expires_at"]
    )
    op.create_index(
        "ix_symbol_job_tenant_event", "symbolization_jobs", ["tenant_id", "event_id"]
    )


def downgrade() -> None:
    """Remove the symbolization schema while retaining the M1 foundation."""
    op.drop_index("ix_symbol_job_tenant_event", table_name="symbolization_jobs")
    op.drop_index("ix_symbol_job_claim", table_name="symbolization_jobs")
    op.drop_table("symbolization_jobs")
    op.drop_index("ix_symbol_artifact_lookup", table_name="symbol_artifacts")
    op.drop_table("symbol_artifacts")
    op.drop_index(op.f("ix_ci_keys_tenant_id"), table_name="ci_keys")
    op.drop_table("ci_keys")
    op.drop_column("inbox_events", "variant")
    op.drop_column("inbox_events", "version_code")
