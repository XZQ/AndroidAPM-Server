"""Bounded, scoped observation statistics over reviewed performance projections."""

from __future__ import annotations

import math
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.config import Settings
from androidapm_server.constants import EVIDENCE_EXPIRED
from androidapm_server.db.models import InboxEvent
from androidapm_server.db.query import _execute_bounded, query_bucket_ms
from androidapm_server.domain import IdentityQuality
from androidapm_server.errors import ApiError
from androidapm_server.performance_catalog import (
    DIMENSION_FIELDS,
    EVENT_CATALOG,
    PERFORMANCE_EVENTS,
    PERFORMANCE_FIELDS,
    DimensionField,
    PerformanceField,
)
from androidapm_server.query import QueryModel, QueryScope, scope_for
from androidapm_server.query_auth import QueryPrincipal


class Observation(QueryModel):
    """Numeric distribution with explicit evidence availability and coverage."""

    state: str
    sample_count: int
    event_count: int
    missing_count: int
    invalid_count: int
    unavailable_count: int
    coverage: float | None
    mean: float | None
    p50: float | None
    p75: float | None
    p95: float | None
    maximum: float | None
    minimum: float | None


class PerformanceMetric(QueryModel):
    """Version comparison for an exact metric contract."""

    id: str
    module: str
    name: str
    field: str
    label: str
    unit: str
    current: Observation
    baseline: Observation
    p95_delta: float | None


class TrendPoint(QueryModel):
    """One observation bucket; an empty bucket remains NO_DATA."""

    from_ms: int
    to_ms: int
    current: Observation
    baseline: Observation


class EventStatistic(QueryModel):
    """Counts of durably reported exact events, not population or sampling denominators."""

    module: str
    name: str
    label: str
    current_count: int
    baseline_count: int
    total_count: int
    non_occurrence_count: int


class CategoryObservation(QueryModel):
    """Fixed categories distinguish missing, invalid, unknown, and expired evidence."""

    state: str
    event_count: int
    available_count: int
    missing_count: int
    invalid_count: int
    unknown_count: int
    expired_count: int
    counts: dict[str, int]


class CategoryStatistic(QueryModel):
    """A reviewed dimension for occurrence-bound current and baseline versions."""

    id: str
    module: str
    name: str
    field: str
    label: str
    current: CategoryObservation
    baseline: CategoryObservation


class WeightedFps(QueryModel):
    """FPS mean weighted only by paired, positive reported window durations."""

    state: str
    value: float | None
    sample_count: int
    event_count: int
    total_window_ms: float | None
    missing_count: int
    invalid_count: int
    unavailable_count: int
    coverage: float | None


class PerformanceResponse(QueryModel):
    """L0 response with no raw URL, SQL, stack, context, or installation values."""

    request_id: str
    scope: QueryScope
    new_release: str
    baseline_release: str
    from_ms: int
    to_ms: int
    source: str = "durable_inbox"
    semantics: str = "reported_observations"
    metrics: list[PerformanceMetric]
    trend_metric: str
    trend: list[TrendPoint]
    event_counts: dict[str, int]
    late_count: int
    non_occurrence_count: int
    network_error_ratio: float | None
    jank_ratio: float | None
    event_statistics: list[EventStatistic]
    dimensions: list[CategoryStatistic]
    weighted_fps: WeightedFps
    baseline_weighted_fps: WeightedFps


async def load_observations(
    session: AsyncSession,
    principal: QueryPrincipal,
    settings: Settings,
    from_ms: int,
    to_ms: int,
    releases: tuple[str, ...] | None = None,
    *,
    performance_only: bool = False,
) -> list[dict[str, Any]]:
    """Project only normalized facts; reject oversized queries before aggregation."""
    statement = select(
        InboxEvent.app_version,
        InboxEvent.release_identity_quality,
        InboxEvent.occurrence_timestamp_ms,
        InboxEvent.received_at,
        InboxEvent.normalized_json,
        InboxEvent.payload_json["module"].as_string().label("module"),
        InboxEvent.payload_json["name"].as_string().label("name"),
    ).where(
        InboxEvent.tenant_id == principal.tenant_id,
        InboxEvent.app_id == principal.app_id,
        InboxEvent.environment == principal.environment,
        InboxEvent.occurrence_timestamp_ms >= from_ms,
        InboxEvent.occurrence_timestamp_ms < to_ms,
    )
    if releases is not None:
        statement = statement.where(InboxEvent.app_version.in_(releases))
    if performance_only:
        statement = statement.where(
            or_(
                *(
                    and_(
                        InboxEvent.payload_json["module"].as_string() == module,
                        InboxEvent.payload_json["name"].as_string() == name,
                    )
                    for module, name in PERFORMANCE_EVENTS
                )
            )
        )
    rows = (await _execute_bounded(session, statement.limit(settings.query_max_rows + 1))).all()
    if len(rows) > settings.query_max_rows:
        raise ApiError(422, "query_row_limit", "Reduce the time window; no partial data returned")
    facts = []
    for row in rows:
        fact = dict(row._mapping)
        fact["normalized_json"] = {
            **fact["normalized_json"],
            "module": fact.pop("module"),
            "name": fact.pop("name"),
        }
        facts.append(fact)
    return facts


