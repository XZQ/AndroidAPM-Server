from __future__ import annotations

import pytest

from androidapm_server.errors import ApiError
from androidapm_server.generated.apm_event_pb2 import ApmBatchEnvelope
from androidapm_server.protocol.envelope_v2 import decode_envelope_v2, stable_batch_id


def envelope(event_id: str = "v2-event-1") -> ApmBatchEnvelope:
    message = ApmBatchEnvelope(
        schema_version=2,
        sdk_name="android-apm",
        sdk_version="0.1.0",
        batch_id=stable_batch_id([event_id]),
        sent_at_ms=1_700_000_000_100,
    )
    message.resource.service_name = "com.example"
    message.resource.service_version = "2.4.1"
    message.resource.deployment_environment = "test"
    message.resource.installation_id = "anonymous-installation"
    event = message.events.add(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="network",
        name="request",
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        process_name="com.example",
        thread_name="main",
    )
    scalars = {
        "nullValue": ("NULL", ""),
        "textValue": ("STRING", "hello"),
        "booleanValue": ("BOOLEAN", "true"),
        "byteValue": ("BYTE", "-7"),
        "shortValue": ("SHORT", "32000"),
        "intValue": ("INT", "42"),
        "longValue": ("LONG", "9223372036854775807"),
        "floatValue": ("FLOAT", "1.25"),
        "doubleValue": ("DOUBLE", "2.5"),
        "charValue": ("CHAR", "A"),
        "bigIntegerValue": ("BIG_INTEGER", "123456789012345678901234567890"),
        "bigDecimalValue": ("BIG_DECIMAL", "1234567890.0000000001"),
    }
    for name, (scalar_type, value) in scalars.items():
        event.typed_fields[name].type = scalar_type
        event.typed_fields[name].value = value
    return message


def test_decodes_all_client_scalar_types_without_guessing() -> None:
    decoded = decode_envelope_v2(envelope().SerializeToString(), max_events=32)

    assert decoded.batch_id == stable_batch_id(["v2-event-1"])
    assert decoded.event_count == 1
    assert decoded.service_name == "com.example"
    assert decoded.service_version == "2.4.1"
    event = decoded.events[0]
    assert event.fields == {
        "nullValue": None,
        "textValue": "hello",
        "booleanValue": True,
        "byteValue": -7,
        "shortValue": 32000,
        "intValue": 42,
        "longValue": 9_223_372_036_854_775_807,
        "floatValue": 1.25,
        "doubleValue": 2.5,
        "charValue": "A",
        "bigIntegerValue": "123456789012345678901234567890",
        "bigDecimalValue": "1234567890.0000000001",
    }
    assert event.field_types["bigDecimalValue"] == "BIG_DECIMAL"
    assert event.unknown["resource.installationId"] == "anonymous-installation"


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        (lambda message: setattr(message, "batch_id", "b2-" + ("0" * 32)), "batch ID"),
        (
            lambda message: setattr(
                message.events[0].typed_fields["intValue"],
                "value",
                "0042",
            ),
            "canonical",
        ),
        (
            lambda message: setattr(
                message.events[0].typed_fields["intValue"],
                "type",
                "FUTURE_SCALAR",
            ),
            "Unknown typed scalar",
        ),
    ],
)
def test_rejects_integrity_and_typed_scalar_violations(
    mutation: object,
    expected_message: str,
) -> None:
    message = envelope()
    mutation(message)  # type: ignore[operator]

    with pytest.raises(ApiError, match=expected_message):
        decode_envelope_v2(message.SerializeToString(), max_events=32)


def test_rejects_legacy_fields_inside_v2_event() -> None:
    message = envelope()
    message.events[0].fields["durationMs"] = "42"

    with pytest.raises(ApiError, match="typed_fields"):
        decode_envelope_v2(message.SerializeToString(), max_events=32)


def test_rejects_oversized_arbitrary_precision_text_before_integer_conversion() -> None:
    message = envelope()
    message.events[0].typed_fields["bigIntegerValue"].value = "1" * 4_097

    with pytest.raises(ApiError, match="4096"):
        decode_envelope_v2(message.SerializeToString(), max_events=32)
