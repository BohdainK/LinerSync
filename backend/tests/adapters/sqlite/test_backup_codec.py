import json
from datetime import UTC, datetime
from pathlib import Path

from linersync.adapters.sqlite.codec import dumps, loads
from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    BackupStatus,
    PlaylistVersion,
    content_hash,
)
from linersync.domain.playlists import PlaylistEntry, PlaylistMeta, PlaylistSnapshot
from linersync.domain.tracks import ServiceId
from tests.factories import NOW, track

GOLDEN = Path(__file__).parent.parent.parent / "fixtures/storage/backup_v1.json"

SPOTIFY = ServiceId("spotify")


def _entry() -> BackedUpPlaylist:
    return BackedUpPlaylist(
        entry_id="e1",
        service=SPOTIFY,
        playlist_id="P1",
        name="Mix",
        latest_version_id="v1",
        track_count=3,
        versions=2,
        first_backed_up_at=NOW,
        last_seen_at=NOW,
        cover_sha256="abc123",
        missing_since=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        archived_at=datetime(2026, 1, 3, 3, 4, 5, tzinfo=UTC),
    )


def _version() -> PlaylistVersion:
    t1 = track("spotify", "T1")
    t2 = track("spotify", "T2")
    snap = PlaylistSnapshot(
        meta=PlaylistMeta(
            service=SPOTIFY,
            playlist_id="P1",
            name="Mix",
            description="d",
            cover_url="https://img.example/c.jpg",
            version_token="tok",
            track_count=3,
        ),
        entries=(
            PlaylistEntry(t1, datetime(2025, 5, 6, 7, 8, 9, tzinfo=UTC)),
            PlaylistEntry(t2),
            PlaylistEntry(t1, datetime(2025, 6, 6, 7, 8, 9, tzinfo=UTC)),
        ),
        expected_total=3,
        taken_at=NOW,
    )
    return PlaylistVersion(
        version_id="v1",
        entry_id="e1",
        backup_id="b1",
        snapshot=snap,
        content_hash=content_hash(snap),
        cover_sha256="abc123",
    )


def _run() -> BackupRun:
    return BackupRun(
        backup_id="b1",
        service=SPOTIFY,
        started_at=NOW,
        finished_at=NOW,
        status=BackupStatus.PARTIAL,
        playlists=3,
        saved=1,
        unchanged=1,
        archived=0,
        unreadable=1,
        errors=("Mix: could not be read",),
        error=None,
    )


def test_backup_models_roundtrip() -> None:
    assert loads(BackedUpPlaylist, dumps(BackedUpPlaylist, _entry())) == _entry()
    assert loads(PlaylistVersion, dumps(PlaylistVersion, _version())) == _version()
    assert loads(BackupRun, dumps(BackupRun, _run())) == _run()


def test_golden_backup_v1() -> None:
    doc = json.loads(GOLDEN.read_text())
    assert loads(BackedUpPlaylist, json.dumps(doc["entry"])) == _entry()
    assert loads(PlaylistVersion, json.dumps(doc["version"])) == _version()
    assert loads(BackupRun, json.dumps(doc["run"])) == _run()
