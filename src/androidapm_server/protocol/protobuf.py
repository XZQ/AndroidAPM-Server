"""Decoder for the SDK's custom length-prefixed Protobuf stream."""

from __future__ import annotations

from google.protobuf.message import DecodeError
from pydantic import ValidationError

from androidapm_server.constants import PROTOBUF_LENGTH_BYTES
from androidapm_server.domain import ApmEvent
from androidapm_server.errors import ApiError
from androidapm_server.generated.apm_event_pb2 import ApmEventMessage
from androidapm_server.protocol.line import _safe_validation_message


def decode_protobuf_batch(payload: bytes, max_events: int, max_event_bytes: int) -> list[ApmEvent]:
    """Decode every big-endian length-prefixed message in an SDK batch."""
    if not payload:
        raise ApiError(422, "invalid_event", "The batch must contain at least one event", False, 0)

    events: list[ApmEvent] = []
    cursor = 0
    while cursor < len(payload):
        event_index = len(events)
        if event_index >= max_events:
            raise ApiError(413, "payload_too_large", "The batch contains too many events")
        if len(payload) - cursor < PROTOBUF_LENGTH_BYTES:
            raise _invalid_frame("The Protobuf length prefix is truncated", event_index)
        length = int.from_bytes(payload[cursor : cursor + PROTOBUF_LENGTH_BYTES], "big")
        cursor += PROTOBUF_LENGTH_BYTES
        if length <= 0:
            raise _invalid_frame("Zero-length Protobuf frames are not allowed", event_index)
        if length > max_event_bytes:
            raise ApiError(413, "payload_too_large", "A Protobuf event frame is too large")
        end = cursor + length
        if end > len(payload):
            raise _invalid_frame("The Protobuf event frame is truncated", event_index)

        message = ApmEventMessage()
        try:
            message.ParseFromString(payload[cursor:end])
            events.append(_to_event(message))
        except DecodeError as error:
            raise _invalid_frame("The Protobuf event is invalid", event_index) from error
        except (ValueError, ValidationError) as error:
            raise ApiError(
                422,
                "invalid_event",
                _safe_validation_message(error),
                False,
                event_index,
            ) from error
        cursor = end
    return events


def _to_event(message: ApmEventMessage) -> ApmEvent:
    """Convert the compatibility message without guessing field types."""
    return ApmEvent(
        timestamp=message.timestamp,
        event_id=message.event_id,
        module=message.module,
        name=message.name,
        kind=message.kind,
        severity=message.severity,
        priority=message.priority,
        process_name=message.process_name,
        thread_name=message.thread_name,
        scene=message.scene or None,
        foreground=message.foreground,
        fields=dict(message.fields),
        global_context=dict(message.global_context),
        extras=dict(message.extras),
    )


def _invalid_frame(message: str, event_index: int) -> ApiError:
    """Create a frame error that rejects the complete batch."""
    return ApiError(422, "invalid_event", message, False, event_index)
