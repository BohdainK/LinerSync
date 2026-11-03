"""Experimental playlist editing (LINERSYNC_APPLE_MUSIC_EDITING=on): remove,
reorder and delete through the web player's host, with its token only."""

import pytest

from linersync.adapters.connectors.apple_music.connector import (
    CAPABILITIES,
    EDITING_CAPABILITIES,
)
from linersync.adapters.connectors.apple_music.plugin import (
    EDITING_ENV,
    AppleMusicPlugin,
    AuthMode,
    editing_from_env,
)
from linersync.domain.capabilities import Capability
from linersync.domain.operations import Removal, RemoveTracks, Reorder
from linersync.ports.errors import WriteFailed
from tests.adapters.connectors.apple_music.sim import AMP_HOST, AppleMusicSim
from tests.adapters.connectors.apple_music.test_connector import (
    CATALOG,
    LIB,
    X,
    Y,
    Z,
    make,
)

EDITING = AppleMusicPlugin(AuthMode.WEB, editing=True)
AMP = f"https://{AMP_HOST}"


def _writes(sim: AppleMusicSim) -> list[tuple[str, str, str]]:
    return [
        (r.method, r.url.host, r.url.path)
        for r in sim.requests
        if r.method in ("PUT", "DELETE")
    ]


# -- configuration ------------------------------------------------------------


def test_editing_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(EDITING_ENV, raising=False)
    assert editing_from_env(AuthMode.WEB) is False
    assert AppleMusicPlugin(AuthMode.WEB).capabilities == CAPABILITIES


def test_editing_on_with_the_web_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EDITING_ENV, "on")
    assert editing_from_env(AuthMode.WEB) is True
    assert EDITING.capabilities == EDITING_CAPABILITIES
    assert {Capability.REMOVE, Capability.REORDER, Capability.DELETE_PLAYLIST} <= (
        EDITING.capabilities
    )


def test_editing_refused_with_the_official_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EDITING_ENV, "on")
    with pytest.raises(
        ValueError, match="only works with LINERSYNC_APPLE_MUSIC_AUTH=web"
    ):
        editing_from_env(AuthMode.DEVELOPER)
    with pytest.raises(ValueError):
        AppleMusicPlugin(AuthMode.DEVELOPER, editing=True)


def test_bad_editing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EDITING_ENV, "maybe")
    with pytest.raises(ValueError, match="'on' or 'off'"):
        editing_from_env(AuthMode.WEB)


def test_setup_guide_says_it_is_experimental() -> None:
    body = EDITING.setup_guide().steps[0].body
    assert "Experimental" in body and "web player's token" in body
    assert "official API can't" in body
    assert (
        "Experimental" not in AppleMusicPlugin(AuthMode.WEB).setup_guide().steps[0].body
    )


# -- writes ---------------------------------------------------------------------


async def test_remove_sends_the_new_list_to_the_web_player_host() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [X, Y, Z, Y])
    connector, _, _ = make(sim, plugin=EDITING)

    await connector.apply(pid, RemoveTracks("op", (Removal(Y, 3),)))

    assert sim.ids(pid) == [X, Y, Z]  # only the copy at position 3 went
    assert _writes(sim) == [("PUT", AMP_HOST, f"/v1/me/library/playlists/{pid}/tracks")]
    (put,) = [r for r in sim.requests if r.method == "PUT"]
    assert b'"library-songs"' in put.content


async def test_reorder_keeps_repeats_and_library_only_songs() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [X, LIB, Y, X])
    connector, _, _ = make(sim, plugin=EDITING)

    await connector.apply(pid, Reorder("op", (X, X, Y, LIB)))

    assert sim.ids(pid) == [X, X, Y, LIB]


async def test_removing_every_song_removes_each() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [X, Y, X])
    connector, _, _ = make(sim, plugin=EDITING)

    await connector.apply(
        pid, RemoveTracks("op", (Removal(X, 0), Removal(Y, 1), Removal(X, 2)))
    )

    assert sim.ids(pid) == []
    assert [w[0] for w in _writes(sim)] == ["DELETE", "DELETE"]  # X once, Y once


async def test_playlist_changed_since_planned_sends_nothing() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [X, Y])
    connector, _, _ = make(sim, plugin=EDITING)

    with pytest.raises(WriteFailed) as info:
        await connector.apply(pid, RemoveTracks("op", (Removal(Z, 1),)))
    assert info.value.maybe_applied is False
    with pytest.raises(WriteFailed):
        await connector.apply(pid, Reorder("op", (Y, X, Z)))
    assert _writes(sim) == []
    assert sim.ids(pid) == [X, Y]


async def test_waits_for_the_new_order_to_show() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [X, Y, Z])
    sim.lag_reads = 2
    connector, sleeps, _ = make(sim, plugin=EDITING)

    await connector.apply(pid, Reorder("op", (Z, Y, X)))

    assert sleeps  # it polled until the change showed
    snap = await connector.read_playlist(pid)
    assert snap.track_ids == (Z, Y, X)


async def test_delete_playlist() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [X])
    connector, _, _ = make(sim, plugin=EDITING)

    await connector.delete_playlist(pid)
    await connector.delete_playlist(pid)  # already gone: fine

    assert pid not in sim.playlists
    assert _writes(sim)[0] == ("DELETE", AMP_HOST, f"/v1/me/library/playlists/{pid}")


async def test_refused_edit_is_write_failed() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Theirs", [X, Y], can_edit=False)
    connector, _, _ = make(sim, plugin=EDITING)
    with pytest.raises(WriteFailed, match="refused"):
        await connector.apply(pid, Reorder("op", (Y, X)))


async def test_without_editing_nothing_is_sent() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [X, Y])
    connector, _, _ = make(sim, plugin=AppleMusicPlugin(AuthMode.WEB))
    with pytest.raises(WriteFailed, match="can't remove"):
        await connector.apply(pid, RemoveTracks("op", (Removal(X, 0),)))
    with pytest.raises(WriteFailed, match="can't delete"):
        await connector.delete_playlist(pid)
    assert _writes(sim) == []


def test_the_official_host_refuses_these_writes() -> None:
    # As seen live: api.music.apple.com answers 401 to them.
    import httpx

    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mix", [X])
    response = sim.handle(
        httpx.Request(
            "DELETE",
            f"https://api.music.apple.com/v1/me/library/playlists/{pid}",
            headers={
                "Authorization": f"Bearer {sim.web_token}",
                "Origin": "https://music.apple.com",
                "Music-User-Token": sim.user_token,
            },
        )
    )
    assert response.status_code == 401
    assert pid in sim.playlists
    assert AMP.startswith("https://amp-api")
