from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from linersync.adapters.connectors.kit.http import HttpClient, HttpSettings, RetryPolicy
from linersync.adapters.connectors.kit.images import ArtworkHasher, http_fetcher
from linersync.adapters.connectors.youtube_music.connector import YouTubeMusicConnector
from linersync.adapters.connectors.youtube_music.parse import API_BASE, SERVICE
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
from tests.adapters.connectors.youtube_music.sim import OMV, YouTubeMusicSim
from tests.factories import track

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
CATALOG = tuple(
    track(
        "youtube_music",
        f"vid{i:08d}",
        title=name.title(),
        artists=(f"{name.title()} Band",),
        album="First",
    )
    for i, name in enumerate(("alpha", "bravo", "charlie"))
)
X, Y, Z = (t.track_id for t in CATALOG)


class BearerAuth:
    def __init__(self, sim: YouTubeMusicSim) -> None:
        self._sim = sim

    async def headers(self) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {self._sim.access_token}"}

    async def refresh(self) -> bool:
        return False


def _client(sim: YouTubeMusicSim, base: str, *, auth: bool) -> HttpClient:
    async def no_sleep(_: float) -> None:
        return None

    return HttpClient(
        SERVICE,
        base,
        settings=HttpSettings(),
        quota=InMemoryQuota(),
        quota_policy=QuotaPolicy(),
        clock=FixedClock(NOW),
        auth=BearerAuth(sim) if auth else None,
        transport=sim.transport,
        sleep=no_sleep,
    )


def make_connector(
    sim: YouTubeMusicSim, *, artwork: bool = False
) -> YouTubeMusicConnector:
    hasher = (
        ArtworkHasher(http_fetcher(_client(sim, "", auth=False))) if artwork else None
    )
    return YouTubeMusicConnector(
        _client(sim, API_BASE, auth=True), hasher, clock=FixedClock(NOW)
    )


@pytest.fixture
def sim() -> YouTubeMusicSim:
    return YouTubeMusicSim(catalog=CATALOG)


def _bodies(sim: YouTubeMusicSim, endpoint: str) -> list[dict[str, Any]]:
    import json

    return [
        json.loads(r.content)
        for r in sim.requests
        if r.url.host == "music.youtube.com" and r.url.path.endswith(f"/{endpoint}")
    ]


# -- reads -----------------------------------------------------------------


async def test_read_follows_continuations_in_order_with_repeats(
    sim: YouTubeMusicSim,
) -> None:
    wanted = [(X, Y, Z, X)[i % 4] for i in range(250)]
    pid = sim.add_playlist("Long", wanted)
    snap = await make_connector(sim, artwork=True).read_playlist(pid)
    assert snap.is_complete
    assert list(snap.track_ids) == wanted
    assert snap.meta.name == "Long"
    assert snap.meta.owned is True
    first = snap.entries[0].track
    assert (first.title, first.artists, first.album) == (
        "Alpha",
        ("Alpha Band",),
        "First",
    )
    assert first.duration_ms == CATALOG[0].duration_ms
    assert first.isrc is None
    assert first.artwork_hash is not None


async def test_list_playlists_follows_continuations(sim: YouTubeMusicSim) -> None:
    pids = [sim.add_playlist(f"P{i}", [X]) for i in range(60)]
    metas = await make_connector(sim).list_playlists()
    assert [m.playlist_id for m in metas] == pids
    assert metas[0].track_count == 1


async def test_unknown_playlist_is_not_found(sim: YouTubeMusicSim) -> None:
    with pytest.raises(PlaylistNotFound):
        await make_connector(sim).read_playlist("PL" + "0" * 32)


