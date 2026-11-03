import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace

import pytest

from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.memory.backups import InMemoryBackups, StaticCovers
from linersync.adapters.memory.jobs import InMemoryJobs, InMemoryRunContexts
from linersync.application.backup_source import BackupConnector
from linersync.application.backups import (
    BACKUP_STOPPED,
    BackupProgress,
    BackupService,
)
from linersync.application.config import EngineConfig
from linersync.application.jobs import (
    INTERRUPTED_ERROR,
    RUN_INTERRUPTED_ERROR,
    JobService,
)
from linersync.application.undo import UndoNotAllowed, UndoService
from linersync.domain.backups import BACKUP_SERVICE, BackupRun, BackupStatus
from linersync.domain.jobs import (
    BackupJob,
    BulkPreviewJob,
    BulkTransferJob,
    Job,
    JobStatus,
    PreviewJob,
    SyncJob,
    TransferJob,
)
from linersync.domain.operations import (
    AddTracks,
    MemberPlan,
    RemoveTracks,
    Reorder,
    SyncPlan,
    UpdateMetadata,
)
from linersync.domain.playlists import PlaylistDraft, PlaylistMeta, PlaylistSnapshot
from linersync.domain.runs import (
    MemberStatus,
    RunKind,
    RunStatus,
    SyncRun,
    Trigger,
)
from linersync.domain.tracks import ServiceId, Track, TrackQuery
from linersync.ports.connector import Connector
from linersync.ports.errors import ReadFailed
from tests.application.conftest import APPLE, SPOTIFY, World
from tests.factories import NOW, snapshot


@dataclass
class FakeEnv:
    world: World
    store: InMemoryBackups = field(default_factory=InMemoryBackups)
    services: set[ServiceId] = field(
        default_factory=lambda: {SPOTIFY, APPLE, BACKUP_SERVICE}
    )
    auth_required: list[ServiceId] = field(default_factory=list)

    def connectors(self) -> Mapping[ServiceId, Connector]:
        all_: dict[ServiceId, Connector] = {
            SPOTIFY: self.world.spotify,
            APPLE: self.world.apple,
            BACKUP_SERVICE: BackupConnector(self.store),
        }
        return {s: c for s, c in all_.items() if s in self.services}

    def engine_config(self) -> EngineConfig:
        return EngineConfig()

    def on_auth_required(self, service: ServiceId) -> None:
        self.auth_required.append(service)


@dataclass
class Harness:
    world: World
    env: FakeEnv
    jobs: InMemoryJobs
    contexts: InMemoryRunContexts
    service: JobService
    seen: list[Job]
    backups: BackupService


def _harness(world: World) -> Harness:
    env = FakeEnv(world)
    jobs, contexts = InMemoryJobs(), InMemoryRunContexts()
    seen: list[Job] = []
    backups = BackupService(
        env.store, world.repos.syncs, StaticCovers(), world.clock, world.ids
    )
    service = JobService(
        jobs,
        contexts,
        world.repos,
        env,
        world.clock,
        world.ids,
        seen.append,
        backups=backups,
    )
    return Harness(world, env, jobs, contexts, service, seen, backups)


@pytest.fixture
def h(world: World) -> Harness:
    return _harness(world)


def _get(h: Harness, job_id: str) -> Job:
    job = h.jobs.get(job_id)
    assert job is not None
    return job


async def test_sync_job_runs_and_records_run(h: Harness) -> None:
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
    assert job.status is JobStatus.QUEUED

    ran = await h.service.run_next()

    assert ran is not None and ran.job_id == job.job_id
    done = _get(h, job.job_id)
    assert done.status is JobStatus.SUCCEEDED
    assert done.started_at == NOW and done.finished_at == NOW
    [run] = h.world.repos.runs.list_for_sync("s1")
    assert done.run_ids == (run.run_id,)
    stored = h.world.repos.runs.get(run.run_id)
    assert stored is not None and stored.status is RunStatus.SUCCEEDED


