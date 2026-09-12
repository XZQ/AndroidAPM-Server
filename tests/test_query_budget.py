"""Bound stalled database queries without returning partial statistical evidence."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from androidapm_server.db import query
from androidapm_server.errors import ApiError


async def test_driver_timeout_returns_a_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = False

    async def stalled(_statement: object) -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled = True
            raise

    monkeypatch.setattr(query, "QUERY_TIMEOUT_SECONDS", 0.01)
    session = SimpleNamespace(bind=None, execute=stalled)
    with pytest.raises(ApiError) as caught:
        await query._execute_bounded(session, select(1))
    assert caught.value.code == "query_timeout"
    assert caught.value.retryable
    assert cancelled
