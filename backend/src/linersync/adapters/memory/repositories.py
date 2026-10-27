from dataclasses import replace
from datetime import datetime

from linersync.domain.attention import AttentionItem
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.playlists import PlaylistSnapshot
from linersync.domain.runs import RunStatus, SyncRun
from linersync.domain.sync import SyncGroup
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.repositories import BreakerState, Repositories


class InMemorySyncs:
    def __init__(self) -> None:
        self._data: dict[str, SyncGroup] = {}

    def get(self, sync_id: str) -> SyncGroup | None:
        return self._data.get(sync_id)

    def save(self, group: SyncGroup) -> None:
        self._data[group.sync_id] = group

    def list(self) -> list[SyncGroup]:
        return list(self._data.values())

    def delete(self, sync_id: str) -> None:
        self._data.pop(sync_id, None)


class InMemoryRuns:
    def __init__(self) -> None:
        self._data: dict[str, SyncRun] = {}
        self._order: dict[str, int] = {}
        self._seq = 0

    def save(self, run: SyncRun) -> None:
        self._data[run.run_id] = run
        self._seq += 1
        self._order[run.run_id] = self._seq

    def get(self, run_id: str) -> SyncRun | None:
        return self._data.get(run_id)

    def list_for_sync(self, sync_id: str, limit: int | None = None) -> list[SyncRun]:
        runs = [r for r in self._data.values() if r.sync_id == sync_id]
        runs.sort(key=lambda r: (r.started_at, self._order[r.run_id]), reverse=True)
        return runs if limit is None else runs[:limit]

    def list_all(self) -> list[SyncRun]:
        return sorted(self._data.values(), key=lambda r: self._order[r.run_id])

    def list_running(self) -> list[SyncRun]:
        return [r for r in self._data.values() if r.status == RunStatus.RUNNING]


class InMemorySnapshots:
    def __init__(self) -> None:
        self._data: dict[tuple[str, str], PlaylistSnapshot | None] = {}

    def save_pre_write(
        self, run_id: str, member_id: str, snap: PlaylistSnapshot | None
    ) -> None:
        self._data[(run_id, member_id)] = snap

    def get_pre_write(self, run_id: str, member_id: str) -> PlaylistSnapshot | None:
        return self._data.get((run_id, member_id))

    def has_pre_write(self, run_id: str, member_id: str) -> bool:
        return (run_id, member_id) in self._data


class InMemoryMatches:
    def __init__(self) -> None:
        self._data: dict[tuple[TrackKey, ServiceId], RememberedMatch] = {}

    def get(
        self, source_key: TrackKey, target_service: ServiceId
    ) -> RememberedMatch | None:
        return self._data.get((source_key, target_service))

    def remember(self, match: RememberedMatch) -> None:
        self._data[(match.source_key, match.target_service)] = match

    def list_all(self) -> list[RememberedMatch]:
        return list(self._data.values())


class InMemoryAttention:
    def __init__(self) -> None:
        self._data: dict[str, AttentionItem] = {}

    def upsert(self, item: AttentionItem) -> AttentionItem:
        for existing in self._data.values():
            if existing.resolved_at is None and existing.dedupe_key == item.dedupe_key:
                stored = replace(
                    item,
                    item_id=existing.item_id,
                    created_at=existing.created_at,
                )
                self._data[existing.item_id] = stored
                return stored
        self._data[item.item_id] = item
        return item

    def get(self, item_id: str) -> AttentionItem | None:
        return self._data.get(item_id)

    def list_open(self) -> list[AttentionItem]:
        return [i for i in self._data.values() if i.resolved_at is None]

    def resolve(self, item_id: str, resolution: str, at: datetime) -> None:
        self._data[item_id] = replace(
            self._data[item_id], resolved_at=at, resolution=resolution
        )


class InMemoryBreakers:
    def __init__(self) -> None:
        self._data: dict[ServiceId, BreakerState] = {}

    def get(self, service: ServiceId) -> BreakerState:
        return self._data.get(service, BreakerState(service))

    def save(self, state: BreakerState) -> None:
        self._data[state.service] = state


class InMemoryApprovals:
    def __init__(self) -> None:
        self._data: set[tuple[str, str, str]] = set()

    def approve(self, sync_id: str, member_id: str, fp: str) -> None:
        self._data.add((sync_id, member_id, fp))

    def is_approved(self, sync_id: str, member_id: str, fp: str) -> bool:
        return (sync_id, member_id, fp) in self._data

    def consume(self, sync_id: str, member_id: str, fp: str) -> bool:
        key = (sync_id, member_id, fp)
        if key not in self._data:
            return False
        self._data.remove(key)
        return True


def in_memory_repositories() -> Repositories:
    return Repositories(
        syncs=InMemorySyncs(),
        runs=InMemoryRuns(),
        snapshots=InMemorySnapshots(),
        matches=InMemoryMatches(),
        attention=InMemoryAttention(),
        breakers=InMemoryBreakers(),
        approvals=InMemoryApprovals(),
    )
