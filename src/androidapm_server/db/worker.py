"""Owner/lease/expiry operations for concurrent durable export workers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.constants import (
    INBOX_STATUS_DEAD_LETTER,
    INBOX_STATUS_DELIVERED,
    INBOX_STATUS_PENDING,
    INBOX_STATUS_PROCESSING,
    MAX_EXPORT_ERROR_LENGTH,
)
from androidapm_server.db.models import InboxEvent

MAX_BACKOFF_SECONDS = 300


async def claim_batch(
    session: AsyncSession,
    owner: str,
    limit: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> list[InboxEvent]:
    """Claim due pending rows and expired leases for one worker owner."""
    observed = now or datetime.now(UTC)
    claimable = or_(
        and_(
            InboxEvent.status == INBOX_STATUS_PENDING,
            InboxEvent.next_attempt_at <= observed,
        ),
        and_(
            InboxEvent.status == INBOX_STATUS_PROCESSING,
            InboxEvent.lease_expires_at <= observed,
        ),
    )
    statement = select(InboxEvent).where(claimable).order_by(InboxEvent.id).limit(limit)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    rows = list((await session.scalars(statement)).all())
    lease_expiry = observed + timedelta(seconds=lease_seconds)
    for row in rows:
        row.status = INBOX_STATUS_PROCESSING
        row.lease_owner = owner
        row.lease_expires_at = lease_expiry
        row.attempt_count += 1
    await session.flush()
    return rows


async def mark_delivered(
    session: AsyncSession,
    owner: str,
    event_ids: Sequence[int],
    now: datetime | None = None,
) -> int:
    """Complete rows only when the caller still owns their active processing lease."""
    if not event_ids:
        return 0
    result = await session.execute(
        update(InboxEvent)
        .where(
            InboxEvent.id.in_(event_ids),
            InboxEvent.status == INBOX_STATUS_PROCESSING,
            InboxEvent.lease_owner == owner,
        )
        .values(
            status=INBOX_STATUS_DELIVERED,
            delivered_at=now or datetime.now(UTC),
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            last_error_message=None,
        )
    )
    return result.rowcount  # type: ignore[attr-defined,no-any-return]


async def mark_failed(
    session: AsyncSession,
    owner: str,
    events: Sequence[InboxEvent],
    retryable: bool,
    max_attempts: int,
    error_code: str,
    error_message: str,
    retry_after_seconds: int | None = None,
    now: datetime | None = None,
) -> int:
    """Retry or dead-letter owned rows without allowing stale owners to mutate them."""
    observed = now or datetime.now(UTC)
    changed = 0
    for event in events:
        should_retry = retryable and event.attempt_count < max_attempts
        delay = retry_after_seconds or min(2 ** min(event.attempt_count, 8), MAX_BACKOFF_SECONDS)
        result = await session.execute(
            update(InboxEvent)
            .where(
                InboxEvent.id == event.id,
                InboxEvent.status == INBOX_STATUS_PROCESSING,
                InboxEvent.lease_owner == owner,
            )
            .values(
                status=INBOX_STATUS_PENDING if should_retry else INBOX_STATUS_DEAD_LETTER,
                next_attempt_at=observed + timedelta(seconds=delay),
                lease_owner=None,
                lease_expires_at=None,
                last_error_code=error_code[:128],
                last_error_message=error_message[:MAX_EXPORT_ERROR_LENGTH],
            )
        )
        changed += result.rowcount  # type: ignore[attr-defined]
    return changed
