from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from linersync.adapters.connectors.kit.http import HttpClient, HttpSettings, RetryPolicy
from linersync.adapters.connectors.kit.images import ArtworkHasher, http_fetcher
from linersync.adapters.connectors.qobuz.auth import QobuzAuth
from linersync.adapters.connectors.qobuz.connector import QobuzConnector
from linersync.adapters.connectors.qobuz.parse import API_BASE, SERVICE
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.operations import AddTracks, Removal, RemoveTracks, UpdateMetadata
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import TrackQuery
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed
from linersync.ports.quota import QuotaPolicy
from tests.adapters.connectors.qobuz.sim import QobuzSim
from tests.adapters.connectors.shapes import key_paths, load_fixture
from tests.factories import track

CATALOG = tuple(
    track(
        "qobuz",
        str(64_000_000 + i),
        title=name.title(),
        artists=(f"{name.title()} Band",),
        album="First",
        isrc=f"GBSIM{i:07d}",
    )
    for i, name in enumerate(("alpha", "bravo", "charlie"))
)
X, Y, Z = (t.track_id for t in CATALOG)
NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)


def _client(sim: QobuzSim, base: str, auth: QobuzAuth | None = None) -> HttpClient:
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


def make_connector(
    sim: QobuzSim, *, artwork: bool = False, user_id: str | None = None
) -> QobuzConnector:
    store = InMemoryCredentials()
    store.save(SERVICE, sim.credentials())
    hasher = ArtworkHasher(http_fetcher(_client(sim, ""))) if artwork else None
    api = _client(sim, API_BASE, QobuzAuth(store))
    return QobuzConnector(api, hasher, user_id=user_id, clock=FixedClock(NOW))


@pytest.fixture
def sim() -> QobuzSim:
    return QobuzSim(catalog=CATALOG)


def _calls(sim: QobuzSim, endpoint: str) -> list[httpx.Request]:
    return [r for r in sim.requests if r.url.path.endswith(f"/{endpoint}")]


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(httpx.QueryParams(request.content.decode()))


# -- reads ---------------------------------------------------------------------


async def test_list_marks_others_playlists_not_owned(sim: QobuzSim) -> None:
    mine = sim.add_playlist("Mine", [X])
    theirs = sim.add_playlist("Theirs", [Y], owner_id=42, owner_name="Qobuz")
    metas = {m.playlist_id: m for m in await make_connector(sim).list_playlists()}
    assert (metas[mine].owned, metas[theirs].owned) == (True, False)
    assert metas[mine].track_count == 1


async def test_list_pages_through_short_pages(sim: QobuzSim) -> None:
    sim.page_cap = 2
    pids = [sim.add_playlist(f"P{i}", [X]) for i in range(5)]
    metas = await make_connector(sim).list_playlists()
    assert [m.playlist_id for m in metas] == pids


async def test_list_without_total_reads_until_an_empty_page(sim: QobuzSim) -> None:
    sim.page_cap = 2
    sim.include_list_total = False
    pids = [sim.add_playlist(f"P{i}", [X]) for i in range(5)]
    metas = await make_connector(sim).list_playlists()
    assert [m.playlist_id for m in metas] == pids


async def test_list_without_user_block_uses_stored_id(sim: QobuzSim) -> None:
    sim.include_user = False
    theirs = sim.add_playlist("Theirs", [Y], owner_id=42)
    connector = make_connector(sim, user_id=str(sim.user_id))
    (meta,) = await connector.list_playlists()
    assert (meta.playlist_id, meta.owned) == (theirs, False)


async def test_read_is_complete_ordered_with_repeats(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, X, Z])
    snap = await make_connector(sim).read_playlist(pid)
    assert snap.track_ids == (X, Y, X, Z)
    assert snap.is_complete
    assert snap.meta.version_token is None
    assert snap.entries[0].track.isrc == "GBSIM0000000"
    assert snap.entries[0].added_at == datetime.fromtimestamp(1730000000, UTC)
    assert len(_calls(sim, "playlist/get")) == 1  # one page needs no re-check


async def test_read_pages_and_rechecks(sim: QobuzSim) -> None:
    sim.page_cap = 2
    wanted = [X, Y, Z, X, Y]
    pid = sim.add_playlist("Mix", wanted)
    snap = await make_connector(sim).read_playlist(pid)
    assert list(snap.track_ids) == wanted
    offsets = [r.url.params.get("offset") for r in _calls(sim, "playlist/get")]
    # Each later page starts one back, at the last song already read.
    assert offsets == ["0", "1", "2", "3", None]


