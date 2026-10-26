import importlib.metadata
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from linersync.adapters.connectors.apple_music.plugin import APPLE_MUSIC_PLUGIN, PLUGIN
from linersync.adapters.connectors.kit.plugin import (
    BrowserSignInSetup,
    ConnectorContext,
    ConnectorPlugin,
)
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId, TrackQuery
from linersync.ports.setup import FieldKind, StepKind, check_guide
from tests.adapters.connectors.apple_music.sim import AppleMusicSim

AM = ServiceId("apple_music")
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def _pem(key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def ctx_for(
    transport: httpx.AsyncBaseTransport, values: dict[str, str]
) -> tuple[ConnectorContext, InMemoryCredentials]:
    store = InMemoryCredentials()
    store.save(AM, values)

    async def no_sleep(_: float) -> None:
        return None

    ctx = ConnectorContext(
        store, InMemoryQuota(), FixedClock(NOW), transport=transport, sleep=no_sleep
    )
    return ctx, store


def test_plugin_shape() -> None:
    assert PLUGIN is APPLE_MUSIC_PLUGIN
    p = APPLE_MUSIC_PLUGIN
    assert isinstance(p, ConnectorPlugin)
    assert isinstance(p, BrowserSignInSetup)
    assert p.service == "apple_music"
    assert (p.display_name, p.category) == ("Apple Music", "streaming")
    assert p.capabilities == frozenset(
        {Capability.READ_ORDER, Capability.ADD, Capability.CREATE_PLAYLIST}
    )
    assert (p.limits.page_size, p.limits.add_batch_size, p.limits.search_cost) == (
        100,
        100,
        2,
    )


def test_guide_is_valid_and_lists_paid_membership_first() -> None:
    guide = APPLE_MUSIC_PLUGIN.setup_guide()
    assert check_guide(guide) == []
    assert guide.prerequisites[0] == (
        "A paid Apple Developer Program membership (needed to create a MusicKit key)"
    )
    steps = {s.step_id: s for s in guide.steps}
    assert [s.step_id for s in guide.steps] == [
        "team_id",
        "media_id",
        "key",
        "details",
        "sign_in",
        "test",
    ]
    assert [s.kind for s in guide.steps] == [
        StepKind.INFO,
        StepKind.INFO,
        StepKind.INFO,
        StepKind.FORM,
        StepKind.BROWSER_SIGN_IN,
        StepKind.TEST,
    ]
    assert steps["team_id"].links[0].url == "https://developer.apple.com/account"
    assert "MusicKit" in steps["media_id"].body
    assert "Media Services" in steps["key"].body
    assert ".p8" in steps["key"].body and "only once" in steps["key"].body
    fields = {f.key: f for f in steps["details"].fields}
    assert list(fields) == ["team_id", "key_id", "private_key"]
    assert fields["private_key"].kind is FieldKind.MULTILINE_SECRET
    assert "HTTPS" in steps["sign_in"].body
    assert "http://127.0.0.1" in steps["sign_in"].body


def test_validate_fields_and_key() -> None:
    p = APPLE_MUSIC_PLUGIN
    good = {
        "team_id": "ABCDE12345",
        "key_id": "KEY1234567",
        "private_key": _pem(ec.generate_private_key(ec.SECP256R1())),
    }
    assert p.validate(good) == {}
    assert p.validate({}) == {
        "team_id": "Required",
        "key_id": "Required",
        "private_key": "Required",
    }
    bad = p.validate({**good, "team_id": "abcde12345", "key_id": "SHORT"})
    assert set(bad) == {"team_id", "key_id"}
    rsa_pem = _pem(rsa.generate_private_key(65537, 2048))
    problems = p.validate({**good, "private_key": rsa_pem})
    assert "P-256" in problems["private_key"]
    assert "private_key" in p.validate({**good, "private_key": "not a key"})


def test_sign_in_config_token_decodes() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    config = APPLE_MUSIC_PLUGIN.sign_in_config(
        {"team_id": "ABCDE12345", "key_id": "KEY1234567", "private_key": _pem(key)}
    )
    assert config["app_name"] == "LinerSync"
    assert config["app_build"] == importlib.metadata.version("linersync")
    token = config["developer_token"]
    assert jwt.get_unverified_header(token)["kid"] == "KEY1234567"
    claims = jwt.decode(token, key.public_key(), algorithms=["ES256"])
    assert claims["iss"] == "ABCDE12345"


def test_accept_sign_in_saves_and_rejects_empty() -> None:
    ctx, store = ctx_for(httpx.MockTransport(lambda r: httpx.Response(500)), {})
    with pytest.raises(ValueError):
        APPLE_MUSIC_PLUGIN.accept_sign_in(ctx, {})
    with pytest.raises(ValueError):
        APPLE_MUSIC_PLUGIN.accept_sign_in(ctx, {"music_user_token": "  "})
    assert store.get(AM) == {}
    APPLE_MUSIC_PLUGIN.accept_sign_in(ctx, {"music_user_token": "mut-1"})
    saved = store.get(AM)
    assert saved["music_user_token"] == "mut-1"
    assert saved["music_user_token_saved_at"] == NOW.isoformat()


async def test_accept_sign_in_forgets_storefront_of_previous_apple_id() -> None:
    # A sign-in may be a different Apple ID in another country.
    sim = AppleMusicSim(catalog=(), storefront="nl")
    ctx, store = ctx_for(sim.transport, sim.credentials(storefront="us"))
    APPLE_MUSIC_PLUGIN.accept_sign_in(ctx, {"music_user_token": sim.user_token})
    assert store.get(AM)["storefront"] == ""
    connector = APPLE_MUSIC_PLUGIN.create(ctx)
    await connector.search(TrackQuery("Anything", ()))
    assert [r.url.path for r in sim.requests] == [
        "/v1/me/storefront",
        "/v1/catalog/nl/search",
    ]
    assert store.get(AM)["storefront"] == "nl"


def test_login_expires_at() -> None:
    p = APPLE_MUSIC_PLUGIN
    assert p.login_expires_at({}) is None
    assert p.login_expires_at({"music_user_token_saved_at": "garbage"}) is None
    saved = {"music_user_token_saved_at": NOW.isoformat()}
    assert p.login_expires_at(saved) == NOW + timedelta(days=180)


async def test_test_connection_messages() -> None:
    p = APPLE_MUSIC_PLUGIN
    sim = AppleMusicSim(catalog=(), storefront="nl")

    ctx, store = ctx_for(sim.transport, sim.credentials())
    result = await p.test_connection(ctx)
    assert (result.ok, result.message) == (True, "Connected")
    assert store.get(AM)["storefront"] == "nl"
    paths = [r.url.path for r in sim.requests]
    assert paths == ["/v1/storefronts/us", "/v1/me/storefront"]

    wrong_key = {**sim.credentials(), "key_id": "KEYOTHER01"}
    ctx, _ = ctx_for(sim.transport, wrong_key)
    result = await p.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Check Team ID, Key ID and key file")

    unreadable = {**sim.credentials(), "private_key": "not a key"}
    ctx, _ = ctx_for(sim.transport, unreadable)
    result = await p.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Check Team ID, Key ID and key file")

    sim.revoke_user_token()
    ctx, _ = ctx_for(sim.transport, sim.credentials())
    result = await p.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Sign in to Apple Music again")

    no_user = {
        k: v for k, v in sim.credentials().items() if not k.startswith("music_user")
    }
    sim.requests.clear()
    ctx, _ = ctx_for(sim.transport, no_user)
    result = await p.test_connection(ctx)
    assert result.ok is False
    assert "Sign in to Apple Music" in result.message
    assert [r.url.path for r in sim.requests] == ["/v1/storefronts/us"]


async def test_test_connection_other_failure() -> None:
    sim = AppleMusicSim(catalog=())
    sim.fail_next("GET", r"/v1/storefronts/us", 400)
    ctx, _ = ctx_for(sim.transport, sim.credentials())
    result = await APPLE_MUSIC_PLUGIN.test_connection(ctx)
    assert result.ok is False
    assert "400" in result.message


async def test_auth_headers_and_refresh() -> None:
    from linersync.adapters.connectors.apple_music.plugin import AppleMusicAuth

    sim = AppleMusicSim(catalog=())
    store = InMemoryCredentials()
    store.save(AM, sim.credentials())
    clock = FixedClock(NOW)
    auth = AppleMusicAuth(store, clock)
    headers = await auth.headers()
    assert headers["Music-User-Token"] == sim.user_token
    first = headers["Authorization"]
    assert first.startswith("Bearer ")
    clock.advance(timedelta(seconds=5))
    assert (await auth.headers())["Authorization"] == first  # cached
    assert await auth.refresh() is True
    assert (await auth.headers())["Authorization"] != first

    no_user = InMemoryCredentials()
    no_user.save(
        AM, {k: v for k, v in sim.credentials().items() if k != "music_user_token"}
    )
    assert "Music-User-Token" not in await AppleMusicAuth(no_user, clock).headers()


def test_create_builds_connector() -> None:
    from linersync.adapters.connectors.apple_music.connector import (
        AppleMusicConnector,
    )

    sim = AppleMusicSim(catalog=())
    ctx, _ = ctx_for(sim.transport, sim.credentials())
    connector = APPLE_MUSIC_PLUGIN.create(ctx)
    assert isinstance(connector, AppleMusicConnector)
    assert connector.service == "apple_music"
    assert connector.display_name == "Apple Music"
    assert connector.capabilities == APPLE_MUSIC_PLUGIN.capabilities
    assert connector.limits == APPLE_MUSIC_PLUGIN.limits
