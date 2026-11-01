"""Helpers for planning playlist moves with as few requests as possible."""

from bisect import bisect_left
from collections import Counter
from collections.abc import Callable, Sequence
from typing import NamedTuple

from linersync.domain.operations import Removal

Label = tuple[str, int]  # a track id and which copy of it this is


def occurrence_labels(ids: Sequence[str]) -> list[Label]:
    """Each id with which copy it is, so the n-th copy stays the n-th."""
    seen: Counter[str] = Counter()
    out: list[Label] = []
    for x in ids:
        out.append((x, seen[x]))
        seen[x] += 1
    return out


def lis_indexes(seq: Sequence[int]) -> list[int]:
    """Indexes into ``seq`` of one longest strictly increasing subsequence:
    the entries that can stay where they are."""
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


class Occurrences(NamedTuple):
    current: list[Label]
    target: list[Label]
    kept: set[Label]  # the occurrences that can stay where they are


def stable_occurrences(current: Sequence[str], target: Sequence[str]) -> Occurrences:
    """Label both orders and find the most occurrences that need not move.
    Raises ValueError when ``target`` is not a permutation of ``current``."""
    if Counter(current) != Counter(target):
        raise ValueError("target is not a permutation of current")
    cur_labels = occurrence_labels(current)
    tgt_labels = occurrence_labels(target)
    target_index = {label: i for i, label in enumerate(tgt_labels)}
    kept = {
        cur_labels[i]
        for i in lis_indexes([target_index[label] for label in cur_labels])
    }
    return Occurrences(cur_labels, tgt_labels, kept)


def at_positions[T](
    items: Sequence[T], removals: Sequence[Removal], track_id: Callable[[T], str]
) -> list[T]:
    """The items the removals point at. Raises ValueError when a position is
    out of range or holds another track: the list changed since planning."""
    out: list[T] = []
    for r in removals:
        in_range = 0 <= r.position < len(items)
        if not in_range or track_id(items[r.position]) != r.track_id:
            raise ValueError(f"position {r.position} does not hold {r.track_id!r}")
        out.append(items[r.position])
    return out
