from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server import symbolizer_worker
from androidapm_server.artifacts import ArtifactIdentity
from androidapm_server.config import Settings
from androidapm_server.constants import (
    ARTIFACT_TYPE_JAVA_MAPPING,
    ARTIFACT_TYPE_NATIVE_ELF,
    INBOX_STATUS_AWAITING_SYMBOLS,
    INBOX_STATUS_PENDING,
    SYMBOL_STATUS_FAILED,
    SYMBOL_STATUS_PENDING,
    SYMBOL_STATUS_PROCESSING,
    SYMBOL_STATUS_SYMBOLIZED,
    SYMBOL_STATUS_SYMBOLS_MISSING,
)
from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent, SymbolArtifact, SymbolizationJob, Tenant
from androidapm_server.db.symbolization import (
    enqueue_symbolization_jobs,
    requeue_matching_missing_jobs,
)
from androidapm_server.domain import (
    ApmEvent,
    IngestMetadata,
    NativeFrameIdentity,
    OccurrenceContext,
)
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.metrics import SYMBOLIZATION_JOBS
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
) -> Settings:
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        environment="test",
        artifact_storage_path=tmp_path,
        symbolization_enabled=True,
        symbolizer_max_attempts=3,
    )
    monkeypatch.setattr("androidapm_server.symbolizer_worker.get_settings", lambda: settings)
    monkeypatch.setattr("androidapm_server.symbolizer_worker.get_session_factory", lambda: factory)
    return settings


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


@pytest.mark.parametrize("timeout", [120, 115])
def test_symbolizer_timeout_cannot_outlive_lease(timeout: int) -> None:
    with pytest.raises(ValueError, match="timeout must be shorter"):
        Settings(symbolizer_timeout_seconds=timeout, symbolizer_lease_seconds=120)


def tool_result(stack: str = "Real.run(Real.kt:10)") -> SymbolizationResult:
    return SymbolizationResult({"symbolizedStack": stack}, "b" * 64, "retrace", "test-r8")


