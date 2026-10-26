from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from linersync.domain.capabilities import Capability
from linersync.domain.matching.types import MatchDecision, Matched, NeedsAttention
from linersync.domain.operations import (
    AddTracks,
    ChangeNotMade,
    CreatePlaylist,
    MemberPlan,
    Operation,
    Removal,
    RemoveTracks,
    Reorder,
    SyncPlan,
    UnresolvedEntry,
    UpdateMetadata,
)
from linersync.domain.planning.diff import diff_ordered
from linersync.domain.playlists import PlaylistDraft, PlaylistSnapshot
from linersync.domain.sync import (
    Direction,
    SyncedEntry,
    SyncGroup,
    SyncMember,
    SyncMode,
    effective_mode,
)
from linersync.domain.tracks import ServiceId, TrackKey


class UnsupportedDirection(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PlanRequest:
    group: SyncGroup
    snapshots: Mapping[str, PlaylistSnapshot | None]  # None = target missing
    capabilities: Mapping[str, frozenset[Capability]]
    matches: Mapping[ServiceId, Mapping[TrackKey, MatchDecision]]


class _Ops:
    def __init__(self, member_id: str) -> None:
        self._member_id = member_id
        self.items: list[Operation] = []

    def next_id(self) -> str:
        return f"{self._member_id}:{len(self.items)}"

    def add(self, op: Operation) -> None:
        self.items.append(op)


def _metadata_differs(current: PlaylistSnapshot, draft: PlaylistDraft) -> bool:
    meta = current.meta
    if (meta.name, meta.description) != (draft.name, draft.description):
        return True
    return draft.cover_url is not None and draft.cover_url != meta.cover_url


def _available(desired: Sequence[str], pool: Sequence[str]) -> list[str]:
    """Desired items in order, limited to the occurrences present in pool."""
    budget = Counter(pool)
    out: list[str] = []
    for item in desired:
        if budget[item] > 0:
            budget[item] -= 1
            out.append(item)
    return out


def plan_member(
    member_id: str,
    current: PlaylistSnapshot | None,
    desired: Sequence[str],
    draft: PlaylistDraft,
    caps: frozenset[Capability],
    mode: SyncMode,
    *,
    protected: frozenset[str] = frozenset(),
    flag_removed: frozenset[str] = frozenset(),
    synced: tuple[SyncedEntry, ...] = (),
) -> MemberPlan:
    ops = _Ops(member_id)
    not_made: list[ChangeNotMade] = []

    def plan(
        expected_after: Sequence[str], synced_: tuple[SyncedEntry, ...]
    ) -> MemberPlan:
        return MemberPlan(
            member_id=member_id,
            operations=tuple(ops.items),
            not_made=tuple(not_made),
            expected_after=tuple(expected_after),
            synced=synced_,
            draft=draft,
        )

    if current is None:
        if Capability.CREATE_PLAYLIST not in caps:
            not_made.append(
                ChangeNotMade(
                    "create",
                    None,
                    "the playlist is missing and this service can't create playlists",
                )
            )
            return plan((), ())
        ops.add(CreatePlaylist(ops.next_id(), draft))
        if desired:
            ops.add(AddTracks(ops.next_id(), tuple(desired)))
        return plan(desired, synced)

    cur = current.track_ids
    can_add = Capability.ADD in caps
    diff = diff_ordered(cur, desired)
    to_add = diff.to_add

    if effective_mode(mode, caps) is SyncMode.ADD_ONLY:
        added = _add(ops, not_made, to_add, can_add)
        wanted = set(desired)
        flagged = dict.fromkeys(t for t in cur if t in flag_removed and t not in wanted)
        for track_id in flagged:
            not_made.append(
                ChangeNotMade(
                    "remove",
                    track_id,
                    "removed from the source, but this sync only adds songs here",
                )
            )
        return plan(cur + added, synced)

    if _metadata_differs(current, draft):
        if Capability.EDIT_METADATA in caps:
            ops.add(UpdateMetadata(ops.next_id(), draft))
        else:
            not_made.append(
                ChangeNotMade(
                    "metadata", None, "this service can't edit playlist details"
                )
            )

    added = _add(ops, not_made, to_add, can_add)
    after_add = cur + added
    unwanted = diff.to_remove
    removed_at = tuple(i for i in unwanted if cur[i] not in protected)
    extras = [cur[i] for i in unwanted if cur[i] in protected]
    removed = [cur[i] for i in removed_at]
    final = _available(desired, after_add) + extras

    if Capability.REORDER in caps:
        order = tuple(final + removed)
        if order != after_add:
            ops.add(Reorder(ops.next_id(), order))
        positions = list(range(len(final), len(order)))
        expected_after: Sequence[str] = final
    else:
        drop = set(removed_at)
        expected_after = [t for i, t in enumerate(after_add) if i not in drop]
        if list(expected_after) != final:
            not_made.append(
                ChangeNotMade("reorder", None, "this service can't reorder songs")
            )
        positions = list(removed_at)

    if removed:
        ops.add(
            RemoveTracks(
                ops.next_id(),
                tuple(Removal(t, p) for t, p in zip(removed, positions, strict=True)),
            )
        )
    return plan(expected_after, synced)


def _add(
    ops: _Ops,
    not_made: list[ChangeNotMade],
    to_add: tuple[str, ...],
    can_add: bool,
) -> tuple[str, ...]:
    if not to_add:
        return ()
    if can_add:
        ops.add(AddTracks(ops.next_id(), to_add))
        return to_add
    not_made.extend(
        ChangeNotMade("add", t, "this service can't add songs") for t in to_add
    )
    return ()


def _user_deleted(
    pairs: Sequence[SyncedEntry], current: Sequence[str]
) -> tuple[dict[TrackKey, int], dict[TrackKey, SyncedEntry]]:
    """Per source key whose paired copies the target now holds fewer of: how
    many copies may still be desired (those present), and a pair to keep in
    `synced` for each excess copy. Keeping the last-synced pair (not a fresh
    match id) keeps the deletion detected even if the song re-matches."""
    have = Counter(current)
    by_key: dict[TrackKey, list[SyncedEntry]] = {}
    for p in pairs:
        by_key.setdefault(p.source_key, []).append(p)
    allowance: dict[TrackKey, int] = {}
    deleted: dict[TrackKey, SyncedEntry] = {}
    for key, paired in by_key.items():
        wanted = Counter(p.target_track_id for p in paired)
        present = sum(min(have[t], n) for t, n in wanted.items())
        if present < len(paired):
            allowance[key] = present
            deleted[key] = next(
                p for p in paired if have[p.target_track_id] < wanted[p.target_track_id]
            )
    return allowance, deleted


def plan_sync(req: PlanRequest) -> SyncPlan:
    group = req.group
    if group.direction is not Direction.ONE_WAY:
        raise UnsupportedDirection(group.direction)
    source = req.snapshots[group.source.member_id]
    if source is None:
        raise ValueError("one-way planning needs a source snapshot")
    meta = source.meta
    draft = PlaylistDraft(meta.name, meta.description, meta.cover_url)

    plans: list[MemberPlan] = []
    unresolved: list[UnresolvedEntry] = []
    for dest in group.destinations:
        member_plan, member_unresolved = _plan_destination(req, dest, source, draft)
        plans.append(member_plan)
        unresolved.extend(member_unresolved)
    return SyncPlan(group.sync_id, tuple(plans), tuple(unresolved))


def _plan_destination(
    req: PlanRequest,
    dest: SyncMember,
    source: PlaylistSnapshot,
    draft: PlaylistDraft,
) -> tuple[MemberPlan, list[UnresolvedEntry]]:
    decisions = req.matches[dest.service]
    caps = req.capabilities[dest.member_id]
    current = req.snapshots[dest.member_id]
    pairs = dest.last_synced.entries if dest.last_synced is not None else ()

    if (
        current is not None
        and pairs
        and effective_mode(dest.mode, caps) is SyncMode.ADD_ONLY
    ):
        allowance, user_deleted = _user_deleted(pairs, current.track_ids)
    else:
        allowance, user_deleted = {}, {}

    synced: list[SyncedEntry] = []
    desired: list[str] = []
    unresolved: list[UnresolvedEntry] = []
    unresolved_keys: set[TrackKey] = set()
    candidates: set[str] = set()
    for position, entry in enumerate(source.entries):
        key = entry.track.key
        decision = decisions[key]
        if isinstance(decision, Matched):
            deleted = user_deleted.get(key)
            if deleted is not None and allowance[key] <= 0:
                synced.append(deleted)  # stays out of desired: not re-added
            else:
                if deleted is not None:
                    allowance[key] -= 1
                synced.append(SyncedEntry(key, decision.target_track_id))
                desired.append(decision.target_track_id)
        elif isinstance(decision, NeedsAttention):
            unresolved.append(
                UnresolvedEntry(dest.member_id, entry.track, position, decision)
            )
            unresolved_keys.add(key)
            candidates.update(c.track.track_id for c in decision.candidates)

    source_keys = {e.track.key for e in source.entries}
    protected = frozenset(
        p.target_track_id for p in pairs if p.source_key in unresolved_keys
    ) | frozenset(candidates)
    flag_removed = frozenset(
        p.target_track_id for p in pairs if p.source_key not in source_keys
    )
    member_plan = plan_member(
        dest.member_id,
        current,
        desired,
        draft,
        caps,
        dest.mode,
        protected=protected,
        flag_removed=flag_removed,
        synced=tuple(synced),
    )
    return member_plan, unresolved
