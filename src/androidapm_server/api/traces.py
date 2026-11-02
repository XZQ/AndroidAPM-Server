"""Investigator-only manual Trace statistics within immutable credential scope."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.api.query import (
    _no_store,
    _optional_identifier,
    _reject_scope_parameters,
    _validate_limit,
    _validate_window,
)
from androidapm_server.config import Settings, get_settings
from androidapm_server.constants import MAX_IDENTIFIER_BYTES
from androidapm_server.db.session import get_session
from androidapm_server.errors import ApiError
from androidapm_server.query import QueryWindow, scope_for
from androidapm_server.query_auth import require_investigator
from androidapm_server.tracing import (
    MAX_TRACE_DETAIL_SPANS,
    TraceDetailResponse,
    TraceListResponse,
    build_trace_list,
    load_spans,
    summarize_trace,
    valid_trace_id,
)
from androidapm_server.web_auth import authenticate_query_request

router = APIRouter(prefix="/v1/query", tags=["traces"])


@router.get("/traces", response_model=TraceListResponse)
async def get_traces(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    from_ms: Annotated[int, Query(alias="fromMs")],
    to_ms: Annotated[int, Query(alias="toMs")],
    release_version: Annotated[str | None, Query(alias="releaseVersion")] = None,
    limit: int = 50,
) -> TraceListResponse:
    """List traces without exposing arbitrary attribute values or assuming total coverage."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    require_investigator(principal)
    _validate_window(from_ms, to_ms, settings)
    _validate_limit(limit, settings)
    release_version = _optional_identifier(release_version, "releaseVersion", MAX_IDENTIFIER_BYTES)
    spans, invalid, expired = await load_spans(
        session, principal, settings, from_ms, to_ms, release_version
    )
    _no_store(response)
    return build_trace_list(
        spans, invalid, principal, request.state.request_id, from_ms, to_ms, limit, expired
    )


@router.get("/traces/{trace_id}", response_model=TraceDetailResponse)
async def get_trace_detail(
    trace_id: str,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    from_ms: Annotated[int, Query(alias="fromMs")],
    to_ms: Annotated[int, Query(alias="toMs")],
    release_version: Annotated[str | None, Query(alias="releaseVersion")] = None,
) -> TraceDetailResponse:
    """Read only observed span edges; missing timing and parents stay explicitly unavailable."""
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(session, request, settings)
    require_investigator(principal)
    _validate_window(from_ms, to_ms, settings)
    canonical = valid_trace_id(trace_id)
    if canonical is None:
        raise ApiError(400, "invalid_trace_id", "Trace ID must be nonzero 128-bit hexadecimal")
    release_version = _optional_identifier(release_version, "releaseVersion", MAX_IDENTIFIER_BYTES)
    spans, _, _ = await load_spans(
        session, principal, settings, from_ms, to_ms, release_version, canonical
    )
    if not spans:
        raise ApiError(404, "trace_not_found", "Trace is not visible in this scope and window")
    if len(spans) > MAX_TRACE_DETAIL_SPANS:
        raise ApiError(
            422, "trace_span_limit", "Reduce the window; trace detail exceeds the span limit"
        )
    _no_store(response)
    summary = summarize_trace(canonical, spans)
    return TraceDetailResponse(
        request_id=request.state.request_id,
        scope=scope_for(principal),
        window=QueryWindow(from_ms=from_ms, to_ms=to_ms),
        summary=summary,
        spans=sorted(
            spans, key=lambda span: (span.start_ms or span.occurrence_timestamp_ms, span.event_id)
        ),
    )
