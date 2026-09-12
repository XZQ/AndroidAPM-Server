from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from androidapm_server.artifacts import ArtifactIdentity
from androidapm_server.constants import (
    ARTIFACT_TYPE_JAVA_MAPPING,
    ARTIFACT_TYPE_NATIVE_ELF,
    INBOX_STATUS_AWAITING_SYMBOLS,
    INBOX_STATUS_PENDING,
    SYMBOL_JOB_JAVA,
    SYMBOL_JOB_NATIVE,
    SYMBOL_STATUS_METADATA_MISSING,
    SYMBOL_STATUS_PENDING,
    SYMBOL_STATUS_SYMBOLIZED,
)
from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent, SymbolArtifact, SymbolizationJob, Tenant
from androidapm_server.db.query import load_window_facts, resolve_issue_fingerprint
from androidapm_server.db.symbolization import (
    claim_symbolization_batch,
    enqueue_symbolization_jobs,
    mark_symbolization_failed,
    mark_symbolized,
    mark_symbols_missing,
    requeue_matching_missing_jobs,
    resolve_artifact,
    start_symbolization_attempt,
)
from androidapm_server.domain import (
    ApmEvent,
    IngestMetadata,
    NativeFrameIdentity,
    OccurrenceContext,
)
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.query import build_top_fingerprints
from androidapm_server.query_auth import QueryPrincipal
from androidapm_server.symbolization import SymbolizationFailure, _run_tool, symbolize_job

TEST_KEY_RING = InstallationHmacKeyRing.parse(
    '{"v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}',
    "v1",
)


