"""Pure planning for Spotify's single-item move and delete-every-copy endpoints."""

from bisect import bisect_left
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

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


def _labels(ids: Sequence[str]) -> list[tuple[str, int]]:
    seen: Counter[str] = Counter()
    out: list[tuple[str, int]] = []
    for x in ids:
        out.append((x, seen[x]))
        seen[x] += 1
    return out


def _lis_indexes(seq: Sequence[int]) -> list[int]:
    """Indexes into ``seq`` of one longest strictly increasing subsequence."""
    tails: list[int] = []  # smallest tail value per length
    tail_idx: list[int] = []  # index in seq of that tail
    prev: list[int] = [-1] * len(seq)
    for i, v in enumerate(seq):
        j = bisect_left(tails, v)
        if j == len(tails):
            tails.append(v)
            tail_idx.append(i)
        else:
            tails[j] = v
            tail_idx[j] = i
        prev[i] = tail_idx[j - 1] if j > 0 else -1
    out: list[int] = []
    k = tail_idx[-1] if tail_idx else -1
    while k != -1:
        out.append(k)
        k = prev[k]
    out.reverse()
    return out


def plan_moves(current: Sequence[str], target: Sequence[str]) -> list[Move]:
    if Counter(current) != Counter(target):
        raise ValueError("target is not a permutation of current")
    cur_labels = _labels(current)
    tgt_labels = _labels(target)
    target_index = {label: i for i, label in enumerate(tgt_labels)}
    seq = [target_index[label] for label in cur_labels]
    kept = {cur_labels[i] for i in _lis_indexes(seq)}

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
    for r in removals:
        if not 0 <= r.position < len(current) or current[r.position] != r.track_id:
            raise ValueError(f"position {r.position} does not hold {r.track_id!r}")
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
