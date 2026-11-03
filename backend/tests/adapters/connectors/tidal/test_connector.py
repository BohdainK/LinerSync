import json
from datetime import UTC, datetime

import httpx
import pytest

from linersync.adapters.connectors.kit.http import HttpClient, HttpSettings, RetryPolicy
from linersync.adapters.connectors.kit.images import ArtworkHasher, http_fetcher
from linersync.adapters.connectors.kit.oauth import PkceAuth
from linersync.adapters.connectors.tidal.connector import TidalConnector
from linersync.adapters.connectors.tidal.parse import API_BASE, SERVICE
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.operations import (
    AddTracks,
    Removal,
    RemoveTracks,
    Reorder,
    UpdateMetadata,
)
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import TrackQuery
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed
from linersync.ports.quota import QuotaPolicy
from tests.adapters.connectors.shapes import key_paths, load_fixture
from tests.adapters.connectors.tidal.sim import TidalSim
from tests.factories import track

TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
CATALOG = tuple(
    track(
        "tidal",
        str(1000 + i),
        title=name.title(),
        artists=(f"{name.title()} Band",),
        album="First",
        isrc=f"NLSIM{i:07d}",
    )
    for i, name in enumerate(("alpha", "bravo", "charlie"))
)
X, Y, Z = (t.track_id for t in CATALOG)
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def _client(sim: TidalSim, base: str, auth: PkceAuth | None = None) -> HttpClient:
    async def no_sleep(_: float) -> None:
        return None

    return HttpClient(
        SERVICE,
        base,
        settings=HttpSettings(),
        quota=InMemoryQuota(),
        quota_policy=QuotaPolicy(),
        clock=FixedClock(NOW),
        auth=auth,
        transport=sim.transport,
        sleep=no_sleep,
    )


def make_connector(sim: TidalSim, *, artwork: bool = False) -> TidalConnector:
    store = InMemoryCredentials()
    store.save(
        SERVICE,
        {
            "client_id": "client",
            "access_token": sim.access_token,
            "refresh_token": "tidal-refresh",
            "expires_at": "2026-10-31T12:00:00+00:00",
        },
    )
    clock = FixedClock(NOW)
    auth = PkceAuth(SERVICE, "Tidal", TOKEN_URL, store, _client(sim, ""), clock)
    hasher = ArtworkHasher(http_fetcher(_client(sim, ""))) if artwork else None

    async def country() -> str:
        return "NL"

    return TidalConnector(_client(sim, API_BASE, auth), hasher, country, clock=clock)


@pytest.fixture
def sim() -> TidalSim:
    return TidalSim(catalog=CATALOG)


def _api_gets(sim: TidalSim) -> list[httpx.Request]:
    return [
        r
        for r in sim.requests
        if r.method == "GET" and r.url.host == "openapi.tidal.com"
    ]


# -- reads -----------------------------------------------------------------


async def test_read_follows_pages_in_order_with_repeats(sim: TidalSim) -> None:
    wanted = [(X, Y, Z, X)[i % 4] for i in range(75)]
    pid = sim.add_playlist("Long", wanted)
    snap = await make_connector(sim, artwork=True).read_playlist(pid)
    assert snap.is_complete
    assert list(snap.track_ids) == wanted
    assert snap.meta.name == "Long"
    assert snap.meta.version_token
    first = snap.entries[0].track
    assert first.title == "Alpha"
    assert first.artists == ("Alpha Band",)
    assert first.album == "First"
    assert first.isrc == "NLSIM0000000"
    assert first.duration_ms == CATALOG[0].duration_ms
    assert first.artwork_hash is not None


async def test_pages_keep_query_when_next_link_is_bare(sim: TidalSim) -> None:
    sim.bare_next_links = True
    wanted = [(X, Y, Z)[i % 3] for i in range(45)]
    pid = sim.add_playlist("Long", wanted)
    for i in range(44):
        sim.add_playlist(f"P{i}", [X])
    connector = make_connector(sim)
    snap = await connector.read_playlist(pid)
    assert snap.is_complete
    assert list(snap.track_ids) == wanted
    assert len(await connector.list_playlists()) == 45


async def test_list_playlists_follows_pages(sim: TidalSim) -> None:
    pids = [sim.add_playlist(f"P{i}", [X]) for i in range(45)]
    metas = await make_connector(sim).list_playlists()
    assert [m.playlist_id for m in metas] == pids
    assert metas[0].track_count == 1


async def test_unknown_playlist_is_not_found(sim: TidalSim) -> None:
    with pytest.raises(PlaylistNotFound):
        await make_connector(sim).read_playlist("00000000-0000-0000-0000-000000000000")


