from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from linersync.application.runner import Deps, SyncRunner, partly_read
from linersync.domain.capabilities import Capability
from linersync.domain.operations import (
    AddTracks,
    ChangeNotMade,
    CreatePlaylist,
    MemberPlan,
)
from linersync.domain.planning.diff import diff_ordered
from linersync.domain.planning.planner import plan_member
from linersync.domain.playlists import PlaylistSnapshot
from linersync.domain.runs import (
    MemberResult,
    MemberStatus,
    RunEvent,
    RunKind,
    RunStatus,
    SyncRun,
    Trigger,
)
from linersync.domain.sync import (
    SOURCE_MEMBER_ID,
    Direction,
    MemberRole,
    PriorState,
    SyncGroup,
    SyncMember,
    SyncMode,
    effective_mode,
)
from linersync.domain.tracks import ServiceId
from linersync.ports.connector import DeletesPlaylists
from linersync.ports.errors import ConnectorError, PlaylistNotFound
from linersync.ports.repositories import SnapshotRepository

_UNDOABLE_KINDS = frozenset({RunKind.SYNC, RunKind.TRANSFER})
_WRITING_KINDS = frozenset({RunKind.SYNC, RunKind.TRANSFER, RunKind.UNDO})
_CHANGED_SINCE = "playlist changed since this run — undo newer runs first"
_REMOVE_BY_HAND = (
    "added by the run being undone, but this service can't remove songs; "
    "remove it by hand"
)
_CREATED = ChangeNotMade(
    "delete", None, "Delete the created playlist by hand if you don't want it"
)


class UndoNotAllowed(Exception):
    pass


class UndoAction(StrEnum):
    RESTORE = "restore"  # put the playlist back as it was before the run
    DELETE_PLAYLIST = "delete_playlist"  # the run created it
    REMOVE_SONGS = "remove_songs"  # the run added them; the service can't remove


@dataclass(frozen=True, slots=True)
class UndoStep:
    """What undoing a run means for one playlist, and whether LinerSync can
    do it (`by_hand` False) or the user must."""

    member_id: str
    action: UndoAction
    by_hand: bool
    playlist_name: str
    service: ServiceId | None  # None: unknown (a created playlist's service)
    track_ids: tuple[str, ...] = ()  # REMOVE_SONGS: the songs, repeats included


Capabilities = Callable[[ServiceId], frozenset[Capability]]


def undo_steps(
    run: SyncRun,
    snapshots: SnapshotRepository,
    capabilities: Capabilities,
    services: Mapping[str, ServiceId] | None = None,
) -> tuple[UndoStep, ...]:
    """One step per playlist the run wrote to. A playlist the run created is
    deleted, by LinerSync where its service can delete playlists, else by
    hand. On a service that can't remove songs, removing what the run added is
    by hand. `services` maps member ids to services, for created playlists."""
    if run.kind not in _UNDOABLE_KINDS or run.dry_run:
        return ()
    plans = {p.member_id: p for p in run.plan.member_plans} if run.plan else {}
    steps: list[UndoStep] = []
    for m in run.members:
        mid = m.member_id
        if not snapshots.has_pre_write(run.run_id, mid):
            continue
        snap = snapshots.get_pre_write(run.run_id, mid)
        plan = plans.get(mid)
        if snap is None:
            service_ = (services or {}).get(mid)
            can_delete = (
                service_ is not None
                and Capability.DELETE_PLAYLIST in capabilities(service_)
                and m.created_playlist_id is not None
                and m.verified_after is not None  # else changes can't be seen
            )
            steps.append(
                UndoStep(
                    mid,
                    UndoAction.DELETE_PLAYLIST,
                    not can_delete,
                    _created_name(plan),
                    service_,
                )
            )
            continue
        service = snap.meta.service
        name = snap.meta.name
        if Capability.REMOVE in capabilities(service):
            steps.append(UndoStep(mid, UndoAction.RESTORE, False, name, service))
            continue
        after = m.verified_after
        if after is None:
            added = (
                ()
                if plan is None
                else tuple(
                    t
                    for op in plan.operations
                    if isinstance(op, AddTracks)
                    for t in op.track_ids
                )
            )
            after = snap.track_ids + added
        cur = list(after)
        extra = tuple(cur[i] for i in diff_ordered(cur, snap.track_ids).to_remove)
        if extra:
            steps.append(
                UndoStep(mid, UndoAction.REMOVE_SONGS, True, name, service, extra)
            )
    return tuple(steps)


