"""Durable inbox to OTLP export worker process."""

from __future__ import annotations

import asyncio
import socket
import uuid

import structlog

from androidapm_server.config import get_settings
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.session import get_session_factory
from androidapm_server.db.worker import claim_batch, mark_delivered, mark_failed
from androidapm_server.logging import configure_logging
from androidapm_server.metrics import EXPORT_EVENTS, EXPORT_REQUEST_SECONDS
from androidapm_server.otlp import build_logs_request
from androidapm_server.otlp.client import ExportResult, OtlpLogsClient

logger = structlog.get_logger(__name__)
MAPPING_ERRORS = (KeyError, TypeError, ValueError, OverflowError)


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

    payload, exportable, invalid = _encode_isolated_batch(events)
    changed = 0
    if invalid:
        async with factory() as session:
            changed = await mark_failed(
                session,
                owner,
                invalid,
                False,
                settings.worker_max_attempts,
                "otlp_mapping_failed",
                "The persisted event cannot be mapped to OTLP",
            )
            await session.commit()
            EXPORT_EVENTS.labels("dead_letter").inc(changed)
    if not exportable:
        return changed
    try:
        with EXPORT_REQUEST_SECONDS.time():
            result = await client.export(payload)
    except Exception:
        # Unexpected transport failures must consume the same bounded retry budget. Never
        # persist an exception message that could contain payloads or transport credentials.
        result = ExportResult(False, True, "export_internal_error", "OTLP transport failed")
    async with factory() as session:
        if result.success:
            completed = await mark_delivered(session, owner, [event.id for event in exportable])
            EXPORT_EVENTS.labels("delivered").inc(completed)
        else:
            completed = await mark_failed(
                session,
                owner,
                exportable,
                result.retryable,
                settings.worker_max_attempts,
                result.error_code or "unknown_export_error",
                result.error_message or "OTLP export failed",
                result.retry_after_seconds,
            )
            # An exhausted retryable failure is a dead letter, not another retry.
            for event in exportable:
                label = (
                    "retry"
                    if result.retryable and event.attempt_count < settings.worker_max_attempts
                    else "dead_letter"
                )
                if completed == len(exportable):
                    EXPORT_EVENTS.labels(label).inc()
        await session.commit()
    return changed + completed


def _encode_isolated_batch(
    events: list[InboxEvent],
) -> tuple[bytes, list[InboxEvent], list[InboxEvent]]:
    """Use one encoding on the healthy path; isolate deterministic historical bad rows."""
    try:
        return build_logs_request(events).SerializeToString(), events, []
    except MAPPING_ERRORS:
        valid, invalid = [], []
        for event in events:
            try:
                build_logs_request([event]).SerializeToString()
            except MAPPING_ERRORS:
                invalid.append(event)
            else:
                valid.append(event)
        payload = build_logs_request(valid).SerializeToString() if valid else b""
        return payload, valid, invalid


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
