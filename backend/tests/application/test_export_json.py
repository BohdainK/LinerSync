import base64
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from linersync.adapters.memory.backups import InMemoryBackups
from linersync.adapters.memory.credentials import InMemoryCredentials
from linersync.adapters.memory.jobs import InMemoryRunContexts
from linersync.adapters.memory.repositories import in_memory_repositories
from linersync.adapters.memory.runtime import FixedClock
from linersync.adapters.sqlite.codec import decode, dumps
from linersync.application.export_json import (
    EXPORT_FORMAT,
    EXPORT_VERSION,
    EXPORTED_WHILE_RUNNING,
    IMPORTED_PAUSE_REASON,
    NEWER_EXPORT,
    NOT_AN_EXPORT,
    ExportDocument,
    ExportService,
    RestoreReport,
    check_covers,
    check_header,
)
from linersync.application.settings import AppSettings
from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    BackupStatus,
    CoverImage,
    PlaylistVersion,
    content_hash,
)
from linersync.domain.jobs import RunContext, RunMemberInfo
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.runs import RunKind, RunStatus, SyncRun, Trigger
from linersync.domain.sync import (
    MemberRole,
    MemberState,
    SyncedEntry,
    SyncStatus,
)
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.repositories import Repositories
from tests.factories import NOW, group, snapshot, track

GOLDEN = Path(__file__).parent.parent / "fixtures/export/export_v1.json"
SPOTIFY = ServiceId("spotify")
APPLE = ServiceId("apple_music")
COVER = CoverImage("c0ffee", "image/jpeg", b"\xff\xd8jpeg-bytes")
SETTINGS = AppSettings(default_schedule="0 * * * *", backup_versions_kept=7)
SECRET = "sk-very-secret-token-123"


class MemorySettings:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self.value = settings or AppSettings()

    def get(self) -> AppSettings:
        return self.value

    def save(self, s: AppSettings) -> None:
        self.value = s


@dataclass
class Stores:
    repos: Repositories = field(default_factory=in_memory_repositories)
    contexts: InMemoryRunContexts = field(default_factory=InMemoryRunContexts)
    backups: InMemoryBackups = field(default_factory=InMemoryBackups)
    settings: MemorySettings = field(default_factory=MemorySettings)

    def service(self) -> ExportService:
        return ExportService(
            self.repos,
            self.contexts,
            self.backups,
            self.settings,
            FixedClock(NOW + timedelta(days=1)),
        )

    def state(self) -> tuple[object, ...]:
        return (
            sorted(self.repos.syncs.list(), key=lambda g: g.sync_id),
            self.repos.runs.list_all(),
            [self.contexts.get(r.run_id) for r in self.repos.runs.list_all()],
            self.repos.matches.list_all(),
            self.backups.list_entries(),
            [
                self.backups.list_versions(e.entry_id)
                for e in self.backups.list_entries()
            ],
            self.backups.list_runs(),
            self.settings.get(),
        )


def _run(run_id: str, status: RunStatus = RunStatus.SUCCEEDED) -> SyncRun:
    return SyncRun(
        run_id=run_id,
        sync_id="s1",
        kind=RunKind.SYNC,
        trigger=Trigger.MANUAL,
        dry_run=False,
        status=status,
        started_at=NOW,
        finished_at=None if status is RunStatus.RUNNING else NOW,
    )


def _context(run_id: str) -> RunContext:
    return RunContext(
        run_id,
        (RunMemberInfo("src", SPOTIFY, "P1", MemberRole.SOURCE),),
        (track("spotify", "a"),),
    )


def _version(
    version_id: str, entry_id: str, playlist_id: str, ids: list[str], cover: bool
) -> PlaylistVersion:
    snap = snapshot("spotify", playlist_id, ids, name=playlist_id)
    return PlaylistVersion(
        version_id,
        entry_id,
        "b1",
        snap,
        content_hash(snap),
        COVER.sha256 if cover else None,
    )


