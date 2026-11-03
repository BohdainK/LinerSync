import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from linersync.adapters.connectors.apple_music.connector import AppleMusicConnector
from linersync.adapters.connectors.apple_music.plugin import (
    APPLE_MUSIC_PLUGIN,
    AppleMusicPlugin,
    AuthMode,
)
from linersync.adapters.connectors.kit.http import HttpSettings
from linersync.adapters.connectors.kit.plugin import ConnectorContext, ConnectorSettings
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.capabilities import Capability
from linersync.domain.operations import (
    AddTracks,
    Removal,
    RemoveTracks,
    Reorder,
    UpdateMetadata,
)
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import ServiceId, Track, TrackQuery
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed
from tests.adapters.connectors.apple_music.sim import AppleMusicSim
from tests.adapters.connectors.shapes import key_paths, load_fixture
from tests.factories import track

AM = ServiceId("apple_music")
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
X, Y, Z = "1000000001", "1000000002", "1000000003"
LIB = "i.abc123"  # a library-only song (uploaded, not in the catalog)
CATALOG = (
    track(
        "apple_music", X, title="Xylophone", artists=("Band X",), isrc="USX000000001"
    ),
    track("apple_music", Y, title="Yellow", artists=("Band Y",), isrc="USY000000002"),
    track("apple_music", Z, title="Zebra", artists=("Band Z",), isrc="USZ000000003"),
    track("apple_music", LIB, title="Demo Tape", artists=("Home Band",)),
)
TRACKS = "/v1/me/library/playlists/{}/tracks"


def make(
    sim: AppleMusicSim,
    *,
    hash_artwork: bool = False,
    storefront: str | None = "us",
    plugin: AppleMusicPlugin = APPLE_MUSIC_PLUGIN,
) -> tuple[AppleMusicConnector, list[float], InMemoryCredentials]:
    store = InMemoryCredentials()
    store.save(AM, sim.credentials(storefront=storefront))
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    ctx = ConnectorContext(
        store,
        InMemoryQuota(),
        FixedClock(NOW),
        # A large burst keeps rate-limiter waits out of `sleeps`.
        settings=ConnectorSettings(
            http=HttpSettings(burst=10_000), hash_artwork=hash_artwork
        ),
        transport=sim.transport,
        sleep=sleep,
    )
    connector = plugin.create(ctx)
    assert isinstance(connector, AppleMusicConnector)
    return connector, sleeps, store


def sent(sim: AppleMusicSim, method: str, path_regex: str) -> list[httpx.Request]:
    return [
        r
        for r in sim.requests
        if r.method == method
        and r.url.host == "api.music.apple.com"
        and re.fullmatch(path_regex, r.url.path)
    ]


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


def big_catalog(n: int) -> tuple[Track, ...]:
    return tuple(
        track(
            "apple_music",
            str(2_000_000_000 + i),
            title=f"Song {i}",
            artists=(f"Artist {i}",),
            isrc=f"USBIG{i:07d}",
        )
        for i in range(n)
    )


# -- read ------------------------------------------------------------------


async def test_read_paginates_and_enriches_isrc() -> None:
    catalog = big_catalog(250)
    sim = AppleMusicSim(catalog=catalog)
    ids = [t.track_id for t in catalog]
    pid = sim.add_playlist("Big", ids, description="Lots")
    connector, _, _ = make(sim)
    snap = await connector.read_playlist(pid)
    assert list(snap.track_ids) == ids
    assert snap.is_complete and snap.expected_total == 250
    assert snap.taken_at == NOW
    assert (snap.meta.name, snap.meta.description) == ("Big", "Lots")
    assert [e.track.isrc for e in snap.entries] == [t.isrc for t in catalog]
    assert all(e.track.url for e in snap.entries)
    pages = sent(sim, "GET", TRACKS.format(r"p\.sim0001"))
    assert len(pages) == 3
    assert pages[0].url.params["limit"] == "100"
    songs = sent(sim, "GET", r"/v1/catalog/us/songs")
    assert len(songs) == 3
    assert [len(r.url.params["ids"].split(",")) for r in songs] == [100, 100, 50]


async def test_read_library_only_song_keeps_library_id_and_no_isrc() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mixed", [X, LIB, X])
    connector, _, _ = make(sim)
    snap = await connector.read_playlist(pid)
    assert snap.track_ids == (X, LIB, X)
    assert snap.entries[0].track.isrc == "USX000000001"
    assert snap.entries[1].track.isrc is None
    (songs,) = sent(sim, "GET", r"/v1/catalog/us/songs")
    assert songs.url.params["ids"] == X  # unique catalog ids only


