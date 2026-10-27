from collections.abc import Callable
from dataclasses import dataclass, field, replace

import pytest

from linersync.adapters.fake.connector import FakeConnector
from linersync.adapters.memory.backups import InMemoryBackups, StaticCovers
from linersync.adapters.memory.repositories import in_memory_repositories
from linersync.adapters.memory.runtime import FixedClock, SequentialIds
from linersync.application.backups import BACKUP_STOPPED, BackupService
from linersync.application.settings import DEFAULT_VERSIONS_KEPT
from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    BackupStatus,
    CoverImage,
)
from linersync.domain.capabilities import Capability
from linersync.domain.playlists import PlaylistMeta, PlaylistSnapshot
from linersync.domain.sync import SyncMode, SyncStatus
from linersync.domain.tracks import ServiceId
from linersync.ports.repositories import Repositories
from tests.factories import NOW, group, snapshot

SPOTIFY = ServiceId("spotify")
C1 = "https://i.example/c1"
C2 = "https://i.example/c2"
IMG1 = CoverImage("sha-c1", "image/jpeg", b"one")
IMG2 = CoverImage("sha-c2", "image/jpeg", b"two")


def with_meta(snap: PlaylistSnapshot, **changes: object) -> PlaylistSnapshot:
    return replace(snap, meta=replace(snap.meta, **changes))  # type: ignore[arg-type]


@dataclass
class Env:
    spotify: FakeConnector = field(
        default_factory=lambda: FakeConnector(SPOTIFY, "Spotify", frozenset(Capability))
    )
    store: InMemoryBackups = field(default_factory=InMemoryBackups)
    repos: Repositories = field(default_factory=in_memory_repositories)
    covers: StaticCovers = field(
        default_factory=lambda: StaticCovers({C1: IMG1, C2: IMG2})
    )
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    ids: SequentialIds = field(default_factory=lambda: SequentialIds("id"))
    versions_kept: Callable[[], int] = lambda: DEFAULT_VERSIONS_KEPT

    def __post_init__(self) -> None:
        self.spotify.add_playlist(snapshot("spotify", "P1", ["a", "b", "c"]))
        self.spotify.add_playlist(snapshot("spotify", "P2", ["d"]))

    def service(self) -> BackupService:
        return BackupService(
            self.store,
            self.repos.syncs,
            self.covers,
            self.clock,
            self.ids,
            versions_kept=self.versions_kept,
        )

    def entry(self, playlist_id: str) -> BackedUpPlaylist:
        found = self.store.find_entry(SPOTIFY, playlist_id)
        assert found is not None
        return found


@pytest.fixture
def env() -> Env:
    return Env()


async def test_first_backup_saves_every_readable_playlist(env: Env) -> None:
    run = await env.service().back_up(env.spotify)
    assert run.status is BackupStatus.SUCCEEDED
    assert (run.playlists, run.saved) == (2, 2)
    assert env.store.list_runs() == [run]
    for pid, ids in (("P1", ("a", "b", "c")), ("P2", ("d",))):
        entry = env.entry(pid)
        version = env.store.get_version(entry.latest_version_id)
        assert version is not None
        assert version.snapshot.track_ids == ids
        assert entry.track_count == len(ids)
        assert entry.versions == 1
        assert entry.first_backed_up_at == NOW and entry.last_seen_at == NOW


async def test_unchanged_playlist_not_read_again_with_token(env: Env) -> None:
    env.spotify.add_playlist(
        with_meta(snapshot("spotify", "P1", ["a", "b", "c"]), version_token="v1")
    )
    svc = env.service()
    await svc.back_up(env.spotify)
    env.spotify.calls.clear()
    run = await svc.back_up(env.spotify)
    assert "read:P1" not in env.spotify.calls
    assert "read:P2" in env.spotify.calls
    assert run.unchanged == 2 and run.saved == 0
    assert env.entry("P1").versions == 1


async def test_changed_playlist_adds_version_and_prunes() -> None:
    env = Env(versions_kept=lambda: 2)
    svc = env.service()
    await svc.back_up(env.spotify)
    for ids in (["a"], ["a", "b"], ["x", "y", "z"]):
        env.spotify.add_playlist(snapshot("spotify", "P1", ids))
        run = await svc.back_up(env.spotify)
        assert run.saved == 1
    entry = env.entry("P1")
    assert entry.versions == 2
    versions = env.store.list_versions(entry.entry_id)
    assert len(versions) == 2
    assert versions[0].version_id == entry.latest_version_id
    assert versions[0].snapshot.track_ids == ("x", "y", "z")
    assert versions[1].snapshot.track_ids == ("a", "b")
    assert entry.track_count == 3


