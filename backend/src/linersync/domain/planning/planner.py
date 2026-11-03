from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from linersync.domain.capabilities import Capability
from linersync.domain.matching.types import (
    MatchDecision,
    Matched,
    MatchVia,
    NeedsAttention,
)
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
from linersync.domain.planning.order import arrange, moved
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


def _metadata_differs(
    current: PlaylistSnapshot,
    draft: PlaylistDraft,
    previous_draft: PlaylistDraft | None,
) -> bool:
    """Compare with the last-synced draft when there is one: the target stores
    text and covers in its own form, so its values never equal the source's."""
    base = previous_draft if previous_draft is not None else current.meta
    if (base.name, base.description) != (draft.name, draft.description):
        return True
    return draft.cover_url is not None and draft.cover_url != base.cover_url


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
    previous_draft: PlaylistDraft | None = None,
    owned: Sequence[str] = (),
    previous_order: Sequence[str] = (),
    previous_pinned: Sequence[str] = (),
    swaps: Sequence[tuple[str, str]] = (),
    mirror: bool = False,
) -> MemberPlan:
    """Plan one destination. In full mode (unless `mirror`) the destination's
    own edits are respected: songs the sync doesn't own (`owned`, the targets
    of the previous pairs) are the user's and kept, and owned songs the user
    moved since `previous_order` keep their place. `swaps` lists
    (old target, new target) pairs whose new version replaces the old in place.
    """
    ops = _Ops(member_id)
    not_made: list[ChangeNotMade] = []

    def plan(
        expected_after: Sequence[str],
        synced_: tuple[SyncedEntry, ...],
        user_added: Sequence[str] = (),
        pinned: Sequence[str] = (),
    ) -> MemberPlan:
        return MemberPlan(
            member_id=member_id,
            operations=tuple(ops.items),
            not_made=tuple(not_made),
            expected_after=tuple(expected_after),
            synced=synced_,
            draft=draft,
            user_added=tuple(user_added),
            pinned=tuple(pinned),
        )

    if current is None:
        if not desired:
            # Created on the first run that has songs: an empty playlist can't
            # always be read back (Apple, D3).
            return plan((), ())
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

    owner = _Ownership(cur, desired, owned, protected)

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
        return plan(cur + added, synced, owner.user_added)

    if _metadata_differs(current, draft, previous_draft):
        if Capability.EDIT_METADATA in caps:
            ops.add(UpdateMetadata(ops.next_id(), draft))
        else:
            not_made.append(
                ChangeNotMade(
                    "metadata", None, "this service can't edit playlist details"
                )
            )

    added = _add(ops, not_made, to_add, can_add)
    if not mirror:
        after, pinned = _respect_edits(
            ops,
            not_made,
            owner,
            desired,
            added,
            caps,
            previous_order,
            previous_pinned,
            swaps,
        )
        return plan(after, synced, owner.user_added, pinned)

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


class _Ownership:
    """Each occurrence of the destination counted against `desired`, then
    against `owned` (the sync's from the last run, so removable), else the
    user's. Protected occurrences are never removed and never the user's."""

    def __init__(
        self,
        cur: Sequence[str],
        desired: Sequence[str],
        owned: Sequence[str],
        protected: frozenset[str],
    ) -> None:
        want = Counter(desired)
        mine = Counter(owned)
        self.cur = tuple(cur)
        self.kept: list[int] = []  # indexes kept, in order
        self.kept_desired: list[int] = []
        self.removed: list[int] = []
        self.user_added: list[str] = []
        self.extras: list[str] = []  # protected, kept, not desired
        for index, item in enumerate(cur):
            if want[item] > 0:
                want[item] -= 1
                if mine[item] > 0:
                    mine[item] -= 1
                self.kept.append(index)
                self.kept_desired.append(index)
            elif item in protected:
                self.kept.append(index)
                self.extras.append(item)
            elif mine[item] > 0:
                mine[item] -= 1
                self.removed.append(index)
            else:
                self.kept.append(index)
                self.user_added.append(item)


def _replace_once(items: Sequence[str], old: str, new: str) -> list[str]:
    out = list(items)
    if old in out:
        out[out.index(old)] = new
    return out


