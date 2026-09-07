"""Exercise complete export cycles against isolated, test-only SQLite storage."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server import worker
from androidapm_server.config import Settings
from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent
from androidapm_server.domain import ApmEvent, IngestMetadata
from androidapm_server.otlp.client import OtlpLogsClient


@pytest.fixture
async def factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    monkeypatch.setattr(
        worker, "get_settings", lambda: Settings(_env_file=None, worker_max_attempts=2)
    )
    metadata = IngestMetadata(
        tenant_id="tenant",
        app_id="app",
        environment="test",
        request_id="request",
        schema_version="1",
        sdk_version="test",
        protocol="line",
    )
    events = [
        ApmEvent(
            event_id=name,
            timestamp=1_700_000_000_000,
            module="core",
            name="sdk_health",
            kind="METRIC",
            severity="INFO",
            priority="NORMAL",
            process_name="app",
            thread_name="main",
            fields={"queueSize": 1},
        )
        for name in ("bad", "healthy")
    ]
    async with factory() as session:
        await insert_batch(session, metadata, events)
        await session.commit()
    yield factory
    await engine.dispose()


async def test_historical_mapping_failure_does_not_block_a_healthy_event(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        await session.execute(
            update(InboxEvent)
            .where(InboxEvent.event_id == "bad")
            .values(
                normalized_json={"indexed_attributes": {"queueSize": 2**63}},
            )
        )
        await session.commit()
    requests: list[bytes] = []

    async def receive(request: httpx.Request) -> httpx.Response:
        requests.append(request.content)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
        client = OtlpLogsClient("http://test/v1/logs", "{}", 1, http)
        assert await worker.export_once(client, "owner") == 2
        assert await worker.export_once(client, "owner") == 0
    parsed = ExportLogsServiceRequest.FromString(requests[0])
    records = [
        r for group in parsed.resource_logs for scope in group.scope_logs for r in scope.log_records
    ]
    assert len(records) == 1
    assert any(
        a.key == "android.apm.event_id" and a.value.string_value == "healthy"
        for a in records[0].attributes
    )
    async with factory() as session:
        rows = {row.event_id: row for row in (await session.scalars(select(InboxEvent))).all()}
        assert rows["bad"].status == "dead_letter"
        assert rows["bad"].last_error_code == "otlp_mapping_failed"
        assert rows["bad"].payload_json["fields"]["queueSize"] == 1
        assert rows["healthy"].status == "delivered"


async def test_unexpected_transport_failure_exhausts_retry_budget_without_logging_payloads(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async def broken_transport(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError("synthetic-sensitive-transport-detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken_transport)) as http:
        client = OtlpLogsClient("http://test/v1/logs", "{}", 1, http)
        assert await worker.export_once(client, "owner") == 2
        async with factory() as session:
            await session.execute(
                update(InboxEvent).values(
                    next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await session.commit()
        assert await worker.export_once(client, "owner") == 2
        assert await worker.export_once(client, "owner") == 0
    async with factory() as session:
        rows = list((await session.scalars(select(InboxEvent))).all())
        assert all(r.status == "dead_letter" and r.attempt_count == 2 for r in rows)
        assert all(r.last_error_message == "OTLP transport failed" for r in rows)
