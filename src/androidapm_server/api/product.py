"""Performance, alert lifecycle, and fixed-scope administration endpoints."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from androidapm_server.alerts import (
    ALERT_RULE_LIMIT,
    AlertRuleInput,
    alert_metric_catalog,
    as_utc,
    configured_channels,
    evaluate_scope,
    rule_json,
)
from androidapm_server.api.query import _audit, _reject_scope_parameters, _validate_window
from androidapm_server.auth import generate_ingest_key
from androidapm_server.auth_budget import HASH_VERIFIER
from androidapm_server.ci_auth import generate_ci_key
from androidapm_server.config import Settings, get_settings
from androidapm_server.db.models import (
    AlertDelivery,
    AlertInstance,
    AlertRule,
    AppSettings,
    AuditLog,
    CiKey,
    InboxEvent,
    IngestKey,
    QueryKey,
    RemoteConfigVersion,
    SymbolArtifact,
    SymbolizationJob,
    Tenant,
)
from androidapm_server.db.query import _execute_bounded
from androidapm_server.db.session import get_session
from androidapm_server.errors import ApiError
from androidapm_server.performance import PerformanceResponse, build_performance, load_observations
from androidapm_server.performance_catalog import PERFORMANCE_BY_ID
from androidapm_server.query import QueryModel, scope_for
from androidapm_server.query_auth import QueryPrincipal, generate_query_key, require_admin
from androidapm_server.remote_config import sign_config
from androidapm_server.web_auth import authenticate_query_request

router = APIRouter(tags=["product"])
Session = Annotated[AsyncSession, Depends(get_session)]
Configuration = Annotated[Settings, Depends(get_settings)]
MANAGEMENT_LIST_LIMIT = 100
KEY_LIMIT = 200
REMOTE_CONFIG_MAX_BYTES = 16_384
KEY_MODELS: dict[str, type[QueryKey] | type[IngestKey] | type[CiKey]] = {
    "query": QueryKey,
    "ingest": IngestKey,
    "ci": CiKey,
}


def _date(value: datetime | None) -> datetime | None:
    return as_utc(value) if value is not None else None


async def _principal(
    request: Request,
    response: Response,
    session: AsyncSession,
    settings: Settings,
    *,
    admin: bool = False,
) -> QueryPrincipal:
    _reject_scope_parameters(request)
    principal = await authenticate_query_request(
        session, request, settings, unsafe=request.method != "GET"
    )
    if admin:
        require_admin(principal)
    response.headers["Cache-Control"] = "private, no-store"
    return principal


def _scope(model: Any, principal: QueryPrincipal) -> tuple[Any, ...]:
    return (
        model.tenant_id == principal.tenant_id,
        model.app_id == principal.app_id,
        model.environment == principal.environment,
    )


async def _tenant_lock(session: AsyncSession, principal: QueryPrincipal) -> None:
    await session.execute(
        update(Tenant).where(Tenant.id == principal.tenant_id).values(active=Tenant.active)
    )


def _record(
    session: AsyncSession,
    principal: QueryPrincipal,
    request: Request,
    action: str,
    object_type: str,
    object_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        _audit(
            principal,
            request.state.request_id,
            action,
            object_type,
            object_id,
            "success",
            details or {},
        )
    )


@router.get("/v1/query/performance", response_model=PerformanceResponse)
async def get_performance(
    request: Request,
    response: Response,
    session: Session,
    settings: Configuration,
    new_release: Annotated[str, Query(alias="newRelease", min_length=1, max_length=256)],
    baseline_release: Annotated[str, Query(alias="baselineRelease", min_length=1, max_length=256)],
    from_ms: Annotated[int, Query(alias="fromMs")],
    to_ms: Annotated[int, Query(alias="toMs")],
    metric: str = "launch.cold_start.launchDurationMs",
) -> PerformanceResponse:
    """Return exact observation distributions, trends, and release deltas."""
    principal = await _principal(request, response, session, settings)
    _validate_window(from_ms, to_ms, settings)
    if new_release == baseline_release:
        raise ApiError(400, "invalid_release_comparison", "The two releases must be different")
    if metric not in PERFORMANCE_BY_ID:
        raise ApiError(400, "invalid_performance_metric", "Select a registered metric")
    rows = await load_observations(
        session,
        principal,
        settings,
        from_ms,
        to_ms,
        (new_release, baseline_release),
        performance_only=True,
    )
    return build_performance(
        rows,
        principal,
        request.state.request_id,
        new_release,
        baseline_release,
        from_ms,
        to_ms,
        PERFORMANCE_BY_ID[metric],
        settings.query_late_after_seconds * 1000,
    )


@router.get("/v1/alerts")
async def get_alerts(
    request: Request, response: Response, session: Session, settings: Configuration
) -> dict[str, Any]:
    """Read scoped rules and the latest 100 episodes and delivery statuses."""
    principal = await _principal(request, response, session, settings)
    rules = (
        await _execute_bounded(
            session,
            select(AlertRule)
            .where(*_scope(AlertRule, principal))
            .order_by(AlertRule.id)
            .limit(ALERT_RULE_LIMIT),
        )
    ).scalars()
    instances = (
        (
            await _execute_bounded(
                session,
                select(AlertInstance)
                .join(AlertRule, AlertRule.id == AlertInstance.rule_id)
                .where(*_scope(AlertRule, principal))
                .order_by(AlertInstance.id.desc())
                .limit(MANAGEMENT_LIST_LIMIT),
            )
        )
        .scalars()
        .all()
    )
    deliveries = (
        (
            await _execute_bounded(
                session,
                select(AlertDelivery)
                .join(AlertInstance, AlertInstance.id == AlertDelivery.instance_id)
                .join(AlertRule, AlertRule.id == AlertInstance.rule_id)
                .where(*_scope(AlertRule, principal))
                .order_by(AlertDelivery.id.desc())
                .limit(MANAGEMENT_LIST_LIMIT),
            )
        )
        .scalars()
        .all()
    )
    return {
        "rules": [rule_json(rule) for rule in rules],
        "metrics": alert_metric_catalog(),
        "channels": list(configured_channels(settings)),
        "historyLimit": MANAGEMENT_LIST_LIMIT,
        "instances": [
            {
                "id": item.id,
                "ruleId": item.rule_id,
                "triggeredAt": _date(item.triggered_at),
                "resolvedAt": _date(item.resolved_at),
                "acknowledgedAt": _date(item.acknowledged_at),
                "triggerValue": item.trigger_value,
                "evidence": item.evidence_json,
            }
            for item in instances
        ],
        "deliveries": [
            {
                "id": item.id,
                "instanceId": item.instance_id,
                "transition": item.transition,
                "channel": item.channel,
                "status": item.status,
                "attemptCount": item.attempt_count,
                "lastError": item.last_error,
                "deliveredAt": _date(item.delivered_at),
            }
            for item in deliveries
        ],
    }


@router.post("/v1/alerts/rules", status_code=201)
async def create_rule(
    input: AlertRuleInput,
    request: Request,
    response: Response,
    session: Session,
    settings: Configuration,
) -> dict[str, Any]:
    """Create a bounded rule under a separately provisioned admin credential."""
    principal = await _principal(request, response, session, settings, admin=True)
    _validate_channel(input, settings)
    await _tenant_lock(session, principal)
    count = await session.scalar(
        select(func.count()).select_from(AlertRule).where(*_scope(AlertRule, principal))
    )
    if count is not None and count >= ALERT_RULE_LIMIT:
        raise ApiError(422, "alert_rule_limit", "The scoped rule limit is reached")
    rule = AlertRule(
        tenant_id=principal.tenant_id,
        app_id=principal.app_id,
        environment=principal.environment,
        **input.model_dump(),
    )
    session.add(rule)
    await session.flush()
    _record(session, principal, request, "alert.rule.create", "alert_rule", str(rule.id))
    await session.commit()
    return rule_json(rule)


def _validate_channel(input: AlertRuleInput, settings: Settings) -> None:
    if input.channel is not None and input.channel not in configured_channels(settings):
        raise ApiError(422, "channel_unconfigured", "Select a server-configured channel")


async def _rule(session: AsyncSession, principal: QueryPrincipal, rule_id: int) -> AlertRule:
    rule = await session.scalar(
        select(AlertRule).where(*_scope(AlertRule, principal), AlertRule.id == rule_id)
    )
    if rule is None:
        raise ApiError(404, "alert_rule_not_found", "The alert rule was not found")
    return rule


@router.put("/v1/alerts/rules/{rule_id}")
async def edit_rule(
    rule_id: int,
    input: AlertRuleInput,
    request: Request,
    response: Response,
    session: Session,
    settings: Configuration,
) -> dict[str, Any]:
    """Edit a rule only after closing its existing episode under its original contract."""
    principal = await _principal(request, response, session, settings, admin=True)
    _validate_channel(input, settings)
    await _tenant_lock(session, principal)
    rule = await _rule(session, principal, rule_id)
    changes = input.model_dump()
    if rule.active_instance_id is not None and any(
        getattr(rule, key) != value for key, value in changes.items() if key != "enabled"
    ):
        raise ApiError(409, "active_alert_episode", "Wait for recovery before editing this rule")
    for key, value in changes.items():
        setattr(rule, key, value)
    if rule.active_instance_id is None:
        rule.state, rule.last_value, rule.evaluated_at = "NO_DATA", None, None
    _record(session, principal, request, "alert.rule.update", "alert_rule", str(rule.id))
    await session.commit()
    return rule_json(rule)


class SilenceInput(QueryModel):
    """Set or clear bounded notification silence without stopping measurement."""

    minutes: int = Field(ge=0, le=10_080)


@router.post("/v1/alerts/rules/{rule_id}/silence")
async def silence_rule(
    rule_id: int,
    input: SilenceInput,
    request: Request,
    response: Response,
    session: Session,
    settings: Configuration,
) -> dict[str, Any]:
    """Suppress new transitions temporarily; record the chosen duration in audit."""
    principal = await _principal(request, response, session, settings, admin=True)
    await _tenant_lock(session, principal)
    rule = await _rule(session, principal, rule_id)
    rule.silence_until = (
        datetime.now(UTC) + timedelta(minutes=input.minutes) if input.minutes else None
    )
    _record(
        session,
        principal,
        request,
        "alert.rule.silence",
        "alert_rule",
        str(rule.id),
        {"minutes": input.minutes},
    )
    await session.commit()
    return rule_json(rule)


@router.post("/v1/alerts/evaluate")
async def evaluate_alerts(
    request: Request, response: Response, session: Session, settings: Configuration
) -> dict[str, int]:
    """Run the same persisted evaluator manually, with CSRF and an audit trail."""
    principal = await _principal(request, response, session, settings, admin=True)
    count = await evaluate_scope(session, settings, principal)
    _record(session, principal, request, "alert.evaluate", "alert_scope", principal.app_id)
    await session.commit()
    return {"evaluated": count}


@router.post("/v1/alerts/instances/{instance_id}/acknowledge")
async def acknowledge(
    instance_id: int,
    request: Request,
    response: Response,
    session: Session,
    settings: Configuration,
) -> dict[str, bool]:
    """Acknowledge one episode in the authenticated scope, idempotently."""
    principal = await _principal(request, response, session, settings, admin=True)
    await _tenant_lock(session, principal)
    instance = await session.scalar(
        select(AlertInstance)
        .join(AlertRule, AlertRule.id == AlertInstance.rule_id)
        .where(*_scope(AlertRule, principal), AlertInstance.id == instance_id)
    )
    if instance is None:
        raise ApiError(404, "alert_instance_not_found", "The alert instance was not found")
    if instance.acknowledged_at is None:
        instance.acknowledged_at = datetime.now(UTC)
        instance.acknowledged_by = principal.key_id
        _record(
            session, principal, request, "alert.acknowledge", "alert_instance", str(instance.id)
        )
    await session.commit()
    return {"acknowledged": True}


class AppSettingsInput(QueryModel):
    """Editable display name; it never changes authorization scope."""

    display_name: str = Field(min_length=1, max_length=128)


@router.put("/v1/manage/settings")
async def update_settings(
    input: AppSettingsInput,
    request: Request,
    response: Response,
    session: Session,
    settings: Configuration,
) -> dict[str, str]:
    """Persist an application's display name and its authoring identity."""
    principal = await _principal(request, response, session, settings, admin=True)
    await _tenant_lock(session, principal)
    row = await session.scalar(select(AppSettings).where(*_scope(AppSettings, principal)))
    if row is None:
        row = AppSettings(
            tenant_id=principal.tenant_id,
            app_id=principal.app_id,
            environment=principal.environment,
            display_name=input.display_name,
        )
        session.add(row)
    else:
        row.display_name = input.display_name
    _record(session, principal, request, "app.settings.update", "app", principal.app_id)
    await session.commit()
    return {"displayName": row.display_name}