async def test_read_error_raises_read_failed(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.fail_next(
        "GET",
        r"/playlists/.*/relationships/items",
        500,
        times=RetryPolicy().max_attempts,
    )
    with pytest.raises(ReadFailed):
        await make_connector(sim).read_playlist(pid)


async def test_change_during_read_fails(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.change_during_read(pid)
    with pytest.raises(ReadFailed, match="changed while it was being read"):
        await make_connector(sim).read_playlist(pid)


async def test_truncated_read_is_incomplete(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.truncate(pid)
    snap = await make_connector(sim).read_playlist(pid)
    assert snap.is_complete is False
    assert snap.track_ids == (X, Y)


async def test_unavailable_track_makes_snapshot_incomplete(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, Z])
    sim.make_unavailable(pid, 1)
    snap = await make_connector(sim).read_playlist(pid)
    assert snap.expected_total == 3
    assert snap.track_ids == (X, Z)
    assert snap.is_complete is False


async def test_video_entry_reads_with_prefixed_id(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    vid = sim.add_video(pid)
    snap = await make_connector(sim).read_playlist(pid)
    assert snap.is_complete
    assert snap.track_ids == (X, vid)
    assert vid.startswith("video:")


async def test_read_sends_country_code(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    connector = make_connector(sim)
    await connector.read_playlist(pid)
    await connector.list_playlists()
    gets = _api_gets(sim)
    assert gets
    assert all(r.url.params.get("countryCode") == "NL" for r in gets)


async def test_expired_token_is_auth_required(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.expire_login()
    with pytest.raises(ReadFailed) as info:
        await make_connector(sim).read_playlist(pid)
    assert info.value.auth_required is True


# -- search ----------------------------------------------------------------


async def test_search_by_isrc_first(sim: TidalSim) -> None:
    hits = await make_connector(sim).search(TrackQuery.from_track(CATALOG[1]))
    assert [t.track_id for t in hits] == [Y]  # the text hit is the same song
    gets = _api_gets(sim)
    assert gets[0].url.path == "/v2/tracks"
    assert gets[0].url.params.get("filter[isrc]") == "NLSIM0000001"
    assert gets[1].url.path.startswith("/v2/searchResults/")


async def test_search_keeps_text_hits_beside_isrc_hits() -> None:
    # The source's ISRC may be only on compilations; the album copy has
    # another ISRC and only the text search finds it.
    comp = track("tidal", "2001", title="Routines", artists=("Band",), isrc="I1")
    album = track("tidal", "2002", title="Routines", artists=("Band",), isrc="I2")
    sim = TidalSim(catalog=[comp, album])
    query = TrackQuery("Routines", ("Band",), isrc="I1")
    hits = await make_connector(sim).search(query)
    assert [t.track_id for t in hits] == ["2001", "2002"]


async def test_search_falls_back_to_metadata(sim: TidalSim) -> None:
    query = TrackQuery("Charlie", ("Charlie Band",), isrc="NOSUCH0000000")
    hits = await make_connector(sim).search(query)
    assert [t.track_id for t in hits] == [Z]
    assert hits[0].artists == ("Charlie Band",)
    last = _api_gets(sim)[-1]
    assert last.url.raw_path.startswith(b"/v2/searchResults/Charlie%20Charlie%20Band/")


async def test_search_encodes_slashes_in_query(sim: TidalSim) -> None:
    await make_connector(sim).search(TrackQuery("AC/DC song", ("AC/DC",)))
    last = _api_gets(sim)[-1]
    assert b"AC%2FDC" in last.url.raw_path


# -- writes ----------------------------------------------------------------


def _mutations(sim: TidalSim, method: str) -> list[httpx.Request]:
    return [
        r
        for r in sim.requests
        if r.method == method and r.url.host == "openapi.tidal.com"
    ]


async def test_create_playlist_is_unlisted(sim: TidalSim) -> None:
    meta = await make_connector(sim).create_playlist(PlaylistDraft("New", "About"))
    p = sim.playlists[meta.playlist_id]
    assert (p.name, p.description, p.access) == ("New", "About", "UNLISTED")
    assert meta.name == "New"
    post = _mutations(sim, "POST")[0]
    assert post.headers["content-type"] == "application/vnd.api+json"
    assert post.headers["idempotency-key"]


async def test_create_truncates_long_name_and_description(sim: TidalSim) -> None:
    draft = PlaylistDraft("n" * 300, "d" * 600)
    meta = await make_connector(sim).create_playlist(draft)
    p = sim.playlists[meta.playlist_id]
    assert len(p.name) == 250
    assert len(p.description) == 500


async def test_add_batches_of_fifty(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [])
    ids = tuple((X, Y, Z)[i % 3] for i in range(120))
    await make_connector(sim).apply(pid, AddTracks("op", ids))
    assert sim.track_ids(pid) == list(ids)
    assert len(_mutations(sim, "POST")) == 3


async def test_add_video_sends_videos_type(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [])
    vid = sim.add_video(pid)
    await make_connector(sim).apply(pid, AddTracks("op", (vid, X)))
    assert sim.track_ids(pid) == [vid, vid, X]


async def test_add_retry_reuses_idempotency_key(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.timeout_next("POST", r"/playlists/.*/relationships/items", apply_first=True)
    await make_connector(sim).apply(pid, AddTracks("op", (Y, Z)))
    assert sim.track_ids(pid) == [X, Y, Z]
    keys = [r.headers["idempotency-key"] for r in _mutations(sim, "POST")]
    assert len(keys) == 2 and keys[0] == keys[1]


async def test_remove_one_of_duplicates_by_position(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, X, Z, X])
    items = sim.item_ids(pid)
    await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 2),)))
    assert sim.track_ids(pid) == [X, Y, Z, X]
    assert sim.item_ids(pid) == [items[0], items[1], items[3], items[4]]


async def test_remove_in_batches_of_fifty(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X] * 120)
    removals = tuple(Removal(X, i) for i in range(0, 120, 2))
    await make_connector(sim).apply(pid, RemoveTracks("op", removals))
    assert sim.track_ids(pid) == [X] * 60
    assert len(_mutations(sim, "DELETE")) == 2


