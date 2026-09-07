"""Bounded tenant-scoped reads for the Query/BFF plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.db.models import InboxEvent, ReleaseDecision
from androidapm_server.errors import ApiError
from androidapm_server.query_auth import QueryPrincipal


@dataclass(frozen=True, slots=True)
class QueryFact:
    """Low-cardinality event facts safe for bounded in-process aggregation."""

    id: int
    event_id: str
    app_version: str | None
    release_identity_quality: str
    installation_identity_quality: str
    installation_hmac: str | None
    occurrence_timestamp_ms: int
    received_at: datetime
    incident_fingerprint: str | None
    schema_version: str
    protocol: str
    inbox_status: str
    module: str
    name: str
    scene: str | None
    sdk_drop_count: int | None
    sdk_drop_rate: float | None
    sdk_emit_count: int | None = None
    installation_hmac_key_version: str | None = None


async def load_window_facts(
    session: AsyncSession,
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
    max_rows: int,
) -> list[QueryFact]:
    """Load no more than the configured fact budget for one authenticated scope."""
    module_expression = InboxEvent.payload_json["module"].as_string()
    name_expression = InboxEvent.payload_json["name"].as_string()
    scene_expression = InboxEvent.payload_json["scene"].as_string()
    registered = InboxEvent.normalized_json["registered_fields"]
    statement = (
        select(
            InboxEvent.id.label("id"),
            InboxEvent.event_id.label("event_id"),
            InboxEvent.app_version.label("app_version"),
            InboxEvent.release_identity_quality.label("release_identity_quality"),
            InboxEvent.installation_identity_quality.label("installation_identity_quality"),
            InboxEvent.installation_hmac.label("installation_hmac"),
            InboxEvent.installation_hmac_key_version.label("installation_hmac_key_version"),
            InboxEvent.occurrence_timestamp_ms.label("occurrence_timestamp_ms"),
            InboxEvent.received_at.label("received_at"),
            InboxEvent.incident_fingerprint.label("incident_fingerprint"),
            InboxEvent.schema_version.label("schema_version"),
            InboxEvent.protocol.label("protocol"),
            InboxEvent.status.label("inbox_status"),
            module_expression.label("module"),
            name_expression.label("name"),
            scene_expression.label("scene"),
            registered["dropCount"].as_integer().label("sdk_drop_count"),
            registered["dropRate"].as_float().label("sdk_drop_rate"),
            registered["emitCount"].as_integer().label("sdk_emit_count"),
        )
        .where(*_scope_window_predicates(principal, from_ms, to_ms))
        .order_by(InboxEvent.id)
        .limit(max_rows + 1)
    )
    rows = (await session.execute(statement)).mappings().all()
    if len(rows) > max_rows:
        raise _query_budget_exceeded(max_rows)
    return [_fact_from_mapping(row) for row in rows]


async def ensure_query_budget(
    session: AsyncSession,
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
    max_rows: int,
) -> None:
    """Reject detail-list scans whose authenticated time window exceeds the row budget."""
    statement = (
        select(InboxEvent.id)
        .where(*_scope_window_predicates(principal, from_ms, to_ms))
        .limit(max_rows + 1)
    )
    rows = (await session.execute(statement)).scalars().all()
    if len(rows) > max_rows:
        raise _query_budget_exceeded(max_rows)


async def list_scoped_events(
    session: AsyncSession,
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
    limit: int,
    *,
    release_version: str | None,
    module: str | None,
    name: str | None,
    fingerprint: str | None,
    cursor: tuple[int, int] | None,
) -> tuple[list[InboxEvent], bool]:
    """Return one deterministic event page inside the credential's fixed data scope."""
    statement: Select[tuple[InboxEvent]] = select(InboxEvent).where(
        *_scope_window_predicates(principal, from_ms, to_ms)
    )
    if release_version is not None:
        statement = statement.where(InboxEvent.app_version == release_version)
    if module is not None:
        statement = statement.where(InboxEvent.payload_json["module"].as_string() == module)
    if name is not None:
        statement = statement.where(InboxEvent.payload_json["name"].as_string() == name)
    if fingerprint is not None:
        statement = statement.where(InboxEvent.incident_fingerprint == fingerprint)
    if cursor is not None:
        timestamp_ms, row_id = cursor
        statement = statement.where(
            or_(
                InboxEvent.occurrence_timestamp_ms < timestamp_ms,
                and_(
                    InboxEvent.occurrence_timestamp_ms == timestamp_ms,
                    InboxEvent.id < row_id,
                ),
            )
        )
    statement = statement.order_by(
        InboxEvent.occurrence_timestamp_ms.desc(), InboxEvent.id.desc()
    ).limit(limit + 1)
    events = list((await session.execute(statement)).scalars().all())
    has_more = len(events) > limit
    return events[:limit], has_more


