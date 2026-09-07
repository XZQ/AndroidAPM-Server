"""Live database backlog snapshots and private per-worker Prometheus listeners."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from prometheus_client import start_http_server
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.config import Settings
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.session import get_session_factory
from androidapm_server.metrics import (
    INBOX_OLDEST_SECONDS,
    INBOX_PENDING,
    INBOX_ROWS,
    INBOX_SNAPSHOT_AVAILABLE,
    INBOX_SNAPSHOT_TIMESTAMP,
    PROCESS_ROLE,
)

INBOX_STATES = ("pending", "processing", "awaiting_symbols", "delivered", "dead_letter")
SNAPSHOT_TIMEOUT_SECONDS = 3


async def refresh_inbox_metrics(session: AsyncSession, now: datetime | None = None) -> bool:
    """Observe exact backlog state; failed observations must not look like an empty inbox."""
    observed = now or datetime.now(UTC)
    eligible = or_(
        and_(InboxEvent.status == "pending", InboxEvent.next_attempt_at <= observed),
        and_(InboxEvent.status == "processing", InboxEvent.lease_expires_at <= observed),
    )
    try:
        async with asyncio.timeout(SNAPSHOT_TIMEOUT_SECONDS):
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(text("SELECT set_config('statement_timeout', '3000', true)"))
            result = await session.execute(
                select(
                    InboxEvent.status,
                    func.count(),
                    func.sum(case((eligible, 1), else_=0)),
                    func.min(case((eligible, InboxEvent.received_at), else_=None)),
                ).group_by(InboxEvent.status)
            )
            rows = result.all()
    except Exception:
        INBOX_SNAPSHOT_AVAILABLE.set(0)
        INBOX_PENDING.set(float("nan"))
        INBOX_OLDEST_SECONDS.set(float("nan"))
        for status in INBOX_STATES:
            INBOX_ROWS.labels(status).set(float("nan"))
        return False
    counts = {row[0]: row[1] for row in rows}
    oldest = min((row[3] for row in rows if row[3] is not None), default=None)
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)
    for status in INBOX_STATES:
        INBOX_ROWS.labels(status).set(counts.get(status, 0))
    INBOX_PENDING.set(sum(row[2] for row in rows))
    INBOX_OLDEST_SECONDS.set(max(0, (observed - oldest).total_seconds()) if oldest else 0)
    INBOX_SNAPSHOT_TIMESTAMP.set(observed.timestamp())
    INBOX_SNAPSHOT_AVAILABLE.set(1)
    return True


async def _snapshot_loop(interval: float) -> None:
    """Refresh independently of an exporter waiting on the downstream service."""
    factory = get_session_factory()
    while True:
        async with factory() as session:
            await refresh_inbox_metrics(session)
        await asyncio.sleep(interval)


@asynccontextmanager
async def worker_observability(role: str, settings: Settings) -> AsyncIterator[None]:
    """Serve process-local counters on a private listener for the worker lifetime."""
    port = settings.worker_metrics_port if role == "export" else settings.symbolizer_metrics_port
    server = start_http_server(port, addr=settings.metrics_bind)[0] if port else None
    PROCESS_ROLE.labels(role).set(1)
    monitor = asyncio.create_task(_snapshot_loop(settings.metrics_refresh_seconds))
    try:
        yield
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor
        if server is not None:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
