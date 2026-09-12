"""Projection bounds keep valid raw events deliverable even when a metric is invalid."""

import json

import pytest

from androidapm_server.domain import ApmEvent
from androidapm_server.normalization import normalize_event


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
