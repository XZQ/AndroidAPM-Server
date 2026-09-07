"""Validate backlog truth, missing snapshots, and metrics from a separate process."""

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
from prometheus_client import generate_latest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from androidapm_server.db.base import Base
from androidapm_server.db.inbox import insert_batch
from androidapm_server.db.models import InboxEvent
from androidapm_server.domain import ApmEvent, IngestMetadata
from androidapm_server.observability import refresh_inbox_metrics


async def test_database_metrics_distinguish_due_expired_waiting_and_failed_reads() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    metadata = IngestMetadata(
        request_id="test",
        tenant_id="private-tenant",
        app_id="app",
        environment="test",
        schema_version="1",
        sdk_version="test",
        protocol="line",
    )
    try:
        async with factory() as session:
            assert await refresh_inbox_metrics(session, now)
            assert "androidapm_inbox_pending 0.0" in generate_latest().decode()
            for i, status in enumerate(
                ["pending", "processing", "processing", "awaiting_symbols", "dead_letter"]
            ):
                await insert_batch(
                    session,
                    metadata,
                    [
                        ApmEvent(
                            event_id=str(i),
                            timestamp=1000,
                            module="core",
                            name="sdk_health",
                            kind="METRIC",
                            severity="INFO",
                            priority="NORMAL",
                            process_name="app",
                            thread_name="main",
                        )
                    ],
                )
                await session.execute(
                    update(InboxEvent)
                    .where(InboxEvent.event_id == str(i))
                    .values(
                        status=status,
                        received_at=now - timedelta(seconds=120 - i),
                        next_attempt_at=now - timedelta(seconds=1),
                        lease_expires_at=now + timedelta(seconds=10 if i == 1 else -10),
                    )
                )
            await session.commit()
            assert await refresh_inbox_metrics(session, now)
            metrics = generate_latest().decode()
            assert "androidapm_inbox_pending 2.0" in metrics
            assert "androidapm_inbox_oldest_seconds 120.0" in metrics
            assert 'androidapm_inbox_rows{status="awaiting_symbols"} 1.0' in metrics
            assert "private-tenant" not in metrics

        async def failed(_statement: object) -> None:
            raise RuntimeError("synthetic DB unavailable")

        assert not await refresh_inbox_metrics(SimpleNamespace(bind=None, execute=failed), now)
        metrics = generate_latest().decode()
        assert "androidapm_inbox_snapshot_available 0.0" in metrics
        assert "androidapm_inbox_pending NaN" in metrics
    finally:
        await engine.dispose()


def test_separate_worker_process_exposes_its_own_counter() -> None:
    script = """
from prometheus_client import start_http_server
from androidapm_server.metrics import EXPORT_EVENTS
server, thread = start_http_server(0, addr='127.0.0.1')
EXPORT_EVENTS.labels('delivered').inc(7)
print(server.server_port, flush=True)
input()
server.shutdown()
server.server_close()
"""
    with subprocess.Popen(  # noqa: S603 - fixed interpreter and synthetic inline fixture.
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout is not None
        port = int(process.stdout.readline().strip())
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/metrics", timeout=5, trust_env=False)
            assert response.status_code == 200
            assert 'androidapm_export_events_total{result="delivered"} 7.0' in response.text
        finally:
            process.communicate("\n", timeout=5)
        assert process.returncode == 0
