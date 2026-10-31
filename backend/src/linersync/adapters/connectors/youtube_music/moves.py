"""Pure planning for YouTube Music's move action, which moves one occurrence
(by setVideoId) before another occurrence, or to the end."""

from collections import Counter
from collections.abc import Sequence

from linersync.adapters.connectors.kit.ordering import lis_indexes, occurrence_labels

# (setVideoId to move, setVideoId to put it before; None = to the end)
Move = tuple[str, str | None]


def plan_moves(current: Sequence[tuple[str, str]], target: Sequence[str]) -> list[Move]:
    """Moves turning ``current`` ((track_id, setVideoId) per position) into the
    ``target`` order of track ids. The n-th copy of a track stays the n-th."""
    track_ids = [t for t, _ in current]
    if Counter(track_ids) != Counter(target):
        raise ValueError("target is not a permutation of current")
    cur_labels = occurrence_labels(track_ids)
    set_of = {label: s for label, (_, s) in zip(cur_labels, current, strict=True)}
    tgt_labels = occurrence_labels(target)
    target_index = {label: i for i, label in enumerate(tgt_labels)}
    kept = {
        cur_labels[i]
        for i in lis_indexes([target_index[label] for label in cur_labels])
    }
    # Walk the target from the end: each moved occurrence goes right before
    # the one that follows it in the target, already in place by then.
    moves: list[Move] = []
    successor: str | None = None
    for label in reversed(tgt_labels):
        if label not in kept:
            moves.append((set_of[label], successor))
        successor = set_of[label]
    return moves


def apply_moves(
    current: Sequence[tuple[str, str]], moves: Sequence[Move]
) -> list[tuple[str, str]]:
    result = list(current)
    for set_id, before in moves:
        item = next(x for x in result if x[1] == set_id)
        result.remove(item)
        at = len(result) if before is None else [s for _, s in result].index(before)
        result.insert(at, item)
    return result
