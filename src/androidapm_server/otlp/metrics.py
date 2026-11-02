"""Export reviewed numeric observations as gauges, never invented histogram/counter samples."""

import math
from collections import defaultdict
from collections.abc import Iterable

from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import InstrumentationScope
from opentelemetry.proto.metrics.v1.metrics_pb2 import (
    Gauge,
    Metric,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource

from androidapm_server.db.models import InboxEvent
from androidapm_server.otlp.logs import SCOPE_NAME, SCOPE_VERSION, _key_values
from androidapm_server.performance_catalog import PERFORMANCE_FIELDS

UNIT_MAP = {
    "events": "{event}",
    "frames": "{frame}",
    "fps": "{frame}/s",
    "percent": "%",
    "ratio": "1",
    "ratio_delta": "1",
    "bytes": "By",
    "KiB": "KiBy",
    "KiB/s": "KiBy/s",
    "MiB": "MiBy",
    "cores": "{core}",
    "depth": "{level}",
    "objects": "{object}",
}
# The catalog already includes SDK health, including its availability/sample gates.
# Reconstructing those fields from normalization duplicates points and loses the gates.
METRIC_FIELDS = PERFORMANCE_FIELDS


def resource_key(event: InboxEvent) -> tuple[str, str, str, str, str]:
    """Use bounded authenticated scope and occurrence-bound release only."""
    return (
        event.tenant_id,
        event.app_id,
        event.environment,
        event.app_version
        if event.release_identity_quality == "OCCURRENCE_BOUND" and event.app_version
        else "",
        event.sdk_version,
    )


def resource_for(key: tuple[str, str, str, str, str]) -> Resource:
    """Avoid installation, process, event ID, URL and arbitrary high-cardinality labels."""
    tenant, app, environment, version, sdk = key
    attrs = {
        "service.name": app,
        "android.apm.tenant_id": tenant,
        "deployment.environment.name": environment,
        "telemetry.sdk.name": "androidapm",
        "telemetry.sdk.version": sdk,
    }
    if version:
        attrs["service.version"] = version
    return Resource(attributes=_key_values(attrs))


def build_metrics_request(events: Iterable[InboxEvent]) -> ExportMetricsServiceRequest:
    """Preserve raw/aggregate/rolling snapshot identities in separate gauge names."""
    grouped: dict[tuple[str, str, str, str, str], dict[str, Metric]] = defaultdict(dict)
    for event in events:
        payload = event.payload_json
        normalized = event.normalized_json or {}
        values = normalized.get("registered_fields", {})
        for field in METRIC_FIELDS:
            if (payload.get("module"), payload.get("name")) != (field.module, field.name):
                continue
            if field.availability_field and values.get(field.availability_field) is not True:
                continue
            if field.sample_count_field is not None:
                count = values.get(field.sample_count_field)
                if (
                    not isinstance(count, (int, float))
                    or isinstance(count, bool)
                    or not math.isfinite(count)
                    or count <= 0
                ):
                    # Empty stage histograms contain placeholder latency zeros, not samples.
                    continue
            value = values.get(field.field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if not math.isfinite(value):
                continue
            metrics = grouped[resource_key(event)]
            name = f"android.apm.observation.{field.id}"
            if name not in metrics:
                metrics[name] = Metric(
                    name=name,
                    unit=UNIT_MAP.get(field.unit, field.unit),
                    description=(
                        f"Reported {field.module}/{field.name}.{field.field} observation. "
                        "One point per source event; aggregate, window, and cumulative snapshot "
                        "values remain gauges. Not a reconstructed histogram or increment."
                    ),
                    gauge=Gauge(),
                )
            timestamp = (event.occurrence_timestamp_ms or event.event_timestamp_ms) * 1_000_000
            metrics[name].gauge.data_points.append(
                NumberDataPoint(time_unix_nano=timestamp, as_double=float(value))
            )
    return ExportMetricsServiceRequest(
        resource_metrics=[
            ResourceMetrics(
                resource=resource_for(key),
                scope_metrics=[
                    ScopeMetrics(
                        scope=InstrumentationScope(name=SCOPE_NAME, version=SCOPE_VERSION),
                        metrics=list(metrics.values()),
                    )
                ],
            )
            for key, metrics in grouped.items()
            if metrics
        ]
    )
