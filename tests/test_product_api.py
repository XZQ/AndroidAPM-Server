"""Local product acceptance: real persistence, fixed scope, RBAC, CSRF, and transitions."""

from __future__ import annotations

import base64
import importlib.util
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server.alerts import deliver_once, evaluate_scope
from androidapm_server.config import Settings, get_settings
from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import (
    AlertDelivery,
    AlertInstance,
    AuditLog,
    InboxEvent,
    QueryKey,
    Tenant,
)
from androidapm_server.db.session import get_session
from androidapm_server.domain import ApmEvent, IngestMetadata, OccurrenceContext
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.main import create_app
from androidapm_server.normalization import normalize_event
from androidapm_server.performance import percentile
from androidapm_server.performance_catalog import PERFORMANCE_FIELDS
from androidapm_server.query_auth import QueryPrincipal, generate_query_key
from androidapm_server.remote_config import generate_signing_keypair, verify_envelope

Factory = async_sessionmaker[AsyncSession]
BACKFILL_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/backfill_performance.py"
NOW = datetime.now(UTC)
NOW_MS = int(NOW.timestamp() * 1000)
WINDOW = {
    "newRelease": "2.0.0",
    "baselineRelease": "1.0.0",
    "fromMs": NOW_MS - 3600_000,
    "toMs": NOW_MS + 60_000,
}
HMAC = json.dumps({"v1": base64.b64encode(b"local-test-installation-key-32bytes").decode()})
PRINCIPAL = QueryPrincipal("a", "worker", "app", "local", "admin", None)


@pytest_asyncio.fixture
async def product() -> AsyncIterator[tuple[AsyncClient, Factory, dict[str, str], Settings]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    keys = {}
    async with factory() as session:
        session.add_all([Tenant(id="a", name="A"), Tenant(id="b", name="B")])
        for label, tenant, role in (
            ("admin", "a", "admin"),
            ("viewer", "a", "viewer"),
            ("investigator", "a", "investigator"),
            ("other", "b", "admin"),
        ):
            key_id, secret, hashed = generate_query_key()
            keys[label] = secret
            session.add(
                QueryKey(
                    key_id=key_id,
                    tenant_id=tenant,
                    key_hash=hashed,
                    app_id="app",
                    environment="local",
                    role=role,
                )
            )
        await session.commit()
    private, public = generate_signing_keypair()
    settings = Settings(
        environment="test",
        installation_hmac_keys_json=HMAC,
        web_session_hmac_key_b64=base64.b64encode(b"web-test-key-material-32bytesxxxx").decode(),
        remote_config_signing_private_key_b64=private,
        notification_channels_json='{"test":{"url":"http://127.0.0.1:9999/hook"}}',
    )
    keys["public"] = public

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, factory, keys, settings
    await engine.dispose()


def auth(keys: dict[str, str], role: str = "admin") -> dict[str, str]:
    return {"Authorization": f"Bearer {keys[role]}"}


async def emit(
    factory: Factory,
    *,
    tenant: str = "a",
    module: str = "launch",
    name: str = "cold_start",
    values: list[dict[str, Any]] | None = None,
    release: str = "2.0.0",
    at: datetime = NOW,
) -> None:
    key_ring = InstallationHmacKeyRing.parse(HMAC, "v1")
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(AuditLog))
        events = [
            ApmEvent(
                timestamp=int(at.timestamp() * 1000),
                event_id=f"{tenant}-{module}-{count}-{index}-{release}",
                module=module,
                name=name,
                kind="METRIC",
                severity="INFO",
                priority="NORMAL",
                process_name="app",
                thread_name="main",
                fields=fields,
                occurrence=OccurrenceContext(
                    service_version=release,
                    version_code="1",
                    app_build="build",
                    variant="debug",
                    installation_id="installation",
                ),
            )
            for index, fields in enumerate(values or [{"launchDurationMs": 120}])
        ]
        await insert_batch(
            session,
            IngestMetadata(
                request_id="test",
                tenant_id=tenant,
                app_id="app",
                environment="local",
                schema_version="3",
                sdk_version="test",
                protocol="protobuf_v3",
            ),
            events,
            key_ring,
        )
        session.add(
            AuditLog(
                tenant_id=tenant,
                actor="test",
                action="fixture",
                object_type="event",
                result="success",
                details_json={},
            )
        )
        await session.commit()


