"""Transactional alert episodes and an owner-fenced webhook delivery outbox."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from androidapm_server.config import Settings, get_settings
from androidapm_server.db.leases import lease_claim_time, lease_write_time
from androidapm_server.db.models import AlertDelivery, AlertInstance, AlertRule, Tenant
from androidapm_server.db.session import get_session_factory
from androidapm_server.errors import ApiError
from androidapm_server.performance import load_observations, summarize
from androidapm_server.performance_catalog import PERFORMANCE_BY_ID
from androidapm_server.query import QueryModel
from androidapm_server.query_auth import QueryPrincipal

logger = structlog.get_logger(__name__)
ALERT_COUNT_EVENTS = {
    "crash.count": frozenset({("crash", "java_crash")}),
    "native_crash.count": frozenset({("crash", "native_crash")}),
    "native_tombstone.count": frozenset({("crash", "tombstone_crash")}),
    "anr.count": frozenset({("anr", "anr_detected")}),
    "network.error_count": frozenset({("network", "network_error")}),
}
ALERT_RULE_LIMIT = 100
DELIVERY_LEASE_SECONDS = 30
DELIVERY_TIMEOUT_SECONDS = 10


class NotificationChannel(BaseModel):
    """Server-provisioned destination; browser input cannot create outbound URLs."""

    model_config = ConfigDict(extra="forbid")
    url: HttpUrl
    headers: dict[str, str] = Field(default_factory=dict)


def configured_channels(settings: Settings) -> dict[str, NotificationChannel]:
    """Validate configured aliases without returning URLs or credentials to the browser."""
    try:
        raw = json.loads(settings.notification_channels_json.get_secret_value())
        if not isinstance(raw, dict) or len(raw) > 20:
            raise ValueError("Invalid channel map")
        if any(not isinstance(key, str) or not key or len(key) > 64 for key in raw):
            raise ValueError("Invalid alias")
        return {key: NotificationChannel.model_validate(value) for key, value in raw.items()}
    except (ValueError, TypeError) as error:
        raise ApiError(
            503,
            "notification_configuration_invalid",
            "Notification channels need server configuration",
        ) from error


def alert_metric_catalog() -> list[dict[str, str]]:
    """Expose exact count identities and configurable performance p95 rules."""
    return [{"id": key, "label": key, "unit": "events"} for key in ALERT_COUNT_EVENTS] + [
        {
            "id": f"{field.id}.p95",
            "label": f"{field.label} · {field.module}/{field.name} P95",
            "unit": field.unit,
        }
        for field in PERFORMANCE_BY_ID.values()
    ]


class AlertRuleInput(QueryModel):
    """Complete validated rule definition; edits apply to the next evaluation."""

    name: str = Field(min_length=1, max_length=128)
    metric: str = Field(max_length=256)
    comparison: Literal["gt", "lt"] = "gt"
    threshold: float = Field(ge=0, allow_inf_nan=False)
    min_samples: int = Field(default=1, ge=1, le=100_000)
    window_seconds: int = Field(default=900, ge=60, le=86_400)
    release_version: str | None = Field(default=None, min_length=1, max_length=256)
    channel: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool = True

    @field_validator("metric")
    @classmethod
    def known_metric(cls, value: str) -> str:
        """Reject arbitrary fields and attacker-selected SQL/query contracts."""
        if value not in ALERT_COUNT_EVENTS and (
            not value.endswith(".p95") or value.removesuffix(".p95") not in PERFORMANCE_BY_ID
        ):
            raise ValueError("Unknown alert metric")
        return value


def as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive UTC values and PostgreSQL's aware values."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def rule_json(rule: AlertRule) -> dict[str, Any]:
    """Build non-secret rule state for aggregate readers."""
    return {
        "id": rule.id,
        "name": rule.name,
        "metric": rule.metric,
        "comparison": rule.comparison,
        "threshold": rule.threshold,
        "minSamples": rule.min_samples,
        "windowSeconds": rule.window_seconds,
        "releaseVersion": rule.release_version,
        "channel": rule.channel,
        "enabled": rule.enabled,
        "state": rule.state,
        "lastValue": rule.last_value,
        "lastSampleCount": rule.last_sample_count,
        "activeInstanceId": rule.active_instance_id,
        "evaluatedAt": as_utc(rule.evaluated_at) if rule.evaluated_at else None,
        "silenceUntil": as_utc(rule.silence_until) if rule.silence_until else None,
    }


async def evaluate_rule(
    session: AsyncSession,
    rule: AlertRule,
    settings: Settings,
    now: datetime,
) -> None:
    """Evaluate while the caller holds the tenant lock, then atomically store transitions."""
    if not rule.enabled:
        return
    to_ms = int(now.timestamp() * 1000)
    from_ms = to_ms - rule.window_seconds * 1000
    principal = QueryPrincipal(
        rule.tenant_id, "alert-worker", rule.app_id, rule.environment, "viewer", None
    )
    rows = await load_observations(
        session,
        principal,
        settings,
        from_ms,
        to_ms,
        (rule.release_version,) if rule.release_version else None,
    )
    if rule.release_version is not None:
        rows = [row for row in rows if row["release_identity_quality"] == "OCCURRENCE_BOUND"]
    value: float | None
    if rule.metric in ALERT_COUNT_EVENTS:
        count = sum(
            (row["normalized_json"].get("module"), row["normalized_json"].get("name"))
            in ALERT_COUNT_EVENTS[rule.metric]
            for row in rows
        )
        # Empty telemetry is unknown coverage, not proof that incidents recovered.
        samples = len(rows)
        value = float(count) if rows else None
    else:
        observation = summarize(rows, PERFORMANCE_BY_ID[rule.metric.removesuffix(".p95")])
        samples = observation.sample_count
        value = observation.p95 if observation.coverage == 1 else None
    rule.last_value = value
    rule.last_sample_count = samples
    rule.evaluated_at = now
    if value is None or samples < rule.min_samples:
        rule.state = "INSUFFICIENT_DATA"
        return
    breached = value > rule.threshold if rule.comparison == "gt" else value < rule.threshold
    rule.state = "FIRING" if breached else "OK"
    instance: AlertInstance | None = None
    transition = ""
    if breached and rule.active_instance_id is None:
        instance = AlertInstance(
            rule_id=rule.id,
            triggered_at=now,
            trigger_value=value,
            evidence_json={
                "fromMs": from_ms,
                "toMs": to_ms,
                "sampleCount": samples,
                "metric": rule.metric,
                "threshold": rule.threshold,
            },
        )
        session.add(instance)
        await session.flush()
        rule.active_instance_id = instance.id
        transition = "FIRING"
    elif not breached and rule.active_instance_id is not None:
        instance = await session.get(AlertInstance, rule.active_instance_id)
        if instance is not None:
            instance.resolved_at = now
            transition = "RESOLVED"
        rule.active_instance_id = None
    if instance is None or not transition or rule.channel is None:
        return
    silenced = rule.silence_until is not None and as_utc(rule.silence_until) > now
    session.add(
        AlertDelivery(
            instance_id=instance.id,
            transition=transition,
            channel=rule.channel,
            status="SUPPRESSED" if silenced else "PENDING",
            next_attempt_at=now,
            payload_json={
                "instanceId": instance.id,
                "ruleId": rule.id,
                "name": rule.name,
                "tenantId": rule.tenant_id,
                "appId": rule.app_id,
                "environment": rule.environment,
                "transition": transition,
                "metric": rule.metric,
                "value": value,
                "threshold": rule.threshold,
                "sampleCount": samples,
                "fromMs": from_ms,
                "toMs": to_ms,
            },
        )
    )


async def evaluate_scope(
    session: AsyncSession,
    settings: Settings,
    principal: QueryPrincipal,
    now: datetime | None = None,
) -> int:
    """Serialize automatic and manual evaluation with the same tenant row lock."""
    # PostgreSQL locks cross-process; SQLite serializes local writers through this update.
    await session.execute(
        update(Tenant).where(Tenant.id == principal.tenant_id).values(active=Tenant.active)
    )
    rules = (
        await session.scalars(
            select(AlertRule)
            .where(
                AlertRule.tenant_id == principal.tenant_id,
                AlertRule.app_id == principal.app_id,
                AlertRule.environment == principal.environment,
                AlertRule.enabled.is_(True),
            )
            .order_by(AlertRule.id)
            .limit(ALERT_RULE_LIMIT)
        )
    ).all()
    for rule in rules:
        await evaluate_rule(session, rule, settings, now or datetime.now(UTC))
    return len(rules)


async def deliver_once(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    client: httpx.AsyncClient,
) -> bool:
    """Claim one outbox row; retries are at least once with a stable idempotency key."""
    owner = uuid.uuid4().hex
    async with factory() as session:
        now = await lease_claim_time(session)
        claimable = (
            AlertDelivery.status.in_(("PENDING", "RETRY", "SENDING")),
            AlertDelivery.next_attempt_at <= lease_write_time(session),
            or_(
                AlertDelivery.lease_expires_at.is_(None),
                AlertDelivery.lease_expires_at <= lease_write_time(session),
            ),
        )
        # A crash after the final claim must not grant another network attempt.
        exhausted = await session.execute(
            update(AlertDelivery)
            .where(*claimable, AlertDelivery.attempt_count >= settings.notification_max_attempts)
            .values(
                status="FAILED",
                last_error="RETRY_EXHAUSTED",
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        candidate = await session.scalar(
            select(AlertDelivery.id)
            .where(
                *claimable,
                AlertDelivery.attempt_count < settings.notification_max_attempts,
            )
            .order_by(AlertDelivery.id)
            .limit(1)
        )
        if candidate is None:
            await session.commit()
            return bool(exhausted.rowcount)  # type: ignore[attr-defined]
        claimed = await session.execute(
            update(AlertDelivery)
            .where(
                AlertDelivery.id == candidate,
                *claimable,
                AlertDelivery.attempt_count < settings.notification_max_attempts,
            )
            .values(
                lease_owner=owner,
                lease_expires_at=now + timedelta(seconds=DELIVERY_LEASE_SECONDS),
                status="SENDING",
                attempt_count=AlertDelivery.attempt_count + 1,
            )
            .returning(AlertDelivery)
        )
        delivery = claimed.scalar_one_or_none()
        await session.commit()
    if delivery is None:
        return False
    status, error = "FAILED", "CHANNEL_UNCONFIGURED"
    channel = configured_channels(settings).get(delivery.channel)
    if channel is not None:
        try:
            # Server-configured destinations only; never follow redirects to another host.
            async with client.stream(
                "POST",
                str(channel.url),
                json=delivery.payload_json,
                headers={**channel.headers, "Idempotency-Key": f"androidapm-alert-{delivery.id}"},
                timeout=DELIVERY_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as response:
                response_status = response.status_code
            if 200 <= response_status < 300:
                status, error = "DELIVERED", ""
            else:
                error = f"HTTP_{response_status}"
                retryable = response_status in (408, 429) or response_status >= 500
                if retryable and delivery.attempt_count < settings.notification_max_attempts:
                    status = "RETRY"
        except httpx.HTTPError:
            error = "TRANSPORT_ERROR"
            if delivery.attempt_count < settings.notification_max_attempts:
                status = "RETRY"
    async with factory() as session:
        finished_at = await lease_claim_time(session)
        await session.execute(
            update(AlertDelivery)
            .where(
                AlertDelivery.id == delivery.id,
                AlertDelivery.lease_owner == owner,
                AlertDelivery.status == "SENDING",
                AlertDelivery.lease_expires_at > lease_write_time(session),
            )
            .values(
                status=status,
                last_error=error or None,
                delivered_at=finished_at if status == "DELIVERED" else None,
                next_attempt_at=finished_at
                + timedelta(seconds=min(3600, 2**delivery.attempt_count * 10)),
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        await session.commit()
    return True


async def alert_loop() -> None:
    """Run independently of OTLP availability; preserve failing scope/outbox work."""
    settings = get_settings()
    factory = get_session_factory()
    async with httpx.AsyncClient() as client:
        while True:
            try:
                async with factory() as session:
                    scopes = (
                        await session.execute(
                            select(
                                AlertRule.tenant_id,
                                AlertRule.app_id,
                                AlertRule.environment,
                            )
                            .join(Tenant, Tenant.id == AlertRule.tenant_id)
                            .where(
                                AlertRule.enabled.is_(True),
                                Tenant.active.is_(True),
                            )
                            .distinct()
                        )
                    ).all()
                for tenant, app, environment in scopes:
                    try:
                        async with factory() as session:
                            await evaluate_scope(
                                session,
                                settings,
                                QueryPrincipal(
                                    tenant, "alert-worker", app, environment, "viewer", None
                                ),
                            )
                            await session.commit()
                    except Exception:
                        logger.warning("alert_scope_evaluation_failed", tenant_id=tenant)
                for _ in range(100):
                    if not await deliver_once(factory, settings, client):
                        break
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("alert_cycle_failed")
            await asyncio.sleep(settings.alert_poll_seconds)


def run() -> None:
    """Run the durable local/production alert evaluator until cancelled."""
    asyncio.run(alert_loop())


if __name__ == "__main__":
    run()