async def test_read_error_raises_read_failed(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.fail_next("browse", 500, times=RetryPolicy().max_attempts)
    with pytest.raises(ReadFailed):
        await make_connector(sim).read_playlist(pid)


async def test_change_during_read_fails(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.change_during_read(pid)
    with pytest.raises(ReadFailed, match="changed while it was being read"):
        await make_connector(sim).read_playlist(pid)


async def test_song_moved_between_later_pages_fails_the_read(
    sim: YouTubeMusicSim,
) -> None:
    pid = sim.add_playlist("Long", [(X, Y, Z)[i % 3] for i in range(300)])
    # After page 2 is served, a song from it moves into page 3: page 3 would
    # repeat it and miss another, with the same count and first page.
    sim.move_during_read(pid, after=100, src=150, dst=250)
    with pytest.raises(ReadFailed, match="changed while it was being read"):
        await make_connector(sim).read_playlist(pid)


async def test_empty_playlist_without_a_count_reads_as_empty(
    sim: YouTubeMusicSim,
) -> None:
    sim.empty_has_count = False
    pid = sim.add_playlist("Empty", [])
    snap = await make_connector(sim).read_playlist(pid)
    assert snap.track_ids == ()
    assert snap.is_complete
    assert snap.meta.track_count == 0


async def test_truncated_read_is_incomplete(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    sim.truncate(pid)
    snap = await make_connector(sim).read_playlist(pid)
    assert snap.is_complete is False
    assert snap.track_ids == (X, Y)


async def test_greyed_out_item_makes_snapshot_incomplete(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, Z])
    sim.grey_out(pid, 1)
    snap = await make_connector(sim).read_playlist(pid)
    assert snap.expected_total == 3
    assert snap.track_ids == (X, Z)
    assert snap.is_complete is False
    assert snap.missing_reason is not None
    assert "1 song" in snap.missing_reason
    assert "remove it" in snap.missing_reason


async def test_saved_playlist_is_not_owned(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Theirs", [X], owned=False)
    snap = await make_connector(sim).read_playlist(pid)
    assert snap.meta.owned is False


async def test_requests_carry_the_web_remix_context(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    await make_connector(sim).read_playlist(pid)
    (first, *_) = _bodies(sim, "browse")
    assert first["browseId"] == f"VL{pid}"
    assert first["context"]["client"]["clientName"] == "WEB_REMIX"


# -- search ----------------------------------------------------------------


async def test_isrc_search_marks_hits(sim: YouTubeMusicSim) -> None:
    sim.isrcs["GBAYE0601498"] = Y
    query = TrackQuery("Bravo", ("Bravo Band",), isrc="GBAYE0601498")
    hits = await make_connector(sim).search(query)
    assert hits[0].track_id == Y
    assert hits[0].found_by_isrc
    assert [b["query"] for b in _bodies(sim, "search")] == [
        "GBAYE0601498",
        "Bravo Band Bravo",
    ]
    # Y came back from both searches; it keeps the ISRC mark, once.
    assert [h.track_id for h in hits].count(Y) == 1


async def test_isrc_never_copied_onto_candidate(sim: YouTubeMusicSim) -> None:
    sim.isrcs["GBAYE0601498"] = Y
    query = TrackQuery("Bravo", ("Bravo Band",), isrc="GBAYE0601498")
    hits = await make_connector(sim).search(query)
    assert all(h.isrc is None for h in hits)


async def test_unindexed_isrc_still_searches_metadata(sim: YouTubeMusicSim) -> None:
    # YouTube answers any ISRC with something; here it isn't the song.
    query = TrackQuery("Charlie", ("Charlie Band",), isrc="USXXX0000001")
    hits = await make_connector(sim).search(query)
    by_id = {h.track_id: h for h in hits}
    assert Z in by_id
    assert by_id[Z].found_by_isrc is False
    unrelated = [h for h in hits if h.track_id != Z]
    assert unrelated and all(h.found_by_isrc for h in unrelated)


async def test_isrc_hit_kept_when_it_is_a_video() -> None:
    video = track("youtube_music", "omv00000001", title="Echo", artists=("Echo Band",))
    sim = YouTubeMusicSim(
        catalog=(*CATALOG, video),
        isrcs={"GBAYE0601498": video.track_id},
        video_types={video.track_id: OMV},
    )
    query = TrackQuery("Echo", ("Echo Band",), isrc="GBAYE0601498")
    hits = await make_connector(sim).search(query)
    assert hits[0].track_id == video.track_id
    assert hits[0].found_by_isrc


async def test_metadata_search_when_no_isrc(sim: YouTubeMusicSim) -> None:
    hits = await make_connector(sim).search(TrackQuery("Charlie", ("Charlie Band",)))
    assert [h.track_id for h in hits] == [Z]
    assert hits[0].found_by_isrc is False
    assert [b["query"] for b in _bodies(sim, "search")] == ["Charlie Band Charlie"]


async def test_art_tracks_preferred() -> None:
    video = track(
        "youtube_music", "omv00000001", title="Alpha", artists=("Alpha Band",)
    )
    sim = YouTubeMusicSim(catalog=(*CATALOG, video), video_types={video.track_id: OMV})
    hits = await make_connector(sim).search(TrackQuery("Alpha", ("Alpha Band",)))
    assert [h.track_id for h in hits] == [X]


async def test_videos_kept_when_no_art_track() -> None:
    video = track(
        "youtube_music", "omv00000001", title="Delta", artists=("Delta Band",)
    )
    sim = YouTubeMusicSim(catalog=(*CATALOG, video), video_types={video.track_id: OMV})
    hits = await make_connector(sim).search(TrackQuery("Delta", ("Delta Band",)))
    assert [h.track_id for h in hits] == [video.track_id]


# -- writes ----------------------------------------------------------------


def _actions(sim: YouTubeMusicSim) -> list[list[dict[str, Any]]]:
    return [b["actions"] for b in _bodies(sim, "browse/edit_playlist")]


async def test_create_playlist_is_private(sim: YouTubeMusicSim) -> None:
    meta = await make_connector(sim).create_playlist(PlaylistDraft("New", "About"))
    p = sim.playlists[meta.playlist_id]
    assert (p.name, p.description, p.privacy) == ("New", "About", "PRIVATE")
    assert (meta.name, meta.track_count, meta.owned) == ("New", 0, True)


async def test_create_truncates_long_name(sim: YouTubeMusicSim) -> None:
    meta = await make_connector(sim).create_playlist(
        PlaylistDraft("n" * 200, "d" * 6000)
    )
    p = sim.playlists[meta.playlist_id]
    assert (len(p.name), len(p.description)) == (150, 5000)


async def test_add_batches_of_fifty(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [])
    ids = tuple((X, Y, Z)[i % 3] for i in range(120))
    await make_connector(sim).apply(pid, AddTracks("op", ids))
    assert sim.track_ids(pid) == list(ids)
    assert [len(a) for a in _actions(sim)] == [50, 50, 20]


async def test_add_keeps_duplicates(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    await make_connector(sim).apply(pid, AddTracks("op", (X, X)))
    assert sim.track_ids(pid) == [X, X, X]
    assert all(a["dedupeOption"] == "DEDUPE_OPTION_SKIP" for a in _actions(sim)[0])


async def test_remove_one_of_duplicates_by_position(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, X, Z, X])
    sets = sim.set_ids(pid)
    await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 2),)))
    assert sim.track_ids(pid) == [X, Y, Z, X]
    assert sim.set_ids(pid) == [sets[0], sets[1], sets[3], sets[4]]


