import base64
import io
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from PIL import Image

from linersync.adapters.connectors.kit.plugin import ConnectorContext, ConnectorSettings
from linersync.adapters.connectors.spotify.connector import SpotifyConnector
from linersync.adapters.connectors.spotify.plugin import SPOTIFY_PLUGIN
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
from linersync.domain.tracks import ServiceId, Track, TrackQuery
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed
from tests.adapters.connectors.shapes import key_paths, load_fixture
from tests.adapters.connectors.spotify.sim import SpotifySim
from tests.factories import track

SP = ServiceId("spotify")
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
X, Y, Z, W = (f"spotify:track:{c * 22}" for c in "xyzw")
LOCAL = "spotify:local:Garage+Band:Demos:Rough+Mix:215"
EPISODE = "spotify:episode:" + "e" * 22
CATALOG = (
    track("spotify", X, title="Xylophone", artists=("Band X",), isrc="USX000000001"),
    track("spotify", Y, title="Yellow", artists=('Band "Y"',), isrc="USY000000002"),
    track("spotify", Z, title="Zebra", artists=("Band Z",), isrc="USZ000000003"),
    track("spotify", W, title="Waltz", artists=("Band W",), isrc="USW000000004"),
    track("spotify", LOCAL, title="Rough Mix", artists=("Garage Band",)),
    track("spotify", EPISODE, title="Episode One", artists=("The Podcast",)),
)


def make(
    sim: SpotifySim, *, hash_artwork: bool = False
) -> tuple[SpotifyConnector, list[float]]:
    store = InMemoryCredentials()
    store.save(
        SP,
        {
            "client_id": "a" * 32,
            "access_token": sim.access_token,
            "refresh_token": "ref",
            "expires_at": "2026-10-26T13:00:00+00:00",
        },
    )
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    ctx = ConnectorContext(
        store,
        InMemoryQuota(),
        FixedClock(NOW),
        settings=ConnectorSettings(hash_artwork=hash_artwork),
        transport=sim.transport,
        sleep=sleep,
    )
    connector = SPOTIFY_PLUGIN.create(ctx)
    assert isinstance(connector, SpotifyConnector)
    return connector, sleeps


def sent(sim: SpotifySim, method: str, path_suffix: str = "") -> list[httpx.Request]:
    return [
        r
        for r in sim.requests
        if r.method == method
        and r.url.host == "api.spotify.com"
        and r.url.path.endswith(path_suffix)
    ]


def body(request: httpx.Request) -> Any:
    return json.loads(request.content)


# -- read ------------------------------------------------------------------


async def test_read_follows_pages_and_is_complete() -> None:
    sim = SpotifySim(catalog=CATALOG)
    uris = [(X, Y, Z)[i % 3] for i in range(120)]
    pid = sim.add_playlist("Big & <Bold>", uris, description='Rock & roll "live"')
    connector, _ = make(sim)
    snap = await connector.read_playlist(pid)
    assert list(snap.track_ids) == uris
    assert snap.is_complete and snap.expected_total == 120
    assert snap.taken_at == NOW
    assert snap.meta.name == "Big & <Bold>"
    assert snap.meta.description == 'Rock & roll "live"'
    pages = sent(sim, "GET", "/items")
    assert len(pages) == 3
    first = pages[0].url.params
    assert (first["limit"], first["offset"]) == ("50", "0")
    assert first["additional_types"] == "track,episode"


async def test_read_raises_when_total_mismatch() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Short", [X, Y])
    sim.truncate(pid)
    connector, _ = make(sim)
    with pytest.raises(ReadFailed):
        await connector.read_playlist(pid)


async def test_read_raises_when_page_totals_disagree() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Moving", [(X, Y)[i % 2] for i in range(60)])
    served = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal served
        response = sim.handle(request)
        if request.url.path.endswith("/items") and request.method == "GET":
            served += 1
            if served == 2:
                page = response.json()
                page["total"] += 1
                return httpx.Response(200, json=page)
        return response

    sim.transport = httpx.MockTransport(handler)
    connector, _ = make(sim)
    with pytest.raises(ReadFailed):
        await connector.read_playlist(pid)


async def test_read_raises_when_playlist_changed_during_read() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Busy", [X, Y])
    sim.change_during_read(pid)
    connector, _ = make(sim)
    with pytest.raises(ReadFailed, match="changed while it was being read"):
        await connector.read_playlist(pid)