async def test_submit_dedupes_active_sync_job(h: Harness) -> None:
    first = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
    second = h.service.submit(SyncJob("s1", Trigger.SCHEDULED), "Mix")
    assert second.job_id == first.job_id
    assert len(h.jobs.list_by_status(JobStatus.QUEUED)) == 1
    assert h.service.active_for_sync("s1") == first

    await h.service.run_next()
    assert h.service.active_for_sync("s1") is None

    third = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
    assert third.job_id != first.job_id
    dry = h.service.submit(SyncJob("s1", Trigger.MANUAL, dry_run=True), "Mix")
    assert dry.job_id != third.job_id
    assert len(h.jobs.list_by_status(JobStatus.QUEUED)) == 2


async def test_jobs_run_fifo(h: Harness) -> None:
    h.world.spotify_to_spotify(("a",))
    first = h.service.submit(SyncJob("s1", Trigger.MANUAL), "One")
    second = h.service.submit(SyncJob("s2", Trigger.MANUAL), "Two")

    ran = await h.service.run_next()
    assert ran is not None and ran.job_id == first.job_id
    assert _get(h, second.job_id).status is JobStatus.QUEUED

    ran = await h.service.run_next()
    assert ran is not None and ran.job_id == second.job_id
    assert await h.service.run_next() is None


async def test_precheck_fails_without_connection(h: Harness) -> None:
    h.env.services = {SPOTIFY}
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.FAILED
    assert done.error == "Connect apple_music first"
    assert done.finished_at == NOW
    assert h.world.repos.runs.list_for_sync("s1") == []


async def test_deleted_sync_fails_cleanly(h: Harness) -> None:
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
    h.world.repos.syncs.delete("s1")

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.FAILED
    assert done.error == "This sync no longer exists"
    assert done.run_ids == ()


async def test_progress_events_update_job(h: Harness) -> None:
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")

    await h.service.run_next()

    mine = [j for j in h.seen if j.job_id == job.job_id]
    running = [j for j in mine if j.status is JobStatus.RUNNING]
    phases = {j.progress.phase for j in running if j.progress is not None}
    assert {"read", "apply"} <= phases
    assert mine[-1].status is JobStatus.SUCCEEDED
    assert mine[-1].progress is not None and mine[-1].progress.phase == "done"


async def test_preview_job_writes_nothing(h: Harness) -> None:
    job = h.service.submit(PreviewJob(h.world.group), "Mix")

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.SUCCEEDED
    assert not any(c.startswith(("apply:", "create:")) for c in h.world.apple.calls)
    [run_id] = done.run_ids
    run = h.world.repos.runs.get(run_id)
    assert run is not None and run.dry_run


async def test_bulk_preview_records_one_dry_run_per_group(h: Harness) -> None:
    other = replace(h.world.group, sync_id="preview-2")
    job = h.service.submit(BulkPreviewJob((h.world.group, other)), "Preview 2 syncs")

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert (done.kind, done.status) == ("bulk_preview", JobStatus.SUCCEEDED)
    assert not any(c.startswith(("apply:", "create:")) for c in h.world.apple.calls)
    runs = [h.world.repos.runs.get(r) for r in done.run_ids]
    assert [r.sync_id for r in runs if r is not None] == ["s1", "preview-2"]
    assert all(r is not None and r.dry_run for r in runs)
    assert all(h.contexts.get(r) is not None for r in done.run_ids)


async def test_bulk_preview_cancel_between_groups(world: World) -> None:
    apple = _clone_apple(world, _GatedApple)
    apple.entered, apple.release = asyncio.Event(), asyncio.Event()
    h = _harness(world)
    other = replace(world.group, sync_id="preview-2")
    job = h.service.submit(BulkPreviewJob((world.group, other)), "Preview 2 syncs")

    task = asyncio.create_task(h.service.run_next())
    async with asyncio.timeout(1):
        await apple.entered.wait()
    h.service.cancel(job.job_id)
    apple.release.set()
    await task

    done = _get(h, job.job_id)
    assert done.status is JobStatus.CANCELLED
    assert len(done.run_ids) == 1


