"""Match stored release identities to the existing 256-byte protocol.

Revision ID: 20260912_0006
Revises: 20260907_0005
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20260912_0006"
down_revision: str | None = "20260907_0005"
branch_labels: str | None = None
depends_on: str | None = None

IDENTITY_COLUMNS = {
    "inbox_events": ("app_version", "app_build", "variant"),
    "symbol_artifacts": ("app_build", "variant"),
    "release_decisions": ("release_version",),
}


def upgrade() -> None:
    """Widen declarations without rewriting identity values or published migrations."""
    for table, columns in IDENTITY_COLUMNS.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(column, existing_type=sa.String(128), type_=sa.String(256))


def downgrade() -> None:
    """Reject a lossy downgrade before any table is narrowed."""
    connection = op.get_bind()
    for table, columns in IDENTITY_COLUMNS.items():
        source = sa.table(table, *(sa.column(column, sa.String()) for column in columns))
        if connection.scalar(
            sa.select(sa.literal(1))
            .select_from(source)
            .where(sa.or_(*(sa.func.length(source.c[column]) > 128 for column in columns)))
            .limit(1)
        ):
            raise RuntimeError(
                "Release identities exceed 128 characters; downgrade would lose data"
            )
    for table, columns in IDENTITY_COLUMNS.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(column, existing_type=sa.String(256), type_=sa.String(128))
