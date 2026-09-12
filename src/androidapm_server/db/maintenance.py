"""Bounded raw-evidence retention and transactionally serialized capacity admission."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, exists, func, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from androidapm_server.config import Settings
from androidapm_server.constants import EVIDENCE_EXPIRED, EVIDENCE_MISSING
from androidapm_server.db.models import AuditLog, InboxEvent, IngestRateWindow, SymbolizationJob
from androidapm_server.errors import ApiError
from androidapm_server.metrics import RETENTION_EVENTS, RETENTION_LAST_SUCCESS
from androidapm_server.normalization import field_availability

LIVE_STATUSES = ("pending", "processing", "awaiting_symbols")
UNFINISHED_SYMBOL_STATUSES = ("pending", "processing", "symbols_missing")
CAPACITY_LOCK_ID = 0x41504D494E424F58
MAINTENANCE_TIMEOUT_SECONDS = 10


async def check_inbox_capacity(session: AsyncSession, settings: Settings) -> None:
    """Check new inserts before ACK; serialize PostgreSQL admissions until commit/rollback."""
    try:
        async with asyncio.timeout(MAINTENANCE_TIMEOUT_SECONDS):
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(text("SELECT set_config('statement_timeout', '10000', true)"))
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": CAPACITY_LOCK_ID}
                )
            # Only count up to the rejecting threshold for a large live backlog.
            live = (
                select(InboxEvent.id)
                .where(InboxEvent.status.in_(LIVE_STATUSES))
                .limit(settings.inbox_max_live_events + 1)
                .subquery()
            )
            count = await session.scalar(select(func.count()).select_from(live))
            raw_bytes = await session.scalar(
                select(func.coalesce(func.sum(InboxEvent.payload_size_bytes), 0)).where(
                    InboxEvent.raw_pruned_at.is_(None)
                )
            )
            if (count or 0) <= settings.inbox_max_live_events and (
                raw_bytes or 0
            ) <= settings.inbox_max_raw_bytes:
                return
    except TimeoutError as error:
        raise _capacity_error() from error
    raise _capacity_error()


def _capacity_error() -> ApiError:
    """Keep the client batch intact when storage admission cannot be confirmed."""
    return ApiError(
        503,
        "inbox_capacity_exceeded",
        "Inbox capacity is temporarily unavailable",
        True,
        headers={"Retry-After": "30"},
    )


async def maintain_once(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Commit one bounded cleanup batch; retain immutable identities for duplicate replay."""
    if not settings.retention_enabled:
        return 0, 0
    observed = now or datetime.now(UTC)
    async with factory() as session:
        async with asyncio.timeout(MAINTENANCE_TIMEOUT_SECONDS):
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(text("SELECT set_config('statement_timeout', '10000', true)"))
            unfinished = exists(
                select(SymbolizationJob.id).where(
                    SymbolizationJob.inbox_event_id == InboxEvent.id,
                    SymbolizationJob.status.in_(UNFINISHED_SYMBOL_STATUSES),
                )
            )
            terminal_time = func.coalesce(
                InboxEvent.finalized_at, InboxEvent.delivered_at, InboxEvent.next_attempt_at
            )
            statement = (
                select(InboxEvent)
                .where(
                    InboxEvent.raw_pruned_at.is_(None),
                    ~unfinished,
                    or_(
                        and_(
                            InboxEvent.status == "delivered",
                            terminal_time
                            <= observed - timedelta(days=settings.delivered_retention_days),
                        ),
                        and_(
                            InboxEvent.status == "dead_letter",
                            terminal_time
                            <= observed - timedelta(days=settings.dead_letter_retention_days),
                        ),
                    ),
                )
                .order_by(InboxEvent.id)
                .limit(settings.retention_batch_size)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list((await session.scalars(statement)).all())
            per_tenant: Counter[str] = Counter()
            for row in rows:
                # Final states cannot be claimed by workers. Preserve scope, occurrence columns,
                # payload hash and HMAC version so cleanup never re-admits a duplicate batch.
                scene = row.payload_json.get("scene")
                scene_state = (
                    EVIDENCE_EXPIRED if isinstance(scene, str) and scene else EVIDENCE_MISSING
                )
                row.payload_json = {
                    key: row.payload_json[key]
                    for key in ("module", "name")
                    if key in row.payload_json
                }
                retained = {
                    key: value
                    for key, value in row.normalized_json.get("indexed_attributes", {}).items()
                    if isinstance(value, (int, float, bool))
                }
                normalized = {
                    **row.normalized_json,
                    "registered_fields": retained,
                    "indexed_attributes": retained,
                    "raw_available": False,
                    # Store presence only; scene strings must expire with raw evidence.
                    "retention": {"scene_state": scene_state},
                }
                normalized["field_states"] = field_availability(normalized, raw_pruned=True)
                row.normalized_json = normalized
                row.native_identity_json = []
                row.raw_pruned_at = observed
                row.payload_size_bytes = 0
                row.last_error_message = None
                if row.symbolization_job is not None:
                    row.symbolization_job.result_json = None
                    row.symbolization_job.last_error_message = None
                per_tenant[row.tenant_id] += 1
            expired = (
                select(IngestRateWindow.key_id, IngestRateWindow.window_started_at)
                .where(
                    IngestRateWindow.window_started_at < observed - timedelta(days=1),
                )
                .order_by(IngestRateWindow.window_started_at)
                .limit(settings.retention_batch_size)
            )
            deleted = await session.execute(
                delete(IngestRateWindow).where(
                    tuple_(IngestRateWindow.key_id, IngestRateWindow.window_started_at).in_(expired)
                )
            )
            quota_deleted = int(deleted.rowcount)  # type: ignore[attr-defined]
            for tenant, count in per_tenant.items():
                session.add(
                    AuditLog(
                        tenant_id=tenant,
                        actor="worker:retention",
                        action="inbox.raw.prune",
                        object_type="retention_batch",
                        result="success",
                        details_json={
                            "count": count,
                            "delivered_days": settings.delivered_retention_days,
                            "dead_letter_days": settings.dead_letter_retention_days,
                        },
                    )
                )
            if quota_deleted:
                session.add(
                    AuditLog(
                        actor="worker:retention",
                        action="quota.expire",
                        object_type="rate_windows",
                        result="success",
                        details_json={"count": quota_deleted},
                    )
                )
            await session.commit()
    RETENTION_EVENTS.labels("raw_pruned").inc(len(rows))
    RETENTION_EVENTS.labels("quota_expired").inc(quota_deleted)
    RETENTION_LAST_SUCCESS.set(observed.timestamp())
    return len(rows), quota_deleted