async def test_empty_tracks_404_is_read_failed_not_empty() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Empty", [])
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed) as err:
        await connector.read_playlist(pid)
    assert err.value.message == (
        "Apple Music returned no song list for this playlist. An empty Apple "
        "Music playlist can't yet be told apart from an error, so it was not "
        "treated as empty. Add one song to it."
    )
    assert err.value.auth_required is False


async def test_read_403_is_auth_required() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    sim.revoke_user_token()
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed) as err:
        await connector.read_playlist(pid)
    assert err.value.auth_required is True


async def test_read_403_on_tracks_is_auth_required() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    sim.fail_next("GET", TRACKS.format(r"p\.sim0001"), 403)
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed) as err:
        await connector.read_playlist(pid)
    assert err.value.auth_required is True


async def test_wrong_developer_token_is_auth_required_after_one_renewal() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    connector, _, _ = make(sim, plugin=AppleMusicPlugin(AuthMode.DEVELOPER))
    sim.key_id = "KEYOTHER01"
    with pytest.raises(ReadFailed) as err:
        await connector.read_playlist(pid)
    assert err.value.auth_required is True
    assert len(sent(sim, "GET", r"/v1/me/library/playlists/p\.sim0001")) == 2


async def test_web_mode_sends_web_player_token_and_origin() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    connector, _, _ = make(sim, plugin=AppleMusicPlugin(AuthMode.WEB))
    await connector.read_playlist(pid)
    api = [r for r in sim.requests if r.url.host == "api.music.apple.com"]
    assert api
    for request in api:
        assert request.headers["authorization"] == f"Bearer {sim.web_token}"
        assert request.headers["origin"] == "https://music.apple.com"
        assert request.headers["music-user-token"] == sim.user_token
    assert [r for r in sim.requests if r.url.host == "music.apple.com"] == []


async def test_web_mode_fetches_rotated_web_token_once_and_saves_it() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    connector, _, store = make(sim, plugin=AppleMusicPlugin(AuthMode.WEB))
    sim.rotate_web_token()
    snap = await connector.read_playlist(pid)
    assert list(snap.track_ids) == [X]
    web = [r.url.path for r in sim.requests if r.url.host == "music.apple.com"]
    assert web == ["/us/new", "/assets/index~sim0001.js"]
    assert store.get(AM)["web_token"] == sim.web_token
    # The read checks the playlist twice; the first try used the old token.
    assert len(sent(sim, "GET", r"/v1/me/library/playlists/p\.sim0001")) == 3


async def test_web_mode_without_a_token_in_the_web_player_is_auth_required() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    connector, _, _ = make(sim, plugin=AppleMusicPlugin(AuthMode.WEB))
    sim.rotate_web_token()
    sim.web_page_path = "/elsewhere"  # the web player page answers 404
    with pytest.raises(ReadFailed) as err:
        await connector.read_playlist(pid)
    assert err.value.auth_required is True
    assert "web player token" in err.value.message


async def test_read_raises_when_playlist_changed_during_read() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Busy", [X, Y, Z])
    sim.change_during_read(pid)
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed) as err:
        await connector.read_playlist(pid)
    assert err.value.message == (
        "Apple Music playlist changed while it was being read; "
        "it will be read again next run"
    )
    assert len(sim.ids(pid)) == 3  # same length: only the date reveals it
    assert sent(sim, "GET", r"/v1/catalog/us/songs") == []  # before enrichment


async def test_read_without_last_modified_skips_check() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Plain", [X, Y, Z], last_modified=False)
    connector, _, _ = make(sim)
    snap = await connector.read_playlist(pid)
    assert snap.track_ids == (X, Y, Z) and snap.is_complete
    assert len(sent(sim, "GET", r"/v1/me/library/playlists/p\.sim0001")) == 2


async def test_read_recheck_failure_is_read_failed() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        if (
            request.method == "GET"
            and request.url.path == f"/v1/me/library/playlists/{pid}"
        ):
            gets += 1
            if gets >= 2:
                return httpx.Response(500, json={"errors": []})
        return sim.handle(request)

    sim.transport = httpx.MockTransport(handler)
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed, match="500"):
        await connector.read_playlist(pid)


