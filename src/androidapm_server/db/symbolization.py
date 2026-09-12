"""Durable symbolization job enqueue, lease, resolution, and completion operations."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from androidapm_server.artifacts import ArtifactIdentity
from androidapm_server.constants import (
    ARTIFACT_STATUS_ACTIVE,
    ARTIFACT_TYPE_JAVA_MAPPING,
    ARTIFACT_TYPE_NATIVE_ELF,
    INBOX_STATUS_AWAITING_SYMBOLS,
    INBOX_STATUS_PENDING,
    MAX_EXPORT_ERROR_LENGTH,
    SYMBOL_JOB_JAVA,
    SYMBOL_JOB_NATIVE,
    SYMBOL_STATUS_DISABLED,
    SYMBOL_STATUS_FAILED,
    SYMBOL_STATUS_METADATA_MISSING,
    SYMBOL_STATUS_PARTIALLY_SYMBOLIZED,
    SYMBOL_STATUS_PENDING,
    SYMBOL_STATUS_PROCESSING,
    SYMBOL_STATUS_SYMBOLIZED,
    SYMBOL_STATUS_SYMBOLS_MISSING,
)
from androidapm_server.db.leases import lease_claim_time, lease_write_time
from androidapm_server.db.models import InboxEvent, SymbolArtifact, SymbolizationJob, utc_now
from androidapm_server.domain import ApmEvent, IngestMetadata
from androidapm_server.symbolization import native_frames_for_job

JAVA_CRASH_NAMES = frozenset({"java_crash"})
NATIVE_CRASH_NAMES = frozenset({"native_crash", "tombstone_crash"})
BUILD_ID_PATTERN = re.compile(r"^[0-9a-f]{8,128}$")
SUPPORTED_ABIS = frozenset({"arm64-v8a", "armeabi-v7a", "x86_64", "x86"})
MAX_SYMBOL_BACKOFF_SECONDS = 300
SYMBOL_ARTIFACT_RECHECK_SECONDS = 60


async def enqueue_symbolization_jobs(
    session: AsyncSession,
    metadata: IngestMetadata,
    events: Sequence[ApmEvent],
    *,
    enabled: bool = True,
) -> int:
    """Create one job per crash identity inside the ingest durability transaction."""
    relevant = {event.event_id: event for event in events if _job_type(event) is not None}
    if not relevant:
        return 0
    inbox_rows = list(
        (
            await session.scalars(
                select(InboxEvent).where(
                    InboxEvent.tenant_id == metadata.tenant_id,
                    InboxEvent.event_id.in_(relevant),
                )
            )
        ).all()
    )
    values: list[dict[str, Any]] = []
    waiting_inbox_ids: list[int] = []
    for inbox in inbox_rows:
        event = relevant[inbox.event_id]
        job_type = _job_type(event)
        if job_type is None:
            continue
        stack = _stack_value(inbox.payload_json, job_type)
        if not enabled:
            status = SYMBOL_STATUS_DISABLED
        elif _has_symbol_identity(inbox, job_type, stack):
            status = SYMBOL_STATUS_PENDING
        else:
            status = SYMBOL_STATUS_METADATA_MISSING
        values.append(
            {
                "inbox_event_id": inbox.id,
                "tenant_id": metadata.tenant_id,
                "event_id": event.event_id,
                "job_type": job_type,
                "status": status,
                "input_sha256": hashlib.sha256(stack.encode()).hexdigest(),
                "attempt_count": 0,
                "next_attempt_at": utc_now(),
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
        )
        if status == SYMBOL_STATUS_PENDING:
            waiting_inbox_ids.append(inbox.id)
    if not values:
        return 0
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        statement = (
            postgresql_insert(SymbolizationJob)
            .values(values)
            .on_conflict_do_nothing(index_elements=["inbox_event_id"])
            .returning(SymbolizationJob.inbox_event_id)
        )
    elif dialect == "sqlite":
        statement = (
            sqlite_insert(SymbolizationJob)
            .values(values)
            .on_conflict_do_nothing(index_elements=["inbox_event_id"])
            .returning(SymbolizationJob.inbox_event_id)
        )
    else:
        raise RuntimeError(f"Unsupported database dialect: {dialect}")
    inserted_waiting = set((await session.execute(statement)).scalars().all()) & set(
        waiting_inbox_ids
    )
    if inserted_waiting:
        await session.execute(
            update(InboxEvent)
            .where(
                InboxEvent.id.in_(inserted_waiting),
                InboxEvent.status == INBOX_STATUS_PENDING,
            )
            .values(status=INBOX_STATUS_AWAITING_SYMBOLS)
        )
    return len(inserted_waiting)


async def claim_symbolization_batch(
    session: AsyncSession,
    owner: str,
    limit: int,
    lease_seconds: int,
    now: datetime | None = None,
) -> list[SymbolizationJob]:
    """Claim due jobs and expired leases without allowing two active owners."""
    observed = await lease_claim_time(session, now)
    claimable = or_(
        and_(
            SymbolizationJob.status == SYMBOL_STATUS_PENDING,
            SymbolizationJob.next_attempt_at <= observed,
        ),
        and_(
            SymbolizationJob.status == SYMBOL_STATUS_PROCESSING,
            SymbolizationJob.lease_expires_at <= observed,
        ),
        and_(
            SymbolizationJob.status == SYMBOL_STATUS_SYMBOLS_MISSING,
            SymbolizationJob.next_attempt_at <= observed,
        ),
    )
    statement = (
        select(SymbolizationJob)
        .options(selectinload(SymbolizationJob.inbox_event))
        .where(claimable)
        .order_by(SymbolizationJob.id)
        .limit(limit)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    jobs = list((await session.scalars(statement)).all())
    lease_expiry = observed + timedelta(seconds=lease_seconds)
    for job in jobs:
        job.status = SYMBOL_STATUS_PROCESSING
        job.lease_owner = owner
        job.lease_expires_at = lease_expiry
    await session.flush()
    return jobs


async def start_symbolization_attempt(
    session: AsyncSession,
    owner: str,
    job: SymbolizationJob,
    max_attempts: int,
    now: datetime | None = None,
) -> bool:
    """Spend retry budget only when an active claim has an artifact and will run a tool."""
    observed = lease_write_time(session, now)
    count = await session.scalar(
        update(SymbolizationJob)
        .where(
            SymbolizationJob.id == job.id,
            SymbolizationJob.status == SYMBOL_STATUS_PROCESSING,
            SymbolizationJob.lease_owner == owner,
            SymbolizationJob.lease_expires_at > observed,
            SymbolizationJob.attempt_count == job.attempt_count,
            SymbolizationJob.attempt_count < max_attempts,
        )
        .values(attempt_count=SymbolizationJob.attempt_count + 1, updated_at=observed)
        .returning(SymbolizationJob.attempt_count)
        .execution_options(synchronize_session=False)
    )
    if count is None:
        return False
    job.attempt_count = count
    return True


async def resolve_native_artifacts(
    session: AsyncSession, job: SymbolizationJob
) -> list[SymbolArtifact]:
    """Resolve the complete module set in one scoped query before spending a tool attempt."""
    frames = native_frames_for_job(job)
    identity = artifact_identity_for_job(job)
    if identity is None:
        return []
    required = {(frame.abi, frame.module_build_id.lower()) for frame in frames}
    artifacts = list(
        (
            await session.scalars(
                select(SymbolArtifact).where(
                    SymbolArtifact.tenant_id == identity.tenant_id,
                    SymbolArtifact.artifact_type == ARTIFACT_TYPE_NATIVE_ELF,
                    SymbolArtifact.status == ARTIFACT_STATUS_ACTIVE,
                    SymbolArtifact.app_id == identity.app_id,
                    SymbolArtifact.version_code == identity.version_code,
                    SymbolArtifact.app_build == identity.app_build,
                    SymbolArtifact.variant == identity.variant,
                    tuple_(SymbolArtifact.abi, SymbolArtifact.build_id).in_(required),
                )
            )
        ).all()
    )
    return (
        artifacts
        if {(artifact.abi, artifact.build_id) for artifact in artifacts} == required
        else []
    )


async def resolve_artifact(session: AsyncSession, job: SymbolizationJob) -> SymbolArtifact | None:
    """Resolve only the exact build identity carried by the crash event and envelope."""
    identity = artifact_identity_for_job(job)
    if identity is None:
        return None
    result = await session.scalars(
        select(SymbolArtifact).where(
            SymbolArtifact.tenant_id == identity.tenant_id,
            SymbolArtifact.artifact_type == identity.artifact_type,
            SymbolArtifact.app_id == identity.app_id,
            SymbolArtifact.version_code == identity.version_code,
            SymbolArtifact.app_build == identity.app_build,
            SymbolArtifact.variant == identity.variant,
            SymbolArtifact.abi == identity.abi,
            SymbolArtifact.build_id == identity.build_id,
        )
    )
    return result.one_or_none()


def artifact_identity_for_job(job: SymbolizationJob) -> ArtifactIdentity | None:
    """Build the exact lookup identity, returning None instead of guessing missing fields."""
    inbox = job.inbox_event
    if (
        inbox.release_identity_quality != "OCCURRENCE_BOUND"
        or not inbox.version_code
        or not inbox.app_build
        or not inbox.variant
    ):
        return None
    if job.job_type == SYMBOL_JOB_JAVA:
        return ArtifactIdentity(
            ARTIFACT_TYPE_JAVA_MAPPING,
            inbox.tenant_id,
            inbox.app_id,
            inbox.version_code,
            inbox.app_build,
            inbox.variant,
        )
    native_frames = inbox.native_identity_json
    first_frame = native_frames[0] if native_frames else {}
    abi = _string_field(first_frame, "abi")
    build_id = _string_field(first_frame, "module_build_id").lower()
    if abi not in SUPPORTED_ABIS or not BUILD_ID_PATTERN.fullmatch(build_id):
        return None
    return ArtifactIdentity(
        ARTIFACT_TYPE_NATIVE_ELF,
        inbox.tenant_id,
        inbox.app_id,
        inbox.version_code,
        inbox.app_build,
        inbox.variant,
        abi,
        build_id,
    )


async def mark_symbols_missing(
    session: AsyncSession,
    owner: str,
    job_id: int,
    error_message: str,
    now: datetime | None = None,
) -> bool:
    """Park an owned job until the exact artifact is uploaded; keep raw export waiting."""
    observed = lease_write_time(session, now)
    result = await session.execute(
        update(SymbolizationJob)
        .where(
            SymbolizationJob.id == job_id,
            SymbolizationJob.status == SYMBOL_STATUS_PROCESSING,
            SymbolizationJob.lease_owner == owner,
            SymbolizationJob.lease_expires_at > observed,
        )
        .values(
            status=SYMBOL_STATUS_SYMBOLS_MISSING,
            next_attempt_at=observed + timedelta(seconds=SYMBOL_ARTIFACT_RECHECK_SECONDS),
            lease_owner=None,
            lease_expires_at=None,
            last_error_code="symbols_missing",
            last_error_message=error_message[:MAX_EXPORT_ERROR_LENGTH],
            updated_at=observed,
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def mark_symbolized(
    session: AsyncSession,
    owner: str,
    job: SymbolizationJob,
    artifact_id: int,
    result_json: dict[str, Any],
    fingerprint_sha256: str,
    tool_name: str,
    tool_version: str,
    now: datetime | None = None,
    *,
    status: str = SYMBOL_STATUS_SYMBOLIZED,
) -> bool:
    """Persist deterministic output and release the corresponding inbox row for OTLP export."""
    if status not in {SYMBOL_STATUS_SYMBOLIZED, SYMBOL_STATUS_PARTIALLY_SYMBOLIZED} or (
        status == SYMBOL_STATUS_PARTIALLY_SYMBOLIZED and job.job_type != SYMBOL_JOB_NATIVE
    ):
        raise ValueError("Unsupported symbolization completion status")
    observed = lease_write_time(session, now)
    changed = await session.execute(
        update(SymbolizationJob)
        .where(
            SymbolizationJob.id == job.id,
            SymbolizationJob.status == SYMBOL_STATUS_PROCESSING,
            SymbolizationJob.lease_owner == owner,
            SymbolizationJob.lease_expires_at > observed,
        )
        .values(
            status=status,
            artifact_id=artifact_id,
            result_json=result_json,
            fingerprint_sha256=fingerprint_sha256,
            tool_name=tool_name,
            tool_version=tool_version,
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=None,
            last_error_message=None,
            updated_at=observed,
        )
    )
    if not changed.rowcount:  # type: ignore[attr-defined]
        return False
    if job.job_type == SYMBOL_JOB_JAVA:
        # Publish the canonical Issue identity in the same fenced transaction as the result.
        # Keep the raw alias and replay hash immutable so old links/retries remain meaningful.
        await session.execute(
            update(InboxEvent)
            .where(
                InboxEvent.id == job.inbox_event_id, InboxEvent.incident_fingerprint.is_not(None)
            )
            .values(incident_fingerprint=fingerprint_sha256)
        )
    await _release_inbox(session, job.inbox_event_id)
    return True


async def mark_symbolization_failed(
    session: AsyncSession,
    owner: str,
    job: SymbolizationJob,
    retryable: bool,
    max_attempts: int,
    error_code: str,
    error_message: str,
    now: datetime | None = None,
) -> bool:
    """Retry an owned tool failure or finalize it and release raw crash export."""
    observed = lease_write_time(session, now)
    should_retry = retryable and job.attempt_count < max_attempts
    delay = min(2 ** min(job.attempt_count, 8), MAX_SYMBOL_BACKOFF_SECONDS)
    result = await session.execute(
        update(SymbolizationJob)
        .where(
            SymbolizationJob.id == job.id,
            SymbolizationJob.status == SYMBOL_STATUS_PROCESSING,
            SymbolizationJob.lease_owner == owner,
            SymbolizationJob.lease_expires_at > observed,
        )
        .values(
            status=SYMBOL_STATUS_PENDING if should_retry else SYMBOL_STATUS_FAILED,
            next_attempt_at=observed + timedelta(seconds=delay),
            lease_owner=None,
            lease_expires_at=None,
            last_error_code=error_code[:128],
            last_error_message=error_message[:MAX_EXPORT_ERROR_LENGTH],
            updated_at=observed,
        )
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        return False
    if not should_retry:
        await _release_inbox(session, job.inbox_event_id)
    return True


async def requeue_matching_missing_jobs(session: AsyncSession, identity: ArtifactIdentity) -> int:
    """Wake parked jobs only when a newly registered artifact matches every identity field."""
    job_type = (
        SYMBOL_JOB_JAVA
        if identity.artifact_type == ARTIFACT_TYPE_JAVA_MAPPING
        else SYMBOL_JOB_NATIVE
    )
    jobs = list(
        (
            await session.scalars(
                select(SymbolizationJob)
                .join(InboxEvent, InboxEvent.id == SymbolizationJob.inbox_event_id)
                .options(selectinload(SymbolizationJob.inbox_event))
                .where(
                    SymbolizationJob.status == SYMBOL_STATUS_SYMBOLS_MISSING,
                    SymbolizationJob.job_type == job_type,
                    SymbolizationJob.tenant_id == identity.tenant_id,
                    InboxEvent.app_id == identity.app_id,
                    InboxEvent.version_code == identity.version_code,
                    InboxEvent.app_build == identity.app_build,
                    InboxEvent.variant == identity.variant,
                )
            )
        ).all()
    )
    changed = 0
    for job in jobs:
        matches = (
            artifact_identity_for_job(job) == identity
            if job_type == SYMBOL_JOB_JAVA
            else any(
                frame.get("abi") == identity.abi
                and str(frame.get("module_build_id", "")).lower() == identity.build_id
                for frame in job.inbox_event.native_identity_json
            )
        )
        if not matches:
            continue
        result = await session.execute(
            update(SymbolizationJob)
            .where(
                SymbolizationJob.id == job.id,
                SymbolizationJob.status == SYMBOL_STATUS_SYMBOLS_MISSING,
            )
            .values(
                status=SYMBOL_STATUS_PENDING,
                next_attempt_at=utc_now(),
                last_error_code=None,
                last_error_message=None,
            )
        )
        changed += int(result.rowcount)  # type: ignore[attr-defined]
    await session.flush()
    return changed


async def _release_inbox(session: AsyncSession, inbox_event_id: int) -> None:
    """Make a symbolized or terminally failed crash eligible for ordinary OTLP export."""
    await session.execute(
        update(InboxEvent)
        .where(
            InboxEvent.id == inbox_event_id,
            InboxEvent.status == INBOX_STATUS_AWAITING_SYMBOLS,
        )
        .values(status=INBOX_STATUS_PENDING)
    )


def _job_type(event: ApmEvent) -> str | None:
    """Map only source-reviewed crash event names to symbolization job types."""
    if event.module != "crash":
        return None
    if event.name in JAVA_CRASH_NAMES:
        return SYMBOL_JOB_JAVA
    if event.name in NATIVE_CRASH_NAMES:
        return SYMBOL_JOB_NATIVE
    return None


def _stack_value(payload: dict[str, Any], job_type: str) -> str:
    """Read current and compatibility stack field spellings without coercing objects."""
    keys = ("stackTrace", "stack_trace") if job_type == SYMBOL_JOB_JAVA else ("backtrace",)
    for key in keys:
        value = _payload_fields(payload).get(key)
        if isinstance(value, str):
            return value
    return ""


def _has_symbol_identity(
    inbox: InboxEvent,
    job_type: str,
    stack: str,
) -> bool:
    """Require the complete exact-match identity before blocking raw crash export."""
    if (
        not stack
        or inbox.release_identity_quality != "OCCURRENCE_BOUND"
        or not inbox.version_code
        or not inbox.app_build
        or not inbox.variant
    ):
        return False
    if job_type == SYMBOL_JOB_JAVA:
        return True
    if not inbox.native_identity_json:
        return False
    first_frame = inbox.native_identity_json[0]
    abi = str(first_frame.get("abi", ""))
    build_id = str(first_frame.get("module_build_id", "")).lower()
    return abi in SUPPORTED_ABIS and BUILD_ID_PATTERN.fullmatch(build_id) is not None


def _payload_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a safe fields mapping from immutable inbox JSON."""
    fields = payload.get("fields")
    return fields if isinstance(fields, dict) else {}


def _string_field(fields: dict[str, Any], key: str) -> str:
    """Return a string field or an empty sentinel for strict identity checks."""
    value = fields.get(key)
    return value.strip() if isinstance(value, str) else ""
