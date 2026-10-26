import asyncio
import time
from collections.abc import Awaitable, Callable

_EPSILON = 1e-9  # float drift from summing intervals is not a real wait


class RateLimiter:
    """Token bucket (as GCRA): `rate_per_second` sustained, up to `burst` at once.

    `acquire` computes its wait once, books the slot at the projected time and
    sleeps once. It never re-reads the clock in a loop, so an injected sleep
    that does not advance time cannot make it spin.
    """

    def __init__(
        self,
        rate_per_second: float,
        burst: int,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_second <= 0 or burst < 1:
            raise ValueError("rate_per_second must be > 0 and burst >= 1")
        self._interval = 1.0 / rate_per_second
        self._tolerance = (burst - 1) * self._interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._tat: float | None = None  # theoretical arrival time of next slot
        self._paused_until: float | None = None

    async def acquire(self) -> None:
        now = self._monotonic()
        start = now
        if self._paused_until is not None:
            start = max(start, self._paused_until)
        if self._tat is not None:
            start = max(start, self._tat - self._tolerance)
            self._tat = max(self._tat, start) + self._interval
        else:
            self._tat = start + self._interval
        wait = start - now
        if wait > _EPSILON:
            await self._sleep(wait)

    def pause_for(self, seconds: float) -> None:
        until = self._monotonic() + seconds
        if self._paused_until is None or until > self._paused_until:
            self._paused_until = until