@pytest.mark.parametrize("field", PERFORMANCE_FIELDS, ids=lambda field: field.id)
def test_every_reviewed_numeric_contract_normalizes_without_raw_indexing(field: Any) -> None:
    event = ApmEvent(
        timestamp=NOW_MS,
        event_id="contract",
        module=field.module,
        name=field.name,
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        process_name="app",
        thread_name="main",
        fields={field.field: 0, "url": "https://secret/path"},
    )
    normalized = normalize_event(event).normalized_json
    assert normalized["registered_fields"][field.field] == 0
    assert "url" not in normalized["indexed_attributes"]
    assert percentile([100, 200, 300, 400], 0.95) == 385


async def test_performance_real_rows_quantiles_missing_zero_and_tenant_scope(product: Any) -> None:
    client, factory, keys, _ = product
    await emit(factory, values=[{"launchDurationMs": value} for value in (100, 200, 300, 400)])
    await emit(factory, release="1.0.0", values=[{"launchDurationMs": 100}])
    await emit(factory, tenant="b", values=[{"launchDurationMs": 99999}])
    response = await client.get(
        "/v1/query/performance", params=WINDOW, headers=auth(keys, "viewer")
    )
    assert response.status_code == 200, response.text
    metric = response.json()["metrics"][0]
    assert metric["current"]["sampleCount"] == 4
    assert metric["current"]["p95"] == 385
    assert metric["p95Delta"] == 285
    assert all(point["current"]["p95"] is None for point in response.json()["trend"][:-2])
    assert "installation" not in response.text and "99999" not in response.text
    assert (
        await client.get(
            "/v1/query/performance", params={**WINDOW, "tenantId": "b"}, headers=auth(keys)
        )
    ).status_code == 400
    await emit(factory, values=[{"launchDurationMs": 0}, {}, {"launchDurationMs": -1}])
    response = await client.get("/v1/query/performance", params=WINDOW, headers=auth(keys))
    observation = response.json()["metrics"][0]["current"]
    assert observation["state"] == "DEGRADED"
    assert observation["missingCount"] == 1 and observation["invalidCount"] == 1
    assert observation["minimum"] == 0


async def test_admin_keys_revoke_never_expose_hash_audit_secret_or_other_scope(
    product: Any,
) -> None:
    client, factory, keys, _ = product
    payload = {"kind": "query", "role": "viewer", "description": "test", "expiresDays": 1}
    for role in ("viewer", "investigator"):
        assert (
            await client.post("/v1/manage/keys", json=payload, headers=auth(keys, role))
        ).status_code == 403
    created = await client.post("/v1/manage/keys", json=payload, headers=auth(keys))
    assert created.status_code == 201, created.text
    value = created.json()
    assert (await client.get("/v1/manage/keys", headers=auth(keys, "other"))).json()["items"][0][
        "id"
    ] != value["id"]
    listed = await client.get("/v1/manage/keys", headers=auth(keys))
    assert value["secret"] not in listed.text and "key_hash" not in listed.text
    assert (
        await client.post(
            f"/v1/manage/keys/query/{value['id']}/revoke", headers=auth(keys, "other")
        )
    ).status_code == 404
    assert (
        await client.post(f"/v1/manage/keys/query/{value['id']}/revoke", headers=auth(keys))
    ).status_code == 200
    assert (
        await client.get(
            "/v1/manage/settings", headers={"Authorization": f"Bearer {value['secret']}"}
        )
    ).status_code == 401
    async with factory() as session:
        audits = (await session.scalars(select(AuditLog))).all()
        assert all(value["secret"] not in json.dumps(row.details_json) for row in audits)


async def test_web_management_csrf_and_scoped_audit(product: Any) -> None:
    client, _, keys, _ = product
    assert (
        await client.post("/v1/web/session", json={"queryKey": keys["admin"]})
    ).status_code == 200
    path = "/v1/manage/settings"
    assert (await client.put(path, json={"displayName": "Sample"})).status_code == 403
    csrf = client.cookies.get("androidapm_csrf")
    assert (
        await client.put(path, json={"displayName": "Sample"}, headers={"X-Apm-Csrf-Token": csrf})
    ).status_code == 200
    assert (await client.get(path)).json()["displayName"] == "Sample"
    assert any(
        item["action"] == "app.settings.update"
        for item in (await client.get("/v1/manage/audit")).json()["items"]
    )