def can_undo(
    run: SyncRun,
    snapshots: SnapshotRepository,
    capabilities: Capabilities,
    services: Mapping[str, ServiceId] | None = None,
) -> bool:
    """Some step of undoing the run is one LinerSync can do itself."""
    steps = undo_steps(run, snapshots, capabilities, services)
    return any(not s.by_hand for s in steps)


def _created_name(plan: MemberPlan | None) -> str:
    ops = () if plan is None else plan.operations
    return next((op.draft.name for op in ops if isinstance(op, CreatePlaylist)), "")


class UndoService:
    """Restores a run's destinations to their pre-write snapshots. An explicit
    user action on a known state: no brake, and the circuit breaker is neither
    consulted nor fed. A destination restored in full also gets back the sync
    memory it had before the run (`last_synced`, `mirror_once`), so the next run
    re-applies the source instead of reading the undo as the user's edits."""

    def __init__(
        self,
        deps: Deps,
        runner: SyncRunner,
        services: Mapping[str, ServiceId] | None = None,
    ) -> None:
        """`services`: member id → service for the run's members (its run
        context); a saved sync's own members fill in the rest."""
        self._deps = deps
        self._runner = runner
        self._services = dict(services or {})

    async def undo(self, run_id: str) -> SyncRun:
        """Raises KeyError for an unknown run, UndoNotAllowed unless
        `can_undo`, whatever the run's status. A member changed since the run
        (by a later run or by hand) is not restored: it fails, untouched."""
        original = self._deps.repos.runs.get(run_id)
        if original is None:
            raise KeyError(run_id)
        snapshots = self._deps.repos.snapshots
        saved = self._deps.repos.syncs.get(original.sync_id)
        services = {m.member_id: m.service for m in saved.members} if saved else {}
        services.update(self._services)
        steps = undo_steps(original, snapshots, self._capabilities, services)
        if not any(not s.by_hand for s in steps):  # see can_undo
            raise UndoNotAllowed(
                f"run {run_id} ({original.kind}, {original.status}"
                f"{', dry run' if original.dry_run else ''}) can't be undone"
            )
        undo_id = self._deps.new_id()
        started_at = self._deps.clock.now()
        deletable = {
            s.member_id
            for s in steps
            if s.action is UndoAction.DELETE_PLAYLIST and not s.by_hand
        }
        results: list[MemberResult] = []
        memories: dict[str, tuple[PriorState, str | None]] = {}
        for m in original.members:
            if not snapshots.has_pre_write(run_id, m.member_id):
                continue  # never written
            snap = snapshots.get_pre_write(run_id, m.member_id)
            if snap is None and m.member_id in deletable:
                results.append(
                    await self._delete_created(original, m, services[m.member_id])
                )
                continue
            if snap is None:
                results.append(
                    MemberResult(
                        m.member_id, MemberStatus.NOTHING_TO_DO, not_made=(_CREATED,)
                    )
                )
                continue
            if m.verified_after is None and self._written_later(original, m.member_id):
                results.append(_changed(m.member_id))
                continue
            group, member = _target(original.sync_id, saved, m.member_id, snap)
            result = await self._undo_member(
                undo_id, group, member, snap, m.verified_after
            )
            results.append(result)
            prior = snapshots.get_prior_state(run_id, m.member_id)
            if (
                prior is not None
                and _restored(result)
                and not self._kept_by_later_run(original, m.member_id)
            ):
                memories[m.member_id] = (prior, snap.meta.playlist_id)
        self._restore_memories(original.sync_id, memories)

        members = tuple(results)
        status = _undo_status(members)
        run = SyncRun(
            run_id=undo_id,
            sync_id=original.sync_id,
            kind=RunKind.UNDO,
            trigger=Trigger.MANUAL,
            dry_run=False,
            status=status,
            started_at=started_at,
            finished_at=self._deps.clock.now(),
            members=members,
            undoes_run_id=run_id,
        )
        self._deps.repos.runs.save(run)
        self._deps.events.publish(
            RunEvent(undo_id, original.sync_id, "done", 1, 1, str(status))
        )
        return run

    async def _delete_created(
        self, original: SyncRun, m: MemberResult, service: ServiceId
    ) -> MemberResult:
        """Delete the playlist the run created, unless it changed since the run
        (by a later run or by hand): then it is kept, untouched."""
        mid = m.member_id
        connector = self._deps.connectors[service]
        assert isinstance(connector, DeletesPlaylists)
        assert m.created_playlist_id is not None and m.verified_after is not None
        if self._written_later(original, mid):
            return _changed(mid)
        try:
            current = await connector.read_playlist(m.created_playlist_id)
        except PlaylistNotFound:
            return MemberResult(mid, MemberStatus.NOTHING_TO_DO)  # already gone
        except ConnectorError as e:
            return _unreadable(mid, f"couldn't read this playlist: {e}")
        partial = partly_read(current, "this playlist")
        if partial is not None:
            return _unreadable(mid, partial)
        if current.track_ids != m.verified_after:
            return _changed(mid)
        try:
            await connector.delete_playlist(m.created_playlist_id)
        except ConnectorError as e:
            return MemberResult(
                mid, MemberStatus.FAILED, error=f"couldn't delete the playlist: {e}"
            )
        return MemberResult(mid, MemberStatus.APPLIED)

    def _capabilities(self, service: ServiceId) -> frozenset[Capability]:
        connector = self._deps.connectors.get(service)
        return frozenset() if connector is None else connector.capabilities

    def _written_later(self, original: SyncRun, member_id: str) -> bool:
        """A later non-dry run of the same sync wrote to this member. Newer runs
        come first in `list_for_sync`."""
        snapshots = self._deps.repos.snapshots
        for run in self._deps.repos.runs.list_for_sync(original.sync_id):
            if run.run_id == original.run_id:
                return False
            if (
                run.kind in _WRITING_KINDS
                and not run.dry_run
                and snapshots.has_pre_write(run.run_id, member_id)
            ):
                return True
        return False

    def _kept_by_later_run(self, original: SyncRun, member_id: str) -> bool:
        """A later sync or transfer run wrote to this member and wasn't undone
        since: its memory must not be rolled back past it."""
        snapshots = self._deps.repos.snapshots
        undone: set[str] = set()
        for run in self._deps.repos.runs.list_for_sync(original.sync_id):
            if run.run_id == original.run_id:
                return False
            if run.kind is RunKind.UNDO:
                mine = [r for r in run.members if r.member_id == member_id]
                if run.undoes_run_id is not None and all(map(_restored, mine)):
                    undone.add(run.undoes_run_id)
                continue
            if (
                run.kind in _UNDOABLE_KINDS
                and not run.dry_run
                and run.run_id not in undone
                and snapshots.has_pre_write(run.run_id, member_id)
            ):
                return True
        return False

    def _restore_memories(
        self, sync_id: str, memories: dict[str, tuple[PriorState, str | None]]
    ) -> None:
        """Gives each restored member back its pre-run memory, unless it has
        since been pointed at another playlist."""
        group = self._deps.repos.syncs.get(sync_id)
        if group is None or not memories:
            return
        updated = group
        for m in group.members:
            if m.member_id not in memories:
                continue
            prior, playlist_id = memories[m.member_id]
            if m.playlist_id != playlist_id:
                continue
            updated = updated.with_member(
                replace(m, last_synced=prior.last_synced, mirror_once=prior.mirror_once)
            )
        if updated != group:
            self._deps.repos.syncs.save(updated)

    async def _undo_member(
        self,
        undo_id: str,
        group: SyncGroup,
        member: SyncMember,
        snap: PlaylistSnapshot,
        verified_after: tuple[str, ...] | None,
    ) -> MemberResult:
        mid = member.member_id
        connector = self._deps.connectors[member.service]
        assert member.playlist_id is not None
        try:
            current = await connector.read_playlist(member.playlist_id)
        except ConnectorError as e:
            return _unreadable(mid, f"couldn't read this playlist: {e}")
        partial = partly_read(current, "this playlist")
        if partial is not None:
            return _unreadable(mid, partial)
        if verified_after is not None and current.track_ids != verified_after:
            return _changed(mid)
        caps = connector.capabilities
        # Undo restores the snapshot exactly: a strict mirror.
        plan = plan_member(
            mid,
            current,
            snap.track_ids,
            snap.meta.draft(),
            caps,
            SyncMode.FULL,
            mirror=True,
        )
        if effective_mode(SyncMode.FULL, caps) is SyncMode.ADD_ONLY:
            # The planner's add-only flags work on track ids (a set); undo must
            # list every surplus copy, repeats included.
            cur = current.track_ids
            surplus = diff_ordered(cur, snap.track_ids).to_remove
            fixes = tuple(
                ChangeNotMade("remove", cur[i], _REMOVE_BY_HAND) for i in surplus
            )
            plan = replace(plan, not_made=plan.not_made + fixes)
        if not plan.operations:
            return MemberResult(mid, MemberStatus.NOTHING_TO_DO, not_made=plan.not_made)
        self._deps.repos.snapshots.save_pre_write(undo_id, mid, current)
        return await self._runner.apply_member_plan(
            undo_id, group, member, plan, current
        )