async def get_scoped_event(
    session: AsyncSession,
    principal: QueryPrincipal,
    event_id: str,
) -> InboxEvent | None:
    """Find one event without exposing whether another tenant owns the identifier."""
    statement = select(InboxEvent).where(
        InboxEvent.tenant_id == principal.tenant_id,
        InboxEvent.app_id == principal.app_id,
        InboxEvent.environment == principal.environment,
        InboxEvent.event_id == event_id,
    )
    return cast(InboxEvent | None, await session.scalar(statement))


async def list_release_decisions(
    session: AsyncSession,
    principal: QueryPrincipal,
    release_version: str | None,
    limit: int,
) -> list[ReleaseDecision]:
    """Read recent human decisions from only the authenticated release scope."""
    statement: Select[tuple[ReleaseDecision]] = select(ReleaseDecision).where(
        ReleaseDecision.tenant_id == principal.tenant_id,
        ReleaseDecision.app_id == principal.app_id,
        ReleaseDecision.environment == principal.environment,
    )
    if release_version is not None:
        statement = statement.where(ReleaseDecision.release_version == release_version)
    statement = statement.order_by(
        ReleaseDecision.created_at.desc(), ReleaseDecision.id.desc()
    ).limit(limit)
    return list((await session.execute(statement)).scalars().all())


def _scope_window_predicates(
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
) -> tuple[Any, ...]:
    """Build the mandatory scope and half-open occurrence-time predicates."""
    return (
        InboxEvent.tenant_id == principal.tenant_id,
        InboxEvent.app_id == principal.app_id,
        InboxEvent.environment == principal.environment,
        InboxEvent.occurrence_timestamp_ms >= from_ms,
        InboxEvent.occurrence_timestamp_ms < to_ms,
    )


def _fact_from_mapping(row: RowMapping) -> QueryFact:
    """Convert one SQLAlchemy mapping into the stable query-fact structure."""
    return QueryFact(
        id=cast(int, row["id"]),
        event_id=cast(str, row["event_id"]),
        app_version=cast(str | None, row["app_version"]),
        release_identity_quality=cast(str, row["release_identity_quality"]),
        installation_identity_quality=cast(str, row["installation_identity_quality"]),
        installation_hmac=cast(str | None, row["installation_hmac"]),
        installation_hmac_key_version=cast(str | None, row["installation_hmac_key_version"]),
        occurrence_timestamp_ms=cast(int, row["occurrence_timestamp_ms"]),
        received_at=cast(datetime, row["received_at"]),
        incident_fingerprint=cast(str | None, row["incident_fingerprint"]),
        schema_version=cast(str, row["schema_version"]),
        protocol=cast(str, row["protocol"]),
        inbox_status=cast(str, row["inbox_status"]),
        module=cast(str | None, row["module"]) or "",
        name=cast(str | None, row["name"]) or "",
        scene=cast(str | None, row["scene"]),
        sdk_drop_count=_optional_int(row["sdk_drop_count"]),
        sdk_drop_rate=_optional_float(row["sdk_drop_rate"]),
        sdk_emit_count=_optional_int(row["sdk_emit_count"]),
    )


def _optional_int(value: object) -> int | None:
    """Normalize a nullable JSON integer selected by PostgreSQL or SQLite."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_float(value: object) -> float | None:
    """Normalize a nullable JSON number selected by PostgreSQL or SQLite."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _query_budget_exceeded(max_rows: int) -> ApiError:
    """Return the stable error used when a window would process too many rows."""
    return ApiError(
        422,
        "query_budget_exceeded",
        f"The requested window exceeds the {max_rows} row query budget",
    )