@pytest.mark.parametrize("strip", [{1}, {2}, {1, 2}], ids=["first", "recheck", "both"])
async def test_read_without_snapshot_id_is_read_failed(strip: set[int]) -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X, Y])
    gets = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets
        response = sim.handle(request)
        if request.method == "GET" and request.url.path == f"/v1/playlists/{pid}":
            gets += 1
            if gets in strip:
                obj = response.json()
                obj.pop("snapshot_id")
                return httpx.Response(200, json=obj)
        return response

    sim.transport = httpx.MockTransport(handler)
    connector, _ = make(sim)
    with pytest.raises(ReadFailed, match="did not give a version"):
        await connector.read_playlist(pid)
    assert gets == min(strip)  # the first missing one stops the read


async def test_read_404_is_not_found() -> None:
    sim = SpotifySim(catalog=CATALOG)
    connector, _ = make(sim)
    with pytest.raises(PlaylistNotFound):
        await connector.read_playlist("nosuchplaylist")


async def test_read_not_owned_is_not_found() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Theirs", [X], owner="other")
    connector, _ = make(sim)
    with pytest.raises(PlaylistNotFound, match="no longer own or collaborate"):
        await connector.read_playlist(pid)
    assert sent(sim, "GET", "/items") == []


async def test_read_collaborative_playlist_of_other_owner() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Shared", [X, Y], owner="other", collaborative=True)
    connector, _ = make(sim)
    snap = await connector.read_playlist(pid)
    assert snap.track_ids == (X, Y)
    assert snap.meta.owned is False


async def test_read_500_is_read_failed_never_empty() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Flaky", [X, Y])
    sim.fail_next("GET", rf"/playlists/{pid}/items", 500, times=4)
    connector, _ = make(sim)
    with pytest.raises(ReadFailed, match="500"):
        await connector.read_playlist(pid)


async def test_item_without_identity_fails_read() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Odd", [X, Y, Z])

    def handler(request: httpx.Request) -> httpx.Response:
        response = sim.handle(request)
        if request.url.path.endswith("/items") and request.method == "GET":
            page = response.json()
            page["items"][1]["item"] = None
            return httpx.Response(200, json=page)
        return response

    sim.transport = httpx.MockTransport(handler)
    connector, _ = make(sim)
    with pytest.raises(ReadFailed, match="can't identify at position 2"):
        await connector.read_playlist(pid)


async def test_read_keeps_local_files_and_repeats() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Mixed", [X, LOCAL, X, EPISODE, LOCAL])
    connector, _ = make(sim)
    snap = await connector.read_playlist(pid)
    assert snap.track_ids == (X, LOCAL, X, EPISODE, LOCAL)
    assert snap.is_complete
    assert snap.entries[3].track.artists == ("The Podcast",)
    assert snap.entries[0].added_at == datetime(2026, 3, 1, 10, tzinfo=UTC)


async def test_read_hashes_artwork_without_credentials() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Art", [X, Y, X, LOCAL])
    connector, _ = make(sim, hash_artwork=True)
    snap = await connector.read_playlist(pid)
    hashes = [e.track.artwork_hash for e in snap.entries]
    assert hashes[0] is not None and len(hashes[0]) == 16
    assert hashes[0] == hashes[2]
    assert hashes[3] is None
    art = [r for r in sim.requests if r.url.host == "i.scdn.co"]
    assert len(art) == 2  # one fetch per distinct url
    assert all("authorization" not in r.headers for r in art)


async def test_expired_login_is_auth_required() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X])
    sim.expire_login()
    connector, _ = make(sim)
    with pytest.raises(ReadFailed) as err:
        await connector.read_playlist(pid)
    assert err.value.auth_required is True


async def test_list_playlists_pages_and_marks_unreadable() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pids = [sim.add_playlist(f"P{i}", [X]) for i in range(55)]
    theirs = sim.add_playlist("Theirs", [X], owner="other")
    connector, _ = make(sim)
    metas = {m.playlist_id: m for m in await connector.list_playlists()}
    assert set(metas) == {*pids, theirs}
    assert metas[pids[0]].track_count == 1 and metas[pids[0]].owned
    assert metas[theirs].readable is False
    assert len(sent(sim, "GET", "/me/playlists")) == 2


# -- search ----------------------------------------------------------------


