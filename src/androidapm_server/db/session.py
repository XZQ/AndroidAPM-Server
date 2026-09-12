"""Async database engine and request-scoped session dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from androidapm_server.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Create the process-wide async SQLAlchemy engine."""
    database_url = get_settings().database_url.get_secret_value()
    # SQL exceptions must not render bound raw telemetry or credential parameters.
    return create_async_engine(database_url, pool_pre_ping=True, hide_parameters=True)


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Create sessions that keep ORM values usable after commit."""
    return async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one request-scoped async database session."""
    async with get_session_factory()() as session:
        yield session


async def close_engine() -> None:
    """Dispose database connections during application shutdown."""
    await get_engine().dispose()
