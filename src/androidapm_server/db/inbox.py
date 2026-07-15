"""Transactional inbox insertion and content-conflict detection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.constants import MAX_EVENT_JSON_BYTES
from androidapm_server.db.models import InboxEvent
from androidapm_server.domain import ApmEvent, IngestMetadata
from androidapm_server.errors import ApiError


@dataclass(frozen=True, slots=True)
class InsertResult:
    """Counts returned by one committed whole-batch insert."""

    received: int
    inserted: int
    duplicates: int


async def insert_batch(
    session: AsyncSession,
    metadata: IngestMetadata,
    events: Sequence[ApmEvent],
) -> InsertResult:
    """Insert all new identities atomically and acknowledge exact duplicates."""
    prepared = _prepare_unique_events(events)
    existing_result = await session.execute(
        select(InboxEvent.event_id, InboxEvent.payload_sha256).where(
            InboxEvent.tenant_id == metadata.tenant_id,
            InboxEvent.event_id.in_(prepared),
        )
    )
    existing: dict[str, str] = dict(existing_result.tuples().all())
    for event_id, existing_hash in existing.items():
        if existing_hash != prepared[event_id][2]:
            raise ApiError(
                409,
                "event_id_conflict",
                "An eventId was previously used for different content",
            )

    rows = [
        _to_row(metadata, event, payload, payload_hash)
        for event, payload, payload_hash in prepared.values()
    ]
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

    # A concurrent transaction may have won the unique-key race after the initial read.
    # Re-read every non-inserted identity so different content is never acknowledged as a
    # harmless duplicate merely because ON CONFLICT protected the physical row.
    raced_ids = prepared.keys() - inserted_ids - existing.keys()
    if raced_ids:
        raced_result = await session.execute(
            select(InboxEvent.event_id, InboxEvent.payload_sha256).where(
                InboxEvent.tenant_id == metadata.tenant_id,
                InboxEvent.event_id.in_(raced_ids),
            )
        )
        raced: dict[str, str] = dict(raced_result.tuples().all())
        if raced.keys() != raced_ids:
            raise RuntimeError("A conflicting inbox row was not visible after UPSERT")
        for event_id, raced_hash in raced.items():
            if raced_hash != prepared[event_id][2]:
                raise ApiError(
                    409,
                    "event_id_conflict",
                    "An eventId was concurrently used for different content",
                )
    # Every original event is acknowledged; duplicates include repeated IDs inside this batch.
    return InsertResult(len(events), inserted, len(events) - inserted)


def _prepare_unique_events(
    events: Sequence[ApmEvent],
) -> dict[str, tuple[ApmEvent, dict[str, Any], str]]:
    """Canonicalize events and reject conflicting identities inside one batch."""
    prepared: dict[str, tuple[ApmEvent, dict[str, Any], str]] = {}
    for index, event in enumerate(events):
        payload = event.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if len(serialized.encode("utf-8")) > MAX_EVENT_JSON_BYTES:
            raise ApiError(
                413,
                "payload_too_large",
                "An event exceeds the canonical event size limit",
                False,
                index,
            )
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        previous = prepared.get(event.event_id)
        if previous is not None and previous[2] != payload_hash:
            raise ApiError(
                409,
                "event_id_conflict",
                "The batch reuses an eventId for different content",
                False,
                index,
            )
        prepared[event.event_id] = (event, payload, payload_hash)
    return prepared


def _to_row(
    metadata: IngestMetadata,
    event: ApmEvent,
    payload: dict[str, Any],
    payload_hash: str,
) -> dict[str, Any]:
    """Create one insert mapping using only trusted envelope identity."""
    return {
        "tenant_id": metadata.tenant_id,
        "event_id": event.event_id,
        "app_id": metadata.app_id,
        "environment": metadata.environment,
        "schema_version": metadata.schema_version,
        "sdk_version": metadata.sdk_version,
        "app_version": metadata.app_version,
        "app_build": metadata.app_build,
        "protocol": metadata.protocol,
        "event_timestamp_ms": event.timestamp,
        "payload_json": payload,
        "payload_sha256": payload_hash,
        "request_id": metadata.request_id,
    }
