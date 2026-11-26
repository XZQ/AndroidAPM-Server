"""Cover scoped pipeline and symbol-job status counts in settings queries.

Revision ID: 20261126_0011
Revises: 20261115_0010
"""

from alembic import op

revision: str = "20261126_0011"
down_revision: str | None = "20261115_0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Serve GET /v1/manage/settings status aggregations from covering indexes."""
    op.create_index(
        "ix_inbox_scope_status",
        "inbox_events",
        ["tenant_id", "app_id", "environment", "status"],
        unique=False,
    )
    op.create_index(
        "ix_symbol_job_tenant_status",
        "symbolization_jobs",
        ["tenant_id", "status", "inbox_event_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the advisory performance indexes; no data or constraint changes."""
    op.drop_index("ix_symbol_job_tenant_status", table_name="symbolization_jobs")
    op.drop_index("ix_inbox_scope_status", table_name="inbox_events")
