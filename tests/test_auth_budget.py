"""Exercise concurrency, cancellation and rate admission without wall-clock load tests."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from androidapm_server import auth, ci_auth, query_auth
from androidapm_server.auth_budget import AuthAttemptBudget, BoundedHashVerifier
from androidapm_server.errors import ApiError


async def test_hash_thread_does_not_block_loop_or_release_capacity_on_request_cancellation() -> (
    None
):
    verifier = BoundedHashVerifier(1)
    started, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def blocking_hash(_encoded: str, _secret: str) -> bool:
        loop.call_soon_threadsafe(started.set)
        release.wait(2)
        loop.call_soon_threadsafe(finished.set)
        return True

    task = asyncio.create_task(verifier.verify(blocking_hash, "synthetic", "synthetic"))
    try:
        async with asyncio.timeout(1):
            await started.wait()
        # This coroutine can progress while the hash worker is blocked.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ApiError) as caught:
            await verifier.verify(blocking_hash, "synthetic", "synthetic")
        assert caught.value.code == "authentication_busy"
        assert caught.value.headers == {"Retry-After": "1"}
    finally:
        release.set()
        async with asyncio.timeout(1):
            await finished.wait()
        verifier.close()


def test_attempt_budget_is_fixed_memory_and_recovers_with_time() -> None:
    now = 0.0
    budget = AuthAttemptBudget(1, 2, lambda: now)
    budget.consume()
    budget.consume()
    with pytest.raises(ApiError):
        budget.consume()
    now = 1.0
    budget.consume()
    with pytest.raises(ApiError):
        budget.consume()


@pytest.mark.parametrize("module", [auth, ci_auth, query_auth])
async def test_rate_admission_happens_before_database_lookup(
    module: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = AuthAttemptBudget(1, 1, lambda: 0.0)
    budget.consume()
    monkeypatch.setattr(module, "AUTH_ATTEMPTS", budget)

    async def forbidden_lookup(*_args: object) -> None:
        pytest.fail("Rate-limited requests must not reach the database")

    session = SimpleNamespace(execute=forbidden_lookup)
    with pytest.raises(ApiError) as caught:
        if module is auth:
            await auth.authenticate_ingest_key(session, "Bearer synthetic", "app", "test")
        elif module is ci_auth:
            await ci_auth.authenticate_ci_key(session, "Bearer synthetic", "artifact:write", "app")
        else:
            await query_auth.authenticate_query_key(session, "Bearer synthetic")
    assert caught.value.status_code == 429
