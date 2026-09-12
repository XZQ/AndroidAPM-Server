from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)

from androidapm_server.db.models import InboxEvent
from androidapm_server.otlp.client import OtlpLogsClient
from androidapm_server.otlp.logs import build_logs_request


def inbox_event() -> InboxEvent:
    return InboxEvent(
        id=1,
        tenant_id="tenant-a",
        event_id="event-1",
        app_id="com.example",
        environment="production",
        schema_version="1",
        sdk_version="0.1.0",
        app_version="1.2.3",
        app_build="123",
        protocol="protobuf",
        event_timestamp_ms=1_700_000_000_000,
        occurrence_timestamp_ms=1_700_000_000_000,
        release_identity_quality="OCCURRENCE_BOUND",
        installation_identity_quality="OCCURRENCE_BOUND",
        installation_hmac="a" * 64,
        installation_hmac_key_version="v1",
        normalized_json={"indexed_attributes": {"dropRate": 0.125}},
        payload_sha256="0" * 64,
        request_id="request-1",
        received_at=datetime(2026, 7, 16, tzinfo=UTC),
        payload_json={
            "timestamp": 1_700_000_000_000,
            "event_id": "event-1",
            "module": "core",
            "name": "sdk_health",
            "kind": "METRIC",
            "severity": "INFO",
            "priority": "NORMAL",
            "process_name": "com.example",
            "thread_name": "main",
            "scene": "home",
            "foreground": True,
            "fields": {"dropRate": "0.125", "stackTrace": "sensitive"},
            "global_context": {"session": "session-1"},
            "extras": {},
        },
    )


def attributes(values: object) -> dict[str, object]:
    return {item.key: item.value for item in values}  # type: ignore[attr-defined]


def test_maps_only_source_reviewed_identity_and_fields_to_otlp_logs() -> None:
    serialized = build_logs_request([inbox_event()]).SerializeToString()
    parsed = ExportLogsServiceRequest.FromString(serialized)
    resource_logs = parsed.resource_logs[0]
    resource = attributes(resource_logs.resource.attributes)
    record = resource_logs.scope_logs[0].log_records[0]
    attrs = attributes(record.attributes)

    assert resource["service.name"].string_value == "com.example"  # type: ignore[union-attr]
    assert resource["deployment.environment.name"].string_value == "production"  # type: ignore[union-attr]
    assert attrs["android.apm.event_id"].string_value == "event-1"  # type: ignore[union-attr]
    assert attrs["android.apm.field.dropRate"].double_value == 0.125  # type: ignore[union-attr]
    assert attrs["android.apm.installation.hmac"].string_value == "a" * 64  # type: ignore[union-attr]
    assert "android.apm.context.session" not in attrs
    assert "android.apm.field.stackTrace" not in attrs
    assert record.body.string_value == "core.sdk_health"
    assert record.time_unix_nano == 1_700_000_000_000_000_000


@pytest.mark.asyncio
async def test_client_classifies_success_retry_and_permanent_rejection() -> None:
    responses = iter(
        [
            httpx.Response(200, content=ExportLogsServiceResponse().SerializeToString()),
            httpx.Response(503, headers={"Retry-After": "99"}),
            httpx.Response(401),
        ]
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OtlpLogsClient("http://collector/v1/logs", "{}", 1, http_client)
        success = await client.export(b"payload")
        retry = await client.export(b"payload")
        permanent = await client.export(b"payload")
    assert success.success
    assert retry.retryable and retry.retry_after_seconds == 60
    assert not permanent.retryable and permanent.error_code == "http_401"