async def test_read_recheck_404_is_not_found() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Doomed", [X])
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        if (
            request.method == "GET"
            and request.url.path == f"/v1/me/library/playlists/{pid}"
        ):
            gets += 1
            if gets == 2:
                return httpx.Response(404, json={"errors": []})
        return sim.handle(request)

    sim.transport = httpx.MockTransport(handler)
    connector, _, _ = make(sim)
    with pytest.raises(PlaylistNotFound):
        await connector.read_playlist(pid)


async def test_read_playlist_404_is_not_found() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    connector, _, _ = make(sim)
    with pytest.raises(PlaylistNotFound):
        await connector.read_playlist("p.nosuchplaylist")


async def test_read_truncated_is_read_failed() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Short", [X, Y])
    sim.truncate(pid)
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed, match="2 of 3"):
        await connector.read_playlist(pid)


async def test_read_catalog_failure_is_read_failed() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X, Y])
    sim.fail_next("GET", r"/v1/catalog/us/songs", 500, times=4)
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed, match="500"):
        await connector.read_playlist(pid)


async def test_read_hashes_artwork_without_credentials() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Art", [X, Y, X, LIB])
    connector, _, _ = make(sim, hash_artwork=True)
    snap = await connector.read_playlist(pid)
    hashes = [e.track.artwork_hash for e in snap.entries]
    assert hashes[0] is not None and len(hashes[0]) == 16
    assert hashes[0] == hashes[2] and hashes[1] is not None
    assert hashes[3] is None
    art = [r for r in sim.requests if r.url.host == "is1-ssl.mzstatic.com"]
    assert len(art) == 2
    assert all("/64x64bb.jpg" in r.url.path for r in art)
    for r in art:
        assert "authorization" not in r.headers
        assert "music-user-token" not in r.headers


async def test_storefront_fetched_once_and_saved() -> None:
    sim = AppleMusicSim(catalog=CATALOG, storefront="nl")
    pid = sim.add_playlist("Mine", [X])
    connector, _, store = make(sim, storefront=None)
    await connector.read_playlist(pid)
    await connector.search(TrackQuery("Yellow", ("Band Y",), isrc="USY000000002"))
    assert len(sent(sim, "GET", r"/v1/me/storefront")) == 1
    assert store.get(AM)["storefront"] == "nl"
    assert len(sent(sim, "GET", r"/v1/catalog/nl/songs")) == 2


async def test_list_playlists_follows_next() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pids = [sim.add_playlist(f"P{i}", [X]) for i in range(150)]
    ro = sim.add_playlist("Followed", [X], can_edit=False)
    connector, _, _ = make(sim)
    metas = {m.playlist_id: m for m in await connector.list_playlists()}
    assert set(metas) == {*pids, ro}
    assert metas[pids[0]].owned and not metas[ro].owned
    assert all(m.service == AM for m in metas.values())
    assert len(sent(sim, "GET", r"/v1/me/library/playlists")) == 2


async def test_list_playlists_repeated_next_is_read_failed() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    sim.add_playlist("Only", [X])

    def handler(request: httpx.Request) -> httpx.Response:
        response = sim.handle(request)
        if request.url.path == "/v1/me/library/playlists":
            page = response.json()
            page["next"] = "/v1/me/library/playlists?offset=100"
            return httpx.Response(200, json=page)
        return response

    sim.transport = httpx.MockTransport(handler)
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed, match="repeated a page"):
        await connector.list_playlists()


# -- add -------------------------------------------------------------------


async def test_add_settles_before_returning() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Lag", [X])
    sim.lag_reads = 2
    connector, sleeps, _ = make(sim)
    await connector.apply(pid, AddTracks("op", (Y, Z)))
    assert sleeps == [1.0, 1.0]
    assert sim.ids(pid) == [X, Y, Z]
    snap = await connector.read_playlist(pid)
    assert snap.track_ids == (X, Y, Z)
    polls = sent(sim, "GET", TRACKS.format(r"p\.sim0001"))
    assert [r.url.params["limit"] for r in polls[:4]] == ["1"] * 4


async def test_add_settle_timeout_returns_without_error() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Slow", [X])
    sim.lag_reads = 100
    connector, sleeps, _ = make(sim)
    await connector.apply(pid, AddTracks("op", (Y,)))
    assert sleeps == [1.0] * 10
    assert len(sent(sim, "GET", TRACKS.format(r"p\.sim0001"))) == 1 + 11
    assert sim.ids(pid) == [X, Y]


