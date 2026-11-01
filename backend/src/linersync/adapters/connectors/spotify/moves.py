"""Pure planning for Spotify's single-item move and delete-every-copy endpoints."""

from collections.abc import Sequence
from dataclasses import dataclass

from linersync.adapters.connectors.kit.ordering import at_positions, stable_occurrences
from linersync.domain.operations import Removal


@dataclass(frozen=True, slots=True)
class Move:
    """Spotify reorder semantics: both indexes refer to the list before the move."""

    range_start: int
    insert_before: int


def apply_moves(items: Sequence[str], moves: Sequence[Move]) -> list[str]:
    result = list(items)
    for m in moves:
        _apply_move(result, m.range_start, m.insert_before)
    return result


def _apply_move[T](work: list[T], start: int, before: int) -> None:
    x = work.pop(start)
    work.insert(before if before < start else before - 1, x)


def plan_moves(current: Sequence[str], target: Sequence[str]) -> list[Move]:
    cur_labels, tgt_labels, kept = stable_occurrences(current, target)
    working = list(cur_labels)
    moves: list[Move] = []
    for t, label in enumerate(tgt_labels):
        if label in kept:
            continue
        i = working.index(label)
        dest = 0 if t == 0 else working.index(tgt_labels[t - 1]) + 1
        moves.append(Move(i, dest))
        _apply_move(working, i, dest)
    return moves


@dataclass(frozen=True, slots=True)
class RemovalPlan:
    delete_uris: tuple[str, ...]
    # (position in the list after deleting, run of ids to insert there), ascending
    reinserts: tuple[tuple[int, tuple[str, ...]], ...]


def plan_removal(current: Sequence[str], removals: Sequence[Removal]) -> RemovalPlan:
    at_positions(current, removals, lambda uri: uri)  # or ValueError: it changed
    dropped = {r.position for r in removals}
    after = [x for i, x in enumerate(current) if i not in dropped]
    delete_uris = tuple(dict.fromkeys(r.track_id for r in removals))
    doomed = set(delete_uris)
    runs: list[tuple[int, tuple[str, ...]]] = []
    last = -2
    for pos, x in enumerate(after):
        if x not in doomed:
            continue
        if pos == last + 1:
            start, ids = runs[-1]
            runs[-1] = (start, (*ids, x))
        else:
            runs.append((pos, (x,)))
        last = pos
    return RemovalPlan(delete_uris, tuple(runs))


def apply_removal(items: Sequence[str], plan: RemovalPlan) -> list[str]:
    doomed = set(plan.delete_uris)
    result = [x for x in items if x not in doomed]
    for pos, ids in plan.reinserts:
        result[pos:pos] = ids
    return result
