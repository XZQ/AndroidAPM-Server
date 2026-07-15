"""Stable public API errors and FastAPI handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from androidapm_server.constants import HEADER_REQUEST_ID

logger = structlog.get_logger(__name__)


class ErrorBody(BaseModel):
    """Safe error envelope shared by every API failure."""

    requestId: str
    code: str
    message: str
    retryable: bool
    eventIndex: int | None = None


@dataclass(slots=True)
class ApiError(Exception):
    """Expected request failure with a stable status and public message."""

    status_code: int
    code: str
    message: str
    retryable: bool = False
    event_index: int | None = None
    headers: dict[str, str] | None = None


async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
    """Serialize an expected failure without leaking payloads or stack traces."""
    request_id = getattr(request.state, "request_id", "unknown")
    body = ErrorBody(
        requestId=request_id,
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        eventIndex=error.event_index,
    )
    headers: dict[str, str] = {HEADER_REQUEST_ID: request_id}
    if error.headers:
        headers.update(error.headers)
    return JSONResponse(status_code=error.status_code, content=body.model_dump(), headers=headers)


async def unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    """Return a non-sensitive response for unexpected server errors."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("unhandled_request_error", request_id=request_id, exc_info=error)
    content: dict[str, Any] = ErrorBody(
        requestId=request_id,
        code="internal_error",
        message="An internal error occurred",
        retryable=True,
    ).model_dump()
    return JSONResponse(
        status_code=500,
        content=content,
        headers={HEADER_REQUEST_ID: request_id},
    )
