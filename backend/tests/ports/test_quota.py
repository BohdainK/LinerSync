from datetime import UTC, datetime, timedelta

from linersync.adapters.memory.quota import InMemoryQuota
from linersync.domain.tracks import ServiceId
from linersync.ports.quota import QuotaPolicy, quota_day_start

SP = ServiceId("spotify")


def test_quota_day_start() -> None:
    now = datetime(2026, 3, 8, 12, tzinfo=UTC)
    assert quota_day_start(now, QuotaPolicy()) == datetime(2026, 3, 8, tzinfo=UTC)
    la = quota_day_start(now, QuotaPolicy(reset_timezone="America/Los_Angeles"))
    assert la == datetime(2026, 3, 8, 8, tzinfo=UTC)
    assert la.utcoffset() == timedelta(0)


def test_in_memory_quota_used_since() -> None:
    q = InMemoryQuota()
    t = datetime(2026, 3, 8, 12, tzinfo=UTC)
    q.record(SP, 3, t - timedelta(hours=2))
    q.record(SP, 4, t)
    q.record(ServiceId("apple_music"), 9, t)
    assert q.used_since(SP, t - timedelta(hours=1)) == 4
    assert q.used_since(SP, t - timedelta(hours=3)) == 7
    assert q.used_since(SP, t + timedelta(hours=1)) == 0
