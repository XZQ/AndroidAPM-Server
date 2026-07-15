"""Durable inbox to OTLP export worker process."""

from __future__ import annotations

import asyncio
import socket
import uuid

import structlog

from androidapm_server.config import get_settings
from androidapm_server.db.session import get_session_factory
from androidapm_server.db.worker import claim_batch, mark_delivered, mark_failed
from androidapm_server.logging import configure_logging
from androidapm_server.metrics import EXPORT_EVENTS, EXPORT_REQUEST_SECONDS
from androidapm_server.otlp import build_logs_request
from androidapm_server.otlp.client import OtlpLogsClient

logger = structlog.get_logger(__name__)


async def export_once(client: OtlpLogsClient, owner: str) -> int:
    """Claim, export, and finalize at most one configured worker batch."""
    settings = get_settings()
    if not settings.export_enabled:
        return 0
    factory = get_session_factory()
    async with factory() as session:
        events = await claim_batch(
            session,
            owner,
            settings.worker_batch_size,
            settings.worker_lease_seconds,
        )
        await session.commit()
    if not events:
        return 0

    request = build_logs_request(events)
    with EXPORT_REQUEST_SECONDS.time():
        result = await client.export(request.SerializeToString())
    async with factory() as session:
        if result.success:
            changed = await mark_delivered(session, owner, [event.id for event in events])
            EXPORT_EVENTS.labels("delivered").inc(changed)
        else:
            changed = await mark_failed(
                session,
                owner,
                events,
                result.retryable,
                settings.worker_max_attempts,
                result.error_code or "unknown_export_error",
                result.error_message or "OTLP export failed",
                result.retry_after_seconds,
            )
            EXPORT_EVENTS.labels("retry" if result.retryable else "dead_letter").inc(changed)
        await session.commit()
    return changed


async def worker_loop() -> None:
    """Poll until cancelled, isolating each database/export cycle."""
    settings = get_settings()
    configure_logging(settings.log_level)
    owner = f"{socket.gethostname()}-{uuid.uuid4().hex}"
    client = OtlpLogsClient(
        settings.otlp_logs_endpoint,
        settings.otlp_headers_json,
        settings.otlp_timeout_seconds,
    )
    logger.info("worker_started", owner=owner)
    try:
        while True:
            try:
                changed = await export_once(client, owner)
                if changed == 0:
                    await asyncio.sleep(settings.worker_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("worker_cycle_failed", owner=owner)
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await client.close()
        logger.info("worker_stopped", owner=owner)


def run() -> None:
    """Run the worker until the process receives cancellation."""
    asyncio.run(worker_loop())


if __name__ == "__main__":
    run()
