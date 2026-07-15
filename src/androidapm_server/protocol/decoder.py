"""Protocol dispatch after content negotiation and bounded decompression."""

from __future__ import annotations

from androidapm_server.constants import LINE_CONTENT_TYPE, PROTOBUF_CONTENT_TYPE
from androidapm_server.domain import ApmEvent
from androidapm_server.errors import ApiError
from androidapm_server.protocol.line import decode_line_batch
from androidapm_server.protocol.protobuf import decode_protobuf_batch


def decode_batch(
    payload: bytes,
    content_type: str,
    max_events: int,
    max_event_bytes: int,
) -> list[ApmEvent]:
    """Decode a complete request body using the selected v1 wire protocol."""
    if content_type == LINE_CONTENT_TYPE:
        return decode_line_batch(payload, max_events)
    if content_type == PROTOBUF_CONTENT_TYPE:
        return decode_protobuf_batch(payload, max_events, max_event_bytes)
    raise ApiError(415, "unsupported_media_type", "The Content-Type is not supported")
