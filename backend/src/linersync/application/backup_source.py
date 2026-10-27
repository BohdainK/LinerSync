"""The stored backup as a read-only connector (D6): restore and archived
playlists as a source."""

from dataclasses import replace

from linersync.domain.backups import BACKUP_SERVICE, PlaylistVersion
from linersync.domain.capabilities import Capability
from linersync.domain.operations import AddTracks, RemoveTracks, Reorder, UpdateMetadata
from linersync.domain.playlists import PlaylistDraft, PlaylistMeta, PlaylistSnapshot
from linersync.domain.tracks import Track, TrackQuery
from linersync.ports.backups import BackupRepository
from linersync.ports.connector import ConnectorLimits
from linersync.ports.errors import PlaylistNotFound, WriteFailed

BACKUP_DISPLAY_NAME = "LinerSync backup"
_READ_ONLY = "The backup is read-only"


class BackupConnector:
    service = BACKUP_SERVICE
    display_name = BACKUP_DISPLAY_NAME
    capabilities = frozenset({Capability.READ_ORDER})
    limits = ConnectorLimits(page_size=10_000, add_batch_size=1, search_cost=0)

    def __init__(self, store: BackupRepository) -> None:
        self._store = store

    async def list_playlists(self) -> list[PlaylistMeta]:
        """Archived entries, with the latest version's description and cover."""
        out: list[PlaylistMeta] = []
        for entry in self._store.list_entries(archived=True):
            latest = self._store.get_version(entry.latest_version_id)
            description = latest.snapshot.meta.description if latest else ""
            cover_url = latest.snapshot.meta.cover_url if latest else None
            out.append(
                PlaylistMeta(
                    BACKUP_SERVICE,
                    entry.entry_id,
                    entry.name,
                    description=description,
                    cover_url=cover_url,
                    version_token=entry.latest_version_id,
                    track_count=entry.track_count,
                )
            )
        return out

    async def read_playlist(self, playlist_id: str) -> PlaylistSnapshot:
        """A version id, or an entry id (its latest version)."""
        version = self._find(playlist_id)
        if version is None:
            raise PlaylistNotFound(BACKUP_SERVICE, f"no backup {playlist_id}")
        snap = version.snapshot
        meta = replace(
            snap.meta,
            service=BACKUP_SERVICE,
            playlist_id=playlist_id,
            version_token=version.version_id,
        )
        return replace(snap, meta=meta, expected_total=len(snap.entries))

    async def search(self, query: TrackQuery) -> list[Track]:
        return []

    async def create_playlist(self, draft: PlaylistDraft) -> PlaylistMeta:
        raise WriteFailed(BACKUP_SERVICE, _READ_ONLY)

    async def apply(
        self,
        playlist_id: str,
        op: UpdateMetadata | AddTracks | Reorder | RemoveTracks,
    ) -> None:
        raise WriteFailed(BACKUP_SERVICE, _READ_ONLY)

    def _find(self, playlist_id: str) -> PlaylistVersion | None:
        version = self._store.get_version(playlist_id)
        if version is not None:
            return version
        entry = self._store.get_entry(playlist_id)
        if entry is None:
            return None
        return self._store.get_version(entry.latest_version_id)
