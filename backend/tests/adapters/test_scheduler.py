import asyncio
import threading
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.util import iscoroutinefunction_partial

from linersync.adapters.scheduler import SyncScheduler, cron_problem
from linersync.domain.sync import SyncGroup, SyncStatus
from tests.factories import group

TZ = "Europe/Amsterdam"
DEFAULT = "0 */6 * * *"


class Due:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, sync_id: str) -> None:
        self.calls.append(sync_id)


@pytest.fixture
async def due() -> Due:
    return Due()


@pytest.fixture
async def sched(due: Due) -> AsyncIterator[SyncScheduler]:
    s = SyncScheduler(due, TZ)
    s.start()
    yield s
    s.shutdown()


def _g(sync_id: str, **kw: object) -> SyncGroup:
    return replace(group(sync_id=sync_id), **kw)  # type: ignore[arg-type]


def test_cron_problem() -> None:
    assert cron_problem("0 */6 * * *", TZ) is None
    assert cron_problem("61 * * * *", TZ)
    assert cron_problem("every day", TZ)


async def test_reconcile_adds_active_syncs_only(sched: SyncScheduler) -> None:
    sched.reconcile(
        [_g("a"), _g("b", status=SyncStatus.PAUSED)],
        DEFAULT,
    )
    runs = sched.next_runs()
    assert set(runs) == {"a"}
    assert runs["a"].tzinfo is not None and runs["a"].utcoffset() == UTC.utcoffset(None)


async def test_override_beats_default(sched: SyncScheduler) -> None:
    sched.reconcile([_g("a", schedule="0 3 * * *")], DEFAULT)
    local = sched.next_runs()["a"].astimezone(ZoneInfo(TZ))
    assert local.hour == 3 and local.minute == 0


async def test_reconcile_removes_deleted_and_updates_changed(
    sched: SyncScheduler,
) -> None:
    sched.reconcile([_g("a"), _g("b")], DEFAULT)
    assert set(sched.next_runs()) == {"a", "b"}
    before = sched.next_runs()["a"]
    sched.reconcile([_g("a", schedule="0 3 * * *")], DEFAULT)
    assert set(sched.next_runs()) == {"a"}
    assert sched.next_runs()["a"].astimezone(ZoneInfo(TZ)).hour == 3
    assert before is not None
    assert len(sched.aps.get_jobs()) == 1


async def test_invalid_stored_expression_is_skipped(sched: SyncScheduler) -> None:
    sched.reconcile([_g("a", schedule="nonsense"), _g("b")], DEFAULT)
    assert set(sched.next_runs()) == {"b"}


async def test_job_options(sched: SyncScheduler) -> None:
    sched.reconcile([_g("a")], DEFAULT)
    job = sched.aps.get_job("sync:a")
    assert job is not None
    assert job.coalesce is True
    assert job.max_instances == 1
    assert job.misfire_grace_time == 300


async def test_due_sync_submits_scheduled_job(sched: SyncScheduler, due: Due) -> None:
    sched.reconcile([_g("s1")], DEFAULT)
    job = sched.aps.get_job("sync:s1")
    assert job is not None
    await job.func()
    assert due.calls == ["s1"]


async def test_fired_job_runs_on_event_loop_thread() -> None:
    loop_thread = threading.get_ident()
    seen: list[int] = []
    s = SyncScheduler(lambda _id: seen.append(threading.get_ident()), TZ)
    s.start()
    try:
        s.reconcile([_g("s1")], DEFAULT)
        job = s.aps.get_job("sync:s1")
        assert job is not None
        assert iscoroutinefunction_partial(job.func)
        job.modify(next_run_time=datetime.now(UTC))
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.05)
    finally:
        s.shutdown()
    assert seen == [loop_thread]


async def test_schedule_backup_sets_and_removes(sched: SyncScheduler) -> None:
    assert sched.next_backup_at() is None
    sched.schedule_backup("0 3 * * *")
    at = sched.next_backup_at()
    assert at is not None and at.utcoffset() == UTC.utcoffset(None)
    local = at.astimezone(ZoneInfo(TZ))
    assert (local.hour, local.minute) == (3, 0)
    sched.schedule_backup(None)
    assert sched.next_backup_at() is None
    sched.schedule_backup("0 3 * * *")
    sched.schedule_backup("nope")
    assert sched.next_backup_at() is None
    sched.schedule_backup("0 3 * * *")
    sched.schedule_backup("0 4 * * *")
    at = sched.next_backup_at()
    assert at is not None and at.astimezone(ZoneInfo(TZ)).hour == 4


async def test_reconcile_keeps_backup_job(sched: SyncScheduler) -> None:
    sched.schedule_backup("0 3 * * *")
    sched.reconcile([_g("a")], DEFAULT)
    sched.reconcile([], DEFAULT)
    assert sched.next_backup_at() is not None
    assert sched.next_runs() == {}
    job = sched.aps.get_job("backup")
    assert job is not None
    assert (job.coalesce, job.max_instances, job.misfire_grace_time) == (True, 1, 300)


async def test_backup_due_calls_callback(due: Due) -> None:
    calls: list[str] = []
    s = SyncScheduler(due, TZ, lambda: calls.append("backup"))
    s.start()
    try:
        s.schedule_backup("0 3 * * *")
        job = s.aps.get_job("backup")
        assert job is not None
        await job.func()
    finally:
        s.shutdown()
    assert calls == ["backup"]
    assert due.calls == []
