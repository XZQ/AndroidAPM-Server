"""Verify schema widening, preserved long values and refusal of a lossy downgrade."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, delete, inspect
from sqlalchemy.orm import Session

from androidapm_server.db.models import ReleaseDecision, Tenant


def test_release_width_migration_roundtrip_and_loss_guard(tmp_path: Path) -> None:
    path = tmp_path / "release-width.db"
    env = os.environ | {
        "APM_DATABASE_URL": f"sqlite+aiosqlite:///{path.as_posix()}",
        "APM_ENVIRONMENT": "test",
    }

    def migrate(*args: str, success: bool = True) -> None:
        result = subprocess.run(  # noqa: S603 - fixed local migration command and isolated database.
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if success:
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0
            assert "downgrade would lose data" in result.stderr

    migrate("upgrade", "head")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        with Session(engine) as session:
            session.add(Tenant(id="migration-width", name="Synthetic"))
            session.add(
                ReleaseDecision(
                    tenant_id="migration-width",
                    app_id="app",
                    environment="test",
                    release_version="v" * 256,
                    decision="continue",
                    actor="test",
                    evidence_from_ms=1,
                    evidence_to_ms=2,
                    reason="synthetic width test",
                    evidence_json={},
                )
            )
            session.commit()
        migrate("downgrade", "20260907_0005", success=False)
        for table, names in {
            "inbox_events": ("app_version", "app_build", "variant"),
            "symbol_artifacts": ("app_build", "variant"),
            "release_decisions": ("release_version",),
        }.items():
            columns = {
                column["name"]: column["type"] for column in inspect(engine).get_columns(table)
            }
            assert all(columns[name].length == 256 for name in names)
        with Session(engine) as session:
            assert session.query(ReleaseDecision).one().release_version == "v" * 256
            session.execute(delete(ReleaseDecision))
            session.execute(delete(Tenant))
            session.commit()
        migrate("downgrade", "20260907_0005")
        migrate("upgrade", "head")
        migrate("check")
    finally:
        engine.dispose()