class _AuthFailingSpotify(FakeConnector):
    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        raise ReadFailed(self.service, "token revoked", auth_required=True)


async def test_auth_required_reported(world: World) -> None:
    world.spotify = _AuthFailingSpotify(SPOTIFY, "Spotify", world.spotify.capabilities)
    h = _harness(world)
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")

    await h.service.run_next()

    [run_id] = _get(h, job.job_id).run_ids
    run = world.repos.runs.get(run_id)
    assert run is not None and run.status is RunStatus.ABORTED
    assert h.env.auth_required == [SPOTIFY]


def _member_plan(member_id: str) -> MemberPlan:
    return MemberPlan(member_id, (), (), ("A",), (), PlaylistDraft("Mix"))


async def test_recover_aborts_running_run_keeping_it_undoable(h: Harness) -> None:
    repos = h.world.repos
    run = SyncRun(
        run_id="r1",
        sync_id="s1",
        kind=RunKind.SYNC,
        trigger=Trigger.MANUAL,
        dry_run=False,
        status=RunStatus.RUNNING,
        started_at=NOW,
        plan=SyncPlan("s1", (_member_plan("dst0"),), ()),
    )
    repos.runs.save(run)
    repos.snapshots.save_pre_write("r1", "dst0", snapshot("apple_music", "A1", []))
    running = Job(
        job_id="j1",
        request=SyncJob("s1", Trigger.MANUAL),
        status=JobStatus.RUNNING,
        created_at=NOW,
        label="Mix",
        started_at=NOW,
        run_ids=("r1",),
    )
    h.jobs.save(running)

    h.service.recover()

    stored = repos.runs.get("r1")
    assert stored is not None
    assert stored.status is RunStatus.ABORTED
    assert stored.error == RUN_INTERRUPTED_ERROR
    assert stored.finished_at == NOW
    [member] = stored.members
    assert member.member_id == "dst0"
    assert member.status is MemberStatus.FAILED
    assert member.error == RUN_INTERRUPTED_ERROR
    job = _get(h, "j1")
    assert job.status is JobStatus.INTERRUPTED
    assert job.error == INTERRUPTED_ERROR
    assert job.finished_at == NOW

    deps = h.world.deps
    try:
        await UndoService(deps, h.world.runner()).undo("r1")
    except UndoNotAllowed:  # pragma: no cover - the failure being tested
        pytest.fail("the recovered run should be undoable")


async def test_recover_keeps_queued_jobs(h: Harness) -> None:
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")

    h.service.recover()

    assert _get(h, job.job_id).status is JobStatus.QUEUED
    ran = await h.service.run_next()
    assert ran is not None and ran.job_id == job.job_id
    assert _get(h, job.job_id).status is JobStatus.SUCCEEDED


async def test_cancel_queued_job(h: Harness) -> None:
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")

    cancelled = h.service.cancel(job.job_id)

    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.finished_at == NOW
    assert _get(h, job.job_id).status is JobStatus.CANCELLED
    assert await h.service.run_next() is None
    with pytest.raises(ValueError):
        h.service.cancel(job.job_id)
    with pytest.raises(KeyError):
        h.service.cancel("nope")


async def test_run_context_saved_for_sync_and_transfer(h: Harness) -> None:
    sync = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
    await h.service.run_next()
    [sync_run] = _get(h, sync.job_id).run_ids
    ctx = h.contexts.get(sync_run)
    assert ctx is not None
    assert [(m.member_id, m.service, m.playlist_id) for m in ctx.members] == [
        ("src", SPOTIFY, "P1"),
        ("dst0", APPLE, "A1"),
    ]

    transfer = h.service.submit(TransferJob(SPOTIFY, "P1", APPLE), "Copy")
    await h.service.run_next()
    [transfer_run] = _get(h, transfer.job_id).run_ids
    run = h.world.repos.runs.get(transfer_run)
    assert run is not None and run.status is RunStatus.SUCCEEDED
    created = run.members[0].created_playlist_id
    assert created is not None
    ctx = h.contexts.get(transfer_run)
    assert ctx is not None
    assert [(m.member_id, m.service, m.playlist_id) for m in ctx.members] == [
        ("src", SPOTIFY, "P1"),
        ("dst0", APPLE, created),
    ]


