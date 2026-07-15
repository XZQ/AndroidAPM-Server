from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.worker import claim_batch
from androidapm_server.domain import ApmEvent, IngestMetadata
from androidapm_server.errors import ApiError

POSTGRES_URL = os.environ.get("APM_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="APM_TEST_POSTGRES_URL is required")


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    assert POSTGRES_URL is not None
    engine = create_async_engine(POSTGRES_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


def event(event_id: str, *, name: str = "postgres") -> ApmEvent:
    return ApmEvent(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="integration",
        name=name,
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        process_name="integration",
        thread_name="test",
    )


def metadata(tenant_id: str, request_id: str) -> IngestMetadata:
    return IngestMetadata(
        request_id=request_id,
        tenant_id=tenant_id,
        app_id="integration.test",
        environment="ci",
        schema_version="1",
        sdk_version="test",
        protocol="protobuf",
    )


@pytest.mark.asyncio
async def test_concurrent_duplicate_insert_has_one_postgres_fact(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = f"integration-{uuid.uuid4().hex}"

    async def submit(request_id: str) -> int:
        async with factory() as session:
            result = await insert_batch(session, metadata(tenant_id, request_id), [event("same")])
            await session.commit()
            return result.inserted

    inserted = await asyncio.gather(submit("one"), submit("two"))
    async with factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(InboxEvent).where(InboxEvent.tenant_id == tenant_id)
        )
        await session.execute(delete(InboxEvent).where(InboxEvent.tenant_id == tenant_id))
        await session.commit()
    assert sorted(inserted) == [0, 1]
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_conflicting_insert_rejects_loser(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = f"integration-{uuid.uuid4().hex}"

    async def submit(request_id: str, name: str) -> int | str:
        async with factory() as session:
            try:
                result = await insert_batch(
                    session,
                    metadata(tenant_id, request_id),
                    [event("same", name=name)],
                )
                await session.commit()
                return result.inserted
            except ApiError as error:
                await session.rollback()
                return error.code

    outcomes = await asyncio.gather(submit("one", "first"), submit("two", "second"))
    async with factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(InboxEvent).where(InboxEvent.tenant_id == tenant_id)
        )
        await session.execute(delete(InboxEvent).where(InboxEvent.tenant_id == tenant_id))
        await session.commit()
    assert sorted(outcomes, key=str) == [1, "event_id_conflict"]
    assert count == 1


@pytest.mark.asyncio
async def test_skip_locked_assigns_distinct_rows_to_concurrent_owners(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = f"integration-{uuid.uuid4().hex}"
    async with factory() as session:
        await insert_batch(
            session,
            metadata(tenant_id, "seed"),
            [event("one"), event("two")],
        )
        await session.commit()

    first_session = factory()
    second_session = factory()
    try:
        first = await claim_batch(first_session, "owner-one", 1, 60, datetime.now(UTC))
        second = await claim_batch(second_session, "owner-two", 1, 60, datetime.now(UTC))
        assert len(first) == len(second) == 1
        assert first[0].id != second[0].id
        await first_session.commit()
        await second_session.commit()
    finally:
        await first_session.close()
        await second_session.close()
    async with factory() as session:
        await session.execute(delete(InboxEvent).where(InboxEvent.tenant_id == tenant_id))
        await session.commit()