async def test_search_isrc_first_then_text() -> None:
    sim = SpotifySim(catalog=CATALOG)
    connector, _ = make(sim)

    found = await connector.search(TrackQuery.from_track(CATALOG[1]))
    assert [t.track_id for t in found] == [Y]
    queries = [r.url.params for r in sent(sim, "GET", "/search")]
    assert [q["q"] for q in queries] == ["isrc:USY000000002"]

    sim.requests.clear()
    miss = TrackQuery('Yel"low', ('Band "Y"', "Other"), isrc="ZZZ000000000")
    found = await connector.search(miss)
    queries = [r.url.params for r in sent(sim, "GET", "/search")]
    assert [q["q"] for q in queries] == [
        "isrc:ZZZ000000000",
        'track:"Yellow" artist:"Band Y"',
    ]
    for q in queries:
        assert (q["type"], q["limit"], q["market"]) == ("track", "10", "from_token")
    assert [t.track_id for t in found] == [Y]

    sim.requests.clear()
    await connector.search(TrackQuery("Zebra", ("Band Z",)))
    queries = [r.url.params for r in sent(sim, "GET", "/search")]
    assert [q["q"] for q in queries] == ['track:"Zebra" artist:"Band Z"']


# -- add -------------------------------------------------------------------


async def test_add_batches_of_100() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Batch", [])
    uris = tuple((X, Y, Z)[i % 3] for i in range(250))
    connector, _ = make(sim)
    await connector.apply(pid, AddTracks("op", uris))
    posts = sent(sim, "POST", "/items")
    assert [len(body(r)["uris"]) for r in posts] == [100, 100, 50]
    assert sim.uris(pid) == list(uris)


async def test_add_skips_local_files() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Local", [X])
    connector, _ = make(sim)
    await connector.apply(pid, AddTracks("op", (Y, LOCAL, Z)))
    assert sim.uris(pid) == [X, Y, Z]
    await connector.apply(pid, AddTracks("op", (LOCAL,)))
    assert len(sent(sim, "POST", "/items")) == 1


async def test_add_failure_on_second_batch_is_maybe_applied() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Batch", [])
    uris = [X] * 100 + ["spotify:track:" + "q" * 22] + [Y] * 10
    connector, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", tuple(uris)))
    assert err.value.maybe_applied is True
    assert len(sent(sim, "POST", "/items")) == 2


async def test_add_rejected_first_batch_is_not_applied() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Batch", [X])
    sim.fail_next("POST", rf"/playlists/{pid}/items", 403)
    connector, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert err.value.maybe_applied is False
    assert sim.uris(pid) == [X]


async def test_write_timeout_is_maybe_applied_not_retried() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Slow", [X])
    sim.timeout_next("POST", rf"/playlists/{pid}/items", apply_first=True)
    connector, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert err.value.maybe_applied is True
    assert len(sent(sim, "POST", "/items")) == 1
    assert sim.uris(pid) == [X, Y]  # it landed once, and only once


async def test_429_on_write_is_retried() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Busy", [X])
    sim.fail_next("POST", rf"/playlists/{pid}/items", 429, headers={"Retry-After": "3"})
    connector, sleeps = make(sim)
    await connector.apply(pid, AddTracks("op", (Y,)))
    assert len(sent(sim, "POST", "/items")) == 2
    assert 3.0 in sleeps
    assert sim.uris(pid) == [X, Y]


# -- remove ----------------------------------------------------------------


async def test_remove_one_copy_of_repeat() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Repeat", [X, Y, X, Z])
    connector, _ = make(sim)
    await connector.apply(pid, RemoveTracks("op", (Removal(X, 2),)))
    assert sim.uris(pid) == [X, Y, Z]
    (delete,) = sent(sim, "DELETE", "/items")
    assert body(delete) == {"items": [{"uri": X}]}
    (post,) = sent(sim, "POST", "/items")
    assert body(post) == {"uris": [X], "position": 0}


async def test_remove_every_copy_needs_no_reinsert() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Repeat", [X, Y, X, LOCAL])
    connector, _ = make(sim)
    removals = (Removal(X, 0), Removal(X, 2), Removal(LOCAL, 3))
    await connector.apply(pid, RemoveTracks("op", removals))
    assert sim.uris(pid) == [Y]
    assert sent(sim, "POST", "/items") == []


