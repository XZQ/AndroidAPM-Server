"""Seed a bounded synthetic release scenario into an explicitly local SQLite database."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from androidapm_server.auth import generate_ingest_key
from androidapm_server.config import get_settings
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import AuditLog, IngestKey, QueryKey, Tenant
from androidapm_server.db.session import get_session_factory
from androidapm_server.domain import (
    ApmEvent,
    EventKind,
    EventPriority,
    EventSeverity,
    IdentityQuality,
    IngestMetadata,
    OccurrenceContext,
)
from androidapm_server.identity import InstallationHmacKeyRing
from androidapm_server.query_auth import generate_query_key

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = (ROOT / ".local").resolve()
TENANT_ID = "local-preview"
TENANT_NAME = "Local Preview"
APP_ID = "com.example.androidapm"
ENVIRONMENT = "production"


async def seed_local_preview() -> tuple[str, str, int, int]:
    """Create short-lived local keys and one idempotent six-hour release fixture."""
    settings = get_settings()
    _assert_local_database(settings.environment, settings.database_url.get_secret_value())
    key_ring = InstallationHmacKeyRing.parse(
        settings.installation_hmac_keys_json.get_secret_value(),
        settings.installation_hmac_active_key_version,
    )
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=1)
    query_id, query_plaintext, query_hash = generate_query_key()
    ingest_id, ingest_plaintext, ingest_hash = generate_ingest_key()
    factory = get_session_factory()
    async with factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.id == TENANT_ID))
        if tenant is None:
            session.add(Tenant(id=TENANT_ID, name=TENANT_NAME))
        elif tenant.name != TENANT_NAME or not tenant.active:
            raise RuntimeError("local preview tenant exists with incompatible state")

        session.add_all(
            [
                QueryKey(
                    key_id=query_id,
                    tenant_id=TENANT_ID,
                    key_hash=query_hash,
                    app_id=APP_ID,
                    environment=ENVIRONMENT,
                    role="investigator",
                    description="Local Web preview; expires after one day",
                    expires_at=expires_at,
                ),
                IngestKey(
                    key_id=ingest_id,
                    tenant_id=TENANT_ID,
                    key_hash=ingest_hash,
                    app_id=APP_ID,
                    environment=ENVIRONMENT,
                    description="Local Android uploader; expires after one day",
                    expires_at=expires_at,
                    requests_per_minute=600,
                    events_per_minute=30_000,
                ),
                _key_audit(query_id, "query_key.create", "query_key", expires_at),
                _key_audit(ingest_id, "ingest_key.create", "ingest_key", expires_at),
            ]
        )
        await session.flush()

        bucket_seconds = 6 * 60 * 60
        bucket_epoch = int(now.timestamp()) // bucket_seconds * bucket_seconds
        occurrence_ms = (bucket_epoch - 5 * 60) * 1_000
        bucket_id = datetime.fromtimestamp(bucket_epoch, tz=UTC).strftime("%Y%m%d%H")
        result = await insert_batch(
            session,
            _metadata(f"local-preview-{bucket_id}"),
            _fixture_events(bucket_id, occurrence_ms),
            key_ring,
        )
        compatibility = await insert_batch(
            session,
            _metadata(
                f"local-preview-v2-{bucket_id}",
                schema_version="2",
                protocol="protobuf-envelope-v2",
                app_version="2.0.0",
                installation_id=f"preview-v2-{bucket_id}",
                release_quality=IdentityQuality.BATCH_DECLARED,
                installation_quality=IdentityQuality.BATCH_DECLARED,
            ),
            [_batch_declared_event(bucket_id, occurrence_ms)],
            key_ring,
        )
        await session.commit()
    return query_plaintext, ingest_plaintext, result.inserted, compatibility.inserted


def _fixture_events(bucket_id: str, timestamp_ms: int) -> list[ApmEvent]:
    """Return an explicit synthetic scenario; values must never be presented as production data."""
    specs: list[tuple[str, str, str, dict[str, object], str, str]] = [
        (
            "baseline-health-a",
            "core",
            "sdk_health",
            {"emitCount": 100, "dropCount": 0, "dropRate": 0.0},
            "1.0.0",
            "baseline-a",
        ),
        (
            "baseline-health-b",
            "core",
            "sdk_health",
            {"emitCount": 80, "dropCount": 0, "dropRate": 0.0},
            "1.0.0",
            "baseline-b",
        ),
        (
            "baseline-crash",
            "crash",
            "java_crash",
            {"stackTrace": "at com.example.checkout.Payment.run(Payment.kt:41)"},
            "1.0.0",
            "baseline-a",
        ),
        (
            "new-health-a",
            "core",
            "sdk_health",
            {"emitCount": 120, "dropCount": 1, "dropRate": 0.0083},
            "2.0.0",
            "new-a",
        ),
        (
            "new-health-b",
            "core",
            "sdk_health",
            {"emitCount": 95, "dropCount": 0, "dropRate": 0.0},
            "2.0.0",
            "new-b",
        ),
        ("new-network", "network", "network_request", {"durationMs": 428.0}, "2.0.0", "new-b"),
        (
            "new-crash-a",
            "crash",
            "java_crash",
            {"stackTrace": "at com.example.checkout.Payment.run(Payment.kt:88)"},
            "2.0.0",
            "new-a",
        ),
        (
            "new-crash-b",
            "crash",
            "java_crash",
            {"stackTrace": "at com.example.checkout.Payment.run(Payment.kt:93)"},
            "2.0.0",
            "new-b",
        ),
        (
            "new-anr",
            "anr",
            "anr_detected",
            {"mainThreadStack": "at com.example.checkout.Cart.block(Cart.kt:120)"},
            "2.0.0",
            "new-b",
        ),
        (
            "new-normal-exit",
            "crash",
            "app_exit",
            {"reasonName": "EXIT_SELF", "exitTimestamp": timestamp_ms - 60_000},
            "2.0.0",
            "new-a",
        ),
        (
            "new-crash-exit",
            "crash",
            "app_exit",
            {"reasonName": "CRASH", "exitTimestamp": timestamp_ms - 120_000},
            "2.0.0",
            "new-b",
        ),
    ]
    return [
        _event(
            f"preview-{bucket_id}-{suffix}",
            module,
            name,
            fields,
            release,
            f"preview-{installation}",
            timestamp_ms,
        )
        for suffix, module, name, fields, release, installation in specs
    ]


def _event(
    event_id: str,
    module: str,
    name: str,
    fields: dict[str, object],
    release: str,
    installation_id: str,
    timestamp_ms: int,
) -> ApmEvent:
    """Build one V3-shaped occurrence event for the local scenario."""
    incident = (module, name) in {("crash", "java_crash"), ("anr", "anr_detected")}
    return ApmEvent(
        timestamp=timestamp_ms,
        event_id=event_id,
        module=module,
        name=name,
        kind=EventKind.ALERT if incident else EventKind.METRIC,
        severity=EventSeverity.ERROR if module in {"crash", "anr"} else EventSeverity.INFO,
        priority=EventPriority.CRITICAL if incident else EventPriority.NORMAL,
        process_name=APP_ID,
        thread_name="main",
        scene="CheckoutActivity",
        foreground=True,
        fields=fields,
        occurrence=OccurrenceContext(
            service_version=release,
            version_code=release.replace(".", ""),
            app_build=f"preview-build-{release}",
            variant="debug",
            installation_id=installation_id,
        ),
    )


def _batch_declared_event(bucket_id: str, timestamp_ms: int) -> ApmEvent:
    """Create one V2 compatibility fact that is visibly excluded from production release ratios."""
    return ApmEvent(
        timestamp=timestamp_ms,
        event_id=f"preview-{bucket_id}-v2-compatibility",
        module="crash",
        name="java_crash",
        kind=EventKind.ALERT,
        severity=EventSeverity.ERROR,
        priority=EventPriority.CRITICAL,
        process_name=APP_ID,
        thread_name="main",
        scene="LegacyUpload",
        foreground=True,
        fields={"stackTrace": "at com.example.legacy.Upload.run(Upload.kt:1)"},
    )


def _metadata(
    request_id: str,
    *,
    schema_version: str = "3",
    protocol: str = "protobuf-envelope-v3",
    app_version: str | None = None,
    installation_id: str | None = None,
    release_quality: IdentityQuality = IdentityQuality.ABSENT,
    installation_quality: IdentityQuality = IdentityQuality.ABSENT,
) -> IngestMetadata:
    """Build authenticated local metadata; V3 event identity remains occurrence-bound."""
    return IngestMetadata(
        request_id=request_id,
        tenant_id=TENANT_ID,
        app_id=APP_ID,
        environment=ENVIRONMENT,
        schema_version=schema_version,
        sdk_version="local-preview",
        app_version=app_version,
        protocol=protocol,
        release_identity_quality=release_quality,
        installation_identity_quality=installation_quality,
        installation_id=installation_id,
    )


def _key_audit(key_id: str, action: str, object_type: str, expires_at: datetime) -> AuditLog:
    """Record local credential creation without retaining the plaintext key."""
    return AuditLog(
        tenant_id=TENANT_ID,
        actor="local-preview-script",
        action=action,
        object_type=object_type,
        object_id=key_id,
        result="success",
        details_json={
            "app_id": APP_ID,
            "environment": ENVIRONMENT,
            "expires_at": expires_at.isoformat(),
            "synthetic_preview": True,
        },
    )


def _assert_local_database(environment: str, database_url: str) -> None:
    """Refuse to seed anything except the repository's ignored local SQLite directory."""
    url = make_url(database_url)
    database = url.database
    if environment.lower() != "local" or url.get_backend_name() != "sqlite" or not database:
        raise RuntimeError("local preview seeding requires APM_ENVIRONMENT=local and SQLite")
    database_path = Path(database).resolve()
    try:
        database_path.relative_to(LOCAL_ROOT)
    except ValueError as error:
        raise RuntimeError("local preview SQLite database must stay under .local") from error


def main() -> None:
    """Seed the scenario and print short-lived local credentials exactly once."""
    query_key, ingest_key, inserted, compatibility_inserted = asyncio.run(seed_local_preview())
    print("Local synthetic preview prepared.")
    print(f"New V3 fixture rows: {inserted}; new V2 compatibility rows: {compatibility_inserted}")
    print("Query key (paste into http://127.0.0.1:8080; expires in one day):")
    print(query_key)
    print("Ingest key (configure only in a local Android debug build; expires in one day):")
    print(ingest_key)


if __name__ == "__main__":
    main()