async def test_remove_refuses_incomplete_playlist(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, Z])
    sim.make_unavailable(pid, 1)
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 0),)))
    assert info.value.maybe_applied is False
    assert sim.track_ids(pid) == [X, Y, Z]


async def test_remove_changed_playlist_is_replanned(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    with pytest.raises(WriteFailed, match="replanned") as info:
        await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(Z, 1),)))
    assert info.value.maybe_applied is False


async def test_reorder_reaches_target(sim: TidalSim) -> None:
    order = [(X, Y, Z)[i % 3] for i in range(30)]
    pid = sim.add_playlist("Mix", order)
    target = tuple(reversed(order))
    await make_connector(sim).apply(pid, Reorder("op", target))
    assert sim.track_ids(pid) == list(target)
    assert all(
        len(json.loads(r.content)["data"]) <= 20 for r in _mutations(sim, "PATCH")
    )


async def test_reorder_refuses_incomplete_playlist(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, Z])
    sim.make_unavailable(pid, 0)
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, Reorder("op", (Z, Y, X)))
    assert info.value.maybe_applied is False


async def test_update_metadata_patches_name_and_description(sim: TidalSim) -> None:
    pid = sim.add_playlist("Old", [X])
    op = UpdateMetadata("op", PlaylistDraft("New", "d" * 600))
    await make_connector(sim).apply(pid, op)
    p = sim.playlists[pid]
    assert p.name == "New"
    assert p.description == "d" * 500


async def test_add_skipped_song_is_reported(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    with pytest.raises(WriteFailed, match="77777") as info:
        await make_connector(sim).apply(pid, AddTracks("op", (Y, "77777", Z)))
    assert info.value.maybe_applied is True
    assert "isn't available" in info.value.message
    assert sim.track_ids(pid) == [X, Y, Z]


async def test_remove_refuses_short_read(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, X])
    sim.truncate(pid)
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 2),)))
    assert info.value.maybe_applied is False
    assert sim.track_ids(pid) == [X, Y, X]


async def test_write_rejected_is_not_applied(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.fail_next("POST", r"/playlists/.*/relationships/items", 403)
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, AddTracks("op", (Y,)))
    assert info.value.maybe_applied is False
    assert sim.track_ids(pid) == [X]


async def test_failure_after_first_batch_is_maybe_applied(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X] * 120)
    sim.fail_next("DELETE", r"/playlists/.*/relationships/items", 403, skip=1)
    removals = tuple(Removal(X, i) for i in range(120))
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, RemoveTracks("op", removals))
    assert info.value.maybe_applied is True


# -- simulator shapes ------------------------------------------------------


async def _raw(sim: TidalSim, path: str, params: dict[str, str]) -> object:
    async with httpx.AsyncClient(transport=sim.transport) as client:
        response = await client.get(
            API_BASE + path,
            params=params,
            headers={
                "Authorization": f"Bearer {sim.access_token}",
                "Accept": "application/vnd.api+json",
            },
        )
    assert response.status_code == 200, response.text
    return response.json()


async def test_sim_shapes_match_fixtures(sim: TidalSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.add_video(pid)
    cases = [
        ("users_me", "/users/me", {}),
        ("playlists", "/playlists", {"filter[owners.id]": "me", "countryCode": "NL"}),
        ("playlist", f"/playlists/{pid}", {"countryCode": "NL"}),
        (
            "playlist_items",
            f"/playlists/{pid}/relationships/items",
            {"countryCode": "NL", "include": "items.artists,items.albums.coverArt"},
        ),
        (
            "tracks_isrc",
            "/tracks",
            {
                "filter[isrc]": CATALOG[0].isrc or "",
                "countryCode": "NL",
                "include": "artists,albums",
            },
        ),
        (
            "search_tracks",
            "/searchResults/alpha/relationships/tracks",
            {"countryCode": "NL", "include": "tracks.artists,tracks.albums"},
        ),
    ]
    for fixture, path, params in cases:
        got = await _raw(sim, path, params)
        extra = key_paths(got) - key_paths(load_fixture("tidal", fixture))
        assert not extra, f"{fixture}: {sorted(extra)}"
