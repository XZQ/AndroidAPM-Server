from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent
from androidapm_server.domain import ApmEvent, IngestMetadata
from androidapm_server.errors import ApiError


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        yield database_session
    await engine.dispose()


def event(event_id: str, name: str = "request") -> ApmEvent:
    return ApmEvent(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="network",
        name=name,
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        process_name="com.example",
        thread_name="main",
    )


def metadata(tenant_id: str = "tenant-a") -> IngestMetadata:
    return IngestMetadata(
        request_id="request-1",
        tenant_id=tenant_id,
        app_id="com.example",
        environment="test",
        schema_version="1",
        sdk_version="0.1.0",
        protocol="protobuf",
    )


@pytest.mark.asyncio
async def test_replay_is_acknowledged_without_second_row(session: AsyncSession) -> None:
    first = await insert_batch(session, metadata(), [event("one"), event("two")])
    await session.commit()
    second = await insert_batch(session, metadata(), [event("one"), event("two")])
    await session.commit()

    count = await session.scalar(select(func.count()).select_from(InboxEvent))
    assert first.inserted == 2
    assert second.inserted == 0
    assert second.duplicates == 2
    assert count == 2


@pytest.mark.asyncio
async def test_same_event_id_isolated_by_tenant(session: AsyncSession) -> None:
    await insert_batch(session, metadata("tenant-a"), [event("same")])
    await session.commit()
    result = await insert_batch(session, metadata("tenant-b"), [event("same")])
    await session.commit()
    assert result.inserted == 1


@pytest.mark.asyncio
async def test_conflicting_identity_rejects_without_insert(session: AsyncSession) -> None:
    await insert_batch(session, metadata(), [event("existing")])
    await session.commit()

    with pytest.raises(ApiError) as caught:
        await insert_batch(session, metadata(), [event("new"), event("existing", "changed")])
    await session.rollback()

    ids = list(await session.scalars(select(InboxEvent.event_id).order_by(InboxEvent.event_id)))
    assert caught.value.code == "event_id_conflict"
    assert ids == ["existing"]


@pytest.mark.asyncio
async def test_conflict_inside_batch_rejects_all(session: AsyncSession) -> None:
    with pytest.raises(ApiError):
        await insert_batch(session, metadata(), [event("same"), event("same", "changed")])
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(InboxEvent)) == 0
