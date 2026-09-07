"""Query/BFF contracts, bounded aggregation, and authenticated opaque cursors."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from androidapm_server.constants import (
    QUERY_STATE_DEGRADED,
    QUERY_STATE_LATE,
    QUERY_STATE_NO_DATA,
    QUERY_STATE_PRESENT,
    QUERY_STATE_UNAVAILABLE,
    QUERY_STATE_UNKNOWN_COVERAGE,
    QUERY_STATE_ZERO,
)
from androidapm_server.db.models import InboxEvent, ReleaseDecision
from androidapm_server.db.query import QueryFact
from androidapm_server.domain import IdentityQuality
from androidapm_server.errors import ApiError
from androidapm_server.query_auth import QueryPrincipal

QUERY_SOURCE = "durable_inbox"
QUERY_CURSOR_VERSION = 1
SESSION_UNAVAILABLE_REASON = "SESSION_ID_NOT_PROVIDED"
OCCURRENCE_UNAVAILABLE_REASON = "OCCURRENCE_IDENTITY_NOT_PROVIDED"
INSTALLATION_UNAVAILABLE_REASON = "INSTALLATION_IDENTITY_NOT_PROVIDED"
SDK_HEALTH_UNAVAILABLE_REASON = "SDK_HEALTH_NOT_PROVIDED"
INCIDENT_EVENTS = frozenset({("crash", "java_crash"), ("anr", "anr_detected")})


def _to_camel(value: str) -> str:
    """Convert internal snake_case names to stable lowerCamelCase API fields."""
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class QueryModel(BaseModel):
    """Base for strict lowerCamelCase Query/BFF request and response models."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class QueryScope(QueryModel):
    """Scope derived exclusively from an authenticated query credential."""

    tenant_id: str
    app_id: str
    environment: str


class QueryWindow(QueryModel):
    """Half-open occurrence-time window used by product queries."""

    from_ms: int
    to_ms: int


class MetricResult(QueryModel):
    """State-bearing metric that never requires a client to infer missingness."""

    state: str
    value: int | float | None = None
    numerator: int | float | None = None
    denominator: int | float | None = None
    sample_count: int
    coverage: float | None = None
    as_of_ms: int
    source: str = QUERY_SOURCE
    reason: str | None = None


class ReleaseMetricSet(QueryModel):
    """First-slice metrics for one occurrence-bound Android release."""

    java_crash_events: MetricResult
    anr_events: MetricResult
    affected_installations: MetricResult
    active_installations: MetricResult
    affected_installation_ratio: MetricResult
    incident_fingerprint_count: MetricResult
    late_ratio: MetricResult
    sdk_drop_rate: MetricResult
    crash_free_sessions: MetricResult
    anr_free_sessions: MetricResult


class ReleaseSlice(QueryModel):
    """One selected release plus exclusions and quality-aware metrics."""

    release_version: str
    state: str
    declared_sample_count: int
    eligible_sample_count: int
    excluded_non_occurrence_count: int
    release_identity_coverage: float | None
    installation_identity_coverage: float | None
    metrics: ReleaseMetricSet


class ReleaseComparison(QueryModel):
    """Simple deltas that remain null whenever either source metric is unavailable."""

    java_crash_event_delta: int | None
    anr_event_delta: int | None
    affected_installation_delta: int | None
    affected_installation_ratio_delta: float | None


class ReleaseTrendPoint(QueryModel):
    """Exact occurrence-time incident counts for both compared releases."""

    bucket_start_ms: int
    bucket_end_ms: int
    new_java_crash_events: int
    new_anr_events: int
    baseline_java_crash_events: int
    baseline_anr_events: int


class ReleaseHealthResponse(QueryModel):
    """Bounded new-versus-baseline health contract for the desktop release flow."""

    request_id: str
    scope: QueryScope
    window: QueryWindow
    new_release: ReleaseSlice
    baseline_release: ReleaseSlice
    comparison: ReleaseComparison
    trend: list[ReleaseTrendPoint]


class FingerprintItem(QueryModel):
    """Aggregate incident fingerprint without raw stack or exception content."""

    fingerprint: str
    event_family: str
    event_count: int
    affected_installation_count: int
    first_seen_ms: int
    last_seen_ms: int


class FingerprintResponse(QueryModel):
    """Bounded top-fingerprint result for one occurrence-bound release."""

    request_id: str
    scope: QueryScope
    window: QueryWindow
    release_version: str
    state: str
    sample_count: int
    fingerprint_coverage: float | None
    items: list[FingerprintItem]


class IssueDistributionItem(QueryModel):
    """One bounded, low-cardinality Issue breakdown row."""

    label: str
    event_count: int
    affected_installation_count: int


class IssueDistribution(QueryModel):
    """State-bearing Issue dimension that never invents unavailable resource data."""

    dimension: str
    state: str
    reason: str | None = None
    items: list[IssueDistributionItem]


class IssueTrendPoint(QueryModel):
    """One occurrence-time bucket for a stable incident fingerprint."""

    bucket_start_ms: int
    bucket_end_ms: int
    event_count: int
    affected_installation_count: int


class IssueDetailResponse(QueryModel):
    """L0 Issue aggregate used by the stable fingerprint detail route."""

    request_id: str
    scope: QueryScope
    window: QueryWindow
    fingerprint: str
    state: str
    reason: str | None = None
    event_family: str | None = None
    event_count: int
    affected_installation_count: int
    first_seen_ms: int | None = None
    last_seen_ms: int | None = None
    trend: list[IssueTrendPoint]
    releases: IssueDistribution
    scenes: IssueDistribution
    device_models: IssueDistribution
    android_versions: IssueDistribution


