"""Decoder and integrity checks for the versioned AndroidAPM batch envelope."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from google.protobuf.message import DecodeError
from pydantic import ValidationError

from androidapm_server.constants import (
    MAX_IDENTIFIER_BYTES,
    SCHEMA_VERSION_V2,
    SDK_NAME,
    V2_BATCH_ID_HASH_BYTES,
    V2_BATCH_ID_PREFIX,
)
from androidapm_server.domain import ApmEvent
from androidapm_server.errors import ApiError
from androidapm_server.generated.apm_event_pb2 import (
    ApmBatchEnvelope,
    ApmEventMessage,
    ApmTypedValue,
)
from androidapm_server.protocol.line import _safe_validation_message

_BATCH_ID_PATTERN = re.compile(r"^b2-[0-9a-f]{32}$")
_MAX_ARBITRARY_PRECISION_CHARACTERS = 4_096
_CANONICAL_NON_FINITE_FLOATS = frozenset({"NaN", "Infinity", "-Infinity"})
_INTEGER_BOUNDS = {
    "BYTE": (-128, 127),
    "SHORT": (-32_768, 32_767),
    "INT": (-2_147_483_648, 2_147_483_647),
    "LONG": (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
}
_RESOURCE_UNKNOWN_KEYS = {
    "resource.serviceName": "service_name",
    "resource.serviceVersion": "service_version",
    "resource.deploymentEnvironment": "deployment_environment",
}


@dataclass(frozen=True, slots=True)
class DecodedEnvelopeV2:
    """Validated V2 request envelope plus its complete event list."""

    events: list[ApmEvent]
    batch_id: str
    sdk_version: str
    service_name: str
    service_version: str
    deployment_environment: str
    installation_id: str

    @property
    def event_count(self) -> int:
        """Return the number of events covered by the atomic ACK."""
        return len(self.events)


def decode_envelope_v2(
    payload: bytes,
    max_events: int,
) -> DecodedEnvelopeV2:
    """Decode one complete V2 envelope and reject any ambiguous scalar."""
    if not payload:
        raise _invalid_envelope("The V2 envelope must not be empty")
    message = ApmBatchEnvelope()
    try:
        message.ParseFromString(payload)
    except DecodeError as error:
        raise _invalid_envelope("The V2 Protobuf envelope is invalid") from error

    if message.schema_version != int(SCHEMA_VERSION_V2):
        raise _invalid_envelope("The body schema version must be 2")
    if message.sdk_name != SDK_NAME:
        raise _invalid_envelope("The SDK name is not supported")
    _require_bounded(message.sdk_version, "SDK version", 64)
    if message.sent_at_ms <= 0:
        raise _invalid_envelope("The envelope sent_at_ms must be positive")
    if not _BATCH_ID_PATTERN.fullmatch(message.batch_id):
        raise _invalid_envelope("The envelope batch ID is malformed")
    if not message.events:
        raise _invalid_envelope("The V2 envelope must contain at least one event")
    if len(message.events) > max_events:
        raise ApiError(413, "payload_too_large", "The batch contains too many events")

    resource_values = {
        name: getattr(message.resource, attribute)
        for name, attribute in _RESOURCE_UNKNOWN_KEYS.items()
    }
    for name, value in resource_values.items():
        _require_bounded(value, name, MAX_IDENTIFIER_BYTES)
    _require_bounded(
        message.resource.installation_id,
        "resource.installationId",
        MAX_IDENTIFIER_BYTES,
    )

    events = [
        _to_event(event, resource_values, index) for index, event in enumerate(message.events)
    ]
    expected_batch_id = stable_batch_id([event.event_id for event in events])
    if message.batch_id != expected_batch_id:
        raise _invalid_envelope("The envelope batch ID does not match its ordered event IDs")
    return DecodedEnvelopeV2(
        events=events,
        batch_id=message.batch_id,
        sdk_version=message.sdk_version,
        service_name=message.resource.service_name,
        service_version=message.resource.service_version,
        deployment_environment=message.resource.deployment_environment,
        installation_id=message.resource.installation_id,
    )


def stable_batch_id(event_ids: list[str]) -> str:
    """Recompute the client batch identity using fixed length delimiters."""
    digest = hashlib.sha256()
    digest.update(bytes((int(SCHEMA_VERSION_V2),)))
    for event_id in event_ids:
        identity = event_id.encode("utf-8")
        digest.update(len(identity).to_bytes(4, byteorder="big", signed=False))
        digest.update(identity)
    return V2_BATCH_ID_PREFIX + digest.digest()[:V2_BATCH_ID_HASH_BYTES].hex()


def _to_event(
    message: ApmEventMessage,
    resource_values: dict[str, str],
    event_index: int,
) -> ApmEvent:
    """Convert one envelope event while retaining every scalar discriminator."""
    if message.HasField("occurrence"):
        raise _invalid_event(
            "V2 events must not carry the schema V3 occurrence field",
            event_index,
        )
    if message.fields:
        raise _invalid_event(
            "V2 events must use typed_fields instead of legacy fields",
            event_index,
        )
    fields: dict[str, object] = {}
    field_types: dict[str, str] = {}
    try:
        for key, typed in message.typed_fields.items():
            fields[key] = _typed_value(typed)
            field_types[key] = typed.type
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
            fields=fields,
            field_types=field_types,
            global_context=dict(message.global_context),
            extras=dict(message.extras),
            unknown=dict(resource_values),
        )
    except (ValueError, ValidationError) as error:
        raise ApiError(
            422,
            "invalid_event",
            _safe_validation_message(error),
            False,
            event_index,
        ) from error


def _typed_value(typed: ApmTypedValue) -> object:
    """Parse a scalar only according to its explicit V2 discriminator."""
    scalar_type = typed.type
    value = typed.value
    if scalar_type == "NULL":
        if value:
            raise ValueError("NULL typed values must not carry text")
        return None
    if scalar_type == "STRING":
        return value
    if scalar_type == "BOOLEAN":
        if value == "true":
            return True
        if value == "false":
            return False
        raise ValueError("BOOLEAN typed values must be true or false")
    if scalar_type in _INTEGER_BOUNDS:
        parsed = _canonical_integer(value)
        lower, upper = _INTEGER_BOUNDS[scalar_type]
        if parsed < lower or parsed > upper:
            raise ValueError(f"{scalar_type} typed value is out of range")
        return parsed
    if scalar_type in {"FLOAT", "DOUBLE"}:
        parsed_float = float(value)
        if not math.isfinite(parsed_float):
            if value not in _CANONICAL_NON_FINITE_FLOATS:
                raise ValueError(f"{scalar_type} non-finite value must use canonical Kotlin text")
            # JSON/JSONB cannot portably store IEEE non-finite numbers. Retain the exact
            # text together with field_types instead of emitting invalid JSON.
            return value
        return parsed_float
    if scalar_type == "CHAR":
        if len(value) != 1:
            raise ValueError("CHAR typed values must contain one character")
        return value
    if scalar_type == "BIG_INTEGER":
        _canonical_integer(value)
        return value
    if scalar_type == "BIG_DECIMAL":
        _validate_big_decimal(value)
        return value
    raise ValueError(f"Unknown typed scalar discriminator: {scalar_type}")


def _canonical_integer(value: str) -> int:
    """Parse the canonical base-ten integer text emitted by Kotlin."""
    if len(value) > _MAX_ARBITRARY_PRECISION_CHARACTERS:
        raise ValueError("Integer typed value exceeds 4096 characters")
    parsed = int(value)
    if str(parsed) != value:
        raise ValueError("Integer typed values must use canonical base-ten text")
    return parsed


def _validate_big_decimal(value: str) -> None:
    """Validate the plain decimal form without converting away precision."""
    if len(value) > _MAX_ARBITRARY_PRECISION_CHARACTERS:
        raise ValueError("BIG_DECIMAL typed value exceeds 4096 characters")
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        raise ValueError("BIG_DECIMAL typed values must use plain canonical decimal text")


def _require_bounded(value: str, name: str, max_bytes: int) -> None:
    """Require one non-empty resource/envelope identifier within its UTF-8 budget."""
    if not value or len(value.encode("utf-8")) > max_bytes:
        raise _invalid_envelope(f"{name} must contain 1-{max_bytes} UTF-8 bytes")


def _invalid_envelope(message: str) -> ApiError:
    """Create a whole-envelope validation failure."""
    return ApiError(422, "invalid_event", message, False, 0)


def _invalid_event(message: str, event_index: int) -> ApiError:
    """Create an event-located V2 validation failure."""
    return ApiError(422, "invalid_event", message, False, event_index)
