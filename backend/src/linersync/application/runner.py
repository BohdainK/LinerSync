from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from linersync.application.breaker import CircuitBreaker
from linersync.application.config import EngineConfig
from linersync.application.matching_service import MatchingFailed, MatchService
from linersync.domain.attention import AttentionItem, AttentionKind
from linersync.domain.matching.types import MatchDecision
from linersync.domain.operations import (
    ChangeNotMade,
    CreatePlaylist,
    MemberPlan,
    Operation,
    SyncPlan,
)
from linersync.domain.planning.brake import BrakeTrip, check_brake, fingerprint
from linersync.domain.planning.planner import PlanRequest, plan_sync
from linersync.domain.playlists import PlaylistDraft, PlaylistSnapshot
from linersync.domain.runs import (
    MemberResult,
    MemberStatus,
    RunEvent,
    RunKind,
    RunStatus,
    SyncRun,
    Trigger,
    VerificationMismatch,
)
from linersync.domain.sync import (
    MemberState,
    SyncedEntry,
    SyncGroup,
    SyncMember,
    SyncStatus,
)
from linersync.domain.tracks import ServiceId, TrackKey
from linersync.ports.connector import Connector
from linersync.ports.errors import ConnectorError, PlaylistNotFound, ReadFailed
from linersync.ports.repositories import Repositories
from linersync.ports.runtime import Clock, EventPublisher, IdFactory

Phase = Literal["read", "match", "plan", "apply", "verify", "done"]

# Re-plans one member from a fresh read of its target (playlist id given).
Replanner = Callable[[PlaylistSnapshot, str], MemberPlan]


@dataclass(frozen=True, slots=True)
class Deps:
    connectors: Mapping[ServiceId, Connector]
    repos: Repositories
    clock: Clock
    events: EventPublisher
    new_id: IdFactory
    breaker: CircuitBreaker
    config: EngineConfig = field(default_factory=EngineConfig)


class _SourceUnavailable(Exception):
    def __init__(self, status: RunStatus, error: str) -> None:
        super().__init__(error)
        self.status = status
        self.error = error


