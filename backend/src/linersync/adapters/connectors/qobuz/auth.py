"""Qobuz sign-in: the app ID and user auth token of a signed-in web player.

Both are pasted from the request headers of play.qobuz.com; nothing is
scraped and no password is stored. A token can't be refreshed: when Qobuz
stops accepting it, the user pastes a new one.
"""

from collections.abc import Mapping

from linersync.adapters.connectors.kit.http import HttpFailure
from linersync.adapters.connectors.qobuz.parse import SERVICE
from linersync.ports.credentials import CredentialStore


class QobuzAuth:
    def __init__(self, credentials: CredentialStore) -> None:
        self._credentials = credentials

    async def headers(self) -> Mapping[str, str]:
        creds = self._credentials.get(SERVICE)
        app_id = creds.get("app_id", "").strip()
        token = creds.get("user_auth_token", "").strip()
        if not app_id or not token:
            raise HttpFailure(
                SERVICE,
                "Qobuz sign-in is missing; sign in again",
                maybe_applied=False,
                auth_required=True,
            )
        return {"X-App-Id": app_id, "X-User-Auth-Token": token}

    async def refresh(self) -> bool:
        return False