async def test_add_settle_keeps_polling_after_failed_poll() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Flaky poll", [X])
    sim.lag_reads = 1
    path = f"/v1/me/library/playlists/{pid}/tracks"
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        if request.method == "GET" and request.url.path == path:
            gets += 1
            if 2 <= gets <= 5:  # every attempt of the first poll fails
                return httpx.Response(
                    503, json={"errors": []}, headers={"Retry-After": "0"}
                )
        return sim.handle(request)

    sim.transport = httpx.MockTransport(handler)
    connector, sleeps, _ = make(sim)
    await connector.apply(pid, AddTracks("op", (Y,)))
    # 3 HTTP retry waits inside the failed poll, then the settle interval, a
    # lagging poll, the settle interval again, and the poll that sees the song.
    assert sleeps == [0.0, 0.0, 0.0, 1.0, 1.0]
    assert gets == 1 + 4 + 2
    assert sim.ids(pid) == [X, Y]


async def test_add_to_empty_playlist_settles_from_404_baseline() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Empty", [])
    sim.lag_reads = 1
    connector, sleeps, _ = make(sim)
    await connector.apply(pid, AddTracks("op", (X, Y)))
    assert sleeps == [1.0]
    assert sim.ids(pid) == [X, Y]


async def test_add_batches_of_100() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Batch", [X])
    ids = tuple((X, Y, Z)[i % 3] for i in range(250))
    connector, _, _ = make(sim)
    await connector.apply(pid, AddTracks("op", ids))
    posts = sent(sim, "POST", TRACKS.format(r"p\.sim0001"))
    assert [len(body(r)["data"]) for r in posts] == [100, 100, 50]
    assert sim.ids(pid) == [X, *ids]


async def test_add_body_types() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Types", [X])
    connector, _, _ = make(sim)
    await connector.apply(pid, AddTracks("op", (Y, LIB)))
    (post,) = sent(sim, "POST", TRACKS.format(r"p\.sim0001"))
    assert body(post) == {
        "data": [{"id": Y, "type": "songs"}, {"id": LIB, "type": "library-songs"}]
    }
    assert sim.ids(pid) == [X, Y, LIB]


async def test_add_timeout_is_maybe_applied() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Slow", [X])
    sim.timeout_next("POST", TRACKS.format(r"p\.sim0001"), apply_first=True)
    connector, _, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert err.value.maybe_applied is True
    assert len(sent(sim, "POST", TRACKS.format(r"p\.sim0001"))) == 1
    assert sim.ids(pid) == [X, Y]  # it landed once, and only once


async def test_add_timeout_with_lag_settles_before_raising() -> None:
    # The POST lands but times out; Apple's library still shows the old songs.
    # Raising at once would let the engine's re-read miss Y and add it again.
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Lag", [X, Z])
    sim.lag_reads = 3
    sim.timeout_next("POST", TRACKS.format(r"p\.sim0001"), apply_first=True)
    connector, sleeps, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert err.value.maybe_applied is True
    assert sleeps == [1.0, 1.0, 1.0]
    assert len(sent(sim, "POST", TRACKS.format(r"p\.sim0001"))) == 1
    snap = await connector.read_playlist(pid)
    assert snap.track_ids == (X, Z, Y)


async def test_add_500_on_second_batch_with_lag_settles_before_raising() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Lag", [X, Z])
    sim.lag_reads = 3
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "POST" and request.url.path.endswith("/tracks"):
            calls += 1
            if calls == 2:  # the second batch lands, then answers 500
                sim.handle(request)
                return httpx.Response(500, json={"errors": []})
        return sim.handle(request)

    sim.transport = httpx.MockTransport(handler)
    connector, sleeps, _ = make(sim)
    ids = tuple([Y] * 150)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", ids))
    assert err.value.maybe_applied is True
    assert calls == 2  # not retried
    assert sleeps == [1.0, 1.0, 1.0]
    snap = await connector.read_playlist(pid)
    assert snap.track_ids == (X, Z, *ids)


async def test_add_settle_timeout_after_landed_failure_still_raises() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Slow", [X])
    sim.lag_reads = 100
    sim.timeout_next("POST", TRACKS.format(r"p\.sim0001"), apply_first=True)
    connector, sleeps, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert err.value.maybe_applied is True
    assert sleeps == [1.0] * 10


async def test_add_rejected_first_batch_raises_without_settling() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    sim.lag_reads = 3
    sim.fail_next("POST", TRACKS.format(r"p\.sim0001"), 400)
    connector, sleeps, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert err.value.maybe_applied is False
    assert sleeps == []
    assert len(sent(sim, "GET", TRACKS.format(r"p\.sim0001"))) == 1  # baseline


