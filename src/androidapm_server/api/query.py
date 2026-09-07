"""Tenant-scoped Query/BFF endpoints for release health and incident evidence."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.config import Settings, get_settings
from androidapm_server.constants import QUERY_RAW_PURPOSES, QUERY_RELEASE_DECISIONS
from androidapm_server.db.models import AuditLog, ReleaseDecision
from androidapm_server.db.query import (
    get_scoped_event,
    list_release_decisions,
    list_scoped_events,
    load_window_facts,
)
from androidapm_server.db.session import get_session
from androidapm_server.errors import ApiError
from androidapm_server.query import (
    DataQualityResponse,
    EventMetadataResponse,
    EventPageResponse,
    FingerprintResponse,
    IssueDetailResponse,
    QueryWindow,
    RawAccessRequest,
    RawEventResponse,
    ReleaseDecisionListResponse,
    ReleaseDecisionRequest,
    ReleaseDecisionResponse,
    ReleaseHealthResponse,
    build_data_quality,
    build_issue_detail,
    build_release_health,
    build_top_fingerprints,
    cursor_filter_hash,
    decision_response,
    decode_cursor,
    encode_cursor,
    event_summary,
    field_states,
    scope_for,
)
from androidapm_server.query_auth import (
    QueryPrincipal,
    require_investigator,
)
from androidapm_server.web_auth import authenticate_query_request

router = APIRouter(prefix="/v1/query", tags=["query"])

MILLISECONDS_PER_DAY = 86_400_000
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_SCOPE_PARAMETERS = frozenset({"tenant", "tenantid", "appid", "environment"})


@router.get("/release-health", response_model=ReleaseHealthResponse)
async def get_release_health(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    new_release: Annotated[str, Query(alias="newRelease")],
    baseline_release: Annotated[str, Query(alias="baselineRelease")],
    from_ms: Annotated[int, Query(alias="fromMs")],
    to_ms: Annotated[int, Query(alias="toMs")],
) -> ReleaseHealthResponse:
    """Compare exact Crash/ANR metrics for two explicit occurrence-bound releases."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    _validate_window(from_ms, to_ms, settings)
    new_release = _validate_identifier(new_release, "newRelease", 128)
    baseline_release = _validate_identifier(baseline_release, "baselineRelease", 128)
    if new_release == baseline_release:
        raise ApiError(400, "invalid_release_comparison", "The two releases must be different")
    facts = await load_window_facts(
        session,
        principal,
        from_ms,
        to_ms,
        settings.query_max_rows,
        release_versions=(new_release, baseline_release),
        late_after_ms=settings.query_late_after_seconds * 1000,
    )
    _no_store(response)
    return build_release_health(
        request.state.request_id,
        principal,
        facts,
        new_release,
        baseline_release,
        from_ms,
        to_ms,
        settings.query_late_after_seconds * 1_000,
        settings.query_min_installations,
        settings.query_min_sdk_health_coverage,
    )


@router.get("/fingerprints", response_model=FingerprintResponse)
async def get_top_fingerprints(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    release_version: Annotated[str, Query(alias="releaseVersion")],
    from_ms: Annotated[int, Query(alias="fromMs")],
    to_ms: Annotated[int, Query(alias="toMs")],
    limit: int = 20,
) -> FingerprintResponse:
    """Return bounded exact-incident fingerprint aggregates without raw evidence."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    _validate_window(from_ms, to_ms, settings)
    release_version = _validate_identifier(release_version, "releaseVersion", 128)
    _validate_limit(limit, settings)
    facts = await load_window_facts(
        session,
        principal,
        from_ms,
        to_ms,
        settings.query_max_rows,
        release_versions=(release_version,),
        late_after_ms=settings.query_late_after_seconds * 1000,
    )
    _no_store(response)
    return build_top_fingerprints(
        request.state.request_id,
        principal,
        facts,
        release_version,
        from_ms,
        to_ms,
        limit,
    )


@router.get("/issues/{fingerprint}", response_model=IssueDetailResponse)
async def get_issue_detail(
    fingerprint: str,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    from_ms: Annotated[int, Query(alias="fromMs")],
    to_ms: Annotated[int, Query(alias="toMs")],
) -> IssueDetailResponse:
    """Return a bounded L0 trend and breakdown for one stable Issue fingerprint."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    _validate_window(from_ms, to_ms, settings)
    validated_fingerprint = _optional_fingerprint(fingerprint)
    if validated_fingerprint is None:  # pragma: no cover - the path always supplies a value
        raise ApiError(400, "invalid_query_filter", "The fingerprint filter is invalid")
    facts = await load_window_facts(
        session,
        principal,
        from_ms,
        to_ms,
        settings.query_max_rows,
        fingerprint=validated_fingerprint,
        late_after_ms=settings.query_late_after_seconds * 1000,
    )
    _no_store(response)
    return build_issue_detail(
        request.state.request_id,
        principal,
        facts,
        validated_fingerprint,
        from_ms,
        to_ms,
    )


