from datetime import UTC, datetime

import pytest

from linersync.adapters.connectors.kit.plugin import (
    ConnectorContext,
    ConnectorPlugin,
    OAuthSetup,
    is_experimental,
)
from linersync.adapters.connectors.qobuz.plugin import PLUGIN, QOBUZ_PLUGIN
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.capabilities import Capability
from linersync.domain.tracks import ServiceId
from linersync.ports.setup import FieldKind, StepKind, check_guide
from tests.adapters.connectors.qobuz.sim import QobuzSim
from tests.factories import track

QB = ServiceId("qobuz")
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)
CATALOG = (track("qobuz", "64000000", title="Alpha", isrc="GBSIM0000000"),)


def ctx_for(
    sim: QobuzSim, **creds: str
) -> tuple[ConnectorContext, InMemoryCredentials]:
    store = InMemoryCredentials()
    store.save(QB, {**sim.credentials(), **creds})

    async def no_sleep(_: float) -> None:
        return None

    ctx = ConnectorContext(
        store, InMemoryQuota(), FixedClock(NOW), transport=sim.transport, sleep=no_sleep
    )
    return ctx, store


@pytest.fixture
def sim() -> QobuzSim:
    return QobuzSim(catalog=CATALOG)


def test_plugin_shape() -> None:
    assert PLUGIN is QOBUZ_PLUGIN
    p = QOBUZ_PLUGIN
    assert isinstance(p, ConnectorPlugin)
    assert not isinstance(p, OAuthSetup)
    assert (p.service, p.display_name, p.category) == ("qobuz", "Qobuz", "streaming")
    assert p.capabilities == {
        Capability.READ_ORDER,
        Capability.ADD,
        Capability.REMOVE,
        Capability.CREATE_PLAYLIST,
        Capability.EDIT_METADATA,
    }
    assert (p.limits.page_size, p.limits.add_batch_size, p.limits.search_cost) == (
        500,
        50,
        2,
    )
    assert is_experimental(p) is True
    assert p.login_expires_at({"user_auth_token": "t"}) is None


def test_guide_is_valid_and_warns() -> None:
    guide = QOBUZ_PLUGIN.setup_guide()
    assert check_guide(guide) == []
    assert [(s.step_id, s.kind) for s in guide.steps] == [
        ("before", StepKind.INFO),
        ("token", StepKind.FORM),
        ("test", StepKind.TEST),
    ]
    warning = guide.steps[0].body
    for phrase in ("Experimental", "unofficial", "web player", "terms"):
        assert phrase in warning
    kinds = {f.key: f.kind for f in guide.steps[1].fields}
    assert kinds == {"app_id": FieldKind.TEXT, "user_auth_token": FieldKind.SECRET}


def test_validate_token_rejects_a_whole_header_line() -> None:
    ok = {"app_id": "950096963", "user_auth_token": "AbC-123_xyz.456"}
    assert QOBUZ_PLUGIN.validate(ok) == {}
    pasted = {**ok, "user_auth_token": "X-User-Auth-Token: AbC-123_xyz.456"}
    assert "X-User-Auth-Token" in QOBUZ_PLUGIN.validate(pasted)["user_auth_token"]


async def test_new_token_for_another_account_forgets_the_old_user(
    sim: QobuzSim,
) -> None:
    ctx, store = ctx_for(sim)
    assert (await QOBUZ_PLUGIN.test_connection(ctx)).ok
    assert store.get(QB)["user_id"] == "1111111"
    # A token for another account, from a reply that doesn't name the user.
    sim.token = "other-account-token"
    sim.include_user = False
    sim.add_playlist("Theirs", ["64000000"], owner_id=42)
    store.save(QB, {"user_auth_token": sim.token})
    metas = await QOBUZ_PLUGIN.create(ctx).list_playlists()
    assert [(m.name, m.owned) for m in metas] == [("Theirs", True)]
    assert (await QOBUZ_PLUGIN.test_connection(ctx)).ok
    assert store.get(QB).get("user_id", "") == ""


def test_validate_app_id() -> None:
    ok = {"app_id": "950096963", "user_auth_token": "token-value"}
    assert QOBUZ_PLUGIN.validate(ok) == {}
    assert "app_id" in QOBUZ_PLUGIN.validate({**ok, "app_id": "X-App-Id: 9"})


async def test_connection_ok_saves_user(sim: QobuzSim) -> None:
    ctx, store = ctx_for(sim)
    result = await QOBUZ_PLUGIN.test_connection(ctx)
    assert (result.ok, result.message, result.account_name) == (
        True,
        "Connected",
        "sam",
    )
    assert store.get(QB)["user_id"] == "1111111"


async def test_connection_rejected_token(sim: QobuzSim) -> None:
    ctx, _ = ctx_for(sim)
    sim.expire_token()
    result = await QOBUZ_PLUGIN.test_connection(ctx)
    assert (result.ok, result.message) == (False, "Sign in to Qobuz again")


async def test_connection_rejected_app_id(sim: QobuzSim) -> None:
    ctx, _ = ctx_for(sim, app_id="999")
    result = await QOBUZ_PLUGIN.test_connection(ctx)
    assert result.ok is False
    assert "app ID" in result.message


async def test_connection_unreachable_does_not_raise(sim: QobuzSim) -> None:
    ctx, _ = ctx_for(sim)
    sim.fail_next(r"playlist/getUserPlaylists", 503, times=10)
    result = await QOBUZ_PLUGIN.test_connection(ctx)
    assert result.ok is False


async def test_create_uses_stored_user_for_ownership(sim: QobuzSim) -> None:
    sim.include_user = False
    sim.add_playlist("Mine", ["64000000"])
    sim.add_playlist("Theirs", ["64000000"], owner_id=42)
    ctx, _ = ctx_for(sim, user_id="1111111")
    metas = await QOBUZ_PLUGIN.create(ctx).list_playlists()
    assert [(m.name, m.owned) for m in metas] == [("Mine", True), ("Theirs", False)]
