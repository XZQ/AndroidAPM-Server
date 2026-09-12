"""Use completed Java symbolization identities without breaking raw fingerprint links.

Revision ID: 20260912_0007
Revises: 20260912_0006
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20260912_0007"
down_revision: str | None = "20260912_0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Preserve raw aliases before applying already completed symbol jobs."""
    op.add_column("inbox_events", sa.Column("raw_incident_fingerprint", sa.String(64)))
    inbox = sa.table(
        "inbox_events",
        sa.column("id"),
        sa.column("incident_fingerprint"),
        sa.column("raw_incident_fingerprint"),
    )
    jobs = sa.table(
        "symbolization_jobs",
        sa.column("inbox_event_id"),
        sa.column("job_type"),
        sa.column("status"),
        sa.column("fingerprint_sha256"),
    )
    op.execute(inbox.update().values(raw_incident_fingerprint=inbox.c.incident_fingerprint))
    canonical = (
        sa.select(jobs.c.fingerprint_sha256)
        .where(
            jobs.c.inbox_event_id == inbox.c.id,
            jobs.c.job_type == "java",
            jobs.c.status == "symbolized",
            jobs.c.fingerprint_sha256.is_not(None),
        )
        .scalar_subquery()
    )
    op.execute(
        inbox.update()
        .where(inbox.c.incident_fingerprint.is_not(None), canonical.is_not(None))
        .values(incident_fingerprint=canonical)
    )
    op.create_index(
        "ix_inbox_raw_fingerprint_time",
        "inbox_events",
        [
            "tenant_id",
            "app_id",
            "environment",
            "raw_incident_fingerprint",
            "occurrence_timestamp_ms",
        ],
    )


def downgrade() -> None:
    """Restore original Issue identities; symbol results and raw payloads remain intact."""
    op.execute(sa.text("UPDATE inbox_events SET incident_fingerprint = raw_incident_fingerprint"))
    op.drop_index("ix_inbox_raw_fingerprint_time", table_name="inbox_events")
    op.drop_column("inbox_events", "raw_incident_fingerprint")
