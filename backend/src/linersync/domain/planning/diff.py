from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ListDiff:
    to_add: tuple[str, ...]
    to_remove: tuple[int, ...]


def diff_ordered(
    current: Sequence[str],
    desired: Sequence[str],
    protected: frozenset[str] = frozenset(),
) -> ListDiff:
    """Diff ordered lists as multisets, preserving repeats.

    Keeps the first min(count_current, count_desired) occurrences of each id in
    current; later occurrences are removed unless protected. Adds walk desired
    in order, emitting each occurrence beyond count_current.
    """
    current_counts = Counter(current)
    desired_counts = Counter(desired)
    keep_budget = {i: min(n, desired_counts[i]) for i, n in current_counts.items()}

    to_remove: list[int] = []
    for index, item in enumerate(current):
        if keep_budget[item] > 0:
            keep_budget[item] -= 1
        elif item not in protected:
            to_remove.append(index)

    seen: Counter[str] = Counter()
    to_add: list[str] = []
    for item in desired:
        seen[item] += 1
        if seen[item] > current_counts[item]:
            to_add.append(item)
    return ListDiff(tuple(to_add), tuple(to_remove))
