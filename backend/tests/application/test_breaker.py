from datetime import timedelta

from linersync.adapters.memory.repositories import in_memory_repositories
from linersync.adapters.memory.runtime import FixedClock
from linersync.application.breaker import CircuitBreaker
from linersync.application.config import BreakerConfig
from linersync.domain.attention import AttentionKind
from linersync.domain.runs import RunStatus, Trigger
from linersync.domain.tracks import ServiceId
from tests.application.conftest import APPLE, SPOTIFY, World
from tests.factories import NOW

SVC = ServiceId("spotify")


def _breaker() -> tuple[CircuitBreaker, FixedClock]:
    clock = FixedClock(NOW)
    return CircuitBreaker(
        in_memory_repositories().breakers, clock, BreakerConfig()
    ), clock


def _trip(b: CircuitBreaker) -> None:
    for _ in range(5):
        b.record_failure(SVC, "boom")


def test_opens_after_threshold() -> None:
    b, _ = _breaker()
    opened = [b.record_failure(SVC, "boom") for _ in range(5)]
    assert opened == [False, False, False, False, True]
    assert not b.allow(SVC)


def test_half_open_after_cooldown() -> None:
    b, clock = _breaker()
    _trip(b)
    clock.advance(timedelta(minutes=29))
    assert not b.allow(SVC)
    clock.advance(timedelta(minutes=2))
    assert b.allow(SVC)
    assert b.record_failure(SVC, "still down")  # re-opens
    assert not b.allow(SVC)
    clock.advance(timedelta(minutes=31))
    assert b.allow(SVC)
    b.record_success(SVC)
    assert b.allow(SVC)
    assert not b.record_failure(SVC, "x")  # counter was reset


def test_success_resets() -> None:
    b, _ = _breaker()
    for _ in range(4):
        b.record_failure(SVC, "boom")
    b.record_success(SVC)
    assert not b.record_failure(SVC, "boom")
    assert b.allow(SVC)


async def test_runner_skips_tripped_source_without_calls(world: World) -> None:
    for _ in range(5):
        world.spotify.fail_reads("P1")
        run = await world.runner().run("s1", trigger=Trigger.SCHEDULED)
        assert run.status is RunStatus.ABORTED
    calls = (list(world.spotify.calls), list(world.apple.calls))
    run = await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.SKIPPED
    assert (world.spotify.calls, world.apple.calls) == calls
    items = [
        i
        for i in world.repos.attention.list_open()
        if i.kind is AttentionKind.SERVICE_FAILING
    ]
    assert len(items) == 1 and items[0].service == SPOTIFY


async def test_not_found_does_not_count(world: World) -> None:
    for _ in range(4):
        world.deps.breaker.record_failure(SPOTIFY, "boom")
    world.spotify.delete_playlist("P1")
    run = await world.runner().run("s1", trigger=Trigger.MANUAL)
    assert run.status is RunStatus.PAUSED_SOURCE_MISSING
    assert world.repos.breakers.get(SPOTIFY).consecutive_failures == 4


async def test_tripped_destination_is_skipped_breaker(world: World) -> None:
    _trip_service(world, APPLE)
    run = await world.runner().run("s1", trigger=Trigger.MANUAL)
    assert [m.status.value for m in run.members] == ["skipped_breaker"]
    assert world.apple.calls == []


async def test_dry_run_records_nothing(world: World) -> None:
    world.spotify.fail_reads("P1")
    await world.runner().run("s1", trigger=Trigger.MANUAL, dry_run=True)
    assert world.repos.breakers.get(SPOTIFY).consecutive_failures == 0


async def test_failing_run_counts_once_and_success_resets(world: World) -> None:
    world.apple.fail_search(10)
    run = await world.runner().run("s1", trigger=Trigger.MANUAL)
    assert run.status is RunStatus.PARTIAL
    assert world.repos.breakers.get(APPLE).consecutive_failures == 1
    world.apple.fail_search(0)
    await world.runner().run("s1", trigger=Trigger.MANUAL)
    assert world.repos.breakers.get(APPLE).consecutive_failures == 0


def _trip_service(world: World, service: ServiceId) -> None:
    b = world.deps.breaker
    for _ in range(5):
        b.record_failure(service, "boom")


async def test_truncated_source_reads_trip_breaker(world: World) -> None:
    world.spotify.truncate_reads("P1")
    for _ in range(5):
        run = await world.runner().run("s1", trigger=Trigger.SCHEDULED)
        assert run.status is RunStatus.ABORTED
    run = await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.SKIPPED


async def test_truncated_destination_read_counts_as_failure(world: World) -> None:
    world.apple.truncate_reads("A1")
    await world.runner().run("s1", trigger=Trigger.MANUAL)
    assert world.repos.breakers.get(APPLE).consecutive_failures == 1


def test_record_success_reports_closing_an_open_breaker() -> None:
    b, _ = _breaker()
    assert not b.record_success(SVC)
    for _ in range(4):
        b.record_failure(SVC, "boom")
    assert not b.record_success(SVC)
    _trip(b)
    assert b.record_success(SVC)
    assert not b.record_success(SVC)


async def test_service_failing_item_resolved_on_recovery(world: World) -> None:
    for _ in range(5):
        world.spotify.fail_reads("P1")
        await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    (item,) = [
        i
        for i in world.repos.attention.list_open()
        if i.kind is AttentionKind.SERVICE_FAILING
    ]
    world.clock.advance(timedelta(minutes=31))
    run = await world.runner().run("s1", trigger=Trigger.SCHEDULED)
    assert run.status is RunStatus.SUCCEEDED
    assert not any(
        i.kind is AttentionKind.SERVICE_FAILING
        for i in world.repos.attention.list_open()
    )
    resolved = world.repos.attention.get(item.item_id)
    assert resolved is not None and resolved.resolution == "recovered"
