from __future__ import annotations

import pytest

from androidapm_server.errors import ApiError
from androidapm_server.generated.apm_event_pb2 import ApmBatchEnvelope
from androidapm_server.protocol.envelope_v3 import decode_envelope_v3, stable_batch_id_v3


def envelope(event_id: str = "v3-event-1") -> ApmBatchEnvelope:
    """Build the smallest complete schema-V3 envelope."""
    message = ApmBatchEnvelope(
        schema_version=3,
        sdk_name="android-apm",
        sdk_version="0.1.0",
        batch_id=stable_batch_id_v3([event_id]),
        sent_at_ms=1_700_000_000_100,
    )
    message.resource.service_name = "com.example"
    message.resource.service_version = "current-uploader-version"
    message.resource.deployment_environment = "test"
    event = message.events.add(
        timestamp=1_700_000_000_000,
        event_id=event_id,
        module="crash",
        name="native_crash",
        kind="ALERT",
        severity="FATAL",
        priority="CRITICAL",
        process_name="com.example",
        thread_name="main",
    )
    event.typed_fields["backtrace"].type = "STRING"
    event.typed_fields["backtrace"].value = "#00 pc 00001234 libsample.so"
    event.occurrence.service_version = "old-occurrence-version"
    event.occurrence.version_code = "42"
    event.occurrence.app_build = "build-42"
    event.occurrence.variant = "release"
    event.occurrence.installation_id = "anonymous-installation"
    frame = event.occurrence.native_frames.add(
        abi="arm64-v8a",
        module_build_id="0123456789abcdef",
        module_name="libsample.so",
        module_relative_pc=0x1234,
    )
    frame.load_bias = 0x70000000
    return message


def test_decodes_occurrence_release_and_native_identity() -> None:
    decoded = decode_envelope_v3(envelope().SerializeToString(), max_events=32)

    assert decoded.batch_id == stable_batch_id_v3(["v3-event-1"])
    assert decoded.event_count == 1
    event = decoded.events[0]
    assert event.occurrence is not None
    assert event.occurrence.service_version == "old-occurrence-version"
    assert event.occurrence.installation_id == "anonymous-installation"
    assert event.occurrence.native_frames[0].module_relative_pc == 0x1234
    assert event.occurrence.native_frames[0].load_bias == 0x70000000
    assert "installation" not in str(event.unknown).lower()


def test_requires_occurrence_and_rejects_legacy_field_smuggling() -> None:
    missing = envelope()
    missing.events[0].ClearField("occurrence")
    with pytest.raises(ApiError, match="occurrence"):
        decode_envelope_v3(missing.SerializeToString(), max_events=32)

    legacy = envelope()
    legacy.events[0].fields["backtrace"] = "legacy"
    with pytest.raises(ApiError, match="typed_fields"):
        decode_envelope_v3(legacy.SerializeToString(), max_events=32)


def test_batch_identity_is_schema_separated_from_v2() -> None:
    assert stable_batch_id_v3(["same-event"]).startswith("b3-")
