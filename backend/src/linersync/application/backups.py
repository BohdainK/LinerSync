"""Back up one service's playlists: versions, covers and the archive step."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from linersync.application.settings import DEFAULT_VERSIONS_KEPT
from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    BackupStatus,
    PlaylistVersion,
    content_hash,
)
from linersync.domain.playlists import PlaylistMeta, PlaylistSnapshot
from linersync.domain.sync import MemberRole, SyncStatus
from linersync.domain.tracks import ServiceId
from linersync.ports.backups import BackupRepository, CoverFetcher
from linersync.ports.connector import Connector
from linersync.ports.errors import ConnectorError, PlaylistNotFound
from linersync.ports.repositories import SyncRepository
from linersync.ports.runtime import Clock, IdFactory

BACKUP_STOPPED = "The backup stopped before it finished"
BACKUP_ALL_LABEL = "Back up all services"

BackupProgress = Callable[[int, int, str], None]
"""(done, total, message)"""


def _no_progress(done: int, total: int, message: str) -> None:
    return None


def _ignore(run: BackupRun) -> None:
    return None


class _AuthRequired(Exception):
    pass


@dataclass
class _Tally:
    playlists: int = 0
    saved: int = 0
    unchanged: int = 0
    archived: int = 0
    unreadable: int = 0


class BackupService:
    def __init__(
        self,
        store: BackupRepository,
        syncs: SyncRepository,
        covers: CoverFetcher,
        clock: Clock,
        new_id: IdFactory,
        versions_kept: Callable[[], int] = lambda: DEFAULT_VERSIONS_KEPT,
    ) -> None:
        self._store = store
        self._syncs = syncs
        self._covers = covers
        self._clock = clock
        self._new_id = new_id
        self._versions_kept = versions_kept

    async def back_up(
        self,
        connector: Connector,
        progress: BackupProgress = _no_progress,
        *,
        on_saved: Callable[[BackupRun], None] = _ignore,
    ) -> BackupRun:
        """Back up one service (D1–D3, D5). Saves and returns its BackupRun, and
        passes it to `on_saved`. A non-ConnectorError exception (e.g.
        JobCancelled) saves the run FAILED with BACKUP_STOPPED, then propagates."""
        backup_id = self._new_id()
        started_at = self._clock.now()
        tally = _Tally()
        errors: list[str] = []

        def finish(status: BackupStatus, error: str | None = None) -> BackupRun:
            run = BackupRun(
                backup_id=backup_id,
                service=connector.service,
                started_at=started_at,
                finished_at=self._clock.now(),
                status=status,
                playlists=tally.playlists,
                saved=tally.saved,
                unchanged=tally.unchanged,
                archived=tally.archived,
                unreadable=tally.unreadable,
                errors=tuple(errors),
                error=error,
            )
            self._store.save_run(run)
            on_saved(run)
            return run

        try:
            failure = await self._run(connector, backup_id, tally, errors, progress)
        except BaseException:
            finish(BackupStatus.FAILED, BACKUP_STOPPED)
            raise
        if failure is not None:
            return finish(BackupStatus.FAILED, failure)
        return finish(BackupStatus.PARTIAL if errors else BackupStatus.SUCCEEDED)

    async def _run(
        self,
        connector: Connector,
        backup_id: str,
        tally: _Tally,
        errors: list[str],
        progress: BackupProgress,
    ) -> str | None:
        """Steps 1–5 short of saving the run. Returns the error of a failed run."""
        sign_in = f"Sign in to {connector.display_name} again"
        try:
            listed = await connector.list_playlists()
        except ConnectorError as e:
            if e.auth_required:
                return sign_in
            return f"Couldn't list the playlists: {e.detail}"
        # A playlist listed twice is backed up once (the first listing wins).
        unique: dict[str, PlaylistMeta] = {}
        for meta in listed:
            unique.setdefault(meta.playlist_id, meta)
        listed = list(unique.values())
        try:
            gone = await self._save_versions(
                connector, listed, backup_id, tally, errors, progress
            )
        except _AuthRequired:
            return sign_in
        present = {m.playlist_id for m in listed} - gone
        tally.archived = self._archive(connector.service, present)
        self._store.prune_covers()
        return None

    async def _save_versions(
        self,
        connector: Connector,
        listed: list[PlaylistMeta],
        backup_id: str,
        tally: _Tally,
        errors: list[str],
        progress: BackupProgress,
    ) -> set[str]:
        """Step 3: read and version each readable listed playlist. Returns the
        ids of playlists that turned out to be gone. Raises _AuthRequired."""
        entries = {
            e.playlist_id: e for e in self._store.list_entries(connector.service)
        }
        readable = [m for m in listed if m.readable]
        tally.unreadable = len(listed) - len(readable)
        tally.playlists = len(readable)
        gone: set[str] = set()
        for i, meta in enumerate(readable):
            progress(i, len(readable), meta.name)
            entry = entries.get(meta.playlist_id)
            latest = (
                self._store.get_version(entry.latest_version_id)
                if entry is not None
                else None
            )
            if (
                latest is not None
                and meta.version_token is not None
                and meta.version_token == latest.snapshot.meta.version_token
            ):
                tally.unchanged += 1
                continue
            try:
                snap = await connector.read_playlist(meta.playlist_id)
            except PlaylistNotFound:
                gone.add(meta.playlist_id)
                continue
            except ConnectorError as e:
                if e.auth_required:
                    raise _AuthRequired from e
                errors.append(f"{meta.name}: couldn't read it: {e.detail}")
                continue
            if not snap.is_complete:
                reason = f"; {snap.missing_reason}" if snap.missing_reason else ""
                errors.append(
                    f"{meta.name}: only {len(snap.entries)} of "
                    f"{snap.expected_total} songs could be read{reason}"
                )
                continue
            digest = content_hash(snap)
            if latest is not None and latest.content_hash == digest:
                tally.unchanged += 1
                continue
            entries[meta.playlist_id] = await self._add_version(
                snap, digest, entry, latest, backup_id
            )
            tally.saved += 1
        return gone

    async def _add_version(
        self,
        snap: PlaylistSnapshot,
        digest: str,
        entry: BackedUpPlaylist | None,
        latest: PlaylistVersion | None,
        backup_id: str,
    ) -> BackedUpPlaylist:
        """Store a new version; returns the saved entry."""
        cover = await self._cover_for(snap.meta.cover_url, latest)
        entry_id = entry.entry_id if entry is not None else self._new_id()
        version = PlaylistVersion(
            version_id=self._new_id(),
            entry_id=entry_id,
            backup_id=backup_id,
            snapshot=snap,
            content_hash=digest,
            cover_sha256=cover,
        )
        self._store.add_version(version)
        kept = max(1, self._versions_kept())
        versions = self._store.list_versions(entry_id)
        for old in versions[kept:]:
            self._store.delete_version(old.version_id)
        count = min(len(versions), kept)
        now = self._clock.now()
        if entry is None:
            saved = BackedUpPlaylist(
                entry_id=entry_id,
                service=snap.meta.service,
                playlist_id=snap.meta.playlist_id,
                name=snap.meta.name,
                latest_version_id=version.version_id,
                track_count=len(snap.entries),
                versions=count,
                first_backed_up_at=now,
                last_seen_at=now,
                cover_sha256=cover,
            )
        else:
            saved = replace(
                entry,
                name=snap.meta.name,
                latest_version_id=version.version_id,
                track_count=len(snap.entries),
                cover_sha256=cover,
                versions=count,
            )
        self._store.save_entry(saved)
        return saved

    async def _cover_for(
        self, url: str | None, latest: PlaylistVersion | None
    ) -> str | None:
        if latest is not None and latest.snapshot.meta.cover_url == url:
            return latest.cover_sha256
        if url is None:
            return None
        image = await self._covers.fetch(url)
        if image is None:
            return None
        self._store.put_cover(image)
        return image.sha256

    def _archive(self, service: ServiceId, present: set[str]) -> int:
        """Step 4 (D3). Returns the number of entries archived."""
        protected = {
            m.playlist_id
            for g in self._syncs.list()
            if g.status is SyncStatus.ACTIVE
            for m in g.members
            if m.role is MemberRole.DESTINATION
            and m.service == service
            and m.playlist_id is not None
        }
        now = self._clock.now()
        archived = 0
        for entry in self._store.list_entries(service):
            if entry.playlist_id in present:
                updated = replace(
                    entry, last_seen_at=now, missing_since=None, archived_at=None
                )
            elif entry.archived:
                continue
            elif entry.playlist_id in protected:
                if entry.missing_since is not None:
                    continue
                updated = replace(entry, missing_since=now)
            else:
                updated = replace(
                    entry, archived_at=now, missing_since=entry.missing_since or now
                )
                archived += 1
            self._store.save_entry(updated)
        return archived
