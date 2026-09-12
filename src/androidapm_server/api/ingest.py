"""Authenticated whole-batch Android event ingestion endpoint."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.auth import authenticate_ingest_key
from androidapm_server.config import Settings, get_settings
from androidapm_server.constants import (
    DEFAULT_RETRY_AFTER_SECONDS,
    GZIP_CONTENT_ENCODING,
    HEADER_APP_BUILD,
    HEADER_APP_ID,
    HEADER_APP_VERSION,
    HEADER_BATCH_ID,
    HEADER_ENVIRONMENT,
    HEADER_EVENT_COUNT,
    HEADER_INSTALLATION_ID,
    HEADER_SCHEMA_VERSION,
    HEADER_SDK_VERSION,
    HEADER_VARIANT,
    HEADER_VERSION_CODE,
    LINE_CONTENT_TYPE,
    MAX_EVENT_JSON_BYTES,
    MAX_IDENTIFIER_BYTES,
    PROTOBUF_CONTENT_TYPE,
    PROTOBUF_ENVELOPE_V2_CONTENT_TYPE,
    PROTOBUF_ENVELOPE_V3_CONTENT_TYPE,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
    SCHEMA_VERSION_V3,
    SUPPORTED_CONTENT_TYPES,
    SUPPORTED_SCHEMA_VERSIONS,
)
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.maintenance import check_inbox_capacity
from androidapm_server.db.quota import consume_quota
from androidapm_server.db.session import get_session
from androidapm_server.db.symbolization import enqueue_symbolization_jobs
from androidapm_server.domain import IdentityQuality, IngestAck, IngestMetadata
from androidapm_server.errors import ApiError
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.metrics import INGEST_ACK_SECONDS, INGEST_EVENTS, INGEST_REQUESTS
from androidapm_server.protocol import decode_batch, decode_envelope_v2, decode_envelope_v3
from androidapm_server.protocol.compression import decompress_gzip_bounded

router = APIRouter(prefix="/v1", tags=["ingest"])


@router.post("/events", response_model=IngestAck)
async def ingest_events(
    request: Request,
    response: Response,
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

    content_type, envelope_version = _negotiate_content_type(
        request.headers.get("content-type", "")
    )
    expected_schema = envelope_version or SCHEMA_VERSION_V1
    if schema_version != expected_schema:
        raise ApiError(
            400,
            "unsupported_schema_version",
            "The schema version does not match the selected media type",
        )
    protocol_label = (
        f"protobuf_envelope_v{envelope_version}"
        if envelope_version is not None
        else ("line" if content_type == LINE_CONTENT_TYPE else "protobuf")
    )
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

    ack_batch_id: str | None = None
    ack_event_count: int | None = None
    if envelope_version == SCHEMA_VERSION_V2:
        envelope_v2 = decode_envelope_v2(payload, settings.max_batch_events)
        _validate_versioned_request_headers(request, envelope_v2.batch_id, envelope_v2.event_count)
        if envelope_v2.sdk_version != sdk_version:
            raise ApiError(
                400,
                "invalid_request",
                "The SDK version header does not match the V2 envelope",
            )
        if envelope_v2.service_name != app_id:
            raise ApiError(
                400,
                "invalid_request",
                "The app identity header does not match the V2 resource",
            )
        if envelope_v2.deployment_environment != environment:
            raise ApiError(
                400,
                "invalid_request",
                "The environment header does not match the V2 resource",
            )
        app_version = _optional_header(request, HEADER_APP_VERSION, MAX_IDENTIFIER_BYTES)
        if app_version is not None and app_version != envelope_v2.service_version:
            raise ApiError(
                400,
                "invalid_request",
                "The app version header does not match the V2 resource",
            )
        events = envelope_v2.events
        app_version = app_version or envelope_v2.service_version
        release_quality = IdentityQuality.BATCH_DECLARED
        installation_quality = IdentityQuality.BATCH_DECLARED
        installation_id = envelope_v2.installation_id
        ack_batch_id = envelope_v2.batch_id
        ack_event_count = envelope_v2.event_count
    elif envelope_version == SCHEMA_VERSION_V3:
        envelope_v3 = decode_envelope_v3(payload, settings.max_batch_events)
        _validate_versioned_request_headers(request, envelope_v3.batch_id, envelope_v3.event_count)
        if envelope_v3.sdk_version != sdk_version:
            raise ApiError(
                400,
                "invalid_request",
                "The SDK version header does not match the V3 envelope",
            )
        if envelope_v3.service_name != app_id:
            raise ApiError(
                400,
                "invalid_request",
                "The app identity header does not match the V3 resource",
            )
        if envelope_v3.deployment_environment != environment:
            raise ApiError(
                400,
                "invalid_request",
                "The environment header does not match the V3 resource",
            )
        events = envelope_v3.events
        # Batch declarations remain diagnostic only; insert_batch selects each event occurrence.
        app_version = _optional_header(request, HEADER_APP_VERSION, MAX_IDENTIFIER_BYTES)
        release_quality = IdentityQuality.OCCURRENCE_BOUND
        installation_quality = IdentityQuality.OCCURRENCE_BOUND
        installation_id = None
        ack_batch_id = envelope_v3.batch_id
        ack_event_count = envelope_v3.event_count
    else:
        events = decode_batch(
            payload,
            content_type,
            settings.max_batch_events,
            MAX_EVENT_JSON_BYTES,
        )
        app_version = _optional_header(request, HEADER_APP_VERSION, MAX_IDENTIFIER_BYTES)
        release_quality = (
            IdentityQuality.REQUEST_DECLARED if app_version is not None else IdentityQuality.ABSENT
        )
        installation_id = _optional_header(request, HEADER_INSTALLATION_ID, 256)
        installation_quality = (
            IdentityQuality.REQUEST_DECLARED
            if installation_id is not None
            else IdentityQuality.ABSENT
        )
    metadata = IngestMetadata(
        request_id=request.state.request_id,
        tenant_id=principal.tenant_id,
        app_id=app_id,
        environment=environment,
        schema_version=schema_version,
        sdk_version=sdk_version,
        app_version=app_version,
        app_build=_optional_header(request, HEADER_APP_BUILD, MAX_IDENTIFIER_BYTES),
        version_code=_optional_header(request, HEADER_VERSION_CODE, 64),
        variant=_optional_header(request, HEADER_VARIANT, MAX_IDENTIFIER_BYTES),
        protocol=protocol_label,
        release_identity_quality=release_quality,
        installation_identity_quality=installation_quality,
        installation_id=installation_id,
    )

    installation_keys = None
    if installation_id is not None or any(event.occurrence is not None for event in events):
        installation_keys = _installation_key_ring(settings)

    try:
        await consume_quota(session, principal, len(events))
        result = await insert_batch(session, metadata, events, installation_keys)
        await enqueue_symbolization_jobs(
            session, metadata, events, enabled=settings.symbolization_enabled
        )
        if result.inserted:
            await check_inbox_capacity(session, settings)
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
    if envelope_version is not None and ack_batch_id is not None and ack_event_count is not None:
        # Android treats these exact post-commit headers as the only explicit-envelope success.
        response.headers[HEADER_SCHEMA_VERSION] = envelope_version
        response.headers[HEADER_BATCH_ID] = ack_batch_id
        response.headers[HEADER_EVENT_COUNT] = str(ack_event_count)
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


def _negotiate_content_type(raw_content_type: str) -> tuple[str, str | None]:
    """Distinguish legacy Protobuf frames from explicit V2 and V3 envelopes."""
    parts = [part.strip() for part in raw_content_type.split(";")]
    base_type = parts[0].lower()
    if base_type not in SUPPORTED_CONTENT_TYPES:
        raise ApiError(415, "unsupported_media_type", "The Content-Type is not supported")
    parameters: dict[str, str] = {}
    for part in parts[1:]:
        if not part:
            continue
        if "=" not in part:
            raise ApiError(415, "unsupported_media_type", "The Content-Type is malformed")
        key, value = part.split("=", maxsplit=1)
        parameters[key.strip().lower()] = value.strip().strip('"')
    envelope_version = parameters.get("version")
    explicit_envelope = (
        base_type == PROTOBUF_CONTENT_TYPE
        and parameters.get("message") == "ApmBatchEnvelope"
        and envelope_version in {SCHEMA_VERSION_V2, SCHEMA_VERSION_V3}
    )
    if (
        base_type == PROTOBUF_CONTENT_TYPE
        and ("message" in parameters or "version" in parameters)
        and not explicit_envelope
    ):
        raise ApiError(
            415,
            "unsupported_media_type",
            "The supported versioned media types are "
            f"{PROTOBUF_ENVELOPE_V2_CONTENT_TYPE} and {PROTOBUF_ENVELOPE_V3_CONTENT_TYPE}",
        )
    return base_type, envelope_version if explicit_envelope else None


def _validate_versioned_request_headers(
    request: Request,
    body_batch_id: str,
    body_event_count: int,
) -> None:
    """Require request ACK metadata to match the decoded explicit body exactly."""
    batch_id = _required_header(request, HEADER_BATCH_ID, 128)
    event_count_text = _required_header(request, HEADER_EVENT_COUNT, 16)
    try:
        event_count = int(event_count_text)
    except ValueError as error:
        raise ApiError(
            400,
            "invalid_request",
            f"{HEADER_EVENT_COUNT} must be an integer",
        ) from error
    if batch_id != body_batch_id or event_count != body_event_count:
        raise ApiError(
            400,
            "invalid_request",
            "The batch identity or event count does not match the versioned body",
        )


def _installation_key_ring(settings: Settings) -> InstallationHmacKeyRing:
    """Load the versioned pseudonymization keys without exposing secret configuration."""
    try:
        return InstallationHmacKeyRing.parse(
            settings.installation_hmac_keys_json.get_secret_value(),
            settings.installation_hmac_active_key_version,
        )
    except ValueError as error:
        raise ApiError(
            503,
            "identity_service_unavailable",
            "Installation pseudonymization is not configured",
            True,
        ) from error
