"""Copy to source: songs the user added on a destination go into the source
playlist once, paired with the destination copy they came from."""

from collections.abc import Callable, Collection, Mapping
from dataclasses import replace

from linersync.application.matching_service import MatchService
from linersync.application.runner import Deps, SyncRunner, partly_read
from linersync.domain.matching.types import (
    Matched,
    NeedsAttention,
    NotOnService,
    RememberedMatch,
)
from linersync.domain.operations import (
    AddTracks,
    ChangeNotMade,
    MemberPlan,
    Operation,
    SyncPlan,
    UnresolvedEntry,
)
from linersync.domain.playlists import PlaylistEntry, PlaylistSnapshot
from linersync.domain.runs import (
    MemberResult,
    MemberStatus,
    RunEvent,
    RunKind,
    RunPhase,
    RunStatus,
    SyncRun,
    Trigger,
)
from linersync.domain.sync import SyncedEntry, SyncGroup, SyncMember
from linersync.domain.tracks import ServiceId, Track, TrackKey
from linersync.ports.connector import Connector
from linersync.ports.errors import ConnectorError, ReadFailed

RunListener = Callable[[SyncRun], None]


class CopyBackService:
    def __init__(self, deps: Deps, runner: SyncRunner) -> None:
        self._deps = deps
        self._runner = runner
        self._matcher = MatchService(deps.repos.matches, deps.config.match)

    async def copy(
        self,
        group: SyncGroup,
        member_id: str,
        track_ids: Collection[str],
        *,
        dry_run: bool,
        seed: Mapping[ServiceId, Mapping[TrackKey, str]] | None,
        on_run: RunListener = lambda run: None,
    ) -> SyncRun:
        """Plan (and unless `dry_run`, make) the copy of the chosen songs the
        user added on destination `member_id` (none chosen = all of them) into
        the source. After a real copy the pairs are saved and the sync runs, so
        the other destinations get the songs. `on_run` hears of each finished
        run: the copy, then the sync. Raises KeyError for an unknown member."""
        dest = next((m for m in group.destinations if m.member_id == member_id), None)
        if dest is None:
            raise KeyError(member_id)
        src = group.source
        source_conn = self._deps.connectors[src.service]
        run_id = self._deps.new_id()
        started_at = self._deps.clock.now()

        source = await _read(source_conn, src.playlist_id, "the source playlist")
        current = await _read(
            self._deps.connectors[dest.service], dest.playlist_id, "this playlist"
        )
        self._event(run_id, group, "read", "read the playlists")
        chosen = _chosen(dest, current, track_ids)
        decisions = await self._matcher.resolve(
            replace(
                current,
                entries=tuple(PlaylistEntry(t) for t in chosen),
                expected_total=len(chosen),
            ),
            source_conn,
            None,
            (seed or {}).get(src.service, {}),
        )
        self._event(run_id, group, "match", f"matched for {src.service}")

        ops: list[Operation] = []
        added: list[str] = []
        synced: list[SyncedEntry] = []
        not_made: list[ChangeNotMade] = []
        unresolved: list[UnresolvedEntry] = []
        in_source = set(source.track_ids)
        for position, t in enumerate(chosen):
            match decisions[t.key]:
                case Matched(target_track_id=tid):
                    synced.append(SyncedEntry(t.key, tid))
                    if tid not in in_source and tid not in added:
                        added.append(tid)
                case NeedsAttention(candidates=candidates) as decision if candidates:
                    # Ambiguous: the person picks the right version.
                    unresolved.append(
                        UnresolvedEntry(src.member_id, t, position, decision)
                    )
                case NeedsAttention() | NotOnService():
                    detail = f"not found on {source_conn.display_name}"
                    not_made.append(ChangeNotMade("add", t.track_id, detail))
        if added:
            ops.append(AddTracks(f"{src.member_id}:0", tuple(added)))
        plan = MemberPlan(
            src.member_id,
            tuple(ops),
            tuple(not_made),
            source.track_ids + tuple(added),
            tuple(synced),
            source.meta.draft(),
        )
        sync_plan = SyncPlan(group.sync_id, (plan,), tuple(unresolved))

        def finish(status: RunStatus, result: MemberResult) -> SyncRun:
            run = SyncRun(
                run_id=run_id,
                sync_id=group.sync_id,
                kind=RunKind.COPY_TO_SOURCE,
                trigger=Trigger.MANUAL,
                dry_run=dry_run,
                status=status,
                started_at=started_at,
                finished_at=self._deps.clock.now(),
                plan=sync_plan,
                members=(result,),
            )
            self._deps.repos.runs.save(run)
            self._event(run_id, group, "done", str(status))
            on_run(run)
            return run

        changes = bool(plan.operations or plan.not_made)
        if dry_run:
            status = MemberStatus.DRY_RUN if changes else MemberStatus.NOTHING_TO_DO
            result = MemberResult(src.member_id, status, not_made=plan.not_made)
            return finish(RunStatus.SUCCEEDED, result)

        if plan.operations:
            # Saved before the write, like a sync run.
            self._deps.repos.runs.save(
                SyncRun(
                    run_id=run_id,
                    sync_id=group.sync_id,
                    kind=RunKind.COPY_TO_SOURCE,
                    trigger=Trigger.MANUAL,
                    dry_run=False,
                    status=RunStatus.RUNNING,
                    started_at=started_at,
                    plan=sync_plan,
                )
            )
            result = await self._runner.apply_member_plan(
                run_id, group, src, plan, source
            )
        else:
            result = MemberResult(
                src.member_id, MemberStatus.NOTHING_TO_DO, not_made=plan.not_made
            )
        ok = result.mismatch is None and result.status in (
            MemberStatus.APPLIED,
            MemberStatus.NOTHING_TO_DO,
        )
        if not ok:
            failed = result.status is MemberStatus.FAILED
            run = finish(RunStatus.FAILED if failed else RunStatus.PARTIAL, result)
            # Songs that made it into the source anyway are paired, so the next
            # sync doesn't add them to this destination a second time.
            present = await self._source_now(src, result)
            landed = tuple(e for e in plan.synced if e.target_track_id in present)
            if landed:
                self._pair(group, dest, landed)
            return run
        run = finish(RunStatus.SUCCEEDED, result)
        if synced:
            paired = self._pair(group, dest, plan.synced)
            synced_run, _ = await self._runner.run_group(
                paired,
                kind=RunKind.SYNC,
                trigger=Trigger.MANUAL,
                dry_run=False,
                persist=True,
            )
            on_run(synced_run)
        return run

    async def _source_now(self, src: SyncMember, result: MemberResult) -> set[str]:
        """The source's ids after a write that didn't verify cleanly: as
        verified, else as read now; none when it can't be read."""
        if result.verified_after is not None:
            return set(result.verified_after)
        try:
            snap = await _read(
                self._deps.connectors[src.service],
                src.playlist_id,
                "the source playlist",
            )
        except ConnectorError:
            return set()
        return set(snap.track_ids)

    def _pair(
        self,
        group: SyncGroup,
        dest: SyncMember,
        copied: tuple[SyncedEntry, ...],
    ) -> SyncGroup:
        """The copied songs become the sync's on `dest`: paired with their
        source song (and remembered so), no longer the user's. Saves the group."""
        state = dest.last_synced
        assert state is not None  # the songs came from its added_by_you
        source_service = group.source.service
        pairs = tuple(
            SyncedEntry(
                TrackKey(source_service, e.target_track_id), e.source_key.track_id
            )
            for e in copied
        )
        for p in pairs:
            self._deps.repos.matches.remember(
                RememberedMatch(p.source_key, dest.service, p.target_track_id)
            )
        moved = {e.source_key.track_id for e in copied}
        updated = replace(
            dest,
            last_synced=replace(
                state,
                entries=state.entries + pairs,
                added_by_you=tuple(
                    t for t in state.added_by_you if t.track_id not in moved
                ),
            ),
        )
        saved = group.with_member(updated)
        self._deps.repos.syncs.save(saved)
        return saved

    def _event(self, run_id: str, group: SyncGroup, phase: RunPhase, msg: str) -> None:
        self._deps.events.publish(RunEvent(run_id, group.sync_id, phase, 1, 1, msg))


