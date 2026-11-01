"""Per-service connection status, stored as one JSON body per service."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, delete, select

from linersync.adapters.sqlite.codec import dumps
from linersync.adapters.sqlite.db import load_one, upsert
from linersync.adapters.sqlite.schema import connections
from linersync.domain.tracks import ServiceId


@dataclass(frozen=True, slots=True)
class ConnectionRecord:
    status: str  # a ConnectionStatus value
    message: str = ""
    account_name: str | None = None
    checked_at: datetime | None = None


class SqliteConnectionRecords:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, service: ServiceId) -> ConnectionRecord | None:
        query = select(connections.c.body).where(connections.c.service == service)
        return load_one(self._engine, ConnectionRecord, query)

    def save(self, service: ServiceId, record: ConnectionRecord) -> None:
        body = dumps(ConnectionRecord, record)
        with self._engine.begin() as conn:
            upsert(conn, connections, {"service": service}, {"body": body})

    def delete(self, service: ServiceId) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(connections).where(connections.c.service == service))
