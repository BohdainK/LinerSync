from collections import Counter

from hypothesis import given
from hypothesis.strategies import lists, sampled_from

from linersync.domain.planning.diff import ListDiff, diff_ordered


def test_append_new() -> None:
    assert diff_ordered(["a", "b"], ["a", "b", "c"]) == ListDiff(("c",), ())


def test_preserves_repeats() -> None:
    assert diff_ordered(["a"], ["a", "b", "a"]) == ListDiff(("b", "a"), ())


def test_removes_extra_occurrence_keeps_first() -> None:
    assert diff_ordered(["a", "b", "a"], ["a", "b"]).to_remove == (2,)


def test_protected_not_removed() -> None:
    assert diff_ordered(["a", "x"], ["a"], frozenset({"x"})).to_remove == ()


def test_absent_from_desired_removed() -> None:
    assert diff_ordered(["a", "x", "x"], ["a"]).to_remove == (1, 2)


@given(
    lists(sampled_from("abcd"), max_size=12),
    lists(sampled_from("abcd"), max_size=12),
)
def test_property_multiset_reaches_desired(
    current: list[str], desired: list[str]
) -> None:
    d = diff_ordered(current, desired)
    removed = set(d.to_remove)
    kept = [x for i, x in enumerate(current) if i not in removed]
    assert Counter(kept + list(d.to_add)) == Counter(desired)