async def test_read_change_during_read_fails(sim: QobuzSim) -> None:
    sim.page_cap = 2
    pid = sim.add_playlist("Mix", [X, Y, Z, X])
    sim.change_during_read(pid)
    with pytest.raises(ReadFailed, match="changed while it was being read"):
        await make_connector(sim).read_playlist(pid)


async def test_read_shift_with_same_length_fails(sim: QobuzSim) -> None:
    sim.page_cap = 2
    pid = sim.add_playlist("Mix", [X, Y, Z, X])
    sim.shift_during_read(pid)
    with pytest.raises(ReadFailed, match="changed while it was being read"):
        await make_connector(sim).read_playlist(pid)


async def test_rejected_app_id_needs_sign_in(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    connector = make_connector(sim)
    sim.app_id = "987654321"  # Qobuz moved the web player to a new app ID
    with pytest.raises(ReadFailed) as read:
        await connector.read_playlist(pid)
    assert read.value.auth_required is True
    with pytest.raises(WriteFailed) as write:
        await connector.apply(pid, AddTracks("op", (Y,)))
    assert write.value.auth_required is True


async def test_read_repeated_occurrence_fails(sim: QobuzSim) -> None:
    sim.page_cap = 2
    pid = sim.add_playlist("Mix", [X, Y, Z])
    real = sim.handle

    def stale_second_page(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("offset") == "1":
            request = httpx.Request(
                "GET",
                request.url.copy_set_param("offset", "0"),
                headers=request.headers,
            )
        return real(request)

    sim.transport = httpx.MockTransport(stale_second_page)
    connector = make_connector(sim)
    with pytest.raises(ReadFailed, match="changed while it was being read"):
        await connector.read_playlist(pid)


async def test_read_empty_playlist(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Empty", [])
    snap = await make_connector(sim).read_playlist(pid)
    assert (snap.track_ids, snap.is_complete) == ((), True)


async def test_truncated_read_fails(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.truncate(pid)
    with pytest.raises(ReadFailed):
        await make_connector(sim).read_playlist(pid)


async def test_unknown_playlist_is_not_found(sim: QobuzSim) -> None:
    with pytest.raises(PlaylistNotFound):
        await make_connector(sim).read_playlist("1")


async def test_rejected_token_needs_sign_in(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.expire_token()
    with pytest.raises(ReadFailed) as e:
        await make_connector(sim).read_playlist(pid)
    assert e.value.auth_required is True
    with pytest.raises(ReadFailed):
        await make_connector(sim).list_playlists()


async def test_read_hashes_artwork(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    snap = await make_connector(sim, artwork=True).read_playlist(pid)
    hashes = [e.track.artwork_hash for e in snap.entries]
    assert all(hashes) and hashes[0] != hashes[1]
    art = [r for r in sim.requests if r.url.host == "static.qobuz.com"]
    assert art and all("x-user-auth-token" not in r.headers for r in art)


# -- search --------------------------------------------------------------------


async def test_search_by_isrc(sim: QobuzSim) -> None:
    found = await make_connector(sim).search(TrackQuery.from_track(CATALOG[1]))
    assert [t.track_id for t in found] == [Y]  # the text hit is the same song
    queries = [r.url.params["query"] for r in _calls(sim, "track/search")]
    assert queries == ["GBSIM0000001", "Bravo Band Bravo"]


async def test_search_keeps_text_hits_beside_isrc_hits() -> None:
    # The source's ISRC may be only on compilations; the album copy has
    # another ISRC and only the text search finds it.
    comp = track("qobuz", "1", title="Routines", artists=("Band",), isrc="I1")
    album = track("qobuz", "2", title="Routines", artists=("Band",), isrc="I2")
    sim = QobuzSim(catalog=[comp, album])
    query = TrackQuery("Routines", ("Band",), isrc="I1")
    found = await make_connector(sim).search(query)
    assert [t.track_id for t in found] == ["1", "2"]


async def test_search_falls_back_to_metadata(sim: QobuzSim) -> None:
    query = TrackQuery("Bravo", ("Bravo Band",), isrc="USUNKNOWN0001")
    found = await make_connector(sim).search(query)
    assert [t.track_id for t in found] == [Y]
    queries = [r.url.params["query"] for r in _calls(sim, "track/search")]
    assert queries == ["USUNKNOWN0001", "Bravo Band Bravo"]


async def test_search_error_is_read_failed(sim: QobuzSim) -> None:
    sim.fail_next(r"track/search", 500, times=RetryPolicy().max_attempts)
    with pytest.raises(ReadFailed):
        await make_connector(sim).search(TrackQuery("Bravo", ()))


# -- writes --------------------------------------------------------------------


async def test_create_playlist_is_private(sim: QobuzSim) -> None:
    meta = await make_connector(sim).create_playlist(PlaylistDraft("New", "Notes"))
    assert (meta.name, meta.description, meta.owned) == ("New", "Notes", True)
    (call,) = _calls(sim, "playlist/create")
    assert _form(call) == {
        "name": "New",
        "description": "Notes",
        "is_public": "false",
        "is_collaborative": "false",
    }
    assert sim.playlists[meta.playlist_id].is_public is False


async def test_add_keeps_repeats_in_order(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    await make_connector(sim).apply(pid, AddTracks("op", (Y, X, Y)))
    assert sim.track_ids(pid) == [X, Y, X, Y]
    (call,) = _calls(sim, "playlist/addTracks")
    assert _form(call)["no_duplicate"] == "false"


async def test_add_in_batches_of_fifty(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [])
    ids = tuple((X, Y, Z)[i % 3] for i in range(51))
    await make_connector(sim).apply(pid, AddTracks("op", ids))
    assert sim.track_ids(pid) == list(ids)
    sizes = [
        len(_form(r)["track_ids"].split(",")) for r in _calls(sim, "playlist/addTracks")
    ]
    assert sizes == [50, 1]


async def test_add_timeout_may_have_applied(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.timeout_next(r"playlist/addTracks", apply_first=True)
    with pytest.raises(WriteFailed) as e:
        await make_connector(sim).apply(pid, AddTracks("op", (Y,)))
    assert e.value.maybe_applied is True
    assert len(_calls(sim, "playlist/addTracks")) == 1  # never retried


async def test_add_to_others_playlist_is_rejected(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Theirs", [X], owner_id=42)
    with pytest.raises(WriteFailed) as e:
        await make_connector(sim).apply(pid, AddTracks("op", (Y,)))
    assert e.value.maybe_applied is False


async def test_remove_exact_occurrence(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, X, Z])
    third = sim.playlist_track_ids(pid)[2]
    await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 2),)))
    assert sim.track_ids(pid) == [X, Y, Z]
    (call,) = _calls(sim, "playlist/deleteTracks")
    assert _form(call) == {"playlist_id": pid, "playlist_track_ids": str(third)}


async def test_remove_refuses_stale_plan(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    with pytest.raises(WriteFailed, match="changed since it was planned") as e:
        await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 1),)))
    assert e.value.maybe_applied is False
    assert _calls(sim, "playlist/deleteTracks") == []


async def test_remove_when_read_fails_is_not_applied(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.truncate(pid)
    with pytest.raises(WriteFailed) as e:
        await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 0),)))
    assert e.value.maybe_applied is False


