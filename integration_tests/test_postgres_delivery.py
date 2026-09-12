from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.artifacts import ArtifactIdentity, StagedArtifact, inspect_native_elf
from androidapm_server.constants import ARTIFACT_TYPE_JAVA_MAPPING
from androidapm_server.db.artifacts import register_artifact
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent, ReleaseDecision, SymbolArtifact, Tenant
from androidapm_server.db.worker import claim_batch
from androidapm_server.domain import ApmEvent, IngestMetadata
from androidapm_server.errors import ApiError

POSTGRES_URL = os.environ.get("APM_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="APM_TEST_POSTGRES_URL is required")


@pytest.mark.asyncio
async def test_linux_toolchain_fixture_has_verified_unstripped_elf_identity(
    tmp_path: Path,
) -> None:
    output = tmp_path / "native-fixture"
    await asyncio.to_thread(
        subprocess.run,
        ["/usr/bin/cc", "-g", "-Wl,--build-id=sha1", "-x", "c", "-", "-o", str(output)],
        input=b"int sample(void) { return 42; } int main(void) { return sample(); }",
        check=True,
    )
    identity = await inspect_native_elf(output)
    assert identity.abi == "x86_64"
    assert len(identity.build_id) == 40


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


async def test_postgres_accepts_full_protocol_release_identity(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = f"width-{uuid.uuid4().hex}"
    value = "v" * 256
    try:
        async with factory() as session:
            session.add(Tenant(id=tenant, name="Synthetic width"))
            await session.flush()
            meta = metadata(tenant, "width").model_copy(
                update={"app_version": value, "app_build": value, "variant": value}
            )
            await insert_batch(session, meta, [event("width")])
            session.add(
                SymbolArtifact(
                    tenant_id=tenant,
                    artifact_type=ARTIFACT_TYPE_JAVA_MAPPING,
                    app_id=meta.app_id,
                    version_code="42",
                    app_build=value,
                    variant=value,
                    abi="",
                    build_id="",
                    checksum_sha256="a" * 64,
                    size_bytes=1,
                    storage_key="synthetic-width",
                    uploaded_by="test",
                )
            )
            session.add(
                ReleaseDecision(
                    tenant_id=tenant,
                    app_id=meta.app_id,
                    environment=meta.environment,
                    release_version=value,
                    decision="continue",
                    actor="test",
                    evidence_from_ms=1,
                    evidence_to_ms=2,
                    reason="synthetic width test",
                    evidence_json={},
                )
            )
            await session.commit()
        async with factory() as session:
            row = await session.scalar(select(InboxEvent).where(InboxEvent.tenant_id == tenant))
            assert row is not None and (row.app_version, row.app_build, row.variant) == (
                value,
                value,
                value,
            )
    finally:
        async with factory() as session:
            for model in (ReleaseDecision, SymbolArtifact, InboxEvent):
                await session.execute(delete(model).where(model.tenant_id == tenant))
            await session.execute(delete(Tenant).where(Tenant.id == tenant))
            await session.commit()


@pytest.mark.asyncio
async def test_concurrent_artifact_identity_has_one_fact_and_detects_conflict(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant_id = f"integration-{uuid.uuid4().hex}"
    async with factory() as session:
        session.add(Tenant(id=tenant_id, name="Integration"))
        await session.commit()
    identity = ArtifactIdentity(
        ARTIFACT_TYPE_JAVA_MAPPING,
        tenant_id,
        "integration.test",
        "42",
        "build-1",
        "release",
    )

    async def submit(checksum: str) -> tuple[bool, bool]:
        async with factory() as session:
            registration = await register_artifact(
                session,
                identity,
                StagedArtifact(Path("unused"), checksum, 100),
                identity.storage_key(checksum, "txt"),
                "integration-ci",
            )
            await session.commit()
            return registration.inserted, registration.conflict

    same = await asyncio.gather(submit("a" * 64), submit("a" * 64))
    assert sorted(same) == [(False, False), (True, False)]
    conflict = await submit("b" * 64)
    assert conflict == (False, True)
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(SymbolArtifact)
            .where(SymbolArtifact.tenant_id == tenant_id)
        )
        await session.execute(delete(SymbolArtifact).where(SymbolArtifact.tenant_id == tenant_id))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()
    assert count == 1


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
