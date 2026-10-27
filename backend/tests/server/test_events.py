import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from linersync.adapters.memory.jobs import InMemoryJobs, InMemoryRunContexts
from linersync.application.jobs import JobService
from linersync.domain.jobs import Job, JobStatus, SyncJob
from linersync.domain.runs import Trigger
from linersync.server.api.schemas import JobOut
from linersync.server.events import EventHub, sse_stream
from tests.application.conftest import World
from tests.application.conftest import world as world
from tests.application.test_jobs import FakeEnv
from tests.server.conftest import AppFactory


def _job(job_id: str) -> JobOut:
    return JobOut.of(
        Job(
            job_id=job_id,
            request=SyncJob("s1", Trigger.MANUAL),
            status=JobStatus.QUEUED,
            label="Mix",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )


async def _never() -> bool:
    return False


async def _take(stream: AsyncIterator[str], count: int) -> list[str]:
    out: list[str] = []
    async for chunk in stream:
        out.append(chunk)
        if len(out) == count:
            break
    await stream.aclose()  # type: ignore[attr-defined]
    return out


async def test_job_change_reaches_subscriber(world: World) -> None:
    hub = EventHub()
    service = JobService(
        InMemoryJobs(),
        InMemoryRunContexts(),
        world.repos,
        FakeEnv(world),
        world.clock,
        world.ids,
        lambda job: hub.publish_job(JobOut.of(job)),
    )
    stream = sse_stream(hub, _never)
    assert await anext(stream) == "retry: 3000\n\n"
    job = service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
    chunk = await anext(stream)
    await stream.aclose()  # type: ignore[attr-defined]
    head, data = chunk.split("\n", 1)
    assert head == "event: job"
    assert json.loads(data.removeprefix("data: "))["job_id"] == job.job_id


async def test_heartbeat_when_idle() -> None:
    chunks = await _take(sse_stream(EventHub(), _never, heartbeat=0.01), 3)
    assert chunks == ["retry: 3000\n\n", ": keep-alive\n\n", ": keep-alive\n\n"]


async def test_slow_subscriber_gets_resync_and_others_unaffected() -> None:
    hub = EventHub()
    slow = hub.subscribe()
    fast = hub.subscribe()
    for i in range(101):
        hub.publish_job(_job(f"j{i}"))
        if i < 100:
            assert await fast.next(0.1) is not None
    assert slow.lagged and not fast.lagged
    assert await fast.next(0.01) is not None  # the 101st
    assert await fast.next(0.01) is None
    first = await slow.next(0.1)
    assert first is not None and '"j1"' in first  # j0 was dropped


async def test_resync_is_emitted_once_after_lag() -> None:
    hub = EventHub()
    stream = sse_stream(hub, _never, heartbeat=0.01)
    assert await anext(stream) == "retry: 3000\n\n"
    for i in range(101):
        hub.publish_job(_job(f"j{i}"))
    chunks = [await anext(stream) for _ in range(102)]
    await stream.aclose()  # type: ignore[attr-defined]
    assert chunks.count("event: resync\ndata: {}\n\n") == 1
    assert chunks[0] == "event: resync\ndata: {}\n\n"


async def test_stops_and_unsubscribes_on_disconnect() -> None:
    hub = EventHub()
    calls = 0

    async def disconnected() -> bool:
        nonlocal calls
        calls += 1
        return calls > 2

    chunks = [c async for c in sse_stream(hub, disconnected, heartbeat=0.01)]
    assert chunks[0] == "retry: 3000\n\n"
    hub.publish_job(_job("x"))  # no subscribers left, nothing raises
    assert hub.subscriber_count == 0


def test_events_route_requires_session(client: TestClient) -> None:
    assert client.get("/api/events").status_code == 401


async def test_closing_the_hub_ends_an_open_stream() -> None:
    hub = EventHub()
    stream = sse_stream(hub, _never, heartbeat=60)
    assert await anext(stream) == "retry: 3000\n\n"
    waiting = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    hub.close()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(waiting, 1)
    assert hub.subscriber_count == 0


async def test_closing_wakes_a_full_queue() -> None:
    hub = EventHub()
    stream = sse_stream(hub, _never, heartbeat=60)
    assert await anext(stream) == "retry: 3000\n\n"
    for i in range(150):
        hub.publish_job(_job(f"j{i}"))
    hub.close()
    chunks = await asyncio.wait_for(_rest(stream), 1)
    assert chunks == []


async def test_stream_after_close_ends_immediately() -> None:
    hub = EventHub()
    hub.close()
    chunks = await asyncio.wait_for(_rest(sse_stream(hub, _never, heartbeat=60)), 1)
    assert chunks == ["retry: 3000\n\n"]
    assert hub.subscriber_count == 0


async def _rest(stream: AsyncIterator[str]) -> list[str]:
    return [chunk async for chunk in stream]


def test_lifespan_shutdown_closes_the_hub(app_factory: AppFactory) -> None:
    app = app_factory()
    with TestClient(app):
        hub: EventHub = app.state.services.events
        assert not hub.closed
    assert hub.closed
