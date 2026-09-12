from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.auth import generate_ingest_key
from androidapm_server.config import Settings, get_settings
from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.maintenance import maintain_once
from androidapm_server.db.models import AuditLog, InboxEvent, QueryKey, ReleaseDecision, Tenant
from androidapm_server.db.session import get_session
from androidapm_server.domain import (
    ApmEvent,
    EventKind,
    EventPriority,
    EventSeverity,
    IdentityQuality,
    IngestMetadata,
    OccurrenceContext,
)
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.main import create_app
from androidapm_server.query_auth import generate_query_key

TEST_HMAC_KEYS_JSON = '{"v1":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}'
TEST_CURSOR_KEY_B64 = base64.b64encode(b"query-cursor-test-key-material-32-bytes").decode()
NOW_MS = int(datetime.now(UTC).timestamp() * 1_000)
FROM_MS = NOW_MS - 60 * 60 * 1_000
TO_MS = NOW_MS + 60 * 1_000


@pytest_asyncio.fixture
async def query_api() -> AsyncIterator[
    tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ]
]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    credentials: dict[str, str] = {}
    async with factory() as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A"),
                Tenant(id="tenant-b", name="Tenant B"),
            ]
        )
        for label, tenant_id, role in (
            ("viewer-a", "tenant-a", "viewer"),
            ("investigator-a", "tenant-a", "investigator"),
            ("investigator-b", "tenant-b", "investigator"),
        ):
            key_id, plaintext, key_hash = generate_query_key()
            credentials[label] = plaintext
            session.add(
                QueryKey(
                    key_id=key_id,
                    tenant_id=tenant_id,
                    key_hash=key_hash,
                    app_id="com.example",
                    environment="production",
                    role=role,
                )
            )
        await session.commit()

    key_ring = InstallationHmacKeyRing.parse(TEST_HMAC_KEYS_JSON, "v1")
    async with factory() as session:
        await _seed_release_data(session, key_ring)
        await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        environment="test",
        installation_hmac_keys_json=TEST_HMAC_KEYS_JSON,
        query_cursor_hmac_key_b64=TEST_CURSOR_KEY_B64,
        query_max_rows=100,
        query_max_limit=100,
    )
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, credentials, key_ring
    await engine.dispose()


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_actual_key_rotation_is_visible_to_query_projection(
    query_api: tuple[
        AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], InstallationHmacKeyRing
    ],
) -> None:
    client, factory, credentials, old_keys = query_api
    rotated = InstallationHmacKeyRing(
        {**old_keys.keys, "v2": b"synthetic-rotation-key-32-bytes!!!"}, "v2"
    )
    async with factory() as session:
        await insert_batch(
            session,
            _metadata("tenant-a"),
            [
                _event(
                    "rotated-health",
                    "core",
                    "sdk_health",
                    {"emitCount": 5, "dropCount": 0, "dropRate": 0},
                    "3.0.0",
                    "installation-zero",
                )
            ],
            rotated,
        )
        await session.commit()
    response = await client.get(
        "/v1/query/release-health",
        headers=auth(credentials["viewer-a"]),
        params=window_params(newRelease="3.0.0", baselineRelease="1.0.0"),
    )
    assert response.status_code == 200
    metric = response.json()["newRelease"]["metrics"]["activeInstallations"]
    assert metric["value"] is None
    assert metric["reason"] == "INSTALLATION_HMAC_CONTINUITY_BREAK"


def window_params(**extra: object) -> dict[str, object]:
    return {"fromMs": FROM_MS, "toMs": TO_MS, **extra}


