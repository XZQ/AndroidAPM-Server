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
from androidapm_server.generated.apm_event_pb2 import ApmBatchEnvelope
from androidapm_server.main import create_app
from androidapm_server.protocol.envelope_v2 import stable_batch_id
from androidapm_server.protocol.envelope_v3 import stable_batch_id_v3
from androidapm_server.remote_config import (
    generate_signing_keypair,
    sign_config,
    verify_envelope,
)

LINE = (
    "ts=1700000000000|eventId=api-event-1|module=network|name=request|kind=METRIC|"
    "severity=INFO|priority=NORMAL|process=com.example|thread=main|fields=durationMs=42"
)
TEST_HMAC_KEYS_JSON = '{"v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}'


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
        installation_hmac_keys_json=TEST_HMAC_KEYS_JSON,
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


@pytest.mark.parametrize("capacity_kind", ["rows", "bytes"])
async def test_capacity_rejects_new_batch_without_ack_and_still_accepts_replay(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
    capacity_kind: str,
) -> None:
    client, factory, key = api
    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        environment="test",
        inbox_max_live_events=1 if capacity_kind == "rows" else 100,
        inbox_max_raw_bytes=1024 if capacity_kind == "bytes" else 1024**3,
        installation_hmac_keys_json=TEST_HMAC_KEYS_JSON,
    )
    first_headers, first_body = v2_request(key)
    first = await client.post("/v1/events", headers=first_headers, content=first_body)
    assert first.status_code == 200
    second_headers, second_body = v2_request(key, event_id="capacity-second")
    if capacity_kind == "bytes":
        envelope = ApmBatchEnvelope.FromString(second_body)
        envelope.events[0].typed_fields["largeUnknownField"].type = "STRING"
        envelope.events[0].typed_fields["largeUnknownField"].value = "x" * 2000
        second_body = envelope.SerializeToString()
    rejected = await client.post("/v1/events", headers=second_headers, content=second_body)
    assert rejected.status_code == 503
    assert rejected.json()["retryable"]
    assert rejected.headers["retry-after"] == "30"
    assert "x-apm-batch-id" not in rejected.headers
    replay = await client.post("/v1/events", headers=first_headers, content=first_body)
    assert replay.status_code == 200 and replay.json()["duplicates"] == 1
    async with factory() as session:
        assert await session.scalar(select(func.count(InboxEvent.id))) == 1


def v2_request(
    key: str,
    event_id: str = "api-v2-event-1",
    resource_environment: str = "test",
) -> tuple[dict[str, str], bytes]:
    """Build a complete versioned request using the generated wire schema."""
    batch_id = stable_batch_id([event_id])
    envelope = ApmBatchEnvelope(
        schema_version=2,
        sdk_name="android-apm",
        sdk_version="0.1.0",
        batch_id=batch_id,
        sent_at_ms=1_700_000_000_100,
    )
    envelope.resource.service_name = "com.example"
    envelope.resource.service_version = "2.4.1"
    envelope.resource.deployment_environment = resource_environment
    envelope.resource.installation_id = "anonymous-installation"
    event = envelope.events.add(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="network",
        name="request",
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        process_name="com.example",
        thread_name="main",
    )
    event.typed_fields["durationMs"].type = "DOUBLE"
    event.typed_fields["durationMs"].value = "42.5"
    return (
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": ("application/x-protobuf; message=ApmBatchEnvelope; version=2"),
            "X-Apm-Schema-Version": "2",
            "X-Apm-Sdk-Version": "0.1.0",
            "X-Apm-App-Id": "com.example",
            "X-Apm-Environment": "test",
            "X-Apm-Batch-Id": batch_id,
            "X-Apm-Event-Count": "1",
        },
        envelope.SerializeToString(),
    )


