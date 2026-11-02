"""Real graph/OTLP contracts and durable multi-signal retry/fencing acceptance."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceResponse
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from androidapm_server import worker
from androidapm_server.config import Settings, get_settings
from androidapm_server.db.base import Base
from androidapm_server.db.models import InboxEvent, QueryKey, Tenant
from androidapm_server.db.session import get_session
from androidapm_server.domain import ApmEvent
from androidapm_server.errors import ApiError
from androidapm_server.main import create_app
from androidapm_server.normalization import normalize_event
from androidapm_server.otlp.client import OtlpLogsClient, OtlpMetricsClient, OtlpTracesClient
from androidapm_server.otlp.logs import build_logs_request
from androidapm_server.otlp.metrics import build_metrics_request
from androidapm_server.otlp.traces import OTLP_MANUAL_OPERATION_NAME, build_traces_request
from androidapm_server.query_auth import QueryPrincipal, generate_query_key
from androidapm_server.tracing import load_spans, span_for_event, summarize_trace

NOW = datetime.now(UTC)
NOW_MS = int(NOW.timestamp() * 1000)
TRACE_ID = "1" * 32
SPAN_ID = "2" * 16
CHILD_ID = "3" * 16
WINDOW = {"fromMs": NOW_MS - 60_000, "toMs": NOW_MS + 1000}
Factory = async_sessionmaker[AsyncSession]


def event(
    event_id: str = "span",
    *,
    module: str = "trace",
    name: str = "secret-operation",
    fields: dict[str, Any] | None = None,
    tenant: str = "tenant",
) -> InboxEvent:
    """Build a valid persisted source observation, using actual normalization."""
    source = ApmEvent(
        event_id=event_id,
        module=module,
        name=name,
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        timestamp=NOW_MS,
        process_name="app",
        thread_name="main",
        fields=fields
        if fields is not None
        else {
            "traceId": TRACE_ID,
            "spanId": SPAN_ID,
            "duration_ms": 100,
            "status": "OK",
            "spanSchemaVersion": 2,
            "startTimestampMs": NOW_MS - 100,
            "endTimestampMs": NOW_MS,
            "attr_auth": "secret-attribute",
        },
    )
    return InboxEvent(
        tenant_id=tenant,
        app_id="app",
        environment="test",
        event_id=event_id,
        schema_version="2",
        sdk_version="test",
        protocol="protobuf",
        app_version="1.0",
        release_identity_quality="OCCURRENCE_BOUND",
        event_timestamp_ms=NOW_MS,
        occurrence_timestamp_ms=NOW_MS,
        normalized_json=normalize_event(source).normalized_json,
        payload_json=source.model_dump(mode="json"),
        payload_sha256="0" * 64,
        request_id="trace-test",
        received_at=NOW,
    )


@pytest.mark.parametrize("invalid", ["", "1" * 31, "z" * 32, "0" * 32, 10, None])
def test_rejects_invalid_trace_identity_without_inventing_a_span(invalid: object) -> None:
    row = event()
    row.payload_json["fields"]["traceId"] = invalid
    assert span_for_event(row) is None
    assert not build_traces_request([row]).resource_spans


@pytest.mark.parametrize(
    ("changes", "state"),
    [
        ({"spanSchemaVersion": 1}, "TIMING_UNAVAILABLE"),
        ({"spanSchemaVersion": None}, "TIMING_UNAVAILABLE"),
        ({"startTimestampMs": None}, "INVALID_TIMING"),
        ({"duration_ms": -1}, "INVALID_TIMING"),
        ({"endTimestampMs": NOW_MS - 500}, "CLOCK_SKEW"),
        ({"endTimestampMs": NOW_MS + 10_000}, "CLOCK_SKEW"),
        ({"startTimestampMs": str(NOW_MS - 100)}, "AVAILABLE"),
    ],
)
def test_real_wall_timing_is_required_and_monotonic_duration_is_not_reconstructed(
    changes: dict[str, Any],
    state: str,
) -> None:
    row = event()
    row.payload_json["fields"].update(changes)
    observation = span_for_event(row)
    assert observation is not None and observation.timing_state == state
    if state == "TIMING_UNAVAILABLE":
        assert observation.start_ms is None and observation.end_ms is None
    request = build_traces_request([row])
    assert bool(request.resource_spans) == (state == "AVAILABLE")


def test_trace_graph_preserves_orphan_cycle_duplicate_and_unknown_completeness() -> None:
    root = span_for_event(event())
    assert root is not None
    orphan = root.model_copy(update={"span_id": CHILD_ID, "parent_span_id": "4" * 16})
    summary = summarize_trace(TRACE_ID, [root, orphan])
    assert summary.orphan_count == 1
    assert orphan.relation_state == "ORPHAN"
    assert summary.coverage_state == "PARTIAL"
    assert summarize_trace(TRACE_ID, [root]).coverage_state == "UNKNOWN_COMPLETENESS"
    root.parent_span_id = CHILD_ID
    orphan.parent_span_id = SPAN_ID
    summary = summarize_trace(TRACE_ID, [root, orphan])
    assert summary.invalid_relation_count == 2
    assert root.relation_state == orphan.relation_state == "CYCLE"
    duplicate = root.model_copy()
    summary = summarize_trace(TRACE_ID, [root, duplicate, orphan])
    assert summary.invalid_relation_count == 3
    assert root.relation_state == duplicate.relation_state == "DUPLICATE_ID"
    assert orphan.relation_state == "AMBIGUOUS_PARENT"


def test_otlp_trace_has_real_ids_times_but_no_dynamic_operation_or_custom_attributes() -> None:
    request = ExportTraceServiceRequest.FromString(
        build_traces_request([event()]).SerializeToString()
    )
    span = request.resource_spans[0].scope_spans[0].spans[0]
    assert span.trace_id == bytes.fromhex(TRACE_ID)
    assert span.span_id == bytes.fromhex(SPAN_ID)
    assert span.start_time_unix_nano == (NOW_MS - 100) * 1_000_000
    assert span.end_time_unix_nano == NOW_MS * 1_000_000
    assert span.name == OTLP_MANUAL_OPERATION_NAME
    assert b"secret-operation" not in request.SerializeToString()
    assert b"secret-attribute" not in request.SerializeToString()
    logs = build_logs_request([event()]).SerializeToString()
    assert b"secret-operation" not in logs and b"secret-attribute" not in logs
    row = event()
    row.payload_json["fields"]["parentSpanId"] = "invalid"
    assert not build_traces_request([row]).resource_spans


def test_metric_snapshot_gauges_preserve_aggregate_identity_and_gc_availability() -> None:
    rows = [
        event(
            "aggregate",
            module="network",
            name="network_aggregate",
            fields={
                "totalRequests": 20,
                "avgDurationMs": 100,
                "url": "secret-url",
            },
        ),
        event("health", module="core", name="sdk_health", fields={"emitCount": 1234}),
        event(
            "gc",
            module="gc_monitor",
            name="memory_churn",
            fields={
                "gcTimeRatio": 0,
                "gcCountersAvailable": False,
            },
        ),
    ]
    request = ExportMetricsServiceRequest.FromString(
        build_metrics_request(rows).SerializeToString()
    )
    metrics = [
        metric
        for resource in request.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    ]
    assert len(metrics) == 3
    assert all(metric.WhichOneof("data") == "gauge" for metric in metrics)
    assert any(
        metric.name.endswith("core.sdk_health.emitCount")
        and metric.gauge.data_points[0].as_double == 1234
        for metric in metrics
    )
    assert any(
        metric.name.endswith("network.network_aggregate.avgDurationMs") for metric in metrics
    )
    assert all(not point.attributes for metric in metrics for point in metric.gauge.data_points)
    assert b"secret-url" not in request.SerializeToString()
    assert b"installation" not in request.SerializeToString()


@pytest.mark.parametrize("count", [None, 0, -1, True, 2])
def test_sdk_metrics_emit_once_and_empty_stage_histograms_have_no_latency_points(
    count: object,
) -> None:
    fields: dict[str, Any] = {"emitCount": 1234, "dispatcherStage.resolve.avgMicros": 0}
    if count is not None:
        fields["dispatcherStage.resolve.count"] = count
    request = build_metrics_request(
        [event("health", module="core", name="sdk_health", fields=fields)]
    )
    points = {
        metric.name: list(metric.gauge.data_points)
        for resource in request.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert len(points["android.apm.observation.core.sdk_health.emitCount"]) == 1
    latency = points.get(
        "android.apm.observation.core.sdk_health.dispatcherStage.resolve.avgMicros"
    )
    if count == 2:
        assert latency is not None and len(latency) == 1 and latency[0].as_double == 0
    else:
        assert latency is None


@pytest.mark.parametrize(
    ("client_class", "response_class", "field"),
    [
        (OtlpLogsClient, ExportLogsServiceResponse, "rejected_log_records"),
        (OtlpMetricsClient, ExportMetricsServiceResponse, "rejected_data_points"),
        (OtlpTracesClient, ExportTraceServiceResponse, "rejected_spans"),
    ],
)
async def test_otlp_partial_success_is_terminal_and_receiver_text_is_not_persisted(
    client_class: type[OtlpLogsClient],
    response_class: Any,
    field: str,
) -> None:
    response = response_class()
    setattr(response.partial_success, field, 1)
    response.partial_success.error_message = "secret receiver details"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=response.SerializeToString())
        )
    ) as http:
        result = await client_class("http://test", "{}", 1, http).export(b"request")
    assert not result.success and not result.retryable
    assert result.error_code == "otlp_partial_rejection"
    assert "secret" not in str(result.error_message)


@pytest.fixture
async def factory(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Factory]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "get_session_factory", lambda: factory)
    settings = Settings(
        _env_file=None, otlp_metrics_enabled=True, otlp_traces_enabled=True, worker_max_attempts=3
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    async with factory() as session:
        session.add_all(
            [
                event(),
                event(
                    "launch", module="launch", name="cold_start", fields={"launchDurationMs": 100}
                ),
            ]
        )
        await session.commit()
    yield factory
    await engine.dispose()


async def due(factory: Factory) -> None:
    """Make retries immediately due without relaxing owner/expiry checks."""
    async with factory() as session:
        await session.execute(update(InboxEvent).values(next_attempt_at=NOW - timedelta(seconds=1)))
        await session.commit()


async def test_signal_progress_survives_restart_and_disabled_switch_cannot_lose_plan(
    factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: Counter[str] = Counter()

    async def receive(request: httpx.Request) -> httpx.Response:
        signal = request.url.path.rsplit("/", 1)[-1]
        calls[signal] += 1
        return httpx.Response(503 if signal == "metrics" and calls[signal] == 1 else 200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
        logs = OtlpLogsClient("http://test/logs", "{}", 1, http)
        metrics = OtlpMetricsClient("http://test/metrics", "{}", 1, http)
        traces = OtlpTracesClient("http://test/traces", "{}", 1, http)
        assert await worker.export_once(logs, "worker-one", metrics, traces) == 2
        async with factory() as session:
            rows = {row.event_id: row for row in (await session.scalars(select(InboxEvent))).all()}
            assert rows["span"].status == "delivered"
            assert rows["launch"].export_signal_states_json == {
                "logs": "delivered",
                "metrics": "pending",
                "traces": "not_applicable",
            }
        await due(factory)
        monkeypatch.setattr(
            worker, "get_settings", lambda: Settings(_env_file=None, worker_max_attempts=3)
        )
        assert await worker.export_once(logs, "worker-restarted", metrics, traces) == 1
        assert calls == {"logs": 1, "metrics": 2, "traces": 1}
    async with factory() as session:
        assert all(
            row.status == "delivered" for row in (await session.scalars(select(InboxEvent))).all()
        )


@pytest.mark.parametrize("lost", ["owner", "expiry"])
async def test_lost_lease_never_records_success_or_starts_next_signal(
    factory: Factory,
    lost: str,
) -> None:
    calls: Counter[str] = Counter()

    async def steal(request: httpx.Request) -> httpx.Response:
        calls[request.url.path] += 1
        async with factory() as session:
            await session.execute(
                update(InboxEvent).values(
                    **(
                        {"lease_owner": "new-owner", "lease_expires_at": NOW + timedelta(hours=1)}
                        if lost == "owner"
                        else {"lease_expires_at": NOW - timedelta(seconds=1)}
                    )
                )
            )
            await session.commit()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(steal)) as http:
        assert (
            await worker.export_once(
                OtlpLogsClient("http://test/logs", "{}", 1, http),
                "stale",
                OtlpMetricsClient("http://test/metrics", "{}", 1, http),
                OtlpTracesClient("http://test/traces", "{}", 1, http),
            )
            == 0
        )
    assert calls == {"/logs": 1}
    async with factory() as session:
        assert all(
            row.export_signal_states_json["logs"] == "pending"
            for row in (await session.scalars(select(InboxEvent))).all()
        )


async def test_permanent_log_failure_does_not_prevent_retrying_another_signal(
    factory: Factory,
) -> None:
    calls: Counter[str] = Counter()

    async def receive(request: httpx.Request) -> httpx.Response:
        signal = request.url.path.rsplit("/", 1)[-1]
        calls[signal] += 1
        return httpx.Response(
            401 if signal == "logs" else 503 if signal == "metrics" and calls[signal] == 1 else 200
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
        clients = (
            OtlpLogsClient("http://test/logs", "{}", 1, http),
            OtlpMetricsClient("http://test/metrics", "{}", 1, http),
            OtlpTracesClient("http://test/traces", "{}", 1, http),
        )
        assert await worker.export_once(clients[0], "first", clients[1], clients[2]) == 2
        await due(factory)
        assert await worker.export_once(clients[0], "next", clients[1], clients[2]) == 1
        assert calls == {"logs": 1, "metrics": 2, "traces": 1}
    async with factory() as session:
        rows = {row.event_id: row for row in (await session.scalars(select(InboxEvent))).all()}
        assert rows["launch"].status == "dead_letter"
        assert rows["launch"].export_signal_states_json["logs"] == "failed"
        assert rows["launch"].export_signal_states_json["metrics"] == "delivered"


async def test_trace_scan_limit_is_rejected_before_partial_results(factory: Factory) -> None:
    async with factory() as session:
        session.add_all(event(f"extra-{index}") for index in range(100))
        await session.commit()
        principal = QueryPrincipal("tenant", "query", "app", "test", "investigator", None)
        with pytest.raises(ApiError) as raised:
            await load_spans(
                session,
                principal,
                Settings(_env_file=None, query_max_rows=100),
                WINDOW["fromMs"],
                WINDOW["toMs"],
                None,
            )
        assert raised.value.status_code == 422 and raised.value.code == "query_row_limit"


async def test_otlp_success_response_body_is_bounded() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 65_537))
    ) as http:
        result = await OtlpMetricsClient("http://test", "{}", 1, http).export(b"data")
    assert result.error_code == "invalid_otlp_response" and result.retryable


async def test_cancellation_after_log_commit_reclaims_only_remaining_signals(
    factory: Factory,
) -> None:
    calls: Counter[str] = Counter()

    async def receive(request: httpx.Request) -> httpx.Response:
        signal = request.url.path.rsplit("/", 1)[-1]
        calls[signal] += 1
        if signal == "metrics" and calls[signal] == 1:
            raise asyncio.CancelledError()
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
        logs = OtlpLogsClient("http://test/logs", "{}", 1, http)
        metrics = OtlpMetricsClient("http://test/metrics", "{}", 1, http)
        traces = OtlpTracesClient("http://test/traces", "{}", 1, http)
        with pytest.raises(asyncio.CancelledError):
            await worker.export_once(logs, "cancelled", metrics, traces)
        async with factory() as session:
            await session.execute(
                update(InboxEvent).values(lease_expires_at=NOW - timedelta(seconds=1))
            )
            await session.commit()
        assert await worker.export_once(logs, "new-owner", metrics, traces) == 2
        assert calls == {"logs": 1, "metrics": 2, "traces": 1}


async def test_trace_query_auth_scope_window_and_raw_attributes(factory: Factory) -> None:
    keys = {}
    async with factory() as session:
        session.add_all([Tenant(id="tenant", name="A"), Tenant(id="other", name="B")])
        for role, tenant in (("investigator", "tenant"), ("viewer", "tenant"), ("other", "other")):
            identifier, plaintext, hashed = generate_query_key()
            keys[role] = plaintext
            session.add(
                QueryKey(
                    key_id=identifier,
                    key_hash=hashed,
                    tenant_id=tenant,
                    app_id="app",
                    environment="test",
                    role="investigator" if role == "other" else role,
                )
            )
        session.add(event("foreign", tenant="other"))
        await session.commit()

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        headers = {"Authorization": f"Bearer {keys['investigator']}"}
        result = await client.get("/v1/query/traces", params=WINDOW, headers=headers)
        assert result.status_code == 200 and result.json()["sampleCount"] == 1
        assert result.json()["scope"]["tenantId"] == "tenant"
        assert "no-store" in result.headers["cache-control"]
        detail = await client.get(f"/v1/query/traces/{TRACE_ID}", params=WINDOW, headers=headers)
        assert detail.status_code == 200 and detail.json()["spans"][0]["name"] == "secret-operation"
        assert "secret-attribute" not in detail.text and '"attr_auth"' not in detail.text
        viewer = await client.get(
            "/v1/query/traces", params=WINDOW, headers={"Authorization": f"Bearer {keys['viewer']}"}
        )
        assert viewer.status_code == 403
        forged = await client.get(
            "/v1/query/traces", params={**WINDOW, "tenantId": "other"}, headers=headers
        )
        assert forged.status_code == 400
        absent = await client.get(f"/v1/query/traces/{'9' * 32}", params=WINDOW, headers=headers)
        assert absent.status_code == 404
        empty = await client.get(
            "/v1/query/traces",
            params={"fromMs": NOW_MS + 1, "toMs": NOW_MS + 10_000},
            headers=headers,
        )
        assert empty.status_code == 200 and empty.json()["state"] == "NO_DATA"
        assert empty.json()["meanSpanDurationMs"] is None
        async with factory() as session:
            row = await session.scalar(select(InboxEvent).where(InboxEvent.event_id == "span"))
            assert row is not None
            row.raw_pruned_at = NOW
            row.payload_json = {**row.payload_json, "fields": {}}
            await session.commit()
        expired = await client.get("/v1/query/traces", params=WINDOW, headers=headers)
        assert expired.status_code == 200
        assert expired.json()["state"] == "UNAVAILABLE"
        assert expired.json()["expiredSpanCount"] == 1
        assert expired.json()["invalidSpanCount"] == 0
