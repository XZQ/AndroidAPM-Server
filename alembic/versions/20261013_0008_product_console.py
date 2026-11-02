"""Persist scoped application settings and alert lifecycle/outbox.

Revision ID: 20261013_0008
Revises: 20260912_0007
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20261013_0008"
down_revision: str | None = "20260912_0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add independent product tables without changing published revisions."""
    pk = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "app_settings",
        sa.Column("id", pk, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("app_id", sa.String(256), nullable=False),
        sa.Column("environment", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.UniqueConstraint("tenant_id", "app_id", "environment", name="uq_app_settings_scope"),
    )
    op.create_table(
        "alert_rules",
        sa.Column("id", pk, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("app_id", sa.String(256), nullable=False),
        sa.Column("environment", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("metric", sa.String(256), nullable=False),
        sa.Column("comparison", sa.String(8), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("min_samples", sa.Integer(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("release_version", sa.String(256)),
        sa.Column("channel", sa.String(64)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("silence_until", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("last_value", sa.Float()),
        sa.Column("last_sample_count", sa.Integer(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("active_instance_id", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alert_rule_scope", "alert_rules", ["tenant_id", "app_id", "environment"])
    op.create_table(
        "alert_instances",
        sa.Column("id", pk, primary_key=True, autoincrement=True),
        sa.Column(
            "rule_id",
            sa.BigInteger(),
            sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.String(64)),
        sa.Column("trigger_value", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_alert_instance_rule_time", "alert_instances", ["rule_id", "triggered_at"])
    op.create_table(
        "alert_deliveries",
        sa.Column("id", pk, primary_key=True, autoincrement=True),
        sa.Column(
            "instance_id",
            sa.BigInteger(),
            sa.ForeignKey("alert_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transition", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(64)),
        sa.UniqueConstraint("instance_id", "transition", "channel", name="uq_alert_delivery"),
    )
    op.create_index(
        "ix_alert_delivery_claim",
        "alert_deliveries",
        ["status", "next_attempt_at", "lease_expires_at"],
    )


def downgrade() -> None:
    """Remove product state only when explicitly rolling back this revision."""
    op.drop_table("alert_deliveries")
    op.drop_table("alert_instances")
    op.drop_table("alert_rules")
    op.drop_table("app_settings")
