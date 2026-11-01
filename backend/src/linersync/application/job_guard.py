"""The job guard: a connector wrapper that makes a job cancellable before its
first write (D3), reports expired logins and records the songs it sees (D8)."""

from collections.abc import Awaitable, Callable, Mapping
from typing import TypeVar

from linersync.domain.operations import (
    AddTracks,
    RemoveTracks,
    Reorder,
    SyncPlan,
    UpdateMetadata,
)
from linersync.domain.playlists import PlaylistDraft, PlaylistMeta, PlaylistSnapshot
from linersync.domain.tracks import ServiceId, Track, TrackKey, TrackQuery
from linersync.ports.connector import Connector
from linersync.ports.errors import ConnectorError

_T = TypeVar("_T")


class JobCancelled(Exception):
    """Deliberately not a ConnectorError: the runner aborts the run."""

    def __init__(self) -> None:
        super().__init__("the job was cancelled before its first write")


class JobGuard:
    def __init__(
        self, on_auth_required: Callable[[ServiceId], None], *, cancellable: bool = True
    ) -> None:
        self.on_auth_required = on_auth_required
        self.cancellable = cancellable
        self.cancel_requested = False
        self.wrote = not cancellable
        self.raised = False
        self.seen: dict[TrackKey, Track] = {}

    def check(self) -> None:
        """Raise JobCancelled iff a cancel was requested and nothing was written."""
        if self.cancel_requested and not self.wrote:
            self.raised = True
            raise JobCancelled()

    def reset_writes(self) -> None:
        """Count nothing as written again: a cancel may stop the job now."""
        self.wrote = not self.cancellable

    def record(self, tracks: list[Track]) -> None:
        for t in tracks:
            self.seen[t.key] = t


class GuardedConnector:
    def __init__(self, inner: Connector, guard: JobGuard) -> None:
        self._inner = inner
        self._guard = guard
        self.service = inner.service
        self.display_name = inner.display_name
        self.capabilities = inner.capabilities
        self.limits = inner.limits

    async def list_playlists(self) -> list[PlaylistMeta]:
        self._guard.check()
        return await self._call(self._inner.list_playlists())

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        self._guard.check()
        snap = await self._call(self._inner.read_playlist(playlist_id))
        self._guard.record([e.track for e in snap.entries])
        return snap

    async def search(self, query: TrackQuery) -> list[Track]:
        self._guard.check()
        found = await self._call(self._inner.search(query))
        self._guard.record(found)
        return found

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        self._guard.wrote = True
        return await self._call(self._inner.create_playlist(draft))

    async def apply(
        self,
        playlist_id: str,
        op: UpdateMetadata | AddTracks | Reorder | RemoveTracks,
    ) -> None:
        self._guard.wrote = True
        await self._call(self._inner.apply(playlist_id, op))

    async def _call(self, call: Awaitable[_T]) -> _T:
        try:
            return await call
        except ConnectorError as e:
            if e.auth_required:
                self._guard.on_auth_required(e.service)
            raise


def referenced_keys(plan: SyncPlan, services: Mapping[str, ServiceId]) -> set[TrackKey]:
    """Every track the plan names (`services`: member id → service). Reorder
    lists only tracks the playlist already holds, so it is skipped."""
    keys: set[TrackKey] = set()
    for mp in plan.member_plans:
        for entry in mp.synced:
            keys.add(entry.source_key)
        service = services.get(mp.member_id)
        if service is None:
            continue
        ids: list[str] = []
        for op in mp.operations:
            if isinstance(op, AddTracks):
                ids.extend(op.track_ids)
            elif isinstance(op, RemoveTracks):
                ids.extend(r.track_id for r in op.removals)
        ids.extend(n.track_id for n in mp.not_made if n.track_id is not None)
        ids.extend(entry.target_track_id for entry in mp.synced)
        keys.update(TrackKey(service, tid) for tid in ids)
    for u in plan.unresolved:
        keys.add(u.source.key)
        keys.update(c.track.key for c in u.decision.candidates)
    return keys
