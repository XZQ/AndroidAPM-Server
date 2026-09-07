from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.config import Settings
from androidapm_server.constants import (
    ARTIFACT_TYPE_JAVA_MAPPING,
    INBOX_STATUS_AWAITING_SYMBOLS,
    INBOX_STATUS_PENDING,
    SYMBOL_STATUS_PENDING,
    SYMBOL_STATUS_SYMBOLIZED,
    SYMBOL_STATUS_SYMBOLS_MISSING,
)
from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent, SymbolArtifact, SymbolizationJob, Tenant
from androidapm_server.db.symbolization import enqueue_symbolization_jobs
from androidapm_server.domain import ApmEvent, IngestMetadata, OccurrenceContext
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.symbolization import SymbolizationFailure, SymbolizationResult
from androidapm_server.symbolizer_worker import symbolize_once

TEST_KEY_RING = InstallationHmacKeyRing.parse(
    '{"v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}',
    "v1",
)


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(Tenant(id="tenant-a", name="Tenant A"))
        await session.commit()
    yield session_factory
    await engine.dispose()


def metadata() -> IngestMetadata:
    return IngestMetadata(
        request_id="request",
        tenant_id="tenant-a",
        app_id="com.example",
        environment="production",
        schema_version="1",
        sdk_version="0.1.0",
        app_build="build-1",
        version_code="42",
        variant="release",
        protocol="protobuf",
    )


def event(event_id: str) -> ApmEvent:
    return ApmEvent(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="crash",
        name="java_crash",
        kind="ALERT",
        severity="FATAL",
        priority="CRITICAL",
        process_name="com.example",
        thread_name="main",
        fields={"stackTrace": "at a.a(SourceFile:1)"},
        occurrence=OccurrenceContext(
            service_version="1.0",
            version_code="42",
            app_build="build-1",
            variant="release",
            installation_id="symbolizer-worker-installation",
        ),
    )


async def seed_job(
    factory: async_sessionmaker[AsyncSession], event_id: str, *, artifact: bool
) -> None:
    async with factory() as session:
        crash = event(event_id)
        await insert_batch(session, metadata(), [crash], TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(), [crash])
        if artifact:
            session.add(
                SymbolArtifact(
                    tenant_id="tenant-a",
                    artifact_type=ARTIFACT_TYPE_JAVA_MAPPING,
                    app_id="com.example",
                    version_code="42",
                    app_build="build-1",
                    variant="release",
                    abi="",
                    build_id="",
                    checksum_sha256="a" * 64,
                    size_bytes=100,
                    storage_key="mapping.txt",
                    uploaded_by="ci",
                )
            )
        await session.commit()


def configure_worker(
    monkeypatch: pytest.MonkeyPatch,
    factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        environment="test",
        artifact_storage_path=tmp_path,
        symbolization_enabled=True,
        symbolizer_max_attempts=3,
    )
    monkeypatch.setattr("androidapm_server.symbolizer_worker.get_settings", lambda: settings)
    monkeypatch.setattr("androidapm_server.symbolizer_worker.get_session_factory", lambda: factory)


@pytest.mark.asyncio
async def test_worker_parks_missing_artifact_until_upload(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed_job(factory, "missing", artifact=False)
    configure_worker(monkeypatch, factory, tmp_path)
    assert await symbolize_once("owner") == 1
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        inbox = await session.scalar(select(InboxEvent))
        assert job is not None and job.status == SYMBOL_STATUS_SYMBOLS_MISSING
        assert inbox is not None and inbox.status == INBOX_STATUS_AWAITING_SYMBOLS


@pytest.mark.asyncio
async def test_worker_persists_success_and_releases_export(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed_job(factory, "success", artifact=True)
    configure_worker(monkeypatch, factory, tmp_path)
    result = SymbolizationResult(
        {"symbolizedStack": "com.example.Real.run(Real.kt:10)"},
        "b" * 64,
        "retrace",
        "test-r8",
    )
    monkeypatch.setattr(
        "androidapm_server.symbolizer_worker.symbolize_job",
        AsyncMock(return_value=result),
    )
    assert await symbolize_once("owner") == 1
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        inbox = await session.scalar(select(InboxEvent))
        assert job is not None and job.status == SYMBOL_STATUS_SYMBOLIZED
        assert job.fingerprint_sha256 == "b" * 64
        assert inbox is not None and inbox.status == INBOX_STATUS_PENDING


@pytest.mark.asyncio
async def test_worker_retries_transient_tool_failure_without_releasing_raw_event(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed_job(factory, "retry", artifact=True)
    configure_worker(monkeypatch, factory, tmp_path)
    monkeypatch.setattr(
        "androidapm_server.symbolizer_worker.symbolize_job",
        AsyncMock(side_effect=SymbolizationFailure("tool_unavailable", "missing", True)),
    )
    assert await symbolize_once("owner") == 1
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        inbox = await session.scalar(select(InboxEvent))
        assert job is not None and job.status == SYMBOL_STATUS_PENDING
        assert job.last_error_code == "tool_unavailable"
        assert inbox is not None and inbox.status == INBOX_STATUS_AWAITING_SYMBOLS


def test_symbolizer_timeout_cannot_outlive_lease() -> None:
    with pytest.raises(ValueError, match="timeout must be shorter"):
        Settings(symbolizer_timeout_seconds=120, symbolizer_lease_seconds=120)
