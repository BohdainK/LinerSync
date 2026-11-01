"""Tidal sign-in: Authorization Code with PKCE (no client secret) and refresh."""

from linersync.adapters.connectors.kit import oauth
from linersync.adapters.connectors.kit.http import HttpClient
from linersync.adapters.connectors.kit.oauth import PkceAuth
from linersync.adapters.connectors.tidal.parse import API_BASE, SERVICE
from linersync.ports.credentials import CredentialStore
from linersync.ports.runtime import Clock

__all__ = ["API_BASE", "SERVICE", "TidalAuth", "authorize_url", "check_redirect_uri"]

# collection.* are for the later library-sync project, asked for now so users
# don't have to sign in again then.
SCOPES = (
    "playlists.read",
    "playlists.write",
    "search.read",
    "user.read",
    "collection.read",
    "collection.write",
)
AUTHORIZE_URL = "https://login.tidal.com/authorize"
TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"


def check_redirect_uri(uri: str) -> None:
    oauth.check_redirect_uri(uri, "Tidal", allow_localhost=True)


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return oauth.authorize_url(
        AUTHORIZE_URL, SCOPES, client_id, redirect_uri, state, challenge
    )


class TidalAuth(PkceAuth):
    def __init__(
        self, credentials: CredentialStore, token_client: HttpClient, clock: Clock
    ) -> None:
        super().__init__(SERVICE, "Tidal", TOKEN_URL, credentials, token_client, clock)