class DataQualityResponse(QueryModel):
    """Quality gates explaining whether a release query is safe to interpret."""

    request_id: str
    scope: QueryScope
    window: QueryWindow
    release_version: str | None
    state: str
    sample_count: int
    protocol_counts: dict[str, int]
    schema_version_counts: dict[str, int]
    inbox_status_counts: dict[str, int]
    release_identity: MetricResult
    installation_identity: MetricResult
    sdk_health: MetricResult
    late_data: MetricResult


class SymbolizationSummary(QueryModel):
    """Non-sensitive symbolization state and tool provenance for L1 responses."""

    status: str
    fingerprint: str | None
    tool_name: str | None
    tool_version: str | None
    last_error_code: str | None


class EventSummary(QueryModel):
    """Allow-listed L1 event metadata with no arbitrary raw fields."""

    event_id: str
    module: str
    name: str
    event_family: str
    app_version: str | None
    app_build: str | None
    version_code: str | None
    variant: str | None
    release_identity_quality: str
    installation_identity_quality: str
    installation_hmac: str | None
    occurrence_timestamp_ms: int
    collection_timestamp_ms: int | None
    received_at: datetime
    timestamp_quality: str
    scene: str | None
    process_name: str | None
    thread_name: str | None
    foreground: bool | None
    incident_fingerprint: str | None
    inbox_status: str
    symbolization: SymbolizationSummary | None


class EventPageResponse(QueryModel):
    """Opaque-cursor event page whose scope and filters are server-bound."""

    request_id: str
    scope: QueryScope
    window: QueryWindow
    items: list[EventSummary]
    next_cursor: str | None


class EventMetadataResponse(QueryModel):
    """One L1 event plus safe normalization state, without raw evidence."""

    request_id: str
    scope: QueryScope
    event: EventSummary
    schema_version: str
    protocol: str
    normalization_version: int
    field_states: dict[str, str]


class RawAccessRequest(QueryModel):
    """Explicit L2 purpose and reason required for an audited raw read."""

    purpose_code: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=10, max_length=512)


class RawEventResponse(QueryModel):
    """Audited L2 evidence returned only after its audit transaction commits."""

    request_id: str
    scope: QueryScope
    event: EventSummary
    raw_payload: dict[str, Any]
    symbolized_result: dict[str, Any] | None


class ReleaseEvidence(QueryModel):
    """Allow-listed evidence summary attached to a human release decision."""

    release_state: str
    baseline_state: str | None = None
    java_crash_events: int | None = Field(default=None, ge=0)
    anr_events: int | None = Field(default=None, ge=0)
    affected_installations: int | None = Field(default=None, ge=0)
    active_installations: int | None = Field(default=None, ge=0)
    affected_installation_ratio: float | None = Field(default=None, ge=0, le=1)
    data_quality_state: str
    query_request_id: str = Field(min_length=1, max_length=64)


class ReleaseDecisionRequest(QueryModel):
    """Human-owned release action with a bounded evidence window and rationale."""

    release_version: str = Field(min_length=1, max_length=128)
    decision: str = Field(min_length=1, max_length=32)
    evidence_from_ms: int = Field(gt=0)
    evidence_to_ms: int = Field(gt=0)
    reason: str = Field(min_length=10, max_length=2_000)
    evidence: ReleaseEvidence


class ReleaseDecisionResponse(QueryModel):
    """Persisted human decision; the service does not automate the action."""

    id: int
    release_version: str
    decision: str
    actor: str
    evidence_from_ms: int
    evidence_to_ms: int
    reason: str
    evidence: ReleaseEvidence
    created_at: datetime


class ReleaseDecisionListResponse(QueryModel):
    """Recent human decisions within the credential's fixed scope."""

    request_id: str
    scope: QueryScope
    items: list[ReleaseDecisionResponse]


def scope_for(principal: QueryPrincipal) -> QueryScope:
    """Create a response scope from authenticated storage, never request parameters."""
    return QueryScope(
        tenant_id=principal.tenant_id,
        app_id=principal.app_id,
        environment=principal.environment,
    )


def build_release_health(
    request_id: str,
    principal: QueryPrincipal,
    facts: list[QueryFact],
    new_release: str,
    baseline_release: str,
    from_ms: int,
    to_ms: int,
    late_after_ms: int,
) -> ReleaseHealthResponse:
    """Aggregate two explicit releases without silently choosing a baseline."""
    new_slice = _release_slice(facts, new_release, to_ms, late_after_ms)
    baseline_slice = _release_slice(facts, baseline_release, to_ms, late_after_ms)
    return ReleaseHealthResponse(
        request_id=request_id,
        scope=scope_for(principal),
        window=QueryWindow(from_ms=from_ms, to_ms=to_ms),
        new_release=new_slice,
        baseline_release=baseline_slice,
        comparison=ReleaseComparison(
            java_crash_event_delta=_metric_delta(
                new_slice.metrics.java_crash_events,
                baseline_slice.metrics.java_crash_events,
            ),
            anr_event_delta=_metric_delta(
                new_slice.metrics.anr_events,
                baseline_slice.metrics.anr_events,
            ),
            affected_installation_delta=_metric_delta(
                new_slice.metrics.affected_installations,
                baseline_slice.metrics.affected_installations,
            ),
            affected_installation_ratio_delta=_ratio_delta(
                new_slice.metrics.affected_installation_ratio,
                baseline_slice.metrics.affected_installation_ratio,
            ),
        ),
        trend=_release_incident_trend(
            facts,
            new_release,
            baseline_release,
            from_ms,
            to_ms,
        ),
    )


