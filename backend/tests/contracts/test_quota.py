from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.sqlite.db import connect, migrate
from linersync.adapters.sqlite.quota import SqliteQuota
from linersync.domain.tracks import ServiceId
from linersync.ports.quota import QuotaRecorder

Factory = Callable[[], QuotaRecorder]
SP = ServiceId("spotify")
AM = ServiceId("apple_music")


def sqlite_quota() -> QuotaRecorder:
    e = connect(":memory:")
    migrate(e)
    return SqliteQuota(e)


pytestmark = pytest.mark.parametrize("make", [InMemoryQuota, sqlite_quota])


def test_used_since_sums_per_service(make: Factory) -> None:
    q = make()
    t = datetime(2026, 1, 1, 12, tzinfo=UTC)
    q.record(SP, 3, t)
    q.record(SP, 4, t)
    q.record(AM, 10, t)
    assert q.used_since(SP, datetime(2026, 1, 1, tzinfo=UTC)) == 7
    assert q.used_since(AM, datetime(2026, 1, 1, tzinfo=UTC)) == 10
    assert q.used_since(ServiceId("x"), datetime(2026, 1, 1, tzinfo=UTC)) == 0


def test_used_since_includes_boundary(make: Factory) -> None:
    q = make()
    t = datetime(2026, 1, 1, 12, tzinfo=UTC)
    q.record(SP, 5, t)
    assert q.used_since(SP, t) == 5


def test_used_since_compares_instants_not_strings(make: Factory) -> None:
    q = make()
    since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    plus2 = datetime.fromisoformat("2026-01-01T01:30+02:00")
    q.record(SP, 5, plus2)
    assert q.used_since(SP, since) == 0
    q.record(SP, 2, datetime.fromisoformat("2026-01-01T03:00+02:00"))
    assert q.used_since(SP, since) == 2


def test_old_rows_pruned(make: Factory) -> None:
    if make is not sqlite_quota:
        pytest.skip("sqlite only")
    from sqlalchemy import func, select

    from linersync.adapters.sqlite.schema import quota_usage

    e = connect(":memory:")
    migrate(e)
    q = SqliteQuota(e)
    now = datetime(2026, 3, 1, tzinfo=UTC)
    q.record(SP, 1, now - timedelta(days=41))
    q.record(SP, 2, now)
    with e.connect() as conn:
        assert (
            conn.execute(select(func.count()).select_from(quota_usage)).scalar_one()
            == 1
        )
    assert q.used_since(SP, now - timedelta(days=100)) == 2
