from dataclasses import dataclass, replace

import pytest

from linersync.adapters.memory.backups import InMemoryBackups, StaticCovers
from linersync.application.backup_source import BACKUP_DISPLAY_NAME, BackupConnector
from linersync.application.backups import BackupService
from linersync.application.runner import SyncRunner
from linersync.application.transfer import TransferService
from linersync.domain.backups import BACKUP_SERVICE, BackedUpPlaylist
from linersync.domain.capabilities import Capability
from linersync.domain.operations import AddTracks
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.runs import RunStatus, SyncRun
from linersync.domain.tracks import ServiceId, TrackQuery
from linersync.ports.connector import Connector
from linersync.ports.errors import PlaylistNotFound, WriteFailed
from tests.application.conftest import APPLE, SPOTIFY, World
from tests.factories import snapshot


@dataclass
class Setup:
    world: World
    store: InMemoryBackups
    backups: BackupService
    connector: BackupConnector

    def entry(self, playlist_id: str) -> BackedUpPlaylist:
        found = self.store.find_entry(SPOTIFY, playlist_id)
        assert found is not None
        return found

    async def transfer(self, playlist_id: str, destination: ServiceId) -> SyncRun:
        w = self.world
        connectors: dict[ServiceId, Connector] = {
            SPOTIFY: w.spotify,
            APPLE: w.apple,
            BACKUP_SERVICE: self.connector,
        }
        deps = replace(w.deps, connectors=connectors)
        runner = SyncRunner(deps)
        return await TransferService(deps, runner).transfer(
            BACKUP_SERVICE, playlist_id, destination
        )


async def _setup(world: World) -> Setup:
    store = InMemoryBackups()
    backups = BackupService(
        store, world.repos.syncs, StaticCovers(), world.clock, world.ids
    )
    await backups.back_up(world.spotify)
    return Setup(world, store, backups, BackupConnector(store))


@pytest.fixture
async def s(world: World) -> Setup:
    return await _setup(world)


def test_connector_attributes() -> None:
    c = BackupConnector(InMemoryBackups())
    assert c.service == BACKUP_SERVICE
    assert c.display_name == BACKUP_DISPLAY_NAME == "LinerSync backup"
    assert c.capabilities == frozenset({Capability.READ_ORDER})
    assert (c.limits.page_size, c.limits.add_batch_size, c.limits.search_cost) == (
        10_000,
        1,
        0,
    )


async def test_read_by_version_and_by_entry(s: Setup) -> None:
    entry = s.entry("P1")
    for pid in (entry.latest_version_id, entry.entry_id):
        snap = await s.connector.read_playlist(pid)
        assert snap.track_ids == ("a", "b", "c")
        assert snap.meta.service == "backup"
        assert snap.meta.playlist_id == pid
        assert snap.meta.version_token == entry.latest_version_id
        assert snap.expected_total == 3
        assert {e.track.service for e in snap.entries} == {SPOTIFY}
    with pytest.raises(PlaylistNotFound) as err:
        await s.connector.read_playlist("nope")
    assert err.value.service == BACKUP_SERVICE


async def test_lists_archived_entries_only(world: World) -> None:
    world.spotify.add_playlist(snapshot("spotify", "P2", ["d"], name="Gone"))
    s = await _setup(world)
    assert await s.connector.list_playlists() == []
    world.spotify.drop_playlist("P2")
    await s.backups.back_up(world.spotify)

    gone = s.entry("P2")
    assert gone.archived and not s.entry("P1").archived
    [meta] = await s.connector.list_playlists()
    assert meta.service == BACKUP_SERVICE
    assert meta.playlist_id == gone.entry_id
    assert meta.name == "Gone"
    assert meta.version_token == gone.latest_version_id
    assert meta.track_count == 1


async def test_writes_rejected(s: Setup) -> None:
    with pytest.raises(WriteFailed) as err:
        await s.connector.create_playlist(PlaylistDraft("x"))
    assert err.value.service == BACKUP_SERVICE
    assert err.value.message == "The backup is read-only"
    with pytest.raises(WriteFailed):
        await s.connector.apply(s.entry("P1").entry_id, AddTracks("op1", ("a",)))
    assert await s.connector.search(TrackQuery("a", ())) == []


async def test_restore_preserves_order_and_repeats(world: World) -> None:
    world.spotify.add_playlist(
        snapshot("spotify", "P9", ["a", "b", "a", "c"], name="Repeats")
    )
    s = await _setup(world)
    version_id = s.entry("P9").latest_version_id
    world.spotify.drop_playlist("P9")
    world.spotify.calls.clear()

    run = await s.transfer(version_id, SPOTIFY)

    assert run.status is RunStatus.SUCCEEDED
    [created] = [m.created_playlist_id for m in run.members if m.member_id == "dst0"]
    assert created is not None
    restored = world.spotify.playlists[created]
    assert restored.track_ids == ("a", "b", "a", "c")
    assert restored.meta.name == "Repeats"
    assert not any(c.startswith("search:") for c in world.spotify.calls)


async def test_restore_flags_songs_that_could_not_be_added(world: World) -> None:
    s = await _setup(world)
    world.spotify.drop_adds(["b"])

    run = await s.transfer(s.entry("P1").latest_version_id, SPOTIFY)

    [dst] = [m for m in run.members if m.member_id == "dst0"]
    assert dst.mismatch is not None
    assert "b" in dst.mismatch.expected
    assert run.status is not RunStatus.SUCCEEDED


async def test_archived_playlist_as_source_to_other_service(world: World) -> None:
    s = await _setup(world)
    world.spotify.drop_playlist("P1")
    await s.backups.back_up(world.spotify)
    entry = s.entry("P1")
    assert entry.archived

    run = await s.transfer(entry.entry_id, APPLE)

    assert run.status is RunStatus.SUCCEEDED
    assert "search:a" in world.apple.calls
    [created] = [m.created_playlist_id for m in run.members if m.member_id == "dst0"]
    assert created is not None
    assert world.apple.playlists[created].track_ids == ("A", "B", "C")
