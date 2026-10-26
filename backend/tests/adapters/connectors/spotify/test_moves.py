from bisect import bisect_left
from collections import Counter

import pytest
from hypothesis import given
from hypothesis.strategies import integers, lists, permutations, sampled_from

from linersync.adapters.connectors.spotify.moves import (
    Move,
    RemovalPlan,
    apply_moves,
    apply_removal,
    plan_moves,
    plan_removal,
)
from linersync.domain.operations import Removal


def _lis_len(current: list[str], target: list[str]) -> int:
    seen: Counter[str] = Counter()
    tpos: dict[tuple[str, int], int] = {}
    for i, x in enumerate(target):
        tpos[(x, seen[x])] = i
        seen[x] += 1
    seen = Counter()
    seq = []
    for x in current:
        seq.append(tpos[(x, seen[x])])
        seen[x] += 1
    tails: list[int] = []
    for v in seq:
        j = bisect_left(tails, v)
        if j == len(tails):
            tails.append(v)
        else:
            tails[j] = v
    return len(tails)


def test_plan_moves_rotation() -> None:
    assert plan_moves("abcd", "bcda") == [Move(0, 4)]


def test_plan_moves_identity_is_empty() -> None:
    assert plan_moves("abca", "abca") == []


@given(
    lists(sampled_from("abcd"), max_size=12).flatmap(
        lambda c: permutations(c).map(lambda t: (c, t))
    )
)
def test_plan_moves_reaches_target_minimally(pair: tuple[list[str], list[str]]) -> None:
    current, target = pair
    moves = plan_moves(current, target)
    assert apply_moves(current, moves) == target
    assert len(moves) == len(current) - _lis_len(current, target)


def test_plan_moves_rejects_non_permutation() -> None:
    with pytest.raises(ValueError):
        plan_moves("abc", "abd")
    with pytest.raises(ValueError):
        plan_moves("abc", "ab")
    with pytest.raises(ValueError):
        plan_moves("aab", "abb")


def test_plan_removal_keeps_other_copy() -> None:
    plan = plan_removal(["a", "b", "a", "c"], [Removal("a", 2)])
    assert plan.delete_uris == ("a",)
    assert plan.reinserts == ((0, ("a",)),)
    assert apply_removal(["a", "b", "a", "c"], plan) == ["a", "b", "c"]


def test_plan_removal_all_copies_needs_no_reinsert() -> None:
    plan = plan_removal(["a", "b", "a", "c"], [Removal("a", 0), Removal("a", 2)])
    assert plan == RemovalPlan(("a",), ())
    assert apply_removal(["a", "b", "a", "c"], plan) == ["b", "c"]


def test_plan_removal_rejects_stale_position() -> None:
    with pytest.raises(ValueError):
        plan_removal(["a", "b"], [Removal("a", 1)])
    with pytest.raises(ValueError):
        plan_removal(["a", "b"], [Removal("a", 5)])


@given(
    lists(sampled_from("abcd"), max_size=12),
    lists(integers(min_value=0, max_value=11), unique=True),
)
def test_apply_removal_matches_positional_removal(
    current: list[str], raw_positions: list[int]
) -> None:
    positions = [p for p in raw_positions if p < len(current)]
    removals = [Removal(current[p], p) for p in positions]
    expected = [x for i, x in enumerate(current) if i not in set(positions)]
    assert apply_removal(current, plan_removal(current, removals)) == expected