@router.get("/v1/manage/settings")
async def get_settings_state(
    request: Request, response: Response, session: Session, settings: Configuration
) -> dict[str, Any]:
    """Expose effective operations and scoped metadata without server or key secrets."""
    principal = await _principal(request, response, session, settings)
    display = await session.scalar(select(AppSettings).where(*_scope(AppSettings, principal)))
    pipeline = (
        await _execute_bounded(
            session,
            select(InboxEvent.status, func.count())
            .where(*_scope(InboxEvent, principal))
            .group_by(InboxEvent.status),
        )
    ).all()
    artifacts = (
        (
            await _execute_bounded(
                session,
                select(SymbolArtifact)
                .where(
                    SymbolArtifact.tenant_id == principal.tenant_id,
                    SymbolArtifact.app_id == principal.app_id,
                )
                .order_by(SymbolArtifact.id.desc())
                .limit(MANAGEMENT_LIST_LIMIT),
            )
        )
        .scalars()
        .all()
    )
    jobs = (
        await _execute_bounded(
            session,
            select(SymbolizationJob.status, func.count())
            .join(InboxEvent, InboxEvent.id == SymbolizationJob.inbox_event_id)
            .where(*_scope(InboxEvent, principal))
            .group_by(SymbolizationJob.status),
        )
    ).all()
    return {
        "scope": scope_for(principal).model_dump(by_alias=True),
        "displayName": display.display_name if display else principal.app_id,
        "role": principal.role,
        "pipeline": {status: count for status, count in pipeline},
        "symbolJobs": {status: count for status, count in jobs},
        "operations": {
            "ingestEnabled": settings.ingest_enabled,
            "exportEnabled": settings.export_enabled,
            "metricsEnabled": settings.otlp_metrics_enabled,
            "tracesEnabled": settings.otlp_traces_enabled,
            "symbolizationEnabled": settings.symbolization_enabled,
            "retentionEnabled": settings.retention_enabled,
            "deliveredRetentionDays": settings.delivered_retention_days,
            "deadLetterRetentionDays": settings.dead_letter_retention_days,
            "queryMaxRows": settings.query_max_rows,
            "alertPollSeconds": settings.alert_poll_seconds,
        },
        "artifacts": [
            {
                "id": item.id,
                "type": item.artifact_type,
                "versionCode": item.version_code,
                "appBuild": item.app_build,
                "variant": item.variant,
                "abi": item.abi,
                "buildId": item.build_id,
                "sha256": item.checksum_sha256,
                "status": item.status,
            }
            for item in artifacts
        ],
    }


