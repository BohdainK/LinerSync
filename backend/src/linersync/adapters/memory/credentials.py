from collections.abc import Mapping

from linersync.domain.tracks import ServiceId


class InMemoryCredentials:
    def __init__(self) -> None:
        self._data: dict[ServiceId, dict[str, str]] = {}

    def get(self, service: ServiceId) -> dict[str, str]:
        return dict(self._data.get(service, {}))

    def save(self, service: ServiceId, values: Mapping[str, str]) -> None:
        self._data.setdefault(service, {}).update(values)

    def delete(self, service: ServiceId) -> None:
        self._data.pop(service, None)