@pytest.mark.asyncio
async def test_waiting_jobs_keep_their_entire_lease_and_retry_budget(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed_job(factory, "first", artifact=True)
    await seed_job(factory, "second", artifact=False)
    await seed_job(factory, "third", artifact=False)
    configure_worker(monkeypatch, factory, tmp_path)
    completed: list[str] = []

    async def check_queue(job: SymbolizationJob, *_: object) -> SymbolizationResult:
        async with factory() as session:
            rows = list((await session.scalars(select(SymbolizationJob))).all())
        assert [row.event_id for row in rows if row.status == SYMBOL_STATUS_PROCESSING] == [
            job.event_id
        ]
        for row in rows:
            if row.event_id not in [*completed, job.event_id]:
                assert row.status == SYMBOL_STATUS_PENDING
                assert row.lease_owner is None and row.lease_expires_at is None
                assert row.attempt_count == 0
        completed.append(job.event_id)
        return tool_result()

    monkeypatch.setattr(symbolizer_worker, "symbolize_job", check_queue)
    assert await symbolize_once("owner") == 3
    assert completed == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_missing_artifact_polls_do_not_exhaust_tool_retries(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed_job(factory, "missing", artifact=False)
    configure_worker(monkeypatch, factory, tmp_path)
    for _ in range(4):
        assert await symbolize_once("owner") == 1
        async with factory() as session:
            job = await session.scalar(select(SymbolizationJob))
            assert job is not None and job.status == SYMBOL_STATUS_SYMBOLS_MISSING
            assert job.attempt_count == 0
            job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    # Replay the same event and register its artifact, then encounter the first tool failure.
    await seed_job(factory, "missing", artifact=True)
    monkeypatch.setattr(
        symbolizer_worker,
        "symbolize_job",
        AsyncMock(side_effect=SymbolizationFailure("tool_unavailable", "missing", True)),
    )
    assert await symbolize_once("owner") == 1
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        assert job is not None and job.status == SYMBOL_STATUS_PENDING
        assert job.attempt_count == 1 and job.last_error_code == "tool_unavailable"


@pytest.mark.asyncio
async def test_reclaimed_attempt_in_same_worker_fences_old_result_and_metrics(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed_job(factory, "reclaim", artifact=True)
    settings = configure_worker(monkeypatch, factory, tmp_path)
    settings.symbolizer_batch_size = 1
    owners: list[str | None] = []
    counter = SYMBOLIZATION_JOBS.labels("symbolized", "java")
    before = counter._value.get()

    async def finish_out_of_order(job: SymbolizationJob, *_: object) -> SymbolizationResult:
        owners.append(job.lease_owner)
        if len(owners) == 1:
            async with factory() as session:
                await session.execute(
                    update(SymbolizationJob)
                    .where(SymbolizationJob.id == job.id)
                    .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
                await session.commit()
            assert await symbolize_once("same-worker" * 20) == 1
            return tool_result("stale result")
        return tool_result("current result")

    monkeypatch.setattr(symbolizer_worker, "symbolize_job", finish_out_of_order)
    assert await symbolize_once("same-worker" * 20) == 0
    assert len(owners) == 2 and owners[0] != owners[1]
    assert all(owner is not None and len(owner) <= 128 for owner in owners)
    assert counter._value.get() - before == 1
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        assert job is not None and job.result_json == {"symbolizedStack": "current result"}
        assert job.attempt_count == 2


@pytest.mark.asyncio
async def test_final_attempt_crash_does_not_grant_another_tool_run(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed_job(factory, "exhausted", artifact=True)
    configure_worker(monkeypatch, factory, tmp_path)
    async with factory() as session:
        await session.execute(
            update(SymbolizationJob).values(
                status=SYMBOL_STATUS_PROCESSING,
                lease_owner="crashed-worker",
                attempt_count=3,
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        await session.commit()
    tool = AsyncMock()
    monkeypatch.setattr(symbolizer_worker, "symbolize_job", tool)
    assert await symbolize_once("owner") == 1
    tool.assert_not_awaited()
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        inbox = await session.scalar(select(InboxEvent))
        assert job is not None and job.status == SYMBOL_STATUS_FAILED
        assert job.last_error_code == "symbolizer_attempts_exhausted" and job.attempt_count == 3
        assert inbox is not None and inbox.status == INBOX_STATUS_PENDING


@pytest.mark.asyncio
async def test_lease_deadline_also_bounds_artifact_resolution(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed_job(factory, "slow-resolution", artifact=True)
    settings = configure_worker(monkeypatch, factory, tmp_path)
    settings.symbolizer_batch_size = 1
    cancelled = asyncio.Event()

    async def stalled_lookup(*_: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    # Accelerate only the monotonic execution deadline; the persisted lease stays valid.
    real_timeout = asyncio.timeout
    monkeypatch.setattr(symbolizer_worker.asyncio, "timeout_at", lambda _: real_timeout(0.05))
    monkeypatch.setattr(symbolizer_worker, "resolve_artifact", stalled_lookup)
    assert await symbolize_once("owner") == 1
    assert cancelled.is_set()
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        assert job is not None and job.status == SYMBOL_STATUS_PENDING
        assert job.last_error_code == "symbolizer_lease_budget_exhausted"
        assert job.attempt_count == 0


@pytest.mark.parametrize("partial", [True, False])
async def test_second_native_module_waits_without_attempt_and_upload_wakes_job(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    partial: bool,
) -> None:
    crash = event("native-two-modules")
    assert crash.occurrence is not None
    frames = tuple(
        NativeFrameIdentity(
            abi="arm64-v8a",
            module_build_id=str(index) * 40,
            module_name=f"lib{index}.so",
            module_relative_pc=index * 16,
        )
        for index in (1, 2)
    )
    crash = crash.model_copy(
        update={
            "name": "native_crash",
            "fields": {"backtrace": "#00 pc 10 lib1.so\n#01 pc 20 lib2.so"},
            "occurrence": crash.occurrence.model_copy(update={"native_frames": frames}),
        }
    )
    artifacts = [
        SymbolArtifact(
            tenant_id="tenant-a",
            artifact_type=ARTIFACT_TYPE_NATIVE_ELF,
            app_id="com.example",
            version_code="42",
            app_build="build-1",
            variant="release",
            abi=frame.abi,
            build_id=frame.module_build_id,
            checksum_sha256=str(index) * 64,
            size_bytes=1,
            storage_key=frame.module_name,
            uploaded_by="ci",
        )
        for index, frame in enumerate(frames, 1)
    ]
    for artifact in artifacts:
        (tmp_path / artifact.storage_key).write_bytes(b"synthetic")
    async with factory() as session:
        await insert_batch(session, metadata(), [crash], TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(), [crash])
        session.add(artifacts[0])
        await session.commit()
    configure_worker(monkeypatch, factory, tmp_path)
    tool = AsyncMock(
        side_effect=["first\nfirst.c:1:2", "??\n??:0:0" if partial else "second\nsecond.c:2:3"]
    )
    monkeypatch.setattr("androidapm_server.symbolization._run_tool", tool)
    assert await symbolize_once("owner") == 1
    tool.assert_not_awaited()
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        assert job is not None and job.status == SYMBOL_STATUS_SYMBOLS_MISSING
        assert job.attempt_count == 0
        session.add(artifacts[1])
        await session.flush()
        assert (
            await requeue_matching_missing_jobs(
                session,
                ArtifactIdentity(
                    ARTIFACT_TYPE_NATIVE_ELF,
                    "tenant-a",
                    "com.example",
                    "42",
                    "build-1",
                    "release",
                    frames[1].abi,
                    frames[1].module_build_id,
                ),
            )
            == 1
        )
        await session.commit()
    assert await symbolize_once("owner") == 1
    assert tool.await_count == 2
    async with factory() as session:
        job = await session.scalar(select(SymbolizationJob))
        inbox = await session.scalar(select(InboxEvent))
        assert job is not None
        assert job.status == ("partially_symbolized" if partial else "symbolized")
        assert job.attempt_count == 1 and job.lease_owner is None
        assert job.result_json is not None
        assert [frame["artifactId"] for frame in job.result_json["nativeFrames"]] == [
            artifact.id for artifact in artifacts
        ]
        assert inbox is not None and inbox.status == INBOX_STATUS_PENDING