class KeyInput(QueryModel):
    """One-time scoped key creation; only an admin can provision new admins."""

    kind: Literal["query", "ingest", "ci"]
    role: Literal["viewer", "investigator", "admin"] = "viewer"
    description: str = Field(min_length=1, max_length=512)
    expires_days: int = Field(default=30, ge=1, le=365)


@router.get("/v1/manage/keys")
async def list_keys(
    request: Request, response: Response, session: Session, settings: Configuration
) -> dict[str, Any]:
    """List scoped credential lifecycle metadata; stored hashes never leave storage."""
    principal = await _principal(request, response, session, settings, admin=True)
    items: list[dict[str, Any]] = []
    for kind, model in (("query", QueryKey), ("ingest", IngestKey), ("ci", CiKey)):
        predicates = (
            _scope(model, principal)
            if kind != "ci"
            else (
                model.tenant_id == principal.tenant_id,
                model.app_id == principal.app_id,
            )
        )
        keys = (
            (
                await _execute_bounded(
                    session,
                    select(model)
                    .where(*predicates)
                    .order_by(model.created_at.desc())
                    .limit(KEY_LIMIT),
                )
            )
            .scalars()
            .all()
        )
        items.extend(
            {
                "kind": kind,
                "id": item.key_id,
                "description": item.description,
                "role": item.role if isinstance(item, QueryKey) else None,
                "active": item.active,
                "expiresAt": _date(item.expires_at),
                "revokedAt": _date(item.revoked_at),
                "createdAt": _date(item.created_at),
                "current": kind == "query" and item.key_id == principal.key_id,
            }
            for item in keys
        )
    return {"items": items, "limitPerKind": KEY_LIMIT}


