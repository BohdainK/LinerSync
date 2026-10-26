from dataclasses import dataclass
from datetime import datetime

from linersync.domain.tracks import ServiceId, Track


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    track: Track
    added_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlaylistMeta:
    service: ServiceId
    playlist_id: str
    name: str
    description: str = ""
    cover_url: str | None = None
    version_token: str | None = None
    owned: bool = True
    readable: bool = True
    unreadable_reason: str | None = None
    track_count: int | None = None


@dataclass(frozen=True, slots=True)
class PlaylistSnapshot:
    meta: PlaylistMeta
    entries: tuple[PlaylistEntry, ...]
    expected_total: int | None
    taken_at: datetime

    @property
    def track_ids(self) -> tuple[str, ...]:
        return tuple(e.track.track_id for e in self.entries)

    @property
    def is_complete(self) -> bool:
        return self.expected_total is not None and self.expected_total == len(
            self.entries
        )


@dataclass(frozen=True, slots=True)
class PlaylistDraft:
    name: str
    description: str = ""
    cover_url: str | None = None
