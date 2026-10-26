from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from linersync.domain.tracks import ServiceId


@dataclass(frozen=True, slots=True)
class QuotaPolicy:
    daily_limit: int | None = None
    reset_timezone: str = "UTC"


class QuotaRecorder(Protocol):
    def record(self, service: ServiceId, units: int, at: datetime) -> None: ...

    def used_since(self, service: ServiceId, since: datetime) -> int: ...


def quota_day_start(now: datetime, policy: QuotaPolicy) -> datetime:
    """The start of the quota day containing `now`, as an aware UTC datetime."""
    zone = ZoneInfo(policy.reset_timezone)
    local = now.astimezone(zone)
    midnight = datetime(local.year, local.month, local.day, tzinfo=zone)
    return midnight.astimezone(UTC)
