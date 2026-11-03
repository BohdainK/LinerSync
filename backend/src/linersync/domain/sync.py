from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from linersync.domain.capabilities import Capability
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.tracks import ServiceId, Track, TrackKey


class SyncMode(StrEnum):
    FULL = "full"
    ADD_ONLY = "add_only"


class Direction(StrEnum):
    ONE_WAY = "one_way"
    TWO_WAY = "two_way"


class MemberRole(StrEnum):
    SOURCE = "source"
    DESTINATION = "destination"
    PEER = "peer"


SOURCE_MEMBER_ID = "src"


def destination_member_id(n: int) -> str:
    return f"dst{n}"


class SyncStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class SyncedEntry:
    source_key: TrackKey
    target_track_id: str


@dataclass(frozen=True, slots=True)
class MemberState:
    entries: tuple[SyncedEntry, ...]
    draft: PlaylistDraft
    version_token: str | None
    synced_at: datetime
    # The destination's ids as verified after the write; () = legacy/unknown.
    target_order: tuple[str, ...] = ()
    pinned: tuple[str, ...] = ()
    removed_by_you: tuple[Track, ...] = ()  # source tracks
    added_by_you: tuple[Track, ...] = ()  # destination tracks
    owned: bool = True  # the account owns the playlist (sources only)


@dataclass(frozen=True, slots=True)
class PriorState:
    """A destination member's sync memory just before a run wrote to it, kept
    with the pre-write snapshot so an undo can restore it too."""

    last_synced: MemberState | None
    mirror_once: bool = False


@dataclass(frozen=True, slots=True)
class SyncMember:
    member_id: str
    service: ServiceId
    playlist_id: str | None
    role: MemberRole
    mode: SyncMode = SyncMode.FULL
    last_synced: MemberState | None = None
    # Full mode plans a strict mirror for the next run only.
    mirror_once: bool = False


@dataclass(frozen=True, slots=True)
class SyncGroup:
    sync_id: str
    name: str
    direction: Direction
    members: tuple[SyncMember, ...]
    schedule: str | None = None
    status: SyncStatus = SyncStatus.ACTIVE
    pause_reason: str | None = None

    def __post_init__(self) -> None:
        ids = [m.member_id for m in self.members]
        if len(set(ids)) != len(ids):
            raise ValueError("member ids must be unique")
        if self.direction is not Direction.ONE_WAY:
            return
        sources = [m for m in self.members if m.role is MemberRole.SOURCE]
        dests = [m for m in self.members if m.role is MemberRole.DESTINATION]
        if len(sources) != 1 or not dests:
            raise ValueError("one-way sync needs exactly one source and a destination")
        if any(m.role is MemberRole.PEER for m in self.members):
            raise ValueError("one-way sync cannot have peers")
        src = sources[0]
        if src.playlist_id is not None and any(
            d.service == src.service and d.playlist_id == src.playlist_id for d in dests
        ):
            raise ValueError("a destination cannot be the source playlist")

    @property
    def source(self) -> SyncMember:
        return next(m for m in self.members if m.role is MemberRole.SOURCE)

    @property
    def destinations(self) -> tuple[SyncMember, ...]:
        return tuple(m for m in self.members if m.role is MemberRole.DESTINATION)

    def with_member(self, m: SyncMember) -> "SyncGroup":
        return replace(
            self,
            members=tuple(m if x.member_id == m.member_id else x for x in self.members),
        )


def effective_mode(requested: SyncMode, caps: frozenset[Capability]) -> SyncMode:
    if Capability.REMOVE not in caps:
        return SyncMode.ADD_ONLY
    return requested
