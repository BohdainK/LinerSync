import asyncio

import pytest

from linersync.adapters.fake.connector import FakeConnector
from linersync.domain.capabilities import Capability
from linersync.domain.operations import (
    AddTracks,
    Removal,
    RemoveTracks,
    Reorder,
)
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import ServiceId
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed
from tests.factories import snapshot, track

SVC = ServiceId("fake")
ALL = frozenset(Capability)


def make(
    caps: frozenset[Capability] = ALL, ids: tuple[str, ...] = ("a", "b", "c")
) -> FakeConnector:
    fc = FakeConnector(SVC, "Fake", caps, catalog=[track("fake", "d")])
    fc.add_playlist(snapshot("fake", "A1", ids))
    return fc


def test_apply_add_reorder_remove() -> None:
    fc = make()

    async def go() -> None:
        await fc.apply("A1", AddTracks("o1", ("d",)))
        assert (await fc.read_playlist("A1")).track_ids == ("a", "b", "c", "d")
        await fc.apply("A1", Reorder("o2", ("d", "c", "b", "a")))
        assert (await fc.read_playlist("A1")).track_ids == ("d", "c", "b", "a")
        await fc.apply("A1", RemoveTracks("o3", (Removal("d", 0), Removal("b", 2))))
        snap = await fc.read_playlist("A1")
        assert snap.track_ids == ("c", "a")
        assert snap.expected_total == 2
        assert snap.meta.version_token == "v3"

    asyncio.run(go())
    assert fc.calls[0] == "apply:AddTracks:A1"
    assert "read:A1" in fc.calls


def test_remove_position_mismatch_asserts() -> None:
    fc = make()
    with pytest.raises(AssertionError):
        asyncio.run(fc.apply("A1", RemoveTracks("o", (Removal("b", 0),))))


def test_write_failure_applied_flag() -> None:
    fc = make()
    fc.fail_writes(AddTracks, times=1, applied=True)
    fc.fail_writes(Reorder, times=1, applied=False)

    async def go() -> None:
        with pytest.raises(WriteFailed) as ei:
            await fc.apply("A1", AddTracks("o1", ("d",)))
        assert ei.value.maybe_applied is True
        assert ei.value.service == SVC
        assert (await fc.read_playlist("A1")).track_ids == ("a", "b", "c", "d")
        with pytest.raises(WriteFailed) as ei2:
            await fc.apply("A1", Reorder("o2", ("d", "c", "b", "a")))
        assert ei2.value.maybe_applied is False
        assert (await fc.read_playlist("A1")).track_ids == ("a", "b", "c", "d")
        await fc.apply("A1", Reorder("o3", ("d", "c", "b", "a")))

    asyncio.run(go())


def test_truncated_read_is_incomplete() -> None:
    fc = make()
    fc.truncate_reads("A1")
    snap = asyncio.run(fc.read_playlist("A1"))
    assert snap.expected_total == 4 and not snap.is_complete


def test_uncapable_op_asserts() -> None:
    fc = make(caps=frozenset({Capability.READ_ORDER, Capability.ADD}))
    with pytest.raises(AssertionError):
        asyncio.run(fc.apply("A1", RemoveTracks("o", (Removal("a", 0),))))
    with pytest.raises(AssertionError):
        asyncio.run(fc.apply("A1", Reorder("o", ("c", "b", "a"))))


def test_failures_search_drop_create() -> None:
    fc = make()
    fc.fail_reads("A1", times=1)
    fc.fail_search(1)
    fc.drop_adds(["d"])

    async def go() -> None:
        with pytest.raises(ReadFailed):
            await fc.read_playlist("A1")
        assert (await fc.read_playlist("A1")).track_ids == ("a", "b", "c")
        from linersync.domain.tracks import TrackQuery

        with pytest.raises(ReadFailed):
            await fc.search(TrackQuery("d", ("x",)))
        assert [t.track_id for t in await fc.search(TrackQuery("D", ("x",)))] == ["d"]
        await fc.apply("A1", AddTracks("o", ("d",)))
        assert (await fc.read_playlist("A1")).track_ids == ("a", "b", "c")
        meta = await fc.create_playlist(PlaylistDraft("New"))
        assert meta.playlist_id == "fake-new-1"
        assert (await fc.read_playlist(meta.playlist_id)).entries == ()
        fc.delete_playlist("A1")
        with pytest.raises(PlaylistNotFound):
            await fc.read_playlist("A1")
        assert len(await fc.list_playlists()) == 1

    asyncio.run(go())
    assert "create:New" in fc.calls and "list" in fc.calls


def test_fail_list() -> None:
    fc = make()
    fc.fail_list(times=2)

    async def go() -> None:
        for _ in range(2):
            with pytest.raises(ReadFailed) as ei:
                await fc.list_playlists()
            assert ei.value.auth_required is False
        assert [m.playlist_id for m in await fc.list_playlists()] == ["A1"]

    asyncio.run(go())
    assert fc.calls == ["list", "list", "list"]


def test_injected_failures_can_require_auth() -> None:
    fc = make()
    fc.fail_list(auth_required=True)
    fc.fail_reads("A1", auth_required=True)

    async def go() -> None:
        with pytest.raises(ReadFailed) as e1:
            await fc.list_playlists()
        assert e1.value.auth_required is True
        with pytest.raises(ReadFailed) as e2:
            await fc.read_playlist("A1")
        assert e2.value.auth_required is True
        await fc.list_playlists()
        await fc.read_playlist("A1")

    asyncio.run(go())
