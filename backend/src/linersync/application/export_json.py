"""Full JSON export (D8) and merge restore (D9).

The export holds settings, syncs, runs and their contexts, remembered matches,
each backup entry with its latest version, the backup runs and the covers those
versions reference. It never holds credentials, the secret key, sessions, jobs
or other server-only state. Restoring merges: local data always wins.
"""

import base64
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from linersync.application.settings import AppSettings
from linersync.domain.backups import (
    COVER_MEDIA_TYPES,
    BackedUpPlaylist,
    BackupRun,
    CoverImage,
    PlaylistVersion,
)
from linersync.domain.jobs import RunContext
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.runs import RunStatus, SyncRun
from linersync.domain.sync import SyncGroup, SyncStatus
from linersync.ports.backups import BackupRepository
from linersync.ports.jobs import RunContextRepository
from linersync.ports.repositories import Repositories
from linersync.ports.runtime import Clock

EXPORT_FORMAT = "linersync-export"
EXPORT_VERSION = 1

NOT_AN_EXPORT = "This file isn't a LinerSync export"
NEWER_EXPORT = (
    "This export was made by a newer version of LinerSync (format {version}); "
    "update LinerSync first"
)
DAMAGED_EXPORT = "The export file is damaged: {detail}"
IMPORTED_PAUSE_REASON = "Imported from an export; check it, then resume it"
EXPORTED_WHILE_RUNNING = "Exported while running"

KINDS = (
    "syncs",
    "runs",
    "run_contexts",
    "remembered_matches",
    "backup_playlists",
    "backup_runs",
)


class SettingsStore(Protocol):
    def get(self) -> AppSettings: ...
    def save(self, s: AppSettings) -> None: ...


@dataclass(frozen=True, slots=True)
class ExportCover:
    sha256: str
    media_type: str
    data_base64: str


@dataclass(frozen=True, slots=True)
class ExportDocument:
    format: str
    version: int
    exported_at: datetime
    app_version: str
    settings: AppSettings
    syncs: tuple[SyncGroup, ...] = ()
    runs: tuple[SyncRun, ...] = ()
    run_contexts: tuple[RunContext, ...] = ()
    remembered_matches: tuple[RememberedMatch, ...] = ()
    backup_playlists: tuple[BackedUpPlaylist, ...] = ()
    backup_versions: tuple[PlaylistVersion, ...] = ()  # latest version per entry
    backup_runs: tuple[BackupRun, ...] = ()
    covers: tuple[ExportCover, ...] = ()  # covers the exported versions reference


@dataclass(frozen=True, slots=True)
class RestoreReport:
    added: dict[str, int]
    skipped: dict[str, int]
    settings_applied: bool


def check_header(data: object) -> None:
    """Raise ValueError unless `data` looks like an export this version reads."""
    if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
        raise ValueError(NOT_AN_EXPORT)
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(NOT_AN_EXPORT)
    if version > EXPORT_VERSION:
        raise ValueError(NEWER_EXPORT.format(version=version))


def check_covers(doc: ExportDocument) -> None:
    """Raise ValueError unless every cover is valid base64 of an allowed image
    type whose sha256 matches its `sha256`. Run before anything is written."""
    for cover in doc.covers:
        if cover.media_type not in COVER_MEDIA_TYPES:
            raise ValueError(f"cover {cover.sha256}: unsupported type")
        data = base64.b64decode(cover.data_base64, validate=True)
        if hashlib.sha256(data).hexdigest() != cover.sha256:
            raise ValueError(f"cover {cover.sha256}: its data doesn't match")


