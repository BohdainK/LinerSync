"""JobService: the persisted job queue and its single FIFO worker (D2, D4)."""

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any, Protocol

from linersync.application.backups import BackupProgress, BackupService
from linersync.application.breaker import CircuitBreaker
from linersync.application.config import EngineConfig
from linersync.application.copy_back import CopyBackService, previewed_track_ids
from linersync.application.job_guard import (
    GuardedConnector,
    JobCancelled,
    JobGuard,
    referenced_keys,
)
from linersync.application.matching_service import MatchingFailed
from linersync.application.runner import Deps, SyncRunner, fail_unwritten, seed_from_run
from linersync.application.transfer import TransferService
from linersync.application.undo import UndoNotAllowed, UndoService
from linersync.domain.backups import BACKUP_SERVICE, BackupRun
from linersync.domain.jobs import (
    ACTIVE_STATUSES,
    BackupJob,
    BulkPreviewJob,
    BulkTransferJob,
    CopyToSourceJob,
    Job,
    JobProgress,
    JobRequest,
    JobStatus,
    PreviewJob,
    RunContext,
    RunMemberInfo,
    SyncJob,
    TransferJob,
    UndoJob,
)
from linersync.domain.runs import RunEvent, RunKind, RunStatus, SyncRun
from linersync.domain.sync import (
    SOURCE_MEMBER_ID,
    MemberRole,
    SyncGroup,
    destination_member_id,
)
from linersync.domain.tracks import ServiceId, Track, TrackKey
from linersync.ports.connector import Connector
from linersync.ports.errors import ConnectorError
from linersync.ports.jobs import JobRepository, RunContextRepository
from linersync.ports.repositories import Repositories
from linersync.ports.runtime import Clock, EventPublisher, IdFactory


class JobEnvironment(Protocol):
    def connectors(self) -> Mapping[ServiceId, Connector]:
        """Fresh instances, set-up services only."""
        ...

    def engine_config(self) -> EngineConfig: ...
    def on_auth_required(self, service: ServiceId) -> None: ...


JobListener = Callable[[Job], None]

INTERRUPTED_ERROR = "LinerSync stopped while this was running"
RUN_INTERRUPTED_ERROR = "LinerSync stopped during this run"
_SYNC_GONE = "This sync no longer exists"
_RUN_GONE = "This run no longer exists"
_PREVIEW_GONE = "The preview is gone; preview again"
_DESTINATION_GONE = "This destination is no longer part of the sync"
_NO_BACKUPS = "Backups aren't available"


class _JobFailed(Exception):
    """Ends the job FAILED with this message; no further runs are made."""


class _NoEvents:
    def publish(self, event: RunEvent) -> None:
        pass


class _JobEvents:
    """Per-job publisher: hands every event to `on_event` with the job id."""

    def __init__(self, job_id: str, on_event: Callable[[str, RunEvent], None]) -> None:
        self._job_id = job_id
        self._on_event = on_event

    def publish(self, event: RunEvent) -> None:
        self._on_event(self._job_id, event)


