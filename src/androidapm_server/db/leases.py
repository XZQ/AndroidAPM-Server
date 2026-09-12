"""Claim fencing tokens and database-clock checks for durable worker leases."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

MAX_LEASE_OWNER_LENGTH = 128


def new_lease_owner(worker: str) -> str:
    """Give every claim a distinct token, including repeated claims by one worker."""
    token = uuid.uuid4().hex
    return f"{worker[: MAX_LEASE_OWNER_LENGTH - len(token) - 1]}:{token}"


def lease_write_time(
    session: AsyncSession, now: datetime | None = None
) -> datetime | ColumnElement[datetime]:
    """Check PostgreSQL expiry at execution time, even after waiting for a row lock."""
    if now is not None:
        return now
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        # CURRENT_TIMESTAMP is fixed at transaction start and can admit expired owners.
        return func.clock_timestamp()
    return datetime.now(UTC)


async def lease_claim_time(session: AsyncSession, now: datetime | None = None) -> datetime:
    """Use the production database clock so worker host clock skew cannot widen a lease."""
    value = lease_write_time(session, now)
    if isinstance(value, datetime):
        return value
    return cast(datetime, await session.scalar(select(value)))
