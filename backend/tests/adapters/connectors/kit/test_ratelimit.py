import asyncio

import pytest

from linersync.adapters.connectors.kit.ratelimit import RateLimiter


class FakeTime:
    """A monotonic clock that only moves when something sleeps on it."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FrozenTime:
    """A monotonic clock that never moves; sleeping is a no-op."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return 100.0

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


async def test_rate_limiter_spaces_requests() -> None:
    t = FakeTime()
    limiter = RateLimiter(2, 1, monotonic=t.monotonic, sleep=t.sleep)
    for _ in range(3):
        await limiter.acquire()
    assert t.sleeps == [0.5, 0.5]


async def test_burst_passes_without_waiting() -> None:
    t = FakeTime()
    limiter = RateLimiter(5, 10, monotonic=t.monotonic, sleep=t.sleep)
    for _ in range(10):
        await limiter.acquire()
    assert t.sleeps == []
    await limiter.acquire()
    assert t.sleeps == pytest.approx([0.2])


async def test_pause_delays_next_acquire() -> None:
    t = FakeTime()
    limiter = RateLimiter(5, 10, monotonic=t.monotonic, sleep=t.sleep)
    await limiter.acquire()
    limiter.pause_for(3.0)
    await limiter.acquire()
    assert t.sleeps == [3.0]
    await limiter.acquire()
    assert t.sleeps == [3.0]


async def test_acquire_never_spins_when_clock_is_frozen() -> None:
    # One sleep per acquire, booked at the projected time: concurrent callers
    # at the same instant are spaced out instead of all firing together.
    t = FrozenTime()
    limiter = RateLimiter(2, 1, monotonic=t.monotonic, sleep=t.sleep)
    await asyncio.gather(*(limiter.acquire() for _ in range(3)))
    assert t.sleeps == [0.5, 1.0]
