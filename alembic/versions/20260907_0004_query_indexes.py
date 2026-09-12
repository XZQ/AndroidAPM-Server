"""Index filtered occurrence queries before bounded SQL aggregation.

Revision ID: 20260907_0004
Revises: 20260828_0003
"""

from alembic import op

revision: str = "20260907_0004"
down_revision: str | None = "20260828_0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Add filter-first indexes without rewriting any acknowledged event."""
    for name, dimension in (("release", "app_version"), ("fingerprint", "incident_fingerprint")):
        op.create_index(
            f"ix_inbox_{name}_time",
            "inbox_events",
            [
                "tenant_id",
                "app_id",
                "environment",
                dimension,
                "occurrence_timestamp_ms",
                "id",
            ],
        )


def downgrade() -> None:
    """Remove only this revision's query indexes."""
    op.drop_index("ix_inbox_fingerprint_time", table_name="inbox_events")
    op.drop_index("ix_inbox_release_time", table_name="inbox_events")
