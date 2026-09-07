from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.db.base import Base
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.worker import claim_batch, mark_delivered, mark_failed

NOW = datetime(2026, 7, 16, tzinfo=UTC)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        database_session.add(
            InboxEvent(
                tenant_id="tenant",
                event_id="event",
                app_id="app",
                environment="test",
                schema_version="1",
                sdk_version="0.1",
                protocol="line",
                event_timestamp_ms=1_700_000_000_000,
                occurrence_timestamp_ms=1_700_000_000_000,
                payload_json={},
                payload_sha256="0" * 64,
                request_id="request",
                received_at=NOW,
                next_attempt_at=NOW,
            )
        )
        await database_session.commit()
        yield database_session
    await engine.dispose()


@pytest.mark.asyncio
async def test_only_owner_can_complete_and_expired_lease_is_reclaimed(
    session: AsyncSession,
) -> None:
    claimed = await claim_batch(session, "owner-a", 10, 60, NOW)
    await session.commit()
    assert len(claimed) == 1
    assert await mark_delivered(session, "owner-b", [claimed[0].id], NOW) == 0
    await session.commit()

    assert await claim_batch(session, "owner-b", 10, 60, NOW + timedelta(seconds=30)) == []
    reclaimed = await claim_batch(session, "owner-b", 10, 60, NOW + timedelta(seconds=61))
    await session.commit()
    assert len(reclaimed) == 1
    assert reclaimed[0].attempt_count == 2
    assert await mark_delivered(session, "owner-b", [reclaimed[0].id], NOW) == 1
    await session.commit()
    row = await session.scalar(select(InboxEvent))
    assert row is not None and row.status == "delivered"


@pytest.mark.asyncio
async def test_retry_then_dead_letter_after_attempt_limit(session: AsyncSession) -> None:
    claimed = await claim_batch(session, "owner", 1, 60, NOW)
    await session.commit()
    assert await mark_failed(
        session, "owner", claimed, True, 2, "http_503", "temporary", now=NOW
    ) == 1
    await session.commit()
    row = await session.scalar(select(InboxEvent))
    assert row is not None and row.status == "pending"

    reclaimed = await claim_batch(session, "owner", 1, 60, NOW + timedelta(seconds=3))
    await session.commit()
    assert await mark_failed(
        session, "owner", reclaimed, True, 2, "http_503", "temporary", now=NOW
    ) == 1
    await session.commit()
    assert row.status == "dead_letter"
