"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from importlib.metadata import version

from fastapi import FastAPI

from linersync.adapters.connectors.kit.discovery import PluginRegistry, discover_plugins
from linersync.adapters.covers import HttpCoverFetcher
from linersync.adapters.scheduler import SyncScheduler
from linersync.adapters.sqlite.backups import SqliteBackups
from linersync.adapters.sqlite.connections import SqliteConnectionRecords
from linersync.adapters.sqlite.credentials import SqliteCredentials
from linersync.adapters.sqlite.db import connect, migrate
from linersync.adapters.sqlite.jobs import SqliteJobs, SqliteRunContexts
from linersync.adapters.sqlite.quota import SqliteQuota
from linersync.adapters.sqlite.repositories import sqlite_repositories
from linersync.adapters.sqlite.secrets import load_or_create_key
from linersync.adapters.sqlite.sessions import SqliteSessions
from linersync.adapters.sqlite.settings import SqliteSettings
from linersync.adapters.system import SystemClock, uuid_ids
from linersync.application.backups import BACKUP_ALL_LABEL, BackupService
from linersync.application.jobs import JobService
from linersync.domain.jobs import BackupJob, Job, SyncJob
from linersync.domain.runs import Trigger
from linersync.ports.backups import CoverFetcher
from linersync.ports.runtime import Clock, IdFactory
from linersync.server import auth
from linersync.server.api import attention as attention_api
from linersync.server.api import backups as backups_api
from linersync.server.api import connections as connections_api
from linersync.server.api import events as events_api
from linersync.server.api import export as export_api
from linersync.server.api import home as home_api
from linersync.server.api import jobs as jobs_api
from linersync.server.api import runs as runs_api
from linersync.server.api import settings as settings_api
from linersync.server.api import stats as stats_api
from linersync.server.api import syncs as syncs_api
from linersync.server.api import transfers as transfers_api
from linersync.server.api.schemas import JobOut
from linersync.server.auth import LoginThrottle, SameOriginMiddleware
from linersync.server.config import ServerConfig
from linersync.server.connections import ConnectionManager, ConnectionStatus
from linersync.server.environment import ServerJobEnvironment
from linersync.server.events import EventHub
from linersync.server.state import AppServices
from linersync.server.web import mount_web


def create_app(
    config: ServerConfig,
    *,
    registry: PluginRegistry | None = None,
    clock: Clock | None = None,
    new_id: IdFactory | None = None,
    start_worker: bool = True,
    covers: CoverFetcher | None = None,
) -> FastAPI:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    engine = connect(config.data_dir / "linersync.db")
    migrate(engine)
    key = load_or_create_key(config.data_dir, config.secret_key)
    used_clock = clock or SystemClock()
    used_registry = registry if registry is not None else discover_plugins()
    used_new_id = new_id or uuid_ids
    credentials = SqliteCredentials(engine, key)
    quota = SqliteQuota(engine)
    settings_store = SqliteSettings(engine)
    connections = ConnectionManager(
        used_registry,
        credentials,
        quota,
        SqliteConnectionRecords(engine),
        used_clock,
        config.connector_settings,
        settings_store,
    )
    repos = sqlite_repositories(engine)
    job_store = SqliteJobs(engine)
    contexts = SqliteRunContexts(engine)
    events = EventHub()

    def reconcile() -> None:
        settings = settings_store.get()
        scheduler.reconcile(repos.syncs.list(), settings.default_schedule)
        scheduler.schedule_backup(settings.backup_schedule)

    def on_job(job: Job) -> None:
        events.publish_job(JobOut.of(job))
        if job.finished and job.sync_id is not None:
            # The runner may have paused the sync (source missing): drop its
            # scheduled run.
            reconcile()

    backups = SqliteBackups(engine)
    own_covers: HttpCoverFetcher | None = None  # ours to close at shutdown
    if covers is None:
        own_covers = HttpCoverFetcher()
        covers = own_covers
    backup_service = BackupService(
        backups,
        repos.syncs,
        covers,
        used_clock,
        used_new_id,
        versions_kept=lambda: settings_store.get().backup_versions_kept,
    )
    jobs = JobService(
        job_store,
        contexts,
        repos,
        ServerJobEnvironment(connections, backups),
        used_clock,
        used_new_id,
        on_job,
        backups=backup_service,
    )

    def on_due(sync_id: str) -> None:
        group = repos.syncs.get(sync_id)
        if group is not None:
            jobs.submit(SyncJob(sync_id, Trigger.SCHEDULED), f"Sync “{group.name}”")

    def on_backup_due() -> None:
        if any(i.status is not ConnectionStatus.NOT_SET_UP for i in connections.list()):
            jobs.submit(BackupJob(trigger=Trigger.SCHEDULED), BACKUP_ALL_LABEL)

    scheduler = SyncScheduler(on_due, config.timezone, on_backup_due)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        jobs.recover()
        worker: asyncio.Task[None] | None = None
        scheduler.start()
        try:
            reconcile()
            if start_worker:
                worker = asyncio.create_task(jobs.serve(), name="linersync-worker")
            yield
        finally:
            events.close()
            scheduler.shutdown()
            if worker is not None:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker
            if own_covers is not None:
                await own_covers.aclose()
            engine.dispose()

    app = FastAPI(
        title="LinerSync",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        lifespan=lifespan,
    )
    app.state.services = AppServices(
        config=config,
        engine=engine,
        clock=used_clock,
        new_id=used_new_id,
        sessions=SqliteSessions(engine),
        throttle=LoginThrottle(),
        registry=used_registry,
        credentials=credentials,
        quota=quota,
        connections=connections,
        repos=repos,
        job_store=job_store,
        contexts=contexts,
        jobs=jobs,
        events=events,
        scheduler=scheduler,
        settings_store=settings_store,
        backups=backups,
    )
    app.add_middleware(SameOriginMiddleware, public_origin=config.public_origin)
    app.include_router(auth.router)
    app.include_router(connections_api.router)
    app.include_router(syncs_api.router)
    app.include_router(runs_api.router)
    app.include_router(jobs_api.router)
    app.include_router(events_api.router)
    app.include_router(transfers_api.router)
    app.include_router(attention_api.router)
    app.include_router(home_api.router)
    app.include_router(settings_api.router)
    app.include_router(stats_api.router)
    app.include_router(backups_api.router)
    app.include_router(export_api.router)
    app.include_router(export_api.import_router)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": version("linersync")}

    if config.web_dir is not None:
        mount_web(app, config.web_dir)

    return app
