"""Regress raw DB parameters and driver messages escaping through exception logs."""

import subprocess
import sys

import pytest

from androidapm_server.config import Settings
from androidapm_server.db import session as database


def test_application_and_asgi_exception_logs_exclude_all_exception_text() -> None:
    script = """
import asyncio, logging, sys
from fastapi import Request
from sqlalchemy.exc import StatementError
from androidapm_server.errors import unhandled_error_handler
from androidapm_server.logging import configure_logging

handler = logging.StreamHandler(sys.stdout)
logging.getLogger("uvicorn").addHandler(handler)
configure_logging("INFO")

async def run():
    try:
        raise ValueError("synthetic-private-driver-value")
    except ValueError as cause:
        try:
            raise StatementError("synthetic-private-statement", "synthetic-private-sql",
                {"payload_json": "synthetic-private-raw"}, cause) from cause
        except StatementError as error:
            error.add_note("synthetic-private-note")
            response = await unhandled_error_handler(Request({"type": "http"}), error)
            assert response.status_code == 500
            assert b"synthetic-private" not in response.body
            logging.getLogger("uvicorn.error").error("ASGI failure", exc_info=True)
            logging.getLogger("worker").error("worker failure", exc_info=True)
asyncio.run(run())
"""
    result = subprocess.run(  # noqa: S603 - fixed local interpreter and synthetic fixture.
        [sys.executable, "-c", script], capture_output=True, text=True, check=True, timeout=10
    )
    assert "synthetic-private" not in result.stdout + result.stderr
    assert "StatementError" in result.stdout
    assert '"function": "run"' in result.stdout
    assert "unhandled_request_error" in result.stdout
    assert "ASGI failure" in result.stdout
    assert "worker failure" in result.stdout


async def test_production_engine_hides_bound_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    database.get_engine.cache_clear()
    monkeypatch.setattr(
        database,
        "get_settings",
        lambda: Settings(database_url="sqlite+aiosqlite:///:memory:", environment="test"),
    )
    engine = database.get_engine()
    try:
        assert engine.sync_engine.hide_parameters
    finally:
        await engine.dispose()
        database.get_engine.cache_clear()
