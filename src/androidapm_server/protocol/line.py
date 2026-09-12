"""Parser for the Android SDK v1 line compatibility protocol."""

from __future__ import annotations

from pydantic import ValidationError

from androidapm_server.domain import ApmEvent
from androidapm_server.errors import ApiError

TOP_LEVEL_SEPARATOR = "|"
PAIR_SEPARATOR = ","
KEY_VALUE_SEPARATOR = "="

REQUIRED_KEYS = frozenset(
    {"ts", "eventId", "module", "name", "kind", "severity", "priority", "process", "thread"}
)
KNOWN_KEYS = REQUIRED_KEYS | {"scene", "foreground", "fields", "context", "extras"}


def decode_line_batch(payload: bytes, max_events: int) -> list[ApmEvent]:
    """Decode a UTF-8 newline-delimited SDK batch into canonical events."""
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ApiError(400, "invalid_encoding", "The request body must be valid UTF-8") from error

    raw_lines = text.splitlines()
    if not raw_lines or all(not line for line in raw_lines):
        raise ApiError(422, "invalid_event", "The batch must contain at least one event", False, 0)
    if len(raw_lines) > max_events:
        raise ApiError(413, "payload_too_large", "The batch contains too many events")

    events: list[ApmEvent] = []
    for index, line in enumerate(raw_lines):
        if not line:
            raise ApiError(422, "invalid_event", "Blank event lines are not allowed", False, index)
        events.append(_decode_line(line, index))
    return events


def _decode_line(line: str, event_index: int) -> ApmEvent:
    """Decode one line and attach a stable event index to validation errors."""
    segments: dict[str, str] = {}
    for segment in line.split(TOP_LEVEL_SEPARATOR):
        key, separator, value = segment.partition(KEY_VALUE_SEPARATOR)
        if not separator or not key:
            raise _invalid_event("Every segment must use key=value", event_index)
        if key in segments:
            raise _invalid_event(f"Duplicate top-level field: {key}", event_index)
        segments[key] = value

    missing = sorted(REQUIRED_KEYS - segments.keys())
    if missing:
        raise _invalid_event(f"Missing required fields: {','.join(missing)}", event_index)

    unknown = {key: value for key, value in segments.items() if key not in KNOWN_KEYS}
    foreground = _parse_foreground(segments.get("foreground"), event_index)
    try:
        return ApmEvent(
            timestamp=int(segments["ts"]),
            event_id=segments["eventId"],
            module=segments["module"],
            name=segments["name"],
            kind=segments["kind"],
            severity=segments["severity"],
            priority=segments["priority"],
            process_name=segments["process"],
            thread_name=segments["thread"],
            scene=segments.get("scene"),
            foreground=foreground,
            fields=_parse_map(segments.get("fields", ""), event_index),
            global_context=_parse_map(segments.get("context", ""), event_index),
            extras=_parse_map(segments.get("extras", ""), event_index),
            unknown=unknown,
        )
    except (ValueError, ValidationError) as error:
        raise _invalid_event(_safe_validation_message(error), event_index) from error


def _parse_map(value: str, event_index: int) -> dict[str, str]:
    """Parse the SDK's sorted comma-separated map using the first equals sign."""
    if not value:
        return {}
    parsed: dict[str, str] = {}
    for item in value.split(PAIR_SEPARATOR):
        key, separator, item_value = item.partition(KEY_VALUE_SEPARATOR)
        if not separator or not key:
            raise _invalid_event("Map entries must use key=value", event_index)
        if key in parsed:
            raise _invalid_event(f"Duplicate map key: {key}", event_index)
        parsed[key] = item_value
    return parsed


def _parse_foreground(value: str | None, event_index: int) -> bool | None:
    """Parse Kotlin's lowercase Boolean rendering without accepting aliases."""
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise _invalid_event("foreground must be true or false", event_index)


def _safe_validation_message(error: ValueError | ValidationError) -> str:
    """Return a bounded schema error without echoing the event payload."""
    if isinstance(error, ValidationError):
        first = error.errors(include_url=False, include_input=False)[0]
        location = ".".join(str(part) for part in first["loc"])
        return f"Invalid {location}: {first['msg']}"
    return str(error)[:256]


def _invalid_event(message: str, event_index: int) -> ApiError:
    """Create an item-located whole-batch validation error."""
    return ApiError(422, "invalid_event", message, False, event_index)