async def test_transfer_job_with_preview_run_reuses_its_pairs(h: Harness) -> None:
    preview = h.service.submit(TransferJob(SPOTIFY, "P1", APPLE, dry_run=True), "Copy")
    await h.service.run_next()
    [preview_run] = _get(h, preview.job_id).run_ids
    h.world.apple.catalog = ()  # a fresh search would now find nothing
    h.world.apple.calls.clear()

    job = h.service.submit(
        TransferJob(SPOTIFY, "P1", APPLE, preview_run_id=preview_run), "Copy"
    )
    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.SUCCEEDED
    assert not [c for c in h.world.apple.calls if c.startswith("search:")]
    [run_id] = done.run_ids
    run = h.world.repos.runs.get(run_id)
    assert run is not None and run.plan is not None
    assert [e.target_track_id for e in run.plan.member_plans[0].synced] == [
        "A",
        "B",
        "C",
    ]


async def test_bulk_transfer_seeds_each_item_from_its_preview(h: Harness) -> None:
    preview = h.service.submit(TransferJob(SPOTIFY, "P1", APPLE, dry_run=True), "Copy")
    await h.service.run_next()
    [preview_run] = _get(h, preview.job_id).run_ids
    h.world.apple.catalog = ()
    h.world.apple.calls.clear()

    item = TransferJob(SPOTIFY, "P1", APPLE, preview_run_id=preview_run)
    job = h.service.submit(BulkTransferJob((item,)), "Copy")
    await h.service.run_next()

    assert _get(h, job.job_id).status is JobStatus.SUCCEEDED
    assert not [c for c in h.world.apple.calls if c.startswith("search:")]


async def test_job_with_unknown_preview_run_fails(h: Harness) -> None:
    job = h.service.submit(
        TransferJob(SPOTIFY, "P1", APPLE, preview_run_id="nope"), "Copy"
    )
    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.FAILED
    assert done.error == "The preview is gone; preview again"


async def test_serve_wakes_on_submit(h: Harness) -> None:
    task = asyncio.create_task(h.service.serve())
    try:
        await asyncio.sleep(0)
        job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
        async with asyncio.timeout(1):
            while not _get(h, job.job_id).finished:
                await asyncio.sleep(0.01)
        assert _get(h, job.job_id).status is JobStatus.SUCCEEDED
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class _GatedApple(FakeConnector):
    """Apple whose searches wait for `release` (the first one sets `entered`)."""

    entered: asyncio.Event
    release: asyncio.Event

    async def search(self, query: TrackQuery) -> list[Track]:
        self.entered.set()
        await self.release.wait()
        return await super().search(query)


class _CancellingApple(FakeConnector):
    """Apple whose first `apply` calls `on_first_apply` before writing."""

    on_first_apply: Callable[[], object] = staticmethod(lambda: None)
    applied = False

    async def apply(
        self,
        playlist_id: str,
        op: UpdateMetadata | AddTracks | Reorder | RemoveTracks,
    ) -> None:
        if not self.applied:
            self.applied = True
            self.on_first_apply()
        await super().apply(playlist_id, op)


def _swap_apple(world: World, apple: FakeConnector) -> None:
    apple.playlists = dict(world.apple.playlists)
    world.apple = apple


def _clone_apple[C: FakeConnector](world: World, cls: type[C]) -> C:
    old = world.apple
    apple = cls(old.service, old.display_name, old.capabilities, catalog=old.catalog)
    _swap_apple(world, apple)
    return apple


