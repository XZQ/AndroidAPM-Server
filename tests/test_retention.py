"""Retention removes raw evidence while preserving durability, active work and replay truth."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from androidapm_server.config import Settings
from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.maintenance import maintain_once
from androidapm_server.db.models import (
    AuditLog,
    InboxEvent,
    IngestKey,
    IngestRateWindow,
    SymbolizationJob,
    Tenant,
)
from androidapm_server.domain import ApmEvent, IngestMetadata, OccurrenceContext
from androidapm_server.errors import ApiError
from androidapm_server.identity import InstallationHmacKeyRing


async def test_retention_is_bounded_idempotent_and_keeps_replay_protection() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    settings = Settings(_env_file=None, retention_batch_size=1)
    keys = InstallationHmacKeyRing({"v1": b"synthetic-retention-test-key-bytes"}, "v1")
    metadata = IngestMetadata(
        request_id="request",
        tenant_id="tenant",
        app_id="app",
        environment="test",
        schema_version="3",
        sdk_version="test",
        protocol="protobuf",
    )
    events = {
        name: ApmEvent(
            event_id=name,
            timestamp=1000,
            module="crash",
            name="java_crash",
            kind="ALERT",
            severity="FATAL",
            priority="CRITICAL",
            process_name="app",
            thread_name="main",
            fields={"stackTrace": "private-stack", "threadName": "private-thread"},
            occurrence=OccurrenceContext(
                service_version="1.0.0",
                version_code="1",
                app_build="build",
                variant="release",
                installation_id="private-installation",
            ),
        )
        for name in [
            "delivered",
            "dead_letter",
            "pending",
            "processing",
            "awaiting_symbols",
            "young",
            "parked",
        ]
    }
    try:
        async with factory() as session:
            session.add(Tenant(id="tenant", name="Test"))
            session.add(
                IngestKey(
                    key_id="key",
                    tenant_id="tenant",
                    key_hash="synthetic-unused-hash",
                    app_id="app",
                    environment="test",
                )
            )
            await insert_batch(session, metadata, list(events.values()), keys)
            for name in events:
                await session.execute(
                    update(InboxEvent)
                    .where(InboxEvent.event_id == name)
                    .values(
                        status="delivered" if name in {"young", "parked"} else name,
                        finalized_at=now - timedelta(days=1 if name == "young" else 40),
                    )
                )
            parked = await session.scalar(select(InboxEvent).where(InboxEvent.event_id == "parked"))
            assert parked is not None
            session.add(
                SymbolizationJob(
                    inbox_event_id=parked.id,
                    tenant_id="tenant",
                    event_id="parked",
                    job_type="java",
                    status="processing",
                    input_sha256="0" * 64,
                )
            )
            for delta in [0, 2]:
                session.add(
                    IngestRateWindow(
                        key_id="key",
                        window_started_at=now - timedelta(days=delta),
                        request_count=1,
                        event_count=1,
                    )
                )
            await session.commit()
        assert await maintain_once(factory, settings, now) == (1, 1)
        assert await maintain_once(factory, settings, now) == (1, 0)
        assert await maintain_once(factory, settings, now) == (0, 0)
        async with factory() as session:
            rows = {row.event_id: row for row in (await session.scalars(select(InboxEvent))).all()}
            for name in ["delivered", "dead_letter"]:
                row = rows[name]
                assert row.raw_pruned_at is not None and row.payload_size_bytes == 0
                assert "private-stack" not in str(row.payload_json)
                assert "private-thread" not in str(row.normalized_json)
                assert row.normalized_json["raw_available"] is False
                assert (await insert_batch(session, metadata, [events[name]], keys)).duplicates == 1
            assert all(
                rows[name].raw_pruned_at is None
                for name in ["pending", "processing", "awaiting_symbols", "young", "parked"]
            )
            with pytest.raises(ApiError) as caught:
                await insert_batch(
                    session,
                    metadata,
                    [
                        events["delivered"].model_copy(
                            update={"fields": {"stackTrace": "different"}}
                        )
                    ],
                    keys,
                )
            assert caught.value.code == "event_id_conflict"
            assert (
                await session.scalar(
                    select(func.count(AuditLog.id)).where(AuditLog.action == "inbox.raw.prune")
                )
                == 2
            )
            assert await session.scalar(select(func.count()).select_from(IngestRateWindow)) == 1
    finally:
        await engine.dispose()
