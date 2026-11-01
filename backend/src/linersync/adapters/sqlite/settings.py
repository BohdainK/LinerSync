"""App settings stored as one JSON body under the key "app"."""

from sqlalchemy import Engine, select

from linersync.adapters.sqlite.codec import dumps
from linersync.adapters.sqlite.db import load_one, upsert
from linersync.adapters.sqlite.schema import settings
from linersync.application.settings import AppSettings

_KEY = "app"


class SqliteSettings:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self) -> AppSettings:
        query = select(settings.c.body).where(settings.c.key == _KEY)
        stored = load_one(self._engine, AppSettings, query)
        return AppSettings() if stored is None else stored

    def save(self, s: AppSettings) -> None:
        with self._engine.begin() as conn:
            upsert(conn, settings, {"key": _KEY}, {"body": dumps(AppSettings, s)})
