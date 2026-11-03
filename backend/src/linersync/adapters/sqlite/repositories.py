"""SQLite implementations of the repository ports (SQLAlchemy Core)."""

from dataclasses import replace
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Engine

from linersync.adapters.sqlite import schema as t
from linersync.adapters.sqlite.codec import dumps, loads
from linersync.adapters.sqlite.db import (
    load_all,
    load_one,
    upsert,
    utc_text,
    utc_text_or_none,
)
from linersync.domain.attention import AttentionItem
from linersync.domain.matching.types import RememberedMatch
from linersync.domain.playlists import PlaylistSnapshot
from linersync.domain.runs import RunStatus, SyncRun
from linersync.domain.sync import PriorState, SyncGroup
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.repositories import BreakerState, Repositories

_SEP = "\x1f"


class SqliteSyncs:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, sync_id: str) -> SyncGroup | None:
        query = sa.select(t.syncs.c.body).where(t.syncs.c.sync_id == sync_id)
        return load_one(self._engine, SyncGroup, query)

    def save(self, group: SyncGroup) -> None:
        with self._engine.begin() as conn:
            seq = conn.execute(
                sa.select(sa.func.coalesce(sa.func.max(t.syncs.c.seq), 0))
            ).scalar_one()
            # seq is creation order: a re-saved sync keeps its place.
            upsert(
                conn,
                t.syncs,
                {"sync_id": group.sync_id},
                {"body": dumps(SyncGroup, group)},
                insert_only={"seq": seq + 1},
            )

    def list(self) -> list[SyncGroup]:
        query = sa.select(t.syncs.c.body).order_by(t.syncs.c.seq)
        return load_all(self._engine, SyncGroup, query)

    def delete(self, sync_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(sa.delete(t.syncs).where(t.syncs.c.sync_id == sync_id))


class SqliteRuns:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save(self, run: SyncRun) -> None:
        values = {
            "sync_id": run.sync_id,
            "status": str(run.status),
            "started_at": utc_text(run.started_at),
            "body": dumps(SyncRun, run),
        }
        with self._engine.begin() as conn:
            seq = conn.execute(
                sa.select(sa.func.coalesce(sa.func.max(t.runs.c.saved_seq), 0))
            ).scalar_one()
            values["saved_seq"] = seq + 1  # every save moves the run last
            upsert(conn, t.runs, {"run_id": run.run_id}, values)

    def get(self, run_id: str) -> SyncRun | None:
        query = sa.select(t.runs.c.body).where(t.runs.c.run_id == run_id)
        return load_one(self._engine, SyncRun, query)

    def list_for_sync(self, sync_id: str, limit: int | None = None) -> list[SyncRun]:
        query = (
            sa.select(t.runs.c.body)
            .where(t.runs.c.sync_id == sync_id)
            .order_by(t.runs.c.started_at.desc(), t.runs.c.saved_seq.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        return load_all(self._engine, SyncRun, query)

    def list_all(self) -> list[SyncRun]:
        query = sa.select(t.runs.c.body).order_by(t.runs.c.saved_seq)
        return load_all(self._engine, SyncRun, query)

    def list_since(self, since: datetime) -> list[SyncRun]:
        query = (
            sa.select(t.runs.c.body)
            .where(t.runs.c.started_at >= utc_text(since))
            .order_by(t.runs.c.saved_seq)
        )
        return load_all(self._engine, SyncRun, query)

    def list_running(self) -> list[SyncRun]:
        query = sa.select(t.runs.c.body).where(
            t.runs.c.status == str(RunStatus.RUNNING)
        )
        return load_all(self._engine, SyncRun, query)


class SqliteSnapshots:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save_pre_write(
        self,
        run_id: str,
        member_id: str,
        snap: PlaylistSnapshot | None,
        prior: PriorState | None = None,
    ) -> None:
        body = None if snap is None else dumps(PlaylistSnapshot, snap)
        kept = None if prior is None else dumps(PriorState, prior)
        key = {"run_id": run_id, "member_id": member_id}
        with self._engine.begin() as conn:
            upsert(conn, t.pre_write_snapshots, key, {"body": body, "prior": kept})

    def get_pre_write(self, run_id: str, member_id: str) -> PlaylistSnapshot | None:
        query = sa.select(t.pre_write_snapshots.c.body).where(
            t.pre_write_snapshots.c.run_id == run_id,
            t.pre_write_snapshots.c.member_id == member_id,
        )
        return load_one(self._engine, PlaylistSnapshot, query)

    def has_pre_write(self, run_id: str, member_id: str) -> bool:
        with self._engine.begin() as conn:
            found = conn.execute(
                sa.select(sa.literal(1)).where(
                    t.pre_write_snapshots.c.run_id == run_id,
                    t.pre_write_snapshots.c.member_id == member_id,
                )
            ).first()
        return found is not None

    def get_prior_state(self, run_id: str, member_id: str) -> PriorState | None:
        query = sa.select(t.pre_write_snapshots.c.prior).where(
            t.pre_write_snapshots.c.run_id == run_id,
            t.pre_write_snapshots.c.member_id == member_id,
        )
        return load_one(self._engine, PriorState, query)


class SqliteMatches:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(
        self, source_key: TrackKey, target_service: ServiceId
    ) -> RememberedMatch | None:
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(t.remembered_matches.c.target_track_id).where(
                    t.remembered_matches.c.source_service == source_key.service,
                    t.remembered_matches.c.source_track_id == source_key.track_id,
                    t.remembered_matches.c.target_service == target_service,
                )
            ).first()
        if row is None:
            return None
        return RememberedMatch(source_key, target_service, row[0])

    def remember(self, match: RememberedMatch) -> None:
        keys = {
            "source_service": str(match.source_key.service),
            "source_track_id": match.source_key.track_id,
            "target_service": str(match.target_service),
        }
        values = {"target_track_id": match.target_track_id}
        with self._engine.begin() as conn:
            upsert(conn, t.remembered_matches, keys, values)

    def list_all(self) -> list[RememberedMatch]:
        with self._engine.begin() as conn:
            rows = conn.execute(sa.select(t.remembered_matches)).all()
        return [
            RememberedMatch(
                TrackKey(ServiceId(r.source_service), r.source_track_id),
                ServiceId(r.target_service),
                r.target_track_id,
            )
            for r in rows
        ]


class SqliteAttention:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def upsert(self, item: AttentionItem) -> AttentionItem:
        key = _SEP.join(item.dedupe_key)
        with self._engine.begin() as conn:
            existing = conn.execute(
                sa.select(t.attention.c.item_id, t.attention.c.body).where(
                    t.attention.c.dedupe_key == key,
                    t.attention.c.resolved_at.is_(None),
                )
            ).first()
            if existing is not None:
                old = loads(AttentionItem, existing.body)
                stored = replace(item, item_id=old.item_id, created_at=old.created_at)
                conn.execute(
                    sa.update(t.attention)
                    .where(t.attention.c.item_id == old.item_id)
                    .values(
                        dedupe_key=key,
                        resolved_at=utc_text_or_none(stored.resolved_at),
                        created_at=utc_text(stored.created_at),
                        body=dumps(AttentionItem, stored),
                    )
                )
                return stored
            conn.execute(
                sa.insert(t.attention).values(
                    item_id=item.item_id,
                    dedupe_key=key,
                    resolved_at=utc_text_or_none(item.resolved_at),
                    created_at=utc_text(item.created_at),
                    body=dumps(AttentionItem, item),
                )
            )
            return item

    def get(self, item_id: str) -> AttentionItem | None:
        query = sa.select(t.attention.c.body).where(t.attention.c.item_id == item_id)
        return load_one(self._engine, AttentionItem, query)

    def list_open(self) -> list[AttentionItem]:
        query = (
            sa.select(t.attention.c.body)
            .where(t.attention.c.resolved_at.is_(None))
            .order_by(sa.literal_column("rowid"))
        )
        return load_all(self._engine, AttentionItem, query)

    def resolve(self, item_id: str, resolution: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            body = conn.execute(
                sa.select(t.attention.c.body).where(t.attention.c.item_id == item_id)
            ).scalar_one_or_none()
            if body is None:
                raise KeyError(item_id)
            item = replace(
                loads(AttentionItem, body), resolved_at=at, resolution=resolution
            )
            conn.execute(
                sa.update(t.attention)
                .where(t.attention.c.item_id == item_id)
                .values(resolved_at=utc_text(at), body=dumps(AttentionItem, item))
            )


class SqliteBreakers:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, service: ServiceId) -> BreakerState:
        query = sa.select(t.breakers.c.body).where(t.breakers.c.service == service)
        state = load_one(self._engine, BreakerState, query)
        return BreakerState(service) if state is None else state

    def save(self, state: BreakerState) -> None:
        body = dumps(BreakerState, state)
        with self._engine.begin() as conn:
            upsert(conn, t.breakers, {"service": state.service}, {"body": body})


class SqliteApprovals:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _where(sync_id: str, member_id: str, fp: str) -> sa.ColumnElement[bool]:
        return sa.and_(
            t.approvals.c.sync_id == sync_id,
            t.approvals.c.member_id == member_id,
            t.approvals.c.fingerprint == fp,
        )

    def approve(self, sync_id: str, member_id: str, fp: str) -> None:
        with self._engine.begin() as conn:
            if conn.execute(
                sa.select(sa.literal(1)).where(self._where(sync_id, member_id, fp))
            ).first():
                return
            conn.execute(
                sa.insert(t.approvals).values(
                    sync_id=sync_id, member_id=member_id, fingerprint=fp
                )
            )

    def is_approved(self, sync_id: str, member_id: str, fp: str) -> bool:
        with self._engine.begin() as conn:
            found = conn.execute(
                sa.select(sa.literal(1)).where(self._where(sync_id, member_id, fp))
            ).first()
        return found is not None

    def consume(self, sync_id: str, member_id: str, fp: str) -> bool:
        with self._engine.begin() as conn:
            deleted = conn.execute(
                sa.delete(t.approvals).where(self._where(sync_id, member_id, fp))
            ).rowcount
        return deleted == 1


def sqlite_repositories(engine: Engine) -> Repositories:
    return Repositories(
        syncs=SqliteSyncs(engine),
        runs=SqliteRuns(engine),
        snapshots=SqliteSnapshots(engine),
        matches=SqliteMatches(engine),
        attention=SqliteAttention(engine),
        breakers=SqliteBreakers(engine),
        approvals=SqliteApprovals(engine),
    )