def _target(
    sync_id: str, saved: SyncGroup | None, member_id: str, snap: PlaylistSnapshot
) -> tuple[SyncGroup, SyncMember]:
    """The member to restore, pointed at the playlist the snapshot was taken of.
    Without a saved group (a transfer) it is synthesised from the snapshot."""
    meta = snap.meta
    found = None
    if saved is not None:
        found = next((m for m in saved.members if m.member_id == member_id), None)
    if saved is not None and found is not None:
        return saved, replace(found, service=meta.service, playlist_id=meta.playlist_id)
    member = SyncMember(
        member_id, meta.service, meta.playlist_id, MemberRole.DESTINATION
    )
    source = SyncMember(
        SOURCE_MEMBER_ID if member_id != SOURCE_MEMBER_ID else "src0",
        meta.service,
        None,
        MemberRole.SOURCE,
    )
    group = SyncGroup(sync_id, meta.name, Direction.ONE_WAY, (source, member))
    return group, member


def _restored(result: MemberResult) -> bool:
    """The member now matches its pre-write snapshot (as far as it can)."""
    ok = (MemberStatus.APPLIED, MemberStatus.NOTHING_TO_DO)
    return result.status in ok and result.mismatch is None


def _changed(member_id: str) -> MemberResult:
    return MemberResult(member_id, MemberStatus.FAILED, error=_CHANGED_SINCE)


def _unreadable(member_id: str, error: str) -> MemberResult:
    return MemberResult(member_id, MemberStatus.SKIPPED_UNREADABLE, error=error)


def _undo_status(results: tuple[MemberResult, ...]) -> RunStatus:
    statuses = [r.status for r in results]
    if MemberStatus.APPLIED not in statuses and MemberStatus.FAILED in statuses:
        return RunStatus.FAILED
    ok = {MemberStatus.APPLIED, MemberStatus.NOTHING_TO_DO}
    if any(r.not_made or r.mismatch or r.status not in ok for r in results):
        return RunStatus.PARTIAL
    return RunStatus.SUCCEEDED
