"""Jellyfin API-key auth: a static header, nothing to refresh."""

from collections.abc import Mapping


class ApiKeyAuth:
    def __init__(self, api_key: str) -> None:
        self._header = f'MediaBrowser Token="{api_key}"'

    async def headers(self) -> Mapping[str, str]:
        return {"Authorization": self._header}

    async def refresh(self) -> bool:
        """A rejected key needs a new one from the user."""
        return False
