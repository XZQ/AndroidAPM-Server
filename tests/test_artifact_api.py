from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.api.ingest import get_settings
from androidapm_server.artifacts import NativeElfIdentity
from androidapm_server.auth import generate_ingest_key
from androidapm_server.ci_auth import generate_ci_key
from androidapm_server.config import Settings
from androidapm_server.constants import CI_SCOPE_ARTIFACT_WRITE
from androidapm_server.db.base import Base
from androidapm_server.db.models import AuditLog, CiKey, IngestKey, SymbolArtifact, Tenant
from androidapm_server.db.session import get_session
from androidapm_server.main import create_app

MAPPING = b"com.example.RealClass -> a:\n    1:1:void run():10:10 -> a\n"


@pytest_asyncio.fixture
async def artifact_api(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession], str, str, Path]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ci_key_id, ci_plaintext, ci_hash = generate_ci_key()
    ingest_key_id, ingest_plaintext, ingest_hash = generate_ingest_key()
    async with factory() as session:
        session.add(Tenant(id="tenant-a", name="Tenant A"))
        session.add(
            CiKey(
                key_id=ci_key_id,
                tenant_id="tenant-a",
                key_hash=ci_hash,
                app_id="com.example",
                scopes_json=[CI_SCOPE_ARTIFACT_WRITE],
            )
        )
        session.add(
            IngestKey(
                key_id=ingest_key_id,
                tenant_id="tenant-a",
                key_hash=ingest_hash,
                app_id="com.example",
                environment="test",
            )
        )
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    artifact_root = tmp_path / "artifacts"
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        environment="test",
        artifact_storage_path=artifact_root,
        max_java_mapping_bytes=1_024,
        max_native_elf_bytes=1_024,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, ci_plaintext, ingest_plaintext, artifact_root
    await engine.dispose()


def artifact_headers(key: str, content: bytes, *, native: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/octet-stream",
        "X-APM-App-Id": "com.example",
        "X-APM-Version-Code": "42",
        "X-APM-App-Build": "20260716.1",
        "X-APM-Variant": "release",
        "X-APM-Checksum-SHA256": hashlib.sha256(content).hexdigest(),
    }
    if native:
        headers["X-APM-ABI"] = "arm64-v8a"
        headers["X-APM-Build-Id"] = "0123456789abcdef"
    return headers


async def test_mapping_identity_accepts_protocol_width(
    artifact_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str, str, Path],
) -> None:
    client, _factory, key, _ingest_key, _root = artifact_api
    headers = artifact_headers(key, MAPPING)
    headers.update({"X-APM-App-Build": "b" * 256, "X-APM-Variant": "r" * 256})
    response = await client.post("/v1/artifacts/java-mapping", headers=headers, content=MAPPING)
    assert response.status_code == 200
    replay = await client.post("/v1/artifacts/java-mapping", headers=headers, content=MAPPING)
    assert replay.json()["duplicate"] is True
    headers["X-APM-App-Build"] += "b"
    assert (
        await client.post("/v1/artifacts/java-mapping", headers=headers, content=MAPPING)
    ).status_code == 400


async def test_packaged_mapping_upload_and_replay(
    artifact_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str, str, Path],
) -> None:
    client, _factory, key, _ingest_key, _root = artifact_api
    content = b"com.example.Real -> com.example.a:\n    void run() -> a\n"
    headers = artifact_headers(key, content)
    first = await client.post("/v1/artifacts/java-mapping", headers=headers, content=content)
    assert first.status_code == 200
    replay = await client.post("/v1/artifacts/java-mapping", headers=headers, content=content)
    assert replay.status_code == 200 and replay.json()["duplicate"]


@pytest.mark.asyncio
async def test_mapping_upload_is_immutable_audited_and_idempotent(
    artifact_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str, str, Path],
) -> None:
    client, factory, ci_key, _ingest_key, artifact_root = artifact_api
    first = await client.post(
        "/v1/artifacts/java-mapping", headers=artifact_headers(ci_key, MAPPING), content=MAPPING
    )
    replay = await client.post(
        "/v1/artifacts/java-mapping", headers=artifact_headers(ci_key, MAPPING), content=MAPPING
    )
    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    assert replay.status_code == 200
    assert replay.json()["duplicate"] is True
    assert replay.json()["artifactId"] == first.json()["artifactId"]
    async with factory() as session:
        artifact = await session.scalar(select(SymbolArtifact))
        assert artifact is not None
        assert (artifact_root / artifact.storage_key).read_bytes() == MAPPING
        assert await session.scalar(select(func.count()).select_from(SymbolArtifact)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 2


@pytest.mark.asyncio
async def test_same_identity_with_different_mapping_is_rejected(
    artifact_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str, str, Path],
) -> None:
    client, factory, ci_key, _ingest_key, _artifact_root = artifact_api
    changed = MAPPING.replace(b"RealClass", b"ChangedClass")
    assert (
        await client.post(
            "/v1/artifacts/java-mapping", headers=artifact_headers(ci_key, MAPPING), content=MAPPING
        )
    ).status_code == 200
    conflict = await client.post(
        "/v1/artifacts/java-mapping", headers=artifact_headers(ci_key, changed), content=changed
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "artifact_identity_conflict"
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(SymbolArtifact)) == 1


@pytest.mark.asyncio
async def test_ingest_key_cannot_upload_artifacts_and_limits_precede_storage(
    artifact_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str, str, Path],
) -> None:
    client, _factory, _ci_key, ingest_key, artifact_root = artifact_api
    unauthorized = await client.post(
        "/v1/artifacts/java-mapping",
        headers=artifact_headers(ingest_key, MAPPING),
        content=MAPPING,
    )
    assert unauthorized.status_code == 401
    oversized = b"x" * 1_025
    rejected = await client.post(
        "/v1/artifacts/java-mapping",
        headers=artifact_headers(_ci_key, oversized),
        content=oversized,
    )
    assert rejected.status_code == 413
    assert not list(artifact_root.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_checksum_mapping_and_native_identity_are_validated(
    artifact_api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str, str, Path],
) -> None:
    client, _factory, ci_key, _ingest_key, _artifact_root = artifact_api
    bad_checksum_headers = artifact_headers(ci_key, MAPPING)
    bad_checksum_headers["X-APM-Checksum-SHA256"] = "0" * 64
    checksum = await client.post(
        "/v1/artifacts/java-mapping", headers=bad_checksum_headers, content=MAPPING
    )
    invalid_mapping = b"not a mapping\n"
    invalid = await client.post(
        "/v1/artifacts/java-mapping",
        headers=artifact_headers(ci_key, invalid_mapping),
        content=invalid_mapping,
    )
    assert checksum.status_code == 400
    assert checksum.json()["code"] == "checksum_mismatch"
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_artifact"

    elf = b"test-elf-placeholder"
    with patch(
        "androidapm_server.api.artifacts.inspect_native_elf",
        new=AsyncMock(return_value=NativeElfIdentity("arm64-v8a", "differentbuildid")),
    ):
        native = await client.post(
            "/v1/artifacts/native-elf",
            headers=artifact_headers(ci_key, elf, native=True),
            content=elf,
        )
    assert native.status_code == 409
    assert native.json()["code"] == "artifact_identity_mismatch"