async def _read(
    connector: Connector, playlist_id: str | None, what: str
) -> PlaylistSnapshot:
    """The whole playlist; a missing id or a partial read raises ReadFailed."""
    if playlist_id is None:
        raise ReadFailed(connector.service, f"{what} doesn't exist yet")
    snap = await connector.read_playlist(playlist_id)
    partial = partly_read(snap, what)
    if partial is not None:
        raise ReadFailed(connector.service, partial)
    return snap


def _chosen(
    dest: SyncMember, current: PlaylistSnapshot, track_ids: Collection[str]
) -> list[Track]:
    """The chosen songs the user added on `dest` that are still on it and not
    the sync's, as read now; each once."""
    state = dest.last_synced
    if state is None:
        return []
    paired = {e.target_track_id for e in state.entries}
    wanted = {
        t.track_id
        for t in state.added_by_you
        if (not track_ids or t.track_id in track_ids) and t.track_id not in paired
    }
    out: dict[str, Track] = {}
    for e in current.entries:
        if e.track.track_id in wanted:
            out.setdefault(e.track.track_id, e.track)
    return list(out.values())


def previewed_track_ids(run: SyncRun) -> tuple[str, ...]:
    """The destination songs a copy run planned for: matched, ambiguous or
    not found. The real copy takes exactly these."""
    if run.plan is None:
        return ()
    ids: dict[str, None] = {}
    for plan in run.plan.member_plans:
        ids.update((e.source_key.track_id, None) for e in plan.synced)
        ids.update((c.track_id, None) for c in plan.not_made if c.track_id)
    ids.update((u.source.track_id, None) for u in run.plan.unresolved)
    return tuple(ids)
