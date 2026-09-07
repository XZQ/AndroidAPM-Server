"""Durable crash symbolization worker using owner/lease/expiry jobs."""

from __future__ import annotations

import asyncio
import socket
import time
import uuid

import structlog

from androidapm_server.artifacts import LocalArtifactStore
from androidapm_server.config import get_settings
from androidapm_server.db.session import get_session_factory
from androidapm_server.db.symbolization import (
    claim_symbolization_batch,
    mark_symbolization_failed,
    mark_symbolized,
    mark_symbols_missing,
    resolve_artifact,
)
from androidapm_server.logging import configure_logging
from androidapm_server.metrics import (
    SYMBOLIZATION_JOBS,
    SYMBOLIZATION_SECONDS,
    WORKER_CYCLE_TIMESTAMP,
)
from androidapm_server.observability import worker_observability
from androidapm_server.symbolization import SymbolizationFailure, symbolize_job

logger = structlog.get_logger(__name__)


async def symbolize_once(owner: str) -> int:
    """Claim and process at most one configured durable symbolization batch."""
    settings = get_settings()
    if not settings.symbolization_enabled:
        return 0
    factory = get_session_factory()
    async with factory() as session:
        jobs = await claim_symbolization_batch(
            session,
            owner,
            settings.symbolizer_batch_size,
            settings.symbolizer_lease_seconds,
        )
        await session.commit()
    if not jobs:
        return 0

    store = LocalArtifactStore(settings.artifact_storage_path)
    changed = 0
    for job in jobs:
        async with factory() as session:
            artifact = await resolve_artifact(session, job)
        if artifact is None:
            async with factory() as session:
                if await mark_symbols_missing(
                    session, owner, job.id, "No exact symbol artifact is registered"
                ):
                    changed += 1
                    SYMBOLIZATION_JOBS.labels("symbols_missing", job.job_type).inc()
                await session.commit()
            continue
        try:
            with SYMBOLIZATION_SECONDS.labels(job.job_type).time():
                result = await symbolize_job(
                    job,
                    artifact,
                    store.path_for(artifact.storage_key),
                    settings.retrace_command_json,
                    settings.retrace_tool_version,
                    settings.llvm_symbolizer_command_json,
                    settings.llvm_symbolizer_tool_version,
                    settings.symbolizer_timeout_seconds,
                )
        except SymbolizationFailure as error:
            async with factory() as session:
                should_retry = (
                    error.retryable and job.attempt_count < settings.symbolizer_max_attempts
                )
                if await mark_symbolization_failed(
                    session,
                    owner,
                    job,
                    error.retryable,
                    settings.symbolizer_max_attempts,
                    error.code,
                    error.message,
                ):
                    changed += 1
                    result_label = "retry" if should_retry else "failed"
                    SYMBOLIZATION_JOBS.labels(result_label, job.job_type).inc()
                await session.commit()
            continue
        except Exception as error:
            logger.exception("symbolizer_job_failed", job_id=job.id, job_type=job.job_type)
            async with factory() as session:
                if await mark_symbolization_failed(
                    session,
                    owner,
                    job,
                    True,
                    settings.symbolizer_max_attempts,
                    "symbolizer_internal_error",
                    type(error).__name__,
                ):
                    changed += 1
                    SYMBOLIZATION_JOBS.labels("retry", job.job_type).inc()
                await session.commit()
            continue
        async with factory() as session:
            if await mark_symbolized(
                session,
                owner,
                job,
                artifact.id,
                result.result_json,
                result.fingerprint_sha256,
                result.tool_name,
                result.tool_version,
            ):
                changed += 1
                SYMBOLIZATION_JOBS.labels("symbolized", job.job_type).inc()
            await session.commit()
    return changed


async def symbolizer_loop() -> None:
    """Poll until cancelled while isolating each durable worker cycle."""
    settings = get_settings()
    configure_logging(settings.log_level)
    owner = f"{socket.gethostname()}-{uuid.uuid4().hex}"
    logger.info("symbolizer_started", owner=owner, enabled=settings.symbolization_enabled)
    try:
        async with worker_observability("symbolizer", settings):
            while True:
                try:
                    changed = await symbolize_once(owner)
                    WORKER_CYCLE_TIMESTAMP.labels("symbolizer").set(time.time())
                    if changed == 0:
                        await asyncio.sleep(settings.symbolizer_poll_seconds)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("symbolizer_cycle_failed", owner=owner)
                    await asyncio.sleep(settings.symbolizer_poll_seconds)
    finally:
        logger.info("symbolizer_stopped", owner=owner)


def run() -> None:
    """Run the symbolization worker until process cancellation."""
    asyncio.run(symbolizer_loop())


if __name__ == "__main__":
    run()
