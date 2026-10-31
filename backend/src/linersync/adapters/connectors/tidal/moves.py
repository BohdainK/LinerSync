"""Pure planning for Tidal's move endpoint, which moves selected occurrences
(by itemId), in order, before another occurrence or to the end."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from linersync.adapters.connectors.kit.ordering import lis_indexes, occurrence_labels

MAX_MOVE = 20  # occurrences per PATCH


@dataclass(frozen=True, slots=True)
class Move:
    item_ids: tuple[str, ...]
    before: str | None  # an itemId; None means "to the end"


def plan_moves(current: Sequence[tuple[str, str]], target: Sequence[str]) -> list[Move]:
    """Moves turning ``current`` ((track_id, item_id) per position) into the
    ``target`` order of track ids. The n-th copy of a track stays the n-th."""
    track_ids = [t for t, _ in current]
    if Counter(track_ids) != Counter(target):
        raise ValueError("target is not a permutation of current")
    cur_labels = occurrence_labels(track_ids)
    item_of = {
        label: item for label, (_, item) in zip(cur_labels, current, strict=True)
    }
    tgt_labels = occurrence_labels(target)
    target_index = {label: i for i, label in enumerate(tgt_labels)}
    kept = {
        cur_labels[i]
        for i in lis_indexes([target_index[label] for label in cur_labels])
    }

    # Walk the target from the end: each run of moved occurrences goes right
    # before the occurrence that follows it in the target, which is already
    # in place relative to everything after it.
    moves: list[Move] = []
    anchor: str | None = None
    run: list[str] = []
    for label in reversed(tgt_labels):
        if label in kept:
            moves.extend(_run_moves(run, anchor))
            run = []
            anchor = item_of[label]
        else:
            run.insert(0, item_of[label])
    moves.extend(_run_moves(run, anchor))
    return moves


def _run_moves(run: Sequence[str], anchor: str | None) -> list[Move]:
    """A run in chunks of MAX_MOVE, last chunk first, each before the next."""
    out: list[Move] = []
    end = len(run)
    while end > 0:
        start = max(0, end - MAX_MOVE)
        out.append(Move(tuple(run[start:end]), anchor))
        anchor = run[start]
        end = start
    return out


def apply_moves(
    current: Sequence[tuple[str, str]], moves: Sequence[Move]
) -> list[tuple[str, str]]:
    result = list(current)
    for m in moves:
        moving = set(m.item_ids)
        by_item = {item: (t, item) for t, item in result}
        rest = [x for x in result if x[1] not in moving]
        at = len(rest) if m.before is None else [i for _, i in rest].index(m.before)
        result = rest[:at] + [by_item[i] for i in m.item_ids] + rest[at:]
    return result
