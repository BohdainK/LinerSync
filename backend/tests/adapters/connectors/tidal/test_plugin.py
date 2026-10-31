from datetime import UTC, datetime

import pytest

from linersync.adapters.connectors.kit.plugin import (
    ConnectorContext,
    ConnectorPlugin,
    OAuthSetup,
)
from linersync.adapters.connectors.tidal.plugin import PLUGIN, TIDAL_PLUGIN
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.ports.setup import StepKind, check_guide
from tests.adapters.connectors.tidal.sim import TidalSim
from tests.factories import track

TD = ServiceId("tidal")
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
CATALOG = (track("tidal", "1000", title="Alpha", isrc="NLSIM0000000"),)


def ctx_for(
    sim: TidalSim, **creds: str
) -> tuple[ConnectorContext, InMemoryCredentials]:
    store = InMemoryCredentials()
    store.save(TD, {"client_id": "client", **creds})

    async def no_sleep(_: float) -> None:
        return None

    ctx = ConnectorContext(
        store, InMemoryQuota(), FixedClock(NOW), transport=sim.transport, sleep=no_sleep
    )
    return ctx, store


def signed_in(
    sim: TidalSim, **extra: str
) -> tuple[ConnectorContext, InMemoryCredentials]:
    return ctx_for(
        sim,
        access_token=sim.access_token,
        refresh_token="tidal-refresh",
        expires_at="2026-10-31T12:00:00+00:00",
        **extra,
    )


@pytest.fixture
def sim() -> TidalSim:
    return TidalSim(catalog=CATALOG)


def test_plugin_shape() -> None:
    assert PLUGIN is TIDAL_PLUGIN
    assert isinstance(TIDAL_PLUGIN, ConnectorPlugin)
    assert isinstance(TIDAL_PLUGIN, OAuthSetup)
    p = TIDAL_PLUGIN
    assert (p.service, p.display_name, p.category) == ("tidal", "Tidal", "streaming")
    assert p.capabilities == frozenset(Capability) - {Capability.DELETE_PLAYLIST}
    assert (p.limits.page_size, p.limits.add_batch_size, p.limits.search_cost) == (
        50,
        50,
        2,
    )
    assert p.login_expires_at({"authorized_at": NOW.isoformat()}) is None


def test_guide_is_valid() -> None:
    guide = TIDAL_PLUGIN.setup_guide()
    assert check_guide(guide) == []
    assert [(s.step_id, s.kind) for s in guide.steps] == [
        ("before", StepKind.INFO),
        ("create_app", StepKind.INFO),
        ("client_id", StepKind.FORM),
        ("sign_in", StepKind.OAUTH),
        ("test", StepKind.TEST),
    ]


def test_validate_client_id() -> None:
    assert TIDAL_PLUGIN.validate({"client_id": "AbC123xyz"}) == {}
    assert "client_id" in TIDAL_PLUGIN.validate({"client_id": "has spaces!"})


def test_authorize_keeps_verifier() -> None:
    req = TIDAL_PLUGIN.authorize({"client_id": "client"}, "https://ls.example/cb", "st")
    assert req.url.startswith("https://login.tidal.com/authorize?")
    assert len(req.keep["code_verifier"]) >= 43


async def test_complete_saves_user_and_country(sim: TidalSim) -> None:
    ctx, store = ctx_for(sim)
    await TIDAL_PLUGIN.complete(
        ctx, "https://ls.example/cb", "code", {"code_verifier": "v" * 43}
    )
    saved = store.get(TD)
    assert saved["refresh_token"].startswith("tidal-refresh-")
    assert saved["access_token"] == sim.access_token
    assert (saved["user_id"], saved["username"], saved["country_code"]) == (
        "123456789",
        "sam",
        "NL",
    )
    assert saved["authorized_at"]


async def test_connection_ok(sim: TidalSim) -> None:
    ctx, _ = signed_in(sim)
    result = await TIDAL_PLUGIN.test_connection(ctx)
    assert (result.ok, result.message, result.account_name) == (
        True,
        "Connected",
        "sam",
    )


async def test_connection_after_revoked_refresh(sim: TidalSim) -> None:
    ctx, store = signed_in(sim)
    sim.expire_login()
    result = await TIDAL_PLUGIN.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Sign in to Tidal again")
    assert store.get(TD)["refresh_token"] == ""


async def test_connection_unreachable_does_not_raise(sim: TidalSim) -> None:
    ctx, _ = signed_in(sim)
    sim.fail_next("GET", r"/users/me", 503, times=10)
    result = await TIDAL_PLUGIN.test_connection(ctx)
    assert result.ok is False


async def test_create_resolves_country_lazily(sim: TidalSim) -> None:
    ctx, store = signed_in(sim)
    sim.add_playlist("Mix", ["1000"])
    metas = await TIDAL_PLUGIN.create(ctx).list_playlists()
    assert [m.name for m in metas] == ["Mix"]
    assert store.get(TD)["country_code"] == "NL"
