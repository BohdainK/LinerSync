from dataclasses import dataclass
from typing import Literal

from linersync.domain.matching.types import NeedsAttention
from linersync.domain.playlists import PlaylistDraft
from linersync.domain.sync import SyncedEntry
from linersync.domain.tracks import Track, TrackKey


@dataclass(frozen=True, slots=True)
class CreatePlaylist:
    op_id: str
    draft: PlaylistDraft


@dataclass(frozen=True, slots=True)
class UpdateMetadata:
    op_id: str
    draft: PlaylistDraft


@dataclass(frozen=True, slots=True)
class AddTracks:
    op_id: str
    track_ids: tuple[str, ...]  # appended at end, in order


@dataclass(frozen=True, slots=True)
class Reorder:
    op_id: str
    order: tuple[str, ...]  # full resulting sequence


@dataclass(frozen=True, slots=True)
class Removal:
    track_id: str
    position: int  # position in the playlist just before RemoveTracks


@dataclass(frozen=True, slots=True)
class RemoveTracks:
    op_id: str
    removals: tuple[Removal, ...]


# The operations a connector's ``apply`` takes (creation has its own method).
PlaylistOp = UpdateMetadata | AddTracks | Reorder | RemoveTracks
Operation = CreatePlaylist | PlaylistOp


@dataclass(frozen=True, slots=True)
class ChangeNotMade:
    kind: Literal["add", "remove", "reorder", "metadata", "create", "delete"]
    track_id: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class MemberPlan:
    member_id: str
    operations: tuple[Operation, ...]
    not_made: tuple[ChangeNotMade, ...]
    expected_after: tuple[str, ...]
    synced: tuple[SyncedEntry, ...]
    draft: PlaylistDraft
    # Pairs the user deleted on the destination; not re-added.
    user_removed: tuple[SyncedEntry, ...] = ()
    # Destination ids the sync doesn't own; kept.
    user_added: tuple[str, ...] = ()
    # Owned ids the user moved; they keep their place.
    pinned: tuple[str, ...] = ()
    # Previous pairs of songs unresolved this run whose target is still on
    # the destination: still the sync's, kept in the saved state but never
    # used to seed matches (unlike `synced`).
    held: tuple[SyncedEntry, ...] = ()

    @property
    def removal_count(self) -> int:
        return sum(
            len(op.removals) for op in self.operations if isinstance(op, RemoveTracks)
        )


@dataclass(frozen=True, slots=True)
class UnresolvedEntry:
    member_id: str
    source: Track
    position: int
    decision: NeedsAttention


@dataclass(frozen=True, slots=True)
class Conflict:
    member_ids: tuple[str, ...]
    source_key: TrackKey | None
    detail: str


@dataclass(frozen=True, slots=True)
class SyncPlan:
    sync_id: str
    member_plans: tuple[MemberPlan, ...]
    unresolved: tuple[UnresolvedEntry, ...]
    conflicts: tuple[Conflict, ...] = ()

    def for_member(self, member_id: str) -> MemberPlan:
        for p in self.member_plans:
            if p.member_id == member_id:
                return p
        raise KeyError(member_id)