async def test_add_403_is_not_applied_with_reason() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Followed", [X], can_edit=False)
    connector, _, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert err.value.maybe_applied is False
    assert err.value.auth_required is False
    assert err.value.message == (
        "Apple Music refused the change (the playlist may not be editable, "
        "or the sign-in expired)"
    )
    assert sim.ids(pid) == [X]


async def test_add_403_on_second_batch_is_maybe_applied() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Batch", [X])
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "POST" and request.url.path.endswith("/tracks"):
            calls += 1
            if calls == 2:
                return httpx.Response(403, json={"errors": []})
        return sim.handle(request)

    sim.transport = httpx.MockTransport(handler)
    connector, sleeps, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", tuple([Y] * 150)))
    assert err.value.maybe_applied is True
    # The refused batch did not land, so the wait targets the first batch only
    # and ends at once.
    assert sleeps == []


async def test_add_baseline_failure_sends_nothing() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Flaky", [X])
    sim.fail_next("GET", TRACKS.format(r"p\.sim0001"), 500, times=4)
    connector, _, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert err.value.maybe_applied is False
    assert sent(sim, "POST", TRACKS.format(r"p\.sim0001")) == []


async def test_uncapable_ops_raise_write_failed_not_applied() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X, Y])
    connector, _, _ = make(sim)
    assert connector.capabilities == frozenset(
        {Capability.READ_ORDER, Capability.ADD, Capability.CREATE_PLAYLIST}
    )
    ops: Sequence[RemoveTracks | Reorder | UpdateMetadata] = (
        RemoveTracks("op", (Removal(X, 0),)),
        Reorder("op", (Y, X)),
        UpdateMetadata("op", PlaylistDraft("New")),
    )
    for op in ops:
        with pytest.raises(WriteFailed, match="Apple Music can't") as err:
            await connector.apply(pid, op)
        assert err.value.maybe_applied is False
    assert sim.requests == []
    assert sim.ids(pid) == [X, Y]


# -- create ----------------------------------------------------------------


async def test_create_waits_for_playlist_to_appear() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    sim.lag_reads = 2
    connector, sleeps, _ = make(sim)
    draft = PlaylistDraft("Fresh & New", "desc", "https://covers.example/c.png")
    meta = await connector.create_playlist(draft)
    (post,) = sent(sim, "POST", r"/v1/me/library/playlists")
    assert body(post) == {"attributes": {"name": "Fresh & New", "description": "desc"}}
    assert sleeps == [1.0, 1.0]
    assert meta.service == AM
    assert (meta.name, meta.description, meta.owned) == ("Fresh & New", "desc", True)
    gets = sent(sim, "GET", rf"/v1/me/library/playlists/{meta.playlist_id}")
    assert len(gets) == 3
    assert not [r for r in sim.requests if r.url.host == "covers.example"]


async def test_create_rejected_is_not_applied() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    sim.fail_next("POST", r"/v1/me/library/playlists", 403)
    connector, _, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.create_playlist(PlaylistDraft("Nope"))
    assert err.value.maybe_applied is False
    assert sim.playlists == {}


async def test_create_timeout_is_maybe_applied() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    sim.timeout_next("POST", r"/v1/me/library/playlists", apply_first=True)
    connector, _, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.create_playlist(PlaylistDraft("Maybe"))
    assert err.value.maybe_applied is True
    assert len(sim.playlists) == 1


# -- search ----------------------------------------------------------------


async def test_search_isrc_then_term() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    connector, _, _ = make(sim)

    found = await connector.search(TrackQuery.from_track(CATALOG[1]))
    assert [t.track_id for t in found] == [Y]  # the term hit is the same song
    assert found[0].isrc == "USY000000002"
    (isrc,) = sent(sim, "GET", r"/v1/catalog/us/songs")
    assert isrc.url.params["filter[isrc]"] == "USY000000002"
    assert len(sent(sim, "GET", r"/v1/catalog/us/search")) == 1

    sim.requests.clear()
    miss = TrackQuery("Yellow", ("Band Y", "Other"), isrc="ZZZ000000000")
    found = await connector.search(miss)
    assert [t.track_id for t in found] == [Y]
    assert len(sent(sim, "GET", r"/v1/catalog/us/songs")) == 1
    (term,) = sent(sim, "GET", r"/v1/catalog/us/search")
    params = term.url.params
    assert (params["types"], params["term"], params["limit"]) == (
        "songs",
        "Yellow Band Y",
        "10",
    )

    sim.requests.clear()
    assert await connector.search(TrackQuery("Nothing Like It", ())) == []
    assert sent(sim, "GET", r"/v1/catalog/us/songs") == []
    (term,) = sent(sim, "GET", r"/v1/catalog/us/search")
    assert term.url.params["term"] == "Nothing Like It"


