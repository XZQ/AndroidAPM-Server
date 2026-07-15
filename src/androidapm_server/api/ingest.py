"""Authenticated whole-batch Android event ingestion endpoint."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.auth import authenticate_ingest_key
from androidapm_server.config import Settings, get_settings
from androidapm_server.constants import (
    DEFAULT_RETRY_AFTER_SECONDS,
    GZIP_CONTENT_ENCODING,
    HEADER_APP_BUILD,
    HEADER_APP_ID,
    HEADER_APP_VERSION,
    HEADER_ENVIRONMENT,
    HEADER_SCHEMA_VERSION,
    HEADER_SDK_VERSION,
    LINE_CONTENT_TYPE,
    MAX_EVENT_JSON_BYTES,
    SUPPORTED_CONTENT_TYPES,
    SUPPORTED_SCHEMA_VERSIONS,
)
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.quota import consume_quota
from androidapm_server.db.session import get_session
from androidapm_server.domain import IngestAck, IngestMetadata
from androidapm_server.errors import ApiError
from androidapm_server.metrics import INGEST_ACK_SECONDS, INGEST_EVENTS, INGEST_REQUESTS
from androidapm_server.protocol import decode_batch
from androidapm_server.protocol.compression import decompress_gzip_bounded

router = APIRouter(prefix="/v1", tags=["ingest"])


@router.post("/events", response_model=IngestAck)
async def ingest_events(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IngestAck:
    """Validate and durably commit an SDK batch before returning success."""
    started = time.perf_counter()
    protocol_label = "unknown"
    if not settings.ingest_enabled:
        raise ApiError(
            503,
            "temporarily_unavailable",
            "Ingestion is temporarily disabled",
            True,
            headers={"Retry-After": str(DEFAULT_RETRY_AFTER_SECONDS)},
        )

    app_id = _required_header(request, HEADER_APP_ID, 256)
    environment = _required_header(request, HEADER_ENVIRONMENT, 128)
    schema_version = _required_header(request, HEADER_SCHEMA_VERSION, 32)
    sdk_version = _required_header(request, HEADER_SDK_VERSION, 64)
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ApiError(400, "unsupported_schema_version", "The schema version is not supported")

    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise ApiError(415, "unsupported_media_type", "The Content-Type is not supported")
    protocol_label = "line" if content_type == LINE_CONTENT_TYPE else "protobuf"
    content_encoding = request.headers.get("content-encoding", "").strip().lower()
    if content_encoding not in {"", GZIP_CONTENT_ENCODING}:
        raise ApiError(415, "unsupported_content_encoding", "The Content-Encoding is not supported")

    # Authenticate before spending memory and CPU on an attacker-controlled request body.
    principal = await authenticate_ingest_key(
        session,
        request.headers.get("authorization"),
        app_id,
        environment,
    )
    body_limit = (
        settings.max_compressed_body_bytes
        if content_encoding == GZIP_CONTENT_ENCODING
        else settings.max_decompressed_body_bytes
    )
    payload = await _read_body_bounded(request, body_limit)
    if content_encoding == GZIP_CONTENT_ENCODING:
        payload = decompress_gzip_bounded(payload, settings.max_decompressed_body_bytes)

    events = decode_batch(
        payload,
        content_type,
        settings.max_batch_events,
        MAX_EVENT_JSON_BYTES,
    )
    metadata = IngestMetadata(
        request_id=request.state.request_id,
        tenant_id=principal.tenant_id,
        app_id=app_id,
        environment=environment,
        schema_version=schema_version,
        sdk_version=sdk_version,
        app_version=_optional_header(request, HEADER_APP_VERSION, 128),
        app_build=_optional_header(request, HEADER_APP_BUILD, 128),
        protocol=protocol_label,
    )

    try:
        await consume_quota(session, principal, len(events))
        result = await insert_batch(session, metadata, events)
        # This commit is the public durability boundary. A 2xx ACK cannot precede it.
        await session.commit()
    except Exception:
        await session.rollback()
        INGEST_REQUESTS.labels("failed", protocol_label).inc()
        raise

    INGEST_REQUESTS.labels("accepted", protocol_label).inc()
    INGEST_EVENTS.labels("inserted").inc(result.inserted)
    INGEST_EVENTS.labels("duplicate").inc(result.duplicates)
    INGEST_ACK_SECONDS.observe(time.perf_counter() - started)
    return IngestAck(
        requestId=metadata.request_id,
        received=result.received,
        inserted=result.inserted,
        duplicates=result.duplicates,
    )


async def _read_body_bounded(request: Request, max_bytes: int) -> bytes:
    """Read ASGI chunks with an application-level hard request-size cap."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as error:
            raise ApiError(400, "invalid_request", "Content-Length must be an integer") from error
        if declared_length < 0:
            raise ApiError(400, "invalid_request", "Content-Length must not be negative")
        if declared_length > max_bytes:
            raise _payload_too_large()

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise _payload_too_large()
        body.extend(chunk)
    return bytes(body)


def _required_header(request: Request, name: str, max_bytes: int) -> str:
    """Read a non-empty bounded required header without echoing its value."""
    value = request.headers.get(name)
    if value is None or not value.strip():
        raise ApiError(400, "invalid_request", f"Missing required header: {name}")
    return _validate_header_value(value.strip(), name, max_bytes)


def _optional_header(request: Request, name: str, max_bytes: int) -> str | None:
    """Read a bounded optional header, treating blank as absent."""
    value = request.headers.get(name)
    if value is None or not value.strip():
        return None
    return _validate_header_value(value.strip(), name, max_bytes)


def _validate_header_value(value: str, name: str, max_bytes: int) -> str:
    """Reject control characters and oversized identity header values."""
    if len(value.encode("utf-8")) > max_bytes:
        raise ApiError(400, "invalid_request", f"Header is too long: {name}")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ApiError(400, "invalid_request", f"Header contains control characters: {name}")
    return value


def _payload_too_large() -> ApiError:
    """Create the stable request body size error."""
    return ApiError(413, "payload_too_large", "The request body is too large")
