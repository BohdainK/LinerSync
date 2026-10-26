import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from linersync.adapters.connectors.kit.plugin import (
    ConnectorContext,
    ConnectorPlugin,
    OAuthSetup,
)
from linersync.adapters.connectors.spotify.plugin import PLUGIN, SPOTIFY_PLUGIN
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.ports.setup import StepKind, check_guide, render_guide

SP = ServiceId("spotify")
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def ctx_for(
    handler: httpx.MockTransport, *, expires_in: timedelta = timedelta(hours=1)
) -> tuple[ConnectorContext, InMemoryCredentials]:
    store = InMemoryCredentials()
    store.save(
        SP,
        {
            "client_id": "a" * 32,
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_at": (NOW + expires_in).isoformat(),
        },
    )

    async def no_sleep(_: float) -> None:
        return None

    return (
        ConnectorContext(
            store, InMemoryQuota(), FixedClock(NOW), transport=handler, sleep=no_sleep
        ),
        store,
    )


def test_plugin_shape() -> None:
    assert PLUGIN is SPOTIFY_PLUGIN
    assert isinstance(SPOTIFY_PLUGIN, ConnectorPlugin)
    assert isinstance(SPOTIFY_PLUGIN, OAuthSetup)
    p = SPOTIFY_PLUGIN
    assert p.service == "spotify"
    assert (p.display_name, p.category) == ("Spotify", "streaming")
    assert p.capabilities == frozenset(Capability) - {Capability.DELETE_PLAYLIST}
    assert (p.limits.page_size, p.limits.add_batch_size, p.limits.search_cost) == (
        50,
        100,
        2,
    )


def test_login_expires_at() -> None:
    p = SPOTIFY_PLUGIN
    assert p.login_expires_at({}) is None
    assert p.login_expires_at({"authorized_at": "garbage"}) is None
    assert p.login_expires_at({"authorized_at": NOW.isoformat()}) == NOW + timedelta(
        days=182
    )


def test_guide_is_valid() -> None:
    guide = SPOTIFY_PLUGIN.setup_guide()
    assert check_guide(guide) == []
    assert [s.step_id for s in guide.steps] == [
        "before",
        "create_app",
        "client_id",
        "sign_in",
        "test",
    ]
    assert guide.steps[3].kind is StepKind.OAUTH
    assert len(guide.prerequisites) == 2


def test_guide_renders_redirect_uri() -> None:
    rendered = render_guide(
        SPOTIFY_PLUGIN.setup_guide(), {"redirect_uri": "http://127.0.0.1:8000/cb"}
    )
    step = rendered.steps[1]
    assert step.copy_values[0].label == "Redirect URI"
    assert step.copy_values[0].value == "http://127.0.0.1:8000/cb"
    assert "Web API" in step.body
    assert step.links[0].url == "https://developer.spotify.com/dashboard"


def test_validate_client_id() -> None:
    assert SPOTIFY_PLUGIN.validate({"client_id": "0123456789abcdef" * 2}) == {}
    assert "client_id" in SPOTIFY_PLUGIN.validate({"client_id": "xyz"})
    assert SPOTIFY_PLUGIN.validate({}) == {"client_id": "Required"}


def test_authorize_keeps_verifier() -> None:
    req = SPOTIFY_PLUGIN.authorize(
        {"client_id": "a" * 32}, "http://127.0.0.1:8000/cb", "state1"
    )
    assert "code_challenge=" in req.url and "state=state1" in req.url
    assert len(req.keep["code_verifier"]) >= 43
    with pytest.raises(ValueError):
        SPOTIFY_PLUGIN.authorize({"client_id": "a" * 32}, "http://localhost/cb", "s")


async def test_complete_exchanges_and_stores_user_id() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.host}{request.url.path}")
        if request.url.host == "accounts.spotify.com":
            return httpx.Response(
                200,
                json={"access_token": "A", "refresh_token": "R", "expires_in": 3600},
            )
        assert request.headers["authorization"] == "Bearer A"
        return httpx.Response(200, json={"id": "user42", "display_name": "Ann"})

    ctx, store = ctx_for(httpx.MockTransport(handler))
    await SPOTIFY_PLUGIN.complete(
        ctx, "https://x.example/cb", "code", {"code_verifier": "v" * 50}
    )
    assert seen == ["POST accounts.spotify.com/api/token", "GET api.spotify.com/v1/me"]
    saved = store.get(SP)
    assert saved["user_id"] == "user42"
    assert saved["refresh_token"] == "R"


async def test_test_connection_ok_and_expired() -> None:
    def ok(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/me"
        return httpx.Response(200, json={"id": "u1", "display_name": "Ann"})

    ctx, _ = ctx_for(httpx.MockTransport(ok))
    result = await SPOTIFY_PLUGIN.test_connection(ctx)
    assert (result.ok, result.message) == (True, "Connected")
    assert result.account_name == "Ann"

    def no_name(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "u1"})

    ctx, _ = ctx_for(httpx.MockTransport(no_name))
    assert (await SPOTIFY_PLUGIN.test_connection(ctx)).account_name == "u1"

    def expired(request: httpx.Request) -> httpx.Response:
        if request.url.host == "accounts.spotify.com":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(401, json={"error": {"status": 401}})

    ctx, _ = ctx_for(httpx.MockTransport(expired))
    result = await SPOTIFY_PLUGIN.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Sign in to Spotify again")

    ctx, _ = ctx_for(httpx.MockTransport(expired), expires_in=timedelta(seconds=-5))
    result = await SPOTIFY_PLUGIN.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Sign in to Spotify again")


async def test_test_connection_other_failure() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden-user")

    ctx, _ = ctx_for(httpx.MockTransport(boom))
    result = await SPOTIFY_PLUGIN.test_connection(ctx)
    assert result.ok is False
    assert "403" in result.message


def test_quota_exceeded_detection() -> None:
    from linersync.adapters.connectors.spotify.plugin import is_quota_exceeded

    body = json.dumps({"reason": "QUOTA_EXCEEDED"})
    assert is_quota_exceeded(httpx.Response(429, text=body))
    assert not is_quota_exceeded(httpx.Response(429, text="not json"))
    assert not is_quota_exceeded(httpx.Response(429, json={"reason": "RATE"}))
    assert not is_quota_exceeded(httpx.Response(500, text=body))


def test_create_builds_connector() -> None:
    from linersync.adapters.connectors.spotify.connector import SpotifyConnector

    ctx, _ = ctx_for(httpx.MockTransport(lambda r: httpx.Response(200)))
    connector = SPOTIFY_PLUGIN.create(ctx)
    assert isinstance(connector, SpotifyConnector)
    assert connector.service == "spotify"
    assert connector.display_name == "Spotify"
    assert connector.capabilities == SPOTIFY_PLUGIN.capabilities
    assert connector.limits == SPOTIFY_PLUGIN.limits