async def test_signed_publish_and_rollback_are_new_revisions(product: Any) -> None:
    client, factory, keys, _ = product
    for payload in ({"apm.enabled": True}, {"apm.enabled": False}):
        assert (
            await client.post("/v1/manage/configs", json={"payload": payload}, headers=auth(keys))
        ).status_code == 201
    rolled = await client.post("/v1/manage/configs", json={"sourceRevision": 1}, headers=auth(keys))
    assert rolled.json()["revision"] == 3
    configs = (await client.get("/v1/manage/configs", headers=auth(keys))).json()
    assert configs["publicKey"] == keys["public"]
    assert configs["items"][0]["payload"] == {"apm.enabled": True}
    from androidapm_server.db.models import RemoteConfigVersion
    from androidapm_server.remote_config import SignedConfig

    async with factory() as session:
        row = await session.scalar(
            select(RemoteConfigVersion).where(RemoteConfigVersion.revision == 3)
        )
        signed = SignedConfig(
            row.revision,
            row.issued_at.replace(tzinfo=UTC),
            row.expires_at.replace(tzinfo=UTC),
            row.rollout_basis_points,
            row.payload_json,
            row.key_id,
            row.signature_b64,
        )
        verify_envelope(keys["public"], signed.envelope())
    assert (
        await client.post(
            "/v1/manage/configs", json={"sourceRevision": 1}, headers=auth(keys, "other")
        )
    ).status_code == 404


async def test_alert_firing_dedup_unknown_recovery_ack_and_retry_outbox(product: Any) -> None:
    client, factory, keys, settings = product
    rule = {
        "name": "Crash",
        "metric": "crash.count",
        "threshold": 0,
        "windowSeconds": 60,
        "channel": "test",
    }
    created = await client.post("/v1/alerts/rules", json=rule, headers=auth(keys))
    assert created.status_code == 201, created.text
    await emit(factory, module="crash", name="app_exit", values=[{"reasonCode": 1}])
    await client.post("/v1/alerts/evaluate", headers=auth(keys))
    assert (await client.get("/v1/alerts", headers=auth(keys))).json()["instances"] == []
    await emit(
        factory, module="crash", name="java_crash", values=[{"stackTrace": "at app.fn(Main.kt:1)"}]
    )
    for _ in range(2):
        assert (await client.post("/v1/alerts/evaluate", headers=auth(keys))).status_code == 200
    state = (await client.get("/v1/alerts", headers=auth(keys))).json()
    assert len(state["instances"]) == len(state["deliveries"]) == 1
    instance_id = state["instances"][0]["id"]
    assert (
        await client.post(
            f"/v1/alerts/instances/{instance_id}/acknowledge", headers=auth(keys, "other")
        )
    ).status_code == 404
    assert (
        await client.post(f"/v1/alerts/instances/{instance_id}/acknowledge", headers=auth(keys))
    ).status_code == 200
    async with factory() as session:
        await evaluate_scope(session, settings, PRINCIPAL, NOW + timedelta(minutes=5))
        await session.commit()
    state = (await client.get("/v1/alerts", headers=auth(keys))).json()
    assert state["rules"][0]["state"] == "INSUFFICIENT_DATA"
    assert state["instances"][0]["resolvedAt"] is None
    await emit(factory, at=NOW + timedelta(minutes=5))
    async with factory() as session:
        await evaluate_scope(session, settings, PRINCIPAL, NOW + timedelta(minutes=5, seconds=1))
        await session.commit()
    state = (await client.get("/v1/alerts", headers=auth(keys))).json()
    assert state["instances"][0]["resolvedAt"] is not None
    assert len(state["deliveries"]) == 2
    async with factory() as session:
        await session.execute(
            update(AlertDelivery).values(next_attempt_at=NOW - timedelta(seconds=1))
        )
        await session.commit()
    attempts: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        attempts.append(request.headers["Idempotency-Key"])
        return httpx.Response(503 if len(attempts) == 1 else 204)

    async with AsyncClient(transport=httpx.MockTransport(transport)) as outbound:
        assert await deliver_once(factory, settings, outbound)
        async with factory() as session:
            delivery = await session.get(AlertDelivery, 1)
            assert delivery.status == "RETRY" and delivery.attempt_count == 1
            delivery.next_attempt_at = NOW - timedelta(seconds=1)
            await session.commit()
        assert await deliver_once(factory, settings, outbound)
    assert attempts[0] == attempts[1]
    async with factory() as session:
        assert (await session.get(AlertDelivery, 1)).status == "DELIVERED"
        assert (await session.scalar(select(func.count()).select_from(AlertInstance))) == 1


