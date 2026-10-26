from collections.abc import Mapping
from typing import Protocol

from linersync.domain.tracks import ServiceId


class CredentialStore(Protocol):
    def get(self, service: ServiceId) -> dict[str, str]:
        """Stored values, or {} when none. Returns a copy."""
        ...

    def save(self, service: ServiceId, values: Mapping[str, str]) -> None:
        """Merge: given keys replace stored ones, other keys are kept."""
        ...

    def delete(self, service: ServiceId) -> None: ...
