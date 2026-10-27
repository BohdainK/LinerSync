"""The job environment: the set-up services' connectors plus the backup (D6)."""

from linersync.application.backup_source import BackupConnector
from linersync.application.config import EngineConfig
from linersync.domain.backups import BACKUP_SERVICE
from linersync.domain.tracks import ServiceId
from linersync.ports.backups import BackupRepository
from linersync.ports.connector import Connector
from linersync.server.connections import ConnectionManager


class ServerJobEnvironment:
    def __init__(
        self, connections: ConnectionManager, backups: BackupRepository
    ) -> None:
        self._connections = connections
        self._backups = backups

    def connectors(self) -> dict[ServiceId, Connector]:
        return self._connections.connectors() | {
            BACKUP_SERVICE: BackupConnector(self._backups)
        }

    def engine_config(self) -> EngineConfig:
        return self._connections.engine_config()

    def on_auth_required(self, service: ServiceId) -> None:
        self._connections.on_auth_required(service)
