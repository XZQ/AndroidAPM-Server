"""Bound unauthenticated work and move memory-hard verification off the event loop."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from androidapm_server.errors import ApiError

AUTH_HASH_WORKERS = 2
AUTH_ATTEMPTS_PER_SECOND = 20
AUTH_ATTEMPT_BURST = 40


def _busy() -> ApiError:
    """Return one safe retry response independent of credential existence."""
    return ApiError(
        429,
        "authentication_busy",
        "Authentication capacity is busy",
        True,
        headers={"Retry-After": "1"},
    )


class AuthAttemptBudget:
    """A process-wide token bucket with fixed memory and no attacker-controlled keys."""

    def __init__(
        self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        """Initialize a burst budget; peer IPs and proxy headers never widen capacity."""
        self._rate, self._burst, self._clock = rate, burst, clock
        self._tokens = float(burst)
        self._updated = clock()
        self._lock = threading.Lock()

    def consume(self) -> None:
        """Charge before credential parsing/database lookup or reject immediately."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self._burst, self._tokens + max(0, now - self._updated) * self._rate)
            self._updated = now
            if self._tokens < 1:
                raise _busy()
            self._tokens -= 1


class BoundedHashVerifier:
    """A fixed thread/memory budget with no unbounded executor queue."""

    def __init__(self, workers: int) -> None:
        """Create a lazy thread pool and one reservation per allowed hash operation."""
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="apm-auth")
        self._slots = threading.BoundedSemaphore(workers)

    async def verify(self, function: Callable[[str, str], bool], encoded: str, secret: str) -> bool:
        """Keep a slot until its actual thread exits, including cancelled HTTP requests."""
        if not self._slots.acquire(blocking=False):
            raise _busy()
        try:
            future = self._pool.submit(function, encoded, secret)
        except BaseException:
            self._slots.release()
            raise
        future.add_done_callback(lambda _future: self._slots.release())
        return await asyncio.wrap_future(future)

    def close(self) -> None:
        """Stop accepting new work; running hashes finish without blocking the event loop."""
        self._pool.shutdown(wait=False, cancel_futures=True)


AUTH_ATTEMPTS = AuthAttemptBudget(AUTH_ATTEMPTS_PER_SECOND, AUTH_ATTEMPT_BURST)
HASH_VERIFIER = BoundedHashVerifier(AUTH_HASH_WORKERS)