def _respect_edits(
    ops: _Ops,
    not_made: list[ChangeNotMade],
    owner: _Ownership,
    desired: Sequence[str],
    added: Sequence[str],
    caps: frozenset[Capability],
    previous_order: Sequence[str],
    previous_pinned: Sequence[str],
    swaps: Sequence[tuple[str, str]],
) -> tuple[list[str], list[str]]:
    """Full-mode order and removals that keep the user's edits; returns the
    expected order after the write and the pinned ids."""
    cur = owner.cur
    # The order as the user sees it: a swapped-in version stands where the old
    # one was (so it inherits its place and pin), other adds at the end.
    view = list(cur)
    appended = list(added)
    old_order, old_pins = list(previous_order), list(previous_pinned)
    kept_desired = set(owner.kept_desired)
    removed_free = list(owner.removed)
    for old, new in swaps:
        spot = next((i for i in removed_free if cur[i] == old), None)
        if spot is None or new not in appended:
            continue
        removed_free.remove(spot)
        appended.remove(new)
        view[spot] = new
        kept_desired.add(spot)
        old_order = _replace_once(old_order, old, new)
        old_pins = _replace_once(old_pins, old, new)
    shown = sorted(set(owner.kept) | kept_desired)
    after_view = [view[i] for i in shown] + appended
    present = [view[i] for i in shown if i in kept_desired]

    pin_count = Counter(_available(old_pins, present))
    if old_order:
        pin_count |= Counter(moved(_available(old_order, present), present))
    pinned: list[str] = []
    for item in present:
        if pin_count[item] > 0:
            pin_count[item] -= 1
            pinned.append(item)

    unpinned = Counter(pinned)
    base: list[str] = []
    for item in _available(desired, after_view):
        if unpinned[item] > 0:
            unpinned[item] -= 1
        else:
            base.append(item)
    final = arrange(after_view, base, pinned + owner.user_added + owner.extras)

    after_add = list(cur) + list(added)
    removed_at = owner.removed
    removed = [cur[i] for i in removed_at]
    if Capability.REORDER in caps:
        order = final + removed
        if order != after_add:
            ops.add(Reorder(ops.next_id(), tuple(order)))
        positions = list(range(len(final), len(order)))
        expected_after = final
    else:
        drop = set(removed_at)
        expected_after = [t for i, t in enumerate(after_add) if i not in drop]
        if expected_after != final:
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
    return expected_after, pinned


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


def _picked_instead(decision: Matched, deleted: SyncedEntry) -> bool:
    """The user deleted one version on the destination and then picked another
    ("your pick"): the pick is wanted, the deleted version stays gone."""
    return (
        decision.via is MatchVia.REMEMBERED
        and decision.target_track_id != deleted.target_track_id
    )


def plan_sync(req: PlanRequest) -> SyncPlan:
    group = req.group
    if group.direction is not Direction.ONE_WAY:
        raise UnsupportedDirection(group.direction)
    source = req.snapshots[group.source.member_id]
    if source is None:
        raise ValueError("one-way planning needs a source snapshot")
    draft = source.meta.draft()

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
    state = dest.last_synced
    pairs = state.entries if state is not None else ()
    add_only = effective_mode(dest.mode, caps) is SyncMode.ADD_ONLY
    mirror = dest.mirror_once and not add_only

    if current is not None and pairs and not mirror:
        allowance, user_deleted = _user_deleted(pairs, current.track_ids)
    else:
        allowance, user_deleted = {}, {}

    user_removed: list[SyncedEntry] = []
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
            if deleted is not None and _picked_instead(decision, deleted):
                deleted = None  # a swap: the pick replaces the deleted version
            if deleted is not None and allowance[key] <= 0:
                synced.append(deleted)  # stays out of desired: not re-added
                user_removed.append(deleted)
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
        previous_draft=state.draft if state is not None else None,
        owned=[p.target_track_id for p in pairs],
        previous_order=state.target_order if state is not None else (),
        previous_pinned=state.pinned if state is not None else (),
        swaps=_swaps(pairs, synced),
        mirror=mirror,
    )
    on_target = Counter(current.track_ids if current is not None else ())
    held: list[SyncedEntry] = []
    for p in pairs:
        if p.source_key in unresolved_keys and on_target[p.target_track_id] > 0:
            on_target[p.target_track_id] -= 1
            held.append(p)
    plan = replace(member_plan, user_removed=tuple(user_removed), held=tuple(held))
    return plan, unresolved


def _swaps(
    previous: Sequence[SyncedEntry], current: Sequence[SyncedEntry]
) -> list[tuple[str, str]]:
    """(old target, new target) for source songs now linked to another version,
    e.g. after the user picked a different match."""
    before: dict[TrackKey, list[str]] = {}
    for p in previous:
        before.setdefault(p.source_key, []).append(p.target_track_id)
    after: dict[TrackKey, list[str]] = {}
    for p in current:
        after.setdefault(p.source_key, []).append(p.target_track_id)
    return [
        (old, new)
        for key, olds in before.items()
        for old, new in zip(olds, after.get(key, ()), strict=False)
        if old != new
    ]