async def test_cancel_before_writes_cancels(world: World) -> None:
    apple = _clone_apple(world, _GatedApple)
    apple.entered, apple.release = asyncio.Event(), asyncio.Event()
    h = _harness(world)
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")

    task = asyncio.create_task(h.service.run_next())
    async with asyncio.timeout(1):
        await apple.entered.wait()
    h.service.cancel(job.job_id)
    apple.release.set()
    await task

    done = _get(h, job.job_id)
    assert done.status is JobStatus.CANCELLED
    assert done.cancel_requested
    assert done.finished_at == NOW
    [run_id] = done.run_ids
    run = world.repos.runs.get(run_id)
    assert run is not None and run.status is RunStatus.ABORTED
    assert run.error is not None and run.error.startswith("JobCancelled")
    assert not any(c.startswith(("apply:", "create:")) for c in apple.calls)
    assert set(apple.playlists) == {"A1"}


async def test_cancel_after_first_write_finishes_run(world: World) -> None:
    apple = _clone_apple(world, _CancellingApple)
    h = _harness(world)
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
    apple.on_first_apply = lambda: h.service.cancel(job.job_id)

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.SUCCEEDED
    assert done.cancel_requested
    [run_id] = done.run_ids
    run = world.repos.runs.get(run_id)
    assert run is not None and run.status is RunStatus.SUCCEEDED
    [member] = run.members
    assert member.verified_after is not None


async def test_bulk_transfer_cancel_between_items(world: World) -> None:
    apple = _clone_apple(world, _CancellingApple)
    h = _harness(world)
    item = TransferJob(SPOTIFY, "P1", APPLE)
    job = h.service.submit(BulkTransferJob((item, item)), "Copy")
    apple.on_first_apply = lambda: h.service.cancel(job.job_id)

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.CANCELLED
    assert done.cancel_requested
    [run_id] = done.run_ids
    run = world.repos.runs.get(run_id)
    assert run is not None and run.status is RunStatus.SUCCEEDED
    assert [c for c in apple.calls if c.startswith("create:")] == ["create:Mix"]


async def test_run_context_has_titles(h: Harness) -> None:
    job = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")

    await h.service.run_next()

    [run_id] = _get(h, job.job_id).run_ids
    ctx = h.contexts.get(run_id)
    assert ctx is not None
    titles = {(t.service, t.track_id): t.title for t in ctx.tracks}
    assert titles == {
        (SPOTIFY, "a"): "a",
        (SPOTIFY, "b"): "b",
        (SPOTIFY, "c"): "c",
        (APPLE, "A"): "a",
        (APPLE, "B"): "b",
        (APPLE, "C"): "c",
    }


async def test_scheduled_and_manual_trigger_share_one_job(h: Harness) -> None:
    manual = h.service.submit(SyncJob("s1", Trigger.MANUAL), "Mix")
    assert manual.status is JobStatus.QUEUED
    scheduled = h.service.submit(SyncJob("s1", Trigger.SCHEDULED), "Mix")
    assert scheduled.job_id == manual.job_id
    assert len(h.jobs.list_by_status(JobStatus.QUEUED)) == 1


# -- backups ---------------------------------------------------------------


async def test_backup_job_backs_up_every_service(h: Harness) -> None:
    job = h.service.submit(BackupJob(), "Back up all services")
    assert job.kind == "backup"

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.SUCCEEDED
    assert len(done.backup_ids) == 2
    runs = [h.env.store.get_run(b) for b in done.backup_ids]
    assert [r.service if r else None for r in runs] == [APPLE, SPOTIFY]
    assert all(r is not None and r.status is BackupStatus.SUCCEEDED for r in runs)
    assert len(h.env.store.list_runs()) == 2
    assert h.env.store.list_runs(BACKUP_SERVICE) == []
    assert h.service.active_backup() is None


async def test_backup_job_dedupes(h: Harness) -> None:
    first = h.service.submit(BackupJob(), "Back up all services")
    assert h.service.active_backup() == first
    again = h.service.submit(BackupJob(trigger=Trigger.SCHEDULED), "Back up")
    assert again.job_id == first.job_id
    other = h.service.submit(BackupJob((SPOTIFY,)), "Back up Spotify")
    assert other.job_id != first.job_id
    assert len(h.jobs.list_by_status(JobStatus.QUEUED)) == 2


