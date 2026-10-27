"""Engine construction, migrations and the stored datetime format."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool

_MIGRATIONS = Path(__file__).parent / "migrations"


def connect(path: Path | str) -> Engine:
    memory = str(path) == ":memory:"
    kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if memory:
        url = "sqlite://"
        kwargs["poolclass"] = StaticPool
    else:
        url = f"sqlite:///{path}"
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        if not memory:
            cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def migrate(engine: Engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "head")


def utc_text(dt: datetime) -> str:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC).isoformat(timespec="microseconds")
