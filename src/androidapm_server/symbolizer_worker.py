"""Durable crash symbolization worker using owner/lease/expiry jobs."""

from __future__ import annotations

import asyncio
import socket
import time
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from androidapm_server.artifacts import LocalArtifactStore
from androidapm_server.config import Settings, get_settings
from androidapm_server.constants import SYMBOL_JOB_NATIVE, SYMBOLIZER_FINALIZE_MARGIN_SECONDS
from androidapm_server.db.leases import new_lease_owner
from androidapm_server.db.models import SymbolizationJob
from androidapm_server.db.session import get_session_factory
from androidapm_server.db.symbolization import (
    claim_symbolization_batch,
    mark_symbolization_failed,
    mark_symbolized,
    mark_symbols_missing,
    resolve_artifact,
    resolve_native_artifacts,
    start_symbolization_attempt,
)
from androidapm_server.logging import configure_logging
from androidapm_server.metrics import (
    SYMBOLIZATION_JOBS,
    SYMBOLIZATION_SECONDS,
    WORKER_CYCLE_TIMESTAMP,
)
from androidapm_server.observability import worker_observability
from androidapm_server.symbolization import NativeArtifacts, SymbolizationFailure, symbolize_job

logger = structlog.get_logger(__name__)


async def symbolize_once(owner: str) -> int:
    """Process a bounded cycle, claiming each job only when it can start immediately."""
    settings = get_settings()
    if not settings.symbolization_enabled:
        return 0
    factory = get_session_factory()
    store = LocalArtifactStore(settings.artifact_storage_path)
    changed = 0
    for _ in range(settings.symbolizer_batch_size):
        claim_owner = new_lease_owner(owner)
        deadline = (
            asyncio.get_running_loop().time()
            + settings.symbolizer_lease_seconds
            - SYMBOLIZER_FINALIZE_MARGIN_SECONDS
        )
        async with factory() as session:
            jobs = await claim_symbolization_batch(
                session, claim_owner, 1, settings.symbolizer_lease_seconds
            )
            await session.commit()
        if not jobs:
            break
        job = jobs[0]
        try:
            # Bound resolution, attempt accounting, tool execution and completion together.
            # Leave a margin to record a retry; an expired claim is always left for reclaim.
            async with asyncio.timeout_at(deadline):
                label = await _process_claim(factory, settings, store, claim_owner, job)
        except TimeoutError:
            label = await _record_failure(
                factory,
                settings,
                claim_owner,
                job,
                SymbolizationFailure(
                    "symbolizer_lease_budget_exhausted",
                    "The symbolization job exceeded its lease execution budget",
                    True,
                ),
            )
        except SymbolizationFailure as error:
            label = await _record_failure(factory, settings, claim_owner, job, error)
        except Exception:
            logger.exception("symbolizer_job_failed", job_id=job.id, job_type=job.job_type)
            label = await _record_failure(
                factory,
                settings,
                claim_owner,
                job,
                SymbolizationFailure(
                    "symbolizer_internal_error",
                    "The symbolization job failed internally",
                    True,
                ),
            )
        if label is not None:
            # Count only committed transitions; stale/expired results are never successes.
            changed += 1
            SYMBOLIZATION_JOBS.labels(label, job.job_type).inc()
        else:
            logger.warning("symbolizer_lease_lost", job_id=job.id, job_type=job.job_type)
    return changed


async def _process_claim(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    store: LocalArtifactStore,
    owner: str,
    job: SymbolizationJob,
) -> str | None:
    """Resolve one claimed job and return its label only after a committed transition."""
    native_artifacts: NativeArtifacts | None = None
    async with factory() as session:
        if job.job_type == SYMBOL_JOB_NATIVE:
            modules = await resolve_native_artifacts(session, job)
            artifact = modules[0] if modules else None
            native_artifacts = {
                (module.abi, module.build_id): (module, store.path_for(module.storage_key))
                for module in modules
            }
        else:
            artifact = await resolve_artifact(session, job)
    if artifact is None:
        async with factory() as session:
            changed = await mark_symbols_missing(
                session, owner, job.id, "No exact symbol artifact is registered"
            )
            await session.commit()
        return "symbols_missing" if changed else None
    if job.attempt_count >= settings.symbolizer_max_attempts:
        # A process crash after the final tool attempt must not grant an extra attempt.
        raise SymbolizationFailure(
            "symbolizer_attempts_exhausted",
            "The symbolization tool retry budget is exhausted",
            False,
        )
    async with factory() as session:
        started = await start_symbolization_attempt(
            session, owner, job, settings.symbolizer_max_attempts
        )
        await session.commit()
    if not started:
        return None
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
            native_artifacts,
        )
    async with factory() as session:
        changed = await mark_symbolized(
            session,
            owner,
            job,
            artifact.id,
            result.result_json,
            result.fingerprint_sha256,
            result.tool_name,
            result.tool_version,
            status=result.status,
        )
        await session.commit()
    return result.status if changed else None


async def _record_failure(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    owner: str,
    job: SymbolizationJob,
    error: SymbolizationFailure,
) -> str | None:
    """Record a bounded failure without changing state or metrics after losing the lease."""
    async with factory() as session:
        changed = await mark_symbolization_failed(
            session,
            owner,
            job,
            error.retryable,
            settings.symbolizer_max_attempts,
            error.code,
            error.message,
        )
        await session.commit()
    if not changed:
        return None
    return (
        "retry"
        if error.retryable and job.attempt_count < settings.symbolizer_max_attempts
        else "failed"
    )


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
