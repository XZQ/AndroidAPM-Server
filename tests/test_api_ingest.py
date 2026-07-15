from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.api.ingest import get_settings
from androidapm_server.auth import generate_ingest_key
from androidapm_server.config import Settings
from androidapm_server.db.base import Base
from androidapm_server.db.models import InboxEvent, IngestKey, RemoteConfigVersion, Tenant
from androidapm_server.db.session import get_session
from androidapm_server.main import create_app
from androidapm_server.remote_config import (
    generate_signing_keypair,
    sign_config,
    verify_envelope,
)

LINE = (
    "ts=1700000000000|eventId=api-event-1|module=network|name=request|kind=METRIC|"
    "severity=INFO|priority=NORMAL|process=com.example|thread=main|fields=durationMs=42"
)


@pytest_asyncio.fixture
async def api() -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession], str]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    key_id, plaintext, key_hash = generate_ingest_key()
    async with factory() as session:
        session.add(Tenant(id="tenant-a", name="Tenant A"))
        session.add(
            IngestKey(
                key_id=key_id,
                tenant_id="tenant-a",
                key_hash=key_hash,
                app_id="com.example",
                environment="test",
            )
        )
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        environment="test",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, plaintext
    await engine.dispose()


def headers(key: str, environment: str = "test") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "text/plain; charset=utf-8",
        "X-APM-Schema-Version": "1",
        "X-APM-App-Id": "com.example",
        "X-APM-Environment": environment,
        "X-APM-SDK-Version": "0.1.0",
    }


@pytest.mark.asyncio
async def test_success_means_durable_insert_and_replay_is_duplicate(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = api
    first = await client.post("/v1/events", headers=headers(key), content=LINE)
    second = await client.post("/v1/events", headers=headers(key), content=LINE)

    assert first.status_code == 200
    assert first.json() | {"requestId": "ignored"} == {
        "requestId": "ignored",
        "status": "accepted",
        "received": 1,
        "inserted": 1,
        "duplicates": 0,
    }
    assert second.status_code == 200
    assert second.json()["duplicates"] == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(InboxEvent)) == 1


@pytest.mark.asyncio
async def test_invalid_event_rejects_whole_request_without_insert(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = api
    response = await client.post(
        "/v1/events",
        headers=headers(key),
        content=LINE + "\n" + LINE.replace("eventId=api-event-1", "eventId="),
    )
    assert response.status_code == 422
    assert response.json()["eventIndex"] == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(InboxEvent)) == 0


@pytest.mark.asyncio
async def test_authentication_and_scope_are_enforced(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, _factory, key = api
    missing = await client.post("/v1/events", headers=headers("invalid"), content=LINE)
    wrong_scope = await client.post(
        "/v1/events", headers=headers(key, environment="production"), content=LINE
    )
    assert missing.status_code == 401
    assert wrong_scope.status_code == 403


@pytest.mark.asyncio
async def test_health_and_metrics_are_exposed(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, _factory, _key = api
    assert (await client.get("/health/live")).status_code == 200
    assert "androidapm_ingest_requests_total" in (await client.get("/metrics")).text


@pytest.mark.asyncio
async def test_remote_config_is_signed_scoped_and_etag_cacheable(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = api
    private_key, public_key = generate_signing_keypair()
    issued = datetime.now(UTC)
    signed = sign_config(
        private_key,
        1,
        issued,
        issued + timedelta(hours=1),
        10_000,
        {"modules": {"network": {"enabled": False}}},
        "key-1",
    )
    async with factory() as session:
        session.add(
            RemoteConfigVersion(
                tenant_id="tenant-a",
                app_id="com.example",
                environment="test",
                revision=1,
                payload_json=signed.payload,
                issued_at=issued,
                expires_at=issued + timedelta(hours=1),
                rollout_basis_points=10_000,
                key_id="key-1",
                signature_b64=signed.signature_b64,
                created_by="test",
            )
        )
        await session.commit()

    config_headers = {**headers(key), "X-APM-Installation-Id": "installation-1"}
    response = await client.get("/v1/config", headers=config_headers)
    assert response.status_code == 200
    verify_envelope(public_key, response.json())
    cached = await client.get(
        "/v1/config",
        headers={**config_headers, "If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304
