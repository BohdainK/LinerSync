"""Helpers for planning playlist moves with as few requests as possible."""

from bisect import bisect_left
from collections import Counter
from collections.abc import Sequence


def occurrence_labels(ids: Sequence[str]) -> list[tuple[str, int]]:
    """Each id with which copy it is, so the n-th copy stays the n-th."""
    seen: Counter[str] = Counter()
    out: list[tuple[str, int]] = []
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
