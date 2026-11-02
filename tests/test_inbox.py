from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent
from androidapm_server.domain import (
    ApmEvent,
    IngestMetadata,
    NativeFrameIdentity,
    OccurrenceContext,
)
from androidapm_server.errors import ApiError
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.otlp import build_logs_request

KEYS_V1 = InstallationHmacKeyRing.parse(
    '{"v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}',
    "v1",
)
KEYS_V2 = InstallationHmacKeyRing.parse(
    '{"v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",'
    '"v2":"ZmVkY2JhOTg3NjU0MzIxMGZlZGNiYTk4NzY1NDMyMTA="}',
    "v2",
)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        yield database_session
    await engine.dispose()


def event(event_id: str, name: str = "request") -> ApmEvent:
    return ApmEvent(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="network",
        name=name,
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        process_name="com.example",
        thread_name="main",
    )


def occurrence_event(event_id: str, installation_id: str = "same-installation") -> ApmEvent:
    """Create one event whose release and installation were frozen before persistence."""
    return event(event_id).model_copy(
        update={
            "occurrence": OccurrenceContext(
                service_version="1.0.0",
                version_code="10",
                app_build="build-10",
                variant="release",
                installation_id=installation_id,
            )
        }
    )


def metadata(tenant_id: str = "tenant-a", *, app_build: str | None = None) -> IngestMetadata:
    return IngestMetadata(
        request_id="request-1",
        tenant_id=tenant_id,
        app_id="com.example",
        environment="test",
        schema_version="1",
        sdk_version="0.1.0",
        app_build=app_build,
        protocol="protobuf",
    )


@pytest.mark.asyncio
async def test_session_identity_is_tenant_scoped_redacted_and_replay_safe(
    session: AsyncSession,
) -> None:
    plaintext = "5b3bd3a7-08ea-4f07-9a47-e04ecaeef009"
    original = occurrence_event("session-event")
    assert original.occurrence is not None
    original = original.model_copy(
        update={
            "occurrence": original.occurrence.model_copy(
                update={
                    "session_id": plaintext,
                    "device_model": "Example/Model",
                    "os_version": "14/API 34",
                }
            ),
            "fields": {"nested": {"secret": plaintext}, "safe": 1},
        }
    )
    await insert_batch(session, metadata(), [original], KEYS_V1)
    await insert_batch(session, metadata("tenant-b"), [original], KEYS_V1)
    await session.commit()
    replay = await insert_batch(session, metadata(), [original], KEYS_V2)
    assert replay.duplicates == 1
    rows = (await session.scalars(select(InboxEvent).order_by(InboxEvent.tenant_id))).all()
    assert rows[0].session_hmac != rows[1].session_hmac
    assert rows[0].session_identity_quality == "OCCURRENCE_BOUND"
    assert rows[0].session_hmac_key_version == "v1"
    assert rows[0].device_model == "Example/Model" and rows[0].os_version == "14/API 34"
    assert plaintext not in json.dumps([row.payload_json for row in rows])
    assert plaintext not in json.dumps([row.normalized_json for row in rows])
    assert KEYS_V1.digest("tenant-a", plaintext) != rows[0].session_hmac
    changed = original.model_copy(
        update={
            "occurrence": original.occurrence.model_copy(
                update={"session_id": "b67f341b-d22d-4978-8a40-a036ae61cb16"}
            )
        }
    )
    with pytest.raises(ApiError, match="conflict"):
        await insert_batch(session, metadata(), [changed], KEYS_V2)


