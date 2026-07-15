"""Database-backed distributed ingest quota enforcement."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.auth import Principal
from androidapm_server.db.models import IngestRateWindow
from androidapm_server.errors import ApiError

WINDOW_SECONDS = 60


async def consume_quota(
    session: AsyncSession,
    principal: Principal,
    event_count: int,
    now: datetime | None = None,
) -> None:
    """Atomically consume one request and its events in a UTC minute window."""
    observed = now or datetime.now(UTC)
    if principal.requests_per_minute < 1 or event_count > principal.events_per_minute:
        raise ApiError(
            429,
            "quota_exceeded",
            "The ingest quota has been exceeded",
            True,
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )
    window = observed.replace(second=0, microsecond=0)
    values = {
        "key_id": principal.key_id,
        "window_started_at": window,
        "request_count": 1,
        "event_count": event_count,
    }
    dialect = session.bind.dialect.name if session.bind is not None else ""
    limits = and_(
        IngestRateWindow.request_count + 1 <= principal.requests_per_minute,
        IngestRateWindow.event_count + event_count <= principal.events_per_minute,
    )
    if dialect == "postgresql":
        statement = (
            postgresql_insert(IngestRateWindow)
            .values(values)
            .on_conflict_do_update(
                index_elements=["key_id", "window_started_at"],
                set_={
                    "request_count": IngestRateWindow.request_count + 1,
                    "event_count": IngestRateWindow.event_count + event_count,
                },
                where=limits,
            )
            .returning(IngestRateWindow.key_id)
        )
    elif dialect == "sqlite":
        statement = (
            sqlite_insert(IngestRateWindow)
            .values(values)
            .on_conflict_do_update(
                index_elements=["key_id", "window_started_at"],
                set_={
                    "request_count": IngestRateWindow.request_count + 1,
                    "event_count": IngestRateWindow.event_count + event_count,
                },
                where=limits,
            )
            .returning(IngestRateWindow.key_id)
        )
    else:
        raise RuntimeError(f"Unsupported database dialect: {dialect}")

    consumed = (await session.execute(statement)).scalar_one_or_none()
    if consumed is None:
        retry_after = max(1, WINDOW_SECONDS - observed.second)
        raise ApiError(
            429,
            "quota_exceeded",
            "The ingest quota has been exceeded",
            True,
            headers={"Retry-After": str(retry_after)},
        )