async def test_performance_unknown_gc_sentinels_never_make_zero_and_limits(product: Any) -> None:
    client, factory, keys, settings = product
    await emit(
        factory,
        module="gc_monitor",
        name="memory_churn",
        values=[
            {
                "allocationRateKiBPerSec": 0,
                "allocationRateAvailable": False,
            }
        ],
    )
    response = await client.get("/v1/query/performance", params=WINDOW, headers=auth(keys))
    gc = next(
        item for item in response.json()["metrics"] if item["field"] == "allocationRateKiBPerSec"
    )
    assert gc["current"]["state"] == "UNAVAILABLE" and gc["current"]["p95"] is None
    settings.query_max_rows = 1
    await emit(factory)
    response = await client.get("/v1/query/performance", params=WINDOW, headers=auth(keys))
    assert response.status_code == 422 and response.json()["code"] == "query_row_limit"


async def test_finite_large_observations_do_not_overflow_the_response(product: Any) -> None:
    client, factory, keys, _ = product
    await emit(factory, values=[{"launchDurationMs": 1e308}, {"launchDurationMs": 1e308}])
    response = await client.get("/v1/query/performance", params=WINDOW, headers=auth(keys))
    assert response.status_code == 200, response.text
    observation = response.json()["metrics"][0]["current"]
    assert observation["mean"] == observation["p95"] == 1e308


async def test_silence_keeps_episodes_and_utc_timestamps_without_delivering(product: Any) -> None:
    client, factory, keys, settings = product
    definition = {"name": "Muted", "metric": "crash.count", "threshold": 0, "channel": "test"}
    rule = (await client.post("/v1/alerts/rules", json=definition, headers=auth(keys))).json()
    response = await client.post(
        f"/v1/alerts/rules/{rule['id']}/silence", json={"minutes": 60}, headers=auth(keys)
    )
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["silenceUntil"]).utcoffset() == timedelta(0)
    await emit(factory, module="crash", name="java_crash")
    async with factory() as session:
        await evaluate_scope(session, settings, PRINCIPAL, NOW + timedelta(seconds=1))
        await session.commit()
    state = (await client.get("/v1/alerts", headers=auth(keys))).json()
    assert state["rules"][0]["state"] == "FIRING"
    assert datetime.fromisoformat(state["rules"][0]["evaluatedAt"]).utcoffset() == timedelta(0)
    assert datetime.fromisoformat(state["instances"][0]["triggeredAt"]).utcoffset() == timedelta(0)
    assert state["deliveries"][0]["status"] == "SUPPRESSED"
    assert (
        await client.put(
            f"/v1/alerts/rules/{rule['id']}",
            json={**definition, "threshold": 100},
            headers=auth(keys),
        )
    ).status_code == 409
    await client.post(
        f"/v1/alerts/rules/{rule['id']}/silence", json={"minutes": 0}, headers=auth(keys)
    )
    async with AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(204))) as out:
        assert not await deliver_once(factory, settings, out)
    assert len(state["instances"]) == 1


