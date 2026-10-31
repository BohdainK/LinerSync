from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from linersync.adapters.connectors.kit.http import HttpClient, HttpFailure, HttpSettings
from linersync.adapters.connectors.kit.oauth import OAuthError, PkceAuth
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.tracks import ServiceId
from linersync.ports.quota import QuotaPolicy

TIDAL = ServiceId("tidal")


async def test_messages_name_the_service() -> None:
    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    clock = FixedClock(datetime(2026, 10, 30, 12, tzinfo=UTC))
    client = HttpClient(
        TIDAL,
        "",
        settings=HttpSettings(),
        quota=InMemoryQuota(),
        quota_policy=QuotaPolicy(),
        clock=clock,
        transport=httpx.MockTransport(no_network),
    )
    store = InMemoryCredentials()
    store.save(TIDAL, {"client_id": "abc"})
    auth = PkceAuth(TIDAL, "Tidal", "https://auth.example/token", store, client, clock)
    with pytest.raises(HttpFailure) as info:
        await auth.headers()
    assert info.value.message == "Tidal sign-in expired; sign in again"
    assert info.value.auth_required is True
    await client.aclose()


def _auth(
    handler: Callable[[httpx.Request], httpx.Response], **creds: str
) -> tuple[PkceAuth, InMemoryCredentials, HttpClient]:
    clock = FixedClock(datetime(2026, 10, 30, 12, tzinfo=UTC))
    client = HttpClient(
        TIDAL,
        "",
        settings=HttpSettings(),
        quota=InMemoryQuota(),
        quota_policy=QuotaPolicy(),
        clock=clock,
        transport=httpx.MockTransport(handler),
    )
    store = InMemoryCredentials()
    store.save(TIDAL, {"client_id": "abc", **creds})
    return (
        PkceAuth(TIDAL, "Tidal", "https://auth.example/token", store, client, clock),
        store,
        client,
    )


async def test_refresh_sends_client_secret_when_stored() -> None:
    forms: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forms.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "new", "expires_in": 60})

    auth, _, client = _auth(handler, refresh_token="r", client_secret="s3cret")
    assert await auth.headers() == {"Authorization": "Bearer new"}
    assert forms[0]["client_secret"] == "s3cret"
    await client.aclose()


async def test_refresh_without_secret_sends_none() -> None:
    forms: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        forms.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "new", "expires_in": 60})

    auth, _, client = _auth(handler, refresh_token="r")
    await auth.headers()
    assert "client_secret" not in forms[0]
    await client.aclose()


@pytest.mark.parametrize(
    "code", ["authorization_pending", "slow_down", "access_denied", "expired_token"]
)
async def test_exchange_failure_keeps_only_the_oauth_error_code(code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(428, json={"error": code, "secret": "do-not-leak"})

    auth, store, client = _auth(handler)
    with pytest.raises(OAuthError) as info:
        await auth.exchange({"grant_type": "device", "device_code": "dc"})
    assert info.value.error == code
    assert "do-not-leak" not in info.value.message
    assert "refresh_token" not in store.get(TIDAL)
    await client.aclose()


async def test_exchange_saves_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "expires_in": 60}
        )

    auth, store, client = _auth(handler)
    await auth.exchange({"grant_type": "device", "device_code": "dc"})
    saved = store.get(TIDAL)
    assert (saved["access_token"], saved["refresh_token"]) == ("a", "r")
    assert saved["authorized_at"]
    await client.aclose()
