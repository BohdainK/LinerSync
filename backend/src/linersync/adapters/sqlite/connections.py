"""Per-service connection status, stored as one JSON body per service."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.sqlite import insert

from linersync.adapters.sqlite.codec import dumps, loads
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
        with self._engine.connect() as conn:
            body = conn.execute(
                select(connections.c.body).where(connections.c.service == service)
            ).scalar_one_or_none()
        if body is None:
            return None
        record: ConnectionRecord = loads(ConnectionRecord, body)
        return record

    def save(self, service: ServiceId, record: ConnectionRecord) -> None:
        body = dumps(ConnectionRecord, record)
        stmt = insert(connections).values(service=service, body=body)
        with self._engine.begin() as conn:
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["service"], set_={"body": body}
                )
            )

    def delete(self, service: ServiceId) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(connections).where(connections.c.service == service))
