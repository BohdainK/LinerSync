from datetime import UTC, datetime, timedelta

import pytest

from linersync.adapters.connectors.kit.plugin import (
    ConnectorContext,
    ConnectorPlugin,
    DeviceCodeSetup,
)
from linersync.adapters.connectors.youtube_music.plugin import (
    PLUGIN,
    YouTubeMusicPlugin,
    YTAuthMode,
    auth_mode_from_env,
)
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.tracks import ServiceId
from linersync.ports.setup import FieldKind, StepKind, check_guide
from tests.adapters.connectors.youtube_music.sim import YouTubeMusicSim
from tests.factories import track

YT = ServiceId("youtube_music")
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
CATALOG = (track("youtube_music", "vid00000000", title="Alpha"),)
HEADERS = YouTubeMusicPlugin(YTAuthMode.HEADERS)
OAUTH = YouTubeMusicPlugin(YTAuthMode.OAUTH)


def ctx_for(
    sim: YouTubeMusicSim, clock: FixedClock | None = None, **creds: str
) -> tuple[ConnectorContext, InMemoryCredentials]:
    store = InMemoryCredentials()
    if creds:
        store.save(YT, creds)

    async def no_sleep(_: float) -> None:
        return None

    ctx = ConnectorContext(
        store,
        InMemoryQuota(),
        clock or FixedClock(NOW),
        transport=sim.transport,
        sleep=no_sleep,
    )
    return ctx, store


@pytest.fixture
def sim() -> YouTubeMusicSim:
    return YouTubeMusicSim(catalog=CATALOG)


def test_mode_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINERSYNC_YTMUSIC_AUTH", raising=False)
    assert auth_mode_from_env() is YTAuthMode.HEADERS
    monkeypatch.setenv("LINERSYNC_YTMUSIC_AUTH", " OAuth ")
    assert auth_mode_from_env() is YTAuthMode.OAUTH
    monkeypatch.setenv("LINERSYNC_YTMUSIC_AUTH", "nonsense")
    assert auth_mode_from_env() is YTAuthMode.HEADERS


def test_plugin_shape() -> None:
    assert isinstance(PLUGIN, ConnectorPlugin)
    assert (HEADERS.service, HEADERS.display_name, HEADERS.category) == (
        "youtube_music",
        "YouTube Music",
        "streaming",
    )
    assert isinstance(OAUTH, DeviceCodeSetup)


def test_headers_guide() -> None:
    guide = HEADERS.setup_guide()
    assert check_guide(guide) == []
    assert [s.kind for s in guide.steps] == [
        StepKind.INFO,
        StepKind.FORM,
        StepKind.TEST,
    ]
    cookie, auth_user = guide.fields()
    assert (cookie.key, cookie.kind) == ("cookie", FieldKind.MULTILINE_SECRET)
    assert auth_user.key == "auth_user"


def test_oauth_guide() -> None:
    guide = OAUTH.setup_guide()
    assert check_guide(guide) == []
    assert [s.kind for s in guide.steps] == [
        StepKind.INFO,
        StepKind.INFO,
        StepKind.FORM,
        StepKind.DEVICE_CODE,
        StepKind.TEST,
    ]
    assert [f.key for f in guide.fields()] == ["client_id", "client_secret"]
    # A consent screen left in "Testing" ends the sign-in after 7 days.
    create = next(s for s in guide.steps if s.step_id == "create_client")
    assert "In production" in create.body
    assert "7 days" in create.body


def test_validate_rejects_cookie_without_sapisid() -> None:
    problems = HEADERS.validate({"cookie": "SID=1; HSID=2"})
    assert problems == {
        "cookie": "The cookie has no SAPISID; copy it from a signed-in "
        "music.youtube.com tab"
    }
    assert HEADERS.validate({"cookie": "SID=1; SAPISID=abc"}) == {}


async def test_cookie_connection_ok_and_remembers_when_seen(
    sim: YouTubeMusicSim,
) -> None:
    ctx, store = ctx_for(sim, cookie=sim.cookie)
    result = await HEADERS.test_connection(ctx)
    assert (result.ok, result.message, result.account_name) == (
        True,
        "Connected",
        "Sam Example",
    )
    assert HEADERS.login_expires_at(store.get(YT)) == NOW + timedelta(days=30)


async def test_cookie_seen_at_resets_only_for_a_new_cookie(
    sim: YouTubeMusicSim,
) -> None:
    clock = FixedClock(NOW)
    ctx, store = ctx_for(sim, clock, cookie=sim.cookie)
    await HEADERS.test_connection(ctx)
    clock.advance(timedelta(days=3))
    await HEADERS.test_connection(ctx)
    assert HEADERS.login_expires_at(store.get(YT)) == NOW + timedelta(days=30)
    store.save(YT, {"cookie": sim.cookie + "; NEW=1"})
    assert HEADERS.login_expires_at(store.get(YT)) is None
    await HEADERS.test_connection(ctx)
    assert HEADERS.login_expires_at(store.get(YT)) == NOW + timedelta(days=33)


async def test_signed_out_cookie_fails(sim: YouTubeMusicSim) -> None:
    ctx, _ = ctx_for(sim, cookie=sim.cookie)
    sim.expire_login()
    result = await HEADERS.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Sign in to YouTube Music again")


