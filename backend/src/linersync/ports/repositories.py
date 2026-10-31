from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from linersync.domain.attention import AttentionItem
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.playlists import PlaylistSnapshot
from linersync.domain.runs import SyncRun
from linersync.domain.sync import SyncGroup
from linersync.domain.tracks import ServiceId, TrackKey


@dataclass(frozen=True, slots=True)
class BreakerState:
    service: ServiceId
    consecutive_failures: int = 0
    opened_at: datetime | None = None
    last_error: str | None = None


class SyncRepository(Protocol):
    def get(self, sync_id: str) -> SyncGroup | None: ...
    def save(self, group: SyncGroup) -> None: ...
    def list(self) -> list[SyncGroup]: ...
    def delete(self, sync_id: str) -> None:
        """No-op when absent."""
        ...


class RunRepository(Protocol):
    def save(self, run: SyncRun) -> None: ...
    def get(self, run_id: str) -> SyncRun | None: ...
    def list_for_sync(self, sync_id: str, limit: int | None = None) -> list[SyncRun]:
        """Newest first; at most `limit` runs when given."""
        ...

    def list_all(self) -> list[SyncRun]:
        """Every run, oldest saved first."""
        ...

    def list_since(self, since: datetime) -> list[SyncRun]:
        """Runs with started_at >= since, oldest saved first."""
        ...

    def list_running(self) -> list[SyncRun]:
        """Runs with status RUNNING, any order."""
        ...


class SnapshotRepository(Protocol):
    def save_pre_write(
        self, run_id: str, member_id: str, snap: PlaylistSnapshot | None
    ) -> None: ...
    def get_pre_write(self, run_id: str, member_id: str) -> PlaylistSnapshot | None: ...
    def has_pre_write(self, run_id: str, member_id: str) -> bool: ...


class MatchRepository(Protocol):
    def get(
        self, source_key: TrackKey, target_service: ServiceId
    ) -> RememberedMatch | None: ...
    def remember(self, match: RememberedMatch) -> None: ...
    def list_all(self) -> list[RememberedMatch]: ...


class AttentionRepository(Protocol):
    def upsert(self, item: AttentionItem) -> AttentionItem:
        """An open item with the same dedupe_key is replaced, keeping its
        item_id and created_at."""
        ...

    def get(self, item_id: str) -> AttentionItem | None: ...
    def list_open(self) -> list[AttentionItem]: ...
    def resolve(self, item_id: str, resolution: str, at: datetime) -> None: ...


class BreakerRepository(Protocol):
    def get(self, service: ServiceId) -> BreakerState:
        """Default: closed, 0 failures."""
        ...

    def save(self, state: BreakerState) -> None: ...


class ApprovalRepository(Protocol):
    def approve(self, sync_id: str, member_id: str, fp: str) -> None: ...
    def is_approved(self, sync_id: str, member_id: str, fp: str) -> bool: ...
    def consume(self, sync_id: str, member_id: str, fp: str) -> bool:
        """True (and the approval is removed) if it was present: an approval
        covers exactly one pass."""
        ...


@dataclass(frozen=True, slots=True)
class Repositories:
    syncs: SyncRepository
    runs: RunRepository
    snapshots: SnapshotRepository
    matches: MatchRepository
    attention: AttentionRepository
    breakers: BreakerRepository
    approvals: ApprovalRepository