def build_top_fingerprints(
    request_id: str,
    principal: QueryPrincipal,
    facts: list[QueryFact],
    release_version: str,
    from_ms: int,
    to_ms: int,
    limit: int,
) -> FingerprintResponse:
    """Aggregate exact Crash/ANR fingerprints while keeping all raw evidence private."""
    declared = [fact for fact in facts if fact.app_version == release_version]
    eligible = [fact for fact in declared if _occurrence_bound(fact)]
    incidents = [fact for fact in eligible if (fact.module, fact.name) in INCIDENT_EVENTS]
    fingerprinted = [fact for fact in incidents if fact.incident_fingerprint is not None]
    grouped: dict[tuple[str, str], list[QueryFact]] = defaultdict(list)
    for fact in fingerprinted:
        fingerprint = fact.incident_fingerprint
        if fingerprint is not None:
            grouped[(fingerprint, _event_family(fact.module, fact.name))].append(fact)
    items = [
        FingerprintItem(
            fingerprint=fingerprint,
            event_family=event_family,
            event_count=len(group),
            affected_installation_count=len(
                {fact.installation_hmac for fact in group if fact.installation_hmac is not None}
            ),
            first_seen_ms=min(fact.occurrence_timestamp_ms for fact in group),
            last_seen_ms=max(fact.occurrence_timestamp_ms for fact in group),
        )
        for (fingerprint, event_family), group in grouped.items()
    ]
    items.sort(key=lambda item: (-item.event_count, -item.last_seen_ms, item.fingerprint))
    coverage = len(fingerprinted) / len(incidents) if incidents else None
    if not declared:
        state = QUERY_STATE_NO_DATA
    elif not eligible:
        state = QUERY_STATE_UNAVAILABLE
    elif not incidents:
        state = QUERY_STATE_ZERO
    elif not fingerprinted:
        state = QUERY_STATE_UNAVAILABLE
    elif len(fingerprinted) < len(incidents):
        state = QUERY_STATE_DEGRADED
    else:
        state = QUERY_STATE_PRESENT
    return FingerprintResponse(
        request_id=request_id,
        scope=scope_for(principal),
        window=QueryWindow(from_ms=from_ms, to_ms=to_ms),
        release_version=release_version,
        state=state,
        sample_count=len(incidents),
        fingerprint_coverage=coverage,
        items=items[:limit],
    )


def build_issue_detail(
    request_id: str,
    principal: QueryPrincipal,
    facts: list[QueryFact],
    fingerprint: str,
    from_ms: int,
    to_ms: int,
) -> IssueDetailResponse:
    """Aggregate one stable fingerprint without exposing L1/L2 event content."""
    declared = [
        fact
        for fact in facts
        if fact.incident_fingerprint == fingerprint
        and (fact.module, fact.name) in INCIDENT_EVENTS
    ]
    eligible = [fact for fact in declared if _occurrence_bound(fact)]
    if not declared:
        state = QUERY_STATE_NO_DATA
        reason = "FINGERPRINT_NOT_FOUND"
    elif not eligible:
        state = QUERY_STATE_UNAVAILABLE
        reason = OCCURRENCE_UNAVAILABLE_REASON
    else:
        state = QUERY_STATE_PRESENT
        reason = None

    families = {_event_family(fact.module, fact.name) for fact in eligible}
    event_family = next(iter(families)) if len(families) == 1 else "MIXED" if families else None
    release_distribution = _issue_distribution(
        "release",
        eligible,
        lambda fact: fact.app_version,
        unavailable_reason=OCCURRENCE_UNAVAILABLE_REASON,
    )
    scene_distribution = _issue_distribution(
        "scene",
        eligible,
        lambda fact: fact.scene,
        unavailable_reason="SCENE_NOT_PROVIDED",
    )
    unavailable_resource = IssueDistribution(
        dimension="device_model",
        state=QUERY_STATE_UNKNOWN_COVERAGE,
        reason="STANDARD_DEVICE_RESOURCE_NOT_PROVIDED",
        items=[],
    )
    unavailable_os = IssueDistribution(
        dimension="android_version",
        state=QUERY_STATE_UNKNOWN_COVERAGE,
        reason="STANDARD_OS_RESOURCE_NOT_PROVIDED",
        items=[],
    )
    return IssueDetailResponse(
        request_id=request_id,
        scope=scope_for(principal),
        window=QueryWindow(from_ms=from_ms, to_ms=to_ms),
        fingerprint=fingerprint,
        state=state,
        reason=reason,
        event_family=event_family,
        event_count=len(eligible),
        affected_installation_count=len(
            {fact.installation_hmac for fact in eligible if fact.installation_hmac is not None}
        ),
        first_seen_ms=min((fact.occurrence_timestamp_ms for fact in eligible), default=None),
        last_seen_ms=max((fact.occurrence_timestamp_ms for fact in eligible), default=None),
        trend=_issue_trend(eligible, from_ms, to_ms),
        releases=release_distribution,
        scenes=scene_distribution,
        device_models=unavailable_resource,
        android_versions=unavailable_os,
    )


