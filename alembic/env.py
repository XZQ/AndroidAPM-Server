"""Async Alembic environment using the validated application database URL."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from androidapm_server.config import get_settings
from androidapm_server.db import models as _models  # noqa: F401
from androidapm_server.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def do_run_migrations(connection: object) -> None:
    """Configure and run migrations on an established sync facade."""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine directly so percent-encoded DB passwords bypass ConfigParser."""
    connectable = create_async_engine(
        get_settings().database_url.get_secret_value(),
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported by this async environment")
asyncio.run(run_async_migrations())
