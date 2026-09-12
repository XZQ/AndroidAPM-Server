"""Transactional inbox insertion, pseudonymization, and content-conflict detection."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.constants import (
    IDENTITY_QUALITY_AUTHENTICATED,
    MAX_EVENT_JSON_BYTES,
    NORMALIZATION_VERSION,
    TIMESTAMP_QUALITY_EVENT_DECLARED,
)
from androidapm_server.db.models import InboxEvent
from androidapm_server.domain import ApmEvent, IdentityQuality, IngestMetadata, NativeFrameIdentity
from androidapm_server.errors import ApiError
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.normalization import NormalizationResult, normalize_event


@dataclass(frozen=True, slots=True)
class InsertResult:
    """Counts returned by one committed whole-batch insert."""

    received: int
    inserted: int
    duplicates: int


@dataclass(frozen=True, slots=True)
class PreparedEvent:
    """One canonical event after plaintext minimization but before database insertion."""

    event: ApmEvent
    payload: dict[str, Any]
    payload_hash: str
    app_version: str | None
    app_build: str | None
    version_code: str | None
    variant: str | None
    release_quality: IdentityQuality
    installation_quality: IdentityQuality
    installation_hmac: str | None
    installation_hmac_key_version: str | None
    installation_plaintext: str | None
    normalization: NormalizationResult

    @property
    def conflict_signature(self) -> tuple[object, ...]:
        """Return every fact that must stay stable for one tenant/event ID."""
        return (
            self.payload_hash,
            self.app_version,
            self.app_build,
            self.version_code,
            self.variant,
            self.release_quality.value,
            self.installation_quality.value,
            self.installation_hmac,
        )


async def insert_batch(
    session: AsyncSession,
    metadata: IngestMetadata,
    events: Sequence[ApmEvent],
    installation_keys: InstallationHmacKeyRing | None = None,
) -> InsertResult:
    """Insert all new identities atomically and acknowledge exact duplicates."""
    prepared = _prepare_unique_events(metadata, events, installation_keys)
    existing_result = await session.execute(
        select(
            InboxEvent.event_id,
            InboxEvent.payload_sha256,
            InboxEvent.app_id,
            InboxEvent.environment,
            InboxEvent.app_version,
            InboxEvent.app_build,
            InboxEvent.version_code,
            InboxEvent.variant,
            InboxEvent.release_identity_quality,
            InboxEvent.installation_identity_quality,
            InboxEvent.installation_hmac,
            InboxEvent.installation_hmac_key_version,
        ).where(
            InboxEvent.tenant_id == metadata.tenant_id,
            InboxEvent.event_id.in_(prepared),
        )
    )
    existing = {row[0]: row[1:] for row in existing_result.tuples().all()}
    for event_id, existing_values in existing.items():
        _assert_existing_matches(
            metadata,
            prepared[event_id],
            existing_values,
            installation_keys,
            concurrent=False,
        )

    rows = [_to_row(metadata, item) for item in prepared.values()]
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        statement = (
            postgresql_insert(InboxEvent)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["tenant_id", "event_id"])
            .returning(InboxEvent.event_id)
        )
    elif dialect == "sqlite":
        statement = (
            sqlite_insert(InboxEvent)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["tenant_id", "event_id"])
            .returning(InboxEvent.event_id)
        )
    else:
        raise RuntimeError(f"Unsupported database dialect: {dialect}")

    inserted_result = await session.execute(statement)
    inserted_ids = set(inserted_result.scalars().all())
    inserted = len(inserted_ids)

    # A concurrent transaction may win the unique-key race after the initial read. Re-read every
    # non-inserted identity so ON CONFLICT never turns different content into a harmless replay.
    raced_ids = prepared.keys() - inserted_ids - existing.keys()
    if raced_ids:
        raced_result = await session.execute(
            select(
                InboxEvent.event_id,
                InboxEvent.payload_sha256,
                InboxEvent.app_id,
                InboxEvent.environment,
                InboxEvent.app_version,
                InboxEvent.app_build,
                InboxEvent.version_code,
                InboxEvent.variant,
                InboxEvent.release_identity_quality,
                InboxEvent.installation_identity_quality,
                InboxEvent.installation_hmac,
                InboxEvent.installation_hmac_key_version,
            ).where(
                InboxEvent.tenant_id == metadata.tenant_id,
                InboxEvent.event_id.in_(raced_ids),
            )
        )
        raced = {row[0]: row[1:] for row in raced_result.tuples().all()}
        if raced.keys() != raced_ids:
            raise RuntimeError("A conflicting inbox row was not visible after UPSERT")
        for event_id, raced_values in raced.items():
            _assert_existing_matches(
                metadata,
                prepared[event_id],
                raced_values,
                installation_keys,
                concurrent=True,
            )
    return InsertResult(len(events), inserted, len(events) - inserted)


def _prepare_unique_events(
    metadata: IngestMetadata,
    events: Sequence[ApmEvent],
    installation_keys: InstallationHmacKeyRing | None,
) -> dict[str, PreparedEvent]:
    """Canonicalize, pseudonymize, and reject conflicting identities inside one batch."""
    prepared: dict[str, PreparedEvent] = {}
    for index, event in enumerate(events):
        item = _prepare_event(metadata, event, installation_keys)
        serialized = json.dumps(
            item.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if len(serialized.encode("utf-8")) > MAX_EVENT_JSON_BYTES:
            raise ApiError(
                413,
                "payload_too_large",
                "An event exceeds the canonical event size limit",
                False,
                index,
            )
        item = PreparedEvent(
            event=item.event,
            payload=item.payload,
            payload_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            app_version=item.app_version,
            app_build=item.app_build,
            version_code=item.version_code,
            variant=item.variant,
            release_quality=item.release_quality,
            installation_quality=item.installation_quality,
            installation_hmac=item.installation_hmac,
            installation_hmac_key_version=item.installation_hmac_key_version,
            installation_plaintext=item.installation_plaintext,
            normalization=item.normalization,
        )
        previous = prepared.get(event.event_id)
        if previous is not None and previous.conflict_signature != item.conflict_signature:
            raise ApiError(
                409,
                "event_id_conflict",
                "The batch reuses an eventId for different content or occurrence identity",
                False,
                index,
            )
        prepared[event.event_id] = item
    return prepared


def _prepare_event(
    metadata: IngestMetadata,
    event: ApmEvent,
    installation_keys: InstallationHmacKeyRing | None,
) -> PreparedEvent:
    """Build one plaintext-free payload plus provenance-aware structured identity."""
    occurrence = event.occurrence
    app_version: str | None
    app_build: str | None
    version_code: str | None
    variant: str | None
    installation_plaintext: str | None
    if occurrence is not None:
        app_version = occurrence.service_version
        app_build = occurrence.app_build
        version_code = occurrence.version_code
        variant = occurrence.variant
        release_quality = IdentityQuality.OCCURRENCE_BOUND
        installation_quality = IdentityQuality.OCCURRENCE_BOUND
        installation_plaintext = occurrence.installation_id
    else:
        app_version = metadata.app_version
        app_build = metadata.app_build
        version_code = metadata.version_code
        variant = metadata.variant
        release_quality = metadata.release_identity_quality
        installation_quality = metadata.installation_identity_quality
        installation_plaintext = metadata.installation_id

    installation_hmac: str | None = None
    installation_hmac_key_version: str | None = None
    if installation_plaintext is not None:
        if installation_keys is None:
            raise ApiError(
                503,
                "identity_service_unavailable",
                "Installation pseudonymization is not configured",
                True,
            )
        installation_hmac_key_version = installation_keys.active_version
        installation_hmac = installation_keys.digest(
            metadata.tenant_id,
            installation_plaintext,
        )

    payload = event.model_dump(mode="json")
    _remove_known_installation_plaintext(payload, installation_plaintext)
    # Identity cannot be silently rewritten: reject a batch if its required routing/release
    # identity embeds the installation secret. Optional evidence is minimized below instead.
    if installation_plaintext and any(
        installation_plaintext in value
        for value in (
            event.event_id,
            event.module,
            event.name,
            event.process_name,
            event.thread_name,
            metadata.tenant_id,
            metadata.app_id,
            metadata.environment,
            metadata.sdk_version,
            metadata.request_id,
            app_version,
            app_build,
            version_code,
            variant,
        )
        if isinstance(value, str)
    ):
        raise ApiError(
            422, "privacy_identity_conflict", "Required identity contains installation data"
        )
    safe_occurrence = None
    if occurrence is not None:
        # A partially scrubbed native frame is no longer a usable exact identity.
        frames = []
        occurrence_payload = payload.get("occurrence", {})
        for frame in occurrence_payload.get("native_frames", []):
            try:
                frames.append(NativeFrameIdentity.model_validate(frame))
            except ValidationError:
                continue
        occurrence_payload["native_frames"] = [frame.model_dump(mode="json") for frame in frames]
        safe_occurrence = occurrence.model_copy(update={"native_frames": tuple(frames)})
    safe_event = event.model_copy(
        update={
            key: payload.get(key, {} if key != "scene" else None)
            for key in ("fields", "field_types", "global_context", "extras", "unknown", "scene")
        }
        | {"occurrence": safe_occurrence}
    )
    normalization = normalize_event(safe_event)
    return PreparedEvent(
        event=safe_event,
        payload=payload,
        payload_hash="",
        app_version=app_version,
        app_build=app_build,
        version_code=version_code,
        variant=variant,
        release_quality=release_quality,
        installation_quality=installation_quality,
        installation_hmac=installation_hmac,
        installation_hmac_key_version=installation_hmac_key_version,
        installation_plaintext=installation_plaintext,
        normalization=normalization,
    )


def _remove_known_installation_plaintext(value: object, plaintext: str | None) -> None:
    """Remove the known identifier from every durable map/list location before serialization."""
    if not plaintext:
        return
    if isinstance(value, dict):
        for key in list(value):
            item = value[key]
            if plaintext in key or (isinstance(item, str) and plaintext in item):
                del value[key]
            else:
                _remove_known_installation_plaintext(item, plaintext)
    elif isinstance(value, list):
        value[:] = [item for item in value if not (isinstance(item, str) and plaintext in item)]
        for item in value:
            _remove_known_installation_plaintext(item, plaintext)


def _assert_existing_matches(
    metadata: IngestMetadata,
    prepared: PreparedEvent,
    existing: tuple[object, ...],
    installation_keys: InstallationHmacKeyRing | None,
    *,
    concurrent: bool,
) -> None:
    """Verify one existing row using its stored HMAC version during key rotation."""
    (
        payload_hash,
        app_id,
        environment,
        app_version,
        app_build,
        version_code,
        variant,
        release_quality,
        installation_quality,
        installation_hmac,
        installation_hmac_key_version,
    ) = existing
    expected = (
        prepared.payload_hash,
        metadata.app_id,
        metadata.environment,
        prepared.app_version,
        prepared.app_build,
        prepared.version_code,
        prepared.variant,
        prepared.release_quality.value,
        prepared.installation_quality.value,
    )
    actual = (
        payload_hash,
        app_id,
        environment,
        app_version,
        app_build,
        version_code,
        variant,
        release_quality,
        installation_quality,
    )
    matches_installation = _matches_existing_installation(
        metadata.tenant_id,
        prepared.installation_plaintext,
        installation_hmac,
        installation_hmac_key_version,
        installation_keys,
    )
    if actual != expected or not matches_installation:
        qualifier = "concurrently " if concurrent else ""
        raise ApiError(
            409,
            "event_id_conflict",
            f"An eventId was {qualifier}used for different content or occurrence identity",
        )


def _matches_existing_installation(
    tenant_id: str,
    plaintext: str | None,
    stored_hmac: object,
    stored_version: object,
    installation_keys: InstallationHmacKeyRing | None,
) -> bool:
    """Compare replay identity with the row's original key version, not the active version."""
    if plaintext is None:
        return stored_hmac is None and stored_version is None
    if (
        installation_keys is None
        or not isinstance(stored_hmac, str)
        or not isinstance(stored_version, str)
    ):
        return False
    try:
        candidate = installation_keys.digest(tenant_id, plaintext, stored_version)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, stored_hmac)