@router.post("/v1/manage/keys", status_code=201)
async def create_key(
    input: KeyInput, request: Request, response: Response, session: Session, settings: Configuration
) -> dict[str, str]:
    """Show a secret exactly once after durable scoped hash and audit commit."""
    principal = await _principal(request, response, session, settings, admin=True)
    await _tenant_lock(session, principal)
    model = KEY_MODELS[input.kind]
    predicates = (
        _scope(model, principal)
        if input.kind != "ci"
        else (
            model.tenant_id == principal.tenant_id,
            model.app_id == principal.app_id,
        )
    )
    count = await session.scalar(
        select(func.count()).select_from(model).where(*predicates, model.active.is_(True))
    )
    if count is not None and count >= KEY_LIMIT:
        raise ApiError(422, "key_limit", "Revoke unused keys before creating another")
    generate = {"query": generate_query_key, "ingest": generate_ingest_key, "ci": generate_ci_key}
    key_id, plaintext, key_hash = await HASH_VERIFIER.run(generate[input.kind])
    common: dict[str, Any] = {
        "key_id": key_id,
        "key_hash": key_hash,
        "tenant_id": principal.tenant_id,
        "app_id": principal.app_id,
        "description": input.description,
        "expires_at": datetime.now(UTC) + timedelta(days=input.expires_days),
    }
    if input.kind == "ci":
        common["scopes_json"] = ["artifact:write"]
    else:
        common["environment"] = principal.environment
        if input.kind == "query":
            common["role"] = input.role
    session.add(model(**common))
    _record(
        session,
        principal,
        request,
        "credential.create",
        f"{input.kind}_key",
        key_id,
        {"role": input.role if input.kind == "query" else None},
    )
    await session.commit()
    return {"id": key_id, "secret": plaintext}


