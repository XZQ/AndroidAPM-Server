"""Add occurrence identity, pseudonyms, normalization, and scoped query credentials.

Revision ID: 20260828_0003
Revises: 20260716_0002
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260828_0003"
down_revision: str | Sequence[str] | None = "20260716_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIGINT_PRIMARY_KEY = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    """Persist identity provenance and create the bounded query control plane."""
    op.add_column(
        "inbox_events",
        sa.Column(
            "scope_identity_quality",
            sa.String(length=32),
            nullable=False,
            server_default="AUTHENTICATED",
        ),
    )
    op.add_column(
        "inbox_events",
        sa.Column(
            "release_identity_quality",
            sa.String(length=32),
            nullable=False,
            server_default="ABSENT",
        ),
    )
    op.add_column(
        "inbox_events",
        sa.Column(
            "installation_identity_quality",
            sa.String(length=32),
            nullable=False,
            server_default="ABSENT",
        ),
    )
    op.add_column("inbox_events", sa.Column("installation_hmac", sa.String(length=64)))
    op.add_column(
        "inbox_events", sa.Column("installation_hmac_key_version", sa.String(length=32))
    )
    op.add_column(
        "inbox_events", sa.Column("occurrence_timestamp_ms", BIGINT_PRIMARY_KEY, nullable=True)
    )
    op.execute(
        sa.text(
            "UPDATE inbox_events SET occurrence_timestamp_ms = event_timestamp_ms "
            "WHERE occurrence_timestamp_ms IS NULL"
        )
    )
    with op.batch_alter_table("inbox_events") as batch:
        batch.alter_column(
            "occurrence_timestamp_ms",
            existing_type=BIGINT_PRIMARY_KEY,
            nullable=False,
        )
    op.add_column(
        "inbox_events", sa.Column("collection_timestamp_ms", BIGINT_PRIMARY_KEY, nullable=True)
    )
    op.add_column(
        "inbox_events",
        sa.Column(
            "timestamp_quality",
            sa.String(length=32),
            nullable=False,
            server_default="EVENT_DECLARED",
        ),
    )
    op.add_column(
        "inbox_events",
        sa.Column(
            "normalization_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "inbox_events",
        sa.Column(
            "normalized_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "inbox_events", sa.Column("incident_fingerprint", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "inbox_events",
        sa.Column(
            "native_identity_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.create_index(
        "ix_inbox_query_scope_time",
        "inbox_events",
        ["tenant_id", "app_id", "environment", "occurrence_timestamp_ms"],
    )
    op.create_index(
        "ix_inbox_query_release",
        "inbox_events",
        [
            "tenant_id",
            "app_id",
            "environment",
            "app_version",
            "release_identity_quality",
        ],
    )
    op.create_index(
        "ix_inbox_incident_fingerprint",
        "inbox_events",
        ["tenant_id", "incident_fingerprint"],
    )

    op.create_table(
        "query_keys",
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=512), nullable=False),
        sa.Column("app_id", sa.String(length=256), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("description", sa.String(length=512)),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_query_keys_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("key_id", name=op.f("pk_query_keys")),
    )
    op.create_index(op.f("ix_query_keys_tenant_id"), "query_keys", ["tenant_id"])

    op.create_table(
        "release_decisions",
        sa.Column("id", BIGINT_PRIMARY_KEY, autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("app_id", sa.String(length=256), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("release_version", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=256), nullable=False),
        sa.Column("evidence_from_ms", BIGINT_PRIMARY_KEY, nullable=False),
        sa.Column("evidence_to_ms", BIGINT_PRIMARY_KEY, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_release_decisions")),
    )
    op.create_index(
        "ix_release_decision_scope_created",
        "release_decisions",
        ["tenant_id", "app_id", "environment", "created_at"],
    )


def downgrade() -> None:
    """Remove the query plane and occurrence-derived columns."""
    op.drop_index("ix_release_decision_scope_created", table_name="release_decisions")
    op.drop_table("release_decisions")
    op.drop_index(op.f("ix_query_keys_tenant_id"), table_name="query_keys")
    op.drop_table("query_keys")
    op.drop_index("ix_inbox_incident_fingerprint", table_name="inbox_events")
    op.drop_index("ix_inbox_query_release", table_name="inbox_events")
    op.drop_index("ix_inbox_query_scope_time", table_name="inbox_events")
    for column in (
        "native_identity_json",
        "incident_fingerprint",
        "normalized_json",
        "normalization_version",
        "timestamp_quality",
        "collection_timestamp_ms",
        "occurrence_timestamp_ms",
        "installation_hmac_key_version",
        "installation_hmac",
        "installation_identity_quality",
        "release_identity_quality",
        "scope_identity_quality",
    ):
        op.drop_column("inbox_events", column)
