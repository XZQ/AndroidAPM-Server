"""Request correlation and safe access logging middleware."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response

from androidapm_server.constants import HEADER_REQUEST_ID

logger = structlog.get_logger(__name__)


async def request_context_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Generate a trusted request id and log only bounded route metadata."""
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    response = await call_next(request)
    response.headers[HEADER_REQUEST_ID] = request_id
    logger.info(
        "http_request",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )
    return response
