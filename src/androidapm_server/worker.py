"""Durable inbox to OTLP export worker process."""

from __future__ import annotations

import asyncio
import socket
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.config import get_settings
from androidapm_server.constants import INBOX_STATUS_PROCESSING
from androidapm_server.db.leases import lease_claim_time, lease_write_time, new_lease_owner
from androidapm_server.db.maintenance import maintain_once
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.session import get_session_factory
from androidapm_server.db.worker import claim_batch, mark_delivered, mark_failed
from androidapm_server.logging import configure_logging
from androidapm_server.metrics import EXPORT_EVENTS, EXPORT_REQUEST_SECONDS, WORKER_CYCLE_TIMESTAMP
from androidapm_server.observability import worker_observability
from androidapm_server.otlp import build_logs_request
from androidapm_server.otlp.client import (
    ExportResult,
    OtlpLogsClient,
    OtlpMetricsClient,
    OtlpTracesClient,
)
from androidapm_server.otlp.metrics import build_metrics_request
from androidapm_server.otlp.traces import build_traces_request

logger = structlog.get_logger(__name__)
MAPPING_ERRORS = (KeyError, TypeError, ValueError, OverflowError)


async def export_once(
    client: OtlpLogsClient,
    owner: str,
    metrics_client: OtlpMetricsClient | None = None,
    traces_client: OtlpTracesClient | None = None,
) -> int:
    """Persist signal progress under the lease; finalize only after every required signal."""
    settings = get_settings()
    if not settings.export_enabled:
        return 0
    owner = new_lease_owner(owner)
    factory = get_session_factory()
    async with factory() as session:
        events = await claim_batch(
            session,
            owner,
            settings.worker_batch_size,
            settings.worker_lease_seconds,
        )
        for event in events:
            if not event.export_signal_states_json:
                required = {"logs": "pending"}
                if settings.otlp_metrics_enabled:
                    required["metrics"] = "pending"
                if settings.otlp_traces_enabled:
                    required["traces"] = "pending"
                # Freeze the required signal set in the same durable claim transaction.
                # Turning off a switch cannot discard a previously pending export.
                event.export_signal_states_json = required
        await session.commit()
    if not events:
        return 0

    owned_clients: list[OtlpLogsClient] = []
    if metrics_client is None and any("metrics" in e.export_signal_states_json for e in events):
        metrics_client = OtlpMetricsClient(
            settings.otlp_metrics_endpoint,
            settings.otlp_headers_json,
            settings.otlp_timeout_seconds,
        )
        owned_clients.append(metrics_client)
    if traces_client is None and any("traces" in e.export_signal_states_json for e in events):
        traces_client = OtlpTracesClient(
            settings.otlp_traces_endpoint, settings.otlp_headers_json, settings.otlp_timeout_seconds
        )
        owned_clients.append(traces_client)
    errors: dict[int, ExportResult] = {}
    try:
        for signal, transport, builder in (
            ("logs", client, build_logs_request),
            ("metrics", metrics_client, build_metrics_request),
            ("traces", traces_client, build_traces_request),
        ):
            pending = [
                event
                for event in events
                if event.export_signal_states_json.get(signal) == "pending"
            ]
            if not pending or transport is None:
                continue
            # Each signal gets a fresh bounded lease. Never initiate a subsequent signal
            # after this claim was stolen or expired during an earlier export.
            live = []
            async with factory() as session:
                observed = await lease_claim_time(session)
                for event in pending:
                    renewal = await session.execute(
                        update(InboxEvent)
                        .where(
                            InboxEvent.id == event.id,
                            InboxEvent.status == INBOX_STATUS_PROCESSING,
                            InboxEvent.lease_owner == owner,
                            InboxEvent.lease_expires_at > lease_write_time(session),
                        )
                        .values(
                            lease_expires_at=observed
                            + timedelta(seconds=settings.worker_lease_seconds)
                        )
                    )
                    if renewal.rowcount:  # type: ignore[attr-defined]
                        live.append(event)
                await session.commit()
            pending = live
            if not pending:
                continue
            payload, exportable, invalid, empty = _encode_signal(pending, signal, builder)
            async with factory() as session:
                for event in empty:
                    await _record_signal(session, event, owner, signal, "not_applicable")
                for event in invalid:
                    result = ExportResult(
                        False,
                        False,
                        "otlp_mapping_failed",
                        "The persisted event cannot be mapped to OTLP",
                    )
                    errors[event.id] = result
                    await _record_signal(session, event, owner, signal, "failed", result)
                await session.commit()
            if not exportable:
                continue
            try:
                with EXPORT_REQUEST_SECONDS.time():
                    async with asyncio.timeout(settings.otlp_timeout_seconds):
                        result = await transport.export(payload)
            except Exception:
                result = ExportResult(False, True, "export_internal_error", "OTLP transport failed")
            async with factory() as session:
                for event in exportable:
                    if result.success:
                        await _record_signal(session, event, owner, signal, "delivered")
                    elif not result.retryable:
                        errors[event.id] = result
                        await _record_signal(session, event, owner, signal, "failed", result)
                    else:
                        errors[event.id] = result
                await session.commit()
    finally:
        for transport in owned_clients:
            await transport.close()
    outcomes: dict[str, int] = {}
    async with factory() as session:
        completed = 0
        for event in events:
            states = event.export_signal_states_json
            retryable = "pending" in states.values()
            failed = "failed" in states.values()
            if not retryable and not failed:
                count = await mark_delivered(session, owner, [event.id])
                label = "delivered"
            else:
                error = errors.get(
                    event.id,
                    ExportResult(
                        False,
                        retryable,
                        event.last_error_code or "otlp_signal_failed",
                        event.last_error_message or "A required OTLP signal was not delivered",
                    ),
                )
                count = await mark_failed(
                    session,
                    owner,
                    [event],
                    retryable,
                    settings.worker_max_attempts,
                    error.error_code or "unknown_export_error",
                    error.error_message or "OTLP export failed",
                    error.retry_after_seconds,
                )
                label = (
                    "retry"
                    if retryable and event.attempt_count < settings.worker_max_attempts
                    else "dead_letter"
                )
            outcomes[label] = outcomes.get(label, 0) + count
            completed += count
        await session.commit()
    # Publish outcomes only after commit, including partial batches where leases were lost.
    for label, count in outcomes.items():
        EXPORT_EVENTS.labels(label).inc(count)
    return completed


