from datetime import UTC, datetime

import httpx
import pytest

from linersync.adapters.connectors.jellyfin.auth import ApiKeyAuth
from linersync.adapters.connectors.jellyfin.connector import JellyfinConnector
from linersync.adapters.connectors.jellyfin.parse import SERVICE
from linersync.adapters.connectors.kit.http import HttpClient, HttpSettings, RetryPolicy
from linersync.adapters.connectors.kit.images import ArtworkHasher, http_fetcher
from linersync.adapters.memory.quota import InMemoryQuota
from linersync.adapters.memory.runtime import FixedClock
from linersync.domain.operations import AddTracks
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import TrackQuery
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed
from linersync.ports.quota import QuotaPolicy
from tests.adapters.connectors.jellyfin.sim import BASE_URL, JellyfinSim
from tests.adapters.connectors.shapes import key_paths, load_fixture
from tests.factories import track

CATALOG = tuple(
    track(
        "jellyfin",
        f"{i + 1:032x}",
        title=name.title(),
        artists=(f"{name.title()} Band",),
        album="First",
    )
    for i, name in enumerate(("alpha", "bravo", "charlie"))
)
X, Y, Z = (t.track_id for t in CATALOG)
USER_ID = "u" * 32


def _connector(sim: JellyfinSim) -> JellyfinConnector:
    async def no_sleep(_: float) -> None:
        return None

    async def user_id() -> str:
        return USER_ID

    clock = FixedClock(datetime(2026, 10, 30, 12, tzinfo=UTC))
    api = HttpClient(
        SERVICE,
        BASE_URL,
        settings=HttpSettings(),
        quota=InMemoryQuota(),
        quota_policy=QuotaPolicy(),
        clock=clock,
        auth=ApiKeyAuth(sim.api_key),
        transport=sim.transport,
        sleep=no_sleep,
    )
    return JellyfinConnector(api, BASE_URL, user_id, None, clock=clock)


@pytest.fixture
def sim() -> JellyfinSim:
    return JellyfinSim(catalog=CATALOG)


async def test_read_paginates_in_order_with_repeats(sim: JellyfinSim) -> None:
    wanted = [(X, Y, Z, X)[i % 4] for i in range(250)]
    pid = sim.add_playlist("Long", wanted)
    snap = await _connector(sim).read_playlist(pid)
    assert snap.is_complete
    assert list(snap.track_ids) == wanted
    assert snap.meta.name == "Long"
    assert snap.entries[0].track.duration_ms == CATALOG[0].duration_ms


async def test_unknown_playlist_is_not_found(sim: JellyfinSim) -> None:
    with pytest.raises(PlaylistNotFound):
        await _connector(sim).read_playlist("0" * 32)


async def test_read_error_raises_read_failed(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.fail_next("GET", r"/Playlists/.*/Items", 500, times=RetryPolicy().max_attempts)
    with pytest.raises(ReadFailed):
        await _connector(sim).read_playlist(pid)


async def test_total_changing_mid_read_fails(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Long", [X] * 150)
    sim.change_during_read(pid)
    with pytest.raises(ReadFailed, match="changed"):
        await _connector(sim).read_playlist(pid)


async def test_truncated_read_fails(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.truncate(pid)
    with pytest.raises(ReadFailed, match="2 of 3"):
        await _connector(sim).read_playlist(pid)


async def test_non_audio_entry_makes_snapshot_incomplete(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.add_non_audio(pid)
    snap = await _connector(sim).read_playlist(pid)
    assert snap.track_ids == (X, Y)
    assert snap.expected_total == 3
    assert snap.is_complete is False


async def test_read_with_revoked_key_is_auth_required(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.revoke_key()
    with pytest.raises(ReadFailed) as info:
        await _connector(sim).read_playlist(pid)
    assert info.value.auth_required


async def test_search_returns_audio_candidates(sim: JellyfinSim) -> None:
    found = await _connector(sim).search(TrackQuery("Bravo", ("Bravo Band",)))
    assert [t.track_id for t in found] == [Y]


async def test_writes_raise_write_failed_not_applied(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    connector = _connector(sim)
    with pytest.raises(WriteFailed) as created:
        await connector.create_playlist(PlaylistDraft("New"))
    assert created.value.maybe_applied is False
    with pytest.raises(WriteFailed) as added:
        await connector.apply(pid, AddTracks("op-1", (Y,)))
    assert added.value.maybe_applied is False
    assert sim.playlists[pid].ids == [X]


async def test_list_playlists_metas(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y], overview="Weekend")
    metas = await _connector(sim).list_playlists()
    assert [(m.playlist_id, m.name, m.description, m.track_count) for m in metas] == [
        (pid, "Mix", "Weekend", 2)
    ]
    assert all(m.service == "jellyfin" for m in metas)


async def test_tracks_carry_artwork_hash(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    connector = _connector(sim)
    images = HttpClient(
        SERVICE,
        "",
        settings=HttpSettings(),
        quota=InMemoryQuota(),
        quota_policy=QuotaPolicy(),
        clock=FixedClock(datetime(2026, 10, 30, tzinfo=UTC)),
        transport=sim.transport,
    )
    connector._artwork = ArtworkHasher(http_fetcher(images))
    snap = await connector.read_playlist(pid)
    assert snap.entries[0].track.artwork_hash


@pytest.mark.parametrize(
    ("fixture", "path", "params"),
    [
        ("users", "/Users", {}),
        ("items_playlists", "/Items", {"IncludeItemTypes": "Playlist"}),
        ("search_audio", "/Items", {"IncludeItemTypes": "Audio", "SearchTerm": "a"}),
    ],
)
async def test_sim_shapes_stay_within_fixtures(
    sim: JellyfinSim, fixture: str, path: str, params: dict[str, str]
) -> None:
    sim.add_playlist("Mix", [X])
    got = await _raw(sim, path, {"userId": USER_ID, **params})
    extra = key_paths(got) - key_paths(load_fixture("jellyfin", fixture))
    assert not extra, f"{fixture}: {sorted(extra)}"


async def test_sim_playlist_items_shape(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    got = await _raw(sim, f"/Playlists/{pid}/Items", {"userId": USER_ID})
    extra = key_paths(got) - key_paths(load_fixture("jellyfin", "playlist_items"))
    assert not extra, sorted(extra)


async def _raw(sim: JellyfinSim, path: str, params: dict[str, str]) -> object:
    async with httpx.AsyncClient(transport=sim.transport) as client:
        response = await client.get(
            BASE_URL + path,
            params=params,
            headers={"Authorization": f'MediaBrowser Token="{sim.api_key}"'},
        )
    return response.json()


async def test_same_length_change_mid_read_fails(sim: JellyfinSim) -> None:
    pid = sim.add_playlist("Long", [(X, Y, Z)[i % 3] for i in range(150)])
    sim.swap_during_read(pid)
    with pytest.raises(ReadFailed, match="changed"):
        await _connector(sim).read_playlist(pid)


async def test_search_reply_not_understood_is_read_failed(sim: JellyfinSim) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("IncludeItemTypes") == "Audio":
            return httpx.Response(200, json={"Items": [{"Type": "Audio"}]})
        return sim.handle(request)

    sim.transport = httpx.MockTransport(handle)
    with pytest.raises(ReadFailed, match="not understood"):
        await _connector(sim).search(TrackQuery("Alpha", ()))