@dataclass(slots=True)
class _Work:
    """Mutable state of one run, handed from phase to phase."""

    run_id: str
    group: SyncGroup
    kind: RunKind
    trigger: Trigger
    dry_run: bool
    started_at: datetime
    source: PlaylistSnapshot | None = None
    # Destinations still in play, keyed by member_id; None = missing (rebuild).
    snapshots: dict[str, PlaylistSnapshot | None] = field(default_factory=dict)
    rebuilt: set[str] = field(default_factory=set)
    # Destinations dropped from planning (unreadable or matching failed).
    skipped: dict[str, MemberResult] = field(default_factory=dict)
    matches: dict[ServiceId, Mapping[TrackKey, MatchDecision]] = field(
        default_factory=dict
    )
    plan: SyncPlan | None = None
    # Services contacted this run, and the last error each one gave (if any).
    called: set[ServiceId] = field(default_factory=set)
    errors: dict[ServiceId, str] = field(default_factory=dict)
    # Member results so far (apply phase), kept for a run that aborts midway.
    results: list[MemberResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What applying one member's plan produced."""

    result: MemberResult
    plan: MemberPlan  # the plan last worked on (a re-plan replaces the original)
    playlist_id: str | None  # the target after applying (the new id if created)
    state: MemberState | None = None  # new last_synced; None = leave unchanged


class _OpFailed(Exception):
    def __init__(self, op: Operation, error: ConnectorError) -> None:
        super().__init__(str(error))
        self.op = op
        self.error = error


class SyncRunner:
    def __init__(self, deps: Deps) -> None:
        self._deps = deps
        self._matcher = MatchService(deps.repos.matches, deps.config.match)

    async def run(
        self, sync_id: str, *, trigger: Trigger, dry_run: bool = False
    ) -> SyncRun:
        """Run a saved sync. Raises KeyError for an unknown `sync_id`."""
        group = self._deps.repos.syncs.get(sync_id)
        if group is None:
            raise KeyError(sync_id)
        run, _ = await self.run_group(
            group, kind=RunKind.SYNC, trigger=trigger, dry_run=dry_run, persist=True
        )
        return run

    async def preview(self, group: SyncGroup) -> SyncRun:
        run, _ = await self.run_group(
            group,
            kind=RunKind.SYNC,
            trigger=Trigger.MANUAL,
            dry_run=True,
            persist=False,
        )
        return run

    async def run_group(
        self,
        group: SyncGroup,
        *,
        kind: RunKind,
        trigger: Trigger,
        dry_run: bool,
        persist: bool,
    ) -> tuple[SyncRun, SyncGroup]:
        """The run is always saved; `persist` only governs saving the group. An
        unexpected exception (not a ConnectorError) ends the run ABORTED."""
        w = _Work(
            run_id=self._deps.new_id(),
            group=group,
            kind=kind,
            trigger=trigger,
            dry_run=dry_run,
            started_at=self._deps.clock.now(),
        )
        try:
            return await self._run_work(w, persist)
        except ConnectorError:
            raise
        except Exception as e:
            return self._abort(w, e), w.group

    async def _run_work(self, w: _Work, persist: bool) -> tuple[SyncRun, SyncGroup]:
        group, dry_run = w.group, w.dry_run
        if group.status is SyncStatus.PAUSED:
            return self._finish(w, RunStatus.SKIPPED), group
        if not self._deps.breaker.allow(group.source.service):
            return self._finish(
                w,
                RunStatus.SKIPPED,
                error=f"{group.source.service} is failing; waiting before retrying",
            ), group

        try:
            w.source = await self._read_source(w)
        except _SourceUnavailable as e:
            if e.status is RunStatus.PAUSED_SOURCE_MISSING and not dry_run:
                w.group = self._pause(w.group, e.error, persist)
            return self._finish(w, e.status, error=e.error), w.group

        await self._read_destinations(w)
        await self._match(w)
        self._plan(w)

        if dry_run:
            return self._finish_dry_run(w), w.group
        return self._finish_applied(w, await self._apply(w), persist), w.group

    # -- read -------------------------------------------------------------
    async def _read_source(self, w: _Work) -> PlaylistSnapshot:
        src = w.group.source
        total = len(w.group.members)
        connector = self._deps.connectors[src.service]
        w.called.add(src.service)
        if src.playlist_id is None:
            raise _SourceUnavailable(RunStatus.ABORTED, "the source has no playlist")
        try:
            snap = await connector.read_playlist(src.playlist_id)
        except PlaylistNotFound:
            w.called.discard(src.service)  # a missing playlist says nothing
            reason = (
                f"The source playlist on {connector.display_name} "
                "no longer exists or can't be accessed"
            )
            raise _SourceUnavailable(RunStatus.PAUSED_SOURCE_MISSING, reason) from None
        except ConnectorError as e:
            w.errors[src.service] = str(e)
            raise _SourceUnavailable(
                RunStatus.ABORTED, f"couldn't read the source playlist: {e}"
            ) from e
        if not snap.is_complete:
            partial = (
                f"the source playlist {src.playlist_id} was only partly read "
                f"{snap.shortfall()}"
            )
            w.errors[src.service] = partial
            raise _SourceUnavailable(RunStatus.ABORTED, partial)
        self._event(w, "read", 1, total, "read the source playlist")
        return snap

    async def _read_destinations(self, w: _Work) -> None:
        total = len(w.group.members)
        for done, dest in enumerate(w.group.destinations, start=2):
            await self._read_destination(w, dest)
            self._event(w, "read", done, total, f"read {dest.member_id}")

    async def _read_destination(self, w: _Work, dest: SyncMember) -> None:
        mid = dest.member_id
        if not self._deps.breaker.allow(dest.service):
            self._skip(
                w,
                mid,
                f"{dest.service} is failing; waiting before retrying",
                MemberStatus.SKIPPED_BREAKER,
            )
            return
        if dest.playlist_id is None:
            w.snapshots[mid] = None
            w.rebuilt.add(mid)
            return
        connector = self._deps.connectors[dest.service]
        w.called.add(dest.service)
        try:
            snap = await connector.read_playlist(dest.playlist_id)
        except PlaylistNotFound:
            w.snapshots[mid] = None
            w.rebuilt.add(mid)
            return
        except ConnectorError as e:
            w.errors[dest.service] = str(e)
            self._skip(w, mid, f"couldn't read this playlist: {e}")
            return
        if not snap.is_complete:
            partial = f"this playlist was only partly read {snap.shortfall()}"
            w.errors[dest.service] = partial
            self._skip(w, mid, partial)
            return
        w.snapshots[mid] = snap

    # -- match ------------------------------------------------------------
    async def _match(self, w: _Work) -> None:
        assert w.source is not None
        services = list(
            dict.fromkeys(
                d.service for d in w.group.destinations if d.member_id in w.snapshots
            )
        )
        for done, service in enumerate(services, start=1):
            previous = next(
                d.last_synced for d in w.group.destinations if d.service == service
            )
            w.called.add(service)
            try:
                w.matches[service] = await self._matcher.resolve(
                    w.source, self._deps.connectors[service], previous
                )
            except MatchingFailed as e:
                w.errors[service] = str(e)
                for d in w.group.destinations:
                    if d.service == service and d.member_id in w.snapshots:
                        self._skip(w, d.member_id, f"couldn't match songs: {e}")
            self._event(w, "match", done, len(services), f"matched for {service}")

    # -- plan -------------------------------------------------------------
    def _plan(self, w: _Work) -> None:
        """Plan over a copy of the group holding only the destinations still in
        play. With none left there is nothing to plan and `w.plan` stays None."""
        assert w.source is not None
        src = w.group.source
        planned = tuple(d for d in w.group.destinations if d.member_id in w.snapshots)
        if not planned:
            return
        members = (src, *planned)
        req = PlanRequest(
            group=replace(w.group, members=members),
            snapshots={src.member_id: w.source, **w.snapshots},
            capabilities={
                m.member_id: self._deps.connectors[m.service].capabilities
                for m in members
            },
            matches=w.matches,
        )
        w.plan = plan_sync(req)
        self._event(w, "plan", 1, 1, "planned the changes")

    # -- apply ------------------------------------------------------------
    async def _apply(self, w: _Work) -> tuple[MemberResult, ...]:
        # Saved before any write, so pre-write snapshots belong to a saved run.
        self._deps.repos.runs.save(
            SyncRun(
                run_id=w.run_id,
                sync_id=w.group.sync_id,
                kind=w.kind,
                trigger=w.trigger,
                dry_run=False,
                status=RunStatus.RUNNING,
                started_at=w.started_at,
                plan=w.plan,
            )
        )
        results = w.results
        for dest in w.group.destinations:
            mid = dest.member_id
            if mid in w.skipped:
                results.append(w.skipped[mid])
                continue
            outcome = await self._apply_destination(w, dest)
            member = dest
            if outcome.result.created_playlist_id is not None:
                # The old pairs point at a playlist that no longer exists.
                member = replace(
                    member, playlist_id=outcome.playlist_id, last_synced=None
                )
            if outcome.state is not None:
                member = replace(member, last_synced=outcome.state)
            w.group = w.group.with_member(member)
            results.append(outcome.result)
        return tuple(results)

    async def _apply_destination(self, w: _Work, dest: SyncMember) -> _Outcome:
        assert w.plan is not None
        mid = dest.member_id
        plan = w.plan.for_member(mid)
        current = w.snapshots[mid]
        rebuilt = mid in w.rebuilt
        if not plan.operations:
            self._supersede_brake(w.group.sync_id, mid)
            state = None if current is None else self._state(plan, current)
            result = MemberResult(
                mid, MemberStatus.NOTHING_TO_DO, not_made=plan.not_made, rebuilt=rebuilt
            )
            return _Outcome(result, plan, dest.playlist_id, state)
        trip = self._brake(w.group.sync_id, plan, current)
        if trip is not None:
            return self._hold(w.group, dest, plan, trip, dest.playlist_id, ())
        self._supersede_brake(w.group.sync_id, mid)
        self._deps.repos.snapshots.save_pre_write(w.run_id, mid, current)

        def replan(fresh: PlaylistSnapshot, playlist_id: str) -> MemberPlan:
            # A target created in this run has no history: the old pairs refer to
            # the deleted playlist and would read as "deleted by the user".
            state = dest.last_synced if current is not None else None
            member = replace(dest, playlist_id=playlist_id, last_synced=state)
            return self._replan(w, member, fresh)

        w.called.add(dest.service)
        outcome = await self._apply_member(
            w.run_id,
            w.group,
            dest,
            plan,
            current,
            replan=replan,
            brake=True,
            errors=w.errors,
        )
        return replace(outcome, result=replace(outcome.result, rebuilt=rebuilt))

    async def apply_member_plan(
        self,
        run_id: str,
        group: SyncGroup,
        member: SyncMember,
        plan: MemberPlan,
        current: PlaylistSnapshot | None,
    ) -> MemberResult:
        """Execute and verify one member's plan. No brake, no pre-write snapshot
        and no re-plan: the caller decides those."""
        outcome = await self._apply_member(
            run_id, group, member, plan, current, replan=None, brake=False
        )
        return outcome.result

    async def _apply_member(
        self,
        run_id: str,
        group: SyncGroup,
        member: SyncMember,
        plan: MemberPlan,
        current: PlaylistSnapshot | None,
        *,
        replan: Replanner | None,
        brake: bool,
        errors: dict[ServiceId, str] | None = None,
    ) -> _Outcome:
        """Run the plan's ops in order. On a failed write the member's remaining
        ops are dropped (so removals never follow it); with a re-plan left, the
        target is re-read and re-planned, else the member fails. A failed
        CreatePlaylist is never re-planned: a playlist may exist already."""
        connector = self._deps.connectors[member.service]
        sink = errors if errors is not None else {}
        mid = member.member_id
        playlist_id = None if current is None else member.playlist_id
        created: str | None = None
        applied: list[str] = []
        replans = self._deps.config.max_replans if replan is not None else 0
        attempt = 0  # re-plans so far; their op ids are namespaced to stay unique

        def failed(error: str) -> _Outcome:
            result = MemberResult(
                mid,
                MemberStatus.FAILED,
                applied_op_ids=tuple(applied),
                not_made=plan.not_made,
                created_playlist_id=created,
                error=error,
            )
            return _Outcome(result, plan, created or member.playlist_id)

        while True:
            try:
                for done, op in enumerate(plan.operations, start=1):
                    try:
                        if isinstance(op, CreatePlaylist):
                            meta = await connector.create_playlist(op.draft)
                            playlist_id = created = meta.playlist_id
                        else:
                            assert playlist_id is not None
                            await connector.apply(playlist_id, op)
                    except ConnectorError as e:
                        sink[member.service] = str(e)
                        raise _OpFailed(op, e) from e
                    prefix = f"{run_id}:r{attempt}" if attempt else run_id
                    applied.append(f"{prefix}:{op.op_id}")
                    self._publish(
                        run_id,
                        group.sync_id,
                        "apply",
                        done,
                        len(plan.operations),
                        f"{mid}: {type(op).__name__}",
                    )
                break
            except _OpFailed as f:
                error = f"couldn't apply {type(f.op).__name__}: {f.error}"
                if isinstance(f.op, CreatePlaylist) or replans <= 0:
                    return failed(error)
                assert replan is not None and playlist_id is not None
                replans -= 1
                attempt += 1
                try:
                    fresh = await self._read_complete(connector, playlist_id)
                except ConnectorError as e:
                    sink[member.service] = str(e)
                    return failed(f"{error}; then couldn't re-read the playlist: {e}")
                plan = replan(fresh, playlist_id)
                trip = self._brake(group.sync_id, plan, fresh) if brake else None
                if trip is not None:
                    return self._hold(
                        group, member, plan, trip, playlist_id, tuple(applied), created
                    )

        assert playlist_id is not None
        mismatch, verified, verify_error = await self._verify(
            connector, mid, playlist_id, plan
        )
        verified_after: tuple[str, ...] | None = None
        if verified is not None:
            verified_after = verified.track_ids
        elif mismatch is not None and verify_error is None:
            verified_after = mismatch.actual  # read fine, list differs
        self._publish(run_id, group.sync_id, "verify", 1, 1, f"verified {mid}")
        if verify_error is not None:
            sink[member.service] = verify_error
        if mismatch is not None:
            self._attention(
                AttentionKind.VERIFY_MISMATCH,
                group,
                member,
                verify_error
                or f"After syncing, {connector.display_name} has "
                f"{len(mismatch.actual)} songs where "
                f"{len(mismatch.expected)} were expected",
            )
        result = MemberResult(
            mid,
            MemberStatus.APPLIED,
            applied_op_ids=tuple(applied),
            not_made=plan.not_made,
            mismatch=mismatch,
            created_playlist_id=created,
            error=verify_error,
            verified_after=verified_after,
        )
        state = None if verified is None else self._state(plan, verified)
        return _Outcome(result, plan, playlist_id, state)

    async def _verify(
        self, connector: Connector, member_id: str, playlist_id: str, plan: MemberPlan
    ) -> tuple[VerificationMismatch | None, PlaylistSnapshot | None, str | None]:
        """(mismatch, the matching snapshot, error). A failed verify read is a
        mismatch with `actual=()`, never an empty playlist."""
        expected = plan.expected_after
        try:
            snap = await self._read_complete(connector, playlist_id)
        except ConnectorError as e:
            mismatch = VerificationMismatch(member_id, expected, ())
            return mismatch, None, f"couldn't verify the changes: {e}"
        if snap.track_ids != expected:
            return VerificationMismatch(member_id, expected, snap.track_ids), None, None
        return None, snap, None

    async def _read_complete(
        self, connector: Connector, playlist_id: str
    ) -> PlaylistSnapshot:
        snap = await connector.read_playlist(playlist_id)
        if not snap.is_complete:
            raise ReadFailed(
                connector.service,
                f"{playlist_id} was only partly read {snap.shortfall()}",
            )
        return snap

    def _replan(
        self, w: _Work, member: SyncMember, fresh: PlaylistSnapshot
    ) -> MemberPlan:
        """Plan just this member again from a fresh read, same match decisions."""
        assert w.source is not None
        src = w.group.source
        members = (src, member)
        req = PlanRequest(
            group=replace(w.group, members=members),
            snapshots={src.member_id: w.source, member.member_id: fresh},
            capabilities={
                m.member_id: self._deps.connectors[m.service].capabilities
                for m in members
            },
            matches=w.matches,
        )
        return plan_sync(req).for_member(member.member_id)

    def _brake(
        self, sync_id: str, plan: MemberPlan, current: PlaylistSnapshot | None
    ) -> BrakeTrip | None:
        """The trip, unless this exact plan was approved. An approval covers one
        pass: letting the plan through uses it up."""
        size = 0 if current is None else len(current.entries)
        trip = check_brake(plan, size, self._deps.config.brake)
        if trip is None:
            return None
        approved = self._deps.repos.approvals.consume(
            sync_id, plan.member_id, fingerprint(plan)
        )
        return None if approved else trip

    def _supersede_brake(self, sync_id: str, member_id: str) -> None:
        """This member's plan no longer trips: its open BRAKE item is moot."""
        repo = self._deps.repos.attention
        for item in repo.list_open():
            if (
                item.kind is AttentionKind.BRAKE
                and item.sync_id == sync_id
                and item.member_id == member_id
            ):
                repo.resolve(item.item_id, "superseded", self._deps.clock.now())

    def _hold(
        self,
        group: SyncGroup,
        member: SyncMember,
        plan: MemberPlan,
        trip: BrakeTrip,
        playlist_id: str | None,
        applied: tuple[str, ...],
        created: str | None = None,
    ) -> _Outcome:
        self._attention(
            AttentionKind.BRAKE,
            group,
            member,
            trip.reason,
            fingerprint=fingerprint(plan),
        )
        result = MemberResult(
            member.member_id,
            MemberStatus.AWAITING_APPROVAL,
            applied_op_ids=applied,
            not_made=plan.not_made,
            created_playlist_id=created,
            error=trip.reason,
        )
        return _Outcome(result, plan, playlist_id)

    def _state(self, plan: MemberPlan, snap: PlaylistSnapshot) -> MemberState:
        return MemberState(
            plan.synced, plan.draft, snap.meta.version_token, self._deps.clock.now()
        )

    def _source_state(self, source: PlaylistSnapshot) -> MemberState:
        meta = source.meta
        return MemberState(
            tuple(SyncedEntry(e.track.key, e.track.track_id) for e in source.entries),
            PlaylistDraft(meta.name, meta.description, meta.cover_url),
            meta.version_token,
            self._deps.clock.now(),
        )

    def _record_attention(self, w: _Work, results: tuple[MemberResult, ...]) -> None:
        if w.plan is not None:
            for u in w.plan.unresolved:
                dest = self._member(w.group, u.member_id)
                self._attention(
                    AttentionKind.UNMATCHED,
                    w.group,
                    dest,
                    "; ".join(u.decision.reasons),
                    source=u.source,
                    target_service=dest.service,
                    candidates=u.decision.candidates,
                )
        for r in results:
            if r.not_made:
                dest = self._member(w.group, r.member_id)
                name = self._deps.connectors[dest.service].display_name
                not_made = self._merged_not_made(w.group.sync_id, r)
                self._attention(
                    AttentionKind.CHANGES_NOT_MADE,
                    w.group,
                    dest,
                    f"{len(not_made)} change(s) couldn't be made on {name}",
                    not_made=not_made,
                )

    def _merged_not_made(
        self, sync_id: str, r: MemberResult
    ) -> tuple[ChangeNotMade, ...]:
        """The open item's changes first, then this run's new ones, de-duplicated
        by (kind, track_id)."""
        existing: tuple[ChangeNotMade, ...] = ()
        for item in self._deps.repos.attention.list_open():
            if (
                item.kind is AttentionKind.CHANGES_NOT_MADE
                and item.sync_id == sync_id
                and item.member_id == r.member_id
            ):
                existing = item.not_made
                break
        merged: dict[tuple[str, str | None], ChangeNotMade] = {}
        for c in (*existing, *r.not_made):
            merged.setdefault((c.kind, c.track_id), c)
        return tuple(merged.values())

    # -- finish -----------------------------------------------------------
    def _finish_applied(
        self, w: _Work, results: tuple[MemberResult, ...], persist: bool
    ) -> SyncRun:
        assert w.source is not None
        self._record_attention(w, results)
        if any(_settled(r) for r in results):
            w.group = w.group.with_member(
                replace(w.group.source, last_synced=self._source_state(w.source))
            )
        if persist:
            self._deps.repos.syncs.save(w.group)
        return self._finish(w, _run_status(results), members=results)

    def _finish_dry_run(self, w: _Work) -> SyncRun:
        results: list[MemberResult] = []
        for dest in w.group.destinations:
            mid = dest.member_id
            if mid in w.skipped:
                results.append(w.skipped[mid])
                continue
            assert w.plan is not None
            plan = w.plan.for_member(mid)
            changes = bool(plan.operations or plan.not_made)
            results.append(
                MemberResult(
                    member_id=mid,
                    status=MemberStatus.DRY_RUN
                    if changes
                    else MemberStatus.NOTHING_TO_DO,
                    not_made=plan.not_made,
                    rebuilt=mid in w.rebuilt,
                )
            )
        status = RunStatus.PARTIAL if w.skipped else RunStatus.SUCCEEDED
        return self._finish(w, status, members=tuple(results))

    def _finish(
        self,
        w: _Work,
        status: RunStatus,
        *,
        members: tuple[MemberResult, ...] = (),
        error: str | None = None,
    ) -> SyncRun:
        self._settle_breaker(w)
        run = SyncRun(
            run_id=w.run_id,
            sync_id=w.group.sync_id,
            kind=w.kind,
            trigger=w.trigger,
            dry_run=w.dry_run,
            status=status,
            started_at=w.started_at,
            finished_at=self._deps.clock.now(),
            plan=w.plan,
            members=members,
            error=error,
        )
        self._deps.repos.runs.save(run)
        self._event(w, "done", 1, 1, str(status))
        return run

    def _abort(self, w: _Work, e: Exception) -> SyncRun:
        """Save the run ABORTED with the error. Members written to without a
        result yet are recorded FAILED, so the run can still be undone."""
        members = list(w.results)
        done = {r.member_id for r in members}
        snapshots = self._deps.repos.snapshots
        error = f"{type(e).__name__}: {e}"
        for dest in w.group.destinations:
            mid = dest.member_id
            if mid not in done and snapshots.has_pre_write(w.run_id, mid):
                members.append(MemberResult(mid, MemberStatus.FAILED, error=error))
        return self._finish(w, RunStatus.ABORTED, members=tuple(members), error=error)

    def _settle_breaker(self, w: _Work) -> None:
        """One verdict per contacted service: a failure (the last error) if any
        call failed, else a success. Dry runs have no side effects."""
        if w.dry_run:
            return
        breaker = self._deps.breaker
        for service in w.called:
            error = w.errors.get(service)
            if error is None:
                if breaker.record_success(service):
                    self._recovered(service)
            elif breaker.record_failure(service, error):
                self._deps.repos.attention.upsert(
                    AttentionItem(
                        item_id=self._deps.new_id(),
                        kind=AttentionKind.SERVICE_FAILING,
                        created_at=self._deps.clock.now(),
                        detail=error,
                        service=service,
                    )
                )

    def _recovered(self, service: ServiceId) -> None:
        repo = self._deps.repos.attention
        for item in repo.list_open():
            if item.kind is AttentionKind.SERVICE_FAILING and item.service == service:
                repo.resolve(item.item_id, "recovered", self._deps.clock.now())

    # -- helpers ----------------------------------------------------------
    def _pause(self, group: SyncGroup, reason: str, persist: bool) -> SyncGroup:
        """Pause the group. Only a persisted group is saved and gets an item: an
        unsaved one (a transfer) must not leave items for a sync that isn't."""
        paused = replace(group, status=SyncStatus.PAUSED, pause_reason=reason)
        if not persist:
            return paused
        self._deps.repos.syncs.save(paused)
        src = group.source
        self._deps.repos.attention.upsert(
            AttentionItem(
                item_id=self._deps.new_id(),
                kind=AttentionKind.SOURCE_MISSING,
                created_at=self._deps.clock.now(),
                detail=reason,
                sync_id=group.sync_id,
                member_id=src.member_id,
                service=src.service,
            )
        )
        return paused

    def _skip(
        self,
        w: _Work,
        member_id: str,
        error: str,
        status: MemberStatus = MemberStatus.SKIPPED_UNREADABLE,
    ) -> None:
        w.snapshots.pop(member_id, None)
        w.rebuilt.discard(member_id)
        w.skipped[member_id] = MemberResult(member_id, status, error=error)

    def _attention(
        self,
        kind: AttentionKind,
        group: SyncGroup,
        member: SyncMember,
        detail: str,
        **extra: Any,
    ) -> None:
        self._deps.repos.attention.upsert(
            AttentionItem(
                item_id=self._deps.new_id(),
                kind=kind,
                created_at=self._deps.clock.now(),
                detail=detail,
                sync_id=group.sync_id,
                member_id=member.member_id,
                service=member.service,
                **extra,
            )
        )

    @staticmethod
    def _member(group: SyncGroup, member_id: str) -> SyncMember:
        return next(m for m in group.members if m.member_id == member_id)

    def _event(self, w: _Work, phase: Phase, done: int, total: int, msg: str) -> None:
        self._publish(w.run_id, w.group.sync_id, phase, done, total, msg)

    def _publish(
        self, run_id: str, sync_id: str, phase: Phase, done: int, total: int, msg: str
    ) -> None:
        self._deps.events.publish(RunEvent(run_id, sync_id, phase, done, total, msg))


def _settled(r: MemberResult) -> bool:
    """The member ended in a known, synced state."""
    if r.status is MemberStatus.APPLIED:
        return r.mismatch is None
    return r.status is MemberStatus.NOTHING_TO_DO


def _run_status(results: tuple[MemberResult, ...]) -> RunStatus:
    statuses = [r.status for r in results]
    ok = {MemberStatus.APPLIED, MemberStatus.NOTHING_TO_DO}
    if all(s in ok for s in statuses) and not any(r.mismatch for r in results):
        return RunStatus.SUCCEEDED
    if all(s is MemberStatus.AWAITING_APPROVAL for s in statuses):
        return RunStatus.AWAITING_APPROVAL
    if MemberStatus.APPLIED not in statuses and MemberStatus.FAILED in statuses:
        return RunStatus.FAILED
    return RunStatus.PARTIAL
