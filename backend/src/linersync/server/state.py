"""Services shared by all routes, built once per app."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy import Engine

from linersync.adapters.connectors.kit.discovery import PluginRegistry
from linersync.adapters.scheduler import SyncScheduler
from linersync.adapters.sqlite.credentials import SqliteCredentials
from linersync.adapters.sqlite.jobs import SqliteJobs, SqliteRunContexts
from linersync.adapters.sqlite.quota import SqliteQuota
from linersync.adapters.sqlite.sessions import SqliteSessions
from linersync.adapters.sqlite.settings import SqliteSettings
from linersync.application.jobs import JobService
from linersync.ports.backups import BackupRepository
from linersync.ports.repositories import Repositories
from linersync.ports.runtime import Clock, IdFactory
from linersync.server.config import ServerConfig

if TYPE_CHECKING:
    from linersync.server.auth import LoginThrottle
    from linersync.server.connections import ConnectionManager
    from linersync.server.events import EventHub
    from linersync.server.flags import FeatureFlags


@dataclass
class AppServices:
    config: ServerConfig
    engine: Engine
    clock: Clock
    new_id: IdFactory
    sessions: SqliteSessions
    throttle: "LoginThrottle"
    registry: PluginRegistry
    credentials: SqliteCredentials
    quota: SqliteQuota
    connections: "ConnectionManager"
    repos: Repositories
    job_store: SqliteJobs
    contexts: SqliteRunContexts
    jobs: JobService
    events: "EventHub"
    scheduler: SyncScheduler
    settings_store: SqliteSettings
    backups: BackupRepository
    flags: "FeatureFlags"


def get_services(request: Request) -> AppServices:
    services: AppServices = request.app.state.services
    return services