def _to_row(metadata: IngestMetadata, prepared: PreparedEvent) -> dict[str, Any]:
    """Create one insert mapping using authenticated scope and sanitized event identity."""
    normalization = prepared.normalization
    return {
        "tenant_id": metadata.tenant_id,
        "event_id": prepared.event.event_id,
        "app_id": metadata.app_id,
        "environment": metadata.environment,
        "schema_version": metadata.schema_version,
        "sdk_version": metadata.sdk_version,
        "app_version": prepared.app_version,
        "app_build": prepared.app_build,
        "version_code": prepared.version_code,
        "variant": prepared.variant,
        "protocol": metadata.protocol,
        "scope_identity_quality": IDENTITY_QUALITY_AUTHENTICATED,
        "release_identity_quality": prepared.release_quality.value,
        "installation_identity_quality": prepared.installation_quality.value,
        "installation_hmac": prepared.installation_hmac,
        "installation_hmac_key_version": prepared.installation_hmac_key_version,
        "event_timestamp_ms": prepared.event.timestamp,
        "occurrence_timestamp_ms": normalization.occurrence_timestamp_ms,
        "collection_timestamp_ms": normalization.collection_timestamp_ms,
        "timestamp_quality": TIMESTAMP_QUALITY_EVENT_DECLARED,
        "payload_json": prepared.payload,
        "payload_sha256": prepared.payload_hash,
        "payload_size_bytes": len(
            json.dumps(prepared.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ),
        "normalization_version": NORMALIZATION_VERSION,
        "normalized_json": normalization.normalized_json,
        "incident_fingerprint": normalization.incident_fingerprint,
        "raw_incident_fingerprint": normalization.incident_fingerprint,
        "native_identity_json": normalization.native_identity_json,
        "request_id": metadata.request_id,
    }
