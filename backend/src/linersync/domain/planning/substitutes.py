"""Songs a service filed under another id than the one it was sent.

Apple Music can take a catalog id and list the song under another (an older
copy of the same song already in the library, say). The playlist then holds
what was asked for, but not the ids the plan expected.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from linersync.domain.matching.scoring import score_candidate
from linersync.domain.matching.types import MatchConfig
from linersync.domain.operations import AddTracks, MemberPlan
from linersync.domain.sync import SyncedEntry
from linersync.domain.tracks import Track, TrackKey


def accept_substitutes(
    plan: MemberPlan,
    actual: Sequence[Track],
    sources: Mapping[TrackKey, Track],
    cfg: MatchConfig,
) -> MemberPlan | None:
    """`plan` rewritten to the ids the service used, when every difference
    between `actual` (the playlist as read after the write) and the plan's
    expected list is a song this plan added that came back under a new id and
    still matches its source song (`sources`, by key). None otherwise."""
    expected = plan.expected_after
    if len(actual) != len(expected):
        return None
    added = {
        t for op in plan.operations if isinstance(op, AddTracks) for t in op.track_ids
    }
    actual_ids = [t.track_id for t in actual]
    swapped: dict[str, Track] = {}
    for want, got in zip(expected, actual, strict=True):
        if want == got.track_id:
            continue
        if want not in added or got.track_id in expected:
            return None
        if swapped.setdefault(want, got).track_id != got.track_id:
            return None
    if not swapped or any(want in actual_ids for want in swapped):
        return None
    for want, got in swapped.items():
        keys = [e.source_key for e in plan.synced if e.target_track_id == want]
        if not keys:
            return None
        for key in keys:
            source = sources.get(key)
            if source is None:
                return None
            if score_candidate(source, got, cfg).confidence < cfg.accept_threshold:
                return None
    synced = tuple(
        SyncedEntry(e.source_key, swapped[e.target_track_id].track_id)
        if e.target_track_id in swapped
        else e
        for e in plan.synced
    )
    return replace(plan, expected_after=tuple(actual_ids), synced=synced)
