"""Versioned source-reviewed normalization for the first Android APM product slice."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

from androidapm_server.constants import (
    EVIDENCE_AVAILABLE,
    EVIDENCE_EXPIRED,
    MAX_FUTURE_SKEW_SECONDS,
    NORMALIZATION_VERSION,
    OTLP_INT64_MAX,
    OTLP_INT64_MIN,
)
from androidapm_server.domain import ApmEvent, NativeFrameIdentity
from androidapm_server.performance_catalog import DIMENSION_FIELDS, PERFORMANCE_FIELDS

FieldType = Literal["integer", "number", "boolean", "string"]
Cardinality = Literal["low", "medium", "high", "sensitive"]


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """One exact `(schema,module,name,field)` normalization contract."""

    schema_min: int
    module: str
    name: str
    field: str
    field_type: FieldType
    unit: str | None
    cardinality: Cardinality
    pii: bool
    indexed: bool


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """Derived, reproducible projection stored beside recoverable raw evidence."""

    occurrence_timestamp_ms: int
    collection_timestamp_ms: int
    normalized_json: dict[str, Any]
    incident_fingerprint: str | None
    native_identity_json: list[dict[str, Any]]


FIELD_REGISTRY: tuple[FieldDefinition, ...] = (
    FieldDefinition(
        1, "crash", "java_crash", "exceptionClass", "string", None, "medium", False, True
    ),
    FieldDefinition(
        1, "crash", "java_crash", "exceptionMessage", "string", None, "sensitive", True, False
    ),
    FieldDefinition(
        1, "crash", "java_crash", "stackTrace", "string", None, "sensitive", True, False
    ),
    FieldDefinition(1, "crash", "java_crash", "threadName", "string", None, "medium", False, True),
    FieldDefinition(1, "crash", "java_crash", "processName", "string", None, "medium", False, True),
    FieldDefinition(1, "anr", "anr_detected", "anrSource", "string", None, "low", False, True),
    FieldDefinition(1, "anr", "anr_detected", "anrCause", "string", None, "low", False, True),
    FieldDefinition(1, "anr", "anr_detected", "durationMs", "number", "ms", "low", False, True),
    FieldDefinition(1, "anr", "anr_detected", "processName", "string", None, "medium", False, True),
    FieldDefinition(
        1, "anr", "anr_detected", "mainThreadStack", "string", None, "sensitive", True, False
    ),
    FieldDefinition(
        1, "anr", "anr_detected", "tracesContent", "string", None, "sensitive", True, False
    ),
    FieldDefinition(
        1, "anr", "anr_detected", "stackSamples", "string", None, "sensitive", True, False
    ),
    FieldDefinition(
        1, "crash", "app_exit", "exitTimestamp", "integer", "ms_epoch", "low", False, False
    ),
    FieldDefinition(1, "crash", "app_exit", "reasonCode", "integer", None, "low", False, True),
    FieldDefinition(1, "crash", "app_exit", "reasonName", "string", None, "low", False, True),
    FieldDefinition(1, "crash", "app_exit", "importance", "integer", None, "low", False, True),
    FieldDefinition(
        1, "crash", "app_exit", "description", "string", None, "sensitive", True, False
    ),
    FieldDefinition(1, "crash", "app_exit", "trace", "string", None, "sensitive", True, False),
    FieldDefinition(1, "core", "sdk_health", "emitCount", "integer", "events", "low", False, True),
    FieldDefinition(1, "core", "sdk_health", "dropCount", "integer", "events", "low", False, True),
    FieldDefinition(1, "core", "sdk_health", "dropRate", "number", "ratio", "low", False, True),
    FieldDefinition(1, "core", "sdk_health", "queueSize", "integer", "events", "low", False, True),
    FieldDefinition(1, "core", "sdk_health", "queueBytes", "integer", "bytes", "low", False, True),
    FieldDefinition(
        1, "core", "sdk_health", "avgUploadLatencyMs", "number", "ms", "low", False, True
    ),
    FieldDefinition(
        1, "core", "sdk_health", "maxUploadLatencyMs", "number", "ms", "low", False, True
    ),
    FieldDefinition(
        1, "core", "sdk_health", "internalErrorCount", "integer", "events", "low", False, True
    ),
    FieldDefinition(
        1,
        "core",
        "sdk_health",
        "diagnosticDroppedCount",
        "integer",
        "events",
        "low",
        False,
        True,
    ),
    FieldDefinition(
        1,
        "core",
        "sdk_health",
        "diagnosticWriteFailureCount",
        "integer",
        "events",
        "low",
        False,
        True,
    ),
)

FIELD_REGISTRY += tuple(
    FieldDefinition(
        1, item.module, item.name, item.field, item.field_type, item.unit, "low", False, True
    )
    for item in PERFORMANCE_FIELDS
)
FIELD_REGISTRY += tuple(
    FieldDefinition(
        1, item.module, item.name, item.field, item.field_type, None, "low", False, True
    )
    for item in DIMENSION_FIELDS
)
FIELD_REGISTRY += (
    FieldDefinition(
        1,
        "core",
        "session_start",
        "session_schema_version",
        "integer",
        "events",
        "low",
        False,
        True,
    ),
    FieldDefinition(
        1, "core", "session_start", "session_count", "integer", "events", "low", False, True
    ),
)
FIELD_REGISTRY += tuple(
    FieldDefinition(
        1,
        "crash",
        name,
        field,
        cast(FieldType, field_type),
        unit,
        cast(Cardinality, cardinality),
        pii,
        indexed,
    )
    for name in ("native_crash", "tombstone_crash")
    for field, field_type, unit, cardinality, pii, indexed in (
        ("signal", "integer", "events", "low", False, True),
        ("signalName", "string", None, "low", False, False),
        ("backtrace", "string", None, "sensitive", True, False),
        ("faultAddr", "string", None, "sensitive", True, False),
    )
)
FIELD_REGISTRY += tuple(
    FieldDefinition(1, "gc_monitor", "memory_churn", field, "boolean", None, "low", False, True)
    for field in ("gcCountersAvailable", "allocationRateAvailable", "reclaimRateAvailable")
)

_REGISTRY_BY_EVENT: dict[tuple[str, str], tuple[FieldDefinition, ...]] = {}
_DIMENSIONS_BY_KEY = {(item.module, item.name, item.field): item for item in DIMENSION_FIELDS}
_unique_fields: dict[tuple[str, str, str], FieldDefinition] = {}
for _definition in FIELD_REGISTRY:
    _unique_fields.setdefault(
        (_definition.module, _definition.name, _definition.field), _definition
    )
FIELD_REGISTRY = tuple(_unique_fields.values())
for _definition in FIELD_REGISTRY:
    _key = (_definition.module, _definition.name)
    _REGISTRY_BY_EVENT[_key] = (*_REGISTRY_BY_EVENT.get(_key, ()), _definition)

_INCIDENT_EVENTS = frozenset(
    {
        ("crash", "java_crash"),
        ("anr", "anr_detected"),
        ("crash", "native_crash"),
        ("crash", "tombstone_crash"),
    }
)
_STACK_FIELDS = {
    ("crash", "java_crash"): ("stackTrace",),
    ("anr", "anr_detected"): ("mainThreadStack", "stackSamples"),
}
_ADDRESS_PATTERN = re.compile(r"(?:0x)?[0-9a-fA-F]{6,}")
_LINE_NUMBER_PATTERN = re.compile(r":\d+\)")


def registry_snapshot() -> list[dict[str, Any]]:
    """Return a deterministic JSON-safe registry snapshot for drift tests and docs."""
    return [asdict(definition) for definition in FIELD_REGISTRY]


def field_availability(normalized: dict[str, Any], *, raw_pruned: bool) -> dict[str, str]:
    """Keep original invalid/missing states; removed available fields become expired."""
    states = normalized.get("field_states")
    if not isinstance(states, dict):
        return {}
    retained = normalized.get("registered_fields")
    if not isinstance(retained, dict):
        retained = {}
    return {
        key: EVIDENCE_EXPIRED
        if raw_pruned and state == EVIDENCE_AVAILABLE and key not in retained
        else state
        for key, state in states.items()
        if isinstance(key, str) and isinstance(state, str)
    }


def normalize_event(
    event: ApmEvent,
    *,
    native_frames: tuple[NativeFrameIdentity, ...] | None = None,
) -> NormalizationResult:
    """Normalize one event, optionally using retained exact frames without inventing identity."""
    definitions = _REGISTRY_BY_EVENT.get((event.module, event.name), ())
    registered_fields: dict[str, Any] = {}
    indexed_attributes: dict[str, Any] = {}
    field_states: dict[str, str] = {}
    by_name = {definition.field: definition for definition in definitions}
    for name, definition in by_name.items():
        if name not in event.fields:
            field_states[name] = "MISSING"
            continue
        converted = _coerce_registered(event.fields[name], definition.field_type)
        dimension = _DIMENSIONS_BY_KEY.get((event.module, event.name, name))
        if (
            dimension is not None
            and dimension.field_type != "boolean"
            and converted is not _INVALID
        ):
            if str(converted) not in dimension.allowed_values:
                # Preserve the state, never put an arbitrary category into aggregate indexes.
                field_states[name] = "UNKNOWN"
                continue
        if isinstance(converted, (int, float)) and not isinstance(converted, bool):
            # Physical counts/durations and ratios have semantic bounds as well as wire bounds.
            if (
                definition.unit
                in {
                    "events",
                    "bytes",
                    "ms",
                    "ms_epoch",
                    "ratio",
                    "fps",
                    "frames",
                    "MiB",
                    "KiB",
                    "KiB/s",
                    "%",
                    "s",
                    "ns",
                    "us",
                    "Hz",
                    "objects",
                    "depth",
                    "cores",
                }
                and converted < 0
            ):
                converted = _INVALID
            elif definition.unit == "ratio" and converted > 1:
                converted = _INVALID
            elif definition.unit == "%" and converted > 100:
                converted = _INVALID
            elif definition.unit == "ratio_delta" and not -1 <= converted <= 1:
                converted = _INVALID
            elif definition.unit == "ms_epoch" and (
                converted <= 0 or converted > event.timestamp + MAX_FUTURE_SKEW_SECONDS * 1_000
            ):
                converted = _INVALID
        if converted is _INVALID:
            field_states[name] = "INVALID"
            continue
        field_states[name] = "AVAILABLE"
        registered_fields[name] = converted
        if definition.indexed:
            indexed_attributes[name] = converted

    occurrence_timestamp_ms = event.timestamp
    timestamp_source = "event.timestamp"
    if (event.module, event.name) == ("crash", "app_exit"):
        candidate = registered_fields.get("exitTimestamp")
        if isinstance(candidate, int) and candidate > 0:
            occurrence_timestamp_ms = candidate
            timestamp_source = "fields.exitTimestamp"

    resolved_native_frames = native_frames
    if resolved_native_frames is None:
        resolved_native_frames = event.occurrence.native_frames if event.occurrence else ()
    fingerprint = _incident_fingerprint(event, resolved_native_frames)
    native_identity = [frame.model_dump(mode="json") for frame in resolved_native_frames]

    normalized = {
        "module": event.module,
        "name": event.name,
        "normalization_version": NORMALIZATION_VERSION,
        "event_family": _event_family(event),
        "occurrence_timestamp_ms": occurrence_timestamp_ms,
        "collection_timestamp_ms": event.timestamp,
        "timestamp_source": timestamp_source,
        "registered_fields": registered_fields,
        "indexed_attributes": indexed_attributes,
        "field_states": field_states,
        "unknown_field_count": len(set(event.fields) - set(by_name)),
        "raw_available": True,
    }
    return NormalizationResult(
        occurrence_timestamp_ms=occurrence_timestamp_ms,
        collection_timestamp_ms=event.timestamp,
        normalized_json=normalized,
        incident_fingerprint=fingerprint,
        native_identity_json=native_identity,
    )


def _event_family(event: ApmEvent) -> str:
    """Return the exact product family without equating all crash-module events."""
    return {
        ("crash", "java_crash"): "JAVA_CRASH",
        ("crash", "native_crash"): "NATIVE_CRASH",
        ("crash", "tombstone_crash"): "NATIVE_TOMBSTONE",
        ("anr", "anr_detected"): "ANR",
        ("crash", "app_exit"): "APP_EXIT",
        ("core", "sdk_health"): "SDK_HEALTH",
        ("core", "session_start"): "SESSION_START",
    }.get((event.module, event.name), "UNREGISTERED")


def _incident_fingerprint(
    event: ApmEvent, native_frames: tuple[NativeFrameIdentity, ...]
) -> str | None:
    """Hash normalized stack evidence while keeping raw sensitive text out of indexes."""
    event_key = (event.module, event.name)
    if event_key not in _INCIDENT_EVENTS:
        return None
    if event.name in {"native_crash", "tombstone_crash"}:
        return _native_fingerprint(event, native_frames)
    evidence = next(
        (str(event.fields[field]) for field in _STACK_FIELDS[event_key] if event.fields.get(field)),
        "",
    )
    if not evidence:
        return None
    normalized_lines = []
    for line in evidence.splitlines()[:256]:
        scrubbed = _ADDRESS_PATTERN.sub("<addr>", line.strip())
        scrubbed = _LINE_NUMBER_PATTERN.sub(":<line>)", scrubbed)
        if scrubbed:
            normalized_lines.append(scrubbed)
    if not normalized_lines:
        return None
    canonical = json.dumps(normalized_lines, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _native_fingerprint(
    event: ApmEvent, native_frames: tuple[NativeFrameIdentity, ...]
) -> str | None:
    """Hash exact native evidence without collapsing relative PCs into one address token."""
    frames: list[Any] = [
        frame.model_dump(mode="json", exclude={"load_bias"}) for frame in native_frames
    ]
    if not frames:
        evidence = event.fields.get("backtrace")
        if not isinstance(evidence, str) or not evidence.strip():
            return None
        for line in evidence.splitlines()[:256]:
            # pc in tombstone frames is a relative ELF offset and must remain in the key.
            frame = re.search(r"#\d+\s+pc\s+([0-9a-fA-F]+)\s+(\S+)(.*)", line)
            if frame:
                frames.append(
                    [
                        frame[1].lower().lstrip("0") or "0",
                        frame[2].replace("\\", "/").rsplit("/", 1)[-1],
                        frame[3].strip(),
                    ]
                )
        # JNI's fallback text is not a frame. Do not merge all unresolved signals as one Issue.
        if not frames:
            return None
    canonical = json.dumps(
        {"family": _event_family(event), "signal": event.fields.get("signal"), "frames": frames},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _Invalid:
    """Sentinel distinguishing failed conversion from a legitimate null-like value."""


_INVALID = _Invalid()


def _coerce_registered(value: Any, expected: FieldType) -> Any:
    """Coerce only source-reviewed fields; arbitrary unknown values remain raw-only."""
    try:
        if expected == "integer":
            if isinstance(value, bool):
                return _INVALID
            converted = int(value)
            if isinstance(value, float) and not value.is_integer():
                return _INVALID
            if isinstance(value, str) and str(converted) != value:
                return _INVALID
            if not OTLP_INT64_MIN <= converted <= OTLP_INT64_MAX:
                return _INVALID
            return converted
        if expected == "number":
            if isinstance(value, bool):
                return _INVALID
            number = float(value)
            return number if math.isfinite(number) else _INVALID
        if expected == "boolean":
            if isinstance(value, bool):
                return value
            if value == "true":
                return True
            if value == "false":
                return False
            return _INVALID
        if expected == "string" and isinstance(value, str):
            return value
    except (TypeError, ValueError, OverflowError):
        return _INVALID
    return _INVALID