async def test_remove_rejects_stale_positions() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Stale", [X, Y, Z])
    connector, _ = make(sim)
    with pytest.raises(WriteFailed, match="changed") as err:
        await connector.apply(pid, RemoveTracks("op", (Removal(Z, 0),)))
    assert err.value.maybe_applied is False
    assert sent(sim, "DELETE") == []
    assert sim.uris(pid) == [X, Y, Z]


async def test_remove_refuses_partial_local_file_removal() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Local repeat", [LOCAL, Y, LOCAL])
    connector, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, RemoveTracks("op", (Removal(LOCAL, 2),)))
    assert err.value.maybe_applied is False
    assert "only some copies of a local file" in str(err.value)
    assert sent(sim, "DELETE") == [] and sent(sim, "POST") == []
    assert sim.uris(pid) == [LOCAL, Y, LOCAL]


async def test_remove_read_failure_is_not_applied() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Flaky", [X, Y])
    sim.fail_next("GET", rf"/playlists/{pid}/items", 500, times=4)
    connector, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, RemoveTracks("op", (Removal(X, 0),)))
    assert err.value.maybe_applied is False


async def test_remove_reinsert_failure_is_maybe_applied() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Repeat", [X, Y, X])
    sim.fail_next("POST", rf"/playlists/{pid}/items", 400)
    connector, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, RemoveTracks("op", (Removal(X, 2),)))
    assert err.value.maybe_applied is True


# -- reorder ---------------------------------------------------------------


async def test_reorder_uses_minimal_moves() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Order", [X, Y, Z, W])
    connector, _ = make(sim)
    await connector.apply(pid, Reorder("op", (Y, Z, W, X)))
    assert sim.uris(pid) == [Y, Z, W, X]
    (put,) = sent(sim, "PUT", "/items")
    assert body(put) == {"range_start": 0, "insert_before": 4, "range_length": 1}


async def test_reorder_failure_after_first_move_is_maybe_applied() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Order", [X, Y, Z, W])

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.method == "PUT" and request.url.path.endswith("/items"):
            calls += 1
            if calls == 2:
                return httpx.Response(400, json={"error": {"status": 400}})
        return sim.handle(request)

    sim.transport = httpx.MockTransport(handler)
    connector, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, Reorder("op", (W, Z, Y, X)))
    assert err.value.maybe_applied is True
    assert calls == 2  # never retried; the 400 stops the sequence


async def test_reorder_put_timeout_is_not_retried() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Order", [X, Y, Z])
    sim.timeout_next("PUT", rf"/playlists/{pid}/items", apply_first=True)
    connector, _ = make(sim)
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, Reorder("op", (Y, Z, X)))
    assert err.value.maybe_applied is True
    assert len(sent(sim, "PUT", "/items")) == 1


# -- metadata and covers -----------------------------------------------------


def big_png() -> bytes:
    img = Image.effect_noise((1500, 1500), 90).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def test_update_metadata_sets_details_and_cover() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Old", [X])
    cover = "https://covers.example/big.png"
    sim.images[cover] = big_png()
    connector, _ = make(sim)
    draft = PlaylistDraft("Mix & Match <3>", 'Rock & roll, "quoted"', cover)
    await connector.apply(pid, UpdateMetadata("op", draft))

    (details,) = sent(sim, "PUT", f"/playlists/{pid}")
    assert body(details) == {"name": draft.name, "description": draft.description}
    (upload,) = sent(sim, "PUT", "/images")
    assert upload.headers["content-type"] == "image/jpeg"
    jpeg = base64.b64decode(upload.content)
    assert len(jpeg) <= 256_000
    assert len(upload.content) <= 256 * 1024
    with Image.open(io.BytesIO(jpeg)) as img:
        assert img.format == "JPEG"
    (fetch,) = [r for r in sim.requests if r.url.host == "covers.example"]
    assert "authorization" not in fetch.headers

    snap = await connector.read_playlist(pid)
    assert (snap.meta.name, snap.meta.description) == (draft.name, draft.description)
    assert snap.meta.cover_url is not None


async def test_update_metadata_dead_cover_skips_cover() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Old", [X])
    connector, _ = make(sim)
    draft = PlaylistDraft("New", "d", "https://covers.example/missing.png")
    await connector.apply(pid, UpdateMetadata("op", draft))
    assert (sim.playlists[pid].name, sim.playlists[pid].description) == ("New", "d")
    assert len(sent(sim, "PUT", f"/playlists/{pid}")) == 1
    assert sent(sim, "PUT", "/images") == []