def _issue_distribution(
    dimension: str,
    facts: list[QueryFact],
    value_for: Callable[[QueryFact], str | None],
    *,
    unavailable_reason: str,
) -> IssueDistribution:
    """Build a deterministic top-10 aggregate for one allow-listed Issue dimension."""
    grouped: dict[str, list[QueryFact]] = defaultdict(list)
    for fact in facts:
        label = value_for(fact)
        if label:
            grouped[str(label)].append(fact)
    items = [
        IssueDistributionItem(
            label=label,
            event_count=len(group),
            affected_installation_count=len(
                {fact.installation_hmac for fact in group if fact.installation_hmac is not None}
            ),
        )
        for label, group in grouped.items()
    ]
    items.sort(key=lambda item: (-item.event_count, item.label))
    if not facts:
        state = QUERY_STATE_NO_DATA
        reason = None
    elif not items:
        state = QUERY_STATE_UNAVAILABLE
        reason = unavailable_reason
    else:
        state = QUERY_STATE_PRESENT
        reason = None
    return IssueDistribution(
        dimension=dimension,
        state=state,
        reason=reason,
        items=items[:10],
    )


def _issue_trend(
    facts: list[QueryFact],
    from_ms: int,
    to_ms: int,
) -> list[IssueTrendPoint]:
    """Return at most 24 explicit occurrence buckets, including real zero buckets."""
    target_buckets = 24
    minute_ms = 60_000
    window_ms = to_ms - from_ms
    raw_bucket_ms = (window_ms + target_buckets - 1) // target_buckets
    bucket_ms = max(minute_ms, ((raw_bucket_ms + minute_ms - 1) // minute_ms) * minute_ms)
    bucket_count = (window_ms + bucket_ms - 1) // bucket_ms
    grouped: dict[int, list[QueryFact]] = defaultdict(list)
    for fact in facts:
        bucket_index = (fact.occurrence_timestamp_ms - from_ms) // bucket_ms
        if 0 <= bucket_index < bucket_count:
            grouped[bucket_index].append(fact)
    points: list[IssueTrendPoint] = []
    for index in range(bucket_count):
        group = grouped[index]
        bucket_start = from_ms + index * bucket_ms
        points.append(
            IssueTrendPoint(
                bucket_start_ms=bucket_start,
                bucket_end_ms=min(bucket_start + bucket_ms, to_ms),
                event_count=len(group),
                affected_installation_count=len(
                    {fact.installation_hmac for fact in group if fact.installation_hmac is not None}
                ),
            )
        )
    return points


def _release_incident_trend(
    facts: list[QueryFact],
    new_release: str,
    baseline_release: str,
    from_ms: int,
    to_ms: int,
) -> list[ReleaseTrendPoint]:
    """Return bounded real-zero buckets for exact Crash/ANR release comparison."""
    target_buckets = 24
    minute_ms = 60_000
    window_ms = to_ms - from_ms
    raw_bucket_ms = (window_ms + target_buckets - 1) // target_buckets
    bucket_ms = max(minute_ms, ((raw_bucket_ms + minute_ms - 1) // minute_ms) * minute_ms)
    bucket_count = (window_ms + bucket_ms - 1) // bucket_ms
    grouped: dict[int, Counter[tuple[str | None, str]]] = defaultdict(Counter)
    for fact in facts:
        if not _occurrence_bound(fact) or (fact.module, fact.name) not in INCIDENT_EVENTS:
            continue
        if fact.app_version not in {new_release, baseline_release}:
            continue
        bucket_index = (fact.occurrence_timestamp_ms - from_ms) // bucket_ms
        if 0 <= bucket_index < bucket_count:
            grouped[bucket_index][(fact.app_version, _event_family(fact.module, fact.name))] += 1
    points: list[ReleaseTrendPoint] = []
    for index in range(bucket_count):
        counts = grouped[index]
        bucket_start = from_ms + index * bucket_ms
        points.append(
            ReleaseTrendPoint(
                bucket_start_ms=bucket_start,
                bucket_end_ms=min(bucket_start + bucket_ms, to_ms),
                new_java_crash_events=counts[(new_release, "JAVA_CRASH")],
                new_anr_events=counts[(new_release, "ANR")],
                baseline_java_crash_events=counts[(baseline_release, "JAVA_CRASH")],
                baseline_anr_events=counts[(baseline_release, "ANR")],
            )
        )
    return points


def build_data_quality(
    request_id: str,
    principal: QueryPrincipal,
    facts: list[QueryFact],
    release_version: str | None,
    from_ms: int,
    to_ms: int,
    late_after_ms: int,
) -> DataQualityResponse:
    """Explain identity, SDK-health, late-data, and inbox coverage for one window."""
    selected = (
        [fact for fact in facts if fact.app_version == release_version]
        if release_version is not None
        else facts
    )
    sample_count = len(selected)
    occurrence_count = sum(_occurrence_bound(fact) for fact in selected)
    installation_count = sum(
        _occurrence_bound(fact) and fact.installation_hmac is not None for fact in selected
    )
    sdk_facts = [fact for fact in selected if (fact.module, fact.name) == ("core", "sdk_health")]
    dropped = [
        fact
        for fact in sdk_facts
        if (fact.sdk_drop_count is not None and fact.sdk_drop_count > 0)
        or (fact.sdk_drop_rate is not None and fact.sdk_drop_rate > 0)
    ]
    max_drop_rate = max(
        (fact.sdk_drop_rate for fact in sdk_facts if fact.sdk_drop_rate is not None),
        default=None,
    )
    late_count = sum(_is_late(fact, late_after_ms) for fact in selected)
    release_metric = _coverage_metric(
        occurrence_count,
        sample_count,
        to_ms,
        OCCURRENCE_UNAVAILABLE_REASON,
    )
    installation_metric = _coverage_metric(
        installation_count,
        occurrence_count,
        to_ms,
        INSTALLATION_UNAVAILABLE_REASON,
    )
    if not selected:
        sdk_metric = _metric(QUERY_STATE_NO_DATA, to_ms, 0)
        late_metric = _metric(QUERY_STATE_NO_DATA, to_ms, 0)
        state = QUERY_STATE_NO_DATA
    else:
        sdk_metric = (
            _metric(
                QUERY_STATE_DEGRADED if dropped else QUERY_STATE_PRESENT,
                to_ms,
                len(sdk_facts),
                value=max_drop_rate,
                reason="SDK_REPORTED_DROPS" if dropped else None,
            )
            if sdk_facts
            else _metric(
                QUERY_STATE_UNAVAILABLE,
                to_ms,
                sample_count,
                reason=SDK_HEALTH_UNAVAILABLE_REASON,
            )
        )
        late_metric = _metric(
            QUERY_STATE_LATE if late_count else QUERY_STATE_ZERO,
            to_ms,
            sample_count,
            value=late_count / sample_count,
            numerator=late_count,
            denominator=sample_count,
        )
        if occurrence_count == 0:
            state = QUERY_STATE_UNAVAILABLE
        elif occurrence_count < sample_count or installation_count < occurrence_count or dropped:
            state = QUERY_STATE_DEGRADED
        elif late_count:
            state = QUERY_STATE_LATE
        elif not sdk_facts:
            state = QUERY_STATE_UNAVAILABLE
        else:
            state = QUERY_STATE_PRESENT
    return DataQualityResponse(
        request_id=request_id,
        scope=scope_for(principal),
        window=QueryWindow(from_ms=from_ms, to_ms=to_ms),
        release_version=release_version,
        state=state,
        sample_count=sample_count,
        protocol_counts=dict(sorted(Counter(fact.protocol for fact in selected).items())),
        schema_version_counts=dict(
            sorted(Counter(fact.schema_version for fact in selected).items())
        ),
        inbox_status_counts=dict(sorted(Counter(fact.inbox_status for fact in selected).items())),
        release_identity=release_metric,
        installation_identity=installation_metric,
        sdk_health=sdk_metric,
        late_data=late_metric,
    )


def event_summary(event: InboxEvent) -> EventSummary:
    """Project a durable event to the explicit L1 allow-list."""
    payload = event.payload_json
    normalized = event.normalized_json
    symbol_job = event.symbolization_job
    symbolization = None
    if symbol_job is not None:
        symbolization = SymbolizationSummary(
            status=symbol_job.status,
            fingerprint=symbol_job.fingerprint_sha256,
            tool_name=symbol_job.tool_name,
            tool_version=symbol_job.tool_version,
            last_error_code=symbol_job.last_error_code,
        )
    return EventSummary(
        event_id=event.event_id,
        module=_string_value(payload, "module") or "",
        name=_string_value(payload, "name") or "",
        event_family=_string_value(normalized, "event_family") or "UNREGISTERED",
        app_version=event.app_version,
        app_build=event.app_build,
        version_code=event.version_code,
        variant=event.variant,
        release_identity_quality=event.release_identity_quality,
        installation_identity_quality=event.installation_identity_quality,
        installation_hmac=event.installation_hmac,
        occurrence_timestamp_ms=event.occurrence_timestamp_ms,
        collection_timestamp_ms=event.collection_timestamp_ms,
        received_at=_aware(event.received_at),
        timestamp_quality=event.timestamp_quality,
        scene=_string_value(payload, "scene"),
        process_name=_string_value(payload, "process_name", "processName"),
        thread_name=_string_value(payload, "thread_name", "threadName"),
        foreground=_bool_value(payload, "foreground"),
        incident_fingerprint=event.incident_fingerprint,
        inbox_status=event.status,
        symbolization=symbolization,
    )


def field_states(event: InboxEvent) -> dict[str, str]:
    """Return only normalization availability states, never registered raw values."""
    value = event.normalized_json.get("field_states")
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def decision_response(decision: ReleaseDecision) -> ReleaseDecisionResponse:
    """Convert a durable decision and its allow-listed evidence back to the API model."""
    return ReleaseDecisionResponse(
        id=decision.id,
        release_version=decision.release_version,
        decision=decision.decision,
        actor=decision.actor,
        evidence_from_ms=decision.evidence_from_ms,
        evidence_to_ms=decision.evidence_to_ms,
        reason=decision.reason,
        evidence=ReleaseEvidence.model_validate(decision.evidence_json),
        created_at=_aware(decision.created_at),
    )


def cursor_filter_hash(
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
    release_version: str | None,
    module: str | None,
    name: str | None,
    fingerprint: str | None,
) -> str:
    """Bind an event cursor to scope, window, and every active filter."""
    value = {
        "tenant": principal.tenant_id,
        "app": principal.app_id,
        "environment": principal.environment,
        "from": from_ms,
        "to": to_ms,
        "release": release_version,
        "module": module,
        "name": name,
        "fingerprint": fingerprint,
    }
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def encode_cursor(
    timestamp_ms: int,
    row_id: int,
    filter_hash: str,
    key_b64: str,
) -> str:
    """Create an integrity-protected URL-safe cursor with no authorization semantics."""
    key = _cursor_key(key_b64)
    payload = json.dumps(
        {"v": QUERY_CURSOR_VERSION, "t": timestamp_ms, "i": row_id, "f": filter_hash},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64url(payload)}.{_b64url(signature)}"


def decode_cursor(cursor: str, filter_hash: str, key_b64: str) -> tuple[int, int]:
    """Verify a cursor signature and reject reuse under a different scoped query."""
    key = _cursor_key(key_b64)
    try:
        encoded_payload, encoded_signature = cursor.split(".", maxsplit=1)
        payload = _b64url_decode(encoded_payload)
        signature = _b64url_decode(encoded_signature)
        expected = hmac.new(key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature mismatch")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("payload is not an object")
        version = value.get("v")
        timestamp_ms = value.get("t")
        row_id = value.get("i")
        bound_filter = value.get("f")
        if (
            version != QUERY_CURSOR_VERSION
            or not isinstance(timestamp_ms, int)
            or isinstance(timestamp_ms, bool)
            or not isinstance(row_id, int)
            or isinstance(row_id, bool)
            or timestamp_ms <= 0
            or row_id <= 0
            or not isinstance(bound_filter, str)
            or not hmac.compare_digest(bound_filter, filter_hash)
        ):
            raise ValueError("cursor payload is invalid")
    except (
        ValueError,
        TypeError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        binascii.Error,
    ) as error:
        raise ApiError(400, "invalid_cursor", "The pagination cursor is invalid") from error
    return timestamp_ms, row_id


def _release_slice(
    facts: list[QueryFact],
    release_version: str,
    as_of_ms: int,
    late_after_ms: int,
) -> ReleaseSlice:
    """Build quality-gated metrics for one explicit release value."""
    declared = [fact for fact in facts if fact.app_version == release_version]
    eligible = [fact for fact in declared if _occurrence_bound(fact)]
    incidents = [fact for fact in eligible if (fact.module, fact.name) in INCIDENT_EVENTS]
    java_count = sum((fact.module, fact.name) == ("crash", "java_crash") for fact in incidents)
    anr_count = sum((fact.module, fact.name) == ("anr", "anr_detected") for fact in incidents)
    active_installations = {
        fact.installation_hmac for fact in eligible if fact.installation_hmac is not None
    }
    affected_installations = {
        fact.installation_hmac for fact in incidents if fact.installation_hmac is not None
    }
    installation_eligible = sum(fact.installation_hmac is not None for fact in eligible)
    release_coverage = len(eligible) / len(declared) if declared else None
    installation_coverage = installation_eligible / len(eligible) if eligible else None
    late_count = sum(_is_late(fact, late_after_ms) for fact in eligible)
    sdk_facts = [fact for fact in eligible if (fact.module, fact.name) == ("core", "sdk_health")]
    dropped = [
        fact
        for fact in sdk_facts
        if (fact.sdk_drop_count is not None and fact.sdk_drop_count > 0)
        or (fact.sdk_drop_rate is not None and fact.sdk_drop_rate > 0)
    ]
    quality_state, quality_reason = _release_quality_state(
        declared,
        eligible,
        installation_eligible,
        dropped,
        late_count,
    )
    java_metric = _count_metric(java_count, len(eligible), as_of_ms, quality_state, quality_reason)
    anr_metric = _count_metric(anr_count, len(eligible), as_of_ms, quality_state, quality_reason)
    if not declared:
        active_metric = _metric(QUERY_STATE_NO_DATA, as_of_ms, 0)
        affected_metric = _metric(QUERY_STATE_NO_DATA, as_of_ms, 0)
        ratio_metric = _metric(QUERY_STATE_NO_DATA, as_of_ms, 0)
    elif not eligible:
        active_metric = _metric(
            QUERY_STATE_UNAVAILABLE,
            as_of_ms,
            len(declared),
            reason=OCCURRENCE_UNAVAILABLE_REASON,
        )
        affected_metric = active_metric.model_copy()
        ratio_metric = active_metric.model_copy()
    elif installation_eligible == 0:
        active_metric = _metric(
            QUERY_STATE_UNAVAILABLE,
            as_of_ms,
            len(eligible),
            reason=INSTALLATION_UNAVAILABLE_REASON,
        )
        affected_metric = active_metric.model_copy()
        ratio_metric = active_metric.model_copy()
    else:
        install_state = (
            QUERY_STATE_DEGRADED if installation_eligible < len(eligible) else QUERY_STATE_PRESENT
        )
        active_count = len(active_installations)
        affected_count = len(affected_installations)
        active_metric = _metric(
            QUERY_STATE_ZERO if active_count == 0 else install_state,
            as_of_ms,
            len(eligible),
            value=active_count,
            coverage=installation_coverage,
        )
        affected_metric = _metric(
            QUERY_STATE_ZERO
            if affected_count == 0 and install_state == QUERY_STATE_PRESENT
            else install_state,
            as_of_ms,
            len(incidents),
            value=affected_count,
            coverage=(
                sum(fact.installation_hmac is not None for fact in incidents) / len(incidents)
                if incidents
                else 1.0
            ),
        )
        if install_state == QUERY_STATE_DEGRADED:
            ratio_metric = _metric(
                QUERY_STATE_DEGRADED,
                as_of_ms,
                len(eligible),
                numerator=affected_count,
                denominator=active_count,
                coverage=installation_coverage,
                reason="INSTALLATION_IDENTITY_COVERAGE_INCOMPLETE",
            )
        elif active_count == 0:
            ratio_metric = _metric(
                QUERY_STATE_NO_DATA,
                as_of_ms,
                len(eligible),
                numerator=affected_count,
                denominator=0,
            )
        else:
            ratio = affected_count / active_count
            ratio_metric = _metric(
                QUERY_STATE_ZERO if affected_count == 0 else QUERY_STATE_PRESENT,
                as_of_ms,
                len(eligible),
                value=ratio,
                numerator=affected_count,
                denominator=active_count,
                coverage=1.0,
            )
    fingerprints = {fact.incident_fingerprint for fact in incidents if fact.incident_fingerprint}
    fingerprint_coverage = (
        sum(fact.incident_fingerprint is not None for fact in incidents) / len(incidents)
        if incidents
        else None
    )
    if not incidents and eligible:
        fingerprint_metric = _metric(QUERY_STATE_ZERO, as_of_ms, len(eligible), value=0)
    elif incidents and not fingerprints:
        fingerprint_metric = _metric(
            QUERY_STATE_UNAVAILABLE,
            as_of_ms,
            len(incidents),
            reason="INCIDENT_FINGERPRINT_NOT_PROVIDED",
        )
    elif incidents:
        fingerprint_metric = _metric(
            QUERY_STATE_DEGRADED if fingerprint_coverage != 1.0 else QUERY_STATE_PRESENT,
            as_of_ms,
            len(incidents),
            value=len(fingerprints),
            coverage=fingerprint_coverage,
        )
    else:
        fingerprint_metric = _metric(quality_state, as_of_ms, len(declared), reason=quality_reason)
    late_metric = (
        _metric(QUERY_STATE_NO_DATA, as_of_ms, 0)
        if not declared
        else _metric(
            QUERY_STATE_UNAVAILABLE,
            as_of_ms,
            len(declared),
            reason=OCCURRENCE_UNAVAILABLE_REASON,
        )
        if not eligible
        else _metric(
            QUERY_STATE_LATE if late_count else QUERY_STATE_ZERO,
            as_of_ms,
            len(eligible),
            value=late_count / len(eligible),
            numerator=late_count,
            denominator=len(eligible),
        )
    )
    if not declared:
        sdk_metric = _metric(QUERY_STATE_NO_DATA, as_of_ms, 0)
    elif not eligible:
        sdk_metric = _metric(
            QUERY_STATE_UNAVAILABLE,
            as_of_ms,
            len(declared),
            reason=OCCURRENCE_UNAVAILABLE_REASON,
        )
    elif not sdk_facts:
        sdk_metric = _metric(
            QUERY_STATE_UNAVAILABLE,
            as_of_ms,
            len(eligible),
            reason=SDK_HEALTH_UNAVAILABLE_REASON,
        )
    else:
        max_drop_rate = max(
            (fact.sdk_drop_rate for fact in sdk_facts if fact.sdk_drop_rate is not None),
            default=0.0,
        )
        sdk_metric = _metric(
            QUERY_STATE_DEGRADED if dropped else QUERY_STATE_ZERO,
            as_of_ms,
            len(sdk_facts),
            value=max_drop_rate,
            reason="SDK_REPORTED_DROPS" if dropped else None,
        )
    session_metric = _metric(
        QUERY_STATE_UNAVAILABLE,
        as_of_ms,
        len(eligible),
        reason=SESSION_UNAVAILABLE_REASON,
    )
    return ReleaseSlice(
        release_version=release_version,
        state=quality_state,
        declared_sample_count=len(declared),
        eligible_sample_count=len(eligible),
        excluded_non_occurrence_count=len(declared) - len(eligible),
        release_identity_coverage=release_coverage,
        installation_identity_coverage=installation_coverage,
        metrics=ReleaseMetricSet(
            java_crash_events=java_metric,
            anr_events=anr_metric,
            affected_installations=affected_metric,
            active_installations=active_metric,
            affected_installation_ratio=ratio_metric,
            incident_fingerprint_count=fingerprint_metric,
            late_ratio=late_metric,
            sdk_drop_rate=sdk_metric,
            crash_free_sessions=session_metric.model_copy(),
            anr_free_sessions=session_metric.model_copy(),
        ),
    )


def _release_quality_state(
    declared: list[QueryFact],
    eligible: list[QueryFact],
    installation_eligible: int,
    dropped: list[QueryFact],
    late_count: int,
) -> tuple[str, str | None]:
    """Choose a deterministic quality state with missing identity taking precedence."""
    if not declared:
        return QUERY_STATE_NO_DATA, None
    if not eligible:
        return QUERY_STATE_UNAVAILABLE, OCCURRENCE_UNAVAILABLE_REASON
    if len(eligible) < len(declared):
        return QUERY_STATE_DEGRADED, "RELEASE_IDENTITY_COVERAGE_INCOMPLETE"
    if installation_eligible < len(eligible):
        return QUERY_STATE_DEGRADED, "INSTALLATION_IDENTITY_COVERAGE_INCOMPLETE"
    if dropped:
        return QUERY_STATE_DEGRADED, "SDK_REPORTED_DROPS"
    if late_count:
        return QUERY_STATE_LATE, "LATE_DATA_PRESENT"
    return QUERY_STATE_PRESENT, None


def _count_metric(
    count: int,
    sample_count: int,
    as_of_ms: int,
    quality_state: str,
    reason: str | None,
) -> MetricResult:
    """Keep count values visible when degraded, but never manufacture unavailable counts."""
    if quality_state in {QUERY_STATE_NO_DATA, QUERY_STATE_UNAVAILABLE}:
        return _metric(quality_state, as_of_ms, sample_count, reason=reason)
    state = (
        quality_state
        if quality_state != QUERY_STATE_PRESENT
        else (QUERY_STATE_ZERO if count == 0 else QUERY_STATE_PRESENT)
    )
    return _metric(state, as_of_ms, sample_count, value=count, reason=reason)


def _coverage_metric(
    numerator: int,
    denominator: int,
    as_of_ms: int,
    missing_reason: str,
) -> MetricResult:
    """Represent coverage without treating a missing denominator as zero health."""
    if denominator == 0:
        return _metric(QUERY_STATE_NO_DATA, as_of_ms, 0)
    if numerator == 0:
        return _metric(
            QUERY_STATE_UNAVAILABLE,
            as_of_ms,
            denominator,
            numerator=0,
            denominator=denominator,
            coverage=0.0,
            reason=missing_reason,
        )
    coverage = numerator / denominator
    return _metric(
        QUERY_STATE_PRESENT if numerator == denominator else QUERY_STATE_DEGRADED,
        as_of_ms,
        denominator,
        value=coverage,
        numerator=numerator,
        denominator=denominator,
        coverage=coverage,
        reason=None if numerator == denominator else "IDENTITY_COVERAGE_INCOMPLETE",
    )


def _metric(
    state: str,
    as_of_ms: int,
    sample_count: int,
    *,
    value: int | float | None = None,
    numerator: int | float | None = None,
    denominator: int | float | None = None,
    coverage: float | None = None,
    reason: str | None = None,
) -> MetricResult:
    """Build one uniform metric response."""
    return MetricResult(
        state=state,
        value=value,
        numerator=numerator,
        denominator=denominator,
        sample_count=sample_count,
        coverage=coverage,
        as_of_ms=as_of_ms,
        reason=reason,
    )


def _metric_delta(new: MetricResult, baseline: MetricResult) -> int | None:
    """Return an integer delta only when both metric values are concrete integers."""
    if type(new.value) is not int or type(baseline.value) is not int:
        return None
    return new.value - baseline.value


def _ratio_delta(new: MetricResult, baseline: MetricResult) -> float | None:
    """Return a ratio delta only when both gated ratios are available."""
    if new.value is None or baseline.value is None:
        return None
    if isinstance(new.value, bool) or isinstance(baseline.value, bool):
        return None
    return float(new.value) - float(baseline.value)


def _occurrence_bound(fact: QueryFact) -> bool:
    """Return whether a release identity was frozen when the event occurred."""
    return fact.release_identity_quality == IdentityQuality.OCCURRENCE_BOUND.value


def _is_late(fact: QueryFact, late_after_ms: int) -> bool:
    """Compare receive time to occurrence time without mixing either query axis."""
    received_ms = int(_aware(fact.received_at).timestamp() * 1_000)
    return received_ms - fact.occurrence_timestamp_ms > late_after_ms


def _event_family(module: str, name: str) -> str:
    """Map only exact product events; crash-module membership alone is insufficient."""
    return {
        ("crash", "java_crash"): "JAVA_CRASH",
        ("anr", "anr_detected"): "ANR",
        ("crash", "app_exit"): "APP_EXIT",
        ("core", "sdk_health"): "SDK_HEALTH",
    }.get((module, name), "UNREGISTERED")


def _string_value(value: dict[str, Any], *keys: str) -> str | None:
    """Read the first string from compatible durable field spellings."""
    for key in keys:
        item = value.get(key)
        if isinstance(item, str):
            return item
    return None


def _bool_value(value: dict[str, Any], key: str) -> bool | None:
    """Read a real boolean without coercing arbitrary raw values."""
    item = value.get(key)
    return item if isinstance(item, bool) else None


def _aware(value: datetime) -> datetime:
    """Restore UTC tzinfo for SQLite while PostgreSQL returns aware timestamps."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _cursor_key(value: str) -> bytes:
    """Decode a dedicated cursor HMAC key and fail closed when it is absent or weak."""
    try:
        key = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ApiError(
            503,
            "query_cursor_unavailable",
            "Query pagination is not configured",
            True,
        ) from error
    if len(key) < 32:
        raise ApiError(
            503,
            "query_cursor_unavailable",
            "Query pagination is not configured",
            True,
        )
    return key


def _b64url(value: bytes) -> str:
    """Encode bytes without padding for URL-safe cursor transport."""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    """Decode one unpadded URL-safe cursor component."""
    decoded = base64.b64decode(
        value + "=" * (-len(value) % 4),
        altchars=b"-_",
        validate=True,
    )
    if _b64url(decoded) != value:
        raise ValueError("cursor component is not canonical base64url")
    return decoded
