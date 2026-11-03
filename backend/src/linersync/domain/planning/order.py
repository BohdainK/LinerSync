"""Order helpers for respecting a user's own edits on a destination playlist."""

from collections import Counter
from collections.abc import Sequence


def moved(previous: Sequence[str], current: Sequence[str]) -> list[str]:
    """Items of `current` outside a longest common subsequence with `previous`.

    Duplicates count separately. On ties the LCS keeps earlier `current` items.
    """
    n, m = len(previous), len(current)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if previous[i] == current[j]:
                table[i][j] = table[i + 1][j + 1] + 1
            else:
                table[i][j] = max(table[i + 1][j], table[i][j + 1])
    out: list[str] = []
    i = j = 0
    while j < m:
        if (
            i < n
            and previous[i] == current[j]
            and table[i][j] == table[i + 1][j + 1] + 1
        ):
            i += 1
            j += 1
        elif i < n and table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            out.append(current[j])
            j += 1
    return out


def arrange(
    current: Sequence[str], base: Sequence[str], anchored: Sequence[str]
) -> list[str]:
    """`base` with each anchored occurrence of `current` kept in place.

    An anchored occurrence goes after the preceding (in `current`) non-anchored
    base occurrence with the greatest position in `base`; none means the front.
    Anchored items sharing an anchor keep their `current` order.
    """
    left = Counter(anchored)
    in_base = Counter(base)
    positions: dict[str, list[int]] = {}
    for index, item in enumerate(base):
        positions.setdefault(item, []).append(index)
    after: dict[int | None, list[str]] = {}
    seen: Counter[str] = Counter()
    anchor: int | None = None
    for item in current:
        if left[item] > 0:
            left[item] -= 1
            after.setdefault(anchor, []).append(item)
        elif seen[item] < in_base[item]:
            position = positions[item][seen[item]]
            seen[item] += 1
            if anchor is None or position > anchor:
                anchor = position
    out = list(after.get(None, []))
    for index, item in enumerate(base):
        out.append(item)
        out.extend(after.get(index, []))
    return out
