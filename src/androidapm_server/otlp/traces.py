"""Map only valid versioned SDK manual spans with real wall-clock endpoints."""

from collections import defaultdict
from collections.abc import Iterable

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import InstrumentationScope
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

from androidapm_server.db.models import InboxEvent
from androidapm_server.otlp.logs import SCOPE_NAME, SCOPE_VERSION, _key_values
from androidapm_server.otlp.metrics import resource_for, resource_key
from androidapm_server.tracing import span_for_event

OTLP_MANUAL_OPERATION_NAME = "androidapm.manual_span"


def build_traces_request(events: Iterable[InboxEvent]) -> ExportTraceServiceRequest:
    """Exclude legacy/invalid/skewed spans; names and attr_* never become OTLP dimensions."""
    grouped: dict[tuple[str, str, str, str, str], list[Span]] = defaultdict(list)
    for event in events:
        observation = span_for_event(event)
        if (
            observation is None
            or observation.timing_state != "AVAILABLE"
            or observation.relation_state == "INVALID_PARENT"
            or observation.parent_span_id == observation.span_id
            or observation.status == "UNKNOWN"
        ):
            continue
        # Span names are host-controlled free text. Preserve the raw operation behind L1,
        # and export a fixed low-cardinality operation with true IDs/time/parent/status.
        grouped[resource_key(event)].append(
            Span(
                trace_id=bytes.fromhex(observation.trace_id),
                span_id=bytes.fromhex(observation.span_id),
                parent_span_id=bytes.fromhex(observation.parent_span_id)
                if observation.parent_span_id
                else b"",
                name=OTLP_MANUAL_OPERATION_NAME,
                kind=Span.SPAN_KIND_INTERNAL,
                start_time_unix_nano=observation.start_ms * 1_000_000,  # type: ignore[operator]
                end_time_unix_nano=observation.end_ms * 1_000_000,  # type: ignore[operator]
                attributes=_key_values(
                    {"android.apm.monotonic_duration_ms": observation.duration_ms}
                ),
                status=Status(
                    code=Status.STATUS_CODE_ERROR
                    if observation.status == "ERROR"
                    else Status.STATUS_CODE_OK
                ),
            )
        )
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=resource_for(key),
                scope_spans=[
                    ScopeSpans(
                        scope=InstrumentationScope(name=SCOPE_NAME, version=SCOPE_VERSION),
                        spans=spans,
                    )
                ],
            )
            for key, spans in grouped.items()
        ]
    )
