from __future__ import annotations

import pytest

from androidapm_server.errors import ApiError
from androidapm_server.generated.apm_event_pb2 import ApmEventMessage
from androidapm_server.protocol.protobuf import decode_protobuf_batch


def make_message(event_id: str = "event-pb-1") -> bytes:
    message = ApmEventMessage(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="crash",
        name="java_crash",
        kind="ALERT",
        severity="FATAL",
        priority="CRITICAL",
        process_name="com.example",
        thread_name="main",
        scene="checkout",
        foreground=True,
        fields={"exception.type": "IllegalStateException"},
        global_context={"version": "1.2.3"},
        extras={"stack": "example"},
    ).SerializeToString()
    return len(message).to_bytes(4, "big") + message


def test_decodes_multiple_length_prefixed_messages() -> None:
    events = decode_protobuf_batch(make_message("one") + make_message("two"), 32, 256 * 1024)
    assert [event.event_id for event in events] == ["one", "two"]
    assert events[0].fields["exception.type"] == "IllegalStateException"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\x00\x00\x00",
        b"\x00\x00\x00\x00",
        b"\x00\x00\x00\x08short",
    ],
)
def test_rejects_empty_or_truncated_frames(body: bytes) -> None:
    with pytest.raises(ApiError):
        decode_protobuf_batch(body, 32, 256 * 1024)


def test_rejects_more_than_configured_event_count() -> None:
    with pytest.raises(ApiError) as caught:
        decode_protobuf_batch(make_message("one") + make_message("two"), 1, 256 * 1024)
    assert caught.value.status_code == 413
