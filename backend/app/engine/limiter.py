"""Token-bucket rate limiter for Exa calls (plan §4.3: 5 QPS default).

One process-local bucket shared by every search of a run. ``rate`` tokens
per second with a one-second burst capacity; ``acquire`` sleeps only the
deficit. The clock and sleep are injectable so tests never wait.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

_EPSILON = 1e-9


class RateLimiter:
    """Async token bucket: at most ``rate`` acquisitions per second."""

    def __init__(
        self,
        rate: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = float(rate)
        self._capacity = float(rate)
        self._tokens = float(rate)
        self._last = monotonic()
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Take one token, sleeping for the deficit when the bucket is empty."""
        async with self._lock:
            while True:
                now = self._monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                # Epsilon tolerance: a refill that lands a float hair below
                # 1.0 would otherwise sleep ~0 forever.
                if self._tokens >= 1.0 - _EPSILON:
                    self._tokens = max(0.0, self._tokens - 1.0)
                    return
                await self._sleep((1.0 - self._tokens) / self._rate)
