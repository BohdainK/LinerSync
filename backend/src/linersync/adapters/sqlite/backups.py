"""SQLite implementation of the backup repository port."""

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from linersync.adapters.sqlite import schema as t
from linersync.adapters.sqlite.codec import dumps, loads
from linersync.adapters.sqlite.db import utc_text
from linersync.domain.backups import (
    BackedUpPlaylist,
    BackupRun,
    CoverImage,
    PlaylistVersion,
)
from linersync.domain.tracks import ServiceId


class SqliteBackups:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save_entry(self, entry: BackedUpPlaylist) -> None:
        values = {
            "service": str(entry.service),
            "playlist_id": entry.playlist_id,
            "archived_at": None
            if entry.archived_at is None
            else utc_text(entry.archived_at),
            "body": dumps(BackedUpPlaylist, entry),
        }
        try:
            with self._engine.begin() as conn:
                updated = conn.execute(
                    sa.update(t.backup_playlists)
                    .where(t.backup_playlists.c.entry_id == entry.entry_id)
                    .values(**values)
                ).rowcount
                if updated == 0:
                    conn.execute(
                        sa.insert(t.backup_playlists).values(
                            entry_id=entry.entry_id, **values
                        )
                    )
        except IntegrityError as exc:
            raise ValueError(
                f"{entry.service}/{entry.playlist_id} already has a backup entry"
            ) from exc

    def get_entry(self, entry_id: str) -> BackedUpPlaylist | None:
        with self._engine.begin() as conn:
            body = conn.execute(
                sa.select(t.backup_playlists.c.body).where(
                    t.backup_playlists.c.entry_id == entry_id
                )
            ).scalar_one_or_none()
        return None if body is None else loads(BackedUpPlaylist, body)

    def find_entry(
        self, service: ServiceId, playlist_id: str
    ) -> BackedUpPlaylist | None:
        with self._engine.begin() as conn:
            body = conn.execute(
                sa.select(t.backup_playlists.c.body).where(
                    t.backup_playlists.c.service == service,
                    t.backup_playlists.c.playlist_id == playlist_id,
                )
            ).scalar_one_or_none()
        return None if body is None else loads(BackedUpPlaylist, body)

    def list_entries(
        self, service: ServiceId | None = None, *, archived: bool | None = None
    ) -> list[BackedUpPlaylist]:
        query = sa.select(t.backup_playlists.c.body)
        if service is not None:
            query = query.where(t.backup_playlists.c.service == service)
        if archived is True:
            query = query.where(t.backup_playlists.c.archived_at.is_not(None))
        elif archived is False:
            query = query.where(t.backup_playlists.c.archived_at.is_(None))
        with self._engine.begin() as conn:
            found = [loads(BackedUpPlaylist, b) for b in conn.execute(query).scalars()]
        found.sort(key=lambda e: (e.name.casefold(), e.entry_id))
        return found

    def delete_entry(self, entry_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.delete(t.backup_versions).where(
                    t.backup_versions.c.entry_id == entry_id
                )
            )
            conn.execute(
                sa.delete(t.backup_playlists).where(
                    t.backup_playlists.c.entry_id == entry_id
                )
            )

    def add_version(self, version: PlaylistVersion) -> None:
        body = dumps(PlaylistVersion, version)
        with self._engine.begin() as conn:
            conn.execute(
                sa.delete(t.backup_versions).where(
                    t.backup_versions.c.version_id == version.version_id
                )
            )
            seq = conn.execute(
                sa.select(sa.func.coalesce(sa.func.max(t.backup_versions.c.seq), 0))
            ).scalar_one()
            conn.execute(
                sa.insert(t.backup_versions).values(
                    version_id=version.version_id,
                    entry_id=version.entry_id,
                    seq=seq + 1,
                    cover_sha256=version.cover_sha256,
                    body=body,
                )
            )

    def get_version(self, version_id: str) -> PlaylistVersion | None:
        with self._engine.begin() as conn:
            body = conn.execute(
                sa.select(t.backup_versions.c.body).where(
                    t.backup_versions.c.version_id == version_id
                )
            ).scalar_one_or_none()
        return None if body is None else loads(PlaylistVersion, body)

    def list_versions(self, entry_id: str) -> list[PlaylistVersion]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                sa.select(t.backup_versions.c.body)
                .where(t.backup_versions.c.entry_id == entry_id)
                .order_by(t.backup_versions.c.seq.desc())
            ).scalars()
            return [loads(PlaylistVersion, b) for b in rows]

    def delete_version(self, version_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.delete(t.backup_versions).where(
                    t.backup_versions.c.version_id == version_id
                )
            )

    def save_run(self, run: BackupRun) -> None:
        values = {
            "backup_id": run.backup_id,
            "service": str(run.service),
            "started_at": utc_text(run.started_at),
            "body": dumps(BackupRun, run),
        }
        # OR REPLACE gives a re-saved run a new rowid: last saved sorts first.
        with self._engine.begin() as conn:
            conn.execute(sa.insert(t.backup_runs).prefix_with("OR REPLACE"), values)

    def get_run(self, backup_id: str) -> BackupRun | None:
        with self._engine.begin() as conn:
            body = conn.execute(
                sa.select(t.backup_runs.c.body).where(
                    t.backup_runs.c.backup_id == backup_id
                )
            ).scalar_one_or_none()
        return None if body is None else loads(BackupRun, body)

    def list_runs(
        self, service: ServiceId | None = None, limit: int | None = None
    ) -> list[BackupRun]:
        query = sa.select(t.backup_runs.c.body).order_by(
            t.backup_runs.c.started_at.desc(), sa.text("backup_runs.rowid DESC")
        )
        if service is not None:
            query = query.where(t.backup_runs.c.service == service)
        if limit is not None:
            query = query.limit(limit)
        with self._engine.begin() as conn:
            return [loads(BackupRun, b) for b in conn.execute(query).scalars()]

    def delete_service(self, service: ServiceId) -> None:
        with self._engine.begin() as conn:
            entry_ids = sa.select(t.backup_playlists.c.entry_id).where(
                t.backup_playlists.c.service == service
            )
            conn.execute(
                sa.delete(t.backup_versions).where(
                    t.backup_versions.c.entry_id.in_(entry_ids)
                )
            )
            conn.execute(
                sa.delete(t.backup_playlists).where(
                    t.backup_playlists.c.service == service
                )
            )
            conn.execute(
                sa.delete(t.backup_runs).where(t.backup_runs.c.service == service)
            )

    def put_cover(self, cover: CoverImage) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                sa.insert(t.covers).prefix_with("OR IGNORE"),
                {
                    "sha256": cover.sha256,
                    "media_type": cover.media_type,
                    "data": cover.data,
                },
            )

    def get_cover(self, sha256: str) -> CoverImage | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(t.covers.c.media_type, t.covers.c.data).where(
                    t.covers.c.sha256 == sha256
                )
            ).first()
        return None if row is None else CoverImage(sha256, row.media_type, row.data)

    def prune_covers(self) -> int:
        used = sa.select(t.backup_versions.c.cover_sha256).where(
            t.backup_versions.c.cover_sha256.is_not(None)
        )
        with self._engine.begin() as conn:
            return conn.execute(
                sa.delete(t.covers).where(t.covers.c.sha256.not_in(used))
            ).rowcount