@router.post("/v1/manage/keys/{kind}/{key_id}/revoke")
async def revoke_key(
    kind: Literal["query", "ingest", "ci"],
    key_id: str,
    request: Request,
    response: Response,
    session: Session,
    settings: Configuration,
) -> dict[str, bool]:
    """Revoke a scoped key; current admin sessions require rotating to another key first."""
    principal = await _principal(request, response, session, settings, admin=True)
    if kind == "query" and key_id == principal.key_id:
        raise ApiError(409, "current_key_revoke", "Sign in with another admin key before revoking")
    await _tenant_lock(session, principal)
    model = KEY_MODELS[kind]
    predicates = (
        _scope(model, principal)
        if kind != "ci"
        else (
            model.tenant_id == principal.tenant_id,
            model.app_id == principal.app_id,
        )
    )
    row = cast(
        QueryKey | IngestKey | CiKey | None,
        await session.scalar(select(model).where(*predicates, model.key_id == key_id)),
    )
    if row is None:
        raise ApiError(404, "credential_not_found", "The scoped credential was not found")
    row.active, row.revoked_at = False, datetime.now(UTC)
    _record(session, principal, request, "credential.revoke", f"{kind}_key", key_id)
    await session.commit()
    return {"revoked": True}


class ConfigInput(QueryModel):
    """Bounded configuration publication or rollback as a new signed revision."""

    payload: dict[str, Any] = Field(default_factory=dict)
    source_revision: int | None = Field(default=None, ge=1)
    expires_hours: int = Field(default=24, ge=1, le=720)
    rollout_basis_points: int = Field(default=10_000, ge=0, le=10_000)


@router.get("/v1/manage/configs")
async def list_configs(
    request: Request, response: Response, session: Session, settings: Configuration
) -> dict[str, Any]:
    """Read immutable scoped configuration history and public verification material."""
    principal = await _principal(request, response, session, settings, admin=True)
    rows = (
        (
            await _execute_bounded(
                session,
                select(RemoteConfigVersion)
                .where(*_scope(RemoteConfigVersion, principal))
                .order_by(RemoteConfigVersion.revision.desc())
                .limit(MANAGEMENT_LIST_LIMIT),
            )
        )
        .scalars()
        .all()
    )
    public_key: str | None = None
    if settings.remote_config_signing_private_key_b64 is not None:
        try:
            key = Ed25519PrivateKey.from_private_bytes(
                base64.b64decode(
                    settings.remote_config_signing_private_key_b64.get_secret_value(), validate=True
                )
            )
            public_key = base64.b64encode(
                key.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
            ).decode()
        except ValueError as error:
            raise ApiError(
                503, "config_signing_unconfigured", "Configure an Ed25519 signing key"
            ) from error
    return {
        "signingConfigured": public_key is not None,
        "publicKey": public_key,
        "keyId": settings.remote_config_public_key_id,
        "items": [
            {
                "revision": row.revision,
                "payload": row.payload_json,
                "issuedAt": _date(row.issued_at),
                "expiresAt": _date(row.expires_at),
                "rolloutBasisPoints": row.rollout_basis_points,
                "active": row.active,
            }
            for row in rows
        ],
    }


