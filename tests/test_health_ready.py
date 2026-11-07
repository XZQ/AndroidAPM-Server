"""Readiness must prove the migrated durable inbox schema, not only a live process."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from androidapm_server import __version__
from androidapm_server.api.health import live, ready


async def test_liveness_reports_the_application_version_without_dependencies() -> None:
    assert await live() == {"status": "ok", "version": __version__}


async def test_readiness_requires_the_migrated_inbox_table() -> None:
    session = AsyncMock()
    assert await ready(session) == {"status": "ready"}
    statement = str(session.execute.await_args.args[0])
    assert "inbox_events" in statement
    assert "LIMIT 1" in statement


async def test_readiness_fails_when_the_schema_query_fails() -> None:
    session = AsyncMock()
    session.execute.side_effect = RuntimeError("relation does not exist")
    with pytest.raises(RuntimeError, match="relation does not exist"):
        await ready(session)