async def test_cookie_connector_reads(sim: YouTubeMusicSim) -> None:
    ctx, _ = ctx_for(sim, cookie=sim.cookie)
    pid = sim.add_playlist("Mix", ["vid00000000"])
    snap = await HEADERS.create(ctx).read_playlist(pid)
    assert snap.track_ids == ("vid00000000",)


async def test_device_flow(sim: YouTubeMusicSim) -> None:
    ctx, store = ctx_for(sim, client_id=sim.client_id, client_secret=sim.client_secret)
    start = await OAUTH.start_device_code(ctx)
    assert (start.user_code, start.verification_url) == (
        "ABCD-EFGH",
        "https://www.google.com/device",
    )
    assert (start.interval_s, start.expires_in_s) == (5, 1800)
    sim.device.outcomes = ["authorization_pending", "slow_down", "ok"]
    assert (await OAUTH.poll_device_code(ctx, start.keep)).status == "pending"
    assert (await OAUTH.poll_device_code(ctx, start.keep)).status == "pending"
    assert (await OAUTH.poll_device_code(ctx, start.keep)).status == "done"
    saved = store.get(YT)
    assert saved["refresh_token"].startswith("yt-refresh-")
    result = await OAUTH.test_connection(ctx)
    assert result.ok and result.account_name == "Sam Example"


@pytest.mark.parametrize(
    ("outcome", "status"), [("access_denied", "denied"), ("expired_token", "expired")]
)
async def test_device_flow_ends(
    sim: YouTubeMusicSim, outcome: str, status: str
) -> None:
    ctx, _ = ctx_for(sim, client_id=sim.client_id, client_secret=sim.client_secret)
    start = await OAUTH.start_device_code(ctx)
    sim.device.outcomes = [outcome]
    poll = await OAUTH.poll_device_code(ctx, start.keep)
    assert poll.status == status
    assert poll.message


async def test_device_start_with_bad_client_is_a_clear_error(
    sim: YouTubeMusicSim,
) -> None:
    ctx, _ = ctx_for(
        sim, client_id="wrong.apps.googleusercontent.com", client_secret="x"
    )
    with pytest.raises(ValueError, match="client ID"):
        await OAUTH.start_device_code(ctx)


async def test_oauth_refresh_uses_client_secret(sim: YouTubeMusicSim) -> None:
    ctx, store = ctx_for(
        sim,
        client_id=sim.client_id,
        client_secret=sim.client_secret,
        refresh_token="yt-refresh-0",
    )
    sim.add_playlist("Mix", ["vid00000000"])
    metas = await OAUTH.create(ctx).list_playlists()
    assert [m.name for m in metas] == ["Mix"]
    assert store.get(YT)["access_token"] == sim.access_token


def test_cookie_expiry_is_an_estimate() -> None:
    from linersync.adapters.connectors.kit.plugin import is_login_expiry_estimated

    assert is_login_expiry_estimated(HEADERS)


def test_oauth_login_has_no_known_expiry() -> None:
    assert OAUTH.login_expires_at({"refresh_token": "r"}) is None


async def test_slow_down_raises_the_poll_interval(sim: YouTubeMusicSim) -> None:
    ctx, _ = ctx_for(sim, client_id=sim.client_id, client_secret=sim.client_secret)
    start = await OAUTH.start_device_code(ctx)
    sim.device.outcomes = ["slow_down", "slow_down", "authorization_pending"]
    first = await OAUTH.poll_device_code(ctx, start.keep)
    assert (first.status, first.interval_s) == ("pending", 10)
    keep = {**start.keep, "interval_s": str(first.interval_s)}
    second = await OAUTH.poll_device_code(ctx, keep)
    assert (second.status, second.interval_s) == ("pending", 15)
    third = await OAUTH.poll_device_code(ctx, keep)
    assert (third.status, third.interval_s) == ("pending", None)


async def test_used_device_code_ends_the_flow(sim: YouTubeMusicSim) -> None:
    ctx, _ = ctx_for(sim, client_id=sim.client_id, client_secret=sim.client_secret)
    start = await OAUTH.start_device_code(ctx)
    sim.device.outcomes = ["invalid_grant"]
    poll = await OAUTH.poll_device_code(ctx, start.keep)
    assert poll.status == "expired"
    assert "new code" in poll.message


async def test_cookie_mode_refuses_device_code(sim: YouTubeMusicSim) -> None:
    ctx, _ = ctx_for(sim, cookie=sim.cookie)
    assert not HEADERS.uses_device_code
    assert OAUTH.uses_device_code
    with pytest.raises(ValueError, match="cookie"):
        await HEADERS.start_device_code(ctx)
    with pytest.raises(ValueError, match="cookie"):
        await HEADERS.poll_device_code(ctx, {"device_code": "x"})


def test_headers_guide_asks_which_signed_in_account() -> None:
    form = next(s for s in HEADERS.setup_guide().steps if s.step_id == "cookie")
    field = next(f for f in form.fields if f.key == "auth_user")
    assert not field.required
    assert HEADERS.validate({"cookie": "SAPISID=a", "auth_user": "2"}) == {}
    assert "auth_user" in HEADERS.validate({"cookie": "SAPISID=a", "auth_user": "x"})