@router.get("/data-quality", response_model=DataQualityResponse)
async def get_data_quality(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    from_ms: Annotated[int, Query(alias="fromMs")],
    to_ms: Annotated[int, Query(alias="toMs")],
    release_version: Annotated[str | None, Query(alias="releaseVersion")] = None,
) -> DataQualityResponse:
    """Expose explicit identity, SDK-health, late-data, and pipeline quality states."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    _validate_window(from_ms, to_ms, settings)
    if release_version is not None:
        release_version = _validate_identifier(release_version, "releaseVersion", 128)
    facts = await load_window_facts(
        session,
        principal,
        from_ms,
        to_ms,
        settings.query_max_rows,
        release_versions=(release_version,) if release_version is not None else None,
        late_after_ms=settings.query_late_after_seconds * 1000,
    )
    _no_store(response)
    return build_data_quality(
        request.state.request_id,
        principal,
        facts,
        release_version,
        from_ms,
        to_ms,
        settings.query_late_after_seconds * 1_000,
    )


@router.get("/events", response_model=EventPageResponse)
async def get_events(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    from_ms: Annotated[int, Query(alias="fromMs")],
    to_ms: Annotated[int, Query(alias="toMs")],
    release_version: Annotated[str | None, Query(alias="releaseVersion")] = None,
    module: str | None = None,
    name: str | None = None,
    fingerprint: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> EventPageResponse:
    """Return an investigator-only allow-listed event page with a bound opaque cursor."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    require_investigator(principal)
    _validate_window(from_ms, to_ms, settings)
    _validate_limit(limit, settings)
    release_version = _optional_identifier(release_version, "releaseVersion", 128)
    module = _optional_identifier(module, "module", 256)
    name = _optional_identifier(name, "name", 256)
    fingerprint = _optional_fingerprint(fingerprint)
    filter_hash = cursor_filter_hash(
        principal, from_ms, to_ms, release_version, module, name, fingerprint
    )
    cursor_key = settings.query_cursor_hmac_key_b64.get_secret_value()
    decoded_cursor = decode_cursor(cursor, filter_hash, cursor_key) if cursor else None
    events, has_more = await list_scoped_events(
        session,
        principal,
        from_ms,
        to_ms,
        limit,
        release_version=release_version,
        module=module,
        name=name,
        fingerprint=fingerprint,
        cursor=decoded_cursor,
    )
    next_cursor = None
    if has_more and events:
        last = events[-1]
        next_cursor = encode_cursor(
            last.occurrence_timestamp_ms,
            last.id,
            filter_hash,
            cursor_key,
        )
    session.add(
        _audit(
            principal,
            request.state.request_id,
            "query.events.list",
            "event_page",
            None,
            "success",
            {
                "from_ms": from_ms,
                "to_ms": to_ms,
                "release_version": release_version,
                "module": module,
                "name": name,
                "fingerprint": fingerprint,
                "returned_count": len(events),
            },
        )
    )
    await session.commit()
    _no_store(response)
    return EventPageResponse(
        request_id=request.state.request_id,
        scope=scope_for(principal),
        window=QueryWindow(from_ms=from_ms, to_ms=to_ms),
        items=[event_summary(event) for event in events],
        next_cursor=next_cursor,
    )