async def test_expired_raw_is_reported_and_audited_instead_of_returning_a_stub(
    query_api: tuple[
        AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], InstallationHmacKeyRing
    ],
) -> None:
    client, factory, credentials, _ = query_api
    async with factory() as session:
        await session.execute(
            update(InboxEvent)
            .where(InboxEvent.event_id == "new-crash-1")
            .values(status="delivered", finalized_at=datetime.now(UTC) - timedelta(days=8))
        )
        await session.commit()
    assert (await maintain_once(factory, Settings(_env_file=None)))[0] == 1
    metadata = await client.get(
        "/v1/query/events/new-crash-1", headers=auth(credentials["investigator-a"])
    )
    assert metadata.status_code == 200 and metadata.json()["rawAvailable"] is False
    assert metadata.json()["fieldStates"]["stackTrace"] == "EXPIRED"
    fingerprint = metadata.json()["event"]["incidentFingerprint"]
    issue = await client.get(
        f"/v1/query/issues/{fingerprint}",
        headers=auth(credentials["viewer-a"]),
        params=window_params(),
    )
    assert issue.status_code == 200
    scenes = issue.json()["scenes"]
    assert scenes["state"] == "DEGRADED" and scenes["reason"] == "SCENE_EVIDENCE_EXPIRED"
    assert scenes["coverage"] == {
        "totalEventCount": 2,
        "availableEventCount": 1,
        "missingEventCount": 0,
        "expiredEventCount": 1,
        "retentionUnknownEventCount": 0,
    }
    raw = await client.post(
        "/v1/query/events/new-crash-1/raw",
        headers=auth(credentials["investigator-a"]),
        json={"purposeCode": "incident_diagnosis", "reason": "Investigate expired raw evidence"},
    )
    assert raw.status_code == 410
    assert raw.json()["code"] == "raw_evidence_expired"
    async with factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "query.event.raw.read")
        )
        assert audit is not None and audit.result == "expired"


