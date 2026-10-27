from collections.abc import Mapping

from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    CoverImage,
    PlaylistVersion,
)
from linersync.domain.tracks import ServiceId


class InMemoryBackups:
    def __init__(self) -> None:
        self._entries: dict[str, BackedUpPlaylist] = {}
        self._versions: dict[str, PlaylistVersion] = {}
        self._runs: dict[str, BackupRun] = {}
        self._run_order: dict[str, int] = {}
        self._run_seq = 0
        self._covers: dict[str, CoverImage] = {}

    def save_entry(self, entry: BackedUpPlaylist) -> None:
        for other in self._entries.values():
            if (
                other.entry_id != entry.entry_id
                and other.service == entry.service
                and other.playlist_id == entry.playlist_id
            ):
                raise ValueError(
                    f"{entry.service}/{entry.playlist_id} already has a backup entry"
                )
        self._entries[entry.entry_id] = entry

    def get_entry(self, entry_id: str) -> BackedUpPlaylist | None:
        return self._entries.get(entry_id)

    def find_entry(
        self, service: ServiceId, playlist_id: str
    ) -> BackedUpPlaylist | None:
        for e in self._entries.values():
            if e.service == service and e.playlist_id == playlist_id:
                return e
        return None

    def list_entries(
        self, service: ServiceId | None = None, *, archived: bool | None = None
    ) -> list[BackedUpPlaylist]:
        found = [
            e
            for e in self._entries.values()
            if (service is None or e.service == service)
            and (archived is None or e.archived == archived)
        ]
        found.sort(key=lambda e: (e.name.casefold(), e.entry_id))
        return found

    def delete_entry(self, entry_id: str) -> None:
        self._entries.pop(entry_id, None)
        self._versions = {
            k: v for k, v in self._versions.items() if v.entry_id != entry_id
        }

    def add_version(self, version: PlaylistVersion) -> None:
        self._versions.pop(version.version_id, None)
        self._versions[version.version_id] = version

    def get_version(self, version_id: str) -> PlaylistVersion | None:
        return self._versions.get(version_id)

    def list_versions(self, entry_id: str) -> list[PlaylistVersion]:
        found = [v for v in self._versions.values() if v.entry_id == entry_id]
        found.reverse()
        return found

    def delete_version(self, version_id: str) -> None:
        self._versions.pop(version_id, None)

    def save_run(self, run: BackupRun) -> None:
        self._runs[run.backup_id] = run
        self._run_seq += 1
        self._run_order[run.backup_id] = self._run_seq

    def get_run(self, backup_id: str) -> BackupRun | None:
        return self._runs.get(backup_id)

    def list_runs(
        self, service: ServiceId | None = None, limit: int | None = None
    ) -> list[BackupRun]:
        runs = [
            r for r in self._runs.values() if service is None or r.service == service
        ]
        runs.sort(
            key=lambda r: (r.started_at, self._run_order[r.backup_id]), reverse=True
        )
        return runs if limit is None else runs[:limit]

    def delete_service(self, service: ServiceId) -> None:
        for e in [e for e in self._entries.values() if e.service == service]:
            self.delete_entry(e.entry_id)
        for r in [r for r in self._runs.values() if r.service == service]:
            del self._runs[r.backup_id]
            del self._run_order[r.backup_id]

    def put_cover(self, cover: CoverImage) -> None:
        self._covers.setdefault(cover.sha256, cover)

    def get_cover(self, sha256: str) -> CoverImage | None:
        return self._covers.get(sha256)

    def prune_covers(self) -> int:
        used = {
            v.cover_sha256
            for v in self._versions.values()
            if v.cover_sha256 is not None
        }
        gone = [s for s in self._covers if s not in used]
        for s in gone:
            del self._covers[s]
        return len(gone)


class StaticCovers:
    def __init__(self, images: Mapping[str, CoverImage] | None = None) -> None:
        self._images = dict(images or {})
        self.calls: list[str] = []

    async def fetch(self, url: str) -> CoverImage | None:
        self.calls.append(url)
        return self._images.get(url)
