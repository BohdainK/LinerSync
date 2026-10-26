from datetime import datetime

from linersync.domain.tracks import ServiceId


class InMemoryQuota:
    def __init__(self) -> None:
        self._entries: list[tuple[ServiceId, int, datetime]] = []

    def record(self, service: ServiceId, units: int, at: datetime) -> None:
        self._entries.append((service, units, at))

    def used_since(self, service: ServiceId, since: datetime) -> int:
        return sum(u for s, u, at in self._entries if s == service and at >= since)