@router.post("/events/{event_id}/raw", response_model=RawEventResponse)
async def get_raw_event(
    event_id: str,
    access: RawAccessRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RawEventResponse:
    """Return one L2 raw event only after an investigator audit commits."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings, unsafe=True)
    require_investigator(principal)
    event_id = _validate_identifier(event_id, "eventId", 128)
    if access.purpose_code not in QUERY_RAW_PURPOSES:
        raise ApiError(400, "invalid_purpose_code", "The raw-access purpose code is invalid")
    event = await get_scoped_event(session, principal, event_id)
    result = "success" if event is not None else "not_found"
    if event is not None and event.raw_pruned_at is not None:
        result = "expired"
    session.add(
        _audit(
            principal,
            request.state.request_id,
            "query.event.raw.read",
            "inbox_event",
            event_id,
            result,
            {"purpose_code": access.purpose_code, "reason": access.reason},
        )
    )
    await session.commit()
    if event is None:
        raise ApiError(404, "event_not_found", "The event was not found")
    if event.raw_pruned_at is not None:
        raise ApiError(
            410, "raw_evidence_expired", "Raw evidence expired under the retention policy"
        )
    symbolized_result = (
        event.symbolization_job.result_json if event.symbolization_job is not None else None
    )
    _no_store(response)
    return RawEventResponse(
        request_id=request.state.request_id,
        scope=scope_for(principal),
        event=event_summary(event),
        raw_payload=event.payload_json,
        symbolized_result=symbolized_result,
    )


@router.get("/events/{event_id}", response_model=EventMetadataResponse)
async def get_event_metadata(
    event_id: str,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EventMetadataResponse:
    """Return one investigator-only L1 event projection and availability state."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    require_investigator(principal)
    event_id = _validate_identifier(event_id, "eventId", 128)
    event = await get_scoped_event(session, principal, event_id)
    session.add(
        _audit(
            principal,
            request.state.request_id,
            "query.event.metadata.read",
            "inbox_event",
            event_id,
            "success" if event is not None else "not_found",
            {},
        )
    )
    await session.commit()
    if event is None:
        raise ApiError(404, "event_not_found", "The event was not found")
    _no_store(response)
    return EventMetadataResponse(
        request_id=request.state.request_id,
        scope=scope_for(principal),
        event=event_summary(event),
        schema_version=event.schema_version,
        protocol=event.protocol,
        normalization_version=event.normalization_version,
        raw_available=event.raw_pruned_at is None,
        field_states=field_states(event),
    )


@router.post("/release-decisions", response_model=ReleaseDecisionResponse, status_code=201)
async def create_release_decision(
    decision: ReleaseDecisionRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReleaseDecisionResponse:
    """Record a human continue/pause/rollback decision without automating rollout."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings, unsafe=True)
    require_investigator(principal)
    if decision.decision not in QUERY_RELEASE_DECISIONS:
        raise ApiError(400, "invalid_release_decision", "The release decision is invalid")
    _validate_window(decision.evidence_from_ms, decision.evidence_to_ms, settings)
    release_version = _validate_identifier(decision.release_version, "releaseVersion", 128)
    actor = f"query-key:{principal.key_id}"
    stored = ReleaseDecision(
        tenant_id=principal.tenant_id,
        app_id=principal.app_id,
        environment=principal.environment,
        release_version=release_version,
        decision=decision.decision,
        actor=actor,
        evidence_from_ms=decision.evidence_from_ms,
        evidence_to_ms=decision.evidence_to_ms,
        reason=decision.reason,
        evidence_json=decision.evidence.model_dump(mode="json", by_alias=True),
    )
    session.add(stored)
    await session.flush()
    session.add(
        _audit(
            principal,
            request.state.request_id,
            "release_decision.record",
            "release_decision",
            str(stored.id),
            "success",
            {
                "release_version": release_version,
                "decision": decision.decision,
                "evidence_from_ms": decision.evidence_from_ms,
                "evidence_to_ms": decision.evidence_to_ms,
            },
        )
    )
    await session.commit()
    _no_store(response)
    return decision_response(stored)


@router.get("/release-decisions", response_model=ReleaseDecisionListResponse)
async def get_release_decisions(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    release_version: Annotated[str | None, Query(alias="releaseVersion")] = None,
    limit: int = 50,
) -> ReleaseDecisionListResponse:
    """Return investigator-visible human release decisions for the fixed scope."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    require_investigator(principal)
    release_version = _optional_identifier(release_version, "releaseVersion", 128)
    _validate_limit(limit, settings)
    decisions = await list_release_decisions(session, principal, release_version, limit)
    _no_store(response)
    return ReleaseDecisionListResponse(
        request_id=request.state.request_id,
        scope=scope_for(principal),
        items=[decision_response(item) for item in decisions],
    )


def _validate_window(from_ms: int, to_ms: int, settings: Settings) -> None:
    """Enforce positive, ordered, bounded occurrence-time windows."""
    if from_ms <= 0 or to_ms <= 0 or from_ms >= to_ms:
        raise ApiError(400, "invalid_query_window", "The query window is invalid")
    if to_ms - from_ms > settings.query_max_window_days * MILLISECONDS_PER_DAY:
        raise ApiError(
            422,
            "query_window_exceeded",
            f"The query window exceeds {settings.query_max_window_days} days",
        )


def _validate_limit(limit: int, settings: Settings) -> None:
    """Apply the dynamic deployment page-size ceiling."""
    if limit <= 0:
        raise ApiError(400, "invalid_query_limit", "The query limit must be positive")
    if limit > settings.query_max_limit:
        raise ApiError(
            422,
            "query_limit_exceeded",
            f"The query limit exceeds {settings.query_max_limit}",
        )


def _validate_identifier(value: str, field: str, max_bytes: int) -> str:
    """Reject blank, untrimmed, control-containing, or oversized identifiers."""
    if (
        not value
        or value != value.strip()
        or len(value.encode("utf-8")) > max_bytes
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ApiError(400, "invalid_query_filter", f"The {field} filter is invalid")
    return value


def _optional_identifier(value: str | None, field: str, max_bytes: int) -> str | None:
    """Validate a present identifier filter."""
    return _validate_identifier(value, field, max_bytes) if value is not None else None


def _optional_fingerprint(value: str | None) -> str | None:
    """Validate a canonical server fingerprint when present."""
    if value is None:
        return None
    if not FINGERPRINT_PATTERN.fullmatch(value):
        raise ApiError(400, "invalid_query_filter", "The fingerprint filter is invalid")
    return value


def _reject_scope_parameters(request: Request) -> None:
    """Reject attempts to self-select tenant/app/environment in query parameters."""
    normalized = {
        key.lower().replace("_", "").replace("-", "") for key in request.query_params.keys()
    }
    if normalized & FORBIDDEN_SCOPE_PARAMETERS:
        raise ApiError(
            400,
            "scope_parameter_forbidden",
            "Tenant, app, and environment are derived from the query credential",
        )


def _audit(
    principal: QueryPrincipal,
    request_id: str,
    action: str,
    object_type: str,
    object_id: str | None,
    result: str,
    details: dict[str, Any],
) -> AuditLog:
    """Create a credential-scoped audit row without secret or raw event content."""
    return AuditLog(
        tenant_id=principal.tenant_id,
        actor=f"query-key:{principal.key_id}",
        action=action,
        object_type=object_type,
        object_id=object_id,
        result=result,
        request_id=request_id,
        details_json={
            "app_id": principal.app_id,
            "environment": principal.environment,
            **details,
        },
    )


def _no_store(response: Response) -> None:
    """Prevent shared or browser caches from retaining scoped diagnostic data."""
    response.headers["Cache-Control"] = "private, no-store"
