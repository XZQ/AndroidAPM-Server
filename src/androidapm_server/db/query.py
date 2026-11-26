"""Bounded tenant-scoped reads for the Query/BFF plane."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import BigInteger, Integer, Select, and_, case, func, literal, or_, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.engine import Result, RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.constants import (
    EVIDENCE_AVAILABLE,
    EVIDENCE_EXPIRED,
    EVIDENCE_MISSING,
    EVIDENCE_RETENTION_UNKNOWN,
)
from androidapm_server.db.models import InboxEvent, ReleaseDecision
from androidapm_server.errors import ApiError
from androidapm_server.query_auth import QueryPrincipal

QUERY_TIMEOUT_SECONDS = 5.0


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
    weight: int = 1
    last_occurrence_timestamp_ms: int | None = None
    late: bool | None = None
    scene_state: str | None = None
    session_identity_quality: str = "ABSENT"
    session_hmac: str | None = None
    session_hmac_key_version: str | None = None
    session_start_valid: bool = False
    device_model: str | None = None
    os_version: str | None = None


async def resolve_issue_fingerprint(
    session: AsyncSession,
    principal: QueryPrincipal,
    fingerprint: str,
    from_ms: int,
    to_ms: int,
) -> str:
    """Resolve an old raw link within scope/window; never guess among split Issues."""
    statement = (
        select(InboxEvent.incident_fingerprint)
        .distinct()
        .where(
            *_scope_window_predicates(principal, from_ms, to_ms),
            InboxEvent.raw_incident_fingerprint == fingerprint,
            InboxEvent.incident_fingerprint.is_not(None),
        )
        .limit(2)
    )
    matches = (await _execute_bounded(session, statement)).scalars().all()
    if len(matches) > 1:
        raise ApiError(
            409,
            "ambiguous_issue_fingerprint",
            "This raw fingerprint resolves to multiple Issues; select a current Issue",
        )
    return str(matches[0]) if matches else fingerprint


def query_bucket_ms(from_ms: int, to_ms: int) -> int:
    """Share the exact at-most-24, minute-aligned bucket width with response builders."""
    raw = (to_ms - from_ms + 23) // 24
    return max(60_000, ((raw + 59_999) // 60_000) * 60_000)


async def load_window_facts(
    session: AsyncSession,
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
    max_rows: int,
    *,
    release_versions: tuple[str, ...] | None = None,
    fingerprint: str | None = None,
    late_after_ms: int = 900_000,
    include_installation_id: bool = True,
    include_session: bool = True,
    include_session_id: bool = True,
    include_scene: bool = True,
    include_bucket: bool = True,
    include_device_os: bool = True,
    device_os_presence_only: bool = False,
    include_envelope_dims: bool = True,
    include_fingerprint: bool = True,
) -> list[QueryFact]:
    """Reduce filtered events in SQL; project only the dimensions required by the caller."""
    registered = InboxEvent.normalized_json["registered_fields"]
    emit = sql_cast(registered["emitCount"].as_string(), BigInteger)
    drops = sql_cast(registered["dropCount"].as_string(), BigInteger)
    rate = registered["dropRate"].as_float()
    valid_health = case((and_(emit > 0, drops >= 0, rate >= 0, rate <= 1), 1), else_=None)
    dialect = session.bind.dialect.name if session.bind is not None else ""
    received_ms = (
        func.extract("epoch", InboxEvent.received_at) * 1000
        if dialect == "postgresql"
        else (func.julianday(InboxEvent.received_at) - 2440587.5) * 86400000
    )
    late = case(
        (received_ms - InboxEvent.occurrence_timestamp_ms > late_after_ms, True), else_=False
    )
    installation_dim = (
        InboxEvent.installation_hmac
        if include_installation_id
        else case((InboxEvent.installation_hmac.is_not(None), "__present__"), else_=None)
    )
    dimensions: list[Any] = [
        InboxEvent.app_version.label("app_version"),
        InboxEvent.release_identity_quality.label("release_identity_quality"),
        installation_dim.label("installation_hmac"),
        InboxEvent.installation_hmac_key_version.label("installation_hmac_key_version"),
        InboxEvent.payload_json["module"].as_string().label("module"),
        InboxEvent.payload_json["name"].as_string().label("name"),
        valid_health.label("sdk_emit_count"),
        late.label("late"),
    ]
    static_selects: list[Any] = []
    if include_session:
        session_dim = (
            InboxEvent.session_hmac
            if include_session_id
            else case((InboxEvent.session_hmac.is_not(None), "__present__"), else_=None)
        )
        dimensions.extend(
            [
                InboxEvent.session_identity_quality.label("session_identity_quality"),
                session_dim.label("session_hmac"),
                InboxEvent.session_hmac_key_version.label("session_hmac_key_version"),
                case(
                    (
                        and_(
                            InboxEvent.payload_json["module"].as_string() == "core",
                            InboxEvent.payload_json["name"].as_string() == "session_start",
                            sql_cast(
                                InboxEvent.normalized_json["registered_fields"][
                                    "session_schema_version"
                                ].as_string(),
                                BigInteger,
                            )
                            == 1,
                            sql_cast(
                                InboxEvent.normalized_json["registered_fields"][
                                    "session_count"
                                ].as_string(),
                                BigInteger,
                            )
                            == 1,
                        ),
                        True,
                    ),
                    else_=False,
                ).label("session_start_valid"),
            ]
        )
    else:
        static_selects.extend(
            [
                literal("ABSENT").label("session_identity_quality"),
                literal(None).label("session_hmac"),
                literal(None).label("session_hmac_key_version"),
                literal(False).label("session_start_valid"),
            ]
        )

    if include_device_os:
        if device_os_presence_only:
            dimensions.extend(
                [
                    case((InboxEvent.device_model.is_not(None), "__present__"), else_=None).label(
                        "device_model"
                    ),
                    case((InboxEvent.os_version.is_not(None), "__present__"), else_=None).label(
                        "os_version"
                    ),
                ]
            )
        else:
            dimensions.extend(
                [
                    InboxEvent.device_model.label("device_model"),
                    InboxEvent.os_version.label("os_version"),
                ]
            )
    else:
        static_selects.extend(
            [
                literal(None).label("device_model"),
                literal(None).label("os_version"),
            ]
        )

    if include_fingerprint:
        dimensions.append(InboxEvent.incident_fingerprint.label("incident_fingerprint"))
    else:
        static_selects.append(literal(None).label("incident_fingerprint"))

    if include_envelope_dims:
        dimensions.extend(
            [
                InboxEvent.schema_version.label("schema_version"),
                InboxEvent.protocol.label("protocol"),
                InboxEvent.status.label("inbox_status"),
            ]
        )
    else:
        static_selects.extend(
            [
                literal("").label("schema_version"),
                literal("").label("protocol"),
                literal("").label("inbox_status"),
            ]
        )

    if include_scene:
        scene = case(
            (InboxEvent.raw_pruned_at.is_(None), InboxEvent.payload_json["scene"].as_string()),
            else_=None,
        )
        retained_scene_state = InboxEvent.normalized_json["retention"]["scene_state"].as_string()
        scene_state = case(
            (
                InboxEvent.raw_pruned_at.is_not(None),
                case(
                    (
                        retained_scene_state.in_((EVIDENCE_EXPIRED, EVIDENCE_MISSING)),
                        retained_scene_state,
                    ),
                    else_=EVIDENCE_RETENTION_UNKNOWN,
                ),
            ),
            (and_(scene.is_not(None), scene != ""), EVIDENCE_AVAILABLE),
            else_=EVIDENCE_MISSING,
        )
        dimensions.extend([scene.label("scene"), scene_state.label("scene_state")])
    else:
        static_selects.extend([literal(None).label("scene"), literal(None).label("scene_state")])

    if include_bucket:
        bucket = sql_cast(
            func.floor(
                (InboxEvent.occurrence_timestamp_ms - from_ms) / query_bucket_ms(from_ms, to_ms)
            ),
            Integer,
        )
        dimensions.append(bucket.label("bucket"))

    predicates = list(_scope_window_predicates(principal, from_ms, to_ms))
    if release_versions is not None:
        predicates.append(InboxEvent.app_version.in_(release_versions))
    if fingerprint is not None:
        predicates.append(InboxEvent.incident_fingerprint == fingerprint)
    statement = (
        select(
            *dimensions,
            *static_selects,
            func.min(InboxEvent.id).label("id"),
            func.min(InboxEvent.event_id).label("event_id"),
            func.min(InboxEvent.occurrence_timestamp_ms).label("occurrence_timestamp_ms"),
            func.max(InboxEvent.occurrence_timestamp_ms).label("last_occurrence_timestamp_ms"),
            func.max(InboxEvent.received_at).label("received_at"),
            func.max(drops).label("sdk_drop_count"),
            func.max(rate).label("sdk_drop_rate"),
            func.count().label("weight"),
        )
        .where(*predicates)
        .group_by(*dimensions)
        .order_by(func.min(InboxEvent.id))
        .limit(max_rows + 1)
    )
    rows = (await _execute_bounded(session, statement)).mappings().all()
    if len(rows) > max_rows:
        raise _query_budget_exceeded(max_rows)
    return [_fact_from_mapping(row) for row in rows]


async def enforce_release_discovery_budget(
    session: AsyncSession,
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
    max_rows: int,
) -> None:
    """Reject release discovery windows with more eligible events than the scan budget."""
    bounded = (
        select(InboxEvent.id)
        .where(
            InboxEvent.tenant_id == principal.tenant_id,
            InboxEvent.app_id == principal.app_id,
            InboxEvent.environment == principal.environment,
            InboxEvent.app_version.is_not(None),
            InboxEvent.app_version != "",
            InboxEvent.occurrence_timestamp_ms >= from_ms,
            InboxEvent.occurrence_timestamp_ms < to_ms,
        )
        .limit(max_rows + 1)
        .subquery()
    )
    count = (await _execute_bounded(session, select(func.count()).select_from(bounded))).scalar()
    if (count or 0) > max_rows:
        raise _query_budget_exceeded(max_rows)


async def list_discovered_releases(
    session: AsyncSession,
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
    max_rows: int,
    limit: int,
) -> list[tuple[str, int]]:
    """Return bounded release versions ordered by latest occurrence time."""
    await enforce_release_discovery_budget(session, principal, from_ms, to_ms, max_rows)
    statement = (
        select(InboxEvent.app_version, func.count())
        .where(
            InboxEvent.tenant_id == principal.tenant_id,
            InboxEvent.app_id == principal.app_id,
            InboxEvent.environment == principal.environment,
            InboxEvent.app_version.is_not(None),
            InboxEvent.app_version != "",
            InboxEvent.occurrence_timestamp_ms >= from_ms,
            InboxEvent.occurrence_timestamp_ms < to_ms,
        )
        .group_by(InboxEvent.app_version)
        .order_by(func.max(InboxEvent.occurrence_timestamp_ms).desc())
        .limit(limit)
    )
    rows = await _execute_bounded(session, statement)
    return [(row[0] or "", int(row[1])) for row in rows.all()]


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
    events = list((await _execute_bounded(session, statement)).scalars().all())
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
        weight=cast(int, row["weight"]),
        last_occurrence_timestamp_ms=cast(int, row["last_occurrence_timestamp_ms"]),
        late=cast(bool, row["late"]),
        event_id=cast(str, row["event_id"]),
        app_version=cast(str | None, row["app_version"]),
        release_identity_quality=cast(str, row["release_identity_quality"]),
        installation_identity_quality="ABSENT",
        installation_hmac=cast(str | None, row["installation_hmac"]),
        installation_hmac_key_version=cast(str | None, row["installation_hmac_key_version"]),
        session_identity_quality=cast(str, row["session_identity_quality"]),
        session_hmac=cast(str | None, row["session_hmac"]),
        session_hmac_key_version=cast(str | None, row["session_hmac_key_version"]),
        session_start_valid=cast(bool, row["session_start_valid"]),
        device_model=cast(str | None, row["device_model"]),
        os_version=cast(str | None, row["os_version"]),
        occurrence_timestamp_ms=cast(int, row["occurrence_timestamp_ms"]),
        received_at=cast(datetime, row["received_at"]),
        incident_fingerprint=cast(str | None, row["incident_fingerprint"]),
        schema_version=cast(str, row["schema_version"]),
        protocol=cast(str, row["protocol"]),
        inbox_status=cast(str, row["inbox_status"]),
        module=cast(str | None, row["module"]) or "",
        name=cast(str | None, row["name"]) or "",
        scene=cast(str | None, row["scene"]),
        scene_state=cast(str | None, row["scene_state"]),
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
        f"The requested window exceeds the {max_rows} aggregate-group query budget",
    )


async def _execute_bounded(session: AsyncSession, statement: Select[Any]) -> Result[Any]:
    """Bound query execution in both the driver and PostgreSQL; never return partial counts."""
    try:
        async with asyncio.timeout(QUERY_TIMEOUT_SECONDS):
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(
                    text("SELECT set_config('statement_timeout', :budget, true)"),
                    {"budget": str(int(QUERY_TIMEOUT_SECONDS * 1000))},
                )
            return await session.execute(statement)
    except TimeoutError as error:
        raise ApiError(
            503, "query_timeout", "Query exceeded the execution time budget", True
        ) from error
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) == "57014":
            raise ApiError(
                503, "query_timeout", "Query exceeded the execution time budget", True
            ) from error
        raise
