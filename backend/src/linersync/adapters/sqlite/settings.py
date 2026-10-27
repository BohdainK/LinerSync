"""App settings stored as one JSON body under the key "app"."""

from sqlalchemy import Engine, delete, insert, select

from linersync.adapters.sqlite.codec import dumps, loads
from linersync.adapters.sqlite.schema import settings
from linersync.application.settings import AppSettings

_KEY = "app"


class SqliteSettings:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self) -> AppSettings:
        with self._engine.connect() as conn:
            body = conn.execute(
                select(settings.c.body).where(settings.c.key == _KEY)
            ).scalar_one_or_none()
        if body is None:
            return AppSettings()
        loaded: AppSettings = loads(AppSettings, body)
        return loaded

    def save(self, s: AppSettings) -> None:
        with self._engine.begin() as conn:
            conn.execute(delete(settings).where(settings.c.key == _KEY))
            conn.execute(
                insert(settings).values(key=_KEY, body=dumps(AppSettings, s))
            )