@pytest.mark.parametrize("stolen", [False, True], ids=["expired-lease", "new-owner"])
async def test_outbox_completion_cannot_overwrite_a_lost_lease(product: Any, stolen: bool) -> None:
    client, factory, keys, settings = product
    await client.post(
        "/v1/alerts/rules",
        json={"name": "Fenced", "metric": "crash.count", "threshold": 0, "channel": "test"},
        headers=auth(keys),
    )
    await emit(factory, module="crash", name="java_crash")
    async with factory() as session:
        await evaluate_scope(session, settings, PRINCIPAL, NOW + timedelta(seconds=1))
        await session.execute(
            update(AlertDelivery).values(next_attempt_at=NOW - timedelta(seconds=1))
        )
        await session.commit()

    async def transport(_: httpx.Request) -> httpx.Response:
        async with factory() as session:
            changes = (
                {"lease_owner": "replacement-owner"}
                if stolen
                else {"lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
            )
            await session.execute(update(AlertDelivery).values(**changes))
            await session.commit()
        return httpx.Response(204)

    async with AsyncClient(transport=httpx.MockTransport(transport)) as out:
        assert await deliver_once(factory, settings, out)
    async with factory() as session:
        row = await session.scalar(select(AlertDelivery))
        assert row is not None and row.status == "SENDING" and row.delivered_at is None
        if stolen:
            assert row.lease_owner == "replacement-owner"


@pytest.mark.parametrize("value", ["", "  "])
def test_empty_signing_environment_is_unconfigured(value: str) -> None:
    assert (
        Settings(remote_config_signing_private_key_b64=value).remote_config_signing_private_key_b64
        is None
    )


async def test_historical_projection_upgrade_preserves_identity_and_expired_evidence(
    product: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location("product_backfill", BACKFILL_SCRIPT)
    assert spec is not None and spec.loader is not None
    backfill_performance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backfill_performance)

    _, factory, _, _ = product
    await emit(factory, values=[{"launchDurationMs": 123}, {"launchDurationMs": 456}])
    async with factory() as session:
        rows = (await session.scalars(select(InboxEvent).order_by(InboxEvent.id))).all()
        snapshot = {
            key: getattr(rows[0], key)
            for key in (
                "payload_json",
                "payload_sha256",
                "event_id",
                "occurrence_timestamp_ms",
                "received_at",
            )
        }
        for row in rows:
            row.normalization_version = 2
            row.normalized_json = {"registered_fields": {}, "field_states": {}}
        rows[1].raw_pruned_at = NOW
        await session.commit()
    monkeypatch.setattr(backfill_performance, "get_session_factory", lambda: factory)
    assert await backfill_performance.backfill() == 1
    assert await backfill_performance.backfill() == 0
    async with factory() as session:
        rows = (await session.scalars(select(InboxEvent).order_by(InboxEvent.id))).all()
        assert rows[0].normalization_version == 4
        assert rows[0].normalized_json["registered_fields"]["launchDurationMs"] == 123
        assert all(getattr(rows[0], key) == value for key, value in snapshot.items())
        assert rows[1].normalization_version == 2
        assert rows[1].normalized_json["registered_fields"] == {}


@pytest.mark.parametrize("live_lease", [False, True])
async def test_outbox_crashed_final_attempt_is_not_sent_again(
    product: Any, live_lease: bool
) -> None:
    """A consumed attempt survives crashes; an active final owner is never fenced out early."""
    client, factory, keys, settings = product
    await client.post(
        "/v1/alerts/rules",
        json={"name": "Bounded", "metric": "crash.count", "threshold": 0, "channel": "test"},
        headers=auth(keys),
    )
    await emit(factory, module="crash", name="java_crash")
    async with factory() as session:
        await evaluate_scope(session, settings, PRINCIPAL, NOW + timedelta(seconds=1))
        await session.execute(
            update(AlertDelivery).values(
                status="SENDING",
                attempt_count=settings.notification_max_attempts,
                lease_owner="crashed-final-owner",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=60 if live_lease else -60),
                next_attempt_at=NOW - timedelta(seconds=1),
            )
        )
        await session.commit()
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with AsyncClient(transport=httpx.MockTransport(transport)) as outbound:
        assert await deliver_once(factory, settings, outbound) is (not live_lease)
    assert requests == []
    async with factory() as session:
        row = await session.scalar(select(AlertDelivery))
        assert row is not None
        assert row.attempt_count == settings.notification_max_attempts
        assert row.status == ("SENDING" if live_lease else "FAILED")
        assert row.lease_owner == ("crashed-final-owner" if live_lease else None)
        if not live_lease:
            assert row.last_error == "RETRY_EXHAUSTED"
