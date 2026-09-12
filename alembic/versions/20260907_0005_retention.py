"""Keep replay identities while pruning expired evidence and bounding raw storage.

Revision ID: 20260907_0005
Revises: 20260907_0004
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20260907_0005"
down_revision: str | None = "20260907_0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add retention markers and conservatively measure existing raw JSON bytes."""
    op.add_column(
        "inbox_events",
        sa.Column("payload_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("inbox_events", sa.Column("raw_pruned_at", sa.DateTime(timezone=True)))
    op.add_column("inbox_events", sa.Column("finalized_at", sa.DateTime(timezone=True)))
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            sa.text(
                "UPDATE inbox_events SET payload_size_bytes = octet_length(CAST(payload_json AS TEXT))"
            )
        )
    else:
        op.execute(
            sa.text(
                "UPDATE inbox_events SET payload_size_bytes = length(CAST(payload_json AS BLOB))"
            )
        )
    op.create_index(
        "ix_inbox_retention", "inbox_events", ["status", "raw_pruned_at", "finalized_at"]
    )
    op.create_index("ix_ingest_rate_expiry", "ingest_rate_windows", ["window_started_at"])


def downgrade() -> None:
    """Drop only schema additions; previously pruned evidence is not recoverable."""
    op.drop_index("ix_ingest_rate_expiry", table_name="ingest_rate_windows")
    op.drop_index("ix_inbox_retention", table_name="inbox_events")
    for name in ("finalized_at", "raw_pruned_at", "payload_size_bytes"):
        op.drop_column("inbox_events", name)
