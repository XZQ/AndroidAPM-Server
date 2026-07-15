from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.auth import Principal
from androidapm_server.db.base import Base
from androidapm_server.db.models import IngestKey, Tenant
from androidapm_server.db.quota import consume_quota
from androidapm_server.errors import ApiError


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        database_session.add(Tenant(id="tenant", name="Tenant"))
        database_session.add(
            IngestKey(
                key_id="key",
                tenant_id="tenant",
                key_hash="unused",
                app_id="app",
                environment="test",
                requests_per_minute=2,
                events_per_minute=3,
            )
        )
        await database_session.commit()
        yield database_session
    await engine.dispose()


PRINCIPAL = Principal("tenant", "key", "app", "test", 2, 3)
NOW = datetime(2026, 7, 16, 1, 2, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_enforces_distributed_request_and_event_window(session: AsyncSession) -> None:
    await consume_quota(session, PRINCIPAL, 1, NOW)
    await session.commit()
    await consume_quota(session, PRINCIPAL, 2, NOW)
    await session.commit()
    with pytest.raises(ApiError) as caught:
        await consume_quota(session, PRINCIPAL, 1, NOW)
    assert caught.value.status_code == 429
    assert caught.value.headers == {"Retry-After": "30"}


@pytest.mark.asyncio
async def test_new_minute_resets_window(session: AsyncSession) -> None:
    await consume_quota(session, PRINCIPAL, 3, NOW)
    await session.commit()
    await consume_quota(session, PRINCIPAL, 3, NOW.replace(minute=3))
    await session.commit()