class JobService:
    def __init__(
        self,
        jobs: JobRepository,
        contexts: RunContextRepository,
        repos: Repositories,
        env: JobEnvironment,
        clock: Clock,
        new_id: IdFactory,
        listener: JobListener = lambda job: None,
        backups: BackupService | None = None,
    ) -> None:
        self._jobs = jobs
        self._contexts = contexts
        self._repos = repos
        self._env = env
        self._clock = clock
        self._new_id = new_id
        self._listener = listener
        self._backups = backups
        self._wake = asyncio.Event()
        self._guards: dict[str, JobGuard] = {}

    # -- queue ------------------------------------------------------------
    def submit(self, request: JobRequest, label: str) -> Job:
        """Queue a job. A sync already queued or running with the same `dry_run`,
        or a backup with the same services, returns that job unchanged."""
        if isinstance(request, (BackupJob, SyncJob)):
            existing = self._find_active(lambda r: _duplicates(r, request))
            if existing is not None:
                return existing
        job = Job(
            job_id=self._new_id(),
            request=request,
            status=JobStatus.QUEUED,
            created_at=self._clock.now(),
            label=label,
        )
        self._save(job)
        self._wake.set()
        return job

    def cancel(self, job_id: str) -> Job:
        """A queued job is cancelled at once; a running one is only marked
        `cancel_requested`. KeyError if unknown, ValueError if finished."""
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.finished:
            raise ValueError(f"job {job_id} has already finished")
        if job.status is JobStatus.QUEUED:
            job = replace(
                job, status=JobStatus.CANCELLED, finished_at=self._clock.now()
            )
        else:
            job = replace(job, cancel_requested=True)
            guard = self._guards.get(job_id)
            if guard is not None:
                guard.cancel_requested = True
        self._save(job)
        return job

    def active_for_sync(self, sync_id: str) -> Job | None:
        return self._find_active(
            lambda r: isinstance(r, SyncJob | CopyToSourceJob) and r.sync_id == sync_id
        )

    def active_backup(self) -> Job | None:
        return self._find_active(lambda r: isinstance(r, BackupJob))

    def _find_active(self, pred: Callable[[JobRequest], bool]) -> Job | None:
        """The first queued or running job whose request matches `pred`."""
        active = self._jobs.list_by_status(*ACTIVE_STATUSES)
        return next((job for job in active if pred(job.request)), None)

    def build_deps(self) -> Deps:
        """Deps on the environment's connectors, for read-only use (estimates)."""
        return self._deps(self._env.connectors(), _NoEvents())

    def recover(self) -> None:
        """D4: running runs end ABORTED (still undoable), running jobs end
        INTERRUPTED, queued jobs stay queued."""
        now = self._clock.now()
        for run in self._repos.runs.list_running():
            plans = run.plan.member_plans if run.plan is not None else ()
            members = fail_unwritten(
                self._repos.snapshots,
                run.run_id,
                run.members,
                (p.member_id for p in plans),
                RUN_INTERRUPTED_ERROR,
            )
            self._repos.runs.save(
                replace(
                    run,
                    status=RunStatus.ABORTED,
                    error=RUN_INTERRUPTED_ERROR,
                    finished_at=now,
                    members=members,
                )
            )
        for job in self._jobs.list_by_status(JobStatus.RUNNING):
            self._save(
                replace(
                    job,
                    status=JobStatus.INTERRUPTED,
                    error=INTERRUPTED_ERROR,
                    finished_at=now,
                )
            )

    # -- worker -----------------------------------------------------------
    async def serve(self) -> None:
        while True:
            self._wake.clear()
            if await self.run_next() is None:
                await self._wake.wait()

    async def run_next(self) -> Job | None:
        """Run the oldest queued job to its end; None when the queue is empty."""
        queued = self._jobs.list_by_status(JobStatus.QUEUED)
        if not queued:
            return None
        job_id = queued[0].job_id
        job = self._update(
            job_id, status=JobStatus.RUNNING, started_at=self._clock.now()
        )
        guard = JobGuard(
            self._env.on_auth_required,
            cancellable=not isinstance(job.request, UndoJob),
        )
        self._guards[job_id] = guard
        status, error = JobStatus.SUCCEEDED, None
        try:
            connectors = self._env.connectors()
            missing = self._missing_service(job.request, connectors)
            if missing is not None:
                raise _JobFailed(f"Connect {missing} first")
            guarded = {s: GuardedConnector(c, guard) for s, c in connectors.items()}
            await self._execute(job, guarded, guard)
        except JobCancelled:
            pass
        except (_JobFailed, ConnectorError) as e:
            # For a ConnectorError the guard has already reported an expired login.
            status, error = JobStatus.FAILED, str(e)
        except Exception as e:
            status, error = JobStatus.FAILED, f"{type(e).__name__}: {e}"
        finally:
            del self._guards[job_id]
        if guard.raised:
            status, error = JobStatus.CANCELLED, None
        return self._end(job_id, status, error)

    def _missing_service(
        self, request: JobRequest, connectors: Mapping[ServiceId, Connector]
    ) -> ServiceId | None:
        for service in self._services(request):
            if service not in connectors:
                return service
        return None

    def _services(self, request: JobRequest) -> list[ServiceId]:
        match request:
            case SyncJob() | CopyToSourceJob():
                group = self._repos.syncs.get(request.sync_id)
                if group is None:
                    raise _JobFailed(_SYNC_GONE)
                return [m.service for m in group.members]
            case PreviewJob():
                return [m.service for m in request.group.members]
            case BulkPreviewJob():
                return list(
                    dict.fromkeys(m.service for g in request.groups for m in g.members)
                )
            case TransferJob():
                return [request.source_service, request.destination]
            case BulkTransferJob():
                return list(
                    dict.fromkeys(
                        s
                        for t in request.transfers
                        for s in (t.source_service, t.destination)
                    )
                )
            case UndoJob():
                run = self._repos.runs.get(request.run_id)
                if run is None:
                    return []
                snaps = (
                    self._repos.snapshots.get_pre_write(request.run_id, m.member_id)
                    for m in run.members
                )
                return list(
                    dict.fromkeys(s.meta.service for s in snaps if s is not None)
                )
            case BackupJob():
                return list(request.services)

    async def _execute(
        self, job: Job, connectors: Mapping[ServiceId, Connector], guard: JobGuard
    ) -> None:
        deps = self._deps(connectors, _JobEvents(job.job_id, self._event))
        runner = SyncRunner(deps)
        request = job.request
        match request:
            case SyncJob():
                group = self._repos.syncs.get(request.sync_id)
                if group is None:
                    raise _JobFailed(_SYNC_GONE)
                run, _ = await runner.run_group(
                    group,
                    kind=RunKind.SYNC,
                    trigger=request.trigger,
                    dry_run=request.dry_run,
                    persist=True,
                    seed=self._seed(request.preview_run_id),
                )
                self._record(job.job_id, run, _group_members(group), guard)
            case PreviewJob():
                run = await runner.preview(request.group)
                self._record(job.job_id, run, _group_members(request.group), guard)
            case BulkPreviewJob():
                for group in request.groups:
                    guard.check()
                    run = await runner.preview(group)
                    self._record(job.job_id, run, _group_members(group), guard)
                    guard.reset_writes()
            case TransferJob():
                transfers = TransferService(deps, runner)
                await self._transfer(job.job_id, transfers, request, guard)
            case BulkTransferJob():
                transfers = TransferService(deps, runner)
                for item in request.transfers:
                    guard.check()
                    await self._transfer(job.job_id, transfers, item, guard)
                    # Each transfer is a finished, verified run, so a cancel
                    # may stop the job before the next one starts.
                    guard.reset_writes()
            case UndoJob():
                original = self._contexts.get(request.run_id)
                members = original.members if original is not None else ()
                services = {m.member_id: m.service for m in members}
                try:
                    undo = UndoService(deps, runner, services)
                    run = await undo.undo(request.run_id)
                except KeyError:
                    raise _JobFailed(_RUN_GONE) from None
                except UndoNotAllowed as e:
                    raise _JobFailed(str(e)) from None
                self._record(job.job_id, run, members, guard)
            case BackupJob():
                await self._back_up(job.job_id, request, connectors, guard)
            case CopyToSourceJob():
                await self._copy_to_source(job.job_id, deps, runner, request, guard)

    async def _copy_to_source(
        self,
        job_id: str,
        deps: Deps,
        runner: SyncRunner,
        request: CopyToSourceJob,
        guard: JobGuard,
    ) -> None:
        """The copy run, then (after a real copy) the sync run."""
        group = self._repos.syncs.get(request.sync_id)
        if group is None:
            raise _JobFailed(_SYNC_GONE)
        dest = next(
            (m for m in group.destinations if m.member_id == request.member_id), None
        )
        if dest is None:
            raise _JobFailed(_DESTINATION_GONE)
        members = _group_members(group)

        def recorded(run: SyncRun) -> None:
            # The copy run names destination songs it couldn't copy on `src`.
            songs: tuple[TrackKey, ...] = ()
            if run.kind is RunKind.COPY_TO_SOURCE:
                songs = tuple(
                    TrackKey(dest.service, tid) for tid in previewed_track_ids(run)
                )
            self._record(job_id, run, members, guard, songs)

        try:
            await CopyBackService(deps, runner).copy(
                group,
                request.member_id,
                request.track_ids,
                dry_run=request.dry_run,
                seed=self._seed(request.preview_run_id),
                on_run=recorded,
            )
        except MatchingFailed as e:
            raise _JobFailed(f"couldn't match songs: {e}") from None

    async def _back_up(
        self,
        job_id: str,
        request: BackupJob,
        connectors: Mapping[ServiceId, Connector],
        guard: JobGuard,
    ) -> None:
        """D4: back up each service in turn; a service's outcome is its
        BackupRun's status, and an unexpected error in one service doesn't stop
        the others. Backups write nothing, so a cancel stops them."""
        if self._backups is None:
            raise _JobFailed(_NO_BACKUPS)
        services = list(request.services) or sorted(
            s for s in connectors if s != BACKUP_SERVICE
        )

        def saved(run: BackupRun) -> None:
            job = self._current(job_id)
            self._update(job_id, backup_ids=(*job.backup_ids, run.backup_id))

        for service in services:
            guard.check()
            connector = connectors[service]
            progress = self._backup_progress(job_id, connector.display_name)
            try:
                await self._backups.back_up(connector, progress, on_saved=saved)
            except JobCancelled:
                raise
            except Exception:  # noqa: BLE001 - D4: the run says FAILED; go on
                continue

    def _backup_progress(self, job_id: str, name: str) -> BackupProgress:
        """Reports a backup's progress on the job, prefixed with `name`."""

        def progress(done: int, total: int, message: str) -> None:
            self._update(
                job_id,
                progress=JobProgress("backup", done, total, f"{name}: {message}"),
            )

        return progress

    async def _transfer(
        self, job_id: str, transfers: TransferService, t: TransferJob, guard: JobGuard
    ) -> None:
        run = await transfers.transfer(
            t.source_service,
            t.playlist_id,
            t.destination,
            dry_run=t.dry_run,
            seed=self._seed(t.preview_run_id),
        )
        members = (
            RunMemberInfo(
                SOURCE_MEMBER_ID, t.source_service, t.playlist_id, MemberRole.SOURCE
            ),
            RunMemberInfo(
                destination_member_id(0), t.destination, None, MemberRole.DESTINATION
            ),
        )
        self._record(job_id, run, members, guard)

    def _seed(self, preview_run_id: str | None) -> dict[ServiceId, dict[TrackKey, str]]:
        """The pairs of the preview run, or none when there is no preview."""
        if preview_run_id is None:
            return {}
        run = self._repos.runs.get(preview_run_id)
        context = self._contexts.get(preview_run_id)
        if run is None or context is None:
            raise _JobFailed(_PREVIEW_GONE)
        return seed_from_run(run, {m.member_id: m.service for m in context.members})

    def _record(
        self,
        job_id: str,
        run: SyncRun,
        members: tuple[RunMemberInfo, ...],
        guard: JobGuard,
        songs: Iterable[TrackKey] = (),
    ) -> None:
        """Note the run on the job and save its context (with `songs`, if the
        guard saw them, besides those the plan names)."""
        self._note_run(job_id, run.run_id)
        created = {
            r.member_id: r.created_playlist_id
            for r in run.members
            if r.created_playlist_id is not None
        }
        info = tuple(
            replace(m, playlist_id=created[m.member_id])
            if m.member_id in created
            else m
            for m in members
        )
        tracks = _run_tracks(run, info, guard)
        known = {t.key for t in tracks}
        extra = tuple(
            guard.seen[k]
            for k in dict.fromkeys(songs)
            if k in guard.seen and k not in known
        )
        self._contexts.save(RunContext(run.run_id, info, tracks + extra))

    # -- helpers ----------------------------------------------------------
    def _deps(
        self, connectors: Mapping[ServiceId, Connector], events: EventPublisher
    ) -> Deps:
        config = self._env.engine_config()
        return Deps(
            connectors=connectors,
            repos=self._repos,
            clock=self._clock,
            events=events,
            new_id=self._new_id,
            breaker=CircuitBreaker(self._repos.breakers, self._clock, config.breaker),
            config=config,
        )

    def _event(self, job_id: str, event: RunEvent) -> None:
        """Record the event's run id (if new) and progress on the job."""
        progress = JobProgress(event.phase, event.done, event.total, event.message)
        self._note_run(job_id, event.run_id, progress=progress)

    def _note_run(self, job_id: str, run_id: str, **changes: Any) -> None:
        """Add `run_id` to the job's runs if new, along with `changes`; with
        neither there is nothing to save."""
        job = self._current(job_id)
        if run_id not in job.run_ids:
            changes["run_ids"] = (*job.run_ids, run_id)
        if changes:
            self._update(job_id, **changes)

    def _end(self, job_id: str, status: JobStatus, error: str | None = None) -> Job:
        return self._update(
            job_id, status=status, error=error, finished_at=self._clock.now()
        )

    def _current(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def _update(self, job_id: str, **changes: Any) -> Job:
        """Apply changes to the stored job (which may carry a cancel request
        made meanwhile), save it and notify."""
        job = replace(self._current(job_id), **changes)
        self._save(job)
        return job

    def _save(self, job: Job) -> None:
        self._jobs.save(job)
        self._listener(job)


def _duplicates(a: JobRequest, b: BackupJob | SyncJob) -> bool:
    """`a` does `b`'s work: a backup of the same services, or a sync of the same
    group with the same `dry_run` (whatever its trigger)."""
    if isinstance(b, BackupJob):
        return isinstance(a, BackupJob) and a.services == b.services
    return isinstance(a, SyncJob) and a.sync_id == b.sync_id and a.dry_run == b.dry_run


def _group_members(group: SyncGroup) -> tuple[RunMemberInfo, ...]:
    return tuple(
        RunMemberInfo(m.member_id, m.service, m.playlist_id, m.role)
        for m in group.members
    )


def _run_tracks(
    run: SyncRun, members: tuple[RunMemberInfo, ...], guard: JobGuard
) -> tuple[Track, ...]:
    """The tracks the run's plan names that the guard saw, plus each
    unresolved entry's source and candidates (D8)."""
    if run.plan is None:
        return ()
    services = {m.member_id: m.service for m in members}
    keys = referenced_keys(run.plan, services)
    tracks: dict[TrackKey, Track] = {k: t for k, t in guard.seen.items() if k in keys}
    for u in run.plan.unresolved:
        tracks.setdefault(u.source.key, u.source)
        for c in u.decision.candidates:
            tracks.setdefault(c.track.key, c.track)
    return tuple(tracks.values())
