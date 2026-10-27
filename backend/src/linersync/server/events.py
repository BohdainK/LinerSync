"""In-process fan-out of job changes to server-sent-event subscribers."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from linersync.server.api.schemas import JobOut

HEARTBEAT_SECONDS = 15.0
QUEUE_SIZE = 100

_RETRY = "retry: 3000\n\n"
_KEEP_ALIVE = ": keep-alive\n\n"
_RESYNC = "event: resync\ndata: {}\n\n"


class Subscription:
    def __init__(self) -> None:
        self.lagged = False
        self.closed = False
        # None is the wake-up sentinel pushed by `close`.
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(QUEUE_SIZE)

    def push(self, message: str) -> None:
        if not self._put(message):
            self.lagged = True

    def close(self) -> None:
        self.closed = True
        self._put(None)

    def _put(self, item: str | None) -> bool:
        """Queue `item`, dropping the oldest when full; False if one was dropped."""
        try:
            self._queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            self._queue.get_nowait()
            self._queue.put_nowait(item)
            return False

    async def next(self, timeout: float) -> str | None:
        """The next message, or None on timeout or once closed."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except TimeoutError:
            return None


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[Subscription] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> Subscription:
        """A subscription; already closed once the hub is closed."""
        sub = Subscription()
        if self._closed:
            sub.close()
        else:
            self._subscribers.add(sub)
        return sub

    def close(self) -> None:
        """End every open stream promptly (server shutdown); later streams end
        right away."""
        self._closed = True
        for sub in tuple(self._subscribers):
            sub.close()
        self._subscribers.clear()

    def unsubscribe(self, sub: Subscription) -> None:
        self._subscribers.discard(sub)

    def publish_job(self, job: JobOut) -> None:
        message = f"event: job\ndata: {job.model_dump_json()}\n\n"
        for sub in tuple(self._subscribers):
            sub.push(message)


async def sse_stream(
    hub: EventHub,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    sub = hub.subscribe()
    try:
        yield _RETRY
        while not sub.closed and not await is_disconnected():
            if sub.lagged:
                sub.lagged = False
                yield _RESYNC
                continue
            message = await sub.next(heartbeat)
            if sub.closed:
                break
            yield _KEEP_ALIVE if message is None else message
    finally:
        hub.unsubscribe(sub)
