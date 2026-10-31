import random

import pytest

from linersync.adapters.connectors.youtube_music.moves import apply_moves, plan_moves


def _items(track_ids: list[str]) -> list[tuple[str, str]]:
    return [(t, f"set-{n}") for n, t in enumerate(track_ids)]


@pytest.mark.parametrize("seed", range(200))
def test_moves_reach_any_permutation(seed: int) -> None:
    rng = random.Random(seed)
    current = [rng.choice("abcdef") for _ in range(rng.randint(0, 60))]
    target = current[:]
    rng.shuffle(target)
    items = _items(current)
    moves = plan_moves(items, target)
    assert [t for t, _ in apply_moves(items, moves)] == target
    assert all(set_id != before for set_id, before in moves)


def test_already_ordered_needs_no_moves() -> None:
    assert plan_moves(_items(list("abcab")), list("abcab")) == []


def test_move_to_end_has_no_successor() -> None:
    assert plan_moves(_items(list("abc")), list("bca")) == [("set-0", None)]


def test_not_a_permutation_raises() -> None:
    with pytest.raises(ValueError):
        plan_moves(_items(list("ab")), list("abc"))