def v3_request(
    key: str,
    event_id: str = "api-v3-event-1",
    *,
    occurrence_version: str = "1.9.0",
) -> tuple[dict[str, str], bytes]:
    """Build a complete occurrence-bound V3 request from the generated schema."""
    batch_id = stable_batch_id_v3([event_id])
    envelope = ApmBatchEnvelope(
        schema_version=3,
        sdk_name="android-apm",
        sdk_version="0.1.0",
        batch_id=batch_id,
        sent_at_ms=1_700_000_000_100,
    )
    envelope.resource.service_name = "com.example"
    envelope.resource.service_version = "2.0.0"
    envelope.resource.deployment_environment = "test"
    event = envelope.events.add(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="crash",
        name="java_crash",
        kind="ALERT",
        severity="FATAL",
        priority="CRITICAL",
        process_name="com.example",
        thread_name="main",
    )
    event.typed_fields["stackTrace"].type = "STRING"
    event.typed_fields["stackTrace"].value = "at a.a(SourceFile:1)"
    event.occurrence.service_version = occurrence_version
    event.occurrence.version_code = "19"
    event.occurrence.app_build = "build-19"
    event.occurrence.variant = "release"
    event.occurrence.installation_id = "v3-anonymous-installation"
    return (
        {
            "Authorization": f"Bearer {key}",
            "Content-Type": ("application/x-protobuf; message=ApmBatchEnvelope; version=3"),
            "X-Apm-Schema-Version": "3",
            "X-Apm-Sdk-Version": "0.1.0",
            "X-Apm-App-Id": "com.example",
            "X-Apm-Environment": "test",
            "X-Apm-Batch-Id": batch_id,
            "X-Apm-Event-Count": "1",
        },
        envelope.SerializeToString(),
    )


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
async def test_v2_returns_exact_ack_headers_and_replay_is_duplicate(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = api
    request_headers, body = v2_request(key)
    first = await client.post("/v1/events", headers=request_headers, content=body)
    second = await client.post("/v1/events", headers=request_headers, content=body)

    assert first.status_code == 200
    assert first.headers["X-Apm-Schema-Version"] == "2"
    assert first.headers["X-Apm-Batch-Id"] == request_headers["X-Apm-Batch-Id"]
    assert first.headers["X-Apm-Event-Count"] == "1"
    assert second.status_code == 200
    assert second.json()["duplicates"] == 1
    async with factory() as session:
        rows = (await session.scalars(select(InboxEvent))).all()
        assert len(rows) == 1
        assert rows[0].protocol == "protobuf_envelope_v2"
        assert rows[0].app_version == "2.4.1"
        assert rows[0].payload_json["fields"]["durationMs"] == 42.5
        assert rows[0].payload_json["field_types"]["durationMs"] == "DOUBLE"
        assert "anonymous-installation" not in str(rows[0].payload_json)
        assert rows[0].installation_hmac is not None
        assert rows[0].installation_hmac_key_version == "v1"
        assert rows[0].release_identity_quality == "BATCH_DECLARED"


@pytest.mark.asyncio
async def test_v3_returns_exact_ack_and_persists_only_occurrence_pseudonym(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = api
    request_headers, body = v3_request(key)
    first = await client.post("/v1/events", headers=request_headers, content=body)
    replay = await client.post("/v1/events", headers=request_headers, content=body)

    assert first.status_code == 200
    assert first.headers["X-Apm-Schema-Version"] == "3"
    assert first.headers["X-Apm-Batch-Id"] == request_headers["X-Apm-Batch-Id"]
    assert first.headers["X-Apm-Event-Count"] == "1"
    assert replay.status_code == 200
    assert replay.json()["duplicates"] == 1
    async with factory() as session:
        row = await session.scalar(select(InboxEvent))
        assert row is not None
        assert row.protocol == "protobuf_envelope_v3"
        assert row.app_version == "1.9.0"
        assert row.app_build == "build-19"
        assert row.version_code == "19"
        assert row.release_identity_quality == "OCCURRENCE_BOUND"
        assert row.installation_identity_quality == "OCCURRENCE_BOUND"
        assert row.installation_hmac is not None
        assert row.installation_hmac_key_version == "v1"
        assert "v3-anonymous-installation" not in str(row.payload_json)


@pytest.mark.asyncio
async def test_v3_replay_with_changed_occurrence_identity_is_a_conflict(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = api
    request_headers, body = v3_request(key, event_id="v3-conflict")
    accepted = await client.post("/v1/events", headers=request_headers, content=body)
    changed_headers, changed_body = v3_request(
        key,
        event_id="v3-conflict",
        occurrence_version="2.0.0",
    )
    conflict = await client.post(
        "/v1/events",
        headers=changed_headers,
        content=changed_body,
    )

    assert accepted.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "event_id_conflict"
    async with factory() as session:
        row = await session.scalar(select(InboxEvent))
        assert row is not None and row.app_version == "1.9.0"


@pytest.mark.asyncio
async def test_v2_header_or_resource_mismatch_rejects_whole_request(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = api
    request_headers, body = v2_request(key)
    request_headers["X-Apm-Event-Count"] = "2"
    count_mismatch = await client.post(
        "/v1/events",
        headers=request_headers,
        content=body,
    )
    request_headers, body = v2_request(key, resource_environment="different")
    resource_mismatch = await client.post(
        "/v1/events",
        headers=request_headers,
        content=body,
    )

    assert count_mismatch.status_code == 400
    assert resource_mismatch.status_code == 400
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(InboxEvent)) == 0


@pytest.mark.asyncio
async def test_v2_media_type_and_schema_must_be_selected_together(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession], str],
) -> None:
    client, factory, key = api
    request_headers, body = v2_request(key)
    request_headers["Content-Type"] = "application/x-protobuf"
    legacy_media = await client.post(
        "/v1/events",
        headers=request_headers,
        content=body,
    )
    request_headers, body = v2_request(key)
    request_headers["X-Apm-Schema-Version"] = "1"
    legacy_schema = await client.post(
        "/v1/events",
        headers=request_headers,
        content=body,
    )

    assert legacy_media.status_code == 400
    assert legacy_schema.status_code == 400
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(InboxEvent)) == 0


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
