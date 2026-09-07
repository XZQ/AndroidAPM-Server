"""Strict decoder for the occurrence-bound AndroidAPM schema V3 envelope."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from google.protobuf.message import DecodeError
from pydantic import ValidationError

from androidapm_server.constants import (
    MAX_IDENTIFIER_BYTES,
    SCHEMA_VERSION_V3,
    SDK_NAME,
    V3_BATCH_ID_HASH_BYTES,
    V3_BATCH_ID_PREFIX,
)
from androidapm_server.domain import ApmEvent, NativeFrameIdentity, OccurrenceContext
from androidapm_server.errors import ApiError
from androidapm_server.generated.apm_event_pb2 import ApmBatchEnvelope, ApmEventMessage
from androidapm_server.protocol.envelope_v2 import _require_bounded, _typed_value
from androidapm_server.protocol.line import _safe_validation_message

_BATCH_ID_PATTERN = re.compile(r"^b3-[0-9a-f]{32}$")
_RESOURCE_UNKNOWN_KEYS = {
    "resource.serviceName": "service_name",
    "resource.deploymentEnvironment": "deployment_environment",
}


@dataclass(frozen=True, slots=True)
class DecodedEnvelopeV3:
    """Validated V3 envelope plus occurrence-bound events and ACK identity."""

    events: list[ApmEvent]
    batch_id: str
    sdk_version: str
    service_name: str
    service_version: str
    deployment_environment: str

    @property
    def event_count(self) -> int:
        """Return the complete event count covered by the atomic ACK."""
        return len(self.events)


def decode_envelope_v3(payload: bytes, max_events: int) -> DecodedEnvelopeV3:
    """Decode one V3 envelope and require occurrence identity on every event."""
    if not payload:
        raise _invalid_envelope("The V3 envelope must not be empty")
    message = ApmBatchEnvelope()
    try:
        message.ParseFromString(payload)
    except DecodeError as error:
        raise _invalid_envelope("The V3 Protobuf envelope is invalid") from error

    if message.schema_version != int(SCHEMA_VERSION_V3):
        raise _invalid_envelope("The body schema version must be 3")
    if message.sdk_name != SDK_NAME:
        raise _invalid_envelope("The SDK name is not supported")
    _require_bounded(message.sdk_version, "SDK version", 64)
    if message.sent_at_ms <= 0:
        raise _invalid_envelope("The envelope sent_at_ms must be positive")
    if not _BATCH_ID_PATTERN.fullmatch(message.batch_id):
        raise _invalid_envelope("The envelope batch ID is malformed")
    if not message.events:
        raise _invalid_envelope("The V3 envelope must contain at least one event")
    if len(message.events) > max_events:
        raise ApiError(413, "payload_too_large", "The batch contains too many events")

    resource_values = {
        name: getattr(message.resource, attribute)
        for name, attribute in _RESOURCE_UNKNOWN_KEYS.items()
    }
    for name, value in resource_values.items():
        _require_bounded(value, name, MAX_IDENTIFIER_BYTES)
    _require_bounded(
        message.resource.service_version,
        "resource.serviceVersion",
        MAX_IDENTIFIER_BYTES,
    )
    # V3 ignores the batch installation for analytics, but a present value remains bounded.
    if message.resource.installation_id:
        _require_bounded(
            message.resource.installation_id,
            "resource.installationId",
            MAX_IDENTIFIER_BYTES,
        )

    events = [
        _to_event(event, resource_values, index) for index, event in enumerate(message.events)
    ]
    expected_batch_id = stable_batch_id_v3([event.event_id for event in events])
    if message.batch_id != expected_batch_id:
        raise _invalid_envelope("The envelope batch ID does not match its ordered event IDs")
    return DecodedEnvelopeV3(
        events=events,
        batch_id=message.batch_id,
        sdk_version=message.sdk_version,
        service_name=message.resource.service_name,
        service_version=message.resource.service_version,
        deployment_environment=message.resource.deployment_environment,
    )


def stable_batch_id_v3(event_ids: list[str]) -> str:
    """Recompute the schema-V3 batch identity with ordered length delimiters."""
    digest = hashlib.sha256()
    digest.update(bytes((int(SCHEMA_VERSION_V3),)))
    for event_id in event_ids:
        identity = event_id.encode("utf-8")
        digest.update(len(identity).to_bytes(4, byteorder="big", signed=False))
        digest.update(identity)
    return V3_BATCH_ID_PREFIX + digest.digest()[:V3_BATCH_ID_HASH_BYTES].hex()


def _to_event(
    message: ApmEventMessage,
    resource_values: dict[str, str],
    event_index: int,
) -> ApmEvent:
    """Convert one strict V3 event without retaining installation plaintext in unknown maps."""
    if message.fields:
        raise _invalid_event(
            "V3 events must use typed_fields instead of legacy fields", event_index
        )
    if not message.HasField("occurrence"):
        raise _invalid_event("V3 events require an occurrence snapshot", event_index)
    fields: dict[str, object] = {}
    field_types: dict[str, str] = {}
    try:
        for key, typed in message.typed_fields.items():
            fields[key] = _typed_value(typed)
            field_types[key] = typed.type
        occurrence = OccurrenceContext(
            service_version=message.occurrence.service_version,
            version_code=message.occurrence.version_code,
            app_build=message.occurrence.app_build,
            variant=message.occurrence.variant,
            installation_id=message.occurrence.installation_id,
            native_frames=tuple(
                NativeFrameIdentity(
                    abi=frame.abi,
                    module_build_id=frame.module_build_id,
                    module_name=frame.module_name,
                    module_relative_pc=frame.module_relative_pc,
                    load_bias=frame.load_bias if frame.HasField("load_bias") else None,
                )
                for frame in message.occurrence.native_frames
            ),
        )
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
            occurrence=occurrence,
        )
    except (ValueError, ValidationError) as error:
        raise ApiError(
            422,
            "invalid_event",
            _safe_validation_message(error),
            False,
            event_index,
        ) from error


def _invalid_envelope(message: str) -> ApiError:
    """Create a whole-envelope validation failure."""
    return ApiError(422, "invalid_event", message, False, 0)


def _invalid_event(message: str, event_index: int) -> ApiError:
    """Create an event-located V3 validation failure."""
    return ApiError(422, "invalid_event", message, False, event_index)