async def test_unreadable_playlist_counted_not_saved(env: Env) -> None:
    env.spotify.add_playlist(
        with_meta(snapshot("spotify", "P3", ["e"]), readable=False)
    )
    run = await env.service().back_up(env.spotify)
    assert run.unreadable == 1
    assert run.playlists == 2
    assert env.store.find_entry(SPOTIFY, "P3") is None
    assert "read:P3" not in env.spotify.calls


async def test_cover_fetched_once_per_url(env: Env) -> None:
    svc = env.service()
    env.spotify.add_playlist(with_meta(snapshot("spotify", "P1", ["a"]), cover_url=C1))
    await svc.back_up(env.spotify)
    for ids in (["a", "b"], ["a", "b", "c"]):
        env.spotify.add_playlist(
            with_meta(snapshot("spotify", "P1", ids), cover_url=C1)
        )
        await svc.back_up(env.spotify)
    assert env.covers.calls == [C1]
    entry = env.entry("P1")
    versions = env.store.list_versions(entry.entry_id)
    assert len(versions) == 3
    assert {v.cover_sha256 for v in versions} == {"sha-c1"}
    assert entry.cover_sha256 == "sha-c1"
    assert env.store.get_cover("sha-c1") == IMG1

    env.spotify.add_playlist(with_meta(snapshot("spotify", "P1", ["z"]), cover_url=C2))
    await svc.back_up(env.spotify)
    assert env.covers.calls == [C1, C2]
    assert env.entry("P1").cover_sha256 == "sha-c2"


async def test_cover_fetch_failure_means_no_cover(env: Env) -> None:
    env.spotify.add_playlist(
        with_meta(snapshot("spotify", "P1", ["a"]), cover_url="https://i.example/x")
    )
    run = await env.service().back_up(env.spotify)
    assert run.status is BackupStatus.SUCCEEDED
    entry = env.entry("P1")
    assert entry.cover_sha256 is None
    version = env.store.get_version(entry.latest_version_id)
    assert version is not None and version.cover_sha256 is None


async def test_gone_playlist_archived(env: Env) -> None:
    svc = env.service()
    await svc.back_up(env.spotify)
    env.spotify.delete_playlist("P2")
    run = await svc.back_up(env.spotify)
    entry = env.entry("P2")
    assert entry.archived_at == NOW
    assert entry.missing_since == NOW
    assert run.archived == 1
    assert len(env.store.list_versions(entry.entry_id)) == 1
    assert env.entry("P1").archived_at is None


async def test_gone_destination_of_active_sync_not_archived(env: Env) -> None:
    g = group(
        source=("apple_music", "A1"),
        destinations=[("spotify", "P2", SyncMode.FULL)],
    )
    env.repos.syncs.save(g)
    svc = env.service()
    await svc.back_up(env.spotify)
    env.spotify.delete_playlist("P2")
    run = await svc.back_up(env.spotify)
    entry = env.entry("P2")
    assert entry.missing_since == NOW
    assert entry.archived_at is None
    assert run.archived == 0

    env.repos.syncs.save(replace(g, status=SyncStatus.PAUSED))
    run = await svc.back_up(env.spotify)
    assert env.entry("P2").archived_at == NOW
    assert run.archived == 1


async def test_gone_source_of_active_sync_archived(env: Env) -> None:
    env.repos.syncs.save(
        group(
            source=("spotify", "P2"),
            destinations=[("apple_music", "A1", SyncMode.FULL)],
        )
    )
    svc = env.service()
    await svc.back_up(env.spotify)
    env.spotify.delete_playlist("P2")
    run = await svc.back_up(env.spotify)
    assert env.entry("P2").archived_at == NOW
    assert run.archived == 1


async def test_reappearing_playlist_leaves_archive(env: Env) -> None:
    svc = env.service()
    await svc.back_up(env.spotify)
    env.spotify.delete_playlist("P2")
    await svc.back_up(env.spotify)
    assert env.entry("P2").archived
    env.spotify.add_playlist(snapshot("spotify", "P2", ["d", "e"]))
    await svc.back_up(env.spotify)
    entry = env.entry("P2")
    assert entry.archived_at is None and entry.missing_since is None
    versions = env.store.list_versions(entry.entry_id)
    assert [v.snapshot.track_ids for v in versions] == [("d", "e"), ("d",)]


async def test_failed_listing_changes_nothing(env: Env) -> None:
    svc = env.service()
    await svc.back_up(env.spotify)
    env.spotify.delete_playlist("P2")
    env.spotify.fail_list()
    before = env.store.list_entries()
    run = await svc.back_up(env.spotify)
    assert run.status is BackupStatus.FAILED
    assert run.error is not None
    assert run.error.startswith("Couldn't list the playlists: ")
    assert run.archived == 0
    assert env.store.list_entries() == before
    assert env.store.list_runs()[0] == run


