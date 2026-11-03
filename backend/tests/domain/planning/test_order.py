from collections import Counter
from itertools import combinations

from hypothesis import given
from hypothesis.strategies import booleans, lists, sampled_from, tuples

from linersync.domain.planning.order import arrange, moved

_ITEMS = sampled_from(["A", "B", "C", "D", "E"])


def test_moved_finds_the_song_the_user_dragged() -> None:
    assert moved(["A", "B", "C", "D"], ["A", "C", "D", "B"]) == ["B"]


def test_moved_is_empty_when_order_kept_with_gaps() -> None:
    assert moved(["A", "B", "C"], ["A", "C"]) == []


def test_moved_counts_duplicates_separately() -> None:
    assert moved(["A", "B"], ["A", "B", "A"]) == ["A"]


def test_arrange_keeps_user_song_after_its_neighbour() -> None:
    assert arrange(["A", "X", "B", "C"], ["A", "C", "B"], ["X"]) == ["A", "X", "C", "B"]


def test_arrange_new_source_song_at_source_position() -> None:
    assert arrange(["A", "B"], ["A", "N", "B"], []) == ["A", "N", "B"]


def test_arrange_anchor_at_front() -> None:
    assert arrange(["X", "A"], ["A"], ["X"]) == ["X", "A"]


def test_arrange_keeps_song_dragged_to_the_end_at_the_end() -> None:
    assert arrange(["A", "C", "D", "B"], ["A", "D", "C"], ["B"]) == ["A", "D", "C", "B"]


def _is_subsequence(small: list[str], big: list[str]) -> bool:
    it = iter(big)
    return all(item in it for item in small)


@given(previous=lists(_ITEMS, max_size=8), current=lists(_ITEMS, max_size=8))
def test_moved_is_sub_multiset_and_rest_is_subsequence_of_previous(
    previous: list[str], current: list[str]
) -> None:
    out = moved(previous, current)
    assert not Counter(out) - Counter(current)
    # some choice of positions for `out` leaves a subsequence of `previous`
    assert any(
        Counter(current[i] for i in chosen) == Counter(out)
        and _is_subsequence(
            [x for i, x in enumerate(current) if i not in chosen], previous
        )
        for chosen in combinations(range(len(current)), len(out))
    )


@given(
    current=lists(tuples(_ITEMS, booleans()), max_size=8),
    base=lists(_ITEMS, max_size=8),
)
def test_arrange_is_base_plus_anchored(
    current: list[tuple[str, bool]], base: list[str]
) -> None:
    items = [item for item, _ in current]
    anchored = [item for item, flag in current if flag]
    out = arrange(items, base, anchored)
    assert Counter(out) == Counter(base) + Counter(anchored)
    assert _is_subsequence(base, out)