async def test_update_metadata(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    await make_connector(sim).apply(
        pid, UpdateMetadata("op", PlaylistDraft("Renamed", "New notes"))
    )
    p = sim.playlists[pid]
    assert (p.name, p.description) == ("Renamed", "New notes")


# -- shapes --------------------------------------------------------------------


async def _raw(sim: QobuzSim, endpoint: str, params: dict[str, Any]) -> Any:
    store = InMemoryCredentials()
    store.save(SERVICE, sim.credentials())
    client = _client(sim, API_BASE, QobuzAuth(store))
    response = await client.request(
        "GET", f"{API_BASE}/{endpoint}", idempotent=True, params=params
    )
    return response.json()


async def test_sim_matches_fixture_shapes(sim: QobuzSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    cases: list[tuple[str, str, dict[str, Any]]] = [
        ("playlist_get", "playlist/get", {"playlist_id": pid, "extra": "tracks"}),
        ("user_playlists", "playlist/getUserPlaylists", {"limit": 10}),
        ("track_search", "track/search", {"query": "alpha"}),
    ]
    for fixture, endpoint, params in cases:
        got = await _raw(sim, endpoint, params)
        extra = key_paths(got) - key_paths(load_fixture("qobuz", fixture))
        assert not extra, f"{fixture}: {sorted(extra)}"
