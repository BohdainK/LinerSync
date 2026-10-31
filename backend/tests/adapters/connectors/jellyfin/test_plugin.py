from dataclasses import replace
from datetime import UTC, datetime

import httpx

from linersync.adapters.connectors.jellyfin.parse import SERVICE
from linersync.adapters.connectors.jellyfin.plugin import JELLYFIN_PLUGIN, PLUGIN
from linersync.adapters.connectors.kit.plugin import ConnectorContext, ConnectorPlugin
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.capabilities import Capability
from linersync.ports.setup import check_guide
from tests.adapters.connectors.jellyfin.sim import JellyfinSim
from tests.factories import track

CATALOG = (track("jellyfin", f"{1:032x}", title="Alpha"),)
GOOD = {"server_url": "https://h/jellyfin", "api_key": "a" * 32, "username": "Sam"}


async def _no_sleep(_: float) -> None:
    return None


def _ctx(
    transport: httpx.AsyncBaseTransport, values: dict[str, str]
) -> tuple[ConnectorContext, InMemoryCredentials]:
    store = InMemoryCredentials()
    store.save(SERVICE, values)
    ctx = ConnectorContext(
        store,
        InMemoryQuota(),
        FixedClock(datetime(2026, 10, 30, 12, tzinfo=UTC)),
        transport=transport,
        sleep=_no_sleep,
    )
    return ctx, store


def test_plugin_shape() -> None:
    assert PLUGIN is JELLYFIN_PLUGIN
    assert isinstance(PLUGIN, ConnectorPlugin)
    assert PLUGIN.category == "self_hosted"
    assert PLUGIN.capabilities == frozenset({Capability.READ_ORDER})
    assert PLUGIN.login_expires_at(GOOD) is None


def test_guide_is_valid() -> None:
    assert check_guide(PLUGIN.setup_guide()) == []


def test_validate_rejects_url_without_scheme() -> None:
    assert PLUGIN.validate(GOOD) == {}
    assert "server_url" in PLUGIN.validate({**GOOD, "server_url": "h.local"})
    assert "api_key" in PLUGIN.validate({**GOOD, "api_key": "nope"})


async def test_trailing_slash_url_still_reaches_server() -> None:
    sim = JellyfinSim(catalog=CATALOG)
    values = {**sim.credentials(), "server_url": sim.credentials()["server_url"] + "/"}
    ctx, _ = _ctx(sim.transport, values)
    result = await PLUGIN.test_connection(ctx)
    assert result.ok, result.message
    assert all("//Users" not in str(r.url) for r in sim.requests)


async def test_connection_ok() -> None:
    sim = JellyfinSim(catalog=CATALOG)
    ctx, store = _ctx(sim.transport, sim.credentials())
    result = await PLUGIN.test_connection(ctx)
    assert (result.ok, result.message, result.account_name) == (
        True,
        "Connected",
        "Sam",
    )
    assert store.get(SERVICE)["user_id"] == "u" * 32


async def test_connection_matches_username_case_insensitively() -> None:
    sim = JellyfinSim(catalog=CATALOG)
    ctx, _ = _ctx(sim.transport, sim.credentials(username="sAM"))
    assert (await PLUGIN.test_connection(ctx)).ok


async def test_connection_unknown_user() -> None:
    sim = JellyfinSim(catalog=CATALOG)
    ctx, _ = _ctx(sim.transport, sim.credentials(username="Kim"))
    result = await PLUGIN.test_connection(ctx)
    assert (result.ok, result.message) == (False, 'No Jellyfin user named "Kim"')


async def test_connection_bad_key() -> None:
    sim = JellyfinSim(catalog=CATALOG)
    sim.revoke_key()
    ctx, _ = _ctx(sim.transport, sim.credentials())
    result = await PLUGIN.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Check your Jellyfin API key")


async def test_connection_unreachable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    ctx, _ = _ctx(httpx.MockTransport(refuse), GOOD)
    result = await PLUGIN.test_connection(ctx)
    assert result.ok is False
    assert result.message


async def test_create_resolves_user_lazily() -> None:
    sim = JellyfinSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [CATALOG[0].track_id])
    ctx, store = _ctx(sim.transport, sim.credentials())
    metas = await PLUGIN.create(ctx).list_playlists()
    assert [m.playlist_id for m in metas] == [pid]
    assert store.get(SERVICE)["user_id"] == "u" * 32


async def test_changed_username_resolves_again() -> None:
    sim = JellyfinSim(catalog=CATALOG, users=(("Sam", "u" * 32), ("Kim", "k" * 32)))
    ctx, store = _ctx(sim.transport, sim.credentials())
    assert (await PLUGIN.test_connection(ctx)).account_name == "Sam"
    store.save(SERVICE, {"username": "Kim"})
    assert (await PLUGIN.test_connection(ctx)).account_name == "Kim"
    assert store.get(SERVICE)["user_id"] == "k" * 32


async def test_connection_checks_server_even_with_cached_user() -> None:
    sim = JellyfinSim(catalog=CATALOG)
    ctx, _ = _ctx(sim.transport, sim.credentials())
    assert (await PLUGIN.test_connection(ctx)).ok
    sim.revoke_key()
    result = await PLUGIN.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Check your Jellyfin API key")


async def test_new_server_url_resolves_user_again() -> None:
    old = JellyfinSim(catalog=CATALOG)
    ctx, store = _ctx(old.transport, old.credentials())
    assert (await PLUGIN.test_connection(ctx)).ok
    new = JellyfinSim(catalog=CATALOG, users=(("Sam", "n" * 32),))
    pid = new.add_playlist("Mix", [CATALOG[0].track_id])
    store.save(SERVICE, {"server_url": "http://moved.test/jf"})
    new_ctx = replace(ctx, transport=new.transport)
    metas = await PLUGIN.create(new_ctx).list_playlists()
    assert [m.playlist_id for m in metas] == [pid]
    assert store.get(SERVICE)["user_id"] == "n" * 32
