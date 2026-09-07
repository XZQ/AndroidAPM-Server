"""Validated domain objects independent from HTTP and persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from androidapm_server.constants import (
    MAX_EVENT_ID_BYTES,
    MAX_FUTURE_SKEW_SECONDS,
    MAX_IDENTIFIER_BYTES,
    MAX_MAP_ENTRIES,
    MAX_MAP_KEY_BYTES,
    MAX_MAP_VALUE_BYTES,
)


class EventKind(StrEnum):
    """Kinds emitted by the Android SDK."""

    METRIC = "METRIC"
    ALERT = "ALERT"
    FILE = "FILE"


class EventSeverity(StrEnum):
    """Severity levels emitted by the Android SDK."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


class EventPriority(StrEnum):
    """Durable-delivery priority emitted by the Android SDK."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IdentityQuality(StrEnum):
    """Provenance quality persisted with every identity-bearing fact."""

    AUTHENTICATED = "AUTHENTICATED"
    OCCURRENCE_BOUND = "OCCURRENCE_BOUND"
    BATCH_DECLARED = "BATCH_DECLARED"
    REQUEST_DECLARED = "REQUEST_DECLARED"
    HOST_CONTEXT = "HOST_CONTEXT"
    ABSENT = "ABSENT"
    INVALID = "INVALID"


class NativeFrameIdentity(BaseModel):
    """Build-relative native frame identity suitable for exact symbol lookup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    abi: str
    module_build_id: str
    module_name: str
    module_relative_pc: int = Field(ge=0)
    load_bias: int | None = Field(default=None, ge=0)

    @field_validator("abi", "module_build_id", "module_name")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        """Require a non-empty bounded native build identifier."""
        if not value or value != value.strip() or len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
            raise ValueError("native frame identity must contain 1-256 trimmed UTF-8 bytes")
        return value


class OccurrenceContext(BaseModel):
    """Release/build/installation snapshot frozen into one durable Android event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service_version: str
    version_code: str
    app_build: str
    variant: str
    installation_id: str = Field(repr=False)
    native_frames: tuple[NativeFrameIdentity, ...] = ()

    @field_validator(
        "service_version",
        "app_build",
        "variant",
        "installation_id",
    )
    @classmethod
    def validate_occurrence_identity(cls, value: str) -> str:
        """Require complete, trimmed, bounded occurrence identity values."""
        if not value or value != value.strip() or len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
            raise ValueError("occurrence identity must contain 1-256 trimmed UTF-8 bytes")
        return value

    @field_validator("version_code")
    @classmethod
    def validate_version_code(cls, value: str) -> str:
        """Require the canonical unsigned decimal versionCode emitted by V3 clients."""
        if (
            not value
            or not value.isascii()
            or not value.isdecimal()
            or (len(value) > 1 and value.startswith("0"))
            or len(value.encode("utf-8")) > 64
        ):
            raise ValueError("versionCode must use canonical unsigned decimal text")
        return value

    @field_validator("native_frames")
    @classmethod
    def validate_native_frame_count(
        cls, value: tuple[NativeFrameIdentity, ...]
    ) -> tuple[NativeFrameIdentity, ...]:
        """Bound repeated native identity before downstream allocation."""
        if len(value) > 256:
            raise ValueError("occurrence identity exceeds 256 native frames")
        return value


class ApmEvent(BaseModel):
    """Canonical validated representation of one AndroidAPM event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: int = Field(gt=0)
    event_id: str
    module: str
    name: str
    kind: EventKind
    severity: EventSeverity
    priority: EventPriority
    process_name: str
    thread_name: str
    scene: str | None = None
    foreground: bool | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    field_types: dict[str, str] = Field(default_factory=dict)
    global_context: dict[str, str] = Field(default_factory=dict)
    extras: dict[str, str] = Field(default_factory=dict)
    unknown: dict[str, str] = Field(default_factory=dict)
    occurrence: OccurrenceContext | None = Field(default=None, repr=False)

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        """Require a bounded printable event identifier for deduplication."""
        encoded = value.encode("utf-8")
        if not value or len(encoded) > MAX_EVENT_ID_BYTES:
            raise ValueError("eventId must contain 1-128 UTF-8 bytes")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise ValueError("eventId must not contain control characters")
        return value

    @field_validator("module", "name", "process_name", "thread_name")
    @classmethod
    def validate_required_identifier(cls, value: str) -> str:
        """Validate required bounded identifiers without changing SDK values."""
        if not value or len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
            raise ValueError("identifier must contain 1-256 UTF-8 bytes")
        return value

    @field_validator("scene")
    @classmethod
    def validate_optional_identifier(cls, value: str | None) -> str | None:
        """Validate a present optional scene identifier."""
        if value is not None and len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
            raise ValueError("scene exceeds 256 UTF-8 bytes")
        return value

    @field_validator("fields", "field_types", "global_context", "extras", "unknown")
    @classmethod
    def validate_map(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Bound map cardinality and serialized key/value sizes."""
        if len(value) > MAX_MAP_ENTRIES:
            raise ValueError(f"map exceeds {MAX_MAP_ENTRIES} entries")
        for key, item in value.items():
            if not key or len(key.encode("utf-8")) > MAX_MAP_KEY_BYTES:
                raise ValueError("map key must contain 1-128 UTF-8 bytes")
            rendered = str(item)
            if len(rendered.encode("utf-8")) > MAX_MAP_VALUE_BYTES:
                raise ValueError("map value exceeds 8192 UTF-8 bytes")
        return value

    @model_validator(mode="after")
    def validate_timestamp_skew(self) -> ApmEvent:
        """Reject events implausibly far in the future while accepting old telemetry."""
        timestamp = datetime.fromtimestamp(self.timestamp / 1000, tz=UTC)
        if timestamp > datetime.now(UTC) + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
            raise ValueError("timestamp is too far in the future")
        return self


class IngestMetadata(BaseModel):
    """Trusted request envelope after header validation and authentication."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    tenant_id: str
    app_id: str
    environment: str
    schema_version: str
    sdk_version: str
    app_version: str | None = None
    app_build: str | None = None
    version_code: str | None = None
    variant: str | None = None
    protocol: str
    release_identity_quality: IdentityQuality = IdentityQuality.ABSENT
    installation_identity_quality: IdentityQuality = IdentityQuality.ABSENT
    installation_id: str | None = Field(default=None, repr=False, exclude=True)


class IngestAck(BaseModel):
    """Diagnostic whole-batch acknowledgement returned after durable commit."""

    requestId: str
    status: str = "accepted"
    received: int
    inserted: int
    duplicates: int
