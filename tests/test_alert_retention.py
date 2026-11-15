"""Alert history retention prunes resolved episodes without touching open ones."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from androidapm_server.config import Settings
from androidapm_server.db.base import Base
from androidapm_server.db.maintenance import maintain_once
from androidapm_server.db.models import (
    AlertDelivery,
    AlertInstance,
    AlertRule,
    AuditLog,
    Tenant,
)


async def _seed(factory: async_sessionmaker, now: datetime) -> dict[str, int]:
    async with factory() as session:
        session.add(Tenant(id="tenant", name="Tenant"))
        rule = AlertRule(
            tenant_id="tenant",
            app_id="app",
            environment="test",
            name="Crash",
            metric="crash.count",
            comparison="gt",
            threshold=0,
            min_samples=1,
            window_seconds=900,
        )
        session.add(rule)
        await session.flush()
        instances = {
            "old_resolved": AlertInstance(
                rule_id=rule.id,
                triggered_at=now - timedelta(days=120),
                resolved_at=now - timedelta(days=100),
                trigger_value=3,
                evidence_json={},
            ),
            "recent_resolved": AlertInstance(
                rule_id=rule.id,
                triggered_at=now - timedelta(days=10),
                resolved_at=now - timedelta(days=5),
                trigger_value=2,
                evidence_json={},
            ),
            "old_open": AlertInstance(
                rule_id=rule.id,
                triggered_at=now - timedelta(days=200),
                resolved_at=None,
                trigger_value=5,
                evidence_json={},
            ),
        }
        session.add_all(instances.values())
        await session.flush()
        ids = {name: instance.id for name, instance in instances.items()}
        for instance in instances.values():
            session.add(
                AlertDelivery(
                    instance_id=instance.id,
                    transition="FIRING",
                    channel="ops",
                    payload_json={},
                    status="DELIVERED",
                )
            )
        rule.active_instance_id = ids["old_open"]
        await session.commit()
    return ids


async def test_retention_prunes_only_old_resolved_episodes_and_their_outbox() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    ids = await _seed(factory, now)
    settings = Settings(_env_file=None, alert_history_retention_days=90)

    await maintain_once(factory, settings, now)

    async with factory() as session:
        remaining = set((await session.scalars(select(AlertInstance.id))).all())
        deliveries = set((await session.scalars(select(AlertDelivery.instance_id))).all())
        audits = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "alert.history.prune")
            )
        ).all()
    assert ids["old_resolved"] not in remaining
    assert ids["recent_resolved"] in remaining
    assert ids["old_open"] in remaining
    assert ids["old_resolved"] not in deliveries
    assert ids["recent_resolved"] in deliveries
    assert ids["old_open"] in deliveries
    assert len(audits) == 1
    assert audits[0].tenant_id == "tenant"
    assert audits[0].details_json["count"] == 1

    # A second pass is a no-op: nothing else aged past the retention window.
    await maintain_once(factory, settings, now)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(AlertInstance)) == 2
    await engine.dispose()


async def test_retention_disabled_keeps_all_alert_history() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    await _seed(factory, now)
    settings = Settings(_env_file=None, retention_enabled=False)
    await maintain_once(factory, settings, now)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(AlertInstance)) == 3
    await engine.dispose()
