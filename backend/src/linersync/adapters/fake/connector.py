from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from linersync.domain.capabilities import Capability
from linersync.domain.matching.normalize import normalize_text
from linersync.domain.operations import AddTracks, RemoveTracks, Reorder, UpdateMetadata
from linersync.domain.playlists import (
    PlaylistDraft,
    PlaylistEntry,
    PlaylistMeta,
    PlaylistSnapshot,
)
from linersync.domain.tracks import ServiceId, Track, TrackQuery
from linersync.ports.connector import ConnectorLimits
from linersync.ports.errors import PlaylistNotFound, ReadFailed, WriteFailed

_Op = UpdateMetadata | AddTracks | Reorder | RemoveTracks

_DEFAULT_LIMITS = ConnectorLimits(100, 100)

_REQUIRED: dict[type, Capability] = {
    AddTracks: Capability.ADD,
    RemoveTracks: Capability.REMOVE,
    Reorder: Capability.REORDER,
    UpdateMetadata: Capability.EDIT_METADATA,
}


class FakeConnector:
    def __init__(
        self,
        service: ServiceId,
        display_name: str,
        capabilities: frozenset[Capability],
        *,
        limits: ConnectorLimits = _DEFAULT_LIMITS,
        catalog: Sequence[Track] = (),
    ) -> None:
        self.service = service
        self.display_name = display_name
        self.capabilities = capabilities
        self.limits = limits
        self.catalog: tuple[Track, ...] = tuple(catalog)
        self.playlists: dict[str, PlaylistSnapshot] = {}
        self.calls: list[str] = []
        self._read_failures: dict[str, int] = {}
        self._truncated: set[str] = set()
        self._write_failures: dict[type, list[bool]] = {}
        self._dropped: set[str] = set()
        self._search_failures = 0
        self._versions: dict[str, int] = {}
        self._created = 0

    # -- test setup -------------------------------------------------------
    def add_playlist(self, snapshot: PlaylistSnapshot) -> None:
        self.playlists[snapshot.meta.playlist_id] = snapshot

    def delete_playlist(self, playlist_id: str) -> None:
        self.playlists.pop(playlist_id, None)

    def fail_reads(self, playlist_id: str, times: int = 1) -> None:
        self._read_failures[playlist_id] = times

    def truncate_reads(self, playlist_id: str) -> None:
        self._truncated.add(playlist_id)

    def fail_writes(self, op_type: type, times: int = 1, applied: bool = False) -> None:
        self._write_failures.setdefault(op_type, []).extend([applied] * times)

    def drop_adds(self, track_ids: Iterable[str]) -> None:
        self._dropped.update(track_ids)

    def fail_search(self, times: int = 1) -> None:
        self._search_failures = times

    # -- Connector --------------------------------------------------------
    async def list_playlists(self) -> list[PlaylistMeta]:
        self.calls.append("list")
        return [s.meta for s in self.playlists.values()]

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        self.calls.append(f"read:{playlist_id}")
        if self._read_failures.get(playlist_id, 0) > 0:
            self._read_failures[playlist_id] -= 1
            raise ReadFailed(self.service, f"injected read failure for {playlist_id}")
        snap = self._get(playlist_id)
        if playlist_id in self._truncated:
            return replace(snap, expected_total=len(snap.entries) + 1)
        return replace(snap, expected_total=len(snap.entries))

    async def search(self, query: TrackQuery) -> list[Track]:
        self.calls.append(f"search:{query.title}")
        if self._search_failures > 0:
            self._search_failures -= 1
            raise ReadFailed(self.service, "injected search failure")
        wanted = normalize_text(query.title)
        return [
            t
            for t in self.catalog
            if (query.isrc is not None and t.isrc == query.isrc)
            or (wanted and wanted in normalize_text(t.title))
        ]

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        self.calls.append(f"create:{draft.name}")
        assert Capability.CREATE_PLAYLIST in self.capabilities, (
            f"{self.service} cannot create playlists"
        )
        self._created += 1
        pid = f"{self.service}-new-{self._created}"
        meta = PlaylistMeta(
            service=self.service,
            playlist_id=pid,
            name=draft.name,
            description=draft.description,
            cover_url=draft.cover_url,
            track_count=0,
        )
        self.playlists[pid] = PlaylistSnapshot(meta, (), 0, datetime.now(UTC))
        return meta

    async def apply(self, playlist_id: str, op: _Op) -> None:
        self.calls.append(f"apply:{type(op).__name__}:{playlist_id}")
        needed = _REQUIRED[type(op)]
        assert needed in self.capabilities, (
            f"{self.service} lacks {needed} but was asked to apply {type(op).__name__}"
        )
        snap = self._get(playlist_id)
        failures = self._write_failures.get(type(op))
        failure = failures.pop(0) if failures else None
        if failure is None or failure:
            self._write(playlist_id, snap, op)
        if failure is not None:
            raise WriteFailed(
                self.service,
                f"injected write failure for {type(op).__name__}",
                maybe_applied=failure,
            )

    # -- internals --------------------------------------------------------
    def _get(self, playlist_id: str) -> PlaylistSnapshot:
        snap = self.playlists.get(playlist_id)
        if snap is None:
            raise PlaylistNotFound(self.service, f"no playlist {playlist_id}")
        return snap

    def _resolve(self, snap: PlaylistSnapshot, track_id: str) -> Track:
        for e in snap.entries:
            if e.track.track_id == track_id:
                return e.track
        for t in self.catalog:
            if t.track_id == track_id:
                return t
        for other in self.playlists.values():
            for e in other.entries:
                if e.track.track_id == track_id:
                    return e.track
        return Track(self.service, track_id, track_id, ("Unknown",))

    def _write(self, playlist_id: str, snap: PlaylistSnapshot, op: _Op) -> None:
        entries = list(snap.entries)
        meta = snap.meta
        now = datetime.now(UTC)
        match op:
            case AddTracks():
                for tid in op.track_ids:
                    if tid not in self._dropped:
                        entries.append(PlaylistEntry(self._resolve(snap, tid), now))
            case Reorder():
                current = Counter(e.track.track_id for e in entries)
                assert Counter(op.order) == current, (
                    f"Reorder is not a permutation of {sorted(current.elements())}"
                )
                pool: dict[str, list[PlaylistEntry]] = {}
                for e in entries:
                    pool.setdefault(e.track.track_id, []).append(e)
                entries = [pool[tid].pop(0) for tid in op.order]
            case RemoveTracks():
                positions = [r.position for r in op.removals]
                assert len(set(positions)) == len(positions), "duplicate positions"
                for r in op.removals:
                    assert 0 <= r.position < len(entries), (
                        f"position {r.position} out of range"
                    )
                    assert entries[r.position].track.track_id == r.track_id, (
                        f"position {r.position} holds "
                        f"{entries[r.position].track.track_id}, not {r.track_id}"
                    )
                for r in sorted(op.removals, key=lambda x: x.position, reverse=True):
                    del entries[r.position]
            case UpdateMetadata():
                meta = replace(
                    meta,
                    name=op.draft.name,
                    description=op.draft.description,
                    cover_url=op.draft.cover_url,
                )
        n = self._versions.get(playlist_id, 0) + 1
        self._versions[playlist_id] = n
        meta = replace(meta, version_token=f"v{n}", track_count=len(entries))
        self.playlists[playlist_id] = replace(
            snap, meta=meta, entries=tuple(entries), expected_total=len(entries)
        )
