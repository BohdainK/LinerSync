from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest

from linersync.adapters.memory.backups import InMemoryBackups
from linersync.adapters.sqlite.backups import SqliteBackups
from linersync.adapters.sqlite.db import connect, migrate
from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    BackupStatus,
    CoverImage,
    PlaylistVersion,
    content_hash,
)
from linersync.domain.tracks import ServiceId
from linersync.ports.backups import BackupRepository
from tests.factories import NOW, snapshot

Factory = Callable[[], BackupRepository]


def sqlite_memory_backups() -> BackupRepository:
    engine = connect(":memory:")
    migrate(engine)
    return SqliteBackups(engine)


pytestmark = pytest.mark.parametrize("make", [InMemoryBackups, sqlite_memory_backups])

SPOTIFY = ServiceId("spotify")
APPLE = ServiceId("apple_music")


def _entry(
    entry_id: str,
    *,
    service: ServiceId = SPOTIFY,
    playlist_id: str = "P1",
    name: str = "Mix",
    archived: bool = False,
) -> BackedUpPlaylist:
    return BackedUpPlaylist(
        entry_id=entry_id,
        service=service,
        playlist_id=playlist_id,
        name=name,
        latest_version_id="v1",
        track_count=2,
        versions=1,
        first_backed_up_at=NOW,
        last_seen_at=NOW,
        archived_at=NOW if archived else None,
    )


def _version(
    version_id: str, entry_id: str = "e1", cover: str | None = None
) -> PlaylistVersion:
    snap = snapshot("spotify", "P1", ["T1", "T2"])
    return PlaylistVersion(
        version_id=version_id,
        entry_id=entry_id,
        backup_id="b1",
        snapshot=snap,
        content_hash=content_hash(snap),
        cover_sha256=cover,
    )


def _run(backup_id: str, started_days: int, service: ServiceId = SPOTIFY) -> BackupRun:
    at = NOW + timedelta(days=started_days)
    return BackupRun(
        backup_id=backup_id,
        service=service,
        started_at=at,
        finished_at=at,
        status=BackupStatus.SUCCEEDED,
    )


def test_entry_roundtrip_find_and_replace(make: Factory) -> None:
    repo = make()
    e = _entry("e1")
    assert repo.get_entry("e1") is None
    assert repo.find_entry(SPOTIFY, "P1") is None
    repo.save_entry(e)
    assert repo.get_entry("e1") == e
    assert repo.find_entry(SPOTIFY, "P1") == e
    assert repo.find_entry(APPLE, "P1") is None
    assert not e.archived
    e2 = replace(e, name="Renamed", archived_at=NOW)
    assert e2.archived
    repo.save_entry(e2)
    assert repo.list_entries() == [e2]
    assert repo.find_entry(SPOTIFY, "P1") == e2


def test_second_entry_for_same_playlist_rejected(make: Factory) -> None:
    repo = make()
    repo.save_entry(_entry("e1"))
    with pytest.raises(ValueError):
        repo.save_entry(_entry("e2"))
    repo.save_entry(_entry("e3", service=APPLE))
    assert len(repo.list_entries()) == 2


def test_list_entries_filters_and_sorts(make: Factory) -> None:
    repo = make()
    repo.save_entry(_entry("1", playlist_id="p1", name="b"))
    repo.save_entry(_entry("2", playlist_id="p2", name="B", archived=True))
    repo.save_entry(_entry("3", playlist_id="p3", name="a"))
    repo.save_entry(_entry("4", service=APPLE, playlist_id="p4", name="c"))

    def ids(
        service: ServiceId | None = None, archived: bool | None = None
    ) -> list[str]:
        return [e.entry_id for e in repo.list_entries(service, archived=archived)]

    assert ids() == ["3", "1", "2", "4"]
    assert ids(service=SPOTIFY) == ["3", "1", "2"]
    assert ids(archived=True) == ["2"]
    assert ids(archived=False) == ["3", "1", "4"]
    assert ids(service=SPOTIFY, archived=False) == ["3", "1"]


def test_versions_newest_first_and_delete(make: Factory) -> None:
    repo = make()
    repo.save_entry(_entry("e1"))
    for v in ("v1", "v2", "v3"):
        repo.add_version(_version(v))
    assert [v.version_id for v in repo.list_versions("e1")] == ["v3", "v2", "v1"]
    assert repo.get_version("v2") == _version("v2")
    repo.delete_version("v2")
    repo.delete_version("v2")
    assert repo.get_version("v2") is None
    assert [v.version_id for v in repo.list_versions("e1")] == ["v3", "v1"]
    assert repo.list_versions("nope") == []


def test_delete_entry_removes_its_versions(make: Factory) -> None:
    repo = make()
    repo.save_entry(_entry("e1"))
    repo.save_entry(_entry("e2", playlist_id="P2"))
    repo.add_version(_version("v1", "e1"))
    repo.add_version(_version("v2", "e2"))
    repo.delete_entry("e1")
    repo.delete_entry("e1")
    assert repo.get_entry("e1") is None
    assert repo.get_version("v1") is None
    assert repo.get_version("v2") is not None


def test_runs_newest_first_filtered_and_limited(make: Factory) -> None:
    repo = make()
    repo.save_run(_run("r1", 1))
    repo.save_run(_run("r2", 3))
    repo.save_run(_run("r3", 3))
    repo.save_run(_run("r4", 2, APPLE))
    assert [r.backup_id for r in repo.list_runs()] == ["r3", "r2", "r4", "r1"]
    assert [r.backup_id for r in repo.list_runs(SPOTIFY)] == ["r3", "r2", "r1"]
    assert [r.backup_id for r in repo.list_runs(limit=2)] == ["r3", "r2"]
    replaced = replace(_run("r1", 1), status=BackupStatus.FAILED, error="x")
    repo.save_run(replaced)
    assert repo.get_run("r1") == replaced
    assert repo.get_run("zz") is None


def test_delete_service_leaves_other_services(make: Factory) -> None:
    repo = make()
    repo.save_entry(_entry("e1"))
    repo.save_entry(_entry("e2", service=APPLE))
    repo.add_version(_version("v1", "e1"))
    repo.add_version(_version("v2", "e2"))
    repo.save_run(_run("r1", 1))
    repo.save_run(_run("r2", 1, APPLE))
    repo.delete_service(SPOTIFY)
    assert [e.entry_id for e in repo.list_entries()] == ["e2"]
    assert repo.get_version("v1") is None
    assert repo.get_version("v2") is not None
    assert [r.backup_id for r in repo.list_runs()] == ["r2"]


def test_prune_covers_keeps_referenced(make: Factory) -> None:
    repo = make()
    x = CoverImage("x", "image/jpeg", b"xx")
    y = CoverImage("y", "image/png", b"yy")
    repo.put_cover(x)
    repo.put_cover(x)
    repo.put_cover(y)
    repo.save_entry(_entry("e1"))
    repo.add_version(_version("v1", cover="x"))
    assert repo.prune_covers() == 1
    assert repo.get_cover("x") == x
    assert repo.get_cover("y") is None
    assert repo.prune_covers() == 0