async def test_remove_refuses_incomplete_playlist(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, Z])
    sim.grey_out(pid, 1)
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 0),)))
    assert info.value.maybe_applied is False
    assert sim.track_ids(pid) == [X, Y, Z]


async def test_remove_refuses_short_read(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y, X])
    sim.truncate(pid)
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(X, 2),)))
    assert info.value.maybe_applied is False


async def test_remove_changed_playlist_is_replanned(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X, Y])
    with pytest.raises(WriteFailed, match="replanned") as info:
        await make_connector(sim).apply(pid, RemoveTracks("op", (Removal(Z, 1),)))
    assert info.value.maybe_applied is False


async def test_reorder_reaches_target(sim: YouTubeMusicSim) -> None:
    order = [(X, Y, Z)[i % 3] for i in range(30)]
    pid = sim.add_playlist("Mix", order)
    target = tuple(reversed(order))
    await make_connector(sim).apply(pid, Reorder("op", target))
    assert sim.track_ids(pid) == list(target)


async def test_update_metadata(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Old", [X])
    await make_connector(sim).apply(
        pid, UpdateMetadata("op", PlaylistDraft("New", "Text"))
    )
    p = sim.playlists[pid]
    assert (p.name, p.description) == ("New", "Text")
    assert len(_actions(sim)) == 1


async def test_status_failed_is_not_applied(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.fail_status_next()
    with pytest.raises(WriteFailed, match="didn't make the change") as info:
        await make_connector(sim).apply(pid, AddTracks("op", (Y,)))
    assert info.value.maybe_applied is False
    assert sim.track_ids(pid) == [X]


async def test_write_timeout_is_maybe_applied(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Mix", [X])
    sim.timeout_next("browse/edit_playlist", apply_first=True)
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, AddTracks("op", (Y,)))
    assert info.value.maybe_applied is True
    assert sim.track_ids(pid) == [X, Y]  # never sent twice


async def test_write_to_saved_playlist_is_rejected(sim: YouTubeMusicSim) -> None:
    pid = sim.add_playlist("Theirs", [X], owned=False)
    with pytest.raises(WriteFailed) as info:
        await make_connector(sim).apply(pid, AddTracks("op", (Y,)))
    assert info.value.maybe_applied is False


# -- simulator shapes ------------------------------------------------------


async def _raw(sim: YouTubeMusicSim, endpoint: str, body: dict[str, Any]) -> Any:
    from linersync.adapters.connectors.youtube_music.parse import context

    async with httpx.AsyncClient(transport=sim.transport) as client:
        response = await client.post(
            f"{API_BASE}/{endpoint}",
            params={"prettyPrint": "false"},
            json={"context": context(), **body},
            headers={"Authorization": f"Bearer {sim.access_token}"},
        )
    assert response.status_code == 200, response.text
    return response.json()


async def test_sim_shapes_match_fixtures(sim: YouTubeMusicSim) -> None:
    long = sim.add_playlist("Long", [X] * 150)
    pid = sim.add_playlist("Mix", [X, Y])
    sim.grey_out(pid, 1)
    for _ in range(30):
        sim.add_playlist("Filler", [X])
    first = await _raw(sim, "browse", {"browseId": f"VL{long}"})
    cases = [
        ("owned_playlist", await _raw(sim, "browse", {"browseId": f"VL{pid}"})),
        (
            "playlist_continuation",
            await _raw(sim, "browse", {"continuation": f"pl:{long}:100"}),
        ),
        (
            "library_playlists",
            await _raw(sim, "browse", {"browseId": "FEmusic_liked_playlists"}),
        ),
        ("library_continuation", await _raw(sim, "browse", {"continuation": "lib:25"})),
        (
            "search_songs",
            await _raw(
                sim,
                "search",
                {"query": "alpha", "params": "EgWKAQIIAWoMEA4QChADEAQQCRAF"},
            ),
        ),
        ("account_menu", await _raw(sim, "account/account_menu", {})),
    ]
    assert first["contents"]
    for fixture, got in cases:
        extra = key_paths(got) - key_paths(load_fixture("youtube_music", fixture))
        assert not extra, f"{fixture}: {sorted(extra)}"
