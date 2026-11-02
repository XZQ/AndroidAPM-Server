"""Projection bounds keep valid raw events deliverable even when a metric is invalid."""

import json

import pytest

from androidapm_server.domain import ApmEvent, NativeFrameIdentity, OccurrenceContext
from androidapm_server.normalization import normalize_event


def test_native_fingerprints_preserve_relative_pcs_and_ignore_load_bias() -> None:
    event = ApmEvent(
        timestamp=1_700_000_000_000,
        event_id="native",
        module="crash",
        name="native_crash",
        kind="ALERT",
        severity="FATAL",
        priority="CRITICAL",
        process_name="app",
        thread_name="main",
        fields={"signal": 11, "backtrace": "#00 pc 00001234 /private/libsample.so"},
    )
    first = normalize_event(event)
    changed_pc = normalize_event(
        event.model_copy(
            update={"fields": {"signal": 11, "backtrace": "#00 pc 00002345 /private/libsample.so"}}
        )
    )
    tombstone = normalize_event(event.model_copy(update={"name": "tombstone_crash"}))
    assert (
        first.incident_fingerprint and first.incident_fingerprint != changed_pc.incident_fingerprint
    )
    assert tombstone.incident_fingerprint != first.incident_fingerprint
    frame = NativeFrameIdentity(
        abi="arm64-v8a",
        module_build_id="0123456789abcdef",
        module_name="libsample.so",
        module_relative_pc=0x1234,
        load_bias=0x70000000,
    )
    context = OccurrenceContext(
        service_version="1",
        version_code="1",
        app_build="b",
        variant="debug",
        installation_id="i",
        native_frames=[frame],
    )
    bound = event.model_copy(update={"occurrence": context})
    relocated = bound.model_copy(
        update={
            "occurrence": context.model_copy(
                update={"native_frames": [frame.model_copy(update={"load_bias": 0x80000000})]}
            )
        }
    )
    assert (
        normalize_event(bound).incident_fingerprint
        == normalize_event(relocated).incident_fingerprint
    )


@pytest.mark.parametrize(
    "backtrace", ["#00 pc (see tombstone for full backtrace)", "fault at 0x12345678", "", "unknown"]
)
def test_native_placeholder_never_invents_an_issue(backtrace: str) -> None:
    event = ApmEvent(
        timestamp=1_700_000_000_000,
        event_id="native-placeholder",
        module="crash",
        name="native_crash",
        kind="ALERT",
        severity="FATAL",
        priority="CRITICAL",
        process_name="app",
        thread_name="main",
        fields={"signal": 11, "backtrace": backtrace},
    )
    assert normalize_event(event).incident_fingerprint is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("dropRate", "NaN"),
        ("dropRate", "Infinity"),
        ("dropRate", "-Infinity"),
        ("dropRate", "1e9999"),
        ("dropRate", 1.01),
        ("dropRate", -0.1),
        ("queueSize", str(2**63)),
        ("queueSize", -1),
        ("queueBytes", None),
    ],
)
def test_invalid_registered_numbers_remain_raw_but_never_enter_json_or_otlp(
    field: str,
    value: object,
) -> None:
    event = ApmEvent(
        timestamp=1_700_000_000_000,
        event_id="bounds",
        module="core",
        name="sdk_health",
        kind="METRIC",
        severity="INFO",
        priority="NORMAL",
        process_name="app",
        thread_name="main",
        fields={field: value, "emitCount": 42},
    )
    result = normalize_event(event).normalized_json
    assert result["field_states"][field] == "INVALID"
    assert field not in result["registered_fields"]
    assert field not in result["indexed_attributes"]
    assert result["registered_fields"]["emitCount"] == 42
    assert event.fields[field] == value
    json.dumps(result, allow_nan=False)


def test_exit_timestamp_cannot_overflow_the_otlp_clock() -> None:
    event = ApmEvent(
        timestamp=1_700_000_000_000,
        event_id="exit-bounds",
        module="crash",
        name="app_exit",
        kind="ALERT",
        severity="INFO",
        priority="NORMAL",
        process_name="app",
        thread_name="main",
        fields={"exitTimestamp": 2**63 - 1},
    )
    result = normalize_event(event)
    assert result.occurrence_timestamp_ms == event.timestamp
    assert result.normalized_json["field_states"]["exitTimestamp"] == "INVALID"