async def count_window_events(
    session: AsyncSession,
    principal: QueryPrincipal,
    from_ms: int,
    to_ms: int,
    release_version: str | None,
    events: frozenset[tuple[str, str]],
) -> tuple[int, int]:
    """Aggregate ``(samples, incident_count)`` in SQL for alert count metrics.

    The predicates mirror ``load_observations`` plus the occurrence-bound release
    filter alert evaluation applies, so the result is identical to counting the
    projected rows. A single-row aggregation keeps high-volume windows evaluable
    without the row budget; the driver/statement timeout still bounds execution.
    """
    module = InboxEvent.payload_json["module"].as_string()
    name = InboxEvent.payload_json["name"].as_string()
    incident = or_(*(and_(module == m, name == n) for m, n in sorted(events)))
    statement = select(
        func.count().label("samples"),
        func.coalesce(func.sum(case((incident, 1), else_=0)), 0).label("incidents"),
    ).where(
        InboxEvent.tenant_id == principal.tenant_id,
        InboxEvent.app_id == principal.app_id,
        InboxEvent.environment == principal.environment,
        InboxEvent.occurrence_timestamp_ms >= from_ms,
        InboxEvent.occurrence_timestamp_ms < to_ms,
    )
    if release_version is not None:
        statement = statement.where(
            InboxEvent.app_version == release_version,
            InboxEvent.release_identity_quality == IdentityQuality.OCCURRENCE_BOUND.value,
        )
    row = (await _execute_bounded(session, statement)).one()
    return int(row.samples), int(row.incidents)


def _value(row: dict[str, Any], field: PerformanceField) -> tuple[str, float | None]:
    normalized = row["normalized_json"]
    if (normalized.get("module"), normalized.get("name")) != (field.module, field.name):
        return "NOT_APPLICABLE", None
    values = normalized.get("registered_fields", {})
    if field.availability_field is not None and values.get(field.availability_field) is not True:
        return "UNAVAILABLE", None
    if field.sample_count_field is not None:
        count = values.get(field.sample_count_field)
        if (
            not isinstance(count, (int, float))
            or isinstance(count, bool)
            or not math.isfinite(count)
            or count <= 0
        ):
            # Empty SDK stage histogram values are placeholders, not observed zero durations.
            return "UNAVAILABLE", None
    value = values.get(field.field)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return "AVAILABLE", float(value)
    state = normalized.get("field_states", {}).get(field.field, "UNAVAILABLE")
    return state, None


