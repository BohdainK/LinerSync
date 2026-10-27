import pytest

from linersync.adapters.fake.connector import FakeConnector
from linersync.application.job_guard import (
    GuardedConnector,
    JobCancelled,
    JobGuard,
    referenced_keys,
)
from linersync.domain.capabilities import Capability
from linersync.domain.matching.types import NeedsAttention, ScoredCandidate
from linersync.domain.operations import (
    AddTracks,
    ChangeNotMade,
    CreatePlaylist,
    MemberPlan,
    Removal,
    RemoveTracks,
    Reorder,
    SyncPlan,
    UnresolvedEntry,
    UpdateMetadata,
)
from linersync.domain.playlists import PlaylistDraft, PlaylistSnapshot
from linersync.domain.sync import SyncedEntry
from linersync.domain.tracks import ServiceId, TrackKey, TrackQuery
from linersync.ports.errors import ReadFailed, WriteFailed
from tests.factories import snapshot, track

SPOTIFY = ServiceId("spotify")
APPLE = ServiceId("apple_music")


def _apple() -> FakeConnector:
    apple = FakeConnector(
        APPLE,
        "Apple Music",
        frozenset(Capability),
        catalog=[track("apple_music", "A", title="a")],
    )
    apple.add_playlist(snapshot("apple_music", "A1", ["X", "Y"]))
    return apple


def _guard(reported: list[ServiceId] | None = None, **kw: bool) -> JobGuard:
    sink = reported if reported is not None else []
    return JobGuard(sink.append, **kw)


def test_check_raises_only_before_first_write() -> None:
    guard = _guard()
    assert not guard.wrote and not guard.raised
    guard.check()

    guard.cancel_requested = True
    with pytest.raises(JobCancelled):
        guard.check()
    assert guard.raised

    written = _guard()
    written.wrote = True
    written.cancel_requested = True
    written.check()
    assert not written.raised

    fixed = _guard(cancellable=False)
    assert fixed.wrote
    fixed.cancel_requested = True
    fixed.check()
    assert not fixed.raised


async def test_reads_record_tracks() -> None:
    inner = _apple()
    guard = _guard()
    conn = GuardedConnector(inner, guard)
    assert conn.service == APPLE
    assert conn.display_name == "Apple Music"
    assert conn.capabilities == inner.capabilities
    assert conn.limits == inner.limits

    await conn.list_playlists()
    snap = await conn.read_playlist("A1")
    found = await conn.search(TrackQuery("a", ("Artist",)))

    assert [t.track_id for t in found] == ["A"]
    assert set(guard.seen) == {
        TrackKey(APPLE, "X"),
        TrackKey(APPLE, "Y"),
        TrackKey(APPLE, "A"),
    }
    assert guard.seen[TrackKey(APPLE, "X")] == snap.entries[0].track

    guard.cancel_requested = True
    with pytest.raises(JobCancelled):
        await conn.read_playlist("A1")
    with pytest.raises(JobCancelled):
        await conn.search(TrackQuery("a", ("Artist",)))
    with pytest.raises(JobCancelled):
        await conn.list_playlists()


class _Expired(FakeConnector):
    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        raise ReadFailed(self.service, "token revoked", auth_required=True)

    async def apply(
        self,
        playlist_id: str,
        op: UpdateMetadata | AddTracks | Reorder | RemoveTracks,
    ) -> None:
        raise WriteFailed(self.service, "token revoked", auth_required=True)


async def test_auth_required_reported_then_reraised() -> None:
    reported: list[ServiceId] = []
    conn = GuardedConnector(
        _Expired(SPOTIFY, "Spotify", frozenset(Capability)), _guard(reported)
    )

    with pytest.raises(ReadFailed):
        await conn.read_playlist("P1")
    assert reported == [SPOTIFY]

    with pytest.raises(WriteFailed):
        await conn.apply("P1", AddTracks("op", ("a",)))
    assert reported == [SPOTIFY, SPOTIFY]

    plain = FakeConnector(SPOTIFY, "Spotify", frozenset(Capability))
    plain.fail_reads("P1")
    plain.add_playlist(snapshot("spotify", "P1", []))
    with pytest.raises(ReadFailed):
        await GuardedConnector(plain, _guard(reported)).read_playlist("P1")
    assert reported == [SPOTIFY, SPOTIFY]


async def test_writes_never_raise_cancel() -> None:
    inner = _apple()
    guard = _guard()
    guard.cancel_requested = True
    conn = GuardedConnector(inner, guard)

    meta = await conn.create_playlist(PlaylistDraft("New"))
    assert guard.wrote
    await conn.apply(meta.playlist_id, AddTracks("op", ("A",)))

    assert inner.calls == ["create:New", f"apply:AddTracks:{meta.playlist_id}"]
    await conn.read_playlist(meta.playlist_id)
    assert not guard.raised

    other = _guard()
    other.cancel_requested = True
    await GuardedConnector(_apple(), other).apply("A1", AddTracks("op", ("A",)))
    assert other.wrote and not other.raised


def test_referenced_keys() -> None:
    dst0 = MemberPlan(
        member_id="dst0",
        operations=(
            CreatePlaylist("c", PlaylistDraft("Mix")),
            UpdateMetadata("m", PlaylistDraft("Mix")),
            AddTracks("add", ("A", "B")),
            RemoveTracks("rm", (Removal("X", 0),)),
            Reorder("ord", ("A", "B", "R", "S")),
        ),
        not_made=(
            ChangeNotMade("add", "N", "not available"),
            ChangeNotMade("reorder", None, "can't reorder"),
        ),
        expected_after=("A", "B", "R", "S"),
        synced=(SyncedEntry(TrackKey(SPOTIFY, "s"), "S"),),
        draft=PlaylistDraft("Mix"),
    )
    dst1 = MemberPlan(
        member_id="dst1",
        operations=(AddTracks("add1", ("a2",)),),
        not_made=(),
        expected_after=("a2",),
        synced=(),
        draft=PlaylistDraft("Mix"),
    )
    unresolved = UnresolvedEntry(
        member_id="dst0",
        source=track("spotify", "u"),
        position=3,
        decision=NeedsAttention(
            ("unsure",), (ScoredCandidate(track("apple_music", "C1"), 0.5, ()),)
        ),
    )
    plan = SyncPlan("s1", (dst0, dst1), (unresolved,))

    keys = referenced_keys(plan, {"src": SPOTIFY, "dst0": APPLE, "dst1": SPOTIFY})

    assert keys == {
        TrackKey(APPLE, "A"),
        TrackKey(APPLE, "B"),
        TrackKey(APPLE, "X"),
        TrackKey(APPLE, "N"),
        TrackKey(APPLE, "S"),
        TrackKey(SPOTIFY, "s"),
        TrackKey(SPOTIFY, "a2"),
        TrackKey(SPOTIFY, "u"),
        TrackKey(APPLE, "C1"),
    }
    assert TrackKey(APPLE, "R") not in keys
