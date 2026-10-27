"""Backups: versioned copies of playlists, kept even after they vanish."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from linersync.domain.playlists import PlaylistSnapshot
from linersync.domain.tracks import ServiceId

BACKUP_SERVICE = ServiceId("backup")
# Raster types a browser renders inertly; covers of any other type are refused.
COVER_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})


class BackupStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BackedUpPlaylist:
    entry_id: str
    service: ServiceId
    playlist_id: str
    name: str
    latest_version_id: str
    track_count: int
    versions: int
    first_backed_up_at: datetime
    last_seen_at: datetime
    cover_sha256: str | None = None
    missing_since: datetime | None = None
    archived_at: datetime | None = None

    @property
    def archived(self) -> bool:
        return self.archived_at is not None


@dataclass(frozen=True, slots=True)
class PlaylistVersion:
    version_id: str
    entry_id: str
    backup_id: str
    snapshot: PlaylistSnapshot
    content_hash: str
    cover_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class CoverImage:
    sha256: str
    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class BackupRun:
    backup_id: str
    service: ServiceId
    started_at: datetime
    finished_at: datetime
    status: BackupStatus
    playlists: int = 0
    saved: int = 0
    unchanged: int = 0
    archived: int = 0
    unreadable: int = 0
    errors: tuple[str, ...] = ()
    error: str | None = None


def content_hash(snap: PlaylistSnapshot) -> str:
    """Hash of name, description and each entry's track key and added_at, in
    order. Cover, version token, track count and taken_at are left out."""
    payload = [
        snap.meta.name,
        snap.meta.description,
        [
            [
                e.track.service,
                e.track.track_id,
                e.added_at.isoformat() if e.added_at is not None else None,
            ]
            for e in snap.entries
        ],
    ]
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
