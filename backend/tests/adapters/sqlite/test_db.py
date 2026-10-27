from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from linersync.adapters.sqlite.db import _MIGRATIONS, connect, migrate, utc_text
from linersync.adapters.sqlite.repositories import SqliteSyncs, sqlite_repositories
from linersync.adapters.sqlite.schema import metadata
from tests.factories import group


def test_migrations_match_schema() -> None:
    engine = connect(":memory:")
    migrate(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    engine = connect(tmp_path / "x.db")
    migrate(engine)
    migrate(engine)


def test_file_db_uses_wal(tmp_path: Path) -> None:
    engine = connect(tmp_path / "x.db")
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_data_survives_reconnect(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    engine = connect(path)
    migrate(engine)
    g = group()
    sqlite_repositories(engine).syncs.save(g)
    engine.dispose()
    engine2 = connect(path)
    assert sqlite_repositories(engine2).syncs.get(g.sync_id) == g


def test_utc_text_orders_like_time() -> None:
    plus2 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone(timedelta(hours=2)))
    utc = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    assert utc_text(plus2) < utc_text(utc)
    assert utc_text(utc) == "2026-01-01T09:00:00.000000+00:00"
    with pytest.raises(ValueError):
        utc_text(datetime(2026, 1, 1, 9, 0))


def test_upgrade_from_0001_keeps_data() -> None:
    engine = connect(":memory:")
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0001")
    g = group()
    SqliteSyncs(engine).save(g)
    migrate(engine)
    assert SqliteSyncs(engine).get(g.sync_id) == g
    tables = set(sa.inspect(engine).get_table_names())
    assert {"backup_playlists", "backup_versions", "backup_runs", "covers"} <= tables
