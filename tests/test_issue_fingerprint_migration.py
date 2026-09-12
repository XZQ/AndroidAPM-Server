"""Verify completed historical jobs adopt canonical fingerprints and preserve raw aliases."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select

from androidapm_server.db.models import InboxEvent, SymbolizationJob, Tenant


def test_historical_symbol_fingerprints_and_downgrade(tmp_path: Path) -> None:
    path = tmp_path / "fingerprints.db"
    env = os.environ | {
        "APM_DATABASE_URL": f"sqlite+aiosqlite:///{path.as_posix()}",
        "APM_ENVIRONMENT": "test",
    }

    def migrate(*args: str) -> None:
        result = subprocess.run(  # noqa: S603 - fixed command against a fresh isolated database.
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

    migrate("upgrade", "20260912_0006")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(Tenant.__table__.insert().values(id="migration", name="Synthetic"))
            for index, status in enumerate(("symbolized", "failed")):
                result = connection.execute(
                    InboxEvent.__table__.insert().values(
                        tenant_id="migration",
                        event_id=f"event-{index}",
                        app_id="app",
                        environment="test",
                        schema_version="3",
                        sdk_version="test",
                        protocol="protobuf",
                        event_timestamp_ms=1000,
                        occurrence_timestamp_ms=1000,
                        payload_json={"module": "crash", "name": "java_crash"},
                        payload_sha256="a" * 64,
                        incident_fingerprint=str(index) * 64,
                        request_id="migration",
                    )
                )
                connection.execute(
                    SymbolizationJob.__table__.insert().values(
                        inbox_event_id=result.inserted_primary_key[0],
                        tenant_id="migration",
                        event_id=f"event-{index}",
                        job_type="java",
                        status=status,
                        input_sha256="a" * 64,
                        fingerprint_sha256="b" * 64,
                    )
                )
        migrate("upgrade", "head")
        with engine.connect() as connection:
            rows = connection.execute(
                select(
                    InboxEvent.incident_fingerprint, InboxEvent.raw_incident_fingerprint
                ).order_by(InboxEvent.id)
            ).all()
            assert rows == [("b" * 64, "0" * 64), ("1" * 64, "1" * 64)]
        migrate("downgrade", "20260912_0006")
        with engine.connect() as connection:
            assert connection.execute(
                select(InboxEvent.incident_fingerprint).order_by(InboxEvent.id)
            ).scalars().all() == ["0" * 64, "1" * 64]
        migrate("upgrade", "head")
        migrate("check")
    finally:
        engine.dispose()
