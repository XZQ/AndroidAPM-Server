"""Cover the capacity admission raw-byte SUM with a partial index.

Revision ID: 20261115_0010
Revises: 20261014_0009
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20261115_0010"
down_revision: str | None = "20261014_0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Let PostgreSQL answer SUM(payload_size_bytes) WHERE raw_pruned_at IS NULL index-only.

    check_inbox_capacity runs this SUM under a global advisory lock on every inserting
    batch; without a covering partial index it scans every unpruned row, so admission
    latency grows with the live inbox. The index is partial because pruned rows carry
    payload_size_bytes = 0 and are excluded from the SUM predicate.
    """
    op.create_index(
        "ix_inbox_raw_bytes",
        "inbox_events",
        ["payload_size_bytes"],
        unique=False,
        postgresql_where=sa.text("raw_pruned_at IS NULL"),
        sqlite_where=sa.text("raw_pruned_at IS NULL"),
    )


def downgrade() -> None:
    """Remove only the advisory performance index; no data or constraint changes."""
    op.drop_index("ix_inbox_raw_bytes", table_name="inbox_events")