async def test_failed_read_keeps_entry_and_marks_partial(env: Env) -> None:
    svc = env.service()
    await svc.back_up(env.spotify)
    old_latest = env.entry("P1").latest_version_id
    env.spotify.add_playlist(snapshot("spotify", "P1", []))
    env.spotify.add_playlist(snapshot("spotify", "P2", ["d", "e"]))
    env.spotify.fail_reads("P1")
    run = await svc.back_up(env.spotify)
    assert run.status is BackupStatus.PARTIAL
    assert run.errors[0].startswith("Mix: couldn't read it")
    p1 = env.entry("P1")
    assert p1.latest_version_id == old_latest
    assert p1.archived_at is None and p1.missing_since is None
    assert run.saved == 1
    p2 = env.entry("P2")
    version = env.store.get_version(p2.latest_version_id)
    assert version is not None and version.snapshot.track_ids == ("d", "e")


async def test_partial_read_is_not_saved(env: Env) -> None:
    env.spotify.truncate_reads("P1")
    run = await env.service().back_up(env.spotify)
    assert env.store.find_entry(SPOTIFY, "P1") is None
    assert run.status is BackupStatus.PARTIAL
    assert "Mix: only 3 of 4 songs could be read" in run.errors
    assert run.saved == 1


async def test_partial_read_keeps_existing_entry_present(env: Env) -> None:
    svc = env.service()
    await svc.back_up(env.spotify)
    old_latest = env.entry("P1").latest_version_id
    env.spotify.add_playlist(snapshot("spotify", "P1", ["a"]))
    env.spotify.truncate_reads("P1")
    run = await svc.back_up(env.spotify)
    assert run.status is BackupStatus.PARTIAL
    p1 = env.entry("P1")
    assert p1.latest_version_id == old_latest and p1.versions == 1
    assert p1.archived_at is None and p1.missing_since is None


async def test_auth_failure_stops_service(env: Env) -> None:
    svc = env.service()
    await svc.back_up(env.spotify)
    env.spotify.delete_playlist("P2")
    env.spotify.fail_reads("P1", auth_required=True)
    run = await svc.back_up(env.spotify)
    assert run.status is BackupStatus.FAILED
    assert run.error == "Sign in to Spotify again"
    p2 = env.entry("P2")
    assert p2.archived_at is None and p2.missing_since is None


async def test_auth_failure_listing(env: Env) -> None:
    env.spotify.fail_list(auth_required=True)
    run = await env.service().back_up(env.spotify)
    assert run.status is BackupStatus.FAILED
    assert run.error == "Sign in to Spotify again"


async def test_gone_on_read_is_archived(env: Env) -> None:
    svc = env.service()
    await svc.back_up(env.spotify)

    real_read = env.spotify.read_playlist

    async def read(playlist_id: str) -> PlaylistSnapshot:
        if playlist_id == "P2":
            env.spotify.delete_playlist("P2")
        return await real_read(playlist_id)

    env.spotify.read_playlist = read  # type: ignore[method-assign]
    env.spotify.add_playlist(snapshot("spotify", "P2", ["d", "e"]))
    run = await svc.back_up(env.spotify)
    assert run.status is BackupStatus.SUCCEEDED
    assert run.archived == 1
    assert env.entry("P2").archived_at == NOW


async def test_exception_saves_stopped_run_and_propagates(env: Env) -> None:
    def progress(done: int, total: int, message: str) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await env.service().back_up(env.spotify, progress)
    run = env.store.list_runs()[0]
    assert run.status is BackupStatus.FAILED
    assert run.error == BACKUP_STOPPED


async def test_progress_reports_each_readable_playlist(env: Env) -> None:
    seen: list[tuple[int, int, str]] = []
    await env.service().back_up(env.spotify, lambda d, t, m: seen.append((d, t, m)))
    assert seen == [(0, 2, "Mix"), (1, 2, "Mix")]


async def test_duplicate_listing_saves_one_entry(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = env.spotify.list_playlists

    async def twice() -> list[PlaylistMeta]:
        listed = await real()
        return [*listed, listed[0]]

    monkeypatch.setattr(env.spotify, "list_playlists", twice)
    run = await env.service().back_up(env.spotify)
    assert run.status is BackupStatus.SUCCEEDED
    assert (run.playlists, run.saved) == (2, 2)
    assert len(env.store.list_entries(SPOTIFY)) == 2
    entry = env.entry("P1")
    assert entry.versions == 1
    assert len(env.store.list_versions(entry.entry_id)) == 1


async def test_on_saved_gets_the_run_also_when_stopped(env: Env) -> None:
    saved: list[BackupRun] = []
    run = await env.service().back_up(env.spotify, on_saved=saved.append)
    assert saved == [run]

    def progress(done: int, total: int, message: str) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await env.service().back_up(env.spotify, progress, on_saved=saved.append)
    assert len(saved) == 2
    assert saved[1].error == BACKUP_STOPPED
