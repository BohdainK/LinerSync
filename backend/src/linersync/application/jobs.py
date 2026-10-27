"""JobService: the persisted job queue and its single FIFO worker (D2, D4)."""

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol

from linersync.application.backups import BackupService
from linersync.application.breaker import CircuitBreaker
from linersync.application.config import EngineConfig
from linersync.application.job_guard import (
    GuardedConnector,
    JobCancelled,
    JobGuard,
    referenced_keys,
)
from linersync.application.runner import Deps, SyncRunner
from linersync.application.transfer import TransferService
from linersync.application.undo import UndoNotAllowed, UndoService
from linersync.domain.backups import BACKUP_SERVICE, BackupRun
from linersync.domain.jobs import (
    ACTIVE_STATUSES,
    BackupJob,
    BulkTransferJob,
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
from linersync.domain.runs import (
    MemberResult,
    MemberStatus,
    RunEvent,
    RunStatus,
    SyncRun,
)
from linersync.domain.sync import MemberRole, SyncGroup
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
        if isinstance(request, BackupJob):
            for job in self._jobs.list_by_status(*ACTIVE_STATUSES):
                if (
                    isinstance(job.request, BackupJob)
                    and job.request.services == request.services
                ):
                    return job
        if isinstance(request, SyncJob):
            for job in self._jobs.list_by_status(*ACTIVE_STATUSES):
                if (
                    isinstance(job.request, SyncJob)
                    and job.request.sync_id == request.sync_id
                    and job.request.dry_run == request.dry_run
                ):
                    return job
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
        for job in self._jobs.list_by_status(*ACTIVE_STATUSES):
            if isinstance(job.request, SyncJob) and job.request.sync_id == sync_id:
                return job
        return None

    def active_backup(self) -> Job | None:
        for job in self._jobs.list_by_status(*ACTIVE_STATUSES):
            if isinstance(job.request, BackupJob):
                return job
        return None

    def build_deps(self) -> Deps:
        """Deps on the environment's connectors, for read-only use (estimates)."""
        return self._deps(self._env.connectors(), _NoEvents())

    def recover(self) -> None:
        """D4: running runs end ABORTED (still undoable), running jobs end
        INTERRUPTED, queued jobs stay queued."""
        now = self._clock.now()
        snapshots = self._repos.snapshots
        for run in self._repos.runs.list_running():
            members = list(run.members)
            done = {m.member_id for m in members}
            plans = run.plan.member_plans if run.plan is not None else ()
            for plan in plans:
                mid = plan.member_id
                if mid not in done and snapshots.has_pre_write(run.run_id, mid):
                    members.append(
                        MemberResult(
                            mid, MemberStatus.FAILED, error=RUN_INTERRUPTED_ERROR
                        )
                    )
            self._repos.runs.save(
                replace(
                    run,
                    status=RunStatus.ABORTED,
                    error=RUN_INTERRUPTED_ERROR,
                    finished_at=now,
                    members=tuple(members),
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
        except _JobFailed as e:
            status, error = JobStatus.FAILED, str(e)
        except JobCancelled:
            pass
        except ConnectorError as e:
            # The guard has already reported an expired login.
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
            case SyncJob():
                group = self._repos.syncs.get(request.sync_id)
                if group is None:
                    raise _JobFailed(_SYNC_GONE)
                return [m.service for m in group.members]
            case PreviewJob():
                return [m.service for m in request.group.members]
            case TransferJob():
                return [request.source_service, request.destination]
            case BulkTransferJob():
                found: dict[ServiceId, None] = {}
                for t in request.transfers:
                    found[t.source_service] = None
                    found[t.destination] = None
                return list(found)
            case UndoJob():
                run = self._repos.runs.get(request.run_id)
                if run is None:
                    return []
                services: dict[ServiceId, None] = {}
                for m in run.members:
                    snap = self._repos.snapshots.get_pre_write(
                        request.run_id, m.member_id
                    )
                    if snap is not None:
                        services[snap.meta.service] = None
                return list(services)
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
                try:
                    run = await runner.run(
                        request.sync_id,
                        trigger=request.trigger,
                        dry_run=request.dry_run,
                    )
                except KeyError:
                    raise _JobFailed(_SYNC_GONE) from None
                self._record(job.job_id, run, _group_members(group), guard)
            case PreviewJob():
                run = await runner.preview(request.group)
                self._record(job.job_id, run, _group_members(request.group), guard)
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
                    guard.wrote = not guard.cancellable
            case UndoJob():
                try:
                    run = await UndoService(deps, runner).undo(request.run_id)
                except KeyError:
                    raise _JobFailed(_RUN_GONE) from None
                except UndoNotAllowed as e:
                    raise _JobFailed(str(e)) from None
                original = self._contexts.get(request.run_id)
                members = original.members if original is not None else ()
                self._record(job.job_id, run, members, guard)
            case BackupJob():
                await self._back_up(job.job_id, request, connectors, guard)

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
        for service in services:
            guard.check()
            connector = connectors[service]

            def progress(
                done: int, total: int, message: str, name: str = connector.display_name
            ) -> None:
                self._update(
                    job_id,
                    progress=JobProgress("backup", done, total, f"{name}: {message}"),
                )

            def saved(run: BackupRun) -> None:
                job = self._current(job_id)
                self._update(job_id, backup_ids=(*job.backup_ids, run.backup_id))

            try:
                await self._backups.back_up(connector, progress, on_saved=saved)
            except JobCancelled:
                raise
            except Exception:  # noqa: BLE001 - D4: the run says FAILED; go on
                continue

    async def _transfer(
        self, job_id: str, transfers: TransferService, t: TransferJob, guard: JobGuard
    ) -> None:
        run = await transfers.transfer(
            t.source_service, t.playlist_id, t.destination, dry_run=t.dry_run
        )
        members = (
            RunMemberInfo("src", t.source_service, t.playlist_id, MemberRole.SOURCE),
            RunMemberInfo("dst0", t.destination, None, MemberRole.DESTINATION),
        )
        self._record(job_id, run, members, guard)

    def _record(
        self,
        job_id: str,
        run: SyncRun,
        members: tuple[RunMemberInfo, ...],
        guard: JobGuard,
    ) -> None:
        """Note the run on the job and save its context."""
        job = self._current(job_id)
        if run.run_id not in job.run_ids:
            self._update(job_id, run_ids=(*job.run_ids, run.run_id))
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
        self._contexts.save(RunContext(run.run_id, info, _run_tracks(run, info, guard)))

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
        job = self._current(job_id)
        run_ids = job.run_ids
        if event.run_id not in run_ids:
            run_ids = (*run_ids, event.run_id)
        progress = JobProgress(event.phase, event.done, event.total, event.message)
        self._update(job_id, run_ids=run_ids, progress=progress)

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