def _entry(entry_id: str, playlist_id: str, latest: str, n: int) -> BackedUpPlaylist:
    return BackedUpPlaylist(
        entry_id=entry_id,
        service=SPOTIFY,
        playlist_id=playlist_id,
        name=playlist_id,
        latest_version_id=latest,
        track_count=n,
        versions=2 if playlist_id == "P1" else 1,
        first_backed_up_at=NOW,
        last_seen_at=NOW,
        cover_sha256=COVER.sha256 if playlist_id == "P1" else None,
    )


def seeded(running: bool = False) -> Stores:
    s = Stores(settings=MemorySettings(SETTINGS))
    g = group()
    dst = replace(
        g.members[1],
        last_synced=MemberState(
            (SyncedEntry(TrackKey(SPOTIFY, "a"), "A"),),
            PlaylistDraft("Mix"),
            "tok",
            NOW,
        ),
    )
    s.repos.syncs.save(g.with_member(dst))
    for run_id in ("r1", "r2"):
        status = (
            RunStatus.RUNNING if running and run_id == "r2" else RunStatus.SUCCEEDED
        )
        s.repos.runs.save(_run(run_id, status))
        s.contexts.save(_context(run_id))
    s.repos.matches.remember(RememberedMatch(TrackKey(SPOTIFY, "b"), APPLE, "B"))
    s.backups.put_cover(COVER)
    s.backups.add_version(_version("v1", "e1", "P1", ["a"], cover=True))
    s.backups.add_version(_version("v2", "e1", "P1", ["a", "b"], cover=True))
    s.backups.add_version(_version("v3", "e2", "P2", [], cover=False))
    s.backups.save_entry(_entry("e1", "P1", "v2", 2))
    s.backups.save_entry(_entry("e2", "P2", "v3", 0))
    s.backups.save_run(
        BackupRun("b1", SPOTIFY, NOW, NOW, BackupStatus.SUCCEEDED, playlists=2)
    )
    return s


ALL_ONE = {
    "syncs": 1,
    "runs": 2,
    "run_contexts": 2,
    "remembered_matches": 1,
    "backup_playlists": 2,
    "backup_runs": 1,
}
ZERO = dict.fromkeys(ALL_ONE, 0)


def test_build_contains_everything_but_secrets() -> None:
    creds = InMemoryCredentials()
    creds.save(SPOTIFY, {"access_token": SECRET, "refresh_token": SECRET + "r"})
    doc = seeded().service().build("1.2.3")
    assert doc.format == EXPORT_FORMAT
    assert doc.version == EXPORT_VERSION
    assert doc.app_version == "1.2.3"
    assert doc.exported_at == NOW + timedelta(days=1)
    assert [g.sync_id for g in doc.syncs] == ["s1"]
    assert doc.syncs[0].members[1].last_synced is not None
    assert [r.run_id for r in doc.runs] == ["r1", "r2"]
    assert [c.run_id for c in doc.run_contexts] == ["r1", "r2"]
    assert len(doc.remembered_matches) == 1
    assert [e.entry_id for e in doc.backup_playlists] == ["e1", "e2"]
    assert sorted(v.version_id for v in doc.backup_versions) == ["v2", "v3"]
    assert [r.backup_id for r in doc.backup_runs] == ["b1"]
    assert len(doc.covers) == 1
    assert doc.covers[0].sha256 == COVER.sha256
    assert doc.covers[0].media_type == "image/jpeg"
    assert base64.b64decode(doc.covers[0].data_base64) == COVER.data
    assert doc.settings == SETTINGS
    text = dumps(ExportDocument, doc)
    for values in [creds.get(SPOTIFY)]:
        for value in values.values():
            assert value not in text


def test_restore_into_empty_install() -> None:
    doc = seeded().service().build("1.2.3")
    target = Stores()
    report = target.service().restore(doc)
    assert report == RestoreReport(added=ALL_ONE, skipped=ZERO, settings_applied=True)
    sync = target.repos.syncs.get("s1")
    assert sync is not None
    assert sync.status is SyncStatus.PAUSED
    assert sync.pause_reason == IMPORTED_PAUSE_REASON
    assert sync.members == doc.syncs[0].members
    entries = target.backups.list_entries()
    assert [e.versions for e in entries] == [1, 1]
    assert [v.version_id for v in target.backups.list_versions("e1")] == ["v2"]
    assert target.backups.get_cover(COVER.sha256) == COVER
    assert target.contexts.get("r1") == _context("r1")
    assert target.backups.get_run("b1") is not None
    assert target.settings.get() == SETTINGS


