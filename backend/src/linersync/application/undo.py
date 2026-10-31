from dataclasses import replace

from linersync.application.runner import Deps, SyncRunner
from linersync.domain.operations import ChangeNotMade
from linersync.domain.planning.diff import diff_ordered
from linersync.domain.planning.planner import plan_member
from linersync.domain.playlists import PlaylistDraft, PlaylistSnapshot
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
    Direction,
    MemberRole,
    SyncGroup,
    SyncMember,
    SyncMode,
    effective_mode,
)
from linersync.ports.errors import ConnectorError

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


class UndoService:
    """Restores a run's destinations to their pre-write snapshots. An explicit
    user action on a known state: no brake, and the circuit breaker is neither
    consulted nor fed. Leaves `last_synced` and the saved group untouched."""

    def __init__(self, deps: Deps, runner: SyncRunner) -> None:
        self._deps = deps
        self._runner = runner

    async def undo(self, run_id: str) -> SyncRun:
        """Raises KeyError for an unknown run, UndoNotAllowed if not undoable:
        only a sync or transfer run that wrote (a pre-write snapshot exists for
        a member), whatever its status. A member changed since the run (by a
        later run or by hand) is not restored: it fails, untouched."""
        original = self._deps.repos.runs.get(run_id)
        if original is None:
            raise KeyError(run_id)
        snapshots = self._deps.repos.snapshots
        wrote = any(
            snapshots.has_pre_write(run_id, m.member_id) for m in original.members
        )
        if original.kind not in _UNDOABLE_KINDS or original.dry_run or not wrote:
            raise UndoNotAllowed(
                f"run {run_id} ({original.kind}, {original.status}"
                f"{', dry run' if original.dry_run else ''}) can't be undone"
            )
        undo_id = self._deps.new_id()
        started_at = self._deps.clock.now()
        saved = self._deps.repos.syncs.get(original.sync_id)
        results: list[MemberResult] = []
        for m in original.members:
            if not snapshots.has_pre_write(run_id, m.member_id):
                continue  # never written
            snap = snapshots.get_pre_write(run_id, m.member_id)
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
            results.append(
                await self._undo_member(undo_id, group, member, snap, m.verified_after)
            )

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
        if not current.is_complete:
            return _unreadable(
                mid,
                f"this playlist was only partly read {current.shortfall()}",
            )
        if verified_after is not None and current.track_ids != verified_after:
            return _changed(mid)
        meta = snap.meta
        caps = connector.capabilities
        plan = plan_member(
            mid,
            current,
            snap.track_ids,
            PlaylistDraft(meta.name, meta.description, meta.cover_url),
            caps,
            SyncMode.FULL,
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
        "src" if member_id != "src" else "src0", meta.service, None, MemberRole.SOURCE
    )
    group = SyncGroup(sync_id, meta.name, Direction.ONE_WAY, (source, member))
    return group, member


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