async def test_completed_symbols_group_issues_without_changing_replay_identity(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    events = [
        crash_event("alias-a"),
        crash_event("alias-b").model_copy(
            update={"fields": {"stackTrace": "at b.b(SourceFile:1)"}}
        ),
    ]
    canonical = "c" * 64
    async with factory() as session:
        await insert_batch(session, metadata(), events, TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(), events)
        artifact = SymbolArtifact(
            tenant_id="tenant-a",
            artifact_type=ARTIFACT_TYPE_JAVA_MAPPING,
            app_id="com.example",
            version_code="42",
            app_build="20260716.1",
            variant="release",
            abi="",
            build_id="",
            checksum_sha256="a" * 64,
            size_bytes=1,
            storage_key="synthetic",
            uploaded_by="ci",
        )
        session.add(artifact)
        await session.commit()
        jobs = await claim_symbolization_batch(session, "owner", 2, 120)
        assert len(jobs) == 2
        for job in jobs:
            assert not await mark_symbolized(
                session, "stale", job, artifact.id, {}, "d" * 64, "retrace", "test"
            )
            assert await mark_symbolized(
                session,
                "owner",
                job,
                artifact.id,
                {"symbolizedStack": "Real.run(Real.kt:1)"},
                canonical,
                "retrace",
                "test",
            )
        await session.commit()
    principal = QueryPrincipal("tenant-a", "key", "com.example", "production", "viewer", None)
    start, end = 1_699_999_999_999, 1_700_000_000_001
    async with factory() as session:
        rows = (await session.scalars(select(InboxEvent))).all()
        assert {row.incident_fingerprint for row in rows} == {canonical}
        assert len({row.raw_incident_fingerprint for row in rows}) == 2
        assert (await insert_batch(session, metadata(), events, TEST_KEY_RING)).duplicates == 2
        facts = await load_window_facts(session, principal, start, end, 100)
        result = build_top_fingerprints("query", principal, facts, "1.0", start, end, 10)
        assert len(result.items) == 1 and result.items[0].event_count == 2
        for row in rows:
            assert row.raw_incident_fingerprint is not None
            assert (
                await resolve_issue_fingerprint(
                    session, principal, row.raw_incident_fingerprint, start, end
                )
                == canonical
            )
            assert (
                await resolve_issue_fingerprint(
                    session, principal, row.raw_incident_fingerprint, end, end + 1
                )
                == row.raw_incident_fingerprint
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


def metadata(*, complete: bool = True) -> IngestMetadata:
    return IngestMetadata(
        request_id="request-1",
        tenant_id="tenant-a",
        app_id="com.example",
        environment="production",
        schema_version="1",
        sdk_version="0.1.0",
        app_version="1.0",
        app_build="20260716.1" if complete else None,
        version_code="42" if complete else None,
        variant="release" if complete else None,
        protocol="protobuf",
    )


def crash_event(
    event_id: str,
    *,
    native: bool = False,
    complete: bool = True,
) -> ApmEvent:
    fields: dict[str, object]
    if native:
        fields = {"backtrace": "#00 pc 00001234 libsample.so"}
        if complete:
            fields |= {"abi": "arm64-v8a", "buildId": "0123456789abcdef"}
    else:
        fields = {"stackTrace": "at a.a(SourceFile:1)"} if complete else {}
    return ApmEvent(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="crash",
        name="native_crash" if native else "java_crash",
        kind="ALERT",
        severity="FATAL",
        priority="CRITICAL",
        process_name="com.example",
        thread_name="main",
        fields=fields,
        occurrence=(
            OccurrenceContext(
                service_version="1.0",
                version_code="42",
                app_build="20260716.1",
                variant="release",
                installation_id="symbolization-installation",
                native_frames=(
                    (
                        NativeFrameIdentity(
                            abi="arm64-v8a",
                            module_build_id="0123456789abcdef",
                            module_name="libsample.so",
                            module_relative_pc=0x1234,
                        ),
                    )
                    if native
                    else ()
                ),
            )
            if complete
            else None
        ),
    )


@pytest.mark.asyncio
async def test_complete_crash_waits_for_symbols_but_missing_metadata_exports_raw(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        complete_event = crash_event("complete")
        incomplete_event = crash_event("incomplete", complete=False)
        await insert_batch(session, metadata(), [complete_event], TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(), [complete_event])
        await insert_batch(session, metadata(complete=False), [incomplete_event], TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(complete=False), [incomplete_event])
        await session.commit()

    async with factory() as session:
        complete_inbox = await session.scalar(
            select(InboxEvent).where(InboxEvent.event_id == "complete")
        )
        incomplete_inbox = await session.scalar(
            select(InboxEvent).where(InboxEvent.event_id == "incomplete")
        )
        assert complete_inbox is not None
        assert incomplete_inbox is not None
        assert complete_inbox.status == INBOX_STATUS_AWAITING_SYMBOLS
        assert complete_inbox.symbolization_job is not None
        assert complete_inbox.symbolization_job.status == SYMBOL_STATUS_PENDING
        assert incomplete_inbox.status == INBOX_STATUS_PENDING
        assert incomplete_inbox.symbolization_job is not None
        assert incomplete_inbox.symbolization_job.status == SYMBOL_STATUS_METADATA_MISSING


@pytest.mark.asyncio
async def test_disabled_symbolizer_records_status_without_blocking_raw_export(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event = crash_event("disabled")
    async with factory() as session:
        await insert_batch(session, metadata(), [event], TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(), [event], enabled=False)
        await session.commit()
    async with factory() as session:
        inbox = await session.scalar(select(InboxEvent))
        assert inbox is not None and inbox.status == INBOX_STATUS_PENDING
        assert inbox.symbolization_job is not None
        assert inbox.symbolization_job.status == "disabled"


@pytest.mark.asyncio
async def test_missing_artifact_is_requeued_then_symbolized_by_active_owner(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event = crash_event("java-1")
    async with factory() as session:
        await insert_batch(session, metadata(), [event], TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(), [event])
        await session.commit()
    async with factory() as session:
        jobs = await claim_symbolization_batch(session, "owner-a", 1, 60, datetime.now(UTC))
        await session.commit()
    assert len(jobs) == 1
    assert jobs[0].attempt_count == 0
    async with factory() as session:
        assert await resolve_artifact(session, jobs[0]) is None
        assert await mark_symbols_missing(session, "owner-a", jobs[0].id, "missing")
        await session.commit()
    async with factory() as session:
        periodic = await claim_symbolization_batch(
            session,
            "periodic-owner",
            1,
            60,
            datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=61),
        )
        assert len(periodic) == 1
        assert periodic[0].attempt_count == 0
        assert await mark_symbols_missing(
            session, "periodic-owner", periodic[0].id, "still missing"
        )
        await session.commit()

    identity = ArtifactIdentity(
        ARTIFACT_TYPE_JAVA_MAPPING,
        "tenant-a",
        "com.example",
        "42",
        "20260716.1",
        "release",
    )
    async with factory() as session:
        artifact = SymbolArtifact(
            tenant_id="tenant-a",
            artifact_type=ARTIFACT_TYPE_JAVA_MAPPING,
            app_id="com.example",
            version_code="42",
            app_build="20260716.1",
            variant="release",
            abi="",
            build_id="",
            checksum_sha256="a" * 64,
            size_bytes=100,
            storage_key="artifact.txt",
            uploaded_by="ci-key",
        )
        session.add(artifact)
        await session.flush()
        assert await requeue_matching_missing_jobs(session, identity) == 1
        await session.commit()
    async with factory() as session:
        requeued = await claim_symbolization_batch(session, "owner-b", 1, 60)
        await session.commit()
    assert len(requeued) == 1
    async with factory() as session:
        assert not await mark_symbolized(
            session,
            "stale-owner",
            requeued[0],
            artifact.id,
            {"symbolizedStack": "Real.run(Real.kt:10)"},
            "b" * 64,
            "retrace",
            "8.13",
        )
        assert await mark_symbolized(
            session,
            "owner-b",
            requeued[0],
            artifact.id,
            {"symbolizedStack": "Real.run(Real.kt:10)"},
            "b" * 64,
            "retrace",
            "8.13",
        )
        await session.commit()
    async with factory() as session:
        inbox = await session.scalar(select(InboxEvent).where(InboxEvent.event_id == "java-1"))
        job = await session.scalar(select(SymbolizationJob))
        assert inbox is not None and inbox.status == INBOX_STATUS_PENDING
        assert job is not None and job.status == SYMBOL_STATUS_SYMBOLIZED


@pytest.mark.asyncio
async def test_exact_native_identity_does_not_fall_back_to_version_only(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event = crash_event("native-1", native=True)
    async with factory() as session:
        await insert_batch(session, metadata(), [event], TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(), [event])
        wrong = SymbolArtifact(
            tenant_id="tenant-a",
            artifact_type=ARTIFACT_TYPE_NATIVE_ELF,
            app_id="com.example",
            version_code="42",
            app_build="20260716.1",
            variant="release",
            abi="arm64-v8a",
            build_id="ffffffffffffffff",
            checksum_sha256="c" * 64,
            size_bytes=100,
            storage_key="wrong.elf",
            uploaded_by="ci-key",
        )
        session.add(wrong)
        await session.commit()
    async with factory() as session:
        job = await session.scalar(
            select(SymbolizationJob).options(selectinload(SymbolizationJob.inbox_event))
        )
        assert job is not None and job.job_type == SYMBOL_JOB_NATIVE
        assert await resolve_artifact(session, job) is None


@pytest.mark.asyncio
async def test_tool_adapters_use_argv_and_produce_stable_fingerprints(tmp_path: Path) -> None:
    mapping_path = tmp_path / "mapping.txt"
    mapping_path.write_text("com.example.Real -> a:\n", encoding="utf-8")
    inbox = InboxEvent(
        tenant_id="tenant-a",
        event_id="java-tool",
        app_id="com.example",
        environment="production",
        schema_version="1",
        sdk_version="0.1.0",
        app_build="build",
        version_code="42",
        variant="release",
        protocol="protobuf",
        event_timestamp_ms=1_700_000_000_000,
        occurrence_timestamp_ms=1_700_000_000_000,
        release_identity_quality="OCCURRENCE_BOUND",
        payload_json={"fields": {"stackTrace": "at a.a(SourceFile:1)"}},
        payload_sha256="d" * 64,
        request_id="request",
    )
    stack = "at a.a(SourceFile:1)"
    job = SymbolizationJob(
        inbox_event=inbox,
        inbox_event_id=1,
        tenant_id="tenant-a",
        event_id="java-tool",
        job_type=SYMBOL_JOB_JAVA,
        input_sha256=hashlib.sha256(stack.encode()).hexdigest(),
    )
    artifact = SymbolArtifact(
        id=1,
        tenant_id="tenant-a",
        artifact_type=ARTIFACT_TYPE_JAVA_MAPPING,
        app_id="com.example",
        version_code="42",
        app_build="build",
        variant="release",
        checksum_sha256="e" * 64,
        size_bytes=mapping_path.stat().st_size,
        storage_key="mapping.txt",
        uploaded_by="ci",
    )
    command = json.dumps(
        [
            sys.executable,
            "-c",
            "import sys; print(sys.stdin.read().replace('a.a', 'com.example.Real.run'))",
        ]
    )
    first = await symbolize_job(
        job,
        artifact,
        mapping_path,
        command,
        "test-r8",
        "[]",
        "unused",
        5,
    )
    second = await symbolize_job(
        job,
        artifact,
        mapping_path,
        command,
        "test-r8",
        "[]",
        "unused",
        5,
    )
    assert "com.example.Real.run" in first.result_json["symbolizedStack"]
    assert first.fingerprint_sha256 == second.fingerprint_sha256

    native_inbox = InboxEvent(
        tenant_id="tenant-a",
        event_id="native-tool",
        app_id="com.example",
        environment="production",
        schema_version="1",
        sdk_version="0.1.0",
        app_build="build",
        version_code="42",
        variant="release",
        protocol="protobuf",
        event_timestamp_ms=1_700_000_000_000,
        occurrence_timestamp_ms=1_700_000_000_000,
        release_identity_quality="OCCURRENCE_BOUND",
        payload_json={"fields": {"backtrace": "not a native frame"}},
        payload_sha256="f" * 64,
        request_id="request",
    )
    native_stack = "not a native frame"
    native_job = SymbolizationJob(
        inbox_event=native_inbox,
        inbox_event_id=2,
        tenant_id="tenant-a",
        event_id="native-tool",
        job_type=SYMBOL_JOB_NATIVE,
        input_sha256=hashlib.sha256(native_stack.encode()).hexdigest(),
    )
    with pytest.raises(SymbolizationFailure, match="module-relative pc"):
        await symbolize_job(
            native_job,
            artifact,
            mapping_path,
            "[]",
            "unused",
            command,
            "test-llvm",
            5,
        )


@pytest.mark.asyncio
async def test_expired_symbol_claim_cannot_start_park_succeed_or_fail(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    event = crash_event("expired")
    async with factory() as session:
        await insert_batch(session, metadata(), [event], TEST_KEY_RING)
        await enqueue_symbolization_jobs(session, metadata(), [event])
        await session.commit()
    observed = datetime.now(UTC)
    expired = observed + timedelta(seconds=60)
    async with factory() as session:
        jobs = await claim_symbolization_batch(session, "old-claim", 1, 60, observed)
        await session.commit()
    job = jobs[0]
    async with factory() as session:
        assert not await start_symbolization_attempt(session, "old-claim", job, 3, expired)
        assert not await mark_symbols_missing(session, "old-claim", job.id, "missing", expired)
        assert not await mark_symbolization_failed(
            session, "old-claim", job, False, 3, "failed", "failure", expired
        )
        assert not await mark_symbolized(
            session, "old-claim", job, 1, {}, "b" * 64, "retrace", "test", expired
        )
        await session.commit()
    async with factory() as session:
        current = (await claim_symbolization_batch(session, "new-claim", 1, 60, expired))[0]
        assert current.attempt_count == 0
        assert await start_symbolization_attempt(session, "new-claim", current, 3, expired)
        await session.commit()
    async with factory() as session:
        assert not await mark_symbolization_failed(
            session, "old-claim", job, False, 3, "stale", "failure", expired
        )
        assert await mark_symbolization_failed(
            session, "new-claim", current, False, 3, "failed", "failure", expired
        )
        await session.commit()
    async with factory() as session:
        inbox = await session.scalar(select(InboxEvent))
        assert inbox is not None and inbox.status == INBOX_STATUS_PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [True, False])
async def test_tool_process_is_killed_and_reaped_on_cancellation_or_timeout(
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    created = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []
    real_spawn = asyncio.create_subprocess_exec

    async def observe_spawn(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await real_spawn(*args, **kwargs)
        processes.append(process)
        created.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe_spawn)
    task = asyncio.create_task(
        _run_tool(
            [sys.executable, "-c", "import time; time.sleep(30)"], b"", 60 if cancel else 0.05
        )
    )
    try:
        await asyncio.wait_for(created.wait(), timeout=10)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=10)
        else:
            with pytest.raises(SymbolizationFailure) as error:
                await asyncio.wait_for(task, timeout=10)
            assert error.value.code == "tool_timeout" and error.value.retryable
        assert len(processes) == 1 and processes[0].returncode is not None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
