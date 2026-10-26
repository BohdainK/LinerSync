import asyncio
import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure, HttpSettings
from linersync.adapters.connectors.spotify.auth import (
    AUTHORIZE_URL,
    SCOPES,
    TOKEN_URL,
    SpotifyAuth,
    authorize_url,
    check_redirect_uri,
    pkce_pair,
)
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.tracks import ServiceId
from linersync.ports.quota import QuotaPolicy

SP = ServiceId("spotify")
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
OLD_REFRESH = "old-refresh-token-value"


class TokenServer:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        await asyncio.sleep(0)  # a real network hop yields
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def token_ok(**extra: object) -> httpx.Response:
    body: dict[str, object] = {
        "access_token": "new-access",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    body.update(extra)
    return httpx.Response(200, json=body)


def make(
    server: TokenServer, **creds: str
) -> tuple[SpotifyAuth, InMemoryCredentials, FixedClock]:
    store = InMemoryCredentials()
    values = {
        "client_id": "a" * 32,
        "access_token": "old-access",
        "refresh_token": OLD_REFRESH,
        "expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    values.update(creds)
    store.save(SP, values)
    clock = FixedClock(NOW)
    client = HttpClient(
        SP,
        TOKEN_URL,
        settings=HttpSettings(rate_per_second=1000, burst=1000),
        quota=InMemoryQuota(),
        quota_policy=QuotaPolicy(),
        clock=clock,
        transport=httpx.MockTransport(server.handler),
    )
    return SpotifyAuth(store, client, clock), store, clock


def form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def test_pkce_rfc7636_example() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    v, challenge = pkce_pair(verifier)
    assert v == verifier
    assert challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_pkce_generated_pair_is_consistent() -> None:
    v, c = pkce_pair()
    assert 43 <= len(v) <= 128
    digest = hashlib.sha256(v.encode("ascii")).digest()
    assert c == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    with pytest.raises(ValueError):
        pkce_pair("short")


def test_authorize_url_has_pkce_and_scopes() -> None:
    url = authorize_url("c" * 32, "https://x.example/cb", "st", "chal")
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == AUTHORIZE_URL
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256"
    assert q["code_challenge"] == "chal"
    assert q["scope"] == " ".join(SCOPES)
    assert q["state"] == "st"
    assert q["redirect_uri"] == "https://x.example/cb"
    assert q["client_id"] == "c" * 32


def test_redirect_uri_rules() -> None:
    check_redirect_uri("https://linersync.example/cb")
    check_redirect_uri("http://127.0.0.1:8000/cb")
    with pytest.raises(ValueError, match="127.0.0.1"):
        check_redirect_uri("http://localhost:8000")
    with pytest.raises(ValueError):
        check_redirect_uri("http://192.168.1.2")


async def test_exchange_code_saves_tokens() -> None:
    server = TokenServer(token_ok(refresh_token="first-refresh"))
    auth, store, _ = make(server)
    await auth.exchange_code("the-code", "https://x.example/cb", "ver")
    saved = store.get(SP)
    assert saved["access_token"] == "new-access"
    assert saved["refresh_token"] == "first-refresh"
    assert saved["expires_at"] == (NOW + timedelta(seconds=3600)).isoformat()
    assert datetime.fromisoformat(saved["authorized_at"]) == NOW
    (req,) = server.requests
    assert req.method == "POST"
    assert str(req.url) == TOKEN_URL
    assert form(req) == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": "https://x.example/cb",
        "client_id": "a" * 32,
        "code_verifier": "ver",
    }
    assert "authorization" not in req.headers


async def test_expired_access_token_refreshed_before_request() -> None:
    server = TokenServer(token_ok())
    auth, store, _ = make(server, expires_at=(NOW + timedelta(seconds=30)).isoformat())
    headers = await auth.headers()
    assert headers == {"Authorization": "Bearer new-access"}
    assert form(server.requests[0]) == {
        "grant_type": "refresh_token",
        "refresh_token": OLD_REFRESH,
        "client_id": "a" * 32,
    }
    assert store.get(SP)["refresh_token"] == OLD_REFRESH  # none returned: kept


async def test_fresh_token_not_refreshed() -> None:
    server = TokenServer(token_ok())
    auth, _, _ = make(server)
    assert await auth.headers() == {"Authorization": "Bearer old-access"}
    assert server.requests == []


async def test_rotated_refresh_token_saved() -> None:
    server = TokenServer(token_ok(refresh_token="rotated"))
    auth, store, _ = make(server)
    assert await auth.refresh() is True
    assert store.get(SP)["refresh_token"] == "rotated"
    assert store.get(SP)["access_token"] == "new-access"


async def test_invalid_grant_is_auth_required_and_not_retried() -> None:
    server = TokenServer(
        httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": OLD_REFRESH},
        )
    )
    auth, _, _ = make(server)
    with pytest.raises(HttpFailure) as info:
        await auth.refresh()
    assert info.value.auth_required
    assert info.value.message == "Spotify sign-in expired; sign in again"
    assert len(server.requests) == 1
    assert OLD_REFRESH not in str(info.value)
    assert info.value.__context__ is None
    assert info.value.__cause__ is None


async def test_dead_refresh_token_discarded_and_not_retried() -> None:
    server = TokenServer(httpx.Response(400, json={"error": "invalid_grant"}))
    auth, store, _ = make(server)
    with pytest.raises(HttpFailure):
        await auth.refresh()
    assert store.get(SP)["refresh_token"] == ""
    assert store.get(SP)["access_token"] == ""
    with pytest.raises(HttpFailure) as again_headers:
        await auth.headers()
    with pytest.raises(HttpFailure) as again_refresh:
        await auth.refresh()
    assert again_headers.value.auth_required
    assert again_refresh.value.auth_required
    assert len(server.requests) == 1


async def test_other_token_errors_do_not_leak_body() -> None:
    server = TokenServer(
        httpx.Response(400, json={"error": "invalid_client", "leak": OLD_REFRESH})
    )
    auth, _, _ = make(server)
    with pytest.raises(HttpFailure) as info:
        await auth.refresh()
    assert not info.value.auth_required
    assert OLD_REFRESH not in str(info.value)
    assert "leak" not in str(info.value)
    assert info.value.__context__ is None
    assert info.value.__cause__ is None


async def test_concurrent_refresh_happens_once() -> None:
    server = TokenServer(token_ok())
    auth, _, _ = make(server, expires_at=NOW.isoformat())
    results = await asyncio.gather(*(auth.headers() for _ in range(5)))
    assert all(r == {"Authorization": "Bearer new-access"} for r in results)
    assert len(server.requests) == 1


async def test_concurrent_401_refreshes_once() -> None:
    server = TokenServer(token_ok())
    auth, _, _ = make(server)
    assert tuple(await asyncio.gather(auth.refresh(), auth.refresh())) == (True, True)
    assert len(server.requests) == 1
