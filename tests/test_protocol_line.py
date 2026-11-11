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


def test_extreme_int64_timestamp_is_a_non_retryable_validation_error() -> None:
    """A client clock bug must not turn into a retryable 500 poison batch."""
    body = BASE_LINE.replace("ts=1700000000000", f"ts={2**63 - 1}")
    with pytest.raises(ApiError) as caught:
        decode_line_batch(body.encode(), max_events=1)
    assert caught.value.status_code == 422
    assert caught.value.retryable is False


def test_ts_error_message_does_not_echo_sender_text() -> None:
    body = BASE_LINE.replace("ts=1700000000000", "ts=SECRET-TEXT")
    with pytest.raises(ApiError) as caught:
        decode_line_batch(body.encode(), max_events=1)
    assert caught.value.status_code == 422
    assert "SECRET-TEXT" not in caught.value.message


@pytest.mark.parametrize("separator", ["\r", "\v", "\f", "\x1c", "\u0085", "\u2028"])
def test_only_newline_separates_events(separator: str) -> None:
    """CR and Unicode separators inside exception messages stay within one event."""
    body = BASE_LINE + f"|fields=exceptionMessage=boom{separator}bang"
    events = decode_line_batch(body.encode(), max_events=32)
    assert len(events) == 1
    assert events[0].fields["exceptionMessage"] == f"boom{separator}bang"


def test_crlf_delimiters_and_single_trailing_newline_are_tolerated() -> None:
    second = BASE_LINE.replace("eventId=event-1", "eventId=event-2")
    events = decode_line_batch(f"{BASE_LINE}\r\n{second}\r\n".encode(), max_events=32)
    assert [event.event_id for event in events] == ["event-1", "event-2"]
    assert events[0].thread_name == "main"