@pytest.mark.asyncio
async def test_optional_context_absence_preserves_pre_extension_payload_hash(
    session: AsyncSession,
) -> None:
    import hashlib

    original = occurrence_event("legacy-v3")
    assert original.occurrence is not None
    legacy = original.model_dump(mode="json")
    for key in ("session_id", "device_model", "os_version", "installation_id"):
        legacy["occurrence"].pop(key, None)
    expected = hashlib.sha256(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    await insert_batch(session, metadata(), [original], KEYS_V1)
    await session.commit()
    row = await session.scalar(select(InboxEvent).where(InboxEvent.event_id == "legacy-v3"))
    assert row is not None and row.payload_sha256 == expected
    assert row.session_identity_quality == "ABSENT" and row.session_hmac is None
    assert (await insert_batch(session, metadata(), [original], KEYS_V2)).duplicates == 1


@pytest.mark.asyncio
async def test_replay_is_acknowledged_without_second_row(session: AsyncSession) -> None:
    first = await insert_batch(session, metadata(), [event("one"), event("two")])
    await session.commit()
    second = await insert_batch(session, metadata(), [event("one"), event("two")])
    await session.commit()

    count = await session.scalar(select(func.count()).select_from(InboxEvent))
    assert first.inserted == 2
    assert second.inserted == 0
    assert second.duplicates == 2
    assert count == 2


@pytest.mark.asyncio
async def test_same_event_id_isolated_by_tenant(session: AsyncSession) -> None:
    await insert_batch(session, metadata("tenant-a"), [event("same")])
    await session.commit()
    result = await insert_batch(session, metadata("tenant-b"), [event("same")])
    await session.commit()
    assert result.inserted == 1


@pytest.mark.asyncio
async def test_conflicting_identity_rejects_without_insert(session: AsyncSession) -> None:
    await insert_batch(session, metadata(), [event("existing")])
    await session.commit()

    with pytest.raises(ApiError) as caught:
        await insert_batch(session, metadata(), [event("new"), event("existing", "changed")])
    await session.rollback()

    ids = list(await session.scalars(select(InboxEvent.event_id).order_by(InboxEvent.event_id)))
    assert caught.value.code == "event_id_conflict"
    assert ids == ["existing"]


@pytest.mark.asyncio
async def test_conflict_inside_batch_rejects_all(session: AsyncSession) -> None:
    with pytest.raises(ApiError):
        await insert_batch(session, metadata(), [event("same"), event("same", "changed")])
    await session.rollback()
    assert await session.scalar(select(func.count()).select_from(InboxEvent)) == 0


@pytest.mark.asyncio
async def test_replay_with_different_build_identity_is_not_silently_deduplicated(
    session: AsyncSession,
) -> None:
    await insert_batch(session, metadata(app_build="build-one"), [event("same")])
    await session.commit()
    with pytest.raises(ApiError) as caught:
        await insert_batch(session, metadata(app_build="build-two"), [event("same")])
    await session.rollback()
    assert caught.value.code == "event_id_conflict"


@pytest.mark.asyncio
async def test_installation_hmac_is_tenant_scoped_and_plaintext_free(
    session: AsyncSession,
) -> None:
    plaintext = "shared-installation"
    await insert_batch(
        session,
        metadata("tenant-a"),
        [occurrence_event("tenant-a-event", plaintext)],
        KEYS_V1,
    )
    await insert_batch(
        session,
        metadata("tenant-b"),
        [occurrence_event("tenant-b-event", plaintext)],
        KEYS_V1,
    )
    await session.commit()

    rows = (await session.scalars(select(InboxEvent).order_by(InboxEvent.tenant_id))).all()
    assert len(rows) == 2
    assert rows[0].installation_hmac != rows[1].installation_hmac
    assert all(plaintext not in str(row.payload_json) for row in rows)


@pytest.mark.asyncio
async def test_replay_uses_stored_hmac_key_version_after_active_key_rotation(
    session: AsyncSession,
) -> None:
    item = occurrence_event("rotated-key-replay")
    first = await insert_batch(session, metadata(), [item], KEYS_V1)
    await session.commit()
    replay = await insert_batch(session, metadata(), [item], KEYS_V2)
    await session.commit()

    row = await session.scalar(
        select(InboxEvent).where(InboxEvent.event_id == "rotated-key-replay")
    )
    assert first.inserted == 1
    assert replay.duplicates == 1
    assert row is not None
    assert row.installation_hmac_key_version == "v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("occurrence_bound", [True, False])
async def test_every_durable_projection_uses_minimized_evidence(
    session: AsyncSession,
    occurrence_bound: bool,
) -> None:
    plaintext = "synthetic-installation-private"
    item = occurrence_event("private-projections", plaintext).model_copy(
        update={
            "module": "crash",
            "name": "java_crash",
            "scene": f"scene/{plaintext}",
            "fields": {
                "threadName": plaintext,
                "exceptionMessage": f"user={plaintext}",
                "stackTrace": "at app.Safe.run(Safe.java:20)",
                "nested": [plaintext, {"value": f"prefix/{plaintext}"}, "safe"],
                f"key/{plaintext}": "secret-key",
            },
        }
    )
    assert item.occurrence is not None
    item = item.model_copy(
        update={
            "occurrence": item.occurrence.model_copy(
                update={
                    "native_frames": (
                        NativeFrameIdentity(
                            abi="arm64-v8a",
                            module_build_id="abcd1234",
                            module_name=plaintext,
                            module_relative_pc=12,
                        ),
                    )
                }
            )
        }
    )
    meta = metadata()
    if not occurrence_bound:
        meta = meta.model_copy(update={"installation_id": plaintext})
        item = item.model_copy(update={"occurrence": None})
    await insert_batch(session, meta, [item], KEYS_V1)
    await session.commit()
    row = await session.scalar(select(InboxEvent))
    assert row is not None
    for document in (row.payload_json, row.normalized_json, row.native_identity_json):
        assert plaintext not in json.dumps(document)
    assert row.normalized_json["field_states"]["threadName"] == "MISSING"
    assert row.payload_json["fields"]["nested"] == [{}, "safe"]
    assert row.native_identity_json == []
    assert plaintext.encode() not in build_logs_request([row]).SerializeToString()
    assert plaintext in str(item.fields)  # No mutation of the caller's event.
    assert (await insert_batch(session, meta, [item], KEYS_V2)).duplicates == 1


@pytest.mark.asyncio
async def test_installation_in_required_identity_rejects_whole_batch(session: AsyncSession) -> None:
    item = occurrence_event("conflicting-identity", "synthetic-installation").model_copy(
        update={"thread_name": "thread/synthetic-installation"}
    )
    with pytest.raises(ApiError, match="Required identity"):
        await insert_batch(session, metadata(), [event("healthy"), item], KEYS_V1)
    assert await session.scalar(select(func.count()).select_from(InboxEvent)) == 0