async def test_search_keeps_term_hits_beside_isrc_hits() -> None:
    # Seen live: the source's ISRC is only on compilations; the album copy has
    # another ISRC and only the term search finds it.
    comp = track(
        "apple_music", "101", title="Routines", artists=("Band",), isrc="ISRC1"
    )
    album = track(
        "apple_music", "102", title="Routines", artists=("Band",), isrc="ISRC2"
    )
    sim = AppleMusicSim(catalog=[comp, album])
    connector, _, _ = make(sim)
    found = await connector.search(TrackQuery("Routines", ("Band",), isrc="ISRC1"))
    assert [t.track_id for t in found] == ["101", "102"]


async def test_search_failure_is_read_failed() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    sim.fail_next("GET", r"/v1/catalog/us/search", 403)
    connector, _, _ = make(sim)
    with pytest.raises(ReadFailed) as err:
        await connector.search(TrackQuery("Yellow", ("Band Y",)))
    assert err.value.auth_required is True


# -- simulator fidelity ------------------------------------------------------


async def _raw(sim: AppleMusicSim, method: str, url: str, **kw: Any) -> Any:
    from linersync.adapters.connectors.apple_music.token import make_developer_token

    token = make_developer_token(sim.team_id, sim.key_id, sim.private_key_pem, NOW)
    async with httpx.AsyncClient(transport=sim.transport) as client:
        response = await client.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Music-User-Token": sim.user_token,
            },
            **kw,
        )
    assert response.status_code in (200, 201, 204), response.text
    return response.json() if response.content else None


async def test_sim_responses_match_fixture_shapes() -> None:
    sim = AppleMusicSim(catalog=CATALOG)
    # Apple omits lastModifiedDate on a playlist that was never changed.
    fresh = sim.add_playlist("Fresh", [X, LIB], description="d", last_modified=False)
    changed = sim.add_playlist("Changed", [X])
    sim.add_playlist("Followed", [Y], can_edit=False)
    api = "https://api.music.apple.com/v1"
    add = {"data": [{"id": Y, "type": "songs"}]}
    await _raw(sim, "POST", f"{api}/me/library/playlists/{changed}/tracks", json=add)
    cases: Sequence[tuple[str, str, str, dict[str, Any]]] = (
        ("storefront", "GET", f"{api}/me/storefront", {}),
        ("storefront", "GET", f"{api}/storefronts/us", {}),
        ("library_playlists", "GET", f"{api}/me/library/playlists", {}),
        ("library_playlist", "GET", f"{api}/me/library/playlists/{fresh}", {}),
        (
            "library_playlist_tracks",
            "GET",
            f"{api}/me/library/playlists/{fresh}/tracks",
            {"params": {"limit": "1"}},
        ),
        ("catalog_songs", "GET", f"{api}/catalog/us/songs", {"params": {"ids": X}}),
        (
            "catalog_songs",
            "GET",
            f"{api}/catalog/us/songs",
            {"params": {"filter[isrc]": "USX000000001"}},
        ),
        (
            "catalog_search",
            "GET",
            f"{api}/catalog/us/search",
            {"params": {"types": "songs", "term": "yellow band", "limit": "10"}},
        ),
        (
            "create_playlist",
            "POST",
            f"{api}/me/library/playlists",
            {"json": {"attributes": {"name": "N", "description": "D"}}},
        ),
    )
    for fixture, method, url, kw in cases:
        got = key_paths(await _raw(sim, method, url, **kw))
        extra = got - key_paths(load_fixture("apple_music", fixture))
        assert not extra, f"{fixture}: {sorted(extra)}"


def test_sim_songs_parse() -> None:
    from linersync.adapters.connectors.apple_music.parse import (
        parse_catalog_song,
        parse_library_song,
    )

    sim = AppleMusicSim(catalog=CATALOG)
    for t in CATALOG:
        lib = parse_library_song(sim.library_song_obj(t.track_id))
        assert (lib.track_id, lib.title, lib.artists) == (
            t.track_id,
            t.title,
            t.artists,
        )
    cat = parse_catalog_song(sim.catalog_song_obj(X))
    assert (cat.track_id, cat.isrc) == (X, "USX000000001")