class ExportService:
    def __init__(
        self,
        repos: Repositories,
        contexts: RunContextRepository,
        backups: BackupRepository,
        settings: SettingsStore,
        clock: Clock,
    ) -> None:
        self._repos = repos
        self._contexts = contexts
        self._backups = backups
        self._settings = settings
        self._clock = clock

    # -- export (D8) ------------------------------------------------------------
    def build(self, app_version: str) -> ExportDocument:
        runs = self._repos.runs.list_all()
        contexts = [
            ctx
            for ctx in (self._contexts.get(r.run_id) for r in runs)
            if ctx is not None
        ]
        entries = self._backups.list_entries()
        versions: list[PlaylistVersion] = []
        covers: dict[str, ExportCover] = {}
        for entry in entries:
            version = self._backups.get_version(entry.latest_version_id)
            if version is None:
                continue
            versions.append(version)
            sha = version.cover_sha256
            if sha is not None and sha not in covers:
                cover = self._backups.get_cover(sha)
                if cover is not None:
                    covers[sha] = ExportCover(
                        cover.sha256,
                        cover.media_type,
                        base64.b64encode(cover.data).decode("ascii"),
                    )
        return ExportDocument(
            format=EXPORT_FORMAT,
            version=EXPORT_VERSION,
            exported_at=self._clock.now(),
            app_version=app_version,
            settings=self._settings.get(),
            syncs=tuple(self._repos.syncs.list()),
            runs=tuple(runs),
            run_contexts=tuple(contexts),
            remembered_matches=tuple(self._repos.matches.list_all()),
            backup_playlists=tuple(entries),
            backup_versions=tuple(versions),
            backup_runs=tuple(self._backups.list_runs()),
            covers=tuple(covers.values()),
        )

    # -- restore (D9) -----------------------------------------------------------
    def restore(self, doc: ExportDocument) -> RestoreReport:
        """Merge `doc` into the stores; anything already present locally is
        skipped and kept as it is. Raises ValueError (before writing anything)
        only for cover data that isn't valid base64."""
        added = dict.fromkeys(KINDS, 0)
        skipped = dict.fromkeys(KINDS, 0)
        # Decoded before anything is written, so bad base64 writes nothing.
        covers = {
            c.sha256: CoverImage(
                c.sha256, c.media_type, base64.b64decode(c.data_base64, validate=True)
            )
            for c in doc.covers
        }

        def count(kind: str, new: bool) -> None:
            (added if new else skipped)[kind] += 1

        repos = self._repos
        for group in doc.syncs:
            new = repos.syncs.get(group.sync_id) is None
            if new:
                repos.syncs.save(
                    replace(
                        group,
                        status=SyncStatus.PAUSED,
                        pause_reason=IMPORTED_PAUSE_REASON,
                    )
                )
            count("syncs", new)
        for run in doc.runs:
            new = repos.runs.get(run.run_id) is None
            if new:
                if run.status is RunStatus.RUNNING:
                    # Like JobService.recover: it ended when last known to
                    # be running, here the moment of the export.
                    run = replace(
                        run,
                        status=RunStatus.ABORTED,
                        error=EXPORTED_WHILE_RUNNING,
                        finished_at=run.finished_at or doc.exported_at,
                    )
                repos.runs.save(run)
            count("runs", new)
        for ctx in doc.run_contexts:
            new = self._contexts.get(ctx.run_id) is None
            if new:
                self._contexts.save(ctx)
            count("run_contexts", new)
        for match in doc.remembered_matches:
            new = repos.matches.get(match.source_key, match.target_service) is None
            if new:
                repos.matches.remember(match)
            count("remembered_matches", new)
        versions = {v.version_id: v for v in doc.backup_versions}
        for entry in doc.backup_playlists:
            count("backup_playlists", self._restore_entry(entry, versions, covers))
        for backup_run in doc.backup_runs:
            new = self._backups.get_run(backup_run.backup_id) is None
            if new:
                self._backups.save_run(backup_run)
            count("backup_runs", new)
        applied = self._settings.get() == AppSettings()
        if applied:
            self._settings.save(doc.settings)
        return RestoreReport(added=added, skipped=skipped, settings_applied=applied)

    def _restore_entry(
        self,
        entry: BackedUpPlaylist,
        versions: dict[str, PlaylistVersion],
        covers: dict[str, CoverImage],
    ) -> bool:
        store = self._backups
        version = versions.get(entry.latest_version_id)
        if (
            version is None
            or version.entry_id != entry.entry_id
            or store.get_entry(entry.entry_id) is not None
            or store.find_entry(entry.service, entry.playlist_id) is not None
            or store.get_version(version.version_id) is not None
        ):
            return False
        sha = version.cover_sha256
        if sha is not None and sha in covers:
            store.put_cover(covers[sha])
        store.add_version(version)
        store.save_entry(replace(entry, versions=1))
        return True