async def test_backup_job_progress_phase(h: Harness) -> None:
    job = h.service.submit(BackupJob((SPOTIFY,)), "Back up Spotify")

    await h.service.run_next()

    progress = [
        j.progress for j in h.seen if j.job_id == job.job_id and j.progress is not None
    ]
    assert progress
    assert all(p.phase == "backup" for p in progress)
    assert progress[0].message == "Spotify: Mix"


async def test_backup_job_precheck_named_service(h: Harness) -> None:
    job = h.service.submit(BackupJob((ServiceId("tidal"),)), "Back up Tidal")

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.FAILED
    assert done.error == "Connect tidal first"
    assert h.env.store.list_runs() == []


async def test_backup_job_without_backups_fails(world: World) -> None:
    env = FakeEnv(world)
    jobs = InMemoryJobs()
    service = JobService(
        jobs, InMemoryRunContexts(), world.repos, env, world.clock, world.ids
    )
    job = service.submit(BackupJob(), "Back up all services")

    await service.run_next()

    done = jobs.get(job.job_id)
    assert done is not None and done.status is JobStatus.FAILED
    assert done.error == "Backups aren't available"


async def test_cancel_running_backup(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = h.service.submit(BackupJob((SPOTIFY,)), "Back up Spotify")
    real = h.backups.back_up

    async def cancelling(
        connector: Connector,
        progress: BackupProgress,
        on_saved: Callable[[BackupRun], None],
    ) -> BackupRun:
        def cancel_then(done: int, total: int, message: str) -> None:
            h.service.cancel(job.job_id)
            progress(done, total, message)

        return await real(connector, cancel_then, on_saved=on_saved)

    monkeypatch.setattr(h.backups, "back_up", cancelling)

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.CANCELLED
    [run] = h.env.store.list_runs(SPOTIFY)
    assert run.status is BackupStatus.FAILED
    assert run.error == BACKUP_STOPPED
    assert done.backup_ids == (run.backup_id,)


async def test_unexpected_exception_in_one_service_does_not_stop_others(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken() -> list[PlaylistMeta]:
        raise RuntimeError("boom")

    # apple_music is backed up first (services run in sorted order).
    monkeypatch.setattr(h.world.apple, "list_playlists", broken)
    job = h.service.submit(BackupJob(), "Back up all services")

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.SUCCEEDED
    [apple] = h.env.store.list_runs(APPLE)
    [spotify] = h.env.store.list_runs(SPOTIFY)
    assert apple.status is BackupStatus.FAILED
    assert apple.error == BACKUP_STOPPED
    assert spotify.status is BackupStatus.SUCCEEDED
    assert done.backup_ids == (apple.backup_id, spotify.backup_id)


async def test_one_failing_service_does_not_stop_others(h: Harness) -> None:
    h.world.spotify.fail_list()
    job = h.service.submit(BackupJob(), "Back up all services")

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.SUCCEEDED
    assert len(done.backup_ids) == 2
    [spotify] = h.env.store.list_runs(SPOTIFY)
    [apple] = h.env.store.list_runs(APPLE)
    assert spotify.status is BackupStatus.FAILED
    assert apple.status is BackupStatus.SUCCEEDED


async def test_restore_job_via_transfer(h: Harness) -> None:
    await h.backups.back_up(h.world.spotify)
    entry = h.env.store.find_entry(SPOTIFY, "P1")
    assert entry is not None
    job = h.service.submit(
        TransferJob(BACKUP_SERVICE, entry.latest_version_id, SPOTIFY), "Restore"
    )

    await h.service.run_next()

    done = _get(h, job.job_id)
    assert done.status is JobStatus.SUCCEEDED, done.error
    [run_id] = done.run_ids
    ctx = h.contexts.get(run_id)
    assert ctx is not None
    [src] = [m for m in ctx.members if m.member_id == "src"]
    assert src.service == BACKUP_SERVICE
    assert src.playlist_id == entry.latest_version_id