@router.post("/v1/manage/configs", status_code=201)
async def publish_config(
    input: ConfigInput,
    request: Request,
    response: Response,
    session: Session,
    settings: Configuration,
) -> dict[str, int]:
    """Publish/rollback monotonically under the shared tenant lock; audit no payloads."""
    principal = await _principal(request, response, session, settings, admin=True)
    private = settings.remote_config_signing_private_key_b64
    if private is None:
        raise ApiError(503, "config_signing_unconfigured", "Configure an Ed25519 signing key")
    await _tenant_lock(session, principal)
    payload = input.payload
    if input.source_revision is not None:
        source = await session.scalar(
            select(RemoteConfigVersion).where(
                *_scope(RemoteConfigVersion, principal),
                RemoteConfigVersion.revision == input.source_revision,
            )
        )
        if source is None:
            raise ApiError(404, "config_not_found", "The scoped source revision was not found")
        payload = source.payload_json
    try:
        encoded = json.dumps(payload, allow_nan=False, ensure_ascii=False)
    except (ValueError, TypeError) as error:
        raise ApiError(422, "config_payload_invalid", "Use a finite JSON object") from error
    if len(encoded.encode()) > REMOTE_CONFIG_MAX_BYTES:
        raise ApiError(422, "config_payload_limit", "Configuration payload exceeds 16 KiB")
    latest = await session.scalar(
        select(func.max(RemoteConfigVersion.revision)).where(
            *_scope(RemoteConfigVersion, principal)
        )
    )
    revision = (latest or 0) + 1
    now = datetime.now(UTC)
    try:
        signed = sign_config(
            private.get_secret_value(),
            revision,
            now,
            now + timedelta(hours=input.expires_hours),
            input.rollout_basis_points,
            payload,
            settings.remote_config_public_key_id,
        )
    except ValueError as error:
        raise ApiError(
            503, "config_signing_unconfigured", "Configure an Ed25519 signing key"
        ) from error
    session.add(
        RemoteConfigVersion(
            tenant_id=principal.tenant_id,
            app_id=principal.app_id,
            environment=principal.environment,
            revision=revision,
            payload_json=payload,
            issued_at=signed.issued_at,
            expires_at=signed.expires_at,
            rollout_basis_points=signed.rollout_basis_points,
            key_id=signed.key_id,
            signature_b64=signed.signature_b64,
            created_by=f"query-key:{principal.key_id}",
        )
    )
    _record(
        session,
        principal,
        request,
        "config.publish",
        "remote_config",
        str(revision),
        {
            "source_revision": input.source_revision,
            "rollout_basis_points": input.rollout_basis_points,
        },
    )
    await session.commit()
    return {"revision": revision}


@router.get("/v1/manage/audit")
async def list_audit(
    request: Request, response: Response, session: Session, settings: Configuration
) -> dict[str, Any]:
    """Expose scoped audit metadata with bounded history and no arbitrary detail values."""
    principal = await _principal(request, response, session, settings, admin=True)
    rows = (
        (
            await _execute_bounded(
                session,
                select(AuditLog)
                .where(
                    AuditLog.tenant_id == principal.tenant_id,
                    AuditLog.details_json["app_id"].as_string() == principal.app_id,
                    or_(
                        AuditLog.details_json["environment"].as_string() == principal.environment,
                        # App-scoped credential actions (CI keys) carry no environment;
                        # they belong to every scoped admin of the same app.
                        AuditLog.details_json["environment"].as_string().is_(None),
                    ),
                )
                .order_by(AuditLog.id.desc())
                .limit(MANAGEMENT_LIST_LIMIT),
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "action": row.action,
                "actor": row.actor,
                "objectType": row.object_type,
                "objectId": row.object_id,
                "result": row.result,
                "createdAt": _date(row.created_at),
                "requestId": row.request_id,
            }
            for row in rows
        ]
    }
