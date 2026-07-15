"""Deterministic AndroidAPM event to OTLP LogRecord mapping."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, InstrumentationScope, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import (
    SEVERITY_NUMBER_DEBUG,
    SEVERITY_NUMBER_ERROR,
    SEVERITY_NUMBER_FATAL,
    SEVERITY_NUMBER_INFO,
    SEVERITY_NUMBER_UNSPECIFIED,
    SEVERITY_NUMBER_WARN,
    LogRecord,
    ResourceLogs,
    ScopeLogs,
    SeverityNumber,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource

from androidapm_server.db.models import InboxEvent

SCOPE_NAME = "androidapm-server"
SCOPE_VERSION = "0.1.0"

SEVERITY_NUMBERS: dict[str, SeverityNumber.ValueType] = {
    "DEBUG": SEVERITY_NUMBER_DEBUG,
    "INFO": SEVERITY_NUMBER_INFO,
    "WARN": SEVERITY_NUMBER_WARN,
    "ERROR": SEVERITY_NUMBER_ERROR,
    "FATAL": SEVERITY_NUMBER_FATAL,
}

# The SDK's compatibility Protobuf stringifies field values. Only source-reviewed
# names are converted back to numeric/bool OTLP attributes; unknown values remain strings.
INTEGER_FIELD_NAMES = frozenset(
    {
        "statusCode",
        "requestSize",
        "responseSize",
        "totalRequests",
        "successCount",
        "errorCount",
        "emitCount",
        "dropCount",
        "queueSize",
        "internalErrorCount",
        "diagnosticDroppedCount",
        "diagnosticWriteFailureCount",
        "frameCount",
        "slowFrameCount",
        "droppedFrames",
        "jankCount",
        "frozenCount",
        "phaseContentProviderCount",
        "viewCount",
        "maxDepth",
    }
)
FLOAT_FIELD_NAMES = frozenset(
    {
        "durationMs",
        "avgDurationMs",
        "maxDurationMs",
        "dnsMs",
        "tcpMs",
        "tlsMs",
        "requestHeaderMs",
        "responseHeaderMs",
        "responseBodyMs",
        "launchDurationMs",
        "backgroundDurationMs",
        "phaseAppCreateMs",
        "phaseContentProviderMs",
        "phaseActivityCreateMs",
        "phaseBeforeActivityMs",
        "phaseActivityLifecycleMs",
        "phaseFirstFrameMs",
        "bottleneckDurationMs",
        "bottleneckRatioPercent",
        "dropRate",
        "avgUploadLatencyMs",
        "maxUploadLatencyMs",
        "fps",
        "refreshRate",
        "averageFrameMs",
        "maxFrameMs",
    }
)
BOOLEAN_FIELD_NAMES = frozenset({"isSlow", "isColdStart"})


def build_logs_request(events: Iterable[InboxEvent]) -> ExportLogsServiceRequest:
    """Build one OTLP request grouped by stable resource identity."""
    grouped: dict[tuple[str, ...], list[InboxEvent]] = defaultdict(list)
    for event in events:
        event_resource_key = (
            event.tenant_id,
            event.app_id,
            event.environment,
            event.app_version or "",
            event.app_build or "",
            event.sdk_version,
            str(event.payload_json.get("process_name", "")),
        )
        grouped[event_resource_key].append(event)

    request = ExportLogsServiceRequest()
    for grouped_resource_key, resource_events in grouped.items():
        request.resource_logs.append(_resource_logs(grouped_resource_key, resource_events))
    return request


def _resource_logs(resource_key: tuple[str, ...], events: list[InboxEvent]) -> ResourceLogs:
    """Map one resource group and all of its log records."""
    tenant_id, app_id, environment, app_version, app_build, sdk_version, process_name = (
        resource_key
    )
    attributes: dict[str, Any] = {
        "service.name": app_id,
        "android.apm.app_id": app_id,
        "android.apm.tenant_id": tenant_id,
        "deployment.environment.name": environment,
        "telemetry.sdk.name": "androidapm",
        "telemetry.sdk.version": sdk_version,
    }
    if app_version:
        attributes["service.version"] = app_version
    if app_build:
        attributes["service.instance.build_id"] = app_build
    if process_name:
        attributes["process.executable.name"] = process_name

    scope_logs = ScopeLogs(
        scope=InstrumentationScope(name=SCOPE_NAME, version=SCOPE_VERSION),
        log_records=[_log_record(event) for event in events],
    )
    return ResourceLogs(
        resource=Resource(attributes=_key_values(attributes)),
        scope_logs=[scope_logs],
        schema_url="https://opentelemetry.io/schemas/1.37.0",
    )


def _log_record(event: InboxEvent) -> LogRecord:
    """Map one inbox row while retaining its stable client identity."""
    payload = event.payload_json
    module = str(payload["module"])
    name = str(payload["name"])
    severity = str(payload["severity"])
    attributes: dict[str, Any] = {
        "android.apm.event_id": event.event_id,
        "android.apm.module": module,
        "android.apm.name": name,
        "android.apm.kind": str(payload["kind"]),
        "android.apm.priority": str(payload["priority"]),
        "android.apm.thread.name": str(payload["thread_name"]),
        "android.apm.protocol": event.protocol,
        "android.apm.schema_version": event.schema_version,
    }
    for optional in ("scene", "foreground"):
        if payload.get(optional) is not None:
            attributes[f"android.apm.{optional}"] = payload[optional]
    _merge_prefixed(
        attributes,
        "android.apm.field.",
        payload.get("fields", {}),
        coerce_registered_fields=True,
    )
    _merge_prefixed(attributes, "android.apm.context.", payload.get("global_context", {}))
    _merge_prefixed(attributes, "android.apm.extra.", payload.get("extras", {}))

    return LogRecord(
        time_unix_nano=event.event_timestamp_ms * 1_000_000,
        observed_time_unix_nano=int(event.received_at.timestamp() * 1_000_000_000),
        severity_number=SEVERITY_NUMBERS.get(severity, SEVERITY_NUMBER_UNSPECIFIED),
        severity_text=severity,
        body=_any_value(f"{module}.{name}"),
        attributes=_key_values(attributes),
    )


def _merge_prefixed(
    target: dict[str, Any],
    prefix: str,
    source: object,
    coerce_registered_fields: bool = False,
) -> None:
    """Preserve bounded SDK maps under collision-free namespaced attributes."""
    if not isinstance(source, Mapping):
        return
    for key, value in source.items():
        target[f"{prefix}{key}"] = (
            _coerce_field(str(key), value) if coerce_registered_fields else value
        )


def _coerce_field(key: str, value: Any) -> Any:
    """Restore source-reviewed scalar types after compatibility stringification."""
    if key in INTEGER_FIELD_NAMES:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if key in FLOAT_FIELD_NAMES:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if key in BOOLEAN_FIELD_NAMES and isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return value


def _key_values(values: Mapping[str, Any]) -> list[KeyValue]:
    """Convert sorted attributes for deterministic fixture snapshots."""
    return [KeyValue(key=key, value=_any_value(values[key])) for key in sorted(values)]


def _any_value(value: Any) -> AnyValue:
    """Convert JSON-compatible values to OTLP without lossy string guessing."""
    if isinstance(value, bool):
        return AnyValue(bool_value=value)
    if isinstance(value, int):
        return AnyValue(int_value=value)
    if isinstance(value, float):
        return AnyValue(double_value=value)
    if value is None:
        return AnyValue(string_value="")
    return AnyValue(string_value=str(value))
