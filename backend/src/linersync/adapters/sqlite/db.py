"""Engine construction, migrations, the stored datetime format and the
read/write shapes the repositories share."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, Select, Table, create_engine, event
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.pool import StaticPool

from linersync.adapters.sqlite.codec import loads

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


def utc_text_or_none(dt: datetime | None) -> str | None:
    return None if dt is None else utc_text(dt)


def load_one[T](engine: Engine, tp: type[T], query: Select[Any]) -> T | None:
    """The one body `query` selects, decoded; None when there is no row."""
    with engine.begin() as conn:
        body = conn.execute(query).scalar_one_or_none()
    return None if body is None else loads(tp, body)


def load_all[T](engine: Engine, tp: type[T], query: Select[Any]) -> list[T]:
    """Every body `query` selects, decoded, in the query's order."""
    with engine.begin() as conn:
        return [loads(tp, body) for body in conn.execute(query).scalars()]


def upsert(
    conn: Connection,
    table: Table,
    key: Mapping[str, Any],
    values: Mapping[str, Any],
    *,
    insert_only: Mapping[str, Any] | None = None,
) -> None:
    """Insert the row, or update `values` on the row whose primary key is `key`.
    `insert_only` columns are written when the row is new and kept otherwise."""
    stmt = insert(table).values({**key, **values, **(insert_only or {})})
    conn.execute(stmt.on_conflict_do_update(index_elements=list(key), set_=values))
