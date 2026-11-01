"""Spotify sign-in: Authorization Code with PKCE (no client secret) and refresh."""

from datetime import timedelta

from linersync.adapters.connectors.kit import oauth
from linersync.adapters.connectors.kit.http import HttpClient
from linersync.adapters.connectors.kit.oauth import PkceAuth, pkce_pair
from linersync.adapters.connectors.spotify.parse import API_BASE, SERVICE
from linersync.ports.credentials import CredentialStore
from linersync.ports.runtime import Clock

SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
    "ugc-image-upload",
    "user-read-private",  # search uses market=from_token
)
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
LOGIN_LIFETIME = timedelta(days=182)

__all__ = [
    "API_BASE",
    "SERVICE",
    "SpotifyAuth",
    "authorize_url",
    "check_redirect_uri",
    "pkce_pair",
]


def check_redirect_uri(uri: str) -> None:
    oauth.check_redirect_uri(uri, "Spotify", allow_localhost=False)


def authorize_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return oauth.authorize_url(
        AUTHORIZE_URL, SCOPES, client_id, redirect_uri, state, challenge
    )


class SpotifyAuth(PkceAuth):
    def __init__(
        self, credentials: CredentialStore, token_client: HttpClient, clock: Clock
    ) -> None:
        super().__init__(
            SERVICE, "Spotify", TOKEN_URL, credentials, token_client, clock
        )
