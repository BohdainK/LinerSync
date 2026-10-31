"""Spotify sign-in: Authorization Code with PKCE (no client secret) and refresh."""

from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from linersync.adapters.connectors.kit.http import HttpClient
from linersync.adapters.connectors.kit.oauth import PkceAuth, pkce_pair
from linersync.domain.tracks import ServiceId
from linersync.ports.credentials import CredentialStore
from linersync.ports.runtime import Clock

SERVICE = ServiceId("spotify")
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
API_BASE = "https://api.spotify.com/v1"
LOGIN_LIFETIME = timedelta(days=182)

__all__ = ["SpotifyAuth", "authorize_url", "check_redirect_uri", "pkce_pair"]


def check_redirect_uri(uri: str) -> None:
    parts = urlsplit(uri)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname == "127.0.0.1":
        return
    if parts.scheme == "http" and parts.hostname == "localhost":
        raise ValueError(
            "Spotify doesn't accept http://localhost; use http://127.0.0.1 instead"
        )
    raise ValueError(
        "Spotify needs an https:// address, or http://127.0.0.1 for local use"
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


class SpotifyAuth(PkceAuth):
    def __init__(
        self, credentials: CredentialStore, token_client: HttpClient, clock: Clock
    ) -> None:
        super().__init__(
            SERVICE, "Spotify", TOKEN_URL, credentials, token_client, clock
        )