@pytest.mark.asyncio
async def test_release_health_uses_exact_occurrence_events_and_explicit_states(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, _, credentials, _ = query_api
    response = await client.get(
        "/v1/query/release-health",
        headers=auth(credentials["viewer-a"]),
        params=window_params(newRelease="2.0.0", baselineRelease="1.0.0"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == {
        "tenantId": "tenant-a",
        "appId": "com.example",
        "environment": "production",
    }
    new_release = body["newRelease"]
    assert new_release["declaredSampleCount"] == 7
    assert new_release["eligibleSampleCount"] == 6
    assert new_release["excludedNonOccurrenceCount"] == 1
    assert new_release["state"] == "DEGRADED"
    assert new_release["metrics"]["javaCrashEvents"]["value"] == 2
    assert new_release["metrics"]["anrEvents"]["value"] == 1
    assert new_release["metrics"]["activeInstallations"]["value"] == 2
    assert new_release["metrics"]["affectedInstallations"]["value"] == 2
    assert new_release["metrics"]["affectedInstallationRatio"]["value"] is None
    assert (
        new_release["metrics"]["affectedInstallationRatio"]["reason"]
        == "RELEASE_IDENTITY_COVERAGE_INCOMPLETE"
    )
    assert new_release["metrics"]["crashFreeSessions"] == {
        "state": "UNAVAILABLE",
        "value": None,
        "numerator": None,
        "denominator": None,
        "sampleCount": 6,
        "coverage": None,
        "asOfMs": TO_MS,
        "source": "durable_inbox",
        "reason": "SESSION_ID_NOT_PROVIDED",
    }
    assert body["baselineRelease"]["metrics"]["javaCrashEvents"]["value"] == 1
    assert body["comparison"]["javaCrashEventDelta"] == 1
    assert (
        sum(
            point["newJavaCrashEvents"]
            for point in body["trend"]
            if point["newJavaCrashEvents"] is not None
        )
        == 2
    )
    assert (
        sum(point["newAnrEvents"] for point in body["trend"] if point["newAnrEvents"] is not None)
        == 1
    )
    assert (
        sum(
            point["baselineJavaCrashEvents"]
            for point in body["trend"]
            if point["baselineJavaCrashEvents"] is not None
        )
        == 1
    )
    assert len(body["trend"]) <= 24
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_data_quality_and_fingerprints_distinguish_real_zero_and_missing_data(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, _, credentials, _ = query_api
    quality = await client.get(
        "/v1/query/data-quality",
        headers=auth(credentials["viewer-a"]),
        params=window_params(releaseVersion="2.0.0"),
    )
    assert quality.status_code == 200, quality.text
    quality_body = quality.json()
    assert quality_body["state"] == "DEGRADED"
    assert quality_body["releaseIdentity"]["coverage"] == pytest.approx(6 / 7)
    assert quality_body["sdkHealth"]["state"] == "ZERO"
    assert quality_body["lateData"]["state"] == "ZERO"

    fingerprints = await client.get(
        "/v1/query/fingerprints",
        headers=auth(credentials["viewer-a"]),
        params=window_params(releaseVersion="2.0.0"),
    )
    assert fingerprints.status_code == 200, fingerprints.text
    fingerprint_body = fingerprints.json()
    assert fingerprint_body["state"] == "PRESENT"
    assert fingerprint_body["sampleCount"] == 3
    assert [item["eventCount"] for item in fingerprint_body["items"]] == [2, 1]

    missing = await client.get(
        "/v1/query/fingerprints",
        headers=auth(credentials["viewer-a"]),
        params=window_params(releaseVersion="9.9.9"),
    )
    assert missing.status_code == 200
    assert missing.json()["state"] == "NO_DATA"
    assert missing.json()["items"] == []

    real_zero = await client.get(
        "/v1/query/release-health",
        headers=auth(credentials["viewer-a"]),
        params=window_params(newRelease="3.0.0", baselineRelease="1.0.0"),
    )
    assert real_zero.status_code == 200, real_zero.text
    zero_slice = real_zero.json()["newRelease"]
    assert zero_slice["state"] == "UNAVAILABLE"
    assert zero_slice["metrics"]["javaCrashEvents"]["state"] == "ZERO"
    assert zero_slice["metrics"]["javaCrashEvents"]["value"] == 0

    unavailable = await client.get(
        "/v1/query/release-health",
        headers=auth(credentials["viewer-a"]),
        params=window_params(newRelease="4.0.0", baselineRelease="1.0.0"),
    )
    assert unavailable.status_code == 200, unavailable.text
    unavailable_slice = unavailable.json()["newRelease"]
    assert unavailable_slice["state"] == "UNAVAILABLE"
    assert unavailable_slice["metrics"]["javaCrashEvents"]["value"] is None
    assert (
        unavailable_slice["metrics"]["javaCrashEvents"]["reason"]
        == "OCCURRENCE_IDENTITY_NOT_PROVIDED"
    )


@pytest.mark.asyncio
async def test_issue_detail_aggregates_trend_and_only_supported_dimensions(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, _, credentials, _ = query_api
    fingerprints = await client.get(
        "/v1/query/fingerprints",
        headers=auth(credentials["viewer-a"]),
        params=window_params(releaseVersion="2.0.0"),
    )
    assert fingerprints.status_code == 200, fingerprints.text
    fingerprint = fingerprints.json()["items"][0]["fingerprint"]

    response = await client.get(
        f"/v1/query/issues/{fingerprint}",
        headers=auth(credentials["viewer-a"]),
        params=window_params(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "PRESENT"
    assert body["eventFamily"] == "JAVA_CRASH"
    assert body["eventCount"] == 2
    assert body["affectedInstallationCount"] == 2
    assert (
        sum(point["eventCount"] for point in body["trend"] if point["eventCount"] is not None) == 2
    )
    assert len(body["trend"]) <= 24
    assert body["releases"]["items"] == [
        {"label": "2.0.0", "eventCount": 2, "affectedInstallationCount": 2}
    ]
    assert body["scenes"]["items"] == [
        {"label": "CheckoutActivity", "eventCount": 2, "affectedInstallationCount": 2}
    ]
    assert body["deviceModels"] == {
        "dimension": "device_model",
        "state": "UNKNOWN_COVERAGE",
        "reason": "STANDARD_DEVICE_RESOURCE_NOT_PROVIDED",
        "items": [],
        "coverage": None,
    }
    assert body["androidVersions"]["state"] == "UNKNOWN_COVERAGE"
    assert response.headers["cache-control"] == "private, no-store"

    cross_tenant = await client.get(
        f"/v1/query/issues/{fingerprint}",
        headers=auth(credentials["investigator-b"]),
        params=window_params(),
    )
    assert cross_tenant.status_code == 200
    assert cross_tenant.json()["state"] == "NO_DATA"
    assert cross_tenant.json()["eventCount"] == 0

    invalid = await client.get(
        "/v1/query/issues/not-a-fingerprint",
        headers=auth(credentials["viewer-a"]),
        params=window_params(),
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_query_filter"


@pytest.mark.asyncio
async def test_viewer_cannot_read_l1_l2_or_write_release_decisions(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, _, credentials, _ = query_api
    event_list = await client.get(
        "/v1/query/events",
        headers=auth(credentials["viewer-a"]),
        params=window_params(),
    )
    assert event_list.status_code == 403
    assert event_list.json()["code"] == "insufficient_query_role"

    raw = await client.post(
        "/v1/query/events/new-crash-1/raw",
        headers=auth(credentials["viewer-a"]),
        json={"purposeCode": "incident_diagnosis", "reason": "Investigate release crash"},
    )
    assert raw.status_code == 403

    decision = await client.post(
        "/v1/query/release-decisions",
        headers=auth(credentials["viewer-a"]),
        json=_decision_body(),
    )
    assert decision.status_code == 403


@pytest.mark.parametrize("release", ["v" * 256, "版" * 85 + "a"])
async def test_full_width_release_can_be_queried_and_recorded(
    query_api: tuple[
        AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], InstallationHmacKeyRing
    ],
    release: str,
) -> None:
    client, _factory, credentials, _keys = query_api
    headers = auth(credentials["investigator-a"])
    health = await client.get(
        "/v1/query/release-health",
        headers=headers,
        params=window_params(newRelease=release, baselineRelease="baseline"),
    )
    assert health.status_code == 200
    assert health.json()["newRelease"]["releaseVersion"] == release
    for endpoint in ("fingerprints", "data-quality", "events"):
        response = await client.get(
            f"/v1/query/{endpoint}", headers=headers, params=window_params(releaseVersion=release)
        )
        assert response.status_code == 200
    body = _decision_body()
    body["releaseVersion"] = release
    decision = await client.post("/v1/query/release-decisions", headers=headers, json=body)
    assert decision.status_code == 201
    assert decision.json()["releaseVersion"] == release
    listed = await client.get(
        "/v1/query/release-decisions", headers=headers, params={"releaseVersion": release}
    )
    assert listed.status_code == 200 and listed.json()["items"][0]["releaseVersion"] == release
    rejected = await client.get(
        "/v1/query/fingerprints",
        headers=headers,
        params=window_params(releaseVersion=release + "a"),
    )
    assert rejected.status_code == 400


async def test_old_issue_links_resolve_with_scope_and_reject_split_aliases(
    query_api: tuple[
        AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], InstallationHmacKeyRing
    ],
) -> None:
    client, factory, credentials, keys = query_api
    events = [
        _event(
            f"alias-{index}",
            "crash",
            "java_crash",
            {"stackTrace": f"at alias.Class{index}.run(SourceFile:1)"},
            "2.0.0",
            f"alias-installation-{index}",
        )
        for index in range(2)
    ]
    canonical = "d" * 64
    async with factory() as session:
        await insert_batch(session, _metadata("tenant-a"), events, keys)
        rows = list(
            (
                await session.scalars(
                    select(InboxEvent).where(
                        InboxEvent.event_id.in_([event.event_id for event in events])
                    )
                )
            ).all()
        )
        alias = rows[0].raw_incident_fingerprint
        assert alias is not None
        for row in rows:
            row.incident_fingerprint = canonical
        await session.commit()
    headers = auth(credentials["investigator-a"])
    detail = await client.get(f"/v1/query/issues/{alias}", headers=headers, params=window_params())
    assert detail.status_code == 200
    assert detail.json()["fingerprint"] == canonical and detail.json()["eventCount"] == 2
    page = await client.get(
        "/v1/query/events", headers=headers, params=window_params(fingerprint=alias)
    )
    assert {item["eventId"] for item in page.json()["items"]} == {
        event.event_id for event in events
    }
    foreign = await client.get(
        f"/v1/query/issues/{alias}",
        headers=auth(credentials["investigator-b"]),
        params=window_params(),
    )
    assert foreign.status_code == 200 and foreign.json()["state"] == "NO_DATA"
    async with factory() as session:
        row = await session.scalar(select(InboxEvent).where(InboxEvent.event_id == "alias-1"))
        assert row is not None
        row.raw_incident_fingerprint = alias
        row.incident_fingerprint = "e" * 64
        await session.commit()
    split = await client.get(f"/v1/query/issues/{alias}", headers=headers, params=window_params())
    assert split.status_code == 409 and split.json()["code"] == "ambiguous_issue_fingerprint"


@pytest.mark.asyncio
async def test_event_pagination_is_allow_listed_audited_and_filter_bound(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, factory, credentials, _ = query_api
    first = await client.get(
        "/v1/query/events",
        headers=auth(credentials["investigator-a"]),
        params=window_params(releaseVersion="2.0.0", limit=1),
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 1
    assert body["nextCursor"] is not None
    assert "rawPayload" not in body["items"][0]
    assert "payloadJson" not in body["items"][0]

    second = await client.get(
        "/v1/query/events",
        headers=auth(credentials["investigator-a"]),
        params=window_params(
            releaseVersion="2.0.0",
            limit=1,
            cursor=body["nextCursor"],
        ),
    )
    assert second.status_code == 200, second.text
    assert second.json()["items"][0]["eventId"] != body["items"][0]["eventId"]

    rebound = await client.get(
        "/v1/query/events",
        headers=auth(credentials["investigator-a"]),
        params=window_params(
            releaseVersion="1.0.0",
            limit=1,
            cursor=body["nextCursor"],
        ),
    )
    assert rebound.status_code == 400
    assert rebound.json()["code"] == "invalid_cursor"

    tampered = body["nextCursor"][:-1] + ("A" if body["nextCursor"][-1] != "A" else "B")
    invalid = await client.get(
        "/v1/query/events",
        headers=auth(credentials["investigator-a"]),
        params=window_params(releaseVersion="2.0.0", limit=1, cursor=tampered),
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_cursor"

    async with factory() as session:
        audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "query.events.list")
        )
    assert audit_count == 2


@pytest.mark.asyncio
async def test_raw_read_requires_purpose_commits_audit_and_never_crosses_tenant(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, factory, credentials, _ = query_api
    invalid_purpose = await client.post(
        "/v1/query/events/new-crash-1/raw",
        headers=auth(credentials["investigator-a"]),
        json={"purposeCode": "curiosity", "reason": "This reason is long enough"},
    )
    assert invalid_purpose.status_code == 400
    assert invalid_purpose.json()["code"] == "invalid_purpose_code"

    raw = await client.post(
        "/v1/query/events/new-crash-1/raw",
        headers=auth(credentials["investigator-a"]),
        json={
            "purposeCode": "incident_diagnosis",
            "reason": "Investigate the new release crash",
        },
    )
    assert raw.status_code == 200, raw.text
    assert raw.json()["rawPayload"]["fields"]["stackTrace"].startswith("at com.example")
    assert "installation-new-a" not in raw.text
    async with factory() as session:
        audit = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "query.event.raw.read",
                AuditLog.object_id == "new-crash-1",
                AuditLog.tenant_id == "tenant-a",
            )
        )
        assert audit is not None
        assert audit.result == "success"
        assert audit.details_json["purpose_code"] == "incident_diagnosis"
        assert "stackTrace" not in str(audit.details_json)

    cross_tenant = await client.post(
        "/v1/query/events/new-crash-1/raw",
        headers=auth(credentials["investigator-b"]),
        json={
            "purposeCode": "incident_diagnosis",
            "reason": "Investigate an event in my tenant",
        },
    )
    assert cross_tenant.status_code == 404


@pytest.mark.asyncio
async def test_scope_parameters_limits_windows_and_row_budget_are_enforced(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, factory, credentials, key_ring = query_api
    forbidden = await client.get(
        "/v1/query/data-quality",
        headers=auth(credentials["viewer-a"]),
        params=window_params(tenantId="tenant-b"),
    )
    assert forbidden.status_code == 400
    assert forbidden.json()["code"] == "scope_parameter_forbidden"

    oversized_window = await client.get(
        "/v1/query/data-quality",
        headers=auth(credentials["viewer-a"]),
        params={"fromMs": NOW_MS - 32 * 86_400_000, "toMs": NOW_MS},
    )
    assert oversized_window.status_code == 422
    assert oversized_window.json()["code"] == "query_window_exceeded"

    oversized_limit = await client.get(
        "/v1/query/events",
        headers=auth(credentials["investigator-a"]),
        params=window_params(limit=101),
    )
    assert oversized_limit.status_code == 422
    assert oversized_limit.json()["code"] == "query_limit_exceeded"

    async with factory() as session:
        events = [
            _event(
                f"budget-{index}",
                "network",
                "request",
                {},
                "3.0.0",
                f"budget-installation-{index}",
            )
            for index in range(94)
        ]
        await insert_batch(session, _metadata("tenant-a"), events, key_ring)
        await session.commit()
    budget = await client.get(
        "/v1/query/data-quality",
        headers=auth(credentials["viewer-a"]),
        params=window_params(),
    )
    assert budget.status_code == 422
    assert budget.json()["code"] == "query_budget_exceeded"
    filtered = await client.get(
        "/v1/query/data-quality",
        headers=auth(credentials["viewer-a"]),
        params=window_params(releaseVersion="2.0.0"),
    )
    assert filtered.status_code == 200
    assert filtered.json()["sampleCount"] == 7
    page = await client.get(
        "/v1/query/events",
        headers=auth(credentials["investigator-a"]),
        params=window_params(module="crash", name="java_crash", releaseVersion="2.0.0"),
    )
    assert page.status_code == 200
    assert len(page.json()["items"]) == 3


async def test_sql_reduction_counts_more_events_than_the_projection_budget(
    query_api: tuple[
        AsyncClient, async_sessionmaker[AsyncSession], dict[str, str], InstallationHmacKeyRing
    ],
) -> None:
    client, factory, credentials, key_ring = query_api
    async with factory() as session:
        await insert_batch(
            session,
            _metadata("tenant-a"),
            [
                _event(
                    f"bulk-{i}",
                    "crash",
                    "java_crash",
                    {"stackTrace": "at app.Safe.run(Safe.java:10)"},
                    "5.0.0",
                    "bulk-installation",
                )
                for i in range(500)
            ],
            key_ring,
        )
        await session.commit()
    result = await client.get(
        "/v1/query/release-health",
        headers=auth(credentials["viewer-a"]),
        params=window_params(newRelease="5.0.0", baselineRelease="1.0.0"),
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["newRelease"]["eligibleSampleCount"] == 500
    assert body["newRelease"]["metrics"]["javaCrashEvents"]["value"] == 500
    assert body["newRelease"]["metrics"]["activeInstallations"]["value"] == 1
    assert (
        sum(
            point["newJavaCrashEvents"]
            for point in body["trend"]
            if point["newJavaCrashEvents"] is not None
        )
        == 500
    )
    quality = await client.get(
        "/v1/query/data-quality",
        headers=auth(credentials["viewer-a"]),
        params=window_params(releaseVersion="5.0.0"),
    )
    assert quality.status_code == 200
    assert quality.json()["sampleCount"] == 500
    assert quality.json()["schemaVersionCounts"] == {"3": 500}


@pytest.mark.asyncio
async def test_release_decisions_are_human_scoped_persisted_and_readable(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, factory, credentials, _ = query_api
    created = await client.post(
        "/v1/query/release-decisions",
        headers=auth(credentials["investigator-a"]),
        json=_decision_body(),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["decision"] == "pause"
    assert body["actor"].startswith("query-key:")

    listed = await client.get(
        "/v1/query/release-decisions",
        headers=auth(credentials["investigator-a"]),
        params={"releaseVersion": "2.0.0"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == [body]

    other_tenant = await client.get(
        "/v1/query/release-decisions",
        headers=auth(credentials["investigator-b"]),
    )
    assert other_tenant.status_code == 200
    assert other_tenant.json()["items"] == []
    async with factory() as session:
        assert await session.scalar(select(func.count(ReleaseDecision.id))) == 1
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "release_decision.record")
        )
        assert audit is not None
        assert audit.details_json["decision"] == "pause"


@pytest.mark.asyncio
async def test_query_credential_audience_and_missing_cursor_key_fail_closed(
    query_api: tuple[
        AsyncClient,
        async_sessionmaker[AsyncSession],
        dict[str, str],
        InstallationHmacKeyRing,
    ],
) -> None:
    client, _, credentials, _ = query_api
    _, ingest_plaintext, _ = generate_ingest_key()
    wrong_audience = await client.get(
        "/v1/query/data-quality",
        headers=auth(ingest_plaintext),
        params=window_params(),
    )
    assert wrong_audience.status_code == 401

    app = client._transport.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        environment="test",
        installation_hmac_keys_json=TEST_HMAC_KEYS_JSON,
        query_max_rows=100,
    )
    page = await client.get(
        "/v1/query/events",
        headers=auth(credentials["investigator-a"]),
        params=window_params(limit=1),
    )
    assert page.status_code == 503
    assert page.json()["code"] == "query_cursor_unavailable"


async def _seed_release_data(
    session: AsyncSession,
    key_ring: InstallationHmacKeyRing,
) -> None:
    events = [
        _event(
            "baseline-health",
            "core",
            "sdk_health",
            {"emitCount": 10, "dropCount": 0, "dropRate": 0.0},
            "1.0.0",
            "installation-baseline",
        ),
        _event(
            "baseline-crash",
            "crash",
            "java_crash",
            {"stackTrace": "at com.example.Baseline.run(Baseline.kt:10)"},
            "1.0.0",
            "installation-baseline",
        ),
        _event(
            "new-health",
            "core",
            "sdk_health",
            {"emitCount": 20, "dropCount": 0, "dropRate": 0.0},
            "2.0.0",
            "installation-new-a",
        ),
        _event(
            "new-network",
            "network",
            "request",
            {"durationMs": 42.0},
            "2.0.0",
            "installation-new-b",
        ),
        _event(
            "new-crash-1",
            "crash",
            "java_crash",
            {"stackTrace": "at com.example.New.run(New.kt:20)"},
            "2.0.0",
            "installation-new-a",
        ),
        _event(
            "new-crash-2",
            "crash",
            "java_crash",
            {"stackTrace": "at com.example.New.run(New.kt:99)"},
            "2.0.0",
            "installation-new-b",
        ),
        _event(
            "new-anr",
            "anr",
            "anr_detected",
            {"mainThreadStack": "at com.example.Main.block(Main.kt:30)"},
            "2.0.0",
            "installation-new-b",
        ),
        _event(
            "new-normal-exit",
            "crash",
            "app_exit",
            {"reasonName": "EXIT_SELF", "exitTimestamp": NOW_MS - 4 * 60 * 1_000},
            "2.0.0",
            "installation-new-a",
        ),
        _event(
            "zero-health",
            "core",
            "sdk_health",
            {"emitCount": 5, "dropCount": 0, "dropRate": 0.0},
            "3.0.0",
            "installation-zero",
        ),
    ]
    await insert_batch(session, _metadata("tenant-a"), events, key_ring)
    batch_declared = _event(
        "new-batch-declared-crash",
        "crash",
        "java_crash",
        {"stackTrace": "at com.example.Legacy.run(Legacy.kt:1)"},
        "2.0.0",
        "legacy-installation",
        occurrence_bound=False,
    )
    await insert_batch(
        session,
        _metadata(
            "tenant-a",
            app_version="2.0.0",
            release_quality=IdentityQuality.BATCH_DECLARED,
            installation_id="legacy-installation",
            installation_quality=IdentityQuality.BATCH_DECLARED,
        ),
        [batch_declared],
        key_ring,
    )
    batch_only = _event(
        "batch-only-health",
        "core",
        "sdk_health",
        {"emitCount": 3, "dropCount": 0, "dropRate": 0.0},
        "4.0.0",
        "batch-only-installation",
        occurrence_bound=False,
    )
    await insert_batch(
        session,
        _metadata(
            "tenant-a",
            app_version="4.0.0",
            release_quality=IdentityQuality.BATCH_DECLARED,
            installation_id="batch-only-installation",
            installation_quality=IdentityQuality.BATCH_DECLARED,
        ),
        [batch_only],
        key_ring,
    )
    await insert_batch(
        session,
        _metadata("tenant-b"),
        [
            _event(
                "tenant-b-crash",
                "crash",
                "java_crash",
                {"stackTrace": "at com.example.Other.run(Other.kt:1)"},
                "2.0.0",
                "tenant-b-installation",
            )
        ],
        key_ring,
    )


def _metadata(
    tenant_id: str,
    *,
    app_version: str | None = None,
    release_quality: IdentityQuality = IdentityQuality.ABSENT,
    installation_id: str | None = None,
    installation_quality: IdentityQuality = IdentityQuality.ABSENT,
) -> IngestMetadata:
    return IngestMetadata(
        request_id=f"seed-{tenant_id}",
        tenant_id=tenant_id,
        app_id="com.example",
        environment="production",
        schema_version="3" if app_version is None else "2",
        sdk_version="0.1.0",
        app_version=app_version,
        protocol="protobuf-envelope-v3" if app_version is None else "protobuf-envelope-v2",
        release_identity_quality=release_quality,
        installation_identity_quality=installation_quality,
        installation_id=installation_id,
    )


def _event(
    event_id: str,
    module: str,
    name: str,
    fields: dict[str, object],
    release_version: str,
    installation_id: str,
    *,
    occurrence_bound: bool = True,
) -> ApmEvent:
    occurrence = (
        OccurrenceContext(
            service_version=release_version,
            version_code=release_version.replace(".", ""),
            app_build=f"build-{release_version}",
            variant="release",
            installation_id=installation_id,
        )
        if occurrence_bound
        else None
    )
    return ApmEvent(
        timestamp=NOW_MS - 5 * 60 * 1_000,
        event_id=event_id,
        module=module,
        name=name,
        kind=EventKind.ALERT
        if (module, name) in {("crash", "java_crash"), ("anr", "anr_detected")}
        else EventKind.METRIC,
        severity=EventSeverity.ERROR if module in {"crash", "anr"} else EventSeverity.INFO,
        priority=EventPriority.CRITICAL if module in {"crash", "anr"} else EventPriority.NORMAL,
        process_name="com.example",
        thread_name="main",
        scene="CheckoutActivity",
        foreground=True,
        fields=fields,
        occurrence=occurrence,
    )


def _decision_body() -> dict[str, object]:
    return {
        "releaseVersion": "2.0.0",
        "decision": "pause",
        "evidenceFromMs": FROM_MS,
        "evidenceToMs": TO_MS,
        "reason": "Crash count increased during the controlled rollout",
        "evidence": {
            "releaseState": "DEGRADED",
            "baselineState": "PRESENT",
            "javaCrashEvents": 2,
            "anrEvents": 1,
            "affectedInstallations": 2,
            "activeInstallations": 2,
            "affectedInstallationRatio": 1.0,
            "dataQualityState": "DEGRADED",
            "queryRequestId": "query-request-from-release-health",
        },
    }