async def _record_signal(
    session: AsyncSession,
    event: InboxEvent,
    owner: str,
    signal: str,
    status: str,
    error: ExportResult | None = None,
) -> None:
    """Fence progress writes with the same owner and database-clock expiry as finalization."""
    states = {**event.export_signal_states_json, signal: status}
    values: dict[str, Any] = {"export_signal_states_json": states}
    if error is not None:
        values.update(last_error_code=error.error_code, last_error_message=error.error_message)
    result = await session.execute(
        update(InboxEvent)
        .where(
            InboxEvent.id == event.id,
            InboxEvent.status == INBOX_STATUS_PROCESSING,
            InboxEvent.lease_owner == owner,
            InboxEvent.lease_expires_at > lease_write_time(session),
        )
        .values(**values)
    )
    if result.rowcount:  # type: ignore[attr-defined]
        event.export_signal_states_json = states
        if error is not None:
            event.last_error_code = error.error_code
            event.last_error_message = error.error_message


def _encode_signal(
    events: list[InboxEvent],
    signal: str,
    builder: Callable[[list[InboxEvent]], Any],
) -> tuple[bytes, list[InboxEvent], list[InboxEvent], list[InboxEvent]]:
    """Isolate deterministic bad rows and mark signals with no truthful projection empty."""
    if signal == "logs":
        payload, log_valid, log_invalid = _encode_isolated_batch(events)
        return payload, log_valid, log_invalid, []
    valid: list[InboxEvent] = []
    invalid: list[InboxEvent] = []
    empty: list[InboxEvent] = []
    group_field = "resource_metrics" if signal == "metrics" else "resource_spans"
    for event in events:
        try:
            request = builder([event])
            request.SerializeToString()
        except MAPPING_ERRORS:
            invalid.append(event)
        else:
            (valid if getattr(request, group_field) else empty).append(event)
    return builder(valid).SerializeToString() if valid else b"", valid, invalid, empty


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
    metrics_client = OtlpMetricsClient(
        settings.otlp_metrics_endpoint, settings.otlp_headers_json, settings.otlp_timeout_seconds
    )
    traces_client = OtlpTracesClient(
        settings.otlp_traces_endpoint, settings.otlp_headers_json, settings.otlp_timeout_seconds
    )
    logger.info("worker_started", owner=owner)
    next_maintenance = 0.0
    try:
        async with worker_observability("export", settings):
            while True:
                try:
                    if time.monotonic() >= next_maintenance:
                        next_maintenance = time.monotonic() + settings.retention_interval_seconds
                        try:
                            pruned, expired = await maintain_once(get_session_factory(), settings)
                            if pruned or expired:
                                logger.info(
                                    "retention_completed", raw_pruned=pruned, quota_expired=expired
                                )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            logger.warning("retention_cycle_failed")
                    changed = await export_once(client, owner, metrics_client, traces_client)
                    WORKER_CYCLE_TIMESTAMP.labels("export").set(time.time())
                    if changed == 0:
                        await asyncio.sleep(settings.worker_poll_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("worker_cycle_failed", owner=owner)
                    await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await client.close()
        await metrics_client.close()
        await traces_client.close()
        logger.info("worker_stopped", owner=owner)


def run() -> None:
    """Run the worker until the process receives cancellation."""
    asyncio.run(worker_loop())


if __name__ == "__main__":
    run()
