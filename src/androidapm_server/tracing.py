"""Bounded manual Trace observations, without inventing missing span timing or parents."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.config import Settings
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.query import _execute_bounded
from androidapm_server.errors import ApiError
from androidapm_server.performance import percentile
from androidapm_server.query import QueryModel, QueryScope, QueryWindow, scope_for
from androidapm_server.query_auth import QueryPrincipal

TRACE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
SPAN_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{16}$")
INTEGER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)$")
TRACE_SPAN_SCHEMA_VERSION = 2
MAX_OTLP_TIMESTAMP_MS = (2**64 - 1) // 1_000_000
CLOCK_ALIGNMENT_TOLERANCE_MS = 5
MAX_TRACE_DETAIL_SPANS = 1_000


class SpanObservation(QueryModel):
    """One L1 span; arbitrary custom attributes remain behind audited raw access."""

    event_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    status: str
    duration_ms: int | None
    start_ms: int | None
    end_ms: int | None
    timing_state: str
    relation_state: str = "UNKNOWN"
    custom_attribute_count: int
    occurrence_timestamp_ms: int
    late: bool
    release_version: str | None
    release_identity_quality: str


class TraceSummary(QueryModel):
    """Trace facts within a query window, with explicit unknown total coverage."""

    trace_id: str
    span_count: int
    error_span_count: int
    duration_ms: int | None
    first_seen_ms: int
    last_seen_ms: int
    timing_available_count: int
    orphan_count: int
    invalid_relation_count: int
    late_count: int
    coverage_state: str = "UNKNOWN_COMPLETENESS"
    state: str


class TraceListResponse(QueryModel):
    """Scoped manual Trace list and aggregate reported-span statistics."""

    request_id: str
    scope: QueryScope
    window: QueryWindow
    source: str = "durable_inbox"
    state: str
    sample_count: int
    invalid_span_count: int
    expired_span_count: int
    trace_count: int
    duration_sample_count: int
    mean_span_duration_ms: float | None
    p95_span_duration_ms: float | None
    items: list[TraceSummary]
    truncated: bool


class TraceDetailResponse(QueryModel):
    """Window-relative span hierarchy; root presence never proves trace completeness."""

    request_id: str
    scope: QueryScope
    window: QueryWindow
    summary: TraceSummary
    spans: list[SpanObservation]


def valid_trace_id(value: object) -> str | None:
    """Validate nonzero W3C 128-bit hexadecimal identity."""
    if isinstance(value, str) and TRACE_ID_PATTERN.fullmatch(value) and int(value, 16):
        return value.lower()
    return None


def _span_id(value: object) -> str | None:
    if isinstance(value, str) and SPAN_ID_PATTERN.fullmatch(value) and int(value, 16):
        return value.lower()
    return None


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= MAX_OTLP_TIMESTAMP_MS else None
    if isinstance(value, str) and len(value) <= 20 and INTEGER_PATTERN.fullmatch(value):
        parsed = int(value)
        return parsed if parsed <= MAX_OTLP_TIMESTAMP_MS else None
    return None


def parse_span(
    payload: dict[str, Any],
    *,
    event_id: str,
    occurrence_ms: int,
    received_at: datetime,
    late_after_ms: int,
    release_version: str | None,
    release_quality: str,
) -> SpanObservation | None:
    """Read the exact default SDK trace contract; preserve invalid time as a quality state."""
    if payload.get("module") != "trace" or payload.get("kind") != "METRIC":
        return None
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        return None
    trace_id, span_id = valid_trace_id(fields.get("traceId")), _span_id(fields.get("spanId"))
    if trace_id is None or span_id is None:
        return None
    raw_parent = fields.get("parentSpanId")
    parent = _span_id(raw_parent) if raw_parent is not None else None
    parent_invalid = raw_parent is not None and parent is None
    duration = _integer(fields.get("duration_ms"))
    status = fields.get("status")
    status = status if isinstance(status, str) and status in {"OK", "ERROR"} else "UNKNOWN"
    start, end = _integer(fields.get("startTimestampMs")), _integer(fields.get("endTimestampMs"))
    timing_state = "TIMING_UNAVAILABLE"
    if _integer(fields.get("spanSchemaVersion")) == TRACE_SPAN_SCHEMA_VERSION:
        if start is None or end is None or duration is None or start == 0:
            timing_state = "INVALID_TIMING"
        elif end < start or abs(end - start - duration) > CLOCK_ALIGNMENT_TOLERANCE_MS:
            # Wall-clock jumps must not be "fixed" by subtracting monotonic duration.
            timing_state = "CLOCK_SKEW"
        else:
            timing_state = "AVAILABLE"
    else:
        start, end = None, None
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    name = payload.get("name")
    return SpanObservation(
        event_id=event_id,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        name=name if isinstance(name, str) else "",
        status=status,
        duration_ms=duration,
        start_ms=start,
        end_ms=end,
        timing_state=timing_state,
        relation_state="INVALID_PARENT" if parent_invalid else "UNKNOWN",
        custom_attribute_count=sum(str(key).startswith("attr_") for key in fields),
        occurrence_timestamp_ms=occurrence_ms,
        late=int(received_at.timestamp() * 1000) - occurrence_ms > late_after_ms,
        release_version=release_version,
        release_identity_quality=release_quality,
    )


def span_for_event(event: InboxEvent) -> SpanObservation | None:
    """Parse one persisted row for OTLP mapping, using only its real event contract."""
    return parse_span(
        event.payload_json,
        event_id=event.event_id,
        occurrence_ms=event.occurrence_timestamp_ms or event.event_timestamp_ms,
        received_at=event.received_at,
        late_after_ms=0,
        release_version=event.app_version,
        release_quality=event.release_identity_quality,
    )


async def load_spans(
    session: AsyncSession,
    principal: QueryPrincipal,
    settings: Settings,
    from_ms: int,
    to_ms: int,
    release_version: str | None,
    trace_id: str | None = None,
) -> tuple[list[SpanObservation], int, int]:
    """Scan exact scoped trace events and reject excess before returning any partial facts."""
    statement = select(
        InboxEvent.event_id,
        InboxEvent.payload_json,
        InboxEvent.occurrence_timestamp_ms,
        InboxEvent.received_at,
        InboxEvent.app_version,
        InboxEvent.release_identity_quality,
        InboxEvent.raw_pruned_at,
    ).where(
        InboxEvent.tenant_id == principal.tenant_id,
        InboxEvent.app_id == principal.app_id,
        InboxEvent.environment == principal.environment,
        InboxEvent.payload_json["module"].as_string() == "trace",
        InboxEvent.occurrence_timestamp_ms >= from_ms,
        InboxEvent.occurrence_timestamp_ms < to_ms,
    )
    if release_version is not None:
        statement = statement.where(InboxEvent.app_version == release_version)
    if trace_id is not None:
        # Case-normalized IDs are allowed on the wire; do not truncate an unbounded scan.
        statement = statement.where(
            func.lower(InboxEvent.payload_json["fields"]["traceId"].as_string()) == trace_id
        )
    rows = (await _execute_bounded(session, statement.limit(settings.query_max_rows + 1))).all()
    if len(rows) > settings.query_max_rows:
        raise ApiError(422, "query_row_limit", "Reduce the time window; no partial data returned")
    spans, invalid, expired = [], 0, 0
    for row in rows:
        if row.raw_pruned_at is not None:
            expired += 1
            continue
        span = parse_span(
            row.payload_json,
            event_id=row.event_id,
            occurrence_ms=row.occurrence_timestamp_ms,
            received_at=row.received_at,
            late_after_ms=settings.query_late_after_seconds * 1000,
            release_version=row.app_version,
            release_quality=row.release_identity_quality,
        )
        if span is None:
            invalid += 1
        else:
            spans.append(span)
    return spans, invalid, expired


def summarize_trace(trace_id: str, spans: list[SpanObservation]) -> TraceSummary:
    """Classify observed graph defects without inferring missing spans or instrumentation."""
    counts = Counter(span.span_id for span in spans)
    by_id = {span.span_id: span for span in spans}
    cyclic: dict[str, bool] = {}
    for identifier in by_id:
        if identifier in cyclic:
            continue
        trail: set[str] = set()
        current: str | None = identifier
        while current in by_id and current not in cyclic and current not in trail:
            trail.add(current)
            current = by_id[current].parent_span_id
        result = current in trail or (current is not None and cyclic.get(current, False))
        for node in trail:
            cyclic[node] = result
    for span in spans:
        if span.relation_state == "INVALID_PARENT":
            continue
        if counts[span.span_id] > 1:
            span.relation_state = "DUPLICATE_ID"
        elif span.parent_span_id is None:
            span.relation_state = "ROOT"
        elif span.parent_span_id not in by_id:
            span.relation_state = "ORPHAN"
        elif counts[span.parent_span_id] > 1:
            span.relation_state = "AMBIGUOUS_PARENT"
        else:
            span.relation_state = "CYCLE" if cyclic[span.span_id] else "CHILD"
    timed = [span for span in spans if span.timing_state == "AVAILABLE"]
    invalid_relations = sum(
        span.relation_state in {"INVALID_PARENT", "CYCLE", "DUPLICATE_ID", "AMBIGUOUS_PARENT"}
        for span in spans
    )
    starts = [span.start_ms for span in timed if span.start_ms is not None]
    ends = [span.end_ms for span in timed if span.end_ms is not None]
    orphan_count = sum(span.relation_state == "ORPHAN" for span in spans)
    return TraceSummary(
        trace_id=trace_id,
        span_count=len(spans),
        error_span_count=sum(span.status == "ERROR" for span in spans),
        duration_ms=max(ends) - min(starts) if len(timed) == len(spans) and timed else None,
        first_seen_ms=min(span.occurrence_timestamp_ms for span in spans),
        last_seen_ms=max(span.occurrence_timestamp_ms for span in spans),
        timing_available_count=len(timed),
        orphan_count=orphan_count,
        invalid_relation_count=invalid_relations,
        late_count=sum(span.late for span in spans),
        coverage_state="PARTIAL" if orphan_count else "UNKNOWN_COMPLETENESS",
        state=(
            "DEGRADED"
            if invalid_relations
            or orphan_count
            or len(timed) != len(spans)
            or any(span.status == "UNKNOWN" or span.duration_ms is None for span in spans)
            else "PRESENT"
        ),
    )


def build_trace_list(
    spans: list[SpanObservation],
    invalid: int,
    principal: QueryPrincipal,
    request_id: str,
    from_ms: int,
    to_ms: int,
    limit: int,
    expired: int = 0,
) -> TraceListResponse:
    """Aggregate reported spans separately from trace duration and unknown completeness."""
    grouped: dict[str, list[SpanObservation]] = defaultdict(list)
    for span in spans:
        grouped[span.trace_id].append(span)
    summaries = [summarize_trace(trace_id, group) for trace_id, group in grouped.items()]
    summaries.sort(key=lambda item: (-item.last_seen_ms, item.trace_id))
    durations = [float(span.duration_ms) for span in spans if span.duration_ms is not None]
    return TraceListResponse(
        request_id=request_id,
        scope=scope_for(principal),
        window=QueryWindow(from_ms=from_ms, to_ms=to_ms),
        state=(
            "DEGRADED"
            if invalid or (expired and spans) or any(item.state == "DEGRADED" for item in summaries)
            else "PRESENT"
            if spans
            else "UNAVAILABLE"
            if expired
            else "NO_DATA"
        ),
        sample_count=len(spans),
        invalid_span_count=invalid,
        expired_span_count=expired,
        trace_count=len(summaries),
        duration_sample_count=len(durations),
        mean_span_duration_ms=math.fsum(durations) / len(durations) if durations else None,
        p95_span_duration_ms=percentile(durations, 0.95),
        items=summaries[:limit],
        truncated=len(summaries) > limit,
    )
