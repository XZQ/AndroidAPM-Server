from __future__ import annotations

import pytest

from androidapm_server.domain import EventKind, EventPriority, EventSeverity
from androidapm_server.errors import ApiError
from androidapm_server.protocol.line import decode_line_batch

BASE_LINE = (
    "ts=1700000000000|eventId=event-1|module=network|name=request|kind=METRIC|"
    "severity=INFO|priority=NORMAL|process=com.example|thread=main"
)


def test_decodes_current_sdk_line_format() -> None:
    events = decode_line_batch(
        (
            BASE_LINE
            + "|scene=home|foreground=true|fields=durationMs=42,url=https://example.test/a=b|"
            "context=version=1.2.3|extras=method=GET\n"
        ).encode(),
        max_events=32,
    )

    event = events[0]
    assert event.event_id == "event-1"
    assert event.kind is EventKind.METRIC
    assert event.severity is EventSeverity.INFO
    assert event.priority is EventPriority.NORMAL
    assert event.foreground is True
    assert event.fields == {"durationMs": "42", "url": "https://example.test/a=b"}


def test_empty_optional_maps_are_valid() -> None:
    event = decode_line_batch(BASE_LINE.encode(), max_events=1)[0]
    assert event.fields == {}
    assert event.global_context == {}
    assert event.foreground is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"", "at least one"),
        ((BASE_LINE + "|foreground=yes").encode(), "foreground"),
        ((BASE_LINE + "|eventId=other").encode(), "Duplicate"),
        ((BASE_LINE.replace("module=network|", "")).encode(), "Missing"),
        (b"\xff", "UTF-8"),
    ],
)
def test_rejects_invalid_batch_as_a_whole(body: bytes, message: str) -> None:
    with pytest.raises(ApiError) as caught:
        decode_line_batch(body, max_events=32)
    assert message in caught.value.message


def test_rejects_blank_line_inside_batch() -> None:
    with pytest.raises(ApiError) as caught:
        decode_line_batch((BASE_LINE + "\n\n" + BASE_LINE).encode(), max_events=32)
    assert caught.value.event_index == 1
