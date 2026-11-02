"""Add occurrence statistics identity and fenced progress for independent OTLP signals.

Revision ID: 20261014_0009
Revises: 20261013_0008
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20261014_0009"
down_revision: str | None = "20261013_0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Keep historical absence explicit; do not backfill session identities."""
    op.add_column(
        "inbox_events",
        sa.Column(
            "session_identity_quality", sa.String(32), nullable=False, server_default="ABSENT"
        ),
    )
    op.add_column("inbox_events", sa.Column("session_hmac", sa.String(64)))
    op.add_column("inbox_events", sa.Column("session_hmac_key_version", sa.String(32)))
    op.add_column("inbox_events", sa.Column("device_model", sa.String(256)))
    op.add_column("inbox_events", sa.Column("os_version", sa.String(256)))
    op.add_column(
        "inbox_events",
        sa.Column(
            "export_signal_states_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )


def downgrade() -> None:
    """Explicit rollback removes only new projection/progress metadata."""
    with op.batch_alter_table("inbox_events") as batch:
        for name in (
            "export_signal_states_json",
            "os_version",
            "device_model",
            "session_hmac_key_version",
            "session_hmac",
            "session_identity_quality",
        ):
            batch.drop_column(name)
