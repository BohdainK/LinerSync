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
    # Why a short read is short, and what the user can do about it.
    missing_reason: str | None = None

    @property
    def track_ids(self) -> tuple[str, ...]:
        return tuple(e.track.track_id for e in self.entries)

    @property
    def is_complete(self) -> bool:
        return self.expected_total is not None and self.expected_total == len(
            self.entries
        )

    def shortfall(self) -> str:
        """How much of the playlist was read, as "(3 of 4 songs; reason)"."""
        reason = f"; {self.missing_reason}" if self.missing_reason else ""
        return f"({len(self.entries)} of {self.expected_total} songs{reason})"


@dataclass(frozen=True, slots=True)
class PlaylistDraft:
    name: str
    description: str = ""
    cover_url: str | None = None
