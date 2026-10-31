"""Tidal sign-in: Authorization Code with PKCE (no client secret) and refresh."""

from urllib.parse import urlencode, urlsplit

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
_LOOPBACK = {"127.0.0.1", "localhost"}


def check_redirect_uri(uri: str) -> None:
    parts = urlsplit(uri)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in _LOOPBACK:
        return
    raise ValueError(
        "Tidal needs an https:// address, or http://127.0.0.1 for local use"
    )


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class TidalAuth(PkceAuth):
    def __init__(
        self, credentials: CredentialStore, token_client: HttpClient, clock: Clock
    ) -> None:
        super().__init__(SERVICE, "Tidal", TOKEN_URL, credentials, token_client, clock)