def percentile(values: list[float], quantile: float) -> float | None:
    """Use the documented linear interpolation quantile over sorted observations."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(rows: list[dict[str, Any]], field: PerformanceField) -> Observation:
    """Exclude invalid/unavailable fields and report the eligible denominator."""
    values: list[float] = []
    states: Counter[str] = Counter()
    for row in rows:
        state, value = _value(row, field)
        if state == "NOT_APPLICABLE":
            continue
        states[state] += 1
        if value is not None:
            values.append(value)
    count = sum(states.values())
    state = "NO_DATA" if count == 0 else "UNAVAILABLE"
    if values:
        state = "PRESENT" if len(values) == count else "DEGRADED"
        if len(values) == count and all(value == 0 for value in values):
            state = "ZERO"
    mean: float | None = None
    for index, value in enumerate(values):
        mean = value if mean is None else mean + (value - mean) / (index + 1)
    return Observation(
        state=state,
        sample_count=len(values),
        event_count=count,
        missing_count=states["MISSING"],
        invalid_count=states["INVALID"],
        unavailable_count=count - len(values) - states["MISSING"] - states["INVALID"],
        coverage=len(values) / count if count else None,
        mean=mean,
        p50=percentile(values, 0.5),
        p75=percentile(values, 0.75),
        p95=percentile(values, 0.95),
        maximum=max(values) if values else None,
        minimum=min(values) if values else None,
    )


def _paired_ratio(
    rows: list[dict[str, Any]],
    module: str,
    name: str,
    numerator: str,
    denominator: str,
) -> float | None:
    pairs: list[tuple[float, float]] = []
    for row in rows:
        normalized = row["normalized_json"]
        if (normalized.get("module"), normalized.get("name")) != (module, name):
            continue
        values = normalized.get("registered_fields", {})
        a, b = values.get(numerator), values.get(denominator)
        if (
            isinstance(a, (int, float))
            and not isinstance(a, bool)
            and isinstance(b, (int, float))
            and not isinstance(b, bool)
            and 0 <= a <= b
            and b > 0
            and math.isfinite(a)
            and math.isfinite(b)
        ):
            pairs.append((float(a), float(b)))
    if not pairs:
        return None
    # Scale before summing: valid finite SDK observations must not produce JSON NaN/Infinity.
    scale = max(b for _, b in pairs)
    return math.fsum(a / scale for a, _ in pairs) / math.fsum(b / scale for _, b in pairs)


def summarize_category(rows: list[dict[str, Any]], field: DimensionField) -> CategoryObservation:
    """Aggregate only immutable allow-listed values without returning unrestricted strings."""
    labels = ("false", "true") if field.field_type == "boolean" else field.allowed_values
    counts = dict.fromkeys(labels, 0)
    states: Counter[str] = Counter()
    for row in rows:
        normalized = row["normalized_json"]
        if (normalized.get("module"), normalized.get("name")) != (field.module, field.name):
            continue
        original = normalized.get("field_states", {}).get(field.field, "MISSING")
        if original == EVIDENCE_EXPIRED:
            # Retention removed previously valid string evidence, not an unknown SDK enum.
            states[EVIDENCE_EXPIRED] += 1
            continue
        value = normalized.get("registered_fields", {}).get(field.field)
        category: str | None = None
        if field.field_type == "boolean" and isinstance(value, bool):
            category = str(value).lower()
        elif field.field_type == "string" and isinstance(value, str) and value in labels:
            category = value
        elif (
            field.field_type == "integer"
            and isinstance(value, int)
            and not isinstance(value, bool)
            and str(value) in labels
        ):
            category = str(value)
        if category is not None:
            counts[category] += 1
            states["AVAILABLE"] += 1
        else:
            # A historic unrestricted normalized string is UNKNOWN, never a dynamic L0 category.
            state = "UNKNOWN" if value is not None or original == "AVAILABLE" else original
            states[state if state in {"MISSING", "INVALID", "UNKNOWN"} else "UNKNOWN"] += 1
    count = sum(states.values())
    available = states["AVAILABLE"]
    return CategoryObservation(
        state=(
            "NO_DATA"
            if not count
            else "UNAVAILABLE"
            if not available
            else "PRESENT"
            if available == count
            else "DEGRADED"
        ),
        event_count=count,
        available_count=available,
        missing_count=states["MISSING"],
        invalid_count=states["INVALID"],
        unknown_count=states["UNKNOWN"],
        expired_count=states[EVIDENCE_EXPIRED],
        counts=counts,
    )


def summarize_weighted_fps(rows: list[dict[str, Any]]) -> WeightedFps:
    """Keep missing/zero windows out of a duration-weighted reported FPS observation."""
    fps_field = next(field for field in PERFORMANCE_FIELDS if field.id == "fps.fps_stats.fps")
    window_field = next(
        field for field in PERFORMANCE_FIELDS if field.id == "fps.fps_stats.windowDurationMs"
    )
    pairs: list[tuple[float, float]] = []
    states: Counter[str] = Counter()
    for row in rows:
        fps_state, fps = _value(row, fps_field)
        if fps_state == "NOT_APPLICABLE":
            continue
        window_state, window = _value(row, window_field)
        if (
            fps_state == "INVALID"
            or window_state == "INVALID"
            or ((fps is not None and fps < 0) or (window is not None and window <= 0))
        ):
            states["INVALID"] += 1
        elif fps_state == "MISSING" or window_state == "MISSING":
            states["MISSING"] += 1
        elif fps is None or window is None:
            states["UNAVAILABLE"] += 1
        else:
            states["AVAILABLE"] += 1
            pairs.append((fps, window))
    count = sum(states.values())
    value: float | None = None
    total: float | None = None
    if pairs:
        scale = max(window for _, window in pairs)
        scaled_total = math.fsum(window / scale for _, window in pairs)
        # Both products are bounded by their respective finite maxima after scaling.
        fps_scale = max(fps for fps, _ in pairs)
        value = (
            (
                math.fsum((fps / fps_scale) * (window / scale) for fps, window in pairs)
                / scaled_total
                * fps_scale
            )
            if fps_scale
            else 0.0
        )
        candidate = scale * scaled_total
        total = candidate if math.isfinite(candidate) else None
    state = (
        "NO_DATA"
        if not count
        else "UNAVAILABLE"
        if not pairs
        else "DEGRADED"
        if len(pairs) < count
        else "ZERO"
        if value == 0
        else "PRESENT"
    )
    return WeightedFps(
        state=state,
        value=value,
        sample_count=len(pairs),
        event_count=count,
        total_window_ms=total,
        missing_count=states["MISSING"],
        invalid_count=states["INVALID"],
        unavailable_count=states["UNAVAILABLE"],
        coverage=len(pairs) / count if count else None,
    )


def build_performance(
    rows: list[dict[str, Any]],
    principal: QueryPrincipal,
    request_id: str,
    new_release: str,
    baseline_release: str,
    from_ms: int,
    to_ms: int,
    trend_field: PerformanceField,
    late_after_ms: int,
) -> PerformanceResponse:
    """Compare occurrence-bound releases with at most 24 minute-aligned buckets."""
    eligible = [row for row in rows if row["release_identity_quality"] == "OCCURRENCE_BOUND"]
    current = [row for row in eligible if row["app_version"] == new_release]
    baseline = [row for row in eligible if row["app_version"] == baseline_release]
    metrics = []
    for field in PERFORMANCE_FIELDS:
        a, b = summarize(current, field), summarize(baseline, field)
        metrics.append(
            PerformanceMetric(
                id=field.id,
                module=field.module,
                name=field.name,
                field=field.field,
                label=field.label,
                unit=field.unit,
                current=a,
                baseline=b,
                p95_delta=a.p95 - b.p95 if a.p95 is not None and b.p95 is not None else None,
            )
        )
    width = query_bucket_ms(from_ms, to_ms)
    trend = [
        TrendPoint(
            from_ms=start,
            to_ms=min(start + width, to_ms),
            current=summarize(
                [r for r in current if start <= r["occurrence_timestamp_ms"] < start + width],
                trend_field,
            ),
            baseline=summarize(
                [r for r in baseline if start <= r["occurrence_timestamp_ms"] < start + width],
                trend_field,
            ),
        )
        for start in range(from_ms, to_ms, width)
    ]
    counts: Counter[str] = Counter()
    current_counts: Counter[str] = Counter()
    baseline_counts: Counter[str] = Counter()
    excluded_counts: Counter[str] = Counter()
    late = 0
    for row in rows:
        normalized = row["normalized_json"]
        event_key = (normalized.get("module", ""), normalized.get("name", ""))
        if event_key in PERFORMANCE_EVENTS:
            key = f"{event_key[0]}/{event_key[1]}"
            counts[key] += 1
            if row["release_identity_quality"] != "OCCURRENCE_BOUND":
                excluded_counts[key] += 1
            else:
                if row["app_version"] == new_release:
                    current_counts[key] += 1
                if row["app_version"] == baseline_release:
                    baseline_counts[key] += 1
        received: datetime = row["received_at"]
        if received.tzinfo is None:
            received = received.replace(tzinfo=UTC)
        late += int(received.timestamp() * 1000 - row["occurrence_timestamp_ms"] > late_after_ms)
    return PerformanceResponse(
        request_id=request_id,
        scope=scope_for(principal),
        new_release=new_release,
        baseline_release=baseline_release,
        from_ms=from_ms,
        to_ms=to_ms,
        metrics=metrics,
        trend_metric=trend_field.id,
        trend=trend,
        event_counts=dict(counts),
        late_count=late,
        non_occurrence_count=len(rows) - len(eligible),
        network_error_ratio=_paired_ratio(
            current, "network", "network_aggregate", "errorCount", "totalRequests"
        ),
        jank_ratio=_paired_ratio(current, "fps", "fps_stats", "jankCount", "frameCount"),
        weighted_fps=summarize_weighted_fps(current),
        baseline_weighted_fps=summarize_weighted_fps(baseline),
        event_statistics=[
            EventStatistic(
                module=contract.module,
                name=contract.name,
                label=contract.label,
                current_count=current_counts[f"{contract.module}/{contract.name}"],
                baseline_count=baseline_counts[f"{contract.module}/{contract.name}"],
                total_count=counts[f"{contract.module}/{contract.name}"],
                non_occurrence_count=excluded_counts[f"{contract.module}/{contract.name}"],
            )
            for contract in EVENT_CATALOG
        ],
        dimensions=[
            CategoryStatistic(
                id=field.id,
                module=field.module,
                name=field.name,
                field=field.field,
                label=field.label,
                current=summarize_category(current, field),
                baseline=summarize_category(baseline, field),
            )
            for field in DIMENSION_FIELDS
        ],
    )
