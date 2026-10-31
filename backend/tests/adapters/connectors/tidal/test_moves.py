import random

import pytest

from linersync.adapters.connectors.tidal.moves import Move, apply_moves, plan_moves


def _items(track_ids: list[str]) -> list[tuple[str, str]]:
    return [(t, f"item-{n}") for n, t in enumerate(track_ids)]


@pytest.mark.parametrize("seed", range(200))
def test_moves_reach_any_permutation(seed: int) -> None:
    rng = random.Random(seed)
    current = [rng.choice("abcdef") for _ in range(rng.randint(0, 60))]
    target = current[:]
    rng.shuffle(target)
    items = _items(current)
    moves = plan_moves(items, target)
    assert [t for t, _ in apply_moves(items, moves)] == target
    assert all(1 <= len(m.item_ids) <= 20 for m in moves)


def test_already_ordered_needs_no_moves() -> None:
    items = _items(list("abcab"))
    assert plan_moves(items, list("abcab")) == []


def test_moving_one_to_the_end() -> None:
    items = _items(list("abc"))
    assert plan_moves(items, list("bca")) == [Move(("item-0",), None)]


def test_long_run_splits_into_twenty() -> None:
    current = [f"t{i}" for i in range(50)]
    target = current[25:] + current[:25]  # the first 25 move to the end
    items = _items(current)
    moves = plan_moves(items, target)
    assert [len(m.item_ids) for m in moves] == [20, 5]
    assert [t for t, _ in apply_moves(items, moves)] == target


def test_not_a_permutation_raises() -> None:
    with pytest.raises(ValueError):
        plan_moves(_items(list("ab")), list("abc"))
    with pytest.raises(ValueError):
        plan_moves(_items(list("ab")), list("aa"))
