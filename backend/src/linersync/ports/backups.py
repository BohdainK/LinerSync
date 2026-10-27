from typing import Protocol

from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    CoverImage,
    PlaylistVersion,
)
from linersync.domain.tracks import ServiceId


class BackupRepository(Protocol):
    def save_entry(self, entry: BackedUpPlaylist) -> None:
        """Insert or replace by entry_id. ValueError if another entry has the
        same (service, playlist_id)."""
        ...

    def get_entry(self, entry_id: str) -> BackedUpPlaylist | None: ...
    def find_entry(
        self, service: ServiceId, playlist_id: str
    ) -> BackedUpPlaylist | None: ...

    def list_entries(
        self, service: ServiceId | None = None, *, archived: bool | None = None
    ) -> list[BackedUpPlaylist]:
        """Sorted by name.casefold(), then entry_id. archived=None: all."""
        ...

    def delete_entry(self, entry_id: str) -> None:
        """Also deletes its versions; no-op when absent."""
        ...

    def add_version(self, version: PlaylistVersion) -> None: ...
    def get_version(self, version_id: str) -> PlaylistVersion | None: ...
    def list_versions(self, entry_id: str) -> list[PlaylistVersion]:
        """Newest added first."""
        ...

    def delete_version(self, version_id: str) -> None: ...
    def save_run(self, run: BackupRun) -> None:
        """Insert or replace by backup_id."""
        ...

    def get_run(self, backup_id: str) -> BackupRun | None: ...
    def list_runs(
        self, service: ServiceId | None = None, limit: int | None = None
    ) -> list[BackupRun]:
        """Newest started_at first; ties: last saved first."""
        ...

    def delete_service(self, service: ServiceId) -> None:
        """Its entries, their versions and its runs."""
        ...

    def put_cover(self, cover: CoverImage) -> None:
        """No-op when the sha256 is already stored."""
        ...

    def get_cover(self, sha256: str) -> CoverImage | None: ...
    def prune_covers(self) -> int:
        """Delete covers no version references; returns the count."""
        ...


class CoverFetcher(Protocol):
    async def fetch(self, url: str) -> CoverImage | None:
        """Never raises; None on any failure."""
        ...
