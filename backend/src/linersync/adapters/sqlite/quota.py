"""API quota usage, stored with sortable UTC timestamps."""

from datetime import datetime, timedelta

from sqlalchemy import Engine, delete, func, insert, select

from linersync.adapters.sqlite.db import utc_text
from linersync.adapters.sqlite.schema import quota_usage
from linersync.domain.tracks import ServiceId

RETENTION = timedelta(days=40)


class SqliteQuota:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, service: ServiceId, units: int, at: datetime) -> None:
        text = utc_text(at)
        cutoff = utc_text(at - RETENTION)
        with self._engine.begin() as conn:
            conn.execute(
                insert(quota_usage).values(service=service, units=units, at=text)
            )
            conn.execute(delete(quota_usage).where(quota_usage.c.at < cutoff))

    def used_since(self, service: ServiceId, since: datetime) -> int:
        with self._engine.connect() as conn:
            total = conn.execute(
                select(func.coalesce(func.sum(quota_usage.c.units), 0)).where(
                    quota_usage.c.service == service,
                    quota_usage.c.at >= utc_text(since),
                )
            ).scalar_one()
        return int(total)