async def test_update_metadata_undecodable_cover_skips_cover() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Old", [X])
    cover = "https://covers.example/broken.png"
    sim.images[cover] = b"this is not an image"
    connector, _ = make(sim)
    await connector.apply(pid, UpdateMetadata("op", PlaylistDraft("New", "", cover)))
    assert sim.playlists[pid].name == "New"
    assert sent(sim, "PUT", "/images") == []


async def test_update_metadata_cover_fetched_before_details() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Old", [X])
    cover = "https://covers.example/big.png"
    sim.images[cover] = big_png()
    connector, _ = make(sim)
    await connector.apply(pid, UpdateMetadata("op", PlaylistDraft("New", "", cover)))
    order = [
        "fetch" if r.url.host == "covers.example" else r.url.path
        for r in sim.requests
        if r.url.host == "covers.example" or r.method == "PUT"
    ]
    assert order == ["fetch", f"/v1/playlists/{pid}", f"/v1/playlists/{pid}/images"]


async def test_update_metadata_upload_failure_is_maybe_applied() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Old", [X])
    cover = "https://covers.example/big.png"
    sim.images[cover] = big_png()
    sim.fail_next("PUT", rf"/playlists/{pid}/images", 400)
    connector, _ = make(sim)
    op = UpdateMetadata("op", PlaylistDraft("New", "", cover))
    with pytest.raises(WriteFailed) as err:
        await connector.apply(pid, op)
    assert err.value.maybe_applied is True
    assert sim.playlists[pid].name == "New"


async def test_create_playlist_private_and_cover_failure_still_returns_meta() -> None:
    sim = SpotifySim(catalog=CATALOG)
    connector, _ = make(sim)
    draft = PlaylistDraft("Fresh & New", "d", "https://covers.example/missing.png")
    meta = await connector.create_playlist(draft)
    (post,) = sent(sim, "POST", "/me/playlists")
    assert body(post) == {"name": "Fresh & New", "description": "d", "public": False}
    assert meta.name == "Fresh & New" and meta.owned and meta.track_count == 0
    assert sim.playlists[meta.playlist_id].public is False
    assert sent(sim, "PUT", "/images") == []


async def test_create_playlist_uploads_cover() -> None:
    sim = SpotifySim(catalog=CATALOG)
    cover = "https://covers.example/big.png"
    sim.images[cover] = big_png()
    connector, _ = make(sim)
    meta = await connector.create_playlist(PlaylistDraft("Fresh", cover_url=cover))
    assert len(sent(sim, "PUT", f"/playlists/{meta.playlist_id}/images")) == 1


# -- simulator fidelity ------------------------------------------------------


async def _raw(sim: SpotifySim, url: str, **params: str) -> Any:
    async with httpx.AsyncClient(transport=sim.transport) as client:
        response = await client.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {sim.access_token}"},
        )
    assert response.status_code == 200
    return response.json()


async def test_sim_responses_match_fixture_shapes() -> None:
    sim = SpotifySim(catalog=CATALOG)
    pid = sim.add_playlist("Mine", [X, LOCAL, EPISODE], image_url="https://i.scdn.co/c")
    sim.add_playlist("Theirs", [X], owner="other")
    api = "https://api.spotify.com/v1"
    cases: Sequence[tuple[str, str, dict[str, str]]] = (
        ("me", f"{api}/me", {}),
        ("my_playlists", f"{api}/me/playlists", {}),
        ("playlist", f"{api}/playlists/{pid}", {}),
        ("playlist_items", f"{api}/playlists/{pid}/items", {"limit": "2"}),
        ("search_isrc", f"{api}/search", {"q": "isrc:USX000000001", "type": "track"}),
        ("search_tracks", f"{api}/search", {"q": 'track:"e"', "type": "track"}),
    )
    for fixture, url, params in cases:
        got = key_paths(await _raw(sim, url, **params))
        extra = got - key_paths(load_fixture("spotify", fixture))
        assert not extra, f"{fixture}: {sorted(extra)}"


def test_sim_track_objects_parse() -> None:
    from linersync.adapters.connectors.spotify.parse import parse_track

    sim = SpotifySim(catalog=CATALOG)
    for t in CATALOG:
        parsed = parse_track(sim.track_obj(t.track_id))
        assert isinstance(parsed, Track)
        assert (parsed.track_id, parsed.title) == (t.track_id, t.title)