def test_import_twice_adds_nothing() -> None:
    doc = seeded().service().build("1.2.3")
    target = Stores()
    first = target.service().restore(doc)
    before = target.state()
    second = target.service().restore(doc)
    assert second.added == ZERO
    assert second.skipped == first.added
    assert target.state() == before


def test_local_data_wins() -> None:
    doc = seeded().service().build("1.2.3")
    local = Stores(settings=MemorySettings(AppSettings(backup_versions_kept=3)))
    local.repos.syncs.save(replace(group(), name="Local"))
    snap = snapshot("spotify", "P1", ["z"], name="Local P1")
    local.backups.add_version(
        PlaylistVersion("lv", "local-e", "lb", snap, content_hash(snap))
    )
    local.backups.save_entry(
        replace(_entry("local-e", "P1", "lv", 1), name="Local P1", cover_sha256=None)
    )
    local.repos.matches.remember(RememberedMatch(TrackKey(SPOTIFY, "b"), APPLE, "LB"))
    report = local.service().restore(doc)
    synced = local.repos.syncs.get("s1")
    assert synced is not None and synced.name == "Local"
    assert synced.status is SyncStatus.ACTIVE
    found = local.backups.find_entry(SPOTIFY, "P1")
    assert found is not None and found.entry_id == "local-e"
    assert local.backups.get_entry("e1") is None
    assert local.backups.get_version("v2") is None
    match = local.repos.matches.get(TrackKey(SPOTIFY, "b"), APPLE)
    assert match is not None and match.target_track_id == "LB"
    assert local.settings.get() == AppSettings(backup_versions_kept=3)
    assert report.settings_applied is False
    assert report.skipped["syncs"] == 1
    assert report.skipped["backup_playlists"] == 1
    assert report.added["backup_playlists"] == 1
    assert report.skipped["remembered_matches"] == 1


def test_running_run_imported_aborted() -> None:
    doc = seeded(running=True).service().build("1.2.3")
    doc = replace(doc, exported_at=NOW + timedelta(hours=1))
    target = Stores()
    target.service().restore(doc)
    run = target.repos.runs.get("r2")
    assert run is not None
    assert run.status is RunStatus.ABORTED
    assert run.error == EXPORTED_WHILE_RUNNING
    # Like recover(): ended when last known to be running, here the export.
    assert run.finished_at == NOW + timedelta(hours=1)
    other = target.repos.runs.get("r1")
    assert other is not None and other.status is RunStatus.SUCCEEDED


def test_check_header() -> None:
    bad: object
    for bad in (
        [],
        {"format": "x", "version": 1},
        {"format": EXPORT_FORMAT, "version": "1"},
    ):
        with pytest.raises(ValueError) as caught:
            check_header(bad)
        assert str(caught.value) == NOT_AN_EXPORT
    with pytest.raises(ValueError) as newer:
        check_header({"format": EXPORT_FORMAT, "version": 2})
    assert str(newer.value) == NEWER_EXPORT.format(version=2)
    assert "format 2" in str(newer.value)
    check_header({"format": EXPORT_FORMAT, "version": 1})


def test_golden_export_v1() -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    check_header(data)
    doc: ExportDocument = decode(ExportDocument, data)
    assert doc.exported_at == datetime(2026, 1, 2, tzinfo=UTC)
    target = Stores()
    report = target.service().restore(doc)
    assert report.added == ALL_ONE
    assert report.settings_applied is True
    assert target.backups.get_cover(COVER.sha256) == COVER


def test_golden_export_v1_decodes_strictly_but_its_cover_is_a_placeholder() -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    doc: ExportDocument = decode(ExportDocument, data, strict=True)
    assert doc == decode(ExportDocument, data)
    with pytest.raises(ValueError, match="doesn't match"):
        check_covers(doc)  # its sha "c0ffee" isn't the data's sha256
